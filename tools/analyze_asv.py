# -*- coding: utf-8 -*-
import glob, math, statistics

cands = glob.glob(r"C:\Users\YZJ\Desktop\*1000ppb*.csv")
print("files:", cands)
path = cands[0]

xs, ys = [], []
for line in open(path, encoding="utf-8-sig"):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    p = line.split(",")
    try:
        x, y = float(p[0]), float(p[1])
    except (ValueError, IndexError):
        continue
    xs.append(x); ys.append(y)

n = len(xs)
print("points:", n, "| E: %.0f .. %.0f mV" % (xs[0], xs[-1]))

# quantization (LSB) of current readings
uy = sorted(set(ys))
d = [b - a for a, b in zip(uy, uy[1:]) if b - a > 1e-9]
print("current LSB ~ %.6f uA" % min(d))

# noise in quiet region -1000..-700 mV
seg = [y for x, y in zip(xs, ys) if -1000 <= x <= -700]
print("quiet region -1000..-700 mV: mean %.3f uA, std %.3f uA" % (statistics.mean(seg), statistics.stdev(seg)))

# local maxima
def local_maxima(xs, ys, win=6):
    out = []
    for i in range(win, len(ys) - win):
        if ys[i] >= max(ys[i - win:i + win + 1]) and (ys[i] > ys[i - win] and ys[i] > ys[i + win]):
            out.append((i, xs[i], ys[i]))
    return out

peaks = [(i, x, y) for i, x, y in local_maxima(xs, ys) if y > 2.0]
# keep highest in each 200 mV neighborhood
peaks.sort(key=lambda t: -t[2])
kept = []
for i, x, y in peaks:
    if all(abs(x - kx) > 200 for _, kx, _ in kept):
        kept.append((i, x, y))
kept.sort()
for i, x, y in kept:
    print("peak: E = %.0f mV, I = %.3f uA" % (x, y))

if len(kept) >= 2:
    (i1, e1, ip1), (i2, e2, ip2) = kept[0], kept[1]
    iv = min(range(i1, i2), key=lambda k: ys[k])
    print("valley: E = %.0f mV, I = %.3f uA" % (xs[iv], ys[iv]))

    def edge(k, step, thresh):
        while 0 < k < len(ys) - 1 and ys[k] > thresh:
            k += step
        return k

    lft = edge(i1, -1, 1.5)          # left edge of peak 1 (near baseline)
    rgt = edge(i2, +1, 1.5)          # right edge of peak 2 (near baseline)

    def charge(ka, kb, va, vb):
        # baseline = straight line between edge points; Q in uC (uA * s)
        q = 0.0
        for k in range(ka, kb):
            f = (xs[k] - va) / (vb - va)
            base = ys[ka] + f * (ys[kb] - ys[ka])
            dE = xs[k + 1] - xs[k]
            dt = abs(dE) / 0.5 * 1e-3 / 1.0  # dV(mV) -> s at 0.5 V/s
            dt = abs(dE) / 500.0            # mV / (mV/s)
            Ik = 0.5 * ((ys[k] - base) + (ys[k + 1] - base))
            q += Ik * dt
        return q

    q1 = charge(lft, iv, xs[lft], xs[iv])
    q2 = charge(iv, rgt, xs[iv], xs[rgt])
    for name, q in (("peak1 (-315 mV)", q1), ("peak2 (~0 mV)", q2)):
        mol = q * 1e-6 / (3 * 96485.33)
        ng = mol * 208.98 * 1e9
        print("%s: Q = %.2f uC  ->  n(Bi) ~ %.2f pmol  (~%.1f ng)" % (name, q, mol * 1e12, ng))
    print("integration windows: %.0f..%.0f mV and %.0f..%.0f mV" % (xs[lft], xs[iv], xs[iv], xs[rgt]))

# start-of-scan residual current (H2 region)
print("first 5 pts:", [(xs[i], round(ys[i], 2)) for i in range(5)])

# plot if matplotlib available
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    ax.plot(xs, ys, lw=1.2, color="#1f77b4")
    for i, x, y in kept:
        ax.annotate("E=%.0f mV\nI=%.1f uA" % (x, y), xy=(x, y), xytext=(x - 480, y - 8),
                    arrowprops=dict(arrowstyle="->", color="k", lw=0.8), fontsize=9)
    ax.set_xlabel("Potential (mV)")
    ax.set_ylabel("Current (uA)")
    ax.set_title("ASV: 1000 ppb Bi + 0.05% HNO$_3$  (SWV, deposition -1400 mV / 300 s)")
    ax.grid(alpha=0.3)
    out = r"C:\Users\YZJ\Desktop\PSoC-Potentiostat-YI-1.0_GUI\ASV_1000ppb_Bi.png"
    fig.tight_layout()
    fig.savefig(out)
    print("plot saved:", out)
except Exception as ex:
    print("no plot:", ex)
