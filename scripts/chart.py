"""
Chart data and the self-contained chart.html.

build_chart_data(ctx, modules) turns the analysis context and the module outputs into one
JSON-safe dict: the last ctx["hz"]["chart_bars"] daily bars (split-adjusted OHLCV), weekly
candles over the same span, indicator series, levels, the ~3-year node zones, the volume profile,
earnings markers and the summary panels (regime, scorecard, signal statistics, key numbers, data notes).
Every number is computed here or in the modules; ui/chart.js only draws.

write_chart_html(chart_data, path) writes one HTML file with ui/chart.css, ui/i18n.js and
ui/chart.js inlined and the data embedded as a JSON block: no server, no CDN, opens offline.
"""
from __future__ import annotations

import html
import importlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import candlesticks as CS
import config as C
import indicators as I
import node_zones as NZ
import price_action as PA

UI_DIR = Path(__file__).resolve().parents[1] / "ui"

VWAP_NOTE = ("Anchored VWAPs are a daily-bar approximation: typical price (H+L+C)/3 x daily volume, "
             "cumulated from the anchor bar; not intraday VWAP.")
PROFILE_NOTE = ("Volume profile is a daily-bar approximation: each bar's volume is spread evenly across "
                "its high-low range.")
LEVEL_KEYS = ("price", "zone_low", "zone_high", "sources", "touches", "holds", "breaks", "flips", "fresh",
              "volume_share", "score", "distance_pct", "distance_atr")
NODE_KEYS = ("zone_low", "zone_high", "grade", "density", "share", "distance_pct", "distance_atr")
MODULE_NAMES = ("market_context", "regime", "trend_momentum", "relative_strength", "mean_reversion",
                "volume_flow", "volatility", "levels", "multi_timeframe", "event_risk", "signals",
                "price_action", "candlesticks", "scorecard", "scenarios", "risk")
LIBRARY_KEYS = ("id", "name", "bias", "last_fired", "fires_total", "horizon", "n", "hit_rate", "median",
                "confidence", "base_median", "base_hit_rate", "edge_median", "edge_hit_rate")


# ---------------------------------------------------------------------------- value helpers
def _num(x) -> float | None:
    """Plain float, or None for missing / non-finite values."""
    if x is None or isinstance(x, (str, bytes)):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _px(x) -> float | None:
    v = _num(x)
    return None if v is None else round(v, C.CHART_PRICE_DECIMALS)


def _round(x, digits: int) -> float | None:
    v = _num(x)
    return None if v is None else round(v, digits)


def _iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _compact(v: float) -> float:
    """v with C.CHART_VALUE_SIG_DIGITS significant digits but at least C.CHART_PRICE_DECIMALS decimals."""
    if v == 0.0:
        return 0.0
    digits = C.CHART_VALUE_SIG_DIGITS - 1 - math.floor(math.log10(abs(v)))
    return round(v, max(C.CHART_PRICE_DECIMALS, digits))


def json_safe(obj, compact: bool = True):
    """Recursively convert to JSON-safe Python: NaN/inf -> None, numpy -> python, Timestamps ->
    "YYYY-MM-DD", tuples -> lists, dict keys -> str; floats go through _compact unless compact=False."""
    if isinstance(obj, dict):
        return {str(k): json_safe(v, compact) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v, compact) for v in obj]
    if isinstance(obj, np.ndarray):
        return [json_safe(v, compact) for v in obj.tolist()]
    if obj is None or obj is pd.NaT or isinstance(obj, (bool, str)):
        return None if obj is pd.NaT else obj
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        v = float(obj)
        if not math.isfinite(v):
            return None
        return _compact(v) if compact else v
    if isinstance(obj, (pd.Timestamp, np.datetime64)):
        return None if pd.isna(obj) else _iso(obj)
    return str(obj)


def _get(d, *path):
    """d[path[0]][path[1]]... with None for any missing step."""
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def _pairs(s: pd.Series, digits: int) -> list[list]:
    """[[date, value]] for the non-missing values of s."""
    v = s.astype(float)
    v = v[np.isfinite(v)]
    return [[_iso(d), round(float(x), digits)] for d, x in v.items()]


# ---------------------------------------------------------------------------- series
def _window(ctx) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(bars up to as_of, ind on the same index, last chart_bars bars of them)."""
    bars = ctx["bars"]
    if ctx.get("as_of"):
        bars = bars.loc[bars.index <= pd.Timestamp(ctx["as_of"])]
    ind = ctx["ind"].reindex(bars.index)
    return bars, ind, bars.iloc[-int(ctx["hz"]["chart_bars"]):]


def _candles(win: pd.DataFrame) -> list[list]:
    o, h, l, c = (np.round(win[k].to_numpy(dtype=float), C.CHART_PRICE_DECIMALS)
                  for k in ("open", "high", "low", "close"))
    v = np.nan_to_num(win["volume"].to_numpy(dtype=float), nan=0.0)
    return [[_iso(d), float(o[i]), float(h[i]), float(l[i]), float(c[i]), int(round(v[i]))]
            for i, d in enumerate(win.index)]


def _weekly(bars: pd.DataFrame, start: pd.Timestamp) -> list[list]:
    """W-FRI candles [[last trading date, o, h, l, c, v, first trading date, partial]] for the
    weeks ending on or after `start` (the first week may begin before the daily window)."""
    wk = I.resample_weekly(bars[["open", "high", "low", "close", "volume"]])
    first = bars.index.to_series().groupby(bars.index.to_period(C.WEEKLY_RULE)).min().to_numpy()
    out = []
    for i, (d, row) in enumerate(wk.iterrows()):
        if d < start:
            continue
        out.append([_iso(d), _px(row["open"]), _px(row["high"]), _px(row["low"]), _px(row["close"]),
                    int(round(_num(row["volume"]) or 0.0)), _iso(first[i]), bool(row["partial"])])
    return out


def _avwap_series(ctx, modules: dict, bars: pd.DataFrame, ind: pd.DataFrame,
                  start: pd.Timestamp, notes: list) -> tuple[dict, dict]:
    """({anchor: [[date, vwap]]} inside the window, {anchor: anchor date}).

    Uses modules.anchored_vwap_series; if that is unavailable, recomputes each series from the
    anchor dates reported by the volume_flow module.
    """
    anchors = {a["anchor"]: a.get("anchor_date") for a in _get(modules, "volume_flow", "anchored_vwaps") or []
               if isinstance(a, dict) and a.get("anchor")}
    try:
        raw = importlib.import_module("modules").anchored_vwap_series({**ctx, "bars": bars, "ind": ind})
    except Exception as exc:  # fall back to the module's anchors; the chart still renders without
        notes.append(f"anchored VWAP series rebuilt from volume_flow anchors ({type(exc).__name__})")
        raw = {name: I.anchored_vwap(bars, pd.Timestamp(d)) for name, d in anchors.items() if d}
    series, dates = {}, {}
    for name, s in raw.items():
        s = s.loc[s.index >= start]
        if s.notna().any():
            series[name] = _pairs(s, C.CHART_PRICE_DECIMALS)
            first = s.first_valid_index()
            dates[name] = anchors.get(name) or (_iso(first) if first is not None else None)
    return series, dates


def _rs_spy(ctx, bars: pd.DataFrame, start: pd.Timestamp) -> tuple[list | None, str | None]:
    """Stock / SPY total-return ratio on common dates, 100 at the first common date in the window."""
    if str(ctx.get("ticker", "")).upper() == C.SPY:
        return None, "SPY is the benchmark: no RS line"
    spy = ctx.get("spy")
    if spy is None or len(spy) == 0:
        return None, "SPY data unavailable: no RS line"
    spy = spy.loc[spy.index <= bars.index[-1]]
    tri = {k: (1.0 + I.daily_returns(df).fillna(0.0)).cumprod() for k, df in (("s", bars), ("b", spy))}
    j = pd.concat(tri, axis=1, join="inner").dropna()
    j = j.loc[j.index >= start]
    if len(j) < 2:
        return None, "fewer than 2 common dates with SPY in the chart window"
    ratio = j["s"] / j["b"]
    return _pairs(100.0 * ratio / ratio.iloc[0], 3), None


def _earnings(ctx, modules: dict, bars: pd.DataFrame, start: pd.Timestamp) -> list[dict]:
    """Past report dates inside the window (marker on the first bar on/after the date) and the next one."""
    if ctx.get("is_etf"):
        return []
    as_of = bars.index[-1]
    nxt = _get(modules, "event_risk", "next_earnings") or {}
    out, upcoming = [], []
    for r in ctx.get("earnings") or []:
        d = pd.to_datetime(r.get("date"), errors="coerce")
        if pd.isna(d):
            continue
        d = d.normalize()
        row = {"date": _iso(d), "eps_actual": _num(r.get("eps_actual")),
               "eps_estimated": _num(r.get("eps_estimated"))}
        if d > as_of or (d == as_of and row["eps_actual"] is None):
            upcoming.append(row)
        elif d >= start:
            row.update(kind="past", bar_date=_iso(bars.index[bars.index.searchsorted(d)]))
            out.append(row)
    upcoming.sort(key=lambda r: r["date"])
    if nxt.get("date"):
        out.append({"date": nxt["date"], "kind": "next", "eps_actual": None,
                    "eps_estimated": _num(nxt.get("eps_estimated")),
                    "calendar_days": nxt.get("calendar_days"), "trading_days_est": nxt.get("trading_days_est")})
    elif upcoming:
        nb = upcoming[0]
        out.append({**nb, "kind": "next",
                    "calendar_days": (pd.Timestamp(nb["date"]) - as_of).days, "trading_days_est": None})
    return sorted(out, key=lambda r: r["date"])


# ---------------------------------------------------------------------------- panels
def _levels(lv: dict) -> dict:
    if not lv.get("available"):
        return {"available": False, "reason": lv.get("reason") or "levels module unavailable",
                "supports": [], "resistances": []}
    def side(xs):
        return [{k: x.get(k) for k in LEVEL_KEYS} for x in xs or []]

    return {"available": True, "supports": side(lv.get("supports")), "resistances": side(lv.get("resistances")),
            "high_52w": lv.get("high_52w"), "low_52w": lv.get("low_52w"),
            "lookback_bars": lv.get("lookback_bars"), "window_start": lv.get("window_start"),
            "notes": lv.get("notes") or []}


def _node_zones(lv: dict) -> dict:
    """levels.node_zones (~3-year node zones, reference only). The side follows the position: the
    zones above the close are resistance, the one holding it and those below support."""
    nz = lv.get("node_zones") or {}
    if not nz.get("available"):
        return {"available": False,
                "reason": nz.get("reason") or ("node zones unavailable" if lv else "levels module unavailable")}

    def rows(xs, side):
        return [{**{k: x.get(k) for k in NODE_KEYS}, "side": side} for x in xs or [] if isinstance(x, dict)]

    at = rows([nz.get("at_close")] if nz.get("at_close") else [], "support")
    return {"available": True, "lookback": NZ.LOOKBACK, "lookback_bars": nz.get("lookback_bars"),
            "window_start": nz.get("window_start"),
            "above": rows(nz.get("above"), "resistance"), "at_close": at[0] if at else None,
            "below": rows(nz.get("below"), "support"), "note": _get(nz, "evidence", "note")}


def _volume_profile(lv: dict) -> dict:
    vp = lv.get("volume_profile") or {}
    if not vp.get("available"):
        return {"available": False, "reason": vp.get("reason") or lv.get("reason") or "volume profile unavailable"}
    return {"available": True, "poc": _px(vp["poc"]), "val": _px(vp["value_area_low"]),
            "vah": _px(vp["value_area_high"]),
            "bins": [[_px(b["low"]), _px(b["high"]), int(round(b["volume"]))] for b in vp["bins"]],
            "hvn": [_px(p) for p in vp.get("hvn") or []], "lvn": [_px(p) for p in vp.get("lvn") or []],
            "lookback_bars": vp.get("lookback_bars"), "window_start": lv.get("window_start"),
            "note": PROFILE_NOTE}


def _regime(modules: dict) -> dict:
    rg, mtf = modules.get("regime") or {}, modules.get("multi_timeframe") or {}
    if not rg.get("available"):
        return {"available": False, "reason": rg.get("reason") or "regime module unavailable"}
    return {"available": True, "label": rg.get("label"), "type": rg.get("type"),
            "direction": _get(rg, "direction", "label"), "direction_sign": rg.get("direction_sign"),
            "strength": _get(rg, "strength", "label"), "adx": _get(rg, "strength", "adx"),
            "vol_label": _get(rg, "volatility", "label"), "vol_pctl": _get(rg, "volatility", "vol_pctl"),
            "rv20": _get(rg, "volatility", "rv20"), "compression": _get(rg, "volatility", "compression"),
            "weekly_direction": _get(mtf, "weekly", "direction"), "weekly_sign": _get(mtf, "weekly", "sign"),
            "alignment": mtf.get("alignment"),
            "mtf_note": mtf.get("note")}


def _scorecard(modules: dict) -> dict:
    sc = modules.get("scorecard") or {}
    pillars = sc.get("pillars") or {}
    if not pillars:
        return {"available": False, "reason": sc.get("reason") or "scorecard unavailable"}
    weights = sc.get("weights") or {}
    order = [p for p in C.PILLARS if p in pillars] + [p for p in pillars if p not in C.PILLARS]
    conf = sc.get("confidence") or {}
    # reasons stay in English; the structured fields (votes, inputs, factors, key/params) let
    # ui/i18n.js write them in Chinese
    pillar_keys = ("score", "raw_score", "reason", "available", "mode", "votes", "inputs", "missing",
                   "not_applicable", "nudges")
    return {
        "available": True,
        "pillars": [{"name": p, **{k: _get(pillars, p, k) for k in pillar_keys}, "weight": weights.get(p)}
                    for p in order],
        "weighted_score": sc.get("weighted_score"), "bias": sc.get("bias"),
        "confidence": {"level": conf.get("level"), "reasons": conf.get("reasons") or [],
                       "factors": conf.get("factors") or []},
        "conflicts": [{k: c.get(k) for k in ("type", "key", "params", "description", "typical_meaning")}
                      if isinstance(c, dict) else {"description": str(c), "typical_meaning": None}
                      for c in sc.get("conflicts") or []],
        "consts": {"slope_bars": C.SLOPE_BARS, "percentile_window": C.PERCENTILE_WINDOW,
                   "divergence_lookback": C.DIVERGENCE_LOOKBACK},
    }


def _active_table(sg: dict, primary: int) -> list[dict]:
    """Rows for an active-signal table (indicator signals, price-action setups, candlestick patterns)."""
    ph = int(sg.get("primary_horizon") or primary)
    rows = []
    for a in sg.get("active") or []:
        hs = a.get("horizons") or {}
        h = hs.get(ph) or hs.get(str(ph)) or {}
        rows.append({
            "id": a.get("id"), "name": a.get("name"), "bias": a.get("bias"), "pillar": a.get("pillar"),
            "fired_date": a.get("fired_date"), "bars_ago": a.get("bars_ago"), "regime_used": a.get("regime_used"),
            "n": h.get("n"), "hit_rate": h.get("hit_rate"), "median": h.get("median"),
            "p25": h.get("p25"), "p75": h.get("p75"), "median_mae": h.get("median_mae"),
            "base_median": _get(h, "same_regime", "median"), "base_hit_rate": _get(h, "same_regime", "hit_rate"),
            "edge_median": _get(h, "edge", "median"), "edge_hit_rate": _get(h, "edge", "hit_rate"),
            "confidence": a.get("confidence") or h.get("confidence"),
        })
    return rows


def _signals(modules: dict, primary: int) -> dict:
    sg = modules.get("signals") or {}
    if not sg.get("available"):
        return {"available": False, "reason": sg.get("reason") or "signal statistics unavailable",
                "primary_horizon": primary, "active": []}
    ph = int(sg.get("primary_horizon") or primary)
    return {"available": True, "primary_horizon": ph, "active": _active_table(sg, primary), "history": sg.get("history")}


def _pattern_block(name: str, payload, ctx, modules: dict, win: pd.DataFrame, primary: int, notes: list) -> dict:
    """Chart 2 / chart 3 payload: the module's chart series plus its active and library tables."""
    mod = modules.get(name) or {}
    try:
        out = payload(ctx, modules, win.index)
    except Exception as exc:  # the main chart stands without this one
        notes.append(f"{name} chart data failed ({type(exc).__name__}: {exc})")
        return {"available": False, "reason": f"chart data failed: {type(exc).__name__}"}
    if not out.get("available"):
        return out
    out["primary_horizon"] = int(mod.get("primary_horizon") or primary)
    out["active"] = _active_table(mod, primary)
    out["library"] = [{k: r.get(k) for k in LIBRARY_KEYS} for r in mod.get("library") or []]
    for key in ("recent", "last_doji"):
        if key in mod:
            out[key] = mod[key]
    return out


def _key_numbers(bars: pd.DataFrame, ind: pd.DataFrame, modules: dict, levels: dict, earnings: list) -> dict:
    close = _num(bars["close"].iloc[-1])
    prev = _num(bars["close"].iloc[-2]) if len(bars) > 1 else None
    row = ind.iloc[-1]
    base = _get(modules, "scenarios", "base") or {}
    nxt = next((e for e in earnings if e["kind"] == "next"), None)
    win52 = bars.iloc[-C.HIGH_52W_BARS:]
    return {
        "price": _px(close), "prev_close": _px(prev),
        "change_pct": None if close is None or not prev else close / prev - 1.0,
        "atr14": _px(row.get("atr14")), "atr_pct": _num(row.get("atr_pct")),
        "rsi14": _round(row.get("rsi14"), 2), "rvol": _round(row.get("rvol50"), 3),
        "adx": _round(row.get("adx14"), 2),
        "high_52w": _px(levels.get("high_52w") if levels.get("available") else win52["high"].max()),
        "low_52w": _px(levels.get("low_52w") if levels.get("available") else win52["low"].min()),
        "next_earnings": nxt,
        "expected_move_1sd_pct": base.get("expected_move_1sd_pct"),
        "expected_move_horizon": _get(modules, "scenarios", "horizon_days"),
    }


def _data_notes(ctx, modules: dict, extra: list) -> dict:
    dq = ctx.get("data_quality") or {}
    skipped = [{"module": str(k), "reason": str(v)} for k, v in (ctx.get("skipped") or {}).items()]
    seen = {s["module"] for s in skipped}
    for name in MODULE_NAMES:
        m = modules.get(name)
        if isinstance(m, dict) and m.get("available") is False and name not in seen:
            skipped.append({"module": name, "reason": str(m.get("reason") or "unavailable")})
    stale = dq.get("stale") or {}
    notes = list(dict.fromkeys([*(ctx.get("notes") or []), *(dq.get("warnings") or []), *extra]))
    return {
        "partial_bar": dq.get("partial_bar_excluded"),
        "stale": stale if stale.get("is_stale") else None,
        "illiquid": _get(dq, "liquidity", "reason") if _get(dq, "liquidity", "illiquid") else None,
        "skipped": skipped,
        "notes": notes,
        "approximations": [VWAP_NOTE, PROFILE_NOTE],
    }


def _data_source(ctx) -> str:
    if ctx.get("data_source"):
        return str(ctx["data_source"])
    route = str(_get(ctx, "connection", "route") or "")
    if route.startswith("third-party"):
        return "FMP stable API via " + route
    return "FMP stable API (official)" if route else "FMP stable API"


# ---------------------------------------------------------------------------- public
def build_chart_data(ctx, modules: dict) -> dict:
    """JSON-safe chart payload for ui/chart.js (see the module docstring)."""
    modules = modules or {}
    hz = ctx["hz"]
    bars, ind, win = _window(ctx)
    if win.empty:
        raise ValueError("no bars to chart")
    start = win.index[0]
    iw = ind.loc[win.index]
    notes: list[str] = []

    avwaps, avwap_anchors = _avwap_series(ctx, modules, bars, ind, start, notes)
    rs, rs_note = _rs_spy(ctx, bars, start)
    lv = modules.get("levels") or {}
    levels = _levels(lv)
    earnings = _earnings(ctx, modules, bars, start)
    profile = ctx.get("profile") or {}
    primary = int(hz["primary"])

    data = {
        "meta": {
            "ticker": ctx.get("ticker"), "name": profile.get("companyName"), "as_of": ctx.get("as_of") or _iso(bars.index[-1]),
            "horizon": ctx.get("horizon"), "primary_horizon": primary, "stat_horizons": list(hz["stat_horizons"]),
            "data_source": _data_source(ctx), "is_etf": bool(ctx.get("is_etf")), "sector_etf": ctx.get("sector_etf"),
            "currency": profile.get("currency"), "exchange": profile.get("exchange"),
            "chart_bars": len(win), "disclaimer": C.DISCLAIMER,
        },
        "default_timeframe": hz.get("chart_timeframe", "daily"),
        "candles": _candles(win),
        "weekly": _weekly(bars, start),
        "sma20": _pairs(iw["sma20"], C.CHART_PRICE_DECIMALS),
        "sma50": _pairs(iw["sma50"], C.CHART_PRICE_DECIMALS),
        "sma200": _pairs(iw["sma200"], C.CHART_PRICE_DECIMALS),
        "avwaps": avwaps,
        "avwap_anchors": avwap_anchors,
        "levels": levels,
        "volume_profile": _volume_profile(lv) if lv else {"available": False, "reason": "levels module unavailable"},
        "node_zones": _node_zones(lv),
        "rvol": _pairs(iw["rvol50"], 3),
        "rsi14": _pairs(iw["rsi14"], 2),
        "rs_spy": rs,
        "rs_spy_note": rs_note,
        "earnings_dates": earnings,
        "regime": _regime(modules),
        "scorecard": _scorecard(modules),
        "signals": _signals(modules, primary),
        "price_action": _pattern_block("price_action", PA.chart_payload, ctx, modules, win, primary, notes),
        "candlesticks": _pattern_block("candlesticks", CS.chart_payload, ctx, modules, win, primary, notes),
        "key_numbers": _key_numbers(bars, ind, modules, levels, earnings),
    }
    data["data_notes"] = _data_notes(ctx, modules, notes)
    return json_safe(data)


def _inline(text: str, tag: str) -> str:
    """Text safe inside <tag>...</tag>: an embedded closing tag would end the element early."""
    return text.replace(f"</{tag}", f"<\\/{tag}").replace(f"</{tag.upper()}", f"<\\/{tag.upper()}")


def embed_json(obj) -> str:
    """JSON for a <script type="application/json"> block: <, > and & become \\u escapes, so the
    text can never close the element or open a comment, and JSON.parse returns the same data."""
    text = json.dumps(json_safe(obj), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def write_chart_html(chart_data: dict, path) -> Path:
    """Write the self-contained chart page (UTF-8) and return its path."""
    css = (UI_DIR / "chart.css").read_text(encoding="utf-8")
    scripts = [(UI_DIR / name).read_text(encoding="utf-8") for name in ("i18n.js", "chart.js")]
    meta = chart_data.get("meta") or {}
    title = html.escape(f"{meta.get('ticker') or ''} 技术分析 · {meta.get('as_of') or ''}")
    page = "\n".join([
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{title}</title>",
        f"<style>\n{_inline(css, 'style')}\n</style>",
        "</head>",
        "<body>",
        '<div class="ta-wrap">',
        '  <div class="ta-top"><span class="ta-app" data-i18n="app_title"></span>'
        '<button type="button" class="ta-lang" id="ta-lang"></button></div>',
        '  <div id="ta-root"></div>',
        "</div>",
        f'<script type="application/json" id="ta-data">{embed_json(chart_data)}</script>',
        *(f"<script>\n{_inline(js, 'script')}\n</script>" for js in scripts),
        "<script>TAChart.boot(document.getElementById(\"ta-root\"), "
        "JSON.parse(document.getElementById(\"ta-data\").textContent));</script>",
        "</body>",
        "</html>",
        "",
    ])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path
