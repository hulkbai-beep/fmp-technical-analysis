"""price_action.py: bar types, pullback counts, pivots and wedges, failed breakouts, always-in,
events, the module output and the chart payload. No network."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import config as C
import indicators as I
import price_action as PA


def frame(rows, start="2024-01-01"):
    """Bars from [open, high, low, close] rows (volume 1M)."""
    idx = pd.bdate_range(start, periods=len(rows), name="date")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["volume"] = 1e6
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c]
    return df


def test_bar_types():
    bars = frame([[10, 11, 9.8, 10.9],     # body 0.9 / range 1.2 -> bull trend bar
                  [10.9, 11, 10, 10.1],    # bear trend bar
                  [10, 11, 9, 10.05],      # doji
                  [10, 11, 9, 10.6],       # body 0.3 of range -> weak bull
                  [10.6, 11, 9, 10],       # weak bear
                  [10, 10, 10, 10]])       # no range -> doji
    assert PA.bar_types(bars).tolist() == [2, -2, 0, 1, -1, 0]


def test_pullback_counts_h1_h2_and_reset_on_new_high():
    # rally, pullback with two attempts higher, then a new high
    highs = np.array([10, 11, 12, 11.5, 11.8, 11.2, 11.6, 12.5, 12.4])
    lows = highs - 1
    cnt, ext = PA.pullback_counts(highs, lows, bull=True)
    assert cnt.tolist() == [0, 0, 0, 0, 1, 0, 2, 0, 0]
    assert ext[6] == pytest.approx(10.2)          # the pullback low so far
    # after the new high (bar 7) a fresh pullback starts at bar 8 with no count yet
    cnt, _ = PA.pullback_counts(highs, lows, bull=True, context=np.array([True] * 6 + [False] * 3))
    assert cnt[6] == 0                            # context lost: no count


def test_pullback_counts_mirror_for_lows():
    lows = np.array([20, 19, 18, 18.5, 18.2, 18.8, 18.4, 17.5])
    highs = lows + 1
    cnt, ext = PA.pullback_counts(highs, lows, bull=False)
    assert cnt.tolist() == [0, 0, 0, 0, 1, 0, 2, 0]
    assert ext[6] == pytest.approx(19.8)          # the rally high so far


def test_pullback_counts_stop_after_max():
    highs = np.array([10, 11] + [10.5, 10.8] * (C.PA_COUNT_MAX + 1), dtype=float)
    cnt, _ = PA.pullback_counts(highs, highs - 1, bull=True)
    assert cnt.max() == C.PA_COUNT_MAX


def test_confirmed_pivots_are_causal():
    h = np.array([1, 2, 5, 2, 1, 3, 1], dtype=float)
    l = h - 0.5
    piv = PA.confirmed_pivots(h, l, 2)
    hi = [p for p in piv if p["kind"] == "H"]
    assert hi == [{"kind": "H", "i": 2, "confirm": 4, "price": 5.0}]   # bar 5 (3) lacks a right side


def zigzag(points, step=1.0):
    """Bars that walk linearly between the given closes (one bar per unit of `step`)."""
    closes = []
    for a, b in zip(points[:-1], points[1:]):
        k = max(1, int(round(abs(b - a) / step)))
        closes += list(np.linspace(a, b, k + 1)[:-1])
    closes.append(points[-1])
    c = np.array(closes)
    return frame([[x, x + 0.2, x - 0.2, x] for x in c])


def test_wedge_top_with_fading_pushes_fires_on_confirmation():
    bars = zigzag([100, 110, 105, 116, 112, 119, 112])    # pushes 110, 116, 119: 6 then 3 -> fading
    fr = PA.analyze_frame(bars, I.compute_all(bars))
    tops = [w for w in fr["wedges"] if w["side"] == "top"]
    assert len(tops) == 1
    assert [round(p) for _, p in tops[0]["pushes"]] == [110, 116, 119]
    assert tops[0]["confirm"] == tops[0]["pushes"][2][0] + C.PA_PIVOT_K


def test_accelerating_pushes_are_not_a_wedge():
    bars = zigzag([100, 104, 101, 108, 105, 116, 110])    # 4 then 8: accelerating
    fr = PA.analyze_frame(bars, I.compute_all(bars))
    assert not [w for w in fr["wedges"] if w["side"] == "top"]


def test_failed_breakouts():
    n = 8
    close = np.full(n, 10.0)
    high, low = close + 0.5, close - 0.5
    rh, rl = np.full(n, 10.5), np.full(n, 9.5)
    is_tr = np.ones(n, dtype=bool)
    high[3], close[3] = 10.9, 10.7             # breaks out, closes outside
    close[4] = 10.3                            # back inside next bar: failed
    low[6], close[6] = 9.2, 9.6                # wick below, closes back inside the same bar
    out = PA.failed_breakouts(close, high, low, rh, rl, is_tr)
    assert [(f["side"], f["breakout"], f["fail"]) for f in out] == [("up", 3, 4), ("down", 6, 6)]


def test_breakout_that_holds_is_not_failed():
    n = 8
    close = np.full(n, 10.0)
    high, low = close + 0.5, close - 0.5
    high[3:], close[3:] = 11.5, 11.2           # stays above the range
    out = PA.failed_breakouts(close, high, low, np.full(n, 10.5), np.full(n, 9.5), np.ones(n, dtype=bool))
    assert out == []


def test_always_in_flips_on_consecutive_trend_bars():
    close = np.array([10, 10.2, 11, 12, 12.1, 11, 10, 10.2])
    high = close + 0.1
    low = close - 0.1
    ema = np.full(8, 10.5)
    types = np.array([0, 0, 2, 2, 0, -2, -2, 0])
    state = np.zeros(8)
    ai = PA.always_in(close, high, low, ema, types, state)
    # long after bars 2-3; bar 5 closes above the EMA, so bars 5-6 are not two bear trend bars below it
    assert ai.tolist() == [0, 0, 0, 1, 1, 1, 1, 1]
    close2 = close.copy()
    close2[5], close2[6] = 10.4, 10.0
    ai2 = PA.always_in(close2, close2 + 0.1, close2 - 0.1, ema, types, state)
    assert ai2[6] == -1
    # a tight channel sets the direction by itself
    assert PA.always_in(close, high, low, ema, np.zeros(8, dtype=int), np.full(8, -2.0)).tolist() == [-1] * 8


def test_events_shape_and_warmup(make_bars):
    bars = make_bars(n=900, seed=11, vol=0.02)
    ind = I.compute_all(bars)
    ev = PA.events(bars, ind)
    assert list(ev.columns) == PA.SIGNAL_IDS and ev.index.equals(bars.index)
    assert all(ev[c].dtype == bool for c in ev.columns)
    assert not ev.iloc[:PA.WARMUP].to_numpy().any()
    assert ev.to_numpy().any()


def test_patterns_md_lists_every_rule_as_coded():
    text = (Path(__file__).resolve().parents[1] / "references" / "patterns.md").read_text(encoding="utf-8")
    for s in PA.SIGNALS:
        assert f"`{s['id']}`" in text and f"`{s['rule']}`" in text, s["id"]


def ctx_for(bars, horizon="swing"):
    return {"ticker": "TEST", "bars": bars, "ind": I.compute_all(bars), "hz": C.HORIZONS[horizon]}


def test_module_output(make_bars):
    out = PA.build_price_action(ctx_for(make_bars(n=1200, seed=4, vol=0.018)))
    json.dumps(out, allow_nan=False, default=str)
    assert out["available"] and out["state"]["label"] in PA.STATES.values()
    assert out["always_in"]["direction"] in ("long", "short", "none")
    assert out["bar"]["type"] in PA.BAR_TYPES.values()
    assert [r["id"] for r in out["library"]] == PA.SIGNAL_IDS
    assert set(out["inactive_ids"]) | {a["id"] for a in out["active"]} == set(PA.SIGNAL_IDS)
    for a in out["active"]:
        assert a["bars_ago"] < C.SIGNAL_ACTIVE_BARS and str(C.HORIZONS["swing"]["primary"]) in json.dumps(a["horizons"])
    for m in out["measured_moves"]:
        assert m["kind"] in ("leg", "range") and m["side"] in ("up", "down") and np.isfinite(m["target"])


def test_module_short_history_is_unavailable(make_bars):
    out = PA.build_price_action(ctx_for(make_bars(n=PA.WARMUP)))
    assert out["available"] is False and out["reason"]


def test_chart_payload_matches_the_window(make_bars):
    bars = make_bars(n=700, seed=8, vol=0.02)
    ctx = ctx_for(bars)
    mods = {"price_action": PA.build_price_action(ctx)}
    win = bars.index[-252:]
    p = PA.chart_payload(ctx, mods, win)
    assert p["available"]
    assert len(p["bar_types"]) == len(p["states"]) == len(p["always_in"]) == 252
    assert all(m["date"] >= str(win[0].date()) for m in p["markers"])
    assert all(r["start"] <= r["end"] for r in p["ranges"])
    # a window that ends before the last bar stops there
    p2 = PA.chart_payload(ctx, mods, bars.index[-300:-48])
    assert len(p2["bar_types"]) == 252 and all(m["date"] <= str(bars.index[-49].date()) for m in p2["markers"])
