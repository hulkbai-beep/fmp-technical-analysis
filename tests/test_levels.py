"""Tests for scripts/levels.py (module H) on hand-built bars with known answers."""
import sys
import types

import numpy as np
import pandas as pd
import pytest

import config as C
import levels as L
from conftest import synthetic_bars

REQUIRED_LEVEL_KEYS = {"price", "zone_low", "zone_high", "sources", "touches", "volume_share",
                       "score", "distance_pct", "distance_atr"}


def frame(highs, lows, volumes=None, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(highs), name="date")
    high, low = np.asarray(highs, dtype=float), np.asarray(lows, dtype=float)
    vol = np.full(len(high), 1000.0) if volumes is None else np.asarray(volumes, dtype=float)
    close = (high + low) / 2.0
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def local_indicators(bars):
    """atr14 / sma50 / sma200 from indicators.py, or computed here if it is not importable."""
    try:
        import indicators
        return indicators.compute_all(bars)
    except ImportError:
        prev = bars["close"].shift(1)
        tr = pd.concat([bars["high"] - bars["low"], (bars["high"] - prev).abs(),
                        (bars["low"] - prev).abs()], axis=1).max(axis=1)
        atr = np.full(len(tr), np.nan)
        atr[13] = tr.iloc[:14].mean()
        for i in range(14, len(tr)):
            atr[i] = atr[i - 1] + (tr.iloc[i] - atr[i - 1]) / 14
        return pd.DataFrame({"atr14": atr,
                             "sma50": bars["close"].rolling(50).mean(),
                             "sma200": bars["close"].rolling(200).mean()}, index=bars.index)


def make_ctx(bars, horizon="swing"):
    return {"ticker": "TEST", "as_of": bars.index[-1].strftime("%Y-%m-%d"), "horizon": horizon,
            "hz": C.HORIZONS[horizon], "bars": bars, "ind": local_indicators(bars),
            "is_etf": False, "earnings": None, "skipped": {}, "notes": []}


# ---------------------------------------------------------------------------- pivots
def test_fractal_pivots_zigzag():
    highs = [10, 11, 12, 11, 10, 11, 13, 12, 11, 12, 11]
    lows = [h - 1 for h in highs]
    df = frame(highs, lows)
    piv = L.fractal_pivots(df, k=2)
    assert [p["price"] for p in piv["highs"]] == [12.0, 13.0]
    assert [p["date"] for p in piv["highs"]] == [df.index[2].strftime("%Y-%m-%d"),
                                                 df.index[6].strftime("%Y-%m-%d")]
    # bar 4 is the strict lowest low of bars 2..6; bar 8 ties with bar 10 (not strict) -> no pivot
    assert [p["price"] for p in piv["lows"]] == [9.0]


def test_fractal_pivots_need_k_bars_on_the_right_and_strict_extremes():
    df = frame([10, 12, 11, 10, 11, 14, 13], [9, 8, 9, 7, 9, 9, 8])
    piv = L.fractal_pivots(df, k=2)
    assert [p["price"] for p in piv["highs"]] == []        # 14 at bar 5 has only one bar after it
    assert [p["price"] for p in piv["lows"]] == [7.0]
    flat = frame([10, 11, 12, 12, 11, 10], [9, 9, 9, 9, 9, 9])
    assert L.fractal_pivots(flat, k=2) == {"highs": [], "lows": []}   # equal highs: no pivot


# ---------------------------------------------------------------------------- volume profile
def test_volume_profile_exact_bins_poc_and_value_area():
    # bins of width 1 on [10, 14]; A spreads over bins 0-1, B over 1-3, C (high == low) sits in bin 2
    df = frame([12, 14, 12.5], [10, 11, 12.5], [200, 330, 50])
    vp = L.volume_profile(df, bins=4)
    assert [b["low"] for b in vp["bins"]] == [10, 11, 12, 13]
    assert [b["volume"] for b in vp["bins"]] == pytest.approx([100, 210, 160, 110])
    assert vp["poc"] == pytest.approx(11.5)
    # 70% of 580 = 406: POC 210, + bin 2 (160 > 100) = 370, + bin 3 (110 > 100) = 480
    assert (vp["value_area_low"], vp["value_area_high"]) == pytest.approx((11.0, 14.0))
    assert vp["value_area_volume_share"] == pytest.approx(480 / 580)
    assert vp["total_volume"] == pytest.approx(580)
    assert vp["note"] == L.PROFILE_NOTE


def test_volume_profile_partial_overlap_and_tied_neighbours():
    # edges 0, .5, 1, 1.5, 2: bar 1 gives 25 per bin, bar 2 adds 40 to bin 1
    df = frame([2.0, 1.0], [0.0, 0.5], [100, 40])
    vp = L.volume_profile(df, bins=4)
    assert [b["volume"] for b in vp["bins"]] == pytest.approx([25, 65, 25, 25])
    assert vp["poc"] == pytest.approx(0.75)
    # 65 < 98 -> neighbours tie at 25 and both join -> 115 >= 98
    assert (vp["value_area_low"], vp["value_area_high"]) == pytest.approx((0.0, 1.5))


def test_volume_profile_hvn_lvn_exclude_edges():
    # point bars (high == low) put volume straight into bins of width 1 on [0, 7]
    prices = [0.0, 1.5, 2.5, 3.5, 4.5, 5.5, 7.0]
    vols = [1, 10, 80, 10, 2, 10, 10]         # bin 0 is a trough but an edge bin
    vp = L.volume_profile(frame(prices, prices, vols), bins=7)
    assert [b["volume"] for b in vp["bins"]] == pytest.approx(vols)
    assert vp["hvn"] == pytest.approx([2.5])
    assert vp["lvn"] == pytest.approx([4.5])


def test_volume_profile_empty_plateau_is_one_lvn():
    prices = [0.0, 0.5, 3.5, 4.0]
    vp = L.volume_profile(frame(prices, prices, [10, 10, 10, 10]), bins=4)
    assert [b["volume"] for b in vp["bins"]] == pytest.approx([20, 0, 0, 20])
    assert vp["lvn"] == pytest.approx([2.0])
    assert vp["hvn"] == []


def test_volume_profile_without_volume_is_unavailable():
    vp = L.volume_profile(frame([2, 3], [1, 2], [0, 0]), bins=4)
    assert vp["available"] is False and vp["reason"]


# ---------------------------------------------------------------------------- gaps
def test_unfilled_and_partially_filled_gaps():
    highs = [10, 12, 12.5, 15, 14, 11.9, 12.0, 11.0]
    lows = [9, 11, 10.6, 13, 12.4, 11.5, 11.2, 10.7]
    df = frame(highs, lows)
    gaps = L.unfilled_gaps(df)
    d = [x.strftime("%Y-%m-%d") for x in df.index]
    assert [(g["date"], g["direction"]) for g in gaps] == [(d[1], "up"), (d[5], "down"), (d[7], "down")]
    up, down, fresh = gaps
    # gap up 10..11, later lows reach 10.6 -> remaining zone 10..10.6
    assert (up["bottom"], up["top"], up["gap_top"]) == (10.0, 10.6, 11.0)
    assert up["partially_filled"] and up["filled"] is False
    # bar 3's gap up (12.5..13) is fully filled by bar 4's low 12.4 -> not returned
    # gap down 11.9..12.4, bar 6's high 12.0 -> remaining zone 12.0..12.4
    assert (down["bottom"], down["top"], down["gap_bottom"]) == (12.0, 12.4, 11.9)
    assert down["partially_filled"]
    assert (fresh["bottom"], fresh["top"], fresh["partially_filled"]) == (11.0, 11.2, False)


def test_gap_filled_exactly_at_the_prior_extreme():
    df = frame([10, 12, 11], [9, 11, 10])       # bar 2 low 10 == bar 0 high 10 -> filled
    assert L.unfilled_gaps(df) == []


# ---------------------------------------------------------------------------- merge / score / select
def test_merge_levels_within_half_atr_uses_running_mean():
    pts = [(100.0, "pivot_low"), (100.3, "poc"), (100.45, "pivot_low"), (101.2, "sma50"), (99.0, "low_52w")]
    cands = [{"price": p, "source": s} for p, s in pts]
    clusters = L.merge_levels(cands, atr=1.0, merge_atr=0.5)
    assert [c["price"] for c in clusters] == pytest.approx([99.0, 100.25, 101.2])
    mid = clusters[1]
    assert (mid["zone_low"], mid["zone_high"]) == (100.0, 100.45)
    assert mid["sources"] == ["pivot_low", "poc"]
    assert mid["source_types"] == ["pivot", "volume_profile"]
    assert mid["n_candidates"] == 3


def test_merge_levels_boundary_and_no_chaining():
    exact = L.merge_levels([{"price": 10.0, "source": "a"}, {"price": 10.5, "source": "b"}], 1.0, 0.5)
    assert len(exact) == 1                               # exactly 0.5 ATR apart -> merged
    chain = L.merge_levels([{"price": p, "source": "x"} for p in (0.0, 0.5, 1.0)], 1.0, 0.5)
    # 1.0 is 0.75 from the running mean 0.25 -> new cluster
    assert [c["price"] for c in chain] == pytest.approx([0.25, 1.0])
    assert L.merge_levels([{"price": float("nan"), "source": "x"}], 1.0, 0.5) == []


def test_score_levels_touches_volume_and_source_bonus():
    # four bars; the level at 10 is touched by bar 0's low and bar 2's high, bar 1 crosses it
    df = frame([11, 12, 10.1, 13], [10.05, 8, 9, 12])
    vp = L.volume_profile(df, bins=5)
    clusters = [{"price": 10.0, "zone_low": 10.0, "zone_high": 10.0, "sources": ["pivot_low", "poc"],
                 "source_types": ["pivot", "volume_profile"], "n_candidates": 2},
                {"price": 12.5, "zone_low": 12.5, "zone_high": 12.5, "sources": ["sma50"],
                 "source_types": ["moving_average"], "n_candidates": 1}]
    scored = L.score_levels(clusters, df, vp, atr=0.4)    # band = level +- 0.1
    assert [c["touches"] for c in scored] == [2, 0]
    expected_share = L._zone_volume(vp, 9.9, 10.1) / vp["total_volume"]
    assert scored[0]["volume_share"] == pytest.approx(expected_share)
    assert scored[0]["score"] == pytest.approx(1.0 + scored[0]["volume_share"] / max(
        c["volume_share"] for c in scored) + C.LEVEL_SOURCE_BONUS)
    assert scored[1]["score"] == pytest.approx(scored[1]["volume_share"] / scored[0]["volume_share"])


def test_merge_levels_keeps_a_candidate_zone():
    cands = [{"price": 100.0, "zone_low": 99.6, "zone_high": 100.4, "source": "gap_up"},
             {"price": 100.3, "source": "pivot_low"}]
    (cl,) = L.merge_levels(cands, atr=1.0, merge_atr=0.5)
    assert (cl["zone_low"], cl["zone_high"]) == (99.6, 100.4)
    assert cl["price"] == pytest.approx(100.15)
    assert cl["source_types"] == ["gap", "pivot"]


def test_gap_candidates_one_zone_when_narrow_two_edges_when_wide():
    narrow = {"date": "2024-03-01", "direction": "up", "top": 10.8, "bottom": 10.0}
    (zone,) = L._gap_candidates(narrow, atr=1.0)                   # 0.8 ATR <= GAP_ZONE_MAX_ATR
    assert zone == {"price": pytest.approx(10.4), "zone_low": 10.0, "zone_high": 10.8, "source": "gap_up",
                    "date": "2024-03-01"}
    wide = {**narrow, "direction": "down", "top": 12.0}
    assert [c["source"] for c in L._gap_candidates(wide, atr=1.0)] == ["gap_down_top", "gap_down_bottom"]


def lv(price, score, lo=None, hi=None, sources=("pivot_low",)):
    return {"price": price, "score": score, "zone_low": price if lo is None else lo,
            "zone_high": price if hi is None else hi, "sources": list(sources)}


def test_select_levels_nearest_qualifying_first_on_each_side():
    clusters = [lv(90, 3.0), lv(95, 0.1), lv(97, 1.0), lv(99, 0.5), lv(101, 0.2), lv(104, 2.0),
                lv(110, 5.0), lv(120, 1.0)]
    sup, res = L.select_levels(clusters, close=100.0, atr=2.0, per_side=3, min_score=0.25)
    assert [c["price"] for c in sup] == [99, 97, 90]      # 95 is too weak to be a key level
    assert [c["price"] for c in res] == [104, 110, 120]   # so is 101
    assert sup[0]["distance_pct"] == pytest.approx(-0.01)
    assert sup[0]["distance_atr"] == pytest.approx(-0.5)
    assert res[0]["distance_atr"] == pytest.approx(2.0)


def test_select_levels_keeps_weak_52_week_extremes_and_drops_zones_holding_the_close():
    clusters = [lv(98, 0.01, sources=["low_52w"]), lv(103, 0.0, sources=["high_52w"]),
                lv(100.2, 2.0, lo=99.7, hi=100.6)]                # contains the close of 100
    sup, res = L.select_levels(clusters, close=100.0, atr=2.0, per_side=3, min_score=0.25)
    assert [c["price"] for c in sup] == [98] and [c["price"] for c in res] == [103]


def patched_candidates(monkeypatch, make):
    """Run build_levels on synthetic bars with _candidates replaced by make(close, atr)."""
    bars = synthetic_bars(n=400)
    ctx = make_ctx(bars)
    close, atr = float(bars["close"].iloc[-1]), float(ctx["ind"]["atr14"].iloc[-1])
    monkeypatch.setattr(L, "_candidates", lambda *a, **k: make(close, atr))
    monkeypatch.setattr(C, "LEVEL_MIN_SCORE", 0.0)               # keep every cluster: this tests sides only
    return L.build_levels(ctx), close, atr


def test_build_levels_splits_a_cluster_that_would_straddle_the_close(monkeypatch):
    # reviewer's case: pivot low 0.15 ATR below, SMA50 0.3 ATR above -> one zone holding the close
    # under a single merge; per-side merging keeps the support at the pivot low
    out, close, atr = patched_candidates(monkeypatch, lambda c, a: [
        {"price": c - 0.15 * a, "source": "pivot_low"}, {"price": c + 0.3 * a, "source": "sma50"},
        {"price": c - 3.0 * a, "source": "pivot_low"}, {"price": c + 4.0 * a, "source": "pivot_high"}])
    s1, r1 = out["supports"][0], out["resistances"][0]
    assert s1["price"] == pytest.approx(close - 0.15 * atr) and s1["sources"] == ["pivot_low"]
    assert r1["price"] == pytest.approx(close + 0.3 * atr) and r1["sources"] == ["sma50"]
    for x in out["supports"]:
        assert x["zone_high"] < close
    for x in out["resistances"]:
        assert x["zone_low"] > close


def test_build_levels_files_the_52_week_high_above_the_close_as_resistance(monkeypatch):
    out, close, atr = patched_candidates(monkeypatch, lambda c, a: [
        {"price": c - 0.1 * a, "source": "pivot_high"}, {"price": c + 0.1 * a, "source": "high_52w"},
        {"price": c - 0.3 * a, "source": "avwap_high_52w"}])
    assert [x["sources"] for x in out["resistances"]] == [["high_52w"]]
    assert all("high_52w" not in x["sources"] for x in out["supports"])
    assert not any("no resistance levels" in n for n in out["notes"])


# ---------------------------------------------------------------------------- build_levels
def test_build_levels_on_synthetic_bars():
    bars = synthetic_bars()
    ctx = make_ctx(bars)
    out = L.build_levels(ctx)
    assert out["available"] is True
    lookback = C.HORIZONS["swing"]["levels_lookback"]
    window = bars.iloc[-lookback:]
    assert out["lookback_bars"] == lookback
    close, atr = out["last_close"], out["atr"]
    assert close == pytest.approx(bars["close"].iloc[-1])
    assert atr == pytest.approx(ctx["ind"]["atr14"].iloc[-1])

    # pivots lie in the window, never in the last k bars
    last_k = {d.strftime("%Y-%m-%d") for d in bars.index[-C.FRACTAL_K:]}
    start = window.index[0].strftime("%Y-%m-%d")
    for p in out["pivots"]["highs"] + out["pivots"]["lows"]:
        assert p["date"] >= start and p["date"] not in last_k
    assert out["pivots"]["highs"] and out["pivots"]["lows"]

    vp = out["volume_profile"]
    assert len(vp["bins"]) == C.PROFILE_BINS
    assert sum(b["volume"] for b in vp["bins"]) == pytest.approx(window["volume"].sum())
    assert vp["price_low"] == pytest.approx(window["low"].min())
    assert vp["price_high"] == pytest.approx(window["high"].max())
    assert vp["value_area_low"] <= vp["poc"] <= vp["value_area_high"]
    assert vp["value_area_volume_share"] >= C.VALUE_AREA_SHARE

    recent = bars.iloc[-C.HIGH_52W_BARS:]
    assert out["high_52w"] == pytest.approx(recent["high"].max())
    assert out["low_52w"] == pytest.approx(recent["low"].min())

    sources = {c["source"] for c in out["candidates"]}
    assert {"pivot_high", "pivot_low", "poc", "value_area_low", "value_area_high", "high_52w",
            "low_52w", "sma50", "sma200"} <= sources

    sup, res = out["supports"], out["resistances"]
    assert 0 < len(sup) <= C.LEVELS_PER_SIDE and 0 < len(res) <= C.LEVELS_PER_SIDE
    assert all(lv["price"] < close for lv in sup) and all(lv["price"] > close for lv in res)
    assert [lv["price"] for lv in sup] == sorted((lv["price"] for lv in sup), reverse=True)
    assert [lv["price"] for lv in res] == sorted(lv["price"] for lv in res)
    for lv in sup + res:
        assert REQUIRED_LEVEL_KEYS <= lv.keys()
        assert lv["zone_low"] <= lv["price"] <= lv["zone_high"]
        assert lv["distance_atr"] == pytest.approx((lv["price"] - close) / atr)
        assert 0.0 <= lv["volume_share"] <= 1.0


def test_build_levels_uses_anchored_vwaps_when_available(monkeypatch):
    bars = synthetic_bars(n=400)
    fake = types.ModuleType("modules")
    fake.anchored_vwap_series = lambda ctx: {"ytd_start": pd.Series(77.7, index=ctx["bars"].index)}
    monkeypatch.setitem(sys.modules, "modules", fake)
    out = L.build_levels(make_ctx(bars))
    assert {"price": 77.7, "source": "avwap_ytd_start"} in out["candidates"]


def test_build_levels_ignores_anchored_vwaps_anchored_a_few_bars_ago(monkeypatch):
    bars = synthetic_bars(n=400)
    young = pd.Series(np.nan, index=bars.index)
    young.iloc[-3:] = 77.7                                        # anchored 2 bars before the last bar
    fake = types.ModuleType("modules")
    fake.anchored_vwap_series = lambda ctx: {"high_52w": young, "ytd_start": pd.Series(66.6, index=bars.index)}
    monkeypatch.setitem(sys.modules, "modules", fake)
    out = L.build_levels(make_ctx(bars))
    sources = {c["source"] for c in out["candidates"]}
    assert "avwap_ytd_start" in sources and "avwap_high_52w" not in sources
    assert any("anchored VWAP high_52w not used as a level: anchored 2 bar(s) ago" in n for n in out["notes"])


def test_build_levels_skips_anchored_vwaps_on_error(monkeypatch):
    def boom(ctx):
        raise RuntimeError("anchor failed")

    fake = types.ModuleType("modules")
    fake.anchored_vwap_series = boom
    monkeypatch.setitem(sys.modules, "modules", fake)
    out = L.build_levels(make_ctx(synthetic_bars(n=400)))
    assert out["available"] is True
    assert not any(c["source"].startswith("avwap_") for c in out["candidates"])
    assert any("anchored VWAPs not used" in n for n in out["notes"])


def test_build_levels_at_all_time_high_has_no_resistances():
    n = 300
    close = 100.0 + np.arange(n, dtype=float)
    high = close + 0.5
    high[-1] = close[-1]                                   # closes on the high of a steady uptrend
    df = frame(high, close - 1.0)
    df["close"] = close
    out = L.build_levels(make_ctx(df))
    assert out["resistances"] == []
    assert out["extremes"]["at_high_52w"] and out["extremes"]["at_all_time_high"]
    assert any(n.startswith("price at an all-time high") and "no resistance levels" in n
               for n in out["notes"])
    assert len(out["supports"]) == C.LEVELS_PER_SIDE


def test_build_levels_short_history_and_missing_sma200():
    out = L.build_levels(make_ctx(synthetic_bars(n=120)))
    assert out["available"] is True
    assert out["lookback_bars"] == 120
    assert not any(c["source"] == "sma200" for c in out["candidates"])
    notes = " | ".join(out["notes"])
    assert "only 120 bars" in notes
    assert "SMA200 unavailable" in notes
    assert "52-week high/low computed over 120 bars" in notes


def test_build_levels_without_atr_is_unavailable():
    out = L.build_levels(make_ctx(synthetic_bars(n=10)))
    assert out["available"] is False and "ATR" in out["reason"]


def test_build_levels_ignores_bars_after_as_of():
    bars = synthetic_bars(n=400)
    ctx = make_ctx(bars)
    cut = bars.index[-6]
    ctx["as_of"] = cut.strftime("%Y-%m-%d")
    out = L.build_levels(ctx)
    ref = L.build_levels(make_ctx(bars.loc[:cut]))
    assert out["window_end"] == ref["window_end"] == ctx["as_of"]
    assert out["supports"] == ref["supports"] and out["resistances"] == ref["resistances"]
