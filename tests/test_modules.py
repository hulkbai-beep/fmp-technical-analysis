"""Tests for modules.py: trend/momentum (C), RS (D), mean reversion (E), volume/flow (F),
volatility (G), event risk (J). Synthetic data only."""
import json

import numpy as np
import pandas as pd
import pytest

import config as C
import indicators as I
import modules as M
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
    ind = ctx["ind"].copy()
    for col, v in vals.items():
        ind.loc[ind.index[-1], col] = v
    return {**ctx, "ind": ind}


def day(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


ALL_BUILDERS = (regime.build_market_context, regime.build_regime, regime.build_multi_timeframe,
                M.build_trend_momentum, M.build_relative_strength, M.build_mean_reversion,
                M.build_volume_flow, M.build_volatility, M.build_event_risk)


# ---------------------------------------------------------------------------- C. trend & momentum
def test_trend_momentum_geometric_path():
    bars = bars_from_close(100 * 1.001 ** np.arange(400))
    out = M.build_trend_momentum(make_ctx(bars))
    assert out["mom_12_1"] == pytest.approx(1.001 ** (C.MOMENTUM_LONG - C.MOMENTUM_SKIP) - 1)
    for name, k in C.RETURN_WINDOWS.items():
        assert out["returns"][name] == pytest.approx(1.001 ** k - 1)
    assert out["sma"]["stack"] == "20>50>200" and out["sma"]["stack_state"] == "bullish"
    assert out["sma"]["slopes"]["sma200"] > 0
    assert out["from_52w_high"]["bars_ago"] == 0
    assert out["notes"] == []


def test_trend_momentum_uses_adjusted_closes():
    bars = bars_from_close(100 * 1.001 ** np.arange(300))
    adj = bars["close"] * np.where(np.arange(300) < 250, 0.9, 1.0)  # a 10% dividend adjustment
    bars = bars.assign(adj_close=adj)
    out = M.build_trend_momentum(make_ctx(bars))
    assert out["returns"]["3m"] == pytest.approx(adj.iloc[-1] / adj.iloc[-64] - 1)


def test_trend_momentum_short_history():
    out = M.build_trend_momentum(make_ctx(synthetic_bars(230)))
    assert out["mom_12_1"] is None and out["returns"]["12m"] is None
    assert out["returns"]["6m"] is not None
    assert any("fewer than 252 bars" in n for n in out["notes"])

    short = M.build_trend_momentum(make_ctx(synthetic_bars(150)))
    assert short["sma"]["sma200"] is None and short["sma"]["stack"] in ("20>50", "50>20")
    assert any("SMA200" in n for n in short["notes"])

    exactly = M.build_trend_momentum(make_ctx(synthetic_bars(C.MOMENTUM_LONG + 1)))
    assert exactly["mom_12_1"] is not None


def test_macd_last_cross_matches_manual():
    # arithmetic decline: MACD falls toward a constant and stays below its signal; the rally crosses it
    path = np.r_[200 - 0.5 * np.arange(120), 200 - 0.5 * 119 + np.arange(1, 16)]
    ctx = make_ctx(bars_from_close(path))
    cross = M.build_trend_momentum(ctx)["macd"]["last_cross"]
    diff = (ctx["ind"]["macd"] - ctx["ind"]["macd_signal"]).to_numpy()
    i = max(j for j in range(1, len(diff)) if diff[j] > 0 >= diff[j - 1] or diff[j] < 0 <= diff[j - 1])
    assert i == 120  # first rally bar
    assert cross["type"] == "bullish" and cross["zero_side"] == "below"
    assert cross["bars_ago"] == len(diff) - 1 - i == 14
    assert cross["date"] == day(ctx["ind"].index[i])


def test_distance_from_52w_high():
    bars = synthetic_bars(400)
    spike = bars.index[-40]
    bars.loc[spike, "high"] = bars["high"].max() * 2
    ctx = make_ctx(bars)
    out = M.build_trend_momentum(ctx)["from_52w_high"]
    close, high = bars["close"].iloc[-1], bars.loc[spike, "high"]
    assert out["date"] == day(spike) and out["bars_ago"] == 39
    assert out["pct"] == pytest.approx(close / high - 1)
    assert out["atr"] == pytest.approx((close - high) / ctx["ind"]["atr14"].iloc[-1])


# ---------------------------------------------------------------------------- D. relative strength
def _bench(n=600, seed=4):
    rng = np.random.default_rng(seed)
    return bars_from_close(300 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n))))


def test_rs_beta_and_correlation_known_answer():
    spy = _bench()
    r = spy["close"].pct_change().fillna(0.0).to_numpy()
    stock = bars_from_close(50 * np.cumprod(1 + 2 * r))
    out = M.build_relative_strength(make_ctx(stock, spy=spy))
    assert out["beta_1y"] == pytest.approx(2.0) and out["corr_1y"] == pytest.approx(1.0)


def test_rs_line_new_high_and_relative_returns():
    spy = _bench()
    t = np.arange(len(spy))
    stock = bars_from_close(spy["close"].to_numpy() * np.exp(0.002 * t) / 6)
    sector = _bench(seed=8)
    out = M.build_relative_strength(make_ctx(stock, spy=spy, sector=sector))
    v = out["vs_spy"]
    assert v["rs_new_high"] is True and v["rs_new_low"] is False
    assert v["ratio_change_3m"] == pytest.approx(np.exp(0.002 * 63) - 1)
    assert v["rs_line_slope"] == pytest.approx(np.exp(0.002 * C.SLOPE_BARS) - 1)
    s, b = stock["close"], spy["close"]
    assert v["rel_return_6m"] == pytest.approx((s.iloc[-1] / s.iloc[-127] - 1) - (b.iloc[-1] / b.iloc[-127] - 1))
    assert v["ratio"] == pytest.approx(100 * np.exp(0.002 * (C.RS_EXTREME_LOOKBACK - 1)))
    assert out["vs_sector"]["available"] and out["vs_sector"]["etf"] == "XLK"


def test_rs_inner_joins_dates():
    spy = _bench()
    stock = _bench(seed=5)
    spy = spy.drop(spy.index[[100, 200, 300]])
    stock = stock.drop(stock.index[[150, 250]])
    out = M.build_relative_strength(make_ctx(stock, spy=spy))
    assert out["vs_spy"]["bars"] == len(stock.index.intersection(spy.index))


def test_rs_spy_ticker_etf_and_missing_frames():
    spy = _bench()
    as_spy = M.build_relative_strength(make_ctx(spy, ticker="SPY", spy=spy, is_etf=True))
    assert not as_spy["available"]
    assert as_spy["vs_spy"]["reason"] == "SPY is the benchmark: RS vs SPY skipped"
    assert as_spy["vs_sector"]["reason"] == "ETF: sector module skipped"
    assert as_spy["beta_1y"] is None

    missing = M.build_relative_strength(make_ctx(_bench(seed=5), skipped={"spy": "HTTP 402: restricted"}))
    assert missing["vs_spy"]["reason"] == "HTTP 402: restricted"
    assert missing["vs_sector"]["reason"] == "sector ETF XLK data unavailable"


def test_rs_short_history_notes():
    spy = _bench()
    stock = bars_from_close(np.linspace(10, 12, 100), start=spy.index[-100])
    out = M.build_relative_strength(make_ctx(stock, spy=spy))
    v = out["vs_spy"]
    assert v["rel_return_3m"] is not None and v["rel_return_6m"] is None and v["rs_new_high"] is None
    assert out["beta_1y"] is None and any("beta" in n for n in out["notes"])


def test_benchmark_rows_after_as_of_are_ignored():
    spy = _bench(700)
    stock = bars_from_close(spy["close"].to_numpy()[:600] * 0.2)
    future = spy.copy()
    future.loc[future.index[600:], ["close", "adj_close"]] *= 3.0
    cut, ahead = make_ctx(stock, spy=spy.iloc[:600]), make_ctx(stock, spy=future)
    assert M.build_relative_strength(cut) == M.build_relative_strength(ahead)
    assert regime.build_market_context(cut)["spy"] == regime.build_market_context(ahead)["spy"]


# ---------------------------------------------------------------------------- E. mean reversion
def test_mean_reversion_values_and_states(bars):
    ctx = with_last(make_ctx(bars), rsi14=75.0, rsi2=5.0, z20=-2.5, bb_pct_b=1.2)
    out = M.build_mean_reversion(ctx)
    assert (out["rsi14_state"], out["rsi2_state"], out["z20_state"], out["bb_state"]) == \
        ("overbought", "oversold", "oversold", "above upper band")
    ind = ctx["ind"].iloc[-1]
    assert out["dist_sma50_atr"] == pytest.approx((bars["close"].iloc[-1] - ind["sma50"]) / ind["atr14"])

    neutral = M.build_mean_reversion(with_last(make_ctx(bars), rsi14=50.0, rsi2=50.0, z20=0.5, bb_pct_b=0.5))
    assert (neutral["rsi14_state"], neutral["rsi2_state"], neutral["z20_state"], neutral["bb_state"]) == \
        ("neutral", "neutral", "neutral", "inside bands")


# ---------------------------------------------------------------------------- F. volume & flow
def _divergence_bars(kind, last_volume=1e6):
    """Flat base, a 10-bar push to an extreme on heavy volume, a 10-bar retrace on heavy volume,
    then one bar marginally beyond the extreme on light volume."""
    s = 1 if kind == "bearish" else -1
    base = 100 + s * 0.2 * (np.arange(179) % 2)
    push = 100 + s * np.arange(1, 11)
    retrace = push[-1] - s * 0.5 * np.arange(1, 11)
    path = np.r_[base, push, retrace, push[-1] + s * 0.5]
    volume = np.r_[np.full(179, 1e6), np.full(20, 5e6), last_volume]
    return bars_from_close(path, volume=volume)


@pytest.mark.parametrize("kind", ["bearish", "bullish"])
def test_obv_divergence_detected(kind):
    out = M.build_volume_flow(make_ctx(_divergence_bars(kind)))
    assert out["obv"]["divergence"] == kind and out["obv"]["divergence_bars_ago"] == 0
    # closes sit near the high on up bars and near the low on down bars, so A/D diverges the same way
    assert out["ad"]["divergence"] == kind


def test_obv_no_divergence_when_volume_confirms():
    out = M.build_volume_flow(make_ctx(_divergence_bars("bearish", last_volume=200e6)))
    assert out["obv"]["divergence"] == "none" and out["obv"]["slope_sign"] == 1


def test_updown_volume_ratio_and_rvol():
    i = np.arange(80)
    close = 100 + (i % 2)                       # up on odd bars, down on even bars
    volume = np.where(i % 2 == 1, 2e6, 1e6)
    out = M.build_volume_flow(make_ctx(bars_from_close(close, volume=volume)))
    assert out["updown_volume_ratio_50d"] == pytest.approx(2.0)
    assert out["rvol"] == pytest.approx(2e6 / 1.5e6)


def test_anchored_vwap_anchors():
    bars = synthetic_bars(400, start="2024-01-02")
    n, idx = len(bars), bars.index
    hi_d, lo_d, gap_d = idx[n - 100], idx[n - 50], idx[n - 30]
    bars.loc[hi_d, "high"] = bars["high"].max() * 1.5
    bars.loc[lo_d, "low"] = bars["low"].min() * 0.5
    bars.loc[gap_d, "open"] = bars["close"].iloc[n - 31] * 1.25
    bars.loc[gap_d, "high"] = max(bars.loc[gap_d, "high"], bars.loc[gap_d, "open"])
    friday = next(d for d in idx[n - 80:] if d.dayofweek == 4)
    saturday, monday = friday + pd.Timedelta(days=1), idx[idx.get_loc(friday) + 1]
    earnings = [{"date": day(idx[n - 200]), "eps_actual": 1.0},
                {"date": day(saturday), "eps_actual": 1.1},
                {"date": day(idx[-1] + pd.Timedelta(days=30)), "eps_actual": None}]
    ctx = make_ctx(bars, earnings=earnings)
    out = M.build_volume_flow(ctx)
    got = {v["anchor"]: v for v in out["anchored_vwaps"]}
    ytd = idx[idx >= pd.Timestamp(f"{idx[-1].year}-01-01")][0]
    assert {k: v["anchor_date"] for k, v in got.items()} == {
        "last_earnings": day(monday), "high_52w": day(hi_d), "low_52w": day(lo_d),
        "ytd_start": day(ytd), "largest_gap_6m": day(gap_d)}
    assert got["last_earnings"]["report_date"] == day(saturday)
    assert got["largest_gap_6m"]["gap_pct"] == pytest.approx(0.25)

    sub = bars.loc[ytd:]
    tp = (sub["high"] + sub["low"] + sub["close"]) / 3
    vwap = (tp * sub["volume"]).sum() / sub["volume"].sum()
    close, atr = bars["close"].iloc[-1], ctx["ind"]["atr14"].iloc[-1]
    assert got["ytd_start"]["vwap"] == pytest.approx(vwap)
    assert got["ytd_start"]["price_vs_vwap_pct"] == pytest.approx(close / vwap - 1)
    assert got["ytd_start"]["price_vs_vwap_atr"] == pytest.approx((close - vwap) / atr)
    assert all(v["method"] == M.VWAP_METHOD for v in got.values())

    series = M.anchored_vwap_series(ctx)
    assert set(series) == set(got)
    assert series["ytd_start"].iloc[-1] == pytest.approx(vwap)
    assert series["ytd_start"].loc[:ytd].iloc[:-1].isna().all()


def test_anchored_vwap_etf_has_no_earnings_anchor(bars):
    earnings = [{"date": day(bars.index[-20]), "eps_actual": 1.0}]
    out = M.build_volume_flow(make_ctx(bars, is_etf=True, earnings=earnings))
    assert "last_earnings" not in {v["anchor"] for v in out["anchored_vwaps"]}
    assert any("ETF" in n for n in out["notes"])


# ---------------------------------------------------------------------------- G. volatility
def test_squeeze_release_detected(bars):
    ctx = make_ctx(bars)
    ind = ctx["ind"].copy()
    n = len(ind)
    on = np.zeros(n, dtype=bool)
    on[n - 12:n - 5] = True
    run = pd.Series(on, index=ind.index)
    ind["squeeze_on"] = run
    ind["squeeze_bars"] = run.astype("int64").groupby((~run).cumsum()).cumsum()
    out = M.build_volatility({**ctx, "ind": ind})
    rel = out["squeeze"]["last_release"]
    i = n - 5
    expected = "up" if bars["close"].iloc[i] > ind["sma20"].iloc[i] else "down"
    assert out["squeeze"]["on"] is False and out["squeeze"]["bars"] == 0
    assert rel == {"date": day(ind.index[i]), "bars_ago": 4, "direction": expected, "squeeze_bars_before": 7}
    assert out["atr14"] == pytest.approx(ind["atr14"].iloc[-1])


def test_volatility_short_history_note():
    out = M.build_volatility(make_ctx(synthetic_bars(150)))
    assert out["bb_width_pctl"] is None and out["notes"]


# ---------------------------------------------------------------------------- J. event risk
def _calendar_bars():
    return synthetic_bars(300, start="2024-01-02")


def test_earnings_reaction_windows():
    bars = _calendar_bars()
    idx = bars.index
    as_of = idx[-1]
    friday = next(d for d in idx[230:] if d.dayofweek == 4)
    j = idx.get_loc(friday)
    trading_reports = [idx[50], idx[113], idx[176]]
    earnings = ([{"date": day(d), "eps_actual": 1.0} for d in trading_reports]
                + [{"date": day(friday + pd.Timedelta(days=1)), "eps_actual": 1.0},   # Saturday
                   {"date": day(as_of), "eps_actual": 1.0},                            # today, pre-market
                   {"date": day(as_of + pd.Timedelta(days=14)), "eps_actual": None, "eps_estimated": 2.0}])
    out = M.build_event_risk(make_ctx(bars, earnings=earnings))
    adj = bars["adj_close"]
    items = out["reactions"]["items"]
    assert [it["date"] for it in items] == [day(d) for d in trading_reports] + [day(friday + pd.Timedelta(days=1))]
    for it, d in zip(items, trading_reports):
        i = idx.get_loc(d)
        assert (it["pre_date"], it["post_date"]) == (day(idx[i - 1]), day(idx[i + 1]))
        assert it["move"] == pytest.approx(adj.iloc[i + 1] / adj.iloc[i - 1] - 1)
    sat = items[-1]
    assert (sat["pre_date"], sat["post_date"]) == (day(friday), day(idx[j + 1]))
    assert sat["move"] == pytest.approx(adj.iloc[j + 1] / adj.iloc[j] - 1)
    moves = [abs(it["move"]) for it in items]
    assert out["reactions"]["n"] == 4 and out["reactions"]["median_abs_move"] == pytest.approx(np.median(moves))
    assert any("fewer than 8" in n for n in out["notes"])

    nxt = out["next_earnings"]
    assert nxt == {"date": day(as_of + pd.Timedelta(days=14)), "calendar_days": 14, "trading_days_est": 10,
                   "eps_estimated": 2.0, "revenue_estimated": None}
    assert out["within_horizon"] is True
    assert out["within_stat_horizons"] == {"5": False, "10": True, "20": True}


def test_earnings_reactions_capped_and_same_day_upcoming():
    bars = _calendar_bars()
    idx = bars.index
    past = [idx[i] for i in range(5, 290, 19)]  # 15 reports
    earnings = [{"date": day(d), "eps_actual": 1.0} for d in past] + [{"date": day(idx[-1]), "eps_actual": None}]
    out = M.build_event_risk(make_ctx(bars, earnings=earnings, horizon="position", hz=C.HORIZONS["position"]))
    assert out["reactions"]["n"] == C.EARNINGS_REACTIONS_MAX
    assert [it["date"] for it in out["reactions"]["items"]] == [day(d) for d in past[-12:]]
    assert out["next_earnings"]["date"] == day(idx[-1]) and out["next_earnings"]["trading_days_est"] == 0
    assert out["within_horizon"] is True and set(out["within_stat_horizons"]) == {"20", "60"}
    assert not any("fewer than" in n for n in out["notes"])


def test_event_risk_skips():
    bars = _calendar_bars()
    etf = M.build_event_risk(make_ctx(bars, is_etf=True, earnings=[{"date": "2024-03-01"}]))
    assert etf == {"available": False, "reason": "ETF: earnings module skipped", "notes": []}
    none = M.build_event_risk(make_ctx(bars, skipped={"earnings": "HTTP 403: premium endpoint"}))
    assert not none["available"] and none["reason"] == "HTTP 403: premium endpoint"

    past_only = M.build_event_risk(make_ctx(bars, earnings=[{"date": day(bars.index[100]), "eps_actual": 1.0}]))
    assert past_only["next_earnings"] is None and past_only["within_horizon"] is None
    assert any("no upcoming earnings" in n for n in past_only["notes"])


# ---------------------------------------------------------------------------- edge-case sweep
def _contexts():
    spy = synthetic_bars(900, seed=3, start="2019-10-01")
    vix = synthetic_bars(900, seed=5, start="2019-10-01", drift=0.0, vol=0.05, start_price=18.0)
    full = synthetic_bars(800)
    earnings = [{"date": day(d), "eps_actual": 1.0} for d in full.index[::63]]
    yield "full", make_ctx(full, spy=spy, sector=spy, vix=vix, earnings=earnings)
    for n in (25, 60, 150, 230):
        yield f"short{n}", make_ctx(synthetic_bars(n), spy=spy, sector=spy, vix=vix, earnings=earnings)
    yield "etf", make_ctx(full, spy=spy, vix=vix, is_etf=True, sector_etf=None)
    yield "spy", make_ctx(spy, ticker="SPY", spy=spy, vix=vix, is_etf=True, sector_etf=None)
    yield "no_frames", make_ctx(full, skipped={"spy": "restricted", "vix": "restricted",
                                               "sector": "restricted", "earnings": "restricted"})


@pytest.mark.parametrize("name, ctx", list(_contexts()), ids=lambda x: x if isinstance(x, str) else "")
def test_all_builders_return_json_safe_dicts(name, ctx):
    for build in ALL_BUILDERS:
        out = build(ctx)
        assert isinstance(out["available"], bool) and isinstance(out["notes"], list), build.__name__
        if not out["available"]:
            assert isinstance(out["reason"], str) and out["reason"]
        json.dumps(out, allow_nan=False)
