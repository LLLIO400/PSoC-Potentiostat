# Copyright (c) 2023 Kyle Lopin (Naresuan University)
# Modified by Zhijian Yi (Guangdong, China), 2026 — PSoC-Potentiostat-YI-1.0
"""
Refactored unit tests for the 7-file structure.
Run: python test_refactored.py
"""

import os as _os
import sys as _sys
import re
import tempfile

# 测试位于 tests/ 子目录：无论从哪里启动，都把项目根加进模块搜索路径
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import os
import struct
import time
import unittest
from unittest import mock
import tkinter as tk

# Import from refactored modules
from config import DeviceParameters, GraphProps, CURRENT_OPTION_LIST
from comm import AmpUsb, SerialComm, get_tia_settings
import comm as comm_module
from plotting import PyplotEmbed
from experiments import CVFrame, AmpFrame, ASVFrame
from dialogs import CVSettingChanges
from utils import open_file, check_display_type, make_voltage_profile

# 参数文件隔离：整套测试只读写临时目录，绝不碰真实的 settings*.txt。
# （必须在任何 DeviceParameters 实例化之前生效；否则保存类测试会把
#  用户真实配置覆盖成测试值——这本身就是一类要防的回归）
import config as config_module
config_module.SETTINGS_DIR_OVERRIDE = tempfile.mkdtemp(
    prefix="psoc_gui_test_settings_")


def _unbind_configure(widget):
    """递归解绑 <Configure>：Windows 上销毁窗口过程中尺寸事件异步到达，
    回调打到正在销毁的部件就会 bgerror（Linux 复现不了，真机报告噪音）。"""
    try:
        widget.unbind('<Configure>')
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        _unbind_configure(child)


def _safe_destroy(root):
    """销毁 tk root：先解绑 <Configure>、取消所有挂起的 after 回调再销毁，
    最后重置 ttkbootstrap 样式单例。不做的话，Windows 上延迟回调/尺寸事件
    会在 root 销毁后触发，bgerror 打 "can't invoke tk command:
    application has been destroyed"（测试报告里的噪音行）。"""
    try:
        _unbind_configure(root)
    except tk.TclError:
        pass
    try:
        for after_id in root.tk.call('after', 'info'):
            try:
                root.after_cancel(after_id)
            except tk.TclError:
                pass
    except tk.TclError:
        pass
    try:
        root.update_idletasks()
        root.update()
    except tk.TclError:
        pass
    try:
        root.destroy()
    except tk.TclError:
        pass
    from ttkbootstrap.style.engine import Style
    Style.instance = None


# ===== Test AmpUSB =====
class TestAmpUSB(unittest.TestCase):
    # 注意：必须打桩 SerialComm。真机测试时常插着 PSoC，AmpUsb.__init__
    # 会自动连上并跑 calibrate()，把 counts_to_current 改成真机标定值，
    # 本测试的 0.05 预期就被覆盖了（真机踩坑：插着设备跑测试必挂）
    def _make_usb(self):
        mocked_root = mock.Mock()
        mocked_root.data_save_type = "Converted"
        device_params = DeviceParameters()
        device_params.adc_tia.shift = 0
        device_params.adc_tia.counts_to_current = 0.05
        with mock.patch('comm.SerialComm') as mock_serial_cls:
            mock_serial_cls.return_value.connected = False
            usb = AmpUsb(mocked_root, device_params)
        return usb

    def test_process_data(self):
        usb = self._make_usb()
        device_params = usb.device_params
        device_params.adc_tia.counts_to_current = 0.05
        _input = [5, 0, 10, 5, 15, 10, 20, 15, 25, 20,
                  20, 15, 15, 10, 10, 5, 5, 0]
        output = usb.process_data(_input, swv=True)
        correct_output = [0.25] * 9
        self.assertListEqual(output, correct_output)

    def test_process_data_swv_odd_length(self):
        """SWV 差分：奇数个采样时丢弃落单的最后一个，不再抛 IndexError。"""
        usb = self._make_usb()
        output = usb.process_data([10, 5, 15, 10, 20], swv=True)  # 5 个（奇数）
        self.assertListEqual(output, [0.25, 0.25])


# ===== Test SerialComm =====
mock_data1 = b'\xc8\x00d\x00\x00\x00d\x00\xc8\x00\xd5\x01\xea\x00\xfb\xff\x0b\xff \xfe'

class TestSerialComm(unittest.TestCase):
    @mock.patch('comm.SerialComm.auto_find_com_port')
    def setUp(self, mock_serial) -> None:
        self.serial_comm = SerialComm()

    @mock.patch('serial.Serial')
    def test_read_data(self, mock_serial):
        self.serial_comm.device = mock.Mock()
        self.serial_comm.device.read.return_value = mock_data1
        data = self.serial_comm.read_data(20, 'int16')
        self.assertListEqual(data, [200, 100, 0, 100, 200, 469, 234, -5, -245, -480])


# ===== Test CVFrame =====
class TestCVFrameUSB(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with mock.patch("experiments.CVFrame.make_graph_area") as mocked_graph:
            cls.mocked_root = mock.Mock()
            cls.mocked_root.frames = [mock.Mock()]
            cls.mocked_root.device_params = DeviceParameters()
            cls.mocked_root.device.get_data.return_value = [5, 0, 10, 5, 15, 10, 20, 15, 25, 20,
                                                            20, 15, 15, 10, 10, 5, 5, 0]
            cls.mocked_root.device.process_data.return_value = [0.25] * 9
            settings = cls.mocked_root.device_params.cv_settings
            settings.start_voltage = 0
            settings.end_voltage = 20
            settings.use_swv = True
            graph_props = GraphProps()
            # SWV 页面固定 use_swv=True（页面模式优先于全局设置）
            cls._cv_frame = CVFrame(cls.mocked_root, None, graph_props, mode='SWV')
            cls._cv_frame.device.run_button = mock.Mock()

    @mock.patch("experiments.CVFrame.make_graph_area")
    def test_get_and_display_data(self, mocked_graph):
        settings = self.mocked_root.device_params.cv_settings
        device = self.mocked_root.device
        mocked_canvas = mock.Mock()
        self._cv_frame.device.get_and_display_data(mocked_canvas)
        args = device.process_data.call_args.args
        kwargs = device.process_data.call_args.kwargs
        self.assertEqual(args, ([5, 0, 10, 5, 15, 10, 20, 15, 25, 20,
                                 20, 15, 15, 10, 10, 5, 5, 0],))  # SWV 不丢首点，保持成对
        # 差分方向随扫描方向（上行扫描时后减前，阳极峰朝上）
        self.assertEqual({'swv': True, 'ascending': True}, kwargs)


# ===== Test CV Settings =====
class TestCVSettingChanges(unittest.TestCase):
    def setUp(self) -> None:
        self.gui = tk.Tk()
        self.gui.device_params = DeviceParameters()
        self.top_level = CVSettingChanges(None, self.gui, None, None)
        self.initial_state = self.gui.device_params.cv_settings.use_swv

    def test_load_correctly(self):
        self.assertEqual(self.initial_state, self.top_level.use_swv.get())

    def tearDown(self):
        self.gui.destroy()
        # 重置 ttkbootstrap 样式单例：否则它指向已销毁的 root，
        # 同进程里后续测试新建 root 会炸 "application has been destroyed"
        from ttkbootstrap.style.engine import Style
        Style.instance = None


# ===== 三页面模式（CV / LS / SWV 共用 CVFrame） =====
class TestCVFrameModes(unittest.TestCase):
    def test_apply_mode(self):
        """_apply_mode 把页面对应的 (sweep_type, use_swv) 写入共享设置。"""
        from experiments import CVFrame
        for mode, (sweep, use_swv) in [('CV', ('CV', False)),
                                       ('LS', ('LS', False)),
                                       ('SWV', ('LS', True))]:
            frame = CVFrame.__new__(CVFrame)
            frame.mode = mode
            frame.settings = DeviceParameters().cv_settings
            frame._apply_mode()
            self.assertEqual(frame.settings.sweep_type, sweep)
            self.assertEqual(frame.settings.use_swv, use_swv)

    def test_mode_settings_dialog_mapping(self):
        """对话框的页面映射与 CVFrame 一致。"""
        from dialogs import CVSettingChanges
        from experiments import CVFrame
        for mode in ('CV', 'LS', 'SWV'):
            sweep, use_swv, title_key = CVSettingChanges.MODE_SETTINGS[mode]
            self.assertEqual((sweep, use_swv), CVFrame.MODE_SETTINGS[mode])

    def test_swv_dialog_shows_swv_group(self):
        """SWV 模式的设置窗口标题为方波设置，保存时 use_swv 固定为 True。"""
        gui = tk.Tk()
        gui.device_params = DeviceParameters()
        try:
            dlg = CVSettingChanges(None, gui, None, None, mode='SWV')
            self.assertTrue(dlg.use_swv.get())
            self.assertEqual(dlg.sweep_type.get(), 'LS')
            dlg.destroy()
            dlg = CVSettingChanges(None, gui, None, None, mode='LS')
            self.assertFalse(dlg.use_swv.get())
            self.assertEqual(dlg.sweep_type.get(), 'LS')
            dlg.destroy()
        finally:
            gui.destroy()


    def test_run_scan_resends_params_per_mode(self):
        """页面模式与固件当前配置不同时必须重发参数（LS 页不能再沿用 CV 配置）。"""
        from experiments import CVFrame
        from unittest import mock as _m
        device = _m.Mock()
        device.last_experiment = 'CV'  # 固件里当前是 CV 参数
        device.set_last_run.side_effect = \
            lambda m: setattr(device, 'last_experiment', m)
        device.connected = True
        master = _m.Mock()
        master.device_params = DeviceParameters()
        after_cbs = []
        master.after.side_effect = lambda ms, cb=None: after_cbs.append(cb)
        handler = CVFrame.USBHandler(_m.Mock(), device, master, _m.Mock(), mode='LS')
        handler.run_scan(_m.Mock(), _m.Mock())
        after_cbs.pop(0)()  # 触发静置结束回调 → 真正写 'R'
        sent = [c.args[0] for c in device.usb_write.call_args_list]
        # 重发 LS 的 S 命令 → 静置跳起始电位（-900 mV → D|2948）→ R
        self.assertEqual(sent, ['S|2948|1148|02399|LS', 'D|2948', 'R'])
        self.assertEqual(device.last_experiment, 'LS')
        # 同页再跑一次：参数未变，不应重发 S 命令
        after_cbs.clear()  # 丢弃第一圈遗留的轮询回调，再跑第二轮
        device.reset_mock()
        handler.run_scan(_m.Mock(), _m.Mock())
        after_cbs.pop(0)()
        sent2 = [c.args[0] for c in device.usb_write.call_args_list]
        self.assertEqual(sent2, ['D|2948', 'R'])

    def test_run_scan_quiet_time_zero_skips_hold(self):
        """静置 0 秒：不写 D| 电位指令，直接同步写 R（关闭预处理）。"""
        from experiments import CVFrame
        from unittest import mock as _m
        device = _m.Mock()
        device.last_experiment = 'LS'
        device.connected = True
        master = _m.Mock()
        master.device_params = DeviceParameters()
        master.device_params.cv_settings.sweep_type = 'LS'
        master.device_params.cv_settings.use_swv = False
        master.device_params.cv_settings.quiet_time_s = 0.0
        handler = CVFrame.USBHandler(_m.Mock(), device, master, _m.Mock(), mode='LS')
        handler.run_scan(_m.Mock(), _m.Mock())
        sent = [c.args[0] for c in device.usb_write.call_args_list]
        self.assertEqual(sent, ['R'])

    def test_run_scan_quiet_time_holds_at_start(self):
        """静置 >0：先写 D| 起始电位再 R，静置时长计入进度窗估算。"""
        from experiments import CVFrame
        from unittest import mock as _m
        device = _m.Mock()
        device.last_experiment = 'SWV'
        device.connected = True
        master = _m.Mock()
        master.device_params = DeviceParameters()
        s = master.device_params.cv_settings
        s.start_voltage, s.end_voltage = -400, 600
        s.sweep_type, s.use_swv = 'LS', False
        s.quiet_time_s = 2.5
        after_cbs = []
        master.after.side_effect = lambda ms, cb=None: after_cbs.append(cb)
        handler = CVFrame.USBHandler(_m.Mock(), device, master, _m.Mock(), mode='SWV')
        with _m.patch.object(CVFrame.USBHandler, '_show_progress') as m_prog:
            handler.run_scan(_m.Mock(), _m.Mock())
            m_prog.assert_called_once_with(2.5)  # 静置计入倒计时总时长
            after_cbs.pop(0)()                   # 静置结束 → 写 'R'
        sent = [c.args[0] for c in device.usb_write.call_args_list]
        self.assertEqual(sent, ['D|2448', 'R'])  # 先 -400 mV 静置，后 R


# ===== CV 多圈 GUI 逐圈调度（回归：设 N 圈只出 N-1 圈） =====
class TestCVMultiCycleLoop(unittest.TestCase):
    """固件不支持多圈 LUT（探测到单圈长度）时，GUI 逐圈调度必须跑满设定的圈数。

    真机踩坑（2026-10-13）：_loop_cycles_left 被初始化成 cycles - 1，
    首圈 Done 抓完计数即归零、直接出图——设 2 圈只出 1 圈
    （日志：x_line 期待 4001 点 vs 数据 2001 点，被截断保存）。
    """

    def _run_multicycle(self, cycles):
        from utils import make_cv_x_line
        device = mock.Mock()
        device.last_experiment = 'CV'          # 命中"参数未变"分支，不重发 S
        device.connected = True
        device.usb_read_data.return_value = 'Done '   # 每次轮询都报告扫完
        cycle_raw = [0] + [100] * 2001         # 首点为上电残留，抓取时被丢弃
        device.get_data.side_effect = lambda n: list(cycle_raw)
        device.process_data.side_effect = [
            [1.0] * 2001, [2.0] * 2001, [3.0] * 2001]

        master = mock.Mock()
        master.device_params = DeviceParameters()
        s = master.device_params.cv_settings
        s.start_voltage, s.end_voltage = -400, 600
        s.sweep_rate, s.delay_time = 0.2, 10000.0
        s.sweep_type, s.use_swv = 'CV', False
        s.cycles = cycles

        after_cbs = []
        master.after.side_effect = lambda ms, fn=None: after_cbs.append(fn)

        handler = CVFrame.USBHandler(mock.Mock(), device, master,
                                     mock.Mock(), mode='CV')
        handler._fw_multi_cycle = False
        canvas = mock.Mock()
        with mock.patch('experiments.messagebox'):
            handler.run_scan(canvas, mock.Mock())
            self.assertTrue(handler.is_running)
            # 首次回调 = 静置结束发 R；此后每圈一次 Done 轮询回调
            for i in range(cycles + 1):
                self.assertTrue(after_cbs, '第 %d 次回调丢失' % (i + 1))
                after_cbs.pop(0)()
        # 命令序列：D| 静置跳起始电位 → R 首圈；之后每圈 抓取(E0)→再启动(R)
        sent = [c.args[0] for c in device.usb_write.call_args_list]
        self.assertEqual(sent, ['D|2448', 'R'] + ['E0', 'R'] * (cycles - 1) + ['E0'])
        self.assertEqual(canvas.update_data.call_count, 1)
        x_line, data = canvas.update_data.call_args.args[:2]
        expected_x = make_cv_x_line(s.start_voltage, s.end_voltage,
                                    master.device_params.dac.voltage_step_size,
                                    s.sweep_start_type, cycles, per_cycle=True)
        self.assertEqual(len(x_line), len(expected_x))
        self.assertEqual(len(data), len(expected_x))   # 不允许再出现截断
        return data

    def test_two_cycles(self):
        """设 2 圈必须出 2 圈（真机翻车的最小场景）。

        圈间重复的起始电位点拼前去掉：第 2 圈贡献 2001-1 = 2000 点，
        总点数与 x 轴（4001）严格一致。"""
        data = self._run_multicycle(2)
        self.assertEqual(data[:2001], [1.0] * 2001)
        self.assertEqual(data[2001:], [2.0] * 2000)

    def test_three_cycles(self):
        """设 3 圈必须出 3 圈（旧 bug 下只出 2 圈的场景）。"""
        data = self._run_multicycle(3)
        self.assertEqual(data, ([1.0] * 2001 + [2.0] * 2000 + [3.0] * 2000))


# ===== 回归：圈数不再按容量拒绝，超容量自动降级逐圈调度 =====
class TestCyclesCapacityGate(unittest.TestCase):
    """任意圈数都必须能保存并跑满：容量内走固件零间隙连续循环，
    超容量时发送端自动降级 GUI 逐圈调度（圈间停约 1 s）。

    历史：对话框曾按波形表容量一刀切拒绝圈数——旧固件时期挡下合法
    的 3 圈（"最多约 2 圈"），刷入多圈固件后又会挡下 6 圈。
    """

    def _save_cycles(self, fw_multi, cycles):
        from config import CURRENT_OPTION_LIST
        gui = tk.Tk()
        gui.device_params = DeviceParameters()
        gui.device_params.fw_cv_multi_cycle = fw_multi
        device = mock.Mock()
        try:
            display = CVSettingChanges(None, gui, None, device)
            display.start_volt.set(-400)
            display.end_volt.set(600)
            display.freq.set(0.2)
            display.cycles.set(cycles)
            before = gui.device_params.cv_settings.cycles
            master = mock.Mock()
            master.device_params = gui.device_params
            with mock.patch('dialogs.messagebox') as mb:
                display.save_cv_changes(CURRENT_OPTION_LIST[0], master,
                                        mock.Mock(), mock.Mock())
            # 先快照保存结果：finally 的恢复会原地改写同一 settings 对象
            saved_cycles = gui.device_params.cv_settings.cycles
            return mb, saved_cycles, device, before
        finally:
            # 恢复默认设置并回写，避免污染共享临时目录里后续测试的基线
            try:
                gui.device_params.cv_settings.update_settings(
                    -900, 900, 0.2, 'CV', 'Start', 100, 5, 100, False,
                    cycles=1)
            except Exception:
                pass
            gui.destroy()
            # ttkbootstrap 的 Style 单例仍指向刚销毁的 root，同进程里
            # 后续测试新建 root 会炸 "application has been destroyed"；
            # 重置后下次组件创建自动按新 root 重建（与 _safe_destroy 一致）
            try:
                from ttkbootstrap.style import Style
                Style.instance = None
            except Exception:
                pass

    def test_over_capacity_cycles_saved_on_old_firmware(self):
        """旧固件（fw_cv_multi_cycle=False）：3 圈 > 容量 2 圈照常保存。"""
        mb, saved, device, _ = self._save_cycles(False, 3)
        self.assertFalse(mb.showerror.called, "不应按 LUT 容量拦截圈数")
        self.assertEqual(saved, 3)
        self.assertTrue(device.send_cv_parameters.called)

    def test_over_capacity_cycles_saved_on_patched_firmware(self):
        """固件多圈（True）：6 圈超容量也必须保存，运行时自动降级逐圈。"""
        mb, saved, device, _ = self._save_cycles(True, 6)
        self.assertFalse(mb.showerror.called, "不应按 LUT 容量拦截圈数")
        self.assertEqual(saved, 6)
        self.assertTrue(device.send_cv_parameters.called)

    def test_unknown_firmware_saved(self):
        """未探测（None）：照常保存。"""
        mb, saved, device, _ = self._save_cycles(None, 3)
        self.assertFalse(mb.showerror.called)
        self.assertEqual(saved, 3)


# ===== 发送端圈数路由：容量内带 |N 固件多圈，超容量发单圈走 GUI 循环 =====
class TestCycleSendRouting(unittest.TestCase):
    """send_cv_parameters 必须按容量选择实现方式：
      N ≤ 容量 → 'S|...|CS|N'（固件 LUT 连续循环）；
      N > 容量 → 'S|...|CS'（单圈命令，无尾缀），由 GUI 逐圈调度跑满。
    绝不能把超容量的 |N 发给固件（LUT 截尾会悄悄少扫圈数）。
    """

    def _send(self, cycles):
        device = mock.Mock()
        device.last_experiment = 'CV'
        device.get_lut_length.return_value = 4002  # 2 圈 LUT（模拟多圈固件）
        master = mock.Mock()
        master.device_params = DeviceParameters()
        s = master.device_params.cv_settings
        s.start_voltage, s.end_voltage = -400, 600   # 容量 = 2 圈
        s.sweep_rate, s.delay_time = 0.2, 2000.0
        s.sweep_type, s.use_swv = 'CV', False
        s.cycles = cycles
        handler = CVFrame.USBHandler(mock.Mock(), device, master,
                                     mock.Mock(), mode='CV')
        handler.format_voltage_with_gnd = lambda mv: ('{0:04d}'.format(2048 - mv),
                                                      2048 - mv)
        handler.format_divider = lambda rate: ('02399', 3840)
        with mock.patch('experiments.time.sleep'):
            handler.send_cv_parameters()
        sent = [c.args[0] for c in device.usb_write.call_args_list]
        s_msg = next(m for m in sent if m.startswith('S|'))
        return s_msg, handler, device

    def test_within_capacity_uses_fw_multicycle(self):
        """2 圈 = 容量上限：带 |2 尾缀，探测命中固件多圈。"""
        s_msg, handler, device = self._send(2)
        self.assertTrue(s_msg.endswith('|CS|2'), s_msg)
        self.assertTrue(handler._fw_multi_cycle)
        self.assertTrue(device.get_lut_length.called)

    def test_over_capacity_falls_back_to_gui_loop(self):
        """6 圈 > 容量 2 圈：显式发 '|1'（固件缓冲残留会继承旧圈数，真机实测），
        不探测，走 GUI 循环跑满 6 圈。"""
        s_msg, handler, device = self._send(6)
        self.assertEqual(s_msg, 'S|2448|1448|02399|CS|1')
        self.assertFalse(handler._fw_multi_cycle)
        self.assertFalse(device.get_lut_length.called)
        self.assertEqual(handler.settings.cycles, 6, "不得私自改写用户圈数")

    def test_capacity_fallback_notice_shown(self):
        """超容量逐圈调度时必须弹双语提示（用户要求，勿静默降级）。"""
        device = mock.Mock()
        device.last_experiment = 'CV'
        device.connected = True
        device.usb_read_data.return_value = 'Done '
        cycle_raw = [0] + [100] * 2001
        device.get_data.side_effect = lambda n: list(cycle_raw)
        device.process_data.side_effect = [[1.0] * 2001] * 7

        master = mock.Mock()
        master.device_params = DeviceParameters()
        s = master.device_params.cv_settings
        s.start_voltage, s.end_voltage = -400, 600
        s.sweep_rate, s.delay_time = 0.2, 2000.0
        s.sweep_type, s.use_swv = 'CV', False
        s.cycles = 3
        after_cbs = []
        master.after.side_effect = lambda ms, fn=None: after_cbs.append(fn)

        handler = CVFrame.USBHandler(mock.Mock(), device, master,
                                     mock.Mock(), mode='CV')
        handler._fw_multi_cycle = False
        handler._cycles_over_capacity = True   # 超容量降级（send 阶段已定）
        canvas = mock.Mock()
        with mock.patch('experiments.messagebox') as mb:
            handler.run_scan(canvas, mock.Mock())
            # 提示在启动扫描时弹出一次，带圈数与容量上限
            self.assertTrue(mb.showinfo.called)
            title = mb.showinfo.call_args.args[0]
            text = mb.showinfo.call_args.args[1]
            self.assertTrue(title, "标题不能为空")
            self.assertIn('3', text)
            after_cbs.pop(0)()                     # 静置结束 → 写 'R'
            for _ in range(3):                     # 每圈一次 Done 轮询回调
                self.assertTrue(after_cbs)
                after_cbs.pop(0)()
        sent = [c.args[0] for c in device.usb_write.call_args_list]
        self.assertEqual(sent, ['D|2448', 'R'] + ['E0', 'R'] * 2 + ['E0'])


# ===== 回归：CA 平滑等长 + 计时电流自动存档 =====
class TestRollingMean(unittest.TestCase):
    """rolling_mean 旧实现把前 N-1 点原样透传（未平滑）：段式采集时
    每段开头混入生数据、段边界出现台阶毛刺——计时电流页用户报告的
    "重复点"。新实现为因果滑动均值，输出与输入等长、头部同样平滑。"""

    def test_output_same_length(self):
        from comm import rolling_mean
        out = rolling_mean(list(range(100)), 50)
        self.assertEqual(len(out), 100)

    def test_constant_input_constant_output(self):
        from comm import rolling_mean
        out = rolling_mean([7.0] * 200, 50)
        self.assertTrue(all(abs(v - 7.0) < 1e-9 for v in out))

    def test_head_is_smoothed_not_raw_passthrough(self):
        from comm import rolling_mean
        out = rolling_mean([100.0] + [0.0] * 99, 50)
        self.assertAlmostEqual(out[1], 50.0)        # 旧实现这里是原始 0
        self.assertAlmostEqual(out[10], 100.0 / 11)
        self.assertAlmostEqual(out[49], 2.0)        # 满窗起点 100/50
        self.assertAlmostEqual(out[99], 0.0)


class TestAmpAutosave(unittest.TestCase):
    """计时电流页此前是唯一没有自动存档的方法页，现与手动保存同格式。"""

    def test_autosave_amp_writes_csv(self):
        import tempfile
        from experiments import AmpFrame
        device = mock.Mock()
        device.settings.sampling_rate = 400
        device.settings.voltage = -500
        device.time = [0.0, 0.5, 1.0]
        device.data = [1.5, 2.5, 3.5]
        tmpdir = tempfile.mkdtemp(prefix="amp_autosave_")
        path = AmpFrame.autosave_amp(device, backup_dir=tmpdir)
        self.assertTrue(path and os.path.exists(path))
        text = open(path, encoding='utf-8-sig').read()
        self.assertIn("# experiment: AMP", text)
        self.assertIn("# sampling_rate_Hz: 400", text)
        self.assertIn("# voltage_mV: -500", text)
        self.assertIn("time,current", text)
        self.assertIn("0.5, 2.5", text)

    def test_autosave_amp_skips_empty(self):
        import tempfile
        from experiments import AmpFrame
        device = mock.Mock()
        device.settings.sampling_rate = 400
        device.settings.voltage = -500
        device.time = []
        device.data = []
        tmpdir = tempfile.mkdtemp(prefix="amp_autosave_empty_")
        path = AmpFrame.autosave_amp(device, backup_dir=tmpdir)
        self.assertIsNone(path)
        self.assertEqual(os.listdir(tmpdir), [])


# ===== Test ASV Settings (LS-ASV 修复 + 搅拌联动) =====
class TestASVSettings(unittest.TestCase):
    def test_delay_time_units_ms(self):
        """LS 溶出延时: |end-plate| mV / (V/s) 数值上等于毫秒, tk after() 直接用。"""
        asv = DeviceParameters().asv_settings
        # 默认: |500-(-1500)| mV / 0.5 V/s = 4000 ms, +500 ms 余量
        self.assertEqual(asv.delay_time, 500 + 2000 / 0.5)

    def test_new_params_default(self):
        asv = DeviceParameters().asv_settings
        self.assertEqual(asv.rest_time, 15)
        self.assertTrue(asv.stirring_enabled)
        self.assertTrue(asv.clean_enabled)
        self.assertFalse(asv.clean_after_strip)

    def test_update_settings_stirring(self):
        asv = DeviceParameters().asv_settings
        asv.update_settings(800, 2, -1500, 300, 500, 0.5,
                            rest_time=20, stirring_enabled=False,
                            clean_after_strip=True, clean_enabled=False)
        self.assertEqual(asv.rest_time, 20)
        self.assertFalse(asv.stirring_enabled)
        self.assertFalse(asv.clean_enabled)
        self.assertTrue(asv.clean_after_strip)
        self.assertEqual(asv.delay_time, 500 + 2000 / 0.5)

    def test_dpv_delay_time(self):
        asv = DeviceParameters().asv_settings
        asv.update_settings(800, 2, -1500, 300, 500, 0.5, sweep_type=1,
                            pulse_height=100, pulse_inc=10, pulse_width=20)
        self.assertEqual(asv.sweep_type, "DPV")
        # 脉冲宽度(ms) * 步数, 仍是毫秒
        self.assertEqual(asv.delay_time, 500 + 20 * 2000 / 10)


# ===== Test stirring GPIO command =====
class TestStirringCommand(unittest.TestCase):
    @mock.patch('comm.SerialComm.auto_find_com_port', return_value=None)
    def setUp(self, mock_port):
        mocked_root = mock.Mock()
        mocked_root.data_save_type = "Converted"
        self.usb = AmpUsb(mocked_root, DeviceParameters())
        self.usb.device = mock.Mock()
        self.usb.device.connected = True
        self.usb.connected = True

    def test_stirring_on(self):
        self.usb.set_stirring(True)
        self.usb.device.write_data.assert_called_with("O|1")

    def test_stirring_off(self):
        self.usb.set_stirring(False)
        self.usb.device.write_data.assert_called_with("O|0")


# ===== Test ASV timing chain =====
class TestASVTimingChain(unittest.TestCase):
    def setUp(self):
        self.master = mock.Mock()
        self.master.device_params = DeviceParameters()
        self.device = mock.Mock()
        self.handler = ASVFrame.USBHandler(mock.Mock(), self.device, self.master, None)
        self.handler.run_button = mock.Mock()

    def test_done_with_null_terminator_accepted(self):
        """真机固件的完成消息是 'Done\\x00'（带空字符），必须识别为完成并取数。
        回归守护：2026-08-25 真机实测 ASV 用 == 比较导致永远等不到完成。"""
        self.device.usb_read_message.return_value = 'Done\x00'
        self.handler.get_and_display_data = mock.Mock()
        self.handler.timer_window = mock.Mock()
        self.handler.is_running = True
        self.handler.run_scan_continue()
        self.handler.get_and_display_data.assert_called_once()

    def test_none_message_does_not_crash(self):
        """设备未就绪时读到 None/空串，不应崩溃也不应误判为完成。"""
        self.device.usb_read_message.return_value = None
        self.handler.get_and_display_data = mock.Mock()
        self.handler.is_running = True
        self.handler._scan_deadline = 0  # 截止时间已过 → 走超时分支
        with mock.patch('experiments.messagebox.showerror'):
            self.handler.run_scan_continue()
        self.handler.get_and_display_data.assert_not_called()
        self.assertFalse(self.handler.is_running)  # 超时后界面恢复

    @mock.patch('dialogs.TimerToplevel')
    def test_full_chain_clean_plate_rest(self, mock_timer):
        settings = self.master.device_params.asv_settings
        self.handler.asv_run(self.handler.graph, self.handler.run_button)
        self.device.start_hardware.assert_called_once()
        self.device.short_tia_resistor.assert_called_once()
        self.device.set_anode_voltage.assert_called_with(settings.clean_volt)
        self.master.after.assert_called_with(
            int(settings.clean_time * 1000), self.handler.change_to_plating_voltage)

        self.handler.change_to_plating_voltage()
        self.device.set_anode_voltage.assert_called_with(settings.plate_volt)
        self.device.set_stirring.assert_called_with(True)
        self.master.after.assert_called_with(
            int(settings.plate_time * 1000), self.handler.start_rest_period)

        self.handler.start_rest_period()
        self.device.set_stirring.assert_called_with(False)
        self.master.after.assert_called_with(
            int(settings.rest_time * 1000), self.handler.give_stripping_step)

    @mock.patch('dialogs.TimerToplevel')
    def test_stirring_disabled_skips_gpio(self, mock_timer):
        self.master.device_params.asv_settings.stirring_enabled = False
        self.handler.change_to_plating_voltage()
        self.device.set_stirring.assert_not_called()
        self.handler.start_rest_period()
        self.device.set_stirring.assert_not_called()

    def test_rest_zero_goes_straight_to_strip(self):
        self.master.device_params.asv_settings.rest_time = 0
        self.handler.give_stripping_step = mock.Mock()
        self.handler.start_rest_period()
        self.handler.give_stripping_step.assert_called_once()

    @mock.patch('dialogs.TimerToplevel')
    def test_clean_disabled_goes_straight_to_plating(self, mock_timer):
        """一次性丝网印刷电极：关闭清洗后应直接进入富集，并立刻开搅拌。"""
        settings = self.master.device_params.asv_settings
        settings.clean_enabled = False
        self.handler.asv_run(self.handler.graph, self.handler.run_button)
        # 直接加富集电压，而不是清洗电压
        self.device.set_anode_voltage.assert_called_with(settings.plate_volt)
        self.device.set_stirring.assert_called_with(True)
        self.master.after.assert_called_with(
            int(settings.plate_time * 1000), self.handler.start_rest_period)
        # 计时弹窗收到的清洗时间应为 0
        timer_args = mock_timer.call_args.args
        self.assertEqual(timer_args[1], 0)

    def test_stop_turns_off_stirring(self):
        # 模拟富集中途（搅拌开启中）点停止
        self.handler._stirring_on = True
        self.handler.after_function = 123
        self.handler.stop()
        self.device.set_stirring.assert_called_with(False)
        self.device.reset.assert_called_once()
        self.master.after_cancel.assert_called_with(123)


# ===== Test ASV with fake device (固件模拟器集成测试) =====
class TestFakeDeviceIntegration(unittest.TestCase):
    """用 FakeSerialComm 走完整 ASV 流程：命令序列 + 数据导出 + 显示。"""

    def setUp(self):
        from fake_device import FakeSerialComm, make_fake_ampusb
        self.master = mock.Mock()
        self.master.device_params = DeviceParameters()
        self.master.data_save_type = "Converted"
        self.fake = FakeSerialComm()
        self.usb = make_fake_ampusb(self.master, self.fake)
        self.handler = ASVFrame.USBHandler(mock.Mock(), self.usb, self.master, None)
        self.handler.run_button = mock.Mock()

    @mock.patch('dialogs.TimerToplevel')
    def test_asv_full_command_and_data_flow(self, _timer):
        self.handler.asv_run(self.handler.graph, self.handler.run_button)
        self.handler.change_to_plating_voltage()
        self.handler.start_rest_period()
        self.handler.give_stripping_step()   # 发扫描参数 + 'R'，after 安排 run_scan_continue
        self.handler.run_scan_continue()     # 假设备已就绪 -> 读数据 -> 显示
        cmds = self.fake.received
        self.assertIn('H', cmds)                          # start hardware
        self.assertIn('s', cmds)                          # short TIA
        self.assertIn('O|1', cmds)                        # 搅拌开
        self.assertIn('O|0', cmds)                        # 搅拌关
        self.assertTrue(any(c.startswith('S|') for c in cmds))  # 扫描 LUT
        self.assertIn('R', cmds)                          # 开始扫描
        self.assertIn('E0', cmds)                         # 导出数据
        # 顺序约束：先开搅拌后关搅拌，关搅拌后才扫描
        self.assertLess(cmds.index('O|1'), cmds.index('O|0'))
        self.assertLess(cmds.index('O|0'), cmds.index('R'))
        # 搅拌日志与电压日志
        self.assertEqual(self.fake.stirring_log, ['on', 'off'])
        self.assertGreaterEqual(len(self.fake.voltage_log), 2)  # 清洗电压 + 富集电压
        # 图形收到非空数据且 x/y 等长
        self.handler.graph.update_data.assert_called_once()
        x, y, _raw = self.handler.graph.update_data.call_args.args
        self.assertGreater(len(x), 10)
        self.assertEqual(len(x), len(y))

    @mock.patch('dialogs.TimerToplevel')
    def test_asv_clean_disabled_flow(self, _timer):
        """关闭清洗：不应发送清洗电压，直接进入富集。"""
        settings = self.master.device_params.asv_settings
        settings.clean_enabled = False
        self.handler.asv_run(self.handler.graph, self.handler.run_button)
        self.handler.start_rest_period()
        self.handler.give_stripping_step()
        self.handler.run_scan_continue()
        # 无清洗阶段 -> 只加过一次电压（富集电压）
        self.assertEqual(len(self.fake.voltage_log), 1)
        self.handler.graph.update_data.assert_called_once()


class TestASVEndToEnd(unittest.TestCase):
    """真实 tk 事件循环 + 假设备的端到端测试（ASV 全流程自动走完）。"""

    def test_run_with_fake_device(self):
        import ttkbootstrap as tb
        from fake_device import FakeSerialComm, make_fake_ampusb
        root = tk.Tk()
        root.withdraw()
        root.device_params = DeviceParameters()
        root.data_save_type = "Converted"
        settings = root.device_params.asv_settings
        # 压缩时间参数，让全流程在 ~1.5 秒内跑完
        settings.clean_time = 0.05
        settings.plate_time = 0.05
        settings.rest_time = 0.05
        settings.sweep_rate = 10.0   # delay_time = 500 + 2000/10 = 700 ms
        settings.update_settings(settings.clean_volt, settings.clean_time,
                                 settings.plate_volt, settings.plate_time,
                                 settings.end_voltage, settings.sweep_rate)
        fake = FakeSerialComm()
        usb = make_fake_ampusb(root, fake)
        handler = ASVFrame.USBHandler(mock.Mock(), usb, root, None)
        graph = handler.graph
        button = tb.Button(root)
        handler.asv_run(graph, button)
        root.after(4000, lambda: _safe_destroy(root))
        root.mainloop()
        # 全流程结束后应已取到并显示数据
        graph.update_data.assert_called_once()
        x, y, _raw = graph.update_data.call_args.args
        self.assertGreater(len(x), 10)
        # 搅拌完整开关一次
        self.assertEqual(fake.stirring_log, ['on', 'off'])
        self.assertIn('E0', fake.received)

    def tearDown(self):
        # E2E 测试销毁了自己的 tk root，重置 ttkbootstrap 样式单例，
        # 避免后续测试拿到指向已销毁 root 的 Style
        from ttkbootstrap.style.engine import Style
        Style.instance = None


# ===== Test algorithms (Phase 7: SG 平滑 / 基线扣除 / 自动寻峰) =====
class TestAlgorithms(unittest.TestCase):
    """用 fake_device 生成的合成 Pb 溶出峰 CSV 验证算法。"""

    @classmethod
    def setUpClass(cls):
        from algorithms import analyze_stripping_peak
        from utils import load_csv_file_full
        cls.results = {}   # conc -> (peaks, processed, voltage, current)
        for conc in (20, 50, 100):
            path = os.path.join(os.path.dirname(__file__),
                                "test_data", f"LS-ASV_Pb_{conc}ppb.csv")
            _meta, header, rows = load_csv_file_full(path)
            v = [float(r[0]) for r in rows]
            c = [float(r[1]) for r in rows]
            peaks, processed = analyze_stripping_peak(v, c)
            cls.results[conc] = (peaks, processed, v, c)

    def test_peak_detected_at_lead_position(self):
        """三个浓度都应在铅溶出峰位（-300 mV 附近 ±80 mV）检出唯一主峰。"""
        for conc, (peaks, _p, _v, _c) in self.results.items():
            self.assertTrue(peaks, f"{conc} ppb 未检出峰")
            self.assertAlmostEqual(peaks[0]['voltage'], -300, delta=80)

    def test_peak_height_linear_with_concentration(self):
        """峰高应随浓度单调递增，且 100ppb/20ppb ≈ 5 倍（±30%）。"""
        h20 = self.results[20][0][0]['height']
        h50 = self.results[50][0][0]['height']
        h100 = self.results[100][0][0]['height']
        self.assertLess(h20, h50)
        self.assertLess(h50, h100)
        self.assertAlmostEqual(h100 / h20, 5.0, delta=1.5)

    def test_baseline_flattened(self):
        """基线扣除后，远离峰区域的电流均值应接近 0。"""
        for conc, (peaks, processed, v, _c) in self.results.items():
            far = [c for vv, c in zip(v, processed) if vv < -1000 or vv > 200]
            self.assertAlmostEqual(sum(far) / len(far), 0, delta=1.0)

    def test_sg_smooth_short_and_even(self):
        """边界情况：短数据原样返回；偶数窗口自动调整不报错。"""
        from algorithms import sg_smooth
        self.assertEqual(sg_smooth([1, 2, 3]), [1, 2, 3])
        out = sg_smooth(list(range(50)), window=10)
        self.assertEqual(len(out), 50)

    def test_detect_peaks_empty(self):
        from algorithms import detect_peaks
        self.assertEqual(detect_peaks([1, 2], [1, 2]), [])
        flat = [5.0] * 100
        self.assertEqual(detect_peaks(list(range(100)), flat), [])


# ===== 算法按钮的图侧集成（Phase 7 GUI 集成） =====
class TestAnalysisGraphOps(unittest.TestCase):
    """验证 PyplotEmbed 的框选/平滑/基线/寻峰标注，以及 CVFrame.get_metadata / ASVFrame.get_metadata。"""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        from config import GraphProps
        from plotting import PyplotData
        fake_frame = tk.Frame(self.root)  # master 必须是真实 tk 组件
        fake_frame.data = PyplotData()
        self.graph = PyplotEmbed(self.root, GraphProps().cv_plot,
                                 fake_frame, 50, -1500, 500)
        # 跳过数据标签弹窗
        self.graph.user_sets_labels_after_run = False

    def tearDown(self):
        _safe_destroy(self.root)
        import matplotlib.pyplot as plt
        plt.close('all')  # 防止 pyplot 注册表累积 figure 导致内存膨胀

    def _load_curve(self):
        from utils import load_csv_file_full
        path = os.path.join(os.path.dirname(__file__),
                            "test_data", "LS-ASV_Pb_50ppb.csv")
        _meta, header, rows = load_csv_file_full(path)
        v = [float(r[0]) for r in rows]
        c = [float(r[1]) for r in rows]
        self.graph.update_data(v, c, label="Pb 50ppb")

    def test_smooth_baseline_peak_workflow(self):
        """完整流程：上数据 -> 框选区域 -> 平滑 -> 基线扣除 -> 寻峰标注。"""
        from algorithms import sg_smooth, subtract_baseline, analyze_stripping_peak
        self._load_curve()
        # 无数据时 transform_latest 应返回 False（另起一个空图验证）
        self.assertTrue(self.graph.toggle_region_selector())
        self.assertFalse(self.graph.toggle_region_selector())
        # 框选峰区域
        self.graph._on_region_selected(-400, -200)
        self.assertEqual(self.graph.peak_region, (-400, -200))
        # 平滑
        line_before = list(self.graph.get_latest_line().get_ydata())
        ok = self.graph.transform_latest(lambda v, c: (v, sg_smooth(c)))
        self.assertTrue(ok)
        line_after = list(self.graph.get_latest_line().get_ydata())
        self.assertNotEqual(line_before, line_after)
        # 基线扣除（区域外拟合）
        ok = self.graph.transform_latest(
            lambda v, c: (v, subtract_baseline(v, c, peak_region=self.graph.peak_region)))
        self.assertTrue(ok)
        # 寻峰 + 标注
        line = self.graph.get_latest_line()
        peaks, _ = analyze_stripping_peak(list(line.get_xdata()),
                                          list(line.get_ydata()),
                                          peak_region=self.graph.peak_region)
        self.assertTrue(peaks)
        self.graph.annotate_peaks(peaks)
        self.assertEqual(len(self.graph._peak_annotations), 2 * len(peaks))
        self.graph.clear_peak_annotations()
        self.assertEqual(self.graph._peak_annotations, [])

    def test_transform_latest_no_data(self):
        self.assertFalse(self.graph.transform_latest(lambda v, c: (v, c)))
        self.assertIsNone(self.graph.get_latest_line())

    def test_restore_latest(self):
        """平滑/基线后可还原为原始数据；未修改的曲线还原返回 False。"""
        from algorithms import sg_smooth
        self._load_curve()
        line = self.graph.get_latest_line()
        orig_v = list(line.get_xdata())
        orig_c = list(line.get_ydata())
        # 连续两次变换后仍可一键还原到最初数据
        self.assertTrue(self.graph.transform_latest(lambda v, c: (v, sg_smooth(c))))
        self.assertTrue(self.graph.transform_latest(lambda v, c: (v, c[::-1])))
        self.assertNotEqual(list(line.get_ydata()), orig_c)
        self.assertTrue(self.graph.restore_latest())
        self.assertEqual(list(line.get_xdata()), orig_v)
        self.assertEqual(list(line.get_ydata()), orig_c)
        # 再次还原：没有备份了
        self.assertFalse(self.graph.restore_latest())

    def test_metadata_and_csv_roundtrip(self):
        """get_metadata 生成参数头；保存后 open_data 能跳过 '#' 行读回数据。"""
        import csv
        import io
        self._load_curve()
        # 构造一个带 master 的 CVFrame 骨架，只测 get_metadata
        frame = CVFrame.__new__(CVFrame)
        frame.master = mock.Mock()
        frame.master.device_params = DeviceParameters()
        meta = frame.get_metadata()
        self.assertEqual(meta["experiment"], "CV")
        self.assertIn("sweep_rate_V/s", meta)
        # ASV 覆盖
        asv_frame = ASVFrame.__new__(ASVFrame)
        asv_frame.master = frame.master
        meta_asv = asv_frame.get_metadata()
        self.assertEqual(meta_asv["experiment"], "ASV")
        self.assertIn("stirring_enabled", meta_asv)
        self.assertIn("rest_time_s", meta_asv)
        # 写入带 metadata 的 CSV
        buf = io.StringIO()
        self.graph.data.save_all_data(buf, "Converted", meta_asv)
        text = buf.getvalue()
        self.assertTrue(text.startswith("# experiment: ASV"))
        # 用 load_csv_file 读回（与 main.open_data 同一入口）
        import os
        import tempfile
        from utils import load_csv_file
        with tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False,
                                         newline='', encoding='utf-8') as f:
            f.write(text)
            tmp = f.name
        try:
            header, data_rows = load_csv_file(tmp)
        finally:
            os.unlink(tmp)
        self.assertEqual(header[0], "voltage")
        # 数据行数与图形数据一致（notes 行已被跳过）
        self.assertEqual(len(data_rows), len(self.graph.data.voltage_data[0]))
        float(data_rows[0][0]); float(data_rows[0][1])

    def test_load_csv_file_formats(self):
        """load_csv_file 兼容本程序格式与通用两列格式；垃圾文件返回 None。"""
        import os
        import tempfile
        from utils import load_csv_file

        def _write(content):
            f = tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False,
                                            newline='', encoding='utf-8')
            f.write(content)
            f.close()
            return f.name

        # 通用两列格式（合成测试数据样式：label 行 + voltage_mV 表头）
        path = _write("label,LS-ASV Pb 50ppb (合成数据)\n"
                      "voltage_mV,current_uA\n-1500,4.6\n-1400,5.1\n")
        try:
            header, rows = load_csv_file(path)
        finally:
            os.unlink(path)
        self.assertEqual(header, ["voltage_mV", "current_uA"])
        self.assertEqual(rows, [["-1500", "4.6"], ["-1400", "5.1"]])

        # 本程序格式：'#' 元数据 + voltage 表头 + 备注行 + 数据
        path = _write("# experiment: ASV\n"
                      "voltage,current_1\nnotes: 测试\n-1500,4.6\n-1400,5.1\n")
        try:
            header, rows = load_csv_file(path)
        finally:
            os.unlink(path)
        self.assertEqual(header[0], "voltage")
        self.assertEqual(len(rows), 2)

        # 无法识别的文件 -> None
        path = _write("hello,world\n1,2\n")
        try:
            self.assertIsNone(load_csv_file(path))
        finally:
            os.unlink(path)


# ===== 删除数据清理（峰标注 + 图例随曲线一起消失） =====
class TestDeleteCleanup(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        from config import GraphProps
        from plotting import PyplotData
        fake_frame = tk.Frame(self.root)
        fake_frame.data = PyplotData()
        self.graph = PyplotEmbed(self.root, GraphProps().cv_plot,
                                 fake_frame, 50, -1500, 500)
        self.graph.user_sets_labels_after_run = False

    def tearDown(self):
        _safe_destroy(self.root)
        import matplotlib.pyplot as plt
        plt.close('all')

    @staticmethod
    def _make_peak_curve(center=-550.0, height=20.0):
        """生成一条带高斯峰的合成溶出曲线（v, c）。"""
        import math
        v = [-1500 + i * 10 for i in range(200)]  # -1500 ~ 490 mV
        c = [2.0 + height * math.exp(-((x - center) / 60.0) ** 2) for x in v]
        return v, c

    def test_delete_removes_annotations_and_legend(self):
        from algorithms import analyze_stripping_peak
        # 两条曲线：生成的峰形数据
        v1, c1 = self._make_peak_curve(-550.0, 20.0)
        v2, c2 = self._make_peak_curve(-700.0, 12.0)
        self.graph.update_data(v1, c1, label="Pb")
        self.graph.update_data(v2, c2, label="Cd")
        self.assertIsNotNone(self.graph.graph_area.axis.get_legend())
        # 寻峰并标注（图上出现红色标记）
        line = self.graph.get_latest_line()
        peaks, _ = analyze_stripping_peak(list(line.get_xdata()),
                                          list(line.get_ydata()))
        self.assertTrue(peaks)
        self.graph.annotate_peaks(peaks)
        self.assertTrue(self.graph._peak_annotations)
        # 删除一条曲线：峰标注必须一起消失，图例仍在（还剩一条）
        self.graph.delete_a_line(1)
        self.assertEqual(self.graph._peak_annotations, [])
        self.assertIsNotNone(self.graph.graph_area.axis.get_legend())
        # 删除全部：图例也消失
        self.graph.delete_all_lines()
        self.assertIsNone(self.graph.graph_area.axis.get_legend())


# ===== 真实计时窗 + 假设备全链路（复现 2026-08-25 真机卡死） =====
class TestAsvRealTimerWindow(unittest.TestCase):
    """不用 Mock 替换 TimerToplevel：真实弹窗 + 真实 tk 事件循环 + 假设备，
    验证 ASV 全流程中窗口能正确切换扫描阶段并最终关闭。"""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.device_params = DeviceParameters()
        self.root.data_save_type = "Converted"

    def tearDown(self):
        _safe_destroy(self.root)

    def _pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)

    def test_stale_tick_cannot_overwrite_scan_phase(self):
        """show_scan 之后残留的定时回调不得把文字改回"静置剩余 1.0"。"""
        from dialogs import TimerToplevel
        from i18n import get_text
        dlg = TimerToplevel(self.root, 0, 1, 1)
        self.root.update()
        dlg.show_scan(5)
        dlg.run()  # 模拟 show_scan 之前已排进事件队列的残留回调
        self.assertEqual(dlg.stage, 'scan')
        self.assertEqual(dlg.toplabel.cget('text'),
                         get_text('scan_progress_left').format(5))
        dlg.close()

    def test_scan_phase_counts_down(self):
        """扫描阶段是会走的倒数：每秒减一，进度条按比例前进，归零显示处理中。"""
        from dialogs import TimerToplevel
        from i18n import get_text
        dlg = TimerToplevel(self.root, 0, 1, 1)
        self.root.update()
        dlg.show_scan(3)
        dlg._scan_tick()
        self.assertEqual(dlg.toplabel.cget('text'),
                         get_text('scan_progress_left').format(2))
        self.assertEqual(dlg._progress_bar['value'], 1)
        dlg._scan_tick()
        dlg._scan_tick()
        self.assertEqual(dlg.toplabel.cget('text'), get_text('scan_finishing'))
        dlg.close()
        self.assertFalse(dlg.winfo_exists())

    def test_full_asv_chain_real_window_fake_device(self):
        """富集0s/静置0s 全链真跑：窗口切扫描→数据回读→画图→窗口自动关闭。"""
        from fake_device import FakeSerialComm, make_fake_ampusb
        from i18n import get_text
        settings = self.root.device_params.asv_settings
        settings.update_settings(settings.clean_volt, 0, settings.plate_volt, 0,
                                 -1180, settings.sweep_rate, sweep_type=1,
                                 pulse_height=50, pulse_inc=16, pulse_width=10,
                                 rest_time=0, clean_enabled=False)
        fake = FakeSerialComm()
        usb = make_fake_ampusb(self.root, fake)
        graph = mock.Mock()
        handler = ASVFrame.USBHandler(graph, usb, self.root, None)
        handler.asv_run(graph, mock.Mock())
        dlg = handler.timer_window
        self.assertIsNotNone(dlg)
        # 扫描阶段（delay ≈ 500+10*20 = 700ms）：窗口应已切到扫描显示
        self._pump(0.3)
        self.assertEqual(dlg.stage, 'scan')
        self.assertEqual(dlg.toplabel.cget('text'),
                         get_text('scan_progress_left').format(
                             max(1, int(round(handler._estimate_scan_seconds())))))
        # 扫描完成后：数据画图 + 窗口自动销毁 + 引用清空
        self._pump(2.0)
        graph.update_data.assert_called_once()
        self.assertFalse(dlg.winfo_exists())
        self.assertIsNone(handler.timer_window)
        self.assertTrue(any(cmd.startswith('P|') for cmd in fake.received))


# ===== DPV-ASV 修复（x 轴 / packet_count / 参数校验） =====
class TestDpvXLineAndValidation(unittest.TestCase):
    def test_make_dpv_x_line(self):
        from utils import make_dpv_x_line
        # 上行
        x = make_dpv_x_line(-1500, -1100, 100)
        self.assertEqual(x, [-1500, -1400, -1300, -1200, -1100])
        # 下行
        x = make_dpv_x_line(500, 100, 100)
        self.assertEqual(x, [500, 400, 300, 200, 100])
        # 不整除时按整数步数截断（与固件 LUT 一致）
        x = make_dpv_x_line(-1500, -1180, 100)
        self.assertEqual(len(x), 320 // 100 + 1)
        # 非法步进
        with self.assertRaises(ValueError):
            make_dpv_x_line(-1500, 500, 0)

    def test_validate_dpv_params(self):
        from utils import validate_dpv_params
        self.assertEqual(validate_dpv_params(-1500, 500, 0, 10, 100),
                         "err_pulse_height")
        self.assertEqual(validate_dpv_params(-1500, 500, 50, 0, 100),
                         "err_pulse_inc")
        self.assertEqual(validate_dpv_params(-1500, 500, 50, 10, 5),
                         "err_pulse_width")
        self.assertEqual(validate_dpv_params(-1500, -1490, 50, 20, 100),
                         "err_scan_range")
        self.assertIsNone(validate_dpv_params(-1500, 500, 50, 10, 100))


class TestDpvAsvEndToEnd(unittest.TestCase):
    """DPV 模式全流程：P 命令（真 DPV 固件） -> 差分数据 -> x 轴点数与数据一一对应。
    用同步 after 的 Mock master 驱动时序链（避免多套 tk root 竞争崩溃）。"""

    @mock.patch('dialogs.TimerToplevel')
    def test_dpv_run_with_fake_device(self, _mock_timer):
        from fake_device import FakeSerialComm, make_fake_ampusb
        master = mock.Mock()
        master.device_params = DeviceParameters()
        master.data_save_type = "Converted"
        # after 立即同步执行回调，整条 清洗->富集->静置->溶出 链一次走完
        master.after.side_effect = lambda _delay, cb: cb()
        settings = master.device_params.asv_settings
        # DPV：脉冲增量 16 mV（= 1 个 8-bit DAC 计数），终点 -1180 mV -> 20 步
        settings.update_settings(settings.clean_volt, settings.clean_time,
                                 settings.plate_volt, settings.plate_time,
                                 -1180, settings.sweep_rate, sweep_type=1,
                                 pulse_height=50, pulse_inc=16, pulse_width=10)
        self.assertEqual(settings.sweep_type, "DPV")
        fake = FakeSerialComm()
        usb = make_fake_ampusb(master, fake)
        handler = ASVFrame.USBHandler(mock.Mock(), usb, master, None)
        handler.asv_run(handler.graph, mock.Mock())
        graph = handler.graph
        graph.update_data.assert_called_once()
        x, y, _raw = graph.update_data.call_args.args
        # 21 步，x 轴与差分后数据等长
        self.assertEqual(len(x), 21)
        self.assertEqual(len(x), len(y))
        self.assertEqual(x[0], settings.low_voltage)
        self.assertEqual(x[-1], -1180)
        # 发的是 P 命令（真 DPV 固件补丁）
        self.assertTrue(any(cmd.startswith('P|') for cmd in fake.received))
        # packet_count 与固件发送点数一致：2*21 点 / 32 点每包 -> 2 包
        self.assertEqual(handler.usb_packet_count, 2)
        # 差分电流应在峰位附近出现明显峰（合成峰高约 400 计数 -> 0.4 µA）
        self.assertGreater(max(y), 0.2)


class TestDpvDialogValidation(unittest.TestCase):
    """ASV 设置对话框：DPV 非法参数弹错并拒绝保存，合法参数正常保存。
    整个类共用一个 tk root，避免反复建销毁解释器。"""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.root.device_params = DeviceParameters()
        cls.root.update_current_range = mock.Mock()

    @classmethod
    def tearDownClass(cls):
        _safe_destroy(cls.root)

    def _make_dialog(self):
        from dialogs import ASVSettingChanges
        return ASVSettingChanges(mock.Mock(), self.root, mock.Mock(), mock.Mock())

    def test_invalid_pulse_inc_rejected(self):
        dialog = self._make_dialog()
        dialog.sweep_type.set(1)       # DPV
        dialog.pulse_inc.set(0)        # 非法
        with mock.patch('dialogs.messagebox.showerror') as err_box:
            dialog.save()
        err_box.assert_called_once()
        # 参数未被保存
        self.assertEqual(self.root.device_params.asv_settings.sweep_type, "LS")
        dialog.destroy()

    def test_valid_dpv_saved(self):
        dialog = self._make_dialog()
        dialog.sweep_type.set(1)
        dialog.pulse_height.set(50)
        dialog.pulse_inc.set(10)
        dialog.pulse_width.set(100)
        with mock.patch('dialogs.messagebox.showerror') as err_box:
            dialog.save()
        err_box.assert_not_called()
        self.assertEqual(self.root.device_params.asv_settings.sweep_type, "DPV")
        # 恢复默认，避免影响其他测试
        self.root.device_params.asv_settings.sweep_type = "LS"


class TestSwvAsvEndToEnd(unittest.TestCase):
    """SWV 溶出全流程：G 命令须符合固件 user_lookup_table_maker_swv 的字段格式。"""

    @mock.patch('dialogs.TimerToplevel')
    def test_swv_run_with_fake_device(self, _mock_timer):
        from fake_device import FakeSerialComm, make_fake_ampusb
        master = mock.Mock()
        master.device_params = DeviceParameters()
        master.data_save_type = "Converted"
        master.after.side_effect = lambda _delay, cb: cb()
        settings = master.device_params.asv_settings
        # SWV：步进 16 mV（=1 个 8-bit DAC 计数），终点 -1180 mV -> 21 步
        settings.update_settings(settings.clean_volt, settings.clean_time,
                                 settings.plate_volt, settings.plate_time,
                                 -1180, settings.sweep_rate, sweep_type=2,
                                 pulse_height=50, pulse_inc=16, pulse_width=20)
        self.assertEqual(settings.sweep_type, "SWV")
        fake = FakeSerialComm()
        usb = make_fake_ampusb(master, fake)
        handler = ASVFrame.USBHandler(mock.Mock(), usb, master, None)
        handler.asv_run(handler.graph, mock.Mock())
        # G 命令格式: G|XXXX|YYYY|IIII|HHHH|ZZZZZ|AB（7 段，末段扫描类型 'LS'）
        g_cmds = [c for c in fake.received if c.startswith('G|')]
        self.assertEqual(len(g_cmds), 1)
        parts = g_cmds[0].split('|')
        self.assertEqual(len(parts), 7)
        step = master.device_params.dac.voltage_step_size
        self.assertEqual(parts[3], "{0:04d}".format(int(16 / step)))   # 步进计数
        self.assertEqual(parts[4], "{0:04d}".format(int(50 / step)))   # 幅值计数
        self.assertEqual(parts[6], "LS")
        # 数据：21 步差分后 21 点，x 轴等长
        handler.graph.update_data.assert_called_once()
        x, y, _raw = handler.graph.update_data.call_args.args
        self.assertEqual(len(x), 21)
        self.assertEqual(len(x), len(y))
        self.assertEqual(x[-1], -1180)
        # packet_count：2*21 点 / 32 每包 -> 2 包
        self.assertEqual(handler.usb_packet_count, 2)

    def test_swv_delay_time(self):
        """SWV 每步一个方波周期：delay = 500 + 周期ms × 步数。"""
        settings = DeviceParameters().asv_settings
        settings.update_settings(-1500, 60, -1500, 300, 500, 0.5, sweep_type=2,
                                 pulse_height=50, pulse_inc=10, pulse_width=100)
        self.assertEqual(settings.delay_time, 500 + 100 * (2000 / 10))


class TestSwvDialogValidation(unittest.TestCase):
    """SWV 单选项同样走参数校验。"""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.root.device_params = DeviceParameters()
        cls.root.update_current_range = mock.Mock()

    @classmethod
    def tearDownClass(cls):
        _safe_destroy(cls.root)

    def test_swv_invalid_and_valid(self):
        from dialogs import ASVSettingChanges
        dialog = ASVSettingChanges(mock.Mock(), self.root, mock.Mock(), mock.Mock())
        dialog.sweep_type.set(2)       # SWV
        dialog.pulse_inc.set(0)        # 非法
        with mock.patch('dialogs.messagebox.showerror') as err_box:
            dialog.save()
        err_box.assert_called_once()
        self.assertNotEqual(self.root.device_params.asv_settings.sweep_type, "SWV")
        dialog.destroy()
        # 合法保存
        dialog = ASVSettingChanges(mock.Mock(), self.root, mock.Mock(), mock.Mock())
        dialog.sweep_type.set(2)
        dialog.pulse_height.set(50)
        dialog.pulse_inc.set(10)
        dialog.pulse_width.set(100)
        with mock.patch('dialogs.messagebox.showerror') as err_box:
            dialog.save()
        err_box.assert_not_called()
        self.assertEqual(self.root.device_params.asv_settings.sweep_type, "SWV")
        self.root.device_params.asv_settings.sweep_type = "LS"  # 还原


# ===== 扫描进度弹窗（模态、禁止操作其它按钮） =====
class TestScanProgressDialog(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        _safe_destroy(self.root)

    def test_modal_grab_and_close(self):
        """弹窗期间持有 grab（主窗口点不动），close 后释放。"""
        from dialogs import ScanProgress
        from i18n import get_text
        dlg = ScanProgress(self.root, 5)
        self.root.update_idletasks()
        self.assertEqual(dlg.title(), get_text("scan_progress_title"))
        self.assertIs(self.root.grab_current(), dlg)  # 模态生效
        dlg.close()
        self.assertIsNone(self.root.grab_current())   # 关闭后释放

    def test_estimate_scan_seconds(self):
        """估算耗时：CV 往返两段、LS 一段、SWV 步数×周期。"""
        from experiments import CVFrame
        master = mock.Mock()
        master.device_params = DeviceParameters()
        handler = CVFrame.USBHandler(mock.Mock(), mock.Mock(), master, mock.Mock())
        s = master.device_params.cv_settings
        s.start_voltage, s.end_voltage = -500, 500
        s.delay_time = 0
        s.use_swv = False
        s.sweep_type = 'CV'
        s.sweep_rate = 0.1  # V/s -> 2 段 × 1V / 0.1 = 20 s
        self.assertAlmostEqual(handler._estimate_scan_seconds(), 20.0)
        s.sweep_type = 'LS'
        self.assertAlmostEqual(handler._estimate_scan_seconds(), 10.0)
        s.use_swv = True
        s.swv_inc, s.swv_period = 4, 20
        # SWV 独立页：固件对 'L' 型扫描只扫单段 start→end，
        # 250 步 × 20ms + 1s 余量 = 6 s（delay_time 不得重复计入）
        s.sweep_start_type = 'Start'
        self.assertAlmostEqual(handler._estimate_scan_seconds(), 6.0)

    def test_timer_window_merges_scan_phase(self):
        """计时窗倒计时结束后原地切换为扫描进度，close 后销毁。"""
        from dialogs import TimerToplevel
        dlg = TimerToplevel(self.root, 0, 1, 0)
        self.root.update_idletasks()
        dlg.show_scan(5)
        self.assertEqual(dlg.stage, 'scan')
        self.assertIsNotNone(dlg._progress_bar)
        dlg.close()
        self.assertFalse(dlg.winfo_exists())

    def test_asv_scan_reuses_timer_window(self):
        """溶出扫描开始时优先复用计时窗（show_scan），不再弹第二个进度窗。"""
        from experiments import ASVFrame
        master = mock.Mock()
        master.device_params = DeviceParameters()
        device = mock.Mock()
        device.connected = True
        handler = ASVFrame.USBHandler(mock.Mock(), device, master, None)
        handler.run_button = mock.Mock()
        timer = mock.Mock()
        handler.timer_window = timer
        handler._progress = None
        handler.run_scan(100)
        timer.show_scan.assert_called_once()      # 计时窗原地切换
        self.assertIsNone(handler._progress)      # 未创建独立进度窗

    def test_run_scan_failure_closes_progress(self):
        """扫描重试耗尽时弹窗必须关闭、运行按钮恢复，否则界面卡死。"""
        from experiments import CVFrame
        device = mock.Mock()
        device.usb_read_data.return_value = "not complete"
        master = mock.Mock()
        master.device_params = DeviceParameters()
        handler = CVFrame.USBHandler(mock.Mock(), device, master, mock.Mock())
        handler.run_button = mock.Mock()
        handler.is_running = True  # 停止/超时守卫：未在运行则直接返回
        tw = mock.Mock()
        handler.timer_window = tw
        handler.run_scan_continue(mock.Mock(), fail_count=999)  # 超过阈值
        tw.close.assert_called_once()
        self.assertIsNone(handler.timer_window)
        self.assertFalse(handler.is_running)


# ===== 标定异常保护（2026-08-25 真机 ZeroDivisionError 启动崩溃） =====
class TestCalibrationGuard(unittest.TestCase):
    GOOD = [200, 100, 0, 100, 200, 491, 245, -3, -247, -491]

    def _bare_usb(self, readings):
        from comm import AmpUsb
        usb = AmpUsb.__new__(AmpUsb)
        usb.device_params = DeviceParameters()
        usb.usb_read_data = mock.Mock(side_effect=readings)
        usb.usb_write = mock.Mock()
        return usb

    def test_zero_divisor_rejected(self):
        """data[5]==data[7] 的无效标定数据必须抛错而不是除零崩溃。"""
        adc = DeviceParameters().adc_tia
        with self.assertRaises(ValueError):
            adc.calibrate([0] * 10)

    def test_retry_recovers(self):
        """第一次数据无效时复位设备重试，第二次成功则正常标定。"""
        usb = self._bare_usb([[0] * 10, list(self.GOOD)])
        usb._calibrate_data()
        self.assertFalse(usb.device_params.adc_tia.calibrate_failed)

    def test_retry_wakes_hardware_without_reset(self):
        """标定重试绝不许用 'X' 复位（X 让固件硬件 Sleep 且断电前不恢复，
        'H' 唤不醒，X 后重试必再失败——真机两次实测实锤）；只能 H 唤醒后重试。"""
        usb = self._bare_usb([[0] * 10, list(self.GOOD)])
        usb._calibrate_data()
        calls = [c.args[0] for c in usb.usb_write.call_args_list]
        self.assertNotIn('X', calls)                 # 重试路径禁止 X
        self.assertEqual(calls, ['H', 'B'])          # 唤醒后重试 B

    def test_calibrate_wakes_hardware_first(self):
        """calibrate() 标定前先 'H' 唤醒：设备可能处于上次会话 X 留下
        的 Sleep 状态（断电前不恢复），直接标定必失败。"""
        usb = self._bare_usb([list(self.GOOD)])
        usb.connected = True
        usb.calibrate()
        calls = [c.args[0] for c in usb.usb_write.call_args_list]
        self.assertLess(calls.index('H'), calls.index('B'))

    def test_fallback_after_two_failures(self):
        """两次都失败：不崩溃，标记 calibrate_failed，沿用理论系数。"""
        usb = self._bare_usb([[0] * 10, [0] * 10])
        usb._calibrate_data()
        self.assertTrue(usb.device_params.adc_tia.calibrate_failed)


# ===== 扫描数据自动备份 =====
class TestAutosave(unittest.TestCase):
    def test_autosave_roundtrip(self):
        """备份文件能被 文件->打开数据 重新载入，且元数据记录实验类型。"""
        import csv
        import tempfile
        from utils import load_csv_file
        frame = CVFrame.__new__(CVFrame)
        frame.mode = 'LS'
        frame.master = mock.Mock()
        frame.master.device_params = DeviceParameters()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = frame.autosave_scan([0, 100, 200], [1.5, 2.5, 3.5],
                                       backup_dir=tmpdir)
            self.assertIsNotNone(path)
            self.assertTrue(os.path.exists(path))
            self.assertIn('LS', os.path.basename(path))
            with open(path, encoding='utf-8') as f:
                content = f.read()
            self.assertIn('# experiment: LS', content)  # 元数据可追溯
            header, data_rows = load_csv_file(path)
            self.assertEqual(header[0], 'voltage')
            self.assertEqual(len(data_rows), 3)
            self.assertAlmostEqual(float(data_rows[1][1]), 2.5)

    def test_autosave_failure_does_not_raise(self):
        """备份失败（目录不可写等）只记日志，不影响扫描主流程。"""
        frame = CVFrame.__new__(CVFrame)
        frame.mode = 'CV'
        frame.master = mock.Mock()
        frame.master.device_params = DeviceParameters()
        # 传一个非法路径（文件而非目录）触发异常
        import tempfile
        with tempfile.NamedTemporaryFile() as bad:
            path = frame.autosave_scan([0], [1.0],
                                       backup_dir=os.path.join(bad.name, 'x'))
        self.assertIsNone(path)


# ===== 只改量程也要刷新显示 =====
class TestCurrentRangeRefresh(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.device_params = DeviceParameters()
        self.root.update_current_range = mock.Mock()

    def tearDown(self):
        _safe_destroy(self.root)

    def test_save_only_range_change_still_refreshes(self):
        """扫描参数不变、只改电流量程时，保存后仍调用 update_current_range。"""
        idx = self.root.device_params.adc_tia.current_option_index
        dlg = CVSettingChanges(mock.Mock(), self.root, mock.Mock(), mock.Mock(),
                               mode='CV')
        dlg.save_cv_changes(CURRENT_OPTION_LIST[idx], self.root,
                            mock.Mock(), mock.Mock())
        self.root.update_current_range.assert_called_once()
        args = self.root.update_current_range.call_args.args
        self.assertEqual(args[0], CURRENT_OPTION_LIST[idx])
        # 换到另一个量程再保存，同样要刷新
        self.root.update_current_range.reset_mock()
        other = (idx + 1) % len(CURRENT_OPTION_LIST)
        dlg = CVSettingChanges(mock.Mock(), self.root, mock.Mock(), mock.Mock(),
                               mode='CV')
        dlg.save_cv_changes(CURRENT_OPTION_LIST[other], self.root,
                            mock.Mock(), mock.Mock())
        self.root.update_current_range.assert_called_once()
        self.assertEqual(self.root.update_current_range.call_args.args[0],
                         CURRENT_OPTION_LIST[other])


class TestRangeRefreshEndToEnd(unittest.TestCase):
    """真实 GUI 实例验证量程刷新：标签格式化必须用数值，传字符串不能崩。

    回归历史 bug：update_current_range 把 '±12.5 µA' 字符串传给
    {0:.1f} 格式化 → ValueError → tk 回调静默吞掉 → 保存设置看似失效。
    """

    @classmethod
    def setUpClass(cls):
        with mock.patch('main.AmpUsb') as mock_usb:
            mock_usb.return_value.connected = False
            import main as main_mod
            cls.app = main_mod.ElectroChemGUI()
            cls.app.update_idletasks()

    @classmethod
    def tearDownClass(cls):
        _safe_destroy(cls.app)

    def test_update_range_with_string_label_does_not_crash(self):
        app = self.app
        app.update_current_range(CURRENT_OPTION_LIST[4], 12.5)
        app.update_idletasks()
        text = app.cv.cv_settings_frame.current_var_str.get()
        self.assertIn('12.5', text)
        # 五个页面的标签都应刷新
        self.assertIn('12.5', app.ls.cv_settings_frame.current_var_str.get())
        self.assertIn('12.5', app.swv.cv_settings_frame.current_var_str.get())
        self.assertIn('12.5', app.amp.settings_frame.current_var_str.get())
        self.assertIn('12.5', app.asv.cv_settings_frame.current_var_str.get())

    def test_save_dialog_end_to_end(self):
        """完整路径：打开 CV 设置窗口改参数再保存，窗口正常关闭、参数生效。"""
        app = self.app
        dlg = CVSettingChanges(app.cv.cv_settings_frame, app, app.cv.graph,
                               app.cv.device, mode='CV')
        app.update_idletasks()
        dlg.start_volt.set(-500)
        dlg.save_cv_changes(dlg.current_options.get(), app, app.cv.graph,
                            app.cv.cv_settings_frame)
        self.assertFalse(dlg.winfo_exists())  # 保存后窗口必须关闭
        self.assertEqual(app.device_params.cv_settings.start_voltage, -500)
        app.device_params.cv_settings.start_voltage = -900  # 还原


# ===== 回归：每个页面的「运行」按钮必须真正进入对应实验流程 =====
class TestRunButtonWiring(unittest.TestCase):
    """点击真实按钮并断言效果，守护"按钮 → 正确流程"这条线。

    回归历史 bug（2026-10-12 真机排查）：ASV 页按钮继承了
    CVFrame._run_scan → device.run_scan(canvas, button)，而
    ASVFrame.USBHandler.run_scan(delay) 签名不同 → TypeError 被
    Tk 回调吞掉 → 表现为"点运行没反应"。既有测试全部直接调
    handler.asv_run(...)（绕过按钮），语言残留测试只查按钮文字
    不查 command，所以全绿。注意：.invoke() 同样会吞回调异常，
    断言必须落在"流程确实被触发"（mock 被调 / 指令发出）上。
    """

    @classmethod
    def setUpClass(cls):
        with mock.patch('main.AmpUsb') as mock_usb:
            mock_usb.return_value.connected = False
            import main as main_mod
            cls.app = main_mod.ElectroChemGUI()
            cls.app.update_idletasks()

    @classmethod
    def tearDownClass(cls):
        _safe_destroy(cls.app)

    def test_asv_frame_overrides_run_scan(self):
        # ASV 处理器的 run_scan(delay) 与基类 run_scan(canvas, button)
        # 签名不同；ASVFrame 必须覆盖 _run_scan 走 asv_run，
        # 否则按钮会落到基类路径并 TypeError
        self.assertIsNot(ASVFrame._run_scan, CVFrame._run_scan)

    def test_asv_run_button_invokes_asv_flow(self):
        app = self.app
        app.asv.device.asv_run = mock.Mock()
        app.asv.run_button.invoke()
        app.asv.device.asv_run.assert_called_once_with(
            app.asv.graph, app.asv.run_button)

    def test_amp_run_button_invokes_amp_run(self):
        app = self.app
        app.amp.device.amp_run = mock.Mock()
        app.amp.run_button.invoke()
        app.amp.device.amp_run.assert_called_once_with(
            app.amp.graph, app.amp)

    def _cv_family_button_starts_scan(self, tab):
        # CV/LS/SWV/DPV：点击后必须发出扫描启动命令 'R'
        # （设备未连接时走恢复分支，但启动指令已发出）
        dev = tab.device.device  # 被 mock 的 comm 对象
        dev.reset_mock()
        tab.run_button.invoke()
        writes = [c.args[0] for c in dev.usb_write.call_args_list
                  if c.args and isinstance(c.args[0], str)]
        self.assertIn('R', writes, f"{tab.mode} 页点击运行未发出 'R'")

    def test_cv_run_button_starts_scan(self):
        self._cv_family_button_starts_scan(self.app.cv)

    def test_ls_run_button_starts_scan(self):
        self._cv_family_button_starts_scan(self.app.ls)

    def test_swv_run_button_starts_scan(self):
        self._cv_family_button_starts_scan(self.app.swv)

    def test_dpv_run_button_starts_scan(self):
        self._cv_family_button_starts_scan(self.app.dpv)

    def test_cv_scan_stop_label_is_generic(self):
        # 回归：CV 家族扫描中的按钮曾误用 asv_stop 文案，显示"停止 ASV"
        from i18n import get_text
        tab = self.app.cv
        tab.device._enter_scan_state()
        try:
            self.assertEqual(tab.run_button.cget('text'), get_text("stop"))
        finally:
            tab.device._exit_scan_state()




# ===== SWV 独立页端到端（假设备按固件分段结构生成数据） =====
class TestSwvStandaloneEndToEnd(unittest.TestCase):
    """SWV 独立页全流程：固件分段结构下 X 轴与数据等长、只画扫描段。"""

    def _run_page(self, start_type, start_mv, end_mv, inc_mv=16):
        from fake_device import FakeSerialComm, make_fake_ampusb
        master = mock.Mock()
        master.device_params = DeviceParameters()
        master.data_save_type = "Converted"
        master.after.side_effect = lambda _delay, cb: cb()
        s = master.device_params.cv_settings
        s.sweep_start_type = start_type
        s.start_voltage = start_mv
        s.end_voltage = end_mv
        s.swv_inc = inc_mv
        s.swv_height = 50
        s.swv_period = 20
        s.sweep_type, s.use_swv = 'LS', True
        fake = FakeSerialComm()
        usb = make_fake_ampusb(master, fake)
        usb.last_experiment = 'CV'  # 强制重发 SWV 参数
        handler = CVFrame.USBHandler(mock.Mock(), usb, master, None, mode='SWV')
        handler.settings = s
        handler.run_scan(handler.graph, mock.Mock())
        handler.graph.update_data.assert_called_once()
        x, y, _raw = handler.graph.update_data.call_args.args
        return x, y, fake, handler

    def test_swv_page_start_type_draws_scan_segment(self):
        """Start 起始（默认）：固件扫 start→end→start，页面只画扫描段。"""
        x, y, fake, handler = self._run_page('Start', 0, 320)
        n_expected = 320 // 16 + 1
        self.assertEqual(len(x), n_expected)
        self.assertEqual(len(x), len(y))
        self.assertEqual(x[0], 0)
        self.assertEqual(x[-1], 320)
        # G 命令确实发出，后缀 LS
        g_cmds = [c for c in fake.received if c.startswith('G|')]
        self.assertEqual(len(g_cmds), 1)
        self.assertTrue(g_cmds[0].endswith('|LS'))
        # 峰在扫描段内：数据应含明显峰形（最大值远大于基线）
        self.assertGreater(max(y) - min(y), 1.0)

    def test_swv_page_zero_start_still_single_segment(self):
        """Zero 起始：固件对 'L' 型仍只扫单段（分段只对 'C' 型生效），
        页面画出完整 start→end 扫描段。"""
        x, y, fake, handler = self._run_page('Zero', 160, 480)
        n_expected = (480 - 160) // 16 + 1
        self.assertEqual(len(x), n_expected)
        self.assertEqual(len(x), len(y))
        self.assertEqual(x[0], 160)
        self.assertEqual(x[-1], 480)
        g_cmds = [c for c in fake.received if c.startswith('G|')]
        self.assertTrue(g_cmds[0].endswith('|LZ'))

    def test_swv_page_estimate_matches_firmware_structure(self):
        """时间估算 = 分段总步数 × 每步周期，不得重复计入 delay_time。"""
        master = mock.Mock()
        master.device_params = DeviceParameters()
        s = master.device_params.cv_settings
        s.sweep_start_type = 'Start'
        s.start_voltage = 0
        s.end_voltage = 320
        s.swv_inc = 16
        s.swv_period = 20
        s.sweep_type, s.use_swv = 'LS', True
        handler = CVFrame.USBHandler.__new__(CVFrame.USBHandler)
        handler.settings = s
        est = handler._estimate_scan_seconds()
        # 固件 'L' 型单段：320/16=20 步 × 20ms + 1s 余量（估算值，允许近似）
        self.assertAlmostEqual(est, 20 * 20 / 1000.0 + 1.0, places=3)


# ===== 统一弹窗规则：CV/LS 页端到端 + 扫描中停止 =====
class TestUnifiedScanWindow(unittest.TestCase):
    """所有页面同一套弹窗规则：合一窗实时倒数、扫描中可停止、出错弹窗。"""

    def _make_page(self, mode, start_mv=0, end_mv=320):
        from fake_device import FakeSerialComm, make_fake_ampusb
        root = tk.Tk(); root.withdraw()
        root.device_params = DeviceParameters()
        root.data_save_type = "Converted"
        s = root.device_params.cv_settings
        s.sweep_start_type = 'Start'
        s.start_voltage = start_mv
        s.end_voltage = end_mv
        s.sweep_rate = 0.5
        s.swv_inc, s.swv_height, s.swv_period = 16, 50, 20
        s.sweep_type, s.use_swv = CVFrame.MODE_SETTINGS[mode]
        s.quiet_time_s = 0.0  # 本族测试验证绘图链路本身，跳过预处理静置
        fake = FakeSerialComm()
        usb = make_fake_ampusb(root, fake)
        usb.last_experiment = 'force-resend'
        graph = mock.Mock()
        handler = CVFrame.USBHandler(graph, usb, root, None, mode=mode)
        handler.settings = s
        button = tb.Button(root, text="run")
        return root, handler, fake, graph, button

    def _pump(self, root, ms=50):
        end = time.time() + ms / 1000.0
        while time.time() < end:
            root.update()

    def test_cv_end_to_end_draws_and_restores(self):
        """CV 页全链：合一窗出现 -> 数据画图 -> 窗口关闭 -> 按钮恢复原命令。"""
        root, handler, fake, graph, button = self._make_page('CV')
        orig_cmd = button.cget('command')
        handler.run_scan(graph, button, _delay=1)
        self.assertTrue(handler.is_running)
        # 扫描中按钮显示通用「停止」（曾误用 asv_stop 显示"停止 ASV"）
        self.assertEqual(str(button.cget('text')), get_text("stop"))
        self.assertIsNotNone(handler.timer_window)  # 合一窗已弹出
        self._pump(root, 300)  # 让轮询回调跑完
        graph.update_data.assert_called_once()
        x, y, _raw = graph.update_data.call_args.args
        self.assertEqual(len(x), len(y))
        self.assertGreater(len(x), 10)
        self.assertFalse(handler.is_running)
        self.assertIsNone(handler.timer_window)     # 扫完窗口关闭
        self.assertEqual(button.cget('command'), orig_cmd)  # 按钮命令恢复
        root.destroy()

    def test_stop_during_scan_resets_everything(self):
        """扫描中点停止：发 X 复位、取消轮询、关窗、按钮恢复；迟到回调不再画图。"""
        root, handler, fake, graph, button = self._make_page('CV')
        handler.run_scan(graph, button, _delay=60000)  # 长时间不触发，模拟扫描中
        self.assertTrue(handler.is_running)
        handler.stop()
        self.assertIn('X', fake.received)           # 设备收到复位
        self.assertFalse(handler.is_running)
        self.assertIsNone(handler.timer_window)     # 窗口关闭
        self.assertIsNone(handler._poll_after_id)   # 轮询已取消
        handler.run_scan_continue(graph)             # 迟到的回调必须直接返回
        graph.update_data.assert_not_called()
        root.destroy()

    def test_ls_end_to_end_draws(self):
        """LS 页同样走统一链路。"""
        root, handler, fake, graph, button = self._make_page('LS')
        handler.run_scan(graph, button, _delay=1)
        self._pump(root, 300)
        graph.update_data.assert_called_once()
        self.assertFalse(handler.is_running)
        root.destroy()

    def test_scan_window_is_timer_toplevel_in_scan_stage(self):
        """CV 页弹出的就是合一窗，且直接进入扫描倒计时状态。"""
        root, handler, fake, graph, button = self._make_page('CV')
        handler.run_scan(graph, button, _delay=60000)
        from dialogs import TimerToplevel
        self.assertIsInstance(handler.timer_window, TimerToplevel)
        self.assertEqual(handler.timer_window.stage, 'scan')
        handler.stop()
        root.destroy()


# ===== 菜单选中标记（选项 + 开发者） =====
class TestMenuIndicators(unittest.TestCase):
    """多选菜单项带选中标记，初值与真实状态一致，点击联动。"""

    def _build(self):
        import main as main_mod
        root = tk.Tk(); root.withdraw()
        class FakeGraph: user_sets_labels_after_run = True
        class FakeCV:
            graph = FakeGraph()
            def save_all_data(self): pass
        class FakeDevice:
            samples_to_smooth = 2
            def set_electrode_config(self, n): root.num_electrodes = n
            def select_voltage_source(self, v):
                root.voltage_source_key = "vsource_vdac" if v == "8-bit DAC" else "vsource_dvdac"
            def get_look_up_table(self): pass
            def get_export_channel(self, c): root.exported = c
        root.cv = FakeCV(); root.device = FakeDevice()
        root.device_params = DeviceParameters()
        root.num_electrodes = 3
        root.voltage_source_key = "vsource_vdac"
        root.data_save_type = "Raw Counts"
        root.open_data = lambda: None
        root.save_current_page_data = lambda: None
        root.delete_all_data_user_prompt = lambda: None
        root.set_adc_channel = lambda i: setattr(
            root.device_params.adc_tia, 'adc_channel', i)
        root.set_data_type = lambda t: setattr(root, 'data_save_type', t)
        main_mod.OptionMenu(root)
        menubar = root.nametowidget(root.cget("menu"))
        return root, menubar

    def _find(self, menu, label_part):
        for i in range(menu.index("end") + 1):
            try:
                if label_part in menu.entrycget(i, "label"):
                    return i
            except tk.TclError:
                continue
        return None

    def _selected(self, menu):
        out = []
        for i in range(menu.index("end") + 1):
            if menu.type(i) == "radiobutton":
                var = menu.tk.call(menu._w, "entrycget", i, "-variable")
                val = menu.tk.call(menu._w, "entrycget", i, "-value")
                if str(menu.tk.globalgetvar(var)) == str(val):
                    out.append(menu.entrycget(i, "label"))
        return out

    def _cascade(self, menu, label_part):
        idx = self._find(menu, label_part)
        self.assertIsNotNone(idx, "未找到菜单项: " + label_part)
        return menu.nametowidget(menu.entrycget(idx, "menu"))

    def test_developer_menus_marked(self):
        """开发者菜单：导出通道与数据导出两组带当前状态标记。"""
        root, menubar = self._build()
        dev = self._cascade(menubar, get_text("menu_developers"))
        chan = self._cascade(dev, get_text("dev_choose_channel"))
        self.assertEqual(self._selected(chan), ["0"])  # 默认通道 0
        dtype = self._cascade(dev, get_text("dev_choose_data"))
        self.assertEqual(self._selected(dtype), [get_text("dev_raw_data")])
        # 点击联动：选通道 2，标记跟随且状态更新
        chan.invoke(2)
        self.assertEqual(root.device_params.adc_tia.adc_channel, 2)
        self.assertEqual(self._selected(chan), ["2"])
        root.destroy()

    def test_option_menus_marked(self):
        """选项菜单四组标记与状态一致。"""
        root, menubar = self._build()
        opts = self._cascade(menubar, get_text("menu_options"))
        ele = self._cascade(opts, get_text("opt_electrode_config"))
        self.assertEqual(self._selected(ele), [get_text("opt_3electrode")])
        vs = self._cascade(opts, get_text("opt_voltage_source"))
        self.assertEqual(self._selected(vs), [get_text("opt_vdac")])
        sm = self._cascade(opts, get_text("opt_smooth_data"))
        self.assertEqual(self._selected(sm), [get_text("opt_values").format(2)])
        root.destroy()


import ttkbootstrap as tb
from i18n import get_text


class TestDeadlinePolling(unittest.TestCase):
    """设备慢时轮询按截止时间坚持，不再固定重试 2 次就放弃（SWV 无曲线的根因修复）。"""

    class _SlowDevice:
        """像真机但永远不回 Done：轮询只能等到截止时间。"""
        def __init__(self):
            self.read_calls = 0
            self.last_experiment = 'CV'
            self.connected = True
        def set_last_run(self, mode):
            self.last_experiment = mode
        def usb_write(self, msg, *a, **k):
            return True
        def usb_read_data(self, *a, **k):
            self.read_calls += 1
            return None

    def _make_handler(self, estimate_s=0.0):
        root = tk.Tk(); root.withdraw()
        root.device_params = DeviceParameters()
        root.data_save_type = "Converted"
        s = root.device_params.cv_settings
        s.start_voltage, s.end_voltage, s.sweep_rate = 0, 320, 0.5
        s.quiet_time_s = 0.0  # 本测试只验证轮询重试，跳过预处理静置
        device = self._SlowDevice()
        handler = CVFrame.USBHandler(mock.Mock(), device, root, None, mode='CV')
        handler.settings = s
        handler._estimate_scan_seconds = lambda: estimate_s
        handler._show_progress = lambda *a, **k: None
        handler._close_progress = lambda: None
        button = tb.Button(root, text="run")
        return root, handler, device, button

    def _pump(self, root, seconds):
        end = time.time() + seconds
        while time.time() < end:
            root.update()

    def test_polling_retries_until_deadline(self):
        """截止前持续重试：重试次数远超旧的 2 次上限。"""
        root, handler, device, button = self._make_handler()
        try:
            handler.run_scan(mock.Mock(), button, _delay=1)
            self._pump(root, 2.5)
            self.assertGreater(device.read_calls, 2, '重试次数应超过旧的 2 次上限')
            handler.stop()
        finally:
            root.destroy()

    def test_poll_stops_after_deadline(self):
        """超过截止时间后停止轮询、恢复按钮，不再死等。"""
        root, handler, device, button = self._make_handler(estimate_s=-100)
        try:
            handler.run_scan(mock.Mock(), button, _delay=1)
            self._pump(root, 0.8)
            count = device.read_calls
            self._pump(root, 1.5)
            self.assertEqual(device.read_calls, count, '超时后不应继续轮询')
            self.assertFalse(handler.is_running)
            self.assertIsNone(handler._poll_after_id)
        finally:
            root.destroy()


class TestLegendLanguageRefresh(unittest.TestCase):
    def test_default_labels_follow_language(self):
        from plotting import PyplotData
        import i18n
        d = PyplotData()
        d.add_data([0, 1], [1, 2])
        i18n.set_language('en')
        d.refresh_default_labels()
        self.assertEqual(d.label, ['data 1'])
        i18n.set_language('zh')
        d.refresh_default_labels()
        self.assertEqual(d.label, ['数据 1'])
        i18n.set_language('en')

    def test_user_named_labels_not_overwritten(self):
        from plotting import PyplotData
        import i18n
        d = PyplotData()
        d.add_data([0, 1], [1, 2])
        d.add_data([0, 1], [3, 4])
        d.change_label('MyRun', 1)
        d.refresh_default_labels()
        self.assertEqual(d.label[1], 'MyRun')

    def test_label_removed_with_data(self):
        from plotting import PyplotData
        d = PyplotData()
        d.add_data([0, 1], [1, 2])
        d.add_data([0, 1], [3, 4])
        d.colors.extend(['#111', '#222'])  # 模拟绘图后写入的颜色记录
        d.remove_data(0)
        self.assertEqual(len(d.label), 1)


class TestDialogCenteredOnMaster(unittest.TestCase):
    def test_timer_centers_on_master(self):
        from dialogs import TimerToplevel
        root = tk.Tk()
        try:
            root.geometry('400x300+50+60')
            root.update()
            timer = TimerToplevel(root, 0, 0, 0)
            timer.update()
            mx = root.winfo_rootx() + root.winfo_width() // 2
            my = root.winfo_rooty() + root.winfo_height() // 2
            tx = timer.winfo_rootx() + timer.winfo_width() // 2
            ty = timer.winfo_rooty() + timer.winfo_height() // 2
            # 容差 100px：居中代码用 winfo_reqwidth（请求尺寸），这里用
            # winfo_width（实际尺寸），Windows 下 DPI 缩放（125%/150%）+
            # 标题栏边框会让两者差出 30px 以上（真机踩坑：Win 下必挂）
            self.assertLess(abs(tx - mx), 100, '弹窗水平中心应贴近主窗口中心')
            self.assertLess(abs(ty - my), 100, '弹窗垂直中心应贴近主窗口中心')
            timer.destroy()
        finally:
            root.destroy()

    def test_timer_fallback_when_master_unmapped(self):
        from dialogs import TimerToplevel
        root = tk.Tk()
        try:
            root.withdraw()
            timer = TimerToplevel(root, 0, 0, 0)  # 不应抛异常
            timer.destroy()
        finally:
            root.destroy()


class TestASVDeadlinePolling(unittest.TestCase):
    """ASV 页与 CV 页同一截止时间轮询规则（修复 LS 跑完没线）。"""

    class _SlowDevice:
        def __init__(self):
            self.read_calls = 0
            self.connected = True
            self.last_experiment = 'ASV'
        def usb_write(self, *a, **k):
            return True
        def usb_read_message(self, *a, **k):
            self.read_calls += 1
            return None
        def reset(self):
            pass

    def _make_handler(self, estimate_s=0.0):
        root = tk.Tk(); root.withdraw()
        root.device_params = DeviceParameters()
        root.data_save_type = "Converted"
        device = self._SlowDevice()
        handler = ASVFrame.USBHandler(mock.Mock(), device, root, None)
        handler.run_button = tb.Button(root, text="run")
        handler.is_running = True
        handler._estimate_scan_seconds = lambda: estimate_s
        handler._show_progress = lambda *a, **k: None
        handler._close_progress = lambda: None
        return root, handler, device

    def _pump(self, root, seconds):
        end = time.time() + seconds
        while time.time() < end:
            root.update()

    def test_asv_polls_until_deadline(self):
        root, handler, device = self._make_handler()
        try:
            handler.run_scan(1)
            self._pump(root, 2.5)
            self.assertGreater(device.read_calls, 2, 'ASV 轮询不应只有旧的 2 次重试')
            handler.stop()
        finally:
            root.destroy()

    def test_asv_timeout_shows_error_and_recovers(self):
        root, handler, device = self._make_handler(estimate_s=-100)
        try:
            with mock.patch('experiments.messagebox.showerror') as err:
                handler.run_scan(1)
                self._pump(root, 0.8)
            err.assert_called_once()  # 超时要弹错误提示，不再静默
            self.assertFalse(handler.is_running)
            self.assertIsNone(handler._poll_after_id)
            self.assertEqual(str(handler.run_button.cget('text')), get_text('asv_run'))
        finally:
            root.destroy()

    def test_asv_stop_cancels_polling(self):
        root, handler, device = self._make_handler()
        try:
            handler.run_scan(60000)  # 长延迟模拟扫描中
            self.assertIsNotNone(handler._poll_after_id)
            with mock.patch.object(handler.device, 'reset', create=True):
                handler.stop()
            self.assertIsNone(handler._poll_after_id)
            count = device.read_calls
            handler.run_scan_continue()  # 迟到回调直接返回
            self.assertEqual(device.read_calls, count)
        finally:
            root.destroy()


class TestChronoDataTry(unittest.TestCase):
    """计时电流：按真实包数读全一段、读失败不死循环。"""

    def _make_handler(self, chunks):
        root = tk.Tk(); root.withdraw()
        root.device_params = DeviceParameters()
        device = mock.Mock()
        device.get_data.side_effect = chunks
        device.process_data.side_effect = lambda raw, *a, **k: raw  # 直通
        handler = AmpFrame.USBHandler(mock.Mock(), device, root)
        handler.t_ptr = -handler.time_step
        handler.data, handler.time = [], []
        return root, handler

    def test_reads_full_buffer_with_packet_count(self):
        root, handler = self._make_handler([[10, 11, 12]])
        try:
            self.assertTrue(handler.data_try())
            # 必须传包数：默认包数来自从未赋值的 usb_count(=0)，
            # 只读 1 个包会让残余数据污染下一条消息（真机没线的根因）
            handler.device.get_data.assert_called_with(handler.number_packets)
            self.assertEqual(handler.data, [10, 11, 12])  # 首段不丢点
            self.assertEqual(len(handler.time), 3)
        finally:
            root.destroy()

    def test_read_failure_returns_not_hangs(self):
        root, handler = self._make_handler([RuntimeError("usb timeout")])
        try:
            self.assertFalse(handler.data_try())  # 异常时直接返回，不死循环
        finally:
            root.destroy()

    def test_empty_chunk_returns(self):
        root, handler = self._make_handler([[]])
        try:
            self.assertFalse(handler.data_try())  # 空数据立即返回
        finally:
            root.destroy()


class TestChronoEndToEnd(unittest.TestCase):
    """计时电流全链：假设备流式推 Done0 -> F0 -> 数据，曲线应实时增长。"""

    def test_streaming_data_plots(self):
        from fake_device import FakeSerialComm, make_fake_ampusb
        root = tk.Tk(); root.withdraw()
        root.device_params = DeviceParameters()
        root.data_save_type = "Converted"
        fake = FakeSerialComm()
        usb = make_fake_ampusb(root, fake)
        graph = mock.Mock()
        handler = AmpFrame.USBHandler(graph, usb, root)
        handler.amp_run(graph, None)
        try:
            end = time.time() + 2.0
            while time.time() < end and not graph.update_amp_data.called:
                root.update()
            graph.update_amp_data.assert_called()
            pts = root.device_params.amp_settings.sampling_rate >= 1000 and 200 or 50
            self.assertGreaterEqual(len(handler.data), 1)
            self.assertEqual(len(handler.data), len(handler.time))
            # 首段点数 = 采样率/5（>=1000Hz 分支），一个点都不许丢
            expected = int(root.device_params.amp_settings.sampling_rate / 5.0) \
                if root.device_params.amp_settings.sampling_rate >= 1000 else \
                int(root.device_params.amp_settings.sampling_rate / 2.0)
            self.assertEqual(len(handler.data), expected)
        finally:
            handler.cancel_run()
            root.destroy()

    def test_garbage_message_ignored(self):
        """残留/乱码消息不能被当 Done0 解析（通道字符必须是数字）。"""
        root = tk.Tk(); root.withdraw()
        root.device_params = DeviceParameters()
        device = mock.Mock()
        device.usb_read_message.return_value = '\x00\xd0\x04d\x00'  # 错位二进制
        handler = AmpFrame.USBHandler(mock.Mock(), device, root)
        handler.running = True
        handler.master = root
        handler.data_try = mock.Mock()
        handler.running_read()
        handler.data_try.assert_not_called()
        handler.running = False
        root.destroy()


class TestLegendNoTitle(unittest.TestCase):
    def test_legend_has_no_title(self):
        pe = PyplotEmbed.__new__(PyplotEmbed)
        pe.data = mock.Mock()
        pe.data.label = ['data 1']
        pe.graph_area = mock.Mock()
        pe.graph_area.axis.get_legend_handles_labels.return_value = (['h'], ['data 1'])
        pe.graph_area.axis.get_legend.return_value = None
        pe.update_legend()
        kwargs = pe.graph_area.axis.legend.call_args.kwargs
        self.assertNotIn('title', kwargs, '图例不应再带“数据系列”标题')


class TestLegacyPyusbEraBugs(unittest.TestCase):
    """同类问题回归守护：不能再有对 SerialComm 下标/endpoint 的访问。"""

    def test_amp_run_does_not_subscript_device(self):
        """amp_run 不得访问 device.device[...]（kit-059 是 SerialComm，不可下标）。"""
        from fake_device import FakeSerialComm, make_fake_ampusb
        root = tk.Tk(); root.withdraw()
        root.device_params = DeviceParameters()
        root.data_save_type = "Converted"
        usb = make_fake_ampusb(root, FakeSerialComm())
        graph = mock.Mock()
        handler = AmpFrame.USBHandler(graph, usb, root)
        # SerialComm/FakeSerialComm 均不可下标：若 amp_run 里再出现
        # device.device[...] 访问，这里会直接抛 TypeError
        handler.amp_run(graph, None)
        handler.cancel_run()
        root.destroy()

    def test_restart_dialog_survives_without_destroy(self):
        """重启对话框在设备对象没有 destroy/close 时也必须能重启流程。"""
        import inspect
        import dialogs
        src = inspect.getsource(dialogs)
        self.assertNotIn('master.device.destroy()\n        os.execl', src,
                         '不能再不加保护地直接调用 master.device.destroy()')

    def test_amp_smoothing_uses_correct_attribute(self):
        """计时电流的平滑必须写 samples_to_smooth（不是拼错的 samples_smooth）。"""
        import inspect
        import experiments
        src = inspect.getsource(experiments)
        self.assertNotIn('device.samples_smooth =', src)


class TestAmpChannelFilterAndNonblocking(unittest.TestCase):
    """计时电流只画选定通道；消息读取非阻塞（全页面通用）。"""

    def _make_handler(self, message):
        root = tk.Tk(); root.withdraw()
        root.device_params = DeviceParameters()
        device = mock.Mock()
        device.usb_read_message.return_value = message
        handler = AmpFrame.USBHandler(mock.Mock(), device, root)
        handler.running = True
        handler.master = root
        handler.data_try = mock.Mock(return_value=True)
        return root, handler, device

    def test_selected_channel_accepted(self):
        ch = None
        root, handler, device = self._make_handler(None)
        try:
            ch = str(root.device_params.adc_tia.adc_channel)
            device.usb_read_message.return_value = 'Done' + ch + '\x00'
            handler.running_read()
            handler.data_try.assert_called_once()
            device.usb_write.assert_called_with('F' + ch)
        finally:
            handler.running = False
            root.destroy()

    def test_other_channels_ignored(self):
        root, handler, device = self._make_handler(None)
        try:
            selected = root.device_params.adc_tia.adc_channel
            other = (selected + 1) % 4
            device.usb_read_message.return_value = 'Done%d\x00' % other
            handler.running_read()
            handler.data_try.assert_not_called()   # 非选定通道不取数
            # 也不应发送 F 命令
            self.assertFalse(any(str(c.args[0]).startswith('F')
                                 for c in device.usb_write.call_args_list))
        finally:
            handler.running = False
            root.destroy()

    def test_time_axis_has_real_gaps(self):
        """相邻两段的时间间隔 = 4×段长（固件轮采 4 通道），不压缩。"""
        root = tk.Tk(); root.withdraw()
        root.device_params = DeviceParameters()
        device = mock.Mock()
        device.get_data.side_effect = [[1, 2, 3], [4, 5, 6]]
        device.process_data.side_effect = lambda raw, *a, **k: raw
        handler = AmpFrame.USBHandler(mock.Mock(), device, root)
        handler.data, handler.time = [], []
        try:
            handler.data_try()
            handler.data_try()
            seg1_end = handler.time[2]
            seg2_start = handler.time[3]
            gap = seg2_start - seg1_end
            expected = (4 - 1) * 3 * handler.time_step + handler.time_step
            self.assertAlmostEqual(gap, expected, places=9)
        finally:
            root.destroy()

    def test_message_read_nonblocking(self):
        """缓冲无字节时消息读取立即返回 None，不调用阻塞 read。"""
        from comm import AmpUsb
        usb = AmpUsb.__new__(AmpUsb)
        usb.connected = True
        serial_dev = mock.Mock()
        serial_dev.bytes_waiting.return_value = 0
        usb.device = serial_dev
        self.assertIsNone(usb.usb_read_data(encoding='str'))
        serial_dev.read_data.assert_not_called()  # 没有进入阻塞读
        # 有字节时只读实际存在的数量
        serial_dev.bytes_waiting.return_value = 6
        serial_dev.read_data.return_value = 'Done0\x00'
        self.assertEqual(usb.usb_read_data(encoding='str'), 'Done0\x00')
        serial_dev.read_data.assert_called_with(6, encoding='str')


class TestGetDataTermination(unittest.TestCase):
    """终止码落在块中间时，必须在其位置截断（真机计时电流垃圾尾的根因）。"""

    def _make_usb(self, byte_stream):
        from comm import AmpUsb
        usb = AmpUsb.__new__(AmpUsb)
        usb.connected = True
        usb.samples_to_smooth = 1
        usb.master = mock.Mock()
        usb.device_params = mock.Mock()
        dev = mock.Mock()
        # 模拟串口：按请求的 64 字节切块返回（无包边界）
        buf = bytearray(byte_stream)
        def read_data(n, encoding=None):
            chunk = bytes(buf[:n]); del buf[:n]
            if encoding == 'int16':
                return list(struct.unpack(f"{len(chunk)//2}h", chunk))
            return chunk.decode('utf-8', errors='replace')
        dev.read_data = read_data
        usb.device = dev
        return usb

    def test_termination_mid_chunk_truncates_exactly(self):
        from comm import TERMINATION_CODE
        import struct as st
        payload = list(range(100, 300)) + [TERMINATION_CODE]  # 200 点 + 终止码
        # 终止码后面紧跟下一条 "Done1" 消息的字节（模拟阻塞等待期间到达）
        stream = st.pack(f"{len(payload)}h", *payload) + b'Done1\x00' + b'\x01' * 40
        usb = self._make_usb(stream)
        data = usb.get_data(7)
        self.assertEqual(len(data), 200)          # 200 个有效点，一个不多
        self.assertEqual(data[:3], [100, 101, 102])
        self.assertEqual(data[-1], 299)
        self.assertNotIn(TERMINATION_CODE, data)  # 终止码不得留在数据里

    def test_termination_at_chunk_end_still_works(self):
        from comm import TERMINATION_CODE
        import struct as st
        payload = [5] * 63 + [TERMINATION_CODE]  # 恰好 64 个 int16，终止码在块尾
        stream = st.pack(f"{len(payload)}h", *payload)
        usb = self._make_usb(stream)
        data = usb.get_data(2)
        self.assertEqual(data, [5] * 63)

class TestPortBusyResilience(unittest.TestCase):
    """端口被占用时不得崩溃：跳过该端口，全部失败则以未连接状态启动。"""

    def _fake_port(self, name='COM8'):
        port = mock.Mock()
        port.vid = comm_module.USB_VENDOR_ID
        port.pid = comm_module.USB_PRODUCT_ID
        port.device = name
        return port

    def test_busy_port_skipped_not_crash(self):
        with mock.patch.object(comm_module.serial.tools.list_ports, 'comports',
                               return_value=[self._fake_port()]), \
             mock.patch.object(comm_module.serial, 'Serial',
                               side_effect=comm_module.serial.SerialException(
                                   "could not open port")):
            sc = comm_module.SerialComm()
        self.assertTrue(sc.found)          # 端口是看到了的
        self.assertFalse(sc.connected)     # 但没连上
        self.assertIsNone(sc.device)       # 不崩溃，返回 None

    def test_busy_first_port_falls_through_to_second(self):
        good = mock.Mock()
        good.readline.return_value = b"Naresuan Potentiostat\r\n"
        serial_effects = [comm_module.serial.SerialException("busy"), good]
        with mock.patch.object(comm_module.serial.tools.list_ports, 'comports',
                               return_value=[self._fake_port('COM8'),
                                             self._fake_port('COM9')]), \
             mock.patch.object(comm_module.serial, 'Serial',
                               side_effect=serial_effects):
            sc = comm_module.SerialComm()
        self.assertTrue(sc.connected)
        self.assertIs(sc.device, good)
        good.close.assert_not_called()

    def test_handshake_failure_closes_handle(self):
        bad = mock.Mock()
        bad.readline.return_value = b""  # 不应答
        with mock.patch.object(comm_module.serial.tools.list_ports, 'comports',
                               return_value=[self._fake_port()]), \
             mock.patch.object(comm_module.serial, 'Serial', return_value=bad):
            sc = comm_module.SerialComm()
        self.assertFalse(sc.connected)
        bad.close.assert_called_once()  # 握手失败必须释放句柄，否则自己占着端口


# ===== Test Stirring Pin Reset =====
class TestStirringPinReset(unittest.TestCase):
    """P2.0 残留高电平修复：连接成功归零 + 关窗归零（附录 D.5）。

    ZK-TD2 用 P13/P04 都需要干净的低→高变化才触发；上次会话异常退出
    会把 P2.0 卡在高电平，导致新会话的 O|1 没有上升沿、继电器不动作。
    """

    def test_connect_sends_stirring_off(self):
        """连接成功（AmpUsb 初始化）时发一次 O|0 归零 P2.0。"""
        from fake_device import FakeSerialComm
        fake = FakeSerialComm()
        with mock.patch.object(comm_module, 'SerialComm', return_value=fake), \
             mock.patch.object(comm_module.AmpUsb, 'find_voltage_source'), \
             mock.patch.object(comm_module.AmpUsb, 'calibrate'):
            usb = comm_module.AmpUsb(mock.Mock(), DeviceParameters())
        self.assertTrue(usb.connected)
        self.assertIn('off', fake.stirring_log)

    def test_destroyer_sends_stirring_off(self):
        """关窗（destroyer）时尽力发一次 O|0。"""
        import main as main_module
        fake_device_obj = mock.Mock()
        fake_device_obj.connected = True
        fake_app = mock.Mock()
        fake_app.device = fake_device_obj
        had_app = hasattr(main_module, 'app')
        old_app = getattr(main_module, 'app', None)
        main_module.app = fake_app
        try:
            with mock.patch.object(main_module.sys, 'exit', side_effect=SystemExit):
                self.assertRaises(SystemExit, main_module.destroyer)
        finally:
            if had_app:
                main_module.app = old_app
            else:
                del main_module.app
        fake_device_obj.set_stirring.assert_called_once_with(False)
        fake_app.destroy.assert_called_once()

    def test_destroyer_survives_disconnected_device(self):
        """设备未连接/已断开时关窗不抛异常。"""
        import main as main_module
        fake_device_obj = mock.Mock()
        fake_device_obj.connected = False
        fake_app = mock.Mock()
        fake_app.device = fake_device_obj
        had_app = hasattr(main_module, 'app')
        old_app = getattr(main_module, 'app', None)
        main_module.app = fake_app
        try:
            with mock.patch.object(main_module.sys, 'exit', side_effect=SystemExit):
                self.assertRaises(SystemExit, main_module.destroyer)
        finally:
            if had_app:
                main_module.app = old_app
            else:
                del main_module.app
        fake_device_obj.set_stirring.assert_not_called()
        fake_app.destroy.assert_called_once()


# ===== Test LUT 弹窗与保存当前页 =====
class TestShowLookupTable(unittest.TestCase):
    """导出 LUT 改为弹窗显示（原来是 print 到控制台，pythonw 下不可见）。"""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        _safe_destroy(self.root)

    def test_popup_shows_lut_values(self):
        from dialogs import ShowLookupTable
        self.root.device = mock.Mock()
        self.root.device.connected = True
        self.root.device.get_look_up_table.return_value = list(range(16))
        dlg = ShowLookupTable(self.root)
        try:
            content = dlg.children['!text'].get('1.0', 'end')
            self.assertIn('0', content)
            self.assertIn('15', content)
            self.assertIn('8:', content)  # 第二行起始索引
        finally:
            dlg.destroy()

    def test_popup_handles_no_data(self):
        from dialogs import ShowLookupTable
        self.root.device = mock.Mock()
        self.root.device.connected = False  # 未连接：不炸，显示提示
        dlg = ShowLookupTable(self.root)
        try:
            content = dlg.children['!text'].get('1.0', 'end')
            self.assertNotEqual(content.strip(), '')
        finally:
            dlg.destroy()


class TestSaveCurrentPageData(unittest.TestCase):
    """保存菜单路由到当前标签页：CV/LS/SWV/ASV 走 save_all_data，CA 走 amp.save_data。"""

    def _make_gui(self, current_frame):
        from main import ElectroChemGUI
        gui = mock.Mock()
        gui.notebook.select.return_value = 'frame_path'
        gui.nametowidget.return_value = current_frame
        gui.amp = mock.Mock()
        return gui

    def test_saves_scan_frame(self):
        from main import ElectroChemGUI
        frame = mock.Mock()
        gui = self._make_gui(frame)
        ElectroChemGUI.save_current_page_data(gui)
        frame.save_all_data.assert_called_once()
        gui.amp.save_data.assert_not_called()

    def test_saves_amp_frame(self):
        from main import ElectroChemGUI
        gui = self._make_gui(None)
        gui.nametowidget.return_value = gui.amp
        ElectroChemGUI.save_current_page_data(gui)
        # CA 数据在页面的 USBHandler（amp.device）上，不是 master.device
        gui.amp.save_data.assert_called_once_with(gui.amp.device)

    def test_no_tab_selected_is_noop(self):
        from main import ElectroChemGUI
        gui = self._make_gui(None)
        gui.notebook.select.return_value = ''
        ElectroChemGUI.save_current_page_data(gui)
        gui.nametowidget.assert_not_called()


# ===== Test DPV 独立页 =====
class TestDPVStandaloneEndToEnd(unittest.TestCase):
    """DPV 独立页全流程：发 'P' 命令，数据按 SWV 同款差分规则处理。"""

    def _run_page(self, start_type, start_mv, end_mv, inc_mv=16):
        from fake_device import FakeSerialComm, make_fake_ampusb
        master = mock.Mock()
        master.device_params = DeviceParameters()
        master.data_save_type = "Converted"
        master.after.side_effect = lambda _delay, cb: cb()
        s = master.device_params.cv_settings
        s.sweep_start_type = start_type
        s.start_voltage = start_mv
        s.end_voltage = end_mv
        s.swv_inc = inc_mv
        s.swv_height = 50
        s.swv_period = 20
        s.sweep_type, s.use_swv = 'LS', True
        fake = FakeSerialComm()
        usb = make_fake_ampusb(master, fake)
        usb.last_experiment = 'CV'  # 强制重发参数
        handler = CVFrame.USBHandler(mock.Mock(), usb, master, None, mode='DPV')
        handler.settings = s
        handler.run_scan(handler.graph, mock.Mock())
        handler.graph.update_data.assert_called_once()
        x, y, _raw = handler.graph.update_data.call_args.args
        return x, y, fake, handler

    def test_dpv_page_sends_p_command(self):
        """DPV 页发 'P' 命令（不是 'G'），字段布局与 'G' 一致，后缀 LS。"""
        x, y, fake, handler = self._run_page('Start', 0, 320)
        p_cmds = [c for c in fake.received if c.startswith('P|')]
        g_cmds = [c for c in fake.received if c.startswith('G|')]
        self.assertEqual(len(p_cmds), 1)
        self.assertEqual(len(g_cmds), 0)
        self.assertTrue(p_cmds[0].endswith('|LS'))
        # 6 个字段：P|start|end|步进|幅值|周期|类型
        self.assertEqual(len(p_cmds[0].split('|')), 7)

    def test_dpv_page_data_shape(self):
        """差分后每步 1 点，x 轴是阶梯基底电位，x 与 y 等长。"""
        x, y, fake, handler = self._run_page('Start', 0, 320)
        n_expected = 320 // 16 + 1
        self.assertEqual(len(x), n_expected)
        self.assertEqual(len(x), len(y))
        self.assertEqual(x[0], 0)
        self.assertEqual(x[-1], 320)

    def test_dpv_page_in_mode_settings(self):
        """MODE_SETTINGS / TITLE_KEYS / 运行按钮文案都有 DPV 项。"""
        from experiments import CVFrame
        self.assertEqual(CVFrame.MODE_SETTINGS['DPV'], ('LS', True))
        self.assertIn('DPV', CVFrame.CVSettingDisplay.TITLE_KEYS)
        from i18n import get_text
        for key in ('tab_dpv', 'dpv_title', 'dpv_run', 'dpv_settings_title'):
            self.assertTrue(get_text(key))  # 缺 key 会返回空或抛错


# ===== Test 删除当前页数据 =====
class TestDeleteCurrentPageData(unittest.TestCase):
    """删除菜单改为按当前标签页删除（与保存对称）；CA 页支持清空。"""

    def test_routes_to_current_frame(self):
        from main import ElectroChemGUI
        gui = mock.Mock()
        frame = mock.Mock()
        gui.notebook.select.return_value = 'path'
        gui.nametowidget.return_value = frame
        ElectroChemGUI.delete_current_page_data(gui)
        frame.delete_all_data.assert_called_once()

    def test_no_tab_selected_is_noop(self):
        from main import ElectroChemGUI
        gui = mock.Mock()
        gui.notebook.select.return_value = ''
        ElectroChemGUI.delete_current_page_data(gui)
        gui.nametowidget.assert_not_called()

    def test_amp_delete_clears_data(self):
        """AmpFrame.delete_all_data 清图线 + USBHandler 的 time/data。"""
        amp = AmpFrame.__new__(AmpFrame)
        amp.graph = mock.Mock()
        amp.device = mock.Mock()
        amp.device.time = [0.1, 0.2]
        amp.device.data = [1.0, 2.0]
        AmpFrame.delete_all_data(amp)
        amp.graph.delete_all_lines.assert_called_once()
        self.assertEqual(amp.device.time, [])
        self.assertEqual(amp.device.data, [])

    def test_delete_all_covers_all_pages(self):
        """delete_all_data（兼容保留）覆盖 6 个页面，包括 amp/asv/dpv。"""
        from main import ElectroChemGUI
        gui = mock.Mock()
        frames = [mock.Mock() for _ in range(6)]
        (gui.cv, gui.ls, gui.swv, gui.dpv, gui.amp, gui.asv) = frames
        ElectroChemGUI.delete_all_data(gui)
        for f in frames:
            f.delete_all_data.assert_called_once()

    def test_save_works_after_delete_and_rescan(self):
        """真机 bug 回归：删除当前页→再扫→保存报 No data to save。
        根因是 delete_all_data 换新 PyplotData 断开了 graph 的引用链；
        修复后必须原地清空，删-扫-存全流程通畅。"""
        from plotting import PyplotData
        frame = CVFrame.__new__(CVFrame)
        frame.data = PyplotData()
        frame.graph = mock.Mock()
        frame.graph.data = frame.data          # PyplotEmbed 的引用绑定
        frame.master = mock.Mock()
        frame.master.data_save_type = "Converted"
        # 第一次扫描 + 删除
        frame.graph.data.add_data([0, 1], [0.1, 0.2])
        CVFrame.delete_all_data(frame)
        # 关键断言 1：删完后 graph 和 frame 仍指向同一对象（引用未断）
        self.assertIs(frame.graph.data, frame.data)
        self.assertEqual(frame.data.index, 0)
        # 关键断言 2：再扫一次（数据经 graph 写入），保存不再被判为空
        frame.graph.data.add_data([0, 1], [0.3, 0.4])
        with mock.patch('experiments.open_file', return_value=None) as m_open:
            CVFrame.save_all_data(frame)       # 不应提前 return
            m_open.assert_called_once()        # 到了开文件对话框这一步
        self.assertEqual(frame.data.index, 1)
        self.assertEqual(frame.data.label[0], get_text("data_label").format(1))

    def test_reset_clears_all_fields(self):
        from plotting import PyplotData
        d = PyplotData()
        d.add_data([0], [1.0])
        d.reset()
        self.assertEqual(d.index, 0)
        self.assertEqual(d.name_index, 0)
        self.assertEqual(d.voltage_data, [])
        self.assertEqual(d.current_data, [])
        self.assertEqual(d.label, [])


# ===== Test 打开数据自动路由到对应页 =====
class TestOpenDataRouting(unittest.TestCase):
    """保存的 CSV 重新打开：按 experiment 元数据跳到对应页载入；
    老文件无元数据按表头猜（time→计时电流页，voltage→CV 页）。"""

    def _write_csv(self, content):
        import tempfile
        fd, path = tempfile.mkstemp(suffix='.csv')
        with os.fdopen(fd, 'w', newline='') as f:
            f.write(content)
        self.addCleanup(os.remove, path)
        return path

    def _make_gui(self):
        from main import ElectroChemGUI
        gui = mock.Mock()
        for name in ('cv', 'ls', 'swv', 'dpv', 'asv', 'amp'):
            setattr(gui, name, mock.Mock())
        return gui

    def _open(self, gui, path):
        from main import ElectroChemGUI
        with mock.patch('main.open_file', return_value=path):
            ElectroChemGUI.open_data(gui)

    def test_routes_by_metadata(self):
        cases = {'CV': 'cv', 'LS': 'ls', 'SWV': 'swv', 'DPV': 'dpv', 'ASV': 'asv'}
        for tag, attr in cases.items():
            gui = self._make_gui()
            path = self._write_csv(
                "# experiment: {0}\nvoltage,数据 1\n ,\n0,0.1\n100,0.2\n".format(tag))
            self._open(gui, path)
            frame = getattr(gui, attr)
            frame.open_data.assert_called_once()
            gui.notebook.select.assert_called_once_with(frame)

    def test_routes_amp_by_metadata(self):
        gui = self._make_gui()
        path = self._write_csv(
            "# experiment: AMP\n# sampling_rate_Hz: 1000\ntime,current\n0.001, -0.1\n0.002, -0.1\n")
        self._open(gui, path)
        gui.amp.open_data.assert_called_once()
        gui.notebook.select.assert_called_once_with(gui.amp)

    def test_legacy_files_guess_by_header(self):
        # 老计时电流文件（无元数据）→ amp 页
        gui = self._make_gui()
        path = self._write_csv("time,current\n0.001, -0.1\n")
        self._open(gui, path)
        gui.amp.open_data.assert_called_once()
        # 老伏安文件（无元数据）→ cv 页
        gui = self._make_gui()
        path = self._write_csv("voltage,数据 1\n ,\n0,0.1\n")
        self._open(gui, path)
        gui.cv.open_data.assert_called_once()

    def test_unknown_experiment_falls_back_to_cv(self):
        gui = self._make_gui()
        path = self._write_csv(
            "# experiment: EIS\nvoltage,数据 1\n ,\n0,0.1\n")
        self._open(gui, path)
        gui.cv.open_data.assert_called_once()

    def test_unreadable_file_warns(self):
        gui = self._make_gui()
        path = self._write_csv("garbage\n1,2,3\n")
        with mock.patch('main.open_file', return_value=path), \
             mock.patch('main.messagebox.showwarning') as warn:
            from main import ElectroChemGUI
            ElectroChemGUI.open_data(gui)
        warn.assert_called_once()
        gui.cv.open_data.assert_not_called()


class TestAmpOpenData(unittest.TestCase):
    """计时电流页 open_data：载入 time/current 并画图；保存带元数据头。"""

    def test_open_data_plots_and_stores(self):
        amp = AmpFrame.__new__(AmpFrame)
        amp.graph = mock.Mock()
        amp.graph.l = None
        amp.device = mock.Mock()
        amp.open_data(["time", "current"],
                      [['0.001', ' -0.1'], ['0.002', ' -0.2'], ['0.003', ' -0.15']])
        self.assertEqual(amp.device.time, [0.001, 0.002, 0.003])
        self.assertEqual(len(amp.device.data), 3)
        amp.graph.update_amp_data.assert_called_once()
        t, y, window = amp.graph.update_amp_data.call_args.args
        self.assertEqual(t[0], 0.001)
        self.assertGreaterEqual(window, t[-1] - t[0])  # 窗口覆盖全程

    def test_open_data_replaces_stale_line(self):
        """旧线引用还在时先删再画（否则数据写到已删除的 Line2D 上）。"""
        amp = AmpFrame.__new__(AmpFrame)
        amp.graph = mock.Mock()
        old_line = mock.Mock()
        amp.graph.l = old_line
        amp.device = mock.Mock()
        amp.open_data(["time", "current"], [['0.001', ' -0.1']])
        old_line.remove.assert_called_once()
        self.assertIsNone(amp.graph.l)

    def test_save_writes_metadata(self):
        import tempfile
        path = tempfile.mktemp(suffix='.csv')
        self.addCleanup(os.remove, path)
        device = mock.Mock()
        device.data = [1.0, 2.0]
        device.time = [0.001, 0.002]
        device.settings.sampling_rate = 1000
        device.settings.voltage = 500
        with mock.patch('experiments.open_file', return_value=path):
            AmpFrame.save_data(device)
        from utils import load_csv_file_full
        metadata, header, data_rows = load_csv_file_full(path)
        self.assertEqual(metadata.get('experiment'), 'AMP')
        self.assertEqual(header[0], 'time')
        self.assertEqual(len(data_rows), 2)


# ===== Test 真窗口全页面 保存/删除/打开 回环 =====
class TestFullGuiRoundTrip(unittest.TestCase):
    """用真实 ElectroChemGUI + 假设备做全页面数据操作回环。

    用户真机踩坑（2026-08-26）：CA 页保存/删除失效、打开文件只有 AMP 正常。
    mock 拼装的测试看不出这些问题，必须用真窗口真控件。
    """

    @classmethod
    def setUpClass(cls):
        import main as main_mod
        import comm as comm_module
        from fake_device import FakeSerialComm
        cls.main_mod = main_mod
        cls.fake = FakeSerialComm()
        with mock.patch.object(comm_module, 'SerialComm', return_value=cls.fake), \
             mock.patch.object(comm_module.AmpUsb, 'calibrate'):
            cls.app = main_mod.ElectroChemGUI()
        cls.app.withdraw()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.destroy()
        except tk.TclError:
            pass

    def _save_page(self, frame, path):
        # open_file('saveas') 返回的是文件句柄不是路径
        fh = open(path, 'w', newline='')
        try:
            with mock.patch('experiments.open_file', return_value=fh):
                frame.save_all_data()
        finally:
            fh.close()

    def _temp_path(self):
        import tempfile
        path = tempfile.mktemp(suffix='.csv')
        self.addCleanup(os.remove, path)
        return path

    def test_scan_pages_save_delete_open_roundtrip(self):
        """CV/LS/SWV/DPV/ASV：注入曲线→保存→删除当前页→重新打开→曲线回来。"""
        app = self.app
        pages = {'CV': app.cv, 'LS': app.ls, 'SWV': app.swv,
                 'DPV': app.dpv, 'ASV': app.asv}
        for tag, frame in pages.items():
            with self.subTest(page=tag):
                frame.graph.update_data([0.0, 100.0, 200.0], [1.0, 2.0, 3.0],
                                        label='roundtrip')
                app.update()
                path = self._temp_path()
                self._save_page(frame, path)
                self.assertTrue(os.path.exists(path))
                # 删除当前页
                app.notebook.select(frame)
                app.delete_current_page_data()
                app.update()
                self.assertEqual(frame.data.index, 0,
                                 f'{tag} 页删除后 data 应清空')
                # 重新打开：应自动跳到该页且曲线回来
                with mock.patch('main.open_file', return_value=path):
                    app.open_data()
                app.update()
                self.assertIs(app.nametowidget(app.notebook.select()), frame,
                              f'打开 {tag} 文件应跳到 {tag} 页')
                self.assertGreaterEqual(len(frame.graph.plotted_lines), 1,
                                        f'{tag} 页打开后应有曲线')

    def test_amp_save_delete_open_roundtrip(self):
        """CA 页：注入数据→保存当前页（菜单路径）→删除→打开→曲线回来。"""
        app = self.app
        handler = app.amp.device  # USBHandler 持有 time/data
        handler.time = [0.001, 0.002, 0.003]
        handler.data = [-0.1, -0.2, -0.15]
        app.notebook.select(app.amp)
        path = self._temp_path()
        with mock.patch('experiments.open_file', return_value=path):
            app.save_current_page_data()  # 菜单路径：保存当前页
        self.assertTrue(os.path.exists(path))
        with open(path, encoding='utf-8') as f:
            self.assertIn('# experiment: AMP', f.read())
        # 删除当前页
        app.delete_current_page_data()
        self.assertEqual(handler.time, [])
        self.assertEqual(handler.data, [])
        # 重新打开：跳到 CA 页且曲线画出来
        with mock.patch('main.open_file', return_value=path):
            app.open_data()
        app.update()
        self.assertIs(app.nametowidget(app.notebook.select()), app.amp)
        self.assertIsNotNone(app.amp.graph.l, 'CA 页打开后应有曲线')
        self.assertEqual(len(handler.time), 3)


# ===== Test 切换语言后全页无中文残留 =====
class TestLanguageSwitchNoResidue(unittest.TestCase):
    """真机 bug 回归：切英文后 ASV 页整页留中文。
    根因：主界面 refresh_language 在 Amp 页抛 AttributeError
    （graph.data 是 list 无 refresh_default_labels），排最后的 ASV 没轮到。
    修复：plotting.refresh_language 加 hasattr 保护 + 主界面逐页 try/except。"""

    @classmethod
    def setUpClass(cls):
        import main as main_mod
        import comm as comm_module
        from fake_device import FakeSerialComm
        with mock.patch.object(comm_module, 'SerialComm',
                               return_value=FakeSerialComm()), \
             mock.patch.object(comm_module.AmpUsb, 'calibrate'):
            cls.app = main_mod.ElectroChemGUI()
        cls.app.withdraw()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.destroy()
        except tk.TclError:
            pass

    @staticmethod
    def _chinese_texts(widget, path, out):
        import re
        zhre = re.compile(r'[\u4e00-\u9fff]')
        for c in widget.winfo_children():
            p = f"{path}/{c.winfo_class()}"
            try:
                t = c.cget('text')
            except Exception:
                t = None
            if isinstance(t, str) and zhre.search(t):
                out.append(f"{p}={t}")
            TestLanguageSwitchNoResidue._chinese_texts(c, p, out)

    def test_english_leaves_no_chinese_on_any_page(self):
        import re
        from i18n import set_language
        app = self.app
        set_language('en')
        app.refresh_language()  # 不应抛异常
        app.update()
        residue = []
        for name in ('cv', 'ls', 'swv', 'dpv', 'amp', 'asv'):
            frame = getattr(app, name)
            self._chinese_texts(frame, name, residue)
            ax = frame.graph.graph_area.axis
            for lbl in (ax.get_title(), ax.get_xlabel(), ax.get_ylabel()):
                if re.search(r'[\u4e00-\u9fff]', lbl or ''):
                    residue.append(f"{name}.axis={lbl}")
        self.assertEqual(residue, [], f"英文模式下残留中文: {residue}")
        # 切回中文确认反向刷新正常
        set_language('zh')
        app.refresh_language()
        app.update()
        self.assertEqual(app.asv.run_button.cget('text'), get_text("asv_run"))

    def test_one_frame_failure_does_not_block_others(self):
        """单页刷新抛异常时，其余页必须照常刷新（主界面逐页保护）。"""
        from i18n import set_language
        app = self.app
        set_language('zh')
        with mock.patch.object(app.cv, 'refresh_language',
                               side_effect=RuntimeError('boom')):
            app.refresh_language()  # 不应抛出
        app.update()
        # ASV（刷新顺序最后）仍被刷新到中文
        self.assertEqual(app.asv.run_button.cget('text'), get_text("asv_run"))


# ===== Test 扫描首次等待按模式计算 =====
class TestScanDelayMs(unittest.TestCase):
    """固件 'L' 型只扫单段：LS/SWV/DPV 页的首次等待不得按往返算 2×
    （用户实测"独立扫描页比 ASV 慢"的根因）。"""

    def _handler(self, mode):
        master = mock.Mock()
        master.device_params = DeviceParameters()
        s = master.device_params.cv_settings
        s.start_voltage, s.end_voltage = 0, 800   # 800 mV 范围
        s.sweep_rate = 0.1                        # 0.1 V/s = 100 mV/s
        s.swv_inc, s.swv_period = 16, 20          # 50 步 × 20 ms
        h = CVFrame.USBHandler(mock.Mock(), mock.Mock(), master, None, mode=mode)
        h.params = master.device_params
        return h

    def test_ls_single_segment(self):
        # 800 mV @ 100 mV/s = 8000 ms（单段），不是 16000
        self.assertEqual(self._handler('LS')._scan_delay_ms(), 8000)

    def test_swv_steps_times_period(self):
        # 50 步 × 20 ms = 1000 ms，不是 2000
        self.assertEqual(self._handler('SWV')._scan_delay_ms(), 1000)

    def test_dpv_same_as_swv(self):
        self.assertEqual(self._handler('DPV')._scan_delay_ms(), 1000)

    def test_cv_keeps_roundtrip(self):
        # CV 三角波往返 2× 是对的：2 × 800/0.1 = 16000
        h = self._handler('CV')
        h.params.cv_settings.delay_time = 2 * 800 / 0.1
        self.assertEqual(h._scan_delay_ms(), 16000)


# ===== ASV/CV 跨页状态泄漏防护 =====
class TestASVDividerIsolation(unittest.TestCase):
    """format_divider 必须读本页 settings：CV 页跑过 SWV 后 use_swv 留在
    True，ASV 溶出扫描的分频值曾被 cv_settings.swv_period 劫持成
    23999（50ms/步，固件扫 ~100s），GUI 却按 ASV 扫速 ~19s 超时——
    真机"ASV scan timed out"的根因。"""

    def _params_after_swv_run(self):
        params = DeviceParameters()
        # 模拟用户刚在 CV 页跑过一发 SWV：标志和周期留在 cv_settings 里
        params.cv_settings.use_swv = True
        params.cv_settings.swv_period = 100   # → divider 23999
        return params

    def test_asv_divider_uses_own_sweep_rate(self):
        master = mock.Mock()
        master.device_params = self._params_after_swv_run()
        h = ASVFrame.USBHandler(mock.Mock(), mock.Mock(), master, None)
        _, divider = h.format_divider(h.settings.sweep_rate)
        # ASV 扫速默认 0.5 V/s → divider 应约 955（~2ms/步），不是 23999
        self.assertNotEqual(divider, 23999)
        self.assertLess(divider, 2000)

    def test_asv_divider_matches_sweep_rate_math(self):
        master = mock.Mock()
        master.device_params = self._params_after_swv_run()
        h = ASVFrame.USBHandler(mock.Mock(), mock.Mock(), master, None)
        rate = h.settings.sweep_rate
        expect = int(round(h.params.clk_freq_isr_pwm /
                           (rate * 1000 / h.params.dac.voltage_step_size)) - 1)
        _, divider = h.format_divider(rate)
        self.assertEqual(divider, expect)

    def test_cv_swv_divider_unchanged(self):
        # CV 页 SWV 行为不许被这次修复改坏：use_swv=True 时仍按方波周期算
        master = mock.Mock()
        master.device_params = self._params_after_swv_run()
        h = CVFrame.USBHandler(mock.Mock(), mock.Mock(), master, None, mode='SWV')
        _, divider = h.format_divider(50)
        self.assertEqual(divider, 23999)


# ===== Test Done 拆包防护 =====
class TestPartialDoneMessage(unittest.TestCase):
    """USB 把 'Done' 拆成两包时，前缀必须暂存等下一包，
    否则轮询永远等不到完成消息（真机 ASV scan timed out 的嫌疑根因）。"""

    def _usb(self, chunks):
        from fake_device import FakeSerialComm, make_fake_ampusb
        master = mock.Mock()
        master.device_params = DeviceParameters()
        fake = FakeSerialComm()
        usb = make_fake_ampusb(master, fake)
        fake.read_data = mock.Mock(side_effect=chunks)
        fake.bytes_waiting = mock.Mock(return_value=5)
        return usb

    def test_split_done_reassembled(self):
        usb = self._usb(['Do', 'ne\x00'])
        self.assertIsNone(usb.usb_read_message())   # 第一包是前缀，暂存
        msg = usb.usb_read_message()                # 第二包拼成完整 Done
        self.assertIsNotNone(msg)
        self.assertIn('Done', msg)

    def test_other_messages_passthrough(self):
        usb = self._usb(['V\x02'])
        self.assertEqual(usb.usb_read_message(), 'V\x02')  # 非 Done 消息照常返回

    def test_no_data_returns_none(self):
        usb = self._usb([None])
        self.assertIsNone(usb.usb_read_message())


# ===== Test CSV 编码兼容 =====
class TestCsvEncodingCompat(unittest.TestCase):
    """中文 Windows 老文件是 GBK（标签"数据 1"），读取端须回退兼容；
    新保存统一 utf-8-sig（真机踩坑：0xca 字节 decode 失败）。"""

    def _write(self, content, encoding):
        import tempfile
        fd, path = tempfile.mkstemp(suffix='.csv')
        with os.fdopen(fd, 'w', encoding=encoding, newline='') as f:
            f.write(content)
        self.addCleanup(os.remove, path)
        return path

    def test_gbk_legacy_file_readable(self):
        from utils import load_csv_file_full
        path = self._write("# experiment: CV\nvoltage,数据 1\n ,\n0,0.1\n100,0.2\n",
                           'gbk')
        loaded = load_csv_file_full(path)
        self.assertIsNotNone(loaded)
        metadata, header, data_rows = loaded
        self.assertEqual(metadata.get('experiment'), 'CV')
        self.assertEqual(header[1], '数据 1')
        self.assertEqual(len(data_rows), 2)

    def test_utf8_file_readable(self):
        from utils import load_csv_file_full
        path = self._write("# experiment: SWV\nvoltage,数据 1\n ,\n0,0.1\n",
                           'utf-8-sig')
        loaded = load_csv_file_full(path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded[0].get('experiment'), 'SWV')

    def test_open_routes_gbk_file(self):
        """完整链路：GBK 老文件 → 打开 → 路由到正确页。"""
        from main import ElectroChemGUI
        gui = mock.Mock()
        for name in ('cv', 'ls', 'swv', 'dpv', 'asv', 'amp'):
            setattr(gui, name, mock.Mock())
        path = self._write("# experiment: ASV\nvoltage,数据 1\n ,\n0,0.1\n",
                           'gbk')
        with mock.patch('main.open_file', return_value=path):
            ElectroChemGUI.open_data(gui)
        gui.asv.open_data.assert_called_once()


class TestRealSaveDialogPath(unittest.TestCase):
    """真实保存路径回归：只桩住"对话框返回文件名"这一步，
    open_file('saveas') 内部真实的 open+编码必须跑通。
    （真机踩坑：asksaveasfile(encoding=...) 把 encoding 传给 Tk 对话框，
    TclError 被 tk 回调吞掉，保存全部无反应，打桩 open_file 的测试抓不到）"""

    def test_saveas_returns_writable_utf8_file(self):
        import tempfile
        from utils import open_file
        fd, path = tempfile.mkstemp(suffix='.csv')
        os.close(fd)
        self.addCleanup(os.remove, path)
        with mock.patch('utils.filedialog.asksaveasfilename',
                        return_value=path) as m:
            f = open_file('saveas')
        # 对话框选项里绝不能混入 encoding/newline（会 TclError）
        _, kwargs = m.call_args
        self.assertNotIn('encoding', kwargs)
        self.assertNotIn('newline', kwargs)
        self.assertIsNotNone(f)
        with f:
            f.write("voltage,数据 1\n0,0.1\n")  # 中文标签必须能写
        with open(path, 'r', encoding='utf-8-sig') as r:
            content = r.read()
        self.assertIn('数据 1', content)

    def test_saveas_cancel_returns_none(self):
        from utils import open_file
        with mock.patch('utils.filedialog.asksaveasfilename', return_value=''):
            self.assertIsNone(open_file('saveas'))

    def test_cv_frame_save_all_data_end_to_end(self):
        """CVFrame.save_all_data 走真实 open_file，写完可用 load_csv_file_full 读回。"""
        import tempfile
        from plotting import PyplotData
        from utils import load_csv_file_full
        frame = CVFrame.__new__(CVFrame)
        frame.data = PyplotData()
        frame.data.add_data([0, 100, 200], [0.1, 0.2, 0.3])
        frame.master = mock.Mock()
        frame.master.data_save_type = "Converted"
        frame.get_metadata = lambda: {'experiment': 'CV'}
        fd, path = tempfile.mkstemp(suffix='.csv')
        os.close(fd)
        self.addCleanup(os.remove, path)
        with mock.patch('utils.filedialog.asksaveasfilename', return_value=path):
            CVFrame.save_all_data(frame)
        loaded = load_csv_file_full(path)
        self.assertIsNotNone(loaded)
        metadata, header, data_rows = loaded
        self.assertEqual(metadata.get('experiment'), 'CV')
        self.assertEqual(len(data_rows), 3)


class TestHardwareTestRobustness(unittest.TestCase):
    """hardware_tests.py 的真机隔离/收尾规则（真机踩坑：
    X 复位后硬件 Sleep 断电不恢复，留给下次连接会标定失败、扫描卡死）。"""

    def _mock_dev(self):
        dev = mock.Mock()
        dev.usb_read_message.return_value = None  # 永远收不到 Done
        return dev

    def test_scan_timeout_resets_and_recovers_with_scan(self):
        """扫描超时必须 X 复位 + 跑恢复小扫描（'H' 唤不醒 X 后的 Sleep，
        真机实测），隔离故障、不毒化后续测试。"""
        import hardware_tests
        dev = self._mock_dev()
        with mock.patch.object(hardware_tests, '_tiny_ls_scan') as tiny:
            with self.assertRaises(hardware_tests.HardwareTestError):
                hardware_tests._run_lut_scan(dev, 'S|1948|2148|02399|LS',
                                             1199, 200, timeout_s=0.5)
        calls = [c.args[0] for c in dev.usb_write.call_args_list]
        self.assertIn('X', calls)
        tiny.assert_called_once_with(dev)  # X 后必须跟恢复扫描

    def test_cleanup_ends_with_completed_scan(self):
        """真机实测收尾必须以"扫描自然结束"结束（绝不能是 X）：
        设备留在 GUI 正常用完后的良好状态，下次连接才不带病。"""
        import hardware_tests
        import fake_device
        import comm as comm_module
        holder = {}
        orig_fake = fake_device.FakeSerialComm
        orig_serial = comm_module.SerialComm

        class Rec(orig_fake):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                holder['dev'] = self

        fake_device.FakeSerialComm = Rec
        try:
            lines, failures, found = hardware_tests.run(make_fake=True)
        finally:
            fake_device.FakeSerialComm = orig_fake
            comm_module.SerialComm = orig_serial
        self.assertTrue(found)
        self.assertEqual(failures, [])
        received = holder['dev'].received
        self.assertNotEqual(received[-1], 'X')     # 收尾绝不是 X
        self.assertIn('R', received[-3:])          # 收尾是跑完一个小扫描


if __name__ == '__main__':
    unittest.main()


# ===== 回归：电流符号 / 差分方向（"ASV 峰向下"根因） =====
class TestCurrentSignConvention(unittest.TestCase):
    """SWV/DPV 差分方向必须跟随扫描方向。

    固件把脉冲放在 value+height（DAC 计数）——DAC 与电极电位反相，
    每步第一个采样在电极电位"低侧"。上行扫描（ASV 溶出）时差分要
    "后减前"，否则阳极峰被画成向下的负峰（真机踩过）。
    """

    def _make_usb(self):
        mocked_root = mock.Mock()
        mocked_root.data_save_type = "Converted"
        device_params = DeviceParameters()
        device_params.adc_tia.shift = 0
        device_params.adc_tia.counts_to_current = 0.001
        with mock.patch('comm.SerialComm') as mock_serial_cls:
            mock_serial_cls.return_value.connected = False
            usb = AmpUsb(mocked_root, device_params)
        return usb

    def test_ascending_diff_anodic_positive(self):
        """上行扫描：波形在高电位侧采样（第 2 个）-> 差分为正（阳极峰朝上）。"""
        usb = self._make_usb()
        # 采样对: [E-h 采样, E+h 采样]，阳极电流集中在 E+h
        out = usb.process_data([100, 500, 100, 500], swv=True, ascending=True)
        self.assertEqual(out, [0.4, 0.4])

    def test_descending_diff_cathodic_negative(self):
        """下行扫描：波形在低电位侧采样（第 1 个）-> 差分为负（阴极峰朝下）。"""
        usb = self._make_usb()
        out = usb.process_data([400, 100, 400, 100], swv=True, ascending=False)
        for value in out:
            self.assertAlmostEqual(value, 0.3)

    def test_legacy_call_without_direction_unchanged(self):
        """不传 ascending 的旧调用保持上游行为（前减后），兼容第三方脚本。"""
        usb = self._make_usb()
        out = usb.process_data([10, 5], swv=True)
        self.assertEqual(out, [0.005])

    def test_invert_current_sign_toggle(self):
        """电流符号诊断开关：整体反相（前端输出反相的机器用）。"""
        import config as config_module
        usb = self._make_usb()
        old = config_module.INVERT_CURRENT_SIGN
        try:
            config_module.INVERT_CURRENT_SIGN = True
            out = usb.process_data([100, -100], swv=False)
            self.assertEqual(out, [-0.1, 0.1])
        finally:
            config_module.INVERT_CURRENT_SIGN = old

    @mock.patch('dialogs.TimerToplevel')
    def test_asv_dpv_peak_points_up(self, _mock_timer):
        """ASV DPV 溶出全流程：峰必须朝上（阳极电流为正，IUPAC）。"""
        from fake_device import FakeSerialComm, make_fake_ampusb
        master = mock.Mock()
        master.device_params = DeviceParameters()
        master.data_save_type = "Converted"
        master.after.side_effect = lambda _delay, cb: cb()
        settings = master.device_params.asv_settings
        settings.update_settings(settings.clean_volt, settings.clean_time,
                                 settings.plate_volt, settings.plate_time,
                                 -1180, settings.sweep_rate, sweep_type=1,
                                 pulse_height=50, pulse_inc=16, pulse_width=10)
        fake = FakeSerialComm()
        usb = make_fake_ampusb(master, fake)
        handler = ASVFrame.USBHandler(mock.Mock(), usb, master, None)
        handler.asv_run(handler.graph, mock.Mock())
        _x, y, _raw = handler.graph.update_data.call_args.args
        self.assertGreater(max(y), 0.2)
        # 峰朝上：最大值明显大于负向幅度（旧的"前减后"实现这里会得到负峰）
        self.assertGreater(max(y), abs(min(y)) * 2)


# ===== 回归：CV 循环圈数 =====
class TestCVCycles(unittest.TestCase):
    """CV 多圈：设置持久化、命令尾缀、固件探测、GUI 逐圈调度兜底。"""

    def test_cycles_default_and_update(self):
        params = DeviceParameters()
        self.assertEqual(params.cv_settings.cycles, 1)
        params.cv_settings.update_settings(
            -900, 900, 0.2, 'CV', 'Start', 100, 5, 100, False, cycles=3)
        self.assertEqual(params.cv_settings.cycles, 3)
        # 非 CV 技术保存时圈数归 1
        params.cv_settings.update_settings(
            -900, 900, 0.2, 'LS', 'Start', 100, 5, 100, False, cycles=3)
        self.assertEqual(params.cv_settings.cycles, 1)

    def test_send_appends_cycles_suffix(self):
        from fake_device import FakeSerialComm, make_fake_ampusb
        master = mock.Mock()
        master.device_params = DeviceParameters()
        master.data_save_type = "Converted"
        fake = FakeSerialComm()
        master.device = make_fake_ampusb(master, fake)
        handler = CVFrame.USBHandler(mock.Mock(), master.device, master, None,
                                     mode='CV')
        s = master.device_params.cv_settings
        s.start_voltage, s.end_voltage = -100, 100  # 小范围，3 圈在容量内
        s.cycles = 3
        s.sweep_type = 'CV'
        s.use_swv = False
        handler.send_cv_parameters()
        s_cmds = [c for c in fake.received if c.startswith('S|')]
        self.assertTrue(s_cmds)
        parts = s_cmds[0].split('|')
        self.assertEqual(parts[5], '3')  # S|start|end|div|CS|3

    def test_send_omits_suffix_when_over_capacity(self):
        """圈数超容量：显式发 '|1'（固件 OUT 缓冲残留会让缺省尾缀继承旧
        圈数，真机实测 got 4002），由 GUI 逐圈跑满，不私自改写圈数。"""
        from fake_device import FakeSerialComm, make_fake_ampusb
        master = mock.Mock()
        master.device_params = DeviceParameters()
        master.data_save_type = "Converted"
        fake = FakeSerialComm(support_multi_cycle=True)
        master.device = make_fake_ampusb(master, fake)
        handler = CVFrame.USBHandler(mock.Mock(), master.device, master, None,
                                     mode='CV')
        s = master.device_params.cv_settings
        s.start_voltage, s.end_voltage = -900, 900  # 大范围，容量仅 1 圈
        s.cycles = 3
        s.sweep_type = 'CV'
        s.use_swv = False
        handler.send_cv_parameters()
        s_cmds = [c for c in fake.received if c.startswith('S|')]
        self.assertTrue(s_cmds)
        self.assertTrue(s_cmds[0].endswith('|CS|1'),  # S|start|end|div|CS|1
                        "超容量时必须显式发 '|1'（防固件继承旧圈数）: %s"
                        % s_cmds[0])
        self.assertEqual(s.cycles, 3, "不得私自改写用户圈数")

    def test_probe_firmware_multi_cycle(self):
        """补丁固件：'g' 回读 N 圈长度 -> 固件多圈模式（零间隙连续循环）。"""
        from fake_device import FakeSerialComm, make_fake_ampusb
        master = mock.Mock()
        master.device_params = DeviceParameters()
        master.data_save_type = "Converted"
        fake = FakeSerialComm(support_multi_cycle=True)
        master.device = make_fake_ampusb(master, fake)
        handler = CVFrame.USBHandler(mock.Mock(), master.device, master, None,
                                     mode='CV')
        s = master.device_params.cv_settings
        s.start_voltage, s.end_voltage = -100, 100  # 小范围避免 LUT 溢出
        s.cycles = 2
        s.sweep_type = 'CV'
        s.use_swv = False
        handler.send_cv_parameters()
        self.assertTrue(handler._fw_multi_cycle)
        self.assertIn('g', fake.received)

    def test_old_firmware_falls_back_to_gui_loop(self):
        """旧固件：LUT 只有 1 圈 -> GUI 逐圈调度，数据按圈拼接。"""
        from fake_device import FakeSerialComm, make_fake_ampusb
        master = mock.Mock()
        master.device_params = DeviceParameters()
        master.data_save_type = "Converted"
        fake = FakeSerialComm(support_multi_cycle=False)
        master.device = make_fake_ampusb(master, fake)
        handler = CVFrame.USBHandler(mock.Mock(), master.device, master, None,
                                     mode='CV')
        s = master.device_params.cv_settings
        s.start_voltage, s.end_voltage = -100, 100
        s.cycles = 3
        s.sweep_type = 'CV'
        s.use_swv = False
        handler.send_cv_parameters()
        self.assertFalse(handler._fw_multi_cycle)
        canvas = mock.Mock()
        canvas.master = master
        canvas.master.autosave_scan = mock.Mock()
        handler.run_button = mock.Mock()
        handler._enter_scan_state()
        handler._loop_cycles_left = 2
        handler._multi_cycle_buffer = []
        handler._show_progress = mock.Mock()
        # 首圈完成 -> 取数并接续第二圈
        handler.run_scan_continue(canvas)
        self.assertEqual(handler._loop_cycles_left, 1)
        self.assertIn('R', fake.received)  # 已重新启动下一圈
        # 第二圈完成 -> 接续第三圈
        handler.run_scan_continue(canvas)
        self.assertEqual(handler._loop_cycles_left, 0)
        # 第三圈完成 -> 拼接显示一次
        handler.run_scan_continue(canvas)
        canvas.update_data.assert_called_once()
        x, y, _raw = canvas.update_data.call_args.args
        self.assertEqual(len(x), len(y))
        # 3 圈数据长度 = 3 × 单圈
        self.assertGreaterEqual(len(y), 3 * 200)


    def test_old_firmware_falls_back_to_gui_loop(self):
        """旧固件：LUT 只有 1 圈 -> GUI 逐圈调度，数据按圈拼接。"""
        from fake_device import FakeSerialComm, make_fake_ampusb
        master = mock.Mock()
        master.device_params = DeviceParameters()
        master.data_save_type = "Converted"
        fake = FakeSerialComm(support_multi_cycle=False)
        master.device = make_fake_ampusb(master, fake)
        handler = CVFrame.USBHandler(mock.Mock(), master.device, master, None,
                                     mode='CV')
        s = master.device_params.cv_settings
        s.start_voltage, s.end_voltage = -100, 100
        s.cycles = 3
        s.sweep_type = 'CV'
        s.use_swv = False
        handler.send_cv_parameters()
        self.assertFalse(handler._fw_multi_cycle)
        canvas = mock.Mock()
        canvas.master = master
        canvas.master.autosave_scan = mock.Mock()
        handler.run_button = mock.Mock()
        handler._enter_scan_state()
        handler._loop_cycles_left = 2
        handler._multi_cycle_buffer = []
        handler._show_progress = mock.Mock()
        handler._scan_deadline = time.time() + 30
        # 每圈：假设备收到 'R' 排队 Done -> run_scan_continue 取数并接续
        for expected_left in (1, 0):
            master.device.usb_write('R')  # 假设备排队 "Done"
            handler.run_scan_continue(canvas)
            self.assertEqual(handler._loop_cycles_left, expected_left)
        # 末圈取完 -> 拼接显示一次
        master.device.usb_write('R')
        handler.run_scan_continue(canvas)
        canvas.update_data.assert_called_once()
        x, y, _raw = canvas.update_data.call_args.args
        self.assertEqual(len(x), len(y))
        # 3 圈数据长度 = 3 × 单圈（每圈 400+ 采样）
        self.assertGreaterEqual(len(y), 3 * 200)

    def test_x_line_multi_cycle_matches_samples(self):
        from utils import make_cv_x_line, cv_lut_length
        from fake_device import build_cv_lut
        # DAC 计数空间: start=2148(-100mV) end=1948(+100mV)。
        # 小范围避免触发 LUT 容量封顶（封顶时固件截尾，长度不可预估）
        for st, x_type in (('S', 'Start'), ('Z', 'Zero')):
            for cyc in (1, 2, 3):
                lut = build_cv_lut(2148, 1948, 2048, cyc, st, peak_height=0)
                est_n, _one = cv_lut_length(2148, 1948, st, cyc)
                self.assertEqual(len(lut), est_n,
                                 f'{st}x{cyc}: 估算与固件分段规则不一致')
                x = make_cv_x_line(-100, 100, 1, x_type, cyc)
                self.assertEqual(len(x), len(lut) - 1,
                                 f'{st}x{cyc}: x 轴应等于采样数(去掉首点)')

    def test_max_cv_cycles_capacity(self):
        from config import max_cv_cycles, DAC
        dac = DAC("DVDAC")
        # ±900 mV 一圈就要 3601 点 -> 容量只够 1 圈
        self.assertEqual(max_cv_cycles(-900, 900, 'Start', dac), 1)
        # -200~600 mV: 每圈 1601 点 -> 容量 3 圈
        self.assertEqual(max_cv_cycles(-200, 600, 'Start', dac), 3)
        # 零起点方式还要扣掉 0↔start / end↔0 的进出段，圈数只会更少
        self.assertLessEqual(max_cv_cycles(-200, 600, 'Zero', dac),
                             max_cv_cycles(-200, 600, 'Start', dac))

    def test_estimate_scan_seconds_scales_with_cycles(self):
        master = mock.Mock()
        master.device_params = DeviceParameters()
        master.data_save_type = "Converted"
        handler = CVFrame.USBHandler(mock.Mock(), master.device, master, None,
                                     mode='CV')
        s = master.device_params.cv_settings
        s.start_voltage, s.end_voltage = -900, 900
        s.sweep_rate = 0.2
        s.use_swv = False
        s.sweep_type = 'CV'
        s.delay_time = 2 * 1800 / 0.2
        s.cycles = 1
        one = handler._estimate_scan_seconds()
        s.cycles = 3
        three = handler._estimate_scan_seconds()
        # 扫描时间按圈数线性增长；前置 delay 只加一次
        per_cycle = one - s.delay_time / 1000.0
        self.assertAlmostEqual(three, s.delay_time / 1000.0 + 3 * per_cycle,
                               places=6)


# ===== 回归：ASV 溶出参数与独立 DPV/SWV 页对齐 =====
class TestASVPulseAlignment(unittest.TestCase):
    """ASV 溶出方法参数必须与独立 DPV/SWV 页同名参数同义同默认。"""

    def test_pulse_defaults_match_pulse_pages(self):
        import config as config_module
        d = config_module.DEFAULT_CV_SETTINGS
        self.assertEqual(config_module.PULSE_HEIGHT, d['swv_height'])
        self.assertEqual(config_module.PULSE_INCREMENT, d['swv_inc'])
        self.assertEqual(config_module.PULSE_WIDTH, d['swv_period'])

    def test_sweep_type_radio_roundtrip(self):
        settings = DeviceParameters().asv_settings
        settings.update_settings(-1500, 60, -1500, 300, 500, 0.5, sweep_type=1)
        self.assertEqual(settings.sweep_type, "DPV")
        settings.update_settings(-1500, 60, -1500, 300, 500, 0.5, sweep_type=2)
        self.assertEqual(settings.sweep_type, "SWV")
        settings.update_settings(-1500, 60, -1500, 300, 500, 0.5, sweep_type=0)
        self.assertEqual(settings.sweep_type, "LS")

    def test_i18n_keys_complete_and_symmetric(self):
        import i18n
        zh, en = i18n.TEXTS['zh'], i18n.TEXTS['en']
        for key in ("settings_cycles", "err_lut_capacity", "saturation_title",
                    "saturation_msg", "asv_stripping_method", "pulse_period",
                    "asv_pulse_hint", "grp_params", "settings_cycles_hint",
                    "dev_current_sign", "status_current_range"):
            self.assertIn(key, zh, f"缺少中文 key: {key}")
            self.assertIn(key, en, f"缺少英文 key: {key}")
        self.assertEqual(set(zh), set(en), "中英字典 key 不对称")


# ===== 回归：ADC 饱和（平头峰）检测 =====
class TestSaturationDetection(unittest.TestCase):
    """平头峰 = 数据触及 ADC 量程被削平；必须提示换更大量程而不是调显示。"""

    def _make_handler(self):
        master = mock.Mock()
        master.device_params = DeviceParameters()
        master.data_save_type = "Converted"
        handler = CVFrame.USBHandler(mock.Mock(), master.device, master, None,
                                     mode='CV')
        return handler

    def test_saturated_data_triggers_warning(self):
        import experiments as experiments_module
        handler = self._make_handler()
        rail = 16384
        raw = [rail] * 30 + [100] * 10
        with mock.patch.object(experiments_module.messagebox,
                               'showwarning') as warn:
            handler._warn_if_saturated(raw)
        warn.assert_called_once()

    def test_normal_data_no_warning(self):
        import experiments as experiments_module
        handler = self._make_handler()
        raw = [100, -100, 200, -200, 300] * 8
        with mock.patch.object(experiments_module.messagebox,
                               'showwarning') as warn:
            handler._warn_if_saturated(raw)
        warn.assert_not_called()

    def test_mostly_negative_rail_also_warns(self):
        import experiments as experiments_module
        handler = self._make_handler()
        # 负向饱和样本（≈ -16384）会被固件终止码截断，这里是临界值模拟
        raw = [-16300] * 25 + [100] * 15
        with mock.patch.object(experiments_module.messagebox,
                               'showwarning') as warn:
            handler._warn_if_saturated(raw)
        warn.assert_called_once()


# ===== 回归：合成 Mock 数据（脱离硬件的全方法回归） =====
class TestMockDataSuite(unittest.TestCase):
    """fake_device 生成的合成数据：路由、寻峰、符号约定、Cottrell 衰减。"""

    @classmethod
    def setUpClass(cls):
        import tempfile
        from fake_device import generate_test_csvs
        cls.tmpdir = tempfile.mkdtemp(prefix="psoc_mock_")
        cls.paths = generate_test_csvs(cls.tmpdir)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _load(self, path):
        from utils import load_csv_file_full
        loaded = load_csv_file_full(path)
        self.assertIsNotNone(loaded, f"{path} 无法识别")
        meta, header, rows = loaded
        v = [float(r[0]) for r in rows]
        c = [float(r[1]) for r in rows]
        return meta, v, c

    def test_all_csvs_route_to_pages(self):
        import main as main_mod
        for path in self.paths:
            meta, _v, _c = self._load(path)
            experiment = str(meta.get('experiment', '')).strip().upper()
            self.assertIn(experiment, main_mod.ElectroChemGUI.EXPERIMENT_PAGES,
                          f"{path} 的 experiment={experiment!r} 无法路由")

    def test_asv_csvs_peak_positive_and_located(self):
        from algorithms import analyze_stripping_peak
        asv_paths = [p for p in self.paths if 'ASV' in os.path.basename(p)]
        self.assertEqual(len(asv_paths), 3)
        for path in asv_paths:
            _meta, v, c = self._load(path)
            peaks, _processed = analyze_stripping_peak(v, c)
            self.assertTrue(peaks, f"{path} 未找到溶出峰")
            top = peaks[0]
            self.assertTrue(-500 < top['voltage'] < -100,
                            f"{path} 峰位异常: {top['voltage']}")
            # IUPAC：阳极溶出峰朝上（正电流）
            self.assertGreater(top['height'], 0)

    def test_cv_csv_three_cycles(self):
        path = next(p for p in self.paths if 'CV_' in os.path.basename(p))
        _meta, v, c = self._load(path)
        self.assertEqual(v.count(700), 3, "3 圈 CV 应 3 次到达终点电位")
        self.assertEqual(v[0], -400)
        self.assertEqual(v[-1], -400, "CV 收尾应回到起始电位")
        # 正向段前半程应出现明显的阳极峰（正电流）
        anodic = max(c[:len(c) // 4])
        self.assertGreater(anodic, 10)

    def test_amp_csv_cottrell_decay(self):
        path = next(p for p in self.paths if 'AMP' in os.path.basename(p))
        _meta, t, c = self._load(path)
        self.assertGreater(len(t), 100)
        # Cottrell 衰减：后半段均值应明显低于前半段
        half = len(c) // 2
        self.assertLess(sum(c[half:]) / len(c[half:]),
                        sum(c[:half]) / len(c[:half]))

    def test_fw_lut_length_matches_estimator(self):
        from fake_device import simulate_lut_length
        from utils import cv_lut_length
        for st in ('S', 'Z'):
            for cyc in (1, 2, 3, 4):
                sim = simulate_lut_length(2200, 1900, 2048, cyc, st)
                est_n, _est_1 = cv_lut_length(2200, 1900, st, cyc)
                self.assertEqual(sim, est_n, f"{st}x{cyc} LUT 长度不一致")


# ===== 回归：ASV 溶出电位范围（起始/终止电位分离） =====
class TestASVStripRange(unittest.TestCase):
    """溶出扫描从"起始电位"扫到"终止电位"；缺省起始=富集电位（旧行为）。"""

    def test_strip_start_custom(self):
        settings = DeviceParameters().asv_settings
        settings.update_settings(-1500, 60, -1500, 300, 500, 0.5,
                                 strip_start=-1200)
        self.assertEqual(settings.strip_start, -1200)
        self.assertEqual(settings.low_voltage, -1200)
        self.assertEqual(settings.start_voltage, -1200)
        self.assertEqual(settings.high_voltage, 500)

    def test_strip_start_defaults_to_plate_voltage(self):
        settings = DeviceParameters().asv_settings
        settings.update_settings(-1500, 60, -1000, 300, 500, 0.5)
        self.assertEqual(settings.strip_start, -1000)
        self.assertEqual(settings.low_voltage, -1000)

    def test_deposition_uses_plate_voltage_not_strip_start(self):
        """富集电位与溶出起始电位互不干扰（电化学语义正确）。"""
        settings = DeviceParameters().asv_settings
        settings.update_settings(-1500, 60, -1000, 300, 500, 0.5,
                                 strip_start=-1050)
        self.assertEqual(settings.plate_volt, -1000)
        self.assertEqual(settings.strip_start, -1050)


# ===== 回归：数据菜单与丝带功能对齐 =====
class TestMenuDataActions(unittest.TestCase):
    """数据菜单新增项：删除当前页/数据分析子菜单路由到当前页方法。"""

    @classmethod
    def setUpClass(cls):
        import main as main_mod
        import comm as comm_module
        from fake_device import FakeSerialComm
        with mock.patch.object(comm_module, 'SerialComm',
                               return_value=FakeSerialComm()), \
             mock.patch.object(comm_module.AmpUsb, 'calibrate'):
            cls.app = main_mod.ElectroChemGUI()
        cls.app.withdraw()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        _safe_destroy(cls.app)

    def test_run_page_method_routes_to_current_page(self):
        """数据分析命令转发到当前页；计时电流页无此功能时友好提示。"""
        self.app.notebook.select(self.app.cv)
        with mock.patch.object(self.app.cv, 'apply_smooth') as smooth:
            self.app.run_page_method('apply_smooth')
        smooth.assert_called_once()
        # 切到计时电流页：没有 apply_smooth -> 弹提示而非崩溃
        self.app.notebook.select(self.app.amp)
        with mock.patch('main.messagebox.showinfo') as info:
            self.app.run_page_method('apply_smooth')
        info.assert_called_once()

    def test_delete_current_page_dialog_guards_empty_page(self):
        """空页面走菜单删除时提示"没有数据"，不弹勾选窗。"""
        self.app.notebook.select(self.app.cv)
        self.app.delete_current_page_data()  # 确保当前页为空
        self.assertEqual(self.app.cv.data.index, 0)
        with mock.patch('main.messagebox.showinfo') as info, \
             mock.patch('dialogs.UserSelectDataDelete') as dlg:
            self.app.delete_current_page_data_dialog()
        info.assert_called_once()
        dlg.assert_not_called()

    def test_delete_current_page_dialog_opens_with_data(self):
        """当前页有数据时弹出勾选删除窗。"""
        from dialogs import UserSelectDataDelete
        self.app.notebook.select(self.app.cv)
        self.app.cv.graph.update_data([0, 1, 2], [1, 2, 3], label='t')
        with mock.patch('dialogs.UserSelectDataDelete') as dlg:
            self.app.delete_current_page_data_dialog()
        dlg.assert_called_once()
        self.app.delete_current_page_data()  # 清理


# ===== 回归：模拟模式（无需硬件跑全套界面） =====
class TestSimulateMode(unittest.TestCase):
    """模拟模式：开关持久化 + 以模拟器构建 GUI（全程不碰串口）。"""

    def setUp(self):
        # 参数文件经 config 的测试隔离开关解析到临时目录，绝不碰真实配置
        self._settings_path = config_module._settings_path(
            config_module.SAVED_SETTINGS_FILE)

    def tearDown(self):
        if os.path.exists(self._settings_path):
            os.remove(self._settings_path)
        from ttkbootstrap.style.engine import Style
        Style.instance = None

    def test_simulate_flag_persists_and_builds_simulator(self):
        import main as main_mod
        from fake_device import FakeSerialComm
        # 打开开关并持久化
        params = DeviceParameters()
        params.cv_settings.simulate_mode = True
        params.cv_settings.save_settings()
        self.assertIn('simulate_mode = True',
                      open(self._settings_path, encoding='utf-8').read())
        # 重启等效：以持久化配置构建 GUI
        app = main_mod.ElectroChemGUI()
        try:
            self.assertTrue(app.device_params.cv_settings.simulate_mode)
            self.assertIsInstance(app.device.device, FakeSerialComm)
            self.assertIn('模拟', app.connect_button.cget('text'))
            self.assertEqual(app.voltage_source_label.get(), '模拟器（无硬件）')
        finally:
            app.destroy()
        # settings.txt 由 tearDown 还原

    def test_default_off_uses_real_device_path(self):
        """默认关闭：走真实设备构造路径（打桩 SerialComm 验证不连模拟器）。"""
        import main as main_mod
        from fake_device import FakeSerialComm
        params = DeviceParameters()
        params.cv_settings.simulate_mode = False
        params.cv_settings.save_settings()
        with mock.patch.object(main_mod, 'AmpUsb',
                               wraps=main_mod.AmpUsb) as spy:
            with mock.patch.object(comm_module, 'SerialComm',
                                   return_value=FakeSerialComm()),                  mock.patch.object(comm_module.AmpUsb, 'calibrate'):
                app = main_mod.ElectroChemGUI()
            try:
                spy.assert_called_once()  # 走了 AmpUsb 真实构造
            finally:
                app.destroy()


# ===== 回归：峰结果导出 CSV =====
    def test_switch_blocked_while_scan_running(self):
        """扫描/计时进行中：切换模拟模式被拒绝，菜单拨回、配置不变。"""
        import main as main_mod
        os.chdir(_ROOT)
        params = DeviceParameters()
        params.cv_settings.simulate_mode = True
        params.cv_settings.save_settings()
        app = main_mod.ElectroChemGUI()
        try:
            app.cv.device.is_running = True  # 模拟器上"正在扫描"

            class FakeVar:
                def __init__(self, v):
                    self.v = bool(v)

                def get(self):
                    return self.v

                def set(self, v):
                    self.v = bool(v)

            var = FakeVar(False)  # 想切回真实设备
            with mock.patch('main.messagebox.showwarning') as warn:
                app.set_simulate_mode(var)
            warn.assert_called_once()
            self.assertTrue(var.v)  # 菜单拨回
            self.assertTrue(app.device_params.cv_settings.simulate_mode)
            from fake_device import FakeSerialComm
            self.assertIsInstance(app.device.device, FakeSerialComm)  # 未切换
        finally:
            app.destroy()
            from ttkbootstrap.style.engine import Style
            Style.instance = None


class TestSimulateHotSwapScanRouting(unittest.TestCase):
    """关键回归：模拟模式热切换后，扫描必须真的走新设备。

    此前的事故：切换只换了 app.device 指针，页面 USBHandler 仍持有
    旧设备引用——测试断言了表层指针就宣称完成，用户点"运行"发现
    扫描根本没走模拟器。本用例锁定用户可见行为：
    切换 -> 页面发扫描命令 -> 命令必须到达新设备。
    """

    def test_scan_routes_to_simulator_after_hot_swap(self):
        import main as main_mod
        import comm as comm_module
        from fake_device import FakeSerialComm
        os.chdir(_ROOT)
        # 用例前提：从"模拟关闭"状态开始（不依赖 settings.txt 现状）
        params = DeviceParameters()
        params.cv_settings.simulate_mode = False
        params.cv_settings.save_settings()
        settings_path = os.path.join(_ROOT, 'settings.txt')
        original = open(settings_path, encoding='utf-8').read()
        try:
            with mock.patch.object(comm_module, 'SerialComm',
                                   return_value=FakeSerialComm()),                  mock.patch.object(comm_module.AmpUsb, 'calibrate'):
                app = main_mod.ElectroChemGUI()
            app.withdraw()
            app.update()
            old_device = app.device

            class FakeVar:
                def __init__(self, v):
                    self.v = bool(v)

                def get(self):
                    return self.v

                def set(self, v):
                    self.v = bool(v)

            # 热切换到模拟器
            with mock.patch('main.messagebox.showinfo'):
                app.set_simulate_mode(FakeVar(True))
            simulator = app.device.device

            # 核心断言 1：页面处理器持有的就是新设备（旧实现正是断在这里）
            self.assertIs(app.cv.device.device, app.device)
            self.assertIs(app.asv.device.device, app.device)
            self.assertIs(app.amp.device.device, app.device)
            self.assertIsNot(app.cv.device.device, old_device)

            # 核心断言 2：页面发出的扫描命令到达新模拟器（而非旧设备）
            cv_settings = app.device_params.cv_settings
            cv_settings.start_voltage, cv_settings.end_voltage = -100, 100
            cv_settings.sweep_rate = 1.0
            cv_settings.cycles = 1
            app.cv.device.send_cv_parameters()
            app.cv.device.device.usb_write('R')
            self.assertTrue(any(c.startswith('S|') for c in simulator.received))
            self.assertIn('R', simulator.received)
            # 旧设备（初始串口桩）不应收到本次扫描命令
            self.assertNotIn('R', old_device.device.received)

            # 核心断言 3：数据也能从新设备取回（E0 导出 -> 图有数据）
            app.cv.device.usb_packet_count = 2
            app.cv.device.device.usb_write('E0')
            raw = app.cv.device.device.get_data(2)
            self.assertTrue(raw)
            converted = app.cv.device.device.process_data(raw)
            self.assertTrue(converted)

            # 空闲时切回真实设备：处理器同样重绑
            with mock.patch('main.messagebox.showinfo'):
                app.set_simulate_mode(FakeVar(False))
            self.assertIs(app.cv.device.device, app.device)
            self.assertIsNot(app.cv.device.device, simulator)
            app.destroy()
        finally:
            open(settings_path, 'w', encoding='utf-8').write(original)
            from ttkbootstrap.style.engine import Style
            Style.instance = None


class TestExportPeaks(unittest.TestCase):
    def setUp(self):
        import main as main_mod
        import comm as comm_module
        from fake_device import FakeSerialComm
        with mock.patch.object(comm_module, 'SerialComm',
                               return_value=FakeSerialComm()), \
             mock.patch.object(comm_module.AmpUsb, 'calibrate'):
            self.app = main_mod.ElectroChemGUI()
        self.app.withdraw()
        self.app.update()

    def tearDown(self):
        _safe_destroy(self.app)

    def test_export_writes_peak_csv(self):
        import tempfile
        from fake_device import synthesize_stripping_voltammogram
        v = list(range(-1500, 501, 16))
        c = synthesize_stripping_voltammogram(v)
        self.app.cv.graph.update_data(v, c, label='peak export')
        path = tempfile.mktemp(suffix='.csv')
        self.addCleanup(os.remove, path)
        with mock.patch('experiments.open_file', return_value=path):
            self.app.cv.export_peaks()
        content = open(path, encoding='utf-8-sig').read()
        self.assertIn('mV', content)
        # 至少导出一个峰行，且峰电位落在合理区间
        rows = [r for r in content.splitlines() if r and r[0].isdigit()]
        self.assertTrue(rows)
        self.assertTrue(-500 < float(rows[0].split(',')[1]) < -100)

    def test_export_without_data_prompts(self):
        self.app.notebook.select(self.app.cv)
        self.app.delete_current_page_data()
        with mock.patch('experiments.messagebox.showinfo') as info:
            self.app.cv.export_peaks()
        info.assert_called_once()


# ===== 回归：峰面积/电量统计 =====
class TestPeakAreaCharge(unittest.TestCase):
    def test_area_and_charge_present(self):
        """传入扫速时，峰附带面积 (µA·mV) 与电量 (µC)，且电量=面积/1000/扫速。"""
        from algorithms import analyze_stripping_peak
        from fake_device import synthesize_stripping_voltammogram
        v = list(range(-1500, 501, 16))
        c = synthesize_stripping_voltammogram(v, peak_uA=4.0)
        peaks, _ = analyze_stripping_peak(v, c, sweep_rate=0.5)
        self.assertTrue(peaks)
        top = peaks[0]
        self.assertGreater(top['area'], 0)
        self.assertAlmostEqual(top['charge'], top['area'] / 1000.0 / 0.5,
                               places=9)

    def test_area_without_sweep_rate(self):
        """不给扫速：只有面积，没有电量键。"""
        from algorithms import analyze_stripping_peak
        from fake_device import synthesize_stripping_voltammogram
        v = list(range(-1500, 501, 16))
        c = synthesize_stripping_voltammogram(v, peak_uA=4.0)
        peaks, _ = analyze_stripping_peak(v, c)
        self.assertGreater(peaks[0]['area'], 0)
        self.assertNotIn('charge', peaks[0])


# ===== 回归：曲线运算 =====
class TestCurveMath(unittest.TestCase):
    def test_compute_operations(self):
        from dialogs import compute_curve_math
        ya, yb = [1.0, 2.0, 3.0], [0.5, 1.0, 1.5]
        self.assertEqual(compute_curve_math('subtract', ya, yb), [0.5, 1.0, 1.5])
        self.assertEqual(compute_curve_math('average', ya, yb), [0.75, 1.5, 2.25])
        norm = compute_curve_math('normalize', ya)
        self.assertAlmostEqual(max(abs(v) for v in norm), 1.0)

    def test_dialog_subtract_adds_result_curve(self):
        import main as main_mod
        import comm as comm_module
        from fake_device import FakeSerialComm
        from dialogs import CurveMathDialog
        with mock.patch.object(comm_module, 'SerialComm',
                               return_value=FakeSerialComm()), \
             mock.patch.object(comm_module.AmpUsb, 'calibrate'):
            app = main_mod.ElectroChemGUI()
        try:
            app.cv.graph.update_data([0, 1, 2], [2.0, 3.0, 4.0], label='A')
            app.cv.graph.update_data([0, 1, 2], [1.0, 1.0, 1.0], label='B')
            dlg = CurveMathDialog(app.cv)
            dlg.op.set('subtract')
            dlg.var_a.set('A')
            dlg.var_b.set('B')
            dlg.apply()
            dlg.destroy()
            labels = app.cv.data.label
            self.assertTrue(any('−' in l for l in labels), labels)
            newest = app.cv.data.current_data[-1]
            self.assertEqual(newest, [1.0, 2.0, 3.0])  # A − B
        finally:
            app.destroy()
            from ttkbootstrap.style.engine import Style
            Style.instance = None

    def test_normalize_single_curve(self):
        import main as main_mod
        import comm as comm_module
        from fake_device import FakeSerialComm
        from dialogs import CurveMathDialog
        with mock.patch.object(comm_module, 'SerialComm',
                               return_value=FakeSerialComm()), \
             mock.patch.object(comm_module.AmpUsb, 'calibrate'):
            app = main_mod.ElectroChemGUI()
        try:
            app.cv.graph.update_data([0, 1, 2], [1.0, 2.0, 4.0], label='N')
            dlg = CurveMathDialog(app.cv)
            dlg.op.set('normalize')
            dlg.apply()
            dlg.destroy()
            newest = app.cv.data.current_data[-1]
            self.assertAlmostEqual(max(newest), 1.0)
        finally:
            app.destroy()
            from ttkbootstrap.style.engine import Style
            Style.instance = None


# ===== 回归：方法文件（保存/加载整套参数） =====
class TestMethodFile(unittest.TestCase):
    def setUp(self):
        import main as main_mod
        import comm as comm_module
        from fake_device import FakeSerialComm
        with mock.patch.object(comm_module, 'SerialComm',
                               return_value=FakeSerialComm()), \
             mock.patch.object(comm_module.AmpUsb, 'calibrate'):
            self.app = main_mod.ElectroChemGUI()
        self.app.withdraw()
        self.app.update()
        import tempfile
        self.path = tempfile.mktemp(suffix='.json')
        self.addCleanup(os.remove, self.path)
        # 保存当前（默认）参数作为基线
        self.app.save_method_file(self.path)

    def tearDown(self):
        _safe_destroy(self.app)

    def test_save_writes_json(self):
        import json
        data = json.load(open(self.path, encoding='utf-8'))
        self.assertEqual(data['method'], 'PSoC-Potentiostat-YI-1.0')
        self.assertIn('start_voltage', data['cv'])
        self.assertIn('strip_start', data['asv'])

    def test_load_restores_parameters(self):
        """改乱参数后加载方法文件应恢复基线，并强制下次运行重发参数。"""
        import json
        data = json.load(open(self.path, encoding='utf-8'))
        data['cv']['start_voltage'] = -800
        data['cv']['end_voltage'] = 600
        data['asv']['strip_start'] = -1400
        data['current_range_index'] = 3
        modded = tempfile.mktemp(suffix='.json')
        self.addCleanup(os.remove, modded)
        json.dump(data, open(modded, 'w', encoding='utf-8'))

        self.app.load_method_file(modded)
        cv = self.app.device_params.cv_settings
        asv = self.app.device_params.asv_settings
        self.assertEqual(cv.start_voltage, -800)
        self.assertEqual(cv.end_voltage, 600)
        self.assertEqual(asv.strip_start, -1400)
        self.assertEqual(self.app.device_params.adc_tia.current_option_index, 3)
        # 下一次运行强制重发扫描参数
        self.assertEqual(self.app.device.last_experiment, '')

    def test_load_malformed_json_shows_error(self):
        bad = tempfile.mktemp(suffix='.json')
        self.addCleanup(os.remove, bad)
        open(bad, 'w', encoding='utf-8').write('{not json')
        with mock.patch('main.messagebox.showerror') as err:
            self.app.load_method_file(bad)
        err.assert_called_once()


# ===== 回归：主题切换与持久化 =====
class TestTheme(unittest.TestCase):
    def test_theme_persists_and_applies(self):
        import main as main_mod
        # 参数文件经 config 的测试隔离开关解析到临时目录
        settings_path = config_module._settings_path(
            config_module.SAVED_SETTINGS_FILE)
        try:
            params = DeviceParameters()
            params.cv_settings.theme = 'darkly'
            params.cv_settings.save_settings()
            app = main_mod.ElectroChemGUI()
            try:
                self.assertEqual(app.style.theme_use(), 'darkly')
                # 菜单切换即时生效并持久化
                app.set_theme('cosmo')
                self.assertEqual(app.style.theme_use(), 'cosmo')
                self.assertIn('theme = cosmo',
                              open(settings_path, encoding='utf-8').read())
            finally:
                app.destroy()
        finally:
            if os.path.exists(settings_path):
                os.remove(settings_path)


# ===== 回归：标准曲线 / 定量分析 =====
class TestCalibration(unittest.TestCase):
    def test_linear_fit_known_points(self):
        """完美直线：k=2, b=1, R²=1。"""
        from calibration import linear_fit
        k, b, r2 = linear_fit([(1, 3.0), (2, 5.0), (3, 7.0), (4, 9.0)])
        self.assertAlmostEqual(k, 2.0, places=9)
        self.assertAlmostEqual(b, 1.0, places=9)
        self.assertAlmostEqual(r2, 1.0, places=9)

    def test_concentration_from_signal(self):
        from calibration import concentration_from_signal
        # y = 0.02·x + 0.01，信号 1.01 -> 50
        self.assertAlmostEqual(concentration_from_signal(0.02, 0.01, 1.01), 50.0)

    def test_standard_addition_intercept(self):
        """标准加入法：未知样本底信号 b，斜率 k -> C = b/k。"""
        from calibration import standard_addition_concentration
        # 未知样 50 ppb：信号 1.0 + 0.02·c加 -> 拟合 k=0.02, b=1.0
        self.assertAlmostEqual(standard_addition_concentration(0.02, 1.0), 50.0)

    def test_fit_rejects_identical_x(self):
        from calibration import linear_fit
        with self.assertRaises(ValueError):
            linear_fit([(5, 1.0), (5, 2.0)])

    def test_dialog_import_and_fit(self):
        """从 20/50/100 ppb 合成曲线导入 -> R² 接近 1，可反算浓度。"""
        import json
        import main as main_mod
        from dialogs import CalibrationDialog
        app = main_mod.ElectroChemGUI()
        try:
            dlg = CalibrationDialog(app)
            # 生成三条已知浓度曲线文件（面积严格线性，拟合应接近完美）
            import os
            import tempfile
            from fake_device import synthesize_stripping_voltammogram
            signals = {}
            for conc in (20, 50, 100):
                path = tempfile.mktemp(suffix='.csv')
                self.addCleanup(os.remove, path)
                v = list(range(-1500, 501, 16))
                c = synthesize_stripping_voltammogram(
                    v, peak_uA=0.2 * conc, seed=1000 + conc)
                with open(path, 'w', encoding='utf-8-sig') as f:
                    f.write("label,x\nvoltage_mV,current_uA\n")
                    for vi, ci in zip(v, c):
                        f.write("{0},{1}\n".format(vi, ci))
                dlg.import_curves.__globals__  # noqa
                with mock.patch('tkinter.filedialog.askopenfilenames',
                                return_value=[path]),                      mock.patch('dialogs.messagebox.showinfo'):
                    dlg.import_curves()
                # 读取导入的信号值用于预期校验
                from algorithms import analyze_stripping_peak
                peaks, _ = analyze_stripping_peak(v, c)
                signals[conc] = peaks[0].get('area', peaks[0]['height'])
            self.assertEqual(len(dlg.points), 3)
            # 补填浓度并拟合
            dlg.points = [(conc, sig) for conc, (c20, sig) in
                          zip((20, 50, 100), sorted(signals.items()))]
            dlg.refresh_fit()
            k, b, r2 = dlg.fit_result
            self.assertGreater(r2, 0.99)
            # 反算：50 ppb 附近的信号应还原 ~50
            from calibration import concentration_from_signal
            self.assertAlmostEqual(
                concentration_from_signal(k, b, signals[50]), 50.0, delta=5.0)
            dlg.destroy()
        finally:
            app.destroy()
            from ttkbootstrap.style.engine import Style
            Style.instance = None


# ===== 回归：标准曲线导入健壮性（旧格式/无峰回退/失败报告） =====
class TestCalibrationImportRobustness(unittest.TestCase):
    def setUp(self):
        import main as main_mod
        import comm as comm_module
        from fake_device import FakeSerialComm
        from dialogs import CalibrationDialog
        with mock.patch.object(comm_module, 'SerialComm',
                               return_value=FakeSerialComm()), \
             mock.patch.object(comm_module.AmpUsb, 'calibrate'):
            self.app = main_mod.ElectroChemGUI()
        self.app.withdraw()
        self.app.update()
        self.dlg = CalibrationDialog(self.app)

    def tearDown(self):
        self.dlg.destroy()
        _safe_destroy(self.app)

    def test_import_no_header_numeric_csv(self):
        """老版本无表头纯数字两列 CSV：回退解析可导入。"""
        import tempfile
        path = tempfile.mktemp(suffix='.csv')
        self.addCleanup(os.remove, path)
        with open(path, 'w', encoding='gbk') as f:
            f.write("-1500,0.10\n-1490,0.12\n-1000,1.80\n-500,1.90\n0,0.30\n")
        with mock.patch('tkinter.filedialog.askopenfilenames',
                        return_value=[path]), \
             mock.patch('dialogs.messagebox.showinfo'):
            self.dlg.import_curves()
        self.assertEqual(len(self.dlg.points), 1)
        self.assertGreater(self.dlg.points[0][1], 0)

    def test_import_unrecognized_reports_failure(self):
        """无法识别的文件必须弹报告，不许静默丢弃。"""
        import tempfile
        path = tempfile.mktemp(suffix='.csv')
        self.addCleanup(os.remove, path)
        open(path, 'w', encoding='utf-8').write("随便写的,不是数据\nabc,def\n")
        with mock.patch('tkinter.filedialog.askopenfilenames',
                        return_value=[path]), \
             mock.patch('dialogs.messagebox.showwarning') as warn:
            self.dlg.import_curves()
        warn.assert_called_once()
        self.assertEqual(len(self.dlg.points), 0)

    def test_import_weak_peak_falls_back_to_max(self):
        """3σ 找不到峰的缓升+小峰曲线：回退取基线扣除后最大值。"""
        import tempfile
        import math
        path = tempfile.mktemp(suffix='.csv')
        self.addCleanup(os.remove, path)
        rows = []
        for i in range(400):
            v = -1500 + i
            c = 0.05 * i / 10 + 5.0 * math.exp(-((i - 200) ** 2) / (2 * 15 ** 2))
            rows.append("{0:.0f},{1:.3f}".format(v, c))
        open(path, 'w', encoding='utf-8').write("\n".join(rows) + "\n")
        with mock.patch('tkinter.filedialog.askopenfilenames',
                        return_value=[path]), \
             mock.patch('dialogs.messagebox.showinfo'):
            self.dlg.import_curves()
        self.assertEqual(len(self.dlg.points), 1)
        self.assertGreater(self.dlg.points[0][1], 0)

    def test_update_selected_concentration_enables_fit(self):
        """导入后补浓度：更新选中行 -> 拟合结果出现。"""
        import tempfile
        path = tempfile.mktemp(suffix='.csv')
        self.addCleanup(os.remove, path)
        with open(path, 'w', encoding='utf-8') as f:
            f.write("-1500,0.1\n-1000,0.5\n-500,2.0\n0,0.4\n500,0.1\n")
        with mock.patch('tkinter.filedialog.askopenfilenames',
                        return_value=[path]), \
             mock.patch('dialogs.messagebox.showinfo'):
            self.dlg.import_curves()
        self.dlg.tree.selection_set(self.dlg.tree.get_children()[0])
        self.dlg.entry_c.insert(0, '50')
        self.dlg.update_selected_conc()
        self.assertEqual(self.dlg.points[0][0], 50.0)
        self.assertIsNone(self.dlg.fit_result)  # 单点不足拟合
        # 手动加第二个点 -> 拟合出现
        self.dlg.entry_c.insert(0, '100')
        self.dlg.entry_s.insert(0, '1.0')
        self.dlg.add_manual()
        self.assertIsNotNone(self.dlg.fit_result)


# ===== 回归：i18n 覆盖（缺键会以键名裸露在界面上） =====
class TestI18nCoverage(unittest.TestCase):
    """扫描全部源码的 get_text("...") 字面量，键必须存在于中英字典。

    背景：主题菜单曾引用未定义的 opt_theme 键，界面上直接显示
    "opt_theme"——语言残留测试只查中文，覆盖不到这类缺键。
    """

    def test_every_get_text_key_exists(self):
        import glob
        import i18n
        zh, en = i18n.TEXTS['zh'], i18n.TEXTS['en']
        keys = set()
        for pattern in ('*.py', os.path.join('tests', '*.py'),
                        os.path.join('tools', '*.py')):
            for path in glob.glob(os.path.join(_ROOT, pattern)):
                with open(path, encoding='utf-8') as f:
                    src = f.read()
                keys |= set(re.findall(
                    r'get_text\(\s*["\']([A-Za-z0-9_]+)["\']', src))
        self.assertTrue(keys, "未扫描到任何 get_text 键")
        missing = sorted(k for k in keys if k not in zh or k not in en)
        self.assertEqual(missing, [], f"缺失的 i18n 键: {missing}")

    def test_gui_theme_keys_exist(self):
        """GUI_THEMES 引用的主题键（动态取键）也必须在字典中。"""
        import i18n
        import config
        for theme, key in config.GUI_THEMES.items():
            self.assertIn(key, i18n.TEXTS['zh'], f"缺少 zh key: {key}")
            self.assertIn(key, i18n.TEXTS['en'], f"缺少 en key: {key}")

    def test_simulate_label_matches_hot_swap(self):
        """模拟模式菜单文案不得再提"重启"（行为已是即时热切换）。"""
        import i18n
        for lang in ('zh', 'en'):
            text = i18n.get_text('simulate_mode')
            self.assertNotIn('重启', text)
            self.assertNotIn('Restart', text)


# ===== 回归：参数持久化（ASV 重启不丢 & CV/ASV 彻底隔离） =====
class TestSettingsPersistence(unittest.TestCase):
    """历史事故：ASV 参数改完不落盘、重启丢失。根因是 ASV 与 CV 共用
    同一参数文件、加载白名单按 CV 键过滤、且 ASV 保存路径缺失。
    本类锁定修复后的行为，防止同类问题复发。"""

    def setUp(self):
        # 每个用例独立临时目录，用例之间互不串扰
        self._saved_override = config_module.SETTINGS_DIR_OVERRIDE
        self.tmp = tempfile.mkdtemp(prefix="psoc_persist_case_")
        config_module.SETTINGS_DIR_OVERRIDE = self.tmp

    def tearDown(self):
        config_module.SETTINGS_DIR_OVERRIDE = self._saved_override

    @staticmethod
    def _set_user_asv_params(asv):
        asv.update_settings(800, 2, -1400, 300, 500, 0.5, sweep_type=2,
                            pulse_height=100, pulse_inc=5, pulse_width=100,
                            rest_time=15, stirring_enabled=True,
                            clean_after_strip=False, clean_enabled=True,
                            strip_start=-1400)

    def test_asv_params_survive_restart(self):
        """ASV 页改参数保存后，重新实例化（模拟重启）必须原样读回。"""
        self._set_user_asv_params(config_module.ASVSettings())
        reopened = config_module.ASVSettings()
        self.assertEqual(reopened.clean_volt, 800)
        self.assertEqual(reopened.plate_volt, -1400)
        self.assertEqual(reopened.plate_time, 300)
        self.assertEqual(reopened.strip_start, -1400)
        self.assertEqual(reopened.end_voltage, 500)
        self.assertEqual(reopened.sweep_type, "SWV")
        self.assertEqual(reopened.sweep_rate, 0.5)
        self.assertEqual(reopened.rest_time, 15)
        self.assertEqual(reopened.pulse_width, 100)
        self.assertTrue(reopened.clean_enabled)
        self.assertTrue(reopened.stirring_enabled)
        self.assertFalse(reopened.clean_after_strip)
        # 派生字段随加载重算，与保存前一致
        self.assertEqual(reopened.low_voltage, -1400)
        self.assertEqual(reopened.high_voltage, 500)

    def test_cv_and_asv_do_not_share_files(self):
        """CV 页与 ASV 页参数互不覆盖：一页保存不影响另一页读回。"""
        asv = config_module.ASVSettings()
        asv.update_settings(800, 2, -1400, 300, 350, 0.5, sweep_type=0,
                            strip_start=-1400)
        cv = config_module.CVSettings(config_module.DAC("DVDAC"))
        cv.update_settings(-100, 800, 0.2, 'CV', 'Start',
                           100, 5, 100, False)
        reopened_asv = config_module.ASVSettings()
        self.assertEqual(reopened_asv.end_voltage, 350)
        self.assertEqual(reopened_asv.strip_start, -1400)
        reopened_cv = config_module.CVSettings(config_module.DAC("DVDAC"))
        self.assertEqual(reopened_cv.start_voltage, -100)
        self.assertEqual(reopened_cv.end_voltage, 800)

    def test_declared_keys_load_without_whitelist_edit(self):
        """DEFAULTS 里声明的参数必须能从文件读回——防止加载漏键复发。"""
        asv_path = config_module._settings_path(
            config_module.ASV_SETTINGS_FILE)
        with open(asv_path, 'w') as f:
            f.write("rest_time = 33.0\n")
        self.assertEqual(config_module.ASVSettings().rest_time, 33.0)

    def test_malformed_file_falls_back_to_defaults(self):
        """参数文件损坏时逐行回退默认值，不得抛异常阻断启动。"""
        cv_path = config_module._settings_path(
            config_module.SAVED_SETTINGS_FILE)
        with open(cv_path, 'w') as f:
            f.write("this line has no equals\n"
                    "start_voltage = not_a_number\n"
                    "use_swv = True\n")
        cv = config_module.CVSettings(config_module.DAC("DVDAC"))
        self.assertEqual(
            cv.start_voltage,
            config_module.DEFAULT_CV_SETTINGS['start_voltage'])
        self.assertTrue(cv.use_swv)  # 合法行仍生效

    def test_save_is_atomic_no_tmp_leftover(self):
        """保存后不得残留 .tmp 中间文件。"""
        self._set_user_asv_params(config_module.ASVSettings())
        tmp_path = config_module._settings_path(
            config_module.ASV_SETTINGS_FILE) + ".tmp"
        self.assertFalse(os.path.exists(tmp_path))

    def test_update_settings_saves_to_disk(self):
        """update_settings 必须落盘（旧版 ASV 缺失这一步，是事故根因）。"""
        asv = config_module.ASVSettings()
        self._set_user_asv_params(asv)
        asv_path = config_module._settings_path(
            config_module.ASV_SETTINGS_FILE)
        self.assertTrue(os.path.exists(asv_path))
        with open(asv_path, 'r') as f:
            content = f.read()
        self.assertIn("plate_time = 300", content)
        self.assertIn("sweep_type = SWV", content)

    def test_pulse_params_roundtrip_with_nondefault_values(self):
        """非默认脉冲参数必须真实生效并写盘（回归：漏掉赋值时
        DPV 延时会按默认 pulse_width/inc 算错，端到端测试当场抓住）。"""
        asv = config_module.ASVSettings()
        asv.update_settings(800, 2, -1400, 300, 500, 0.5, sweep_type=1,
                            pulse_height=80, pulse_inc=10, pulse_width=20,
                            strip_start=-1400)
        self.assertEqual(asv.pulse_height, 80)
        self.assertEqual(asv.pulse_inc, 10)
        self.assertEqual(asv.pulse_width, 20)
        self.assertEqual(asv.delay_time, 500 + 20 * 1900 / 10)
        reopened = config_module.ASVSettings()
        self.assertEqual(reopened.pulse_inc, 10)
        self.assertEqual(reopened.pulse_width, 20)
        self.assertEqual(reopened.delay_time, asv.delay_time)

    def test_amp_params_roundtrip_and_isolation(self):
        """安培法页参数同机制持久化（此前该类完全不落盘），且与
        CV/ASV 各自独立文件互不影响。"""
        amp = config_module.AmpSettings(480000.0, None)
        amp.update_settings(650, 500.0)
        self.assertEqual(amp.pwm_period_value,
                         int(round(480000.0 / 500.0)))
        reopened = config_module.AmpSettings(480000.0, None)
        self.assertEqual(reopened.voltage, 650)
        self.assertEqual(reopened.sampling_rate, 500.0)
        # 采样率改变后 PWM 周期按新值重算（运行前下发的依据）
        self.assertEqual(reopened.pwm_period_value, amp.pwm_period_value)
        # 本用例的临时目录里只有 settings_amp.txt，另外两页不受影响
        self.assertTrue(os.path.exists(config_module._settings_path(
            config_module.AMP_SETTINGS_FILE)))
        self.assertFalse(os.path.exists(config_module._settings_path(
            config_module.ASV_SETTINGS_FILE)))
        self.assertFalse(os.path.exists(config_module._settings_path(
            config_module.SAVED_SETTINGS_FILE)))

    def test_app_prefs_roundtrip(self):
        """应用级偏好（语言/电流量程/开发者选项）持久化——此前全是
        内存态，重启即丢（与 ASV 参数事故同类）。"""
        app = config_module.AppSettings()
        app.update_settings(language='en', current_range_index=3,
                            data_save_type='Raw Counts', adc_channel=2)
        reopened = config_module.AppSettings()
        self.assertEqual(reopened.language, 'en')
        self.assertEqual(reopened.current_range_index, 3)
        self.assertEqual(reopened.data_save_type, 'Raw Counts')
        self.assertEqual(reopened.adc_channel, 2)

    def test_app_prefs_invalid_language_falls_back(self):
        """语言字段损坏/非法时回退默认中文，不崩溃。"""
        app_path = config_module._settings_path(
            config_module.APP_SETTINGS_FILE)
        with open(app_path, 'w') as f:
            f.write("language = fr\n")
        self.assertEqual(config_module.AppSettings().language, 'zh')


# ===== 回归：参数持久化全字段往返（泛化防护网） =====
class TestSettingsAllFieldsRoundtrip(unittest.TestCase):
    """对四个参数文件的每一个声明字段做"改值→保存→重载→逐一比对"。

    TestSettingsPersistence 锁的是历史事故的具体场景；本类是泛化网：
    以后任何页在 DEFAULTS 里新增参数、或基类的类型校验/保存逻辑回归，
    都会被逐字段自动抓住，不依赖有人记得为新字段手写用例。
    """

    # DEFAULTS 里的"派生字段"：加载后由源字段重算，不参与往返比对。
    # 目前只有 CV 的声明表混有历史派生键；ASV/Amp/App 的声明表
    # 全部是源字段（这正是新机制的设计要求）
    DERIVED_KEYS = {
        config_module.SAVED_SETTINGS_FILE: {
            'start_dac_value', 'end_dac_value', 'delay_time',
            'pwm_period_value', 'low_voltage', 'high_voltage'},
    }

    def setUp(self):
        self._saved_override = config_module.SETTINGS_DIR_OVERRIDE
        self.tmp = tempfile.mkdtemp(prefix="psoc_roundtrip_case_")
        config_module.SETTINGS_DIR_OVERRIDE = self.tmp

    def tearDown(self):
        config_module.SETTINGS_DIR_OVERRIDE = self._saved_override

    @staticmethod
    def _alternate(default, options):
        """按默认值类型构造一个"合法但必然不同"的值。"""
        if isinstance(default, bool):
            return not default
        if isinstance(default, int):
            return default + 1
        if isinstance(default, float):
            return default * 2 + 1.5
        if options:
            return options[1] if default == options[0] else options[0]
        return str(default) + "_x"

    def _check(self, cls, args, filename):
        derived = self.DERIVED_KEYS.get(filename, set())
        options = getattr(cls, 'STRING_OPTIONS', {})
        mutated = cls(*args)
        for key in cls.DEFAULTS:
            if key in derived:
                continue
            setattr(mutated, key,
                    self._alternate(cls.DEFAULTS[key], options.get(key)))
        mutated.save_settings()
        reloaded = cls(*args)
        for key in cls.DEFAULTS:
            if key in derived:
                continue
            self.assertEqual(
                getattr(reloaded, key), getattr(mutated, key),
                msg="{0} 字段 {1} 往返不一致".format(filename, key))

    def test_cv_all_fields(self):
        self._check(config_module.CVSettings,
                    (config_module.DAC("DVDAC"),),
                    config_module.SAVED_SETTINGS_FILE)

    def test_asv_all_fields(self):
        self._check(config_module.ASVSettings, (),
                    config_module.ASV_SETTINGS_FILE)

    def test_amp_all_fields(self):
        self._check(config_module.AmpSettings, (480000.0, None),
                    config_module.AMP_SETTINGS_FILE)

    def test_app_all_fields(self):
        self._check(config_module.AppSettings, (),
                    config_module.APP_SETTINGS_FILE)


# ===== 整机会话点遍：真窗口 + 假设备，模拟用户一次完整实验 =====
class TestGuiSessionWalkthrough(unittest.TestCase):
    """同一会话内连续模拟用户操作：改参数（对话框真实保存路径）→
    六个页面逐一真跑扫描 → 每页存 CSV → 方法文件存/载 → 数据分析
    菜单真调 → 主题/语言/开发者选项切换。

    与零散端到端测试的区别：专抓"单功能各自正常、连起来才出错"的
    组合问题。所有弹窗统一打桩，全程出现 showerror 即失败。
    """

    @classmethod
    def setUpClass(cls):
        import main as main_mod
        import comm as comm_module
        from fake_device import FakeSerialComm
        cls.main_mod = main_mod
        with mock.patch.object(comm_module, 'SerialComm',
                               return_value=FakeSerialComm()), \
             mock.patch.object(comm_module.AmpUsb, 'calibrate'):
            cls.app = main_mod.ElectroChemGUI()
        cls.app.withdraw()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        _safe_destroy(cls.app)

    def _wait_until(self, cond, timeout=25.0, message=''):
        """泵 tk 事件循环直到条件满足；超时即失败。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.update()
            if cond():
                return
            time.sleep(0.02)
        self.fail('等待超时: ' + message)

    def _temp_path(self, suffix):
        path = tempfile.mktemp(suffix=suffix)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def _save_page(self, frame, path):
        fh = open(path, 'w', newline='')
        try:
            with mock.patch('experiments.open_file', return_value=fh):
                frame.save_all_data()
        finally:
            fh.close()

    def _popups(self):
        """打桩全部弹窗（showerror/showwarning/showinfo），返回三个 patch。"""
        import tkinter as tk
        return (mock.patch.object(tk.messagebox, 'showerror'),
                mock.patch.object(tk.messagebox, 'showwarning'),
                mock.patch.object(tk.messagebox, 'showinfo'))

    def _close_popups(self):
        """销毁残留 Toplevel（分析/关于等对话框），避免影响后续步骤。"""
        import tkinter as tk
        for child in self.app.winfo_children():
            if isinstance(child, tk.Toplevel) and child is not self.app:
                child.destroy()
        self.app.update()

    def test_01_scans_save_open_roundtrip(self):
        app = self.app
        p_err, p_warn, p_info = self._popups()
        with p_err as err, p_warn, p_info:
            self._session_scans(err)
        err.assert_not_called()
        self._close_popups()

    def _session_scans(self, err):
        app = self.app
        # ---- 1. 压缩时间参数（走真实保存路径，顺带验证持久化写入） ----
        cv = app.device_params.cv_settings
        cv.update_settings(-100, 100, 5.0, 'CV', 'Start', 100, 5, 100, False)
        asv = app.device_params.asv_settings
        asv.update_settings(800, 0.05, -1400, 0.05, 500, 10.0,
                            rest_time=0.05, strip_start=-1400)

        # ---- 2. 四个扫描页逐一：点运行 → 等自然结束 → 存 CSV → 核对 ----
        for tag, frame in (('CV', app.cv), ('LS', app.ls), ('SWV', app.swv),
                           ('DPV', app.dpv)):
            with self.subTest(page=tag):
                app.notebook.select(frame)
                app.update()
                handler = frame.device
                frame._run_scan()
                self._wait_until(lambda f=frame: f.data.index >= 1,
                                 message=f'{tag} 扫描未产生数据')
                self._wait_until(
                    lambda h=handler: not getattr(h, 'running', False),
                    message=f'{tag} 扫描未结束')
                path = self._temp_path('.csv')
                self._save_page(frame, path)
                from utils import load_csv_file_full
                _meta, _header, rows = load_csv_file_full(path)
                self.assertTrue(len(rows) > 0, f'{tag} 存盘无数据行')
                self.assertEqual(_meta.get('experiment'), tag,
                                 f'{tag} 存盘元数据页标记不符')
                # 文件导入回环：删除当前页 → 重新打开 → 曲线回来且自动跳页
                app.delete_current_page_data()
                app.update()
                self.assertEqual(frame.data.index, 0, f'{tag} 删除后应清空')
                with mock.patch('main.open_file', return_value=path):
                    app.open_data()
                app.update()
                self.assertIs(app.nametowidget(app.notebook.select()), frame,
                              f'打开 {tag} 文件应跳到 {tag} 页')
                self.assertGreaterEqual(len(frame.graph.plotted_lines), 1,
                                        f'{tag} 重新打开后应有曲线')

        # ---- 2b. ASV 三种溶出方式分别真跑：LS / DPV / SWV ----
        # DPV/SWV 用大步进+短脉冲压缩时长：delay = 500 + 20*1900/50 ≈ 1.3 s
        for sweep_code, tag in ((0, 'LS'), (1, 'DPV'), (2, 'SWV')):
            with self.subTest(asv_mode=tag):
                asv.update_settings(800, 0.05, -1400, 0.05, 500, 10.0,
                                    sweep_type=sweep_code,
                                    pulse_height=100, pulse_inc=50,
                                    pulse_width=20,
                                    rest_time=0.05, strip_start=-1400)
                app.notebook.select(app.asv)
                app.update()
                handler = app.asv.device
                handler.asv_run(app.asv.graph, app.asv.run_button)
                self._wait_until(lambda: app.asv.data.index >= 1,
                                 message=f'ASV-{tag} 扫描未产生数据')
                self._wait_until(
                    lambda: not getattr(handler, 'running', False),
                    message=f'ASV-{tag} 扫描未结束')
                path = self._temp_path('.csv')
                self._save_page(app.asv, path)
                from utils import load_csv_file_full
                _meta, _header, rows = load_csv_file_full(path)
                self.assertTrue(len(rows) > 0, f'ASV-{tag} 存盘无数据行')
                self.assertEqual(_meta.get('experiment'), 'ASV')
                self.assertEqual(_meta.get('sweep_type'), tag,
                                 f'ASV-{tag} 存盘头溶出方式不符')

        # ---- 3. 计时电流页：运行→取到数据→停止 ----
        app.notebook.select(app.amp)
        app.update()
        app.amp.toggle_amp_run(app.amp.device, app.amp.graph)
        self._wait_until(lambda: len(app.amp.device.time) > 20,
                         message='计时电流未取到数据')
        app.amp.toggle_amp_run(app.amp.device, app.amp.graph)
        self._wait_until(lambda: not getattr(app.amp, 'running', False),
                         message='计时电流未停止')

    def test_02_dialogs_analysis_and_export(self):
        app = self.app
        p_err, p_warn, p_info = self._popups()
        with p_err as err, p_warn, p_info:
            # ---- ASV 设置对话框：真实打开→改清洗电压→保存→参数与文件一致 ----
            from dialogs import (ASVSettingChanges, About, CalibrationDialog,
                                 ChangeCompareValue, ChangeDataLegend,
                                 EnterLoggingInfo, ShowLookupTable)
            dlg = ASVSettingChanges(app.asv, app, app.asv.graph, app.asv.device)
            app.update()
            dlg.clean_volt.set(700)
            dlg.save()
            app.update()
            self.assertEqual(app.device_params.asv_settings.clean_volt, 700)
            asv_txt = open(config_module._settings_path(
                config_module.ASV_SETTINGS_FILE), encoding='utf-8').read()
            self.assertIn('clean_volt = 700', asv_txt)

            # ---- 数据分析（注入带峰合成曲线后真调算法） ----
            import math
            app.notebook.select(app.cv)
            app.update()
            v_values = [float(v) for v in range(-1000, 201, 5)]
            c_values = [0.5 + 2.5 * math.exp(-((v + 300.0) ** 2)
                                             / (2 * 80.0 ** 2))
                        for v in v_values]
            app.cv.graph.update_data(v_values, c_values, label='synthetic')
            app.update()
            app.run_page_method('apply_smooth')
            app.run_page_method('apply_baseline')
            app.run_page_method('find_peaks_now')
            app.run_page_method('restore_original')
            app.run_page_method('toggle_region_select')
            app.run_page_method('toggle_region_select')

            # ---- 寻峰结果导出 CSV（打桩"另存为"给临时路径） ----
            export_path = self._temp_path('.csv')
            with mock.patch('experiments.open_file',
                            return_value=export_path):
                app.run_page_method('export_peaks')
            self.assertTrue(os.path.exists(export_path), '寻峰导出未生成文件')

            # ---- 多曲线运算对话框（真打开，统一收尾关闭） ----
            app.run_page_method('curve_math')

            # ---- 其余对话框逐个真打开 ----
            app.change_data_labels()
            EnterLoggingInfo(app)
            About(app)
            CalibrationDialog(app)
            ShowLookupTable(app)
            ChangeCompareValue(app)
            self._close_popups()

            # ---- 选项菜单处理器：平滑窗口/用户标注/电极制式/电压源 ----
            self.main_mod.set_smoothing_value(app, 3)
            self.assertEqual(app.device.samples_to_smooth, 3)
            self.main_mod.set_user_label_option(app, True)
            self.assertTrue(app.cv.graph.user_sets_labels_after_run)
            app.device.set_electrode_config(2)
            app.device.set_electrode_config(3)
            self.main_mod.set_voltage_source(app, "vsource_dvdac")

            # ---- 开发者菜单：电流符号开关（切过去再切回来） ----
            self.main_mod.set_current_sign(True)
            self.assertTrue(config_module.INVERT_CURRENT_SIGN)
            self.main_mod.set_current_sign(False)
            self.assertFalse(config_module.INVERT_CURRENT_SIGN)
        err.assert_not_called()
        self._close_popups()

    def test_03_method_file_prefs_and_cleanup(self):
        app = self.app
        p_err, p_warn, p_info = self._popups()
        with p_err as err, p_warn, p_info:
            # ---- 方法文件：存→改乱→载回→参数还原 ----
            asv = app.device_params.asv_settings
            method_path = self._temp_path('.json')
            app.save_method_file(path=method_path)
            self.assertTrue(os.path.exists(method_path))
            expected = asv.plate_time
            asv.update_settings(800, 0.05, -1400, 9.0, 500, 10.0,
                                rest_time=0.05, strip_start=-1400)
            app.load_method_file(path=method_path)
            self.assertEqual(app.device_params.asv_settings.plate_time,
                             expected)

            # ---- 主题 / 语言 ----
            app.set_theme('darkly')
            self.assertEqual(app.style.theme_use(), 'darkly')
            app.set_theme('cosmo')
            self.main_mod.set_language_and_refresh(app, 'en')
            self.main_mod.set_language_and_refresh(app, 'zh')

            # ---- 开发者选项持久化 ----
            app.set_data_type('Raw Counts')
            app.set_adc_channel(2)
            self.assertEqual(app.data_save_type, 'Raw Counts')
            self.assertEqual(app.device_params.adc_tia.adc_channel, 2)
            prefs = config_module.AppSettings()
            self.assertEqual(prefs.language, 'zh')
            self.assertEqual(prefs.data_save_type, 'Raw Counts')
            self.assertEqual(prefs.adc_channel, 2)

            # ---- 收尾：删除全部页面数据（模拟用户清场） ----
            app.delete_all_data()
            app.update()
            for frame in (app.cv, app.ls, app.swv, app.dpv, app.amp, app.asv):
                data = getattr(frame, 'data', None)
                # AmpFrame.data 是普通 list；只有带整数 index 的采集对象
                # （SavedData）才用 index 计数曲线
                if data is not None and isinstance(getattr(data, 'index', None),
                                                   int):
                    self.assertEqual(data.index, 0, '清场后页面数据应清空')
        err.assert_not_called()
        self._close_popups()


# ===== 回归：搅拌实时状态显示（静置期间不得再显示"开"） =====
class TestStirringLiveIndicator(unittest.TestCase):
    """真机踩坑：右侧参数栏"自动搅拌"显示的是配置项而非继电器实时
    状态——静置/溶出期间搅拌已停仍显示"开"，用户以为搅拌没停。
    修复后面板跟随继电器实时状态，倒计时窗文案标注各阶段搅拌状态，
    且显示阶段由真实流程切换点强制同步（消除独立秒表累计偏差）。"""

    def test_panel_shows_live_state_during_rest(self):
        import main as main_mod
        import comm as comm_module
        from fake_device import FakeSerialComm
        from i18n import get_text
        with mock.patch.object(comm_module, 'SerialComm',
                               return_value=FakeSerialComm()), \
             mock.patch.object(comm_module.AmpUsb, 'calibrate'):
            app = main_mod.ElectroChemGUI()
        try:
            app.withdraw()
            app.update()
            asv = app.device_params.asv_settings
            # 富集 0.3 s + 静置 2 s：静置窗口内足以观察到"关"+窗内文案
            asv.update_settings(800, 0.2, -1400, 0.3, 500, 10.0,
                                rest_time=2.0, strip_start=-1400)
            var = app.asv.cv_settings_frame.stirring_var_str
            handler = app.asv.device
            on_text = get_text("asv_stirring_on")
            off_text = get_text("asv_stirring_off")
            handler.asv_run(app.asv.graph, app.asv.run_button)

            def pump_until(cond, timeout):
                deadline = time.time() + timeout
                while time.time() < deadline:
                    app.update()
                    if cond():
                        return True
                    time.sleep(0.02)
                return False

            # 清洗/富集阶段：继电器开 → 面板"开"
            self.assertTrue(pump_until(lambda: var.get() == on_text, 5),
                            '运行启动后面板应显示搅拌"开"')
            # 静置开始：继电器关 → 面板"关"
            self.assertTrue(pump_until(lambda: var.get() == off_text, 8),
                            '静置开始后面板应显示搅拌"关"')
            # 倒计时窗已切到静置段，且文案明确标注"搅拌已停止"
            tw = getattr(handler, 'timer_window', None)
            if tw is not None and tw.winfo_exists():
                self.assertIn('搅拌已停止', tw.toplabel.cget('text'),
                              '静置段倒计时文案未标注搅拌状态')
            # 等溶出自然结束：面板保持"关"（继电器确实未再吸合）
            self.assertTrue(pump_until(
                lambda: getattr(handler, 'running', False) is False
                and app.asv.data.index >= 1, 20),
                'ASV 流程未自然结束')
            self.assertEqual(var.get(), off_text)
        finally:
            _safe_destroy(app)
            from ttkbootstrap.style.engine import Style
            Style.instance = None
