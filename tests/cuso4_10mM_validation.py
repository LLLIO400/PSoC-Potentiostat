# -*- coding: utf-8 -*-
"""10 mM CuSO4 真机验证(高浓度适配): CA 成核/生长、CV 定性、ASV 链路。
注意: 10 mM 下电流常超 ±100 uA 量程,削顶属预期;验证目标是链路与现象。输出存 实验记录/。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hardware_tests import HardwareTestError, TICK_5MS, _connect, _poll_done

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


def ca(seconds, mv, samples):
    """计时电流: M|<dac>|<samples>, X 停止, F0 取数"""
    dev.clear_in_buffer()
    dev.usb_write('T|01199'); time.sleep(0.15)
    dev.usb_write('D|{0:04d}'.format(2048 - mv)); time.sleep(0.3)
    dev.samples_to_smooth = 50
    dev.clear_in_buffer()
    dev.usb_write('M|{0:04d}|{1:04d}'.format(2048 - mv, samples))
    time.sleep(seconds)
    dev.usb_write('X'); time.sleep(0.5)
    dev.clear_in_buffer()
    dev.usb_write('F0'); time.sleep(1.0)
    packets = (samples + 1 + 31) // 32
    raw = [v for v in dev.get_data(packets) if -16384 < v < 16383]
    return dev.process_data(list(raw))


def cv_scan(tag="CV"):
    hold(-400, 3.0)
    dev.clear_in_buffer()
    dev.usb_write('S|2448|1448|{0:05d}|CS|1'.format(TICK_5MS))
    time.sleep(0.2)
    dev.usb_write('C|01199')
    time.sleep(0.1)
    dev.usb_write('R')
    wait_done(20)
    dev.usb_write('E0'); time.sleep(1.0)
    packets = (2001 + 1 + 31) // 32
    raw = [v for v in dev.get_data(packets) if -16384 < v < 16383]
    raw.pop(0)
    data = dev.process_data(list(raw))
    return data


def asv_chain(dep_s=30):
    """沉积 -1400 mV(dep_s, 搅拌) → 静置 10 s → SWV 溶出 -1400→+500"""
    dev.clear_in_buffer()
    dev.usb_write('O|1')
    dev.usb_write('D|3448')
    print("  恒电位沉积 %d s @ -1400 mV(搅拌中)..." % dep_s)
    time.sleep(dep_s)
    dev.usb_write('O|0')
    hold(-1400, 10)
    dev.clear_in_buffer()
    dev.usb_write('G|3448|1548|0005|0100|23999|LS')
    time.sleep(0.2)
    dev.usb_write('C|01199')   # 每点 2×1200 ticks=5 ms, 与 GUI 一致
    time.sleep(0.1)
    dev.usb_write('R')
    wait_done(30)
    dev.usb_write('E0'); time.sleep(1.0)
    expected = 2 * (1900 // 5 + 1)   # 762 样本
    packets = (expected + 1 + 31) // 32
    raw = [v for v in dev.get_data(packets) if -16384 < v < 16383][:expected]
    x = [-1400 + 5 * k for k in range(len(raw) // 2)]
    d = [(raw[2 * i + 1] - raw[2 * i]) * 0.0511 for i in range(len(raw) // 2)]
    return x, d


def main():
    global dev
    dev = _connect()
    if dev is None:
        print("未检测到设备")
        return 2
    print("设备已连接, 溶液: 10 mM CuSO4(高浓度: 削顶属预期)")
    t_start = time.time()

    # ============ 1) CA 成核/生长 30 s @ -500 mV(流式采集) ============
    print("\n== 1) CA 30 s @ -500 mV(流式采集, 成核/生长) ==")
    dev.clear_in_buffer()
    dev.usb_write('T|01199'); time.sleep(0.15)
    dev.usb_write('D|1548'); time.sleep(0.3)   # -500 mV(与用户上午 CA 同电位)
    dev.samples_to_smooth = 50
    dev.clear_in_buffer()
    dev.usb_write('M|1548|0200')
    ca_t, ca_data, t_ca = [], [], time.time()
    while time.time() - t_ca < 30:
        m = dev.usb_read_message()
        if m and 'Done' in str(m):
            dev.usb_write('F0'); time.sleep(0.05)
            raw = [v for v in dev.get_data(8) if -16384 < v < 16383]
            ca_data.extend(dev.process_data(list(raw)))
            ca_t.extend([time.time() - t_ca] * len(raw))
        time.sleep(0.05)
    dev.usb_write('X'); time.sleep(0.5)
    dev.clear_in_buffer()
    dev.samples_to_smooth = 1
    seg = [ca_data[max(0, i - 50):i] for i in range(50, len(ca_data), 50)]
    means = [sum(s_)/len(s_) if s_ else 0 for s_ in seg]
    print("  点数 %d, 分段均值 %s uA" % (len(ca_data), [round(m, 1) for m in means]))
    ok("CA 完整且有真实电流", len(ca_data) > 500 and
       (abs(means[0]) > 0.5 or abs(means[-1]) > 0.5),
       "首段 %.1f uA → 末段 %.1f uA, %d 点" % (means[0], means[-1], len(ca_data)))
    plots["CA 30s @ -500mV"] = (ca_t, ca_data)

    # ============ 2) CV 定性 ============
    print("\n== 2) CV -400→600(定性;阴极侧预期削顶) ==")
    cv = cv_scan()
    tri = [(2048 - 0 and 0) for _ in range(0)]  # placeholder
    x1 = []
    for i in range(len(cv)):
        x1.append(-400 + i if i <= 1000 else 600 - (i - 1000))
    sat = sum(1 for v in cv if abs(v) >= 95)
    print("  点数 %d, 范围 %.1f ~ %.1f uA, 削顶点 %d" %
          (len(cv), min(cv), max(cv), sat))
    ok("CV 完成", len(cv) == 2001, "%d 点, 削顶 %d 点(10 mM 属预期)" % (len(cv), sat))
    plots["CV(定性)"] = (x1, cv)

    # ============ 3) ASV 链路(短沉积) ============
    print("\n== 3) ASV 链路: 清洗 → 沉积 30 s → SWV 溶出(GUI 标准流程) ==")
    dev.usb_write('D|1248')                    # +800 mV 清洗 2 s(GUI 标准首步)
    time.sleep(2)
    dev.usb_write('O|1')
    dev.usb_write('D|3448')
    print("  恒电位沉积 30 s @ -1400 mV(搅拌中)...")
    time.sleep(30)
    dev.usb_write('O|0')
    hold(-1400, 10)
    last_err = None
    for attempt in range(4):
        if attempt == 1:
            # 恢复 1: H 唤醒 + X 复位
            dev.usb_write('H'); time.sleep(0.3)
            dev.usb_write('X'); time.sleep(0.5)
        elif attempt == 2:
            # 恢复 2: 小扫描自然收尾(解除可能的导出阻塞)
            dev.clear_in_buffer()
            dev.usb_write('S|2348|2448|{0:05d}|LS'.format(TICK_5MS))
            time.sleep(0.2)
            dev.usb_write('C|01199')
            time.sleep(0.1)
            dev.usb_write('R')
            try:
                wait_done(10)
            except HardwareTestError:
                pass
            dev.usb_write('E0'); time.sleep(0.5)
            dev.get_data(10)
        elif attempt == 3:
            # 恢复 3: 重连设备(已知有效的最终手段)
            print("  重连设备兜底...")
            new_dev = _connect()
            if new_dev is None:
                raise HardwareTestError("重连失败")
            dev = new_dev
        print("  溶出超时, 已执行恢复动作, 重试(第%d次)" % (attempt + 1))
        dev.clear_in_buffer()
        dev.usb_write('G|3448|1548|0005|0100|23999|LS')
        time.sleep(0.2)
        dev.usb_write('C|01199')
        time.sleep(0.1)
        dev.usb_write('R')
        try:
            wait_done(30)
            break
        except HardwareTestError as err:
            last_err = err
    else:
        raise last_err
    dev.usb_write('E0'); time.sleep(1.0)
    expected = 2 * (1900 // 5 + 1)   # 762 样本
    packets = (expected + 1 + 31) // 32
    raw = [v for v in dev.get_data(packets) if -16384 < v < 16383][:expected]
    x3 = [-1400 + 5 * k for k in range(len(raw) // 2)]
    asv = [(raw[2 * i + 1] - raw[2 * i]) * 0.0511 for i in range(len(raw) // 2)]
    pk = max(asv); ipk = asv.index(pk)
    print("  溶出 %d 点, 最大 %+.1f uA @ %d mV" % (len(asv), pk, x3[ipk]))
    ok("ASV 链路完成", len(asv) == 381,
       "%d 点, 最大 %+.1f uA @ %d mV(10 mM 溶出可能削顶)" % (len(asv), pk, x3[ipk]))
    plots["ASV 链路(沉积30s→SWV溶出)"] = (x3, asv)

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
        fig, axes = plt.subplots(1, 3, figsize=(16, 5), dpi=130)
        for ax, (title, (xx, yy)) in zip(axes, plots.items()):
            ax.plot(xx, yy, lw=0.9)
            ax.set_title(title, fontsize=10)
            ax.grid(alpha=0.3)
        axes[0].set_xlabel("t (s)"); axes[0].set_ylabel("uA")
        axes[1].set_xlabel("Potential (mV)"); axes[2].set_xlabel("Potential (mV)")
        fig.suptitle("10 mM CuSO4 | 2026-10-13(高浓度: 削顶属预期)", fontsize=12)
        fig.tight_layout()
        png = os.path.join(OUT, "CuSO4_10mM_验证_20261013.png")
        fig.savefig(png)
        print("图: %s" % png)
    except Exception as exc:
        print("出图失败: %s" % exc)

    import csv as _csv
    csv_path = os.path.join(OUT, "CuSO4_10mM_验证_20261013_汇总.csv")
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
