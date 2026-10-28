#!/usr/bin/env python3
"""
Driver of the ladder test V3 (references/ladder_v3_spec.md): loads the frozen FMP data, builds and
caches each stock's zones, and runs the steps of the plan.

  python scripts/ladder_run.py zones --set main          zones(d) of every bar, cached per stock
  python scripts/ladder_run.py ladders --set main        D_1..D_5 of the fixed group (S6, step 4)
  python scripts/ladder_run.py power --set main          the power check (S14.2, step 5)
  python scripts/ladder_run.py simulate --set main       every configuration of S13 / 14.3, cached per stock
  python scripts/ladder_run.py report --set main         the main test, the secondary items and the rest
  python scripts/ladder_run.py confirm --set holdout     step 7: the confirmation set on the held-out stocks

The data folder holds `<set>/<part>/<SYMBOL>.json` (sets main, spares, prev; parts full, adj, div,
split, earn, mcap, profile, income) and `macro/treasury.json`, as fetched in step 2
(`references/ladder_v3/data_manifest.json`). Held-out data are never read here before step 7.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ladder_sim as S  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
V3 = REPO / "references" / "ladder_v3"
DATA = Path(os.environ.get("LADDER_DATA", "/home/claude/v3data"))
CACHE = Path(os.environ.get("LADDER_CACHE", "/home/claude/v3cache"))
DATA_END = "2026-10-27"
SETS = ("main", "spares", "prev", "holdout")  # held-out data stay on the user's computer until step 7


def market() -> S.Market:
    return S.Market.load(S.read_rows(DATA / "main" / "full" / "SPY.json"),
                         S.read_rows(DATA / "macro" / "treasury.json"), DATA_END)


def sample() -> dict:
    return json.loads((V3 / "sample.json").read_text(encoding="utf-8"))


def symbols(set_name: str) -> list:
    if set_name in ("main", "holdout"):
        return (V3 / f"{set_name}.txt").read_text().split()
    if set_name not in SETS:
        raise SystemExit(f"unknown set {set_name}")
    return sorted(p.stem for p in (DATA / set_name / "full").glob("*.json") if p.stem != "SPY")


def entity(symbol: str, set_name: str, smp: dict | None = None) -> dict:
    """Membership spans and bars_from of a drawn company; previously tested stocks have no spans here
    (self-test 2 treats its synthetic stocks as members)."""
    if set_name == "prev":
        return {"series": symbol, "spans": [], "bars_from": None}
    smp = smp or sample()
    return smp["drawn"][symbol]


def build(symbol: str, set_name: str, mk: S.Market, cut=None, leak: int = 0, smp=None) -> S.StockData:
    return S.build_stock(symbol, DATA / set_name, entity(symbol, set_name, smp), mk, cut=cut, leak=leak)


def zone_file(symbol: str, set_name: str) -> Path:
    return CACHE / f"{symbol}_{set_name}.npz"


def prepared(symbol: str, set_name: str, mk: S.Market, leak: int = 0, smp=None) -> S.Prepared:
    data = build(symbol, set_name, mk, leak=leak, smp=smp)
    f = zone_file(symbol, set_name)
    if not f.exists():          # an ordinary exception: a SystemExit in a pool worker would hang the pool
        raise FileNotFoundError(f"no zone cache for {symbol} ({set_name}): run `zones --set {set_name}` first")
    return S.prepare(data, cache_file=f, leak=leak)


def _zones_job(job):
    symbol, set_name = job
    t0 = time.time()
    mk = market()
    data = build(symbol, set_name, mk)
    S.zone_arrays(data, cache_file=zone_file(symbol, set_name))
    return symbol, len(data.c), round(time.time() - t0, 1)


def cmd_zones(args) -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    syms = args.symbols or symbols(args.set)
    jobs = [(s, args.set) for s in syms]
    with Pool(args.workers) as pool:
        for sym, n, sec in pool.imap_unordered(_zones_job, jobs):
            print(f"{sym}: {n} bars, {sec}s", flush=True)
    return 0


def cmd_ladders(args) -> int:
    """S6, step 4: D_i = the median depth 1 - L_i / C[t] of the i-th level of the zone ladders (k = 5) at every
    plan start of the sample, pooled over stocks. Ladders only: nothing is simulated."""
    mk = market()
    smp = sample()
    rows = []
    for sym in symbols(args.set):
        prep = prepared(sym, args.set, mk, smp=smp)
        for t in S.plan_starts(prep, S.Config())[0]:
            px, _ = S.start_ladder(prep, t, S.K_MAX)
            rows.append(np.r_[1.0 - px / prep.data.c[t], np.full(S.K_MAX - len(px), np.nan)])
    dep = np.array(rows)
    out = {"what": "fixed group depths D_1..D_5 (spec S6): median depth of the i-th zone-ladder level at the plan "
                   "starts, k = 5, ladders only", "set": args.set, "plans": int(len(dep)),
           "levels_present": [int(np.isfinite(dep[:, i]).sum()) for i in range(S.K_MAX)],
           "D": [round(float(np.nanmedian(dep[:, i])), 6) for i in range(S.K_MAX)]}
    print(json.dumps(out, indent=1))
    if args.write:
        (V3 / "fixed_depths.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return 0


def fixed_depths() -> np.ndarray:
    f = V3 / "fixed_depths.json"
    if not f.exists():
        raise FileNotFoundError("references/ladder_v3/fixed_depths.json is missing: run `ladders --write` (step 4)")
    return np.array(json.loads(f.read_text())["D"], dtype=float)


def runs_dir(set_name: str) -> Path:
    return CACHE / "runs" / set_name


def _simulate_job(job):
    import ladder_analysis as A
    symbol, set_name, config = job
    cfg, groups = A.CONFIGS[config]
    mk = market()
    prep = prepared(symbol, set_name, mk)
    placebos = A.PLACEBOS if config == "power" else ()
    res = S.run_stock(prep, mk, cfg, groups=groups, fixed_depth=fixed_depths() if "fixed" in groups else None,
                      placebos=placebos)
    # the power step keeps no outcome of the zone group (it runs only for the floor, S14.2)
    A.save_stock(res, runs_dir(set_name) / A.file_name(config) / f"{symbol}.npz",
                 drop=("zone",) if config == "power" else ())
    return symbol, config, len(res["plan"]["t"])


def simulate(set_name: str, configs: list, workers: int) -> None:
    missing = [s for s in symbols(set_name) if not zone_file(s, set_name).exists()]
    if missing:
        raise SystemExit(f"no zone cache for {', '.join(missing)} ({set_name}): run `zones --set {set_name}` first")
    if any("fixed" in A_groups(c) for c in configs):
        fixed_depths()
    jobs = [(s, set_name, c) for c in configs for s in symbols(set_name)]
    t0 = time.time()
    with Pool(workers) as pool:
        for i, (sym, cfg, n) in enumerate(pool.imap_unordered(_simulate_job, jobs, chunksize=1), 1):
            if i % 50 == 0 or i == len(jobs):
                print(f"{i}/{len(jobs)} runs ({cfg}, {sym}: {n} plans), {round(time.time() - t0)}s", flush=True)


def cmd_power(args) -> int:
    import ladder_analysis as A
    simulate(args.set, ["power"], args.workers)
    run = A.load_run(runs_dir(args.set), "power", symbols(args.set))
    out = A.power(run)
    out["what"] = "power check of spec S14.2: placebo k against the 20-draw random mean, R_252"
    out["passes_1pp"] = bool(out["mde80"] <= 0.01)
    print(json.dumps({k: out[k] for k in ("G", "plans", "se_q80", "factor", "mde80", "passes_1pp")}, indent=1))
    (V3 / f"power_{args.set}.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return 0


def cmd_simulate(args) -> int:
    import ladder_analysis as A
    configs = args.configs or [c for c in A.CONFIGS if c != "power"]
    simulate(args.set, configs, args.workers)
    return 0


def A_groups(config: str) -> tuple:
    import ladder_analysis as A
    return A.CONFIGS[config][1]


def _json(x):
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, np.integer):
        return int(x)
    return float(x)


def cmd_report(args) -> int:
    import ladder_analysis as A
    syms = symbols(args.set)
    runs = {c: A.load_run(runs_dir(args.set), c, syms) for c in A.CONFIGS if c != "power"}
    pw = json.loads((V3 / f"power_{args.set}.json").read_text()) if (V3 / f"power_{args.set}.json").exists() else None
    main = A.main_test(runs["main"], se_q80=pw["se_q80"] if pw else None)
    valuation = not args.no_valuation
    items = A.secondary(runs, valuation=valuation)
    out = {"what": f"ladder test V3 on the {args.set} sample (spec S14)", "main": main, "secondary": items,
           "valuation_items": valuation, "holm_m": len(items),
           "descriptive": A.descriptive(runs), "confirmation_set": A.confirmation_set(main, items),
           "power": {k: pw[k] for k in ("G", "se_q80", "mde80")} if pw else None,
           "starts_skipped": runs["main"].skipped}
    (V3 / f"results_{args.set}.json").write_text(json.dumps(out, indent=1, default=_json) + "\n", encoding="utf-8")
    print(json.dumps({"theta": main["theta"], "screen": main["screen"]}, indent=1, default=_json))
    return 0


def cmd_confirm(args) -> int:
    """S14.4, step 7: the confirmation set of results_main.json on the held-out runs (simulated first)."""
    import ladder_analysis as A
    res = json.loads((V3 / "results_main.json").read_text())
    conf = [(i if i == "main" else int(i), float(sg)) for i, sg in res["confirmation_set"]]
    if not conf:
        print("the confirmation set is empty: nothing to confirm")
        return 0
    syms = symbols(args.set)
    need = {"main"} | {c for c in A.CONFIGS if c != "power"} if any(i != "main" for i, _ in conf) else {"main"}
    runs = {c: A.load_run(runs_dir(args.set), c, syms) for c in need}
    out = {"what": f"S14.4 one-sided check on the {args.set} stocks", "set": conf,
           "results": A.confirm(runs, conf, valuation=res.get("valuation_items", True))}
    (V3 / f"confirm_{args.set}.json").write_text(json.dumps(out, indent=1, default=_json) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=1, default=_json))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    z = sub.add_parser("zones", help="cache zones(d) of every bar")
    z.add_argument("--set", default="main", choices=SETS)
    z.add_argument("--symbols", nargs="*")
    z.add_argument("--workers", type=int, default=4)
    z.set_defaults(func=cmd_zones)
    ld = sub.add_parser("ladders", help="D_1..D_5 of the fixed group from the start ladders (step 4)")
    ld.add_argument("--set", default="main", choices=SETS)
    ld.add_argument("--write", action="store_true", help="write references/ladder_v3/fixed_depths.json")
    ld.set_defaults(func=cmd_ladders)
    pw = sub.add_parser("power", help="the power check before the main test (S14.2)")
    pw.add_argument("--set", default="main", choices=SETS)
    pw.add_argument("--workers", type=int, default=4)
    pw.set_defaults(func=cmd_power)
    sm = sub.add_parser("simulate", help="run the configurations of S13 / 14.3 on every plan")
    sm.add_argument("--set", default="main", choices=SETS)
    sm.add_argument("--configs", nargs="*")
    sm.add_argument("--workers", type=int, default=4)
    sm.set_defaults(func=cmd_simulate)
    rp = sub.add_parser("report", help="the statistics of S14 on the cached runs")
    rp.add_argument("--set", default="main", choices=SETS)
    rp.add_argument("--no-valuation", action="store_true", help="leave out items 22-25 (m = 23, S14.3)")
    rp.set_defaults(func=cmd_report)
    cf = sub.add_parser("confirm", help="step 7: the confirmation set on the held-out runs (S14.4)")
    cf.add_argument("--set", default="holdout", choices=SETS)
    cf.set_defaults(func=cmd_confirm)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
