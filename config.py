# Copyright (c) 2015-2023 Kyle Lopin (Naresuan University) <kylel@nu.ac.th>
# Licensed under the Creative Commons Attribution-ShareAlike 3.0 (CC BY-SA 3.0 US) License
# Modified by Zhijian Yi (Guangdong, China), 2026 — PSoC-Potentiostat-YI-1.0

"""Configuration and device parameter classes."""

import csv
import logging
import os

from i18n import get_text

# ======================================================================
# Globals
# ======================================================================

CURRENT_OPTION_LIST = [
    u'±100 µA', u'±50 µA', u'±33 µA',
    u'±25 µA', u'±12.5 µA', u'±8 µA',
    u'±4 µA', u'±2 µA', u'±1 µA',
    u'±0.5 µA', u'±0.25 µA'
]

TIA_RESISTOR_VALUES = [20, 30, 40, 80, 120, 250, 500, 1000]
CURRENT_LIMIT_VALUES = [100, 50, 33, 25, 12.5, 8, 4, 2, 1, 0.5, 0.25]

ADC_VREF = 2048  # mV
ADC_BITS = 12  # bits
PWM_FREQ = 480000.  # Hz
VIRTUAL_GROUND = 2048.  # mV

# ======================================================================
# Graph Properties
# ======================================================================

class GraphProps(object):
    def __init__(self):
        self.amp_plot = {
            'xlabel': "'" + get_text("axis_time") + "'",
            'ylabel': "'" + get_text("axis_current") + "'",
            'xlim': "[0, 200]",
            'ylim': "[-.1,1]",
            'title': "'" + get_text("chart_amp_title") + "'",
            'subplots_adjust': "bottom=0.15, left=0.12"
        }
        self.cv_plot = {
            'xlabel': "'" + get_text("axis_voltage") + "'",
            'ylabel': "'" + get_text("axis_current") + "'",
            'title': "'" + get_text("chart_cv_title") + "'",
            'subplots_adjust': "bottom=0.15, left=0.12"
        }
        self.asv_plot = {
            'xlabel': "'" + get_text("axis_voltage") + "'",
            'ylabel': "'" + get_text("axis_current") + "'",
            'title': "'" + get_text("chart_asv_title") + "'",
            'subplots_adjust': "bottom=0.15, left=0.12"
        }

# ======================================================================
# Properties / Settings
# ======================================================================

DEFAULT_CV_SETTINGS = {
    'start_dac_value': 159, 'start_voltage': -900, 'end_voltage': 900,
    'sweep_start_type': 'Start', 'end_dac_value': 78,
    'sweep_type': 'CV', 'sweep_rate': 0.2,
    'delay_time': 1400.0, 'pwm_period_value': 3840,
    'swv_height': 100, 'swv_inc': 5, 'swv_period': 100,
    'use_swv': False,
    'cycles': 1,  # CV 循环圈数（仅 CV 有效；固件 LUT 或 GUI 循环调度实现）
    'quiet_time_s': 3.0,  # 扫描前静置秒数：先跳起始电位等暂态衰减（0=关闭）
    'simulate_mode': False,  # 模拟模式：无需硬件，用内置模拟器跑全套界面
    'theme': 'cosmo',  # 界面主题（选项菜单切换）：cosmo / darkly / superhero
}

GUI_THEMES = {'cosmo': "theme_cosmo", 'darkly': "theme_darkly",
              'superhero': "theme_superhero"}

# CV 最大循环圈数：圈数受固件波形表（LUT）容量限制，超出会截尾
MAX_CV_CYCLES = 9
# 固件 waveform_lut 容量（globals.h: MAX_LUT_SIZE）
FIRMWARE_LUT_SIZE = 5000

# ADC 采样饱和判据：原始计数达到满量程附近即为削顶（平头的根因）。
# 固件 ADC 数据左移 3 位存储（标定按 /8 还原），12 位满量程 ±2048 → ±16384；
# 终止码恰为 -16384，故负向饱和样本会被误当终止码提前截断，
# 两者都按饱和处理并提示用户换更大量程
ADC_SATURATION_COUNTS = 16200

# 电流符号约定：本代码库换算出的电流遵守 IUPAC 约定（氧化/阳极为正）。
# 个别焊装的模拟前端若把 TIA 输出接反，阳极峰会朝下——把此项改为 True
# 即可整体反相，无需改代码（开发者菜单亦有同功能开关）
INVERT_CURRENT_SIGN = False

SWEEP_TYPE_OPTIONS = ['CV', 'LS']
SWEEP_START_TYPE_OPTIONS = ['Start', 'Zero']

START_VOLTAGE_CV = -900.
END_VOLTAGE_CV = 900.
SWEEP_RATE = 0.5

# Amperometry properties
DEVICE_SAMPLING_RATE = 1000
DOWN_SAMPLING = 50

# Anode stripping voltammetry properties
CLEAN_VOLTAGE_ASV = 800  # mV
CLEAN_TIME = 2  # seconds
PLATING_VOLTAGE = -1500  # mV
PLATING_TIME = 5  # seconds
END_ASV_VOLTAGE = 500  # mV
REST_TIME = 15  # seconds，富集结束后、溶出扫描前的静置时间
STIRRING_ENABLED = True  # 富集（及清洗）阶段自动搅拌（P2.0 GPIO -> ZK-TD2 继电器）
CLEAN_ENABLED = True  # 溶出前清洗步骤；一次性丝网印刷电极可关闭
CLEAN_AFTER_STRIP = False  # 溶出结束后自动回到清洗电压
# DPV in ASV（与独立 DPV/SWV 页共享同一套默认值：swv_height/swv_inc/swv_period）
PULSE_HEIGHT = 100  # mV
PULSE_INCREMENT = 5  # mV
PULSE_WIDTH = 100  # ms，语义=每一步总时长（脉冲+基底各占一半），同 SWV 周期

# ======================================================================
# 参数文件：每页一个文件，互不共用。历史上 CV 与 ASV 共用同一个
# settings.txt 的同名键（start_voltage/end_voltage/sweep_rate…），
# 语义却不同（ASV 的 end_voltage 是溶出终止电位），互相覆盖，
# 加上 ASV 保存路径缺失，造成"ASV 参数改完重启就丢"。
# ======================================================================
SAVED_SETTINGS_FILE = "settings.txt"      # CV/LS/SWV/DPV 页
ASV_SETTINGS_FILE = "settings_asv.txt"    # ASV 溶出页
AMP_SETTINGS_FILE = "settings_amp.txt"    # 安培法页（暂未接入持久化）

# 参数文件目录锚定到代码所在目录：GUI 无论从哪个工作目录启动，
# 读写的都是同一份配置，不会把 settings*.txt 散落到启动目录
SETTINGS_DIR = os.path.dirname(os.path.abspath(__file__))
# 测试隔离开关：设为临时目录后，所有参数文件改在该目录读写
# （tests/test_refactored.py 导入时统一设置，测试绝不碰真实配置）
SETTINGS_DIR_OVERRIDE = None


def _settings_path(filename):
    """参数文件完整路径。"""
    return os.path.join(SETTINGS_DIR_OVERRIDE or SETTINGS_DIR, filename)


# 加载校验的"非法值"哨兵。绝不能用 False 当非法标记——否则合法的
# False 值（如 stirring_enabled = False）会被当成非法而永远读不回来
_INVALID = object()


class PersistedSettings(object):
    """声明式参数持久化基类。

    子类只需声明两个类属性：
      DEFAULTS      —— 全部可持久化参数 → 默认值（值的类型即加载校验规则）
      SETTINGS_FILE —— 本页参数文件名（各页各一个文件，杜绝互相覆盖）
    加载/保存/类型校验全部由基类完成：新增参数只要加进 DEFAULTS 就自动
    持久化，不存在"忘了在 load/save 白名单里补一行"这类回归。
    保存为原子写（先写 .tmp 再 os.replace），中途崩溃不会留下坏文件。
    """
    DEFAULTS = {}
    SETTINGS_FILE = None
    # 字符串型参数的合法取值表；未列出的字符串参数不做取值校验
    STRING_OPTIONS = {}

    def load_settings(self):
        """读参数文件；文件不存在或单行损坏时该参数回退默认值，绝不崩溃。"""
        for key, default in self.DEFAULTS.items():
            setattr(self, key, default)
        path = _settings_path(self.SETTINGS_FILE)
        try:
            with open(path, 'r') as _file:
                for line in _file.readlines():
                    if '=' not in line:
                        continue
                    attribute, value = line.split('=', 1)
                    attribute = attribute.strip(' ')
                    value = value.strip()
                    if attribute in self.DEFAULTS:
                        valid = self._validated(attribute, value)
                        if valid is not _INVALID:
                            setattr(self, attribute, valid)
        except FileNotFoundError:
            pass  # 首次运行尚无参数文件，全部用默认值
        except Exception as e:
            logging.debug("Load error in %s: %s", path, e)

    def _validated(self, attribute, value):
        """按 DEFAULTS 中默认值的类型转换并校验。

        合法时返回转换后的值——它可能恰为 False/0，调用方用 _INVALID
        哨兵区分"非法"，不能复用任何正常值域里的对象当标记；
        非法时返回 _INVALID，调用方回退默认值。
        """
        default = self.DEFAULTS[attribute]
        try:
            if isinstance(default, bool):
                return value == "True"
            if isinstance(default, int):
                try:
                    return int(value)
                except ValueError:
                    # 界面 DoubleVar 传来的整数可能是 "300.0" 这类浮点写法
                    as_float = float(value)
                    return int(as_float) if as_float.is_integer() else _INVALID
            if isinstance(default, float):
                return float(value)
        except (ValueError, TypeError):
            return _INVALID
        options = self.STRING_OPTIONS.get(attribute)
        if options and value not in options:
            return _INVALID
        return value

    def save_settings(self):
        """只写 DEFAULTS 声明的参数；原子替换，写一半失败也不会丢旧配置。"""
        path = _settings_path(self.SETTINGS_FILE)
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, 'w') as _file:
                for key in self.DEFAULTS:
                    _file.write("{0} = {1}\n".format(key, getattr(self, key)))
            os.replace(tmp_path, path)
        except Exception as e:
            logging.debug("exception in saving settings file: {0}".format(e))


class DeviceParameters(object):
    def __init__(self):
        self.number_in_endpoints = 1
        self.number_out_endpoints = 1
        self.clk_freq_isr_pwm = PWM_FREQ
        self.virtual_ground_shift = VIRTUAL_GROUND
        self.dac = DAC("DVDAC", voltage_range=4080)
        self.adc_tia = ADC_TIA()
        self.cv_settings = CVSettings(self.dac)
        self.amp_settings = AmpSettings(self.clk_freq_isr_pwm, self.dac)
        self.asv_settings = ASVSettings(self.clk_freq_isr_pwm, self.dac)
        self.app_settings = AppSettings()
        self.pwm_period_value = self.calculate_pwm_period()
        self.pwm_compare = int(self.pwm_period_value / 2)
        self.usb_count = 0
        self.user_sets_labels_after_run = True
        self.last_run = 'cv'
        # 固件 CV 多圈能力（None=未探测 / True=LUT 多圈 / False=旧固件逐圈）。
        # 由 experiments._probe_fw_multi_cycle 探测后缓存；设置对话框据此
        # 决定是否按 LUT 容量限制圈数（旧固件逐圈调度与容量无关）
        self.fw_cv_multi_cycle = None

    def calculate_pwm_period(self):
        voltage_step_size = self.dac.voltage_step_size
        pwm_period_value = int(round(
            self.clk_freq_isr_pwm /
            (self.cv_settings.sweep_rate * 1000 / voltage_step_size)
        ))
        return pwm_period_value

    def set_dac(self, _type):
        _voltage_range = self.dac.range
        self.dac = DAC(_type, _voltage_range)


class CVSettings(PersistedSettings):
    DEFAULTS = DEFAULT_CV_SETTINGS
    SETTINGS_FILE = SAVED_SETTINGS_FILE
    STRING_OPTIONS = {
        'sweep_type': SWEEP_TYPE_OPTIONS,
        'sweep_start_type': SWEEP_START_TYPE_OPTIONS,
        'theme': list(GUI_THEMES),
    }

    def __init__(self, dac):
        self.load_settings()
        self.delay_time = 2 * abs(self.start_voltage - self.end_voltage) / self.sweep_rate
        if self.use_swv:
            self.delay_time = (2 * self.swv_period *
                               abs(self.start_voltage - self.end_voltage) / self.swv_inc)
        self.low_voltage = min([self.start_voltage, self.end_voltage])
        self.high_voltage = max([self.start_voltage, self.end_voltage])
        self.start_dac_value = None
        self.end_dac_value = None
        self.calc_dac_values(dac)

    def calc_dac_values(self, dac):
        self.start_dac_value = dac.get_dac_count(self.start_voltage, shift=True)
        self.end_dac_value = dac.get_dac_count(self.end_voltage, shift=True)

    def calculate_pwm_period(self, clk_freq, dac):
        pwm_period_value = int(round(
            clk_freq / (self.sweep_rate * 1000 / dac.voltage_step_size)
        ))
        return pwm_period_value

    def update_settings(self, start_voltage, end_voltage,
                        sweep_rate, sweep_type, start_type,
                        swv_height, swv_inc, swv_period,
                        use_swv, cycles=1):
        self.start_voltage = start_voltage
        self.end_voltage = end_voltage
        self.low_voltage = min([self.start_voltage, self.end_voltage])
        self.high_voltage = max([self.start_voltage, self.end_voltage])
        self.sweep_rate = sweep_rate
        self.delay_time = 2 * abs(self.start_voltage - self.end_voltage) / self.sweep_rate
        if use_swv:
            # SWV 全程扫描时间 = 步数 × 周期；与 __init__ 保持同一公式，
            # 否则设置窗口保存后 delay_time 退回 CV 公式，扫描没完就停止轮询
            self.delay_time = (2 * self.swv_period *
                               abs(self.start_voltage - self.end_voltage) / self.swv_inc)
        self.sweep_type = sweep_type
        self.sweep_start_type = start_type
        self.swv_height = swv_height
        self.swv_inc = swv_inc
        self.swv_period = swv_period
        self.use_swv = use_swv
        self.cycles = max(1, int(cycles)) if sweep_type == 'CV' else 1
        self.save_settings()


DEFAULT_AMP_SETTINGS = {
    'voltage': 500,                          # mV
    'sampling_rate': float(DEVICE_SAMPLING_RATE),  # Hz
}


class AmpSettings(PersistedSettings):
    """安培法页参数：独立文件 settings_amp.txt，与 CV/ASV 各自独立。

    此前该类无任何持久化——改动只到内存，重启即回默认值（与 ASV
    事故同症状）。接入 PersistedSettings 后由声明表自动读写。
    """

    DEFAULTS = DEFAULT_AMP_SETTINGS
    SETTINGS_FILE = AMP_SETTINGS_FILE

    def __init__(self, clock_freq, dac):
        self._clock_freq = clock_freq
        self.raw_sampling_rate = DEVICE_SAMPLING_RATE  # Hz（ADC 原始采样率）
        self.down_sample = DOWN_SAMPLING
        self.load_settings()
        self.pwm_period_value = self.calculate_pwm_period(clock_freq)

    def calculate_pwm_period(self, clk_freq):
        pwm_period_value = int(round(clk_freq / self.sampling_rate))
        return pwm_period_value

    def update_settings(self, voltage, rate):
        """更新并立即落盘。采样率无需在此下发设备——安培法每次运行
        开始都会按 amp_settings.sampling_rate 重设 PWM（amp_run），
        但 pwm_period_value 必须同步重算，否则运行前的一致性比较会
        误判"设备已是该采样率"而跳过下发。"""
        self.voltage = voltage
        self.sampling_rate = rate
        self.pwm_period_value = self.calculate_pwm_period(self._clock_freq)
        self.save_settings()


# ASV 页参数默认值（独立于 CV 页；值的类型即加载校验规则）。
# 注意 end_voltage 在 ASV 语义 = 溶出终止电位，与 CV 页同名键含义不同，
# 因此绝不与 CV 页共用文件或键名
DEFAULT_ASV_SETTINGS = {
    'clean_volt': CLEAN_VOLTAGE_ASV,
    'clean_time': float(CLEAN_TIME),
    'plate_volt': PLATING_VOLTAGE,
    'plate_time': float(PLATING_TIME),
    'strip_start': PLATING_VOLTAGE,   # 溶出起始电位，默认与富集电位相同
    'end_voltage': END_ASV_VOLTAGE,   # 溶出终止电位（即原"峰值电压"）
    'rest_time': float(REST_TIME),
    'stirring_enabled': STIRRING_ENABLED,
    'clean_enabled': CLEAN_ENABLED,
    'clean_after_strip': CLEAN_AFTER_STRIP,
    'sweep_rate': SWEEP_RATE,
    'sweep_type': 'LS',               # LS / DPV / SWV
    'pulse_height': float(PULSE_HEIGHT),
    'pulse_inc': float(PULSE_INCREMENT),
    'pulse_width': float(PULSE_WIDTH),
}
ASV_SWEEP_TYPES = ['LS', 'DPV', 'SWV']


class ASVSettings(PersistedSettings):
    """ASV 溶出页参数：独立类、独立文件 settings_asv.txt。

    不再继承 CVSettings——曾经继承导致三重叠加：
    ①加载白名单按 CV 的键过滤，ASV 专属键全被丢弃；
    ②__init__ 加载后用默认常量无条件覆盖用户参数；
    ③与 CV 页共用同名键互相覆盖。结果就是"ASV 参数改完重启必丢"。
    """

    DEFAULTS = DEFAULT_ASV_SETTINGS
    SETTINGS_FILE = ASV_SETTINGS_FILE
    STRING_OPTIONS = {'sweep_type': ASV_SWEEP_TYPES}

    def __init__(self, clock_freq=None, dac=None):
        self.load_settings()
        self._apply_derived()

    def _apply_derived(self):
        """由用户参数推导的运行时字段：不持久化，加载/更新后统一重算。"""
        self.low_voltage = self.strip_start
        self.high_voltage = self.end_voltage
        # 扫描区间镜像同步到 start_voltage：运行框架的通用估算逻辑
        # 读 start_voltage——不同步会拿 CV 页起始电位把扫描时长估半
        # （真机踩过：进度窗口时间错一半）
        self.start_voltage = self.low_voltage
        self.sweep_start_type = 'Start'   # 溶出固定从溶出起始电位起扫
        self.cycles = 1                   # ASV 溶出固定单段扫描，无循环概念
        self.delay_time = 500 + abs(self.high_voltage - self.low_voltage) / self.sweep_rate
        if self.sweep_type in ('DPV', 'SWV'):
            self.delay_time = (500 + self.pulse_width *
                               abs(self.high_voltage - self.low_voltage) / self.pulse_inc)

    def update_settings(self, clean_voltage, clean_time, electroplate_voltage,
                        plating_time, end_voltage, sweep_rate, sweep_type=0,
                        pulse_height=PULSE_HEIGHT, pulse_inc=PULSE_INCREMENT,
                        pulse_width=PULSE_WIDTH,
                        rest_time=None, stirring_enabled=None, clean_after_strip=None,
                        clean_enabled=None, strip_start=None):
        self.clean_volt = clean_voltage
        self.clean_time = clean_time
        self.plate_volt = electroplate_voltage
        self.plate_time = plating_time
        self.end_voltage = end_voltage
        # 溶出起始电位缺省沿用富集电位（与旧版行为一致）
        self.strip_start = (electroplate_voltage if strip_start is None
                            else strip_start)
        if rest_time is not None:
            self.rest_time = rest_time
        if stirring_enabled is not None:
            self.stirring_enabled = stirring_enabled
        if clean_after_strip is not None:
            self.clean_after_strip = clean_after_strip
        if clean_enabled is not None:
            self.clean_enabled = clean_enabled
        self.sweep_rate = sweep_rate
        self.pulse_height = pulse_height
        self.pulse_inc = pulse_inc
        self.pulse_width = pulse_width
        if sweep_type == 1:
            self.sweep_type = "DPV"
        elif sweep_type == 2:
            # SWV：pulse_width 复用为方波周期（ms），每步一个周期
            self.sweep_type = "SWV"
        else:
            self.sweep_type = "LS"
        self._apply_derived()
        self.save_settings()


DEFAULT_APP_SETTINGS = {
    'language': 'zh',               # 界面语言：zh / en
    'current_range_index': 0,       # 电流量程档位（CURRENT_LIMIT_VALUES 下标）
    'data_save_type': 'Converted',  # 开发者选项：CSV 存换算值/原始计数
    'adc_channel': 0,               # 开发者选项：ADC 通道
}
APP_SETTINGS_FILE = "settings_app.txt"
APP_LANGUAGE_OPTIONS = ['zh', 'en']


class AppSettings(PersistedSettings):
    """应用级偏好设置：不属于任何测量页的全局选项。

    语言、电流量程、开发者选项此前全是内存态，重启即丢
    （与 ASV 参数事故同类）。
    """

    DEFAULTS = DEFAULT_APP_SETTINGS
    SETTINGS_FILE = APP_SETTINGS_FILE
    STRING_OPTIONS = {'language': APP_LANGUAGE_OPTIONS}

    def __init__(self):
        self.load_settings()

    def update_settings(self, language=None, current_range_index=None,
                        data_save_type=None, adc_channel=None):
        """更新列出的项并立即落盘。"""
        if language is not None:
            self.language = language
        if current_range_index is not None:
            self.current_range_index = int(current_range_index)
        if data_save_type is not None:
            self.data_save_type = data_save_type
        if adc_channel is not None:
            self.adc_channel = int(adc_channel)
        self.save_settings()


class DAC(object):
    def __init__(self, _type, voltage_range=4080.0):
        self.source = _type
        if _type == "8-bit DAC":
            self.bits = 8
            self.voltage_step_size = 16
        elif _type == "DVDAC":
            self.bits = 12
            self.voltage_step_size = 1
        self.range = voltage_range
        self.virtual_ground = VIRTUAL_GROUND

    def set_bits(self):
        if self.source == "8-bit DAC":
            self.bits = 8
        elif self.source == "DVDAC":
            self.bits = 12

    def set_step_size(self):
        if self.source == "8-bit DAC":
            return 16
        elif self.source == "DVDAC":
            return 1
        return

    def get_dac_count(self, _input_voltage, shift=False, actual=False):
        if actual:
            return int(round(-_input_voltage + self.virtual_ground) / self.voltage_step_size)
        if shift:
            return int(round((_input_voltage + self.virtual_ground) / self.voltage_step_size))
        return int(round(_input_voltage / self.voltage_step_size))

    def set_source(self, _source):
        if _source == self.source:
            return
        self.source = _source
        self.set_bits()
        self.voltage_step_size = self.set_step_size()


class ADC_TIA(object):
    def __init__(self, adc_config=1, tia_resistor=20, adc_gain=1, bits=ADC_BITS):
        self.current_options = CURRENT_LIMIT_VALUES
        self.current_option_index = 0
        self.adc_config = adc_config
        if self.adc_config == 1:
            self.adc_vref = 4096  # mV, +-2.048 V
        elif self.adc_config == 2:
            self.adc_vref = 2048  # mV, +-1.024 V
        else:
            raise NotImplementedError
        self.tia_resistor = tia_resistor
        self.adc_gain = adc_gain
        if adc_gain == 1:
            self.range_index = tia_resistor
        else:
            self.range_index = tia_resistor + adc_gain / 2
        self.bits = bits
        self.counts_to_current = self.calc_counts_to_current_ua()
        self.shift = 0
        self.calibrate_failed = False  # 标定失败时为 True（沿用理论系数）
        self.adc_channel = 0
        self.current_lims = self.current_options[self.current_option_index]

    def calc_current_lims(self):
        max_voltage = self.adc_config / 2.0
        max_current = max_voltage / float(self.tia_resistor)
        return max_current

    def calc_counts_to_current_ua(self):
        counts_to_volts = float(ADC_VREF) / (2 ** self.bits)
        volts_to_current = 1.0 / self.tia_resistor
        return counts_to_volts * volts_to_current

    def set_value(self, new_tia_value, new_adc_gain_value, new_adc_config, current_range_index):
        self.tia_resistor = new_tia_value
        self.adc_gain = new_adc_gain_value
        self.adc_config = new_adc_config
        self.range_index = current_range_index
        self.counts_to_current = self.calc_counts_to_current_ua()
        self.current_option_index = current_range_index
        self.current_lims = self.current_options[self.current_option_index]

    def calibrate(self, data):
        # 设备偶发回传无效标定数据（除数为零），调用方负责重试/兜底
        if not data or len(data) < 10:
            raise ValueError("calibration data malformed: %r" % (data,))
        if (data[5] - data[7]) == 0 or (data[7] - data[9]) == 0:
            raise ValueError("calibration data invalid (zero divisor): %r" % (data,))
        self.shift = data[7]
        lower_count_to_current = (data[0] / 8.0) / (data[5] - data[7])
        upper_count_to_current = (data[4] / 8.0) / (data[7] - data[9])
        self.counts_to_current = (float(lower_count_to_current) + float(
            upper_count_to_current)) / 2.0
        logging.info('adc calibrate, counts to current: {0}'.format(self.counts_to_current))


def max_cv_cycles(start_voltage, end_voltage, sweep_start_type, dac,
                  lut_size=FIRMWARE_LUT_SIZE):
    """给定电位范围与起始方式，估算固件波形表还能容纳的最大 CV 圈数。

    固件 LUT 分段规则（lut_protocols.c）：
      'Start' 三角：每圈 2r+1 点 + 1 点收尾保持
      'Zero'  零起：0→start 与 end→0 各占 |g−s|/|g−e|+1 点，每圈仍为 2r+1 点
    返回值至少为 1（容量不足时由固件截尾，GUI 只做事前提示）。
    """
    start_dac = dac.get_dac_count(start_voltage, shift=True)
    end_dac = dac.get_dac_count(end_voltage, shift=True)
    r = abs(end_dac - start_dac)
    budget = lut_size - 1  # 收尾保持点
    if str(sweep_start_type).startswith('Z'):
        gnd = dac.get_dac_count(0, shift=True)
        budget -= (abs(start_dac - gnd) + abs(end_dac - gnd) + 2)
    per_cycle = 2 * r + 1
    if per_cycle <= 0:
        return MAX_CV_CYCLES
    return max(1, min(MAX_CV_CYCLES, budget // per_cycle))
