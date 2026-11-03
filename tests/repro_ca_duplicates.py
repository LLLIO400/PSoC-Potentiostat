# -*- coding: utf-8 -*-
"""真机复现 CA（计时电流）采集，定位"重复点"来源。

完全镜像 experiments.AmpFrame.USBHandler 的协议时序：
  T|<period> → M|<dac>|<packet_size> → 轮询 "Done<ch>" → 'F<ch>' → get_data
记录每段：墙钟时间、样本数、转换后数值、原始计数、段 md5。
分析：段间重复 / 段内连续重复值 / GUI 时间轴公式下的重复时间戳。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hashlib

from comm import COMPLETE_MESSAGE
from config import DeviceParameters
from experiments import AMP_FW_CHANNELS
from hardware_tests import _connect

RUN_SECONDS = 14.0


def main():
    dev = _connect()
    if dev is None:
        print("未检测到设备")
        return 2
    dp = dev.device_params
    rate = dp.amp_settings.sampling_rate
    packet_size = int(rate / 2.0)
    number_packets = int((packet_size + 1.0) / 32.0) + ((packet_size + 1.0) % 32 > 0)
    time_step = 1.0 / rate
    print("采样率 %s Hz, 段大小 %d, 包数 %d, time_step %.4f s, 固件通道数 %d"
          % (rate, packet_size, number_packets, time_step, AMP_FW_CHANNELS))

    # 与 GUI 一致：设采样率 + 采集期间加大平滑
    period = int(round(dp.clk_freq_isr_pwm / rate)) - 1
    dev.clear_in_buffer()
    dev.usb_write('T|{0:05d}'.format(period))
    time.sleep(0.15)
    dev.usb_write('D|1548')          # -500 mV，与用户运行一致
    dev.samples_to_smooth = 50
    time.sleep(0.15)
    dev.clear_in_buffer()
    dev.usb_write('M|1548|{0:04d}'.format(packet_size))
    print("M 已发送，开始采集 %.0f s ..." % RUN_SECONDS)

    segments = []          # (t_wall, len, converted, md5_raw)
    raw_segments = []
    t0 = time.time()
    msg_log = []
    fetched = 0
    while time.time() - t0 < RUN_SECONDS:
        get = dev.usb_read_message()
        if get:
            msg_log.append((time.time() - t0, get if isinstance(get, str)
                            else repr(get)))
        if get and get.startswith(COMPLETE_MESSAGE) and len(get) > 4:
            ch = get[4]
            if not ch.isdigit() or int(ch) != 0:
                continue
            dev.usb_write('F' + ch)
            raw = dev.get_data(number_packets)
            conv = dev.process_data(list(raw))
            tw = time.time() - t0
            segments.append((tw, len(conv), conv,
                             hashlib.md5(bytes(str(raw), 'utf-8')).hexdigest()[:8]))
            raw_segments.append(list(raw))
            fetched += 1
        time.sleep(0.05)

    dev.usb_write('X')
    time.sleep(0.2)
    dev.clear_in_buffer()
    dev.samples_to_smooth = 1
    print("采集结束: 收到消息 %d 条, 抓取段 %d 个" % (len(msg_log), fetched))

    # ---- 消息时序 ----
    print("\n== 消息时序(前 12 条) ==")
    for tw, m in msg_log[:12]:
        print("  %6.2f s  %r" % (tw, m))
    from collections import Counter
    print("Done 消息统计:", Counter(m for _, m in msg_log if 'Done' in str(m)))

    # ---- 段统计 ----
    print("\n== 段统计 ==")
    for tw, ln, conv, h in segments:
        print("  t=%6.2f s len=%4d head=%s tail=%s md5=%s"
              % (tw, ln, [round(v, 2) for v in conv[:3]],
                 [round(v, 2) for v in conv[-3:]], h))

    # ---- 段间完全重复? ----
    print("\n== 段间重复检查 ==")
    dup_seg = 0
    for i in range(1, len(segments)):
        if segments[i][3] == segments[i - 1][3]:
            dup_seg += 1
            print("  段 %d 与 %d 完全相同 (md5 %s)" % (i - 1, i, segments[i][3]))
    if not dup_seg:
        print("  无完全重复段")

    # ---- 段内连续重复值 ----
    print("\n== 段内连续重复值(相邻两值完全相等) ==")
    total_pairs = 0
    for i, (tw, ln, conv, h) in enumerate(segments):
        runs = []
        j = 1
        while j < ln:
            if conv[j] == conv[j - 1]:
                k = j
                while k < ln and conv[k] == conv[j - 1]:
                    k += 1
                runs.append((j - 1, k - j))
                j = k
            else:
                j += 1
        total_pairs += len(runs)
        if runs:
            print("  段 %d (t=%.2f): %d 处连续相等, 例: %s"
                  % (i, tw, len(runs), runs[:5]))
    print("  合计 %d 处" % total_pairs)

    # ---- GUI 时间轴公式下的重复时间戳 ----
    print("\n== GUI 时间轴公式重复检查 ==")
    t_axis = []
    idx = 0
    for tw, ln, conv, h in segments:
        base = idx * AMP_FW_CHANNELS * ln * time_step
        t_axis.extend(base + x * time_step for x in range(1, ln + 1))
        idx += 1
    dups = sum(1 for a, b in zip(t_axis, t_axis[1:]) if abs(b - a) < 1e-12)
    backwards = sum(1 for a, b in zip(t_axis, t_axis[1:]) if b < a)
    print("  总点数 %d, 相邻重复时间戳 %d, 时间回退 %d"
          % (len(t_axis), dups, backwards))

    # ---- 原始计数里的重复（区分固件 vs 平滑） ----
    print("\n== 原始计数连续重复（未平滑） ==")
    dev.samples_to_smooth = 1
    total_raw_pairs = 0
    for i, r in enumerate(raw_segments):
        data = [v for v in r if -16384 < v < 16383]
        pairs = sum(1 for a, b in zip(data, data[1:]) if a == b)
        total_raw_pairs += pairs
    print("  原始计数相邻重复合计 %d 处（多则固件/流问题，少则正常量化）"
          % total_raw_pairs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
