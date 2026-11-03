# Copyright (c) 2015-2023 Kyle Lopin (Naresuan University) <kylel@nu.ac.th>
# Licensed under the Creative Commons Attribution-ShareAlike 3.0 (CC BY-SA 3.0 US) License
# Modified by Zhijian Yi (Guangdong, China), 2026 — PSoC-Potentiostat-YI-1.0

"""USB and serial communication with the PSoC potentiostat device."""

import logging
import os
import struct
import time

import serial
import serial.tools.list_ports

import config as _config
from config import (TIA_RESISTOR_VALUES, CURRENT_OPTION_LIST,
                    CURRENT_LIMIT_VALUES)
from i18n import get_text

USB_IN_BYTE_SIZE = 64
USB_TERMINATION_SIGNAL = 255 * 257
USB_OUT_BYTE_SIZE = 32
TEST_MESSAGE = "USB Test"
TEST_MESSAGES = {
    "USB Test": "base",
    "USB Test - 059": "kit-059",
    "USB Test - v04": "v04",
    b"Naresuan Potentiostat": "v1.5"
}
PRODUCT_STRING = "Naresuan Potentiostat"
RUNNING_DELAY = 3000
FAIL_COUNT_THRESHOLD = 2
FAILURE_DELAY = 500
COMPLETE_MESSAGE = "Done"
TERMINATION_CODE = -16384

USB_VENDOR_ID = 0x04B4
USB_PRODUCT_ID = 0xF232
BAUD_RATE = 115200
MAX_TIA_SETTING = 7


class AmpUsb(object):
    def __init__(self, _master, _device_params, vendor_id=USB_VENDOR_ID, product_id=USB_PRODUCT_ID):
        self.connected = False
        self.device_params = _device_params
        self.master = _master
        self.device_type = None
        self.last_experiment = "CV"
        self.samples_to_smooth = 1
        logging.info("attempting connection")
        self.device = SerialComm()
        if self.device.connected:
            self.connected = True
            print("Initializing run parameters")
            logging.info("Initializing run parameters")
            self.find_voltage_source()
            time.sleep(0.5)
            self.usb_write("A|1|0|0|F|2")
            self.calibrate()
            try:
                # P2.0 归零：上次会话异常退出会残留高电平，
                # ZK-TD2（P13/P04）需要干净的低→高变化才会触发
                self.set_stirring(False)
            except Exception:
                logging.debug("stirring reset on connect skipped", exc_info=True)

    def connection_test(self, fails=0):
        self.device.clear_in_buffer()
        self.device.connected = True
        self.usb_write("I")
        received_message = self.usb_read_message()
        if received_message in TEST_MESSAGES:
            self.set_device_type(TEST_MESSAGES[received_message])
            logging.info("Device type %s selected", self.device_type)
            return True
        else:
            logging.info("test failed")
            fails = fails + 1
            if fails > 4:
                self.device_type = None
                self.master.failed_connection()
                self.attempt_reconnection()
                return False
            else:
                return self.connection_test(fails)

    def set_device_type(self, _device_type):
        self.device_type = _device_type

    def find_voltage_source(self):
        self.usb_write("VR")
        time.sleep(0.2)
        source_input = self.usb_read_data(2)
        print(f"source input: {source_input}")
        if not source_input:
            return
        if source_input[1] == 0:
            from dialogs import VoltageSourceSelect
            VoltageSourceSelect(self.master, source_input[1])
        elif source_input[1] == 1:
            logging.info("VDAC is set in device")
            self.master.voltage_source_key = "vsource_vdac"
            self.master.set_voltage_source_label(get_text("vsource_vdac"))
            self.device_params.dac.set_source("8-bit DAC")
        elif source_input[1] == 2:
            logging.info("DVDAC is voltage source")
            self.master.voltage_source_key = "vsource_dvdac"
            self.master.set_voltage_source_label(get_text("vsource_dvdac"))
            self.device_params.dac.set_source("DVDAC")
        else:
            raise IOError

    def select_voltage_source(self, source):
        logging.info('selecting source: {0}'.format(source))
        if source == "8-bit DAC":
            self.usb_write("VS1")
            self.master.voltage_source_key = "vsource_vdac"
            self.master.set_voltage_source_label(get_text("vsource_vdac"))
            self.device_params.dac.set_source(source)
            self.send_cv_parameters()
        elif source == "DVDAC":
            self.usb_write("VS2")
            self.device_params.dac.set_source("DVDAC")
            self.master.voltage_source_key = "vsource_dvdac"
            self.master.set_voltage_source_label(get_text("vsource_dvdac"))
            self.send_cv_parameters()

    def start_hardware(self):
        self.usb_write('H')

    def send_cv_parameters(self):
        self.master.cv.device.send_cv_parameters()

    def write_timer_compare(self, value):
        self.master.device_params.PWM_compare = int(value)
        _formatted_value = '{0:05d}'.format(int(value))
        self.usb_write('C|' + _formatted_value)

    def get_data(self, number_packets=None):
        full_array = []
        if not number_packets:
            number_packets = ((self.device_params.usb_count + 1) / (USB_IN_BYTE_SIZE / 2) - 1)
        count = 0
        while number_packets + 1 > count:
            try:
                data_array = self.device.read_data(USB_IN_BYTE_SIZE, 'int16')
                print(f"got usb data: {data_array}, asked for size: {USB_IN_BYTE_SIZE}")
                full_array.extend(data_array)
                if TERMINATION_CODE in data_array:
                    # 串口没有包边界：终止码可能落在某个 64 字节块的中间，
                    # 其后字节（下一条消息/下一段数据）已被一并读入。
                    # 必须在终止码位置精确截断，只删最后一个元素会把
                    # 终止码和垃圾尾全留在数据里（真机计时电流踩过）
                    full_array = full_array[:full_array.index(TERMINATION_CODE)]
                    break
                count += 1
            except Exception as _error:
                print(f"Got error reading data: {_error}")
        # 平滑必须作用于完整数据：原实现在读完第一个包后就对单包
        # 求均值返回，开了平滑的所有页面拿到的都是错误数据
        if self.samples_to_smooth > 1 and full_array:
            return rolling_mean(full_array, self.samples_to_smooth)
        return full_array

    def get_lut_length(self):
        """探测固件当前波形表长度（'g' 命令，固件回 2 字节 uint16）。

        CV 多圈用它判断固件是否支持 LUT 循环：发完 'S|...|CS|N' 后回读，
        长度 ≈ N 圈期望值 → 固件多圈模式；≈ 1 圈 → 旧固件，GUI 逐圈调度。
        """
        self.usb_write('g')
        time.sleep(0.05)
        data = self.usb_read_data(2, encoding='int16')
        if not data:
            return None
        return int(data[0])

    def process_data(self, _raw_data, swv=False, ascending=None):
        """原始 ADC 计数 -> 电流（µA）。

        swv=True 时按 [脉冲, 基底] 成对差分。固件 LUT 把脉冲放在
        value+height（DAC 计数）——DAC 与电极电位反相（dac = 2048 − V），
        即第一个采样在电极电位的"低侧"。差分方向必须跟着扫描方向走：

          ascending=True （电位上行，如 ASV 溶出）：Δi = i(E+h) − i(E−h)
          ascending=False（电位下行）            ：Δi = i(E−h) − i(E+h)

        否则上行扫描的阳极峰会被画成向下的负峰（真机 ASV 踩过）。
        旧调用不传 ascending 时保持上游历史行为（前减后）。
        """
        if self.master.data_save_type == "Converted":
            logging.debug("Converting data")
            count_to_current = self.device_params.adc_tia.counts_to_current
            shift = self.device_params.adc_tia.shift
            logging.debug("count to current: %4.4f", count_to_current)
            sign = -1.0 if _config.INVERT_CURRENT_SIGN else 1.0
            current = [(x - shift) * count_to_current * sign for x in _raw_data]
            if swv:
                # SWV/DPV 数据正/反向成对：奇数个采样说明末尾落单，丢弃最后一个
                if len(current) % 2:
                    logging.warning("swv data has odd length %d, dropping last point",
                                    len(current))
                    current = current[:-1]
                # ascending=None: 兼容旧调用（前减后）；显式给方向时按
                # 扫描方向取差分（上行: 后减前 / 下行: 前减后）
                if ascending is None:
                    forward_first = True
                else:
                    forward_first = not ascending
                swv_data = []
                _index = 0
                while _index < len(current):
                    if forward_first:
                        swv_data.append(current[_index] - current[_index + 1])
                    else:
                        swv_data.append(current[_index + 1] - current[_index])
                    _index += 2
                return swv_data
            return current
        elif self.master.data_save_type == "Raw Counts":
            logging.debug("sending raw data back")
            return _raw_data
        return _raw_data

    def reset(self):
        self.usb_write('X')

    def calibrate(self):
        print(f"calibrating data, connected: {self.connected}")
        if self.connected:
            # 固件 'X' 复位会让硬件进 Sleep 且断电前一直保持（真机实测：
            # 上次会话以 X 收尾后，本次标定 ADC 不转换、回五个 -4）。
            # 标定前先 'H' 唤醒，已醒着时无副作用
            self.usb_write('H')
            time.sleep(0.3)
            self.usb_write('B')
            time.sleep(2)
            self._calibrate_data()
            print("done setting after")
            logging.debug("running calibration")

    def _calibrate_data(self):
        # 设备偶发回传无效标定数据：唤醒后重试一次；仍失败则用理论系数兜底，
        # 保证 GUI 能启动（电流读数精度略降，标记 calibrate_failed）。
        # 注意：重试绝不能用 'X' 复位——X 让固件硬件进 Sleep 且断电前
        # 不恢复，'H' 唤不醒（真机两次实测实锤），X 后重试必然再失败
        for attempt in range(2):
            if attempt > 0:
                logging.warning("calibration data invalid, wake device and retry")
                print("标定数据无效，唤醒设备后重试…")
                self.usb_write('H')
                time.sleep(1)
                self.usb_write('B')
                time.sleep(2)
            raw_data = self.usb_read_data(20, encoding='int16')
            print(f"Calibration data: {raw_data}")
            try:
                self.device_params.adc_tia.calibrate(raw_data)
                self.device_params.adc_tia.calibrate_failed = False
                return
            except (ValueError, ZeroDivisionError, TypeError) as err:
                logging.warning("calibration attempt %d failed: %s", attempt, err)
        logging.error("calibration failed after retry; using theoretical coefficients")
        print("警告：标定失败，已使用理论换算系数（电流读数可能有偏差）")
        self.device_params.adc_tia.calibrate_failed = True

    def get_export_channel(self, channel=None):
        canvas = self.master.preview_graph
        print("TODO update this")

    def get_look_up_table(self):
        self.usb_write('l|1000')
        look_up_table = self.usb_read_data(2000, encoding='int16')
        logging.debug("look up table: %s", look_up_table)
        return look_up_table

    def set_electrode_config(self, num_electrodes):
        self.usb_write('L|' + str(num_electrodes))
        self.master.num_electrodes = num_electrodes
        self.master.electrode_config_label.set(
            get_text("electrode_config_msg").format(num_electrodes))

    def set_anode_voltage(self, voltage):
        logging.debug("setting anode voltage to {0} mV".format(voltage))
        formatted_voltage_to_send = self.device_params.dac.get_dac_count(voltage, actual=True)
        self.usb_write("D|{0:04d}".format(formatted_voltage_to_send))

    def short_tia_resistor(self):
        logging.debug("Shorting TIA resistor")
        self.usb_write('s')

    def stop_shorting_tia_resistor(self):
        logging.debug("Stop shorting tia resistor")
        self.usb_write('d')

    def set_stirring(self, on):
        """搅拌联动：通过 P2.0 GPIO 控制 ZK-TD2 继电器（方案 A）。

        发送 'O|1' 开启 / 'O|0' 关闭。
        注意：'O' 命令在当前官方固件（Amperometry_v059_2）中尚未实现，
        需要固件侧在 main.c 的命令 switch 中新增：
            case 'O': Stir_Pin_Write(OUT_Data_Buffer[2] - '0'); break;
        详见升级方案文档 B.2 节。
        """
        logging.debug("stirring %s", "on" if on else "off")
        self.usb_write("O|{0}".format(1 if on else 0))

    def set_custom_resistor_channel(self, channel):
        self.usb_write("A|2|7|0|T|" + channel)

    def set_adc_tia(self, current_range_index):
        adc_config, tia_position, adc_gain = get_tia_settings(current_range_index)
        logging.debug('setting tia/adc to position: %s, gain: %s, config: %s',
                      tia_position, adc_gain, adc_config)
        self.usb_write("A|{0}|{1}|{2}|F|0".format(adc_config, tia_position, adc_gain))
        self.device_params.adc_tia.set_value(TIA_RESISTOR_VALUES[tia_position],
                                             adc_gain, adc_config, current_range_index)
        logging.debug("TIA resistor changed to: %s", self.device_params.adc_tia.tia_resistor)
        current_limit = CURRENT_LIMIT_VALUES[current_range_index]
        self.master.update_current_range(CURRENT_OPTION_LIST[current_range_index], current_limit)
        self.calibrate()

    def format_divider(self, _sweep_rate):
        clk_freq = self.device_params.clk_freq_isr_pwm
        raw_divider = int(round(
            clk_freq / (_sweep_rate * 1000 / self.device_params.dac.voltage_step_size)
        ) - 1)
        return '{0:05d}'.format(raw_divider), raw_divider

    def format_voltage(self, _in_volts):
        # 与 experiments.format_voltage_with_gnd 同一约定：
        # DAC 计数 = 虚地 − 目标电极电位（DAC 与电极电位反相）。
        # 此前这里写成了 "虚地 + 电位"，hardware_tests 用它换算扫描
        # 起止电位时方向整体写反（+100 mV 会扫到 −100 mV）
        input_voltage = self.device_params.virtual_ground_shift - _in_volts
        dac_value = self.device_params.dac.get_dac_count(input_voltage)
        return '{0:04d}'.format(dac_value), dac_value

    def usb_write(self, message):
        if not self.device.connected:
            logging.info("Device not connected")
            self.master.failed_connection()
        else:
            logging.debug("writing message: %s", message)
            try:
                self.device.write_data(message)
            except Exception as error:
                print(f"Error in writing to device: {error}")
                logging.debug(f"Error in writing to device: {error}")
                self.connection_test()

    def clear_in_buffer(self):
        """清输入缓冲：底层设备支持就清，不支持则静默跳过。
        （停止计时电流时调用；AmpUsb 此前没有此方法，停止时会 AttributeError）"""
        clearer = getattr(self.device, 'clear_in_buffer', None)
        if callable(clearer):
            clearer()

    def usb_read_data(self, _size: int = USB_IN_BYTE_SIZE, encoding: str = None) -> list:
        if not self.connected:
            logging.info("not working")
            return None
        try:
            if encoding == 'str':
                # 消息读取必须非阻塞：pyserial read(64) 会等满 timeout(1s)，
                # 扫描/计时电流的轮询每次调用都冻住主线程 1 秒（界面卡顿、
                # 曲线要停止后才刷新的根因）。先看缓冲里实际有多少字节，
                # 没有就立即返回 None，有就只读实际存在的字节数
                waiting = self.device.bytes_waiting()
                if waiting <= 0:
                    return None
                _size = min(waiting, _size)
            return self.device.read_data(_size, encoding=encoding)
        except Exception as error:
            logging.error("Failed read")
            self.connection_test()
            return None

    def usb_read_message(self, _size=USB_IN_BYTE_SIZE):
        chunk = self.usb_read_data(_size, encoding='str') or ''
        buffer = getattr(self, '_msg_prefix_buffer', '') + chunk
        if not buffer:
            return None
        if COMPLETE_MESSAGE in buffer:
            # 完整完成消息到手，清空暂存
            self._msg_prefix_buffer = ''
            print(f"usb read message: {buffer}")
            return buffer
        if COMPLETE_MESSAGE.startswith(buffer):
            # 'Done' 被 USB 拆成两包（真机发生过：扫完却报 scan timed out）：
            # 暂存这段前缀等下一包，否则前缀被丢弃、轮询永远等不到完成消息
            self._msg_prefix_buffer = buffer
            return None
        self._msg_prefix_buffer = ''
        print(f"usb read message: {buffer}")
        return buffer

    def attempt_reconnection(self):
        logging.debug("usb_comm reconnection protocol")
        self.usb_read_data()
        self.serial_connect()

    def set_last_run(self, run_type):
        self.last_experiment = run_type

    def serial_connect(self):
        """Attempt to reconnect via serial."""
        self.device = SerialComm()
        if self.device.connected:
            self.connected = True


class SerialComm:
    def __init__(self):
        self.connected = False
        self.found = False
        self.device = self.auto_find_com_port()
        print(f"Done initializing SerialComm with state: {self.connected}")

    def auto_find_com_port(self):
        available_ports = serial.tools.list_ports
        for port in available_ports.comports():
            if USB_VENDOR_ID == port.vid and USB_PRODUCT_ID == port.pid:
                print("found device")
                self.found = True
                try:
                    device = serial.Serial(port.device, BAUD_RATE, timeout=1.0)
                except serial.SerialException as error:
                    # 端口被占用（上一个进程没退干净/串口监视器开着）等
                    # 情况不该让整个程序崩溃：记下来，继续试下一个端口
                    logging.warning("端口 %s 打不开（%s），跳过", port.device, error)
                    print(f"port {port.device} busy or unavailable: {error}, skipping")
                    continue
                print("made serial")
                device.write(b"I")
                for i in range(3):
                    _input = device.readline()
                    if b"Naresuan Potentiostat" in _input:
                        self.connected = True
                        print("got device")
                        return device
                # 握手没成功：关掉句柄再继续，避免我们自己占着端口
                device.close()
        if self.found and not self.connected:
            logging.warning("找到了设备端口但未能建立连接（被占用或无应答）")
        return None

    def clear_in_buffer(self):
        if self.device:
            self.device.reset_input_buffer()

    def bytes_waiting(self):
        """串口输入缓冲中当前可读的字节数（非阻塞查询）。"""
        if not self.device:
            return 0
        return self.device.in_waiting

    def read_data(self, data_length, encoding=None):
        data = self.device.read(data_length)
        print(f"read byte data: {data}, lenght asked for: {data_length}")
        if encoding == 'int16':
            size = int(len(data) / 2)
            converted_data = struct.unpack(f"{size}h", data)
            return list(converted_data)
        elif encoding == 'str':
            return data.decode("utf-8")
        elif encoding:
            raise Exception(f"Encoding: '{encoding}' not supported")
        return data

    def write_data(self, message):
        if self.connected:
            if type(message) is str:
                message = message.encode('utf-8')
            print(f"writing data: {message}")
            self.device.write(message)

    def poll_for_data(self):
        if self.device.in_waiting:
            return self.device.read_all()


def get_tia_settings(range_selected):
    if range_selected == 0:
        return 1, 0, 0
    adc_config = 2
    range_selected -= 1
    if range_selected > MAX_TIA_SETTING:
        adc_gain_setting = range_selected % MAX_TIA_SETTING
        tia_position = MAX_TIA_SETTING
    else:
        tia_position = range_selected
        adc_gain_setting = 0
    return adc_config, tia_position, adc_gain_setting


def rolling_mean(array, number_points):
    """因果滑动均值，输出与输入等长。

    窗口不足时（序列开头）用已有点的平均，全程平滑强度一致。
    旧实现把前 number_points-1 个点原样透传（未平滑），段式采集
    （计时电流每段 200~500 点）时每段开头混入一截生数据，段边界
    出现台阶状毛刺——用户报告的计时电流"重复点"即此。"""
    new_array = []
    for i in range(len(array)):
        lo = max(0, i - number_points + 1)
        new_array.append(float(sum(array[lo:i + 1])) / (i - lo + 1))
    return new_array

