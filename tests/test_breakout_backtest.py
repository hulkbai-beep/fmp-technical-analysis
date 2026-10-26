"""Tests for scripts/breakout_backtest.py on hand-built bars (no network)."""
import numpy as np
import pandas as pd

import breakout_backtest as BB
from conftest import synthetic_bars
from test_trading_ranges import path, RANGE


def test_first_passage_and_labels():
    high = np.array([10, 11, 12.5, 10])
    low = np.array([9, 9.5, 10, 7.5])
    assert BB.first_passage((high, low), 0, 12.0, 8.0, 10) == "up"
    assert BB.first_passage((high, low), 2, 12.0, 8.0, 10) == "down"
    assert BB.first_passage((high, low), 0, 12.0, 8.0, 1) == "open"
    both = (np.array([10, 13]), np.array([9, 7]))
    assert BB.first_passage(both, 0, 12.0, 8.0, 5) == "ambiguous"
    assert BB._label("up", 1) == "held" and BB._label("up", -1) == "broken" and BB._label("open", 1) == "open"


def test_donchian_events_are_declustered():
    bars = path([100, 190], leg=60)                     # a steady rise: new highs every bar
    ev = BB.donchian_events(bars)
    ups = [e["t"] for e in ev if e["d"] == 1]
    assert ups and all(b - a > BB.DONCHIAN_BARS for a, b in zip(ups, ups[1:]))


def test_walk_finds_the_box_breakout():
    box = path(RANGE + [125, 135, 140], leg=10, start="2020-01-02")
    lead = synthetic_bars(n=80, start="2019-09-02").iloc[:, :5]
    lead = lead[lead.index < box.index[0]] * (90 / lead["close"].iloc[-1])
    bars = pd.concat([lead.assign(volume=1e6), box])
    rows = BB.walk(bars, forward=10, symbol="X")
    b = rows[rows["kind"] == "box_2atr"]
    assert len(b) >= 1 and (b["side"] == "up").any()
    assert {"box_2atr", "box_measured", "donchian_2atr"} <= set(rows["kind"])
    assert all(len(p) == len(BB.PLACEBO_OFFSET_BARS) for p in rows["placebo"])


def test_main_writes_report(tmp_path):
    p = tmp_path / "abc.csv"
    synthetic_bars(n=900, seed=4)[["open", "high", "low", "close", "volume"]].rename_axis("Date").to_csv(p)
    assert BB.main([str(p), "--split", "2021-06-01", "--out", str(tmp_path / "o")]) == 0
    text = (tmp_path / "o" / "report.md").read_text(encoding="utf-8")
    assert "## By variant" in text and "donchian_2atr" in text
