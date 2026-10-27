#!/usr/bin/env python3
"""
Save quarterly financial statements from FMP, for the support-zone x fundamentals test.

  python scripts/fetch_fundamentals.py --out DIR --symbols AAPL MSFT ... [--limit 120]
                                       [--as-reported AAPL GE ...]

For each symbol writes DIR/<SYMBOL>.json = {"income": [...], "balance": [...], "cashflow": [...]}
from income-statement, balance-sheet-statement and cash-flow-statement (period=quarter). The rows
carry filingDate / acceptedDate, which fundamentals_zones.py uses as the point in time a quarter
became public. FMP's standardized statements can include later restatements; --as-reported also
saves income-statement-as-reported for the listed symbols (DIR/asreported/<SYMBOL>.json) so the
size of that difference can be checked. An existing file is kept, so a re-run only fetches what is
missing. The files hold FMP data only, never the API key.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import fmp_client

STATEMENTS = {"income": "income-statement", "balance": "balance-sheet-statement",
              "cashflow": "cash-flow-statement"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--as-reported", nargs="*", default=[])
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    client = fmp_client.FMPClient()
    failed = []
    for sym in a.symbols:
        path = out / f"{sym}.json"
        if path.exists():
            continue
        data = {}
        for key, ep in STATEMENTS.items():
            try:
                data[key] = client.get(ep, symbol=sym, period="quarter", limit=a.limit) or []
            except fmp_client.FMPError as exc:
                failed.append(f"{sym} {ep}: {exc}")
                data[key] = []
        path.write_text(json.dumps(data))
        print(sym, {k: len(v) for k, v in data.items()}, flush=True)
    if a.as_reported:
        (out / "asreported").mkdir(exist_ok=True)
        for sym in a.as_reported:
            try:
                rows = client.get("income-statement-as-reported", symbol=sym, period="quarter", limit=a.limit)
            except fmp_client.FMPError as exc:
                failed.append(f"{sym} income-statement-as-reported: {exc}")
                continue
            (out / "asreported" / f"{sym}.json").write_text(json.dumps(rows or []))
    for f in failed:
        print("FAILED", f, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
