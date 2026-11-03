# Copyright (c) 2015-2023 Kyle Lopin (Naresuan University) <kylel@nu.ac.th>
# Licensed under the Creative Commons Attribution-ShareAlike 3.0 (CC BY-SA 3.0 US) License
# Modified by Zhijian Yi (Guangdong, China), 2026 — PSoC-Potentiostat-YI-1.0

"""Experiment frames: CV, Amperometry and ASV panels with their USB handlers."""

import csv
import logging
import math
import os
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

import ttkbootstrap as tb

from config import ADC_SATURATION_COUNTS, max_cv_cycles
from comm import (USB_IN_BYTE_SIZE, COMPLETE_MESSAGE,
                  FAILURE_DELAY)
from i18n import get_text
from plotting import PyplotData, PyplotEmbed
from utils import (make_voltage_profile, make_dpv_x_line, make_cv_x_line,
                   cv_lut_length, open_file, check_display_type)

# 固件 ADC_CHANNELS（globals.h）：计时电流 ISR 攒满一段后在 4 个
# 通道间轮切，Done0..Done3 循环发送；只有选定通道是真实电流
AMP_FW_CHANNELS = 4


def _grid_param_rows(frame, rows):
    """在 frame 上按 (名称key, 值var) 列表铺两列参数行：
    名称左对齐灰色小字，值右对齐加粗。返回名称标签列表（随语言刷新）。"""
    name_labels = []
    for i, (key, var) in enumerate(rows):
        name = tb.Label(frame, text=get_text(key), bootstyle='secondary',
                        anchor='w', font=('', 9))
        name.grid(row=i, column=0, sticky='ew', padx=(0, 8), pady=3)
        value = tb.Label(frame, textvariable=var, anchor='e',
                         font=('', 10, 'bold'))
        value.grid(row=i, column=1, sticky='ew', pady=3)
        name_labels.append(name)
    frame.columnconfigure(0, weight=3)
    frame.columnconfigure(1, weight=2)
    return name_labels


class CVFrame(tb.Frame):
    # 各页面共用的扫描方式映射：页面 -> (sweep_type, use_swv)
    # DPV 数据处理与 SWV 相同（每步 2 个交错采样点、差分），故 use_swv=True；
    # 发参数时按 self.mode 区分 'G'(SWV) / 'P'(DPV) 命令
    MODE_SETTINGS = {'CV': ('CV', False), 'LS': ('LS', False),
                     'SWV': ('LS', True), 'DPV': ('LS', True)}

    def __init__(self, master, parent_notebook, graph_properties,
                 initialize=True, mode='CV'):
        tb.Frame.__init__(self, parent_notebook)
        self.master = master
        self.mode = mode  # 'CV' / 'LS' / 'SWV'
        self.settings = master.device_params.cv_settings
        self._apply_mode()
        print(f"settings: {self.settings}")
        self.data = PyplotData()
        self.columnconfigure(0, weight=1)
        # 右侧参数栏保留最小宽度，窗口偏窄时压缩图区而不是挤掉参数
        self.columnconfigure(1, minsize=250)
        self.rowconfigure(3, weight=1)
        # 布局参考主流电化学工作站（CHI/Autolab/PSTrace）：
        # 顶部命令丝带 → 分隔线 → 左图区（卡片化）/右参数面板。
        # PyplotEmbed 的 master 必须是页面本身（数据/图例回调挂在页面上），
        # 用 grid(in_=) 把它放进图区卡片，不改控件父子关系
        self.command_bar = tb.Frame(self, padding=(10, 6, 10, 2))
        self.command_bar.grid(row=0, column=0, columnspan=2, sticky='ew')
        ttk.Separator(self, orient='horizontal').grid(row=1, column=0,
                                                      columnspan=2, sticky='ew')
        # matplotlib 工具栏独立行：小窗口下也不会被丝带按钮挤没；
        # 默认隐藏（grid_remove 记住位置），点"添加工具栏"时显示
        self.toolbar_row = tb.Frame(self, padding=(10, 0, 10, 0))
        self.toolbar_row.grid(row=2, column=0, columnspan=2, sticky='ew')
        self.toolbar_row.grid_remove()
        self.plot_card = tb.Labelframe(self, text=get_text(self._caption_key()),
                                       bootstyle='info', padding=(4, 2, 4, 4))
        self.plot_card.grid(row=3, column=0, sticky='nsew', padx=(10, 4),
                            pady=(6, 8))
        # 卡片内部网格必须配重，否则图组件停在初始 5 英寸宽度不随卡片拉伸
        self.plot_card.rowconfigure(0, weight=1)
        self.plot_card.columnconfigure(0, weight=1)
        options_frame = tb.Frame(self)
        options_frame.grid(row=3, column=1, sticky='nsew', padx=(4, 10),
                           pady=(6, 8))
        self.graph = self.make_graph_area(master, graph_properties)
        self.graph.grid(in_=self.plot_card, row=0, column=0, sticky='nsew')
        self.device = self.USBHandler(self.graph, master.device, master, self.data,
                                      mode=self.mode)
        self.params_group = tb.Labelframe(options_frame, text=get_text("grp_params"),
                                          bootstyle='info', padding=10)
        self.params_group.pack(side="top", fill=tk.X)
        self.cv_settings_frame = self.CVSettingDisplay(master, self.params_group,
                                                       self.graph, master.device_params,
                                                       self.device, mode=self.mode)
        self.cv_settings_frame.pack(side="top", fill=tk.X)
        if initialize:
            time.sleep(0.4)
            self.device.send_cv_parameters()
            time.sleep(0.1)
            master.device.usb_write("L|3")
            time.sleep(0.1)
        self.make_cv_buttons(self.command_bar, self.graph)

    def _caption_key(self):
        # 图区卡片标题复用页签文案
        return {'CV': 'tab_cv', 'LS': 'tab_ls', 'SWV': 'tab_swv',
                'DPV': 'tab_dpv'}[self.mode]

    def _apply_mode(self):
        """把本页面对应的扫描方式写入共享设置（运行/发参数前调用）。"""
        self.settings.sweep_type, self.settings.use_swv = \
            self.MODE_SETTINGS[self.mode]

    def _run_scan(self):
        self._apply_mode()  # 每个页面固定跑自己的技术，与全局设置无关
        self.device.run_scan(self.graph, self.run_button)

    def make_graph_area(self, master, graph_props):
        if check_display_type() == 'matplotlib':
            current_lim = master.device_params.adc_tia.current_lims
            low_voltage = self.settings.low_voltage
            high_voltage = self.settings.high_voltage
            graph = PyplotEmbed(self.toolbar_row, graph_props.cv_plot, self,
                                current_lim, low_voltage, high_voltage)
            # 三个页面共用 cv_plot，标题无法区分且页签已标明技术，直接去掉图标题
            graph.graph_area.axis.set_title('')
            graph.text_keys = {'title': None,
                               'xlabel': 'axis_voltage',
                               'ylabel': 'axis_current'}
        else:
            raise NotImplementedError
        return graph

    def make_cv_buttons(self, bar, cv_graph):
        # 顶部命令丝带：运行(实心) | 保存/删除(描边) | 数据分析组(描边)
        # ...右侧: 样式/工具栏。matplotlib 工具栏切换显示在本丝带内
        self.run_button = tb.Button(bar, text=self._get_run_button_text(),
                                    bootstyle="success", width=14,
                                    command=self._run_scan)
        self.run_button.pack(side='left', padx=(0, 10))
        self.save_button = tb.Button(bar, text=get_text("cv_save"),
                                     bootstyle="success-outline", width=7,
                                     command=self.save_all_data)
        self.save_button.pack(side='left', padx=3)
        self.delete_button = tb.Button(bar, text=get_text("cv_delete"),
                                       bootstyle="danger-outline", width=7,
                                       command=self.user_select_delete_some_data)
        self.delete_button.pack(side='left', padx=3)
        ttk.Separator(bar, orient='vertical').pack(side='left', fill='y',
                                                   padx=8, pady=2)
        # 数据分析按钮（作用于图上最新一条曲线），按使用流程排列
        self.region_button = tb.Button(bar, text=get_text("btn_select_region"),
                                       bootstyle="secondary-outline", width=9,
                                       command=self.toggle_region_select)
        self.region_button.pack(side='left', padx=3)
        self.smooth_button = tb.Button(bar, text=get_text("btn_smooth"),
                                       bootstyle="secondary-outline", width=7,
                                       command=self.apply_smooth)
        self.smooth_button.pack(side='left', padx=3)
        self.baseline_button = tb.Button(bar, text=get_text("btn_baseline"),
                                         bootstyle="secondary-outline", width=7,
                                         command=self.apply_baseline)
        self.baseline_button.pack(side='left', padx=3)
        self.peaks_button = tb.Button(bar, text=get_text("btn_find_peaks"),
                                      bootstyle="secondary-outline", width=7,
                                      command=self.find_peaks_now)
        self.peaks_button.pack(side='left', padx=3)
        self.restore_button = tb.Button(bar, text=get_text("btn_restore"),
                                        bootstyle="secondary-outline", width=7,
                                        command=self.restore_original)
        self.restore_button.pack(side='left', padx=3)
        # 右侧：打开数据（原先只能从文件菜单进）+ 视图类按钮
        self.toolbar_button = tb.Button(bar, text=get_text("cv_add_toolbar"),
                                        bootstyle="secondary-outline",
                                        command=cv_graph.toolbar_toggle)
        self.toolbar_button.pack(side='right', padx=3)
        self.style_button = tb.Button(bar, text=get_text("cv_change_style"),
                                      bootstyle="secondary-outline",
                                      command=self.change_data_labels)
        self.style_button.pack(side='right', padx=3)
        self.open_button = tb.Button(bar, text=get_text("ribbon_open"),
                                     bootstyle="secondary-outline",
                                     command=self.open_data)
        self.open_button.pack(side='right', padx=3)
        self._region_hint_shown = False

    def restore_original(self):
        """还原最新曲线为平滑/基线扣除之前的原始数据。"""
        from tkinter import messagebox
        if self.graph.restore_latest():
            messagebox.showinfo(get_text("title_peak_analysis"),
                                get_text("restore_done"))
        else:
            messagebox.showinfo(get_text("title_peak_analysis"),
                                get_text("restore_none"))

    def toggle_region_select(self):
        """切换峰区域框选模式（在图上拖选 mV 区间，供基线扣除/寻峰使用）。"""
        active = self.graph.toggle_region_selector()
        self.region_button.config(bootstyle="info" if active else "secondary")
        if active and not self._region_hint_shown:
            self._region_hint_shown = True
            messagebox.showinfo(get_text("region_hint_title"),
                                get_text("region_hint"))

    def apply_smooth(self):
        """Savitzky-Golay 平滑最新曲线。"""
        from algorithms import sg_smooth
        if not self.graph.transform_latest(lambda v, c: (v, sg_smooth(c))):
            logging.info("No curve to smooth")

    def apply_baseline(self):
        """对最新曲线做基线扣除；若已框选峰区域则只用区域外数据拟合基线。"""
        from algorithms import subtract_baseline
        region = self.graph.peak_region

        def _subtract(v, c):
            # subtract_baseline 只返回扣除后的电流
            return v, subtract_baseline(v, c, peak_region=region)
        if not self.graph.transform_latest(_subtract):
            logging.info("No curve for baseline subtraction")

    def get_sweep_rate(self):
        """本页技术的扫速 (V/s)，用于电量换算（ASV 页覆盖）。"""
        return self.settings.sweep_rate

    def find_peaks_now(self):
        """自动寻峰：平滑 + 基线扣除 + 峰检测（含面积/电量），标注并弹窗。"""
        from tkinter import messagebox
        from algorithms import analyze_stripping_peak
        line = self.graph.get_latest_line()
        if line is None:
            messagebox.showinfo(get_text("title_peak_analysis"),
                                get_text("no_peak_found"))
            return
        v, c = line.get_xdata(), line.get_ydata()
        peaks, _ = analyze_stripping_peak(v, c,
                                          peak_region=self.graph.peak_region,
                                          sweep_rate=self.get_sweep_rate())
        self.graph.annotate_peaks(peaks)
        if not peaks:
            messagebox.showinfo(get_text("title_peak_analysis"),
                                get_text("no_peak_found"))
            return
        lines = [get_text("peak_result_full").format(
            i, p['voltage'], p['height'], p['half_width'],
            p.get('area', 0.0), p.get('charge', 0.0))
            for i, p in enumerate(peaks, 1)]
        messagebox.showinfo(get_text("title_peak_analysis"), "\n".join(lines))

    def curve_math(self):
        """多曲线运算（相减/平均/归一化），结果加入当前页。"""
        from dialogs import CurveMathDialog
        CurveMathDialog(self)

    def export_peaks(self):
        """把最新曲线的寻峰结果导出为 CSV（峰电位/峰电流/半峰宽）。"""
        from algorithms import analyze_stripping_peak
        from tkinter import messagebox as _mb
        line = self.graph.get_latest_line()
        if line is None:
            _mb.showinfo(get_text("export_peaks"),
                         get_text("export_peaks_none"))
            return
        v, c = line.get_xdata(), line.get_ydata()
        peaks, _processed = analyze_stripping_peak(
            v, c, peak_region=self.graph.peak_region,
            sweep_rate=self.get_sweep_rate())
        if not peaks:
            _mb.showinfo(get_text("export_peaks"),
                         get_text("no_peak_found"))
            return
        _file = open_file('saveas', return_filename=True)
        if not _file:
            return
        try:
            with open(_file, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f, lineterminator='\n')
                writer.writerow([get_text("export_peaks")])
                writer.writerow(["#", get_text("axis_voltage") + " (mV)",
                                 get_text("axis_current") + " (µA)",
                                 get_text("peak_half_width") + " (mV)",
                                 "面积 (µA·mV)", "电量 (µC)"])
                for i, p in enumerate(peaks, 1):
                    writer.writerow([i, round(p['voltage'], 2),
                                     round(p['height'], 4),
                                     round(p['half_width'], 2),
                                     round(p.get('area', 0.0), 4),
                                     round(p.get('charge', 0.0), 6)])
            logging.info("peaks exported: %s", _file)
        except Exception:
            logging.exception("peak export failed")
            _mb.showerror(get_text("save_error_title"), "export failed")

    def get_metadata(self):
        """生成保存到 CSV 头部的参数信息（ASV 页覆盖此方法）。"""
        s = self.master.device_params.cv_settings
        return {
            "experiment": getattr(self, 'mode', 'CV'),
            "start_voltage_mV": s.start_voltage,
            "end_voltage_mV": s.end_voltage,
            "sweep_rate_V/s": s.sweep_rate,
            "use_swv": s.use_swv,
            "swv_inc_mV": s.swv_inc,
            "swv_height_mV": s.swv_height,
            "swv_period_ms": s.swv_period,
            "current_range": self.master.device_params.adc_tia.current_lims,
        }

    def _get_run_button_text(self):
        return get_text({'CV': 'cv_run', 'LS': 'ls_run',
                         'SWV': 'swv_run', 'DPV': 'dpv_run'}[self.mode])

    def refresh_language(self):
        # Refresh all UI text on this panel when the language changes.
        if getattr(self.device, 'is_running', False):
            self.run_button.config(text=get_text("asv_stop"))
        else:
            self.run_button.config(text=self._get_run_button_text())
        self.save_button.config(text=get_text("cv_save"))
        self.style_button.config(text=get_text("cv_change_style"))
        self.delete_button.config(text=get_text("cv_delete"))
        self.open_button.config(text=get_text("ribbon_open"))
        self.toolbar_button.config(text=get_text("cv_add_toolbar"))
        self.region_button.config(text=get_text("btn_select_region"))
        self.smooth_button.config(text=get_text("btn_smooth"))
        self.baseline_button.config(text=get_text("btn_baseline"))
        self.peaks_button.config(text=get_text("btn_find_peaks"))
        self.restore_button.config(text=get_text("btn_restore"))
        if getattr(self, 'params_group', None) is not None:
            self.params_group.config(text=get_text("grp_params"))
        if getattr(self, 'plot_card', None) is not None:
            self.plot_card.config(text=get_text(self._caption_key()))
        self.cv_settings_frame.refresh_language()
        self.graph.refresh_language()

    def change_data_labels(self):
        from dialogs import ChangeDataLegend
        ChangeDataLegend(self, self.graph)

    def delete_some_data(self, list_of_index_to_delete):
        for index in reversed(list_of_index_to_delete):
            self.graph.delete_a_line(index)

    def save_all_data(self):
        logging.debug("saving all data")
        if self.data.index == 0:
            logging.info("No data to save")
            return
        _file = open_file('saveas')
        if _file:
            self.data.save_all_data(_file, self.master.data_save_type,
                                    self.get_metadata())

    def autosave_scan(self, x_line, currents, backup_dir=None):
        """每次扫描完成自动备份一份 CSV 到程序目录下的 autosave_backup/ 文件夹。

        文件格式与手动“保存数据”一致（元数据注释行 + voltage 表头 + 备注行），
        可直接用 文件->打开数据 重新载入；备份失败只记日志，不影响主流程。
        """
        try:
            if backup_dir is None:
                backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "autosave_backup")
            os.makedirs(backup_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            metadata = self.get_metadata()
            experiment = str(metadata.get('experiment', getattr(self, 'mode', 'CV')))
            path = os.path.join(backup_dir, "{0}_{1}.csv".format(stamp, experiment))
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f, lineterminator='\n')
                for key, value in metadata.items():
                    writer.writerow(["# {0}: {1}".format(key, value)])
                writer.writerow(["voltage", "{0}_{1}".format(experiment, stamp)])
                writer.writerow([" ", "autosave"])
                for volt, cur in zip(x_line, currents):
                    writer.writerow([volt, cur])
            logging.info("autosaved scan backup: %s", path)
            return path
        except Exception:
            logging.exception("autosave failed")
            return None

    def delete_all_data(self):
        self.graph.delete_all_lines()
        # 原地清空，不能换新对象：graph/device 都引用着同一个
        # PyplotData，换对象会断链（删后扫描能画但保存报无数据）
        self.data.reset()

    def user_select_delete_some_data(self):
        from dialogs import UserSelectDataDelete
        UserSelectDataDelete(self)

    def open_data(self, header, data_rows):
        """从 load_csv_file 返回的 (表头, 数据行) 载入曲线。"""
        logging.debug("opening data in cv frame")
        _data_hold = [[] for _ in range(len(header))]
        for row in data_rows:
            if len(row) < len(header):
                continue
            for i in range(len(header)):
                _data_hold[i].append(float(row[i]))
        if not _data_hold[0]:
            return
        for i in range(1, len(header)):
            self.graph.update_data(_data_hold[0], _data_hold[i], label=header[i])

    def set_tia_current_lim(self, _value, current_limit):
        self.cv_settings_frame.set_current_var_str(_value)
        self.graph.resize_y(current_limit)

    class USBHandler(object):
        def __init__(self, graph, device, master, data, mode='CV'):
            self.graph = graph
            self.device = device
            self.master = master
            self.data = data
            self.params = master.device_params
            self.mode = mode  # 'CV'/'LS'/'SWV'/'DPV'，决定是否需要重发扫描参数
            self.settings = master.device_params.cv_settings
            self.usb_packet_count = 0
            self.run_chrono = False
            self.run_button = None
            self._progress = None
            # 统一弹窗规则：与 ASV 页共用 TimerToplevel（计时/扫描合一窗）
            self.timer_window = None
            self._poll_after_id = None   # 轮询回调 id，停止时取消
            self.is_running = False
            self._saved_button_cmd = None

        def _estimate_scan_seconds(self):
            """估算扫描耗时（秒），仅用于进度弹窗提示，不保证精确。"""
            s = self.settings
            # CV 设置用 start/end_voltage（low/high 只是镜像缓存）；
            # ASV 设置是独立类，同样提供 start_voltage，
            # 但其真实扫描区间是 low_voltage(=富集电位)~high_voltage(=终止电位)，
            # start_voltage 已在 ASVSettings 中同步为 low_voltage
            if hasattr(s, 'start_voltage'):
                start, end = s.start_voltage, s.end_voltage
            else:
                start, end = s.low_voltage, s.high_voltage
            range_mv = abs(end - start)
            delay_s = getattr(s, 'delay_time', 0) / 1000.0
            sweep_type = getattr(s, 'sweep_type', 'CV')
            if getattr(s, 'use_swv', False) or sweep_type in ('SWV', 'DPV'):
                # 方波/差分脉冲：步数 × 每步周期
                if hasattr(s, 'plate_time'):
                    # ASV 设置是独立类（无 swv_inc/swv_period 字段），
                    # 必须用 pulse_inc/pulse_width，
                    # 否则会误读 swv_inc/swv_period 默认值导致估算翻倍
                    inc = getattr(s, 'pulse_inc', 1)
                    period_ms = getattr(s, 'pulse_width', 10)
                else:
                    inc = getattr(s, 'swv_inc', getattr(s, 'pulse_inc', 1))
                    period_ms = getattr(s, 'swv_period', getattr(s, 'pulse_width', 10))
                # 固件对 'L' 型扫描（本软件 SWV/DPV 均为此类）只扫单段
                # start→end，每步 1 个周期；delay_time 在 SWV 下本身就是
                # 全程扫描时间，不能再加一遍
                return (range_mv / max(float(inc), 0.001)) * period_ms / 1000.0 + 1.0
            rate = max(getattr(s, 'sweep_rate', 0.1), 1e-6)  # V/s
            one_way = (range_mv / 1000.0) / rate
            if sweep_type == 'CV':
                # CV 三角波往返 = 2×，再乘循环圈数
                cycles = max(1, int(getattr(s, 'cycles', 1)))
                return delay_s + cycles * 2 * one_way
            return delay_s + one_way

        def _cycle_scan_seconds(self):
            """GUI 逐圈调度时单圈耗时（固件多圈模式无需分圈）。"""
            total = self._estimate_scan_seconds()
            if self.mode == 'CV':
                cycles = max(1, int(getattr(self.settings, 'cycles', 1)))
                if cycles > 1:
                    return total / cycles
            return total

        def _show_progress(self, extra_seconds=0.0):
            """统一弹窗：所有页面共用 TimerToplevel（计时/扫描合一窗，屏幕居中）。
            CV/LS/SWV 无前置阶段，窗口直接以扫描倒计时状态出现。
            extra_seconds：扫描前静置时长，计入倒计时总数（否则静置期
            会先消耗倒计时，显示提前归零）。"""
            try:
                from dialogs import TimerToplevel
                est = max(1, int(round(self._estimate_scan_seconds()
                                       + float(extra_seconds))))
                self.timer_window = TimerToplevel(self.master, 0, 0, 0, scan_est=0)
                self.timer_window.show_scan(est)
            except Exception:
                logging.exception("failed to show unified scan window")
                self.timer_window = None

        def _close_progress(self):
            tw, self.timer_window = getattr(self, 'timer_window', None), None
            if tw is not None:
                try:
                    tw.close()
                except Exception:
                    pass

        def _enter_scan_state(self):
            """扫描期间：运行按钮变为「停止」，点击即中断（与 ASV 页同一规则）。"""
            self.is_running = True
            if self.run_button is None:
                return
            try:
                self._saved_button_cmd = self.run_button.cget('command')
            except Exception:
                self._saved_button_cmd = None
            try:
                self.run_button.state(['!disabled'])
                # CV/LS/SWV/DPV 扫描中统一显示通用「停止」；
                # asv_stop 是 ASV 页专属文案（曾误用导致 CV 扫描显示"停止 ASV"）
                self.run_button.config(text=get_text("stop"), command=self.stop,
                                       bootstyle="danger")
            except Exception:
                pass

        def _exit_scan_state(self):
            """扫描结束/停止/失败：恢复运行按钮原样。"""
            self.is_running = False
            if self.run_button is None:
                return
            try:
                self.run_button.state(['!disabled'])
                if self._saved_button_cmd is not None:
                    self.run_button.config(command=self._saved_button_cmd)
                    self._saved_button_cmd = None
                self.run_button.config(
                    text=get_text({'CV': 'cv_run', 'LS': 'ls_run',
                                   'SWV': 'swv_run', 'DPV': 'dpv_run'}.get(self.mode, 'cv_run')),
                    bootstyle="success")
            except Exception:
                pass

        def stop(self):
            """扫描中点击停止：取消轮询、复位设备、关窗、恢复按钮。"""
            self.is_running = False
            if self._poll_after_id is not None:
                try:
                    self.master.after_cancel(self._poll_after_id)
                except Exception:
                    pass
                self._poll_after_id = None
            self._close_progress()
            try:
                self.device.usb_write('X')
            except Exception:
                pass
            self._exit_scan_state()

        def send_cv_parameters(self):
            logging.debug("sending cv params here")
            formatted_start_volt, start_dac_value =                 self.format_voltage_with_gnd(self.settings.start_voltage)
            formatted_end_volt, end_dac_value =                 self.format_voltage_with_gnd(self.settings.end_voltage)
            formatted_freq_divider, pwm_period =                 self.format_divider(self.settings.sweep_rate)
            self.params.PWM_period = pwm_period
            sweep_type_to_send = self.settings.sweep_type[0] +                                  self.settings.sweep_start_type[0]
            if self.mode == 'DPV':
                # 真 DPV 走自研固件 'P' 命令（[脉冲,基底] 成对，50% 占空比），
                # 字段布局与 SWV 的 'G' 一致：P|start|end|步进4|幅值4|周期5|类型
                formatted_inc, increment = self.format_voltage(self.settings.swv_inc)
                formatted_dpv_height, dpv_height =                     self.format_voltage(self.settings.swv_height)
                formatted_freq_divider, pwm_period =                     self.format_divider(int(self.settings.swv_period / 2))
                to_amp_device = '|'.join(["P", formatted_start_volt, formatted_end_volt,
                                          formatted_inc, formatted_dpv_height,
                                          formatted_freq_divider, sweep_type_to_send])
            elif self.settings.use_swv:
                formatted_inc, increment = self.format_voltage(self.settings.swv_inc)
                formatted_swv_height, swv_height =                     self.format_voltage(self.settings.swv_height)
                formatted_freq_divider, pwm_period =                     self.format_divider(int(self.settings.swv_period / 2))
                print(f"pwm period: {pwm_period}, freq divider: {formatted_freq_divider}, "
                      f"swv period: {self.settings.swv_period}")
                to_amp_device = '|'.join(["G", formatted_start_volt, formatted_end_volt,
                                          formatted_inc, formatted_swv_height,
                                          formatted_freq_divider, sweep_type_to_send])
            else:
                to_amp_device = '|'.join(["S", formatted_start_volt,
                                          formatted_end_volt, formatted_freq_divider,
                                          sweep_type_to_send])
                increment = 1
                # CV 循环圈数：固件补丁解析尾缀 "|N"（data_buffer[21]），
                # LUT 把 N 圈全装进波形表（约 5000 点封顶）。圈数超出容量时
                # 显式发 "|1"（固件缓冲残留会让缺省尾缀继承上次的 N，
                # 真机实测），由 run_scan 探测后自动用 GUI 逐圈调度跑满
                # N 圈（圈间停约 1 s）——不拒绝用户的圈数设置
                cycles = max(1, int(getattr(self.settings, 'cycles', 1)))
                self._cycles_over_capacity = False
                if self.mode == 'CV' and cycles > 1:
                    capacity = max_cv_cycles(
                        self.settings.start_voltage, self.settings.end_voltage,
                        self.settings.sweep_start_type, self.params.dac)
                    if cycles <= capacity:
                        to_amp_device += '|{0}'.format(min(cycles, 9))
                    else:
                        to_amp_device += '|1'
                        self._cycles_over_capacity = True
                        logging.info("cycles %d exceeds fw LUT capacity %d, "
                                     "sending |1 and using GUI per-cycle loop",
                                     cycles, capacity)
            print(end_dac_value, start_dac_value, increment)
            self.params.actual_low_volt = (- start_dac_value + start_dac_value
                                           % self.params.dac.voltage_step_size)
            time.sleep(0.5)
            self.device.usb_write(to_amp_device)
            if (not self.settings.use_swv and self.mode == 'CV'
                    and max(1, int(getattr(self.settings, 'cycles', 1))) > 1):
                # 发完 'S' 立即回读 LUT 长度判断固件是否支持多圈，
                # 包数按实际实现方式计算（多圈 LUT 或单圈×GUI 调度）
                if getattr(self, '_cycles_over_capacity', False):
                    # 圈数超出 LUT 容量：已按单圈发送（见上），固件多圈
                    # 探测无意义，直接走 GUI 逐圈调度跑满全部圈数
                    self._fw_multi_cycle = False
                else:
                    # 发完 'S' 立即回读 LUT 长度判断固件是否支持多圈，
                    # 包数按实际实现方式计算（多圈 LUT 或单圈×GUI 调度）
                    self._fw_multi_cycle = self._probe_fw_multi_cycle(
                        start_dac_value, end_dac_value)
                expected_n, expected_1 = cv_lut_length(
                    start_dac_value, end_dac_value,
                    self.settings.sweep_start_type,
                    max(1, int(getattr(self.settings, 'cycles', 1))))
                stream_int16 = (expected_n if self._fw_multi_cycle else expected_1) + 1
                packet_count = stream_int16 / (float(USB_IN_BYTE_SIZE) / 2.0)
                self.usb_packet_count = max(1, int(math.ceil(packet_count)))
            elif self.settings.use_swv:
                # 固件对 'L' 型 SWV（本软件发送的后缀）只扫单段 start→end，
                # 每步 2 个采样（正/反脉冲各一）
                n_scan = abs(end_dac_value - start_dac_value) // increment + 1
                total_samples = 2 * n_scan
                packet_count = float(total_samples) / (USB_IN_BYTE_SIZE / 2.0)
                self.usb_packet_count = int(packet_count) + (packet_count % 1 > 0)
            else:
                packet_count = ((2 * abs(end_dac_value - start_dac_value + 1) / increment)
                                / (float(USB_IN_BYTE_SIZE) / 2.0))
                self.usb_packet_count = int(packet_count) + (packet_count % USB_IN_BYTE_SIZE > 0)
            time.sleep(0.01)
            compare_value = pwm_period / 2
            print("test2")
            self.device.write_timer_compare(compare_value)
            return 1

        def _probe_fw_multi_cycle(self, start_dac_value, end_dac_value):
            """'g' 命令回读固件波形表长度，判断多圈 CV 由固件 LUT 连续循环
            （新固件，零间隙）还是需要 GUI 逐圈调度（旧固件）。
            回读失败/长度不认识时按旧固件处理（GUI 调度兜底）。"""
            try:
                got = self.device.get_lut_length()
            except Exception:
                logging.exception("lut length probe failed")
                return False
            if got is None:
                return False
            expected_n, expected_1 = cv_lut_length(
                start_dac_value, end_dac_value, self.settings.sweep_start_type,
                max(1, int(getattr(self.settings, 'cycles', 1))))
            logging.info("lut length probe: got %s, expect N-cycle %s / 1-cycle %s",
                         got, expected_n, expected_1)
            if abs(got - expected_n) <= 2:
                self.params.fw_cv_multi_cycle = True
                return True
            if abs(got - expected_1) <= 2:
                self.params.fw_cv_multi_cycle = False
                return False
            logging.warning("unexpected lut length %s, fallback to gui loop", got)
            self.params.fw_cv_multi_cycle = False
            return False

        def update_adc_setting(self):
            pass

        def _scan_delay_ms(self):
            """首次轮询前的等待时间（按页面模式算）。

            固件对 'L' 型扫描只走单段 start→end，但 cv_settings.delay_time
            的通用公式按往返算了 2×（LS/SWV/DPV 页白等一倍时间——用户实测
            "独立页比 ASV 慢"的根因）。这里按模式修正：
            CV 三角波往返 = 2×（固件多圈再乘圈数；GUI 逐圈只等首圈），
            LS 单段 = 1×，SWV/DPV = 步数 × 每步周期。"""
            s = self.params.cv_settings
            range_mv = abs(s.end_voltage - s.start_voltage)
            if self.mode in ('SWV', 'DPV'):
                return int(s.swv_period * range_mv / max(float(s.swv_inc), 0.001))
            if self.mode == 'LS':
                return int(range_mv / max(s.sweep_rate, 1e-6))
            cycles = max(1, int(getattr(s, 'cycles', 1)))
            if (self.mode == 'CV' and cycles > 1
                    and not getattr(self, '_fw_multi_cycle', False)):
                # GUI 逐圈调度：首圈扫完即可来取，等满 N 圈会白等
                return int(s.delay_time)
            return int(s.delay_time) * cycles  # CV：往返 2× 已含在 delay_time

        def run_scan(self, canvas, run_button, _delay=None):
            # 三种扫描共用设备：页面模式与固件当前配置不同就重发参数
            if self.device.last_experiment != self.mode:
                self.params.cv_settings.sweep_type, self.params.cv_settings.use_swv = \
                    CVFrame.MODE_SETTINGS[self.mode]
                self.send_cv_parameters()
                self.device.set_last_run(self.mode)
            self.run_button = run_button
            self._enter_scan_state()  # 扫描中按钮变「停止」，不再禁用
            # CV 多圈：固件 LUT 不支持时改用 GUI 逐圈调度
            #（首圈已随本次 'R' 运行，扫完自动接续下一圈）
            self._multi_cycle_buffer = None
            self._loop_cycles_left = 0
            cycles = max(1, int(getattr(self.settings, 'cycles', 1)))
            if (self.mode == 'CV' and cycles > 1
                    and not getattr(self, '_fw_multi_cycle', False)):
                self._loop_cycles_left = cycles
                # 计数语义 = 还需抓取的圈数（含首圈）：每次 Done 抓取
                # 当前圈后减一，归零才收尾出图。曾误写 cycles - 1，
                # 导致设 N 圈只出 N-1 圈（2 圈变 1 圈，真机踩过）
                if getattr(self, '_cycles_over_capacity', False):
                    # 圈数超出波形表容量：明确告知用户本次走逐圈调度，
                    # 全自动跑满 N 圈（勿用 showwarning 级别，非异常）
                    logging.info("capacity fallback notice shown for %d cycles",
                                 cycles)
                    try:
                        messagebox.showinfo(
                            get_text("cv_capacity_fallback_title"),
                            get_text("cv_capacity_fallback_msg").format(
                                cycles,
                                max_cv_cycles(
                                    self.settings.start_voltage,
                                    self.settings.end_voltage,
                                    self.settings.sweep_start_type,
                                    self.params.dac)))
                    except Exception:
                        pass
                self._multi_cycle_buffer = []
                logging.info("fw multi-cycle unsupported, using gui loop: %d cycles",
                             cycles)
            # 预处理静置（等效 CHI 静置时间）：先跳到起始电位等暂态衰减
            # 再启动扫描。电位大跳变后的慢衰减电流无法被脉冲差分抵消
            # （SWV/DPV 起始巨大负漂），也会叠加在 LS/CV 起始基线上。
            # 时长可在设置窗口调整，0 = 关闭；ASV 页由 rest 阶段等效覆盖。
            # 未连接时维持旧行为：立即发 R（接线语义：点运行必出启动指令）
            hold_ms = 0
            if self.device.connected and self.mode in ('CV', 'LS', 'SWV', 'DPV'):
                hold_s = max(0.0, float(getattr(self.settings,
                                                'quiet_time_s', 3.0)))
                if hold_s > 0:
                    hold_mv = (0 if self.settings.sweep_start_type == 'Zero'
                               else self.settings.start_voltage)
                    self.device.usb_write('D|{0}'.format(
                        self.format_voltage_with_gnd(hold_mv)[0]))
                    hold_ms = int(hold_s * 1000)
            if hold_ms == 0:
                # 无静置（或未连接）：立即 R 并直接进入轮询
                self.device.usb_write('R')
                if not self.device.connected:
                    self._exit_scan_state()
                    logging.debug("Couldn't find out endpoint to send message to run")
                    return
                self._show_progress()
                logging.debug("device reading")
                if not _delay:
                    _delay = int(200 + self._scan_delay_ms())
                self._scan_deadline = (time.time() + int(_delay) / 1000.0
                                       + 1.5 * self._estimate_scan_seconds() + 5.0)
                self._poll_after_id = self.master.after(
                    int(_delay), lambda: self.run_scan_continue(canvas))
                return
            # 有静置：进度窗先出现（静置计入倒计时），到点再发 'R'
            self._show_progress(hold_ms / 1000.0)
            self._poll_after_id = self.master.after(
                hold_ms, lambda: self._start_lut_scan(canvas, _delay))

        def _start_lut_scan(self, canvas, _delay=None):
            """静置结束发 'R' 并开始轮询；静置中点了停止则直接放弃。"""
            if not self.is_running:
                return
            self.device.usb_write('R')
            logging.debug("device reading")
            if not _delay:
                _delay = int(200 + self._scan_delay_ms())
            # 轮询截止 = 首次等待 + 1.5×预估扫描时间 + 5s 余量。
            # 不能固定只重试 2 次：delay_time 估算偏小时会在扫描
            # 结束前放弃，表现为"跑完没有曲线"（真机 SWV 踩过）
            self._scan_deadline = (time.time() + int(_delay) / 1000.0
                                   + 1.5 * self._estimate_scan_seconds() + 5.0)
            self._poll_after_id = self.master.after(
                int(_delay), lambda: self.run_scan_continue(canvas))

        def run_scan_continue(self, canvas, fail_count=0):
            if not self.is_running:
                return  # 用户已停止，丢弃迟到的轮询回调
            check_message = self.device.usb_read_data(encoding='str')
            print(f"got check message: {check_message}")
            if check_message and COMPLETE_MESSAGE in check_message:
                self._poll_after_id = None
                if self._loop_cycles_left > 0:
                    self._fetch_one_cycle(canvas)
                else:
                    self.get_and_display_data(canvas)
            elif time.time() < getattr(self, '_scan_deadline', 0):
                # 设备可能还没扫完，稍后再查（直到超过截止时间才放弃）
                self._poll_after_id = self.master.after(
                    FAILURE_DELAY, lambda: self.run_scan_continue(canvas, fail_count + 1))
            else:
                # 超时：关弹窗、恢复按钮，否则界面会一直卡住
                self._poll_after_id = None
                self._close_progress()
                self._exit_scan_state()
                logging.error("Failed to run the scan")

        def _fetch_one_cycle(self, canvas):
            """GUI 逐圈调度（旧固件多圈 CV）：取回当前圈数据并接续下一圈。

            圈间电极停在起始电位（约 GUI 轮询周期量级），对 CV 相当于
            每圈开始前多静置片刻；固件 LUT 多圈则零间隙连续循环。"""
            try:
                self.device.usb_write('E0')
                raw = self.device.get_data(self.usb_packet_count)
                raw.pop(0)
                converted = self.device.process_data(raw)
                if converted:
                    self._multi_cycle_buffer.append(converted)
                self._loop_cycles_left -= 1
                if self._loop_cycles_left > 0:
                    self.device.usb_write('R')
                    self._poll_after_id = self.master.after(
                        FAILURE_DELAY, lambda: self.run_scan_continue(canvas))
                else:
                    self.get_and_display_data(canvas)
            except Exception as err:
                logging.exception("multi-cycle fetch failed")
                self._close_progress()
                self._exit_scan_state()
                try:
                    messagebox.showerror(get_text("asv_data_error_title"),
                                         get_text("asv_data_error_msg").format(err))
                except Exception:
                    pass

        def get_and_display_data(self, canvas, _channel=None):
            try:
                self._get_and_display_data(canvas, _channel)
            except Exception as err:
                # tk 回调里的异常会被静默吞掉，必须显式报错（与 ASV 页同一规则）
                logging.exception("get_and_display_data failed")
                try:
                    messagebox.showerror(get_text("asv_data_error_title"),
                                         get_text("asv_data_error_msg").format(err))
                except Exception:
                    pass
            finally:
                self._close_progress()
                self._exit_scan_state()

        def _get_and_display_data(self, canvas, _channel=None):
            if not _channel:
                _channel = self.params.adc_tia.adc_channel
            buffer = getattr(self, '_multi_cycle_buffer', None)
            if buffer:
                # GUI 逐圈调度：各圈数据已取回，拼接成一条曲线。
                # 后续各圈首点与前圈末点同电位（圈间停在起始电位），
                # 与 make_cv_x_line 的分段去重规则一致，拼前去重，
                # 否则总点数比 x 轴多 1×(N-1)，末点又被截断丢弃
                merged = []
                for i, part in enumerate(buffer):
                    merged.extend(part if i == 0 else part[1:])
                self.data = merged
                self._multi_cycle_buffer = None
                raw_data = None
                is_swv = False
            else:
                self.device.usb_write('E' + str(_channel))
                if self.run_chrono:
                    self.usb_packet_count = 125
                raw_data = self.device.get_data(self.usb_packet_count)
                print(f'raw data got {self.usb_packet_count} packets')
                print(f"len raw data: {len(raw_data)}")
                is_swv = self.params.cv_settings.use_swv
                if is_swv:
                    # SWV 数据正/反向成对，第一个点是有效采样，不能丢
                    # （CV/LS 的第一个点是上电残留垃圾值，只对非 SWV 丢弃）
                    logging.debug("swv raw data: %d samples", len(raw_data))
                else:
                    raw_data.pop(0)
                if not raw_data:
                    return
                # 差分方向必须跟随扫描方向：上行扫描（终止>起始）时
                # 固件首采样在电位低侧，前减后会把阳极峰画成向下的负峰
                ascending = (self.params.cv_settings.end_voltage
                             > self.params.cv_settings.start_voltage)
                self.data = self.device.process_data(raw_data, swv=is_swv,
                                                     ascending=ascending)
                self._warn_if_saturated(raw_data)
            print(f"processed data: {self.data}")
            if is_swv:
                # 固件 'L' 型 SWV 单段扫描：差分后每步 1 点，
                # X 轴是阶梯基底电位（与 ASV DPV/SWV 同规则），
                # 不能用 make_voltage_profile：它不识别 SWV 的点数规则
                s = self.params.cv_settings
                inc = float(s.swv_inc)
                x_line = make_dpv_x_line(s.start_voltage, s.end_voltage, inc)
                if len(x_line) != len(self.data):
                    # 固件与上位机点数不一致时对齐到较短者，避免画图崩溃
                    logging.warning("x/data length mismatch: %d vs %d, truncating",
                                    len(x_line), len(self.data))
                    n = min(len(x_line), len(self.data))
                    x_line = x_line[:n]
                    self.data = self.data[:n]
            else:
                increment = self.params.dac.voltage_step_size
                s = self.params.cv_settings
                cycles = max(1, int(getattr(s, 'cycles', 1)))
                if self.mode == 'CV' and cycles > 1:
                    x_line = make_cv_x_line(
                        s.start_voltage, s.end_voltage, increment,
                        s.sweep_start_type, cycles,
                        per_cycle=not getattr(self, '_fw_multi_cycle', False))
                else:
                    x_line = make_voltage_profile(
                        s.start_voltage, s.end_voltage,
                        increment, s.sweep_type,
                        s.sweep_start_type, None)
            if not self.run_chrono and len(x_line) != len(self.data):
                # 端点/首点规则差异导致的 1~2 点偏差不致命：对齐到较短者
                logging.warning("x/data length mismatch: %d vs %d, truncating",
                                len(x_line), len(self.data))
                n = min(len(x_line), len(self.data))
                x_line = x_line[:n]
                self.data = self.data[:n]
            print(f"xline: {x_line}")
            print(f"len xline: {len(x_line)}")
            if self.run_chrono:
                x_line = range(4001)
            canvas.update_data(x_line, self.data, raw_data)
            # 自动备份扫描数据，防止忘记手动保存导致数据丢失
            canvas.master.autosave_scan(x_line, self.data)

        def _warn_if_saturated(self, raw_data, ratio=0.05):
            """检测 ADC 削顶（"平头峰"的根因）：采样触及 ADC 满量程说明
            电流量程太小，数据本身被削平——调整显示范围无法恢复，
            必须换更大量程（更不敏感）后重扫。"""
            if not raw_data:
                return
            sat = sum(1 for x in raw_data if abs(x) >= ADC_SATURATION_COUNTS)
            if sat / len(raw_data) < ratio:
                return
            logging.warning("adc saturation: %d/%d samples at rail", sat, len(raw_data))
            try:
                messagebox.showwarning(
                    get_text("saturation_title"),
                    get_text("saturation_msg").format(
                        int(round(100.0 * sat / len(raw_data)))))
            except Exception:
                pass

        def format_divider(self, _sweep_rate):
            clk_freq = self.params.clk_freq_isr_pwm
            # 必须读本页自己的 settings，不能读 params.cv_settings：
            # ASV 处理器继承本方法，若读 CV 页的 use_swv/swv_period，
            # 只要之前跑过一发 SWV（use_swv 留在 True），ASV 溶出扫描的
            # 分频值就会被 CV 页的方波周期劫持（真机踩过：divider 变成
            # 23999=50ms/步，固件扫 100s 而 GUI 按 ASV 扫速 ~19s 就超时）
            page_params = self.settings
            if getattr(page_params, 'use_swv', False):
                raw_divider = int(clk_freq / (2000 / page_params.swv_period)) - 1
            else:
                raw_divider = int(round(clk_freq /
                                        (_sweep_rate * 1000 / self.params.dac.voltage_step_size)) - 1)
            return '{0:05d}'.format(raw_divider), raw_divider

        def format_voltage(self, _in_volts):
            dac_value = self.params.dac.get_dac_count(_in_volts)
            if dac_value == 0:
                dac_value = 1
            print(f"formated dac to {dac_value}, from {_in_volts}")
            return '{0:04d}'.format(dac_value), dac_value

        def format_voltage_with_gnd(self, _in_volts):
            input_voltage = self.params.virtual_ground_shift - _in_volts
            dac_value = self.params.dac.get_dac_count(input_voltage)
            return '{0:04d}'.format(dac_value), dac_value

        def set_adc_tia(self, *args):
            self.device.set_adc_tia(*args)

        def usb_read_message(self):
            return self.device.usb_read_message()

    class CVSettingDisplay(tb.Frame):
        # 页面 -> 设置区标题的 i18n key
        TITLE_KEYS = {'CV': 'cv_title', 'LS': 'ls_title', 'SWV': 'swv_title',
                          'DPV': 'dpv_title'}

        # 名称 i18n key：按技术区分（DPV 用"脉冲"，SWV 用"方波"）
        PULSE_KEYS = {'SWV': ('settings_swv_height', 'settings_swv_increment',
                              'settings_swv_period'),
                      'DPV': ('settings_dpv_height', 'settings_dpv_increment',
                              'settings_dpv_period')}

        def __init__(self, _master, _frame, graph, device_params, device,
                     mode='CV'):
            tb.Frame.__init__(self, master=_frame)
            self.mode = mode
            self.start_voltage_var_str = tk.StringVar()
            self.end_voltage_var_str = tk.StringVar()
            self.freq_var_str = tk.StringVar()
            self.current_var_str = tk.StringVar()
            self.cycles_var_str = tk.StringVar()
            self.swv_pulse_var_str = tk.StringVar()
            self.swv_inc_var_str = tk.StringVar()
            self.swv_period_var_str = tk.StringVar()
            self.device = device

            rows = [('cv_start_volt', self.start_voltage_var_str),
                    ('cv_end_volt', self.end_voltage_var_str),
                    ('cv_sweep_rate', self.freq_var_str)]
            if mode == 'CV':
                rows.append(('settings_cycles', self.cycles_var_str))
            rows.append(('param_current_range', self.current_var_str))
            if mode in ('SWV', 'DPV'):
                height_key, inc_key, period_key = self.PULSE_KEYS[mode]
                rows += [(height_key, self.swv_pulse_var_str),
                         (inc_key, self.swv_inc_var_str),
                         (period_key, self.swv_period_var_str)]
            self._name_labels = _grid_param_rows(self, rows)

            self.settings_button = tb.Button(
                self, text=get_text("cv_change_settings"),
                bootstyle="primary-outline",
                command=lambda: self.change_cv_settings(_master, graph))
            self.settings_button.grid(row=len(rows), column=0, columnspan=2,
                                      sticky='ew', pady=(12, 0))
            self.cv_label_update(device_params)

        def refresh_language(self):
            self.settings_button.config(text=get_text("cv_change_settings"))
            self.cv_label_update(self.device.params)

        def set_current_var_str(self, tia_value):
            self.current_var_str.set('±{0:g} µA'.format(tia_value))

        def cv_label_update(self, device_params):
            s = device_params.cv_settings
            self.start_voltage_var_str.set('{0} mV'.format(s.start_voltage))
            self.end_voltage_var_str.set('{0} mV'.format(s.end_voltage))
            self.freq_var_str.set('{0} V/s'.format(s.sweep_rate))
            self.current_var_str.set('±{0:g} µA'.format(
                device_params.adc_tia.current_lims))
            self.cycles_var_str.set('{0}'.format(getattr(s, 'cycles', 1)))
            self.swv_pulse_var_str.set('{0} mV'.format(s.swv_height))
            self.swv_inc_var_str.set('{0} mV'.format(s.swv_inc))
            self.swv_period_var_str.set('{0} ms'.format(s.swv_period))
            # 名称行跟随语言
            rows = [('cv_start_volt', None), ('cv_end_volt', None),
                    ('cv_sweep_rate', None)]
            if self.mode == 'CV':
                rows.append(('settings_cycles', None))
            rows.append(('param_current_range', None))
            if self.mode in ('SWV', 'DPV'):
                rows += [(k, None) for k in self.PULSE_KEYS[self.mode]]
            for label, (key, _var) in zip(self._name_labels, rows):
                label.config(text=get_text(key))

        def change_cv_settings(self, master, graph):
            from dialogs import CVSettingChanges
            CVSettingChanges(self, master, graph, self.device, mode=self.mode)


class AmpFrame(tb.Frame):
    def __init__(self, master, parent_notebook, graph_properties):
        tb.Frame.__init__(self, parent_notebook)
        self.data = []
        self.running = False
        self.columnconfigure(0, weight=1)
        # 右侧参数栏保留最小宽度，窗口偏窄时压缩图区而不是挤掉按钮
        self.columnconfigure(1, minsize=250)
        self.rowconfigure(3, weight=1)
        # 与 CV 页同布局：顶部命令丝带 + 图区卡片 + 右参数面板
        self.command_bar = tb.Frame(self, padding=(10, 6, 10, 2))
        self.command_bar.grid(row=0, column=0, columnspan=2, sticky='ew')
        ttk.Separator(self, orient='horizontal').grid(row=1, column=0,
                                                      columnspan=2, sticky='ew')
        self.toolbar_row = tb.Frame(self, padding=(10, 0, 10, 0))
        self.toolbar_row.grid(row=2, column=0, columnspan=2, sticky='ew')
        self.toolbar_row.grid_remove()
        self.plot_card = tb.Labelframe(self, text=get_text('tab_amp'),
                                       bootstyle='info', padding=(4, 2, 4, 4))
        self.plot_card.grid(row=3, column=0, sticky='nsew', padx=(10, 4),
                            pady=(6, 8))
        self.plot_card.rowconfigure(0, weight=1)
        self.plot_card.columnconfigure(0, weight=1)
        self.graph = self.make_graph_area(master, graph_properties)
        self.graph.grid(in_=self.plot_card, row=0, column=0, sticky='nsew')
        options_frame = tb.Frame(self)
        options_frame.grid(row=3, column=1, sticky='nsew', padx=(4, 10),
                           pady=(6, 8))
        device = self.USBHandler(self.graph, master.device, master)
        self.device = device  # 删除当前页数据时要清它持有的 time/data 列表
        self.params_group = tb.Labelframe(options_frame, text=get_text("grp_params"),
                                          bootstyle='info', padding=10)
        self.params_group.pack(side="top", fill=tk.X)
        self.settings_frame = self.AmpSettingsDisplay(master, self.params_group,
                                                      self.graph, device,
                                                      master.device_params)
        self.settings_frame.pack(side="top", fill=tk.X)
        self.make_buttons(self.command_bar, self.graph, device)

    def make_graph_area(self, master, graph_props):
        if check_display_type() == 'matplotlib':
            current_lim = master.device_params.adc_tia.current_lims
            graph = PyplotEmbed(self.toolbar_row, graph_props.amp_plot, self,
                                current_lim, 1, 1)
            # 图题已上移到图区卡片标题，图内不再重复
            graph.graph_area.axis.set_title('')
            graph.text_keys = {'title': None,
                               'xlabel': 'axis_time',
                               'ylabel': 'axis_current'}
        else:
            graph = None
        return graph

    def make_buttons(self, bar, graph, device):
        """底部命令栏（横向）：运行/停止 + 保存。"""
        self.run_button = tb.Button(bar, text=get_text("amp_start"),
                                    bootstyle="success", width=14,
                                    command=lambda: self.toggle_amp_run(device, graph))
        self.run_button.pack(side='left', padx=(0, 10))
        self.save_button = tb.Button(bar, text=get_text("amp_save"),
                                     bootstyle="success-outline", width=7,
                                     command=lambda: self.save_data(device))
        self.save_button.pack(side='left', padx=3)

    def refresh_language(self):
        """Refresh all UI text on this panel when the language changes."""
        if self.running:
            self.run_button.config(text=get_text("amp_stop"))
        else:
            self.run_button.config(text=get_text("amp_start"))
        if self.running:
            self.run_button.config(text=get_text("amp_stop"))
        else:
            self.run_button.config(text=get_text("amp_start"))
        self.save_button.config(text=get_text("amp_save"))
        if getattr(self, 'params_group', None) is not None:
            self.params_group.config(text=get_text("grp_params"))
        if getattr(self, 'plot_card', None) is not None:
            self.plot_card.config(text=get_text('tab_amp'))
        self.settings_frame.refresh_language()
        self.graph.refresh_language()

    def toggle_amp_run(self, device, graph):
        if self.running:
            self.run_button.config(text=get_text("amp_start"))
            self.running = False
            device.cancel_run()
            # 与其他页一致：运行结束自动备份（time/current 两列，与手动
            # 保存同格式，可用 文件->打开数据 原样载回计时电流页）
            self.autosave_amp(device)
        else:
            logging.info("starting amperometry run")
            self.run_button.config(text=get_text("amp_stop"))
            self.running = True
            device.amp_run(graph, self)

    @staticmethod
    def autosave_amp(device, backup_dir=None):
        """计时电流自动备份（该页此前是唯一没有自动存档的页面）。"""
        try:
            if len(device.data) == 0:
                return None
            if backup_dir is None:
                backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "autosave_backup")
            os.makedirs(backup_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(backup_dir, "{0}_AMP.csv".format(stamp))
            with open(path, 'w', newline='', encoding='utf-8-sig') as csv_file:
                csv_file.write("# experiment: AMP\n")
                csv_file.write("# sampling_rate_Hz: {0}\n".format(
                    device.settings.sampling_rate))
                csv_file.write("# voltage_mV: {0}\n".format(
                    device.settings.voltage))
                csv_file.write("time,current\n")
                for i in range(len(device.data)):
                    csv_file.write("{0}, {1}\n".format(device.time[i],
                                                       device.data[i]))
            logging.info("autosaved scan backup: %s", path)
            return path
        except Exception:
            logging.exception("amp autosave failed")
            return None

    def delete_all_data(self):
        """清空计时电流页：图线 + USBHandler 持有的 time/data 列表。"""
        self.graph.delete_all_lines()
        # 计时电流的线是直接 axis.plot 出来的（self.graph.l），
        # 不在 plotted_lines 里，delete_all_lines 删不到它，必须单独移除；
        # 且 update_amp_data 复用该引用，删后必须置 None，
        # 否则下次运行/打开会往已删除的 Line2D 上写数据（画不出来）
        if getattr(self.graph, 'l', None) is not None:
            try:
                self.graph.l.remove()
            except Exception:
                pass
            self.graph.l = None
        self.device.time = []
        self.device.data = []

    def open_data(self, header, data_rows):
        """打开计时电流 CSV（time,current 两列）并画到本页图区。"""
        t, c = [], []
        for row in data_rows:
            if len(row) < 2:
                continue
            t.append(float(row[0]))
            c.append(float(row[1]))
        if not t:
            return
        self.device.time = list(t)
        self.device.data = list(c)
        # update_amp_data 是滚动窗口逻辑：若旧线还在会被复用导致数据不更新
        if getattr(self.graph, 'l', None) is not None:
            try:
                self.graph.l.remove()
            except Exception:
                pass
            self.graph.l = None
        span = t[-1] - t[0]
        self.graph.update_amp_data(t, c, span + 1 if span > 0 else 10)

    @staticmethod
    def save_data(device):
        if len(device.data) == 0:
            logging.info("No amperometry data to save")
            return
        _file = open_file('saveas', return_filename=True)
        if _file:
            with open(_file, 'w', newline='', encoding='utf-8-sig') as csv_file:
                writer = csv.writer(csv_file, dialect='excel')
                # 元数据头：打开文件时据此路由到计时电流页
                writer.writerow(["# experiment: AMP"])
                writer.writerow(["# sampling_rate_Hz: {0}".format(
                    device.settings.sampling_rate)])
                writer.writerow(["# voltage_mV: {0}".format(
                    device.settings.voltage)])
                writer.writerow(["time", "current"])
                for i in range(len(device.data)):
                    writer.writerow([device.time[i], f" {device.data[i]}"])

    def set_tia_current_lim(self, _value, current_limit):
        self.settings_frame.set_current_var_str(_value)
        self.graph.resize_y(current_limit)

    def change_data_labels(self):
        pass

    class USBHandler(object):
        def __init__(self, graph, device, master):
            self.graph = graph
            self.device = device
            self.master = master
            self.settings = master.device_params.amp_settings
            self.running = None
            self.device_samples_smooth = 1
            sampling_rate = self.settings.sampling_rate
            if sampling_rate >= 1000:
                self.data_packet_size = int((sampling_rate / 5.0))
            else:
                self.data_packet_size = int(sampling_rate / 2.0)
            self.number_packets = (int((self.data_packet_size + 1.0) / 32.0)
                                   + ((self.data_packet_size + 1.0) % 32 > 0))
            self.time_step = 1.0 / self.settings.sampling_rate
            self.endpoint = None
            self.uint16_array = [0] * 256
            self.t_ptr = -self.time_step
            self._amp_segment_index = 0
            self.data = []
            self.time = []
            self.first_read_dumbed = False
            self.t_lenght_fixed = False
            self._reader = None

        def amp_run(self, graph, amp_frame):
            self.t_ptr = -self.time_step
            self._amp_segment_index = 0  # 选定通道已接收的段数（算真实时间轴用）
            self.data = []
            self.time = []
            self.running = True
            self.device_samples_smooth = self.device.samples_to_smooth
            self.device.samples_to_smooth = 50  # 计时电流期间加大平滑
            if (self.device.device_params.pwm_period_value !=
                    self.settings.pwm_period_value):
                self.set_sample_rate(self.settings.sampling_rate)
            formatted_packets_size = '{0:04}'.format(self.data_packet_size)
            # 清掉上一会话残留的缓冲数据，避免首段混入旧采样
            # （表现为运行起点处的双重尖峰）
            self.device.clear_in_buffer()
            self.device.usb_write("M|" + self.format_voltage(self.settings.voltage) + '|'
                                  + formatted_packets_size)
            time.sleep(0.1)
            self.master.after(300, self.running_read)

        def running_read(self):
            self._reader = self.master.after(50, self.running_read)
            if not self.running:
                return
            try:
                self._running_read_impl()
            except Exception as err:
                # tk 回调里的异常会被静默吞掉，必须显式报错
                logging.exception("amperometry read loop failed")
                messagebox.showerror(get_text("asv_data_error_title"),
                                     get_text("asv_data_error_msg").format(err))

        def _running_read_impl(self):
            get = self.device.usb_read_message()
            # 固件缓冲攒满后发 "Done<通道>"（6 字节）；残留数据或
            # 其它消息不能当通道号解析，否则回发错误的 'F' 命令
            if get and get.startswith(COMPLETE_MESSAGE) and len(get) > 4:
                channel = get[4]
                if not channel.isdigit():
                    logging.warning("bad amp channel in message: %r", get)
                    return
                # 固件在 AMP_FW_CHANNELS 个通道间轮采轮发，只有选定通道
                # （开发者菜单的 ADC 通道）是真实电流；其它通道的段不取数，
                # 否则 4 路不同偏移交错画在一条线上（满屏竖直条纹）
                if int(channel) != self.master.device_params.adc_tia.adc_channel:
                    return
                logging.debug("getting channel: %s", channel)
                self.device.usb_write("F" + channel)
                if self.data_try():
                    self.graph.update_amp_data(self.time, self.data, 10)

        def data_try(self):
            try:
                # 必须按实际包数读：默认包数来自从未赋值的 usb_count(=0)，
                # 只读 1 个包会让残余数据污染下一条消息，整条流错位
                usb_input = self.device.get_data(self.number_packets)
                data = self.device.process_data(usb_input)
            except Exception as error:
                # 读失败就放弃这一段，绝不能原地死循环（会卡死整个界面）
                logging.error("missed data read: %s", error)
                return False
            if not data:
                return False
            # 计时电流固件缓冲没有首点脏数据的问题（那是 'E' 导出路径的
            # 特性），首段也是有效数据，全部保留。
            # 时间轴按真实经过时间：固件在 4 个通道间轮采，选定通道相邻
            # 两段之间隔着另外 3 段的采集时间，不能首尾相接压缩
            self.data.extend(data)
            len_data = len(data)
            base = self._amp_segment_index * AMP_FW_CHANNELS * len_data * self.time_step
            self.time.extend([base + x * self.time_step for x in range(1, len_data + 1)])
            self._amp_segment_index += 1
            return True

        def cancel_run(self):
            self.running = False
            if self._reader:
                self.master.after_cancel(self._reader)
            self.device.samples_to_smooth = self.device_samples_smooth
            self._reader = None
            self.device.usb_write('X')
            self.device.clear_in_buffer()
            self.first_read_dumbed = False

        def set_sample_rate(self, rate):
            formatted_period, self.pwm_timer_period_amp = self.format_period(rate)
            self.device.usb_write('T|' + formatted_period)
            self.device.device_params.PWM_period = self.pwm_timer_period_amp

        def set_voltage(self, voltage):
            self.device.device_params.amp_settings.voltage = voltage
            formatted_voltage = self.format_voltage(voltage)
            self.device.usb_write('D|' + formatted_voltage)

        def set_adc_tia(self, *args):
            self.device.set_adc_tia(*args)

        def format_period(self, rate):
            clk_freq = self.device.device_params.clk_freq_isr_pwm
            raw_divider = int(round(clk_freq / (rate))) - 1
            return '{0:05d}'.format(raw_divider), raw_divider

        def format_voltage(self, in_voltage):
            input_voltage = self.device.device_params.virtual_ground_shift - in_voltage
            dac_value = self.device.device_params.dac.get_dac_count(input_voltage)
            return '{0:04d}'.format(dac_value)

    class AmpSettingsDisplay(tb.Frame):
        def __init__(self, master, frame, graph, device, device_params):
            tb.Frame.__init__(self, master=frame)
            self.master = master
            self.graph = graph
            self.device = device
            self.voltage_var_str = tk.StringVar()
            self.sampling_rate_var_str = tk.StringVar()
            self.current_var_str = tk.StringVar()
            self._name_labels = _grid_param_rows(self, [
                ('amp_sample_rate', self.sampling_rate_var_str),
                ('amp_voltage', self.voltage_var_str),
                ('param_current_range', self.current_var_str)])
            self.settings_button = tb.Button(self, text=get_text("amp_change_settings"),
                                             bootstyle="primary-outline",
                                             command=self.change_amp_settings)
            self.settings_button.grid(row=3, column=0, columnspan=2,
                                      sticky='ew', pady=(12, 0))
            self.label_update(device_params)

        def refresh_language(self):
            self.settings_button.config(text=get_text("amp_change_settings"))
            self.label_update(self.master.device_params)

        def set_current_var_str(self, tia_value):
            self.current_var_str.set('±{0:g} µA'.format(tia_value))

        def label_update(self, params):
            self.voltage_var_str.set('{0} mV'.format(params.amp_settings.voltage))
            self.sampling_rate_var_str.set(
                '{0} Hz'.format(int(params.amp_settings.sampling_rate)))
            self.current_var_str.set('±{0:g} µA'.format(
                params.adc_tia.current_lims))
            for label, key in zip(self._name_labels,
                                  ('amp_sample_rate', 'amp_voltage',
                                   'param_current_range')):
                label.config(text=get_text(key))

        def change_amp_settings(self):
            from dialogs import AmpSettingsChanges
            AmpSettingsChanges(self, self.master, self.graph, self.device)


class ASVFrame(CVFrame):
    def __init__(self, *args):
        CVFrame.__init__(self, *args, initialize=False)
        # 搅拌继电器实时状态 → 右侧参数栏。此前面板只显示配置项，
        # 静置/溶出期间继电器已关仍显示"开"（真机被当成"搅拌没停"）
        self.device.on_stirring_change = self._on_stirring_live

    def _on_stirring_live(self, on):
        self.cv_settings_frame.stirring_var_str.set(
            get_text("asv_stirring_on" if on else "asv_stirring_off"))

    def _caption_key(self):
        return 'tab_asv'  # 图区卡片标题（继承的 __init__ 按 CV 建）

    def get_sweep_rate(self):
        return self.settings.sweep_rate  # ASV 溶出扫速

    def _run_scan(self):
        # ASV 必须走 清洗→富集→静置→溶出 完整流程（asv_run），
        # 不能复用 CV 的 _run_scan：ASV 处理器的 run_scan(delay) 签名
        # 与 CV 的 run_scan(canvas, run_button) 不同，误调会 TypeError
        # 且被 Tk 吞掉（表现为"点运行没反应"）
        self.device.asv_run(self.graph, self.run_button)

    def _get_run_button_text(self):
        # 运行中保持“停止”文字
        if getattr(self.device, 'is_running', False):
            return get_text("asv_stop")
        return get_text("asv_run")

    def get_metadata(self):
        """保存 ASV 参数到 CSV 头部。"""
        s = self.master.device_params.asv_settings
        return {
            "experiment": "ASV",
            "sweep_type": s.sweep_type,
            "low_voltage_mV": s.low_voltage,
            "high_voltage_mV": s.high_voltage,
            "end_voltage_mV": s.end_voltage,
            "sweep_rate_V/s": s.sweep_rate,
            "clean_enabled": s.clean_enabled,
            "clean_volt_mV": s.clean_volt,
            "clean_time_s": s.clean_time,
            "plate_volt_mV": s.plate_volt,
            "plate_time_s": s.plate_time,
            "rest_time_s": s.rest_time,
            "stirring_enabled": s.stirring_enabled,
            "clean_after_strip": s.clean_after_strip,
            "pulse_height_mV": s.pulse_height,
            "pulse_inc_mV": s.pulse_inc,
            "pulse_width_or_period_ms": s.pulse_width,
            "current_range": self.master.device_params.adc_tia.current_lims,
        }

    def make_graph_area(self, master, graph_props):
        current_lim = master.device_params.adc_tia.current_lims
        low_voltage = master.device_params.asv_settings.low_voltage
        high_voltage = master.device_params.asv_settings.high_voltage
        graph = PyplotEmbed(self.toolbar_row, graph_props.asv_plot, self,
                            current_lim, low_voltage, high_voltage)
        # 图题已上移到图区卡片标题，图内不再重复
        graph.graph_area.axis.set_title('')
        graph.text_keys = {'title': None,
                           'xlabel': 'axis_voltage',
                           'ylabel': 'axis_current'}
        return graph

    class USBHandler(CVFrame.USBHandler):
        def __init__(self, graph, device, master, data, **kwargs):
            CVFrame.USBHandler.__init__(self, graph, device, master, data, **kwargs)
            self.graph = graph
            self.device = device
            self.master = master
            self.data = data
            self.params = master.device_params
            self.settings = master.device_params.asv_settings
            self.usb_packet_count = 0
            self.run_button = None
            self.after_function = None
            self.is_running = False
            self._stirring_on = False

        def _set_stirring(self, on):
            """按需开关搅拌：未启用搅拌或状态未变化时不发命令。"""
            if not self.params.asv_settings.stirring_enabled:
                return
            if self._stirring_on == on:
                return
            self._stirring_on = on
            self.device.set_stirring(on)
            self._notify_stirring_live(on)

        def _notify_stirring_live(self, on):
            """把搅拌继电器实时状态回传界面（静置/溶出期间显示"关"）。"""
            callback = getattr(self, 'on_stirring_change', None)
            if callback is None:
                return
            try:
                callback(on)
            except Exception:
                logging.exception("stirring live indicator update failed")

        def send_cv_parameters(self):
            logging.debug("sending asv params here")
            formatted_start_volt, start_dac_value =                 self.format_voltage_with_gnd(self.settings.low_voltage)
            formatted_end_volt, end_dac_value =                 self.format_voltage_with_gnd(self.settings.high_voltage)
            formatted_freq_divider, pwm_period =                 self.format_divider(self.settings.sweep_rate)
            self.params.PWM_period = pwm_period
            sweep_type_to_send = self.settings.sweep_type
            if sweep_type_to_send == 'LS':
                print("sending linear sweep")
                to_amp_device = '|'.join(["S", formatted_start_volt,
                                          formatted_end_volt, formatted_freq_divider,
                                          sweep_type_to_send])
            elif sweep_type_to_send == 'DPV':
                # 真 DPV 走自研固件 'P' 命令（LUT_make_dpv_line，[脉冲,基底] 成对）：
                # P|XXXX|YYYY|IIII|HHHH|ZZZZZ|AB —— 字段布局与 SWV 的 'G' 一致
                print("Sending DPV settings")
                self.params.PWM_period = int((self.settings.pulse_width * self.params.clk_freq_isr_pwm
                                              / 1000) / 2) - 1
                pwm_period = self.params.PWM_period
                print("DPV timer: ", self.params.PWM_period)
                pulse_height = int(self.settings.pulse_height / self.params.dac.voltage_step_size)
                pulse_increment = int(self.settings.pulse_inc / self.params.dac.voltage_step_size)
                to_amp_device = "P|{0}|{1}|{2:04d}|{3:04d}|{4:05d}|LS"                                 "".format(formatted_start_volt, formatted_end_volt,
                                          pulse_increment, pulse_height, self.params.PWM_period)
            elif sweep_type_to_send == 'SWV':
                # 固件 'G' = SWV LUT（user_lookup_table_maker_swv），字段偏移固定：
                # G|XXXX|YYYY|IIII|HHHH|ZZZZZ|AB —— 步进4位/幅值4位/周期5位/扫描类型/起始类型
                print("Sending SWV settings")
                self.params.PWM_period = int((self.settings.pulse_width
                                              * self.params.clk_freq_isr_pwm / 1000) / 2) - 1
                pwm_period = self.params.PWM_period
                swv_inc = int(self.settings.pulse_inc / self.params.dac.voltage_step_size)
                swv_height = int(self.settings.pulse_height / self.params.dac.voltage_step_size)
                to_amp_device = "G|{0}|{1}|{2:04d}|{3:04d}|{4:05d}|LS"                                 "".format(formatted_start_volt, formatted_end_volt,
                                          swv_inc, swv_height, self.params.PWM_period)
            else:
                raise ValueError("Unknown sweep type for ASV: {0}| Use LS, DPV or SWV"
                                 "".format(sweep_type_to_send))
            if sweep_type_to_send == 'DPV':
                # DPV 每步发 2 个采样点（脉冲前/脉冲末），步数 = 范围 // 步进 + 1
                n_steps = abs(end_dac_value - start_dac_value) // pulse_increment + 1
                packet_count = (2.0 * n_steps) / (float(USB_IN_BYTE_SIZE) / 2.0)
            elif sweep_type_to_send == 'SWV':
                # SWV 每步 2 个采样点（正/负半周），步数 = 范围 // 步进 + 1
                n_steps = abs(end_dac_value - start_dac_value) // swv_inc + 1
                packet_count = (2.0 * n_steps) / (float(USB_IN_BYTE_SIZE) / 2.0)
            else:
                packet_count = (2 * (abs(end_dac_value - start_dac_value) + 1)
                                / (float(USB_IN_BYTE_SIZE) / 2.0))
            self.usb_packet_count = int(packet_count) + (packet_count % USB_IN_BYTE_SIZE > 0)
            self.device.usb_write(to_amp_device)
            time.sleep(0.01)
            compare_value = pwm_period / 2
            self.device.write_timer_compare(compare_value)

        def set_adc_tia(self, *args):
            self.device.set_adc_tia(*args)

        def asv_run(self, graph, run_button):
            from dialogs import TimerToplevel
            settings = self.params.asv_settings
            # 保留引用：倒计时结束后该窗口原地切换为扫描进度（合一），
            # 扫描结束/停止/失败时负责关闭；总时间含扫描段预估
            self.timer_window = TimerToplevel(
                self.master,
                settings.clean_time if settings.clean_enabled else 0,
                settings.plate_time,
                settings.rest_time,
                scan_est=max(1, int(round(self._estimate_scan_seconds()))))
            self.device.start_hardware()
            if not self.run_button:
                self.run_button = run_button
            self.is_running = True
            self.run_button.config(text=get_text("asv_stop"), command=self.stop,
                                   bootstyle="danger")
            self.device.short_tia_resistor()
            if settings.clean_enabled:
                # 清洗阶段（一次性丝网印刷电极可关闭）：加清洗电压，可同时搅拌
                self._set_stirring(True)
                self.device.set_anode_voltage(settings.clean_volt)
                self.after_function = self.master.after(
                    int(settings.clean_time * 1000),
                    self.change_to_plating_voltage)
            else:
                # 无清洗步骤，直接进入富集
                self.change_to_plating_voltage()

        def change_to_plating_voltage(self):
            # 进入富集阶段：加富集电压，同时按设置启动搅拌
            settings = self.params.asv_settings
            self.device.set_anode_voltage(settings.plate_volt)
            self._set_stirring(True)
            self.after_function = self.master.after(
                int(settings.plate_time * 1000),
                self.start_rest_period)

        def start_rest_period(self):
            # 富集结束：先停搅拌，再静置 rest_time 秒让溶液静止，然后溶出
            self._set_stirring(False)
            try:
                tw = getattr(self, 'timer_window', None)
                if tw is not None and tw.winfo_exists():
                    # 显示时钟与真实阶段切换强制对齐：倒计时窗是独立秒表，
                    # 会累计偏差（真机踩过：静置已开始、窗口仍显示富集搅拌
                    # 剩余 2 秒）
                    tw.set_stage('rest')
            except Exception:
                logging.debug("timer stage sync skipped", exc_info=True)
            rest_time = self.params.asv_settings.rest_time
            if rest_time > 0:
                self.after_function = self.master.after(
                    int(rest_time * 1000), self.give_stripping_step)
            else:
                self.give_stripping_step()

        def give_stripping_step(self):
            self.device.stop_shorting_tia_resistor()
            time.sleep(0.005)
            self.send_cv_parameters()
            time.sleep(0.005)
            delay_time = self.params.asv_settings.delay_time
            self.run_scan(delay_time)
            self.device.last_experiment = "ASV"

        def run_scan(self, delay):
            # 扫描期间保持"停止"按钮可用，用户可随时中断
            self.device.usb_write('R')
            if self.device.connected:
                tw = getattr(self, 'timer_window', None)
                if tw is not None:
                    # 窗口合一：计时窗原地切换为扫描进度，不再弹第二个窗
                    est = max(1, int(round(self._estimate_scan_seconds())))
                    tw.show_scan(est)
                else:
                    self._show_progress()  # 无计时窗时退回独立进度弹窗
                logging.debug("device reading, scan delay: %s ms", int(delay))
                # 轮询截止 = 首次等待 + 1.5×预估扫描时间 + 5s 余量。
                # 与 CV 页同一规则：固定重试次数会在 delay_time 估算
                # 偏小时提前放弃，表现为"跑完没有曲线"（真机 LS 踩过）
                self._scan_deadline = (time.time() + int(delay) / 1000.0
                                       + 1.5 * self._estimate_scan_seconds() + 5.0)
                self._poll_after_id = self.master.after(
                    int(delay), lambda: self.run_scan_continue())
            else:
                logging.debug("Couldn't find out endpoint to send message to run")

        def run_scan_continue(self):
            if not self.is_running:
                return  # 用户已停止，丢弃迟到的轮询回调
            check_message = self.device.usb_read_message()
            logging.debug('check message: %s', check_message)
            # 真机固件回的是 'Done\x00'（带空字符），用 == 比较会误判为未完成；
            # 与 CV 路径一致改用 in 判断，并防空（None/空串）
            if check_message and COMPLETE_MESSAGE in check_message:
                self._poll_after_id = None
                self.get_and_display_data()
            elif time.time() < getattr(self, '_scan_deadline', 0):
                # 设备可能还没扫完，稍后再查（直到超过截止时间才放弃）
                self._poll_after_id = self.master.after(
                    FAILURE_DELAY, lambda: self.run_scan_continue())
            else:
                # 超时：关窗、恢复按钮、弹错误提示，不再静默放弃
                self._poll_after_id = None
                logging.error("ASV scan timed out waiting for device")
                self._close_timer()
                self._close_progress()
                self.is_running = False
                self.run_button.state(['!disabled'])
                self.run_button.config(text=get_text("asv_run"),
                                       command=lambda: self.asv_run(self.graph, self.run_button),
                                       bootstyle="primary")
                messagebox.showerror(get_text("asv_data_error_title"),
                                     get_text("asv_data_error_msg").format(
                                         "scan timed out"))

        def get_and_display_data(self):
            # tk 回调里的异常会被静默吞掉（表现为窗口卡死），
            # 必须捕获并显式报错 + 恢复界面
            try:
                self._get_and_display_data_impl()
            except Exception as err:
                logging.exception("ASV get_and_display_data failed")
                self._close_timer()
                self._close_progress()
                self.is_running = False
                self.run_button.state(['!disabled'])
                self.run_button.config(
                    text=get_text("asv_run"),
                    command=lambda: self.asv_run(self.graph, self.run_button),
                    bootstyle="primary")
                messagebox.showerror(get_text("asv_data_error_title"),
                                     get_text("asv_data_error_msg").format(err))

        def _get_and_display_data_impl(self):
            self.device.usb_write('E0')
            raw_data = self.device.get_data(self.usb_packet_count)
            raw_data.pop(0)
            self.run_button.state(['!disabled'])
            if not raw_data:
                return
            is_pulse = self.params.asv_settings.sweep_type in ("DPV", "SWV")
            # ASV 溶出固定从富集电位（低）扫向终止电位（高），即电位上行。
            # 固件首采样在电位低侧：差分必须"后减前"（i(E+h) − i(E−h)），
            # 阳极溶出峰才朝上——旧代码一律前减后，是"峰向下"的根因
            self.data = self.device.process_data(raw_data, swv=is_pulse,
                                                 ascending=True)
            self._warn_if_saturated(raw_data)
            print('make x line: ', self.params.asv_settings.pulse_inc)
            if is_pulse:
                # DPV/SWV：差分后每步 1 点，x 轴是阶梯基底电位
                x_line = make_dpv_x_line(self.params.asv_settings.low_voltage,
                                         self.params.asv_settings.high_voltage,
                                         self.params.asv_settings.pulse_inc)
            else:
                x_line = make_voltage_profile(self.params.asv_settings.low_voltage,
                                              self.params.asv_settings.high_voltage,
                                              self.params.dac.voltage_step_size,
                                              self.params.asv_settings.sweep_type,
                                              self.params.asv_settings.sweep_start_type,
                                              # LS 不是方波，swv_height 必须传 None，
                                              # 否则 x 轴会被展开成方波阶梯（长度 4 倍于数据）
                                              None)
            if len(x_line) != len(self.data):
                # 固件与上位机点数规则不一致时对齐到较短者，避免画图崩溃
                logging.warning("x/data length mismatch: %d vs %d, truncating",
                                len(x_line), len(self.data))
                n = min(len(x_line), len(self.data))
                x_line = x_line[:n]
                self.data = self.data[:n]
            self.graph.update_data(x_line, self.data, raw_data)
            self._close_timer()  # 溶出扫描结束，关闭计时/进度合一窗口
            self._close_progress()
            # 自动备份扫描数据，防止忘记手动保存导致数据丢失
            self.graph.master.autosave_scan(x_line, self.data)
            if self.params.asv_settings.clean_after_strip:
                # 溶出结束后回到清洗电压，清洁电极表面
                self.device.short_tia_resistor()
                self.device.set_anode_voltage(self.params.asv_settings.clean_volt)
            self.is_running = False
            self.run_button.config(text=get_text("asv_run"),
                                   command=lambda: self.asv_run(self.graph, self.run_button),
                                   bootstyle="primary")

        def _close_timer(self):
            """安全关闭 ASV 计时/扫描合一窗口（不存在则忽略）。"""
            tw, self.timer_window = getattr(self, 'timer_window', None), None
            if tw is not None:
                try:
                    tw.close()
                except Exception:
                    pass

        def stop(self):
            self.is_running = False
            self.run_button.config(text=get_text("asv_run"),
                                   command=lambda: self.asv_run(self.graph, self.run_button),
                                   bootstyle="primary")
            if self.after_function:
                self.master.after_cancel(self.after_function)
            if getattr(self, '_poll_after_id', None):
                # 停止时取消扫描轮询，避免迟到的回调画出不完整数据
                self.master.after_cancel(self._poll_after_id)
                self._poll_after_id = None
            # 停止时必须关掉搅拌，避免继电器一直吸合
            self._set_stirring(False)
            self._close_timer()  # 中途停止也要关掉计时/进度窗口
            self._close_progress()
            self.device.reset()

    class CVSettingDisplay(tb.Frame):
        def __init__(self, _master, _frame, graph, device_params, device,
                     **_ignored):
            tb.Frame.__init__(self, master=_frame)
            self.clean_voltage_var_str = tk.StringVar()
            self.cleaning_time_var_str = tk.StringVar()
            self.plating_voltage_var_str = tk.StringVar()
            self.plating_time_var_str = tk.StringVar()
            self.strip_start_var_str = tk.StringVar()
            self.end_voltage_var_str = tk.StringVar()
            self.rest_time_var_str = tk.StringVar()
            self.stirring_var_str = tk.StringVar()
            self.freq_var_str = tk.StringVar()
            self.current_var_str = tk.StringVar()
            self.stripping_type = tk.StringVar()
            self.device = device
            # 行序 = 实验流程：清洗 → 富集 → 静置 → 搅拌 → 溶出(范围/扫速)
            self._rows = [
                ('asv_clean_volt', self.clean_voltage_var_str),
                ('asv_clean_time', self.cleaning_time_var_str),
                ('asv_plate_volt', self.plating_voltage_var_str),
                ('asv_plate_time', self.plating_time_var_str),
                ('asv_rest_time', self.rest_time_var_str),
                ('asv_stirring', self.stirring_var_str),
                ('asv_strip_start', self.strip_start_var_str),
                ('asv_peak_volt', self.end_voltage_var_str),
                ('asv_sweep_rate', self.freq_var_str),
                ('param_current_range', self.current_var_str),
                ('asv_stripping_method', self.stripping_type),
            ]
            self._name_labels = _grid_param_rows(self, self._rows)
            self.settings_button = tb.Button(
                self, text=get_text("asv_change_settings"),
                bootstyle="primary-outline",
                command=lambda: self.change_asv_settings(_master, graph))
            self.settings_button.grid(row=len(self._rows), column=0,
                                      columnspan=2, sticky='ew', pady=(12, 0))
            self.label_update(device_params)

        def refresh_language(self):
            self.settings_button.config(text=get_text("asv_change_settings"))
            self.label_update(self.device.params)

        @staticmethod
        def _secs(value):
            # 兼容历史设置里可能出现的浮点/字符串
            return '{0:g} s'.format(float(value))

        def label_update(self, params):
            s = params.asv_settings
            if s.clean_enabled:
                self.clean_voltage_var_str.set('{0} mV'.format(s.clean_volt))
                self.cleaning_time_var_str.set(self._secs(s.clean_time))
            else:
                self.clean_voltage_var_str.set('—')
                self.cleaning_time_var_str.set(get_text("asv_clean_off"))
            self.plating_voltage_var_str.set('{0} mV'.format(s.plate_volt))
            self.plating_time_var_str.set(self._secs(s.plate_time))
            self.strip_start_var_str.set('{0} mV'.format(
                getattr(s, 'strip_start', s.low_voltage)))
            self.end_voltage_var_str.set('{0} mV'.format(s.end_voltage))
            self.rest_time_var_str.set(self._secs(s.rest_time))
            stirring_on = bool(getattr(self.device, '_stirring_on', False))
            # 搅拌行显示继电器实时状态（此前显示配置项 stirring_enabled：
            # 静置/溶出期间继电器已关仍显示"开"，真机被当成"搅拌没停"）
            self.stirring_var_str.set(get_text(
                "asv_stirring_on" if stirring_on else "asv_stirring_off"))
            self.freq_var_str.set('{0} V/s'.format(s.sweep_rate))
            self.current_var_str.set('±{0:g} µA'.format(
                params.adc_tia.current_lims))
            self.stripping_type.set(get_text(
                {'LS': 'strip_ls', 'DPV': 'strip_dpv',
                 'SWV': 'strip_swv'}.get(s.sweep_type, 'strip_ls')))
            for label, (key, _var) in zip(self._name_labels, self._rows):
                label.config(text=get_text(key))

        def change_asv_settings(self, master, graph):
            from dialogs import ASVSettingChanges
            ASVSettingChanges(self, master, graph, self.device)

        def set_current_var_str(self, tia_value):
            self.current_var_str.set('±{0:g} µA'.format(tia_value))
