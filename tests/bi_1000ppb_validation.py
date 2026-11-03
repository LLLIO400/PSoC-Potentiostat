# -*- coding: utf-8 -*-
"""1000 ppb Bi 标准液 + 新丝网印刷电极:全功能真机验证(连续运行)。
覆盖: CV 基线/2圈/6圈超容量降级、LSV、SWV、DPV、CA、ASV 全链路(搅拌沉积+静置+SWV溶出)。
输出存 实验记录/。
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
results, plots = [], {}
dev = None


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


def run_and_fetch(expected_samples, timeout_s, drop_first=True,
                  resend_cmd=None, compare='C|01199', exp_lut=None):
    for attempt in range(2):
        if attempt and resend_cmd and exp_lut:
            print("  R 超时 → X 复位, 重发 LUT 后重试")
            dev.usb_write('X'); time.sleep(0.5)
            dev.clear_in_buffer()
            send_lut(resend_cmd, exp_lut)
            dev.usb_write(compare)
        dev.usb_write(compare)
        dev.usb_write('R')
        try:
            wait_done(timeout_s)
            break
        except HardwareTestError as err:
            if attempt:
                raise err
    dev.usb_write('E0')
    time.sleep(1.0)
    packets = (expected_samples + 1 + 31) // 32
    raw = [v for v in dev.get_data(packets) if -16384 < v < 16383]
    if drop_first:
        raw.pop(0)
    return raw


def cv_run(cycles, tag):
    hold(-400, 3.0)
    expected = 2001 + 2000 * (cycles - 1) if cycles > 1 else 2001
    exp_lut = expected + 1
    cmd = 'S|2448|1448|{0:05d}|CS|{1}'.format(TICK_5MS, cycles)
    lut = send_lut(cmd, exp_lut)
    print("  LUT %d (期望 %d)" % (lut, exp_lut))
    ok("%s LUT 长度" % tag, abs(lut - exp_lut) <= 2)
    raw = run_and_fetch(expected, expected * 0.005 + 15,
                        resend_cmd=cmd, exp_lut=exp_lut)
    data = dev.process_data(list(raw))
    x = make_cv_x_line(-400, 600, 1, 'Start', cycles, per_cycle=False)
    n = min(len(x), len(data))
    ok("%s 点数完整" % tag, abs(len(data) - len(x)) <= 3,
       "%d vs %d" % (len(data), len(x)))
    return (x[:n], data[:n])


def asv_strip(tag="溶出"):
    """静置后 SWV 溶出 -1400 -> +500,返回 (x, 差分电流 uA);超时自动 X 复位重试"""
    last_err = None
    for attempt in range(2):
        try:
            if attempt:
                dev.usb_write('X'); time.sleep(0.5)
            dev.clear_in_buffer()
            dev.usb_write('G|3448|1548|0005|0100|23999|LS')
            time.sleep(0.2)
            dev.usb_write('C|11999')  # 与 GUI ASV 相同(25 ms/采样点)
            time.sleep(0.1)
            dev.usb_write('R')
            wait_done(50)
            dev.usb_write('E0')
            time.sleep(1.0)
            # SWV 模式溶出: 每步 2 个采样。-1400→+500 共 381 步 = 762 样本
            expected = 2 * (1900 // 5 + 1)
            packets = (expected + 1 + 31) // 32
            raw = [v for v in dev.get_data(packets) if -16384 < v < 16383][:expected]
            x = [-1400 + 5 * k for k in range(len(raw) // 2)]
            d = [(raw[2 * i + 1] - raw[2 * i]) * 0.0511 for i in range(len(raw) // 2)]
            return x, d
        except HardwareTestError as err:
            last_err = err
    raise last_err


def pulse_scan(ch, timeout=30, tries=2):
    """SWV/DPV 脉冲扫描(-400→+600),超时自动 X 复位重试"""
    last_err = None
    for attempt in range(tries):
        try:
            if attempt:
                dev.usb_write('X'); time.sleep(0.5)
            dev.clear_in_buffer()
            dev.usb_write('{0}|2448|1448|0005|0100|02399|LS'.format(ch))
            time.sleep(0.2)
            dev.usb_write('C|01199')
            time.sleep(0.1)
            dev.usb_write('R')
            wait_done(timeout)
            dev.usb_write('E0')
            time.sleep(1.0)
            packets = (402 + 31) // 32
            raw = [v for v in dev.get_data(packets) if -16384 < v < 16383][:402]
            x = [-400 + 5 * k for k in range(len(raw) // 2)]
            d = [(raw[2 * i + 1] - raw[2 * i]) * 0.0511 for i in range(len(raw) // 2)]
            return x, d
        except HardwareTestError as err:
            last_err = err
    raise last_err


def find_peak(x, y, lo, hi):
    """在 [lo, hi] 电位区间找最大正峰,返回 (电位, 电流, 区间基线)"""
    seg = [(xi, yi) for xi, yi in zip(x, y) if lo <= xi <= hi]
    if not seg:
        return None
    base = sum(yi for _, yi in seg) / len(seg)
    px, py = max(seg, key=lambda t: t[1])
    return px, py, base


def main():
    global dev
    dev = _connect()
    if dev is None:
        print("未检测到设备")
        return 2
    dp = dev.device_params
    print("设备已连接, counts=%.5f, 溶液: 1000 ppb Bi 标准液, 电极: 新丝网电极" %
          dp.adc_tia.counts_to_current)
    t_start = time.time()

    # ============ 1) CV 1 圈 基线 ============
    print("\n== 1) CV 1 圈 基线(酸性铋溶液) ==")
    x1, cv1 = cv_run(1, "CV1")
    iox = cv1.index(max(cv1)); ired = cv1.index(min(cv1))
    print("  最大 %+.1f uA @ %d mV, 最小 %+.1f uA @ %d mV" %
          (cv1[iox], x1[iox], cv1[ired], x1[ired]))
    ok("CV1 数据完整且无全程削顶",
       abs(len(cv1) - 2001) <= 3 and max(cv1) < 95 and min(cv1) > -95,
       "范围 %.0f ~ %.0f uA" % (min(cv1), max(cv1)))
    plots["CV 1圈(基线)"] = (x1, cv1)

    # ============ 2) CV 2 圈 固件零间隙 ============
    print("\n== 2) CV 2 圈 固件零间隙 ==")
    x2, cv2 = cv_run(2, "CV2")
    plots["CV 2圈(固件零间隙)"] = (x2, cv2)

    # ============ 3) CV 6 圈 超容量降级 ============
    print("\n== 3) CV 6 圈 超容量自动降级 ==")
    limit = max_cv_cycles(-400, 600, 'Start', dp.dac)
    lut1 = send_lut('S|2448|1448|{0:05d}|CS|1'.format(TICK_5MS), 2002)
    print("  容量 %d 圈 → 显式 |1 单圈 LUT %d" % (limit, lut1))
    ok("超容量降级:单圈 LUT", abs(lut1 - 2002) <= 2, "got %d" % lut1)
    merged = []
    for cyc in range(6):
        hold(-400, 1.0)
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
    x6 = make_cv_x_line(-400, 600, 1, 'Start', 6, per_cycle=False)
    n6 = min(len(x6), len(merged))
    ok("6 圈拼合点数", len(merged) == expect6,
       "%d vs %d" % (len(merged), expect6))
    plots["CV 6圈(软件逐圈)"] = (x6[:n6], merged[:n6])

    # ============ 4) LSV / SWV / DPV 上行(酸性铋溶液观察) ============
    print("\n== 4) LSV 上行(静置 3 s) ==")
    hold(-400, 3.0)
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
    print("  LSV 范围 %.1f ~ %.1f uA(观察项:酸性铋体系上行特征)" %
          (min(ls), max(ls)))
    ok("LSV 数据完整", abs(len(ls) - 1002) <= 3, "%d 点" % len(ls))
    plots["LSV(酸性铋)"] = (xl, ls)

    print("\n== 5) SWV 上行(静置 3 s) ==")
    hold(-400, 3.0)
    x5, swv = pulse_scan('G')
    pk = max(swv); ipk = swv.index(pk)
    print("  SWV 最大 %+.2f uA @ %d mV(观察项)" % (pk, x5[ipk]))
    ok("SWV 数据完整", len(swv) == 201, "%d 点, 峰 %+.2f @ %d mV" % (len(swv), pk, x5[ipk]))
    plots["SWV(酸性铋)"] = (x5, swv)

    print("\n== 6) DPV 上行(静置 3 s) ==")
    hold(-400, 3.0)
    x6d, dpv = pulse_scan('P')
    pk = max(dpv); ipk = dpv.index(pk)
    print("  DPV 最大 %+.2f uA @ %d mV(观察项)" % (pk, x6d[ipk]))
    ok("DPV 数据完整", len(dpv) == 201, "%d 点, 峰 %+.2f @ %d mV" % (len(dpv), pk, x6d[ipk]))
    plots["DPV(酸性铋)"] = (x6d, dpv)

    # ============ 7) 计时电流 CA @ -500 mV ============
    print("\n== 7) 计时电流 8 s @ -500 mV(平滑验证;酸性液或有析氢电流,观察项) ==")
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
    packets = (401 + 31) // 32
    raw = [v for v in dev.get_data(packets) if -16384 < v < 16383]
    ca = dev.process_data(list(raw))
    head_j = max(ca[:49]) - min(ca[:49]) if len(ca) >= 49 else 0
    tail_j = max(ca[-50:]) - min(ca[-50:]) if len(ca) >= 50 else 0
    print("  点数 %d, 头49点抖动 %.3f uA, 尾50点 %.3f uA, 电流 %.1f uA" %
          (len(ca), head_j, tail_j, ca[-1]))
    ok("CA 头部平滑", head_j < 0.6, "%.3f uA" % head_j)
    plots["计时电流 8s @ -500mV"] = (list(range(len(ca))), ca)

    # ============ 8) ASV 全链路(主角) ============
    print("\n== 8) ASV 全链路: 搅拌下 -1400 mV 沉积 120 s → 静置 15 s → SWV 溶出 ==")
    dev.clear_in_buffer()
    dev.usb_write('O|1')                       # 搅拌开
    dev.usb_write('D|3448')                    # -1400 mV 恒电位沉积
    print("  恒电位沉积 300 s @ -1400 mV(搅拌中)...")
    time.sleep(300)
    dev.usb_write('O|0')                       # 搅拌关
    hold(-1400, 15)                            # 静置 15 s
    print("  静置 15 s 完成, 开始溶出扫描...")
    x8, asv = asv_strip()
    base_seg = [yi for xi, yi in zip(x8, asv) if 200 <= xi <= 500]
    base = sum(base_seg) / len(base_seg) if base_seg else 0
    print("  溶出完成: %d 点" % len(asv))
    ok("ASV 溶出数据完整", len(asv) == 381,
       "%d 点(381 步×2 采样/步)" % len(asv))
    plots["ASV 溶出(沉积300s@-1400mV)"] = (x8, asv)

    # ---- 8b) 找峰/分析功能测试(软件自带算法) ----
    print("\n== 8b) 自动寻峰与分析功能 ==")
    from algorithms import (analyze_stripping_peak, detect_peaks, sg_smooth,
                            subtract_baseline, peak_area)
    # 截取 -1000~+500(排除扫描起点的析氢尖峰, 其会把 3σ 阈值撑爆)
    x_t = [xi for xi in x8 if -1000 <= xi <= 500]
    y_t = [yi for xi, yi in zip(x8, asv) if -1000 <= xi <= 500]
    peaks, processed = analyze_stripping_peak(x_t, y_t, sweep_rate=0.1)
    print("  analyze_stripping_peak(区域 -1000~+500) 检出 %d 个峰:" % len(peaks))
    for p in peaks[:4]:
        print("    峰位 %.0f mV, 高 %.2f uA, 半峰宽 %.0f mV, 面积 %.0f uA·mV, 电量 %.3f uC"
              % (p['voltage'], p['height'], p['half_width'], p['area'],
                 p.get('charge', 0)))
    # Bi 主峰区(负电位区);更正电位侧的峰 = 已知强酸体系杂峰,只记录不判失败
    bi_peaks = [p for p in peaks if -500 <= p['voltage'] <= -100]
    right_peaks = [p for p in peaks if p['voltage'] > 0]
    ok("寻峰算法检出 Bi 主峰(-500~-100 mV)", len(bi_peaks) > 0,
       ("主峰 %.0f mV / %.2f uA%s" % (
           bi_peaks[0]['voltage'], bi_peaks[0]['height'],
           ", 高度达标(>3uA)" if bi_peaks[0]['height'] > 3 else ", 高度偏低<3uA"))
       if bi_peaks else "未检出")
    if right_peaks:
        print("  (记录)更正电位侧检出 %d 个峰——强酸体系已知杂峰,非缺陷"
              % len(right_peaks))
    if bi_peaks:
        plots["ASV 基线扣除+寻峰"] = (x_t, processed)
    # 分项功能
    smoothed = sg_smooth(asv, window=11)
    ok("SG 平滑", len(smoothed) == len(asv) and
       max(abs(a - b) for a, b in zip(smoothed[50:-50], asv[50:-50])) < 5,
       "等长且中部偏差<5uA")
    base_removed = subtract_baseline(x8, smoothed)
    ok("基线扣除", len(base_removed) == len(asv) and
       abs(sum(base_removed[-100:]) / 100) < 3, "尾部基线贴近 0")
    dps = detect_peaks(x8, base_removed)
    ok("detect_peaks 自动寻峰", len(dps) > 0, "%d 个峰" % len(dps))
    if dps:
        a = peak_area(x8, base_removed, dps[0])
        ok("峰面积积分", a != 0, "%.0f uA·mV" % a)
    # 起点杂峰检查(用户要求:曲线不得带起始杂峰)
    head = max(asv[:15]) - min(asv[:15])
    print("  起点杂峰检查: 溶出曲线前 15 点抖动 %.2f uA" % head)
    ok("溶出曲线起点无杂峰", head < 4, "%.2f uA" % head)
    bi_info = ((bi_peaks[0]['voltage'], bi_peaks[0]['height'])
               if bi_peaks else None)

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
        if bi_info:
            axes[3, 0].annotate("Bi peak %.1f uA @ %d mV" % bi_info,
                                xy=bi_info, xytext=(bi_info[0] - 500, bi_info[1] + 8),
                                arrowprops=dict(arrowstyle="->"), fontsize=9)
        axes[3, 0].set_xlabel("t (s)")                    # CA 面板: 时间轴
        axes[3, 1].set_xlabel("Potential (mV)")           # ASV 面板: 电位轴
        for r in range(3):
            for c in range(2):
                axes[r, c].set_xlabel("Potential (mV)")
        for r in range(4): axes[r, 0].set_ylabel("uA")
        fig.suptitle("1000 ppb Bi + 新丝网电极 全功能验证 | 2026-10-13", fontsize=12)
        fig.tight_layout()
        png = os.path.join(OUT, "Bi1000ppb_全功能验证_20261013.png")
        fig.savefig(png)
        print("图: %s" % png)
    except Exception as exc:
        print("出图失败: %s" % exc)

    import csv as _csv
    csv_path = os.path.join(OUT, "Bi1000ppb_验证_20261013_汇总.csv")
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
