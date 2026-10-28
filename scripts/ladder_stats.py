#!/usr/bin/env python3
"""
Statistics of the ladder test V3 (references/ladder_v3_spec.md, section 14): calendar-block bootstrap
intervals with the small-block correction, the main screen, p-values and Holm's step-down, power
(MDE80 from the placebo ladders), win rate and payoff ratio per random draw, and the one-sided
held-out check. numpy only (Student's t from the regularised incomplete beta function).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import ladder_sim as S

B = 10_000
LEVEL = 0.90


# ----------------------------------------------------------------------------- Student's t
def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction of the incomplete beta function (modified Lentz)."""
    tiny, eps = 1e-300, 1e-15
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 10_000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbt) * _betacf(b, a, 1.0 - x) / b


def t_cdf(x: float, df: float) -> float:
    if not np.isfinite(x):
        return 1.0 if x > 0 else 0.0
    tail = 0.5 * betainc(df / 2.0, 0.5, df / (df + x * x))
    return 1.0 - tail if x >= 0 else tail


def t_ppf(p: float, df: float) -> float:
    """Quantile of Student's t by bisection on t_cdf (to 1e-12)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -t_ppf(1.0 - p, df)
    lo, hi = 0.0, 1.0
    while t_cdf(hi, df) < p:
        hi *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return 0.5 * (lo + hi)


# ----------------------------------------------------------------------------- blocks
def year_block(years: np.ndarray, width: int = 1, first: int = 2008) -> np.ndarray:
    """Block label per plan: the calendar year of its start, or the first year of its `width`-year block
    counted from `first` (2-year blocks 2008-09, 2010-11, ...)."""
    y = np.asarray(years, dtype=np.int64)
    return y if width == 1 else first + width * ((y - first) // width)


@dataclass
class Blocks:
    """The blocks of one plan set and their bootstrap table (S14.1, S16)."""
    labels: np.ndarray                     # block label per plan
    boot_seed: int = S.BOOT_SEED
    n_boot: int = B
    uniq: np.ndarray = field(init=False)
    index: np.ndarray = field(init=False)  # block index per plan
    table: np.ndarray = field(init=False)  # (n_boot, G) resampled block indices

    def __post_init__(self):
        self.uniq, self.index = np.unique(np.asarray(self.labels), return_inverse=True)
        tag = f"{self.boot_seed}|blocks|" + ",".join(str(x) for x in self.uniq)
        gen = np.random.Generator(np.random.PCG64(S.seed_of(tag)))
        self.table = gen.integers(0, len(self.uniq), size=(self.n_boot, len(self.uniq)))

    @property
    def G(self) -> int:
        return len(self.uniq)

    def sums(self, x: np.ndarray, w=None) -> np.ndarray:
        """Per-block sums of x (plans in rows; extra axes kept)."""
        x = np.asarray(x, dtype=float)
        out = np.zeros((self.G,) + x.shape[1:])
        np.add.at(out, self.index, x if w is None else x * w)
        return out

    def resample(self, per_block: np.ndarray) -> np.ndarray:
        """(n_boot, ...) sums of per-block values over each resample's blocks."""
        return per_block[self.table].sum(axis=1)


def _summary(theta: float, rs: np.ndarray, G: int, level: float = LEVEL) -> dict:
    rs = rs[np.isfinite(rs)]
    sd = float(np.std(rs, ddof=1)) if len(rs) > 1 else float("nan")
    se = math.sqrt(G / (G - 1)) * sd if G > 1 else float("nan")
    q = t_ppf(0.5 + level / 2.0, G - 1) if G > 1 else float("nan")
    lo_t, hi_t = theta - q * se, theta + q * se
    lo_p = float(np.percentile(rs, 100 * (1 - level) / 2)) if len(rs) else float("nan")
    hi_p = float(np.percentile(rs, 100 * (1 + level) / 2)) if len(rs) else float("nan")
    p = 2.0 * (1.0 - t_cdf(abs(theta) / se, G - 1)) if se > 0 else float("nan")
    return {"theta": theta, "se": se, "sd_boot": sd, "G": G, "lo": lo_t, "hi": hi_t, "lo_pct": lo_p,
            "hi_pct": hi_p, "lo_screen": min(lo_t, lo_p), "p": p, "n_boot_used": int(len(rs))}


def mean_diff(diff: np.ndarray, blocks: Blocks, level: float = LEVEL) -> dict:
    """Paired mean difference over plans: theta and its interval (S14.1)."""
    diff = np.asarray(diff, dtype=float)
    s, n = blocks.sums(diff), blocks.sums(np.ones_like(diff))
    rs = blocks.resample(s) / blocks.resample(n)
    out = _summary(float(diff.mean()), rs, blocks.G, level)
    out["n"] = int(len(diff))
    return out


def unpaired_diff(xa: np.ndarray, la: np.ndarray, xb: np.ndarray, lb: np.ndarray, level: float = LEVEL,
                  boot_seed: int = S.BOOT_SEED) -> dict:
    """mean(A) - mean(B) for two plan sets, both means computed in every resample from the same resampled
    blocks (the union of both sets' blocks)."""
    xa, xb = np.asarray(xa, float), np.asarray(xb, float)
    blocks = Blocks(np.r_[la, lb], boot_seed=boot_seed)
    ia, ib = blocks.index[:len(xa)], blocks.index[len(xa):]
    sa, na, sb, nb = (np.zeros(blocks.G) for _ in range(4))
    np.add.at(sa, ia, xa)
    np.add.at(na, ia, 1.0)
    np.add.at(sb, ib, xb)
    np.add.at(nb, ib, 1.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        rs = blocks.resample(sa) / blocks.resample(na) - blocks.resample(sb) / blocks.resample(nb)
    out = _summary(float(xa.mean() - xb.mean()), rs, blocks.G, level)
    out["n_a"], out["n_b"] = int(len(xa)), int(len(xb))
    return out


def per_draw_metric(r_zone: np.ndarray, f_zone: np.ndarray, r_draws: np.ndarray, f_draws: np.ndarray,
                    blocks: Blocks, metric: str, level: float = LEVEL, common: bool = False) -> dict:
    """S12 metrics that are not plain means, zone minus the draws: each draw's value over its own plans with
    a fill, averaged over the draws, against zone's over its plans with a fill; recomputed in every resample.
    metric: "win_rate" (share of R > 0), "payoff" (mean R of winners / |mean R of losers|) or "mean" (the
    mean of the values given in r_zone / r_draws, e.g. the average cost, the safety margin or the stop flag).
    common: for each draw only the plans where both zone and that draw filled, on both sides."""
    def parts(r, f):
        r = np.where(f, r, 0.0)
        if metric == "mean":
            return [blocks.sums(r), blocks.sums(f.astype(float))]
        win = (f & (r > 0)).astype(float)
        lose = (f & ~(r > 0)).astype(float)
        return [blocks.sums(win), blocks.sums(f.astype(float)), blocks.sums(r * win), blocks.sums(r * lose),
                blocks.sums(lose)]

    def value(*a):
        with np.errstate(invalid="ignore", divide="ignore"):
            if metric == "mean":
                return a[0] / a[1]
            w, n, sw, sl, nl = a
            if metric == "win_rate":
                return w / n
            return (sw / w) / np.abs(sl / nl)

    fz = np.asarray(f_zone, bool)
    fd = np.asarray(f_draws, bool)
    rz_, rd_ = np.asarray(r_zone, float), np.asarray(r_draws, float)
    if common:
        both = fz[:, None] & fd
        pz = parts(np.broadcast_to(rz_[:, None], rd_.shape), both)     # each (G, n_draws)
        pd_ = parts(rd_, both)
        theta = float(np.nanmean(value(*[x.sum(axis=0) for x in pz]) - value(*[x.sum(axis=0) for x in pd_])))
        with np.errstate(invalid="ignore"):
            rs = np.nanmean(value(*[blocks.resample(x) for x in pz]) - value(*[blocks.resample(x) for x in pd_]),
                            axis=1)
        return _summary(theta, rs, blocks.G, level)
    pz = parts(rz_, fz)
    pd_ = parts(rd_, fd)                                          # each (G, n_draws)
    theta = float(value(*[x.sum(axis=0) for x in pz]) - np.nanmean(value(*[x.sum(axis=0) for x in pd_])))
    rz = value(*[blocks.resample(x) for x in pz])
    with np.errstate(invalid="ignore"):
        rd = np.nanmean(value(*[blocks.resample(x) for x in pd_]), axis=1)
    return _summary(theta, rz - rd, blocks.G, level)


def leave_one_out(diff: np.ndarray, labels: np.ndarray) -> dict:
    """The range of theta leaving out one block (year) at a time."""
    diff, labels = np.asarray(diff, float), np.asarray(labels)
    vals = {str(y): float(diff[labels != y].mean()) for y in np.unique(labels) if (labels != y).any()}
    return {"min": min(vals.values()), "max": max(vals.values()), "by_left_out": vals}


def holm(pvalues: dict, alpha: float = 0.10) -> dict:
    """Holm's step-down at family-wise `alpha`: adjusted p and rejection per item. An item without a p
    (NaN: no SE) counts in m as p = 1 and is never rejected."""
    pv = {k: (float(v) if np.isfinite(v) else 1.0) for k, v in pvalues.items()}
    items = sorted(pv, key=lambda k: (pv[k], str(k)))
    m = len(items)
    out, running, stop = {}, 0.0, False
    for i, k in enumerate(items):
        adj = min(1.0, (m - i) * pv[k])
        running = max(running, adj)
        rej = (not stop) and np.isfinite(pvalues[k]) and pv[k] <= alpha / (m - i)
        if not rej:
            stop = True
        out[k] = {"p": pvalues[k], "p_holm": running, "reject": bool(rej)}
    return out


def mde80(se_k: np.ndarray, G: int) -> dict:
    """S14.2: MDE80 = (t(0.95, G-1) + t(0.80, G-1)) * the 80th percentile of the placebo SEs."""
    q80 = float(np.percentile(np.asarray(se_k, float), 80))
    f = t_ppf(0.95, G - 1) + t_ppf(0.80, G - 1)
    return {"se_q80": q80, "factor": f, "mde80": f * q80, "G": G}


def screen(main: dict, theta_126: float, theta_43: float) -> dict:
    """S14.1: lower bound above 0, theta >= +1 pp, the same sign at 126 bars and on the 43 stocks."""
    th = main["theta"]
    checks = {"lower_bound_above_0": main["lo_screen"] > 0, "theta_at_least_1pp": th >= 0.01,
              "same_sign_126": np.sign(theta_126) == np.sign(th) and th != 0,
              "same_sign_43": np.sign(theta_43) == np.sign(th) and th != 0}
    return {"passes": all(checks.values()), **checks}


def one_sided_lower_unpaired(xa: np.ndarray, la: np.ndarray, xb: np.ndarray, lb: np.ndarray, direction: float,
                             K: int) -> dict:
    """S14.4 for an unpaired item (mean(A) - mean(B), S14.3): the lower bound at level 1 - 0.05/K of
    direction * (mean(A) - mean(B)), both means from the same resampled blocks, the smaller of the t bound
    and the percentile bound."""
    a = 0.05 / K
    (x1, l1), (x2, l2) = ((xa, la), (xb, lb)) if direction > 0 else ((xb, lb), (xa, la))
    u = unpaired_diff(x1, l1, x2, l2, level=1 - 2 * a)
    return {"theta_dir": u["theta"], "se": u["se"], "level": 1 - a, "lo": u["lo_screen"],
            "confirmed": bool(u["lo_screen"] > 0)}


def one_sided_lower(diff: np.ndarray, blocks: Blocks, direction: float, K: int) -> dict:
    """S14.4: the lower bound at level 1 - 0.05/K of direction * diff, with 14.1's method (the smaller of
    the t bound and the percentile bound)."""
    x = direction * np.asarray(diff, float)
    a = 0.05 / K
    s, n = blocks.sums(x), blocks.sums(np.ones_like(x))
    rs = blocks.resample(s) / blocks.resample(n)
    base = _summary(float(x.mean()), rs, blocks.G)
    lo_t = base["theta"] - t_ppf(1 - a, blocks.G - 1) * base["se"]
    lo_p = float(np.percentile(rs, 100 * a))
    return {"theta_dir": base["theta"], "se": base["se"], "level": 1 - a, "lo": min(lo_t, lo_p),
            "confirmed": min(lo_t, lo_p) > 0}
