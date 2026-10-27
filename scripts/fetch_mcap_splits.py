#!/usr/bin/env python3
"""
Save daily market-cap history and split history from FMP for the fundamentals x zones test.

  python fetch_mcap_splits.py --root DIR --symbols AAPL MSFT ... [--start 2005-01-01]

Writes DIR/mcap/<SYMBOL>.json (historical-market-capitalization, fetched in ~3-year chunks,
merged, deduped by date, oldest first) and DIR/splits/<SYMBOL>.json (splits). Run with the
fmp-technical-analysis scripts dir on PYTHONPATH. Prints chunks whose oldest row is more than
10 days after the chunk start (possible truncation, or simply no data that early).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import fmp_client


def chunks(start: date, end: date, years: int = 3):
    s = start
    while s <= end:
        e = min(date(s.year + years, s.month, s.day) - timedelta(days=1), end)
        yield s, e
        s = e + timedelta(days=1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--start", default="2005-01-01")
    a = ap.parse_args(argv)
    root = Path(a.root)
    (root / "mcap").mkdir(parents=True, exist_ok=True)
    (root / "splits").mkdir(parents=True, exist_ok=True)
    client = fmp_client.FMPClient()
    start, end = date.fromisoformat(a.start), date.today()
    failed = []
    for sym in a.symbols:
        mpath = root / "mcap" / f"{sym}.json"
        if not mpath.exists():
            rows, notes = {}, []
            first_seen = None
            for s, e in chunks(start, end):
                try:
                    got = client.get("historical-market-capitalization", symbol=sym,
                                     **{"from": s.isoformat(), "to": e.isoformat()}) or []
                except fmp_client.FMPError as exc:
                    failed.append(f"{sym} mcap {s}..{e}: {exc}")
                    continue
                for r in got:
                    rows[r["date"]] = r
                if got:
                    lo = min(r["date"] for r in got)
                    if first_seen is not None and date.fromisoformat(lo) > s + timedelta(days=10):
                        notes.append(f"chunk {s}..{e}: {len(got)} rows, oldest {lo}")
                    first_seen = first_seen or lo
            out = [rows[d] for d in sorted(rows)]
            mpath.write_text(json.dumps(out))
            span = f"{out[0]['date']}..{out[-1]['date']}" if out else "none"
            print(sym, "mcap", len(out), span, *(["TRUNC?"] + notes if notes else []), flush=True)
        spath = root / "splits" / f"{sym}.json"
        if not spath.exists():
            try:
                sp = client.get("splits", symbol=sym) or []
                spath.write_text(json.dumps(sp))
                print(sym, "splits", len(sp), flush=True)
            except fmp_client.FMPError as exc:
                failed.append(f"{sym} splits: {exc}")
    for f in failed:
        print("FAILED", f, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
