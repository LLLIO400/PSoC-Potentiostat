# -*- coding: utf-8 -*-
"""全技术链路真机验证(连续运行,中途不换电极/溶液)。
试剂: 5 mM K3[Fe(CN)6] + 0.1 M KCl。覆盖: CV 基线/2圈零间隙/6圈超容量降级、
LSV、SWV、DPV、计时电流、ASV 沉积-溶出链路(含搅拌)。输出存 实验记录/。

关键经验(勿回退):
  1. E0 取数包数必须精确 = ceil((样本数+1)/32),多要包会卡住设备导出;
  2. E0 之后设备仍在收尾,等 1.0 s 再发下一条命令,否则命令可能丢失;
  3. 超容量时发显式 '|1'(固件 OUT 缓冲残留会让缺省尾缀继承旧圈数);
  4. LUT 长度探测不匹配 → X 复位解除阻塞后重发。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DeviceParameters, max_cv_cycles
from hardware_tests import HardwareTestError, TICK_5MS, _connect, _poll_done
from utils import make_cv_x_line, cv_lut_length

OUT = os.path.join(_ROOT, "实验记录")
os.makedirs(OUT, exist_ok=True)
START_MV, END_MV = -400, 600
results, plots = [], {}
dev = None   # 会话设备(main 里赋值)


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))


def wait_done(timeout_s):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        m = dev.usb_read_message()
        if m and 'Done' in str(m):
            return time.time() - t0
        time.sleep(0.05)
    raise HardwareTestError("超时未收到 Done")


def hold(mv, seconds):
    dev.usb_write('D|{0:04d}'.format(2048 - mv))
    time.sleep(seconds)


def send_lut(cmd, expected_len, tries=3):
    """发 LUT 命令并探测长度;不匹配先清缓冲重发,末次 X 解锁后重试"""
    global dev
    for attempt in range(tries):
        dev.clear_in_buffer()
        dev.usb_write(cmd)
        time.sleep(0.4 if attempt else 0.2)
        got = dev.get_lut_length()
        if got is not None and abs(got - expected_len) <= 2:
            return got
        print("  LUT 未生效(第%d次): got %s, 期望 %d" % (attempt + 1, got, expected_len))
        if attempt == tries - 2:
            dev.usb_write('X')
            time.sleep(0.5)
    raise HardwareTestError("LUT 长度 %s != 期望 %d" % (got, expected_len))


def run_and_fetch(expected_samples, timeout_s, drop_first=True):
    dev.usb_write('R')
    wait_done(timeout_s)
    dev.usb_write('E0')
    time.sleep(1.0)                      # 经验 2: 等设备收尾
    packets = (expected_samples + 1 + 31) // 32   # 经验 1: 精确包数
    raw = [v for v in dev.get_data(packets) if -16384 < v < 16383]
    if drop_first:
        raw.pop(0)
    return raw


def cv_run(cycles, tag):
    hold(START_MV, 3.0)
    expected = 2001 + 2000 * (cycles - 1) if cycles > 1 else 2001
    exp_lut = expected + 1
    lut = send_lut('S|2448|1448|{0:05d}|CS|{1}'.format(TICK_5MS, cycles), exp_lut)
    print("  LUT %d (期望 %d)" % (lut, exp_lut))
    ok("%s LUT 长度" % tag, abs(lut - exp_lut) <= 2)
    t0 = time.time()
    raw = run_and_fetch(expected, timeout_s=expected * 0.005 + 15)
    el = time.time() - t0
    data = dev.process_data(list(raw))
    x = make_cv_x_line(START_MV, END_MV, 1, 'Start', cycles, per_cycle=False)
    n = min(len(x), len(data))
    print("  R 后 %.1f s 完成, %d 点" % (el, len(data)))
    ok("%s 点数完整" % tag, abs(len(data) - len(x)) <= 3,
       "%d vs %d" % (len(data), len(x)))
    return (x[:n], data[:n])


def pulse(cmd_char, tag):
    hold(START_MV, 3.0)
    dev.clear_in_buffer()
    dev.usb_write('{0}|2448|1448|0005|0100|02399|LS'.format(cmd_char))
    time.sleep(0.2)
    dev.usb_write('C|{0:05d}'.format(TICK_5MS // 2))
    time.sleep(0.1)
    dev.usb_write('R')
    wait_done(30)
    dev.usb_write('E0')
    time.sleep(1.0)
    raw = [v for v in dev.get_data(13) if -16384 < v < 16383][:402]
    x = [-400 + 5 * k for k in range(len(raw) // 2)]
    d = [raw[2 * i + 1] - raw[2 * i] for i in range(len(raw) // 2)]
    return x, d


def main():
    global dev
    dev = _connect()
    if dev is None:
        print("未检测到设备")
        return 2
    dp = dev.device_params
    print("设备已连接, counts=%.5f, 试剂: 5 mM K3[Fe(CN)6]/0.1M KCl" %
          dp.adc_tia.counts_to_current)
    t_start = time.time()

    # ============ 1) CV 1 圈 基线 ============
    print("\n== 1) CV 1 圈 基线 ==")
    x1, cv1 = cv_run(1, "CV1")
    iox = cv1.index(max(cv1)); ired = cv1.index(min(cv1))
    print("  氧化峰 %+.1f uA @ %d mV, 还原峰 %+.1f uA @ %d mV"
          % (cv1[iox], x1[iox], cv1[ired], x1[ired]))
    ok("CV1 氧化峰向上", cv1[iox] > 5 and -400 <= x1[iox] <= 600,
       "%+.1f uA @ %d mV" % (cv1[iox], x1[iox]))
    ok("CV1 还原峰向下", cv1[ired] < -5, "%+.1f uA" % cv1[ired])
    plots["CV 1圈(基线)"] = (x1, cv1)

    # ============ 2) CV 2 圈 固件零间隙 ============
    print("\n== 2) CV 2 圈 固件零间隙 ==")
    x2, cv2 = cv_run(2, "CV2")
    plots["CV 2圈(固件零间隙)"] = (x2, cv2)

    # ============ 3) CV 6 圈 超容量降级 ============
    print("\n== 3) CV 6 圈 超容量自动降级 ==")
    limit = max_cv_cycles(START_MV, END_MV, 'Start', dp.dac)
    lut1 = send_lut('S|2448|1448|{0:05d}|CS|1'.format(TICK_5MS), 2002)
    print("  容量 %d 圈 → 显式 |1 单圈 LUT %d" % (limit, lut1))
    ok("超容量降级:单圈 LUT", abs(lut1 - 2002) <= 2, "got %d" % lut1)
    merged = []
    for cyc in range(6):
        hold(START_MV, 1.0)
        dev.clear_in_buffer()
        dev.usb_write('R')
        wait_done(20)
        dev.usb_write('E0')
        time.sleep(1.0)
        packets = (2001 + 1 + 31) // 32
        raw = [v for v in dev.get_data(packets) if -16384 < v < 16383]
        raw.pop(0)
        cvc = dev.process_data(list(raw))
        merged.extend(cvc if cyc == 0 else cvc[1:])
        print("  圈 %d/6 完成 (累计 %d 点)" % (cyc + 1, len(merged)))
    expect6 = 2001 + 2000 * 5
    x6 = make_cv_x_line(START_MV, END_MV, 1, 'Start', 6, per_cycle=False)
    n6 = min(len(x6), len(merged))
    ok("6 圈拼合点数", len(merged) == expect6,
       "%d vs %d" % (len(merged), expect6))
    plots["CV 6圈(软件逐圈)"] = (x6[:n6], merged[:n6])

    # ============ 4) LSV ============
    print("\n== 4) LSV 上行(静置 3 s) ==")
    hold(START_MV, 3.0)
    dev.clear_in_buffer()
    dev.usb_write('S|2448|1448|{0:05d}|LS'.format(TICK_5MS))
    time.sleep(0.2)
    dev.usb_write('C|{0:05d}'.format(TICK_5MS // 2))
    time.sleep(0.1)
    dev.usb_write('R')
    wait_done(20)
    dev.usb_write('E0')
    time.sleep(1.0)
    packets = (1002 + 31) // 32
    raw = [v for v in dev.get_data(packets) if -16384 < v < 16383]
    raw.pop(0)
    ls = dev.process_data(list(raw))
    xl = [-400 + 5 * i for i in range(len(ls))]
    wave = [v for xi, v in zip(xl, ls) if 200 <= xi <= 500]
    ok("LSV 氧化波向上", bool(wave) and max(wave) > 5,
       "max %.1f uA" % (max(wave) if wave else 0))
    plots["LSV(静置3s)"] = (xl, ls)

    # ============ 5) SWV ============
    print("\n== 5) SWV 上行(静置 3 s) ==")
    x5, swv = pulse('G', "SWV")
    pk = max(swv); ipk = swv.index(pk)
    print("  氧化峰 %+.2f uA @ %d mV" % (pk * 0.0511, x5[ipk]))
    ok("SWV 氧化峰向上", pk > 0 and 100 <= x5[ipk] <= 500,
       "%+.2f uA @ %d mV" % (pk * 0.0511, x5[ipk]))
    plots["SWV(静置3s)"] = (x5, [v * 0.0511 for v in swv])

    # ============ 6) DPV ============
    print("\n== 6) DPV 上行(静置 3 s) ==")
    x6d, dpv = pulse('P', "DPV")
    pk = max(dpv); ipk = dpv.index(pk)
    print("  氧化峰 %+.2f uA @ %d mV" % (pk * 0.0511, x6d[ipk]))
    ok("DPV 氧化峰向上", pk > 0 and 100 <= x6d[ipk] <= 500,
       "%+.2f uA @ %d mV" % (pk * 0.0511, x6d[ipk]))
    plots["DPV(静置3s)"] = (x6d, [v * 0.0511 for v in dpv])

    # ============ 7) 计时电流 CA @ -500 mV ============
    print("\n== 7) 计时电流 8 s @ -500 mV ==")
    dev.clear_in_buffer()
    dev.usb_write('T|01199'); time.sleep(0.15)
    dev.usb_write('D|1548'); time.sleep(0.3)
    dev.samples_to_smooth = 50
    dev.clear_in_buffer()
    dev.usb_write('M|1548|{0:04d}'.format(400))
    time.sleep(8.0)
    dev.usb_write('X'); time.sleep(0.5)
    dev.clear_in_buffer()
    dev.usb_write('F0'); time.sleep(1.0)
    raw = [v for v in dev.get_data(40) if -16384 < v < 16383]
    ca = dev.process_data(list(raw))
    head_j = max(ca[:49]) - min(ca[:49]) if len(ca) >= 49 else 0
    tail_j = max(ca[-50:]) - min(ca[-50:]) if len(ca) >= 50 else 0
    print("  点数 %d, 头49点抖动 %.3f uA, 尾50点 %.3f uA"
          % (len(ca), head_j, tail_j))
    ok("CA 头部平滑", head_j < 0.6, "%.3f uA" % head_j)
    plots["计时电流 8s"] = (list(range(len(ca))), ca)

    # ============ 8) ASV 链路 ============
    print("\n== 8) ASV 链路(沉积 -200 mV 30 s 含搅拌) ==")
    dev.clear_in_buffer()
    dev.usb_write('O|1')
    dev.usb_write('D|{0:04d}'.format(2048 - 200))
    print("  恒电位沉积 30 s @ -200 mV(搅拌中)...")
    time.sleep(30)
    dev.usb_write('O|0')
    time.sleep(10)
    x8, asv = pulse('G', "ASV")
    pk = max(asv); ipk = asv.index(pk)
    wave = [v for xi, v in zip(x8, asv) if 0 <= xi <= 450]
    print("  溶出完成, 波区(0~+450)均值 %+.2f uA, 最大 %+.2f uA @ %d mV"
          % (sum(wave) / len(wave) * 0.0511, pk * 0.0511, x8[ipk]))
    ok("ASV 链路完成且溶出区呈阳极特征",
       sum(wave) / len(wave) > -5 and pk > 0,
       "均值 %+.2f uA, 峰 %+.2f uA @ %d mV"
       % (sum(wave) / len(wave) * 0.0511, pk * 0.0511, x8[ipk]))
    plots["ASV 链路(沉积30s→SWV溶出)"] = (x8, [v * 0.0511 for v in asv])

    # ============ 汇总 ============
    fails = [n for n, c, _ in results if not c]
    print("\n======== 汇总: %d/%d PASS | 总用时 %.0f s ========" %
          (len(results) - len(fails), len(results), time.time() - t_start))
    for n, c, d in results:
        print("  [%s] %s %s" % ("PASS" if c else "FAIL", n, d))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(4, 2, figsize=(14, 16), dpi=130)
        for ax, (title, (xx, yy)) in zip(axes.ravel(), plots.items()):
            ax.plot(xx, yy, lw=0.9)
            ax.set_title(title, fontsize=10)
            ax.grid(alpha=0.3)
        axes[3, 0].set_xlabel("t (s)"); axes[3, 1].set_xlabel("t (s)")
        for r in range(3):
            for c in range(2):
                axes[r, c].set_xlabel("Potential (mV)")
        for r in range(4): axes[r, 0].set_ylabel("uA")
        fig.suptitle("全技术链路真机验证 | 5 mM K3[Fe(CN)6]/0.1M KCl | 2026-10-13",
                     fontsize=12)
        fig.tight_layout()
        png = os.path.join(OUT, "全技术链路验证_20261013.png")
        fig.savefig(png)
        print("图: %s" % png)
    except Exception as exc:
        print("出图失败: %s" % exc)

    import csv as _csv
    csv_path = os.path.join(OUT, "全技术链路验证_20261013_汇总.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = _csv.writer(f)
        w.writerow(["检查项", "结果", "详情"])
        for n, c, d in results:
            w.writerow([n, "PASS" if c else "FAIL", d])
    print("汇总 CSV: %s" % csv_path)
    dev.usb_write('X')
    return 0 if not fails else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except HardwareTestError as exc:
        print("FAIL: %s" % exc)
        sys.exit(1)
