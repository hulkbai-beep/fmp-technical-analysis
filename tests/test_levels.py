"""Tests for scripts/levels.py (module H) on hand-built bars with known answers."""
import sys
import types

import numpy as np
import pandas as pd
import pytest

import config as C
import levels as L
from conftest import synthetic_bars

REQUIRED_LEVEL_KEYS = {"price", "zone_low", "zone_high", "sources", "touches", "holds", "breaks", "weak", "flips",
                       "fresh", "volume_share", "score", "distance_pct", "distance_atr"}


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


# ---------------------------------------------------------------------------- swing pivots
def flat_atr(df, value=1.0):
    return pd.Series(value, index=df.index)


def test_swing_pivots_atr_zigzag():
    highs = [10, 11, 12, 13, 14, 13, 12, 11.5, 12.5, 13.5, 14.5, 13.0, 12.5]
    df = frame(highs, [h - 0.5 for h in highs])
    piv = L.swing_pivots(df, flat_atr(df), mult=1.5)
    d = [x.strftime("%Y-%m-%d") for x in df.index]
    # bar 0 starts the count (never a pivot); 14 is confirmed by bar 5's low 12.5 = 14 - 1.5
    assert [(p["date"], p["price"]) for p in piv["highs"]] == [(d[4], 14.0), (d[10], 14.5)]
    assert [(p["date"], p["price"]) for p in piv["lows"]] == [(d[7], 11.0)]
    # swing = smaller leg: 14 <- 9.5 (4.5) / -> 11 (3); 11: 3 / 3.5; 14.5: 3.5 / -> latest low 12 (2.5)
    assert [p["swing_atr"] for p in piv["highs"]] == pytest.approx([3.0, 2.5])
    assert piv["lows"][0]["swing_atr"] == pytest.approx(3.0)


def test_swing_pivots_ignore_moves_smaller_than_the_threshold():
    highs = [10, 10.8, 10.2, 10.9, 10.1, 10.7, 10.3]
    df = frame(highs, [h - 0.5 for h in highs])
    assert L.swing_pivots(df, flat_atr(df), mult=1.5) == {"highs": [], "lows": []}
    assert L.swing_pivots(df, flat_atr(df, np.nan), mult=0.1) == {"highs": [], "lows": []}   # no ATR yet


def test_swing_pivots_are_confirmed_only_by_a_later_bar():
    highs = [10, 11, 12, 13, 14, 13, 12, 11.5, 12.5, 13.5, 14.5, 13.0, 12.5]
    df = frame(highs + [12.8], [h - 0.5 for h in highs] + [11.0])     # last bar: new low, own range 1.8 ATR
    piv = L.swing_pivots(df, flat_atr(df), mult=1.5)
    d = [x.strftime("%Y-%m-%d") for x in df.index]
    assert [p["date"] for p in piv["lows"]] == [d[7]]                  # the 11.0 low at bar 7 only
    assert [p["date"] for p in piv["highs"]] == [d[4], d[10]]


def test_swing_pivots_do_not_repeat_a_pivot_bar_as_the_opposite_extreme():
    df = frame([100.0, 100.8, 100.9, 99.8, 98.9], [98.9, 98.2, 98.2, 99.2, 98.4])
    piv = L.swing_pivots(df, flat_atr(df), mult=1.5)
    d = [x.strftime("%Y-%m-%d") for x in df.index]
    assert [(p["date"], p["price"]) for p in piv["highs"]] == [(d[2], 100.9)]
    assert [(p["date"], p["price"]) for p in piv["lows"]] == [(d[1], 98.2)]


# ---------------------------------------------------------------------------- supply / demand
def ohlc(rows, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(rows), name="date")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    return df.assign(volume=1000.0)


BASE_ROWS = [(10, 10.5, 9.5, 10), (10.2, 11.5, 10.1, 11.4), (11.4, 12.5, 11.3, 12.3),
             (12.3, 12.8, 12.0, 12.5), (12.5, 12.6, 11.0, 11.2)]


def test_base_zones_fresh_demand_then_tested_then_broken():
    df = ohlc(BASE_ROWS)
    d = [x.strftime("%Y-%m-%d") for x in df.index]
    # bar 0: range 1 ATR, then closes 11.4, 12.3 (+2.3 ATR by bar 2) with no low under 9.5
    assert L.base_zones(df, flat_atr(df)) == [
        {"date": d[0], "kind": "demand", "zone_low": 9.5, "zone_high": 10.5, "fresh": True}]
    tested = ohlc(BASE_ROWS + [(11.2, 11.3, 10.3, 10.6)])            # low 10.3 trades back into it
    assert [z["fresh"] for z in L.base_zones(tested, flat_atr(tested))] == [False]
    broken = ohlc(BASE_ROWS + [(11.2, 11.3, 10.3, 10.6), (10.6, 10.7, 9.3, 9.4)])
    zones = L.base_zones(broken, flat_atr(broken))
    # the demand zone is gone (close 9.4 < 9.5); bar 3 became a supply base (12.5 -> 9.4)
    assert [(z["kind"], z["zone_low"], z["zone_high"], z["fresh"]) for z in zones] == [("supply", 12.0, 12.8, True)]


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


# ---------------------------------------------------------------------------- zones / tests / select
def test_density_zones_merge_close_candidates_and_split_far_ones():
    cands = [{"price": 100.0, "source": "pivot_low", "weight": 1.0},
             {"price": 100.3, "source": "poc", "weight": 1.0},
             {"price": 103.0, "source": "sma50", "weight": 0.25}]
    low, high = L.density_zones(cands, atr=1.0)
    assert low["sources"] == ["pivot_low", "poc"] and low["source_types"] == ["pivot", "volume_profile"]
    assert low["n_candidates"] == 2 and low["weight"] == pytest.approx(2.0)
    assert low["price"] == pytest.approx(100.15)                   # weighted mean of the members inside
    assert low["zone_low"] < 100.0 and low["zone_high"] > 100.3
    assert low["zone_high"] - low["zone_low"] <= C.ZONE_MAX_ATR + 1e-9
    # a lone candidate: the Gaussian's width at half its peak, 2.355 bandwidths
    assert high["price"] == pytest.approx(103.0)
    assert high["zone_high"] - high["zone_low"] == pytest.approx(2.355 * C.KDE_BANDWIDTH_ATR, abs=0.02)


def test_density_zones_bound_and_candidate_zones():
    cands = [{"price": 10.4, "zone_low": 10.0, "zone_high": 10.8, "source": "gap_up", "weight": 0.75}]
    (z,) = L.density_zones(cands, atr=1.0)
    assert z["zone_low"] < 10.2 and z["zone_high"] > 10.6 and z["source_types"] == ["gap"]
    (clipped,) = L.density_zones([{"price": 99.9, "source": "pivot_low"}], atr=1.0, bound=(-np.inf, 99.95))
    assert clipped["zone_high"] == 99.95 and clipped["price"] == pytest.approx(99.9)
    assert L.density_zones([{"price": float("nan"), "source": "x"}], 1.0) == []


def test_zone_sources_name_only_candidates_inside_the_band():
    # the 52-week low sits 0.6 ATR below the pivot low: in the same density region, outside the band
    cands = [{"price": 99.10, "source": "pivot_low", "weight": 2.5},
             {"price": 98.50, "source": "low_52w", "weight": 1.0}]
    (z,) = L.density_zones(cands, atr=1.0)
    assert z["sources"] == ["pivot_low"] and z["source_types"] == ["pivot"]
    weak = {"sources": z["sources"], "score": 0.9}
    assert L.qualifies(weak) is False                                  # no 52-week exemption for it
    assert L.qualifies({"sources": ["low_52w"], "score": 0.0}) is True


TEST_BARS = [(12, 11), (11.2, 10.3), (11.8, 10.8), (12, 11.2), (11, 9.8), (9.6, 9.0), (9.5, 8.8),
             (10.2, 9.4), (9.7, 8.9), (9.2, 8.5)]


def test_zone_tests_hold_break_and_flip():
    df = frame(*zip(*TEST_BARS))
    tests = L.zone_tests(df, 10.0, 10.5, atr=1.0)
    # from above, out above, high 11.8 >= 10.5 + 1 -> hold; then down through -> break;
    # back up from below, out below, low 8.9 <= 10 - 1 -> hold right after a break -> flip
    assert [(t["end"], t["entry"], t["result"], t["flip"]) for t in tests] == [
        (2, 1, "hold", False), (5, 1, "break", False), (8, -1, "hold", True)]
    scored, = L.score_zones([{"price": 10.25, "zone_low": 10.0, "zone_high": 10.5, "weight": 2.0}],
                            df, {"available": False}, atr=1.0, half_life=1e12)
    assert (scored["touches"], scored["holds"], scored["breaks"], scored["weak"], scored["flips"]) == (3, 2, 1, 0, 1)
    assert scored["score"] == pytest.approx(1.0 - 0.5 + 1.5 + C.LEVEL_CONFLUENCE_WEIGHT * 2.0)
    assert scored["fresh"] is False and scored["last_test"] == df.index[8].strftime("%Y-%m-%d")


def test_zone_tests_leave_out_unresolved_tests_and_count_jumps_and_weak_ones():
    pending = frame(*zip(*(TEST_BARS + [(10.2, 9.5), (9.7, 9.5)])))
    assert len(L.zone_tests(pending, 10.0, 10.5, atr=1.0)) == 3        # out below, 9.0 not reached yet
    inside = frame(*zip(*(TEST_BARS + [(10.2, 9.5)])))
    assert len(L.zone_tests(inside, 10.0, 10.5, atr=1.0)) == 3         # still in the zone at the last bar
    jump = frame([12, 9.5], [11, 9.0])
    assert [t["result"] for t in L.zone_tests(jump, 10.0, 10.5, atr=1.0)] == ["break"]
    weak = frame([12, 11, 11.1, 10.9, 11.0], [11, 10.2, 10.7, 10.3, 10.8])   # out above, no 1-ATR rally, back in
    assert [t["result"] for t in L.zone_tests(weak, 10.0, 10.5, atr=1.0)] == ["weak"]
    (scored,) = L.score_zones([{"price": 10.25, "zone_low": 10.0, "zone_high": 10.5, "weight": 0.0}],
                              weak, {"available": False}, atr=1.0, half_life=1e12)
    # touches = holds + breaks + weak, so a report can print all three without arithmetic
    assert (scored["touches"], scored["holds"], scored["breaks"], scored["weak"]) == (1, 0, 0, 1)


def test_score_zones_decay_and_volume_share():
    df = frame(*zip(*TEST_BARS))
    vp = L.volume_profile(df, bins=10)
    z = {"price": 10.25, "zone_low": 10.0, "zone_high": 10.5, "weight": 0.0}
    (s,) = L.score_zones([z], df, vp, atr=1.0, half_life=1.0)
    n = len(df)
    assert s["score"] == pytest.approx(0.5 ** (n - 1 - 2) - 0.5 * 0.5 ** (n - 1 - 5) + 1.5 * 0.5 ** (n - 1 - 8))
    assert s["volume_share"] == pytest.approx(L._zone_volume(vp, 10.0, 10.5) / vp["total_volume"])


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


def test_select_levels_nearest_then_strongest_within_reach():
    clusters = [lv(99, 1.2), lv(98, 0.5), lv(97, 1.0), lv(95, 5.0), lv(93, 3.0), lv(90, 9.0),
                lv(101, 2.0), lv(103, 1.5), lv(120, 8.0)]
    sup, res = L.select_levels(clusters, close=100.0, atr=2.0, per_side=3, min_score=1.0, reach_atr=4.0)
    # 98 is too weak; 90 is out of reach (5 ATR) although strongest; 97 loses to 95 and 93
    assert [c["price"] for c in sup] == [99, 95, 93]
    # only 103 is in reach besides the nearest: the nearest zone beyond reach fills the third place
    assert [c["price"] for c in res] == [101, 103, 120]
    assert sup[0]["distance_pct"] == pytest.approx(-0.01)
    assert sup[0]["distance_atr"] == pytest.approx(-0.5)
    assert res[0]["distance_atr"] == pytest.approx(0.5)


def test_select_levels_keeps_weak_52_week_extremes_and_drops_zones_holding_the_close():
    clusters = [lv(98, 0.01, sources=["low_52w"]), lv(103, 0.0, sources=["high_52w"]),
                lv(100.2, 2.0, lo=99.7, hi=100.6)]                # contains the close of 100
    sup, res = L.select_levels(clusters, close=100.0, atr=2.0, per_side=3, min_score=1.0)
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

    # pivots lie in the window; they alternate, so the counts differ by at most one
    start = window.index[0].strftime("%Y-%m-%d")
    highs, lows = out["pivots"]["highs"], out["pivots"]["lows"]
    assert highs and lows and all(p["date"] >= start for p in highs + lows)
    assert abs(len(highs) - len(lows)) <= 1
    assert all(p["swing_atr"] > 0 for p in out["pivots"]["highs"] + out["pivots"]["lows"])

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
    assert out["evidence"] == {"note": C.LEVELS_EVIDENCE_NOTE} and out["evidence"]["note"].startswith("reference only")
    assert "node_zones" not in out


def test_build_levels_uses_anchored_vwaps_when_available(monkeypatch):
    bars = synthetic_bars(n=400)
    fake = types.ModuleType("modules")
    fake.anchored_vwap_series = lambda ctx: {"ytd_start": pd.Series(77.7, index=ctx["bars"].index)}
    monkeypatch.setitem(sys.modules, "modules", fake)
    out = L.build_levels(make_ctx(bars))
    assert {"price": 77.7, "source": "avwap_ytd_start",
            "weight": C.LEVEL_SOURCE_WEIGHTS["anchored_vwap"]} in out["candidates"]


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
    assert out["supports"] and all(x["zone_high"] < df["close"].iloc[-1] for x in out["supports"])


def test_a_record_close_well_below_its_own_high_is_at_the_high():
    # AAPL 2026-11-04: a record close (349.51 > 348.91) and a record high (354.47) on one bar, but the close
    # ended 0.58 ATR below that high; within-0.5-ATR-of-the-high alone said "not at a high"
    n = 300
    close = 100.0 + np.arange(n, dtype=float)
    high, low = close + 0.5, close - 1.0
    high[-1] = close[-1] + 4.0                             # a long upper wick on the last bar
    df = frame(high, low)
    df["close"] = close
    out = L.build_levels(make_ctx(df))
    assert df["close"].iloc[-1] < out["high_52w"] - C.EXTREME_NEAR_ATR * out["atr"]
    ext = out["extremes"]
    assert (ext["at_high_52w"], ext["at_all_time_high"], ext["at_low_52w"], ext["at_all_time_low"]) == \
        (True, True, False, False)
    assert "highest close of the period" in ext["rule"] and f"{C.EXTREME_NEAR_ATR:g} ATR" in ext["rule"]
    assert any(n.startswith("price at an all-time high") for n in out["notes"])
    # a pullback 3 below the prior close is neither the highest close nor near the high: not at a high
    close2 = close[:-1].copy()
    close2[-1] = close2[-2] - 3.0
    df2 = frame(close2 + 0.5, close2 - 1.0)
    df2["close"] = close2
    ext2 = L.build_levels(make_ctx(df2))["extremes"]
    assert not ext2["at_high_52w"] and not ext2["at_all_time_high"]


def test_extremes_closing_basis_mirrors_for_lows_and_separates_52_weeks_from_history():
    bars = frame([30.0] * 50 + [20.0] * 260, [28.0] * 50 + [18.0] * 260)
    bars["close"] = [29.0] * 50 + [19.5] * 259 + [19.0]   # the lowest close, 1 above the lowest low (18)
    r52 = L._range_52w(bars, [])
    ext = L._extremes(bars, r52, 19.0, near=0.1)
    assert ext["at_low_52w"] and ext["at_all_time_low"] and not ext["at_high_52w"]
    old = bars.copy()
    old.iloc[0, old.columns.get_loc("close")] = 10.0        # a lower close outside the last 252 bars
    old.iloc[0, old.columns.get_loc("low")] = 9.0
    ext = L._extremes(old, L._range_52w(old, []), 19.0, near=0.1)
    assert ext["at_low_52w"] and not ext["at_all_time_low"]


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


# ---------------------------------------------------------------------------- ex-dividend gaps
def legacy_unfilled_gaps(df):
    """levels.unfilled_gaps before the dividend handling (verbatim), the reference for "no change"."""
    high = df["high"].to_numpy(dtype=float, na_value=np.nan)
    low = df["low"].to_numpy(dtype=float, na_value=np.nan)
    if len(df) < 2:
        return []
    later_low = np.r_[np.fmin.accumulate(low[::-1])[::-1][1:], np.inf]
    later_high = np.r_[np.fmax.accumulate(high[::-1])[::-1][1:], -np.inf]
    later_low = np.where(np.isnan(later_low), np.inf, later_low)
    later_high = np.where(np.isnan(later_high), -np.inf, later_high)
    out = []
    for t in range(1, len(df)):
        if low[t] > high[t - 1] and later_low[t] > high[t - 1]:
            top = min(low[t], later_low[t])
            out.append({"date": L._iso(df.index[t]), "direction": "up",
                        "top": float(top), "bottom": float(high[t - 1]), "filled": False,
                        "partially_filled": bool(top < low[t]),
                        "gap_top": float(low[t]), "gap_bottom": float(high[t - 1])})
        elif high[t] < low[t - 1] and later_high[t] < low[t - 1]:
            bottom = max(high[t], later_high[t])
            out.append({"date": L._iso(df.index[t]), "direction": "down",
                        "top": float(low[t - 1]), "bottom": float(bottom), "filled": False,
                        "partially_filled": bool(bottom > high[t]),
                        "gap_top": float(low[t - 1]), "gap_bottom": float(high[t])})
    return out


def with_dividend(df, at, amount):
    """df with a "dividend" column: `amount` on the bar positions in `at`, 0 elsewhere."""
    div = np.zeros(len(df))
    div[np.atleast_1d(at)] = amount
    return df.assign(dividend=div)


def test_ex_dividend_drop_within_the_dividend_is_not_a_gap():
    # bar 1 drops below bar 0's low by 0.2 on its ex-date, paying 0.3; later bars stay below
    df = frame([11.0, 9.8, 9.7, 9.75], [10.0, 9.5, 9.4, 9.45])
    raw = L.unfilled_gaps(df)
    assert [(g["direction"], g["top"], g["bottom"], g["ex_dividend"]) for g in raw] == [("down", 10.0, 9.8, None)]
    ex = with_dividend(df, 1, 0.3)
    assert L.unfilled_gaps(ex) == []
    assert L.ex_dividend_gaps(ex) == [{"date": L._iso(df.index[1]), "amount": 0.3, "raw_direction": "down",
                                       "raw_unfilled": True}]
    assert L.ex_dividend_gaps(df) == [] and L.ex_dividend_gaps(df.iloc[:1]) == []
    # filled later in raw terms (a high back at bar 0's low): still listed, flagged as no level change
    refilled = with_dividend(frame([11.0, 9.8, 10.0], [10.0, 9.5, 9.4]), 1, 0.3)
    assert L.ex_dividend_gaps(refilled)[0]["raw_unfilled"] is False


def test_ex_dividend_gap_beyond_the_dividend_stays_with_its_zone_net_of_it():
    # bar 1's high is 0.5 under bar 0's low: 0.2 more than the 0.3 dividend; bar 2 trades into it
    df = with_dividend(frame([11.0, 9.5, 9.6, 9.4], [10.0, 9.0, 9.1, 9.0]), 1, 0.3)
    (g,) = L.unfilled_gaps(df)
    assert g["direction"] == "down" and g["ex_dividend"] == 0.3
    assert (g["gap_bottom"], g["gap_top"]) == pytest.approx((9.5, 9.7))     # zone edge 10.0 - 0.3
    assert (g["bottom"], g["top"], g["partially_filled"]) == pytest.approx((9.6, 9.7, True))
    assert L.ex_dividend_gaps(df) == []
    # a later high reaching the net edge fills it, although the raw edge (10.0) is not reached
    filled = with_dividend(frame([11.0, 9.5, 9.7], [10.0, 9.0, 9.1]), 1, 0.3)
    assert L.unfilled_gaps(filled) == []


def test_ex_dividend_up_gap_keeps_its_raw_zone():
    # a dividend can only explain a drop: a gap up on an ex-dividend bar is tested and bounded with high[t-1]
    df = frame([11.0, 12.0, 12.2], [10.0, 11.2, 11.5])
    up = with_dividend(df, 1, 0.3)
    (g,) = L.unfilled_gaps(up)
    assert g == {**legacy_unfilled_gaps(df)[0], "ex_dividend": 0.3}
    assert (g["direction"], g["bottom"], g["top"], g["gap_bottom"], g["gap_top"]) == ("up", 11.0, 11.2, 11.0, 11.2)
    assert L.ex_dividend_gaps(up) == []
    # filled at the raw edge: a later low of 10.9 closes it, although it stays above high[t-1] - D = 10.7
    assert L.unfilled_gaps(with_dividend(frame([11.0, 12.0, 12.2], [10.0, 11.2, 10.9]), 1, 0.3)) == []


@pytest.mark.parametrize("highs, lows, amount", [
    ([11.0, 11.0, 11.0], [10.0, 10.0, 10.0], 1.5),     # trades flat on its ex-date
    ([11.0, 10.9, 11.0], [10.0, 10.6, 10.65], 0.5),    # a little lower, above the prior high net of D
    ([11.0, 11.4, 11.5], [10.0, 10.8, 10.9], 0.5),     # a little higher, overlapping the prior bar
])
def test_an_ex_dividend_bar_that_holds_up_is_no_gap_up(highs, lows, amount):
    df = frame(highs, lows)
    assert L.unfilled_gaps(df) == []
    ex = with_dividend(df, 1, amount)
    assert L.unfilled_gaps(ex) == [] and L.ex_dividend_gaps(ex) == []


@pytest.mark.parametrize("seed", [3, 11])
def test_dividends_never_change_gaps_up(seed):
    bars = synthetic_bars(n=600, seed=seed, vol=0.03)
    div = (0.02 * bars["close"].shift(1)).fillna(0.0)                 # a 2% dividend on every bar but the first
    ex = bars.assign(dividend=div)
    legacy_up = [g for g in legacy_unfilled_gaps(bars) if g["direction"] == "up"]
    got_up = [g for g in L.unfilled_gaps(ex) if g["direction"] == "up"]
    assert legacy_up and [{k: v for k, v in g.items() if k != "ex_dividend"} for g in got_up] == legacy_up
    assert all(g["ex_dividend"] == pytest.approx(div.loc[pd.Timestamp(g["date"])]) for g in got_up)
    assert {g["raw_direction"] for g in L.ex_dividend_gaps(ex)} <= {"down"}


def test_a_dividend_on_a_bar_after_the_gap_changes_nothing():
    df = frame([11.0, 9.5, 9.6, 9.4], [10.0, 9.0, 9.1, 9.0])
    later = with_dividend(df, 2, 0.4)                 # the gap is on bar 1; bar 2 goes ex
    assert L.unfilled_gaps(later) == [{**g, "ex_dividend": None} for g in legacy_unfilled_gaps(df)]


@pytest.mark.parametrize("seed", [1, 7, 11, 23])
def test_gaps_without_dividends_match_the_legacy_function(seed):
    bars = synthetic_bars(n=600, seed=seed, vol=0.03)
    expected = [{**g, "ex_dividend": None} for g in legacy_unfilled_gaps(bars)]
    assert expected                                                   # the series has gaps to compare
    assert L.unfilled_gaps(bars) == expected
    assert L.unfilled_gaps(bars.assign(dividend=0.0)) == expected
    assert L.unfilled_gaps(bars.assign(dividend=np.nan)) == expected
    assert L.ex_dividend_gaps(bars.assign(dividend=0.0)) == []


def test_ex_dividend_gaps_list_raw_gaps_the_dividend_explains_filled_or_not():
    bars = synthetic_bars(n=400, seed=3, vol=0.03)
    raw = legacy_unfilled_gaps(bars)
    downs = [i for i in range(1, len(bars)) if bars["high"].iloc[i] < bars["low"].iloc[i - 1]]
    assert len(downs) >= 3
    # each raw gap down goes ex with a dividend 1 cent larger than the gap: all of them neutralized
    div = np.zeros(len(bars))
    for i in downs:
        div[i] = bars["low"].iloc[i - 1] - bars["high"].iloc[i] + 0.01
    ex = bars.assign(dividend=div)
    got = L.ex_dividend_gaps(ex)
    assert [g["date"] for g in got] == [L._iso(bars.index[i]) for i in downs]
    assert all(g["raw_direction"] == "down" and g["amount"] > 0 for g in got)
    # raw_unfilled marks exactly the raw gaps the legacy function still listed as levels
    assert [g["date"] for g in got if g["raw_unfilled"]] == [g["date"] for g in raw if g["direction"] == "down"]
    assert any(not g["raw_unfilled"] for g in got)
    assert [g for g in L.unfilled_gaps(ex) if g["direction"] == "down"] == []
    assert [g["date"] for g in L.unfilled_gaps(ex) if g["direction"] == "up"] == \
        [g["date"] for g in raw if g["direction"] == "up"]


def test_build_levels_gap_adjustment_and_unchanged_without_dividends():
    bars = synthetic_bars(n=400, vol=0.03)
    ctx = make_ctx(bars)
    base = L.build_levels(ctx)
    lookback = C.HORIZONS["swing"]["levels_lookback"]
    assert base["gap_adjustment"] == {"method": L.GAP_DIVIDEND_METHOD, "ex_dividend_bars": 0, "neutralized": []}
    assert [{k: v for k, v in g.items() if k != "ex_dividend"} for g in base["gaps"]] == \
        legacy_unfilled_gaps(bars.iloc[-(lookback + 1):])
    assert L.build_levels({**ctx, "bars": bars.assign(dividend=0.0)}) == base

    # ex-dividend drops on raw gap-down bars of the window, each 1 cent larger than its gap
    gap_bars = bars.iloc[-(lookback + 1):]
    downs = [i for i in range(1, len(gap_bars)) if gap_bars["high"].iloc[i] < gap_bars["low"].iloc[i - 1]]
    assert downs
    div = pd.Series(0.0, index=bars.index)
    for i in downs:
        div[gap_bars.index[i]] = gap_bars["low"].iloc[i - 1] - gap_bars["high"].iloc[i] + 0.01
    div[bars.index[-lookback - 5]] = 0.2                             # before the window: not counted
    out = L.build_levels({**ctx, "bars": bars.assign(dividend=div)})
    adj = out["gap_adjustment"]
    assert adj["ex_dividend_bars"] == len(downs)
    assert [g["date"] for g in adj["neutralized"]] == [L._iso(gap_bars.index[i]) for i in downs]
    assert not any(g["direction"] == "down" for g in out["gaps"])
    assert not any(c["source"].startswith("gap_down") for c in out["candidates"])
    open_raw = [g["date"] for g in legacy_unfilled_gaps(gap_bars) if g["direction"] == "down"]
    assert [g["date"] for g in adj["neutralized"] if g["raw_unfilled"]] == open_raw
    note = next(n for n in out["notes"] if "raw gap(s) on ex-dividend bars" in n)
    assert note.startswith(f"{len(downs)} raw gap(s) on ex-dividend bars are no larger than the dividend")
    assert (f"{len(open_raw)} of them would otherwise be unfilled-gap levels" in note if open_raw
            else "no gap level changes" in note)
    assert not any("raw gap(s) on ex-dividend bars" in n for n in base["notes"])


def test_build_levels_notes_when_dividend_data_is_missing():
    bars = synthetic_bars(n=400)
    out = L.build_levels({**make_ctx(bars), "skipped": {"dividends": "restricted: Premium endpoint"}})
    assert any("dividend data unavailable (restricted: Premium endpoint)" in n for n in out["notes"])
    assert out["gap_adjustment"]["ex_dividend_bars"] == 0
