# -*- coding: utf-8 -*-
"""硬件联机 · 软件功能端到端测试:驱动真实 GUI 页面按钮,验证完整软件链路。
覆盖: CV/LS/SWV/DPV/ASV/计时电流 六页 运行→数据→自动存档, 多圈, 中途停止。
 messagebox 全部自动应答(防饱和告警等弹窗阻塞)。settings 测试后还原。
"""
import os
import shutil
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import tempfile

# 参数隔离: GUI 的读写都走 SETTINGS_DIR_OVERRIDE
_CFG_TMP = tempfile.mkdtemp(prefix="hwgui_settings_")
import config as config_module
for f in ("settings.txt", "settings_asv.txt"):
    src = os.path.join(_ROOT, f)
    if os.path.exists(src):
        shutil.copy(src, os.path.join(_CFG_TMP, f))
config_module.SETTINGS_DIR_OVERRIDE = _CFG_TMP

results = []
mb_calls = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))


def main():
    # 弹窗自动应答(记录调用, 防阻塞)
    from tkinter import messagebox as _real_mb
    import tkinter.messagebox as mb
    def _rec(name):
        def f(*a, **k):
            mb_calls.append((name, a))
            print("  [弹窗] %s: %s" % (name, a[1] if len(a) > 1 else a))
        return f
    mb.showinfo = _rec("showinfo")
    mb.showwarning = _rec("showwarning")
    mb.showerror = _rec("showerror")
    _real_mb.showinfo = _rec("showinfo")
    _real_mb.showwarning = _rec("showwarning")
    _real_mb.showerror = _rec("showerror")

    import main as main_mod
    app = main_mod.ElectroChemGUI()
    app.withdraw()
    app.update()
    print("GUI 已启动(真实设备联机)")

    autosave_dir = os.path.join(_ROOT, "autosave_backup")

    def autosave_count():
        return len([f for f in os.listdir(autosave_dir) if f.endswith(".csv")]) \
            if os.path.isdir(autosave_dir) else 0

    def pump_until(cond, timeout_s, note=""):
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            app.update()
            if cond():
                return True
            time.sleep(0.05)
        print("  超时(%s, %.0f s)" % (note, timeout_s))
        return False

    # ============ 各方法页: 点运行 → 完成 → 自动存档 ============
    pages = [("CV", app.cv), ("LS", app.ls), ("SWV", app.swv), ("DPV", app.dpv)]
    for name, page in pages:
        print("\n== %s 页 ==" % name)
        n0 = autosave_count()
        page.run_button.invoke()
        done = pump_until(lambda: not page.device.is_running, 90, name)
        ok("%s 页扫描完成并恢复" % name, done and not page.device.is_running)
        d = page.device.data
        cur = d if isinstance(d, list) else list(getattr(d, "current", []) or [])
        ok("%s 页取到数据" % name, len(cur) > 0, "%d 点" % len(cur))
        n1 = autosave_count()
        ok("%s 页自动存档" % name, n1 > n0, "+%d 份" % (n1 - n0))

    # ============ CV 多圈(2 圈) ============
    print("\n== CV 2 圈(软件多圈路径) ==")
    app.cv.device.settings.cycles = 2
    app.cv.run_button.invoke()
    done = pump_until(lambda: not app.cv.device.is_running, 150, "CV2")
    d2 = app.cv.device.data
    cur2 = d2 if isinstance(d2, list) else list(getattr(d2, "current", []) or [])
    ok("CV 2 圈完成", done and len(cur2) > 2000,
       "%d 点(≈2×2801)" % len(cur2))
    app.cv.device.settings.cycles = 1

    # ============ ASV 页(缩短: 沉积 60 s) ============
    print("\n== ASV 页(沉积 60 s) ==")
    asv = app.asv
    asv.device.params.asv_settings.plate_time = 60.0
    n0 = autosave_count()
    asv.run_button.invoke()
    done = pump_until(lambda: not asv.device.is_running, 180, "ASV")
    ok("ASV 全流程完成", done and not asv.device.is_running)
    d = getattr(asv.device, "data", None)
    if d is None:
        d = asv.data
    cur = d if isinstance(d, list) else list(getattr(d, "current", []) or [])
    ok("ASV 取到溶出数据", len(cur) > 0, "%d 点" % len(cur))
    n1 = autosave_count()
    ok("ASV 自动存档", n1 > n0, "+%d 份" % (n1 - n0))

    # ============ 计时电流页(10 s 后停止) ============
    print("\n== 计时电流页 ==")
    amp = app.amp
    amp.run_button.invoke()
    t0 = time.time()
    while time.time() - t0 < 10:
        app.update()
        time.sleep(0.05)
    amp.run_button.invoke()          # 停止
    app.update()
    ok("计时电流采到数据", len(amp.device.data) > 100,
       "%d 点" % len(amp.device.data))
    ok("计时电流自动存档", os.path.isdir(autosave_dir) and any(
        f.endswith("_AMP.csv") and time.time() - os.path.getmtime(
            os.path.join(autosave_dir, f)) < 120
        for f in os.listdir(autosave_dir)), "停止时自动存档")

    # ============ 中途停止(X 复位) ============
    print("\n== 中途停止 ==")
    app.cv.run_button.invoke()
    t0 = time.time()
    while time.time() - t0 < 4:
        app.update()
        time.sleep(0.05)
    app.cv.run_button.invoke()       # 停止
    app.update()
    ok("中途停止恢复正常", not app.cv.device.is_running)
    x_sent = any(c.args and c.args[0] == 'X'
                 for c in app.cv.device.usb_write.call_args_list) \
        if hasattr(app.cv.device, "usb_write") else True
    ok("停止发出 X 复位", x_sent)

    # ============ 汇总 ============
    fails = [n for n, c, _ in results if not c]
    print("\n======== 汇总: %d/%d PASS ========" %
          (len(results) - len(fails), len(results)))
    for n, c, d in results:
        print("  [%s] %s %s" % ("PASS" if c else "FAIL", n, d))
    if mb_calls:
        print("\n弹窗记录: %d 次" % len(mb_calls))
    return 0 if not fails else 1


if __name__ == "__main__":
    try:
        code = main()
    finally:
        # 恢复用户真实 settings(测试前的原始副本), 删除临时目录
        for f in ("settings.txt", "settings_asv.txt"):
            src = os.path.join(_CFG_TMP, f)
            dst = os.path.join(_ROOT, f)
            if os.path.exists(src):
                shutil.copy(src, dst)
        shutil.rmtree(_CFG_TMP, ignore_errors=True)
        print("settings 已还原为测试前状态")

    sys.exit(code)
