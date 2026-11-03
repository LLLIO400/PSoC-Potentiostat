# Copyright (c) 2015-2023 Kyle Lopin (Naresuan University) <kylel@nu.ac.th>
# Licensed under the Creative Commons Attribution-ShareAlike 3.0 (CC BY-SA 3.0 US) License
# Modified by Zhijian Yi (Guangdong, China), 2026 — PSoC-Potentiostat-YI-1.0

"""Utility functions for file I/O, display detection and voltage profile generation."""

import csv
import logging
import tkinter as tk
from tkinter import filedialog

from i18n import get_text


def open_file(_type, return_filename=False):
    """Open a file dialog for saving or opening CSV files.

    :param _type: 'saveas' or 'open'
    :param return_filename: if True, return filename string instead of file object
    """
    options = {
        'defaultextension': ".csv",
        'filetypes': [(get_text("file_csv"), "*.csv"), (get_text("file_all"), "*.*")]
    }
    if _type == 'saveas':
        if return_filename:
            return filedialog.asksaveasfilename(**options)
        # 必须显式 UTF-8：中文 Windows 上 open() 默认 GBK，
        # 曲线标签"数据 1"会写成 GBK 字节，读取端按 UTF-8 解码直接炸
        # （真机踩坑：老文件 0xca 字节 decode 失败，只有纯 ASCII 的 CA 文件能打开）
        # 注意不能用 asksaveasfile(encoding=...)：它把 encoding 当对话框选项
        # 传给 Tk，直接 TclError，对话框弹不出来（真机踩坑：保存全部无反应）
        filename = filedialog.asksaveasfilename(**options)
        if filename:
            return open(filename, 'w', encoding='utf-8-sig', newline='')
        return None
    elif _type == 'open':
        return filedialog.askopenfilename(**options)
    return None


def load_csv_file_full(file_path):
    """读取本程序保存的 CSV，返回 (metadata 字典, 表头行, 数据行列表)；无法识别时返回 None。

    兼容格式：
    1. 本程序伏安格式：'# key: value' 元数据行 + 'voltage' 表头 + 标签行 + 数据
    2. 本程序计时电流格式：'# ' 元数据行 + 'time' 表头 + 数据
    3. 通用两列格式：'voltage_mV,current_uA' 表头 + 数据（如合成测试数据）
    """
    try:
        with open(file_path, 'r', newline='', encoding='utf-8-sig') as f:
            rows = [row for row in csv.reader(f) if row]
    except UnicodeDecodeError:
        # 老版本在中文 Windows 上存的是 GBK（曲线标签"数据 1"等），回退兼容
        with open(file_path, 'r', newline='', encoding='gbk') as f:
            rows = [row for row in csv.reader(f) if row]
    metadata = {}
    header_idx = None
    for i, row in enumerate(rows):
        cell = row[0].strip()
        if cell.startswith('#'):
            # 元数据行：'# key: value'
            body = cell.lstrip('#').strip()
            if ':' in body:
                key, value = body.split(':', 1)
                metadata[key.strip()] = value.strip()
            continue
        if cell.startswith('label'):
            continue  # 通用格式的标签行
        if cell.startswith('voltage') or cell.startswith('time'):
            header_idx = i
        break  # 首条有效行不是可识别表头 -> 不识别
    if header_idx is None:
        return None
    header = rows[header_idx]
    data_rows = rows[header_idx + 1:]
    if header[0].strip() == 'voltage':
        # 伏安格式：表头后紧跟一行曲线标签/备注，跳过
        data_rows = data_rows[1:]
    return metadata, header, data_rows


def load_csv_file(file_path):
    """兼容旧调用：只返回 (表头行, 数据行列表)；无法识别时返回 None。"""
    loaded = load_csv_file_full(file_path)
    if loaded is None:
        return None
    _metadata, header, data_rows = loaded
    return header, data_rows


def check_display_type():
    """Check if matplotlib is available for graphing."""
    try:
        import matplotlib
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        return "matplotlib"
    except ImportError:
        return "canvas"


def make_voltage_profile(start: int, end: int, increment: int,
                         sweep_type="CV", start_volt_type="Zero",
                         swv_height=None):
    """Generate a voltage profile for the given sweep parameters."""
    if sweep_type == "CV" and start_volt_type == "Zero":
        return make_x_line_zero_cv(start, end, increment, swv_height)
    if sweep_type == "LS":
        return make_x_line_linear(start, end, increment, swv_height)
    if sweep_type == "CV" and start_volt_type == "Start":
        return make_x_line_triangle(start, end, increment, swv_height)
    logging.error(f"make x line got a bad type: {sweep_type}, {start_volt_type}")
    raise NotImplementedError


def _append_segment(line, a, b, inc):
    """追加一段 a→b 的电位序列；与上一段末点重复的端点去掉
    （对应固件 LUT_fix_lut_index 去掉段间重复 DAC 值的规则）。"""
    seg = _make_side(a, b, inc, None)
    if line and seg and seg[0] == line[-1]:
        seg = seg[1:]
    line.extend(seg)


def make_cv_x_line(start, end, inc, start_type="Start", cycles=1,
                   per_cycle=False):
    """多圈 CV 的 x 轴电位序列（电极电位 mV），与固件 LUT 分段规则一致。

    'Start' 型：N 圈 start→end→start 三角波（每圈 2 段）；
    'Zero' 型 ：固件连续模式（per_cycle=False）为 0→start 一次，
                N−1 个往返后末圈 start→end 并从 end 回 0；
                GUI 循环模式（per_cycle=True）逐圈重跑同一条 LUT，
                每圈都是完整的 0→start→end→0，按圈拼接。

    per_cycle=True 仅在旧固件 GUI 循环调度时使用；固件 LUT 多圈
    （零间隙连续循环）用默认 per_cycle=False。
    """
    cycles = max(1, int(cycles))
    line = []
    if start_type == 'Zero' and per_cycle:
        for _ in range(cycles):
            _append_segment(line, 0, start, inc)
            _append_segment(line, start, end, inc)
            _append_segment(line, end, 0, inc)
        return line
    if start_type == 'Zero':
        _append_segment(line, 0, start, inc)
        for _ in range(cycles - 1):
            _append_segment(line, start, end, inc)
            _append_segment(line, end, start, inc)
        _append_segment(line, start, end, inc)
        _append_segment(line, end, 0, inc)
        return line
    for _ in range(cycles):  # 'Start' 型：两种模式波形相同
        _append_segment(line, start, end, inc)
        _append_segment(line, end, start, inc)
    return line


def cv_lut_length(start_dac, end_dac, start_type, cycles,
                  ground_dac=2048, lut_size=5000):
    """按固件 LUT 分段规则计算 CV 波形表长度（'g' 命令回读的期望值）。

    分段规则见 lut_protocols.c：每圈采样 2r+1 点，但段间重复端点被
    LUT_fix_lut_index 去掉，故每多一圈波形表净增 2r 点。
    返回 (N圈期望长度, 1圈期望长度)，供 GUI 探测固件是否支持多圈。
    """
    r = abs(end_dac - start_dac)
    extra = 2 * r  # 每多一个往返，波形表净增点数（段间端点去重后）
    if str(start_type).startswith('Z'):
        gs = abs(start_dac - ground_dac)
        ge = abs(end_dac - ground_dac)
        # 单圈：0→start（去重后 gs 点）+ start→end（r 点）+ end→0（ge+1 点）+ 收尾 1 点
        one = gs + r + ge + 2
    else:
        one = 2 * r + 2  # start→end→start + 收尾保持
    multi = one + (max(1, int(cycles)) - 1) * extra
    # LUT 塞满时固件提前收尾，按满容量封顶
    cap = lut_size + 1
    return min(multi, cap), min(one, cap)


def make_x_line_linear(start: int, end: int, inc: int, swv_height: int) -> list:
    side_maker = _make_side
    if swv_height:
        side_maker = _make_swv_side
    return side_maker(start, end, inc, swv_height)


def make_x_line_zero_cv(start, end, inc, swv_height):
    side_maker = _make_side
    if swv_height:
        side_maker = _make_swv_side
    _line = []
    _line.extend(side_maker(0, start, inc, swv_height))
    next_start = _line[-2]
    if swv_height:
        next_start = _line[-5] + int(swv_height / 2)
    _line.extend(side_maker(next_start, end, inc, swv_height))
    next_start = _line[-2]
    if swv_height:
        next_start = _line[-5] + int(swv_height / 2)
    _line.extend(side_maker(next_start, 0, inc, swv_height))
    return _line


def make_x_line_triangle(start, end, inc, swv_height):
    side_maker = _make_side
    if swv_height:
        side_maker = _make_swv_side
    _line = []
    _line.extend(side_maker(start, end, inc, swv_height))
    next_start = _line[-2]
    if swv_height:
        next_start = _line[-5] + int(swv_height / 2)
    _line.extend(side_maker(next_start, start, inc, swv_height))
    return _line


def _make_side(start, end, increment, *args):
    increment = abs(increment)
    lut = []
    if start < end:
        while start <= end:
            lut.append(start)
            start += increment
    else:
        while start >= end:
            lut.append(start)
            start -= increment
    return lut


def _make_swv_side(start, end, increment, pulse_height):
    increment = abs(increment)
    lut = []
    half_pulse = int(pulse_height / 2)
    if start <= end:
        while start <= end:
            lut.extend(2 * [(start + half_pulse)])
            lut.extend(2 * [(start - half_pulse)])
            start += increment
    elif start > end:
        while start >= end:
            lut.extend(2 * [(start + half_pulse)])
            lut.extend(2 * [(start - half_pulse)])
            start -= increment
    return lut


def make_dpv_x_line(start, end, pulse_inc):
    """DPV 阶梯基底电位：每步 1 个点，与差分后的数据点一一对应。

    点数规则与固件 G 命令 LUT 一致：|end - start| // pulse_inc + 1。
    （注意不能用 make_voltage_profile：它不识别 'DPV'，且 swv_height
    展开规则会把 x 轴变成方波阶梯，长度与差分数据对不上。）
    """
    if pulse_inc <= 0:
        raise ValueError("pulse_inc must be positive, got {0}".format(pulse_inc))
    # 设置界面读出的 pulse_inc 是 float，// 结果也是 float，range() 会炸；
    # 必须转 int（2026-08-25 真机 'float' object cannot be interpreted as an integer）
    n_steps = int(abs(end - start) // pulse_inc) + 1
    step = pulse_inc if end >= start else -pulse_inc
    return [start + i * step for i in range(n_steps)]


def validate_dpv_params(low_voltage, high_voltage, pulse_height,
                        pulse_inc, pulse_width):
    """DPV 参数校验。返回 i18n 错误 key；参数合法时返回 None。"""
    if pulse_height <= 0:
        return "err_pulse_height"
    if pulse_inc <= 0:
        return "err_pulse_inc"
    if pulse_width < 10:
        return "err_pulse_width"
    if abs(high_voltage - low_voltage) < pulse_inc:
        return "err_scan_range"
    return None



def get_data_from_csv_file(_filename):
    """Read data from a CSV file and return as list of lists."""
    with open(_filename, 'r', newline='') as _file:
        _reader = csv.reader(_file)
        first_array = next(_reader)
        _data_hold = []
        for i in range(len(first_array)):
            _data_hold.append([])
        for row in _reader:
            for i, data in enumerate(row):
                _data_hold[i].append(float(data))
    return _data_hold
