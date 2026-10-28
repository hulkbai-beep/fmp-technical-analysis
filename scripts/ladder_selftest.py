#!/usr/bin/env python3
"""
Self-tests of the ladder test V3 (references/ladder_v3_spec.md, section 15), run in step 3, before the
rules are registered.

  python scripts/ladder_selftest.py one [--quick]   no future data
  python scripts/ladder_selftest.py two             planted levels that truly hold (synthetic stocks)

Self-test 1 reads main-sample bars (never held-out ones) but reports only pass or fail and coverage
counts: no return is computed or printed. Its parts:
  inputs   every precomputed array built from data cut after bar c equals the full build up to c
  kernel   the whole simulator state at the close of c is the same with the inputs after c intact,
           blanked (NaN) or replaced by another path (the bars before c mirrored), for every group,
           draw and configuration
  audit    executed orders were created at an earlier close, sales follow their signal close,
           dividends use the shares of the previous close, interest a rate dated by the previous bar,
           no income row counts on or before its period end, and the simulator takes no earnings input
  leaks    each of the 11 injected leaks of ladder_sim (LEAK_*), switched on alone, makes it fail
  seeds    two runs are identical; another MASTER moves every random level
"""
from __future__ import annotations

import argparse
import copy
import inspect
import json
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ladder_run as R  # noqa: E402
import ladder_sim as S  # noqa: E402
import node_zones as NZ  # noqa: E402

FIXED_FOR_TESTS = np.array([0.03, 0.08, 0.14, 0.20, 0.27])     # D_i come at step 4; any depths run the code
MAIN_GROUPS = ("zone", "random", "gaps", "vol", "fixed", "lump")
PLACEBOS = tuple(range(1, 21))
MAIN = S.Config()
VARIANTS = (S.Config(fill_mode=S.CLOSE_CONFIRM, name="close confirm"), S.Config(time_limit=True, name="time limit"),
            S.Config(sma_level=True, name="200-day"), S.Config(stop_pct=0.20, name="stop 20%"),
            S.Config(ratio=(1, 2, 3, 2, 2), name="1:2:3:2:2"), S.Config(ratio=(1, 2, 4), name="1:2:4"),
            S.Config(ratio=(1, 1, 1, 1), name="1:1:1:1"), S.Config(ratio=(1, 1, 1, 1, 1), name="1:1:1:1:1"),
            S.Config(ratio=(1, 1, 1), name="1:1:1"))
BREAKOUT = S.Config(start_on_breakout=True, name="breakout start")
CUT_TYPES = ("fill", "stop signal", "scale-out start", "sale signal", "replacement", "ex-dividend", "horizon",
             "start")
NEEDED = {k: 20 for k in CUT_TYPES if k != "start"}
LEAKS = {S.LEAK_ZONES_NEXT: "zones of d+1", S.LEAK_STOP_NEXT_CLOSE: "C[d+1] in stop condition 1",
         S.LEAK_START_SMA_NEXT: "SMA200[d+1] in the start condition", S.LEAK_VOL_NEXT_SIGMA: "sig3m[d+1] in vol",
         S.LEAK_DETECTOR_NEXT: "C[d+1] in the detector", S.LEAK_SPY_NEXT: "SPY[d+1] in stop condition 3",
         S.LEAK_SAME_BAR_FILL: "a limit filled on its own bar", S.LEAK_STOP_SAME_OPEN: "a stop sold at its own open",
         S.LEAK_DIVIDEND_NEW_SHARES: "a dividend on shares bought on the ex-date",
         S.LEAK_RATE_SAME_DAY: "interest at the bar's own date", S.LEAK_EPS_PERIOD_END: "EPS known at the period end"}
INPUT_LEAKS = (S.LEAK_START_SMA_NEXT, S.LEAK_DETECTOR_NEXT, S.LEAK_RATE_SAME_DAY, S.LEAK_EPS_PERIOD_END)
OUT = R.V3 / "selftest1.json"


# ----------------------------------------------------------------------------- part 1: input builds
ARRAYS = ("o", "h", "l", "c", "v", "div", "rate", "rate_src", "spy", "member", "mcap", "pe", "sma200", "sig3m")


def _eq(a, b) -> bool:
    a, b = np.asarray(a), np.asarray(b)
    if a.shape != b.shape:
        return False
    if a.dtype.kind in "fc":
        return bool(np.array_equal(a, b, equal_nan=True))
    if a.dtype.kind == "M":
        return bool(np.array_equal(a.astype("int64"), b.astype("int64")))
    return bool(np.array_equal(a, b))


def input_checks(symbol: str, full: S.Prepared, mk: S.Market, cuts: list, leak: int, zones: bool, smp) -> dict:
    """Each cut c: build from data cut at date[c] and compare with the full build up to c."""
    bad: dict = {}
    n_arr = 0
    bars = pd.DataFrame({"high": full.data.h, "low": full.data.l, "close": full.data.c, "volume": full.data.v})
    full_masks = {cfg.name: S.start_mask(full, cfg, leak) for cfg in (MAIN, BREAKOUT)}
    for c in cuts:
        cd = R.build(symbol, "main", mk, cut=str(full.data.dates[c]), leak=leak, smp=smp)
        if len(cd.c) != c + 1 or not _eq(cd.dates, full.data.dates[:c + 1]):
            bad["dates"] = bad.get("dates", 0) + 1
            continue
        for name in ARRAYS:
            n_arr += 1
            if not _eq(getattr(cd, name), getattr(full.data, name)[:c + 1]):
                bad[name] = bad.get(name, 0) + 1
        n_arr += 1
        if not _eq(cd.report_dates, full.data.report_dates[full.data.report_dates <= full.data.dates[c]]):
            bad["report_dates"] = bad.get("report_dates", 0) + 1
        if zones:
            cbars = pd.DataFrame({"high": cd.h, "low": cd.l, "close": cd.c, "volume": cd.v})
            for dd in range(max(S.LOOKBACK - 1, c - 4), c + 1):
                n_arr += 1
                zs = NZ.node_zones(cbars.iloc[dd - S.LOOKBACK + 1:dd + 1], S.LOOKBACK)
                lo = np.array([z["zone_low"] for z in zs])
                hi = np.array([z["zone_high"] for z in zs])
                k = full.n_z[dd]
                if len(zs) != k or not (_eq(lo, full.z_lo[dd, :k]) and _eq(hi, full.z_hi[dd, :k])):
                    bad["zones"] = bad.get("zones", 0) + 1
                # the cut bars give the same window as the full bars
                if dd == c and not bars.iloc[dd - S.LOOKBACK + 1:dd + 1].reset_index(drop=True).equals(
                        cbars.iloc[dd - S.LOOKBACK + 1:dd + 1].reset_index(drop=True)):
                    bad["zone window"] = bad.get("zone window", 0) + 1
        cp = S.prepare(cd, zones=(full.z_lo[:c + 1], full.z_hi[:c + 1], full.n_z[:c + 1]), leak=leak)
        for name, a, b in (("detector brk", cp.brk, full.brk), ("detector broken low", cp.b_lo, full.b_lo),
                           ("detector broken high", cp.b_hi, full.b_hi), ("detector watched low", cp.det[0], full.det[0]),
                           ("detector watched high", cp.det[1], full.det[1]), ("detector counter", cp.det[2], full.det[2])):
            n_arr += 1
            if not _eq(a, b[:c + 1]):
                bad[name] = bad.get(name, 0) + 1
        for cfg in (MAIN, BREAKOUT):
            n_arr += 1
            if not _eq(S.start_mask(cp, cfg, leak), full_masks[cfg.name][:c + 1]):
                bad[f"plan starts ({cfg.name})"] = bad.get(f"plan starts ({cfg.name})", 0) + 1
    return {"cuts": len(cuts), "arrays_compared": n_arr, "mismatches": bad}


# ----------------------------------------------------------------------------- part 2: kernel invariance
def version(prep: S.Prepared, c: int, how: str) -> S.Prepared:
    """The inputs after bar c blanked ("blank") or replaced by the bars before c in reverse ("mirror")."""
    if how == "full":
        return prep
    n = len(prep.data.c)
    src = np.clip(2 * c - np.arange(n), 0, n - 1)

    def f(a, fill):
        a = a.copy()
        if how == "blank":
            a[c + 1:] = fill
        else:
            a[c + 1:] = a[src[c + 1:]]
        return a

    d = copy.copy(prep.data)
    for name in ("o", "h", "l", "c", "v", "sma200", "sig3m", "div", "rate", "spy", "mcap", "pe"):
        setattr(d, name, f(getattr(d, name), np.nan))
    return S.Prepared(d, f(prep.z_lo, np.nan), f(prep.z_hi, np.nan), f(prep.n_z, 0), f(prep.brk, False),
                      f(prep.b_lo, np.nan), f(prep.b_hi, np.nan), prep.det)


def runs_at(prep: S.Prepared, t: int, c: int, leak: int, breakout: bool) -> dict:
    """Every group, draw and configuration of the plan at t, stopped after the close of c."""
    out = {}
    cfgs = ((BREAKOUT, ("zone", "random"), ()),) if breakout else \
        ((MAIN, MAIN_GROUPS, PLACEBOS),) + tuple((v, ("zone", "random"), ()) for v in VARIANTS)
    for cfg, groups, plc in cfgs:
        res, floor = S.run_plan(prep, t, cfg, groups=groups, fixed_depth=FIXED_FOR_TESTS, placebos=plc, stop_at=c,
                                leak=leak)
        out[(cfg.name, "floor", 0)] = (floor[:c + 1].copy(),)
        for g, draws in res.items():
            for j, x in enumerate(draws):
                out[(cfg.name, g, j)] = x
    return out


def same(a: dict, b: dict) -> bool:
    if a.keys() != b.keys():
        return False
    return all(len(a[k]) == len(b[k]) and all(_eq(x, y) for x, y in zip(a[k], b[k])) for k in a)


def full_runs(prep: S.Prepared, cfg: S.Config, ts: list, leak: int):
    """Complete runs (no stop) of every plan: their events give the cuts and feed the audit."""
    groups = MAIN_GROUPS if cfg is MAIN else ("zone", "random")
    plc = PLACEBOS if cfg is MAIN else ()
    for t in ts:
        res, _ = S.run_plan(prep, t, cfg, groups=groups, fixed_depth=FIXED_FOR_TESTS, placebos=plc, leak=leak)
        yield t, [ev for draws in res.values() for (_, ev, _) in draws]


def cut_candidates(prep: S.Prepared, t: int, evs: list) -> dict:
    end = S.plan_end(prep, t)
    cand = {k: set() for k in CUT_TYPES}
    for ev in evs:
        for e in ev:
            bar, code = int(e[0]), int(e[1])
            if code in (S.EV_FILL, S.EV_CLOSE_FILL):
                cand["fill"].add(bar)
            elif code == S.EV_STOP_SIGNAL:
                cand["stop signal"].add(bar)
            elif code == S.EV_SCALE_START:
                cand["scale-out start"].add(bar)
            elif code == S.EV_SALE_SIGNAL:
                cand["sale signal"].add(bar)
            elif code == S.EV_ORDER and int(e[5]) > 0:
                cand["replacement"].add(bar)
    cand["ex-dividend"] = {int(b) for b in np.flatnonzero(prep.data.div[t + 1:end + 1] > 0) + t + 1}
    cand["horizon"] = {t + int(h) for h in S.HORIZONS if t + h <= end}
    cand["start"] = {t}
    return {k: {b for b in v if t <= b <= end} for k, v in cand.items()}


# ----------------------------------------------------------------------------- part 3: audit
def audit_events(ev: np.ndarray) -> dict:
    """Timing rules on one run's events; returns counts of violations by rule."""
    bad = {}
    sh_close: dict = {}
    sh = 0.0
    last_bar = None
    for e in ev:
        bar, code = int(e[0]), int(e[1])
        if last_bar is not None and bar != last_bar:
            sh_close[last_bar] = sh
        last_bar = bar
        if code in (S.EV_FILL, S.EV_CLOSE_FILL) and not int(e[5]) < bar:
            bad["order filled at or before the close that created it"] = bad.get(
                "order filled at or before the close that created it", 0) + 1
        if code in (S.EV_STOP_SALE, S.EV_SALE) and not int(e[5]) < bar:
            bad["sale at or before its signal close"] = bad.get("sale at or before its signal close", 0) + 1
        if code == S.EV_DIVIDEND:
            prev = [b for b in sh_close if b < bar]
            want = sh_close[max(prev)] if prev else 0.0
            if not np.isclose(e[3], want, rtol=1e-12, atol=1e-15):
                bad["dividend not on the shares of the previous close"] = bad.get(
                    "dividend not on the shares of the previous close", 0) + 1
        if code in (S.EV_FILL, S.EV_CLOSE_FILL, S.EV_TIME_BUY, S.EV_LUMP_BUY):
            sh += e[3]
        elif code == S.EV_SALE:
            sh -= e[3]
        elif code in (S.EV_STOP_SALE, S.EV_LIQUIDATE):
            sh = 0.0
    return bad


def audit_rate(data: S.StockData) -> int:
    src = data.rate_src[1:]
    ok = np.isnat(src) | (src <= data.dates[:-1])
    return int((~ok).sum())


def audit_income(symbol: str) -> int:
    """Income rows that would count on or before their period end (S13); none may."""
    rows = S.read_rows(R.DATA / "main" / "income" / f"{symbol}.json")
    return sum(1 for r in rows if r.get("date") and S.income_known(r) <= S._day(r["date"]))


def audit_signature() -> list:
    names = list(inspect.signature(S.run_group.py_func).parameters)
    return [n for n in names if any(w in n.lower() for w in ("earn", "report", "eps"))]


# ----------------------------------------------------------------------------- one stock
def stock_job(job) -> dict:
    symbol, leak, parts, per_type, quick = job
    smp = R.sample()
    mk = R.market()
    prep = R.prepared(symbol, "main", mk, leak=leak, smp=smp)
    d = prep.data
    rng = np.random.Generator(np.random.PCG64(S.seed_of(f"{S.MASTER}|selftest1|{symbol}|cuts")))
    rep = {"symbol": symbol}
    ts = S.plan_starts(prep, MAIN, leak)[0]
    tb = S.plan_starts(prep, BREAKOUT, leak)[0]
    # complete runs: the cut candidates and the audit
    audit: dict = {}
    n_runs = 0
    pairs = []
    if "kernel" in parts or "audit" in parts:
        cands = []
        for t, evs in full_runs(prep, MAIN, ts, leak):
            n_runs += len(evs)
            for ev in evs:
                for k, v in audit_events(ev).items():
                    audit[k] = audit.get(k, 0) + v
            cands.append((t, cut_candidates(prep, t, evs), False))
        bcands = []
        for t, evs in full_runs(prep, BREAKOUT, tb, leak):
            n_runs += len(evs)
            for ev in evs:
                for k, v in audit_events(ev).items():
                    audit[k] = audit.get(k, 0) + v
            bcands.append((t, cut_candidates(prep, t, evs), True))
        chosen = set()
        for typ in CUT_TYPES:
            pool = sorted({(t, b, br) for t, cd, br in cands for b in cd[typ]})
            take = per_type if typ != "start" else max(1, per_type // 2)
            for i in rng.permutation(len(pool))[:take]:
                chosen.add(pool[i])
            bpool = sorted({(t, b, br) for t, cd, br in bcands for b in cd[typ]})
            for i in rng.permutation(len(bpool))[:1 if not quick else 0]:
                chosen.add(bpool[i])
        lookup = {(t, br): cd for t, cd, br in cands + bcands}
        pairs = sorted(chosen)
        cover = {k: 0 for k in CUT_TYPES}
        for t, c, br in pairs:
            for k in CUT_TYPES:
                cover[k] += c in lookup[(t, br)][k]
        rep["coverage"] = cover
    if "kernel" in parts:
        bad = 0
        compared = 0
        for t, c, br in pairs:
            ref = runs_at(prep, t, c, leak, br)
            compared += len(ref)
            for how in ("blank", "mirror"):
                if not same(ref, runs_at(version(prep, c, how), t, c, leak, br)):
                    bad += 1
            # timing audit of the stopped runs of every configuration
            for key, x in ref.items():
                if key[1] != "floor":
                    n_runs += 1
                    for k, v in audit_events(x[1]).items():
                        audit[k] = audit.get(k, 0) + v
        rep["kernel"] = {"pairs": len(pairs), "runs_compared_per_version": compared, "pairs_differing": bad}
    if "audit" in parts or "kernel" in parts:
        rep["audit"] = {"runs": n_runs, "violations": audit, "rate_dated_after_previous_bar": audit_rate(d),
                        "income_known_by_period_end": audit_income(symbol)}
    if "inputs" in parts:
        first = pd.DatetimeIndex(d.dates)
        month = first.year * 12 + first.month
        ms = [i for i in range(1, len(d.c)) if month[i] != month[i - 1] and first[i] >= S.FRAME_START
              and i >= S.LOOKBACK - 1]
        if quick:
            ms = ms[::6]
        cuts = sorted(set(ms) | {c for _, c, _ in pairs})
        rep["inputs"] = input_checks(symbol, prep, mk, cuts, leak, zones=(leak == 0), smp=smp)
        rep["audit"] = rep.get("audit", {"runs": 0, "violations": {}})
        rep["audit"]["rate_dated_after_previous_bar"] = audit_rate(d)
        rep["audit"]["income_known_by_period_end"] = audit_income(symbol)
    return rep


# ----------------------------------------------------------------------------- seeds
def seeds_check(symbols: list, n_plans: int = 30) -> dict:
    mk = R.market()
    smp = R.sample()
    rng = np.random.Generator(np.random.PCG64(S.seed_of(f"{S.MASTER}|selftest1|seeds")))
    same_runs, moved, unmoved = True, 0, 0
    for sym in [symbols[i] for i in rng.permutation(len(symbols))[:10]]:
        prep = R.prepared(sym, "main", mk, smp=smp)
        ts = S.plan_starts(prep, MAIN)[0]
        for t in [ts[i] for i in rng.permutation(len(ts))[:max(1, n_plans // 10)]]:
            a, _ = S.run_plan(prep, t, MAIN, MAIN_GROUPS, FIXED_FOR_TESTS, placebos=PLACEBOS)
            b, _ = S.run_plan(prep, t, MAIN, MAIN_GROUPS, FIXED_FOR_TESTS, placebos=PLACEBOS)
            same_runs &= all(_eq(x[i], y[i]) for g in a for x, y in zip(a[g], b[g]) for i in range(3))
            o, _ = S.run_plan(prep, t, MAIN, MAIN_GROUPS, FIXED_FOR_TESTS, placebos=PLACEBOS, master=S.MASTER + 1)
            for g in ("random", "gaps", "placebo"):
                for (_, e1, _), (_, e2, _) in zip(a[g], o[g]):
                    p1 = S.start_orders(e1, t, 4)
                    p2 = S.start_orders(e2, t, 4)
                    capped = (e1[(e1[:, 0] == t) & (e1[:, 1] == S.EV_CAP)].shape[0] > 0) or \
                             (e2[(e2[:, 0] == t) & (e2[:, 1] == S.EV_CAP)].shape[0] > 0)
                    both = np.isfinite(p1) & np.isfinite(p2)
                    if capped:
                        both[0] = False
                    moved += int((p1[both] != p2[both]).sum())
                    unmoved += int((p1[both] == p2[both]).sum())
    return {"identical_reruns": bool(same_runs), "levels_moved_by_master": moved, "levels_not_moved": unmoved,
            "passes": bool(same_runs and unmoved == 0 and moved > 0)}


# ----------------------------------------------------------------------------- the test
def run_test(symbols: list, leak: int, parts: tuple, per_type: int, workers: int, quick: bool) -> dict:
    jobs = [(s, leak, parts, per_type, quick) for s in symbols]
    with Pool(workers) as pool:
        reps = list(pool.imap_unordered(stock_job, jobs))
    reps.sort(key=lambda r: r["symbol"])
    out = {"leak": leak, "parts": list(parts), "stocks": len(reps)}
    fails = []
    if "inputs" in parts:
        mm: dict = {}
        for r in reps:
            for k, v in r["inputs"]["mismatches"].items():
                mm[k] = mm.get(k, 0) + v
        out["inputs"] = {"cuts": sum(r["inputs"]["cuts"] for r in reps),
                         "arrays_compared": sum(r["inputs"]["arrays_compared"] for r in reps), "mismatches": mm}
        if mm:
            fails.append("inputs")
    if "kernel" in parts:
        cover = {k: sum(r["coverage"][k] for r in reps) for k in CUT_TYPES}
        k = {"pairs": sum(r["kernel"]["pairs"] for r in reps),
             "runs_compared_per_version": sum(r["kernel"]["runs_compared_per_version"] for r in reps),
             "pairs_differing": sum(r["kernel"]["pairs_differing"] for r in reps), "coverage": cover}
        k["coverage_ok"] = k["pairs"] >= 300 and all(cover[t] >= n for t, n in NEEDED.items())
        out["kernel"] = k
        if k["pairs_differing"] or not k["coverage_ok"]:
            fails.append("kernel")
    viol: dict = {}
    rate_bad = inc_bad = 0
    runs = 0
    for r in reps:
        a = r.get("audit", {})
        runs += a.get("runs", 0)
        rate_bad += a.get("rate_dated_after_previous_bar", 0)
        inc_bad += a.get("income_known_by_period_end", 0)
        for kk, v in a.get("violations", {}).items():
            viol[kk] = viol.get(kk, 0) + v
    sig = audit_signature()
    out["audit"] = {"runs": runs, "violations": viol, "rate_dated_after_previous_bar": rate_bad,
                    "income_known_by_period_end": inc_bad, "earnings_inputs_of_the_simulator": sig}
    if viol or rate_bad or inc_bad or sig:
        fails.append("audit")
    out["failed_parts"] = fails
    out["passes"] = not fails
    return out


def cmd_one(args) -> int:
    t0 = time.time()
    syms = R.symbols("main")
    if args.symbols:
        syms = args.symbols
    report = {"what": "self-test 1 of references/ladder_v3_spec.md S15 (no future data); pass/fail and coverage "
                      "counts only, no returns", "data_end": R.DATA_END}
    base = run_test(syms, 0, ("inputs", "kernel", "audit"), args.per_type, args.workers, args.quick)
    report["main"] = base
    print("leak 0:", "pass" if base["passes"] else f"FAIL {base['failed_parts']}", round(time.time() - t0), "s",
          flush=True)
    controls = {}
    for leak, what in LEAKS.items():
        parts = ("inputs", "audit") if leak in INPUT_LEAKS else ("kernel", "audit")
        r = run_test(syms, leak, parts, args.per_type, args.workers, args.quick)
        controls[str(leak)] = {"leak": what, "caught": not r["passes"], "failed_parts": r["failed_parts"],
                               "detail": {k: r[k] for k in ("inputs", "kernel", "audit") if k in r}}
        print(f"leak {leak} ({what}):", "caught by " + ",".join(r["failed_parts"]) if not r["passes"] else "NOT CAUGHT",
              round(time.time() - t0), "s", flush=True)
    report["positive_controls"] = controls
    report["seeds"] = seeds_check(syms)
    print("seeds:", report["seeds"], flush=True)
    report["passes"] = bool(base["passes"] and all(c["caught"] for c in controls.values())
                            and report["seeds"]["passes"])
    report["seconds"] = round(time.time() - t0)
    if not args.quick and not args.symbols:
        OUT.write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    print("self-test 1:", "PASS" if report["passes"] else "FAIL", flush=True)
    return 0 if report["passes"] else 1


# ============================================================================= self-test 2
LARGE_CAPS = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA")
FUNDS = ("SPY", "QQQ", "IWM", "GLD", "TLT", "XLE", "XLU")
N_SOURCES = 20
BLOCK_MEAN = 40
PLANT_K = 4
HOLD_LOW, HOLD_CLOSE, UP_BARS, PLACEBO_AT = 0.005, 0.01, 10, 1.03
WORLDS = tuple([("0", r) for r in range(1, 11)] + [("0.5", r) for r in range(1, 6)] +
               [("1", r) for r in range(1, 6)] + [("placebo", r) for r in range(1, 6)])
Q = {"0": 0.0, "0.5": 0.5, "1": 1.0, "placebo": 1.0}
BREAK_BARS = 20
OUT2 = R.V3 / "selftest2.json"


def st2_sources(mk: S.Market) -> list:
    """S15: 20 previously tested stocks with a bar on each of SPY's dates and no move over 50% (no jump
    to check), not large caps and not funds, chosen with seed MASTER."""
    elig = []
    for sym in R.symbols("prev"):
        if sym in LARGE_CAPS or sym in FUNDS:
            continue
        df, _, rep = S.clean_bars(S.read_rows(R.DATA / "prev" / "full" / f"{sym}.json"), [], [])
        d = df.index.values.astype("datetime64[D]")
        if len(d) == len(mk.spy_dates) and bool((d == mk.spy_dates).all()) and not rep["jumps"]:
            elig.append(sym)
    rng = np.random.Generator(np.random.PCG64(S.seed_of(f"{S.MASTER}|selftest2|sources")))
    return sorted(elig[i] for i in rng.permutation(len(elig))[:N_SOURCES])


def world_index(rep: int, n: int) -> np.ndarray:
    """A stationary bootstrap of source bars 1..n-1 (mean block BLOCK_MEAN), common to every stock and SPY of
    the replication; bar 0 is the real first bar."""
    rng = np.random.Generator(np.random.PCG64(S.seed_of(f"{S.MASTER}|selftest2|world|{rep}")))
    new = rng.random(n) < 1.0 / BLOCK_MEAN
    start = rng.integers(1, n, size=n)
    idx = np.zeros(n, dtype=np.int64)
    for j in range(1, n):
        if j == 1 or new[j]:
            idx[j] = start[j]
        else:
            idx[j] = idx[j - 1] + 1 if idx[j - 1] + 1 < n else 1
    return idx


def month_starts(dates: np.ndarray) -> np.ndarray:
    di = pd.DatetimeIndex(dates)
    m = (di.year * 12 + di.month).to_numpy()
    return np.r_[False, m[1:] != m[:-1]]


def synth_path(src: tuple, idx: np.ndarray, q: float, placebo: bool, unif: np.ndarray, ms: np.ndarray) -> tuple:
    """S15: the synthetic bars of one stock, built bar by bar from the source's ratios to the previous close on
    the world's dates, with the planted holds. Returns (o, h, l, c, v, holds)."""
    o, h, l, c, v = src
    n = len(idx)
    so, sh, sl, sc, sv = (np.empty(n) for _ in range(5))
    so[0], sh[0], sl[0], sc[0], sv[0] = o[0], h[0], l[0], c[0], v[0]
    levels, used = [], []
    up, mi, holds = 0, -1, 0
    px, lo = np.empty(S.K_MAX + 1), np.empty(S.K_MAX + 1)
    for j in range(1, n):
        k = idx[j]
        prev = sc[j - 1]
        if up > 0:                                   # after a hold: absolute returns, the bar's shape kept
            r = c[k] / c[k - 1]
            cj = prev * max(r, 1.0 / r)
            so[j], sh[j], sl[j], sc[j] = cj * o[k] / c[k], cj * h[k] / c[k], cj * l[k] / c[k], cj
            up -= 1
        else:
            f = prev / c[k - 1]
            so[j], sh[j], sl[j], sc[j] = o[k] * f, h[k] * f, l[k] * f, c[k] * f
        sv[j] = v[k]
        for li, p in enumerate(levels):              # the first bar whose low would go below a planted level
            if used[li] or not sl[j] < p:
                continue
            used[li] = True
            u_hold, u_low, u_close = unif[mi, li]
            if u_hold < q:
                sl[j] = p * (1.0 - HOLD_LOW * u_low)
                so[j] = max(so[j], sl[j])
                sc[j] = max(sc[j], p * (1.0 + HOLD_CLOSE * u_close))
                sh[j] = max(sh[j], so[j], sc[j])
                up = UP_BARS
                holds += 1
                break
        if ms[j] and j >= S.LOOKBACK - 1:            # the ladder at this month start's close (S4, k = 4)
            mi += 1
            levels, used = [], []
            if q > 0:
                win = pd.DataFrame({"high": sh[j - S.LOOKBACK + 1:j + 1], "low": sl[j - S.LOOKBACK + 1:j + 1],
                                    "close": sc[j - S.LOOKBACK + 1:j + 1], "volume": sv[j - S.LOOKBACK + 1:j + 1]})
                zs = NZ.node_zones(win, S.LOOKBACK)
                zl = np.array([z["zone_low"] for z in zs] or [np.nan])
                zh = np.array([z["zone_high"] for z in zs] or [np.nan])
                m = S.zone_ladder(zl, zh, len(zs), sc[j], PLANT_K, px, lo, np.nan, np.nan)
                levels = [float(px[i]) * (PLACEBO_AT if placebo else 1.0) for i in range(m)]
                used = [False] * m
    return so, sh, sl, sc, sv, holds


def synth_stock(name: str, dates: np.ndarray, bars: tuple, spy: np.ndarray, mk: S.Market) -> S.StockData:
    n = len(dates)
    rate, rate_src = S.interest_rates(dates, mk)
    o, h, l, c, v = bars
    return S.StockData(name, dates, o, h, l, c, v, np.zeros(n, bool), np.zeros(n), rate, rate_src, spy,
                       np.ones(n, bool), np.full(n, np.nan), np.full(n, np.nan), np.array([], "datetime64[D]"),
                       n - 1, False, False, {})


def _st2_name(kind: str, rep: int, sym: str) -> str:
    return f"selftest2|q{kind}|w{rep}|{sym}"


def _st2_world(kind: str, rep: int, sym: str, mk: S.Market):
    """The synthetic stock (cached path and zones) and the plans' random tags (common to every q)."""
    cache = R.CACHE / "selftest2"
    cache.mkdir(parents=True, exist_ok=True)
    name = _st2_name(kind, rep, sym)
    fpath = cache / (name.replace("|", "_") + "_path.npz")
    dates = mk.spy_dates
    if fpath.exists():
        z = np.load(fpath)
        bars, spy, holds = (z["o"], z["h"], z["l"], z["c"], z["v"]), z["spy"], int(z["holds"])
    else:
        src_df, _, _ = S.clean_bars(S.read_rows(R.DATA / "prev" / "full" / f"{sym}.json"), [], [])
        src = tuple(src_df[k].to_numpy(float) for k in ("open", "high", "low", "close")) + \
            (src_df["volume"].fillna(0).to_numpy(float),)
        idx = world_index(rep, len(dates))
        unif = np.random.Generator(np.random.PCG64(S.seed_of(f"{S.MASTER}|selftest2|plant|{rep}|{sym}"))).random(
            (400, PLANT_K, 3))
        o, h, l, c, v, holds = synth_path(src, idx, Q[kind], kind == "placebo", unif, month_starts(dates))
        sc = mk.spy_close
        spy = np.empty(len(dates))
        spy[0] = sc[0]
        for j in range(1, len(dates)):
            spy[j] = spy[j - 1] * sc[idx[j]] / sc[idx[j] - 1]
        bars = (o, h, l, c, v)
        np.savez_compressed(fpath, o=o, h=h, l=l, c=c, v=v, spy=spy, holds=holds)
    data = synth_stock(f"selftest2|w{rep}|{sym}", dates, bars, spy, mk)
    prep = S.prepare(data, cache_file=cache / (name.replace("|", "_") + "_zones.npz"))
    return prep, holds


def st2_phase1(job) -> dict:
    """Path, zones and the start ladders' depths (k = 5) of one synthetic stock."""
    kind, rep, sym = job
    mk = R.market()
    prep, holds = _st2_world(kind, rep, sym, mk)
    ts = S.plan_starts(prep, MAIN)[0]
    depths = []
    for t in ts:
        px, _ = S.start_ladder(prep, t, S.K_MAX)
        depths.append(np.r_[1.0 - px / prep.data.c[t], np.full(S.K_MAX - len(px), np.nan)])
    return {"kind": kind, "rep": rep, "sym": sym, "holds": holds, "plans": len(ts),
            "depths": np.array(depths).reshape(-1, S.K_MAX)}


def st2_phase2(job) -> dict:
    """All main groups on every plan of one synthetic stock: R at 126 and 252 bars for zone and the random
    mean, and the zone fills broken within 20 bars."""
    kind, rep, sym, fixed = job
    mk = R.market()
    prep, _ = _st2_world(kind, rep, sym, mk)
    d = prep.data
    ts = S.plan_starts(prep, MAIN)[0]
    rows = []
    fills = broken = 0
    growth = S.delist_growth(d, mk)
    for t in ts:
        res, _ = S.run_plan(prep, t, MAIN, MAIN_GROUPS, fixed_depth=fixed)
        rz = S.plan_returns(res["zone"][0][0], t, d, growth)
        rr = np.mean([S.plan_returns(o_, t, d, growth) for o_, _, _ in res["random"]], axis=0)
        ex = [S.excluded(prep, t, int(hh), growth) is not None for hh in S.HORIZONS]
        rows.append((t, pd.Timestamp(d.dates[t]).year, ex[1], ex[2], rz[1], rr[1], rz[2], rr[2]))
        for e in res["zone"][0][1]:
            if int(e[1]) != S.EV_FILL:
                continue
            b, z = int(e[0]), e[6]
            if b + BREAK_BARS > d.last or not np.isfinite(z):
                continue
            fills += 1
            cc = d.c[b:b + BREAK_BARS + 1]
            below = cc < z
            broken += bool((below[1:] & below[:-1]).any())
    return {"kind": kind, "rep": rep, "sym": sym, "rows": rows, "fills": fills, "broken": broken}


def st2_world_stats(rows: list) -> dict:
    import ladder_stats as T
    a = np.array([r for r in rows if not r[3]], dtype=float)          # counted at 252 bars
    diff = a[:, 6] - a[:, 7]
    blocks = T.Blocks(a[:, 1].astype(int))
    m = T.mean_diff(diff, blocks)
    th126 = float((a[:, 4] - a[:, 5]).mean())
    sc = T.screen(m, th126, m["theta"])                               # every synthetic stock is one of "the 43"
    return {"theta": m["theta"], "lo_screen": m["lo_screen"], "se": m["se"], "G": m["G"], "plans": int(len(a)),
            "theta_126": th126, "passes_screen": sc["passes"]}


def cmd_two(args) -> int:
    import ladder_stats as T
    t0 = time.time()
    mk = R.market()
    srcs = st2_sources(mk)
    worlds = WORLDS if not args.quick else (("0", 1), ("1", 1))
    syms = srcs if not args.quick else srcs[:3]
    jobs = [(k, r, s) for k, r in worlds for s in syms]
    p1 = []
    with Pool(args.workers) as pool:
        for x in pool.imap_unordered(st2_phase1, jobs, chunksize=1):
            p1.append(x)
            if len(p1) % 25 == 0:
                print(f"paths and zones {len(p1)}/{len(jobs)}, {round(time.time() - t0)} s", flush=True)
    print("paths and zones", round(time.time() - t0), "s", flush=True)
    fixed = {}
    for k, r in worlds:
        dep = np.vstack([x["depths"] for x in p1 if (x["kind"], x["rep"]) == (k, r)])
        fixed[(k, r)] = np.array([np.nanmedian(dep[:, i]) if np.isfinite(dep[:, i]).any() else 0.0
                                  for i in range(S.K_MAX)])
    jobs2 = [(k, r, s, fixed[(k, r)]) for k, r, s in jobs]
    p2 = []
    with Pool(args.workers) as pool:
        for x in pool.imap_unordered(st2_phase2, jobs2, chunksize=1):
            p2.append(x)
            if len(p2) % 25 == 0:
                print(f"simulations {len(p2)}/{len(jobs2)}, {round(time.time() - t0)} s", flush=True)
    print("simulations", round(time.time() - t0), "s", flush=True)
    per_world = {}
    for k, r in worlds:
        part = [x for x in p2 if (x["kind"], x["rep"]) == (k, r)]
        rows = [row for x in part for row in x["rows"]]
        ws = st2_world_stats(rows)
        ws["fills"] = sum(x["fills"] for x in part)
        ws["broken"] = sum(x["broken"] for x in part)
        ws["holds"] = sum(x["holds"] for x in p1 if (x["kind"], x["rep"]) == (k, r))
        ws["fixed_depths"] = [round(float(x), 4) for x in fixed[(k, r)]]
        per_world[f"q={k} rep {r}"] = ws
    pooled = {}
    for k in ("0", "0.5", "1", "placebo"):
        ws = [v for kk, v in per_world.items() if kk.startswith(f"q={k} ")]
        if not ws:
            continue
        th = np.array([w["theta"] for w in ws])
        n = len(th)
        half = T.t_ppf(0.95, n - 1) * th.std(ddof=1) / math.sqrt(n) if n > 1 else float("nan")
        pooled[k] = {"replications": n, "theta": float(th.mean()), "lo": float(th.mean() - half),
                     "hi": float(th.mean() + half), "screen_passes": int(sum(w["passes_screen"] for w in ws)),
                     "break_share": sum(w["broken"] for w in ws) / max(1, sum(w["fills"] for w in ws))}
    gates = {}
    if all(k in pooled for k in ("0", "0.5", "1", "placebo")):
        g0, g5, g1, gp = pooled["0"], pooled["0.5"], pooled["1"], pooled["placebo"]
        gates = {"q0_theta_within_0.3pp": abs(g0["theta"]) <= 0.003,
                 "q0_screen_at_most_1_of_10": g0["screen_passes"] <= 1,
                 "q1_screen_at_least_4_of_5": g1["screen_passes"] >= 4, "q1_theta_at_least_1pp": g1["theta"] >= 0.01,
                 "q0.5_interval_above_0": g5["lo"] > 0,
                 "theta_rises_with_q": g0["theta"] < g5["theta"] < g1["theta"],
                 "placebo_theta_at_most_0.3pp": gp["theta"] <= 0.003,
                 "break_share_falls_with_q": g0["break_share"] > g5["break_share"] > g1["break_share"]}
    report = {"what": "self-test 2 of references/ladder_v3_spec.md S15 (planted levels that truly hold), "
                      "synthetic stocks only", "sources": srcs, "worlds": per_world, "pooled": pooled, "gates": gates,
              "passes": bool(gates) and all(gates.values()), "seconds": round(time.time() - t0)}
    if not args.quick:
        OUT2.write_text(json.dumps(report, indent=1, default=float) + "\n", encoding="utf-8")
    print(json.dumps({"pooled": pooled, "gates": gates, "passes": report["passes"]}, indent=1, default=float))
    return 0 if report["passes"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    one = sub.add_parser("one", help="self-test 1: no future data")
    one.add_argument("--symbols", nargs="*")
    one.add_argument("--per-type", type=int, default=2)
    one.add_argument("--workers", type=int, default=4)
    one.add_argument("--quick", action="store_true", help="fewer cuts, nothing written (development)")
    one.set_defaults(func=cmd_one)
    two = sub.add_parser("two", help="self-test 2: planted levels that truly hold")
    two.add_argument("--workers", type=int, default=4)
    two.add_argument("--quick", action="store_true", help="two worlds, three stocks, nothing written (development)")
    two.set_defaults(func=cmd_two)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
