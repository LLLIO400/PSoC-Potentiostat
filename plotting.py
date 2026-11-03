# Copyright (c) 2015-2023 Kyle Lopin (Naresuan University) <kylel@nu.ac.th>
# Licensed under the Creative Commons Attribution-ShareAlike 3.0 (CC BY-SA 3.0 US) License
# Modified by Zhijian Yi (Guangdong, China), 2026 — PSoC-Potentiostat-YI-1.0

"""Plotting backends: matplotlib embedded in tkinter and canvas fallback."""

import csv
import logging
import tkinter as tk
import traceback

import ttkbootstrap as tb

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk as NavToolbar
import matplotlib.pyplot as plt

from i18n import get_text

logging.getLogger('matplotlib.font_manager').disabled = True


# 浅色/深色绘图配色（UI 主题联动）
PLOT_COLORS = {
    'light': {'fig': '#ffffff', 'text': '#333333', 'spine': '#8a8a8a'},
    'dark': {'fig': '#2b2b2b', 'text': '#e8e8e8', 'spine': '#9a9a9a'},
}


def beautify_axis(axis, dark=False):
    """现代风格坐标轴：去顶右脊线、浅色虚线网格、柔和刻度。"""
    colors = PLOT_COLORS['dark' if dark else 'light']
    face = colors['fig']
    axis.set_facecolor(face)
    axis.spines['top'].set_visible(False)
    axis.spines['right'].set_visible(False)
    for side in ('left', 'bottom'):
        axis.spines[side].set_color(colors['spine'])
        axis.spines[side].set_linewidth(0.8)
    axis.grid(True, linestyle='--', linewidth=0.6,
              alpha=0.35 if dark else 0.3,
              color=colors['text'] if dark else '#333333')
    axis.set_axisbelow(True)
    axis.tick_params(labelsize=10, colors=colors['text'])
    for label in (axis.xaxis.label, axis.yaxis.label):
        label.set_color(colors['text'])
    legend = axis.get_legend()
    if legend is not None:
        legend.get_frame().set_facecolor(face)
        for text in legend.get_texts():
            text.set_color(colors['text'])


def _setup_cjk_font():
    """Set a CJK-capable font for matplotlib.

    matplotlib 默认的 DejaVu Sans 不含中文字形，中文图题/坐标轴会显示为方框，
    这里自动从系统中挑一个可用的中文字体。
    """
    import matplotlib.font_manager as fm
    available = {f.name for f in fm.fontManager.ttflist}
    for name in ("Microsoft YaHei", "SimHei", "PingFang SC",
                 "Noto Sans CJK SC", "Noto Sans CJK JP",
                 "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "Source Han Sans SC"):
        if name in available:
            matplotlib.rcParams['font.sans-serif'] = [name, 'DejaVu Sans']
            break
    matplotlib.rcParams['axes.unicode_minus'] = False


_setup_cjk_font()

# 工具栏按钮英文标识 -> i18n key（用于鼠标悬停提示）
_TOOLTIP_KEYS = {
    'Home': 'toolbar_home',
    'Back': 'toolbar_back',
    'Forward': 'toolbar_forward',
    'Pan': 'toolbar_pan',
    'Zoom': 'toolbar_zoom',
    'Subplots': 'toolbar_subplots',
    'Save': 'toolbar_save',
}

# 子图配置窗口的滑块参数名 -> i18n key
_SLIDER_LABEL_KEYS = {
    'left': 'subplot_left',
    'bottom': 'subplot_bottom',
    'right': 'subplot_right',
    'top': 'subplot_top',
    'wspace': 'subplot_wspace',
    'hspace': 'subplot_hspace',
}

# 工具栏右下角的模式提示 -> i18n key
_MODE_KEYS = {
    'pan/zoom': 'mode_pan_zoom',
    'zoom rect': 'mode_zoom_rect',
}


class LocalizedNavToolbar(NavToolbar):
    """NavigationToolbar2Tk with tooltips following the current UI language."""

    def __init__(self, canvas, window):
        self.toolitems = self._localized_toolitems()
        super().__init__(canvas, window)

    @staticmethod
    def _localized_toolitems():
        localized = []
        for text, tooltip, image_file, callback in NavToolbar.toolitems:
            if text is None:
                localized.append((None, None, None, None))
            else:
                key = _TOOLTIP_KEYS.get(text)
                localized.append((text, get_text(key) if key else tooltip,
                                  image_file, callback))
        return localized

    def configure_subplots(self, *args):
        """打开子图边距调整窗口，并把窗口内文字本地化为当前语言。"""
        tool = super().configure_subplots(*args)
        if tool is None:
            return None
        tool_fig = tool.figure
        manager = tool_fig.canvas.manager
        if manager is not None:
            manager.set_window_title(get_text("subplot_tool_title"))
        if getattr(tool_fig, '_suptitle', None) is not None:
            tool_fig._suptitle.set_text(get_text("subplot_tool_hint"))
        for slider in tool._sliders:
            key = _SLIDER_LABEL_KEYS.get(slider.label.get_text())
            if key:
                slider.label.set_text(get_text(key))
        tool.buttonreset.label.set_text(get_text("btn_reset"))
        tool_fig.canvas.draw_idle()
        return tool

    def set_message(self, s):
        """工具栏右下角的模式提示（平移/缩放、框选缩放）跟随当前语言。"""
        key = _MODE_KEYS.get(s)
        if key:
            s = get_text(key)
        super().set_message(s)

    def save_figure(self, *args):
        """保存图像对话框本地化（改写自 matplotlib 原方法）。"""
        import os
        import pathlib
        from tkinter import filedialog, messagebox
        filetypes = self.canvas.get_supported_filetypes_grouped()
        tk_filetypes = [
            (name, " ".join(f"*.{ext}" for ext in exts))
            for name, exts in sorted(filetypes.items())
        ]
        default_filetype = self.canvas.get_supported_filetypes()[
            self.canvas.get_default_filetype()]
        filetype_variable = tk.StringVar(self.canvas.get_tk_widget(),
                                         default_filetype)
        initialdir = os.path.expanduser(matplotlib.rcParams['savefig.directory'])
        initialfile = pathlib.Path(self.canvas.get_default_filename()).stem
        fname = filedialog.asksaveasfilename(
            master=self.canvas.get_tk_widget().master,
            title=get_text("save_figure_title"),
            filetypes=tk_filetypes,
            defaultextension='',
            initialdir=initialdir,
            initialfile=initialfile,
            typevariable=filetype_variable)
        if fname in ["", ()]:
            return None
        if initialdir != "":
            matplotlib.rcParams['savefig.directory'] = os.path.dirname(str(fname))
        if pathlib.Path(fname).suffix[1:] != "":
            extension = None
        else:
            extension = filetypes[filetype_variable.get()][0]
        try:
            self.canvas.figure.savefig(fname, format=extension)
            return fname
        except Exception as e:
            messagebox.showerror(get_text("save_error_title"), str(e))


class PyplotData(object):
    def __init__(self):
        self.voltage_data = []
        self.time_data = []
        self.current_data = []
        self.y_raw_data = []
        self.label = []
        # 每条曲线记录其默认编号（用户改过名的记 None）：
        # 语言切换时按新语言重新生成默认名，自定义名不动
        self.label_default = []
        self.colors = []
        self.notes = []
        self.index = 0
        self.name_index = 0

    def reset(self):
        """原地清空所有曲线数据。
        必须原地清：graph.data / device handler 的 data 都是对
        frame.data 的引用（plotting.py: self.data = _master_frame.data），
        若用"换新对象"的方式清空，旧对象仍被图和扫描流程写入，
        而保存却查新空对象——表现为删过一次数据后再也存不了
        （真机实测：删→扫→存 报 No data to save，曲线其实画出来了）。"""
        self.voltage_data = []
        self.time_data = []
        self.current_data = []
        self.y_raw_data = []
        self.label = []
        self.label_default = []
        self.colors = []
        self.notes = []
        self.index = 0
        self.name_index = 0

    def add_data(self, new_voltage, new_current, _new_raw_y=None, _label=None):
        if not _label:
            _label = get_text("data_label").format(self.name_index + 1)
            self.label_default.append(self.name_index + 1)
        else:
            self.label_default.append(None)
        self.voltage_data.append(new_voltage)
        self.current_data.append(new_current)
        self.label.append(_label)
        self.notes.append(" ")
        if _new_raw_y:
            self.y_raw_data.append(_new_raw_y)
        else:
            self.y_raw_data.append([0])
        self.index += 1
        self.name_index += 1
        logging.debug("adding data, index: %i", self.index)

    def change_label(self, new_label, index):
        self.label[index] = new_label
        self.label_default[index] = None  # 用户命名后不再随语言切换改名

    def refresh_default_labels(self):
        """语言切换后，把仍是默认名的曲线按新语言重新生成。"""
        for i, num in enumerate(self.label_default):
            if num is not None:
                self.label[i] = get_text("data_label").format(num)

    def remove_data(self, _index):
        self.voltage_data.pop(_index)
        self.current_data.pop(_index)
        self.y_raw_data.pop(_index)
        self.notes.pop(_index)
        self.label.pop(_index)
        self.label_default.pop(_index)
        self.colors.pop(_index)
        self.index -= 1

    def save_all_data(self, filename, save_type="Converted", metadata=None):
        try:
            writer = csv.writer(filename, dialect='excel', lineterminator='\n')
            if metadata:
                # metadata 头：'# key: value' 形式，实验条件可追溯
                for key, value in metadata.items():
                    writer.writerow(["# {0}: {1}".format(key, value)])
            line = ["voltage"]
            for i in range(self.index):
                line.append(self.label[i])
            writer.writerow(line)
            length = len(self.current_data[0])
            width = self.index
            line = [" "]
            for i in range(self.index):
                line.append(self.notes[i])
            writer.writerow(line)
            if save_type == "Converted":
                _data_array = self.current_data
            elif save_type == "Raw Counts":
                _data_array = self.y_raw_data
            for i in range(length):
                line[0] = self.voltage_data[0][i]
                for j in range(width):
                    line[j + 1] = _data_array[j][i]
                writer.writerow(line)
        except Exception as error:
            logging.error("failed saving")
            logging.error(error)
            traceback.print_exc()
            filename.close()


class PyplotEmbed(tb.Frame):
    def __init__(self, toolbox_frame, plt_props, _master_frame, y_lims, x_low, x_high):
        tb.Frame.__init__(self, master=_master_frame)
        self.master = _master_frame
        self.l = None
        self.user_sets_labels_after_run = True
        self.label_instance = ""
        self.graph_area = tb.Frame(self)
        self.plotted_lines = []
        self.data = _master_frame.data
        self.legend_displayed = False
        # i18n keys for graph texts, set by the owning frame (see experiments.py)
        self.text_keys = None
        # 峰区域框选（基线扣除用）与峰标注
        self.peak_region = None
        self._span_selector = None
        self._peak_annotations = []
        # 曲线变换前的原始数据备份（曲线索引 -> (v, c)），供“还原”使用
        self._original_curves = {}
        self.init_graph_area(plt_props, toolbox_frame, y_lims, x_low, x_high)
        self.toolbar_status = False
        self._resize_id = None
        self.bind('<Configure>', self._on_resize)

    def _on_resize(self, event):
        if self._resize_id:
            self.after_cancel(self._resize_id)
        self._resize_id = self.after(100, self._do_resize, event)

    # 图区边距（占画布比例）：轴盒尽量撑满卡片，左侧留给 y 轴标签
    AXES_MARGINS = dict(left=0.085, right=0.975, top=0.95, bottom=0.12)

    def _do_resize(self, event):
        if not self.winfo_exists():
            return
        if event.width < 50 or event.height < 50:
            return
        dpi = self.graph_area.figure_bed.get_dpi()
        new_width = event.width / dpi
        new_height = event.height / dpi
        self.graph_area.figure_bed.set_size_inches(new_width, new_height, forward=False)
        # tight_layout 对本图（含图例/峰标注）会失效跳过，改用确定性边距
        self.graph_area.figure_bed.subplots_adjust(**self.AXES_MARGINS)
        self.graph_area.canvas.draw_idle()

    def init_graph_area(self, plt_props, toolbox_frame, y_lim, x_low, x_high):
        self._dark = False
        self.graph_area.figure_bed = plt.figure(figsize=(5, 4))
        self.graph_area.axis = plt.subplot(111)
        self.graph_area.axis.format_coord = lambda x, y: ""
        if plt_props:
            for key, value in plt_props.items():
                eval("plt." + key + "(" + value + ")")
        if plt_props and "xlim" not in plt_props:
            logging.info("setting xlim (low, high): {0}, {1}".format(x_low, x_high))
            plt.xlim(x_low, x_high)
        if isinstance(y_lim, list):
            plt.ylim(y_lim[0], y_lim[1])
        else:
            plt.ylim(-y_lim, y_lim)
        self.graph_area.figure_bed.subplots_adjust(**self.AXES_MARGINS)
        beautify_axis(self.graph_area.axis, dark=self._dark)
        self.graph_area.canvas = FigureCanvasTkAgg(self.graph_area.figure_bed, master=self)
        self.graph_area.canvas._tkcanvas.config(highlightthickness=0)
        self.graph_area.canvas.mpl_connect('button_press_event', self.legend_handler)
        # 工具栏宿主行（页面上的专用行）；toggle 时联动其显示，
        # 避免挤进按钮丝带后被压成 0 宽度（小窗口下"点了没反应"）
        self._toolbar_host = toolbox_frame
        self.toolbar = LocalizedNavToolbar(self.graph_area.canvas, toolbox_frame)
        self.toolbar.pack_forget()
        self.graph_area.canvas.draw()
        self.graph_area.canvas.get_tk_widget().pack(side='left', fill=tk.BOTH, expand=1)

    def update_data(self, x_data, y_data, _raw_y_data=None, label=None):
        if self.user_sets_labels_after_run:
            self.data.add_data(x_data, y_data, _raw_y_data)
            self.display_data()
            if not label:
                from dialogs import UserSetDataLabel
                UserSetDataLabel(self)
            else:
                self.change_label(label)
        else:
            self.data.add_data(x_data, y_data, _raw_y_data)
            self.display_data()

    def simple_update_data(self, x_data, y_data):
        self.voltage_line, = self.graph_area.axis.plot(x_data, y_data)

    def change_label(self, label, index=None):
        if not index:
            index = self.data.index - 1
        self.data.change_label(label, index)
        self.update_legend()

    def add_notes(self, _notes):
        self.data.notes[-1] = _notes

    def display_data_user_input(self, x_data, y_data):
        self.display_data(x_data, y_data, self.data.index - 1)

    def display_data(self):
        index = self.data.index - 1
        x_data = self.data.voltage_data[index]
        y_data = self.data.current_data[index]
        _label = self.data.label[index]
        # 图例画在坐标轴内部右上角，不再为它预留右侧 10% 死空间
        if len(x_data) > len(y_data):
            x_data = x_data[:len(y_data)]
            logging.error('MISMATCHED DATA LENGTH X DATA IS TOO LONG')
        elif len(y_data) > len(x_data):
            y_data = y_data[:len(x_data)]
            logging.error('MISMATCHED DATA LENGTH Y DATA IS TOO LONG')
        line_color = '#7cb1ff' if getattr(self, '_dark', False) else None
        l = self.graph_area.axis.plot(x_data, y_data, label=_label,
                                      linewidth=1.8, color=line_color)
        self.data.colors.append(l[0].get_color())
        self.plotted_lines.append(l)
        self.update_legend()

    def update_amp_data(self, t, y, time_displayed):
        if not self.l and t:
            self.l, = self.graph_area.axis.plot(t, y)
            self.graph_area.axis.set_xlim(t[-1] - time_displayed, t[-1])
        elif y:
            self.l.set_ydata(y)
            self.l.set_xdata(t)
            self.graph_area.axis.set_xlim(t[-1] - time_displayed, t[-1])
        self.graph_area.canvas.draw()

    def update_legend(self):
        handles, labels = self.graph_area.axis.get_legend_handles_labels()
        old_legend = self.graph_area.axis.get_legend()
        if not handles:
            # 曲线删光后图例一并移除
            if old_legend is not None:
                old_legend.remove()
                self.graph_area.canvas.draw()
            self.legend_displayed = False
            return
        self.graph_area.axis.legend(
            handles, self.data.label,
            loc='upper right',  # 图框内右上角
            prop={'size': 10},
            fancybox=True
        )
        self.graph_area.canvas.draw()

    def delete_all_lines(self):
        logging.debug("deleting all lines")
        while self.plotted_lines:
            l = self.plotted_lines.pop(0)
            l.pop().remove()
            del l
        self._original_curves.clear()
        self.clear_peak_annotations()
        self.update_legend()

    def delete_a_line(self, index):
        logging.debug("deleting line: %i", index)
        line = self.plotted_lines.pop(index)
        self.data.remove_data(index)
        line.pop().remove()
        del line
        self._original_curves.clear()  # 索引已整体偏移，旧备份全部失效
        self.clear_peak_annotations()  # 峰标注属于被删/旧曲线，一并清除
        self.update_legend()

    def change_line_color(self, _color, index):
        self.plotted_lines[index][0].set_color(_color)
        self.data.colors[index] = _color

    def update_graph(self):
        self.graph_area.canvas.draw()

    def apply_theme(self, dark):
        """界面主题切换时联动图区（深色：深底浅字；浅色：白底深字）。"""
        self._dark = bool(dark)
        colors = PLOT_COLORS['dark' if self._dark else 'light']
        self.graph_area.figure_bed.set_facecolor(colors['fig'])
        beautify_axis(self.graph_area.axis, dark=self._dark)
        for line in self.graph_area.axis.get_lines():
            if line.get_linewidth() >= 1.5:  # 数据曲线在深底上略提亮
                line.set_color('#7cb1ff' if self._dark else line.get_color())
        self.graph_area.canvas.draw_idle()

    def toolbar_toggle(self):
        host = getattr(self, '_toolbar_host', None)
        if self.toolbar_status:
            self.toolbar.pack_forget()
            if host is not None:
                try:
                    host.grid_remove()
                except tk.TclError:
                    pass
            self.toolbar_status = False
        else:
            if host is not None:
                try:
                    host.grid()
                except tk.TclError:
                    pass
            self.toolbar.pack(side='left', anchor='w')
            self.toolbar_status = True

    def toggle_region_selector(self):
        """开关峰区域框选（SpanSelector）。返回当前是否激活。"""
        from matplotlib.widgets import SpanSelector
        if self._span_selector is None:
            self._span_selector = SpanSelector(
                self.graph_area.axis, self._on_region_selected, 'horizontal',
                useblit=True, props=dict(alpha=0.3, facecolor='#2780e3'),
                interactive=True)
            self._span_selector.set_active(False)
        active = not self._span_selector.active
        self._span_selector.set_active(active)
        return active

    def _on_region_selected(self, xmin, xmax):
        self.peak_region = (xmin, xmax)
        logging.info("peak region selected: %.1f ~ %.1f", xmin, xmax)

    def transform_latest(self, func):
        """对最新一条曲线应用 func(v, c) -> (v2, c2) 并刷新显示。

        首次变换前自动备份原始数据，可用 restore_latest() 还原。
        """
        index = self.data.index - 1
        if index < 0:
            return False
        if index not in self._original_curves:
            self._original_curves[index] = (list(self.data.voltage_data[index]),
                                            list(self.data.current_data[index]))
        v = list(self.data.voltage_data[index])
        c = list(self.data.current_data[index])
        v2, c2 = func(v, c)
        self._apply_curve(index, v2, c2)
        return True

    def restore_latest(self):
        """把最新一条曲线还原为变换前的原始数据。没有备份时返回 False。"""
        index = self.data.index - 1
        if index < 0 or index not in self._original_curves:
            return False
        v, c = self._original_curves.pop(index)
        self._apply_curve(index, list(v), list(c))
        return True

    def _apply_curve(self, index, v, c):
        self.data.voltage_data[index] = v
        self.data.current_data[index] = c
        line = self.plotted_lines[index][0]
        line.set_xdata(v)
        line.set_ydata(c)
        axis = self.graph_area.axis
        xlim = axis.get_xlim()  # 保留用户/参数设置的 x 范围
        axis.relim()
        axis.autoscale_view()
        axis.set_xlim(xlim)
        self.graph_area.canvas.draw()

    def get_latest_line(self):
        """返回最新一条曲线的 Line2D；没有数据时返回 None。"""
        if not self.plotted_lines:
            return None
        return self.plotted_lines[-1][0]

    def annotate_peaks(self, peaks):
        """在图上标注峰位（红色倒三角 + 电压值）。"""
        self.clear_peak_annotations()
        axis = self.graph_area.axis
        for p in peaks:
            marker, = axis.plot(p['voltage'], p['height'], 'rv', markersize=8)
            text = axis.annotate(
                "{0:.0f}".format(p['voltage']),
                (p['voltage'], p['height']),
                textcoords='offset points', xytext=(0, 10),
                ha='center', fontsize=9, color='red')
            self._peak_annotations.extend([marker, text])
        self.graph_area.canvas.draw()

    def clear_peak_annotations(self):
        for artist in self._peak_annotations:
            try:
                artist.remove()
            except ValueError:
                pass
        self._peak_annotations = []
        self.graph_area.canvas.draw()

    def _rebuild_toolbar(self):
        """Rebuild the toolbar so hover tooltips follow the current language."""
        toolbox_frame = self.toolbar.master
        was_shown = self.toolbar_status
        self.toolbar.destroy()
        self.toolbar = LocalizedNavToolbar(self.graph_area.canvas, toolbox_frame)
        if not was_shown:
            self.toolbar.pack_forget()
        else:
            self.toolbar.pack(side='left', anchor='w')
        self.toolbar_status = was_shown

    def refresh_language(self):
        """Update graph title, axis labels, legend title and toolbar tooltips."""
        if self.text_keys:
            axis = self.graph_area.axis
            if self.text_keys.get('title'):
                axis.set_title(get_text(self.text_keys['title']))
            else:
                axis.set_title('')  # 页面已有页签标明技术，图内不再放标题
            axis.set_xlabel(get_text(self.text_keys['xlabel']))
            axis.set_ylabel(get_text(self.text_keys['ylabel']))
        # 默认图例名（数据 N / data N）跟随语言重新生成，用户命名过的不动
        # 计时电流页的 graph.data 是普通 list（不是 PyplotData），无此方法，
        # 不设保护会抛 AttributeError 中断主界面的整轮语言刷新（真机踩过：
        # Amp 页崩掉导致排最后的 ASV 页整页留中文）
        if hasattr(self.data, 'refresh_default_labels'):
            self.data.refresh_default_labels()
        if self.legend_displayed:
            self.update_legend()
        self._rebuild_toolbar()
        self.graph_area.canvas.draw_idle()

    def legend_handler(self, event):
        if event.x > (0.82 * self.winfo_width()):
            from dialogs import ChangeDataLegend
            self.master.change_data_labels()

    def resize_x(self, x_low, x_high):
        self.graph_area.axis.set_xlim([x_low, x_high])
        self.graph_area.canvas.draw()

    def resize_y(self, _current_limit):
        self.graph_area.axis.set_ylim(-_current_limit * 1.2, _current_limit * 1.2)
        self.graph_area.canvas.draw()
