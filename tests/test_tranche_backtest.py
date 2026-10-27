"""Tests for scripts/tranche_backtest.py (no network)."""
import math

import numpy as np
import pandas as pd

import tranche_backtest as TB
from conftest import synthetic_bars


def arrs(rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def test_ladder_cost_fills_and_forced_buy():
    # close 100 at t; limit 95 fills on bar 2 at 95; limit 90 never fills -> bought at the last close 104
    a = arrs([(100, 100, 100, 100), (99, 99.5, 97, 98), (96, 96.5, 94, 95.5), (100, 104.5, 99, 104)])
    cost, filled = TB.ladder_cost(a, 0, [95, 90], window=3)
    assert filled == 1
    assert abs(cost - 3 / (1 / 100 + 1 / 95 + 1 / 104)) < 1e-12
    # a bar opening below the limit fills at its open
    b = arrs([(100, 100, 100, 100), (93, 94, 92, 93.5), (94, 95, 93, 94)])
    cost_b, filled_b = TB.ladder_cost(b, 0, [95], window=2)
    assert filled_b == 1 and abs(cost_b - 2 / (1 / 100 + 1 / 93)) < 1e-12


def test_gain_sign():
    assert TB.gain(100, 95) > 0 > TB.gain(100, 105)
    assert abs(TB.gain(100, 100)) < 1e-12
    assert abs(TB.gain(100, 50) - 100 * math.log(2)) < 1e-12


def test_walk_rows_and_no_future_in_limits():
    bars = synthetic_bars(1150, seed=21, vol=0.02)
    full = TB.walk(bars, step=20, window=21, symbol="X")
    assert len(full)
    assert {f"gain_{p}" for p in TB.PLANS} <= set(full.columns)
    assert (full["gain_lump"] == 0).all()
    assert full["p_gain_node"].map(len).eq(2).all()
    cut = 1000
    part = TB.walk(bars.iloc[:cut + 23].copy(), step=20, window=21, symbol="X")
    a = full[full["t"] <= cut][["t", "dist1_pct", "dist2_pct", "grade1"]].reset_index(drop=True)
    b = part[part["t"] <= cut][["t", "dist1_pct", "dist2_pct", "grade1"]].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


def test_verdict_and_plan_table_run():
    bars = synthetic_bars(1300, seed=4, vol=0.02)
    rows = TB.walk(bars, step=10, window=63, symbol="X")
    rows["period"] = np.where(rows["t"] < rows["t"].median(), "train", "test")
    v = TB.verdict(rows)
    assert list(v.index) == list(TB.HYPOTHESES)
    p = TB.plan_table(rows)
    assert list(p.index) == list(TB.PLANS)
    assert abs(p.loc["lump", "mean_gain"]) < 1e-12
