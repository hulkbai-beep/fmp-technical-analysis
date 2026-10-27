"""Tests for scripts/sr_methods.py (no network)."""
import numpy as np
import pandas as pd

import sr_methods as M
from conftest import synthetic_bars


def np_bars(rows):
    """(open, high, low, close) arrays from (o, h, l, c) tuples."""
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def test_trade_target_stop_and_gap():
    # support zone [99, 100], ATR 1: stop 98.5, target 101
    win = np_bars([(103, 103, 102, 102.5), (101, 101, 99.5, 100.5), (100.5, 101.2, 100.2, 101)])
    assert abs(M.trade(win, 0, 99, 100, "support", 1.0, 5) - (1 - 2 * M.COST * 100)) < 1e-12
    loss = np_bars([(103, 103, 102, 102.5), (101, 101, 99.5, 100.5), (100, 100.5, 98.4, 99)])
    assert abs(M.trade(loss, 0, 99, 100, "support", 1.0, 5) - (-1.5 - 2 * M.COST * 100)) < 1e-12
    gap = np_bars([(103, 103, 102, 102.5), (98, 98.2, 97, 97.5)])
    assert np.isnan(M.trade(gap, 0, 99, 100, "support", 1.0, 5))
    # opened inside the zone: filled at the open; resistance mirrored
    inside = np_bars([(97, 97.5, 96.5, 97), (99.5, 99.8, 98, 98.2), (98, 98.1, 97.9, 98)])
    assert abs(M.trade(inside, 0, 99, 100, "resistance", 1.0, 2) - ((99.5 - 98) - 2 * M.COST * 99.5)) < 1e-12


def test_confirmed_outcome():
    up = (100.5, 101.5, 100.3, 101.0)
    closes_back = np_bars([(102, 102, 101.5, 101.8), (100.8, 101, 99.4, 100.6), up, (101, 101.2, 100.5, 101.1)])
    assert M.confirmed_outcome(closes_back, 0, 99, 100, "support", 1.0, 5, 0.0) == "held"
    assert M.confirmed_outcome(closes_back, 0, 99, 100, "support", 1.0, 5, 0.5) == "held"   # tail 1.2 of 1.6
    inside = np_bars([(102, 102, 101.5, 101.8), (100.8, 101, 99.4, 99.8), up])
    assert M.confirmed_outcome(inside, 0, 99, 100, "support", 1.0, 5, 0.0) == "unconfirmed"
    small_tail = np_bars([(102, 102, 101.5, 101.8), (100.2, 102, 99.9, 101.9), up])
    assert M.confirmed_outcome(small_tail, 0, 99, 100, "support", 1.0, 5, 0.5) == "unconfirmed"
    assert M.confirmed_outcome(small_tail, 0, 99, 100, "support", 1.0, 5, 0.0) in ("held", "open")
    assert np.isnan(M.trade(inside, 0, 99, 100, "support", 1.0, 5, wick=0.0))


def test_period_levels_use_the_previous_week():
    idx = pd.bdate_range("2024-01-01", periods=15)
    bars = pd.DataFrame({"open": 10.0, "high": 12.0, "low": 8.0, "close": 11.0, "volume": 1.0}, index=idx)
    bars.loc[idx[5:10], ["high", "low", "close"]] = [20.0, 14.0, 18.0]      # week 2
    lv = M.period_levels(bars, "W-FRI")
    assert lv.iloc[:5].isna().all().all()
    wk3 = lv.loc[idx[10]]
    p = (20 + 14 + 18) / 3
    assert abs(wk3["P"] - p) < 1e-12 and abs(wk3["R1"] - (2 * p - 14)) < 1e-12 and abs(wk3["S2"] - (p - 6)) < 1e-12
    assert abs(wk3["cR3"] - (18 + 1.1 * 6 / 4)) < 1e-12 and abs(lv.loc[idx[7], "P"] - (12 + 8 + 11) / 3) < 1e-12


def test_ichimoku_is_causal():
    bars = synthetic_bars(n=200)
    full, cut = M.ichimoku(bars), M.ichimoku(bars.iloc[:150])
    assert np.allclose(full.iloc[:150].dropna(), cut.dropna())
    t = 120                                                             # span B at t: 52-bar midpoint 26 bars back
    span_b = (bars["high"].iloc[t - 77:t - 25].max() + bars["low"].iloc[t - 77:t - 25].min()) / 2
    assert np.isclose(span_b, full["kumo_top"].iat[t]) or np.isclose(span_b, full["kumo_bottom"].iat[t])


def test_fib_zones_retracement_and_extension():
    fpiv = [(10, -1, 100.0, 12), (20, 1, 110.0, 22)]                    # up leg 100 -> 110, confirmed at 22
    z = M.fib_zones(fpiv, 30, 104.5, 1.0)
    got = {(m, r): (side, round(lo, 3), round(hi, 3)) for m, side, lo, hi, r in z}
    assert got[("fib_ret", 0.5)] == ("resistance", 104.8, 105.2) and got[("fib_ret", 0.618)][0] == "support"
    assert got[("alt_ret", 0.45)][0] == "resistance" and ("fib_ret", 0.382) in got and ("alt_ret", 0.7) in got
    assert not any(m.endswith("_ext") for m, *_ in z) and not M.fib_zones(fpiv, 21, 104.5, 1.0)
    # leg 100 -> 110 before the latest pivot (a pullback low at 106); close back above 110
    fpiv3 = fpiv + [(25, -1, 106.0, 27)]
    ext = [x for x in M.fib_zones(fpiv3, 30, 111.0, 1.0) if x[0].endswith("_ext")]   # reach: up to 115
    assert {(m, k) for m, _, _, _, k in ext} == {("fib_ext", 1.272), ("alt_ext", 1.15), ("alt_ext", 1.45)}
    assert all(side == "resistance" for _, side, *_ in ext)


def test_walk_and_main_report(tmp_path):
    bars = synthetic_bars(n=700)
    rows = M.walk(bars, step=10, forward=10, symbol="X",
                  windows=[(str(bars.index[500].date()), str(bars.index[520].date()))])
    assert {"fresh", "pivot_w", "kijun", "fib_ret", "confirm_close"} <= set(rows["method"])
    assert rows["excluded"].any() and all(len(p) == 2 for p in rows["placebo"])
    conf = rows[rows["method"] == "confirm_close"]
    assert set(conf["outcome"].dropna()) <= {"held", "broken", "open", "unconfirmed", "gap"}
    assert conf.loc[conf["outcome"] == "unconfirmed", "pnl"].isna().all()
    v = M.verdict(rows[~rows["excluded"]])
    assert {"screen", "bonferroni", "bonf_lo", "pnl_edge"} <= set(v.columns)
    lo, bl = v["edge_lo"].astype(float), v["bonf_lo"].astype(float)
    ok = np.isfinite(lo) & np.isfinite(bl)
    assert ok.any() and (bl[ok] <= lo[ok] + 1e-12).all()
    p = tmp_path / "q.csv"
    bars[["open", "high", "low", "close", "volume"]].rename_axis("Date").to_csv(p)
    assert M.main([str(p), "--step", "20", "--split", "2021-06-01", "--exclude", "2021-01-04:2021-01-29",
                   "--out", str(tmp_path / "o")]) == 0
    text = (tmp_path / "o" / "report.md").read_text(encoding="utf-8")
    assert "## Verdict, main sample" in text and "## Trade measure, main sample" in text


def test_hourly_walk_builds_once_per_session_with_daily_pivots():
    from test_regime_split import hourly_bars
    import intraday_levels as IL
    h = hourly_bars(days=120)
    rows = M.walk(h, forward=14, symbol="H", hourly=True)
    pos = IL.session_info(h)["pos"].to_numpy()
    assert set(pos[rows["t"].unique()]) == {M.HOURLY_BUILD_BAR}
    assert {"pivot_d", "camarilla_d"} <= set(rows["method"])
    day = h.index.normalize()
    r = rows[(rows["method"] == "pivot_d")].iloc[0]
    prev = h[day == day.unique()[list(day.unique()).index(day[r["t"]]) - 1]]
    p = (prev["high"].max() + prev["low"].min() + prev["close"].iloc[-1]) / 3
    lv = M.period_levels(h, "D").iloc[r["t"]]
    assert abs(lv["P"] - p) < 1e-9


def test_round_cross_levels_and_walk(tmp_path):
    import round_cross as RC
    from test_regime_split import hourly_bars
    assert RC.crossed(99.5, 100.2, 5.0, 0.0) == 100.0 and RC.crossed(99.5, 100.2, 5.0, 0.3) is None
    assert RC.crossed(101.0, 99.0, 1.0, 0.0) == 99.0 and RC.crossed(98.0, 98.9, 1.0, 0.7) == 98.7
    h = hourly_bars(days=200)
    rows = RC.walk(h, forward=14, symbol="H")
    assert {"round", "control"} == set(rows["kind"])
    assert not rows.assign(d=rows["date"].dt.normalize()).duplicated(["kind", "level", "d"]).any()
    p = tmp_path / "H.csv"
    h.rename_axis("Date").to_csv(p)
    assert RC.main([str(p), "--hourly", "--split", "2021-06-01", "--out", str(tmp_path / "o")]) == 0
    assert "edge" in (tmp_path / "o" / "report.md").read_text(encoding="utf-8")
