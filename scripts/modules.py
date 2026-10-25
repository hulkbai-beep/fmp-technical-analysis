"""
Analysis modules C (trend & momentum), D (relative strength), E (mean reversion), F (volume & flow),
G (volatility structure) and J (event risk).

Each build_x(ctx) takes the context dict built by analyze.py and returns a plain dict with
"available", "notes" and, when the module or a block is skipped, "reason". Returns use the
total-return index of regime.return_index (adj_close ratios); price levels use split-adjusted
OHLC so they compare with the chart and the key levels. See references/methodology.md.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import indicators as I
import regime as R
from regime import iso, last, num, pct

VWAP_METHOD = "daily-bar approximation: typical price x volume"
EARNINGS_METHOD = ("two-day move: adj close of the last bar before the report date -> adj close of the "
                   "first bar after it (FMP earnings has no before-open/after-close flag, so the window "
                   "covers both)")


def _atr_units(close: float | None, ref: float | None, atr: float | None) -> float | None:
    if close is None or ref is None or not atr:
        return None
    return (close - ref) / atr


# ---------------------------------------------------------------------------- C. trend & momentum
def _sma_stack(values: dict[int, float | None]) -> tuple[str | None, str | None]:
    """Order of the available SMAs from top to bottom (e.g. "50>20>200") and bullish/bearish/mixed."""
    have = {p: v for p, v in values.items() if v is not None}
    if len(have) < 2:
        return None, None
    order = sorted(have, key=lambda p: have[p], reverse=True)
    if order == sorted(have):
        state = "bullish"
    elif order == sorted(have, reverse=True):
        state = "bearish"
    else:
        state = "mixed"
    return ">".join(str(p) for p in order), state


def _last_macd_cross(ind: pd.DataFrame) -> dict | None:
    """Most recent MACD / signal-line cross after the EMA warm-up (C.MACD_SLOW + C.MACD_SIGNAL bars)."""
    diff = (ind["macd"] - ind["macd_signal"]).to_numpy(dtype=float, na_value=np.nan)
    prev, cur = diff[:-1], diff[1:]
    bull = (cur > 0) & (prev <= 0)
    bear = (cur < 0) & (prev >= 0)
    hits = np.flatnonzero(bull | bear) + 1
    hits = hits[hits >= C.MACD_SLOW + C.MACD_SIGNAL]
    if hits.size == 0:
        return None
    i = int(hits[-1])
    macd_at = float(ind["macd"].iloc[i])
    return {
        "type": "bullish" if bull[i - 1] else "bearish",
        "date": iso(ind.index[i]),
        "bars_ago": len(ind) - 1 - i,
        "zero_side": "below" if macd_at < 0 else "above",
        "macd_at_cross": macd_at,
    }


def build_trend_momentum(ctx) -> dict:
    """C: 12-1 momentum, period returns, SMA stack and slopes, MACD, distance from the 52-week high."""
    bars, ind = ctx["bars"], ctx["ind"]
    n = len(bars)
    notes: list[str] = []
    tri = R.return_index(bars)
    row = ind.iloc[-1]
    close, atr = last(bars["close"]), num(row["atr14"])

    mom = None
    if n > C.MOMENTUM_LONG:
        mom = num(tri.iloc[-1 - C.MOMENTUM_SKIP] / tri.iloc[-1 - C.MOMENTUM_LONG] - 1.0)
    else:
        notes.append(f"12-1 momentum unavailable: fewer than {C.MOMENTUM_LONG} bars before the last bar (have {n})")

    returns = {}
    for name, k in C.RETURN_WINDOWS.items():
        returns[name] = R.window_return(tri, k)
        if returns[name] is None:
            notes.append(f"{name} return needs {k + 1} bars (have {n})")

    smas = {p: num(row[f"sma{p}"]) for p in C.SMA_PERIODS}
    stack, stack_state = _sma_stack(smas)
    missing = [f"SMA{p}" for p, v in smas.items() if v is None]
    if missing:
        notes.append(f"{', '.join(missing)} unavailable with {n} bars; stack uses the remaining SMAs")
    sma = {
        **{f"sma{p}": v for p, v in smas.items()},
        "slopes": {f"sma{p}": num(row[f"sma{p}_slope"]) for p in C.SMA_PERIODS},
        "close_vs": {f"sma{p}": pct(close, v) for p, v in smas.items()},
        "stack": stack,
        "stack_state": stack_state,
        "slope_bars": C.SLOPE_BARS,
    }

    macd = {"macd": num(row["macd"]), "signal": num(row["macd_signal"]), "hist": num(row["macd_hist"]),
            "last_cross": _last_macd_cross(ind)}

    win = bars["high"].iloc[-C.HIGH_52W_BARS:]
    hi_date = win.idxmax()
    hi = num(win.loc[hi_date])
    if n < C.HIGH_52W_BARS:
        notes.append(f"52-week high uses the {n} bars available (fewer than {C.HIGH_52W_BARS})")
    from_high = {"high": hi, "date": iso(hi_date), "bars_ago": n - 1 - bars.index.get_loc(hi_date),
                 "pct": pct(close, hi), "atr": _atr_units(close, hi, atr)}

    return {"available": True, "close": close, "mom_12_1": mom, "returns": returns, "sma": sma,
            "macd": macd, "from_52w_high": from_high, "notes": notes}


# ---------------------------------------------------------------------------- D. relative strength
def _beta_corr(stock: pd.DataFrame, spy: pd.DataFrame, notes: list) -> tuple[float | None, float | None]:
    """Beta (cov / var, both ddof=1) and correlation of daily returns over the last C.BETA_WINDOW common days."""
    rets = R.joined_index(stock, spy).pct_change().dropna().iloc[-C.BETA_WINDOW:]
    if len(rets) < C.BETA_WINDOW:
        notes.append(f"beta / correlation need {C.BETA_WINDOW} common daily returns with SPY (have {len(rets)})")
        return None, None
    s, b = rets["stock"].to_numpy(), rets["bench"].to_numpy()
    var_b = b.var(ddof=1)
    if var_b == 0 or s.std(ddof=1) == 0:
        notes.append("beta / correlation undefined: zero return variance")
        return None, None
    return num(np.cov(s, b, ddof=1)[0, 1] / var_b), num(np.corrcoef(s, b)[0, 1])


def build_relative_strength(ctx) -> dict:
    """D: RS line vs SPY and vs the sector ETF, 3/6-month relative returns, RS extremes, 1-year beta/correlation."""
    bars = ctx["bars"]
    notes: list[str] = []
    ticker = str(ctx.get("ticker", "")).upper()
    spy, sector, etf = R.frame(ctx, "spy"), R.frame(ctx, "sector"), ctx.get("sector_etf")

    if ticker == C.SPY:
        vs_spy = {"available": False, "benchmark": C.SPY, "reason": "SPY is the benchmark: RS vs SPY skipped"}
    elif spy is None:
        vs_spy = {"available": False, "benchmark": C.SPY,
                  "reason": R.skip_reason(ctx, ("spy", C.SPY), "SPY data unavailable")}
    else:
        vs_spy = R.rs_block(bars, spy, C.SPY, notes)

    if ctx.get("is_etf"):
        vs_sector = {"available": False, "benchmark": etf, "reason": "ETF: sector module skipped"}
    elif sector is None:
        default = f"sector ETF {etf} data unavailable" if etf else "no sector ETF mapped for this sector"
        vs_sector = {"available": False, "benchmark": etf,
                     "reason": R.skip_reason(ctx, ("sector", "sector_etf", etf), default)}
    else:
        vs_sector = R.rs_block(bars, sector, etf, notes)
    vs_sector["etf"] = etf

    beta = corr = None
    if vs_spy["available"]:
        beta, corr = _beta_corr(bars, spy, notes)

    blocks = {"vs_spy": vs_spy, "vs_sector": vs_sector}
    missing = {k: b["reason"] for k, b in blocks.items() if not b["available"]}
    out = {"available": len(missing) < len(blocks), **blocks, "beta_1y": beta, "corr_1y": corr, "notes": notes}
    if missing:
        out["reason"] = "; ".join(f"{k}: {v}" for k, v in missing.items())
    return out


# ---------------------------------------------------------------------------- E. mean reversion
def _band_state(x: float | None, hi: float, lo: float) -> str | None:
    if x is None:
        return None
    return "overbought" if x > hi else "oversold" if x < lo else "neutral"


def build_mean_reversion(ctx) -> dict:
    """E: RSI(14), RSI(2), z-score vs the 20-day mean, Bollinger %B, distance from SMA50 in ATRs."""
    bars, ind = ctx["bars"], ctx["ind"]
    row = ind.iloc[-1]
    close, sma50, atr = last(bars["close"]), num(row["sma50"]), num(row["atr14"])
    rsi14, rsi2, z20, pct_b = num(row["rsi14"]), num(row["rsi2"]), num(row["z20"]), num(row["bb_pct_b"])
    notes = []
    if sma50 is None:
        notes.append(f"SMA{R.SMA_MID} unavailable with {len(bars)} bars: dist_sma50_atr omitted")
    if pct_b is None:
        bb_state = None
    else:
        bb_state = "above upper band" if pct_b > 1 else "below lower band" if pct_b < 0 else "inside bands"
    return {
        "available": True,
        "rsi14": rsi14, "rsi14_state": _band_state(rsi14, C.MR_RSI14_OVERBOUGHT, C.MR_RSI14_OVERSOLD),
        "rsi2": rsi2, "rsi2_state": _band_state(rsi2, C.MR_RSI2_OVERBOUGHT, C.MR_RSI2_OVERSOLD),
        "z20": z20, "z20_state": _band_state(z20, C.MR_Z_EXTENDED, -C.MR_Z_EXTENDED),
        "bb_pct_b": pct_b, "bb_state": bb_state,
        "dist_sma50_atr": _atr_units(close, sma50, atr),
        "notes": notes,
    }


# ---------------------------------------------------------------------------- F. volume & flow
def _flow_line(line: pd.Series, close: pd.Series, volume: pd.Series, name: str) -> dict:
    """Slope and price divergence of a cumulative flow line (OBV or A/D).

    slope_20d = line change over C.SLOPE_BARS bars / total volume of those bars (scale-free, -1..1).
    Bearish divergence at t: close >= max(close over the prior L bars) while the line is below its
    prior L-bar max; bullish is the mirror at lows (L = C.DIVERGENCE_LOOKBACK). The most recent hit
    within the last C.SIGNAL_ACTIVE_BARS bars is reported.
    """
    k, lb = C.SLOPE_BARS, C.DIVERGENCE_LOOKBACK
    n = len(line)
    slope = None
    if n > k:
        vol = float(volume.iloc[-k:].sum())
        slope = num((line.iloc[-1] - line.iloc[-1 - k]) / vol) if vol > 0 else None
    out = {"slope_20d": slope, "slope_sign": R.sign(slope), "divergence": "none",
           "divergence_date": None, "divergence_bars_ago": None,
           "detail": f"no {name} divergence in the last {C.SIGNAL_ACTIVE_BARS} bars"}

    def prior(s, how):
        return getattr(s.shift(1).rolling(lb, min_periods=lb), how)()

    bear = ((close >= prior(close, "max")) & (line < prior(line, "max"))).to_numpy()
    bull = ((close <= prior(close, "min")) & (line > prior(line, "min"))).to_numpy()
    for i in range(n - 1, max(n - C.SIGNAL_ACTIVE_BARS, 0) - 1, -1):
        if bear[i] or bull[i]:
            kind = "bearish" if bear[i] else "bullish"
            ext, side = ("high", "below") if bear[i] else ("low", "above")
            out.update(divergence=kind, divergence_date=iso(line.index[i]), divergence_bars_ago=n - 1 - i,
                       detail=f"{iso(line.index[i])}: close made a new {lb}-bar closing {ext} while {name} "
                              f"stayed {side} its prior {lb}-bar {ext}")
            break
    return out


def _updown_ratio(bars: pd.DataFrame, notes: list) -> float | None:
    """Sum of volume on up-close days / sum on down-close days over the last C.UPDOWN_VOLUME_PERIOD bars."""
    m = C.UPDOWN_VOLUME_PERIOD
    if len(bars) <= m:
        notes.append(f"up/down volume ratio needs {m + 1} bars (have {len(bars)})")
        return None
    chg = bars["close"].diff().iloc[-m:]
    vol = bars["volume"].iloc[-m:]
    up, down = float(vol[chg > 0].sum()), float(vol[chg < 0].sum())
    if down == 0:
        notes.append(f"up/down volume ratio undefined: no down-close volume in the last {m} bars")
        return None
    return up / down


def _split_earnings(ctx) -> tuple[list[pd.Timestamp], list[tuple[pd.Timestamp, dict]]]:
    """(past report dates ascending, upcoming rows ascending) relative to as_of.

    Upcoming = date > as_of, or date == as_of without an actual EPS yet (reports after today's close).
    """
    as_of = R.as_of(ctx)
    rows = []
    for r in ctx.get("earnings") or []:
        d = pd.to_datetime(r.get("date"), errors="coerce")
        if not pd.isna(d):
            rows.append((d.normalize(), r))
    rows.sort(key=lambda x: x[0])
    upcoming = [(d, r) for d, r in rows if d > as_of or (d == as_of and num(r.get("eps_actual")) is None)]
    upcoming_dates = {d for d, _ in upcoming}
    past = sorted({d for d, _ in rows if d <= as_of and d not in upcoming_dates})
    return past, upcoming


def _vwap_anchors(ctx) -> tuple[list[dict], list[str]]:
    """Anchor bars for the anchored VWAPs ({anchor, date, extra fields}) and notes on missing ones."""
    bars, ind = ctx["bars"], ctx["ind"]
    dates = bars.index
    anchors, notes = [], []

    if ctx.get("is_etf"):
        notes.append("last_earnings anchor skipped: ETF")
    else:
        past = [d for d in _split_earnings(ctx)[0] if dates[0] <= d <= dates[-1]]
        if past:
            pos = int(dates.searchsorted(past[-1]))
            anchors.append({"anchor": "last_earnings", "date": dates[pos], "report_date": iso(past[-1])})
        else:
            why = R.skip_reason(ctx, ("earnings",), "no past earnings date in the data")
            notes.append(f"last_earnings anchor unavailable: {why}")

    win = bars.iloc[-C.HIGH_52W_BARS:]
    anchors.append({"anchor": "high_52w", "date": win["high"].idxmax()})
    anchors.append({"anchor": "low_52w", "date": win["low"].idxmin()})
    anchors.append({"anchor": "ytd_start", "date": dates[dates.year == dates[-1].year][0]})

    prev_close, prev_atr = bars["close"].shift(1), ind["atr14"].shift(1)
    gap = (bars["open"] - prev_close) / prev_atr.where(prev_atr > 0)
    recent = gap.iloc[-C.LARGEST_GAP_LOOKBACK:].dropna()
    if recent.empty:
        notes.append("largest_gap_6m anchor unavailable: no bar with a prior-day ATR in the lookback")
    else:
        d = recent.abs().idxmax()
        anchors.append({"anchor": "largest_gap_6m", "date": d, "gap_atr": num(recent.loc[d]),
                        "gap_pct": pct(num(bars["open"].loc[d]), num(prev_close.loc[d]))})
    return anchors, notes


def anchored_vwap_series(ctx) -> dict[str, pd.Series]:
    """Anchored VWAP series for the chart, one per available anchor (same anchors as build_volume_flow)."""
    return {a["anchor"]: I.anchored_vwap(ctx["bars"], a["date"]) for a in _vwap_anchors(ctx)[0]}


def build_volume_flow(ctx) -> dict:
    """F: RVOL, OBV and A/D slope and divergence, 50-day up/down volume ratio, anchored VWAPs."""
    bars, ind = ctx["bars"], ctx["ind"]
    notes: list[str] = []
    close, atr = last(bars["close"]), last(ind["atr14"])
    avg_vol = last(bars["volume"].shift(1).rolling(C.RVOL_PERIOD, min_periods=C.RVOL_PERIOD).mean())
    if avg_vol is None:
        notes.append(f"RVOL needs {C.RVOL_PERIOD + 1} bars (have {len(bars)})")

    anchors, anchor_notes = _vwap_anchors(ctx)
    notes.extend(anchor_notes)
    vwaps = []
    for a in anchors:
        v = last(I.anchored_vwap(bars, a["date"]))
        extra = {k: val for k, val in a.items() if k not in ("anchor", "date")}
        vwaps.append({"anchor": a["anchor"], "anchor_date": iso(a["date"]), "vwap": v,
                      "price_vs_vwap_pct": pct(close, v), "price_vs_vwap_atr": _atr_units(close, v, atr),
                      "method": VWAP_METHOD, **extra})

    return {
        "available": True,
        "volume": last(bars["volume"]),
        "avg_volume_50d": avg_vol,
        "rvol": last(ind["rvol50"]),
        "obv": _flow_line(ind["obv"], bars["close"], bars["volume"], "OBV"),
        "ad": _flow_line(ind["ad"], bars["close"], bars["volume"], "A/D"),
        "updown_volume_ratio_50d": _updown_ratio(bars, notes),
        "anchored_vwaps": vwaps,
        "notes": notes,
    }


# ---------------------------------------------------------------------------- G. volatility
def _last_squeeze_release(bars: pd.DataFrame, ind: pd.DataFrame) -> dict | None:
    """Most recent bar where the squeeze switched off; direction = close vs SMA20 on that bar."""
    on = ind["squeeze_on"].to_numpy(dtype=bool)
    rel = np.flatnonzero(on[:-1] & ~on[1:]) + 1
    if rel.size == 0:
        return None
    i = int(rel[-1])
    c, mid = num(bars["close"].iloc[i]), num(ind["sma20"].iloc[i])
    direction = None if c is None or mid is None else "up" if c > mid else "down" if c < mid else "flat"
    return {"date": iso(ind.index[i]), "bars_ago": len(ind) - 1 - i, "direction": direction,
            "squeeze_bars_before": int(ind["squeeze_bars"].iloc[i - 1])}


def build_volatility(ctx) -> dict:
    """G: ATR(14), ATR%, realized vol, Bollinger bandwidth and its percentile, TTM-style squeeze."""
    bars, ind = ctx["bars"], ctx["ind"]
    row = ind.iloc[-1]
    notes = []
    if num(row["bb_width_pctl"]) is None:
        notes.append(f"percentiles unavailable: each needs a full {C.PERCENTILE_WINDOW}-bar window of its "
                     f"input (have {len(ind)} bars)")
    on = bool(row["squeeze_on"])
    return {
        "available": True,
        "atr14": num(row["atr14"]), "atr_pct": num(row["atr_pct"]), "atr_pct_pctl": num(row["atr_pct_pctl"]),
        "rv20": num(row["rv20"]), "rv20_pctl": num(row["rv20_pctl"]),
        "bb_width": num(row["bb_width"]), "bb_width_pctl": num(row["bb_width_pctl"]),
        "squeeze": {"on": on, "bars": int(row["squeeze_bars"]),
                    "last_release": _last_squeeze_release(bars, ind)},
        "notes": notes,
    }


# ---------------------------------------------------------------------------- J. event risk
def _reactions(bars: pd.DataFrame, past: list[pd.Timestamp]) -> list[dict]:
    """Two-day reactions for the latest C.EARNINGS_REACTIONS_MAX past reports with bars on both sides."""
    tri = R.return_index(bars)
    dates = bars.index
    items = []
    for d in reversed(past):
        pre = int(dates.searchsorted(d, side="left")) - 1   # last bar before the report date
        post = int(dates.searchsorted(d, side="right"))      # first bar after the report date
        if pre < 0 or post >= len(dates):
            continue
        items.append({"date": iso(d), "pre_date": iso(dates[pre]), "post_date": iso(dates[post]),
                      "move": num(tri.iloc[post] / tri.iloc[pre] - 1.0)})
        if len(items) == C.EARNINGS_REACTIONS_MAX:
            break
    return items[::-1]


def build_event_risk(ctx) -> dict:
    """J: next earnings date (calendar / estimated trading days), past earnings reactions, horizon flag."""
    if ctx.get("is_etf"):
        return {"available": False, "reason": "ETF: earnings module skipped", "notes": []}
    if not ctx.get("earnings"):
        return {"available": False, "notes": [],
                "reason": R.skip_reason(ctx, ("earnings",), "no earnings data")}
    notes: list[str] = []
    hz = ctx.get("hz") or C.HORIZONS[ctx.get("horizon", C.DEFAULT_HORIZON)]
    as_of = R.as_of(ctx)
    past, upcoming = _split_earnings(ctx)

    next_e = within = per_h = None
    if upcoming:
        d, row = upcoming[0]
        td = int(np.busday_count(as_of.date(), d.date()))
        next_e = {"date": iso(d), "calendar_days": (d - as_of).days, "trading_days_est": td,
                  "eps_estimated": num(row.get("eps_estimated")),
                  "revenue_estimated": num(row.get("revenue_estimated"))}
        within = td <= hz["primary"]
        per_h = {str(h): td <= h for h in hz["stat_horizons"]}
    else:
        notes.append("no upcoming earnings date in the data")

    items = _reactions(ctx["bars"], past)
    moves = np.abs([it["move"] for it in items if it["move"] is not None])
    last_e = None
    if past:  # the latest report on or before the last bar; its reaction may still be missing (no bar after it yet)
        d = past[-1]
        last_e = {"date": iso(d), "calendar_days_ago": (as_of - d).days,
                  "move": next((it["move"] for it in items if it["date"] == iso(d)), None)}
    if len(items) < C.EARNINGS_REACTIONS_MIN:
        notes.append(f"only {len(items)} past earnings reactions with bars on both sides "
                     f"(fewer than {C.EARNINGS_REACTIONS_MIN}): treat the median move as a small-sample estimate")
    return {
        "available": True,
        "next_earnings": next_e,
        "last_earnings": last_e,
        "within_horizon": within,
        "within_stat_horizons": per_h,
        "horizon_days": hz["primary"],
        "reactions": {"n": len(items),
                      "median_abs_move": num(np.median(moves)) if len(moves) else None,
                      "mean_abs_move": num(moves.mean()) if len(moves) else None,
                      "items": items},
        "method": EARNINGS_METHOD,
        "trading_days_note": "trading days estimated with numpy business days (US exchange holidays not excluded)",
        "notes": notes,
    }
