"""Tests for scripts/levels_backtest.py on hand-built bars (no network)."""
import numpy as np
import pandas as pd
import pytest

import levels_backtest as B
from conftest import synthetic_bars


def frame(highs, lows, closes):
    idx = pd.bdate_range("2024-01-01", periods=len(highs), name="date")
    return pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes, "volume": 1000.0}, index=idx)


def test_outcome_held_broken_open_and_untouched():
    # support zone 10..10.5, ATR 1: bar 1 trades into it, bar 2 reaches 11.5 -> held
    held = frame([12, 11, 11.6], [11, 10.2, 10.6], [11.5, 10.8, 11.4])
    assert B.outcome(held, 0, 10.0, 10.5, "support", 1.0, 20) == "held"
    broken = frame([12, 11, 10.2], [11, 10.2, 9.5], [11.5, 10.8, 9.8])    # close 9.8 < 10 first
    assert B.outcome(broken, 0, 10.0, 10.5, "support", 1.0, 20) == "broken"
    open_ = frame([12, 11, 11.2], [11, 10.2, 10.6], [11.5, 10.8, 11.0])
    assert B.outcome(open_, 0, 10.0, 10.5, "support", 1.0, 20) == "open"
    assert B.outcome(open_, 0, 8.0, 8.5, "support", 1.0, 20) is None
    # resistance 12..12.5 mirrored: bar 1 trades up into it, bar 2 falls to 11.0 -> held
    res = frame([11.5, 12.2, 11.6], [11, 11.6, 10.9], [11.2, 12.0, 11.1])
    assert B.outcome(res, 0, 12.0, 12.5, "resistance", 1.0, 20) == "held"


def test_load_csv_normalizes_columns(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("Date,Open,High,Low,Close,Adj Close,Volume\n"
                 "2024-01-03,2,3,1,2.5,2.4,100\n2024-01-02,1,2,0.5,1.5,1.4,100\n2024-01-04,null,3,1,2,2,0\n")
    df = B.load_csv(p)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert list(df.index.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-03"]


def test_walk_uses_only_past_bars_and_summarizes():
    bars = synthetic_bars(n=420)
    seen = []

    def build(ctx):
        seen.append((ctx["bars"].index[-1], ctx["as_of"]))
        close = float(ctx["bars"]["close"].iloc[-1])
        atr = float(ctx["ind"]["atr14"].iloc[-1])
        return {"available": True, "last_close": close, "atr": atr,
                "supports": [{"price": close - atr, "zone_low": close - 1.2 * atr, "zone_high": close - 0.8 * atr}],
                "resistances": []}

    rows = B.walk(bars, "swing", step=20, forward=20, build=build)
    assert all(last.strftime("%Y-%m-%d") == as_of for last, as_of in seen)
    assert (rows["side"] == "support").all() and rows["width_atr"].to_numpy() == pytest.approx(0.4)
    assert all(len(p) == len(B.PLACEBO_OFFSETS) for p in rows["placebo"])
    s = B.summarize(rows)
    assert list(s.index) == ["S1", "all"]
    assert s.loc["all", "n"] == len(rows)
    assert np.isfinite(s.loc["all", "edge"])
