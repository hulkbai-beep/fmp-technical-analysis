"""Tests for scripts/node_zones.py and scripts/node_zones_backtest.py (no network)."""
import math

import numpy as np
import pandas as pd

import node_zones as NZ
import node_zones_backtest as NB
from conftest import synthetic_bars


def band_bars(bands, n_each=200, seed=3):
    """Daily bars that trade inside each (low, high, volume) band in turn, one bar per day."""
    rng = np.random.default_rng(seed)
    rows = []
    for lo, hi, vol in bands:
        for _ in range(n_each):
            a, b = np.sort(rng.uniform(lo, hi, 2))
            o, c = rng.uniform(a, b, 2)
            rows.append((o, b, a, c, vol))
    idx = pd.bdate_range("2015-01-02", periods=len(rows), name="date")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)


def test_smooth_keeps_mass_and_minima():
    x = np.r_[np.zeros(5), np.ones(10), np.zeros(5), 3 * np.ones(10), np.zeros(5)]
    y = NZ.smooth(x, 2.0)
    assert abs(y.sum() - x.sum()) < 1e-9
    m = NZ.local_minima(y)
    assert len(m) == 1 and 15 <= m[0] <= 19
    assert NZ.local_minima(np.array([3.0, 1.0, 1.0, 1.0, 3.0])) == [2]
    assert NZ.local_minima(np.array([1.0, 2.0, 3.0])) == []


def test_two_bands_give_two_zones_graded_by_density():
    bars = band_bars([(100, 110, 1e6), (130, 135, 3e6)])
    zones = NZ.node_zones(bars, lookback=len(bars))
    assert len(zones) == 2
    top, bottom = zones                       # highest first
    assert 128 <= top["zone_low"] < 131 and 134 < top["zone_high"] <= 136
    assert 98 <= bottom["zone_low"] < 102 and 108 < bottom["zone_high"] <= 111
    assert top["density"] > bottom["density"]
    assert NZ.GRADES.index(top["grade"]) >= NZ.GRADES.index(bottom["grade"])
    assert bottom["side"] == "support"        # last close is in the upper band
    assert abs(sum(z["share"] for z in zones) - 1) < 0.1


def test_reference_block_splits_zones_around_the_close():
    # three bands; the last bars trade in the middle one, so it holds the close
    bars = band_bars([(100, 110, 1e6), (160, 165, 3e6), (130, 135, 2e6)])
    out = NZ.reference_block(bars, atr=2.0, lookback=len(bars))
    assert out["available"] and out["lookback_bars"] == len(bars) and out["n_zones"] == 3
    close = bars["close"].iloc[-1]
    at, (up,), (down,) = out["at_close"], out["above"], out["below"]
    assert at["zone_low"] <= close <= at["zone_high"] and at["distance_pct"] == 0 == at["distance_atr"]
    assert up["zone_low"] > close and down["zone_high"] < close
    assert up["distance_pct"] == up["zone_low"] / close - 1 and up["distance_atr"] == (up["zone_low"] - close) / 2.0
    assert down["distance_pct"] == down["zone_high"] / close - 1 < 0
    assert {"zone_low", "zone_high", "side", "grade", "density", "share"} <= up.keys()
    # above the close = resistance; holding it or below = support
    assert (up["side"], at["side"], down["side"]) == ("resistance", "support", "support")
    assert out["evidence"] == {"backtested_edge": False, "note": NZ.NOTE} and out["notes"] == []


def test_a_zone_holding_the_close_is_support_even_above_its_midpoint():
    # the last bars trade near the bottom of the upper band, so its midpoint is above the close
    bars = band_bars([(100, 110, 1e6), (130, 140, 3e6)])
    bars.iloc[-20:] = [131.0, 132.0, 130.5, 131.5, 3e6]
    zones = NZ.node_zones(bars, lookback=len(bars))
    close = bars["close"].iloc[-1]
    (held,) = [z for z in zones if z["zone_low"] <= close <= z["zone_high"]]
    assert (held["zone_low"] + held["zone_high"]) / 2 > close and held["side"] == "support"
    out = NZ.reference_block(bars, atr=1.0, lookback=len(bars))
    assert out["at_close"]["side"] == "support" and all(z["side"] == "resistance" for z in out["above"])


def test_reference_block_nearest_first_per_side_and_notes():
    bands = [(100, 104, 1e6), (110, 114, 1e6), (120, 124, 1e6), (130, 134, 1e6), (140, 144, 1e6)]
    bars = band_bars(bands, n_each=60)
    out = NZ.reference_block(bars, atr=None, lookback=1000, per_side=2)
    assert out["above"] == [] and out["at_close"] is not None
    assert [z["zone_high"] for z in out["below"]] == sorted((z["zone_high"] for z in out["below"]), reverse=True)
    assert len(out["below"]) == 2 and all(z["distance_atr"] is None for z in out["below"])
    assert any("only 300 bars" in n for n in out["notes"]) and any("no node zone above" in n for n in out["notes"])
    assert NZ.reference_block(bars.iloc[:1], atr=1.0)["available"] is False


def test_reference_block_refuses_a_range_too_wide_for_its_bins():
    # a 1000x fall over the window would need ~400,000 bins of 0.25% of the last close
    n = 756
    close = 1000.0 * np.exp(-np.log(1000.0) * np.arange(n) / (n - 1))
    idx = pd.bdate_range("2020-01-02", periods=n, name="date")
    bars = pd.DataFrame({"open": close, "high": close * 1.02, "low": close * 0.98, "close": close,
                         "volume": 1e6}, index=idx)
    out = NZ.reference_block(bars, atr=1.0)
    assert out["available"] is False and "x the last close" in out["reason"]
    assert NZ.node_zones(bars) == []


def test_grade_cuts():
    assert NZ.grade(0.1) == "weak" and NZ.grade(0.5) == "medium"
    assert NZ.grade(0.8) == "strong" and NZ.grade(2.5) == "very_strong"


def arrs(rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def test_trade_support_fill_exit_and_mae():
    # zone [99, 100], ATR 1; touched on bar 1 (fill at 100), low 98.5 on bar 2, exit at close of bar 3
    a = arrs([(103, 103, 102, 102.5), (101, 101, 99.5, 100.5), (100, 100.2, 98.5, 99), (99, 104, 99, 103.5)])
    ret, mae = NB.trade(a, 0, 99, 100, "support", 1.0, entry=5, hold=2)
    assert abs(ret - (103.5 - 100 - NB.COST * 203.5)) < 1e-12
    assert abs(mae - 1.5) < 1e-12
    # opened below the near edge: filled at the open
    a2 = arrs([(103, 103, 102, 102.5), (99.5, 100.5, 99.2, 100), (100, 101, 99.8, 100.5)])
    ret2, _ = NB.trade(a2, 0, 99, 100, "support", 1.0, entry=5, hold=1)
    assert abs(ret2 - (100.5 - 99.5 - NB.COST * 200)) < 1e-12


def test_trade_gap_unreached_and_short_data():
    gap = arrs([(103, 103, 102, 102.5), (98, 98.5, 97, 97.5), (97, 98, 96, 97)])
    assert all(math.isnan(v) for v in NB.trade(gap, 0, 99, 100, "support", 1.0, 5, 1))
    far = arrs([(103, 103, 102, 102.5), (103, 104, 102.5, 103.5), (103, 104, 102.5, 103.5)])
    assert all(math.isnan(v) for v in NB.trade(far, 0, 99, 100, "support", 1.0, 5, 1))
    short = arrs([(103, 103, 102, 102.5), (101, 101, 99.5, 100.5)])
    assert all(math.isnan(v) for v in NB.trade(short, 0, 99, 100, "support", 1.0, 5, 3))


def test_trade_resistance_is_mirrored():
    # zone [105, 106]: short filled at 105 on bar 1, high 106.5, exit 103 at bar 2's close
    a = arrs([(102, 103, 101.5, 102.5), (104, 106.5, 103.8, 104), (104, 104.2, 102.8, 103)])
    ret, mae = NB.trade(a, 0, 105, 106, "resistance", 1.0, entry=5, hold=1)
    assert abs(ret - (105 - 103 - NB.COST * 208)) < 1e-12
    assert abs(mae - 1.5) < 1e-12


def test_walk_zones_use_no_future_bars():
    bars = synthetic_bars(1100, seed=11, vol=0.02)
    cut = 950
    full = NB.walk(bars, step=25, entry=5, holds=(5, 10))
    part = NB.walk(bars.iloc[:cut + 16].copy(), step=25, entry=5, holds=(5, 10))
    cols = ["t", "side", "rank", "grade", "lo_atr", "hi_atr"]
    a = full[full["t"] <= cut][cols].reset_index(drop=True)
    b = part[part["t"] <= cut][cols].reset_index(drop=True)
    assert len(a) and len(a) == len(b)
    pd.testing.assert_frame_equal(a, b)
    assert {"ret5", "mae10", "p_ret10", "placebo"} <= set(full.columns)


def test_verdict_runs_on_walk_rows():
    bars = synthetic_bars(1200, seed=5, vol=0.02)
    rows = NB.walk(bars, step=10, entry=21, holds=(21, 63), symbol="X")
    rows["period"] = np.where(rows["date"] < rows["date"].iloc[len(rows) // 2], "train", "test")
    v = NB.verdict(rows)
    assert list(v.index) == list(NB.HYPOTHESES)
    assert set(v["screen"]) <= {"yes", "no"}
