"""Tests for signal_stats.py: every rule on constructed indicator frames, de-clustering, forward
return / MAE arithmetic, baselines, confidence labels, edge, active detection. No network."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import config as C
import signal_stats as S
from conftest import synthetic_bars

N = 300
OHLC = ("open", "high", "low", "close")


def neutral(n=N):
    """Bars and an indicator frame on which no rule fires: close sits exactly on every SMA,
    MACD stays above its signal line, no squeeze, normal volume, no new highs."""
    idx = pd.bdate_range("2020-01-02", periods=n, name="date")
    bars = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1e6},
                        index=idx)
    for c in OHLC:
        bars["adj_" + c] = bars[c]
    ind = pd.DataFrame({
        "sma20": 100.0, "sma50": 100.0, "sma200": 100.0, "rsi2": 50.0, "rsi14": 50.0,
        "hh55_prev": 200.0, "hh252_prev": 200.0, "rvol50": 1.0, "atr14": 1.0, "z20": 0.0,
        "macd": 1.0, "macd_signal": 0.5, "squeeze_on": False, "squeeze_bars": 0,
    }, index=idx)
    return bars, ind


def put(df, rows, **vals):
    """Set columns at integer row positions (in place on df itself, no chained assignment)."""
    rows = np.atleast_1d(rows)
    for col, v in vals.items():
        df.iloc[rows, df.columns.get_loc(col)] = v


def fired(bars, ind, sid):
    return np.flatnonzero(S.events(bars, ind)[sid].to_numpy()).tolist()


def ctx_for(bars, ind, horizon="swing"):
    return dict(ticker="TEST", bars=bars, ind=ind, hz=C.HORIZONS[horizon])


# ---------------------------------------------------------------------------- library / events frame
def test_library_matches_contract():
    assert S.SIGNAL_IDS == [
        "uptrend_pullback", "downtrend_oversold", "breakout_55d", "new_52w_high", "squeeze_release_up",
        "squeeze_release_down", "golden_cross", "death_cross", "pullback_rising_50", "extension_2sigma",
        "gap_up_atr", "gap_down_atr", "macd_bull_cross_below_zero", "macd_bear_cross_above_zero"]
    for s in S.SIGNALS:
        assert s["bias"] in (-1, 1)
        assert s["regime"] in ("above_200", "below_200", "current_side")
        assert s["pillar"] in C.PILLARS
        assert 1 <= s["family"] <= 10 and s["rule"]


def test_signals_md_lists_every_rule_as_coded():
    doc = Path(__file__).resolve().parents[1] / "references" / "signals.md"
    text = doc.read_text(encoding="utf-8")
    for s in S.SIGNALS:
        assert f"`{s['id']}`" in text and f"`{s['rule']}`" in text, s["id"]


def test_neutral_frame_fires_nothing():
    bars, ind = neutral()
    ev = S.events(bars, ind)
    assert list(ev.columns) == S.SIGNAL_IDS
    assert ev.index.equals(bars.index)
    assert all(ev[c].dtype == bool for c in ev.columns)
    assert not ev.to_numpy().any()


def test_all_nan_indicators_fire_nothing():
    bars, ind = neutral()
    ind = ind.astype({"squeeze_on": object, "squeeze_bars": float})
    ind.loc[:, :] = np.nan
    assert not S.events(bars, ind).to_numpy().any()


# ---------------------------------------------------------------------------- rules
def test_uptrend_pullback():
    bars, ind = neutral()
    put(bars, [250, 260, 280], close=101.0)
    put(bars, 270, close=99.0)
    put(ind, [250, 270, 280], rsi2=5.0)
    put(ind, 260, rsi2=10.0)                      # not strictly below 10
    put(ind, 280, sma200=np.nan)                  # NaN input -> False
    assert fired(bars, ind, "uptrend_pullback") == [250]


def test_downtrend_oversold():
    bars, ind = neutral()
    put(bars, [250, 260], close=99.0)
    put(bars, 270, close=101.0)
    put(ind, [250, 270], rsi14=25.0)
    put(ind, 260, rsi14=30.0)
    assert fired(bars, ind, "downtrend_oversold") == [250]


def test_breakout_55d():
    bars, ind = neutral()
    put(bars, [250, 260, 270, 280], close=201.0)
    put(bars, 290, close=200.0)                   # equal to the prior high: no breakout
    put(ind, [250, 280, 290], rvol50=1.5)
    put(ind, 260, rvol50=1.49)
    put(ind, 270, rvol50=np.nan)
    put(ind, 280, hh55_prev=np.nan)
    assert fired(bars, ind, "breakout_55d") == [250]


def test_new_52w_high_requires_sma200():
    bars, ind = neutral()
    put(bars, [250, 260], close=201.0)
    put(bars, 270, close=200.0)
    put(ind, 260, sma200=np.nan)
    assert fired(bars, ind, "new_52w_high") == [250]


@pytest.mark.parametrize("close, sid, other", [(101.0, "squeeze_release_up", "squeeze_release_down"),
                                               (99.0, "squeeze_release_down", "squeeze_release_up"),
                                               (100.0, "squeeze_release_down", "squeeze_release_up")])
def test_squeeze_release_direction(close, sid, other):
    bars, ind = neutral()
    put(ind, range(100, 105), squeeze_on=True)    # 5-bar squeeze, released at 105
    put(ind, range(100, 105), squeeze_bars=[1, 2, 3, 4, 5])
    put(bars, 105, close=close)
    assert fired(bars, ind, sid) == [105]
    assert fired(bars, ind, other) == []


def test_squeeze_release_needs_min_bars_and_release():
    bars, ind = neutral()
    put(ind, range(100, 104), squeeze_on=True)    # only 4 bars
    put(ind, range(100, 104), squeeze_bars=[1, 2, 3, 4])
    put(ind, range(200, 210), squeeze_on=True)    # still on at the last bar checked
    put(ind, range(200, 210), squeeze_bars=list(range(1, 11)))
    put(bars, [104, 209], close=101.0)
    assert fired(bars, ind, "squeeze_release_up") == []
    put(bars, 210, close=101.0)
    assert fired(bars, ind, "squeeze_release_up") == [210]


def test_golden_and_death_cross():
    bars, ind = neutral()
    put(ind, range(0, 100), sma50=99.0)
    put(ind, range(100, 150), sma50=101.0)        # golden at 100
    put(ind, range(150, 200), sma50=99.0)         # death at 150
    put(ind, range(200, 210), sma50=100.0)        # touch from below, no cross
    put(ind, range(210, 220), sma50=101.0)        # 100 -> 101 at 210 counts (<= at t-1)
    put(ind, 249, sma200=np.nan)                  # NaN at t-1 -> no death cross at 250
    put(ind, range(250, N), sma50=99.0)
    assert fired(bars, ind, "golden_cross") == [100, 210]
    assert fired(bars, ind, "death_cross") == [150]           # 101 -> 100 at 220 only touches


def test_pullback_rising_50():
    bars, ind = neutral()
    sma50 = 100.0 + 0.1 * np.arange(N)            # rising 2.0 per 20 bars
    put(ind, range(N), sma50=sma50, sma200=90.0)
    put(bars, range(N), close=0.0)                # far from the 50 SMA everywhere ...
    put(bars, 250, close=sma50[250] + 0.9)        # ... except here (within 1 ATR)
    put(bars, 260, close=sma50[260] - 0.9)
    put(bars, 270, close=sma50[270] + 1.1)        # outside 1 ATR
    put(bars, 10, close=sma50[10])                # sma50[t-20] not defined yet
    assert fired(bars, ind, "pullback_rising_50") == [250, 260]

    put(ind, range(N), sma50=130.0 - 0.1 * np.arange(N))   # falling 50 SMA
    put(bars, 250, close=130.0 - 0.1 * 250)
    assert fired(bars, ind, "pullback_rising_50") == []


def test_extension_2sigma():
    bars, ind = neutral()
    put(ind, 250, z20=2.01)
    put(ind, 260, z20=2.0)
    put(ind, 270, z20=np.nan)
    assert fired(bars, ind, "extension_2sigma") == [250]


@pytest.mark.parametrize("sign, sid", [(+1, "gap_up_atr"), (-1, "gap_down_atr")])
def test_gaps_use_prior_atr(sign, sid):
    bars, ind = neutral()
    put(bars, [100, 120, 140, 160, 180], open=100.0 + sign * 2.0)   # 2-point gap vs prior close 100
    put(bars, 200, open=100.0 + sign * 1.0)                          # exactly 1 ATR: not > 1
    put(ind, [100, 140, 160, 180, 200], rvol50=2.0)
    put(ind, 120, rvol50=1.9)
    put(ind, 139, atr14=3.0)                      # prior-bar ATR too wide -> no gap signal at 140
    put(ind, 160, atr14=3.0)                      # same-bar ATR is ignored -> fires at 160
    put(ind, 179, atr14=np.nan)
    assert fired(bars, ind, sid) == [100, 160]
    other = "gap_down_atr" if sid == "gap_up_atr" else "gap_up_atr"
    assert fired(bars, ind, other) == []


def test_macd_crosses():
    bars, ind = neutral()
    put(ind, 99, macd=-1.0, macd_signal=-0.5)
    put(ind, 100, macd=-0.4, macd_signal=-0.5)    # bullish cross below zero
    put(ind, 149, macd=0.6, macd_signal=0.5)
    put(ind, 150, macd=0.4, macd_signal=0.5)      # bearish cross above zero; 151 crosses back up above zero
    put(ind, 199, macd=-0.6, macd_signal=-0.5)
    put(ind, 200, macd=-0.7, macd_signal=-0.5)    # still below the signal line: no bullish cross
    put(ind, 249, macd=-0.1, macd_signal=0.0)
    put(ind, 250, macd=0.6, macd_signal=0.5)      # bullish cross but above zero
    put(ind, 19, macd=-1.0, macd_signal=-0.5)
    put(ind, 20, macd=-0.4, macd_signal=-0.5)     # inside the EMA warm-up
    assert fired(bars, ind, "macd_bull_cross_below_zero") == [100]
    assert fired(bars, ind, "macd_bear_cross_above_zero") == [150]


# ---------------------------------------------------------------------------- statistics
def test_decluster():
    pos = np.array([0, 1, 2, 5, 6, 12])
    assert S.decluster(pos, 5).tolist() == [0, 6, 12]
    assert S.decluster(pos, 1).tolist() == [0, 2, 5, 12]
    assert S.decluster(np.array([], dtype=int), 5).tolist() == []


def hand_bars():
    idx = pd.bdate_range("2021-01-04", periods=10, name="date")
    adj_close = [100, 102, 99, 105, 110, 108, 111, 115, 120, 118]
    adj_low = [99, 101, 97, 104, 107, 106, 110, 114, 119, 116]
    return pd.DataFrame({"open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0, "volume": 1e6,
                         "adj_open": adj_close, "adj_high": adj_close, "adj_low": adj_low,
                         "adj_close": adj_close}, index=idx).astype(float)


def test_forward_return_and_mae_arithmetic():
    b = hand_bars()
    fwd, mae = S.forward_outcomes(b, 3)
    assert fwd[0] == pytest.approx(105 / 100 - 1)
    assert mae[0] == pytest.approx(97 / 100 - 1)
    assert fwd[6] == pytest.approx(118 / 111 - 1)
    assert mae[6] == pytest.approx(114 / 111 - 1)          # positive: no low below the entry close
    assert np.isnan(fwd[7:]).all() and np.isnan(mae[7:]).all()


def test_bearish_mae_is_the_highest_high_after_entry():
    b = hand_bars()
    b["adj_high"] = b["adj_close"] + 1.0
    fwd, mae = S.forward_outcomes(b, 3, bias=-1)
    assert fwd[0] == pytest.approx(105 / 100 - 1)            # forward return does not depend on the side
    assert mae[0] == pytest.approx(106 / 100 - 1)            # max(adj_high[1..3]) = 105 + 1
    assert mae[5] == pytest.approx(121 / 108 - 1)
    b.loc[b.index[2], "adj_high"] = np.nan
    _, mae2 = S.forward_outcomes(b, 3, bias=-1)
    assert mae2[0] == pytest.approx(51 / 50 - 1)             # adj window incomplete -> high / close


def test_forward_return_falls_back_to_close():
    b = hand_bars()
    put(b, 3, adj_close=np.nan, close=55.0)
    put(b, 2, adj_low=np.nan, low=45.0)
    fwd, mae = S.forward_outcomes(b, 3)
    assert fwd[0] == pytest.approx(55 / 50 - 1)             # adj pair incomplete -> split-adjusted
    assert mae[0] == pytest.approx(45 / 50 - 1)             # adj window incomplete -> low / close
    assert fwd[1] == pytest.approx(110 / 102 - 1)           # untouched windows stay adjusted
    b2 = b.drop(columns=["adj_open", "adj_high", "adj_low", "adj_close"])
    fwd2, _ = S.forward_outcomes(b2, 3)
    assert fwd2[1] == pytest.approx(50 / 50 - 1)


def test_stats_declusters_and_drops_events_without_outcome():
    b = hand_bars()
    ev = pd.DataFrame(False, index=b.index, columns=S.SIGNAL_IDS)
    put(ev, [0, 1, 4, 8], extension_2sigma=True)            # 1 is inside 0's window; 8 has no t+3
    st = S.stats(b, None, "extension_2sigma", 3, ev=ev)
    fwd = [105 / 100 - 1, 115 / 110 - 1]
    assert st["n"] == 2
    assert st["median"] == pytest.approx(np.median(fwd))
    assert st["mean"] == pytest.approx(np.mean(fwd))
    assert st["hit_rate"] == 1.0
    assert st["p25"] == pytest.approx(np.percentile(fwd, 25))
    # extension_2sigma is bearish: its adverse excursion is the highest high after entry
    assert st["median_mae"] == pytest.approx(np.median([105 / 100 - 1, 115 / 110 - 1]))
    assert st["dates_sample"] == [S._iso(b.index[0]), S._iso(b.index[4])]
    assert st["confidence"] == "insufficient"

    masked = S.stats(b, None, "extension_2sigma", 3, ev=ev, regime_mask=b.index != b.index[0])
    assert masked["n"] == 1 and masked["dates_sample"] == [S._iso(b.index[1])]   # 1 kept, 4 inside its window


def test_baselines():
    b = hand_bars()
    fwd, mae = S.forward_outcomes(b, 3)
    u = S.baseline(b, 3)
    assert u["n"] == 7                                       # overlapping, every day with t+3 inside
    assert u["median"] == pytest.approx(np.median(fwd[:7]))
    assert u["hit_rate"] == pytest.approx((fwd[:7] > 0).mean())
    assert u["median_mae"] == pytest.approx(np.median(mae[:7]))
    mask = np.zeros(10, dtype=bool)
    mask[[0, 2, 4, 8]] = True
    m = S.baseline(b, 3, mask=mask)
    assert m["n"] == 3
    assert m["mean"] == pytest.approx(fwd[[0, 2, 4]].mean())
    assert S.baseline(b, 3, mask=np.zeros(10, dtype=bool))["median"] is None


def test_stats_match_brute_force_on_random_walk():
    b = synthetic_bars(900, seed=11)
    ev = pd.DataFrame(False, index=b.index, columns=S.SIGNAL_IDS)
    rng = np.random.default_rng(0)
    ev["golden_cross"] = rng.random(len(b)) < 0.08
    h = 10
    st = S.stats(b, None, "golden_cross", h, ev=ev)
    ac, al = b["adj_close"].to_numpy(), b["adj_low"].to_numpy()
    rets, maes, nxt = [], [], -1
    for t in np.flatnonzero(ev["golden_cross"].to_numpy()):
        if t + h >= len(b) or t < nxt:
            continue
        rets.append(ac[t + h] / ac[t] - 1)
        maes.append(al[t + 1:t + h + 1].min() / ac[t] - 1)
        nxt = t + h + 1
    assert st["n"] == len(rets)
    assert st["median"] == pytest.approx(np.median(rets))
    assert st["p75"] == pytest.approx(np.percentile(rets, 75))
    assert st["median_mae"] == pytest.approx(np.median(maes))


@pytest.mark.parametrize("n, label", [(0, "insufficient"), (14, "insufficient"), (15, "low"),
                                      (29, "low"), (30, "normal"), (500, "normal")])
def test_confidence_labels(n, label):
    assert S.confidence(n) == label


def test_edge():
    sig = {"median": 0.02, "hit_rate": 0.60}
    same = {"median": 0.005, "hit_rate": 0.52}
    unc = {"median": 0.004, "hit_rate": 0.53}
    e = S.edge(sig, same, unc)
    assert e["median"] == pytest.approx(0.015)
    assert e["hit_rate"] == pytest.approx(0.08)
    assert e["vs_unconditional_median"] == pytest.approx(0.016)
    assert S.edge({"median": None, "hit_rate": None}, same, unc) == {
        "median": None, "hit_rate": None, "vs_unconditional_median": None}


# ---------------------------------------------------------------------------- module builder
def test_active_within_last_three_bars():
    bars, ind = neutral()
    put(bars, range(N), close=101.0)              # above the 200 SMA throughout
    put(ind, [N - 10, N - 3], rsi2=5.0)           # uptrend_pullback: last fire 2 bars ago
    put(ind, N - 4, z20=3.0)                      # extension: last fire 3 bars ago -> not active
    out = S.build_signal_stats(ctx_for(bars, ind))
    assert [a["id"] for a in out["active"]] == ["uptrend_pullback"]
    a = out["active"][0]
    assert a["bars_ago"] == 2 and a["fired_date"] == S._iso(bars.index[N - 3])
    assert a["regime_used"] == "above_200"
    assert "extension_2sigma" in out["inactive_ids"] and "uptrend_pullback" not in out["inactive_ids"]
    lib = {x["id"]: x for x in out["library"]}
    assert lib["extension_2sigma"]["last_fired"] == S._iso(bars.index[N - 4])
    assert lib["uptrend_pullback"]["fires_total"] == 2
    assert lib["golden_cross"] == {"id": "golden_cross", "name": S._BY_ID["golden_cross"]["name"],
                                   "last_fired": None, "fires_total": 0}

    put(ind, N - 1, rsi2=5.0)                     # most recent fire is reported
    assert S.build_signal_stats(ctx_for(bars, ind))["active"][0]["bars_ago"] == 0


def test_insufficient_and_empty_samples():
    bars, ind = neutral()
    put(bars, range(N), close=101.0)
    put(ind, [50, 100, 150, 200, N - 1], rsi2=5.0)
    out = S.build_signal_stats(ctx_for(bars, ind))
    a = out["active"][0]
    assert set(a["horizons"]) == {5, 10, 20}
    assert a["horizons"][10]["n"] == 4
    assert a["confidence"] == "insufficient" and a["scorable"] is False

    bars, ind = neutral()
    put(bars, range(N), close=101.0)
    put(ind, N - 1, rsi2=5.0)                     # first ever fire, on the last bar: no outcomes
    a = S.build_signal_stats(ctx_for(bars, ind))["active"][0]
    hz = a["horizons"][10]
    assert hz["n"] == 0 and hz["median"] is None and hz["hit_rate"] is None
    assert hz["edge"] == {"median": None, "hit_rate": None, "vs_unconditional_median": None}
    assert hz["same_regime"]["n"] == N - 10 and a["confidence"] == "insufficient"


def test_current_side_restricts_events_and_baseline():
    bars, ind = neutral()
    put(bars, range(0, 150), close=101.0)         # above the 200 SMA ...
    put(bars, range(150, N), close=99.0)          # ... then below it
    put(ind, [20, 60, 100, 170, 200, N - 2], z20=3.0)
    out = S.build_signal_stats(ctx_for(bars, ind))
    a = out["active"][0]
    assert a["id"] == "extension_2sigma" and a["regime_used"] == "below_200"
    for h in (5, 10, 20):
        hz = a["horizons"][h]
        assert hz["n"] == 2                        # only the below-200 fires at 170 and 200
        assert hz["same_regime"]["n"] == 150 - h
        assert hz["unconditional"]["n"] == N - h


def test_build_on_synthetic_history():
    pytest.importorskip("indicators", reason="indicators.py not available yet")
    import indicators as I
    full = synthetic_bars(1500, seed=5)
    ind_full = I.compute_all(full)
    ev = S.events(full, ind_full)
    t = int(np.flatnonzero(ev["extension_2sigma"].to_numpy())[-1])
    bars, ind = full.iloc[:t + 1], ind_full.iloc[:t + 1]
    out = S.build_signal_stats(dict(ticker="SYN", bars=bars, ind=ind, hz=C.HORIZONS["position"]))
    assert out["available"] is True and out["primary_horizon"] == 60
    assert out["history"] == {"first_date": S._iso(bars.index[0]), "last_date": S._iso(bars.index[-1]),
                              "bars": len(bars), "adj_coverage": 1.0}
    assert len(out["library"]) == len(S.SIGNALS) and len(out["caveats"]) >= 6
    a = next(x for x in out["active"] if x["id"] == "extension_2sigma")
    assert a["bars_ago"] == 0
    for h in (20, 60):
        hz = a["horizons"][h]
        assert hz["edge"]["median"] == pytest.approx(hz["median"] - hz["same_regime"]["median"])
        assert hz["edge"]["hit_rate"] == pytest.approx(hz["hit_rate"] - hz["same_regime"]["hit_rate"])
        assert hz["edge"]["vs_unconditional_median"] == pytest.approx(
            hz["median"] - hz["unconditional"]["median"])
        assert hz["n"] <= hz["same_regime"]["n"] <= hz["unconditional"]["n"]
    assert a["confidence"] == a["horizons"][60]["confidence"]
    json.dumps(out, allow_nan=False)


def test_bearish_signal_and_its_baselines_measure_mae_on_highs():
    pytest.importorskip("indicators", reason="indicators.py not available yet")
    import indicators as I
    full = synthetic_bars(1500, seed=5)
    ind_full = I.compute_all(full)
    t = int(np.flatnonzero(S.events(full, ind_full)["extension_2sigma"].to_numpy())[-1])
    bars, ind = full.iloc[:t + 1], ind_full.iloc[:t + 1]
    a = next(x for x in S.build_signal_stats(dict(ticker="SYN", bars=bars, ind=ind, hz=C.HORIZONS["swing"]))["active"]
             if x["id"] == "extension_2sigma")
    assert a["mae_basis"] == "highest high after entry"
    hz = a["horizons"][10]
    _, high_side = S.forward_outcomes(bars, 10, bias=-1)
    assert hz["unconditional"]["median_mae"] == pytest.approx(np.nanmedian(high_side))
    assert hz["median_mae"] > 0 and hz["unconditional"]["median_mae"] > 0   # a rise is adverse here


def test_empty_history_is_unavailable():
    bars, ind = neutral(0)
    out = S.build_signal_stats(ctx_for(bars, ind))
    assert out["available"] is False and out["reason"]
    assert out["active"] == [] and out["inactive_ids"] == S.SIGNAL_IDS
