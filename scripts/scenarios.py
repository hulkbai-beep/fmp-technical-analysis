"""
Conflicts, scenarios and risk framing (step 5).

build_conflicts lists the places where the module readings disagree (price vs flow, daily vs weekly,
signals vs regime, earnings inside the horizon, ...); there are no scores, weights or bias.
build_scenarios derives conditional bull / bear / base scenarios from the key levels, ATR and
realized volatility; build_risk adds illustrative stop, reward/risk and position-size arithmetic.
Every input module may be missing or unavailable: the affected inputs are skipped and the reasons
say so. Nothing here is a recommendation. Rules are documented in references/methodology.md.
"""
from __future__ import annotations

import math

import config as C

ILLUSTRATIVE = "Illustrative arithmetic, not a recommendation."


# ---------------------------------------------------------------------------- small helpers
def _num(x) -> float | None:
    """Plain float, or None for missing / non-finite / non-numeric values."""
    if x is None or isinstance(x, (str, bytes)):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _get(d, *keys):
    """Nested dict lookup; None as soon as a level is missing or not a dict."""
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _first(*values):
    """First value that is a finite number."""
    for v in values:
        if _num(v) is not None:
            return _num(v)
    return None


def _flag(x) -> bool | None:
    return None if x is None else bool(x)


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def _px(x: float) -> str:
    return f"{x:.2f}"


def _usable(modules: dict | None, name: str) -> dict | None:
    """The module dict when present and not marked unavailable."""
    m = (modules or {}).get(name)
    return m if isinstance(m, dict) and m.get("available", True) is not False else None


def _block(d, key: str) -> dict | None:
    b = _get(d, key)
    return b if isinstance(b, dict) and b.get("available", True) is not False else None


def _why_missing(modules: dict | None, name: str) -> str:
    m = (modules or {}).get(name)
    if not isinstance(m, dict):
        return f"{name} module missing"
    return f"{name} unavailable: {m.get('reason') or 'no reason given'}"


def _horizon(ctx) -> tuple[str, dict]:
    name = (ctx or {}).get("horizon") or C.DEFAULT_HORIZON
    return name, (ctx or {}).get("hz") or C.HORIZONS.get(name, C.HORIZONS[C.DEFAULT_HORIZON])


def _last_col(ctx, frame: str, col: str) -> float | None:
    df = (ctx or {}).get(frame)
    if df is None or col not in getattr(df, "columns", ()) or len(df) == 0:
        return None
    return _num(df[col].iloc[-1])


# ---------------------------------------------------------------------------- signals
def _horizon_stats(sig: dict, h: int) -> dict:
    hs = sig.get("horizons") or {}
    return hs.get(h) or hs.get(str(h)) or {}


def _signal_view(sig: dict, primary: int) -> dict:
    """Bias, sample confidence and whether the same-regime edge at the primary horizon confirms the bias."""
    hs = _horizon_stats(sig, primary)
    bias = _sign(_num(sig.get("bias")) or 0)
    conf = sig.get("confidence") or hs.get("confidence") or "insufficient"
    em, eh = _num(_get(hs, "edge", "median")), _num(_get(hs, "edge", "hit_rate"))
    confirmed = (conf in ("normal", "low") and bias != 0 and em is not None and eh is not None
                 and bias * em > C.SIGNAL_EDGE_MIN and bias * eh > C.SIGNAL_HIT_EDGE_MIN)
    return {"id": sig.get("id"), "name": sig.get("name") or sig.get("id"), "bias": bias,
            "confidence": conf, "n": _num(hs.get("n")), "edge_median": em, "edge_hit_rate": eh,
            "confirmed": bool(confirmed)}


def _active_signals(modules, primary: int) -> list[dict]:
    sigs = _usable(modules, "signals")
    return [_signal_view(s, primary) for s in (_get(sigs, "active") or []) if isinstance(s, dict)]


# ---------------------------------------------------------------------------- conflicts
def _conflict(kind: str, involved: list[str], description: str, meaning: str, key: str | None = None,
              params: dict | None = None, **extra) -> dict:
    """One conflict; `key` names its typical meaning and `params` holds the numbers in the description,
    so the UI can render both in another language."""
    return {"type": kind, "involved": involved, **extra, "description": description, "typical_meaning": meaning,
            "key": key or kind, "params": params or {}}


def _conflicts(ctx, modules, views: list[dict]) -> list[dict]:
    out = []
    vf = _usable(modules, "volume_flow")
    for kind in ("bearish", "bullish"):
        lines = [n for n, k in (("OBV", "obv"), ("A/D", "ad")) if _get(vf, k, "divergence") == kind]
        if not lines:
            continue
        detail = _get(vf, "obv" if "OBV" in lines else "ad", "detail") or ""
        if kind == "bearish":
            desc = f"price at a new {C.DIVERGENCE_LOOKBACK}-bar closing high while {'/'.join(lines)} did not confirm"
            meaning = ("Participation is not confirming the price high; such divergences often precede a stall or "
                       "pullback, though they can persist for weeks in strong trends.")
        else:
            desc = f"price at a new {C.DIVERGENCE_LOOKBACK}-bar closing low while {'/'.join(lines)} held above its prior low"
            meaning = ("Selling pressure is not confirming the new low: often a sign of waning downside momentum, "
                       "but not a reversal signal by itself.")
        out.append(_conflict("price_flow_divergence", ["price"] + lines,
                             f"{desc}" + (f" ({detail})" if detail else ""), meaning, key=f"divergence_{kind}",
                             params={"kind": kind, "lines": lines, "lookback": C.DIVERGENCE_LOOKBACK}))

    mtf = _usable(modules, "multi_timeframe")
    if _get(mtf, "alignment") == "contradicts":
        out.append(_conflict("daily_vs_weekly", ["daily", "weekly"], _get(mtf, "note") or "daily and weekly "
                             "directions are opposite", "A counter-trend move within the larger trend: the daily "
                             "move can run for a swing, but the weekly trend usually dominates over longer horizons.",
                             params={"daily_sign": _num(_get(mtf, "daily_sign")),
                                     "weekly_sign": _num(_get(mtf, "weekly", "sign"))}))

    vs_spy = _block(_usable(modules, "relative_strength"), "vs_spy")
    rel3, rel6 = _num(_get(vs_spy, "rel_return_3m")), _num(_get(vs_spy, "rel_return_6m"))
    spy = _block(_usable(modules, "market_context"), "spy")
    if rel3 is not None and rel6 is not None and rel3 > 0 and rel6 > 0 and _flag(_get(spy, "above_sma200")) is False:
        rel = _num(_get(spy, "pct_vs_sma200"))
        out.append(_conflict("rs_vs_market", ["relative_strength", "market_context"],
                             f"outperforming SPY over 3m ({_pct(rel3)}) and 6m ({_pct(rel6)}) while SPY is below "
                             "its 200-day SMA" + (f" ({_pct(rel)})" if rel is not None else ""),
                             "Leadership against a risk-off tape: relative strength in a weak market can mark "
                             "future leaders, but in broad selloffs strong names are often sold late.",
                             params={"rel_return_3m": rel3, "rel_return_6m": rel6,
                                     "spy_pct_vs_sma200": rel}))

    sq_on = _get(_usable(modules, "volatility"), "squeeze", "on")
    direction = _num(_get(_usable(modules, "regime"), "direction_sign"))
    if sq_on and direction == 0:
        bars = _num(_get(modules, "volatility", "squeeze", "bars"))
        out.append(_conflict("squeeze_no_direction", ["volatility_setup", "regime"],
                             f"squeeze on{f' for {int(bars)} bars' if bars else ''} with no prevailing direction",
                             "Break direction unknown: a squeeze stores energy but says nothing about direction; "
                             "the first decisive close outside the range on volume usually settles it.",
                             params={"bars": bars}))

    ev = _usable(modules, "event_risk")
    if _get(ev, "within_horizon"):
        nxt = _get(ev, "next_earnings") or {}
        med, n = _num(_get(ev, "reactions", "median_abs_move")), _num(_get(ev, "reactions", "n"))
        move = f"; median absolute two-day reaction {med * 100:.1f}% over {int(n or 0)} reports" if med is not None else ""
        td = _num(nxt.get("trading_days_est"))
        when = f" (~{int(td)} trading days)" if td is not None else ""
        out.append(_conflict("earnings_in_horizon", ["event_risk"],
                             f"earnings on {nxt.get('date') or 'an upcoming date'}{when} fall inside the "
                             f"{_horizon(ctx)[1].get('primary')}-day horizon{move}",
                             "The event can override the technicals: an earnings reaction adds a jump to the "
                             "ordinary range, can gap through levels and stops, and can reset the trend.",
                             params={"date": nxt.get("date"), "trading_days": td, "horizon": _horizon(ctx)[1].get("primary"),
                                     "median_abs_move": med, "n": n}))

    regime_sign = _sign(direction) if direction is not None else 0
    for bias, word, regime_word, meaning in (
            (1, "bullish", "a downtrend",
             "Counter-trend setups: bullish signals in a downtrend have mostly worked as short-lived bounces; "
             "judge them on the same-regime statistics, not the unconditional ones."),
            (-1, "bearish", "an uptrend",
             "Counter-trend setups: bearish signals in an uptrend have mostly marked pauses rather than "
             "reversals; judge them on the same-regime statistics, not the unconditional ones.")):
        ids = [v["id"] for v in views if v["bias"] == bias]
        if ids and regime_sign == -bias:
            out.append(_conflict("signals_vs_regime", ids + ["regime"],
                                 f"{word} signal(s) {', '.join(ids)} active in {regime_word} regime",
                                 meaning, key=f"signals_vs_regime_{word}", params={"bias": bias, "ids": ids},
                                 signals=ids))
    bull_ids = [v["id"] for v in views if v["bias"] > 0]
    bear_ids = [v["id"] for v in views if v["bias"] < 0]
    if bull_ids and bear_ids:
        out.append(_conflict("signals_both_ways", bull_ids + bear_ids,
                             f"bullish {', '.join(bull_ids)} and bearish {', '.join(bear_ids)} are active together",
                             "Opposing setups at the same time usually mean a turning point or noise; weigh them "
                             "by sample size and same-regime edge.", params={"bull": bull_ids, "bear": bear_ids},
                             signals=bull_ids + bear_ids))
    return out


# ---------------------------------------------------------------------------- conflicts module
def build_conflicts(ctx, modules) -> dict:
    """Disagreements between the module readings, each with its typical meaning. Listed only: no scores."""
    modules = modules or {}
    horizon, hz = _horizon(ctx)
    primary = int(_num(_get(modules, "signals", "primary_horizon")) or hz["primary"])
    return {"available": True, "horizon": horizon, "primary_horizon": primary,
            "items": _conflicts(ctx, modules, _active_signals(modules, primary))}


# ---------------------------------------------------------------------------- volatility bands
def sigma_h(rv20: float, h: int) -> float:
    """The h-day move as a fraction of the close: annualized rv20 x sqrt(h / 252)."""
    return rv20 * math.sqrt(h / C.TRADING_DAYS_PER_YEAR)


def _calibrated(table: dict, h: int) -> int:
    """The calibrated horizon nearest h (the shorter one on a tie)."""
    return min(table, key=lambda k: (abs(k - h), k))


def touch_rate(k: float | None, h: int) -> float | None:
    """Share of calibration paths that reached a price k x sigma_h from the close within h days
    (config.VOL_TOUCH), interpolated in k on a log scale; None outside the calibrated range of k."""
    if k is None or not math.isfinite(k):
        return None
    row = C.VOL_TOUCH[_calibrated(C.VOL_TOUCH, h)]
    ks = sorted(row)
    if not ks[0] <= k <= ks[-1]:
        return None
    for a, b in zip(ks, ks[1:]):
        if k <= b:
            w = (k - a) / (b - a)
            return math.exp((1 - w) * math.log(row[a]) + w * math.log(row[b]))
    return row[ks[-1]]


def _pct1(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


# ---------------------------------------------------------------------------- scenarios
def _market(ctx, modules) -> dict:
    """Last close, ATR(14), 20-day realized vol and 52-week / all-time extremes, from ctx or the modules."""
    lv, vol = _usable(modules, "levels"), _usable(modules, "volatility")
    tm, rg = _usable(modules, "trend_momentum"), _usable(modules, "regime")
    bars = (ctx or {}).get("bars")
    has_bars = bars is not None and len(bars) > 0
    close = _first(_last_col(ctx, "bars", "close"), _get(lv, "last_close"), _get(tm, "close"),
                   _get(rg, "direction", "close"))
    atr = _first(_get(vol, "atr14"), _get(lv, "atr"), _last_col(ctx, "ind", "atr14"))
    rv20 = _first(_get(vol, "rv20"), _get(rg, "volatility", "rv20"), _last_col(ctx, "ind", "rv20"))
    recent = bars.iloc[-C.HIGH_52W_BARS:] if has_bars else None
    hi52 = _first(_get(lv, "high_52w"), _get(tm, "from_52w_high", "high"),
                  recent["high"].max() if has_bars else None)
    lo52 = _first(_get(lv, "low_52w"), recent["low"].min() if has_bars else None)
    if has_bars:
        ath = hi52 is not None and hi52 >= float(bars["high"].max())
        atl = lo52 is not None and lo52 <= float(bars["low"].min())
    else:
        ath = bool(_get(lv, "extremes", "at_all_time_high"))
        atl = bool(_get(lv, "extremes", "at_all_time_low"))
    return {"close": close, "atr": atr, "rv20": rv20, "high_52w": hi52, "low_52w": lo52,
            "high_52w_is_all_time": ath, "low_52w_is_all_time": atl,
            "supports": [x for x in (_get(lv, "supports") or []) if _num(_get(x, "price")) is not None],
            "resistances": [x for x in (_get(lv, "resistances") or []) if _num(_get(x, "price")) is not None]}


def _sources(level: dict, default: str, keep: int = 3) -> str:
    src = list(level.get("sources") or [])
    if not src:
        return default
    return ", ".join(src[:keep]) + (f" +{len(src) - keep} more" if len(src) > keep else "")


def _zone(level: dict, close: float, atr: float, label: str) -> dict:
    """Target zone = level price +- C.TARGET_ZONE_ATR x ATR; the merged level's own range is kept alongside."""
    p = float(level["price"])
    pad = C.TARGET_ZONE_ATR * atr
    return {"kind": "level", "label": label, "price": p, "zone_low": p - pad, "zone_high": p + pad,
            "level_zone_low": _first(level.get("zone_low"), p), "level_zone_high": _first(level.get("zone_high"), p),
            "sources": list(level.get("sources") or []),
            "distance_pct": p / close - 1, "distance_atr": (p - close) / atr}


def _projection(price: float, mult: float, d: int, close: float, atr: float) -> dict:
    pad = C.TARGET_ZONE_ATR * atr
    word = "overhead" if d > 0 else "lower"
    return {"kind": "projection", "label": f"projection, no {word} level ({mult:g}x ATR from the trigger)",
            "price": price, "zone_low": price - pad, "zone_high": price + pad, "sources": [],
            "distance_pct": price / close - 1, "distance_atr": (price - close) / atr}


def _directional(d: int, mk: dict, ticker: str) -> dict:
    """Bull (d=+1) or bear (d=-1) scenario: trigger, target zones beyond it, invalidation, text."""
    close, atr = mk["close"], mk["atr"]
    side_levels = mk["resistances"] if d > 0 else mk["supports"]
    kind, cond, back = ("resistance", "close above", "below") if d > 0 else ("support", "close below", "above")
    ext, ext_name = (mk["high_52w"], "high") if d > 0 else (mk["low_52w"], "low")
    ext_all_time = mk["high_52w_is_all_time"] if d > 0 else mk["low_52w_is_all_time"]

    if side_levels:
        near = side_levels[0]
        level = float(_first(near.get("zone_high") if d > 0 else near.get("zone_low"), near["price"]))
        near_edge = float(_first(near.get("zone_low") if d > 0 else near.get("zone_high"), near["price"]))
        basis = f"{'top' if d > 0 else 'bottom'} of the nearest {kind} zone: {_sources(near, kind)}"
    elif ext is not None and d * (ext - close) > 0:
        level = near_edge = float(ext)
        basis = (f"all-time {ext_name} (available history)" if ext_all_time else f"52-week {ext_name}") + \
                f": no {kind} zone {'above' if d > 0 else 'below'} the last close"
    else:
        level = near_edge = close + d * C.NO_LEVEL_TRIGGER_ATR * atr
        if ext is None:
            why = f"no {kind} level and no 52-week {ext_name} available"
        else:
            why = f"price at its {'all-time' if ext_all_time else '52-week'} {ext_name}, no {kind} level"
        basis = f"last close {'+' if d > 0 else '-'} {C.NO_LEVEL_TRIGGER_ATR:g} ATR: {why}"

    inv = min(near_edge, level - C.INVALIDATION_MIN_ATR * atr) if d > 0 else max(near_edge, level + C.INVALIDATION_MIN_ATR * atr)
    targets = [_zone(lv, close, atr, kind) for lv in side_levels if d * (float(lv["price"]) - level) > 0]
    if not targets:
        targets = [_projection(level + d * m * atr, m, d, close, atr) for m in C.SCENARIO_PROJECTION_ATR
                   if level + d * m * atr > 0]

    volume = f"RVOL >= {C.TRIGGER_RVOL:g}"
    zones = ", ".join(f"{_px(z['zone_low'])}-{_px(z['zone_high'])}" + (" (projection)" if z["kind"] == "projection" else "")
                      for z in targets) or "none identified"
    text = (f"If {ticker} closes {'above' if d > 0 else 'below'} {_px(level)} ({basis}) on {volume}, the next "
            f"zones are {zones}; the scenario is invalidated by a close back {back} {_px(inv)}.")
    return {
        "trigger": {"level": level, "condition": cond, "volume": volume, "basis": basis,
                    "distance_pct": level / close - 1, "distance_atr": (level - close) / atr},
        "targets": targets,
        "invalidation": {"level": inv, "condition": f"close {back}",
                         "basis": f"the trigger zone's far edge or {C.INVALIDATION_MIN_ATR:g} ATR beyond the trigger, "
                                  f"whichever is further"},
        "text": text,
    }


def _earnings_caveat(ev, h: int, sd: float | None) -> str | None:
    """The base case's caveat when earnings fall inside the horizon, worded from the numbers: the median
    absolute two-day move and how many past reactions moved more than the h-day 1-sigma move sd."""
    if not _get(ev, "within_horizon"):
        return None
    text = (f"Earnings on {_get(ev, 'next_earnings', 'date')} fall inside the {h}-day horizon and add a jump to "
            f"the ordinary range the band describes")
    med = _num(_get(ev, "reactions", "median_abs_move"))
    if med is None:
        return text + "."
    text += (f": over the last {int(_num(_get(ev, 'reactions', 'n')) or 0)} reports the median absolute two-day "
             f"move was {med * 100:.1f}%")
    moves = [abs(m) for m in (_num(_get(x, "move")) for x in (_get(ev, "reactions", "items") or [])) if m is not None]
    if sd is not None and moves:
        text += (f", and {sum(m > sd for m in moves)} of {len(moves)} moved more than the {h}-day 1-sigma move "
                 f"of {sd * 100:.1f}% (the largest {max(moves) * 100:.1f}%)")
    return text + "."


def build_scenarios(ctx, modules) -> dict:
    """Conditional bull / bear scenarios from the key levels and a base case from realized vol."""
    modules = modules or {}
    _, hz = _horizon(ctx)
    h = int(hz["primary"])
    mk = _market(ctx, modules)
    close, atr, rv = mk["close"], mk["atr"], mk["rv20"]
    if close is None or atr is None or atr <= 0:
        return {"available": False, "horizon_days": h, "notes": [],
                "reason": "last close or ATR(14) unavailable: scenarios need both"}
    ticker = str((ctx or {}).get("ticker") or "the stock")
    notes = []
    if _usable(modules, "levels") is None:
        notes.append(f"{_why_missing(modules, 'levels')}: triggers fall back to 52-week extremes or ATR offsets")
    else:
        notes.append(f"triggers and level targets come from the key levels, which are {C.LEVELS_EVIDENCE_NOTE}")

    caveat = _earnings_caveat(_usable(modules, "event_risk"), h, None if rv is None else sigma_h(rv, h))

    if rv is None:
        base = {"available": False, "reason": "20-day realized volatility unavailable", "earnings_caveat": caveat}
    else:
        sd = sigma_h(rv, h)
        move, k95 = close * sd, C.VOL_BAND_K95
        hc = _calibrated(C.VOL_BAND_COVER, h)
        cover = C.VOL_BAND_COVER[hc]
        low95 = close - k95 * move
        if low95 <= 0:
            notes.append(f"the {k95:g}-sigma band reaches zero: no lower 95% bound")
        base = {"available": True, "rv20": rv, "expected_move_1sd_pct": sd, "expected_move_1sd_price": move,
                "expected_move_atr": move / atr, "range_low": close - move, "range_high": close + move,
                "band_95_k": k95, "expected_move_95_pct": k95 * sd, "expected_move_95_price": k95 * move,
                "range_95_low": low95 if low95 > 0 else None, "range_95_high": close + k95 * move,
                "coverage": {"horizon_days": hc, "within_1sd": cover.get(1.0), "within_2sd": cover.get(2.0),
                             "within_95_band": cover.get(k95), "basis": C.VOL_CALIBRATION},
                "earnings_caveat": caveat,
                "method": "last close x (1 +- k x rv20 (annualized) x sqrt(h / 252)); zero drift, assumes recent "
                          "volatility persists. Returns have fat tails, so 2 sigma is not a 95% range: in the "
                          f"calibration sample ({C.VOL_CALIBRATION}) the 1-sigma band held "
                          f"{_pct1(cover.get(1.0))} of {hc}-day closes, 2 sigma {_pct1(cover.get(2.0))} and "
                          f"{k95:g} sigma {_pct1(cover.get(k95))}"}
        band95 = (f"{_px(low95)} to {_px(close + k95 * move)}" if low95 > 0
                  else f"zero to {_px(close + k95 * move)}")
        base["text"] = (f"Over {h} trading days, 20-day realized volatility of {rv * 100:.1f}% annualized implies "
                        f"a 1-sigma move of about +-{sd * 100:.1f}% (+-{_px(move)}, {move / atr:.1f} ATR): roughly "
                        f"{_px(close - move)} to {_px(close + move)}, a band that held {_pct1(cover.get(1.0))} of "
                        f"{hc}-day closes in calibration. The calibrated 95% band, +-{k95:g} sigma "
                        f"(+-{k95 * sd * 100:.1f}%), is roughly {band95}." + (f" {caveat}" if caveat else ""))

    return {
        "available": True,
        "ticker": ticker,
        "horizon_days": h,
        "last_close": close,
        "atr": atr,
        "bull": _directional(1, mk, ticker),
        "bear": _directional(-1, mk, ticker),
        "base": base,
        "notes": notes,
    }


# ---------------------------------------------------------------------------- risk
def _size(entry: float, stop: float | None, d: int) -> dict | None:
    """Whole shares that risk C.RISK_PER_TRADE of C.ACCOUNT_SIZE between entry and stop."""
    if stop is None or d * (entry - stop) <= 0:
        return None
    risk_dollars = C.ACCOUNT_SIZE * C.RISK_PER_TRADE
    per_share = d * (entry - stop)
    exact = risk_dollars / per_share
    shares = math.floor(exact + 1e-9)   # guard float noise at exact multiples
    notional = shares * entry
    pct = notional / C.ACCOUNT_SIZE
    if shares == 0:
        flag = (f"risk per share {_px(per_share)} exceeds the ${risk_dollars:,.0f} risk budget: no whole-share "
                f"position fits ({exact:.2f} shares)")
    else:
        flag = "illustrative only; would exceed the account" if pct > 1.0 else None
    return {"stop": stop, "risk_per_share": per_share, "shares": shares, "fractional_shares": exact,
            "notional": notional, "notional_pct_account": pct, "exceeds_account": pct > 1.0, "flag": flag}


def _risk_side(d: int, mk: dict, scen_side: dict | None, h: int) -> dict:
    """Long (d=+1) or short (d=-1) arithmetic from the last close. The primary stop and the first target are
    the calibrated volatility-band edges; the structural stop and the level target are kept for reference."""
    entry, atr, rv = mk["close"], mk["atr"], mk["rv20"]
    stop_side, target_side = (mk["supports"], mk["resistances"]) if d > 0 else (mk["resistances"], mk["supports"])
    notes = []

    sd = None if rv is None or rv <= 0 else sigma_h(rv, h)
    vol_stop = vol_target = None
    if sd is None:
        notes.append("20-day realized volatility unavailable: no volatility stop or target")
    else:
        vol_stop = entry * (1 - d * C.VOL_STOP_K * sd)
        vol_target = entry * (1 + d * C.VOL_TARGET_K * sd)
        if vol_stop <= 0:
            vol_stop = None
            notes.append("volatility stop would be at or below zero")
        if vol_target <= 0:
            vol_target = None
            notes.append("volatility target would be at or below zero")

    ref = stop_side[0] if stop_side else None
    structural = None
    if ref is not None:
        edge = _first(ref.get("zone_low") if d > 0 else ref.get("zone_high"), ref["price"])
        structural = edge - d * C.STRUCTURE_STOP_BUFFER_ATR * atr
    else:
        notes.append(f"no {'support' if d > 0 else 'resistance'} level {'below' if d > 0 else 'above'} the close: "
                     "no structural stop")
    atr_stop = entry - d * C.STOP_ATR_MULT * atr
    if atr_stop <= 0:
        atr_stop = None
        notes.append("ATR stop would be at or below zero")

    if target_side:
        level_target, lt_basis = float(target_side[0]["price"]), f"nearest {'resistance' if d > 0 else 'support'}"
    elif scen_side and scen_side.get("targets"):
        level_target, lt_basis = float(scen_side["targets"][0]["price"]), scen_side["targets"][0]["label"]
    else:
        level_target, lt_basis = None, "none"
    vt_basis = f"entry {'+' if d > 0 else '-'} {C.VOL_TARGET_K:g} x rv20 x sqrt({h} / 252): the 1-sigma band edge"
    if vol_target is not None:
        target, t_basis = vol_target, vt_basis
    else:
        target, t_basis = level_target, lt_basis

    stops = {"vol": vol_stop, "structural": structural, "atr": atr_stop}
    dist_atr = {k: (None if s is None else d * (entry - s) / atr) for k, s in stops.items()}
    dist_pct = {k: (None if s is None else d * (entry - s) / entry) for k, s in stops.items()}
    dist_sigma = {k: (None if p is None or sd is None else p / sd) for k, p in dist_pct.items()}
    touch = {k: (None if x is None or x <= 0 else touch_rate(x, h)) for k, x in dist_sigma.items()}
    t_sigma = None if target is None or sd is None else d * (target / entry - 1) / sd
    rr = {k: (None if s is None or target is None or d * (entry - s) <= 0 else d * (target - entry) / (d * (entry - s)))
          for k, s in stops.items()}
    sizes = {k: _size(entry, s, d) for k, s in stops.items()}
    primary = "vol" if sizes["vol"] else "atr"
    example = {"account": C.ACCOUNT_SIZE, "risk_pct": C.RISK_PER_TRADE,
               "risk_dollars": C.ACCOUNT_SIZE * C.RISK_PER_TRADE, "stop_basis": primary,
               **(sizes[primary] or {"stop": None, "shares": None, "notional": None}), "by_stop": sizes}
    if example["shares"] == 0:
        notes.append(f"position-size example: {example['flag']}")
    odds = None
    if vol_stop is not None and vol_target is not None:
        hp = _calibrated(C.VOL_PAIR_ODDS, h)
        odds = dict(zip(("target_first", "stop_first", "neither"), C.VOL_PAIR_ODDS[hp]), horizon_days=hp)

    return {
        "entry_ref": entry,
        "sigma_h_pct": sd,
        "vol_stop": vol_stop,
        "vol_stop_basis": (f"entry {'-' if d > 0 else '+'} {C.VOL_STOP_K:g} x rv20 x sqrt({h} / 252): the calibrated "
                           "95% band edge"),
        "vol_target": vol_target,
        "vol_target_basis": vt_basis,
        "structural_stop": structural,
        "structural_ref": None if ref is None else {
            "price": float(ref["price"]), "zone_low": _num(ref.get("zone_low")), "zone_high": _num(ref.get("zone_high")),
            "sources": list(ref.get("sources") or []), "distance_atr": (float(ref["price"]) - entry) / atr,
            "distance_pct": float(ref["price"]) / entry - 1},
        "structural_stop_basis": (f"nearest {'support zone low -' if d > 0 else 'resistance zone high +'} "
                                  f"{C.STRUCTURE_STOP_BUFFER_ATR:g} ATR (reference only)"),
        "atr_stop": atr_stop,
        "atr_stop_basis": f"entry {'-' if d > 0 else '+'} {C.STOP_ATR_MULT:g} ATR",
        "stop_distance_atr": dist_atr,
        "stop_distance_pct": dist_pct,
        "stop_distance_sigma": dist_sigma,
        "stop_touch_rate": touch,
        "first_target": target,
        "first_target_basis": t_basis,
        "first_target_sigma": t_sigma,
        "first_target_touch_rate": None if t_sigma is None or t_sigma <= 0 else touch_rate(t_sigma, h),
        "level_target": level_target,
        "level_target_basis": lt_basis + (" (reference only)" if level_target is not None else ""),
        "vol_pair_odds": odds,
        "reward_risk": rr,
        "position_size_example": example,
        "notes": notes,
    }


def _zero_drift(drops: str) -> str:
    return f"The volatility bands and touch rates assume zero drift, so they leave {drops} out."


def _ex_dividend_note(nx: dict, proj: dict | None) -> str:
    """The risk note for an ex-dividend date inside the horizon (dividends.next_ex_dividend), with the
    projected number and total of ex-dates inside it when there are several (dividends.projected_in_horizon)."""
    amount, share, atrs = (_num(nx.get(k)) for k in ("amount", "amount_pct", "amount_atr"))
    size = ", ".join(s for s in (None if share is None else f"{share * 100:.2f}% of the close",
                                 None if atrs is None else f"{atrs:.2f} ATR") if s)
    n, total = int(_num(_get(proj, "ex_dates")) or 0), _num(_get(proj, "amount_pct"))
    more = (f" At the spacing of the last 12 months about {n} ex-dates would fall inside the horizon"
            + (f", about {total * 100:.2g}% of the close in all" if total else "") + "." if n >= 2 else "")
    return (f"Ex-dividend date {nx.get('date')} falls inside the horizon: the price is expected to open lower by "
            f"about the dividend, ${amount:.4g}" + (f" ({size})" if size else "") + ", for mechanical reasons, so "
            "a long-side stop closer to the price than that can be touched by the ex-dividend drop alone."
            + more + " " + _zero_drift("these drops" if n >= 2 else "this drop"))


def _projected_ex_dividend_note(proj: dict) -> str:
    """The risk note when no ex-dividend date is announced but the spacing of the last 12 months puts
    some inside the horizon (dividends.projected_in_horizon)."""
    n, total = int(_num(proj.get("ex_dates")) or 0), _num(proj.get("amount_pct"))
    one = n == 1
    size = f", about {total * 100:.2g}% of the close{'' if one else ' in all'} at the last amount" if total else ""
    drops = "this drop" if one else "these drops"
    return (f"No ex-dividend date is announced yet, but at the spacing of the last 12 months about {n} would fall "
            f"inside the horizon{size}: the price is expected to open lower by about the dividend on "
            f"{'it' if one else 'each'} for mechanical reasons, so a long-side stop closer to the price than that "
            f"can be touched by {drops} alone. " + _zero_drift(drops))


def build_risk(ctx, modules, scenarios) -> dict:
    """Illustrative long- and short-side stop, reward/risk and position-size arithmetic. The notes name
    earnings and an ex-dividend date inside the horizon (context; the arithmetic does not change)."""
    modules = modules or {}
    mk = _market(ctx, modules)
    if mk["close"] is None or mk["atr"] is None or mk["atr"] <= 0:
        return {"available": False, "reason": "last close or ATR(14) unavailable", "notes": [ILLUSTRATIVE]}
    _, hz = _horizon(ctx)
    h = int(hz["primary"])
    notes = [ILLUSTRATIVE,
             "Stops and the first target are the calibrated volatility-band edges (stop at the 95% band, "
             f"{C.VOL_STOP_K:g} sigma; target at 1 sigma). The structural stop and the level target come from the "
             "key levels and are shown for reference only.",
             f"Touch rates say how often ordinary moves reached each price within {h} trading days in the "
             f"calibration sample ({C.VOL_CALIBRATION}). With no measured edge, a stop/target pair is worth about "
             "zero before costs whatever its reward/risk.",
             "Entry reference = last close; no commissions, slippage or financing; a gap can fill a stop well "
             "beyond its level.",
             "Short side ignores borrow availability and cost.",
             C.DISCLAIMER]
    ev = _usable(modules, "event_risk")
    events = []
    if _get(ev, "within_horizon"):
        events.append(f"Earnings on {_get(ev, 'next_earnings', 'date')} fall inside the horizon: overnight gap "
                      "risk through either stop.")
    dv = _usable(modules, "dividends")
    proj = _get(dv, "projected_in_horizon")
    if _get(dv, "within_horizon") and _num(_get(dv, "next_ex_dividend", "amount")) is not None:
        events.append(_ex_dividend_note(_get(dv, "next_ex_dividend"), proj))
    elif (_get(dv, "pays_dividends") and _get(dv, "next_ex_dividend") is None
          and (_num(_get(proj, "ex_dates")) or 0) >= 1):
        events.append(_projected_ex_dividend_note(proj))
    notes[1:1] = events
    return {
        "available": True,
        "label": ILLUSTRATIVE,
        "atr": mk["atr"],
        "horizon_days": h,
        "long_side": _risk_side(1, mk, _get(scenarios, "bull"), h),
        "short_side": _risk_side(-1, mk, _get(scenarios, "bear"), h),
        "notes": notes,
    }
