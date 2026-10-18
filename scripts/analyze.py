#!/usr/bin/env python3
"""
Technical analysis of one US-listed stock or ETF: fetch FMP data, run every module, write
metrics.json (every computed number) and a self-contained chart.html.

  python scripts/analyze.py TICKER [--horizon swing|position] [--out DIR] [--refresh]

  --horizon   swing (default: daily focus, 5/10/20-day stats, ~6-month levels) or
              position (weekly emphasis, 20/60-day stats, ~2-year levels)
  --out       output folder (default ./ta-output/<TICKER>_<as_of>/)
  --refresh   ignore the local cache and fetch everything again

Exit codes: 0 done; 1 the analysis could not run (unknown ticker, no data, FMP or network
error); 2 configuration or usage problem (missing or rejected API key, bad arguments).
The report itself (report.md) is written by Claude from metrics.json using
references/report_template.md. The API key is never printed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import platform
import sys
import time
import traceback
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd

import chart
import config as C
import fetch
import fmp_client
import indicators as I
import levels
import modules as M
import regime as R
import scorecard as SC
import signal_stats as SS

# Module builders that take ctx only, in metrics.json order (scorecard, scenarios, risk follow).
BUILDERS = (
    ("market_context", R.build_market_context),
    ("regime", R.build_regime),
    ("trend_momentum", M.build_trend_momentum),
    ("relative_strength", M.build_relative_strength),
    ("mean_reversion", M.build_mean_reversion),
    ("volume_flow", M.build_volume_flow),
    ("volatility", M.build_volatility),
    ("levels", levels.build_levels),
    ("multi_timeframe", R.build_multi_timeframe),
    ("event_risk", M.build_event_risk),
    ("signals", SS.build_signal_stats),
)
MODULE_KEYS = tuple(name for name, _ in BUILDERS) + ("scorecard", "scenarios", "risk")
TOP_KEYS = ("ticker", "as_of", "data_source", "horizon", "snapshot", "data_quality",
            *MODULE_KEYS, "meta")
SCRIPTS_DIR = Path(__file__).resolve().parent


def _iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------- JSON
def json_clean(obj, sig: int = C.METRICS_SIG_DIGITS):
    """Plain-JSON copy of obj: NaN/inf -> None, numpy -> Python, Timestamps/dates -> "YYYY-MM-DD",
    tuples/sets -> lists, dict keys -> str, floats rounded to `sig` significant digits."""
    if isinstance(obj, dict):
        return {str(k): json_clean(v, sig) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [json_clean(v, sig) for v in obj]
    if isinstance(obj, np.ndarray):
        return json_clean(obj.tolist(), sig)
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        v = float(obj)
        return float(f"{v:.{sig}g}") if math.isfinite(v) else None
    if obj is pd.NaT or (isinstance(obj, np.datetime64) and np.isnat(obj)):
        return None
    if isinstance(obj, (pd.Timestamp, np.datetime64, dt.date)):
        return pd.Timestamp(obj).strftime("%Y-%m-%d")
    return str(obj)


def dumps(metrics: dict) -> str:
    return json.dumps(metrics, ensure_ascii=False, indent=1, allow_nan=False)


# ---------------------------------------------------------------------------- pieces
def data_source(connection: dict) -> str:
    """"FMP stable API (official)" or "FMP stable API via third-party proxy <host>"."""
    base = str((connection or {}).get("base") or "")
    host = urllib.parse.urlparse(base).hostname or base or "unknown host"
    if host.lower() in fmp_client.OFFICIAL_HOSTS:
        return "FMP stable API (official)"
    return f"FMP stable API via third-party proxy {host}"


def _snapshot(ctx: dict) -> dict:
    bars, profile, quote = ctx["bars"], ctx.get("profile") or {}, ctx.get("quote") or {}
    close = float(bars["close"].iloc[-1])
    prev = float(bars["close"].iloc[-2]) if len(bars) > 1 else None
    ts = quote.get("timestamp")
    quote_time = None
    if isinstance(ts, (int, float)) and math.isfinite(ts) and ts > 0:
        quote_time = pd.Timestamp(ts, unit="s", tz="UTC").tz_convert(C.MARKET_TZ).isoformat()
    return {
        "price": close,
        "price_date": ctx["as_of"],
        "prev_close": prev,
        "change_1d_pct": None if not prev else close / prev - 1.0,
        "quote_price": quote.get("price"),
        "quote_time": quote_time,
        "name": profile.get("companyName") or quote.get("name"),
        "exchange": profile.get("exchange") or quote.get("exchange"),
        "sector": None if ctx.get("is_etf") else profile.get("sector"),   # FMP labels SPY "Financial Services"
        "industry": None if ctx.get("is_etf") else profile.get("industry"),
        "is_etf": bool(ctx.get("is_etf")),
        "currency": profile.get("currency"),
        "sector_etf": ctx.get("sector_etf"),
        "benchmark": C.SPY,
        "history": {"first_date": _iso(bars.index[0]), "bars": int(len(bars))},
        "note": "price = last complete daily close (split-adjusted); quote_price is FMP's latest quote "
                "and can be intraday",
    }


def config_snapshot(horizon: str) -> dict:
    """The key parameters behind this run (all of them live in scripts/config.py)."""
    return {
        "horizon": {"name": horizon, **C.HORIZONS[horizon]},
        "data": {"history_start": C.HISTORY_START, "eod_row_cap": C.EOD_ROW_CAP, "chunk_years": C.CHUNK_YEARS,
                 "benchmark_years": C.BENCHMARK_YEARS, "market_close_buffer": C.MARKET_CLOSE_BUFFER,
                 "stale_bdays": C.STALE_BDAYS},
        "indicators": {"sma": list(C.SMA_PERIODS), "slope_bars": C.SLOPE_BARS, "rsi": [C.RSI_PERIOD, C.RSI_SHORT_PERIOD],
                       "atr": C.ATR_PERIOD, "adx": C.ADX_PERIOD, "macd": [C.MACD_FAST, C.MACD_SLOW, C.MACD_SIGNAL],
                       "bollinger": [C.BB_PERIOD, C.BB_STD], "keltner": [C.KC_PERIOD, C.KC_ATR_PERIOD, C.KC_MULT],
                       "rvol": C.RVOL_PERIOD, "realized_vol": C.REALIZED_VOL_PERIOD,
                       "percentile_window": C.PERCENTILE_WINDOW},
        "regime": {"adx_trend": C.ADX_TREND, "adx_range": C.ADX_RANGE, "vol_low_pctl": C.VOL_LOW_PCTL,
                   "vol_high_pctl": C.VOL_HIGH_PCTL, "vol_extreme_pctl": C.VOL_EXTREME_PCTL,
                   "compression_bw_pctl": C.COMPRESSION_BW_PCTL},
        "levels": {"fractal_k": C.FRACTAL_K, "profile_bins": C.PROFILE_BINS, "value_area": C.VALUE_AREA_SHARE,
                   "merge_atr": C.LEVEL_MERGE_ATR, "touch_atr": C.TOUCH_ATR, "per_side": C.LEVELS_PER_SIDE},
        "signals": {"active_bars": C.SIGNAL_ACTIVE_BARS, "params": dict(C.SIGNAL_PARAMS),
                    "confidence_n_normal": C.CONFIDENCE_N_NORMAL, "confidence_n_low": C.CONFIDENCE_N_LOW,
                    "edge_min": C.SIGNAL_EDGE_MIN, "hit_edge_min": C.SIGNAL_HIT_EDGE_MIN},
        "scorecard": {"bias_steps": [{"label": label, "bound": bound} for bound, label in C.BIAS_STEPS],
                      "position_weight_shift": dict(C.POSITION_WEIGHT_SHIFT),
                      "conf_agreement_high": C.CONF_AGREEMENT_HIGH, "conf_agreement_low": C.CONF_AGREEMENT_LOW},
        "risk": {"trigger_rvol": C.TRIGGER_RVOL, "stop_atr_mult": C.STOP_ATR_MULT,
                 "structure_stop_buffer_atr": C.STRUCTURE_STOP_BUFFER_ATR, "account_size": C.ACCOUNT_SIZE,
                 "risk_per_trade": C.RISK_PER_TRADE},
    }


def _safe(name: str, fn, errors: list, *args) -> dict:
    """fn(*args); an exception becomes {"available": False, "reason": "internal error: ..."} and an
    entry in `errors`, so one failing module does not stop the others."""
    try:
        return fn(*args)
    except Exception as exc:  # reported in meta.errors and the module's reason
        frames = traceback.extract_tb(exc.__traceback__)
        ours = [f for f in frames if Path(f.filename).resolve().parent == SCRIPTS_DIR]
        frame = (ours or frames)[-1]
        msg = f"{type(exc).__name__}: {exc} ({Path(frame.filename).name}:{frame.lineno})"
        errors.append(f"{name}: {msg}")
        print(f"[analyze] {name} failed: {msg}", file=sys.stderr)
        return {"available": False, "reason": f"internal error: {msg}", "notes": []}


def _unavailable(modules: dict) -> dict:
    """{module: reason} for every module or module block reported as unavailable."""
    out = {}
    for name, m in modules.items():
        if not isinstance(m, dict):
            continue
        if m.get("available") is False:
            out[name] = m.get("reason") or "unavailable"
            continue
        for key, block in m.items():
            if isinstance(block, dict) and block.get("available") is False and block.get("reason"):
                out[f"{name}.{key}"] = block["reason"]
    return out


# ---------------------------------------------------------------------------- analysis
def run_analysis(ticker: str, horizon: str = C.DEFAULT_HORIZON, client=None, now=None,
                 refresh: bool = False) -> tuple[dict, dict | None]:
    """Fetch, compute every module and return (metrics, chart_data).

    metrics is JSON-ready with the top-level keys in TOP_KEYS order; chart_data is the
    chart.build_chart_data payload (None, with an entry in meta.errors, if the chart failed).
    Raises ValueError for an unknown horizon or a symbol without price data, and lets
    fmp_client.FMPError / InvalidKey propagate.
    """
    symbol = (ticker or "").strip().upper()
    if not symbol:
        raise ValueError("no ticker given")
    if horizon not in C.HORIZONS:
        raise ValueError(f"unknown horizon {horizon!r}: use {' or '.join(C.HORIZONS)}")
    started = time.time()
    client = client if client is not None else fmp_client.FMPClient(refresh=refresh)
    calls0, hits0 = client.stats["network_calls"], client.stats["cache_hits"]
    restricted0, errors0 = len(client.restricted), len(client.errors)

    data = fetch.load_all(client, symbol, now=now)
    bars = data["bars"]
    as_of = _iso(bars.index[-1])
    source = data_source(data["connection"])
    ctx = {
        "ticker": symbol, "as_of": as_of, "horizon": horizon, "hz": C.HORIZONS[horizon],
        "bars": bars, "ind": I.compute_all(bars),
        **{k: data[k] for k in ("spy", "sector", "sector_etf", "vix", "profile", "quote", "earnings", "is_etf",
                                "data_quality", "notes", "skipped", "connection")},
        "data_source": source,
    }

    errors: list[str] = []
    mods = {name: _safe(name, fn, errors, ctx) for name, fn in BUILDERS}
    mods["scorecard"] = _safe("scorecard", SC.build_scorecard, errors, ctx, mods)
    mods["scenarios"] = _safe("scenarios", SC.build_scenarios, errors, ctx, mods, mods["scorecard"])
    mods["risk"] = _safe("risk", SC.build_risk, errors, ctx, mods, mods["scenarios"])
    chart_data = None
    try:
        chart_data = chart.build_chart_data(ctx, mods)
    except Exception as exc:  # the metrics stand without the chart
        errors.append(f"chart: {type(exc).__name__}: {exc}")
        print(f"[analyze] chart failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    meta = {
        "network_calls": client.stats["network_calls"] - calls0,
        "cache_hits": client.stats["cache_hits"] - hits0,
        "restricted": list(client.restricted[restricted0:]),
        "errors": list(client.errors[errors0:]) + errors,
        "skipped": dict(data["skipped"]),
        "modules_unavailable": _unavailable(mods),
        "notes": list(data["notes"]),
        "connection": data["connection"],
        "fetched_at": data.get("fetched_at"),
        "runtime_seconds": round(time.time() - started, 2),
        "versions": {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__},
        "config_snapshot": config_snapshot(horizon),
        "disclaimer": C.DISCLAIMER,
    }
    metrics = {"ticker": symbol, "as_of": as_of, "data_source": source, "horizon": horizon,
               "snapshot": _snapshot(ctx), "data_quality": data["data_quality"], **mods, "meta": meta}
    return json_clean({k: metrics[k] for k in TOP_KEYS}), chart_data


# ---------------------------------------------------------------------------- output
def default_out_dir(ticker: str, as_of: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in ticker)
    return Path("ta-output") / f"{safe}_{as_of}"


def write_outputs(metrics: dict, chart_data: dict | None, out_dir) -> dict:
    """Write metrics.json (and chart.html when there is chart data); returns their paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"metrics": out / "metrics.json", "chart": None}
    paths["metrics"].write_text(dumps(metrics) + "\n", encoding="utf-8")
    if chart_data is not None:
        paths["chart"] = chart.write_chart_html(chart_data, out / "chart.html")
    return paths


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def summary(metrics: dict, paths: dict) -> str:
    """Short plain-text summary of a run for the terminal."""
    m, meta = metrics, metrics["meta"]
    snap, rg, sc = m["snapshot"], m["regime"], m["scorecard"]
    lines = [f"{m['ticker']}  {m['horizon']}  as of {m['as_of']}  close {snap['price']} "
             f"({_pct(snap['change_1d_pct'])})  [{m['data_source']}]",
             f"  metrics.json : {paths['metrics']}",
             f"  chart.html   : {paths['chart'] or 'not written (see errors)'}",
             f"  regime       : {rg.get('label') or rg.get('reason')}"
             + (f"  (type {rg['type']})" if rg.get("type") else "")]
    if sc.get("available", True) and sc.get("bias"):
        conf = (sc.get("confidence") or {}).get("level")
        lines.append(f"  bias         : {sc['bias']} (weighted score {sc['weighted_score']:+.2f}), confidence {conf}")
    else:
        lines.append(f"  bias         : unavailable ({sc.get('reason')})")
    sg = m["signals"]
    primary = str(sg.get("primary_horizon"))
    active = [f"{a['id']} (n={(a['horizons'].get(primary) or {}).get('n')}, {a['confidence']})"
              for a in sg.get("active") or []]
    lines.append(f"  signals      : {', '.join(active) if active else 'none active'}")
    lines.append(f"  API calls    : {meta['network_calls']} network, {meta['cache_hits']} from cache")
    skipped = {**meta["skipped"], **{k: v for k, v in meta["modules_unavailable"].items() if "." not in k}}
    if skipped:
        lines.append("  skipped      : " + "; ".join(f"{k} ({v})" for k, v in skipped.items()))
    dq = m["data_quality"]
    if dq.get("partial_bar_excluded"):
        lines.append(f"  WARNING      : {dq['partial_bar_excluded']['reason']}")
    if (dq.get("stale") or {}).get("is_stale"):
        st = dq["stale"]
        lines.append(f"  WARNING      : stale data, last bar {st['last_bar']} is {st['business_days_behind']} "
                     f"business days behind {st['expected_session']}")
    if (dq.get("liquidity") or {}).get("illiquid"):
        lines.append(f"  WARNING      : illiquid ({dq['liquidity']['reason']})")
    for e in meta["errors"]:
        lines.append(f"  error        : {e}")
    return "\n".join(lines)


def _redact(text: str) -> str:
    key = os.environ.get("FMP_API_KEY", "").strip()
    return text.replace(key, "***") if key else text


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Technical analysis of one US stock or ETF from FMP data.")
    ap.add_argument("ticker", help="e.g. AAPL, BRK.B, SPY")
    ap.add_argument("--horizon", choices=tuple(C.HORIZONS), default=C.DEFAULT_HORIZON)
    ap.add_argument("--out", help="output folder (default ./ta-output/<TICKER>_<as_of>/)")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache and fetch everything again")
    args = ap.parse_args(argv)
    symbol = args.ticker.strip().upper()
    try:
        metrics, chart_data = run_analysis(symbol, args.horizon, refresh=args.refresh)
    except fmp_client.InvalidKey as e:
        print(f"API key problem: {_redact(str(e))}", file=sys.stderr)
        return 2
    except fmp_client.FMPError as e:
        print(f"FMP error: {_redact(str(e))}\nRun `python scripts/fmp_client.py check` to test the connection.",
              file=sys.stderr)
        return 1
    except (ValueError, LookupError) as e:
        msg = _redact(str(e))
        hint = ("Run `python scripts/fmp_client.py check` to test the connection and your plan."
                if "error:" in msg or "restricted:" in msg else
                "Check the symbol: a US-listed stock or ETF; share classes use a dot, e.g. BRK.B.")
        print(f"Cannot analyse {symbol}: {msg}\n{hint}", file=sys.stderr)
        return 1
    except Exception as e:  # unexpected: show where, never the key
        print(_redact(traceback.format_exc()), file=sys.stderr)
        print(f"Unexpected error: {_redact(f'{type(e).__name__}: {e}')}", file=sys.stderr)
        return 1
    paths = write_outputs(metrics, chart_data, args.out or default_out_dir(symbol, metrics["as_of"]))
    print(summary(metrics, paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
