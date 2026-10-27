#!/usr/bin/env python3
"""
Long-window volume-profile node zones, as reverse-engineered from a sample table of support and
resistance zones (12 symbols, 71 zones, 2026-10-23 / 10-26; see the project's sr-reverse report).

  python scripts/node_zones.py BARS.csv [--lookback 756] [--as-of YYYY-MM-DD]

From the last `lookback` bars (about 3 years of daily bars):
  1. volume by price: bins BIN_SHARE of the last close wide, from the window's lowest low to its
     highest high; each bar's volume is spread over its [low, high] range (levels._bin_volumes);
  2. the profile is smoothed with a Gaussian of SMOOTH_BINS bins and cut at its local minima (the
     low-volume nodes), so each piece between two minima is one high-volume node;
  3. a node with less than MIN_NODE_SHARE of the window's volume is dropped; the zone of a node is
     the run of its bins whose smoothed volume is at least CORE_SHARE of the node's peak;
  4. density = the zone's share of the window's volume / its width as a share of the window's
     price range, i.e. how many times the window's average volume per unit of price; the grade is
     weak / medium / strong / very_strong at the GRADE_CUTS of the density (fitted to the sample's
     50 matched zones: 64% exact there; refitted leaving one symbol out, about 58% against 42% for
     always 'strong'; very_strong is under-called);
  5. side: support when the zone's midpoint is below the last close, else resistance (67 of the
     sample's 71 zones; the other 4 contain the close).

On the sample, with daily bars, the zones overlap the table's by a mean IoU of about 0.6 (50 of 71
at 0.5 or more); the table's zones moved 2-8% at random reach about 0.43. A time-at-price profile
(volume set to 1 per bar) places the zones as well; volume shows in the grades (Spearman 0.75 vs
0.65). Some of the table's edges are rounded by hand, which this does not copy.

analyze.py shows them as levels.node_zones (reference_block), reference only: bought or shorted for
1-3 months they did no better than the same zones at other prices, and the grades did not sort the
outcomes (node_zones_backtest.py, tranche_backtest.py; methodology.md H.2).
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import levels as L

LOOKBACK = 756              # bars of the profile window (~3 years of daily bars)
BIN_SHARE = 0.0025          # bin width as a share of the last close
SMOOTH_BINS = 2.0           # Gaussian sigma of the smoothing, in bins
CORE_SHARE = 0.3            # zone = the node's bins at >= this share of its smoothed peak
MIN_NODE_SHARE = 0.005      # nodes holding less of the window's volume are dropped
GRADE_CUTS = ((1.9, "very_strong"), (0.8, "strong"), (0.5, "medium"), (0.0, "weak"))
GRADES = ("weak", "medium", "strong", "very_strong")
PER_SIDE = 3                # zones listed above and below the close in levels.node_zones
MAX_BINS = 12000            # no zones when the window's range needs more bins (> 30x the close): the
                            # binning holds bars x bins floats (the 32-symbol tests peak at 9,129)
NOTE = ("reference only: about 3 years of daily bars, each bar's volume spread over its range; the grade is the "
        "zone's volume density, not a strength. In walk-forward tests on 32 US stocks and ETFs (daily bars "
        "2008-2026), buying or shorting at these zones for 1-3 months did no better than at the same zones moved "
        "to other prices, the grades did not sort the outcomes, and as tranche prices they matched a fixed ladder "
        "of the same depth (references/methodology.md H.2)")


def smooth(x: np.ndarray, sigma: float = SMOOTH_BINS) -> np.ndarray:
    """Gaussian smoothing with mirrored ends (scipy's gaussian_filter1d 'reflect' mode)."""
    if sigma <= 0 or len(x) < 2:
        return x.astype(float)
    r = int(4 * sigma + 0.5)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    pad = min(r, len(x) - 1)
    y = np.convolve(np.pad(x.astype(float), pad, mode="symmetric"), k, mode="same")[pad:pad + len(x)]
    return y


def local_minima(y: np.ndarray) -> list[int]:
    """Interior local minima of y; a flat bottom is reported once, at its middle."""
    out, i, n = [], 1, len(y)
    while i < n - 1:
        if y[i] < y[i - 1]:
            j = i
            while j + 1 < n and y[j + 1] == y[i]:
                j += 1
            if j + 1 < n and y[j + 1] > y[i]:
                out.append((i + j) // 2)
            i = j + 1
        else:
            i += 1
    return out


def grade(density: float) -> str:
    return next(g for cut, g in GRADE_CUTS if density >= cut)


def node_zones(bars: pd.DataFrame, lookback: int = LOOKBACK) -> list[dict]:
    """Zones of the last `lookback` bars, highest first: zone_low, zone_high, share (of the
    window's volume), density, grade and side relative to the last close."""
    d = bars[["high", "low", "close", "volume"]].dropna().iloc[-lookback:]
    if len(d) < 2:
        return []
    close = float(d["close"].iloc[-1])
    lo, hi = float(d["low"].min()), float(d["high"].max())
    if hi <= lo or close <= 0:
        return []
    bins = max(1, int(np.ceil((hi - lo) / (BIN_SHARE * close))))
    if bins > MAX_BINS:
        return []
    vol = np.clip(d["volume"].to_numpy(dtype=float), 0.0, None)
    edges, v = L._bin_volumes(d["high"].to_numpy(dtype=float), d["low"].to_numpy(dtype=float), vol, bins)
    if v.sum() <= 0:
        return []
    vs = smooth(v)
    total = vs.sum()
    cuts = [0] + local_minima(vs) + [len(vs)]
    out = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        seg = vs[a:b]
        if seg.sum() < MIN_NODE_SHARE * total:
            continue
        core = np.flatnonzero(seg >= CORE_SHARE * seg.max())
        i0, i1 = a + core[0], a + core[-1]
        z_lo, z_hi = float(edges[i0]), float(edges[i1 + 1])
        share = float(vs[i0:i1 + 1].sum() / total)
        density = share / ((z_hi - z_lo) / (hi - lo))
        out.append({"zone_low": z_lo, "zone_high": z_hi, "share": share, "density": float(density),
                    "grade": grade(density),
                    "side": "support" if (z_lo + z_hi) / 2 < close else "resistance"})
    return sorted(out, key=lambda z: -z["zone_low"])


def reference_block(bars: pd.DataFrame, atr: float | None, lookback: int = LOOKBACK,
                    per_side: int = PER_SIDE) -> dict:
    """levels.node_zones in metrics.json: the zone holding the last close (if any) and the nearest
    `per_side` zones wholly above and wholly below it, nearest first, with the distance of the near
    edge from the close (% and ATRs). Reference only (NOTE)."""
    d = bars[["high", "low", "close", "volume"]].dropna().iloc[-lookback:]
    notes = [] if len(d) >= lookback else [f"node-zone window has only {len(d)} bars (lookback {lookback})"]
    if len(d) >= 2 and d["close"].iloc[-1] > 0:
        ratio = (d["high"].max() - d["low"].min()) / d["close"].iloc[-1]
        if ratio > MAX_BINS * BIN_SHARE:
            return {"available": False, "notes": notes,
                    "reason": f"the window's price range is {ratio:.0f}x the last close (a collapse, reverse "
                              f"splits or a bad print), more than the {MAX_BINS * BIN_SHARE:g}x the bins allow"}
    zones = node_zones(d, lookback)
    if not zones:
        return {"available": False, "reason": "no volume profile (too few bars, no range or no volume)",
                "notes": notes}
    close = float(d["close"].iloc[-1])

    def row(z: dict, edge: float | None) -> dict:
        out = {k: z[k] for k in ("zone_low", "zone_high", "grade", "density", "share")}
        out["distance_pct"] = 0.0 if edge is None else edge / close - 1.0
        out["distance_atr"] = None if not atr else (0.0 if edge is None else (edge - close) / atr)
        return out

    above = sorted((z for z in zones if z["zone_low"] > close), key=lambda z: z["zone_low"])
    below = [z for z in zones if z["zone_high"] < close]          # node_zones lists highest first
    at = next((z for z in zones if z["zone_low"] <= close <= z["zone_high"]), None)
    for side, zs in (("above", above), ("below", below)):
        if not zs:
            notes.append(f"no node zone {side} the last close: price is near the "
                         f"{'top' if side == 'above' else 'bottom'} of the window's range")
    return {
        "available": True,
        "lookback_bars": len(d),
        "window_start": d.index[0].strftime("%Y-%m-%d"),
        "window_end": d.index[-1].strftime("%Y-%m-%d"),
        "method": {"bin_share": BIN_SHARE, "smooth_bins": SMOOTH_BINS, "core_share": CORE_SHARE,
                   "min_node_share": MIN_NODE_SHARE, "grade_cuts": {g: cut for cut, g in GRADE_CUTS}},
        "n_zones": len(zones),
        "at_close": None if at is None else row(at, None),
        "above": [row(z, z["zone_low"]) for z in above[:per_side]],
        "below": [row(z, z["zone_high"]) for z in below[:per_side]],
        "evidence": {"backtested_edge": False, "note": NOTE},
        "notes": notes,
    }


def main(argv=None) -> int:
    import levels_backtest as B
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--lookback", type=int, default=LOOKBACK)
    ap.add_argument("--as-of", help="last bar to use (default: the file's last bar)")
    a = ap.parse_args(argv)
    bars = B.load_csv(a.csv)
    if a.as_of:
        bars = bars.loc[:a.as_of]
    print(f"close {bars['close'].iloc[-1]:.2f} on {bars.index[-1].date()}")
    for z in node_zones(bars, a.lookback):
        print(f"{z['side']:10s} {z['grade']:11s} {z['zone_low']:10.2f} - {z['zone_high']:10.2f}"
              f"  share {z['share']:.1%}  density {z['density']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
