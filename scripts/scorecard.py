"""
Scorecard, scenarios and risk framing (step 5).

build_scorecard turns the module outputs into six pillar scores (-2..+2), regime-dependent
weights, a 5-step bias with a confidence level and an explicit list of conflicts.
build_scenarios derives conditional bull / bear / base scenarios from the key levels, ATR and
realized volatility; build_risk adds illustrative stop, reward/risk and position-size arithmetic.
Every input module may be missing or unavailable: the affected inputs are skipped and the reasons
say so. Nothing here is a recommendation. Rules are documented in references/methodology.md.
"""
from __future__ import annotations

import math

import config as C

PILLARS = tuple(C.PILLARS)
SCORE_MIN, SCORE_MAX = -2, 2
_PILLAR_ALIASES = {"volatility": "volatility_setup", "volume": "volume_flow", "flow": "volume_flow",
                   "rs": "relative_strength"}
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


def round_half_away(x: float) -> int:
    """Round to the nearest integer, halves away from zero (0.5 -> 1, -0.5 -> -1)."""
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def _clip(x: int) -> int:
    return max(SCORE_MIN, min(SCORE_MAX, int(x)))


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


# ---------------------------------------------------------------------------- pillar tally
class _Tally:
    """One pillar's votes (+1 / 0 / -1; None = input missing), its raw inputs and reason fragments.

    mode "votes": score = round_half_away(2 * net / N), N = applicable slots (missing ones abstain).
    mode "conditions": score = net, each firing condition +-1. Both are clipped to -2..+2.
    """

    def __init__(self, mode: str):
        self.mode = mode
        self.votes: dict[str, int | None] = {}
        self.inputs: dict = {}
        self.parts: list[str] = []
        self.missing: list[str] = []
        self.not_applicable: list[str] = []

    def add(self, name: str, vote: int, text: str | None = None) -> None:
        self.votes[name] = int(vote)
        if text:
            self.parts.append(text)

    def miss(self, name: str, label: str | None = None) -> None:
        self.votes[name] = None
        if label and label not in self.missing:
            self.missing.append(label)

    def result(self) -> dict:
        used = [v for v in self.votes.values() if v is not None]
        out = {"inputs": self.inputs, "votes": dict(self.votes), "nudges": [], "mode": self.mode,
               "missing": list(self.missing), "not_applicable": list(self.not_applicable)}
        if not used:
            if self.not_applicable and not self.missing:
                reason = f"not applicable ({'; '.join(self.not_applicable)})"
            else:
                reason = "insufficient data" + (f" ({'; '.join(self.missing)})" if self.missing else "")
            return {"score": 0, "raw_score": 0, "available": False, "reason": reason, **out}
        net = sum(used)
        if self.mode == "votes":
            n = len(self.votes)
            score = _clip(round_half_away(2 * net / n))
            tail = f"net {net:+d} of {n} votes"
        else:
            score = _clip(net)
            tail = f"net {net:+d} from firing conditions"
        reason = f"{', '.join(self.parts)}: {tail} -> {score:+d}"
        if self.missing:
            reason += f" (unavailable: {'; '.join(self.missing)})"
        if self.not_applicable:
            reason += f" (not applicable: {'; '.join(self.not_applicable)})"
        return {"score": score, "raw_score": score, "available": True, "reason": reason, **out}


def _compare(t: _Tally, name: str, a, b, a_label: str, b_label: str) -> None:
    """Vote +1 / -1 / 0 for a above / below / equal to b."""
    if a is None or b is None:
        t.miss(name, f"{a_label} vs {b_label}")
        return
    v = _sign(a - b)
    word = {1: "above", -1: "below", 0: "at"}[v]
    rel = f" ({_pct(a / b - 1)})" if b else ""
    t.add(name, v, f"{a_label} {word} {b_label}{rel}")


def _signed(t: _Tally, name: str, label: str, value, fmt=_pct) -> None:
    if value is None:
        t.miss(name, label)
    else:
        t.add(name, _sign(value), f"{label} {fmt(value)}")


# ---------------------------------------------------------------------------- pillars
def _trend(ctx, modules) -> dict:
    """Close vs SMA50 and SMA200, SMA50 vs SMA200, SMA200 slope (+ weekly direction in position mode)."""
    t = _Tally("votes")
    tm, rg = _usable(modules, "trend_momentum"), _usable(modules, "regime")
    if tm is None and rg is None:
        t.missing.append(f"{_why_missing(modules, 'trend_momentum')}; {_why_missing(modules, 'regime')}")
    sma, rd = _get(tm, "sma") or {}, _get(rg, "direction") or {}
    close = _first(_get(tm, "close"), rd.get("close"))
    sma50 = _first(sma.get("sma50"), rd.get("sma50"))
    sma200 = _first(sma.get("sma200"), rd.get("sma200"))
    slope = _first(_get(sma, "slopes", "sma200"), rd.get("sma200_slope"))
    t.inputs.update(close=close, sma50=sma50, sma200=sma200, sma200_slope=slope)
    _compare(t, "close_vs_sma50", close, sma50, "close", "SMA50")
    _compare(t, "close_vs_sma200", close, sma200, "close", "SMA200")
    _compare(t, "sma50_vs_sma200", sma50, sma200, "SMA50", "SMA200")
    if slope is None:
        t.miss("sma200_slope", "SMA200 slope")
    else:
        word = {1: "rising", -1: "falling", 0: "flat"}[_sign(slope)]
        t.add("sma200_slope", _sign(slope), f"SMA200 {word} ({_pct(slope)} over {C.SLOPE_BARS} bars)")

    if _horizon(ctx)[1].get("weekly_emphasis"):
        w = _num(_get(_usable(modules, "multi_timeframe"), "weekly", "sign"))
        t.inputs["weekly_sign"] = w
        if w is None:
            t.miss("weekly_direction", "weekly direction")
        else:
            label = _get(modules, "multi_timeframe", "weekly", "direction") or {1: "up", -1: "down", 0: "mixed"}[_sign(w)]
            t.add("weekly_direction", _sign(w), f"weekly {str(label).lower()}")
    return t.result()


def _momentum(modules) -> dict:
    """Signs of 12-1 momentum, 3m and 6m returns, MACD histogram and MACD vs zero."""
    t = _Tally("votes")
    tm = _usable(modules, "trend_momentum")
    if tm is None:
        t.missing.append(_why_missing(modules, "trend_momentum"))
    mom = _num(_get(tm, "mom_12_1"))
    r3, r6 = _num(_get(tm, "returns", "3m")), _num(_get(tm, "returns", "6m"))
    hist, macd = _num(_get(tm, "macd", "hist")), _num(_get(tm, "macd", "macd"))
    t.inputs.update(mom_12_1=mom, return_3m=r3, return_6m=r6, macd=macd, macd_hist=hist)
    _signed(t, "mom_12_1", "12-1 momentum", mom)
    _signed(t, "return_3m", "3m return", r3)
    _signed(t, "return_6m", "6m return", r6)
    _signed(t, "macd_hist", "MACD hist", hist, lambda x: f"{x:+.2f}")
    if macd is None:
        t.miss("macd_vs_zero", "MACD")
    else:
        t.add("macd_vs_zero", _sign(macd), f"MACD {macd:+.2f} ({'above' if macd > 0 else 'below' if macd < 0 else 'at'} zero)")
    return t.result()


def _relative_strength(ctx, modules) -> dict:
    """Per applicable benchmark: signs of the 3m and 6m relative returns and the RS-line extreme vote."""
    t = _Tally("votes")
    rs = _usable(modules, "relative_strength")
    ticker = str((ctx or {}).get("ticker") or "").upper()
    benches = []
    if ticker == C.SPY:
        t.not_applicable.append("SPY is the benchmark")
    else:
        benches.append(("spy", "vs_spy", C.SPY))
    if (ctx or {}).get("is_etf"):
        t.not_applicable.append("ETF: no sector comparison")
    else:
        benches.append(("sector", "vs_sector", _get(rs, "vs_sector", "etf") or (ctx or {}).get("sector_etf") or "sector ETF"))

    for key, block_key, sym in benches:
        b = _block(rs, block_key)
        if b is None:
            why = _why_missing(modules, "relative_strength") if rs is None else (
                f"RS vs {sym}: {_get(rs, block_key, 'reason') or 'block missing'}")
            for slot in ("3m", "6m", "extreme"):
                t.miss(f"{key}_{slot}", why)
            continue
        r3, r6 = _num(b.get("rel_return_3m")), _num(b.get("rel_return_6m"))
        hi, lo = _flag(b.get("rs_new_high")), _flag(b.get("rs_new_low"))
        t.inputs.update({f"{key}_rel_return_3m": r3, f"{key}_rel_return_6m": r6,
                         f"{key}_rs_new_high": hi, f"{key}_rs_new_low": lo})
        bits = []
        for slot, val in (("3m", r3), ("6m", r6)):
            if val is None:
                t.miss(f"{key}_{slot}", f"{slot} relative return vs {sym}")
                bits.append(f"{slot} n/a")
            else:
                t.add(f"{key}_{slot}", _sign(val))
                bits.append(f"{slot} {_pct(val)}")
        if hi is None and lo is None:
            t.miss(f"{key}_extreme", f"RS-line extreme vs {sym}")
        else:
            ext = 1 if hi and not lo else -1 if lo and not hi else 0
            t.add(f"{key}_extreme", ext)
            bits.append({1: "RS line at a new high", -1: "RS line at a new low", 0: "RS line not at an extreme"}[ext])
        t.parts.append(f"vs {sym} " + " ".join(bits))
    return t.result()


def _volume_flow(modules) -> dict:
    """Up/down volume ratio, OBV/A-D divergence, OBV/A-D 20-bar slope, close vs the anchored VWAPs."""
    t = _Tally("votes")
    vf = _usable(modules, "volume_flow")
    if vf is None:
        t.missing.append(_why_missing(modules, "volume_flow"))

    ratio = _num(_get(vf, "updown_volume_ratio_50d"))
    t.inputs["updown_volume_ratio_50d"] = ratio
    if ratio is None:
        t.miss("updown_volume", "up/down volume ratio")
    else:
        v = 1 if ratio > C.FLOW_UPDOWN_HIGH else -1 if ratio < C.FLOW_UPDOWN_LOW else 0
        t.add("updown_volume", v, f"up/down volume {ratio:.2f}")

    div = {name: _get(vf, key, "divergence") for name, key in (("OBV", "obv"), ("A/D", "ad"))}
    t.inputs.update(obv_divergence=div["OBV"], ad_divergence=div["A/D"])
    known = {k: v for k, v in div.items() if v is not None}
    if not known:
        t.miss("divergence", "OBV/A-D divergence")
    else:
        bear = [k for k, v in known.items() if v == "bearish"]
        bull = [k for k, v in known.items() if v == "bullish"]
        v = 1 if bull and not bear else -1 if bear and not bull else 0
        text = (f"{'/'.join(bear)} bearish divergence" if v < 0 else f"{'/'.join(bull)} bullish divergence"
                if v > 0 else "OBV/A-D divergences conflict" if bear else "no OBV/A-D divergence")
        t.add("divergence", v, text)

    slopes = {name: _num(_get(vf, key, "slope_20d")) for name, key in (("OBV", "obv"), ("A/D", "ad"))}
    t.inputs.update(obv_slope_20d=slopes["OBV"], ad_slope_20d=slopes["A/D"])
    signs = {k: _sign(v) for k, v in slopes.items() if v is not None}
    if not signs:
        t.miss("flow_slope", "OBV/A-D slope")
    else:
        vals = set(signs.values())
        v = vals.pop() if len(vals) == 1 else 0
        names = " and ".join(signs)
        word = {1: "rising", -1: "falling", 0: "flat or mixed"}[v]
        t.add("flow_slope", v, f"{names} {word} over {C.SLOPE_BARS} bars")

    rel = [_num(a.get("price_vs_vwap_pct")) for a in (_get(vf, "anchored_vwaps") or []) if isinstance(a, dict)]
    rel = [r for r in rel if r is not None]
    above, below = sum(r > 0 for r in rel), sum(r < 0 for r in rel)
    t.inputs.update(avwaps_above=above, avwaps_below=below, avwaps_total=len(rel))
    if not rel:
        t.miss("avwap", "anchored VWAPs")
    else:
        v = 1 if above / len(rel) > C.FLOW_AVWAP_MAJORITY else -1 if below / len(rel) > C.FLOW_AVWAP_MAJORITY else 0
        t.add("avwap", v, f"close above {above} of {len(rel)} anchored VWAPs")
    return t.result()


def _direction_sign(modules, trend_score: int | None) -> int | None:
    """Regime direction sign; the trend pillar's sign when the regime module is missing."""
    d = _num(_get(_usable(modules, "regime"), "direction_sign"))
    if d is not None:
        return _sign(d)
    return None if trend_score is None else _sign(trend_score)


def _volatility_setup(modules, direction: int | None) -> dict:
    """Stored energy (squeeze/compression) in the trend direction, a recent squeeze release, vol extremes."""
    t = _Tally("conditions")
    vol, rg = _usable(modules, "volatility"), _usable(modules, "regime")
    sq = _get(vol, "squeeze") if isinstance(_get(vol, "squeeze"), dict) else None
    on = _flag(_get(sq, "on"))
    compression = _flag(_get(rg, "volatility", "compression"))
    bw_pctl = _first(_get(vol, "bb_width_pctl"), _get(rg, "volatility", "bb_width_pctl"))
    if compression is None and bw_pctl is not None:
        compression = bw_pctl < C.COMPRESSION_BW_PCTL
    label = _get(rg, "volatility", "label")
    vol_pctl = _num(_get(rg, "volatility", "vol_pctl"))
    sq_bars = _num(_get(sq, "bars"))
    release = _get(sq, "last_release") if isinstance(_get(sq, "last_release"), dict) else {}
    ago, rel_dir = _num(release.get("bars_ago")), release.get("direction")
    t.inputs.update(squeeze_on=on, squeeze_bars=sq_bars, compression=compression, bb_width_pctl=bw_pctl,
                    vol_label=label, vol_pctl=vol_pctl, direction_sign=direction,
                    last_release={"date": release.get("date"), "bars_ago": ago, "direction": rel_dir} if release else None)
    if vol is None:
        t.missing.append(_why_missing(modules, "volatility"))
    if rg is None:
        t.missing.append(_why_missing(modules, "regime"))

    if on is None and compression is None:
        t.miss("stored_energy", "squeeze / compression")
    elif on or compression:
        bits = [f"squeeze on ({int(sq_bars or 0)} bars)"] if on else []
        if compression:
            bits.append("compression" + (f" (BB width pctl {bw_pctl:.0f})" if bw_pctl is not None else ""))
        v = direction or 0
        where = {1: "uptrend (+1)", -1: "downtrend (-1)"}.get(v, "no prevailing direction: break direction unknown")
        t.add("stored_energy", v, f"{' + '.join(bits)}, {where}")
    else:
        text = "no " + " or ".join(w for w, flag in (("squeeze", on), ("compression", compression)) if flag is False)
        if compression is None:
            text += (f" (compression unknown: the BB-width percentile needs {C.PERCENTILE_WINDOW} bars "
                     "of bandwidth history)")
            t.missing.append("compression (BB-width percentile)")
        elif on is None:
            text += " (squeeze state unknown)"
            t.missing.append("squeeze state")
        t.add("stored_energy", 0, text)

    if on is None:
        t.miss("release", "squeeze state")
    else:
        d = {"up": 1, "down": -1}.get(rel_dir, 0)
        if ago is not None and ago < C.SQUEEZE_RELEASE_RECENT_BARS and d:
            t.add("release", d, f"squeeze released {int(ago)} bar(s) ago, {rel_dir} ({d:+d})")
        else:
            t.add("release", 0, "no recent squeeze release")

    if label is None:
        t.miss("vol_extreme", "volatility label")
    elif label == "extreme":
        pctl = f" (pctl {vol_pctl:.0f})" if vol_pctl is not None else ""
        if (direction or 0) > 0:
            t.add("vol_extreme", 0, f"extreme vol{pctl} in an uptrend: fragile")
        else:
            t.add("vol_extreme", -1, f"extreme vol{pctl} without an uptrend (-1)")
    else:
        t.add("vol_extreme", 0, f"{label} vol")
    return t.result()


def _mean_reversion(modules) -> dict:
    """Oversold conditions vote +1 (bounce potential), overbought ones -1."""
    t = _Tally("conditions")
    mr = _usable(modules, "mean_reversion")
    if mr is None:
        t.missing.append(_why_missing(modules, "mean_reversion"))
    rsi14, rsi2 = _num(_get(mr, "rsi14")), _num(_get(mr, "rsi2"))
    z20, pct_b = _num(_get(mr, "z20")), _num(_get(mr, "bb_pct_b"))
    dist = _num(_get(mr, "dist_sma50_atr"))
    t.inputs.update(rsi14=rsi14, rsi2=rsi2, z20=z20, bb_pct_b=pct_b, dist_sma50_atr=dist)

    def band(name, label, x, lo, hi, fmt):
        if x is None:
            t.miss(name, label)
        elif x < lo:
            t.add(name, 1, f"{label} {fmt(x)} oversold (+1)")
        elif x > hi:
            t.add(name, -1, f"{label} {fmt(x)} overbought (-1)")
        else:
            t.add(name, 0, f"{label} {fmt(x)}")

    band("rsi14", "RSI14", rsi14, C.MR_RSI14_OVERSOLD, C.MR_RSI14_OVERBOUGHT, lambda x: f"{x:.1f}")
    band("rsi2", "RSI2", rsi2, C.MR_RSI2_OVERSOLD, C.MR_RSI2_OVERBOUGHT, lambda x: f"{x:.1f}")
    # z20 beyond +-2 and %B outside 0..1 are the same condition with the default bands: one vote.
    if z20 is not None:
        band("band", "z20", z20, -C.MR_Z_EXTENDED, C.MR_Z_EXTENDED, lambda x: f"{x:+.2f}")
    else:
        band("band", "%B", pct_b, 0.0, 1.0, lambda x: f"{x:.2f}")
    band("dist_sma50", "dist SMA50", dist, -C.MR_DIST_SMA50_ATR, C.MR_DIST_SMA50_ATR, lambda x: f"{x:+.1f} ATR")
    return t.result()


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
    pillar = _PILLAR_ALIASES.get(sig.get("pillar"), sig.get("pillar"))
    return {"id": sig.get("id"), "name": sig.get("name") or sig.get("id"), "bias": bias, "pillar": pillar,
            "confidence": conf, "n": _num(hs.get("n")), "edge_median": em, "edge_hit_rate": eh,
            "confirmed": bool(confirmed)}


def _active_signals(modules, primary: int) -> list[dict]:
    sigs = _usable(modules, "signals")
    return [_signal_view(s, primary) for s in (_get(sigs, "active") or []) if isinstance(s, dict)]


def _apply_nudges(pillars: dict, views: list[dict], notes: list) -> list[dict]:
    """Confirmed signals (normal/low sample, edge beyond both thresholds in the bias direction)
    move their pillar by +-1 each; the net move per pillar is capped at C.SIGNAL_NUDGE_MAX."""
    applied = []
    for v in views:
        if not v["confirmed"]:
            continue
        if v["pillar"] not in pillars:
            notes.append(f"signal {v['id']}: pillar {v['pillar']!r} is not scored; no nudge")
            continue
        pillars[v["pillar"]]["nudges"].append({k: v[k] for k in ("id", "bias", "confidence", "n",
                                                                 "edge_median", "edge_hit_rate")})
        applied.append(v)
    for p in pillars.values():
        if not p["nudges"]:
            continue
        net = sum(n["bias"] for n in p["nudges"])
        step = max(-C.SIGNAL_NUDGE_MAX, min(C.SIGNAL_NUDGE_MAX, net))
        p["score"] = _clip(p["raw_score"] + step)
        desc = ", ".join(f"{n['id']} (n={int(n['n'] or 0)}, edge {_pct(n['edge_median'])}, "
                         f"hit {n['edge_hit_rate'] * 100:+.0f}pp)" for n in p["nudges"])
        capped = f", net {net:+d} capped at {step:+d}" if step != net else ""
        p["reason"] += f"; signal nudge {step:+d} from {desc}{capped} -> {p['score']:+d}"
    return applied


# ---------------------------------------------------------------------------- weights, bias
def pillar_weights(regime_type: str | None, horizon: str = "swing") -> dict[str, float]:
    """C.REGIME_WEIGHTS for the regime type (transitional if unknown), plus C.POSITION_WEIGHT_SHIFT in
    position mode; negatives floored at 0, renormalized to sum 1."""
    base = C.REGIME_WEIGHTS.get(regime_type) or C.REGIME_WEIGHTS["transitional"]
    w = {p: float(base.get(p, 0.0)) for p in PILLARS}
    if horizon == "position":
        for p, d in C.POSITION_WEIGHT_SHIFT.items():
            if p in w:
                w[p] = max(0.0, w[p] + d)
    total = sum(w.values())
    return {p: v / total for p, v in w.items()} if total > 0 else {p: 1 / len(w) for p in w}


def _neutral_index() -> int:
    return next(i for i, (bound, _) in enumerate(C.BIAS_STEPS) if 0 <= bound)


def bias_label(score: float) -> tuple[str, int]:
    """(label, sign) from C.BIAS_STEPS. A score exactly on a boundary takes the label nearer Neutral,
    so the scale is symmetric: +-0.4 is Neutral, +-1.2 is Moderately bullish / bearish."""
    s = round(float(score), 9)
    if s < 0:
        idx = next(i for i, (bound, _) in enumerate(C.BIAS_STEPS) if s < bound)
    else:
        idx = next(i for i, (bound, _) in enumerate(C.BIAS_STEPS) if s <= bound)
    return C.BIAS_STEPS[idx][1], _sign(idx - _neutral_index())


# ---------------------------------------------------------------------------- confidence
def _mtf_vs_bias(mtf: dict | None, bias_sign: int) -> tuple[str | None, str]:
    """Weekly picture relative to the bias: confirms / contradicts / mixed, or None if unavailable."""
    w = _num(_get(mtf, "weekly", "sign"))
    daily_weekly = _get(mtf, "alignment")
    if w is None:
        return None, "weekly direction unavailable"
    w = _sign(w)
    if bias_sign == 0:
        state = "confirms" if w == 0 else "mixed"
    elif daily_weekly == "contradicts" or w == -bias_sign:
        state = "contradicts"
    elif daily_weekly == "confirms" and w == bias_sign:
        state = "confirms"
    else:
        state = "mixed"
    meaning = {"confirms": "consistent with the bias", "contradicts": "against the bias",
               "mixed": "no clear confirmation of the bias"}[state]
    wword = {1: "uptrend", -1: "downtrend", 0: "mixed"}[w]
    return state, f"weekly {wword} (daily/weekly {daily_weekly or 'n/a'}): {meaning}"


def _confidence(pillars: dict, weights: dict, bias_sign: int, modules, views: list[dict]) -> dict:
    if bias_sign == 0:
        agree = [p for p, v in pillars.items() if v["available"] and v["score"] == 0]
    else:
        agree = [p for p, v in pillars.items() if _sign(v["score"]) == bias_sign]
    agreement = round(sum(weights[p] for p in agree), 4)
    mtf_state, mtf_text = _mtf_vs_bias(_usable(modules, "multi_timeframe"), bias_sign)

    support = [v["id"] for v in views if v["confirmed"] and bias_sign != 0 and v["bias"] == bias_sign]
    against = [v["id"] for v in views if v["confirmed"] and v["bias"] != bias_sign]
    insufficient = [v["id"] for v in views if v["confidence"] not in ("normal", "low")]
    insufficient_only = bool(views) and len(insufficient) == len(views)
    signal_support = {"supporting": support, "opposing": against, "insufficient": insufficient,
                      "unconfirmed": [v["id"] for v in views if not v["confirmed"] and v["id"] not in insufficient],
                      "insufficient_only": insufficient_only}

    reasons = [f"pillar agreement {agreement:.2f} (weight share of pillars "
               f"{'scoring 0' if bias_sign == 0 else 'on the bias side'}: {', '.join(agree) or 'none'})",
               mtf_text]
    # factors: the same reasons as codes and numbers, for rendering in other languages (ui/i18n.js)
    w = _num(_get(modules, "multi_timeframe", "weekly", "sign"))
    factors = [{"code": "agreement", "value": agreement, "pillars": agree, "neutral": bias_sign == 0},
               {"code": "mtf", "state": mtf_state, "weekly_sign": None if w is None else _sign(w),
                "daily_weekly": _get(modules, "multi_timeframe", "alignment")}]
    if views:
        reasons.append(f"active signals: {len(support)} sample-backed in favour, {len(against)} against, "
                       f"{len(insufficient)} with an insufficient sample")
        factors.append({"code": "signals", "supporting": len(support), "opposing": len(against),
                        "insufficient": len(insufficient)})
    else:
        reasons.append("no active signals")
        factors.append({"code": "no_signals"})

    if agreement < C.CONF_AGREEMENT_LOW or mtf_state == "contradicts":
        level = "low"
        why = ["agreement_low"] if agreement < C.CONF_AGREEMENT_LOW else ["weekly_contradicts"]
        reasons.append(f"low: agreement < {C.CONF_AGREEMENT_LOW:.2f}" if agreement < C.CONF_AGREEMENT_LOW
                       else "low: the weekly picture contradicts the bias")
    elif agreement >= C.CONF_AGREEMENT_HIGH and mtf_state == "confirms" and not insufficient_only and not against:
        level, why = "high", []
        reasons.append(f"high: agreement >= {C.CONF_AGREEMENT_HIGH:.2f}, weekly confirms, no opposing or "
                       "insufficient-only signal evidence")
    else:
        level, why, text = "medium", [], []
        if agreement < C.CONF_AGREEMENT_HIGH:
            why.append("agreement_below_high")
            text.append(f"agreement < {C.CONF_AGREEMENT_HIGH:.2f}")
        if mtf_state != "confirms":
            why.append(f"weekly_{mtf_state or 'unavailable'}")
            text.append(f"weekly {mtf_state or 'unavailable'}")
        if insufficient_only:
            why.append("insufficient_only")
            text.append("active signals all have insufficient samples")
        if against:
            why.append("opposing")
            text.append(f"sample-backed signals against the bias ({', '.join(against)})")
        reasons.append("medium: " + "; ".join(text))
    factors.append({"code": "level", "level": level, "why": why, "opposing": against,
                    "low": C.CONF_AGREEMENT_LOW, "high": C.CONF_AGREEMENT_HIGH})
    return {"level": level, "agreement": agreement, "mtf_alignment": mtf_state,
            "daily_weekly": _get(modules, "multi_timeframe", "alignment"),
            "signal_support": signal_support, "reasons": reasons, "factors": factors}


# ---------------------------------------------------------------------------- conflicts
_PAIR_MEANINGS = {   # keyed by pillar pair in C.PILLARS order; inner key = sign of the first pillar
    ("trend", "mean_reversion"): {
        1: "Overbought in an uptrend: this often resolves by sideways consolidation or a shallow pullback "
           "rather than a reversal.",
        -1: "Oversold in a downtrend: bounces from oversold readings tend to be brief unless the trend inputs turn."},
    ("trend", "momentum"): {
        1: "Momentum is fading inside an intact trend: often a pause or consolidation; a trend change also "
           "needs the moving averages to turn.",
        -1: "Momentum is improving inside a downtrend: often an early base or a counter-trend rally; the trend "
            "inputs have not turned yet."},
    ("trend", "relative_strength"): {
        1: "Rising in absolute terms but lagging the benchmark: the move is mostly market beta rather than "
           "stock-specific leadership.",
        -1: "Falling in absolute terms but holding up better than the benchmark: relative resilience, which can "
            "mark leadership once the market turns."},
    ("trend", "volume_flow"): {
        1: "The price trend is not confirmed by volume flow: distribution under a rising price can precede a stall.",
        -1: "Accumulation into a falling price: selling pressure may be fading, but flow can lead price by weeks "
            "or fail to turn it."},
    ("momentum", "mean_reversion"): {
        1: "Strong momentum is what makes a stock overbought; in momentum phases overbought readings are weak "
           "reversal signals on their own.",
        -1: "Weak momentum with oversold readings: oversold conditions can persist while momentum stays negative."},
}
_GENERIC_PAIR = ("The two pillars measure different things over different windows; opposite readings lower "
                 "conviction, and the regime weights decide the net read.")


def _conflict(kind: str, involved: list[str], description: str, meaning: str, key: str | None = None,
              params: dict | None = None, **extra) -> dict:
    """One conflict; `key` names its typical meaning and `params` holds the numbers in the description,
    so the UI can render both in another language."""
    return {"type": kind, "involved": involved, **extra, "description": description, "typical_meaning": meaning,
            "key": key or kind, "params": params or {}}


def _conflicts(ctx, modules, pillars: dict, views: list[dict]) -> list[dict]:
    out = []
    names = [p for p in PILLARS if p in pillars]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            sa, sb = _sign(pillars[a]["score"]), _sign(pillars[b]["score"])
            if sa and sb and sa == -sb:
                known = sa in _PAIR_MEANINGS.get((a, b), {})
                meaning = _PAIR_MEANINGS[(a, b)][sa] if known else _GENERIC_PAIR
                out.append(_conflict("pillar_pair", [a, b], f"{a} {pillars[a]['score']:+d} vs {b} "
                                     f"{pillars[b]['score']:+d}", meaning,
                                     key=f"{a}|{b}|{sa:+d}" if known else "pillar_pair",
                                     params={"a": a, "b": b, "score_a": pillars[a]["score"],
                                             "score_b": pillars[b]["score"]}, pillars=[a, b]))

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

    rs_score = pillars.get("relative_strength", {}).get("score", 0)
    spy = _block(_usable(modules, "market_context"), "spy")
    if rs_score > 0 and _flag(_get(spy, "above_sma200")) is False:
        rel = _num(_get(spy, "pct_vs_sma200"))
        out.append(_conflict("rs_vs_market", ["relative_strength", "market_context"],
                             f"relative strength {rs_score:+d} while SPY is below its 200-day SMA"
                             + (f" ({_pct(rel)})" if rel is not None else ""),
                             "Leadership against a risk-off tape: relative strength in a weak market can mark "
                             "future leaders, but in broad selloffs strong names are often sold late.",
                             params={"rs_score": rs_score, "spy_pct_vs_sma200": rel}))

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
                             "The event can override the technicals: earnings reactions often exceed the "
                             "1-sigma technical range, gap through levels and stops, and can reset the trend.",
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


# ---------------------------------------------------------------------------- scorecard
def build_scorecard(ctx, modules) -> dict:
    """Six pillars (-2..+2) with reasons, regime weights, weighted score, 5-step bias, confidence, conflicts."""
    modules = modules or {}
    horizon, hz = _horizon(ctx)
    primary = int(_num(_get(modules, "signals", "primary_horizon")) or hz["primary"])
    notes: list[str] = []

    pillars = {"trend": _trend(ctx, modules), "momentum": _momentum(modules),
               "relative_strength": _relative_strength(ctx, modules), "volume_flow": _volume_flow(modules)}
    direction = _direction_sign(modules, pillars["trend"]["score"] if pillars["trend"]["available"] else None)
    pillars["volatility_setup"] = _volatility_setup(modules, direction)
    pillars["mean_reversion"] = _mean_reversion(modules)
    pillars = {p: pillars[p] for p in PILLARS if p in pillars}

    views = _active_signals(modules, primary)
    nudged = _apply_nudges(pillars, views, notes)

    rtype = _get(_usable(modules, "regime"), "type")
    if rtype not in C.REGIME_WEIGHTS:
        notes.append(f"regime type {rtype!r} unknown or unavailable: transitional weights used")
    weights = pillar_weights(rtype if rtype in C.REGIME_WEIGHTS else "transitional", horizon)
    weighted = round(sum(weights[p] * pillars[p]["score"] for p in pillars), 4)
    bias, bias_sign = bias_label(weighted)
    missing = [p for p, v in pillars.items() if not v["available"]]
    if missing:
        notes.append(f"pillars without data score 0 and keep their weight: {', '.join(missing)}")

    return {
        "available": True,
        "horizon": horizon,
        "pillars": pillars,
        "weights": weights,
        "weight_basis": {"regime_type": rtype, "weights_used": rtype if rtype in C.REGIME_WEIGHTS else "transitional",
                         "position_shift": horizon == "position"},
        "weighted_score": weighted,
        "bias": bias,
        "bias_sign": bias_sign,
        "confidence": _confidence(pillars, weights, bias_sign, modules, views),
        "conflicts": _conflicts(ctx, modules, pillars, views),
        "signal_nudges": [{k: v[k] for k in ("id", "pillar", "bias", "confidence", "n")} for v in nudged],
        "primary_horizon": primary,
        "notes": notes,
    }


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


def build_scenarios(ctx, modules, scorecard) -> dict:
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

    ev = _usable(modules, "event_risk")
    caveat = None
    if _get(ev, "within_horizon"):
        med = _num(_get(ev, "reactions", "median_abs_move"))
        caveat = (f"Earnings on {_get(ev, 'next_earnings', 'date')} fall inside the {h}-day horizon"
                  + (f"; the median absolute two-day earnings move was {med * 100:.1f}% over the last "
                     f"{int(_num(_get(ev, 'reactions', 'n')) or 0)} reports" if med is not None else "")
                  + ", so the realized range can be much wider than the 1-sigma estimate.")

    if rv is None:
        base = {"available": False, "reason": "20-day realized volatility unavailable", "earnings_caveat": caveat}
    else:
        sd = rv * math.sqrt(h / C.TRADING_DAYS_PER_YEAR)
        move = close * sd
        base = {"available": True, "rv20": rv, "expected_move_1sd_pct": sd, "expected_move_1sd_price": move,
                "expected_move_atr": move / atr, "range_low": close - move, "range_high": close + move,
                "earnings_caveat": caveat,
                "method": "last close x rv20 (annualized) x sqrt(h / 252); zero drift, assumes recent volatility "
                          "persists; about 68% of outcomes under a normal approximation, fat tails in practice"}
        base["text"] = (f"Over {h} trading days, 20-day realized volatility of {rv * 100:.1f}% annualized implies "
                        f"a 1-sigma move of about +-{sd * 100:.1f}% (+-{_px(move)}, {move / atr:.1f} ATR): roughly "
                        f"{_px(close - move)} to {_px(close + move)}." + (f" {caveat}" if caveat else ""))

    bias_sign = int(_num(_get(scorecard, "bias_sign")) or 0)
    leaning = {1: "bull", -1: "bear", 0: "neither"}[_sign(bias_sign)]
    return {
        "available": True,
        "ticker": ticker,
        "horizon_days": h,
        "last_close": close,
        "atr": atr,
        "bull": _directional(1, mk, ticker),
        "bear": _directional(-1, mk, ticker),
        "base": base,
        "bias": _get(scorecard, "bias"),
        "leaning": leaning,
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


def _risk_side(d: int, mk: dict, scen_side: dict | None) -> dict:
    """Long (d=+1) or short (d=-1) arithmetic from the last close."""
    entry, atr = mk["close"], mk["atr"]
    stop_side, target_side = (mk["supports"], mk["resistances"]) if d > 0 else (mk["resistances"], mk["supports"])
    notes = []

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
        target, t_basis = float(target_side[0]["price"]), f"nearest {'resistance' if d > 0 else 'support'}"
    elif scen_side and scen_side.get("targets"):
        target, t_basis = float(scen_side["targets"][0]["price"]), scen_side["targets"][0]["label"]
    else:
        target, t_basis = None, "none"

    stops = {"structural": structural, "atr": atr_stop}
    dist_atr = {k: (None if s is None else d * (entry - s) / atr) for k, s in stops.items()}
    dist_pct = {k: (None if s is None else d * (entry - s) / entry) for k, s in stops.items()}
    rr = {k: (None if s is None or target is None or d * (entry - s) <= 0 else d * (target - entry) / (d * (entry - s)))
          for k, s in stops.items()}
    sizes = {k: _size(entry, s, d) for k, s in stops.items()}
    primary = "structural" if sizes["structural"] else "atr"
    example = {"account": C.ACCOUNT_SIZE, "risk_pct": C.RISK_PER_TRADE,
               "risk_dollars": C.ACCOUNT_SIZE * C.RISK_PER_TRADE, "stop_basis": primary,
               **(sizes[primary] or {"stop": None, "shares": None, "notional": None}), "by_stop": sizes}
    if example["shares"] == 0:
        notes.append(f"position-size example: {example['flag']}")

    return {
        "entry_ref": entry,
        "structural_stop": structural,
        "structural_ref": None if ref is None else {
            "price": float(ref["price"]), "zone_low": _num(ref.get("zone_low")), "zone_high": _num(ref.get("zone_high")),
            "sources": list(ref.get("sources") or []), "distance_atr": (float(ref["price"]) - entry) / atr,
            "distance_pct": float(ref["price"]) / entry - 1},
        "structural_stop_basis": (f"nearest {'support zone low -' if d > 0 else 'resistance zone high +'} "
                                  f"{C.STRUCTURE_STOP_BUFFER_ATR:g} ATR"),
        "atr_stop": atr_stop,
        "atr_stop_basis": f"entry {'-' if d > 0 else '+'} {C.STOP_ATR_MULT:g} ATR",
        "stop_distance_atr": dist_atr,
        "stop_distance_pct": dist_pct,
        "first_target": target,
        "first_target_basis": t_basis,
        "reward_risk": rr,
        "position_size_example": example,
        "notes": notes,
    }


def build_risk(ctx, modules, scenarios) -> dict:
    """Illustrative long- and short-side stop, reward/risk and position-size arithmetic."""
    modules = modules or {}
    mk = _market(ctx, modules)
    if mk["close"] is None or mk["atr"] is None or mk["atr"] <= 0:
        return {"available": False, "reason": "last close or ATR(14) unavailable", "notes": [ILLUSTRATIVE]}
    notes = [ILLUSTRATIVE,
             "Entry reference = last close; no commissions, slippage or financing; a gap can fill a stop well "
             "beyond its level.",
             "Short side ignores borrow availability and cost.",
             C.DISCLAIMER]
    ev = _usable(modules, "event_risk")
    if _get(ev, "within_horizon"):
        notes.insert(1, f"Earnings on {_get(ev, 'next_earnings', 'date')} fall inside the horizon: overnight gap "
                        "risk through either stop.")
    return {
        "available": True,
        "label": ILLUSTRATIVE,
        "atr": mk["atr"],
        "long_side": _risk_side(1, mk, _get(scenarios, "bull")),
        "short_side": _risk_side(-1, mk, _get(scenarios, "bear")),
        "notes": notes,
    }
