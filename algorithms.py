# Copyright (c) 2026 Zhijian Yi (Guangdong, China) — PSoC-Potentiostat-YI-1.0
# Licensed under CC BY-SA 3.0 (same as the upstream PSoC-Potentiostat project)
"""数据处理算法（升级方案 Phase 7 / 附录 B.4）。

- sg_smooth:        Savitzky-Golay 平滑，替代简单移动平均
- subtract_baseline: 多项式基线扣除（支持手动框选峰区域或自动 σ 裁剪）
- detect_peaks:     自动寻峰（峰位 / 峰高 / 半峰宽）
- analyze_stripping_peak: 平滑 + 基线扣除 + 寻峰一步到位
"""

import numpy as np
from scipy.signal import savgol_filter, find_peaks, peak_widths


def sg_smooth(data, window=11, polyorder=3):
    """Savitzky-Golay 平滑。窗口自动适配数据长度（奇数且 <= 数据点数）。"""
    n = len(data)
    if n < 5:
        return list(data)
    if window % 2 == 0:
        window += 1
    if window > n:
        window = n if n % 2 == 1 else n - 1
    if polyorder >= window:
        polyorder = window - 1
    return list(savgol_filter(data, window, polyorder))


def _auto_baseline_mask(current, deg=3, sigma=2.0, iters=3):
    """自动基线掩码：迭代拟合多项式，剔除残差大于 sigma 倍的点（即峰）。"""
    c = np.asarray(current, dtype=float)
    x = np.arange(len(c))
    mask = np.ones(len(c), dtype=bool)
    for _ in range(iters):
        if mask.sum() < deg + 2:
            break
        coeffs = np.polyfit(x[mask], c[mask], deg=deg)
        residual = c - np.polyval(coeffs, x)
        std = np.std(residual[mask])
        if std == 0:
            break
        mask = residual < sigma * std  # 只剔除正向峰（溶出峰向上）
    return mask


def subtract_baseline(voltage, current, peak_region=None, deg=3):
    """多项式基线扣除。

    peak_region: (start_x, end_x)，框选的峰区域；区域外数据用于拟合基线。
                 None 时自动用 σ 裁剪剔除峰后拟合。
    返回扣除基线后的电流列表。
    """
    v = np.asarray(voltage, dtype=float)
    c = np.asarray(current, dtype=float)
    if peak_region is not None:
        lo, hi = min(peak_region), max(peak_region)
        mask = (v < lo) | (v > hi)
    else:
        mask = _auto_baseline_mask(c, deg=deg)
    if mask.sum() < deg + 2:
        mask = np.ones(len(v), dtype=bool)
    coeffs = np.polyfit(v[mask], c[mask], deg=deg)
    return list(c - np.polyval(coeffs, v))


def detect_peaks(voltage, current, height=None, distance=10):
    """自动寻峰。

    height: 最小峰高，None 时取 3 倍标准差（3σ 阈值）。
    distance: 最小峰间距（数据点数）。
    返回 [{'voltage': 峰位, 'height': 峰高, 'half_width': 半峰宽}, ...]，
    单位与输入一致（mV / µA），按峰高降序排列。
    """
    v = np.asarray(voltage, dtype=float)
    c = np.asarray(current, dtype=float)
    if len(c) < 3:
        return []
    if height is None:
        height = float(np.std(c)) * 3
    peaks, properties = find_peaks(c, height=height, distance=distance)
    if len(peaks) == 0:
        return []
    widths = peak_widths(c, peaks, rel_height=0.5)[0]
    # 半峰宽从数据点数换算成 x 轴单位
    dv = abs(float(np.median(np.diff(v)))) if len(v) > 1 else 1.0
    results = []
    for idx, w in zip(peaks, widths):
        results.append({
            'voltage': float(v[idx]),
            'height': float(c[idx]),
            'half_width': float(w * dv),
        })
    results.sort(key=lambda p: p['height'], reverse=True)
    return results


def peak_area(voltage, current, peak, factor=1.5):
    """单峰面积（µA·mV）：峰位 ±factor×半峰宽 区间内，
    对基线扣除后的电流做梯形积分。"""
    v = np.asarray(voltage, dtype=float)
    c = np.asarray(current, dtype=float)
    w = float(peak.get('half_width', 0.0)) * factor
    mask = (v >= peak['voltage'] - w) & (v <= peak['voltage'] + w)
    if mask.sum() < 2:
        return 0.0
    vv, cc = v[mask], c[mask]
    order = np.argsort(vv)
    return float(np.trapz(cc[order], vv[order]))


def analyze_stripping_peak(voltage, current, peak_region=None,
                           smooth_window=11, baseline_deg=3,
                           sweep_rate=None):
    """溶出峰一键分析：SG 平滑 -> 基线扣除 -> 寻峰（+ 面积/电量）。

    sweep_rate: 扫速 (V/s)。提供时每个峰附带：
      area  = 峰面积 (µA·mV)
      charge= 电量 (µC) = 面积(µA·mV) / 1000 / 扫速(V/s)
    返回 (peaks, processed_current)；peaks 为空列表表示未检测到峰。
    """
    smoothed = sg_smooth(current, window=smooth_window)
    processed = subtract_baseline(voltage, smoothed,
                                  peak_region=peak_region, deg=baseline_deg)
    peaks = detect_peaks(voltage, processed)
    for p in peaks:
        p['area'] = peak_area(voltage, processed, p)
        if sweep_rate:
            p['charge'] = p['area'] / 1000.0 / float(sweep_rate)
    return peaks, processed
