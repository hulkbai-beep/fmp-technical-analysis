#!/usr/bin/env python3
"""
Save split-adjusted daily bars from FMP as one CSV per symbol, for levels_backtest.py.

  python scripts/fetch_csv.py --out DIR [--start 2005-01-01] [--symbols AAPL MSFT ...] [--refresh]

Without --symbols it fetches BASELINE_UNIVERSE (32 symbols across trends, ranges, high volatility
and ETFs). Each file is DIR/<SYMBOL>.csv with Date, Open, High, Low, Close and Volume, oldest first,
from historical-price-eod/full (split-adjusted, the basis the skill uses). An existing file is kept
unless --refresh is given, so a re-run only fetches what is missing. The CSVs hold prices only, never
the API key. About two requests per symbol from 2005.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

import fetch as F
import fmp_client

# Rough character of each group over 2005-2026; the backtest splits by the direction at each build
# bar, so a symbol's group only makes sure every kind of market is in the sample.
BASELINE_UNIVERSE = {
    "long uptrend": ["NVDA", "AAPL", "MSFT", "COST", "LLY", "AVGO"],
    "long decline": ["INTC", "PFE", "WBA", "VZ", "BA", "NKE"],
    "mostly sideways": ["KO", "PG", "JNJ", "PEP", "XOM", "MMM", "T"],
    "high volatility": ["TSLA", "AMD", "MRNA", "PLTR", "COIN", "SMCI"],
    "etf": ["SPY", "QQQ", "IWM", "XLE", "XLU", "GLD", "TLT"],
}


def bars_csv(rows: list) -> pd.DataFrame:
    """FMP /full rows -> Date-indexed OHLCV, oldest first, one row per date."""
    df = F._frame(rows, F.FULL_FIELDS).sort_index(kind="mergesort")
    df = df[~df.index.duplicated(keep="last")].dropna(subset=F.PRICE)
    df.index.name = "Date"
    return df.rename(columns=str.capitalize)[["Open", "High", "Low", "Close", "Volume"]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--refresh", action="store_true", help="fetch again even if the CSV exists")
    a = ap.parse_args(argv)
    symbols = a.symbols or [s for group in BASELINE_UNIVERSE.values() for s in group]
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
        rows, problem = F.fetch_history(client, F.FULL, sym.upper(), a.start, end)
        df = bars_csv(rows)
        if df.empty:
            failed.append(sym)
            print(f"{sym}: no data ({problem})", file=sys.stderr)
            continue
        df.to_csv(path, float_format="%.6f")
        note = f" ({problem})" if problem else ""
        print(f"{sym}: {len(df)} bars {df.index[0]:%Y-%m-%d}..{df.index[-1]:%Y-%m-%d}{note}")
    print(f"network calls: {client.stats['network_calls']}, cache hits: {client.stats['cache_hits']}")
    if failed:
        print(f"failed: {' '.join(failed)}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
