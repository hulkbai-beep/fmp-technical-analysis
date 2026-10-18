"""Tests for regime.py: market context (A), regime (B), multi-timeframe (I). Synthetic data only."""
import json

import numpy as np
import pandas as pd
import pytest

import config as C
import indicators as I
import regime
from conftest import synthetic_bars

OHLC = ("open", "high", "low", "close")


def make_ctx(bars, **kw):
    ctx = dict(ticker="TEST", as_of=bars.index[-1].strftime("%Y-%m-%d"), horizon="swing",
               hz=C.HORIZONS["swing"], bars=bars, ind=I.compute_all(bars), spy=None, sector=None,
               sector_etf="XLK", vix=None, profile=None, quote=None, earnings=None, is_etf=False,
               data_quality={}, notes=[], skipped={})
    ctx.update(kw)
    return ctx


def benchmark(n=900, seed=3, start="2019-10-01", **kw):
    """Benchmark frame: adj_* equal to open/high/low/close, as fetch delivers SPY / sector ETFs."""
    df = synthetic_bars(n, seed=seed, start=start, **kw)
    for c in OHLC:
        df["adj_" + c] = df[c]
    return df


def bars_from_close(close, start="2022-01-03", spread=0.003, volume=1e6):
    """Daily bars from a close path: open = previous close, high/low = body +/- spread."""
    idx = pd.bdate_range(start, periods=len(close), name="date")
    close = pd.Series(np.asarray(close, dtype=float), index=idx)
    open_ = close.shift(1).fillna(close.iloc[0])
    df = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * (1 + spread),
                       "low": np.minimum(open_, close) * (1 - spread), "close": close,
                       "volume": volume}, index=idx)
    for c in OHLC:
        df["adj_" + c] = df[c]
    return df


def with_last(ctx, **vals):
    """Copy of ctx with chosen ind values overwritten on the last bar."""
    ind = ctx["ind"].copy()
    for col, v in vals.items():
        ind.loc[ind.index[-1], col] = v
    return {**ctx, "ind": ind}


# ---------------------------------------------------------------------------- A. market context
def test_market_context_full(bars):
    spy, vix = benchmark(), benchmark(seed=5, drift=0.0, vol=0.05, start_price=18.0)
    out = regime.build_market_context(make_ctx(bars, spy=spy, sector=benchmark(seed=9), vix=vix))
    assert out["available"] and "reason" not in out
    s = out["spy"]
    spy_cut = spy.loc[:bars.index[-1], "close"]
    sma200 = spy_cut.rolling(200).mean()
    assert s["date"] == bars.index[-1].strftime("%Y-%m-%d")
    assert s["sma200"] == pytest.approx(sma200.iloc[-1])
    assert s["above_sma200"] == (spy_cut.iloc[-1] > sma200.iloc[-1])
    assert s["sma200_slope_20d"] == pytest.approx(sma200.iloc[-1] / sma200.iloc[-21] - 1)
    v = out["vix"]
    w = vix.loc[:bars.index[-1], "close"].iloc[-C.PERCENTILE_WINDOW:]
    assert v["pctl_1y"] == pytest.approx(100 * (w <= w.iloc[-1]).mean())
    sec = out["sector"]
    assert sec["etf"] == "XLK" and sec["trend_label"] in ("Uptrend", "Downtrend", "Mixed")
    assert sec["rs_3m"] is not None and sec["rs_line_slope"] is not None
    json.dumps(out, allow_nan=False)


def test_vix_drops_non_equity_rows_and_future_rows(bars):
    vix = benchmark(seed=5, drift=0.0, vol=0.05, start_price=18.0).loc[:bars.index[-1]]
    as_of = bars.index[-1]
    vix.loc[as_of, "close"] = vix["close"].iloc[-C.PERCENTILE_WINDOW:].max() * 1.1
    saturday = bars.index[-10] + pd.offsets.Week(weekday=5)
    extra = vix.iloc[[-1, -1]].copy()
    extra.index = pd.DatetimeIndex([saturday, as_of + pd.Timedelta(days=3)], name="date")
    extra["close"] = 1000.0
    vix = pd.concat([vix, extra]).sort_index()
    out = regime.build_market_context(make_ctx(bars, vix=vix))["vix"]
    # with the Saturday row inside the window, today's close would not be the 1-year maximum
    assert out["pctl_1y"] == 100.0
    assert out["date"] == as_of.strftime("%Y-%m-%d")
    assert out["level"] == pytest.approx(vix.loc[as_of, "close"])


def test_market_context_missing_frames_use_skipped_reasons(bars):
    ctx = make_ctx(bars, sector=benchmark(seed=9),
                   skipped={"spy": "HTTP 402: restricted", "vix": "empty response"})
    out = regime.build_market_context(ctx)
    assert out["available"]  # the sector block is still there
    assert out["spy"] == {"available": False, "reason": "HTTP 402: restricted"}
    assert out["vix"]["reason"] == "empty response"
    assert out["sector"]["available"] and out["sector"]["rs_3m"] is None
    assert any("RS vs SPY unavailable" in n for n in out["notes"])
    assert "spy: HTTP 402" in out["reason"]

    none = regime.build_market_context(make_ctx(bars, sector_etf=None))
    assert not none["available"]
    assert none["sector"]["reason"] == "no sector ETF mapped for this sector"


def test_market_context_etf_skips_sector(bars):
    out = regime.build_market_context(make_ctx(bars, spy=benchmark(), sector=benchmark(seed=9), is_etf=True))
    assert out["sector"] == {"available": False, "etf": "XLK", "reason": "ETF: sector module skipped"}
    assert out["spy"]["available"]


def test_spy_short_history():
    bars = synthetic_bars(150)
    out = regime.build_market_context(make_ctx(bars, spy=benchmark(150, start="2020-01-02")))["spy"]
    assert out["available"] and out["sma200"] is None and out["above_sma200"] is None


# ---------------------------------------------------------------------------- B. regime labels
def _crafted(bars, sma50_x, sma200_x, slope, adx, rv_p=50.0, atr_p=50.0, bw_p=50.0, pdi=30.0, mdi=10.0):
    ctx = make_ctx(bars)
    c = float(bars["close"].iloc[-1])
    return with_last(ctx, sma50=c * sma50_x, sma200=c * sma200_x, sma200_slope=slope, adx14=adx,
                     plus_di=pdi, minus_di=mdi, rv20_pctl=rv_p, atr_pct_pctl=atr_p, bb_width_pctl=bw_p)


@pytest.mark.parametrize("args, label, rtype, sign", [
    ((0.95, 0.90, 0.01, 30.0), "Strong uptrend / normal vol", "trend", 1),
    ((1.05, 1.10, -0.01, 30.0), "Strong downtrend / normal vol", "trend", -1),
    ((0.95, 0.90, 0.01, 22.0), "Transitional (uptrend bias) / normal vol", "transitional", 1),
    ((1.05, 1.10, -0.01, 22.0), "Transitional (downtrend bias) / normal vol", "transitional", -1),
    ((1.05, 0.90, 0.01, 22.0), "Transitional (mixed) / normal vol", "transitional", 0),
    ((0.95, 0.90, 0.01, 15.0), "Range / normal vol", "range", 1),
])
def test_regime_label_combinations(bars, args, label, rtype, sign):
    out = regime.build_regime(_crafted(bars, *args))
    assert (out["label"], out["type"], out["direction_sign"]) == (label, rtype, sign)


def test_regime_mixed_trending_uses_di_direction(bars):
    out = regime.build_regime(_crafted(bars, 1.05, 0.90, 0.01, 30.0, rv_p=85.0, pdi=10.0, mdi=35.0))
    assert out["label"] == "Mixed (trending down) / high vol"
    assert out["type"] == "trend" and out["direction_sign"] == 0
    assert out["direction"]["votes"] == {"close_vs_sma50": -1, "close_vs_sma200": 1,
                                         "sma50_vs_sma200": 1, "sma200_slope": 1}


def test_regime_volatility_uses_max_percentile(bars):
    low = regime.build_regime(_crafted(bars, 0.95, 0.90, 0.01, 15.0, rv_p=10.0, atr_p=15.0, bw_p=5.0))
    assert low["label"] == "Range / low vol (compression)" and low["type"] == "range"
    # one low and one normal percentile -> the max decides -> normal
    mixed = regime.build_regime(_crafted(bars, 0.95, 0.90, 0.01, 15.0, rv_p=10.0, atr_p=50.0))
    assert mixed["volatility"]["vol_pctl"] == 50.0 and mixed["volatility"]["label"] == "normal"
    extreme = regime.build_regime(_crafted(bars, 0.95, 0.90, 0.01, 30.0, rv_p=97.0, atr_p=40.0))
    assert extreme["label"] == "Strong uptrend / extreme vol" and extreme["type"] == "vol_extreme"


def test_regime_constructed_trending_series():
    rng = np.random.default_rng(1)
    up = bars_from_close(100 * np.exp(np.cumsum(0.003 + 0.006 * rng.normal(size=600))))
    down = bars_from_close(100 * np.exp(np.cumsum(-0.003 + 0.006 * rng.normal(size=600))))
    r_up, r_down = regime.build_regime(make_ctx(up)), regime.build_regime(make_ctx(down))
    assert r_up["label"].startswith("Strong uptrend /") and r_up["direction_sign"] == 1
    assert r_up["strength"]["label"] == "trending" and r_up["type"] == "trend"
    assert r_down["label"].startswith("Strong downtrend /") and r_down["direction_sign"] == -1


def test_regime_constructed_range_series():
    rng = np.random.default_rng(2)
    out = regime.build_regime(make_ctx(bars_from_close(100 * (1 + 0.01 * rng.normal(size=600)))))
    assert out["strength"]["adx"] < C.ADX_RANGE
    assert out["label"].startswith("Range /") and out["type"] == "range"


def test_regime_constructed_high_vol_and_compression():
    rng = np.random.default_rng(3)
    calm_then_wild = np.r_[rng.normal(0.0003, 0.01, 500), rng.normal(0.0, 0.06, 15)]
    wild = regime.build_regime(make_ctx(bars_from_close(100 * np.exp(np.cumsum(calm_then_wild)), spread=0.0)))
    assert wild["volatility"]["label"] == "extreme" and wild["type"] == "vol_extreme"
    assert "/ extreme vol" in wild["label"]

    normal_then_calm = np.r_[rng.normal(0.0003, 0.015, 500), rng.normal(0.0, 0.002, 30)]
    calm = regime.build_regime(make_ctx(bars_from_close(100 * np.exp(np.cumsum(normal_then_calm)), spread=0.0)))
    assert calm["volatility"]["label"] == "low" and calm["volatility"]["compression"] is True
    assert calm["label"].endswith("/ low vol (compression)")


def test_regime_short_history_still_labels():
    out = regime.build_regime(make_ctx(synthetic_bars(150)))
    d = out["direction"]
    assert d["sma200"] is None and d["votes"]["close_vs_sma200"] is None
    assert d["label"] in ("Uptrend", "Downtrend", "Strong uptrend", "Strong downtrend")  # one vote left
    assert out["volatility"]["label"] is None and "vol" not in out["label"]
    assert out["label"] and any("sma200" in n for n in out["notes"])

    tiny = regime.build_regime(make_ctx(synthetic_bars(20)))
    assert tiny["strength"]["label"] is None and tiny["type"] == "transitional"
    assert tiny["label"] == "Undetermined"


# ---------------------------------------------------------------------------- I. multi-timeframe
def test_alignment_truth_table():
    assert regime.alignment(1, 1) == "confirms"
    assert regime.alignment(-1, -1) == "confirms"
    assert regime.alignment(1, -1) == "contradicts"
    assert regime.alignment(-1, 1) == "contradicts"
    assert regime.alignment(0, 1) == "mixed"
    assert regime.alignment(1, 0) == "mixed"
    assert regime.alignment(None, 1) is None


def test_multi_timeframe_uptrend_confirms():
    rng = np.random.default_rng(1)
    bars = bars_from_close(100 * np.exp(np.cumsum(0.003 + 0.006 * rng.normal(size=600))), start="2022-01-03")
    bars = bars.loc[:"2024-04-19"]  # a Friday: complete final week
    out = regime.build_multi_timeframe(make_ctx(bars))
    w = out["weekly"]
    assert w["direction"] == "Uptrend" and w["sign"] == 1 and out["daily_sign"] == 1
    assert out["alignment"] == "confirms" and "confirms" in out["note"]
    assert w["partial_week"] is False
    wk_close = I.resample_weekly(bars)["close"]
    assert w["sma10w"] == pytest.approx(wk_close.iloc[-10:].mean())
    assert w["sma40w"] == pytest.approx(wk_close.iloc[-40:].mean())
    assert w["rsi14w"] == pytest.approx(I.rsi(wk_close, 14).iloc[-1])


def test_multi_timeframe_partial_week_and_short_history():
    bars = synthetic_bars(400).loc[:"2021-07-14"]  # a Wednesday
    out = regime.build_multi_timeframe(make_ctx(bars))
    assert out["weekly"]["partial_week"] is True and out["weekly"]["date"] == "2021-07-14"
    assert any("partial" in n for n in out["notes"])

    short = regime.build_multi_timeframe(make_ctx(synthetic_bars(150)))
    assert short["weekly"]["sma40w"] is None and short["alignment"] is None
    assert any("40 weekly bars" in n for n in short["notes"])
