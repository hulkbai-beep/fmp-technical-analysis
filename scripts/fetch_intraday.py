#!/usr/bin/env python3
"""
Save FMP intraday bars as one CSV per symbol, for intraday_levels.py.

  python scripts/fetch_intraday.py --out DIR [--interval 1hour] [--start 2010-01-01]
                                   [--symbols AAPL MSFT ...] [--refresh]

Without --symbols it fetches fetch_csv.BASELINE_UNIVERSE. Each file is DIR/<SYMBOL>.csv with
Date (bar start, US Eastern exchange time, regular session only), Open, High, Low, Close and
Volume, oldest first, from historical-chart/<interval>. Requests cover CHUNK_DAYS calendar days each
(well under one response's size), so 1hour from 2010 is about 100 requests per symbol. The prices are
as FMP returns them; intraday_levels.py checks them against the daily CSVs for split adjustment.
An existing file is kept unless --refresh is given. The CSVs hold prices only, never the API key.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

import fetch as F
import fetch_csv as FC
import fmp_client

CHUNK_DAYS = 61
FIELDS = ["open", "high", "low", "close", "volume"]


def fetch_symbol(client, symbol: str, interval: str, start, end) -> tuple[pd.DataFrame, list[str]]:
    """All bars from start to end in CHUNK_DAYS windows; (frame, problems)."""
    rows, problems = [], []
    frm = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    while frm <= end:
        to = min(end, frm + pd.Timedelta(days=CHUNK_DAYS - 1))
        data, problem = fmp_client.get_with_reason(client, f"historical-chart/{interval}", symbol=symbol,
                                                   **{"from": f"{frm:%Y-%m-%d}", "to": f"{to:%Y-%m-%d}"})
        if problem:
            problems.append(f"{frm:%Y-%m-%d}..{to:%Y-%m-%d}: {problem}")
            if problem.startswith("restricted"):
                break
        else:
            rows.extend(F._rows(data))
        frm = to + pd.Timedelta(days=1)
    if not rows:
        return pd.DataFrame(columns=[c.capitalize() for c in FIELDS]), problems
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index(kind="mergesort")
    df = df[~df.index.duplicated(keep="last")][FIELDS].apply(pd.to_numeric, errors="coerce").dropna(subset=FIELDS[:4])
    df.index.name = "Date"
    return df.rename(columns=str.capitalize), problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", default="1hour", choices=["1hour", "30min", "15min", "5min"])
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args(argv)
    symbols = a.symbols or [s for g in FC.BASELINE_UNIVERSE.values() for s in g]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    client = fmp_client.FMPClient()
    end = pd.Timestamp.today().normalize()
    failed = []
    for sym in symbols:
        path = out / f"{sym.upper()}.csv"
        if path.exists() and not a.refresh:
            print(f"{sym}: kept {path}")
            continue
        df, problems = fetch_symbol(client, sym.upper(), a.interval, a.start, end)
        if df.empty:
            failed.append(sym)
            print(f"{sym}: no data ({'; '.join(problems[:2])})", file=sys.stderr)
            continue
        df.to_csv(path, float_format="%.6f")
        note = f" ({len(problems)} chunk problems, first: {problems[0]})" if problems else ""
        print(f"{sym}: {len(df)} bars {df.index[0]:%Y-%m-%d %H:%M}..{df.index[-1]:%Y-%m-%d %H:%M}{note}")
    print(f"network calls: {client.stats['network_calls']}, cache hits: {client.stats['cache_hits']}")
    if failed:
        print(f"failed: {' '.join(failed)}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
