# Copyright (c) 2015-2023 Kyle Lopin (Naresuan University) <kylel@nu.ac.th>
# Licensed under the Creative Commons Attribution-ShareAlike 3.0 (CC BY-SA 3.0 US) License
# Modified by Zhijian Yi (Guangdong, China), 2026 — PSoC-Potentiostat-YI-1.0

"""All dialog windows (toplevels) for settings, data management and user interaction."""

import logging
import os
import sys
import tkinter as tk
import tkinter.font
from tkinter import ttk

import ttkbootstrap as tb

from config import (CURRENT_OPTION_LIST, CURRENT_LIMIT_VALUES, TIA_RESISTOR_VALUES,
                    GraphProps, MAX_CV_CYCLES)
from i18n import get_text
from plotting import PyplotEmbed
from utils import make_voltage_profile, open_file, validate_dpv_params
from tkinter import messagebox

COLOR_CHOICES = ['black', 'gray', 'red', 'green', 'blue', 'orange', 'magenta']


class CVSettingChanges(tb.Toplevel):
    # 页面 -> (sweep_type, use_swv, 设置窗口标题 key)
    MODE_SETTINGS = {'CV': ('CV', False, 'cv_settings_title'),
                     'LS': ('LS', False, 'ls_settings_title'),
                     'SWV': ('LS', True, 'swv_settings_title'),
                     'DPV': ('LS', True, 'dpv_settings_title')}

    def __init__(self, cv_display, _master: tk.Tk, cv_graph, device, mode='CV'):
        tb.Toplevel.__init__(self, master=_master)
        self.master = _master
        self.mode = mode
        self.preview_graph = None
        self.start_volt = tk.DoubleVar()
        self.end_volt = tk.DoubleVar()
        self.entry_delay = None
        self.use_swv = tk.BooleanVar()
        self.swv_pulse = tk.DoubleVar()
        self.swv_inc = tk.DoubleVar()
        self.swv_period = tk.DoubleVar()
        self.freq = tk.DoubleVar()
        self._current_range = ""
        self.device = device
        self.data = None
        self.title(get_text(self.MODE_SETTINGS[mode][2]))

        self.preview_frame = tb.Frame(self)
        self.options_frame = tb.Frame(self, padding=10)
        self.options_frame.pack(side='right', fill=tk.BOTH, expand=True)

        _settings = _master.device_params.cv_settings

        def _entry_row(parent, row, label_key, var, value, unit):
            tb.Label(parent, text=get_text(label_key), width=14, anchor='w'
                     ).grid(row=row, column=0, sticky='w', padx=(5, 2), pady=4)
            tb.Entry(parent, textvariable=var, width=12
                     ).grid(row=row, column=1, sticky='w', pady=4)
            tb.Label(parent, text=unit, width=5, anchor='w'
                     ).grid(row=row, column=2, sticky='w', padx=2)
            var.set(value)

        # ---- 基本参数 ----
        basic = tb.LabelFrame(self.options_frame, text=get_text("grp_basic"), padding=8)
        basic.pack(fill=tk.X, pady=(0, 8))
        _entry_row(basic, 0, "settings_start_voltage", self.start_volt,
                   _settings.start_voltage, "")
        _entry_row(basic, 1, "settings_end_voltage", self.end_volt,
                   _settings.end_voltage, "")
        _entry_row(basic, 2, "settings_sweep_rate", self.freq,
                   _settings.sweep_rate, "")
        tb.Label(basic, text=get_text("settings_current_range"), width=14, anchor='w'
                 ).grid(row=3, column=0, sticky='w', padx=(5, 2), pady=4)
        self.current_options = tk.StringVar(basic)
        self.current_option_list = CURRENT_OPTION_LIST
        self.current_options.set(
            self.current_option_list[_master.device_params.adc_tia.current_option_index])
        tb.Combobox(basic, textvariable=self.current_options,
                    values=self.current_option_list, state='readonly', width=10
                    ).grid(row=3, column=1, sticky='w', pady=4)

        # ---- 扫描设置：扫描方式由所在页面固定，这里只选起始电压方式 ----
        self.preview_var = tk.IntVar()
        self.sweep_type = tk.StringVar()
        self.sweep_type.set(self.MODE_SETTINGS[mode][0])
        self.start_voltage_type = tk.StringVar()
        self.start_voltage_type.set(self.master.device_params.cv_settings.sweep_start_type)

        sweep = tb.LabelFrame(self.options_frame, text=get_text("grp_sweep"), padding=8)
        sweep.pack(fill=tk.X, pady=(0, 8))
        tb.Label(sweep, text=get_text("settings_start_voltage"), width=14, anchor='w'
                 ).grid(row=0, column=0, sticky='w', padx=(5, 2), pady=4)
        tb.Radiobutton(sweep, text=get_text("settings_start_zero"),
                       variable=self.start_voltage_type,
                       value="Zero", command=self.set_sweep_type
                       ).grid(row=0, column=1, sticky='w')
        tb.Radiobutton(sweep, text=get_text("settings_start_start"),
                       variable=self.start_voltage_type,
                       value="Start", command=self.set_sweep_type
                       ).grid(row=0, column=2, sticky='w')

        # ---- CV 循环圈数（仅 CV 页；旧固件自动改用软件逐圈调度，一样有效）----
        self.cycles = None
        if mode == 'CV':
            self.cycles = tk.IntVar()
            self.cycles.set(max(1, int(getattr(_settings, 'cycles', 1))))
            tb.Label(sweep, text=get_text("settings_cycles"), width=14, anchor='w'
                     ).grid(row=1, column=0, sticky='w', padx=(5, 2), pady=4)
            tb.Spinbox(sweep, from_=1, to=MAX_CV_CYCLES, increment=1,
                       textvariable=self.cycles, width=6
                       ).grid(row=1, column=1, sticky='w', pady=4)
            tb.Label(sweep, text=get_text("settings_cycles_hint"), wraplength=230,
                     justify='left', foreground='gray'
                     ).grid(row=2, column=0, columnspan=3, sticky='w', padx=(5, 2))

        # ---- 扫描前静置（等效 CHI 静置时间；四个方法页通用）----
        # 电位跳到起始值后等暂态衰减再开扫，消除起点大负漂/削顶
        self.quiet_time = tk.DoubleVar()
        self.quiet_time.set(max(0.0, float(getattr(_settings,
                                                   'quiet_time_s', 3.0))))
        quiet_row = 3 if mode == 'CV' else 1
        tb.Label(sweep, text=get_text("settings_quiet_time"), width=14,
                 anchor='w').grid(row=quiet_row, column=0, sticky='w',
                                  padx=(5, 2), pady=4)
        tb.Spinbox(sweep, from_=0.0, to=60.0, increment=0.5, width=6,
                   textvariable=self.quiet_time
                   ).grid(row=quiet_row, column=1, sticky='w', pady=4)
        tb.Label(sweep, text=get_text("settings_quiet_time_hint"),
                 wraplength=230, justify='left', foreground='gray'
                 ).grid(row=quiet_row + 1, column=0, columnspan=3,
                        sticky='w', padx=(5, 2))

        # ---- 脉冲参数（方波/差分脉冲页显示） ----
        self.use_swv.set(self.MODE_SETTINGS[mode][1])
        # 无论哪个页面都要从设置初始化方波变量：
        # 否则 CV/LS 页保存时会把 swv_height/inc/period 清零并写入 settings.txt，
        # 导致方波页扫参损坏（format_divider 除零）
        self.swv_pulse.set(_settings.swv_height)
        self.swv_inc.set(_settings.swv_inc)
        self.swv_period.set(_settings.swv_period)
        if mode in ('SWV', 'DPV'):
            swv = tb.LabelFrame(self.options_frame, text=get_text("grp_pulse"),
                                padding=8)
            swv.pack(fill=tk.X, pady=(0, 8))
            # 标签按页面技术叫法区分：DPV 用"脉冲"，SWV 用"方波"；
            # 三者存储与含义共用（周期 = 每一步总时长，与 ASV 溶出一致）
            pulse_keys = (["settings_dpv_height", "settings_dpv_increment",
                           "settings_dpv_period"] if mode == 'DPV' else
                          ["settings_swv_height", "settings_swv_increment",
                           "settings_swv_period"])
            for i, (key, var, unit, val) in enumerate(zip(
                    pulse_keys,
                    [self.swv_pulse, self.swv_inc, self.swv_period],
                    ["mV", "mV", "ms"],
                    [_settings.swv_height, _settings.swv_inc, _settings.swv_period])):
                _entry_row(swv, i, key, var, val, unit)

        # ---- 预览 + 底部按钮 ----
        tb.Checkbutton(self.options_frame, text=get_text("settings_preview"),
                       variable=self.preview_var, command=self.preview
                       ).pack(anchor='w', padx=5, pady=(0, 5))
        button_frame = tb.Frame(self.options_frame)
        button_frame.pack(side='bottom', fill=tk.X, pady=(5, 0))
        tb.Button(button_frame, text=get_text("settings_save"), width=12,
                  bootstyle="primary",
                  command=lambda: self.save_cv_changes(self.current_options.get(),
                                                       _master, cv_graph, cv_display)
                  ).pack(side='left', padx=10, expand=True)
        tb.Button(button_frame, text=get_text("settings_exit"), width=12,
                  bootstyle="secondary-outline",
                  command=self.destroy).pack(side='left', padx=10, expand=True)

        # 按内容自动调整窗口大小，避免不同系统/DPI 下右侧和底部被裁剪
        self.update_idletasks()
        _w = max(480, self.options_frame.winfo_reqwidth() + 30)
        _h = max(560, self.options_frame.winfo_reqheight() + 30)
        self._base_geometry = "{0}x{1}".format(_w, _h)
        self.geometry(self._base_geometry)
        self.minsize(_w, _h)

        self.end_volt.trace("w", self.trace_delay)
        self.start_volt.trace("w", self.trace_delay)
        self.freq.trace("w", self.trace_delay)
        self.swv_pulse.trace("w", self.trace_delay)
        self.swv_inc.trace("w", self.trace_delay)
        self.swv_period.trace("w", self.trace_delay)
        self.use_swv.trace("w", self.trace_delay)

    def destroy(self):
        # 销毁前摘掉挂起的 300ms 预览回调：快速打开又关闭设置窗口时，
        # 迟到的回调打到已销毁部件会产生 "invalid command name"/bgerror
        # 噪音（测试报告与控制台踩过）
        if getattr(self, 'entry_delay', None):
            try:
                self.after_cancel(self.entry_delay)
            except Exception:
                pass
            self.entry_delay = None
        tb.Toplevel.destroy(self)

    def trace_delay(self, *args):
        self.entry_delay = self.after(300, self.set_sweep_type)

    def set_sweep_type(self, *args):
        if not self.preview_var.get():
            return
        if self.entry_delay:
            self.after_cancel(self.entry_delay)
            self.entry_delay = None
        self.make_graph(self.sweep_type.get(), self.start_voltage_type.get(),
                        self.use_swv.get())

    def preview(self):
        if self.preview_var.get():
            sweep_type = self.sweep_type.get()
            start_place = self.start_voltage_type.get()
            print(f"making preview of type: {sweep_type}, dpv: {self.use_swv.get()}")
            self.make_graph(sweep_type, start_place, self.use_swv.get())
        else:
            if self.preview_graph:
                self.preview_graph.pack_forget()
                self.preview_graph.destroy()
                self.preview_graph = None
            self.geometry(self._base_geometry)

    def make_graph(self, sweep_type, start_volt_type, use_swv):
        self.geometry("860x" + self._base_geometry.split('x')[1])
        blank_frame = tb.Frame()
        try:
            start_volt = int(float(self.start_volt.get()))
            end_volt = int(float(self.end_volt.get()))
            rate = float(self.freq.get())
            swv_inc = int(float(self.swv_inc.get()))
            swv_pulse = int(float(self.swv_pulse.get()))
            swv_period = float(self.swv_period.get())
        except Exception as _error:
            print(f"Error converting numbers in make_graph: {_error}")
            return -1
        low_voltage = min([start_volt, end_volt])
        high_volt = max([start_volt, end_volt])
        voltage_step = self.master.device_params.dac.voltage_step_size
        ylims = [low_voltage, high_volt]
        if use_swv:
            self.data = make_voltage_profile(start_volt, end_volt, swv_inc,
                                             sweep_type, start_volt_type, swv_pulse)
            ylims = [low_voltage - swv_pulse, high_volt + swv_pulse]
            total_time = swv_period * len(self.data) / 4
            time = []
            time_runner = 0
            for i in range(int(len(self.data) / 2)):
                time.append(time_runner)
                time_runner += swv_period / 2
                time.append(time_runner)
        else:
            self.data = make_voltage_profile(start_volt, end_volt,
                                             voltage_step, sweep_type, start_volt_type)
            steps_per_second = rate * float(voltage_step)
            total_time = len(self.data) * steps_per_second
            time = [x * steps_per_second for x in range(len(self.data))]
        xlims = [0, total_time]

        plt_props = {'xlabel': "'time (msec)'",
                     'ylabel': "'voltage (mV)'",
                     'title': "'" + get_text("voltage_profile_title") + "'",
                     'subplots_adjust': "bottom=0.15, left=0.12"}
        if self.preview_graph:
            self.preview_graph.graph_area.axis.set_xlim(xlims)
            self.preview_graph.graph_area.axis.set_ylim(ylims)
            self.preview_graph.voltage_line.set_data(time, self.data)
            self.preview_graph.update_graph()
        else:
            self.preview_graph = PyplotEmbed(blank_frame, plt_props, self, ylims, 0,
                                             total_time)
            self.preview_graph.pack(side='left', fill=tk.BOTH, expand=True)
            self.preview_graph.simple_update_data(time, self.data)

    def save_cv_changes(self, _range, _master, cv_graph, cv_display):
        old_cv_settings = _master.device_params.cv_settings
        try:
            self._start_volt = int(float(self.start_volt.get()))
            self._end_volt = int(float(self.end_volt.get()))
            self._freq = float(self.freq.get())
            self._swv_inc = int(float(self.swv_inc.get()))
            self._swv_pulse = int(float(self.swv_pulse.get()))
            self._swv_period = int(float(self.swv_period.get()))
            self._use_swv = self.use_swv.get()
            self._cycles = (max(1, int(self.cycles.get()))
                            if self.cycles is not None else 1)
            self._quiet_time = max(0.0, float(self.quiet_time.get()))
        except (ValueError, tk.TclError) as error:
            logging.info("Error in data input format: %s", error)
            messagebox.showerror(get_text("title_invalid_params"),
                                 get_text("err_invalid_number"))
            return
        # 圈数不再按 LUT 容量硬性拒绝：容量内走固件零间隙连续循环，
        # 超容量时发送端自动降级为 GUI 逐圈调度（send_cv_parameters），
        # 任意圈数都能保存并跑满——曾在对话框里一刀切拦截，
        # 把用户合法的多圈设置挡下（真机踩坑）
        quiet_before = getattr(old_cv_settings, 'quiet_time_s', 3.0)
        old_cv_settings.quiet_time_s = self._quiet_time
        params_changed = self.sweep_param_is_changed(_master.device_params)
        if params_changed:
            old_cv_settings.update_settings(
                self._start_volt, self._end_volt, self._freq,
                self.sweep_type.get(), self.start_voltage_type.get(),
                self._swv_pulse, self._swv_inc, self._swv_period, self._use_swv,
                cycles=self._cycles)
            cv_display.cv_label_update(_master.device_params)
            self.device.send_cv_parameters()
            x_lim_low = old_cv_settings.low_voltage
            x_lim_high = old_cv_settings.high_voltage
            cv_graph.resize_x(x_lim_low, x_lim_high)
        elif old_cv_settings.quiet_time_s != quiet_before:
            # 只有静置时长变化：update_settings 未触发，单独持久化
            old_cv_settings.save_settings()
        current_range_index = CURRENT_OPTION_LIST.index(_range)
        if _master.device_params.adc_tia.current_option_index != current_range_index:
            self.device.set_adc_tia(current_range_index)
        _master.device_params.adc_tia.current_option_index = current_range_index
        # 量程变化也要刷新三个页面的参数显示和图 y 轴（修复只改量程不刷新的问题）
        _master.update_current_range(CURRENT_OPTION_LIST[current_range_index],
                                     CURRENT_LIMIT_VALUES[current_range_index])
        self.destroy()

    def sweep_param_is_changed(self, _old_params):
        cycles_changed = (self.cycles is not None
                          and self._cycles != getattr(_old_params.cv_settings,
                                                      'cycles', 1))
        if (cycles_changed
                or self._start_volt != _old_params.cv_settings.low_voltage
                or self._end_volt != _old_params.cv_settings.high_voltage
                or self._freq != _old_params.cv_settings.sweep_rate
                or self._swv_period != _old_params.cv_settings.swv_period
                or self._swv_inc != _old_params.cv_settings.swv_inc
                or self._swv_pulse != _old_params.cv_settings.swv_height
                or self._use_swv != _old_params.cv_settings.use_swv):
            logging.debug("sweep_param is changed")
            return True
        logging.debug("sweep param are not changed")
        return False


class ASVSettingChanges(tb.Toplevel):
    def __init__(self, asv_frame, master, graph, device):
        tb.Toplevel.__init__(self, master=master)
        self.title(get_text("asv_settings_title"))
        self.asv_frame = asv_frame
        self.master = master
        self.graph = graph
        self.device = device
        self.entry_delay = None
        self.minsize(480, 650)

        self.clean_volt = tk.DoubleVar()
        self.clean_time = tk.DoubleVar()
        self.plate_volt = tk.DoubleVar()
        self.plate_time = tk.DoubleVar()
        self.end_voltage = tk.DoubleVar()
        self.sweep_rate = tk.DoubleVar()
        self.rest_time = tk.DoubleVar()
        self.clean_enabled = tk.BooleanVar()
        self.stirring_enabled = tk.BooleanVar()
        self.clean_after_strip = tk.BooleanVar()
        self.sweep_type = tk.IntVar()
        self.strip_start = tk.DoubleVar()

        self.settings = master.device_params
        asv = self.settings.asv_settings
        self.clean_volt.set(asv.clean_volt)
        self.clean_time.set(self.settings.asv_settings.clean_time)
        self.plate_volt.set(self.settings.asv_settings.plate_volt)
        self.plate_time.set(self.settings.asv_settings.plate_time)
        self.end_voltage.set(self.settings.asv_settings.end_voltage)
        self.sweep_rate.set(self.settings.asv_settings.sweep_rate)
        self.rest_time.set(self.settings.asv_settings.rest_time)
        self.clean_enabled.set(self.settings.asv_settings.clean_enabled)
        self.stirring_enabled.set(self.settings.asv_settings.stirring_enabled)
        self.clean_after_strip.set(asv.clean_after_strip)
        # 溶出起始电位（旧实例无该字段时回退扫描下限）
        self.strip_start.set(getattr(asv, 'strip_start', asv.low_voltage))

        # 按钮区先 pack 到底部，保证窗口偏小时也不会被挤掉
        button_frame = tb.Frame(self)
        button_frame.pack(side='bottom', fill=tk.X, pady=8)
        tb.Button(button_frame, text=get_text("btn_save"), width=15, bootstyle="primary",
                  command=self.save).pack(side='left', expand=True)
        tb.Button(button_frame, text=get_text("btn_exit"), width=15,
                  bootstyle="secondary-outline",
                  command=self.destroy).pack(side='left', expand=True)

        self.options_frame = tb.Frame(self, padding=(10, 5))
        self.options_frame.pack(fill=tk.BOTH, expand=True)

        def _group(title_key):
            g = tb.LabelFrame(self.options_frame, text=get_text(title_key), padding=8)
            g.pack(fill=tk.X, pady=4)
            return g

        # ---- 清洗（含溶出后清洗：沿用本组电压/时间，语义集中一处）----
        g = _group("grp_clean")
        self.entry_row(g, get_text("asv_clean_volt") + ": ", self.clean_volt, "mV", 0)
        self.entry_row(g, get_text("asv_clean_time") + ": ", self.clean_time, "secs", 1)
        tb.Checkbutton(g, text=get_text("asv_enable_clean"),
                       variable=self.clean_enabled
                       ).grid(row=2, column=0, columnspan=3, sticky='w', padx=10)
        tb.Checkbutton(g, text=get_text("asv_clean_after_strip"),
                       variable=self.clean_after_strip
                       ).grid(row=3, column=0, columnspan=3, sticky='w', padx=10)

        # ---- 富集（静置在富集结束之后，故紧跟富集参数）----
        g = _group("grp_plate")
        self.entry_row(g, get_text("asv_plate_volt") + ": ", self.plate_volt, "mV", 0)
        self.entry_row(g, get_text("asv_plate_time") + ": ", self.plate_time, "secs", 1)
        tb.Checkbutton(g, text=get_text("asv_enable_stirring"),
                       variable=self.stirring_enabled
                       ).grid(row=2, column=0, columnspan=3, sticky='w', padx=10)
        self.entry_row(g, get_text("asv_rest_time") + ": ", self.rest_time, "secs", 3)

        # ---- 溶出 ----
        g = _group("grp_strip")
        # 溶出方式紧跟溶出参数（与独立 DPV/SWV 页同一套参数含义与默认值）：
        # LS=线性扫描；DPV/SWV 时下方"脉冲参数"生效
        self.sweep_type = tk.IntVar()
        self.sweep_type.set({'LS': 0, 'DPV': 1, 'SWV': 2}.get(
            getattr(self.settings.asv_settings, 'sweep_type', 'LS'), 0))
        tb.Label(g, text=get_text("asv_stripping_method"), width=16, anchor='w'
                 ).grid(row=0, column=0, sticky='w', padx=(10, 2), pady=4)
        radio_frame = tb.Frame(g)
        radio_frame.grid(row=0, column=1, columnspan=2, sticky='w')
        tb.Radiobutton(radio_frame, text=get_text("settings_sweep_ls"),
                       variable=self.sweep_type, value=0
                       ).pack(side='left', padx=(0, 8))
        tb.Radiobutton(radio_frame, text=get_text("settings_sweep_dpv"),
                       variable=self.sweep_type, value=1
                       ).pack(side='left', padx=(0, 8))
        tb.Radiobutton(radio_frame, text=get_text("settings_sweep_swv"),
                       variable=self.sweep_type, value=2
                       ).pack(side='left')
        self.entry_row(g, get_text("asv_strip_start") + ": ", self.strip_start, "mV", 1)
        self.entry_row(g, get_text("asv_peak_volt") + ": ", self.end_voltage, "mV", 2)
        self.entry_row(g, get_text("asv_sweep_rate") + ": ", self.sweep_rate, "V/s", 3)

        # ---- 通用 ----
        g = _group("grp_general")
        tb.Label(g, text=get_text("settings_current_range"), width=16, anchor='w'
                 ).grid(row=0, column=0, sticky='w', padx=(10, 2), pady=4)
        self.current_options = tk.StringVar()
        self.current_options.set(
            CURRENT_OPTION_LIST[master.device_params.adc_tia.current_option_index])
        tb.Combobox(g, textvariable=self.current_options,
                    values=CURRENT_OPTION_LIST, state='readonly', width=12
                    ).grid(row=0, column=1, sticky='w', pady=4)

        # ---- 脉冲参数（选 DPV/SWV 溶出时生效；含义与独立页一致：
        #      高度/增量 mV，周期 = 每一步总时长 ms）----
        g = _group("grp_pulse")
        self.pulse_height = tk.DoubleVar()
        self.pulse_inc = tk.DoubleVar()
        self.pulse_width = tk.DoubleVar()
        self.pulse_height.set(self.settings.asv_settings.pulse_height)
        self.pulse_inc.set(self.settings.asv_settings.pulse_inc)
        self.pulse_width.set(self.settings.asv_settings.pulse_width)

        label_strs = [get_text("pulse_height"), get_text("pulse_increment"),
                      get_text("pulse_period")]
        entries = [self.pulse_height, self.pulse_inc, self.pulse_width]
        unit_str = ["mV", "mV", "ms"]
        for i in range(3):
            self.entry_row(g, label_strs[i], entries[i], unit_str[i], i)
        tb.Label(g, text=get_text("asv_pulse_hint"), bootstyle='secondary',
                 font=('', 9), wraplength=420, justify='left'
                 ).grid(row=4, column=0, columnspan=3, sticky='w', padx=10,
                        pady=(2, 0))

        # 按内容自动调整窗口大小，避免不同系统/DPI 下内容被裁剪
        self.update_idletasks()
        _w = max(500, self.options_frame.winfo_reqwidth() + 30)
        _h = max(650, self.options_frame.winfo_reqheight() + 80)  # 含底部按钮区
        self.geometry("{0}x{1}".format(_w, _h))


    @staticmethod
    def entry_row(frame, label_str, entry_variable, unit_str, row):
        tb.Label(frame, text=label_str, width=16, anchor='w'
                 ).grid(row=row, column=0, sticky='w', padx=(10, 2), pady=4)
        tb.Entry(frame, textvariable=entry_variable, width=12
                 ).grid(row=row, column=1, sticky='w', pady=4)
        tb.Label(frame, text=unit_str, width=6, anchor='w'
                 ).grid(row=row, column=2, sticky='w', padx=2)

    def save(self):
        try:
            values = (self.clean_volt.get(), self.clean_time.get(),
                      self.plate_volt.get(), self.plate_time.get(),
                      self.end_voltage.get(), self.sweep_rate.get(),
                      self.pulse_height.get(), self.pulse_inc.get(),
                      self.pulse_width.get(), self.rest_time.get())
        except tk.TclError:
            messagebox.showerror(get_text("title_invalid_params"),
                                 get_text("err_invalid_number"))
            return
        if self.strip_start.get() >= self.end_voltage.get():
            # 溶出是阳极方向扫描：起始电位必须低于终止电位
            messagebox.showerror(get_text("title_invalid_params"),
                                 get_text("err_strip_range"))
            return
        if self.sweep_type.get() in (1, 2):
            # DPV/SWV 参数校验：非法参数会让固件 LUT/时序计算出错
            err = validate_dpv_params(self.strip_start.get(), self.end_voltage.get(),
                                      self.pulse_height.get(), self.pulse_inc.get(),
                                      self.pulse_width.get())
            if err:
                messagebox.showerror(get_text("title_invalid_params"),
                                     get_text(err))
                return
        self.settings.asv_settings.update_settings(
            self.clean_volt.get(), self.clean_time.get(),
            self.plate_volt.get(), self.plate_time.get(),
            self.end_voltage.get(), self.sweep_rate.get(),
            self.sweep_type.get(), self.pulse_height.get(),
            self.pulse_inc.get(), self.pulse_width.get(),
            rest_time=self.rest_time.get(),
            stirring_enabled=self.stirring_enabled.get(),
            clean_after_strip=self.clean_after_strip.get(),
            clean_enabled=self.clean_enabled.get(),
            strip_start=self.strip_start.get())
        x_lim_low = self.settings.asv_settings.low_voltage
        x_lim_high = self.settings.asv_settings.high_voltage
        self.graph.resize_x(x_lim_low, x_lim_high)
        position = CURRENT_OPTION_LIST.index(self.current_options.get())
        if position != self.master.device_params.adc_tia.current_option_index:
            self.device.set_adc_tia(position)
        self.master.update_current_range(CURRENT_OPTION_LIST[position],
                                         CURRENT_LIMIT_VALUES[position])
        self.label_update(self.settings)
        self.destroy()

    def label_update(self, params):
        # ASVFrame 本身没有 label_update：右侧参数栏在它的
        # cv_settings_frame 子面板上（与 main.py 刷新调用一致）
        self.asv_frame.cv_settings_frame.label_update(params)


class AmpSettingsChanges(tb.Toplevel):
    def __init__(self, display, master, graph, device):
        tb.Toplevel.__init__(self, master=master)
        self.sample_rate = 0
        self.current_range = ""
        self.title(get_text("amp_settings_title"))
        self.display = display
        tb.Label(self, text=get_text("amp_voltage") + ": ").grid(row=0, column=0,
                                                                 padx=10, pady=10)
        voltage = tb.Entry(self)
        voltage.insert(0, str(master.device_params.amp_settings.voltage))
        voltage.grid(row=0, column=1)
        tb.Label(self, text="mV").grid(row=0, column=3, padx=10, pady=10)

        rate = tb.Entry(self)
        rate.insert(0, str(master.device_params.amp_settings.sampling_rate / 1000))
        rate.grid(row=1, column=1)

        tb.Label(self, text=get_text("settings_current_range")).grid(row=2, column=0,
                                                                     padx=10, pady=10)
        self.current_options = tk.StringVar(self)
        self.current_option_list = CURRENT_OPTION_LIST
        self.current_options.set(
            self.current_option_list[master.device_params.adc_tia.current_option_index])
        current = tb.Combobox(self, textvariable=self.current_options,
                              values=self.current_option_list, state='readonly',
                              width=10)
        current.grid(row=2, column=1)
        tb.Button(self, text=get_text("settings_save"), bootstyle="primary",
                  command=lambda: self.save_amp_changes(voltage.get(), rate.get(), device, master)
                  ).grid(row=3, column=0)
        tb.Button(self, text=get_text("settings_exit"),
                  bootstyle="secondary-outline",
                  command=self.destroy).grid(row=3, column=1)

    def save_amp_changes(self, _voltage, _sampling_rate, device, master):
        current_range = self.current_options.get()
        try:
            voltage = int(float(_voltage))
            sampling_rate = float(_sampling_rate)
        except ValueError as error:
            logging.info("Error in data input format: %s", error)
            return
        if self.params_are_changed(master.device_params, voltage, sampling_rate):
            device.set_voltage(voltage)
            # 参数对象统一更新并持久化（settings_amp.txt）。采样率不用在
            # 这里下发——安培法每次运行开始都按 amp_settings.sampling_rate
            # 重设 PWM（experiments.amp_run）
            master.device_params.amp_settings.update_settings(
                voltage, sampling_rate)
        current_range_index = CURRENT_OPTION_LIST.index(current_range)
        if master.device_params.adc_tia.current_option_index != current_range_index:
            device.set_adc_tia(current_range_index)
        self.display.label_update(master.device_params)
        self.destroy()

    @staticmethod
    def params_are_changed(old_params, new_voltage, new_rate):
        if (new_voltage != old_params.amp_settings.voltage
                or new_rate != old_params.amp_settings.sampling_rate):
            logging.debug("amperometry parameters changed")
            return True
        logging.debug("amperometry parameters not changed")
        return False


class ChangeCompareValue(tb.Toplevel):
    def __init__(self, _master):
        tb.Toplevel.__init__(self, _master)
        self.title(get_text("title_pwm_compare"))
        tb.Label(self, text=get_text("msg_pwm_compare")).pack(side='top')
        tb.Label(self, text=get_text("msg_pwm_range").format(
            _master.device_params.PWM_period)).pack(side='top')
        tb.Label(self, text=get_text("msg_pwm_current") +
                 str(_master.device_params.PWM_compare)).pack(side='top')
        value_varstring = tk.StringVar()
        value_varstring.set(_master.device_params.PWM_compare)
        value_box = tb.Entry(self, textvariable=value_varstring)
        value_box.pack(side='top')
        button_frame = tb.Frame(self)
        button_frame.pack(side='top')
        tb.Button(button_frame, text=get_text("btn_exit"),
                  command=self.destroy).pack(side='left')
        tb.Button(button_frame, text=get_text("btn_save"), bootstyle="primary",
                  command=lambda: self.compare_reg_change(_master, value_varstring.get())
                  ).pack(side='left')
        logging.error("put in here something to check if the number for compare is correct")

    def compare_reg_change(self, master, _value):
        master.device.write_timer_compare(_value)
        self.destroy()


class UserDeleteDataWarning(tb.Toplevel):
    def __init__(self, master):
        tb.Toplevel.__init__(self, master)
        self.title(get_text("title_delete_all"))
        warning_frame = tb.Frame(self)
        tb.Label(warning_frame, text=get_text("msg_delete_all")).pack(side='top')
        buttons_frame = tb.Frame(self)
        tb.Button(buttons_frame, text=get_text("btn_yes"), bootstyle="danger",
                  command=lambda: self.call_delete_data(master)).pack(side='left', padx=10)
        tb.Button(buttons_frame, text=get_text("btn_dont_delete"),
                  command=self.destroy).pack(side='left', padx=10)
        warning_frame.pack(side='top')
        buttons_frame.pack(side='top', pady=10)

    def call_delete_data(self, master):
        master.delete_current_page_data()
        self.destroy()


class UserSetDataLabel(tb.Toplevel):
    def __init__(self, master):
        tb.Toplevel.__init__(self, master)
        self.title(get_text("data_save_options"))
        label_frame = tb.Frame(self)
        label_frame.pack(side='top')
        tb.Label(label_frame, text=get_text("label_data_label")).pack(side='left')
        _label = tk.StringVar()
        label_box = tb.Entry(label_frame, textvariable=_label)
        label_box.pack(side='left')
        tb.Label(self, text=get_text("label_notes") + ":").pack(side='top')
        user_notes = tk.Text(self, width=20, height=5, wrap=tk.WORD)
        user_notes.pack(side='top')
        buttons_frame = tb.Frame(self)
        buttons_frame.pack(side='bottom', fill=tk.X, expand=1, pady=10)
        tb.Button(buttons_frame, text=get_text("btn_save"), bootstyle="primary",
                  command=lambda: self.save_user_input(
                      master, _label.get(), user_notes.get("1.0", 'end-1c'))
                  ).pack(side='left', fill=tk.X, expand=1, padx=10)
        tb.Button(buttons_frame, text=get_text("btn_exit"),
                  command=self.destroy).pack(side='left', fill=tk.X, expand=1, padx=10)

    def save_user_input(self, master_pyplot, label, notes):
        master_pyplot.change_label(label)
        master_pyplot.add_notes(notes)
        self.destroy()


class UserSelectDataDelete(tb.Toplevel):
    """勾选要删除的曲线后确认删除。"""
    def __init__(self, master):
        tb.Toplevel.__init__(self, master)
        size = len(master.data.label)
        length = 150 + 40 * size
        self.geometry("340x{0}".format(length))
        self.title(get_text("data_select_delete"))
        tb.Label(self, text=get_text("data_delete_hint"),
                 font=("", 12), wraplength=300, justify='left'
                 ).pack(side='top', padx=15, pady=(15, 5), anchor='w')
        list_frame = tb.Frame(self)
        list_frame.pack(side='top', fill=tk.BOTH, expand=1, padx=15)
        choices = []
        for _label in master.data.label:
            var = tk.IntVar()
            choices.append(var)
            tb.Checkbutton(list_frame, text=_label, variable=var
                           ).pack(anchor='w', pady=2)
        button_frame = tb.Frame(self)
        button_frame.pack(side='bottom', fill=tk.X, pady=10)
        tb.Button(button_frame, text=get_text("btn_yes"), width=10, bootstyle="danger",
                  command=lambda: self.send_delete_selection(master, choices)
                  ).pack(side='left', padx=20, expand=1)
        tb.Button(button_frame, text=get_text("btn_no"), width=10,
                  command=self.destroy).pack(side='left', padx=20, expand=1)

    def send_delete_selection(self, master, picks):
        _list = []
        _index = 0
        for pick in picks:
            if pick.get() == 1:
                _list.append(_index)
            _index += 1
        master.delete_some_data(_list)
        self.destroy()


class ChangeDataLegend(tb.Toplevel):
    def __init__(self, _master, graph):
        tb.Toplevel.__init__(self, master=_master)
        self.legend_entries = []
        self.color_picks = []
        tb.Label(self, text=get_text("data_legend_title")).pack(side="top")
        for i in range(_master.data.index):
            horizontal_frame = tb.Frame(self)
            horizontal_frame.pack(side="top")
            tb.Label(horizontal_frame, text=get_text("label_color")).pack(side='left')
            self.color_picks.append(tk.StringVar())
            self.color_picks[i].set(_master.data.colors[i])
            drop_menu = tb.Combobox(horizontal_frame, textvariable=self.color_picks[i],
                                    values=COLOR_CHOICES, state='readonly', width=10)
            drop_menu.pack(side='left')
            tb.Label(horizontal_frame, text=get_text("label_change_label")).pack(side='left')
            self.legend_entries.append(tk.StringVar())
            self.legend_entries[i].set(_master.data.label[i])
            tb.Entry(horizontal_frame,
                     textvariable=self.legend_entries[i]).pack(side="left")
        bottom_frame = tb.Frame(self)
        bottom_frame.pack(side='bottom')
        tb.Button(bottom_frame, text=get_text("btn_save"), width=10, bootstyle="primary",
                  command=lambda: self.save(_master, graph)).pack(
                      side='left', padx=10, fill=tk.X, expand=1)
        tb.Button(bottom_frame, text=get_text("btn_exit"), width=10,
                  command=self.destroy).pack(side='left', padx=10, fill=tk.X, expand=1)

    def save(self, _master, graph):
        i = 0
        for pick in self.color_picks:
            _master.data.colors[i] = pick.get()
            graph.change_line_color(pick.get(), i)
            _master.data.label[i] = self.legend_entries[i].get()
            i += 1
        graph.update_legend()
        self.destroy()


class EnterLoggingInfo(tb.Toplevel):
    def __init__(self, master):
        tb.Toplevel.__init__(self, master)
        self.title(get_text("title_logging"))
        tb.Label(self, text=get_text("msg_logging")).pack(side='top')
        entry = tk.Text(self, width=30, height=6, wrap=tk.WORD)
        entry.pack(side='top')
        button_frame = tb.Frame(self)
        tb.Button(button_frame, text=get_text("btn_save"), width=10, bootstyle="primary",
                  command=lambda: self.save(entry.get("1.0", 'end-1c'))
                  ).pack(side='left', padx=10, fill=tk.X, expand=1)
        tb.Button(button_frame, text=get_text("btn_exit"), width=10,
                  command=self.destroy).pack(side='left', padx=10, fill=tk.X, expand=1)
        button_frame.pack(side='top')

    def save(self, message):
        logging.info("User entered the following message")
        logging.info(message)
        self.destroy()


class EnterCustomTIAResistor(tb.Toplevel):
    def __init__(self, master):
        tb.Toplevel.__init__(self, master)
        self.title(get_text("title_custom_tia"))
        tb.Label(self, text=get_text("custom_tia_desc")).pack(side='top')
        top_frame = tb.Frame(self)
        top_frame.pack(side='top')
        tb.Label(top_frame, text=get_text("custom_tia_value")).pack(side='left')
        _tia_resistor_value = tk.StringVar()
        resistor_value_enter = tb.Entry(top_frame, textvariable=_tia_resistor_value)
        resistor_value_enter.pack(side='left')
        middle_frame = tb.Frame(self)
        middle_frame.pack(side='top')
        button_frame = tb.Frame(self)
        button_frame.pack(side='top')
        tb.Button(button_frame, text=get_text("btn_save"), width=10, bootstyle="primary",
                  command=lambda: self.save(master, resistor_value_enter.get())
                  ).pack(side='left', padx=10, fill=tk.X, expand=1)
        tb.Button(button_frame, text=get_text("btn_exit"), width=10,
                  command=self.destroy).pack(side='left', padx=10, fill=tk.X, expand=1)

    def save(self, master, resistor_value):
        try:
            resistor_value = float(resistor_value)
        except ValueError:
            messagebox.showerror(get_text("title_invalid_params"),
                                 get_text("err_invalid_number"))
            return
        if resistor_value <= 0:
            messagebox.showerror(get_text("title_invalid_params"),
                                 get_text("err_invalid_number"))
            return
        tia = master.device_params.adc_tia
        # set_value 需要完整签名（阻值, 增益, ADC 配置, 量程序号）；
        # 自定义阻值沿用当前量程序号，仅更新换算系数与阻值模型
        tia.set_value(resistor_value, 0, 1, tia.current_option_index)
        master.device.set_custom_resistor_channel('0')
        current_limit = 1200.0 / resistor_value
        master.update_current_range(
            CURRENT_OPTION_LIST[tia.current_option_index], current_limit)
        self.destroy()


class VoltageSourceSelect(tb.Toplevel):
    def __init__(self, master, current_value):
        tb.Toplevel.__init__(self, master=master)
        self.geometry("300x200")
        self.source_selected = None
        self.master = master
        logging.debug('current value: {0}'.format(current_value))
        if current_value == 0:
            _label = get_text("vsource_unknown")
        else:
            _label = get_text("data_default")
        tb.Label(self, text=_label).pack(side='top')
        tb.Button(self, text=get_text("vsource_vdac"), width=20,
                  command=lambda: self.send_selection("VDAC")).pack(side='top')
        tb.Button(self, text=get_text("vsource_dvdac"), width=20,
                  command=lambda: self.send_selection("DVDAC")).pack(side='top')
        tb.Button(self, text=get_text("vsource_unknown"), width=20,
                  command=lambda: self.send_selection("default")).pack(side='top')
        tb.Label(self, text=get_text("msg_restart1")).pack(side='top')
        self.lift()
        self.attributes("-topmost", True)

    def send_selection(self, source_selected):
        logging.info('source: {0}'.format(source_selected))
        if source_selected == 'VDAC':
            self.master.device.select_voltage_source("8-bit DAC")
        elif source_selected == "DVDAC":
            self.master.device.select_voltage_source(source_selected)
        self.source_selected = source_selected
        self.destroy()


class MasterDestroyer(tb.Toplevel):
    def __init__(self, master):
        tb.Toplevel.__init__(self, master=master)
        self.geometry("300x300")
        self.lift()
        self.attributes("-topmost", True)
        tb.Label(self, text=get_text("msg_restart1")).pack(side='top')
        tb.Label(self, text=get_text("msg_restart2")).pack(side='top')
        try:
            # 设备对象不一定有 destroy/close（kit-059 走串口），
            # 重启在即，端口由操作系统回收，失败不阻塞重启
            closer = (getattr(master.device, 'destroy', None)
                      or getattr(master.device, 'close', None))
            if callable(closer):
                closer()
        except Exception:
            pass
        os.execl(sys.executable, sys.executable, *sys.argv)
        tb.Button(self, text=get_text("btn_exit"), width=40,
                  command=lambda: self.destroy()).pack(side='top')


class About(tb.Toplevel):
    def __init__(self, master):
        tb.Toplevel.__init__(self, master=master)
        self.title(get_text("menu_about"))
        # 不设固定尺寸：窗口自适应文字内容（固定 300x300 会裁掉后半段）
        body = tb.Frame(self, padding=24)
        body.pack(fill='both', expand=True)
        tb.Label(body, text=get_text("about_text"),
                 font=("", 11), justify='left').pack()
        self.resizable(False, False)
        self.update_idletasks()
        # 居中于主窗口（主窗口未映射时回退屏幕中心）
        try:
            mx = master.winfo_rootx() + master.winfo_width() // 2
            my = master.winfo_rooty() + master.winfo_height() // 2
            if mx <= 0 or my <= 0:
                raise ValueError("master not mapped")
            self.geometry("+{0}+{1}".format(
                max(mx - self.winfo_reqwidth() // 2, 0),
                max(my - self.winfo_reqheight() // 2, 0)))
        except Exception:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            self.geometry("+{0}+{1}".format(max((sw - self.winfo_reqwidth()) // 2, 0),
                                            max((sh - self.winfo_reqheight()) // 2, 0)))


class ShowLookupTable(tb.Toplevel):
    """开发者菜单"导出 LUT"：从固件读回查找表并弹窗显示（原来是 print 到
    控制台，pythonw 启动时用户根本看不到，等于半死功能）。"""

    COLUMNS = 8  # 每行显示的数值个数

    def __init__(self, master):
        tb.Toplevel.__init__(self, master=master)
        self.title(get_text("lut_title"))
        self.geometry("560x420")
        lut = None
        try:
            if getattr(master.device, 'connected', False):
                lut = master.device.get_look_up_table()
        except Exception as err:
            logging.warning("failed to read LUT: %s", err)
        text = tk.Text(self, wrap='none', font=("Consolas", 10))
        scroll = tb.Scrollbar(self, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(side='left', fill='both', expand=True)
        if not lut:
            text.insert('1.0', get_text("lut_empty"))
        else:
            lines = []
            for start in range(0, len(lut), self.COLUMNS):
                chunk = lut[start:start + self.COLUMNS]
                lines.append("{0:>5}: {1}".format(
                    start, "  ".join("{0:>6}".format(v) for v in chunk)))
            text.insert('1.0', "\n".join(lines))
        text.configure(state='disabled')


def compute_curve_math(op, ya, yb=None):
    """曲线运算纯计算（可单测）：
    subtract=ya−yb；average=(ya+yb)/2；normalize=ya/max|ya|。"""
    if op == 'subtract':
        return [a - b for a, b in zip(ya, yb)]
    if op == 'average':
        return [(a + b) / 2.0 for a, b in zip(ya, yb)]
    if op == 'normalize':
        peak = max(abs(v) for v in ya) or 1.0
        return [v / peak for v in ya]
    raise ValueError("unknown curve op: " + str(op))


class CurveMathDialog(tb.Toplevel):
    """多曲线运算（相减/平均/归一化），结果作为新曲线加入当前页。

    要求参与曲线 x 对齐（同一页同参数扫描天然对齐）；
    长度不一致时按较短者截断。
    """

    def __init__(self, frame):
        tb.Toplevel.__init__(self, master=frame)
        self.title(get_text("curve_math"))
        self.frame = frame
        labels = list(frame.data.label)
        if len(labels) < 1:
            messagebox.showinfo(get_text("curve_math"),
                                get_text("curve_need").format(1))
            self.destroy()
            return

        body = tb.Frame(self, padding=12)
        body.pack(fill='both', expand=True)

        self.op = tk.StringVar(value='subtract')
        ops = [('subtract', "curve_op_subtract"),
               ('average', "curve_op_average"),
               ('normalize', "curve_op_normalize")]
        for i, (value, key) in enumerate(ops):
            tb.Radiobutton(body, text=get_text(key), variable=self.op,
                           value=value).grid(row=0, column=i, sticky='w',
                                             padx=(0, 10))

        self.var_a = tk.StringVar(value=labels[-1])
        self.var_b = tk.StringVar(value=labels[-2] if len(labels) > 1
                                  else labels[-1])
        for row, (text_key, var) in enumerate((("curve_a", self.var_a),
                                               ("curve_b", self.var_b)), start=1):
            tb.Label(body, text=get_text(text_key)).grid(row=row, column=0,
                                                         sticky='w', pady=4)
            state = 'readonly' if (len(labels) > 1 or text_key == "curve_a")                 else 'disabled'
            tb.Combobox(body, textvariable=var, values=labels, width=24,
                        state=state).grid(row=row, column=1, sticky='w')

        button_frame = tb.Frame(body)
        button_frame.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(12, 0))
        tb.Button(button_frame, text=get_text("btn_save"), bootstyle="primary",
                  command=self.apply).pack(side='left', expand=True, padx=4)
        tb.Button(button_frame, text=get_text("btn_exit"),
                  bootstyle="secondary-outline",
                  command=self.destroy).pack(side='left', expand=True, padx=4)
        self.transient(frame)
        self.resizable(False, False)

    def apply(self):
        data = self.frame.data
        labels = list(data.label)
        op = self.op.get()
        name_a, name_b = self.var_a.get(), self.var_b.get()
        ia, ib = labels.index(name_a), labels.index(name_b)
        need = 1 if op == 'normalize' else 2
        if (op != 'normalize' and (ia == ib or len(labels) < 2)) \
                or len(labels) < need:
            messagebox.showinfo(get_text("curve_math"),
                                get_text("curve_need").format(need))
            return
        ya = list(data.current_data[ia])
        xa = list(data.voltage_data[ia])
        if op == 'normalize':
            y = compute_curve_math(op, ya)
            result_name = "{0} ({1})".format(name_a, get_text("curve_op_normalize"))
        else:
            yb = list(data.current_data[ib])
            n = min(len(ya), len(yb))
            y = compute_curve_math(op, ya[:n], yb[:n])
            xa = xa[:n]
            op_text = get_text("curve_op_subtract" if op == 'subtract'
                               else "curve_op_average")
            result_name = "{0} − {1}" if op == 'subtract' else "{0} + {1}"
            result_name = ("{0} − {1}" if op == 'subtract' else "{0} 与 {1} 平均")
            result_name = result_name.format(name_a, name_b)
        self.frame.graph.update_data(xa, y, label=result_name)
        self.destroy()


class CalibrationDialog(tb.Toplevel):
    """标准曲线 / 定量分析（不依赖硬件）。

    - 标准点手动录入，或从曲线 CSV 批量导入（自动寻峰；
      识别不了的格式自动回退"纯数字两列"解析，绝不静默丢弃）；
    - 最小二乘拟合 y = k·x + b（R²），右栏实时显示拟合线；
    - 外标法由信号反算浓度；可选标准加入法（外推信号 0）。
    """

    def __init__(self, master):
        tb.Toplevel.__init__(self, master=master)
        self.title(get_text("calibration_title"))
        self.master = master
        self.points = []          # [(浓度, 信号), ...]，浓度 None = 待补填
        self.rows_meta = []       # 与表格行一一对应：(来源文件名,)
        self.signal_type = tk.StringVar(value='area')
        self.unit = tk.StringVar(value='ppb')
        self.fit_result = None
        self._build()
        self.transient(master)
        self.resizable(True, True)
        self._center_on_master()

    # ---- 界面 ----
    def _build(self):
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(0, weight=1)

        # 左栏：标准点
        left = tb.Labelframe(self, text=get_text("calibration_points_group"),
                             bootstyle='info', padding=8)
        left.grid(row=0, column=0, sticky='nsew', padx=(10, 4), pady=10)
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        type_row = tb.Frame(left)
        type_row.grid(row=0, column=0, sticky='ew', pady=(0, 4))
        tb.Label(type_row, text=get_text("calibration_signal"),
                 bootstyle='secondary').pack(side='left')
        for value, key in (('area', "calibration_signal_area"),
                           ('height', "calibration_signal_height")):
            tb.Radiobutton(type_row, text=get_text(key),
                           value=value, variable=self.signal_type,
                           command=self.refresh_fit).pack(side='left',
                                                          padx=(0, 8))
        tb.Label(type_row, text=get_text("calibration_unit"),
                 bootstyle='secondary').pack(side='left', padx=(10, 2))
        tb.Entry(type_row, textvariable=self.unit, width=7)
        # 单位仅用于显示（Entry 不放 var 之外的复杂逻辑）
        unit_entry = tb.Entry(type_row, width=7)
        unit_entry.insert(0, self.unit.get())
        unit_entry.pack(side='left')

        columns = ('c', 's', 'src')
        self.tree = ttk.Treeview(left, columns=columns, show='headings',
                                 height=9)
        self.tree.heading('c', text=get_text("calibration_conc"))
        self.tree.heading('s', text=get_text("calibration_signal_head"))
        self.tree.heading('src', text=get_text("calibration_source"))
        self.tree.column('c', width=90, anchor='e')
        self.tree.column('s', width=100, anchor='e')
        self.tree.column('src', width=130, anchor='w')
        self.tree.grid(row=1, column=0, sticky='nsew')

        button_row = tb.Frame(left)
        button_row.grid(row=2, column=0, sticky='ew', pady=(6, 2))
        tb.Button(button_row, text=get_text("calibration_import"),
                  command=self.import_curves).pack(side='left', padx=2)
        tb.Button(button_row, text=get_text("calibration_update_conc"),
                  command=self.update_selected_conc).pack(side='left', padx=2)
        tb.Button(button_row, text=get_text("calibration_delete"),
                  command=self.delete_selected).pack(side='left', padx=2)
        tb.Button(button_row, text=get_text("btn_reset"),
                  command=self.clear_all).pack(side='left', padx=2)

        add_row = tb.Frame(left)
        add_row.grid(row=3, column=0, sticky='ew', pady=(2, 0))
        tb.Label(add_row, text=get_text("calibration_conc")).pack(side='left')
        self.entry_c = tb.Entry(add_row, width=9)
        self.entry_c.pack(side='left', padx=(2, 8))
        tb.Label(add_row, text=get_text("calibration_signal_head")).pack(side='left')
        self.entry_s = tb.Entry(add_row, width=11)
        self.entry_s.pack(side='left', padx=(2, 6))
        tb.Button(add_row, text=get_text("calibration_add"),
                  command=self.add_manual).pack(side='left')

        # 右栏：拟合 + 未知样
        right = tb.Frame(self)
        right.grid(row=0, column=1, sticky='nsew', padx=(4, 10), pady=10)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        fit_card = tb.Labelframe(right, text=get_text("calibration_fit_group"),
                                 bootstyle='info', padding=8)
        fit_card.grid(row=0, column=0, sticky='nsew')
        fit_card.rowconfigure(1, weight=1)
        fit_card.columnconfigure(0, weight=1)
        self.fit_label = tb.Label(fit_card, text=get_text("calibration_need_fit"),
                                  font=('', 10, 'bold'), wraplength=280,
                                  justify='left')
        self.fit_label.grid(row=0, column=0, sticky='ew')
        import matplotlib
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        self._fig = matplotlib.figure.Figure(figsize=(3.6, 2.5), dpi=100)
        self._axis = self._fig.add_subplot(111)
        self._canvas_fig = FigureCanvasTkAgg(self._fig, master=fit_card)
        self._canvas_fig.get_tk_widget().grid(row=1, column=0, sticky='nsew')
        self._style_plot()

        unknown_card = tb.Labelframe(right, text=get_text("calibration_unknown_group"),
                                     bootstyle='info', padding=8)
        unknown_card.grid(row=1, column=0, sticky='nsew', pady=(8, 0))
        unknown_card.columnconfigure(0, weight=1)
        row1 = tb.Frame(unknown_card)
        row1.grid(row=0, column=0, sticky='ew')
        tb.Label(row1, text=get_text("calibration_unknown")).pack(side='left')
        self.entry_unknown = tb.Entry(row1, width=11)
        self.entry_unknown.pack(side='left', padx=(2, 6))
        tb.Button(row1, text=get_text("calibration_calc"),
                  command=self.calc_unknown).pack(side='left', padx=2)
        self.addition_var = tk.BooleanVar(value=False)
        tb.Checkbutton(unknown_card, text=get_text("calibration_addition"),
                       variable=self.addition_var).grid(row=1, column=0,
                                                        sticky='w', pady=(4, 2))
        self.unknown_result = tk.StringVar(value='')
        tb.Label(unknown_card, textvariable=self.unknown_result,
                 font=('', 11, 'bold')).grid(row=2, column=0, sticky='w')

    def _style_plot(self):
        from plotting import beautify_axis
        self._axis.set_xlabel(get_text("calibration_conc") + " (" +
                              self.unit.get() + ")")
        self._axis.set_ylabel(get_text("calibration_signal_head"))
        beautify_axis(self._axis)
        self._fig.tight_layout()

    def _center_on_master(self):
        try:
            mx = self.master.winfo_rootx() + self.master.winfo_width() // 2
            my = self.master.winfo_rooty() + self.master.winfo_height() // 2
            self.update_idletasks()
            self.geometry("+{0}+{1}".format(
                max(mx - self.winfo_reqwidth() // 2, 0),
                max(my - self.winfo_reqheight() // 2, 0)))
        except Exception:
            pass

    # ---- 数据操作 ----
    def _signal_of(self, peaks):
        """按信号类型取值：面积 (µA·mV) 或峰高 (µA)。"""
        if not peaks:
            return None
        top = peaks[0]
        return top.get('area', top['height']) \
            if self.signal_type.get() == 'area' else top['height']

    @staticmethod
    def _load_curve_numbers(path):
        """解析曲线 CSV 的 (电压, 电流) 两列。

        依次尝试：本程序格式（含元数据/多曲线列）-> 纯数字两列（无表头的
        老文件）。识别不了时返回 None，由调用方明确报错。
        """
        from utils import load_csv_file_full
        loaded = load_csv_file_full(path)
        if loaded:
            _meta, _header, rows = loaded
            try:
                v = [float(r[0]) for r in rows if len(r) > 1]
                c = [float(r[1]) for r in rows if len(r) > 1]
                if len(v) >= 5 and len(v) == len(c):
                    return v, c
            except (ValueError, IndexError):
                pass
        # 兜底：跳过一切非数字行（老版本无表头/手工整理的文件）
        import csv as _csv
        v, c = [], []
        encodings = ('utf-8-sig', 'gbk')
        for enc in encodings:
            v, c = [], []
            try:
                with open(path, 'r', newline='', encoding=enc) as f:
                    for row in _csv.reader(f):
                        if len(row) < 2:
                            continue
                        try:
                            v.append(float(row[0]))
                            c.append(float(row[1]))
                        except ValueError:
                            continue  # 表头/元数据/备注行
                if len(v) >= 5:
                    return v, c
            except (UnicodeDecodeError, OSError):
                continue
        return None if len(v) < 5 else (v, c)

    def import_curves(self):
        """从若干曲线 CSV 导入标准点（逐个报告成败，绝不静默丢弃）。"""
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(
            filetypes=[(get_text("file_csv"), "*.csv"),
                       (get_text("file_all"), "*.*")])
        if not paths:
            return
        from algorithms import analyze_stripping_peak
        ok, failed = 0, []
        for path in paths:
            name = os.path.basename(path)
            numbers = self._load_curve_numbers(path)
            if not numbers:
                failed.append("{0}: {1}".format(name,
                                                get_text("calibration_bad_format")))
                continue
            v, c = numbers
            peaks, processed = analyze_stripping_peak(v, c)
            signal = self._signal_of(peaks)
            note = ''
            if signal is None:
                # 3σ 找不到峰：放宽为"扣除基线后的最大值"，由用户自行核对
                if not len(processed):
                    failed.append("{0}: {1}".format(
                        name, get_text("calibration_no_data")))
                    continue
                signal = float(max(processed))
                note = get_text("calibration_relaxed")
            self.points.append((None, float(signal)))
            self.rows_meta.append(name)
            self.tree.insert('', 'end', values=(
                get_text("calibration_pending"),
                "{0:.4g}".format(signal),
                name + note))
            ok += 1
        self.refresh_fit()
        if failed:
            messagebox.showwarning(
                get_text("calibration_title"),
                get_text("calibration_import_summary").format(
                    len(paths), ok, len(failed), "\n".join(failed)))
        elif ok:
            messagebox.showinfo(
                get_text("calibration_title"),
                get_text("calibration_import_done").format(ok))

    def update_selected_conc(self):
        """把浓度输入框的值写入选中行（导入后补填浓度用）。"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(get_text("calibration_title"),
                                get_text("calibration_pick_row"))
            return
        try:
            conc = float(self.entry_c.get())
        except (ValueError, tk.TclError):
            messagebox.showerror(get_text("title_invalid_params"),
                                 get_text("err_invalid_number"))
            return
        item = selection[0]
        index = self.tree.index(item)
        if not (0 <= index < len(self.points)):
            return
        self.points[index] = (conc, self.points[index][1])
        _c, signal, source = self.tree.item(item, 'values')
        self.tree.item(item, values=("{0:g}".format(conc), signal, source))
        self.entry_c.delete(0, 'end')
        self.refresh_fit()

    def add_manual(self):
        try:
            conc = float(self.entry_c.get())
            signal = float(self.entry_s.get())
        except (ValueError, tk.TclError):
            messagebox.showerror(get_text("title_invalid_params"),
                                 get_text("err_invalid_number"))
            return
        self.points.append((conc, signal))
        self.rows_meta.append('')
        self.tree.insert('', 'end',
                         values=("{0:g}".format(conc),
                                 "{0:.4g}".format(signal), ''))
        self.entry_c.delete(0, 'end')
        self.entry_s.delete(0, 'end')
        self.refresh_fit()

    def delete_selected(self):
        for item in reversed(self.tree.selection()):
            index = self.tree.index(item)
            self.tree.delete(item)
            if 0 <= index < len(self.points):
                self.points.pop(index)
                self.rows_meta.pop(index)
        self.refresh_fit()

    def clear_all(self):
        self.points.clear()
        self.rows_meta.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.refresh_fit()

    # ---- 拟合与定量 ----
    def refresh_fit(self):
        from calibration import linear_fit
        valid = [p for p in self.points if p[0] is not None]
        if len(valid) < 2:
            self.fit_result = None
            pending = sum(1 for p in self.points if p[0] is None)
            self.fit_label.config(text=get_text("calibration_need_fit") +
                                  (get_text("calibration_pending_hint").format(
                                      pending) if pending else ''))
            return
        try:
            k, b, r2 = linear_fit(valid)
        except ValueError as err:
            self.fit_result = None
            self.fit_label.config(text=str(err))
            return
        self.fit_result = (k, b, r2)
        self.fit_label.config(text=get_text("calibration_fit_text").format(
            k, b, r2))
        self._redraw_plot(k, b)

    def _redraw_plot(self, k, b):
        valid = [p for p in self.points if p[0] is not None]
        xs = [p[0] for p in valid]
        ys = [p[1] for p in valid]
        self._axis.clear()
        self._axis.plot(xs, ys, 'o', color='#2780e3',
                        label=get_text("calibration_points"))
        x_min, x_max = min(xs), max(xs)
        pad = (x_max - x_min) * 0.08 or 1.0
        x_line = [x_min - pad, x_max + pad]
        self._axis.plot(x_line, [k * x + b for x in x_line], '-',
                        color='#e83e8c')
        self._style_plot()
        self._canvas_fig.draw_idle()

    def calc_unknown(self):
        from calibration import (concentration_from_signal,
                                 standard_addition_concentration)
        if self.fit_result is None:
            self.refresh_fit()
        if self.fit_result is None:
            messagebox.showinfo(get_text("calibration_title"),
                                get_text("calibration_need_fit"))
            return
        k, b, _r2 = self.fit_result
        try:
            signal = float(self.entry_unknown.get())
        except (ValueError, tk.TclError):
            messagebox.showerror(get_text("title_invalid_params"),
                                 get_text("err_invalid_number"))
            return
        try:
            if self.addition_var.get():
                conc = standard_addition_concentration(k, b)
                self.unknown_result.set("{0:.4g} {1} ({2})".format(
                    conc, self.unit.get(), get_text("calibration_addition")))
            else:
                conc = concentration_from_signal(k, b, signal)
                self.unknown_result.set("{0:.4g} {1}".format(
                    conc, self.unit.get()))
        except ValueError as err:
            messagebox.showerror(get_text("calibration_title"), str(err))



class ScanProgress(tb.Toplevel):
    """扫描进行中的模态弹窗。

    grab_set() 使弹窗独占输入，扫描期间禁止点击软件其它按钮；
    关闭按钮（右上角 X）被禁用，只能由扫描完成/失败后调用 close() 关闭。
    """

    def __init__(self, master, est_seconds):
        tb.Toplevel.__init__(self, master=master)
        self.title(get_text("scan_progress_title"))
        self.transient(master)
        self.resizable(False, False)
        # 扫描中途关窗会丢失进度提示，禁用右上角关闭
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        tb.Label(self, text=get_text("scan_progress_msg").format(est_seconds),
                 padding=20, justify='center').pack(padx=10, pady=(15, 5))
        self._bar = tb.Progressbar(self, mode='indeterminate', length=280)
        self._bar.pack(padx=20, pady=(5, 20))
        self._bar.start(12)
        # 弹窗居中到主窗口
        self.update_idletasks()
        try:
            mx = master.winfo_rootx() + master.winfo_width() // 2
            my = master.winfo_rooty() + master.winfo_height() // 2
            self.geometry("+{0}+{1}".format(max(mx - self.winfo_reqwidth() // 2, 0),
                                            max(my - self.winfo_reqheight() // 2, 0)))
        except Exception:
            pass
        self.grab_set()  # 模态：屏蔽主窗口的一切点击/按键

    def close(self):
        """扫描结束（成功或失败）后关闭弹窗、释放模态。"""
        try:
            self._bar.stop()
        except Exception:
            pass
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


class TimerToplevel(tb.Toplevel):
    def __init__(self, master, clean_time, plate_time, rest_time=0, scan_est=0):
        tb.Toplevel.__init__(self, master)
        self.transient(master)
        self.geometry("300x300")
        # 弹窗居中到软件主窗口（取主窗口中心，失败退回屏幕中心）
        try:
            self.update_idletasks()
            _mx = master.winfo_rootx() + master.winfo_width() // 2
            _my = master.winfo_rooty() + master.winfo_height() // 2
            if _mx <= 0 or _my <= 0:
                raise ValueError("master not mapped")
            self.geometry("+{0}+{1}".format(max(_mx - 150, 0), max(_my - 150, 0)))
        except Exception:
            try:
                _sw = self.winfo_screenwidth()
                _sh = self.winfo_screenheight()
                self.geometry("+{0}+{1}".format(max((_sw - 300) // 2, 0),
                                                max((_sh - 300) // 2, 0)))
            except Exception:
                pass
        # 禁止点 X 关闭：窗口生命周期由 ASV 流程控制（扫描结束/停止时自动关）
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self._progress_bar = None
        self._scan_after_id = None
        self.scan_est = scan_est      # 扫描段预估秒数，计入总时间
        self._scan_total = 0
        self._scan_left = 0
        tb.Label(self, text=get_text("timer_toplevel")).pack()
        self.clean_time = clean_time
        self.plate_time = plate_time
        self.rest_time = rest_time
        self.stage = 'clean' if clean_time > 0 else 'plate'
        init_key = "time_cleaning_left" if clean_time > 0 else "time_plating_left"
        init_time = clean_time if clean_time > 0 else plate_time
        self.toplabel = tb.Label(self, text=get_text(init_key).format(init_time))
        self.toplabel.pack()
        self.bottomlabel = tb.Label(
            self, text=get_text("time_total_left").format(
                plate_time + clean_time + rest_time + scan_est))
        self.bottomlabel.pack()
        self.after(1000, self.run)

    def set_stage(self, stage):
        """由 ASV 流程在真实阶段切换点调用，显示与实际命令强制对齐。

        倒计时窗是独立秒表，与 after() 阶段链之间存在累计偏差
        （真机踩过：静置已开始、窗口仍显示富集搅拌剩余 2 秒）。
        """
        if self.stage == 'scan':
            return  # 扫描段已接管显示
        if stage == 'rest':
            self.clean_time = 0
            self.plate_time = 0
            if self.rest_time <= 0:
                self.rest_time = 1
            self.stage = 'rest'
            self.toplabel.config(
                text=get_text("time_resting_left").format(self.rest_time))

    def run(self):
        if not self.winfo_exists():
            return
        if self.stage == 'scan':
            # show_scan 已接管显示；残留的定时回调不得再覆盖文字/自我调度，
            # 否则窗口会永远卡在"静置剩余 1.0"
            return
        if self.stage == 'clean':
            self.clean_time -= 1
            if self.clean_time <= 0:
                self.stage = 'plate'
        elif self.stage == 'plate':
            self.plate_time -= 1
            if self.plate_time <= 0:
                self.stage = 'rest' if self.rest_time > 0 else 'done'
        elif self.stage == 'rest':
            self.rest_time -= 1
            if self.rest_time <= 0:
                self.stage = 'done'
        stage_key = {'clean': 'time_cleaning_left',
                     'plate': 'time_plating_left'}.get(self.stage, 'time_resting_left')
        stage_time = {'clean': self.clean_time,
                      'plate': self.plate_time}.get(self.stage, self.rest_time)
        self.toplabel.config(text=get_text(stage_key).format(stage_time))
        remaining = (max(self.clean_time, 0) + max(self.plate_time, 0)
                     + max(self.rest_time, 0) + self.scan_est)
        self.bottomlabel.config(text=get_text("time_total_left").format(remaining))
        if remaining > 0:
            self.after(1000, self.run)

    def show_scan(self, est_seconds):
        """倒计时结束后由 ASV 流程调用：原地切换为扫描阶段（实时倒数）。"""
        try:
            self.stage = 'scan'
            self.scan_est = 0  # 扫描时间已转入倒数，不再计入"总时间剩余"
            self._scan_total = max(1, int(est_seconds))
            self._scan_left = self._scan_total
            self.toplabel.config(
                text=get_text("scan_progress_left").format(self._scan_left))
            self.bottomlabel.config(
                text=get_text("time_total_left").format(self._scan_left))
            if self._progress_bar is None:
                self._progress_bar = tb.Progressbar(
                    self, mode='determinate', maximum=self._scan_total)
                self._progress_bar.pack(fill='x', padx=20, pady=10)
            self._progress_bar.config(maximum=self._scan_total)
            self._progress_bar['value'] = 0
            self._scan_after_id = self.after(1000, self._scan_tick)
        except Exception:
            pass

    def _scan_tick(self):
        """扫描阶段每秒更新一次倒数。"""
        if not self.winfo_exists() or self.stage != 'scan':
            return
        self._scan_left -= 1
        if self._scan_left > 0:
            self.toplabel.config(
                text=get_text("scan_progress_left").format(self._scan_left))
            self.bottomlabel.config(
                text=get_text("time_total_left").format(self._scan_left))
            self._progress_bar['value'] = self._scan_total - self._scan_left
            self._scan_after_id = self.after(1000, self._scan_tick)
        else:
            self.toplabel.config(text=get_text("scan_finishing"))
            self.bottomlabel.config(text="")
            self._progress_bar['value'] = self._scan_total

    def close(self):
        """扫描结束/用户停止时关闭窗口。"""
        try:
            if self._scan_after_id is not None:
                self.after_cancel(self._scan_after_id)
                self._scan_after_id = None
        except Exception:
            pass
        try:
            if self._progress_bar is not None:
                self._progress_bar.stop()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
