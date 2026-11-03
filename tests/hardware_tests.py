# -*- coding: utf-8 -*-
# Copyright (c) 2026 Zhijian Yi (Guangdong, China) — PSoC-Potentiostat-YI-1.0
# Licensed under CC BY-SA 3.0 (same as the upstream PSoC-Potentiostat project)
"""PSoC-Stat 真机实测（A 档：全自动判定）。

插着 PSoC 设备时，用与 GUI 完全相同的通信栈（comm.AmpUsb）发真实命令，
验证固件全链路。设计依据：软件测试里设备端是 FakeSerialComm，它永远乖乖
回 Done，抓不到真固件的病——真机踩过的坑全是这类：
  * 'S' 大 LUT 扫描固件卡死（CV/ASV 无响应）
  * SWV 实际节拍与标称不符
  * 扫描中途异常后必须拔 USB 才能恢复

覆盖项目：
  1. 连接与设备识别（'I' 命令）
  2. 标定值合理性（'B' 标定，counts_to_current 非 0 且在合理量级）
  3. 小 LS 扫描（约 200 台阶，秒级）——S→C→R→Done→E0 全链路
  4. 大 LUT CV 扫描（2800 台阶，复现真机卡死场景的原参数）
  5. 小 SWV 扫描（'G' 命令，差分数据成对）
  6. 小 DPV 扫描（'P' 命令，验证 Dave 的 DPV 补丁在固件里工作）
  7. 连续 3 次重复扫描（抓偶发卡死/内存泄漏）
  8. 扫描中途复位恢复（'X' 后再扫必须成功）
  9. 计时电流 3 秒（'M'/'F0' 数据流）

每次扫描记录"实测耗时 vs 理论耗时"，偏差 >20% 报 WARN（节拍问题线索，
可直接转给固件作者），不计 FAIL；超时/无 Done/数据长度错误才 FAIL。

用法：
  python hardware_tests.py            # 真机
  python hardware_tests.py --fake     # 沙盒自检（用假设备验证本脚本逻辑）
返回码：0 = 全部通过；1 = 存在 FAIL；2 = 未检测到设备
"""
import os as _os
import sys as _sys

# 测试位于 tests/ 子目录：无论从哪里启动，都把项目根加进模块搜索路径
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import sys
import time

from comm import AmpUsb, COMPLETE_MESSAGE, TERMINATION_CODE, USB_IN_BYTE_SIZE
from config import DeviceParameters

# 扫描参数（与 GUI 同一套换算，见 comm.format_voltage / format_divider）
CLK_FREQ = 480000          # PWM_isr 时钟 Hz（config.PWM_FREQ）
TICK_5MS = 2399            # 5 ms/拍的 period 寄存器值
TICK_10MS = 4799           # 10 ms/拍


class HardwareTestError(Exception):
    pass


class _StubMaster:
    """AmpUsb 需要的最小 master 接口（不建 tk 窗口）。"""
    data_save_type = "Converted"

    def failed_connection(self):
        raise HardwareTestError("设备连接中断")

    def set_voltage_source_label(self, *args, **kwargs):
        pass


def _connect(make_fake=False):
    """连接设备；返回 AmpUsb 或 None（未检测到）。"""
    if make_fake:
        import comm as comm_module
        from fake_device import FakeSerialComm
        comm_module.SerialComm = FakeSerialComm
    master = _StubMaster()
    dev = AmpUsb(master, DeviceParameters())
    return dev if dev.connected else None


def _poll_done(dev, timeout_s):
    """轮询等待 Done；返回 (是否收到, 从发 R 起算的耗时秒)。"""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        msg = dev.usb_read_message()
        if msg and COMPLETE_MESSAGE in msg:
            return True, time.time() - t0
        time.sleep(0.05)
    return False, time.time() - t0


def _fetch_data(dev, expect_points):
    """'E0' 取数；返回 int16 原始样本列表。"""
    dev.usb_write('E0')
    time.sleep(0.1)
    n_packets = int(expect_points * 2 / USB_IN_BYTE_SIZE) + 3
    return dev.get_data(n_packets)


def _tiny_ls_scan(dev, timeout_s=8):
    """1 秒小 LS 扫描：扫描自然结束时固件自动关 ISR，设备落在
    "GUI 正常用完"的良好状态。用作 X 之后的恢复动作与会话收尾。
    （旧固件：会话以 X 收尾后设备停在 Sleep 态，下次连接标定失败；
    本分支固件 'X' 已不休眠硬件，此恢复动作保留作无害兜底。）"""
    start = _dac_count(dev, -100)
    end = _dac_count(dev, 100)
    points = abs(end - start)
    msg = "S|{0:04d}|{1:04d}|{2:05d}|LS".format(start, end, TICK_5MS)
    return _run_lut_scan(dev, msg, TICK_5MS // 2, points, timeout_s)


def _run_lut_scan(dev, params_msg, compare, expect_points, timeout_s):
    """发 LUT 参数 → C 比较值 → R → 等 Done → E0 取数。

    返回 (样本数, 实测耗时秒)。失败抛 HardwareTestError。
    """
    dev.clear_in_buffer()
    dev.usb_write(params_msg)
    time.sleep(0.2)
    dev.usb_write('C|{0:05d}'.format(compare))
    time.sleep(0.1)
    t0 = time.time()
    dev.usb_write('R')
    ok, _ = _poll_done(dev, timeout_s)
    elapsed = time.time() - t0
    if not ok:
        # 超时隔离：扫描可能仍在固件里跑，不复位会毒化后续所有测试
        # （真机踩坑：一次超时后，下一项的 R 被固件以 Error1 拒绝）。
        # X 会让硬件 Sleep，'H' 唤不醒（真机实测），必须 X 后跑一个小
        # 扫描让它自然结束——同一会话内 X 后再扫描可恢复正常（已验证）
        dev.usb_write('X')
        time.sleep(0.3)
        try:
            _tiny_ls_scan(dev)
        except HardwareTestError:
            pass  # 恢复扫描也失败也没关系，由后续测试各自判定
        dev.clear_in_buffer()
        raise HardwareTestError(
            "扫描超时（{0:.0f} 秒内未收到 Done，命令 {1}）".format(
                timeout_s, params_msg))
    raw = _fetch_data(dev, expect_points)
    n = len(raw)
    # 数据长度允许 ±10% 余量（首点丢弃/终止码截断等正常差异）
    if n < expect_points * 0.9 or n > expect_points * 1.2:
        raise HardwareTestError(
            "数据点数异常：预期约 {0}，实收 {1}".format(expect_points, n))
    return n, elapsed


def _dac_count(dev, mv):
    return dev.format_voltage(mv)[1]


class HardwareTester:
    def __init__(self, dev):
        self.dev = dev
        self.lines = []
        self.failures = []

    def _report(self, name, status, detail=""):
        line = "  {0:4s} {1}{2}".format(status, name,
                                         "（{0}）".format(detail) if detail else "")
        print(line)
        self.lines.append(line)
        if status == "FAIL":
            self.failures.append(name)

    def _timing_note(self, name, elapsed, expected_s):
        """节拍偏差 >20% 记 WARN（线索，不算失败）。"""
        dev_pct = (elapsed / expected_s - 1) * 100 if expected_s else 0
        note = "实测 {0:.1f}s / 理论 {1:.1f}s（偏差 {2:+.0f}%）".format(
            elapsed, expected_s, dev_pct)
        if abs(dev_pct) > 20:
            self._report(name + " - 节拍偏差", "WARN", note + "，超 20%")
        else:
            self.lines.append("       ↳ " + note)
            print("       ↳ " + note)

    # ---- 各测试项 ----

    def t01_connection(self):
        # 自己发 'I' 查询：connection_test 的识别表对字节/字符串键敏感，
        # 这里只验证"设备能回应识别串"这一本质
        self.dev.clear_in_buffer()
        self.dev.usb_write('I')
        time.sleep(0.2)
        msg = self.dev.usb_read_message()
        if not msg:
            raise HardwareTestError("设备未回应识别串")
        if isinstance(msg, bytes):
            msg = msg.decode('utf-8', errors='replace')
        return "识别串: {0}".format(msg.strip('\x00'))

    def t02_calibration(self):
        c = self.dev.device_params.adc_tia.counts_to_current
        if not (0 < c < 1):
            raise HardwareTestError("counts_to_current 异常: {0}".format(c))
        return "counts_to_current = {0:.5f} µA/count{1}".format(
            c, "（标定失败，用了理论值）"
            if getattr(self.dev.device_params.adc_tia, 'calibrate_failed', False)
            else "")

    def t03_small_ls(self):
        n, elapsed = _tiny_ls_scan(self.dev, timeout_s=15)
        self._timing_note("小 LS", elapsed, 200 * 0.005)
        return "{0} 点".format(n)

    def t04_large_lut_cv(self):
        """真机卡死场景原参数：S|2548|1148|02399|CS（2800 台阶）。"""
        points = 2 * abs(2548 - 1148)  # 三角往返
        n, elapsed = _run_lut_scan(self.dev, "S|2548|1148|02399|CS",
                                   2399 // 2, points, timeout_s=45)
        self._timing_note("大 LUT CV", elapsed, points * 0.005)
        return "{0} 点".format(n)

    def _small_pulse(self, cmd):
        start = _dac_count(self.dev, -100)
        end = _dac_count(self.dev, 100)
        steps = abs(end - start) // 5
        points = steps * 2  # 50% 占空：每台阶 2 拍
        # 字段宽度必须与 GUI 完全一致：inc/height 都是 4 位（固件按固定
        # 偏移 [12,4]/[17,4] 解析）——真机实测踩坑：发 3 位字段固件
        # 解析错位，只建 1 个台阶，扫完只回 4 个点
        msg = "{0}|{1:04d}|{2:04d}|0005|0010|{3:05d}|LS".format(
            cmd, start, end, TICK_10MS)
        n, elapsed = _run_lut_scan(self.dev, msg, TICK_10MS // 2,
                                   points, timeout_s=20)
        self._timing_note("{0} 扫描".format(cmd), elapsed, points * 0.010)
        return "{0} 点（{1} 对差分）".format(n, steps)

    def t05_small_swv(self):
        return self._small_pulse('G')

    def t06_small_dpv(self):
        return self._small_pulse('P')

    def t07_repeat_scans(self):
        for i in range(3):
            self.t03_small_ls()
        return "连续 3 次均成功"

    def t08_reset_recovery(self):
        # 扫到一半发 X 中止
        start = _dac_count(self.dev, -100)
        end = _dac_count(self.dev, 100)
        self.dev.usb_write("S|{0:04d}|{1:04d}|{2:05d}|LS".format(
            start, end, TICK_5MS))
        time.sleep(0.2)
        self.dev.usb_write('C|{0:05d}'.format(TICK_5MS // 2))
        self.dev.usb_write('R')
        time.sleep(0.3)  # 扫描进行中
        self.dev.usb_write('X')
        time.sleep(0.5)
        self.dev.clear_in_buffer()
        # 复位后必须能正常再扫（同一会话内 X 后扫描可恢复，已真机验证）
        self.t03_small_ls()
        return "中止后再次扫描成功"

    def t09_chronoamperometry(self):
        dev = self.dev
        dev.clear_in_buffer()
        # 100 Hz 采样：T|period；M|电压|每段点数
        dev.usb_write('T|{0:05d}'.format(int(round(CLK_FREQ / 100)) - 1))
        time.sleep(0.1)
        dev.usb_write('M|{0:04d}|0150'.format(_dac_count(dev, 0)))
        ok, _ = _poll_done(dev, 5)  # 第一段 150 点 @100Hz = 1.5 s
        if not ok:
            raise HardwareTestError("计时电流 5 秒内未收到数据就绪消息")
        dev.usb_write('F0')
        time.sleep(0.1)
        raw = dev.get_data(int(150 * 2 / USB_IN_BYTE_SIZE) + 3)
        dev.usb_write('X')  # 停止
        time.sleep(0.3)
        # X 会停止计时电流 ISR 并让硬件 Sleep，补一个小扫描让设备
        # 落在"扫描自然结束"的良好状态，不带病进入收尾
        _tiny_ls_scan(dev)
        dev.clear_in_buffer()
        if len(raw) < 100:
            raise HardwareTestError("计时电流数据过少: {0}".format(len(raw)))
        return "{0} 点（首段 @100Hz）".format(len(raw))

    TESTS = [
        ("连接与设备识别", t01_connection),
        ("标定值合理性", t02_calibration),
        ("小 LS 扫描（S 命令全链路）", t03_small_ls),
        ("大 LUT CV 扫描（2800 台阶，卡死场景原参数）", t04_large_lut_cv),
        ("小 SWV 扫描（G 命令）", t05_small_swv),
        ("小 DPV 扫描（P 命令补丁）", t06_small_dpv),
        ("连续 3 次重复扫描", t07_repeat_scans),
        ("扫描中途复位恢复（X）", t08_reset_recovery),
        ("计时电流 3 秒（M/F0）", t09_chronoamperometry),
    ]

    def run_all(self):
        for i, (name, func) in enumerate(self.TESTS, 1):
            label = "{0}. {1}".format(i, name)
            try:
                detail = func(self)
                self._report(label, "OK", detail)
            except HardwareTestError as err:
                self._report(label, "FAIL", str(err))
            except Exception as err:
                self._report(label, "FAIL", "异常: {0!r}".format(err))
        return self.failures


def run(make_fake=False):
    """入口：返回 (报告行列表, 失败列表, 是否检测到设备)。"""
    # Windows 中文控制台是 GBK：µ（U+00B5 微符号）等字符 print 直接
    # UnicodeEncodeError 崩掉整个测试（真机踩坑），无法编码就替换
    try:
        sys.stdout.reconfigure(errors='replace')
        sys.stderr.reconfigure(errors='replace')
    except Exception:
        pass
    try:
        dev = _connect(make_fake)
    except HardwareTestError:
        return [], ["连接设备失败"], False
    if dev is None:
        return ["  未检测到 PSoC 设备，硬件实测跳过"], [], False
    tester = HardwareTester(dev)
    failures = tester.run_all()
    try:
        # 收尾：绝不能让设备停在 X 后的 Sleep 态（断电前一直保持，
        # 下次连接会标定失败、扫描卡死——真机两次实测实锤）。
        # 跑一个小扫描让它自然结束 = "GUI 正常用完"的良好状态。
        # 哪怕前面有 FAIL 也要跑，尽量把设备救回来
        _tiny_ls_scan(dev)
    except Exception:
        pass
    return tester.lines, failures, True


if __name__ == "__main__":
    lines, failures, found = run(make_fake='--fake' in sys.argv)
    print()
    if not found:
        sys.exit(2)
    if failures:
        print("硬件实测未通过 {0} 项: {1}".format(len(failures), ", ".join(failures)))
        sys.exit(1)
    print("硬件实测全部通过")
    sys.exit(0)
