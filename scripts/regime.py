"""
Regime-level builders: market context (module A), regime (B) and multi-timeframe (I).

Each build_x(ctx) takes the context dict built by analyze.py and returns a plain dict with
"available", "notes" and, when the module or one of its blocks is skipped, "reason". The helpers
in the first section (value extraction, return index, direction votes, relative-strength block)
are shared with modules.py. Definitions are documented in references/methodology.md.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import config as C
import indicators as I

SMA_MID, SMA_LONG = C.SMA_PERIODS[1], C.SMA_PERIODS[2]


# ---------------------------------------------------------------------------- shared helpers
def num(x) -> float | None:
    """Plain float, or None for missing / non-finite values."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def last(s: pd.Series) -> float | None:
    return num(s.iloc[-1]) if len(s) else None


def sign(x: float | None) -> int | None:
    return None if x is None else int(np.sign(x))


def pct(a: float | None, b: float | None) -> float | None:
    """a / b - 1, None when an input is missing or b is 0."""
    if a is None or b is None or b == 0:
        return None
    return a / b - 1.0


def as_of(ctx) -> pd.Timestamp:
    return pd.Timestamp(ctx.get("as_of") or ctx["bars"].index[-1])


def frame(ctx, key: str) -> pd.DataFrame | None:
    """ctx[key] cut to dates <= as_of (a benchmark never runs past the analysed bar); None if empty."""
    df = ctx.get(key)
    if df is None or len(df) == 0:
        return None
    df = df.loc[df.index <= as_of(ctx)]
    return df if len(df) else None


def skip_reason(ctx, keys, default: str) -> str:
    """The reason fetch recorded in ctx["skipped"] under any of `keys` (case-insensitive), else default."""
    skipped = {str(k).lower(): v for k, v in (ctx.get("skipped") or {}).items()}
    for k in keys:
        if k and str(k).lower() in skipped:
            return str(skipped[str(k).lower()])
    return default


def return_index(bars: pd.DataFrame) -> pd.Series:
    """Cumulative total-return index (1.0 at the first bar) from indicators.daily_returns.

    Ratios of the index equal adj_close ratios; where the dividend-adjusted pair is missing the
    daily return falls back to split-adjusted closes (price return only).
    """
    return (1.0 + I.daily_returns(bars).fillna(0.0)).cumprod()


def window_return(s: pd.Series, k: int) -> float | None:
    """s[t] / s[t-k] - 1 at the last bar; None with k or fewer values."""
    if len(s) <= k:
        return None
    return num(s.iloc[-1] / s.iloc[-1 - k] - 1.0)


def direction_votes(close, sma50, sma200, sma200_slope) -> dict:
    """+1 bullish / -1 bearish / 0 flat for each direction condition; None when an input is missing."""
    def above(a, b):
        return None if a is None or b is None else int(np.sign(a - b))

    return {
        "close_vs_sma50": above(close, sma50),
        "close_vs_sma200": above(close, sma200),
        "sma50_vs_sma200": above(sma50, sma200),
        "sma200_slope": sign(sma200_slope),
    }


def direction_from_votes(votes: dict) -> tuple[str | None, int]:
    """("Uptrend", +1) if every available vote is bullish, ("Downtrend", -1) if every one is
    bearish, ("Mixed", 0) otherwise; (None, 0) with no votes."""
    vals = [v for v in votes.values() if v is not None]
    if not vals:
        return None, 0
    if all(v > 0 for v in vals):
        return "Uptrend", 1
    if all(v < 0 for v in vals):
        return "Downtrend", -1
    return "Mixed", 0


def joined_index(stock: pd.DataFrame, bench: pd.DataFrame) -> pd.DataFrame:
    """Return indexes of two frames on their common dates (inner join), columns stock / bench."""
    return pd.concat({"stock": return_index(stock), "bench": return_index(bench)},
                     axis=1, join="inner").dropna()


def rs_block(stock: pd.DataFrame, bench: pd.DataFrame, symbol: str, notes: list) -> dict:
    """Relative strength of `stock` vs `bench`; RS line = ratio of their return indexes.

    `ratio` is the RS line rebased to 100 at the start of the last C.RS_EXTREME_LOOKBACK common
    bars. Messages about missing inputs are appended to `notes`.
    """
    j = joined_index(stock, bench)
    n = len(j)
    if n < 2:
        return {"available": False, "benchmark": symbol,
                "reason": f"fewer than 2 common dates with {symbol}"}
    ratio = j["stock"] / j["bench"]
    base = max(0, n - C.RS_EXTREME_LOOKBACK)
    out = {
        "available": True, "benchmark": symbol, "date": iso(j.index[-1]), "bars": n,
        "ratio": num(100.0 * ratio.iloc[-1] / ratio.iloc[base]), "ratio_base_date": iso(ratio.index[base]),
    }
    for name, k in C.RS_WINDOWS.items():
        s, b = window_return(j["stock"], k), window_return(j["bench"], k)
        out[f"return_{name}"] = s
        out[f"benchmark_return_{name}"] = b
        out[f"rel_return_{name}"] = None if s is None or b is None else s - b
        out[f"ratio_change_{name}"] = window_return(ratio, k)
        if s is None:
            notes.append(f"vs {symbol}: {name} relative return needs {k + 1} common bars (have {n})")
    out["rs_line_slope"] = window_return(ratio, C.SLOPE_BARS)
    if n >= C.RS_EXTREME_LOOKBACK:
        win = ratio.iloc[-C.RS_EXTREME_LOOKBACK:]
        out["rs_new_high"] = bool(ratio.iloc[-1] >= win.max())
        out["rs_new_low"] = bool(ratio.iloc[-1] <= win.min())
    else:
        out["rs_new_high"] = out["rs_new_low"] = None
        notes.append(f"vs {symbol}: RS new high/low needs {C.RS_EXTREME_LOOKBACK} common bars (have {n})")
    return out


def daily_direction(ctx) -> dict:
    """Daily direction from close vs SMA50/SMA200, SMA50 vs SMA200 and the SMA200 slope sign."""
    row = ctx["ind"].iloc[-1]
    close = num(ctx["bars"]["close"].iloc[-1])
    sma50, sma200, slope200 = num(row["sma50"]), num(row["sma200"]), num(row["sma200_slope"])
    votes = direction_votes(close, sma50, sma200, slope200)
    label, sgn = direction_from_votes(votes)
    return {
        "label": label, "sign": sgn, "close": close, "sma50": sma50, "sma200": sma200,
        "close_vs_sma50": pct(close, sma50), "close_vs_sma200": pct(close, sma200),
        "sma50_vs_sma200": pct(sma50, sma200), "sma200_slope": slope200, "votes": votes,
    }


# ---------------------------------------------------------------------------- A. market context
def _price_basis(ctx, symbol) -> str | None:
    """Price basis fetch used for a benchmark ("dividend-adjusted", "split-adjusted", ...)."""
    return ((ctx.get("data_quality") or {}).get("benchmark_basis") or {}).get(symbol)


def _spy_block(ctx, notes: list) -> dict:
    spy = frame(ctx, "spy")
    if spy is None:
        return {"available": False, "reason": skip_reason(ctx, ("spy", C.SPY), "SPY data unavailable")}
    close = spy["close"]
    sma200 = I.sma(close, SMA_LONG)
    c, s = last(close), last(sma200)
    if s is None:
        notes.append(f"SPY: SMA{SMA_LONG} needs {SMA_LONG} bars (have {len(spy)})")
    return {
        "available": True, "date": iso(spy.index[-1]), "close": c, "sma200": s,
        "above_sma200": None if s is None else bool(c > s), "pct_vs_sma200": pct(c, s),
        "sma200_slope_20d": last(sma200 / sma200.shift(C.SLOPE_BARS) - 1.0),
        "price_basis": _price_basis(ctx, C.SPY),
    }


def _vix_block(ctx, notes: list) -> dict:
    vix = frame(ctx, "vix")
    if vix is None:
        return {"available": False, "reason": skip_reason(ctx, ("vix", C.VIX), "^VIX data unavailable")}
    # ^VIX has a few rows on non-equity days: keep equity sessions only (stock or SPY dates).
    sessions = ctx["bars"].index
    spy = frame(ctx, "spy")
    if spy is not None:
        sessions = sessions.union(spy.index)
    close = vix["close"][vix.index.isin(sessions)].dropna()
    if close.empty:
        return {"available": False, "reason": "no ^VIX closes on equity sessions"}
    if len(close) < len(vix):
        notes.append(f"^VIX: dropped {len(vix) - len(close)} rows that are not equity sessions")
    pctl = last(I.rolling_percentile(close, C.PERCENTILE_WINDOW))
    if pctl is None:
        notes.append(f"^VIX: 1-year percentile needs {C.PERCENTILE_WINDOW} sessions (have {len(close)})")
    return {"available": True, "date": iso(close.index[-1]), "level": last(close), "pctl_1y": pctl}


def _sector_block(ctx, notes: list) -> dict:
    etf = ctx.get("sector_etf")
    if ctx.get("is_etf"):
        return {"available": False, "etf": etf, "reason": "ETF: sector module skipped"}
    sec = frame(ctx, "sector")
    if sec is None:
        default = f"sector ETF {etf} data unavailable" if etf else "no sector ETF mapped for this sector"
        return {"available": False, "etf": etf, "reason": skip_reason(ctx, ("sector", "sector_etf", etf), default)}
    close = sec["close"]
    sma50, sma200 = I.sma(close, SMA_MID), I.sma(close, SMA_LONG)
    slope50, slope200 = (last(s / s.shift(C.SLOPE_BARS) - 1.0) for s in (sma50, sma200))
    c, s50, s200 = last(close), last(sma50), last(sma200)
    label, sgn = direction_from_votes(direction_votes(c, s50, s200, slope200))
    if s200 is None or slope200 is None:
        notes.append(f"sector {etf}: SMA{SMA_LONG} or its slope unavailable ({len(sec)} bars); "
                     "trend label uses the remaining inputs")
    out = {
        "available": True, "etf": etf, "date": iso(sec.index[-1]), "close": c,
        "sma50": s50, "sma200": s200, "pct_vs_sma50": pct(c, s50), "pct_vs_sma200": pct(c, s200),
        "sma50_slope": slope50, "sma200_slope": slope200, "trend_label": label, "trend_sign": sgn,
        "price_basis": _price_basis(ctx, etf),
    }
    spy = frame(ctx, "spy")
    if spy is None:
        rs = {}
        notes.append(f"sector {etf}: RS vs SPY unavailable (no SPY data)")
    else:
        rs_notes: list[str] = []
        rs = rs_block(sec, spy, C.SPY, rs_notes)
        notes.extend(f"sector {etf} {m}" for m in rs_notes)
    out.update(
        rs_3m=rs.get("rel_return_3m"), rs_6m=rs.get("rel_return_6m"),
        rs_ratio_change_3m=rs.get("ratio_change_3m"), rs_ratio_change_6m=rs.get("ratio_change_6m"),
        rs_line_slope=rs.get("rs_line_slope"), rs_new_high=rs.get("rs_new_high"), rs_new_low=rs.get("rs_new_low"),
    )
    return out


def build_market_context(ctx) -> dict:
    """A: SPY vs its SMA200 and that SMA's slope, ^VIX level and 1-year percentile, sector ETF trend and RS vs SPY."""
    notes: list[str] = []
    blocks = {"spy": _spy_block(ctx, notes), "vix": _vix_block(ctx, notes), "sector": _sector_block(ctx, notes)}
    missing = {k: b["reason"] for k, b in blocks.items() if not b["available"]}
    out = {"available": len(missing) < len(blocks), **blocks, "notes": notes}
    if missing:
        out["reason"] = "; ".join(f"{k}: {v}" for k, v in missing.items())
    return out


# ---------------------------------------------------------------------------- B. regime
def _strength(adx: float | None) -> str | None:
    if adx is None:
        return None
    if adx >= C.ADX_TREND:
        return "trending"
    return "range" if adx < C.ADX_RANGE else "transitional"


def _vol_label(pctl: float | None) -> str | None:
    if pctl is None:
        return None
    if pctl > C.VOL_EXTREME_PCTL:
        return "extreme"
    if pctl > C.VOL_HIGH_PCTL:
        return "high"
    return "low" if pctl < C.VOL_LOW_PCTL else "normal"


def _head(direction: dict, strength: str | None, plus_di, minus_di) -> str:
    """Leading part of the regime label: direction, or Range / Transitional by trend strength."""
    if strength == "range":
        return "Range"
    if strength == "transitional":
        bias = {1: "uptrend bias", -1: "downtrend bias"}.get(direction["sign"], "mixed")
        return f"Transitional ({bias})"
    if strength == "trending" and direction["label"] == "Mixed":
        return f"Mixed (trending {'up' if (plus_di or 0) >= (minus_di or 0) else 'down'})"
    return direction["label"] or "Undetermined"


def build_regime(ctx) -> dict:
    """B: direction (SMA votes), trend strength (ADX) and volatility (percentiles) -> one label and type."""
    ind = ctx["ind"]
    row = ind.iloc[-1]
    n = len(ind)
    notes: list[str] = []

    direction = daily_direction(ctx)
    gone = [k for k, v in direction["votes"].items() if v is None]
    if gone:
        notes.append(f"direction inputs unavailable with {n} bars: {', '.join(gone)}; "
                     "direction uses the remaining inputs")

    adx, plus_di, minus_di = num(row["adx14"]), num(row["plus_di"]), num(row["minus_di"])
    strength = _strength(adx)
    if strength is None:
        notes.append(f"ADX({C.ADX_PERIOD}) unavailable with {n} bars: trend strength unknown, type set to transitional")
    if strength == "trending" and direction["sign"] != 0:
        direction["label"] = "Strong " + direction["label"].lower()

    rv_p, atr_p, bw_p = num(row["rv20_pctl"]), num(row["atr_pct_pctl"]), num(row["bb_width_pctl"])
    have = [p for p in (rv_p, atr_p) if p is not None]
    vol_pctl = max(have) if have else None
    vol = _vol_label(vol_pctl)
    compression = None if bw_p is None else bool(bw_p < C.COMPRESSION_BW_PCTL)
    if vol is None:
        notes.append(f"volatility percentiles need {C.PERCENTILE_WINDOW} bars of 20-day vol / ATR% "
                     f"(have {n} bars): volatility label omitted")

    label = _head(direction, strength, plus_di, minus_di)
    if vol is not None:
        label += f" / {vol} vol"
    if compression:
        label += " (compression)"

    if vol == "extreme":
        rtype = "vol_extreme"
    else:
        rtype = {"trending": "trend", "range": "range"}.get(strength, "transitional")

    return {
        "available": True,
        "date": iso(ind.index[-1]),
        "label": label,
        "type": rtype,
        "direction_sign": direction["sign"],
        "direction": direction,
        "strength": {"adx": adx, "plus_di": plus_di, "minus_di": minus_di, "label": strength},
        "volatility": {
            "rv20": num(row["rv20"]), "rv20_pctl": rv_p, "atr_pct": num(row["atr_pct"]), "atr_pct_pctl": atr_p,
            "vol_pctl": vol_pctl, "bb_width_pctl": bw_p, "label": vol, "compression": compression,
        },
        "notes": notes,
    }


# ---------------------------------------------------------------------------- I. multi-timeframe
_DIR_WORD = {1: "uptrend", -1: "downtrend", 0: "mixed"}


def alignment(weekly_sign: int | None, daily_sign: int) -> str | None:
    """confirms (same non-zero sign), contradicts (opposite non-zero signs), else mixed."""
    if weekly_sign is None:
        return None
    if weekly_sign != 0 and weekly_sign == daily_sign:
        return "confirms"
    if weekly_sign != 0 and daily_sign == -weekly_sign:
        return "contradicts"
    return "mixed"


def build_multi_timeframe(ctx) -> dict:
    """I: weekly (W-FRI) close vs 10w/40w SMA and weekly RSI, and whether it confirms the daily direction."""
    notes: list[str] = []
    fast, slow = C.WEEKLY_SMA_FAST, C.WEEKLY_SMA_SLOW
    wk = I.resample_weekly(ctx["bars"])
    close = wk["close"]
    c, f, s = last(close), last(I.sma(close, fast)), last(I.sma(close, slow))
    rsi_w = last(I.rsi(close, C.WEEKLY_RSI_PERIOD))
    partial = bool(wk["partial"].iloc[-1])
    if partial:
        notes.append(f"latest weekly bar is partial (week in progress through {iso(wk.index[-1])})")

    if f is None or s is None:
        w_sign = w_label = None
        notes.append(f"weekly direction needs {slow} weekly bars (have {len(wk)})")
    elif c > f > s:
        w_sign, w_label = 1, "Uptrend"
    elif c < f < s:
        w_sign, w_label = -1, "Downtrend"
    else:
        w_sign, w_label = 0, "Mixed"

    daily_sign = daily_direction(ctx)["sign"]
    align = alignment(w_sign, daily_sign)
    if align is None:
        note = "Weekly direction unavailable: no multi-timeframe check."
    else:
        stack = {1: f"close > {fast}w > {slow}w SMA", -1: f"close < {fast}w < {slow}w SMA",
                 0: f"close not stacked with the {fast}w/{slow}w SMAs"}[w_sign]
        meaning = {"confirms": "the weekly picture confirms the daily one",
                   "contradicts": "the weekly picture contradicts the daily one",
                   "mixed": "no clear higher-timeframe confirmation"}[align]
        note = f"Weekly {_DIR_WORD[w_sign]} ({stack}) vs. daily {_DIR_WORD[daily_sign]}: {meaning}."

    return {
        "available": True,
        "weekly": {
            # key names carry the default periods, like the daily ind columns
            "date": iso(wk.index[-1]), "bars": len(wk), "close": c,
            "sma10w": f, "sma40w": s, "rsi14w": rsi_w,
            "direction": w_label, "sign": w_sign, "partial_week": partial,
        },
        "daily_sign": daily_sign,
        "alignment": align,
        "note": note,
        "notes": notes,
    }
