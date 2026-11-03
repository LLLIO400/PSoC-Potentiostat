# -*- coding: utf-8 -*-
# Copyright (c) 2026 Zhijian Yi (Guangdong, China) — PSoC-Potentiostat-YI-1.0
"""真机验证：多圈固件补丁刷入后的 LUT 连续循环路径。

预期行为（区别于旧固件的 GUI 逐圈调度）：
  S|start|end|divider|CS|3 → 'g' 回读 LUT 长度 ≈ 3 圈期望值（6002）
  → 单次 'R' 连续跑完 3 圈（约 30 s，零间隙）→ Done → E0 一次抓全
用法：python tests/verify_patched_multicycle.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from comm import COMPLETE_MESSAGE
from config import DeviceParameters, max_cv_cycles
from hardware_tests import HardwareTestError, TICK_5MS, _connect, _poll_done
from utils import make_cv_x_line, cv_lut_length

START_MV, END_MV = -400, 600
CYCLES = 3  # 超出容量时自动按 LUT 容量封顶（与 GUI 同规则）


def main():
    dev = _connect()
    if dev is None:
        print("未检测到设备")
        return 2
    print("设备已连接, counts_to_current = %.5f"
          % dev.device_params.adc_tia.counts_to_current)

    start_dac = dev.format_voltage(START_MV)[1]
    end_dac = dev.format_voltage(END_MV)[1]
    limit = max_cv_cycles(START_MV, END_MV, 'Start', dev.device_params.dac)
    n = min(CYCLES, limit)
    print("本范围 LUT 容量上限 %d 圈 → 用 %d 圈验证" % (limit, n))

    dev.clear_in_buffer()
    dev.usb_write("S|{0:04d}|{1:04d}|{2:05d}|CS|{3}".format(
        start_dac, end_dac, TICK_5MS, n))
    time.sleep(0.2)
    dev.usb_write('C|{0:05d}'.format(TICK_5MS // 2))
    time.sleep(0.1)

    expected_n, expected_1 = cv_lut_length(start_dac, end_dac, 'Start', n)
    got = dev.get_lut_length()
    print("LUT 探测: got %s（GUI 公式期望 %d，单圈 %d）" % (got, expected_n, expected_1))
    if got is None or abs(got - expected_n) > 2:
        raise HardwareTestError(
            "LUT 长度 %s 与 %d 圈期望值 %d 不符 —— 多圈补丁未生效？" % (got, n, expected_n))
    print("√ 多圈 LUT 已生成（固件补丁生效）")

    dev.clear_in_buffer()
    t0 = time.time()
    dev.usb_write('R')
    ok, _ = _poll_done(dev, timeout_s=45)
    elapsed = time.time() - t0
    if not ok:
        raise HardwareTestError("45 秒内未收到 Done")
    print("√ 单次 R 连续跑完 %d 圈: Done @ %.1f s（旧固件逐圈调度同参数约 33 s+圈间停顿）"
          % (CYCLES, elapsed))

    dev.usb_write('E0')
    time.sleep(0.1)
    raw = dev.get_data(int(expected_n * 2 / 64) + 3)
    raw.pop(0)  # 首点上电残留，与 GUI 同规则丢弃
    data = dev.process_data(raw)
    print("数据: 原始 %d 样本, 转换 %d 点, I 范围 %.2f ~ %.2f µA"
          % (len(raw), len(data), min(data), max(data)))
    x = make_cv_x_line(START_MV, END_MV, 1, "Start", n, per_cycle=False)
    if abs(len(data) - len(x)) > 5:
        raise HardwareTestError("点数 %d 偏离期望 %d 过多" % (len(data), len(x)))
    if max(data) - min(data) < 5.0:
        raise HardwareTestError("电流摆幅 < 5 µA，不像铁氰化钾 CV")

    n_pts = min(len(x), len(data))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(os.path.join(_ROOT, "实验记录"), exist_ok=True)
    csv_path = os.path.join(_ROOT, "实验记录", "verify_patched_%s.csv" % stamp)
    with open(csv_path, "w", encoding="utf-8-sig") as f:
        f.write("# experiment: CV (patched fw LUT multi-cycle)\n")
        f.write("# start_voltage_mV: %d\n# end_voltage_mV: %d\n"
                % (START_MV, END_MV))
        f.write("# cycles: %d (firmware LUT loop, single R)\n" % CYCLES)
        f.write("voltage_mV,current_uA\n")
        for i in range(n):
            f.write("%s,%s\n" % (x[i], data[i]))
    print("CSV: %s" % csv_path)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 6), dpi=150)
        ax.plot(x[:n], data[:n], lw=1.0)
        ax.set_xlabel("Potential (mV)")
        ax.set_ylabel("Current (uA)")
        ax.set_title("Patched fw: %d-cycle CV in ONE run (zero-gap LUT loop)"
                     % n)
        ax.grid(alpha=0.3)
        png = os.path.join(_ROOT, "实验记录", "verify_patched_%s.png" % stamp)
        fig.tight_layout()
        fig.savefig(png)
        print("图: %s" % png)
    except Exception as exc:
        print("出图失败（不影响判定）: %s" % exc)

    print("PASS: 多圈固件全部验证通过（LUT 长度 / 单次连续运行 / 数据完整）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except HardwareTestError as exc:
        print("FAIL: %s" % exc)
        sys.exit(1)
