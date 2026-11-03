# -*- coding: utf-8 -*-
# Copyright (c) 2026 Zhijian Yi (Guangdong, China) — PSoC-Potentiostat-YI-1.0
# Licensed under CC BY-SA 3.0 (same as the upstream PSoC-Potentiostat project)
"""PSoC-Potentiostat-YI-1.0 测试与诊断：环境诊断 + 全量测试，输出统一《test_report.txt》（检测报告，内容为中文）。

流程：
  一、环境诊断（Python 版本 / 依赖导入 / 核心文件语法 / import main 自检）
  二、全量测试（每个测试类独立子进程运行，单个卡死不影响其余）
  三、汇总写入 检测报告.txt（utf-8-sig，记事本可直接打开）

为什么每个测试类独立子进程：
1. 几十个 tk 窗口在同一进程反复创建销毁，容易内存累积崩溃/卡死；
2. 单个测试类卡死时只影响自己，能看清楚是谁卡的。

用法：python run_tests.py
返回码：0 = 诊断与测试全部通过；1 = 存在问题（详见报告）
"""
import os
import subprocess
import sys
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))   # tests/
ROOT_DIR = os.path.dirname(TESTS_DIR)                    # 项目根（GUI 运行目录）

REPORT_FILE = "test_report.txt"  # 文件名用英文，避免中文文件名在部分环境引发编码问题
CORE_FILES = ["main.py", "config.py", "comm.py", "experiments.py",
              "plotting.py", "dialogs.py", "utils.py", "i18n.py",
              "algorithms.py"]
DEPENDENCIES = ["serial", "matplotlib", "tkinter", "ttkbootstrap",
                "numpy", "scipy"]
TEST_TIMEOUT = 120  # 单个测试类超时秒数


def _version_of(module):
    if module == "tkinter":
        import tkinter
        return f"Tcl/Tk {tkinter.TkVersion}"
    mod = sys.modules.get(module) or __import__(module)
    return getattr(mod, "__version__", "（无版本号）")


def run_diagnostics(report):
    """环境诊断，返回是否存在问题。"""
    problems = []
    report.append("=" * 50)
    report.append("一、环境诊断")
    report.append("=" * 50)

    report.append(f"Python: {sys.version.split()[0]}  ({sys.executable})")
    report.append("")

    report.append("[依赖导入]")
    for dep in DEPENDENCIES:
        try:
            __import__(dep)
            report.append(f"  OK   {dep}  {_version_of(dep)}")
        except Exception as exc:
            problems.append(f"依赖 {dep} 导入失败")
            report.append(f"  FAIL {dep}: {exc}")
    report.append("")

    report.append("[核心文件语法]")
    for f in CORE_FILES:
        r = subprocess.run([sys.executable, "-m", "py_compile", f],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            report.append(f"  OK   {f}")
        else:
            problems.append(f"{f} 语法错误")
            report.append(f"  FAIL {f}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ''}")
    report.append("")

    report.append("[import main 自检（不打开窗口）]")
    r = subprocess.run([sys.executable, "-c", "import main"],
                       capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace")
    if r.returncode == 0:
        report.append("  OK   import main")
    else:
        problems.append("import main 失败")
        report.append(f"  FAIL import main: {r.stderr.strip()[-300:]}")
    report.append("")
    return problems


def list_classes():
    import test_refactored
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(test_refactored)
    return sorted({s._tests[0].__class__.__name__ for s in suite})


def _setup_paths():
    """测试文件在 tests/ 下、被测模块在根目录：
    本进程与子进程都把 tests/ 加入模块搜索路径，工作目录切到根目录
    （与 GUI 运行时一致：settings.txt、报告、日志都落在项目根）。"""
    sys.path.insert(0, TESTS_DIR)   # test_refactored / fake_device
    sys.path.insert(0, ROOT_DIR)    # 被测模块 config / comm / ...
    os.chdir(ROOT_DIR)


def run_tests(report):
    """逐类运行测试，返回失败类名列表。"""
    classes = list_classes()
    report.append("=" * 50)
    report.append(f"二、全量测试（共 {len(classes)} 个测试类）")
    report.append("=" * 50)
    print(f"共 {len(classes)} 个测试类，逐类独立运行……\n")
    failed = []
    # 子进程强制 UTF-8 IO：Windows 中文 locale 下子进程管道默认 GBK，
    # 打印 µ 等字符会在子进程里崩（真机踩坑）
    child_env = dict(os.environ, PYTHONIOENCODING="utf-8:replace",
                     PYTHONPATH=TESTS_DIR + os.pathsep +
                     os.environ.get("PYTHONPATH", ""))
    for i, cls in enumerate(classes, 1):
        try:
            r = subprocess.run(
                [sys.executable, "-m", "unittest", f"test_refactored.{cls}"],
                capture_output=True, text=True, timeout=TEST_TIMEOUT,
                encoding="utf-8", errors="replace", env=child_env)
            tail = (r.stdout + r.stderr).strip().splitlines()
            ok = r.returncode == 0 and any(l.startswith("OK") for l in tail)
            # 摘要跳过 tk 销毁噪音行：它是测试判 OK 之后的清理时序
            # 问题，不代表测试结果，但会把摘要顶掉（真机报告踩坑）。
            # "invalid command name" 是 matplotlib/tk 的 after_idle 绘制
            # 回调在部件销毁后迟到触发，同类噪音
            clean = [l for l in tail
                     if "bgerror" not in l
                     and "application has been destroyed" not in l
                     and "invalid command name" not in l]
            summary = clean[-1] if clean else (tail[-1] if tail else "(无输出)")
        except subprocess.TimeoutExpired:
            ok, summary = False, f"超时 {TEST_TIMEOUT} 秒（卡死）"
        status = "OK  " if ok else "FAIL"
        line = f"[{i:2d}/{len(classes)}] {status} {cls}: {summary}"
        print(line)
        report.append(line)
        if not ok:
            failed.append(cls)
            # 报告里附失败详情尾部，否则真机上只能看到 "FAILED (errors=1)"
            # 无法定位（真机踩坑：TestHardwareTestRobustness 挂了看不到原因）
            report.append("        --- 失败详情（输出尾部） ---")
            for l in tail[-15:]:
                report.append("        " + l)
    report.append("")
    return failed


def run_hardware(report):
    """真机实测（--hardware 时）：插着 PSoC 跑 A 档 9 项。"""
    import hardware_tests
    report.append("=" * 50)
    report.append("三、硬件实测（真机）")
    report.append("=" * 50)
    print("\n开始硬件实测（真机）……")
    # --hardware-fake 仅供沙盒自检：用假设备验证硬件测试脚本自身逻辑
    lines, failures, found = hardware_tests.run(
        make_fake="--hardware-fake" in sys.argv)
    report.extend(lines)
    if not found:
        report.append("（未检测到设备，已跳过）")
        print("未检测到 PSoC 设备，硬件实测跳过")
    report.append("")
    return failures if found else []


def main():
    _setup_paths()
    # Windows 中文控制台是 GBK，非常见字符（µ 等）会让 print 崩溃
    try:
        sys.stdout.reconfigure(errors='replace')
        sys.stderr.reconfigure(errors='replace')
    except Exception:
        pass
    with_hardware = "--hardware" in sys.argv
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    report = ["PSoC-Potentiostat-YI-1.0 检测报告", f"生成时间: {started}", ""]

    diag_problems = run_diagnostics(report)
    if diag_problems:
        print("环境诊断存在问题：")
        for p in diag_problems:
            print(f"  - {p}")
        print("仍继续运行测试……\n")
    else:
        print("环境诊断通过，开始全量测试……\n")

    failed = run_tests(report)

    hw_failed = []
    if with_hardware or "--hardware-fake" in sys.argv:
        hw_failed = run_hardware(report)
        with_hardware = True

    report.append("=" * 50)
    report.append("四、结论" if with_hardware else "三、结论")
    report.append("=" * 50)
    if not diag_problems and not failed and not hw_failed:
        verdict = "通过：环境诊断与全部测试均正常"
        if with_hardware:
            verdict += "，硬件实测全部通过"
        report.append(verdict)
        code = 0
    else:
        verdict = "存在问题，详见上文"
        report.append(verdict)
        for p in diag_problems:
            report.append(f"  诊断: {p}")
        if failed:
            report.append(f"  测试未通过 {len(failed)} 个: {', '.join(failed)}")
        if hw_failed:
            report.append(f"  硬件实测未通过 {len(hw_failed)} 项: {', '.join(hw_failed)}")
            report.append("  （扫描类 FAIL 多为固件侧问题，可把本报告发给固件作者）")
        code = 1

    with open(REPORT_FILE, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("\n".join(report) + "\n")

    print()
    print(verdict)
    print(f"报告已写入 {REPORT_FILE}")
    return code


if __name__ == "__main__":
    sys.exit(main())
