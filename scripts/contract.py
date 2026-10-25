#!/usr/bin/env python3
"""
The ta-1.1 contract: a small, versioned summary of one stock's swing and position runs for other tools.

metrics.json is internal and may change between versions. The contract is the public interface: fields are only
ever added, and renaming or removing one means a new major version (ta-2.0). It selects and normalises facts and
judges nothing (no "buy", no target of its own). contracts/ta-1.1.schema.json describes every field;
tests/test_contract.py checks both.

Blocks that do not depend on the horizon (data quality, market, event, volatility, extension, trend, regime, RS,
flow, weekly, price-action context) sit at the top; what does (scorecard, levels, scenarios, stops, earnings inside
the horizon, signal and pattern statistics) sits under horizons.swing and horizons.position.

  python scripts/contract.py SWING/metrics.json POSITION/metrics.json [-o contract.json]
  python scripts/analyze.py TICKER --contract contract.json     (runs the other horizon from the cache)

Conventions: as_of is the last complete daily bar; prices are split-adjusted closes; fractions are decimals
(0.05 = 5%); zones are {low, high} with the level price as mid; null means missing, never estimated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import scorecard as SC

CONTRACT = "ta-1.1"   # 1.1 adds event.last_earnings*, targets' kind; 1.x versions only add fields
HORIZONS = ("swing", "position")
# Modules computed the same way for both horizons; the contract takes them from the swing run.
SHARED = ("snapshot", "data_quality", "market_context", "regime", "trend_momentum", "relative_strength",
          "mean_reversion", "volume_flow", "volatility", "multi_timeframe")
REGIME_STATES = ("uptrend", "downtrend", "range", "transitional", "vol_extreme")


def _get(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _num(x):
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def _mod(m, name):
    """The module block when it ran, else None."""
    b = m.get(name)
    return b if isinstance(b, dict) and b.get("available") else None


def _pick(d, keys):
    return None if not isinstance(d, dict) else {k: d.get(k) for k in keys}


# ---------------------------------------------------------------------------- derived facts
def regime_state(regime):
    """uptrend / downtrend for a trend with a direction, transitional for a trend without one, else the type."""
    if not regime:
        return None
    rtype, sign = regime.get("type"), regime.get("direction_sign")
    if rtype == "trend":
        return {1: "uptrend", -1: "downtrend"}.get(sign, "transitional")
    return rtype if rtype in REGIME_STATES else None


def price_location(close, supports, resistances):
    """Where the last close sits against the zones: inside one (nearest first), between, or beyond all of them."""
    if close is None or (not supports and not resistances):
        return None
    for side, zones in (("support", supports), ("resistance", resistances)):
        for i, z in enumerate(zones):
            if z["low"] is not None and z["high"] is not None and z["low"] <= close <= z["high"]:
                return {"state": f"inside_{side}", "zone": i}
    if not resistances:
        return {"state": "above_all", "zone": None}
    if not supports:
        return {"state": "below_all", "zone": None}
    return {"state": "between", "zone": None}


def config_hash(swing, position):
    snap = {h: _get(m, "meta", "config_snapshot") for h, m in (("swing", swing), ("position", position))}
    return hashlib.sha256(json.dumps(snap, sort_keys=True).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------- shared blocks
def _company(snap):
    return {"name": snap.get("name"), "exchange": snap.get("exchange"), "sector": snap.get("sector"),
            "industry": snap.get("industry"), "currency": snap.get("currency"), "is_etf": bool(snap.get("is_etf")),
            "sector_etf": snap.get("sector_etf"), "benchmark": snap.get("benchmark"),
            "history_first_date": _get(snap, "history", "first_date"), "history_bars": _get(snap, "history", "bars")}


def _data_quality(dq):
    dq = dq or {}
    stale, liq, pb, spac = dq.get("stale") or {}, dq.get("liquidity") or {}, dq.get("partial_bar_excluded"), dq.get("spac_like_start")
    return {"stale": bool(stale.get("is_stale")), "business_days_behind": stale.get("business_days_behind"),
            "partial_bar_excluded": _get(pb, "date") if isinstance(pb, dict) else None,
            "adj_coverage": _num(dq.get("adj_coverage")), "illiquid": bool(liq.get("illiquid")),
            "illiquid_reason": liq.get("reason"), "avg_volume": _num(liq.get("avg_volume")),
            "avg_dollar_volume": _num(liq.get("avg_dollar_volume")),
            "spac_like_start": None if not spac else _pick(spac, ("first_date", "median_close")),
            "history_start_override": dq.get("history_start_override"), "warnings": list(dq.get("warnings") or [])}


def _market(mc):
    spy, vix, sec = (_get(mc, k) for k in ("spy", "vix", "sector"))
    ok = lambda b: isinstance(b, dict) and b.get("available")  # noqa: E731
    return {
        "spy": None if not ok(spy) else {"date": spy.get("date"), "above_sma200": spy.get("above_sma200"),
                                         "vs_sma200": _num(spy.get("pct_vs_sma200")),
                                         "sma200_slope_20d": _num(spy.get("sma200_slope_20d"))},
        "vix": None if not ok(vix) else {"date": vix.get("date"), "level": _num(vix.get("level")),
                                         "pctl_1y": _num(vix.get("pctl_1y"))},
        "sector": None if not ok(sec) else {"etf": sec.get("etf"), "trend": sec.get("trend_label"),
                                            "trend_sign": sec.get("trend_sign"), "vs_sma50": _num(sec.get("pct_vs_sma50")),
                                            "vs_sma200": _num(sec.get("pct_vs_sma200")), "rs_3m": _num(sec.get("rs_3m")),
                                            "rs_6m": _num(sec.get("rs_6m"))},
    }


def _event(er):
    """The next and the latest report, and past reactions. Whether the next one falls inside a horizon is under
    horizons.*. The latest report date lets a caller tell when fundamentals predate the newest statements."""
    if not er:
        return {"available": False, "reason": None, "next_earnings": None, "calendar_days": None,
                "trading_days_to": None, "eps_estimated": None, "last_earnings": None,
                "days_since_last_earnings": None, "last_earnings_move": None, "reactions_n": None,
                "median_abs_move": None, "mean_abs_move": None}
    ne, le, rx = er.get("next_earnings") or {}, er.get("last_earnings") or {}, er.get("reactions") or {}
    return {"available": True, "reason": None, "next_earnings": ne.get("date"), "calendar_days": ne.get("calendar_days"),
            "trading_days_to": ne.get("trading_days_est"), "eps_estimated": _num(ne.get("eps_estimated")),
            "last_earnings": le.get("date"), "days_since_last_earnings": le.get("calendar_days_ago"),
            "last_earnings_move": _num(le.get("move")),
            "reactions_n": rx.get("n"), "median_abs_move": _num(rx.get("median_abs_move")),
            "mean_abs_move": _num(rx.get("mean_abs_move"))}


def _volatility(v):
    if not v:
        return None
    sq = v.get("squeeze") or {}
    return {"atr14": _num(v.get("atr14")), "atr_pct": _num(v.get("atr_pct")), "atr_pct_pctl": _num(v.get("atr_pct_pctl")),
            "rv20": _num(v.get("rv20")), "rv20_pctl": _num(v.get("rv20_pctl")),
            "bb_width_pctl": _num(v.get("bb_width_pctl")), "squeeze_on": sq.get("on"), "squeeze_bars": sq.get("bars")}


def _extension(mr):
    return _pick(mr, ("rsi14", "rsi14_state", "rsi2", "rsi2_state", "z20", "z20_state", "bb_pct_b", "dist_sma50_atr"))


def _trend(tm, lv):
    if not tm:
        return None
    sma, macd, hi = tm.get("sma") or {}, tm.get("macd") or {}, tm.get("from_52w_high") or {}
    cross = macd.get("last_cross")
    return {"sma20": _num(sma.get("sma20")), "sma50": _num(sma.get("sma50")), "sma200": _num(sma.get("sma200")),
            "slopes": sma.get("slopes"), "close_vs": sma.get("close_vs"), "stack": sma.get("stack"),
            "stack_state": sma.get("stack_state"), "mom_12_1": _num(tm.get("mom_12_1")), "returns": tm.get("returns"),
            "macd": {"macd": _num(macd.get("macd")), "signal": _num(macd.get("signal")), "hist": _num(macd.get("hist")),
                     "last_cross": _pick(cross, ("type", "date", "bars_ago", "zero_side"))},
            "high_52w": _num(hi.get("high")), "high_52w_date": hi.get("date"), "from_52w_high": _num(hi.get("pct")),
            "low_52w": _num(_get(lv, "low_52w")), "low_52w_date": _get(lv, "low_52w_date")}


def _regime(rg):
    if not rg:
        return None
    d, s, v = rg.get("direction") or {}, rg.get("strength") or {}, rg.get("volatility") or {}
    return {"state": regime_state(rg), "type": rg.get("type"), "label": rg.get("label"),
            "direction": d.get("label"), "direction_sign": rg.get("direction_sign"), "adx": _num(s.get("adx")),
            "strength": s.get("label"), "vol": v.get("label"), "vol_pctl": _num(v.get("vol_pctl")),
            "compression": v.get("compression")}


def _rs_side(b):
    if not isinstance(b, dict) or not b.get("available"):
        return None
    return {"benchmark": b.get("benchmark"), "rel_return_3m": _num(b.get("rel_return_3m")),
            "rel_return_6m": _num(b.get("rel_return_6m")), "ratio_change_3m": _num(b.get("ratio_change_3m")),
            "ratio_change_6m": _num(b.get("ratio_change_6m")), "rs_line_slope": _num(b.get("rs_line_slope")),
            "rs_new_high": b.get("rs_new_high"), "rs_new_low": b.get("rs_new_low")}


def _rs(m):
    rs = m.get("relative_strength") or {}
    return {"vs_spy": _rs_side(rs.get("vs_spy")), "vs_sector": _rs_side(rs.get("vs_sector")),
            "beta_1y": _num(rs.get("beta_1y")), "corr_1y": _num(rs.get("corr_1y"))}


def _flow(vf):
    if not vf:
        return None
    line = lambda b: _pick(b, ("slope_sign", "divergence", "divergence_bars_ago"))  # noqa: E731
    return {"volume": _num(vf.get("volume")), "avg_volume_50d": _num(vf.get("avg_volume_50d")), "rvol": _num(vf.get("rvol")),
            "updown_volume_ratio_50d": _num(vf.get("updown_volume_ratio_50d")), "obv": line(vf.get("obv")),
            "ad": line(vf.get("ad")),
            "anchored_vwaps": [{"anchor": a.get("anchor"), "date": a.get("anchor_date"), "vwap": _num(a.get("vwap")),
                                "price_vs_vwap_pct": _num(a.get("price_vs_vwap_pct"))}
                               for a in vf.get("anchored_vwaps") or []]}


def _weekly(mtf):
    if not mtf:
        return None
    w = mtf.get("weekly") or {}
    return {"direction": w.get("direction"), "sign": w.get("sign"), "sma10w": _num(w.get("sma10w")),
            "sma40w": _num(w.get("sma40w")), "rsi14w": _num(w.get("rsi14w")), "daily_sign": mtf.get("daily_sign"),
            "alignment": mtf.get("alignment")}


def _context(pa, cs):
    """Price-action state and recent candlesticks: context next to the scorecard, never part of it."""
    st, ai = _get(pa, "state") or {}, _get(pa, "always_in") or {}
    return {"scored": False,
            "pa_state": st.get("label"), "pa_state_since": st.get("since"),
            "always_in": ai.get("direction"), "always_in_since": ai.get("since"),
            "last_failed_breakout": _pick(_get(pa, "last_failed_breakout"), ("side", "date", "level", "bars_ago")),
            "measured_moves": [_pick(x, ("side", "target", "reached", "distance_pct")) for x in _get(pa, "measured_moves") or []],
            "recent_candles": [_pick(x, ("id", "bias", "date", "bars_ago")) for x in (_get(cs, "recent") or [])[:5]]}


# ---------------------------------------------------------------------------- per-horizon blocks
def _zone(z):
    return {"price": _num(z.get("price")), "low": _num(z.get("zone_low")), "high": _num(z.get("zone_high")),
            "score": _num(z.get("score")), "touches": z.get("touches"), "holds": z.get("holds"), "breaks": z.get("breaks"),
            "flips": z.get("flips"), "fresh": z.get("fresh"), "last_test": z.get("last_test"),
            "reaction_score": _num(z.get("reaction_score")), "sources": list(z.get("sources") or []),
            "distance_pct": _num(z.get("distance_pct")), "distance_atr": _num(z.get("distance_atr"))}


def _scenario(s):
    if not isinstance(s, dict) or not s.get("trigger"):
        return None
    t, inv = s.get("trigger") or {}, s.get("invalidation") or {}
    return {"trigger": _num(t.get("level")), "condition": t.get("condition"), "volume": t.get("volume"),
            "trigger_distance_pct": _num(t.get("distance_pct")), "trigger_basis": t.get("basis"),
            # kind "level" = an actual support/resistance zone; "projection" = no level beyond the trigger, so the
            # target is the trigger +- a multiple of ATR (a caller may prefer a valuation range or a wider projection)
            "targets": [{"kind": x.get("kind"), "price": _num(x.get("price")), "low": _num(x.get("zone_low")),
                         "high": _num(x.get("zone_high")), "label": x.get("label"),
                         "distance_pct": _num(x.get("distance_pct"))}
                        for x in s.get("targets") or []],
            "invalidation": _num(inv.get("level")), "invalidation_condition": inv.get("condition")}


def _stops(side):
    if not isinstance(side, dict):
        return None
    d = side.get("stop_distance_pct") or {}
    return {"structural": _num(side.get("structural_stop")), "structural_distance_pct": _num(d.get("structural")),
            "atr": _num(side.get("atr_stop")), "atr_distance_pct": _num(d.get("atr"))}


def _evidence(entries, primary, source=None):
    """Active signals or patterns with their statistics at the primary horizon. confirmed is the scorecard's
    own test: a sample-backed edge over the same-regime baseline in the signal's direction."""
    out = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        v = SC._signal_view(e, primary)
        hs = SC._horizon_stats(e, primary)
        row = {"id": e.get("id"), "name": e.get("name"), "bias": v["bias"], "pillar": v["pillar"],
               "fired_date": e.get("fired_date"), "bars_ago": e.get("bars_ago"), "n": v["n"],
               "confidence": v["confidence"], "hit_rate": _num(hs.get("hit_rate")), "median": _num(hs.get("median")),
               "same_regime_median": _num(_get(hs, "same_regime", "median")),
               "edge_median": v["edge_median"], "edge_hit_rate": v["edge_hit_rate"], "confirmed": v["confirmed"]}
        if source:
            row["source"] = source
        out.append(row)
    return out


def _horizon(m):
    sc, lv, sn, rk = _mod(m, "scorecard"), _mod(m, "levels"), _mod(m, "scenarios"), _mod(m, "risk")
    sig, pa, cs, er = m.get("signals") or {}, m.get("price_action") or {}, m.get("candlesticks") or {}, _mod(m, "event_risk")
    primary = int(_num(sig.get("primary_horizon")) or _num(_get(sc, "primary_horizon")) or 0)
    supports = [_zone(z) for z in _get(lv, "supports") or []]
    resistances = [_zone(z) for z in _get(lv, "resistances") or []]
    conf = _get(sc, "confidence") or {}
    base = _get(sn, "base") or {}
    return {
        "horizon_days": primary or None,
        "stat_horizons": sig.get("stat_horizons"),
        "bias": None if not sc else {
            "score": _num(sc.get("weighted_score")), "label": sc.get("bias"), "sign": sc.get("bias_sign"),
            "bias_confidence": conf.get("level"), "agreement": _num(conf.get("agreement")),
            "mtf_alignment": conf.get("mtf_alignment"),
            "signal_support": _pick(conf.get("signal_support"), ("supporting", "opposing", "insufficient", "unconfirmed"))},
        "pillars": None if not sc else {k: {"score": p.get("score"), "available": bool(p.get("available")),
                                            "weight": _num((sc.get("weights") or {}).get(k))}
                                        for k, p in (sc.get("pillars") or {}).items()},
        "weights_used": _get(sc, "weight_basis", "weights_used"),
        "conflicts": [_pick(c, ("type", "key", "involved", "description", "typical_meaning")) for c in _get(sc, "conflicts") or []],
        "price_location": price_location(_num(_get(lv, "last_close")), supports, resistances),
        "supports": supports,
        "resistances": resistances,
        "levels_window": None if not lv else _pick(lv, ("lookback_bars", "window_start", "window_end")),
        "bull": _scenario(_get(sn, "bull")),
        "bear": _scenario(_get(sn, "bear")),
        "range_1sd": None if not base.get("available") else {
            "low": _num(base.get("range_low")), "high": _num(base.get("range_high")),
            "move_pct": _num(base.get("expected_move_1sd_pct")), "earnings_caveat": base.get("earnings_caveat")},
        "stops_ref": None if not rk else {"long": _stops(rk.get("long_side")), "short": _stops(rk.get("short_side"))},
        "earnings_within_horizon": None if not er else er.get("within_horizon"),
        "signals": _evidence(sig.get("active"), primary),
        "patterns": _evidence(pa.get("active"), primary, "price_action") + _evidence(cs.get("active"), primary, "candlesticks"),
    }


# ---------------------------------------------------------------------------- contract
def _check_pair(swing, position):
    for name, m in (("swing", swing), ("position", position)):
        if m.get("horizon") != name:
            raise ValueError(f"the {name} input is a {m.get('horizon')!r} run")
    for k in ("ticker", "as_of"):
        if swing.get(k) != position.get(k):
            raise ValueError(f"the two runs differ in {k}: {swing.get(k)!r} vs {position.get(k)!r}")


def build(swing: dict, position: dict) -> dict:
    """The ta-1.1 contract from one stock's swing and position metrics (same ticker, same last bar)."""
    _check_pair(swing, position)
    m = swing
    snap = m.get("snapshot") or {}
    warnings = [f"{k} differs between the swing and position runs; the swing run's is used"
                for k in SHARED if swing.get(k) != position.get(k)]
    dq = _data_quality(m.get("data_quality"))
    if dq["stale"]:
        warnings.append(f"stale data: the last bar is {dq['business_days_behind']} business days behind")
    if dq["partial_bar_excluded"]:
        warnings.append(f"today's partial bar ({dq['partial_bar_excluded']}) is excluded")
    if dq["illiquid"]:
        warnings.append(f"illiquid: {dq['illiquid_reason']}")
    if dq["spac_like_start"]:
        warnings.append("history starts like a pre-merger SPAC shell; consider --history-start")
    unavailable = {}
    for x in (swing, position):
        for k, why in (_get(x, "meta", "modules_unavailable") or {}).items():
            unavailable.setdefault(k, why)
    errors = []
    for x in (swing, position):
        errors += [e for e in _get(x, "meta", "errors") or [] if e not in errors]
    stamps = [s for s in (_get(x, "meta", "generated_at") or _get(x, "meta", "fetched_at") for x in (swing, position)) if s]
    event = _event(_mod(m, "event_risk"))
    if not event["available"]:
        event["reason"] = _get(m, "event_risk", "reason")
    return {
        "contract": CONTRACT,
        "config_hash": config_hash(swing, position),
        "ticker": m.get("ticker"),
        "generated_at": max(stamps) if stamps else m.get("as_of"),
        "as_of": m.get("as_of"),
        "as_of_basis": "last_close",
        "data_source": m.get("data_source"),
        "last_close": _num(snap.get("price")),
        "quote": {"price": _num(snap.get("quote_price")), "time": snap.get("quote_time")},
        "atr14": _num(_get(m, "volatility", "atr14")),
        "company": _company(snap),
        "data_quality": dq,
        "market": _market(_mod(m, "market_context")),
        "event": event,
        "volatility": _volatility(_mod(m, "volatility")),
        "extension": _extension(_mod(m, "mean_reversion")),
        "trend": _trend(_mod(m, "trend_momentum"), _mod(m, "levels")),
        "regime": _regime(_mod(m, "regime")),
        "rs": _rs(m),
        "flow": _flow(_mod(m, "volume_flow")),
        "weekly": _weekly(_mod(m, "multi_timeframe")),
        "context_only": _context(m.get("price_action"), m.get("candlesticks")),
        "horizons": {"swing": _horizon(swing), "position": _horizon(position)},
        "skipped": dict(_get(m, "meta", "skipped") or {}),
        "modules_unavailable": unavailable,
        "restricted": list(_get(m, "meta", "restricted") or []),
        "warnings": warnings,
        "errors": errors,
        "disclaimer": _get(m, "meta", "disclaimer"),
    }


def dumps(c: dict) -> str:
    return json.dumps(c, ensure_ascii=False, indent=1, allow_nan=False)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=f"Build the {CONTRACT} contract from a swing and a position metrics.json")
    ap.add_argument("swing_metrics")
    ap.add_argument("position_metrics")
    ap.add_argument("-o", "--out", help="write the contract here instead of printing it")
    a = ap.parse_args(argv)
    try:
        swing, position = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (a.swing_metrics, a.position_metrics))
        text = dumps(build(swing, position))
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"Cannot build the contract: {e}", file=sys.stderr)
        return 1
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"({CONTRACT} contract written to {a.out})")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
