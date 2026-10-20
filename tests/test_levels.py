"""Tests for scripts/levels.py (module H) on hand-built bars with known answers."""
import sys
import types

import numpy as np
import pandas as pd
import pytest

import config as C
import levels as L
from conftest import synthetic_bars

REQUIRED_LEVEL_KEYS = {"price", "zone_low", "zone_high", "sources", "touches", "holds", "breaks", "flips",
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
    assert (scored["touches"], scored["holds"], scored["breaks"], scored["flips"]) == (3, 2, 1, 1)
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
