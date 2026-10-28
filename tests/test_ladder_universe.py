"""Offline checks of the V3 stock list: membership spans, allocation, usability and a seeded draw."""
import json
from datetime import date

import ladder_universe as U


def _hist(tmp_path):
    p = tmp_path / "hist.csv"
    p.write_text('date,tickers\n2007-01-02,"A,B,FB,OLD"\n2009-01-02,"A,B,FB,NEW"\n'
                 '2022-06-09,"A,B,META,NEW"\n', encoding="utf-8")
    return p


def test_membership_spans(tmp_path):
    spans = U.membership(str(_hist(tmp_path)), date(2026, 10, 27))
    assert spans["OLD"] == [["2007-01-02", "2009-01-02"]]
    assert spans["NEW"] == [["2009-01-02", "2026-10-28"]]
    assert spans["FB"] == [["2007-01-02", "2022-06-09"]]
    assert U.is_member(spans["OLD"], date(2008, 12, 31)) and not U.is_member(spans["OLD"], date(2009, 1, 2))


def test_frame_merges_aliases(tmp_path):
    out = tmp_path / "f"
    U.main(["frame", "--history", str(_hist(tmp_path)), "--out", str(out)])
    fr = json.loads((out / "frame.json").read_text())["frame"]
    assert fr["META"]["tickers"] == ["FB", "META"] and fr["META"]["first_start"] == "2007-01-02"
    assert "FB" not in fr and "OLD" in fr          # OLD was a member in 2008


def test_allocate_largest_remainder():
    a = U.allocate({"x": 50, "y": 30, "z": 20}, 7)
    assert sum(a.values()) == 7 and a == {"x": 4, "y": 2, "z": 1}
    assert U.allocate({"x": 1, "y": 9}, 20) == {"x": 1, "y": 9}


def test_usable_rejects_reused_and_delisted():
    v = {"first_start": "2006-01-03", "last_end": "2012-01-03", "spans": [["2006-01-03", "2012-01-03"]]}
    good = {"profile": {"companyName": "X"}, "early": {"first": "2005-01-03"}, "late": {"last": "2012-01-03"}}
    assert U.usable(v, good)[0]
    reused = {"profile": {"companyName": "Y"}, "early": {"first": "2014-04-17"}, "late": {"last": "2012-01-03"}}
    assert not U.usable(v, reused)[0]
    gone = {"profile": {"companyName": "X"}, "early": {"first": "2005-01-03"}, "late": {"last": None}}
    assert not U.usable(v, gone)[0]
    fund = {"profile": {"companyName": "F", "isEtf": True}, "early": {"first": "2005-01-03"}}
    assert not U.usable(v, fund)[0]


def test_sample_is_seeded_and_disjoint(tmp_path):
    span = [["2005-01-03", "2026-10-28"]]
    sectors = ["Technology", "Healthcare", "Energy", "Utilities"]
    ents = {f"S{i:03d}": {"tickers": [f"S{i:03d}"], "series": f"S{i:03d}", "spans": span,
                          "sector": sectors[i % 4], "kind": "pool"} for i in range(400)}
    for s in U.MAG7:
        ents[s] = {"tickers": [s], "series": s, "spans": span, "sector": "Technology", "kind": "mag7"}
    ents["NFLX"] = {"tickers": ["NFLX"], "series": "NFLX", "spans": span, "sector": "Technology",
                    "kind": "previously tested"}
    ep = tmp_path / "entities.json"
    ep.write_text(json.dumps({"meta": {}, "entities": ents}))
    outs = []
    for k in range(2):
        out = tmp_path / f"o{k}"
        U.main(["sample", "--entities", str(ep), "--out", str(out), "--seed", "7"])
        outs.append(json.loads((out / "sample.json").read_text()))
    a = outs[0]
    assert a["main"] == outs[1]["main"] and a["holdout"] == outs[1]["holdout"]
    assert a["main"][:7] == list(U.MAG7) and len(a["main"]) == 50 and len(a["holdout"]) == 50
    assert not set(a["main"]) & set(a["holdout"])
    assert "NFLX" not in a["main"] + a["holdout"]
    rest = [s for b in a["reserve"] for s in b["main"] + b["holdout"]] + [s for v in a["spares"].values() for s in v]
    assert len(rest) == len(set(rest)) and not set(rest) & set(a["main"] + a["holdout"])
    assert a["main_by_sector"] == {"Energy": 11, "Healthcare": 11, "Technology": 11, "Utilities": 10}


def test_entities_merge_renames_and_cut_reused_tickers():
    sp = lambda a, b: {"tickers": [], "spans": [[a, b]], "first_start": a, "last_end": b}
    frame = {"OLDCO": sp("1996-01-02", "2015-03-02"), "NEWCO": sp("2015-03-02", "2026-10-29"),
             "CLA": sp("2010-01-04", "2026-10-29"), "CLB": sp("2012-01-03", "2026-10-29"),
             "UTX": sp("1996-01-02", "2020-04-03"), "RTX": sp("2020-04-03", "2026-10-29"),
             "TT": {"tickers": [], "spans": [["2002-05-13", "2008-06-06"], ["2020-03-03", "2026-10-29"]],
                    "first_start": "2002-05-13", "last_end": "2026-10-29"}}
    for k, v in frame.items():
        v["tickers"] = [k]
    prof = {"OLDCO": None, "NEWCO": "New Co", "CLA": "Two Class Inc.", "CLB": "Two Class, Inc", "UTX": None,
            "RTX": "RTX Corp", "TT": "Trane Technologies plc"}
    recs = {k: {"symbol": k, "profile": {"companyName": prof[k], "sector": "Industrials"},
                "early": {"first": "2005-01-03"}} for k in frame}
    for k in ("OLDCO", "UTX"):
        recs[k]["late"] = {"last": frame[k]["last_end"]}
    verdicts = [{"left": "OLDCO", "joined": "NEWCO", "day": "2015-03-02", "verdict": "same"},
                {"left": "UTX", "joined": "RTX", "day": "2020-04-03", "verdict": "same"}]
    ents = U.build_entities(frame, recs, verdicts, {}, [])["entities"]
    assert ents["NEWCO"]["tickers"] == ["NEWCO", "OLDCO"] and ents["NEWCO"]["series"] == "NEWCO"
    assert ents["NEWCO"]["spans"] == [["1996-01-02", "2026-10-29"]] and ents["NEWCO"]["kind"] == "pool"
    assert ents["CLA"]["tickers"] == ["CLA", "CLB"] and ents["CLA"]["series"] == "CLA"   # earlier member
    assert ents["RTX"]["kind"] == "previously tested"
    assert ents["TT"]["spans"] == [["2020-03-03", "2026-10-29"]] and ents["TT"]["bars_from"] == "2020-03-03"


def test_series_verdicts(tmp_path):
    days = [f"2015-02-{d:02d}" for d in range(2, 28)]
    cl = tmp_path / "closes.jsonl"
    rows = [{"symbol": "A", "day": "2015-03-02", "closes": [[d, 10 + i * 0.1] for i, d in enumerate(days)]},
            {"symbol": "B", "day": "2015-03-02", "closes": [[d, 2 * (10 + i * 0.1)] for i, d in enumerate(days)]},
            {"symbol": "C", "day": "2015-03-02", "closes": [[d, 10 + (i % 3)] for i, d in enumerate(days)]},
            {"symbol": "D", "day": "2015-03-02", "closes": [[days[0], 5.0]]}]
    cl.write_text("".join(json.dumps(r) + "\n" for r in rows))
    pairs = [{"left": "A", "joined": x, "day": "2015-03-02"} for x in ("B", "C", "D")]
    v = {r["joined"]: r["verdict"] for r in U.series_verdicts(pairs, cl)}
    assert v == {"B": "same", "C": "different", "D": "unknown"}


def test_name_matching():
    assert U.same_name("IBM", "International Business Machines Corporation")
    assert U.same_name("Supermicro", "Super Micro Computer, Inc.")
    assert not U.same_name("Clear Channel Communications", "Compañía Cervecerías Unidas S.A.")
    assert U.name_key("Two Class Inc.") == U.name_key("Two Class, Inc")
    assert U.name_key("H&R Block, Inc.") != U.name_key("Block, Inc.")


def test_switch_pairs_finds_same_day_ticker_changes():
    frame = {
        "OLDT": {"tickers": ["OLDT"], "spans": [["2005-01-03", "2015-03-02"]],
                 "first_start": "2005-01-03", "last_end": "2015-03-02"},
        "NEWT": {"tickers": ["NEWT"], "spans": [["2015-03-04", "2026-10-29"]],
                 "first_start": "2015-03-04", "last_end": "2026-10-29"},
        "FAR": {"tickers": ["FAR"], "spans": [["2015-04-01", "2026-10-29"]],
                "first_start": "2015-04-01", "last_end": "2026-10-29"},
        "NOBAR": {"tickers": ["NOBAR"], "spans": [["2015-03-02", "2026-10-29"]],
                  "first_start": "2015-03-02", "last_end": "2026-10-29"},
    }
    recs = {s: {"symbol": s, "profile": {"companyName": s}, "early": {"first": "2005-01-03"},
                "late": {"last": "2015-03-02"}} for s in ("OLDT", "NEWT", "FAR")}
    recs["NOBAR"] = {"symbol": "NOBAR", "profile": {}, "early": {"first": None}}
    pairs = U.switch_pairs(frame, recs)
    assert pairs == [{"left": "OLDT", "joined": "NEWT", "day": "2015-03-02"}]


def test_duplicates_and_plan_months():
    import numpy as np
    import pandas as pd
    idx = pd.bdate_range("2005-01-03", periods=1400)
    rng = np.random.default_rng(1)
    base = pd.Series(50 * np.exp(np.cumsum(rng.normal(0, 0.02, len(idx)))), index=idx)
    other = pd.Series(30 * np.exp(np.cumsum(rng.normal(0, 0.02, len(idx)))), index=idx)
    twin = pd.Series(base.to_numpy() * np.exp(rng.normal(0, 0.002, len(idx))), index=idx)
    assert U.duplicate_of(base, base * 3.0) == "same series"
    assert U.duplicate_of(base.iloc[:300], (base * 0.5).iloc[200:]) == "same series"   # partial overlap
    assert U.duplicate_of(base, twin) == "twin"
    assert U.duplicate_of(base, other) is None
    spans = [["2007-06-01", "2026-10-29"]]
    n = U.plan_months(base, spans, None)
    last = idx[-1]
    assert n == (last.year - 2008) * 12 + last.month      # every month from 2008-01 (bar 756 is in 2007-11)
    assert U.plan_months(base, spans, "2006-01-02") < n and U.plan_months(base.iloc[:700], spans, None) == 0
