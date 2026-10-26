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


def frame_o(opens, highs, lows, closes):
    df = frame(highs, lows, closes)
    df["open"] = opens
    return df


def test_outcome_gap_ambiguous_and_same_bar_order():
    # support 10..10.5: bar 1 lies wholly below the zone without trading in it -> gap, not broken
    gap = frame([12, 9.8], [11, 9.0], [11.5, 9.5])
    assert B.outcome(gap, 0, 10.0, 10.5, "support", 1.0, 20) == "gap"
    # bar 1 opens above the zone, dips into it and also reaches 11.5: order unknown -> ambiguous
    amb = frame_o([11.8, 11.4], [12, 11.6], [11, 10.3], [11.5, 11.0])
    assert B.outcome(amb, 0, 10.0, 10.5, "support", 1.0, 20) == "ambiguous"
    # same bar but it opened inside the zone: the touch came first -> held
    inside = frame_o([11.8, 10.4], [12, 11.6], [11, 10.3], [11.5, 11.0])
    assert B.outcome(inside, 0, 10.0, 10.5, "support", 1.0, 20) == "held"
    # a later bar reaching the reaction and closing below the zone: the high came before the close -> held
    later = frame_o([11.8, 10.8, 10.6], [12, 11, 11.6], [11, 10.2, 9.0], [11.5, 10.8, 9.5])
    assert B.outcome(later, 0, 10.0, 10.5, "support", 1.0, 20) == "held"
    # resistance 12..12.5, bar 1 wholly above it -> gap
    rgap = frame([11.5, 13.5], [11, 12.8], [11.2, 13.0])
    assert B.outcome(rgap, 0, 12.0, 12.5, "resistance", 1.0, 20) == "gap"


def _rows(symbols=("A", "B"), years=(2019, 2020, 2021), seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for s in symbols:
        for y in years:
            for d in pd.bdate_range(f"{y}-01-01", periods=30):
                out.append({"symbol": s, "date": d, "side": "support", "rank": 1, "direction": "Mixed",
                            "width_atr": 0.5, "dist_atr": 1.0,
                            "outcome": rng.choice(["held", "broken", None, "gap"]),
                            "placebo": list(rng.choice(["held", "broken", "open"], 2))})
    return pd.DataFrame(out)


def test_edge_interval_is_reproducible_and_brackets_the_edge():
    rows = _rows()
    s = B.summarize(rows, by="symbol")
    assert list(s.index) == ["A", "B", "all"]
    lo, hi = B.edge_interval(rows)
    assert (lo, hi) == B.edge_interval(rows)
    assert lo <= s.loc["all", "edge"] <= hi
    assert s.loc["all", "gap"] == (rows["outcome"] == "gap").sum()
    one = rows[(rows["symbol"] == "A") & (rows["date"].dt.year == 2019)]
    assert all(np.isnan(B.edge_interval(one)))


def test_periods_split():
    d = pd.Series(pd.to_datetime(["2010-05-01", "2017-01-01", "2021-12-31", "2023-03-01"]))
    assert list(B.periods(d, ["2017-01-01", "2022-01-01"])) == ["train", "validation", "validation", "test"]
    assert list(B.periods(d, ["2020-01-01"])) == ["p1", "p1", "p2", "p2"]


def test_main_writes_report(tmp_path):
    bars = synthetic_bars(n=420)
    p = tmp_path / "xyz.csv"
    bars[["open", "high", "low", "close", "volume"]].rename_axis("Date").to_csv(p)
    out = tmp_path / "out"
    assert B.main([str(p), "--step", "20", "--split", "2021-01-01", "--out", str(out)]) == 0
    rows = pd.read_csv(out / "rows.csv")
    assert set(rows["symbol"]) == {"XYZ"} and {"direction", "period", "placebo"} <= set(rows.columns)
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "## By symbol" in text and "## By period" in text and "| XYZ |" in text


def test_box_source_offers_only_active_box_edges(tmp_path):
    from test_trading_ranges import path, RANGE
    bars = path(RANGE + [105] * 30, leg=10, start="2020-01-02")
    bars = pd.concat([synthetic_bars(n=300, start="2018-11-01").iloc[:, :5].iloc[:-1]
                      .loc[lambda d: d.index < bars.index[0]], bars])
    rows = B.walk(bars, "swing", step=5, forward=10, build=B.box_build(bars), symbol="BOX")
    assert not rows.empty and set(rows["rank"]) == {1}
    assert (rows["side"] == "support").any() and (rows["side"] == "resistance").any()
    assert (rows["width_atr"] < 1).all()
