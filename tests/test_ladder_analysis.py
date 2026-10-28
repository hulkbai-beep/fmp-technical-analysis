"""Tests for scripts/ladder_analysis.py on hand-made plan tables (no network, no FMP data, no simulation)."""
import math

import numpy as np
import pytest

import ladder_analysis as A
import ladder_sim as S
import ladder_stats as T

N_DRAWS = {"zone": 1, "random": 20, "gaps": 20, "vol": 1, "fixed": 1, "lump": 1, "placebo": 20}


def make_run(config, groups, syms, months, k, seed=0):
    """A Run with random outcomes: monthly plans per stock, plans past the data end excluded."""
    rng = np.random.default_rng(seed)
    sym = np.repeat(np.array(syms), len(months))
    date = np.tile(months, len(syms)).astype("datetime64[D]")
    t = np.tile(800 + 21 * np.arange(len(months)), len(syms)).astype(np.int64)
    n = len(t)
    years = date.astype("datetime64[Y]").astype(int) + 1970
    end = np.datetime64("2026-10-27")
    excl = np.zeros((n, 4), np.int64)
    hdate = np.empty((n, 4), "datetime64[D]")
    for i, h in enumerate(S.HORIZONS):
        hd = date + np.timedelta64(int(h * 365 / 252), "D")
        excl[:, i] = np.where(hd > end, 1, 0)
        hdate[:, i] = np.where(hd > end, np.datetime64("NaT", "D"), hd)
    pe_def = rng.random(n) < 0.8
    pe_a, pe_b = pe_def & (rng.random(n) < 0.5), pe_def & (rng.random(n) < 0.1)
    g = {}
    for gr in groups:
        D = N_DRAWS[gr]
        R = rng.normal(0.08, 0.25, (n, D, 4))
        R[np.repeat(excl.astype(bool)[:, None, :], D, axis=1)] = np.nan
        out = np.zeros((n, D, 4, 6), np.float32)
        fills = rng.integers(0, 4, (n, D, 4)) if gr != "lump" else np.ones((n, D, 4))
        out[..., 0] = np.where(fills > 0, rng.random((n, D, 4)), 0.0)
        out[..., 1] = np.where(fills > 0, 100 * (1 - 0.05 * rng.random((n, D, 4))), np.nan)
        out[..., 2] = np.where(fills > 0, -0.2 * rng.random((n, D, 4)), 0.0)
        out[..., 3] = (fills > 0) & (rng.random((n, D, 4)) < 0.2)
        out[..., 4] = fills
        earn = np.where(rng.random((n, D)) < 0.05, t[:, None] + rng.integers(0, 600, (n, D)), S.NO_BAR)
        start_px = 100 * (1 - 0.05 * np.arange(1, k + 1))[None, None, :] * (1 + 0.01 * rng.normal(size=(n, D, 1)))
        g[gr] = {"R": R, "out": out, "earn": earn, "start_px": start_px,
                 "cap": (rng.random((n, D)) < 0.1).astype(np.int64),
                 "first_fill": np.where(rng.random((n, D)) < 0.3, -1, rng.integers(1, 200, (n, D))),
                 "placements": rng.integers(1, 4, (n, D)), "capped": rng.integers(0, 2, (n, D))}
    cleaning = {s: {"jumps": [], "delisted": False, "data_gap": False} for s in syms}
    return A.Run(config, sym, t, date, years, np.full(n, 100.0), excl, hdate, pe_def, pe_a, pe_b, g,
                 {"below the 200-day average": 3}, cleaning)


def all_runs():
    syms = [f"S{i:02d}" for i in range(8)] + ["AAPL", "MSFT"]
    months = np.arange(np.datetime64("2008-02"), np.datetime64("2026-10")).astype("datetime64[D]")
    runs = {}
    for c, (cfg, groups) in A.CONFIGS.items():
        if c != "power":
            runs[c] = make_run(c, groups, syms, months, len(cfg.weights()), seed=len(runs))
    return runs


def test_reports_on_synthetic_runs():
    runs = all_runs()
    main = A.main_test(runs["main"])
    assert set(main["avgcost_minus_zone"]) == {"random", "gaps", "vol", "fixed", "lump"}
    assert "up_placements_capped_all" in main["random_shift"] and "mean_depth" in main["gaps_shift"]
    items = A.secondary(runs)
    assert sorted(items) == list(range(1, 28))
    short = A.secondary(runs, valuation=False)
    assert sorted(short) == [i for i in range(1, 28) if i not in A.VALUATION_ITEMS]
    # Holm over 23 items: every adjusted p is at most the one over 27
    assert all(short[i]["p_holm"] <= items[i]["p_holm"] + 1e-12 for i in short)
    de = A.descriptive(runs)
    assert set(de["metrics_zone_minus_group"]["random"]) == set(A.METRICS)
    assert set(de["time_limit_no_fill_in_42"]) == {"zone - random", "time limit (zone) - main (zone)", "share_of_plans"}
    assert de["cleaning"]["plans_by_horizon"][252]["counted"] == int(runs["main"].counted(252).sum())
    conf = A.confirm(runs, [("main", 1.0), (13, 1.0), (24, -1.0), (11, -1.0)])
    assert set(conf) == {"main", "13", "24", "11"} and all(v["level"] == 1 - 0.05 / 4 for v in conf.values())
    with pytest.raises(ValueError):
        A.confirm(runs, [("main", 1.0), (2, -1.0)])          # a benchmark comparison is not a rule (S14.4)
    x, m = A.theta(runs["main"], "zone", "random")
    mm = m & np.isfinite(x)
    want = T.one_sided_lower(x[mm], T.Blocks(runs["main"].year[mm]), 1.0, 4)
    assert math.isclose(conf["main"]["lo"], want["lo"])


def test_average_cost_is_taken_per_draw():
    """Up draws fill every plan, down draws only plans 0 and 1: per draw (and on the common subset) the
    random ladder costs the same as zone, though a per-plan mean over the draws that filled would not."""
    run = make_run("main", ("zone", "random"), ["S00"], np.array(["2010-02-01", "2010-03-01", "2011-02-01",
                                                                  "2011-03-01"], "datetime64[D]"), 4)
    zone, rnd = run.g["zone"]["out"], run.g["random"]["out"]
    zone[..., 1], zone[..., 4] = 95.0, 1
    rnd[:, 0::2, :, 1], rnd[:, 0::2, :, 4] = 96.0, 1
    rnd[:, 1::2, :, 1], rnd[:, 1::2, :, 4] = 94.0, 1
    rnd[2:, 1::2, :, 1], rnd[2:, 1::2, :, 4] = np.nan, 0
    run.excl[:] = 0
    run.g["zone"]["R"][:] = 0.0
    run.g["random"]["R"][:] = 0.0
    out = A.main_test(run)
    assert math.isclose(out["avgcost_minus_zone"]["random"], 0.0, abs_tol=1e-12)
    assert math.isclose(out["avgcost_minus_zone_common"]["random"], 0.0, abs_tol=1e-12)
    m = A.per_draw(run, "avgcost")
    assert math.isclose(m["theta"], 0.0, abs_tol=1e-12)


def test_confirmation_set_takes_the_rule_items_only():
    main = {"screen": {"passes": True}, "theta": {"theta": 0.02}}
    items = {i: {"p_holm": 0.01, "theta": 0.02 if i % 2 else -0.02} for i in range(1, 28)}
    cs = A.confirmation_set(main, items)
    assert [i for i, _ in cs] == ["main", 5, 6] + list(range(10, 20)) + [22, 23, 24, 25]
    assert dict(cs)[5] == 1.0 and dict(cs)[6] == -1.0


def test_without_earnings_leaves_out_plans_with_a_missing_report_date():
    runs = all_runs()
    main = runs["main"]
    base = A.secondary(runs)[21]
    n = len(main.t)
    main.report_unknown = np.zeros((n, 4), bool)
    main.report_unknown[: n // 3] = True
    item = A.secondary(runs)[21]
    x, mm = A.theta(main, "zone", "random")
    keep = mm & ~main.earnings_flag(252) & np.isfinite(x)
    assert base["n"] == int(keep.sum())
    assert item["n"] == int((keep & ~main.unknown(252)).sum()) < base["n"]
    assert item["share_left_out_report_date_missing"] > 0
