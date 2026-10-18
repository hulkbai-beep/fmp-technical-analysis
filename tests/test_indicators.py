"""Known-answer tests for scripts/indicators.py on small synthetic inputs (no network)."""
import math

import numpy as np
import pandas as pd
import pytest

import config as C
import indicators as ind
from conftest import synthetic_bars


def _series(values, start="2024-01-01"):
    return pd.Series(np.asarray(values, dtype=float), index=pd.bdate_range(start, periods=len(values), name="date"))


def _bars(high, low, close, volume=None, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(close), name="date")
    volume = np.full(len(close), 1_000.0) if volume is None else volume
    return pd.DataFrame({"open": np.asarray(close, float), "high": np.asarray(high, float),
                         "low": np.asarray(low, float), "close": np.asarray(close, float),
                         "volume": np.asarray(volume, float)}, index=idx)


def _assert_values(series, expected, **tol):
    np.testing.assert_allclose(series.to_numpy(dtype=float), np.asarray(expected, dtype=float),
                               equal_nan=True, **(tol or {"rtol": 1e-12, "atol": 1e-12}))


# ---------------------------------------------------------------------------- averages
def test_sma_arange():
    _assert_values(ind.sma(_series(np.arange(8)), 3), [np.nan, np.nan, 1, 2, 3, 4, 5, 6])


def test_ema_arange_closed_form():
    """EMA seeded at x0 = 0 on x_t = t lags by e_t = (1-a)/a * (1 - (1-a)^t), a = 2/(n+1)."""
    n, t = 9, np.arange(60)
    a = 2.0 / (n + 1)
    expected = t - (1 - a) / a * (1 - (1 - a) ** t)
    _assert_values(ind.ema(_series(t), n), expected, rtol=1e-12, atol=1e-10)


def test_ema_seeds_at_first_valid_value():
    out = ind.ema(_series([np.nan, np.nan, 4.0, 4.0, 10.0]), 3)
    _assert_values(out, [np.nan, np.nan, 4.0, 4.0, 7.0])


def test_wilder_hand_computed():
    """n=3 on 1..6: seed mean(1,2,3)=2, then 2+(4-2)/3=8/3, 8/3+(5-8/3)/3=31/9, 31/9+(6-31/9)/3=116/27."""
    _assert_values(ind.wilder(_series([1, 2, 3, 4, 5, 6]), 3), [np.nan, np.nan, 2, 8 / 3, 31 / 9, 116 / 27])


def test_wilder_leading_and_interior_nan():
    out = ind.wilder(_series([np.nan, np.nan, 3, 6, 9, np.nan, 12]), 2)
    # seed at the 2nd valid value: (3+6)/2 = 4.5; then 4.5+(9-4.5)/2 = 6.75; NaN input skipped;
    # 6.75+(12-6.75)/2 = 9.375
    _assert_values(out, [np.nan, np.nan, np.nan, 4.5, 6.75, np.nan, 9.375])


def test_wilder_too_short_is_all_nan():
    assert ind.wilder(_series([1.0, 2.0]), 3).isna().all()


# ---------------------------------------------------------------------------- RSI
def test_rsi_extremes_and_warmup():
    n = 14
    rising = ind.rsi(_series(np.arange(40) + 100.0), n)
    assert rising.iloc[:n].isna().all()
    _assert_values(rising.iloc[n:], np.full(40 - n, 100.0))
    _assert_values(ind.rsi(_series(140.0 - np.arange(40)), n).iloc[n:], np.zeros(40 - n))
    _assert_values(ind.rsi(_series(np.full(40, 50.0)), n).iloc[n:], np.full(40 - n, 50.0))


def test_rsi_hand_computed_wilder_3():
    """Closes 10, 11, 10.5, 11.5, 12 -> changes +1, -0.5, +1, +0.5.
    Bar 3: avg gain = (1+0+1)/3 = 2/3, avg loss = (0+0.5+0)/3 = 1/6, RS = 4, RSI = 100 - 100/5 = 80.
    Bar 4: avg gain = 2/3 + (0.5-2/3)/3 = 11/18, avg loss = 1/6 + (0-1/6)/3 = 1/9,
           RS = 5.5, RSI = 100 - 100/6.5 = 1100/13 = 84.615...
    """
    out = ind.rsi(_series([10, 11, 10.5, 11.5, 12]), 3)
    _assert_values(out, [np.nan, np.nan, np.nan, 80.0, 1100 / 13])


def test_rsi_matches_published_wilder_example():
    """StockCharts' RSI(14) worked example. Their spreadsheet rounds the averages to 2 dp
    (0.24 / 0.10 -> 70.53); unrounded Wilder gives 70.46, so allow 0.1."""
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03,
              45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45,
              45.78, 45.35, 44.03, 44.18, 44.22, 44.57, 43.42, 42.66, 43.13]
    published = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42,
                 39.99, 41.46, 41.87, 45.46, 37.30, 33.08, 37.77]
    out = ind.rsi(_series(closes), 14)
    assert out.iloc[:14].isna().all()
    np.testing.assert_allclose(out.iloc[14:], published, atol=0.1)


# ---------------------------------------------------------------------------- TR / ATR / ADX
def test_atr_constant_range():
    df = _bars(high=np.full(30, 101.0), low=np.full(30, 99.0), close=np.full(30, 100.0))
    out = ind.atr(df, 14)
    assert out.iloc[:13].isna().all()
    _assert_values(out.iloc[13:], np.full(17, 2.0))


def test_true_range_and_atr_with_gaps():
    """TR: bar0 h-l = 1; bar1 gap up |12-9.5| = 2.5; bar2 |10-11.5| = 1.5 < h-l = 1.8;
    bar3 gap down |8.5-10.2| = 1.7. ATR(2): 1.75, 1.75+(1.8-1.75)/2 = 1.775, 1.775+(1.7-1.775)/2 = 1.7375."""
    df = _bars(high=[10, 12, 11.8, 9.0], low=[9, 11, 10.0, 8.5], close=[9.5, 11.5, 10.2, 8.8])
    _assert_values(ind.true_range(df), [1.0, 2.5, 1.8, 1.7])
    _assert_values(ind.atr(df, 2), [np.nan, 1.75, 1.775, 1.7375])


def test_adx_perfect_trends():
    n, t = 14, np.arange(80, dtype=float)
    up = ind.adx(_bars(high=102 + t, low=100 + t, close=101 + t), n)
    # +DM = 1, -DM = 0, TR = 2 every bar -> +DI = 50, -DI = 0, DX = 100
    assert up[["plus_di", "minus_di"]].iloc[:n].isna().all().all()
    assert up["adx"].iloc[:2 * n - 1].isna().all()
    _assert_values(up["plus_di"].iloc[n:], np.full(80 - n, 50.0))
    _assert_values(up["minus_di"].iloc[n:], np.zeros(80 - n))
    _assert_values(up["adx"].iloc[2 * n - 1:], np.full(80 - 2 * n + 1, 100.0))

    down = ind.adx(_bars(high=202 - t, low=200 - t, close=201 - t), n)
    _assert_values(down["minus_di"].iloc[n:], np.full(80 - n, 50.0))
    _assert_values(down["plus_di"].iloc[n:], np.zeros(80 - n))
    _assert_values(down["adx"].iloc[2 * n - 1:], np.full(80 - 2 * n + 1, 100.0))


def test_adx_flat_bars_and_bounds(bars):
    flat = ind.adx(_bars(high=np.full(40, 10.0), low=np.full(40, 10.0), close=np.full(40, 10.0)), 14)
    assert (flat.dropna() == 0).all().all()
    out = ind.adx(bars, 14).dropna()
    assert ((out >= 0) & (out <= 100)).all().all()


# ---------------------------------------------------------------------------- MACD / bands
def test_macd_constant_is_zero():
    out = ind.macd(_series(np.full(60, 42.0)), 12, 26, 9)
    assert (out == 0).all().all()


def test_macd_signal_and_hist(bars):
    out = ind.macd(bars["close"], 12, 26, 9)
    line = bars["close"].ewm(span=12, adjust=False).mean() - bars["close"].ewm(span=26, adjust=False).mean()
    _assert_values(out["macd"], line)
    _assert_values(out["signal"], line.ewm(span=9, adjust=False).mean())
    _assert_values(out["hist"], out["macd"] - out["signal"])


def test_bollinger_constant():
    out = ind.bollinger(_series(np.full(30, 50.0)), 20, 2.0).iloc[19:]
    assert (out["mid"] == 50).all() and (out["width"] == 0).all()
    assert out["pct_b"].isna().all()


def test_bollinger_known_std():
    """1..5, n=5: mid 3, population std sqrt(2) -> bands 3 +/- 2*sqrt(2);
    %B(5) = (5 - lower) / (4*sqrt(2)) = (1 + sqrt(2)) / (2*sqrt(2)); width = 4*sqrt(2)/3."""
    r2 = math.sqrt(2)
    last = ind.bollinger(_series([1, 2, 3, 4, 5]), 5, 2.0).iloc[-1]
    assert last["mid"] == pytest.approx(3.0)
    assert last["upper"] == pytest.approx(3 + 2 * r2)
    assert last["lower"] == pytest.approx(3 - 2 * r2)
    assert last["pct_b"] == pytest.approx((1 + r2) / (2 * r2))
    assert last["width"] == pytest.approx(4 * r2 / 3)


def test_keltner_definition(bars):
    out = ind.keltner(bars, 20, 10, 1.5)
    mid = ind.ema(bars["close"], 20)
    _assert_values(out["mid"], mid)
    _assert_values(out["upper"] - mid, 1.5 * ind.atr(bars, 10))
    _assert_values(mid - out["lower"], 1.5 * ind.atr(bars, 10))


def test_squeeze_runs():
    idx = pd.bdate_range("2024-01-01", periods=9)
    #            NaN  on   on   on   off  on   on   off  on
    bb_upper = [np.nan, 9, 9, 9, 11, 9, 9, 9, 9]
    bb_lower = [np.nan, 1, 1, 1, 1, 1, 1, -1, 1]
    kc = pd.DataFrame({"upper": 10.0, "lower": 0.0}, index=idx)
    bb = pd.DataFrame({"upper": bb_upper, "lower": bb_lower}, index=idx, dtype=float)
    out = ind.squeeze(bb, kc)
    assert out["on"].tolist() == [False, True, True, True, False, True, True, False, True]
    assert out["bars"].tolist() == [0, 1, 2, 3, 0, 1, 2, 0, 1]
    assert out["on"].dtype == bool and out["bars"].dtype == np.int64


def test_squeeze_matches_definition_on_real_shapes(bars):
    bb, kc = ind.bollinger(bars["close"]), ind.keltner(bars)
    on = ind.squeeze(bb, kc)["on"]
    expected = (bb["upper"] < kc["upper"]) & (bb["lower"] > kc["lower"])
    assert on.equals(expected) and on.any() and not on.all()


def test_zscore_known_values():
    # classic example: mean 5, population std 2
    z = ind.zscore(_series([2, 4, 4, 4, 5, 5, 7, 9]), 8)
    assert z.iloc[:7].isna().all()
    assert z.iloc[-1] == pytest.approx(2.0)
    assert ind.zscore(_series([1, 2, 3, 4, 5]), 5).iloc[-1] == pytest.approx(2 / math.sqrt(2))
    assert ind.zscore(_series(np.full(10, 3.0)), 5).isna().all()


# ---------------------------------------------------------------------------- volume / flow
def test_obv_hand_computed():
    out = ind.obv(_series([10, 11, 11, 10, 12]), _series([100, 200, 300, 400, 500]))
    _assert_values(out, [0, 200, 200, -200, 300])


def test_ad_line_hand_computed():
    """CLV: (9.5-8 - (10-9.5))/2 = 0.5; (0 - 2)/2 = -1; h == l -> 0; (1.5 - 0.5)/2 = 0.5."""
    df = _bars(high=[10, 11, 10, 12], low=[8, 9, 10, 10], close=[9.5, 9, 10, 11.5], volume=[1000, 2000, 500, 400])
    _assert_values(ind.ad_line(df), [500, -1500, -1500, -1300])


def test_rvol_excludes_today():
    vol = _series([100, 100, 100, 100, 100, 1000])
    out = ind.rvol(vol, 5)
    assert out.iloc[:5].isna().all()
    assert out.iloc[-1] == pytest.approx(10.0)       # 1000 / mean(previous five) = 1000 / 100
    assert ind.rvol(_series(np.zeros(8)), 5).isna().all()


def test_anchored_vwap_hand_computed():
    """Anchor on bar 1: typical prices 10, 12, 9 with volumes 100, 300, 0 ->
    10, (1000 + 3600) / 400 = 11.5, unchanged 11.5 on the zero-volume bar."""
    df = _bars(high=[20, 11, 13, 10], low=[18, 9, 11, 8], close=[19, 10, 12, 9], volume=[999, 100, 300, 0])
    out = ind.anchored_vwap(df, df.index[1])
    _assert_values(out, [np.nan, 10.0, 11.5, 11.5])
    # anchor on Saturday 2024-01-06: the VWAP starts at the next bar (Monday 2024-01-08)
    weekend = ind.anchored_vwap(_bars(high=[20, 11], low=[18, 9], close=[19, 10], start="2024-01-05"), "2024-01-06")
    _assert_values(weekend, [np.nan, 10.0])


def test_anchored_vwap_zero_volume_and_late_anchor():
    df = _bars(high=[11, 12], low=[9, 10], close=[10, 11], volume=[0, 0])
    assert ind.anchored_vwap(df, df.index[0]).isna().all()
    assert ind.anchored_vwap(df, df.index[-1] + pd.Timedelta(days=1)).isna().all()


# ---------------------------------------------------------------------------- volatility / statistics
def test_realized_vol_constant_and_alternating():
    n, r = 20, 0.01
    steady = ind.realized_vol(_series(100 * np.exp(r * np.arange(60))), n)
    assert steady.iloc[:n].isna().all()
    np.testing.assert_allclose(steady.iloc[n:], 0.0, atol=1e-10)
    # +r, -r alternating: n log returns with mean 0 -> sample std = r * sqrt(n / (n - 1))
    alt = ind.realized_vol(_series(100 * np.exp(r * (np.arange(60) % 2))), n)
    expected = r * math.sqrt(n / (n - 1)) * math.sqrt(C.TRADING_DAYS_PER_YEAR)
    np.testing.assert_allclose(alt.iloc[n:], expected, rtol=1e-9)


def test_rolling_percentile_known_ranks():
    """window 3 on [3, 1, 2, 2, 5, 0]: 2 in {3,1,2} -> 2/3; 2 in {1,2,2} -> 3/3 (ties count);
    5 in {2,2,5} -> 3/3; 0 in {2,5,0} -> 1/3."""
    out = ind.rolling_percentile(_series([3, 1, 2, 2, 5, 0]), 3)
    _assert_values(out, [np.nan, np.nan, 200 / 3, 100, 100, 100 / 3])


def test_rolling_percentile_nan_safe_and_matches_brute_force(bars):
    out = ind.rolling_percentile(_series([1, np.nan, 2, 3, 4, np.nan, 5]), 2)
    _assert_values(out, [np.nan, np.nan, np.nan, 100, 100, np.nan, np.nan])
    s = bars["close"].iloc[:300]
    brute = s.rolling(50).apply(lambda w: 100.0 * (w <= w[-1]).mean(), raw=True)
    _assert_values(ind.rolling_percentile(s, 50), brute)
    assert ind.rolling_percentile(s.iloc[:10], 50).isna().all()


def test_daily_returns_fallback():
    df = _bars(high=[11, 12, 13, 14], low=[9, 10, 11, 12], close=[10, 11, 12, 13])
    df["adj_close"] = [np.nan, 5.0, 5.5, 6.6]
    # bar 1: adj pair incomplete -> close 11/10 - 1; bars 2-3 use adj_close
    _assert_values(ind.daily_returns(df), [np.nan, 0.1, 0.1, 0.2])
    _assert_values(ind.daily_returns(df.drop(columns="adj_close")), [np.nan, 0.1, 1 / 11, 1 / 12])


# ---------------------------------------------------------------------------- weekly
def test_resample_weekly_holiday_and_partial_week():
    # week 1: Mon 2024-03-18 .. Fri 03-22; week 2: Mon 03-25 .. Thu 03-28 (Good Friday 03-29 closed);
    # week 3: Mon 04-01 .. Wed 04-03 (latest, incomplete)
    days = pd.to_datetime(["2024-03-18", "2024-03-19", "2024-03-20", "2024-03-21", "2024-03-22",
                           "2024-03-25", "2024-03-26", "2024-03-27", "2024-03-28",
                           "2024-04-01", "2024-04-02", "2024-04-03"])
    k = np.arange(len(days), dtype=float)
    df = pd.DataFrame({"open": 100 + k, "high": 102 + k, "low": 99 + k, "close": 101 + k,
                       "volume": 10 + k}, index=pd.DatetimeIndex(days, name="date"))
    for col in ("open", "high", "low", "close"):
        df["adj_" + col] = df[col] / 2
    df.loc[days[6], "high"] = 200.0     # week-2 high on a Tuesday
    df.loc[days[10], "low"] = 50.0      # week-3 low on a Tuesday

    w = ind.resample_weekly(df)
    assert list(w.index) == list(pd.to_datetime(["2024-03-22", "2024-03-28", "2024-04-03"]))
    assert w.index.name == "date"
    assert w["open"].tolist() == [100, 105, 109]
    assert w["high"].tolist() == [106, 200, 113]
    assert w["low"].tolist() == [99, 104, 50]
    assert w["close"].tolist() == [105, 109, 112]
    assert w["volume"].tolist() == [sum(10 + k[:5]), sum(10 + k[5:9]), sum(10 + k[9:])]
    assert w["adj_close"].tolist() == [52.5, 54.5, 56.0]
    assert w["adj_high"].tolist() == [53.0, 55.0, 56.5]   # set before the overrides
    assert w["partial"].tolist() == [False, False, True]

    # once the week completes on Friday it is no longer partial
    full = pd.concat([df, df.iloc[[-1]].set_axis(pd.DatetimeIndex(["2024-04-05"], name="date"))])
    assert ind.resample_weekly(full)["partial"].tolist() == [False, False, False]


# ---------------------------------------------------------------------------- compute_all
def test_compute_all_columns_and_types(bars):
    out = ind.compute_all(bars)
    assert list(out.columns) == list(ind.IND_COLUMNS)
    assert out.index.equals(bars.index)
    assert out["squeeze_on"].dtype == bool and out["squeeze_bars"].dtype == np.int64
    assert out.drop(columns=["squeeze_on", "squeeze_bars"]).dtypes.eq(np.float64).all()
    last = out.iloc[-1]
    assert last.drop(["squeeze_on", "squeeze_bars"]).notna().all()
    assert last["atr_pct"] == pytest.approx(last["atr14"] / bars["close"].iloc[-1])
    assert last["sma50_slope"] == pytest.approx(out["sma50"].iloc[-1] / out["sma50"].iloc[-1 - C.SLOPE_BARS] - 1)
    np.testing.assert_allclose(out["ret1"].iloc[1:], bars["adj_close"].pct_change().iloc[1:], rtol=1e-12)
    assert out["hh55_prev"].iloc[-1] == bars["close"].iloc[-56:-1].max()
    assert out["hh252_prev"].iloc[-1] == bars["close"].iloc[-253:-1].max()


def test_compute_all_without_adjusted_columns(bars):
    raw = bars[["open", "high", "low", "close", "volume"]]
    out = ind.compute_all(raw)
    np.testing.assert_allclose(out["ret1"].iloc[1:], raw["close"].pct_change().iloc[1:], rtol=1e-12)
    np.testing.assert_allclose(out["rv20"].dropna(), ind.realized_vol(raw["close"], 20).dropna(), rtol=1e-9)


def test_compute_all_short_history():
    out = ind.compute_all(synthetic_bars(n=60))
    assert out["sma200"].isna().all() and out["hh252_prev"].isna().all() and out["rv20_pctl"].isna().all()
    assert out["sma20"].notna().iloc[-1] and out["rsi14"].notna().iloc[-1]


def test_compute_all_has_no_look_ahead():
    bars = synthetic_bars(n=700, seed=11)
    full = ind.compute_all(bars)
    for k in (1, 2, 15, 30, 60, 201, 253, 300, 520, 699):
        part = ind.compute_all(bars.iloc[:k])
        pd.testing.assert_frame_equal(part, full.iloc[:k], check_exact=False, rtol=1e-10, atol=1e-10)
