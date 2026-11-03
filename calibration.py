# Copyright (c) 2026 Zhijian Yi (Guangdong, China) — PSoC-Potentiostat-YI-1.0
# Licensed under CC BY-SA 3.0 (same as the upstream PSoC-Potentiostat project)
"""标准曲线 / 定量分析（不依赖硬件）。

- linear_fit:                        最小二乘直线拟合 y = k·x + b，附 R²
- concentration_from_signal:         外标法：由信号反算浓度
- standard_addition_concentration:   标准加入法：由斜率/截距外推未知样浓度
"""

import numpy as np


def linear_fit(points):
    """最小二乘拟合 y = k·x + b。

    points: [(x, y), ...] 至少 2 点且 x 不全相同。
    返回 (k, b, r2)；r2 为决定系数（完全水平线时定义为 1.0）。
    """
    n = len(points)
    if n < 2:
        raise ValueError("linear fit needs at least 2 points")
    xs = np.asarray([p[0] for p in points], dtype=float)
    ys = np.asarray([p[1] for p in points], dtype=float)
    mx, my = xs.mean(), ys.mean()
    sxx = float(((xs - mx) ** 2).sum())
    if sxx == 0:
        raise ValueError("all x values are identical")
    k = float(((xs - mx) * (ys - my)).sum() / sxx)
    b = float(my - k * mx)
    ss_res = float(((ys - (k * xs + b)) ** 2).sum())
    ss_tot = float(((ys - my) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return k, b, r2


def concentration_from_signal(k, b, signal):
    """外标法：C = (signal − b) / k。斜率为 0 时无法定量。"""
    if k == 0:
        raise ValueError("zero slope: cannot quantify")
    return (signal - b) / k


def standard_addition_concentration(k, b):
    """标准加入法：信号 = k·c加 + b，外推信号 0 处 c加 = −b/k，
    未知样浓度 C = b/k（取绝对值以容忍拟合方向）。"""
    if k == 0:
        raise ValueError("zero slope: cannot quantify")
    return abs(b / k)
