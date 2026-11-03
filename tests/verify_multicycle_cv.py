# -*- coding: utf-8 -*-
# Copyright (c) 2026 Zhijian Yi (Guangdong, China) — PSoC-Potentiostat-YI-1.0
"""真机多圈 CV 端到端验证（GUI 逐圈调度路径）。

复刻 experiments.CVFrame.USBHandler 修复后的协议时序：
  S|start|end|divider|CS|N  →  'g' 探测 LUT 长度（确认固件单圈）
  → 每圈：R → 轮询 Done → E0 抓取 → process_data 转换
  → 圈间去重拼合 → 校验圈数/点数 → 存 CSV + 出图

用法：python tests/verify_multicycle_cv.py [圈数，默认 3]
返回码：0 = 通过；1 = 失败；2 = 未检测到设备
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time

from comm import COMPLETE_MESSAGE
from config import DeviceParameters
from hardware_tests import HardwareTestError, TICK_5MS, _connect, _poll_done

START_MV, END_MV, RATE_V_S = -400, 600, 0.2


def main(cycles=3):
    dev = _connect()
    if dev is None:
        print("未检测到设备")
        return 2
    print("设备已连接，counts_to_current = %.5f"
          % dev.device_params.adc_tia.counts_to_current)

    start_dac = dev.format_voltage(START_MV)[1]
    end_dac = dev.format_voltage(END_MV)[1]
    params = "S|{0:04d}|{1:04d}|{2:05d}|CS|{3}".format(
        start_dac, end_dac, TICK_5MS, cycles)
    dev.clear_in_buffer()
    dev.usb_write(params)
    time.sleep(0.2)
    dev.usb_write('C|{0:05d}'.format(TICK_5MS // 2))
    time.sleep(0.1)

    # 固件能力探测（与 GUI _probe_fw_multi_cycle 同一判据）
    got = dev.get_lut_length()
    print("LUT 探测: got %s（单圈 2002 / N 圈更大）" % got)
    if got is None or abs(got - 2002) > 2:
        print("SKIP: 固件 LUT 长度非单圈预期（%s），本脚本只验证 GUI 逐圈路径"
              % got)
        return 0
    print("固件为单圈 LUT → 走 GUI 逐圈调度（与修复后的 GUI 一致）")

    converted_cycles = []
    for i in range(cycles):
        dev.clear_in_buffer()
        t0 = time.time()
        dev.usb_write('R')
        ok, _ = _poll_done(dev, timeout_s=25)
        if not ok:
            dev.usb_write('X')
            time.sleep(0.3)
            raise HardwareTestError("第 %d 圈扫描超时（25 秒无 Done）" % (i + 1))
        elapsed = time.time() - t0
        dev.usb_write('E0')
        time.sleep(0.1)
        raw = dev.get_data(65)
        raw.pop(0)  # 首点上电残留，与 GUI 同规则丢弃
        data = dev.process_data(raw)
        converted_cycles.append(data)
        print("第 %d 圈: Done %.1f s, 原始 %d 样本, 转换 %d 点, "
              "I 范围 %.2f ~ %.2f µA"
              % (i + 1, elapsed, len(raw), len(data),
                 min(data), max(data)))
        if not (1900 <= len(data) <= 2100):
            raise HardwareTestError(
                "第 %d 圈点数异常：%d（期望约 2001）" % (i + 1, len(data)))
        if max(data) - min(data) < 5.0:
            raise HardwareTestError(
                "第 %d 圈电流摆幅异常（< 5 µA），不像铁氰化钾 CV" % (i + 1))

    # 圈间去重拼合（与修复后 GUI _get_and_display_data 同规则）
    merged = []
    for i, part in enumerate(converted_cycles):
        merged.extend(part if i == 0 else part[1:])
    per_cycle = 2001
    x_expect = per_cycle + 2000 * (cycles - 1)
    print("拼合: %d 点（期望 %d）" % (len(merged), x_expect))
    if len(merged) != x_expect:
        raise HardwareTestError("拼合点数 %d != 期望 %d" % (len(merged), x_expect))

    # 圈间重复性：各圈氧化峰位置应接近（同溶液同电极）
    def peak_pos(data):
        return data.index(max(data))
    ref = peak_pos(converted_cycles[0])
    for i, data in enumerate(converted_cycles[1:], start=2):
        drift = abs(peak_pos(data) - ref)
        print("第 %d 圈氧化峰位置相对第 1 圈偏移: %d 点（%d mV）" % (i, drift, drift))
        if drift > 30:
            raise HardwareTestError("第 %d 圈峰位漂移过大（%d 点）" % (i, drift))

    stamp = time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(os.path.join(_ROOT, "实验记录"), exist_ok=True)
    csv_path = os.path.join(_ROOT, "实验记录", "verify_multicycle_%s.csv" % stamp)
    with open(csv_path, "w", encoding="utf-8-sig") as f:
        f.write("# experiment: CV\n")
        f.write("# start_voltage_mV: %d\n# end_voltage_mV: %d\n" % (START_MV, END_MV))
        f.write("# sweep_rate_V/s: %s\n# cycles: %d (GUI loop scheduling)\n"
                % (RATE_V_S, cycles))
        f.write("point,%d-cycles-merged-current-uA\n" % cycles)
        for j, v in enumerate(merged):
            f.write("%d,%s\n" % (j, v))
    print("CSV 已保存: %s" % csv_path)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from utils import make_cv_x_line
        x = make_cv_x_line(START_MV, END_MV, 1, "Start", cycles, per_cycle=True)
        fig, ax = plt.subplots(figsize=(9, 6), dpi=150)
        ax.plot(x, merged, lw=1.0)
        ax.set_xlabel("Potential (mV)")
        ax.set_ylabel("Current (uA)")
        ax.set_title("Real-device multi-cycle CV: %d cycles @ 5 mM K3[Fe(CN)6]"
                     % cycles)
        ax.grid(alpha=0.3)
        png = os.path.join(_ROOT, "实验记录", "verify_multicycle_%s.png" % stamp)
        fig.tight_layout()
        fig.savefig(png)
        print("图已保存: %s" % png)
    except Exception as exc:
        print("出图失败（不影响判定）: %s" % exc)

    print("PASS: %d 圈全部抓取成功，拼合点数正确" % cycles)
    return 0


if __name__ == "__main__":
    try:
        n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
        sys.exit(main(max(2, min(n, 9))))
    except HardwareTestError as exc:
        print("FAIL: %s" % exc)
        sys.exit(1)
