# Copyright (c) 2026 Zhijian Yi (Guangdong, China) — PSoC-Potentiostat-YI-1.0
# Licensed under CC BY-SA 3.0 (same as the upstream PSoC-Potentiostat project)
"""固件模拟器 + 合成测试数据（无需真实硬件）。

FakeSerialComm 模拟 PSoC-Stat 固件（Amperometry_v059_2 + 本分支补丁：
'O' 搅拌、'P' 真 DPV、'S|...|N' CV 多圈、'g' LUT 长度回读）对上位机
各 USB/串口命令的应答行为，使 ASV 全流程与 CV 多圈可以在无设备条件下测试。

采样对顺序对齐补丁固件 LUT（这是"ASV 峰向下"回归测试的关键）：
固件把脉冲放在 value + pulse_height（DAC 计数），而 DAC 计数与电极
电位反相（dac = 2048 − V），所以每步第一个采样在电极电位"低侧"（E−h），
第二个在高侧（E+h 或基底 E）。GUI 端按扫描方向取差分（上行: 后减前）。

用法:
    from fake_device import FakeSerialComm, make_fake_ampusb
    fake = FakeSerialComm(support_multi_cycle=True)
    usb = make_fake_ampusb(master, fake)   # master 需有 device_params 属性

直接运行本文件可在 test_data/ 生成全套合成回归数据（CV 多圈 / LSV /
SWV / DPV / ASV / 计时电流），均带 '# experiment:' 元数据头，
可用 GUI 的 文件->打开数据 直接路由到对应页面。
"""

import logging
import math
import os
import random
import struct

from comm import (AmpUsb, COMPLETE_MESSAGE, TERMINATION_CODE,
                  USB_IN_BYTE_SIZE, PRODUCT_STRING)

# 模拟电极/溶液参数（铅在乙酸缓冲液中的溶出峰）
PEAK_POSITION_RATIO = 0.6   # 峰出现在扫描区间的 60% 处（约 -300 mV 附近）
PEAK_HEIGHT_COUNTS = 400    # 峰高（ADC 计数），约 0.4 µA @ 0.001 µA/count
PEAK_SIGMA_RATIO = 0.04     # 峰宽（占扫描区间比例）
BASELINE_COUNTS = 100       # 基线计数
NOISE_COUNTS = 8            # 噪声幅度（计数）


def synthesize_ls_data(n_points, peak_height=PEAK_HEIGHT_COUNTS, seed=42):
    """生成线性扫描溶出的合成 ADC 计数序列：线性基线 + 高斯峰 + 噪声。"""
    rng = random.Random(seed)
    data = []
    peak_center = PEAK_POSITION_RATIO * n_points
    sigma = max(PEAK_SIGMA_RATIO * n_points, 1.0)
    for i in range(n_points):
        baseline = BASELINE_COUNTS + int(20.0 * i / n_points)  # 缓升基线
        peak = peak_height * math.exp(-((i - peak_center) ** 2) / (2 * sigma ** 2))
        noise = rng.randint(-NOISE_COUNTS, NOISE_COUNTS)
        data.append(int(baseline + peak + noise))
    return data


def synthesize_dpv_data(n_points, peak_height=PEAK_HEIGHT_COUNTS, seed=42,
                        ascending=True):
    """生成 DPV/SWV 合成采样对，顺序对齐固件 LUT（见模块 docstring）。

    每步 2 个采样：第一个 = 电位低侧（E−h），第二个 = 高侧（E+h/基底）。
    上行扫描（如 ASV 溶出）波形放在第二个采样；下行扫描时第一个采样
    才在扫描方向上，波形取负（还原波，阴极电流为负）。
    """
    rng = random.Random(seed)
    peak = synthesize_ls_data(n_points, peak_height, seed)
    data = []
    for i in range(n_points):
        low = BASELINE_COUNTS + rng.randint(-NOISE_COUNTS, NOISE_COUNTS)
        high = BASELINE_COUNTS + rng.randint(-NOISE_COUNTS, NOISE_COUNTS)
        if ascending:
            high += peak[i]
        else:
            low -= peak[i]
        data.append(low)
        data.append(high)
    return data


def synthesize_cv_cycle_data(n_points, peak_height=PEAK_HEIGHT_COUNTS, seed=42):
    """生成半圈（单程）CV 合成数据：峰幅度带方向——上行段为阳极峰（正），
    供 build_cv_lut 按段拼接时可对下行段取负（cathodic）。"""
    return synthesize_ls_data(n_points, peak_height, seed)


def build_cv_lut(start, end, gnd, cycles, start_type, peak_height=PEAK_HEIGHT_COUNTS,
                 seed=42):
    """按补丁固件 lut_protocols.c 的分段规则生成 CV 采样序列（DAC 计数空间）。

    与 LUT_MakeTriangle_Wave / LUT_MakeCVStartZero（cycles 版）逐点一致：
      'S'：N 个 [start→end→start] 往返，段间去重端点，收尾保持 start 1 拍；
      'Z'：0→start 一次，N−1 个往返，末圈 start→end 后 end→0 收尾保持 1 拍。
    波形：电极电位上行的段（DAC 递减，a > b）加阳极峰，回程段为
    阴极峰（负向），幅度对称——模拟可逆电对。
    """
    def seg(a, b, wave):
        return synthesize_ls_data(abs(b - a) + 1, wave, seed)

    def wave_of(a, b):
        # DAC 计数 a > b = 电极电位上行 = 阳极峰（正）；反之阴极峰（负）
        return peak_height if a > b else -peak_height

    lut = []
    if start_type == 'Z':
        parts = [(gnd, start, False)]
        for _ in range(cycles - 1):
            parts += [(start, end, False), (end, start, False)]
        parts += [(start, end, False), (end, gnd, True)]
        for a, b, keep_last in parts:
            piece = seg(a, b, wave_of(a, b))
            lut.extend(piece if keep_last else piece[:-1])
        lut.append(BASELINE_COUNTS)  # 收尾保持点
    else:
        for cycle in range(cycles):
            lut.extend(seg(start, end, wave_of(start, end))[:-1])
            piece = seg(end, start, wave_of(end, start))
            lut.extend(piece if cycle == cycles - 1 else piece[:-1])
        lut.append(BASELINE_COUNTS)  # 收尾保持点（停在 start）
    return lut


def simulate_lut_length(start, end, gnd, cycles, start_type):
    """补丁固件 'S|...|CS|N' 加载后 'g' 应回读的波形表长度（回归基准）。"""
    return len(build_cv_lut(start, end, gnd, cycles, start_type,
                            peak_height=0, seed=42))


class FakeSerialComm:
    """模拟 SerialComm 接口的假设备（AmpUsb.device 的直接替换物）。

    支持命令: I / VR / B / S| / G| / P| / g / R / E<n> / D| / O| / H / s / d /
    X / C| / T| / A| / L| / M / F
    收到的所有命令记录在 self.received，搅拌与电压命令另有专门日志。

    :param support_multi_cycle: False 时模拟未刷多圈补丁的旧固件
        （'S' 忽略圈数后缀、'g' 回读 1 圈长度），用于回归 GUI 逐圈调度路径
    """

    def __init__(self, peak_height=PEAK_HEIGHT_COUNTS, seed=42,
                 support_multi_cycle=True):
        self.connected = True
        self.found = True
        self.peak_height = peak_height
        self.seed = seed
        self.support_multi_cycle = support_multi_cycle
        self.received = []       # 全部命令（str）
        self.stirring_log = []   # [('on'/'off')]
        self.voltage_log = []    # [dac_count]
        self._out = bytearray()  # 待读取的字节缓冲
        self._pending_data = None  # 'E' 命令时要发的 int16 列表
        self._lut_length = 0     # 'g' 命令回读的波形表长度
        self._amp_pts = 0        # 'M' 命令设定的每段点数（0 = 未在计时电流模式）
        self._amp_followup = False  # 数据读完后补发下一条 "Done0"

    # ---- 写（上位机 -> 设备） ----
    def write_data(self, message):
        if isinstance(message, bytes):
            message = message.decode('utf-8', errors='replace')
        logging.debug("fake device got: %s", message)
        self.received.append(message)
        cmd = message[0]
        if cmd == 'I':
            self._queue_str(PRODUCT_STRING)
        elif cmd == 'V' and message == 'VR':
            self._out.extend(b'V\x02')          # 报告使用 DVDAC
        elif cmd == 'B':
            self._queue_calibration()
        elif cmd == 'S':
            self._prepare_ls_data(message)
        elif cmd == 'G':
            self._prepare_dpv_data(message)
        elif cmd == 'P':
            # 真 DPV（自研固件 'P' 命令）: P|start|end|inc|height|period|AB
            self._prepare_dpv_data(message)
        elif cmd == 'g':
            # 回读波形表长度（2 字节 uint16），多圈能力探测用
            self._out.extend(struct.pack('H', self._lut_length & 0xFFFF))
        elif cmd == 'R':
            # 真机固件回的是 'Done\x00'（带空字符结尾），如实模拟，
            # 防止上位机用 == 比较时漏判（2026-08-25 真机实测踩过）
            self._queue_str(COMPLETE_MESSAGE + '\x00')
        elif cmd == 'E':
            self._queue_pending_data()
        elif cmd == 'D':
            self.voltage_log.append(int(message[2:6]))
        elif cmd == 'O':
            self.stirring_log.append('on' if message[2:3] == '1' else 'off')
        elif cmd == 'M':
            # 计时电流：M|电压|每段点数。固件攒满一段后发 "Done<通道>"，
            # 这里立即备好第一段的就绪消息（6 字节，含空字符结尾）
            self._amp_pts = int(message.split('|')[2])
            self._out.extend(b'Done0\x00')
        elif cmd == 'F':
            # 'F<通道>' 导出一段流式数据：pts 个样本 + 终止码。
            # 下一条 "Done0" 不能紧跟在数据后（字节流会丢失 USB 包边界，
            # 可能被当 int16 吃掉），改为缓冲读空后再补发
            if self._amp_pts:
                payload = ([BASELINE_COUNTS + (i % 5) for i in range(self._amp_pts)]
                           + [TERMINATION_CODE])
                self._out.extend(struct.pack(f"{len(payload)}h", *payload))
                self._amp_followup = True
        # 其余命令（H/s/d/X/C/T/A/L）只记录，无应答

    # ---- 读（设备 -> 上位机），签名与 SerialComm.read_data 一致 ----
    def read_data(self, data_length, encoding=None):
        chunk = bytes(self._out[:data_length])
        del self._out[:data_length]
        if not self._out and getattr(self, '_amp_followup', False):
            # 一段数据被读空后，固件继续采集的下一段就绪
            self._amp_followup = False
            self._out.extend(b'Done0\x00')
        if encoding == 'int16':
            size = len(chunk) // 2
            return list(struct.unpack(f"{size}h", chunk))
        if encoding == 'str':
            return chunk.decode('utf-8', errors='replace')
        return chunk

    def clear_in_buffer(self):
        self._out.clear()

    def bytes_waiting(self):
        return len(self._out)

    def poll_for_data(self):
        if self._out:
            return bytes(self._out)
        return None

    # ---- 内部应答构造 ----
    def _queue_str(self, text):
        self._out.extend(text.encode('utf-8'))

    def _queue_calibration(self):
        # 10 个 int16: shift=0, counts_to_current = 0.001 µA/count
        values = [8, 0, 0, 0, 8, 1000, 0, 0, 0, -1000]
        self._out.extend(struct.pack(f"{len(values)}h", *values))

    @staticmethod
    def _parse_lut_range(message):
        """从 'S|XXXX|YYYY|...' / 'G|XXXX|YYYY|...' 解析起止 DAC 计数。"""
        parts = message.split('|')
        return int(parts[1]), int(parts[2])

    @staticmethod
    def _ground_count(start, end):
        """按数值范围推断 DAC 类型的虚拟地计数（8-bit: 128，DVDAC: 2048）。"""
        return 128 if max(abs(start), abs(end)) < 300 else 2048

    def _prepare_ls_data(self, message):
        """按补丁固件 user_lookup_table_maker 如实生成：
        'L' 型（LS）单段 start→end + 末尾保持 1 点；
        'C' 型（CV）分段，段间去掉重复端点，末尾保持 1 点——
        'S' 三角 N 圈 start→end→start…；'Z' 零起点 0→start→(N 圈)→0
        （计数空间，圈数取自尾缀 '|N'；旧固件模式忽略圈数）。
        峰只出现在电极电位上行的段。
        """
        parts = message.split('|')
        start, end = int(parts[1]), int(parts[2])
        suffix = parts[4] if len(parts) >= 5 else 'LS'
        gnd = self._ground_count(start, end)

        if not suffix.startswith('C'):
            scan = synthesize_ls_data(abs(end - start) + 1, self.peak_height,
                                      self.seed)
            data = scan + [scan[-1]]  # 固件末尾多保持 1 个 tick
        else:
            cycles = 1
            if (len(parts) >= 6 and parts[5].isdigit()
                    and self.support_multi_cycle):
                cycles = max(1, int(parts[5]))
            data = build_cv_lut(start, end, gnd, cycles, suffix[-1],
                                peak_height=self.peak_height, seed=self.seed)
        self._pending_data = data
        self._lut_length = len(data)

    def _prepare_dpv_data(self, message):
        parts = message.split('|')
        start, end = int(parts[1]), int(parts[2])
        if parts[0] == 'P' or len(parts) >= 7:
            # SWV 'G' / 真 DPV 'P' 格式: X|start|end|inc|height|period|AB
            # 固件对 'L' 型只扫单段 start→end，每步 2 个采样（正/反脉冲交错）
            inc = max(int(parts[3]), 1)
        else:
            # DPV 旧格式: G|start|end|height|inc|period
            inc = max(int(parts[4]), 1)
        n_points = abs(end - start) // inc + 1
        # DAC 计数递减 = 电极电位上行（ASV 溶出方向）
        ascending = start > end
        self._pending_data = synthesize_dpv_data(n_points, self.peak_height,
                                                 self.seed, ascending=ascending)
        self._lut_length = len(self._pending_data)

    def _queue_pending_data(self):
        if self._pending_data is None:
            # 没有先收到扫描参数就要求导出：返回单点 + 终止码
            self._pending_data = [BASELINE_COUNTS]
        # 首元素会被 GUI pop 掉（对应固件 data[0] 被覆写的行为），末尾加终止码。
        # 与固件一致：LUT 常驻内存，重复 'E' 导出返回同样数据（GUI 逐圈
        # 调度依赖这一点——每圈重发 'R' 后直接 'E0' 取数）
        payload = [0] + self._pending_data + [TERMINATION_CODE]
        self._out.extend(struct.pack(f"{len(payload)}h", *payload))


def make_fake_ampusb(master, fake=None):
    """构造一个跳过真实连接流程、直接使用假设备的 AmpUsb 实例。"""
    usb = AmpUsb.__new__(AmpUsb)
    usb.connected = True
    usb.device_params = master.device_params
    usb.master = master
    usb.device_type = 'kit-059'
    usb.last_experiment = 'CV'
    usb.samples_to_smooth = 1
    usb.device = fake if fake is not None else FakeSerialComm()
    return usb


# ======================================================================
# 合成回归数据（test_data/）：脱离硬件的全方法回归测试集
# ======================================================================

def _gaussian(x, center, sigma, height):
    return height * math.exp(-((x - center) ** 2) / (2.0 * sigma ** 2))


def synthesize_cv_voltammogram(voltages, e_anodic=200.0, e_cathodic=80.0,
                               peak_uA=40.0, sigma_mV=45.0, seed=7):
    """可逆电对（铁氰化钾型）多圈 CV：上行段阳极峰（正），下行段阴极峰（负），
    ΔEp ≈ 120 mV（SPE 实测量级），峰电流随圈数轻微衰减（表面吸附耗竭）。

    :param voltages: 完整电位序列（make_cv_x_line 的输出，mV）
    :return: 电流列表（µA）
    """
    rng = random.Random(seed)
    currents = []
    prev_v = None
    cycle_no = 0
    for v in voltages:
        if prev_v is not None and v > prev_v and (
                prev_v < -800 or abs(v - prev_v) > 1000):
            cycle_no += 1  # 序列跳变 = 新的一圈开始（'Zero' 型归零段）
        decay = 1.0 / (1.0 + 0.08 * cycle_no)
        i = _gaussian(v, e_anodic, sigma_mV, peak_uA * decay)
        i -= _gaussian(v, e_cathodic, sigma_mV, peak_uA * 0.85 * decay)
        i += rng.uniform(-0.6, 0.6)  # 噪声
        currents.append(round(i, 4))
        prev_v = v
    return currents


def synthesize_stripping_voltammogram(voltages, peak_mV=-300.0, peak_uA=4.0,
                                      sigma_mV=60.0, seed=11):
    """ASV 溶出曲线：缓升基线 + 阳极溶出峰（正，朝上）。"""
    rng = random.Random(seed)
    v0, v1 = voltages[0], voltages[-1]
    currents = []
    for v in voltages:
        frac = (v - v0) / max(v1 - v0, 1e-9)
        baseline = 0.3 + 0.5 * frac
        i = baseline + _gaussian(v, peak_mV, sigma_mV, peak_uA)
        i += rng.uniform(-0.12, 0.12)
        currents.append(round(i, 4))
    return currents


def synthesize_cottrell_amperometry(duration_s, rate_hz=20.0, i0_uA=12.0, seed=13):
    """计时电流：电容充电尖峰 + Cottrell 衰减 i(t) = i0·t^(-1/2) + 稳态。"""
    rng = random.Random(seed)
    t, c = [], []
    n = int(duration_s * rate_hz)
    for k in range(1, n + 1):
        ti = k / rate_hz
        ic = i0_uA / math.sqrt(max(ti, 1.0 / rate_hz)) * 0.35 + 1.2
        c.append(round(ic + rng.uniform(-0.15, 0.15), 4))
        t.append(round(ti, 3))
    return t, c


def _write_voltammetry_csv(path, experiment, voltage, current, extra_meta=None):
    """按本程序 CSV 格式写伏安数据（# 元数据 + voltage 表头 + 备注行）。"""
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        f.write("# experiment: {0}\n".format(experiment))
        for key, value in (extra_meta or {}).items():
            f.write("# {0}: {1}\n".format(key, value))
        f.write("voltage,{0} (合成数据)\n".format(experiment))
        f.write(" ,regression mock\n")
        for v, i in zip(voltage, current):
            f.write("{0},{1}\n".format(v, i))


def generate_test_csvs(output_dir="test_data"):
    """生成全套合成回归数据（脱机测试 GUI 打开/路由/分析/换算）。

    换算约定：IUPAC（氧化电流为正）。ASV 三浓度沿用假想灵敏度
    0.2 µA/ppb；CV 为 5 mM 铁氰化钾量级；AMP 为 Cottrell 衰减。
    """
    os.makedirs(output_dir, exist_ok=True)
    paths = []

    # ---- ASV 铅溶出（20/50/100 ppb，LS 溶出，峰约 -300 mV）----
    asv_voltages = list(range(-1500, 500 + 16, 16))
    for conc in (20, 50, 100):
        currents = synthesize_stripping_voltammogram(
            asv_voltages, peak_uA=0.2 * conc, seed=1000 + conc)
        path = os.path.join(output_dir, "LS-ASV_Pb_{0}ppb.csv".format(conc))
        _write_voltammetry_csv(path, "ASV", asv_voltages, currents, {
            "sweep_type": "LS", "low_voltage_mV": -1500, "high_voltage_mV": 500,
            "plate_volt_mV": -1500, "plate_time_s": 60,
            "conc_ppb": conc})
        paths.append(path)

    # ---- CV 3 圈（铁氰化钾，'Start' 三角）----
    from utils import make_cv_x_line
    cv_x = make_cv_x_line(-400, 700, 5, 'Start', 3)
    cv_i = synthesize_cv_voltammogram(cv_x, peak_uA=40.0)
    path = os.path.join(output_dir, "CV_K3FeCN6_3cycles.csv")
    _write_voltammetry_csv(path, "CV", cv_x, cv_i, {
        "start_voltage_mV": -400, "end_voltage_mV": 700,
        "sweep_rate_V/s": 0.1, "cycles": 3})
    paths.append(path)

    # ---- LSV ----
    lsv_x = list(range(-800, 300 + 4, 4))
    lsv_i = synthesize_stripping_voltammogram(lsv_x, peak_mV=100.0, peak_uA=18.0,
                                              sigma_mV=70.0, seed=21)
    path = os.path.join(output_dir, "LSV_ferri_1scan.csv")
    _write_voltammetry_csv(path, "LS", lsv_x, lsv_i, {
        "start_voltage_mV": -800, "end_voltage_mV": 300, "sweep_rate_V/s": 0.1})
    paths.append(path)

    # ---- SWV / DPV（阶梯基底电位 x + 差分峰）----
    for name, experiment, peak_mv, peak_ua, seed in (
            ("SWV_ferri_net.csv", "SWV", 120.0, 25.0, 31),
            ("DPV_ferri_pulse.csv", "DPV", 180.0, 20.0, 41)):
        from utils import make_dpv_x_line
        x = make_dpv_x_line(-600, 500, 5)
        rng = random.Random(seed)
        currents = []
        for v in x:
            base = 0.4 + 0.6 * (v + 600) / 1100.0
            currents.append(round(base + _gaussian(v, peak_mv, 80.0, peak_ua)
                                  + rng.uniform(-0.3, 0.3), 4))
        path = os.path.join(output_dir, name)
        _write_voltammetry_csv(path, experiment, x, currents, {
            "pulse_height_mV": 50, "pulse_inc_mV": 5, "pulse_period_ms": 100})
        paths.append(path)

    # ---- 计时电流（Cottrell）----
    t, c = synthesize_cottrell_amperometry(120.0)
    path = os.path.join(output_dir, "AMP_cottrell_120s.csv")
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        f.write("# experiment: AMP\n")
        f.write("# sampling_rate_Hz: 20\n")
        f.write("# voltage_mV: 500\n")
        f.write("time,current (合成数据)\n")
        for ti, ci in zip(t, c):
            f.write("{0},{1}\n".format(ti, ci))
    paths.append(path)

    return paths


if __name__ == '__main__':
    for p in generate_test_csvs():
        print("生成:", p)
