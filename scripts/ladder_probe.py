#!/usr/bin/env python3
"""
Step 1 of the support/resistance ladder test (plan V3): which data FMP gives on this plan.

  python ladder_probe.py --out DIR [--removed-sample 40]

Run with the skill's scripts dir on PYTHONPATH (fmp_client.py, config.py) and FMP_API_KEY set.
Checks, and saves the lists the later steps need:

  universe   current S&P 500 members, the dated list of index changes, delisted companies and
             symbol changes; rebuilds the membership on a few past dates from the change list
  removed    for a deterministic sample of tickers that left the index since 2008: split-adjusted
             and dividend-adjusted prices up to the removal, market cap, profile (sector), earnings
  prices     dividend-adjusted bars, dividends and splits for current symbols
  earnings   how far back report dates go, and whether a time of day (before open / after close) is given
  valuation  analyst estimates (is there any as-of date, i.e. point-in-time consensus?), quarterly
             ratios and statements (for a trailing P/E fallback)
  rates      3-month Treasury yield history (cash interest), fetched in ~90-day windows

Writes DIR/probe.json (every check: status, rows, date span, field names, a few sample rows),
DIR/probe.md (the same as a table) and DIR/raw/*.json (the full lists: sp500_current,
sp500_changes, delisted, symbol_change, treasury). Only FMP data is written, never the API key.
About 350 requests; cached responses are reused on a re-run.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import fmp_client

FULL = "historical-price-eod/full"
ADJ = "historical-price-eod/dividend-adjusted"
PLAN_START = date(2008, 1, 1)          # first plan date; the 3 years before only feed the zones
HISTORY_START = date(2005, 1, 1)
# Well-known removals kept in the sample whatever the deterministic pick is: failures, takeovers
# and a ticker that was later reused.
KNOWN_REMOVED = ["LEH", "WB", "CFC", "MER", "YHOO", "CELG", "TWX", "MON", "RHT", "ATVI", "TWTR",
                 "XLNX", "FRC", "SIVB", "CTXS", "DISCA"]
RENAMED = [("FB", "META"), ("GOOG", "GOOGL")]
CURRENT = ["AAPL", "KO", "GE", "XOM"]
# Names FMP might use for point-in-time estimates; a 404 here only means the endpoint does not exist.
ESTIMATE_CANDIDATES = ["analyst-estimates-historical", "historical-analyst-estimates",
                       "earnings-estimates", "eps-estimates-historical"]
DATE_KEYS = ("date", "dateAdded", "delistedDate", "ipoDate", "filingDate", "acceptedDate",
             "paymentDate", "recordDate", "declarationDate", "lastUpdated")
ASOF_HINTS = ("asof", "as_of", "published", "updated", "estimatedate", "revision", "snapshot")


def iso(d) -> str:
    return d.isoformat() if isinstance(d, date) else str(d)


def parse_day(text):
    """'2023-12-18', '2023-12-18 00:00:00' or 'December 18, 2023' -> date, else None."""
    if not text:
        return None
    s = str(text).strip()
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date()
        except ValueError:
            continue
    return None


def rows_of(data):
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for k in ("historical", "data", "results"):
            if isinstance(data.get(k), list):
                return [r for r in data[k] if isinstance(r, dict)]
        return [data]
    return []


def span(rows, keys=DATE_KEYS):
    """Earliest and latest value of the first date-like key present in the rows."""
    for k in keys:
        ds = sorted(d for d in (parse_day(r.get(k)) for r in rows) if d)
        if ds:
            return k, ds[0].isoformat(), ds[-1].isoformat()
    return None, None, None


class Probe:
    def __init__(self, client):
        self.client = client
        self.checks = []

    def call(self, group, label, endpoint, keep=3, **params):
        """One request, recorded as a check. Returns the rows ([] when restricted or failed)."""
        entry = {"group": group, "label": label, "endpoint": endpoint,
                 "params": {k: v for k, v in params.items()}}
        try:
            data = self.client.get(endpoint, **params)
        except fmp_client.InvalidKey:
            raise
        except fmp_client.Unreachable:
            raise
        except fmp_client.RestrictedEndpoint as e:
            entry.update(status="RESTRICTED", detail=str(e)[:200])
            self.checks.append(entry)
            return []
        except fmp_client.FMPError as e:
            entry.update(status="ERROR", detail=str(e)[:200])
            self.checks.append(entry)
            return []
        rows = rows_of(data)
        key, lo, hi = span(rows)
        fields = sorted({k for r in rows[:50] for k in r})
        entry.update(status="OK" if rows else "EMPTY", rows=len(rows), date_key=key, first=lo, last=hi,
                     fields=fields, sample=rows[:keep])
        self.checks.append(entry)
        return rows

    def note(self, group, label, **info):
        self.checks.append({"group": group, "label": label, "status": "INFO", **info})


def paged(probe, group, label, endpoint, limit=100, max_pages=400, **params):
    """All pages of a paged list endpoint (page=0,1,... until a short or empty page)."""
    out = []
    for page in range(max_pages):
        rows = probe.call(group, f"{label} page {page}", endpoint, keep=1, page=page, limit=limit, **params)
        out.extend(rows)
        if len(rows) < limit:
            break
    # keep only the first and last page checks in the summary, plus a total
    pages = [c for c in probe.checks if c.get("label", "").startswith(f"{label} page ")]
    for c in pages[1:-1]:
        probe.checks.remove(c)
    probe.note(group, f"{label} total", rows=len(out), pages=len(pages), first=span(out)[1], last=span(out)[2])
    return out


def chunks(start: date, end: date, days: int):
    s = start
    while s <= end:
        e = min(s + timedelta(days=days - 1), end)
        yield s, e
        s = e + timedelta(days=1)


def members_on(current, changes, day):
    """Index members on `day`, rebuilt backwards from today's list: undo every change dated after `day`
    (drop the ticker that was added, put back the one that was removed)."""
    members = set(current)
    for ch in sorted(changes, key=lambda c: c["day"], reverse=True):
        if ch["day"] <= day:
            break
        if ch["added"]:
            members.discard(ch["added"])
        if ch["removed"]:
            members.add(ch["removed"])
    return members


def normalise_changes(rows):
    out = []
    for r in rows:
        d = parse_day(r.get("date")) or parse_day(r.get("dateAdded"))
        if not d:
            continue
        out.append({"day": d, "added": (r.get("symbol") or "").strip() or None,
                    "removed": (r.get("removedTicker") or "").strip() or None,
                    "added_name": r.get("addedSecurity"), "removed_name": r.get("removedSecurity"),
                    "reason": r.get("reason")})
    return out


def removed_sample(changes, n):
    """Every k-th removal since PLAN_START (sorted by date, then ticker), so the pick spreads over years."""
    rem = sorted({(c["day"], c["removed"]) for c in changes if c["removed"] and c["day"] >= PLAN_START})
    if not rem:
        return []
    step = max(1, len(rem) // max(1, n))
    return [rem[i] for i in range(0, len(rem), step)][:n]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--removed-sample", type=int, default=40)
    a = ap.parse_args(argv)
    out = Path(a.out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    client = fmp_client.FMPClient()
    p = Probe(client)
    today = date.today()

    def save(name, rows):
        (out / "raw" / f"{name}.json").write_text(json.dumps(rows), encoding="utf-8")

    try:
        # ---------------------------------------------------------------- universe
        current = p.call("universe", "S&P 500 members today", "sp500-constituent", keep=3)
        save("sp500_current", current)
        changes_raw = p.call("universe", "S&P 500 changes (dated)", "historical-sp500-constituent", keep=5)
        save("sp500_changes", changes_raw)
        changes = normalise_changes(changes_raw)
        cur_syms = {r.get("symbol") for r in current if r.get("symbol")}
        if cur_syms and changes:
            sizes = {}
            for y in (2005, 2008, 2012, 2016, 2020, 2024):
                sizes[str(y)] = len(members_on(cur_syms, changes, date(y, 1, 2)))
            ever = set(cur_syms)
            for c in changes:
                if c["day"] >= HISTORY_START and c["removed"]:
                    ever.add(c["removed"])
            p.note("universe", "rebuilt membership size on Jan 2 (should be ~500)", sizes=sizes,
                   earliest_change=min(c["day"] for c in changes).isoformat(),
                   changes_since_2005=sum(c["day"] >= HISTORY_START for c in changes),
                   tickers_ever_in_index_since_2005=len(ever),
                   has_sector_field=any("sector" in r for r in current))
        delisted = paged(p, "universe", "delisted companies", "delisted-companies")
        save("delisted", delisted)
        sym_change = p.call("universe", "symbol changes", "symbol-change", keep=5)
        if len(sym_change) >= 1000:   # may be capped; try a paged / limited form too
            more = p.call("universe", "symbol changes (limit=10000)", "symbol-change", keep=1, limit=10000)
            if len(more) > len(sym_change):
                sym_change = more
        save("symbol_change", sym_change)

        # ---------------------------------------------------------------- removed members
        pick = removed_sample(changes, a.removed_sample)
        by_ticker = {}
        for c in changes:
            if c["removed"]:
                by_ticker.setdefault(c["removed"], c["day"])
        for t in KNOWN_REMOVED:
            if t in by_ticker and (by_ticker[t], t) not in pick:
                pick.append((by_ticker[t], t))
            elif t not in by_ticker:
                pick.append((None, t))
        delisted_syms = {r.get("symbol"): r for r in delisted}
        summary = []
        for day, sym in pick:
            end = (day or today) + timedelta(days=60)
            start = max(HISTORY_START, (day or today) - timedelta(days=3 * 365))
            win = {"from": iso(start), "to": iso(end)}
            full = p.call("removed", f"{sym} split-adjusted bars before removal", FULL, keep=1, symbol=sym, **win)
            adj = p.call("removed", f"{sym} dividend-adjusted bars before removal", ADJ, keep=1, symbol=sym, **win)
            mc = p.call("removed", f"{sym} market cap before removal", "historical-market-capitalization",
                        keep=1, symbol=sym, **win)
            prof = p.call("removed", f"{sym} profile", "profile", keep=1, symbol=sym)
            ern = p.call("removed", f"{sym} earnings dates", "earnings", keep=1, symbol=sym, limit=1000)
            later = p.call("removed", f"{sym} bars 1-2 years after removal (ticker reused?)", FULL, keep=1,
                           symbol=sym, **{"from": iso((day or today) + timedelta(days=365)),
                                          "to": iso((day or today) + timedelta(days=730))}) if day else []
            last_bar = span(full)[2]
            summary.append({
                "ticker": sym, "removed": iso(day) if day else None,
                "split_adj_rows": len(full), "last_bar": last_bar,
                "div_adj_rows": len(adj), "mcap_rows": len(mc),
                "profile_name": (prof[0].get("companyName") if prof else None),
                "profile_sector": (prof[0].get("sector") if prof else None),
                "earnings_rows": len(ern), "bars_after_removal": len(later),
                "in_delisted_list": sym in delisted_syms,
                "delisted_date": (delisted_syms.get(sym) or {}).get("delistedDate"),
            })
        p.note("removed", "removed members: what is available", rows=len(summary), table=summary,
               with_prices=sum(s["split_adj_rows"] > 0 for s in summary),
               with_div_adj=sum(s["div_adj_rows"] > 0 for s in summary),
               with_mcap=sum(s["mcap_rows"] > 0 for s in summary),
               with_sector=sum(bool(s["profile_sector"]) for s in summary),
               with_earnings=sum(s["earnings_rows"] > 0 for s in summary))
        for old, new in RENAMED:
            p.call("removed", f"renamed ticker {old} (2013)", FULL, keep=1, symbol=old,
                   **{"from": "2013-01-01", "to": "2013-03-31"})
            p.call("removed", f"renamed ticker {new} (2013)", FULL, keep=1, symbol=new,
                   **{"from": "2013-01-01", "to": "2013-03-31"})

        # ---------------------------------------------------------------- prices, dividends, splits
        for sym in CURRENT:
            p.call("prices", f"{sym} dividend-adjusted 2005-2008", ADJ, keep=2, symbol=sym,
                   **{"from": "2005-01-01", "to": "2008-12-31"})
            p.call("prices", f"{sym} dividends", "dividends", keep=3, symbol=sym, limit=1000)
            p.call("prices", f"{sym} splits", "splits", keep=3, symbol=sym)
        p.call("prices", "SPY split-adjusted 2005-2008", FULL, keep=1, symbol="SPY",
               **{"from": "2005-01-01", "to": "2008-12-31"})

        # ---------------------------------------------------------------- earnings
        for sym in ("AAPL", "GE"):
            p.call("earnings", f"{sym} earnings (all)", "earnings", keep=3, symbol=sym, limit=1000)
        p.call("earnings", "earnings calendar Jan-Feb 2010 (time of day?)", "earnings-calendar", keep=5,
               **{"from": "2010-01-15", "to": "2010-02-15"})

        # ---------------------------------------------------------------- valuation
        for period in ("annual", "quarter"):
            rows = p.call("valuation", f"AAPL analyst estimates ({period})", "analyst-estimates", keep=3,
                          symbol="AAPL", period=period, page=0, limit=100)
            hints = sorted({k for r in rows for k in r if any(h in k.lower() for h in ASOF_HINTS)})
            p.note("valuation", f"analyst estimates ({period}): fields that could be an as-of date",
                   fields=hints, point_in_time=bool(hints))
        for ep in ESTIMATE_CANDIDATES:
            p.call("valuation", f"candidate endpoint {ep}", ep, keep=2, symbol="AAPL", limit=10)
        p.call("valuation", "AAPL ratios (quarter)", "ratios", keep=1, symbol="AAPL", period="quarter", limit=120)
        p.call("valuation", "AAPL key metrics (quarter)", "key-metrics", keep=1, symbol="AAPL",
               period="quarter", limit=120)
        p.call("valuation", "AAPL income statement (quarter)", "income-statement", keep=1, symbol="AAPL",
               period="quarter", limit=120)
        p.call("valuation", "AAPL market cap 2005-2007", "historical-market-capitalization", keep=1,
               symbol="AAPL", **{"from": "2005-01-01", "to": "2007-12-31"})

        # ---------------------------------------------------------------- rates
        tsy = []
        for s, e in chunks(HISTORY_START, today, 90):
            tsy.extend(p.call("rates", f"treasury {s}..{e}", "treasury-rates", keep=1,
                              **{"from": iso(s), "to": iso(e)}))
        seen = {}
        for r in tsy:
            if r.get("date"):
                seen[str(r["date"])[:10]] = r
        tsy = [seen[d] for d in sorted(seen)]
        save("treasury", tsy)
        rate_checks = [c for c in p.checks if c.get("label", "").startswith("treasury ")]
        bad = [c["label"] for c in rate_checks if c["status"] != "OK"]
        for c in rate_checks[1:-1]:
            p.checks.remove(c)
        p.note("rates", "treasury total", rows=len(tsy), first=tsy[0]["date"] if tsy else None,
               last=tsy[-1]["date"] if tsy else None, failed_windows=bad[:20],
               has_month3=any("month3" in r for r in tsy))
    except (fmp_client.InvalidKey, fmp_client.Unreachable) as e:
        p.note("fatal", "stopped", detail=str(e)[:300])

    p.note("meta", "requests", network_calls=client.stats["network_calls"], cache_hits=client.stats["cache_hits"],
           run_date=today.isoformat())
    (out / "probe.json").write_text(json.dumps(p.checks, indent=1, default=str), encoding="utf-8")
    lines = ["| group | check | status | rows | first | last | detail |", "|---|---|---|---|---|---|---|"]
    for c in p.checks:
        detail = c.get("detail") or ""
        if c["status"] == "INFO":
            detail = json.dumps({k: v for k, v in c.items()
                                 if k not in ("group", "label", "status", "table")}, default=str)[:300]
        lines.append(f"| {c['group']} | {c['label']} | {c['status']} | {c.get('rows', '')} | "
                     f"{c.get('first') or ''} | {c.get('last') or ''} | {detail.replace('|', '/')} |")
    (out / "probe.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
