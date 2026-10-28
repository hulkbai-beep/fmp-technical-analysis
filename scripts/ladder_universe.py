#!/usr/bin/env python3
"""
Step 2 of the support/resistance ladder test (plan V3): the stock list, drawn once and frozen.

  python ladder_universe.py frame    --history HIST.csv --out DIR
  python ladder_universe.py check    --frame DIR/frame.json --out DIR/check.jsonl [--only LIST]       (FMP)
  python ladder_universe.py pairs    --frame DIR/frame.json --check CHECK.jsonl [...] --out PAIRS.json
  python ladder_universe.py closes   --pairs PAIRS.json --out CLOSES.jsonl                             (FMP)
  python ladder_universe.py wiki     --out WIKI_DIR
  python ladder_universe.py entities --frame ... --check ... --pairs ... --closes ... --wiki HTML [...] --out ENT.json
  python ladder_universe.py sample   --entities ENT.json --out DIR [--seed N]
  python ladder_universe.py fetch    --symbols-file LIST.txt --out DATA_DIR [--start 2005-01-01]        (FMP)

frame    S&P 500 membership from the daily snapshots of github.com/fja05680/sp500 ("S&P 500 Historical
         Components & Changes (Updated).csv": one row per date with every member's ticker). Each row
         holds until the next row's date; the last one until today. Writes every ticker that was a
         member on any day since FRAME_START with its membership intervals [start, end).
check    per ticker, FMP's profile (name, sector, ETF flag), the earliest split-adjusted bar in the 3
         years before its first membership day, and the bars around its last membership day. A ticker
         without bars there (a company FMP has no prices for, or a ticker reused by a company whose
         bars start later) is not usable.
pairs    tickers that left the index within a few days of another one joining: renamed companies
         show up this way, among the ordinary replacements.
closes   both tickers' closes in the 45 days before each such switch: a constant ratio means FMP
         keeps one series under both tickers (a rename).
wiki     Wikipedia's current list and table of changes, for the members' company names.
entities tickers merged into companies (same series, same FMP company name, SAME_COMPANY), spans
         whose Wikipedia name differs from FMP's listed for review, BARS_FROM applied; each company's
         kind: the seven large caps, previously tested (any ticker in PREVIOUSLY_TESTED), no usable
         ticker, not eligible (no month 2008..LAST_PLAN as a member with 3 years of bars), or pool.
sample   the pool stratified by FMP sector: 43 for the main sample and 50 held out, allocated by
         largest remainder in proportion to each sector's size and taken in a seeded random order;
         then reserve batches of 10 + 10 and spares. Writes main.txt, holdout.txt and sample.json
         (strata, orders and every draw, so the list can be rebuilt).
fetch    for each symbol: split-adjusted and dividend-adjusted daily bars, dividends, splits, earnings
         dates, market cap history, profile and quarterly income statements, as files under DATA_DIR.

Only FMP data and public membership data are written, never the API key.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

FRAME_START = date(2008, 1, 1)       # first plan date
HISTORY_START = date(2005, 1, 1)     # zones use the 3 years before the first plan
LAST_PLAN = date(2025, 10, 31)       # the last start date a 12-month plan can be evaluated from
ZONE_BARS_DAYS = 1100                # calendar days for the 756 bars of the zone window
SEED = 20261028
N_MAIN, N_HOLDOUT, RESERVE_BATCH, RESERVE_BATCHES, SPARES = 43, 50, 10, 6, 3
SNAPSHOT_END = date(2026, 8, 18)     # last row of the membership history
SWITCH_DAYS = 3                      # a rename can show up a few days apart in the snapshots
CLOSE_DAYS = 45                      # calendar days of closes compared before a ticker switch
SAME_SERIES_SD = 0.001               # sd of the log close ratio below which two tickers are one series
WIKI_PAGES = {"wiki_sp500.html": "List_of_S%26P_500_companies",
              "wiki_sp500_changes.html": "Historical_components_of_the_S%26P_500"}

MAG7 = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA")
# Same companies under the tickers the membership history uses: Alphabet's class A traded as GOOG
# until 2014-04-03, Meta as FB until 2022-06-09; Alphabet's class C (GOOG since 2014) is left out.
MAG7_ALIASES = {"GOOG": "GOOGL", "FB": "META"}
# 32 symbols of the first cleaned data set, 63 added for the zones x fundamentals test and ISRG (used
# to reverse-engineer the zone algorithm): the zone method has seen all of them.
PREVIOUSLY_TESTED = (
    "AAPL AMD AVGO BA COIN COST GLD INTC IWM JNJ KO LLY MMM MRNA MSFT NKE NVDA PEP PFE PG PLTR QQQ "
    "SMCI SPY T TLT TSLA VZ WBA XLE XLU XOM "
    "AMZN GOOGL META NFLX ORCL CSCO IBM QCOM TXN ADBE CRM AMAT MU HPQ EBAY CVX COP SLB OXY HAL CAT DE "
    "HON GE UNP UPS FDX LMT RTX MCD SBUX HD LOW TGT WMT DIS CMCSA F GM MO PM CL KMB GIS MRK ABT BMY "
    "AMGN GILD UNH CVS MDT TMO DHR NEE DUK SO NEM FCX BBY M KHC MCK "
    "ISRG").split()


def day(s) -> date:
    return date.fromisoformat(str(s)[:10])


def fmp_symbol(t: str) -> str:
    """Membership-history ticker -> FMP's form (BRK.B -> BRK-B)."""
    return t.strip().upper().replace(".", "-").replace("/", "-")


# ----------------------------------------------------------------------------- frame
def membership(history_csv: str, today: date) -> dict:
    """ticker -> [[start, end), ...] from the snapshot rows (sorted by date)."""
    rows = []
    with open(history_csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append((day(r["date"]), {t.strip() for t in r["tickers"].split(",") if t.strip()}))
    rows.sort(key=lambda x: x[0])
    spans: dict[str, list] = {}
    open_since: dict[str, date] = {}
    for i, (d, members) in enumerate(rows):
        for t in members:
            open_since.setdefault(t, d)
        nxt = rows[i + 1][0] if i + 1 < len(rows) else today + timedelta(days=1)
        for t in [t for t in open_since if t not in members]:
            spans.setdefault(t, []).append([open_since.pop(t), d])
        if i + 1 == len(rows):
            for t, s in open_since.items():
                spans.setdefault(t, []).append([s, nxt])
    return {t: [[a.isoformat(), b.isoformat()] for a, b in sorted(v)] for t, v in spans.items()}


def is_member(spans: list, d: date) -> bool:
    return any(day(a) <= d < day(b) for a, b in spans)


def cmd_frame(a) -> int:
    today = date.today()
    spans = membership(a.history, today)
    frame = {}
    for t, iv in spans.items():
        # only spans still open on FRAME_START or later: a ticker that left before 2008 and came
        # back is often another company (AMP, HCA, KMI, DG, HLT ...), and plans start in 2008
        iv2 = [x for x in iv if day(x[1]) > FRAME_START]
        if not iv2:
            continue
        sym = fmp_symbol(MAG7_ALIASES.get(t, t))
        frame.setdefault(sym, {"tickers": [], "spans": []})
        frame[sym]["tickers"].append(t)
        frame[sym]["spans"].extend(iv2)
    for v in frame.values():
        v["spans"].sort()
        v["first_start"] = v["spans"][0][0]
        v["last_end"] = v["spans"][-1][1]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = {"source": "github.com/fja05680/sp500, S&P 500 Historical Components & Changes (Updated).csv",
            "built": today.isoformat(), "frame_start": FRAME_START.isoformat(), "aliases": MAG7_ALIASES,
            "symbols": len(frame)}
    (out / "frame.json").write_text(json.dumps({"meta": meta, "frame": frame}, indent=1), encoding="utf-8")
    print(f"{len(frame)} symbols were members on some day since {FRAME_START}")
    return 0


# ----------------------------------------------------------------------------- check (FMP)
def _rows(data):
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else ([data] if isinstance(data, dict) else [])


def _dates(rows):
    return sorted(str(r["date"])[:10] for r in rows if r.get("date"))


def cmd_check(a) -> int:
    import fmp_client
    frame = json.loads(Path(a.frame).read_text(encoding="utf-8"))["frame"]
    out = Path(a.out)
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["symbol"])
    client = fmp_client.FMPClient()
    today = date.today()
    only = set(Path(a.only).read_text(encoding="utf-8").split()) if a.only else None
    todo = [s for s in sorted(frame) if s not in done and (only is None or s in only)]
    print(f"{len(done)} already checked, {len(todo)} to go", flush=True)
    with out.open("a", encoding="utf-8") as f:
        for n, sym in enumerate(todo, 1):
            v = frame[sym]
            first, last = day(v["first_start"]), day(v["last_end"])
            rec = {"symbol": sym}
            try:
                prof = _rows(client.get("profile", symbol=sym))
                p = prof[0] if prof else {}
                rec["profile"] = {k: p.get(k) for k in ("companyName", "sector", "industry", "isEtf", "isFund",
                                                         "isActivelyTrading", "ipoDate", "exchange", "country")}
                frm = max(HISTORY_START, first - timedelta(days=3 * 365))
                to = max(first, HISTORY_START + timedelta(days=60)) + timedelta(days=60)
                ds = _dates(_rows(client.get("historical-price-eod/full", symbol=sym,
                                             **{"from": frm.isoformat(), "to": to.isoformat()})))
                rec["early"] = {"from": frm.isoformat(), "to": to.isoformat(), "rows": len(ds),
                                "first": ds[0] if ds else None}
                if last < today - timedelta(days=30):
                    ds = _dates(_rows(client.get("historical-price-eod/full", symbol=sym,
                                                 **{"from": (last - timedelta(days=60)).isoformat(),
                                                    "to": (last + timedelta(days=10)).isoformat()})))
                    rec["late"] = {"rows": len(ds), "last": ds[-1] if ds else None}
            except (fmp_client.InvalidKey, fmp_client.Unreachable):
                raise
            except fmp_client.FMPError as e:
                rec["error"] = str(e)[:200]
            f.write(json.dumps(rec) + "\n")
            f.flush()
            if n % 50 == 0:
                print(f"{n}/{len(todo)}", flush=True)
    print(f"network calls {client.stats['network_calls']}, cache hits {client.stats['cache_hits']}")
    return 0


# ----------------------------------------------------------------------------- renamed and reused tickers
def load_checks(paths) -> dict:
    recs = {}
    for path in paths:              # later files replace earlier records (re-checks)
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                recs[r["symbol"]] = r
    return recs


def in_pool(sym: str, v: dict, rec: dict | None) -> bool:
    return (sym not in MAG7 and sym not in PREVIOUSLY_TESTED and usable(v, rec)[0]
            and eligible(v, rec))


def switch_pairs(frame: dict, recs: dict) -> list:
    """(left, joined, day) where ticker `left` left the index within SWITCH_DAYS of the day ticker
    `joined` joined, both with FMP bars and at least one in the sampling pool. A renamed company shows
    up this way (FMP keeps its series under the new ticker), and so does every ordinary replacement;
    `closes` fetches both tickers' closes before that day and `entities` tells the two apart."""
    bars = {s for s, r in recs.items() if (r.get("early") or {}).get("first")}
    pool = {s for s in frame if s in recs and in_pool(s, frame[s], recs[s])}
    joined = {}
    for s, v in frame.items():
        for x, _ in v["spans"]:
            joined.setdefault(day(x), []).append(s)
    out = set()
    for s, v in frame.items():
        for _, x in v["spans"]:
            x = day(x)
            if x >= SNAPSHOT_END or s not in bars:
                continue
            for k in range(-SWITCH_DAYS, SWITCH_DAYS + 1):
                for t in joined.get(x + timedelta(days=k), []):
                    if t != s and t in bars and (s in pool or t in pool):
                        out.add((s, t, x.isoformat()))
    return [{"left": s, "joined": t, "day": x} for s, t, x in sorted(out, key=lambda r: (r[2], r[0], r[1]))]


def cmd_pairs(a) -> int:
    frame = json.loads(Path(a.frame).read_text(encoding="utf-8"))["frame"]
    rows = switch_pairs(frame, load_checks(a.check))
    Path(a.out).write_text(json.dumps(rows, indent=0), encoding="utf-8")
    print(f"{len(rows)} pairs, {len({(r[k], r['day']) for r in rows for k in ('left', 'joined')})} windows")
    return 0


def cmd_closes(a) -> int:
    """Split-adjusted closes of both tickers of every pair in the CLOSE_DAYS before the switch day."""
    import fmp_client
    pairs = json.loads(Path(a.pairs).read_text(encoding="utf-8"))
    want = sorted({(p[k], p["day"]) for p in pairs for k in ("left", "joined")})
    out = Path(a.out)
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["symbol"], r["day"]))
    todo = [w for w in want if w not in done]
    print(f"{len(done)} windows already fetched, {len(todo)} to go", flush=True)
    client = fmp_client.FMPClient()
    with out.open("a", encoding="utf-8") as f:
        for n, (sym, x) in enumerate(todo, 1):
            frm, to = day(x) - timedelta(days=CLOSE_DAYS), day(x) + timedelta(days=5)
            rec = {"symbol": sym, "day": x}
            try:
                rows = _rows(client.get("historical-price-eod/full", symbol=sym,
                                        **{"from": frm.isoformat(), "to": to.isoformat()}))
                rec["closes"] = sorted([str(r["date"])[:10], r.get("close")] for r in rows if r.get("date"))
            except (fmp_client.InvalidKey, fmp_client.Unreachable):
                raise
            except fmp_client.FMPError as e:
                rec["error"] = str(e)[:200]
            f.write(json.dumps(rec) + "\n")
            f.flush()
            if n % 100 == 0:
                print(f"{n}/{len(todo)}", flush=True)
    print(f"network calls {client.stats['network_calls']}, cache hits {client.stats['cache_hits']}")
    return 0


def cmd_wiki(a) -> int:
    """Wikipedia's "List of S&P 500 companies" (current members) and "Historical components of the
    S&P 500" (the table of changes, with company names) as HTML: `entities` uses the names to find
    tickers that FMP now gives to another company. (Where Python's certificate store is out of date,
    PowerShell's Invoke-WebRequest saves the same URLs.)"""
    import urllib.request
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, page in WIKI_PAGES.items():
        url = "https://en.wikipedia.org/api/rest_v1/page/html/" + page
        req = urllib.request.Request(url, headers={"User-Agent": "fmp-technical-analysis (research script)"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        (out / name).write_bytes(data)
        print(f"{name}: {len(data)} bytes")
    return 0


# ----------------------------------------------------------------------------- sample
def usable(v: dict, rec: dict | None) -> tuple[bool, str]:
    """(usable, reason) for one frame entry and its check record."""
    if rec is None:
        return False, "not checked"
    if rec.get("error"):
        return False, "fmp error"
    p = rec.get("profile") or {}
    if p.get("isEtf") or p.get("isFund"):
        return False, "fund"
    early = rec.get("early") or {}
    if not early.get("first"):
        return False, "no bars before or at its first membership day"
    first = day(v["first_start"])
    if day(early["first"]) > max(first, HISTORY_START) + timedelta(days=10):
        return False, "bars start after it joined the index (ticker reused or history missing)"
    late = rec.get("late")
    if late is not None and (not late.get("last") or day(late["last"]) < day(v["last_end"]) - timedelta(days=10)):
        return False, "no bars up to its last membership day"
    return True, ""


def eligible(v: dict, rec: dict) -> bool:
    """At least one month in FRAME_START..LAST_PLAN as a member with ~3 years of bars before it."""
    first_bar = day(rec["early"]["first"])
    lo = max(FRAME_START, first_bar + timedelta(days=ZONE_BARS_DAYS))
    return lo <= LAST_PLAN and any(day(b) > lo and day(a) <= LAST_PLAN for a, b in v["spans"])


def allocate(sizes: dict, n: int) -> dict:
    """Largest-remainder allocation of n over strata in proportion to their sizes (ties by name)."""
    total = sum(sizes.values())
    if total == 0 or n <= 0:
        return {k: 0 for k in sizes}
    n = min(n, total)
    quota = {k: n * s / total for k, s in sizes.items()}
    alloc = {k: min(int(q), sizes[k]) for k, q in quota.items()}
    rest = n - sum(alloc.values())
    order = sorted(sizes, key=lambda k: (-(quota[k] - int(quota[k])), k))
    while rest > 0:
        moved = False
        for k in order:
            if rest and alloc[k] < sizes[k]:
                alloc[k] += 1
                rest -= 1
                moved = True
        if not moved:
            break
    return alloc


def draw(order: dict, taken: dict, n: int) -> list:
    """Take n more symbols, allocated over strata by what is left in each, in each stratum's order."""
    left = {k: len(v) - taken[k] for k, v in order.items()}
    alloc = allocate(left, n)
    out = []
    for k in sorted(order):
        out += order[k][taken[k]:taken[k] + alloc[k]]
        taken[k] += alloc[k]
    return out


# ----------------------------------------------------------------------------- entities
# Decisions by hand after the series and name checks (2026-10-28, before any draw), listed in
# entities.json. SAME_COMPANY: a renamed company whose old ticker has no usable FMP bars (moved to the
# new ticker, or the ticker was reused since), so the price check could not pair them.
SAME_COMPANY = {
    "ANTM": "ELV",    # Anthem renamed Elevance Health, 2022
    "ARNC": "HWM",    # Arconic Inc. (formerly Alcoa Inc.) renamed Howmet Aerospace, 2020
    "BHGE": "BKR",    # Baker Hughes, a GE company, renamed Baker Hughes Company, 2019
    "HCP": "DOC",     # HCP renamed Healthpeak (PEAK) in 2019, ticker DOC since 2024
    "HRS": "LHX",     # Harris renamed L3Harris after buying L3, 2019
    "KORS": "CPRI",   # Michael Kors Holdings renamed Capri Holdings, 2018
    "LB": "BBWI",     # L Brands renamed Bath & Body Works, 2021
    "TMK": "GL",      # Torchmark renamed Globe Life, 2019
}
# BARS_FROM: FMP's bars under the ticker before this date belong to another company (the ticker was
# reused) or join two companies; they are not used and membership before the date is dropped.
NEVER = "9999-12-31"
BARS_FROM = {
    "AGN": (NEVER, "Allergan Inc. until 2015-03, then Actavis renamed Allergan plc; FMP's series can't be told apart"),
    "BBT": (NEVER, "BB&T (renamed Truist 2019); FMP's BBT is Beacon Financial and its closes differ from TFC's"),
    "CCU": (NEVER, "Clear Channel Communications; FMP's CCU is Compania Cervecerias Unidas"),
    "DD": ("2019-06-03", "old DuPont until 2017-08 and DowDuPont until 2019-05 are joined under DD"),
    "EP": (NEVER, "El Paso Corp.; FMP's EP is Empire Petroleum"),
    "FOX": ("2019-03-19", "21st Century Fox class B until 2019-03-19, Fox Corp after, joined under FOX"),
    "JCI": ("2016-09-02", "Johnson Controls Inc. until 2016-09-02, then Tyco renamed Johnson Controls International"),
    "LSI": (NEVER, "LSI Corp.; FMP's LSI is Life Storage"),
    "PARA": (NEVER, "Paramount Global; FMP's PARA is Banzai International and its closes differ from VIAC's"),
    "TT": ("2020-03-03", "Trane Inc. until 2008; FMP's TT is Trane Technologies (formerly Ingersoll-Rand)"),
}
NAME_STOP = set("""inc incorporated corp corporation co company companies plc ltd limited the group
holdings holding sa nv ag se lp llc trust international intl class common stock new de and of""".split())


def _words(name) -> list:
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", s.replace("&", " and "))


def name_tokens(name) -> set:
    return {t for t in _words(name) if t not in NAME_STOP and len(t) > 1}


def same_name(a, b) -> bool:
    """Loose match of a Wikipedia name with FMP's: half the significant words shared, or one name
    is the other's initials or its words run together (IBM, Supermicro)."""
    ta, tb = name_tokens(a), name_tokens(b)
    if ta and tb and len(ta & tb) / min(len(ta), len(tb)) >= 0.5:
        return True
    legal = NAME_STOP - {"group", "holdings", "holding", "trust", "international", "intl", "new", "and", "of"}
    wa, wb = [w for w in _words(a) if w not in legal], [w for w in _words(b) if w not in legal]
    for x, y in ((wa, wb), (wb, wa)):
        if len(x) == 1 and len(y) > 1 and (x[0] == "".join(w[0] for w in y)
                                           or (len(x[0]) >= 4 and "".join(y).startswith(x[0]))):
            return True
    return False


def name_key(name) -> str:
    """FMP company name, case and punctuation aside: tickers with equal keys are one company (share
    classes, and old tickers whose FMP profile shows the renamed company)."""
    return " ".join(_words(name))


def wiki_tables(paths) -> tuple[dict, list]:
    """(current members {symbol: name}, changes [(date, added, added name, removed, removed name)])
    from the pages `wiki` saves."""
    import pandas as pd
    cur, chg = {}, []
    tables = [t for path in ([paths] if isinstance(paths, (str, Path)) else paths) for t in pd.read_html(str(path))]
    for t in tables:
        cols = [" ".join(str(x) for x in c) if isinstance(c, tuple) else str(c) for c in t.columns]
        t.columns = cols
        if "Symbol" in cols and "Security" in cols:
            cur.update({fmp_symbol(str(s)): str(n) for s, n in zip(t["Symbol"], t["Security"])})
        elif any(c.startswith("Added") for c in cols) and any(c.startswith("Removed") for c in cols):
            dcol = next(c for c in cols if "Date" in c)
            at, an = (next(c for c in cols if c.startswith("Added") and k in c) for k in ("Ticker", "Security"))
            rt, rn = (next(c for c in cols if c.startswith("Removed") and k in c) for k in ("Ticker", "Security"))
            for r in t.itertuples(index=False):
                r = dict(zip(cols, r))
                try:
                    d = pd.to_datetime(r[dcol]).date()
                except (ValueError, TypeError):
                    continue
                clean = lambda x: "" if pd.isna(x) else str(x).strip()
                chg.append((d, fmp_symbol(clean(r[at])), clean(r[an]), fmp_symbol(clean(r[rt])), clean(r[rn])))
    return cur, chg


def span_names(tickers, span, current: dict, changes: list) -> list:
    """Names Wikipedia gives the member for a span: added within a week of its start, removed within a
    week of its end, or the current list for a span still open at the end of the history."""
    tk = {fmp_symbol(MAG7_ALIASES.get(t, t)) for t in tickers}
    a, b = day(span[0]), day(span[1])
    out = [an for d, at, an, _, _ in changes if at in tk and an and abs((d - a).days) <= 7]
    out += [rn for d, _, _, rt, rn in changes if rt in tk and rn and abs((d - b).days) <= 7]
    if b >= SNAPSHOT_END:
        out += [current[t] for t in sorted(tk) if t in current]
    return out


def series_verdicts(pairs: list, closes_path) -> list:
    """Per pair: same FMP series (log close ratio constant: sd < SAME_SERIES_SD on >= 10 common days
    before the switch), different, or unknown (too few common days)."""
    import math
    import statistics
    closes = {}
    for line in Path(closes_path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            closes[(r["symbol"], r["day"])] = {d: c for d, c in (r.get("closes") or []) if c}
    out = []
    for p in pairs:
        x = p["day"]
        c1, c2 = closes.get((p["left"], x), {}), closes.get((p["joined"], x), {})
        common = [d for d in sorted(set(c1) & set(c2)) if d < x]
        rec = dict(p, common=len(common))
        if len(common) < 10:
            rec["verdict"] = "unknown"
        else:
            r = [math.log(c2[d] / c1[d]) for d in common]
            rec["sd"] = statistics.stdev(r)
            rec["verdict"] = "same" if rec["sd"] < SAME_SERIES_SD else "different"
        out.append(rec)
    return out


class _Union:
    def __init__(self):
        self.up = {}

    def find(self, x):
        self.up.setdefault(x, x)
        while self.up[x] != x:
            self.up[x] = self.up[self.up[x]]
            x = self.up[x]
        return x

    def join(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.up[max(ra, rb)] = min(ra, rb)


def merge_spans(spans: list) -> list:
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def build_entities(frame: dict, recs: dict, verdicts: list, current: dict, changes: list) -> dict:
    """Companies from tickers. Tickers are one company when their FMP closes before a ticker switch
    are the same series (`series_verdicts`), when FMP gives them the same company name (share classes,
    and old tickers whose profile shows the renamed company), or by SAME_COMPANY. A span whose
    Wikipedia name matches none of FMP's name for the ticker is listed for review. BARS_FROM cuts a
    ticker's bars and membership. The company's series is its usable ticker whose FMP bars run latest
    (ties: the earliest membership, then the name); its membership is the union of its tickers' spans."""
    uf = _Union()
    for s in frame:
        uf.find(s)
    for v in verdicts:
        if v["verdict"] == "same":
            uf.join(v["left"], v["joined"])
    by_name = {}
    for s in frame:
        nm = ((recs.get(s) or {}).get("profile") or {}).get("companyName")
        if nm and usable(frame[s], recs.get(s))[0]:
            by_name.setdefault(name_key(nm), []).append(s)
    for group in by_name.values():
        for s in group[1:]:
            uf.join(group[0], s)
    for a_, b_ in SAME_COMPANY.items():
        if a_ in frame and b_ in frame:
            uf.join(a_, b_)
    comps = {}
    for s in frame:
        comps.setdefault(uf.find(s), []).append(s)
    review = []
    entities = {}
    for members in comps.values():
        members = sorted(members)
        spans, cuts = [], {}
        for s in members:
            nm = ((recs.get(s) or {}).get("profile") or {}).get("companyName")
            cut = BARS_FROM.get(s, (None, None))[0]
            if cut:
                cuts[s] = BARS_FROM[s]
            for sp in frame[s]["spans"]:
                names = span_names(frame[s]["tickers"], sp, current, changes)
                if nm and names and not any(same_name(n, nm) for n in names):
                    review.append({"ticker": s, "span": sp, "wikipedia": sorted(set(names)), "fmp": nm})
                if cut and sp[1] <= cut:
                    continue
                spans.append([max(sp[0], cut), sp[1]] if cut else sp)
        ok = [s for s in members if usable(frame[s], recs.get(s))[0] and BARS_FROM.get(s, ("",))[0] != NEVER]

        def runs_until(s):
            return (recs[s].get("late") or {}).get("last") or "9999-12-31"

        series = (min(ok, key=lambda s: (-int(runs_until(s).replace("-", "")),
                                         frame[s]["first_start"], s)) if ok else None)
        sector = "Unknown"
        for s in ([series] if series else []) + members:
            sec = (((recs.get(s) or {}).get("profile") or {}).get("sector") or "").strip()
            if sec:
                sector = sec
                break
        merged = merge_spans(spans)
        bars_from = BARS_FROM.get(series, (None,))[0] if series else None
        kind = ("mag7" if any(s in MAG7 for s in members) else
                "previously tested" if any(s in PREVIOUSLY_TESTED for s in members) else
                "no usable ticker" if not ok else "pool")
        if kind == "pool":
            first_bar = min(day(recs[s]["early"]["first"]) for s in ok)
            if bars_from:
                first_bar = max(first_bar, day(bars_from))
            lo = max(FRAME_START, first_bar + timedelta(days=ZONE_BARS_DAYS))
            if not (lo <= LAST_PLAN and any(day(b) > lo and day(a) <= LAST_PLAN for a, b in merged)):
                kind = "not eligible"
        entities[series or members[0]] = {"tickers": members, "series": series, "spans": merged,
                                          "bars_from": bars_from, "sector": sector, "kind": kind,
                                          "cuts": cuts}
    return {"entities": entities, "review": review}


def cmd_entities(a) -> int:
    frame = json.loads(Path(a.frame).read_text(encoding="utf-8"))["frame"]
    recs = load_checks(a.check)
    pairs = json.loads(Path(a.pairs).read_text(encoding="utf-8"))
    verdicts = series_verdicts(pairs, a.closes)
    current, changes = wiki_tables(a.wiki)
    res = build_entities(frame, recs, verdicts, current, changes)
    ents = res["entities"]
    kinds = {}
    for e in ents.values():
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    multi = {k: e["tickers"] for k, e in ents.items() if len(e["tickers"]) > 1}
    meta = {"frame": a.frame, "checks": a.check, "pairs": len(pairs), "wiki": a.wiki,
            "wiki_current": len(current), "wiki_changes": len(changes),
            "same_series_pairs": [v for v in verdicts if v["verdict"] == "same"],
            "series_verdicts": {k: sum(v["verdict"] == k for v in verdicts) for k in ("same", "different", "unknown")},
            "kinds": kinds, "same_company": SAME_COMPANY, "bars_from": BARS_FROM}
    out = Path(a.out)
    out.write_text(json.dumps({"meta": meta, "entities": ents, "review": res["review"]}, indent=1), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("wiki_current", "wiki_changes", "series_verdicts", "kinds")}, indent=1))
    print(f"{len(multi)} companies with several tickers; {len(res['review'])} spans to review")
    return 0


def cmd_sample(a) -> int:
    """The draw: companies of kind "pool" in entities.json, stratified by the FMP sector of their
    series ticker, in a seeded random order per stratum. 43 for the main sample (with the seven large
    caps), 50 held out, RESERVE_BATCHES batches of 10 + 10 for an enlarged sample, then SPARES per
    stratum. A drawn company that fails the checks after the fetch is replaced by the next company of
    its stratum's order not yet drawn (spares first, then further down the order), main before
    held out, in list order."""
    import numpy as np
    doc = json.loads(Path(a.entities).read_text(encoding="utf-8"))
    ents = doc["entities"]
    pool = {}
    for k in sorted(ents):
        if ents[k]["kind"] == "pool":
            pool.setdefault(ents[k]["sector"], []).append(k)
    rng = np.random.default_rng(a.seed)
    order = {k: [v[i] for i in rng.permutation(len(v))] for k, v in sorted(pool.items())}
    taken = {k: 0 for k in order}
    main = draw(order, taken, N_MAIN)
    holdout = draw(order, taken, N_HOLDOUT)
    reserve = [{"main": draw(order, taken, RESERVE_BATCH), "holdout": draw(order, taken, RESERVE_BATCH)}
               for _ in range(RESERVE_BATCHES)]
    spares = {k: order[k][taken[k]:taken[k] + SPARES] for k in order}
    mag7 = [k for k in sorted(ents) if ents[k]["kind"] == "mag7"]
    assert sorted(mag7) == sorted(MAG7), mag7
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tick = lambda ks: [ents[k]["series"] for k in ks]
    (out / "main.txt").write_text("\n".join(list(MAG7) + tick(main)) + "\n", encoding="utf-8")
    (out / "holdout.txt").write_text("\n".join(tick(holdout)) + "\n", encoding="utf-8")
    summary = {
        "seed": a.seed, "entities": a.entities, "meta": doc["meta"],
        "pool_by_sector": {k: len(v) for k, v in sorted(pool.items())},
        "main_by_sector": {k: sum(s in main for s in v) for k, v in sorted(pool.items())},
        "holdout_by_sector": {k: sum(s in holdout for s in v) for k, v in sorted(pool.items())},
        "main": list(MAG7) + main, "holdout": holdout, "reserve": reserve, "spares": spares,
        "order": order, "drawn": {k: ents[k] for k in list(MAG7) + main + holdout
                                  + [x for r in reserve for x in r["main"] + r["holdout"]]
                                  + [x for v in spares.values() for x in v]},
    }
    (out / "sample.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("pool_by_sector", "main_by_sector", "holdout_by_sector")}, indent=1))
    print("main:", " ".join(summary["main"]))
    print(f"holdout: {len(holdout)} companies (names in holdout.txt)")
    return 0


# ----------------------------------------------------------------------------- fetch (FMP)
FETCH_PARTS = ("full", "adj", "div", "split", "earn", "mcap", "profile", "income")


def cmd_fetch(a) -> int:
    """Files DATA_DIR/<part>/<SYM>.json, skipped when present; a part that fails is not written, so a
    rerun retries it."""
    import fetch as F
    import fmp_client
    syms = [s.strip() for s in Path(a.symbols_file).read_text(encoding="utf-8").split() if s.strip()]
    parts = a.what.split(",") if a.what else list(FETCH_PARTS)
    out = Path(a.out)
    for sub in parts:
        (out / sub).mkdir(parents=True, exist_ok=True)
    client = fmp_client.FMPClient()
    today = date.today()
    log = []
    for sym in syms:
        rec = {"symbol": sym}
        for sub, ep in (("full", F.FULL), ("adj", F.ADJUSTED)):
            path = out / sub / f"{sym}.json"
            if sub in parts and not path.exists():
                rows, problem = F.fetch_history(client, ep, sym, a.start, today.isoformat())
                seen = {}
                for r in rows:
                    seen[str(r["date"])[:10]] = r
                rows = [seen[d] for d in sorted(seen)]
                rec[sub] = {"rows": len(rows), "first": rows[0]["date"] if rows else None,
                            "last": rows[-1]["date"] if rows else None, "problem": problem}
                if problem is None or (not rows and problem == "no data returned"):
                    path.write_text(json.dumps(rows), encoding="utf-8")
        simple = (("div", "dividends", {"limit": 1000}), ("split", "splits", {}),
                  ("earn", "earnings", {"limit": 1000}), ("profile", "profile", {}),
                  ("income", "income-statement", {"period": "quarter", "limit": 120}))
        for sub, ep, params in simple:
            path = out / sub / f"{sym}.json"
            if sub not in parts or path.exists():
                continue
            try:
                rows = client.get(ep, symbol=sym, **params) or []
            except (fmp_client.InvalidKey, fmp_client.Unreachable):
                raise
            except fmp_client.FMPError as e:
                rec[sub] = {"error": str(e)[:200]}
                continue
            path.write_text(json.dumps(rows), encoding="utf-8")
            rec[sub] = {"rows": len(rows) if isinstance(rows, list) else 1}
        path = out / "mcap" / f"{sym}.json"
        if "mcap" in parts and not path.exists():
            rows, s, errors = {}, date.fromisoformat(a.start), []
            while s <= today:
                e = min(date(s.year + 3, s.month, s.day) - timedelta(days=1), today)
                try:
                    got = client.get("historical-market-capitalization", symbol=sym,
                                     **{"from": s.isoformat(), "to": e.isoformat()}) or []
                except (fmp_client.InvalidKey, fmp_client.Unreachable):
                    raise
                except fmp_client.FMPError as exc:
                    errors.append(str(exc)[:120])
                    got = []
                for r in got:
                    rows[str(r["date"])[:10]] = r
                s = e + timedelta(days=1)
            rec["mcap"] = {"rows": len(rows), "errors": errors}
            if not errors:
                path.write_text(json.dumps([rows[d] for d in sorted(rows)]), encoding="utf-8")
        log.append(rec)
        print(json.dumps(rec), flush=True)
    with (out / "fetch_log.jsonl").open("a", encoding="utf-8") as f:
        for rec in log:
            f.write(json.dumps(rec) + "\n")
    print(f"network calls {client.stats['network_calls']}, cache hits {client.stats['cache_hits']}")
    return 0


# ----------------------------------------------------------------------------- verify (after the fetch)
DUP_CORR = 0.97        # daily log returns this correlated on >= 250 common days: one company twice
SAME_WINDOW = 60       # common days in the rolling same-series test


def load_closes(path) -> "pd.Series":
    import pandas as pd
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({str(r["date"])[:10]: r.get("close") for r in rows}, dtype=float).dropna()
    s.index = pd.to_datetime(s.index)
    return s[s > 0].sort_index()


def duplicate_of(a: "pd.Series", b: "pd.Series") -> str | None:
    """'same series' when the log close ratio holds still (sd < SAME_SERIES_SD over SAME_WINDOW common
    days while the price moves), 'twin' when daily log returns correlate >= DUP_CORR on >= 250 common
    days, else None."""
    import numpy as np
    j = a.to_frame("a").join(b.to_frame("b"), how="inner")
    if len(j) < SAME_WINDOW:
        return None
    r = np.log(j["b"] / j["a"])
    moving = np.log(j["a"]).diff().rolling(SAME_WINDOW).std() > 0.002
    still = r.rolling(SAME_WINDOW).std() < SAME_SERIES_SD
    if bool((still & moving).any()):
        return "same series"
    if len(j) >= 250:
        ra, rb = np.log(j["a"]).diff(), np.log(j["b"]).diff()
        if ra.corr(rb) >= DUP_CORR:
            return "twin"
    return None


def plan_months(closes: "pd.Series", spans: list, bars_from: str | None) -> int:
    """Months FRAME_START..LAST_PLAN whose first bar falls in a membership span with 756 bars of
    history (from bars_from when set)."""
    import pandas as pd
    s = closes if not bars_from else closes[closes.index >= pd.Timestamp(bars_from)]
    if len(s) < 756:
        return 0
    idx = s.index
    first = pd.Series(range(len(idx)), index=idx).groupby([idx.year, idx.month]).first()
    n = 0
    for i in first:
        d = idx[i].date()
        if i >= 755 and FRAME_START <= d <= LAST_PLAN and any(day(x) <= d < day(y) for x, y in spans):
            n += 1
    return n


def cmd_verify(a) -> int:
    """Per drawn company: its series' bars, the months it can start a plan, zero-volume bars, one-day
    moves over 50%, and the drawn, previously tested or large-cap stocks it duplicates. Writes these
    statuses only, never prices, so it can run where the held-out data are kept."""
    import pandas as pd
    smp = json.loads(Path(a.sample).read_text(encoding="utf-8"))
    drawn = smp["drawn"]
    role = {}
    for k in smp["main"]:
        role[k] = "main"
    for k in smp["holdout"]:
        role[k] = "holdout"
    for i, b in enumerate(smp["reserve"]):
        for side in ("main", "holdout"):
            for k in b[side]:
                role[k] = f"reserve {i + 1} {side}"
    for v in smp["spares"].values():
        for k in v:
            role[k] = "spare"
    dirs = [Path(d) for d in a.data]
    series, out = {}, {}
    for k, e in drawn.items():
        sym = e["series"]
        path = next((d / "full" / f"{sym}.json" for d in dirs if (d / "full" / f"{sym}.json").exists()), None)
        if path is None:
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        c = load_closes(path)
        series[k] = c
        vol0 = sum(1 for r in rows if not r.get("volume"))
        jumps = int((c.pct_change().abs() > 0.5).sum())
        out[k] = {"role": role.get(k), "series": sym, "bars": len(c),
                  "first": str(c.index[0].date()) if len(c) else None,
                  "last": str(c.index[-1].date()) if len(c) else None,
                  "plan_months": plan_months(c, e["spans"], e.get("bars_from")),
                  "zero_volume_bars": vol0, "moves_over_50pct": jumps, "duplicates": []}
    others = {}
    for d in [Path(x) for x in (a.prev or [])]:
        for p in sorted((d / "full").glob("*.json")):
            if p.stem not in series:
                others[p.stem] = load_closes(p)
    keys = list(series)
    for i, k in enumerate(keys):
        for k2 in keys[i + 1:]:
            why = duplicate_of(series[k], series[k2])
            if why:
                out[k]["duplicates"].append([k2, why])
                out[k2]["duplicates"].append([k, why])
        for s2, c2 in others.items():
            why = duplicate_of(series[k], c2)
            if why:
                out[k]["duplicates"].append([s2 + " (previously tested)", why])
    missing = [k for k in drawn if k not in out]
    doc = {"sample": a.sample, "data": a.data, "prev": a.prev, "missing": missing, "companies": out}
    Path(a.out).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    bad = {k: v for k, v in out.items() if v["plan_months"] == 0 or v["duplicates"]}
    print(f"{len(out)} companies checked, {len(missing)} without data, {len(bad)} failing:")
    for k, v in bad.items():
        print(f"  {k} ({v['role']}): plan months {v['plan_months']}, duplicates {v['duplicates']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("frame")
    p.add_argument("--history", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("check")
    p.add_argument("--frame", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--only", help="file with the symbols to check (default: all)")
    p = sub.add_parser("pairs")
    p.add_argument("--frame", required=True)
    p.add_argument("--check", required=True, nargs="+")
    p.add_argument("--out", required=True)
    p = sub.add_parser("closes")
    p.add_argument("--pairs", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("wiki")
    p.add_argument("--out", required=True)
    p = sub.add_parser("entities")
    p.add_argument("--frame", required=True)
    p.add_argument("--check", required=True, nargs="+")
    p.add_argument("--pairs", required=True)
    p.add_argument("--closes", required=True)
    p.add_argument("--wiki", required=True, nargs="+", help="the pages `wiki` saves")
    p.add_argument("--out", required=True)
    p = sub.add_parser("sample")
    p.add_argument("--entities", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=SEED)
    p = sub.add_parser("fetch")
    p.add_argument("--symbols-file", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--start", default=HISTORY_START.isoformat())
    p.add_argument("--what", help="comma list of parts (default all): " + ",".join(FETCH_PARTS))
    p = sub.add_parser("verify")
    p.add_argument("--sample", required=True)
    p.add_argument("--data", required=True, nargs="+", help="fetch folders of the drawn companies")
    p.add_argument("--prev", nargs="*", help="fetch folders of previously tested and large-cap stocks")
    p.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    return {"frame": cmd_frame, "check": cmd_check, "pairs": cmd_pairs, "closes": cmd_closes, "wiki": cmd_wiki,
            "entities": cmd_entities, "verify": cmd_verify,
            "sample": cmd_sample, "fetch": cmd_fetch}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
