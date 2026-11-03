# Copyright (c) 2015-2023 Kyle Lopin (Naresuan University) <kylel@nu.ac.th>
# Licensed under the Creative Commons Attribution-ShareAlike 3.0 (CC BY-SA 3.0 US) License
# Modified by Zhijian Yi (Guangdong, China), 2026 — PSoC-Potentiostat-YI-1.0

"""Main entry point and GUI root window for the PSoC Potentiostat."""

import ctypes
import json
import logging
import os
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

import ttkbootstrap as tb

import config as _config

try:
    from config import (DeviceParameters, GraphProps, CURRENT_OPTION_LIST,
                        CURRENT_LIMIT_VALUES)
    from comm import AmpUsb
    from experiments import CVFrame, AmpFrame, ASVFrame
    from dialogs import (ChangeDataLegend, UserDeleteDataWarning, UserSelectDataDelete,
                         EnterLoggingInfo, EnterCustomTIAResistor, VoltageSourceSelect,
                         About, ChangeCompareValue, ShowLookupTable)
    from utils import (open_file, check_display_type,
                       load_csv_file_full)
    from i18n import get_text, set_language
except Exception as import_error:
    # 导入阶段出错（缺依赖 / 某个 .py 文件语法错误等）：留证并停住窗口，避免闪退
    import traceback
    error_msg = traceback.format_exc()
    print("=" * 50)
    print("程序启动失败（导入阶段）")
    print(error_msg)
    print("如提示缺少模块，请先双击运行 install_env.bat")
    print("=" * 50)
    try:
        with open("startup_error.txt", "w", encoding="utf-8") as _log_file:
            _log_file.write(error_msg)
    except Exception:
        pass
    try:
        import tkinter as _tk
        from tkinter import messagebox as _messagebox
        _root = _tk.Tk()
        _root.withdraw()
        _messagebox.showerror(
            "程序启动失败",
            "详细错误已保存到 startup_error.txt\n\n" + error_msg[-800:])
        _root.destroy()
    except Exception:
        pass
    try:
        input("按回车键退出...")
    except EOFError:
        pass
    raise SystemExit(1)

OPTIONS_BACKGROUND = 'LightCyan4'
logging.getLogger('PIL').setLevel(logging.WARNING)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    print("Set DPI awareness")
except:
    pass


class ElectroChemGUI(tb.Window):
    def __init__(self, parent=None):
        if not os.path.exists('logging'):
            os.makedirs('logging')
        date = time.strftime("%Y_%m_%d")
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        logging.basicConfig(
            level=logging.DEBUG,
            filename="logging/" + date + "_logging_file.log",
            format="%(asctime)-15s %(levelname)s %(module)s %(lineno)d: %(message)s")

        self.device_params = DeviceParameters()
        # 界面语言/开发者数据格式：启动即恢复上次选择（settings_app.txt）
        set_language(self.device_params.app_settings.language)
        self.data_save_type = self.device_params.app_settings.data_save_type
        self.device_params.adc_tia.adc_channel = \
            self.device_params.app_settings.adc_channel
        self.display_type = check_display_type()
        tb.Window.__init__(
            self, themename=getattr(self.device_params.cv_settings,
                                    'theme', 'cosmo') or 'cosmo')
        self.parent = parent
        self.voltage_source_label = tk.StringVar()
        self.electrode_config_label = tk.StringVar()
        self.electrode_config_label.set(get_text("electrode_3"))
        self.num_electrodes = 3
        self.voltage_source_key = None
        self.device = self._make_device()
        if getattr(self.device_params.cv_settings, 'simulate_mode', False):
            self.voltage_source_label.set(get_text("simulate_device"))
        graph_props = GraphProps()
        self.frames = self.make_bottom_frames()

        self.notebook = tb.Notebook(self)
        self.cv = CVFrame(self, self.notebook, graph_props, mode='CV')
        # LS/SWV 页与 CV 共用底层代码；只让 CV 页做上电初始化，避免重复发参数
        self.ls = CVFrame(self, self.notebook, graph_props,
                          initialize=False, mode='LS')
        self.swv = CVFrame(self, self.notebook, graph_props,
                           initialize=False, mode='SWV')
        self.dpv = CVFrame(self, self.notebook, graph_props,
                           initialize=False, mode='DPV')
        self.amp = AmpFrame(self, self.notebook, graph_props)
        self.asv = ASVFrame(self, self.notebook, graph_props)

        self.notebook.add(self.cv, text=get_text("tab_cv"))
        self.notebook.add(self.ls, text=get_text("tab_ls"))
        self.notebook.add(self.swv, text=get_text("tab_swv"))
        self.notebook.add(self.dpv, text=get_text("tab_dpv"))
        self.notebook.add(self.amp, text=get_text("tab_amp"))
        self.notebook.add(self.asv, text=get_text("tab_asv"))

        self.notebook.pack(side='top', expand=True, fill=tk.BOTH)
        # 底部状态栏（单行一体化）：连接 | 电压源 | 电极制式 | 电流量程
        self.current_range_label = tk.StringVar()
        self.current_range_label.set(
            get_text("status_current_range") + ': ±'
            + str(self.device_params.adc_tia.current_lims) + ' µA')
        status_bar = self.frames[0]
        self.connect_button = tb.Button(status_bar, command=self.connect,
                                        padding=(12, 2))
        self.update_connect_button()
        self.connect_button.pack(side='left', padx=(0, 12))
        for textvariable in (self.voltage_source_label,
                             self.electrode_config_label,
                             self.current_range_label):
            tb.Label(status_bar, textvariable=textvariable,
                     bootstyle='secondary', font=('', 10)
                     ).pack(side='left', padx=(0, 14))
        OptionMenu(self)
        # 设备在构造时若已连上端口，立即恢复上次使用的电流量程
        self._apply_saved_current_range()

    def _apply_saved_current_range(self):
        """设备（重新）就绪后，把持久化的电流量程恢复到设备。

        默认档（±100 µA，设备上电即此档）不发命令，默认用户零副作用；
        设备未连接时跳过，connect() 成功后会再调一次。
        """
        if not getattr(self.device, 'connected', False):
            return
        index = self.device_params.app_settings.current_range_index
        adc = self.device_params.adc_tia
        if not isinstance(index, int) or not 0 <= index < len(CURRENT_LIMIT_VALUES):
            return
        if index == 0 or index == adc.current_option_index:
            return
        try:
            self.device.set_adc_tia(index)
        except Exception:
            logging.exception("restore saved current range failed")

    def _make_device(self):
        """按配置返回真实设备或内置模拟器（选项菜单开关，即时热切换）。"""
        if not getattr(self.device_params.cv_settings, 'simulate_mode', False):
            return AmpUsb(self, self.device_params)
        try:
            tests_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     'tests')
            if tests_dir not in sys.path:
                sys.path.insert(0, tests_dir)
            from fake_device import FakeSerialComm, make_fake_ampusb
            logging.info("simulate mode: using built-in simulator")
            return make_fake_ampusb(self, FakeSerialComm())
        except ImportError as err:
            logging.warning("simulate_mode requested but unavailable: %s", err)
            messagebox.showinfo(get_text("simulate_mode"),
                                get_text("simulate_need_tests"))
            return AmpUsb(self, self.device_params)

    def any_scan_running(self):
        """是否有扫描 / 计时电流正在进行（切换模拟模式时必须停止）。"""
        checks = [getattr(frame.device, 'is_running', False)
                  for frame in (self.cv, self.ls, self.swv, self.dpv, self.asv)]
        checks.append(bool(getattr(self.amp, 'running', False)))
        return any(checks)

    def set_simulate_mode(self, var):
        """热切换真实设备 <-> 内置模拟器（写入 settings.txt 持久化）。

        有扫描/计时进行中时拒绝切换并拨回菜单状态。
        """
        enabled = bool(var.get())
        cv = self.device_params.cv_settings
        if bool(getattr(cv, 'simulate_mode', False)) == enabled:
            return
        if self.any_scan_running():
            var.set(not enabled)
            messagebox.showwarning(get_text("simulate_mode"),
                                   get_text("simulate_busy"))
            return
        cv.simulate_mode = enabled
        cv.save_settings()
        old_device = self.device
        self.device = self._make_device()
        # 关键：各页面的 USBHandler 在构造时持有旧设备引用，
        # 必须全部重绑到新设备，否则切换后扫描仍走旧设备（等于没切换）
        for frame in (self.cv, self.ls, self.swv, self.dpv, self.asv, self.amp):
            if hasattr(frame, 'device'):
                frame.device.device = self.device
        self._apply_saved_current_range()
        # 关闭旧真实串口，避免端口占用导致切回时连不上
        try:
            serial_holder = getattr(old_device, 'device', None)
            if serial_holder is not None and hasattr(serial_holder, 'close')                     and type(old_device).__name__ == 'AmpUsb':
                serial_holder.close()
        except Exception:
            logging.debug("old serial close skipped", exc_info=True)
        self.update_connect_button()
        if enabled:
            self.voltage_source_label.set(get_text("simulate_device"))
        else:
            # 真实模式：电压源由连接流程重新探测；未连接时清空旧标注
            self.voltage_source_label.set('')
        messagebox.showinfo(get_text("simulate_mode"),
                            get_text("simulate_on" if enabled
                                     else "simulate_off"))

    # ------------------------------------------------------------------
    # 方法文件：整套实验参数保存 / 加载（.json）
    # ------------------------------------------------------------------
    METHOD_KEYS_CV = ('start_voltage', 'end_voltage', 'sweep_rate',
                      'sweep_type', 'sweep_start_type', 'swv_height',
                      'swv_inc', 'swv_period', 'use_swv', 'cycles')
    METHOD_KEYS_ASV = ('clean_volt', 'clean_time', 'plate_volt', 'plate_time',
                       'strip_start', 'end_voltage', 'rest_time',
                       'stirring_enabled', 'clean_enabled', 'clean_after_strip',
                       'sweep_rate', 'sweep_type', 'pulse_height',
                       'pulse_inc', 'pulse_width')

    def save_method_file(self, path=None):
        """把全部实验参数保存为 .json 方法文件（可共享/复现实验条件）。"""
        from tkinter import filedialog
        if not path:
            path = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[(get_text("file_json"), "*.json"),
                           (get_text("file_all"), "*.*")])
        if not path:
            return
        p = self.device_params
        method = {
            "method": "PSoC-Potentiostat-YI-1.0",
            "cv": {k: getattr(p.cv_settings, k, None)
                   for k in self.METHOD_KEYS_CV},
            "asv": {k: getattr(p.asv_settings, k, None)
                    for k in self.METHOD_KEYS_ASV},
            "amp": {"voltage": p.amp_settings.voltage,
                    "sampling_rate": p.amp_settings.sampling_rate},
            "current_range_index": p.adc_tia.current_option_index,
            "simulate_mode": bool(getattr(p.cv_settings, 'simulate_mode', False)),
        }
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(method, f, ensure_ascii=False, indent=2)
            logging.info("method file saved: %s", path)
        except Exception as err:
            logging.exception("method save failed")
            messagebox.showerror(get_text("method_save"), str(err))

    def load_method_file(self, path=None):
        """加载方法文件并应用到全部页面（白名单字段，忽略未知键）。"""
        from tkinter import filedialog
        if not path:
            path = filedialog.askopenfilename(
                filetypes=[(get_text("file_json"), "*.json"),
                           (get_text("file_all"), "*.*")])
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as err:
            messagebox.showerror(get_text("method_load"), str(err))
            return
        p = self.device_params
        cv, asv, amp = data.get("cv", {}), data.get("asv", {}), data.get("amp", {})
        # CV/LS/SWV/DPV 共用一组扫描参数
        p.cv_settings.update_settings(
            cv.get('start_voltage', p.cv_settings.start_voltage),
            cv.get('end_voltage', p.cv_settings.end_voltage),
            cv.get('sweep_rate', p.cv_settings.sweep_rate),
            cv.get('sweep_type', p.cv_settings.sweep_type),
            cv.get('sweep_start_type', p.cv_settings.sweep_start_type),
            cv.get('swv_height', p.cv_settings.swv_height),
            cv.get('swv_inc', p.cv_settings.swv_inc),
            cv.get('swv_period', p.cv_settings.swv_period),
            cv.get('use_swv', p.cv_settings.use_swv),
            cycles=cv.get('cycles', getattr(p.cv_settings, 'cycles', 1)))
        p.asv_settings.update_settings(
            asv.get('clean_volt', p.asv_settings.clean_volt),
            asv.get('clean_time', p.asv_settings.clean_time),
            asv.get('plate_volt', p.asv_settings.plate_volt),
            asv.get('plate_time', p.asv_settings.plate_time),
            asv.get('end_voltage', p.asv_settings.end_voltage),
            asv.get('sweep_rate', p.asv_settings.sweep_rate),
            sweep_type={'LS': 0, 'DPV': 1, 'SWV': 2}.get(
                asv.get('sweep_type', p.asv_settings.sweep_type), 0),
            pulse_height=asv.get('pulse_height', p.asv_settings.pulse_height),
            pulse_inc=asv.get('pulse_inc', p.asv_settings.pulse_inc),
            pulse_width=asv.get('pulse_width', p.asv_settings.pulse_width),
            rest_time=asv.get('rest_time', p.asv_settings.rest_time),
            stirring_enabled=asv.get('stirring_enabled',
                                     p.asv_settings.stirring_enabled),
            clean_after_strip=asv.get('clean_after_strip',
                                      p.asv_settings.clean_after_strip),
            clean_enabled=asv.get('clean_enabled',
                                  p.asv_settings.clean_enabled),
            strip_start=asv.get('strip_start',
                                getattr(p.asv_settings, 'strip_start',
                                        p.asv_settings.low_voltage)))
        amp = p.amp_settings
        amp_data = data.get("amp", {})
        if amp_data:
            # update_settings 立即落盘：与 CV/ASV 的方法导入行为一致，
            # 否则导入的安培法参数重启即丢
            amp.update_settings(amp_data.get('voltage', amp.voltage),
                                amp_data.get('sampling_rate',
                                             amp.sampling_rate))
        # 电流量程
        index = data.get("current_range_index")
        if isinstance(index, int) and 0 <= index < len(CURRENT_OPTION_LIST) \
                and index != p.adc_tia.current_option_index:
            self.device.set_adc_tia(index)
        else:
            self.update_current_range(
                CURRENT_OPTION_LIST[p.adc_tia.current_option_index],
                CURRENT_LIMIT_VALUES[p.adc_tia.current_option_index])
        # 刷新所有页面参数显示与横轴
        for frame in (self.cv, self.ls, self.swv, self.dpv):
            frame.cv_settings_frame.cv_label_update(p)
            frame.graph.resize_x(p.cv_settings.low_voltage,
                                 p.cv_settings.high_voltage)
        self.amp.settings_frame.label_update(p)
        self.asv.cv_settings_frame.label_update(p)
        self.asv.graph.resize_x(p.asv_settings.low_voltage,
                                p.asv_settings.high_voltage)
        # 下一次运行强制重发扫描参数（固件里的 LUT 还是旧条件）
        self.device.last_experiment = ''
        logging.info("method file loaded: %s", path)

    def set_theme(self, theme):
        """切换界面主题并持久化（即时生效，图区同步深浅配色）。"""
        if theme not in _config.GUI_THEMES:
            return
        self.style.theme_use(theme)
        dark = theme in ('darkly', 'superhero', 'solar')
        for frame in (self.cv, self.ls, self.swv, self.dpv, self.amp, self.asv):
            try:
                frame.graph.apply_theme(dark)
            except Exception:
                logging.exception("apply plot theme failed on %s",
                                  type(frame).__name__)
        self.device_params.cv_settings.theme = theme
        self.device_params.cv_settings.save_settings()

    def open_calibration(self):
        from dialogs import CalibrationDialog
        CalibrationDialog(self)

    def run_curve_math(self):
        self.run_page_method("curve_math")

    def set_data_type(self, _type):
        logging.info("Developer option to save raw adc counts selected")
        self.data_save_type = _type
        self.device_params.app_settings.update_settings(data_save_type=_type)

    def update_connect_button(self):
        if getattr(self.device_params.cv_settings, 'simulate_mode', False) \
                and self.device.connected:
            self.connect_button.config(text='● ' + get_text("status_simulate"),
                                       bootstyle="info")
        elif self.device.connected:
            self.connect_button.config(text='● ' + get_text("status_connected"),
                                       bootstyle="success")
        elif self.device.device.found:
            self.connect_button.config(
                text='● ' + get_text("status_found_not_connected"),
                bootstyle="warning")
        else:
            self.connect_button.config(text='● ' + get_text("status_not_found"),
                                       bootstyle="danger")

    def change_data_labels(self):
        # 必须传页面 frame（对话框读取 _master.data 的曲线表），
        # 传 self（App 根窗口）会 AttributeError——历史 bug，点遍测试抓到
        ChangeDataLegend(self.cv, self.cv.graph)

    def set_voltage_source_label(self, message):
        self.voltage_source_label.set(message)

    def update_current_range(self, _value, current_limit):
        # 标签格式化用 {0:.1f}，必须传数值；_value 字符串仅保留兼容旧调用
        # （此前漏了 dpv 页，改量程后 DPV 页标签和 y 轴停留在旧量程）
        for frame in (self.cv, self.ls, self.swv, self.dpv):
            frame.set_tia_current_lim(current_limit, current_limit)
        self.amp.set_tia_current_lim(current_limit, current_limit)
        self.asv.set_tia_current_lim(current_limit, current_limit)
        self.current_range_label.set(
            get_text("status_current_range") + ': ±' + str(current_limit) + ' µA')
        # 电流量程持久化：所有改量程的路径（菜单/对话框/方法导入）都
        # 汇聚到本函数或 set_adc_tia，最终都经过这里
        try:
            index = CURRENT_LIMIT_VALUES.index(current_limit)
        except (ValueError, TypeError):
            return
        if index != self.device_params.app_settings.current_range_index:
            self.device_params.app_settings.update_settings(
                current_range_index=index)

    # CSV 元数据 experiment 标记 -> 对应页面
    EXPERIMENT_PAGES = {'CV': 'cv', 'LS': 'ls', 'SWV': 'swv',
                        'DPV': 'dpv', 'ASV': 'asv', 'AMP': 'amp'}

    def open_data(self):
        logging.debug("opening data")
        _file_name = open_file('open')
        if not _file_name:
            return
        logging.debug("a file named %s opened", _file_name)
        try:
            loaded = load_csv_file_full(_file_name)
        except Exception as err:  # 文件无法读取
            logging.warning("failed to read %s: %s", _file_name, err)
            loaded = None
        if loaded is None:
            messagebox.showwarning(get_text("open_failed_title"),
                                   get_text("open_failed_msg"))
            return
        metadata, header, data_rows = loaded
        experiment = str(metadata.get('experiment', '')).strip().upper()
        if not experiment:
            # 老文件无元数据：按表头猜——time 是计时电流，voltage 默认 CV 页
            experiment = 'AMP' if header[0].strip().startswith('time') else 'CV'
        attr = ElectroChemGUI.EXPERIMENT_PAGES.get(experiment)
        frame = getattr(self, attr, None) if attr else None
        if frame is None or not hasattr(frame, 'open_data'):
            logging.warning("unknown experiment %r, falling back to CV page",
                            experiment)
            frame = self.cv
        logging.debug("routing %s data to %s page", experiment or '?', attr)
        self.notebook.select(frame)  # 跳到数据所属的页面
        frame.open_data(header, data_rows)

    def delete_all_data_user_prompt(self):
        UserDeleteDataWarning(self)

    def delete_all_data(self):
        logging.debug("deleting all data")
        for frame in (self.cv, self.ls, self.swv, self.dpv, self.amp, self.asv):
            if hasattr(frame, 'delete_all_data'):
                frame.delete_all_data()

    def current_page_frame(self):
        current = self.notebook.select()
        return self.nametowidget(current) if current else None

    def run_page_method(self, name):
        """把菜单触发的分析/样式操作转发到当前页（与丝带同一套方法）。"""
        frame = self.current_page_frame()
        method = getattr(frame, name, None)
        if not callable(method):
            # 计时电流页没有曲线分析功能
            messagebox.showinfo(get_text("menu_analysis"),
                                get_text("analysis_unavailable"))
            return
        method()

    def delete_current_page_data_dialog(self):
        """菜单版"删除当前页数据"：与丝带相同，勾选曲线后删除。"""
        frame = self.current_page_frame()
        if not hasattr(frame, 'delete_some_data'):
            messagebox.showinfo(get_text("data_delete_page"),
                                get_text("analysis_unavailable"))
            return
        if getattr(frame, 'data', None) is not None and frame.data.index == 0:
            messagebox.showinfo(get_text("data_delete_page"),
                                get_text("data_none"))
            return
        from dialogs import UserSelectDataDelete
        UserSelectDataDelete(frame)

    def delete_current_page_data(self):
        # 只清当前标签页的数据（用户确认：删除与保存对称，都按页操作）
        current = self.notebook.select()
        if not current:
            return
        frame = self.nametowidget(current)
        logging.debug("deleting data of current page: %s", frame)
        if hasattr(frame, 'delete_all_data'):
            frame.delete_all_data()

    def save_current_page_data(self):
        """保存当前标签页的数据：CV/LS/SWV/ASV 走 save_all_data，计时电流走自己的 save_data。"""
        current = self.notebook.select()
        if not current:
            return
        frame = self.nametowidget(current)
        logging.debug("saving data of current page: %s", frame)
        if frame is self.amp:
            # 计时电流的数据在页面自己的 USBHandler 上（time/data 列表），
            # 不是 master.device（AmpUsb）——传错对象会 AttributeError 静默失败
            self.amp.save_data(self.amp.device)
        elif hasattr(frame, 'save_all_data'):
            frame.save_all_data()

    def quit(self):
        destroyer()

    def set_adc_channel(self, _channel):
        self.device_params.adc_tia.adc_channel = _channel
        self.device_params.app_settings.update_settings(adc_channel=_channel)

    def connect(self, button=None):
        logging.debug("trying connecting")
        if self.device.connected:
            logging.info("device is connected")
        else:
            logging.debug("attempting to connect")
            self.device = AmpUsb(self, self.device_params)
            self.update_connect_button()
            self._apply_saved_current_range()

    def failed_connection(self):
        logging.info("failed connection")
        if hasattr(self, "connect_button"):
            self.connect_button.config(text=get_text("status_not_found"),
                                       bootstyle="danger")

    def make_bottom_frames(self):
        """底部状态栏：单行一体化横条。返回 [状态栏]，
        frames[1]/[2] 保留为同一对象的兼容别名。"""
        status_bar = tb.Frame(self, padding=(10, 5, 10, 5))
        status_bar.pack(side='bottom', fill=tk.X)
        ttk.Separator(self, orient='horizontal').pack(side='bottom', fill=tk.X)
        return [status_bar]

    def refresh_language(self):
        """Refresh all UI text when language changes."""
        self.title(get_text("app_title"))
        OptionMenu(self)  # 重建菜单栏，菜单文字随语言切换
        self.notebook.tab(self.cv, text=get_text("tab_cv"))
        self.notebook.tab(self.ls, text=get_text("tab_ls"))
        self.notebook.tab(self.swv, text=get_text("tab_swv"))
        self.notebook.tab(self.dpv, text=get_text("tab_dpv"))
        self.notebook.tab(self.amp, text=get_text("tab_amp"))
        self.notebook.tab(self.asv, text=get_text("tab_asv"))
        self.update_connect_button()
        self.electrode_config_label.set(
            get_text("electrode_config_msg").format(self.num_electrodes))
        if self.voltage_source_key:
            self.voltage_source_label.set(get_text(self.voltage_source_key))
        # 逐页独立刷新：任何一页内部出错都不能连累后面的页
        # （真机踩过：Amp 页 graph.data 是 list，refresh 抛异常被 tk 吞掉，
        # 排最后的 ASV 页整页留中文）
        for frame in (self.cv, self.ls, self.swv, self.dpv, self.amp, self.asv):
            try:
                frame.refresh_language()
            except Exception:
                logging.exception("refresh_language failed on %s",
                                  type(frame).__name__)


class OptionMenu(tk.Menu):
    def __init__(self, master):
        tk.Menu.__init__(self, master=master)
        menubar = tk.Menu(master)
        file_menu = tk.Menu(menubar, tearoff=0)
        options_menu = tk.Menu(menubar, tearoff=0)
        data_menu = tk.Menu(menubar, tearoff=0)
        developer_menu = tk.Menu(menubar, tearoff=0)
        language_menu = tk.Menu(menubar, tearoff=0)

        make_option_menu(options_menu, master)
        make_file_option_menu(file_menu, master)
        make_data_menu(data_menu, master)
        make_developer_menu(developer_menu, master)
        make_language_menu(language_menu, master)

        menubar.add_cascade(label=get_text("menu_file"), menu=file_menu)
        menubar.add_cascade(label=get_text("menu_data"), menu=data_menu)
        menubar.add_cascade(label=get_text("menu_options"), menu=options_menu)
        menubar.add_cascade(label=get_text("menu_developers"), menu=developer_menu)
        # 关于：直接作为菜单栏命令项（原先 cascade 套同名项，出现两个"关于"）
        menubar.add_command(label=get_text("menu_about"),
                            command=lambda: About(master))
        menubar.add_cascade(label=get_text("menu_language"), menu=language_menu)

        master.config(menu=menubar)


def make_language_menu(language_menu, master):
    language_menu.add_command(label=get_text("menu_language_zh"),
                              command=lambda: set_language_and_refresh(master, "zh"))
    language_menu.add_command(label=get_text("menu_language_en"),
                              command=lambda: set_language_and_refresh(master, "en"))


def set_language_and_refresh(master, lang):
    set_language(lang)
    master.refresh_language()
    # 语言选择持久化，重启后保持（与主题/模拟模式同一待遇）
    try:
        master.device_params.app_settings.update_settings(language=lang)
    except AttributeError:
        pass  # 测试里可能传入 mock master


def make_data_menu(data_menu, master):
    # 与丝带功能对齐：保存/删除当前页/删除全部 + 数据分析子菜单
    data_menu.add_command(label=get_text("file_save_all"),
                          command=master.save_current_page_data)
    data_menu.add_command(label=get_text("data_delete_page"),
                          command=lambda: master.delete_current_page_data_dialog())
    data_menu.add_command(label=get_text("data_delete_all"),
                          command=master.delete_all_data_user_prompt)
    data_menu.add_separator()
    analysis_menu = tk.Menu(data_menu, tearoff=0)
    data_menu.add_cascade(label=get_text("menu_analysis"), menu=analysis_menu)
    for key, attr in (("btn_select_region", "toggle_region_select"),
                      ("btn_smooth", "apply_smooth"),
                      ("btn_baseline", "apply_baseline"),
                      ("btn_find_peaks", "find_peaks_now"),
                      ("btn_restore", "restore_original")):
        analysis_menu.add_command(
            label=get_text(key),
            command=lambda attr=attr: master.run_page_method(attr))
    data_menu.add_command(label=get_text("cv_change_style"),
                          command=lambda: master.run_page_method(
                              "change_data_labels"))
    data_menu.add_command(label=get_text("export_peaks"),
                          command=lambda: master.run_page_method(
                              "export_peaks"))
    data_menu.add_command(label=get_text("curve_math"),
                          command=lambda: master.run_page_method(
                              "curve_math"))
    data_menu.add_command(label=get_text("calibration_title"),
                          command=lambda: master.open_calibration())


def make_file_option_menu(file_menu, master):
    file_menu.add_command(label=get_text("file_open"),
                          command=master.open_data)
    file_menu.add_separator()
    file_menu.add_command(label=get_text("method_save"),
                          command=lambda: master.save_method_file())
    file_menu.add_command(label=get_text("method_load"),
                          command=lambda: master.load_method_file())
    file_menu.add_separator()
    file_menu.add_command(label=get_text("file_quit"),
                          command=master.quit)


def make_option_menu(options_menu, master):
    # 自定义 TIA 电阻：接口已变（需配合外部电阻硬件实测），先置灰防误触
    options_menu.add_command(label=get_text("options_add_tia"),
                             state='disabled')

    # 以下四组多选项改用单选菜单项（带圆点标记），
    # 变量初值从当前真实状态读取，语言切换重建菜单时也会重新同步
    user_label_var = tk.BooleanVar(
        options_menu, value=bool(master.cv.graph.user_sets_labels_after_run))
    electrode_var = tk.IntVar(
        options_menu, value=getattr(master, 'num_electrodes', 3))
    vsource_var = tk.StringVar(
        options_menu, value=getattr(master, 'voltage_source_key', None) or "")
    smooth_var = tk.IntVar(
        options_menu, value=getattr(master.device, 'samples_to_smooth', 1))
    simulate_var = tk.BooleanVar(
        options_menu,
        value=bool(master.device_params.cv_settings.simulate_mode))
    # 必须保住变量引用：否则函数返回后变量被回收，圆点标记会消失
    master._option_menu_vars = (user_label_var, electrode_var,
                                vsource_var, smooth_var)
    user_set_label_options = tk.Menu(options_menu, tearoff=0)
    options_menu.add_cascade(label=get_text("data_set_label"), menu=user_set_label_options)
    user_set_label_options.add_radiobutton(
        label=get_text("true"), variable=user_label_var, value=True,
        command=lambda: set_user_label_option(master, True))
    user_set_label_options.add_radiobutton(
        label=get_text("false"), variable=user_label_var, value=False,
        command=lambda: set_user_label_option(master, False))

    electrode_config_options = tk.Menu(options_menu, tearoff=0)
    options_menu.add_cascade(label=get_text("opt_electrode_config"), menu=electrode_config_options)
    electrode_config_options.add_radiobutton(
        label=get_text("opt_2electrode"), variable=electrode_var, value=2,
        command=lambda: master.device.set_electrode_config(2))
    electrode_config_options.add_radiobutton(
        label=get_text("opt_3electrode"), variable=electrode_var, value=3,
        command=lambda: master.device.set_electrode_config(3))

    # 电压源状态键是 "vsource_vdac"/"vsource_dvdac"，可能尚未探测到（None）
    user_set_voltage_source = tk.Menu(options_menu, tearoff=0)
    options_menu.add_cascade(label=get_text("opt_voltage_source"), menu=user_set_voltage_source)
    user_set_voltage_source.add_radiobutton(
        label=get_text("opt_vdac"), variable=vsource_var, value="vsource_vdac",
        command=lambda: set_voltage_source(master, "8-bit DAC", vsource_var))
    user_set_voltage_source.add_radiobutton(
        label=get_text("opt_dvdac"), variable=vsource_var, value="vsource_dvdac",
        command=lambda: set_voltage_source(master, "DVDAC", vsource_var))
    options_menu.add_command(label=get_text("opt_logging_comments"),
                             command=lambda: EnterLoggingInfo(master))
    user_set_smoothing_value = tk.Menu(options_menu, tearoff=0)
    for i in range(1, 6):
        user_set_smoothing_value.add_radiobutton(
            label=get_text("opt_values").format(i),
            variable=smooth_var, value=i,
            command=lambda value=i: set_smoothing_value(master, value))
    options_menu.add_cascade(label=get_text("opt_smooth_data"), menu=user_set_smoothing_value)
    # 模拟模式：无需硬件跑全套界面（教学/演示/回归）
    options_menu.add_checkbutton(label=get_text("simulate_mode"),
                                 variable=simulate_var,
                                 command=lambda: master.set_simulate_mode(
                                     simulate_var))
    # 界面主题（即时切换，选择持久化）
    theme_var = tk.StringVar(
        options_menu, value=getattr(master.device_params.cv_settings,
                                    'theme', 'cosmo') or 'cosmo')
    master._option_menu_vars = master._option_menu_vars + (theme_var,)
    theme_menu = tk.Menu(options_menu, tearoff=0)
    options_menu.add_cascade(label=get_text("opt_theme"), menu=theme_menu)
    for theme_name, key in _config.GUI_THEMES.items():
        theme_menu.add_radiobutton(
            label=get_text(key), variable=theme_var, value=theme_name,
            command=lambda t=theme_name: master.set_theme(t))


def set_smoothing_value(master, value):
    logging.info("setting smoothing value: {0}".format(value))
    master.device.samples_to_smooth = value


def set_current_sign(invert):
    """电流符号诊断开关：整体反相换算出的电流（前端输出反相的机器用）。"""
    _config.INVERT_CURRENT_SIGN = bool(invert)
    logging.info("current sign inverted: %s", bool(invert))


def set_voltage_source(master, value, var=None):
    # var 传入时先把标记落到目标项；若设备拒绝/失败会在下面恢复
    master.device.select_voltage_source(value)
    # select_voltage_source 内部会更新 master.voltage_source_key，
    # 用它回写菜单变量，保证标记永远和真实状态一致
    if var is not None:
        var.set(getattr(master, 'voltage_source_key', None) or "")


def set_user_label_option(master, value):
    master.cv.graph.user_sets_labels_after_run = value


def make_developer_menu(developer_menu, master):
    # 状态选择项改用单选菜单项（带圆点标记），变量须挂在 master 上防止回收
    channel_var = tk.IntVar(
        developer_menu, value=master.device_params.adc_tia.adc_channel)
    data_type_var = tk.StringVar(developer_menu, value=master.data_save_type)
    master._developer_menu_vars = (channel_var, data_type_var)

    channel_option_menu = tk.Menu(developer_menu, tearoff=0)
    developer_menu.add_cascade(label=get_text("dev_choose_channel"), menu=channel_option_menu)
    for i in range(4):
        channel_option_menu.add_radiobutton(
            label=str(i), variable=channel_var, value=i,
            command=lambda channel=i: master.set_adc_channel(channel))

    data_option_menu = tk.Menu(developer_menu, tearoff=0)
    developer_menu.add_cascade(label=get_text("dev_choose_data"), menu=data_option_menu)
    data_option_menu.add_radiobutton(
        label=get_text("dev_converted_data"), variable=data_type_var, value="Converted",
        command=lambda: master.set_data_type("Converted"))
    data_option_menu.add_radiobutton(
        label=get_text("dev_raw_data"), variable=data_type_var, value="Raw Counts",
        command=lambda: master.set_data_type("Raw Counts"))

    # 电流符号诊断开关：个别焊装的模拟前端 TIA 输出反相（阳极峰朝下）时，
    # 无需改代码即可整体反相（comm.process_data 每次换算时动态读取）
    sign_var = tk.BooleanVar(
        developer_menu, value=bool(_config.INVERT_CURRENT_SIGN))
    master._developer_menu_vars = (channel_var, data_type_var, sign_var)
    sign_menu = tk.Menu(developer_menu, tearoff=0)
    developer_menu.add_cascade(label=get_text("dev_current_sign"), menu=sign_menu)
    sign_menu.add_radiobutton(
        label=get_text("dev_sign_normal"), variable=sign_var, value=False,
        command=lambda: set_current_sign(False))
    sign_menu.add_radiobutton(
        label=get_text("dev_sign_inverted"), variable=sign_var, value=True,
        command=lambda: set_current_sign(True))

    developer_option_menu = tk.Menu(developer_menu, tearoff=0)
    developer_menu.add_cascade(label=get_text("dev_get_channel"), menu=developer_option_menu)
    for i in range(4):
        # 固件侧功能未实现（comm.py 里只有 TODO 占位），置灰防误点
        developer_option_menu.add_command(label=get_text("dev_channel") + str(i),
                                          state='disabled',
                                          command=lambda channel=i:
                                          master.device.get_export_channel(channel))

    developer_menu.add_command(label=get_text("dev_export_lut"),
                               command=lambda: ShowLookupTable(master))
    developer_menu.add_command(label=get_text("dev_change_pwm"),
                               command=lambda: ChangeCompareValue(master))


def destroyer():
    # 关窗前尽力拉低 P2.0（搅拌继电器触发脚），
    # 避免残留高电平导致 ZK-TD2 下次收不到上升沿
    try:
        _dev = getattr(app, "device", None)
        if _dev is not None and getattr(_dev, "connected", False):
            _dev.set_stirring(False)
    except Exception:
        pass
    app.destroy()
    sys.exit()


if __name__ == '__main__':
    try:
        app = ElectroChemGUI()
        app.protocol("WM_DELETE_WINDOW", destroyer)
        app.title(get_text("app_title"))
        app.geometry("1400x900")
        # 右侧参数栏 + 按钮列较高，限制最小尺寸避免页面元素被裁剪
        app.minsize(1300, 840)
        app.update()  # 主窗口真正显示出来后，再通知 bat 关闭控制台
        try:
            _flag = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 '_gui_ready.flag')
            with open(_flag, 'w') as _f:
                _f.write('ready')
        except Exception:
            pass
        app.mainloop()
    except Exception:
        # 启动崩溃时：写日志文件 + 弹窗 + 控制台停住，确保错误可见
        import traceback
        error_msg = traceback.format_exc()
        try:
            logging.error("startup crash:\n%s", error_msg)
        except Exception:
            pass
        print(error_msg)
        try:
            with open("startup_error.txt", "w", encoding="utf-8") as _log_file:
                _log_file.write(error_msg)
        except Exception:
            pass
        try:
            _root = tk.Tk()
            _root.withdraw()
            from tkinter import messagebox
            messagebox.showerror(
                "程序启动失败",
                "详细错误已保存到 startup_error.txt\n\n" + error_msg[-800:])
            _root.destroy()
        except Exception:
            pass
        try:
            input("按回车键退出...")
        except EOFError:
            pass
        raise SystemExit(1)
