"""Tests for scripts/ladder_stats.py and the per-stock aggregation of scripts/ladder_sim.py (no network,
no FMP data)."""
import math

import numpy as np
import pytest

import ladder_sim as S
import ladder_stats as T
from test_ladder_sim import ZONES, codes, prep_of, stock


def test_student_t():
    assert math.isclose(T.t_ppf(0.95, 17), 1.7396067260750, rel_tol=1e-9)
    assert math.isclose(T.t_ppf(0.975, 10), 2.2281388519863, rel_tol=1e-9)
    assert math.isclose(T.t_ppf(0.95, 1), 6.3137515146750, rel_tol=1e-9)
    assert math.isclose(T.t_cdf(2.0, 5), 0.9490302605850709, rel_tol=1e-12)
    assert math.isclose(T.t_cdf(-2.0, 5), 1 - 0.9490302605850709, rel_tol=1e-9)
    assert T.t_ppf(0.5, 7) == 0.0 and math.isclose(T.t_ppf(0.05, 9), -T.t_ppf(0.95, 9))
    # the MDE80 factor of S14.2 for 18 blocks
    assert round(T.t_ppf(0.95, 17) + T.t_ppf(0.80, 17), 2) == 2.60


def test_blocks_table_is_fixed_by_the_block_set():
    a = T.Blocks(np.array([2008, 2008, 2009, 2011]))
    b = T.Blocks(np.array([2011, 2009, 2008]))
    c = T.Blocks(np.array([2008, 2009, 2010]))
    assert a.G == 3 and np.array_equal(a.table, b.table) and not np.array_equal(a.table, c.table)
    assert a.table.shape == (T.B, 3) and a.table.min() >= 0 and a.table.max() <= 2
    assert list(T.year_block(np.array([2008, 2009, 2010, 2025]), 2)) == [2008, 2008, 2010, 2024]


def test_mean_diff_interval():
    rng = np.random.default_rng(1)
    years = np.repeat(np.arange(2008, 2026), 30)
    year_effect = rng.normal(0, 0.02, 18)
    diff = 0.01 + np.repeat(year_effect, 30) + rng.normal(0, 0.01, len(years))
    blk = T.Blocks(years)
    m = T.mean_diff(diff, blk)
    assert math.isclose(m["theta"], diff.mean()) and m["G"] == 18 and m["n"] == len(diff)
    # the bootstrap SD of a mean of 18 block means is about sd(block means)/sqrt(18); SE adds sqrt(G/(G-1))
    bm = np.array([diff[years == y].mean() for y in range(2008, 2026)])
    assert abs(m["sd_boot"] / (bm.std(ddof=0) / math.sqrt(18)) - 1) < 0.05
    assert math.isclose(m["se"], math.sqrt(18 / 17) * m["sd_boot"])
    q = T.t_ppf(0.95, 17)
    assert math.isclose(m["lo"], m["theta"] - q * m["se"]) and m["lo_screen"] == min(m["lo"], m["lo_pct"])
    assert math.isclose(m["p"], 2 * (1 - T.t_cdf(abs(m["theta"]) / m["se"], 17)))


def test_unpaired_and_one_sided():
    rng = np.random.default_rng(2)
    la = np.repeat(np.arange(2008, 2020), 10)
    lb = np.repeat(np.arange(2010, 2024), 8)
    xa, xb = rng.normal(0.05, 0.1, len(la)), rng.normal(0.0, 0.1, len(lb))
    u = T.unpaired_diff(xa, la, xb, lb)
    assert math.isclose(u["theta"], xa.mean() - xb.mean()) and u["G"] == 16
    blk = T.Blocks(la)
    up = T.one_sided_lower(xa, blk, +1.0, K=1)
    dn = T.one_sided_lower(-xa, blk, -1.0, K=1)
    assert math.isclose(up["lo"], dn["lo"]) and up["level"] == 0.95
    assert T.one_sided_lower(xa, blk, +1.0, K=4)["lo"] < up["lo"]


def test_holm():
    p = {"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.20}
    h = T.holm(p, alpha=0.10)
    assert h["a"]["reject"] and math.isclose(h["a"]["p_holm"], 0.04)
    assert h["c"]["reject"] and math.isclose(h["c"]["p_holm"], 0.09)       # 3 * 0.03
    assert h["b"]["reject"] and math.isclose(h["b"]["p_holm"], 0.09)       # 0.04 <= 0.10 / 2; max(0.09, 0.08)
    assert not h["d"]["reject"] and math.isclose(h["d"]["p_holm"], 0.20)


def test_holm_with_a_missing_p():
    p = {1: 0.5, 2: float("nan"), 3: 0.001, 4: 0.002, 5: 0.0001}
    p.update({i: 0.9 for i in range(6, 28)})
    h = T.holm(p, alpha=0.10)
    assert h[5]["reject"] and h[3]["reject"] and h[4]["reject"]
    assert math.isclose(h[3]["p_holm"], 26 * 0.001) and math.isclose(h[4]["p_holm"], 25 * 0.002)
    assert not h[2]["reject"] and h[2]["p_holm"] == 1.0 and math.isnan(h[2]["p"])


def test_one_sided_unpaired():
    rng = np.random.default_rng(4)
    la, lb = np.repeat(np.arange(2008, 2020), 10), np.repeat(np.arange(2010, 2024), 8)
    xa, xb = rng.normal(0.03, 0.1, len(la)), rng.normal(0.0, 0.1, len(lb))
    K = 3
    up = T.one_sided_lower_unpaired(xa, la, xb, lb, +1.0, K)
    u = T.unpaired_diff(xa, la, xb, lb, level=1 - 2 * 0.05 / K)
    assert math.isclose(up["lo"], u["lo_screen"]) and math.isclose(up["theta_dir"], xa.mean() - xb.mean())
    G = u["G"]
    assert math.isclose(u["lo"], u["theta"] - T.t_ppf(1 - 0.05 / K, G - 1) * u["se"])
    dn = T.one_sided_lower_unpaired(xa, la, xb, lb, -1.0, K)
    assert math.isclose(dn["theta_dir"], xb.mean() - xa.mean()) and dn["level"] == 1 - 0.05 / K
    assert not dn["confirmed"]


def test_per_draw_mean_and_common_subset():
    years = np.repeat(np.arange(2010, 2016), 4)
    vz = np.tile([-0.05, -0.02, -0.10, np.nan], 6)                 # zone's average cost; NaN: no fill
    fz = np.isfinite(vz)
    vd = np.stack([np.tile([-0.04, np.nan, -0.08, -0.01], 6), np.tile([-0.06, -0.03, np.nan, np.nan], 6)], axis=1)
    fd = np.isfinite(vd)
    blk = T.Blocks(years)
    own = T.per_draw_metric(vz, fz, vd, fd, blk, "mean")
    want = np.nanmean(vz) - np.mean([np.nanmean(vd[:, 0]), np.nanmean(vd[:, 1])])
    assert math.isclose(own["theta"], want)
    com = T.per_draw_metric(vz, fz, vd, fd, blk, "mean", common=True)
    d0 = np.mean([-0.05 - -0.04, -0.10 - -0.08])                    # plans 0 and 2 of each block
    d1 = np.mean([-0.05 - -0.06, -0.02 - -0.03])                    # plans 0 and 1
    assert math.isclose(com["theta"], (d0 + d1) / 2)
    # win rate on the common subset: each draw against zone on the plans both filled
    rz = np.tile([0.1, -0.05, 0.2, -0.1], 6)
    rd = np.stack([np.tile([0.1, 0.1, -0.2, -0.1], 6), np.tile([0.05, -0.05, -0.05, -0.05], 6)], axis=1)
    fz2 = np.tile([True, True, True, False], 6)
    fd2 = np.stack([np.tile([True, False, True, True], 6), np.ones(24, bool)], axis=1)
    w = T.per_draw_metric(rz, fz2, rd, fd2, blk, "win_rate", common=True)
    want0 = 0.5 - 0.5                                               # plans 0, 2: zone wins both, draw 1 wins one
    want0 = np.mean([1.0, 1.0]) - np.mean([1.0, 0.0])
    want1 = np.mean([1.0, 0.0, 1.0]) - np.mean([1.0, 0.0, 0.0])
    assert math.isclose(w["theta"], (want0 + want1) / 2)


def test_mde80_and_screen():
    r = T.mde80(np.linspace(0.001, 0.005, 20), G=18)
    assert math.isclose(r["se_q80"], np.percentile(np.linspace(0.001, 0.005, 20), 80))
    assert math.isclose(r["mde80"], r["factor"] * r["se_q80"])
    main = {"theta": 0.015, "lo_screen": 0.002}
    assert T.screen(main, 0.004, 0.01)["passes"]
    assert not T.screen(main, -0.004, 0.01)["passes"]
    assert not T.screen({"theta": 0.009, "lo_screen": 0.002}, 0.004, 0.01)["passes"]


def test_per_draw_win_rate_and_payoff():
    years = np.repeat(np.arange(2010, 2016), 4)
    rz = np.tile([0.1, -0.05, 0.2, -0.1], 6)
    fz = np.ones(24, bool)
    rd = np.stack([np.tile([0.1, 0.1, -0.2, -0.1], 6), np.tile([0.05, -0.05, -0.05, -0.05], 6)], axis=1)
    fd = np.ones((24, 2), bool)
    fd[0, 1] = False                                        # a draw's plan without a fill is left out
    blk = T.Blocks(years)
    w = T.per_draw_metric(rz, fz, rd, fd, blk, "win_rate")
    wr_d = [0.5, 5 / 23]
    assert math.isclose(w["theta"], 0.5 - np.mean(wr_d))
    pf = T.per_draw_metric(rz, fz, rd, fd, blk, "payoff")
    pz = 0.15 / 0.075
    pd1 = 0.1 / 0.15
    r2 = rd[:, 1][fd[:, 1]]
    pd2 = r2[r2 > 0].mean() / abs(r2[r2 <= 0].mean())
    assert math.isclose(pf["theta"], pz - (pd1 + pd2) / 2)


def test_first_earnings_bar_matches_the_flag():
    g = np.zeros(10, bool)
    g[[3, 7]] = True
    ev = np.array([[2, S.EV_FILL, 97, 1, 0, 0, 95], [3, S.EV_FILL, 90, 1, 1, 0, 88],
                   [6, S.EV_STOP_SIGNAL, 80, 2, -1, 6, np.nan], [7, S.EV_STOP_SALE, 79, 2, -1, 6, np.nan]], float)
    first = S.first_earnings_bar(ev, g)
    assert first == 3
    for upto in range(10):
        assert S.earnings_flag(ev, g, upto) == (first <= upto)
    assert S.first_earnings_bar(ev[:1], g) == S.NO_BAR


def test_delisting_growth_and_exclusions():
    spy_rows = [{"date": str(np.datetime64("2010-01-04") + np.timedelta64(i, "D")), "close": 100.0} for i in range(30)]
    tsy = [{"date": "2010-01-01", "month3": 3.65}]
    mk = S.Market.load(spy_rows, tsy)
    c = np.full(12, 100.0)
    d = stock(c, last=11, delisted=True)
    d.dates = mk.spy_dates[:12].copy()
    g = S.delist_growth(d, mk)
    assert g[0] == 1.0 and math.isclose(g[1], 1 + 0.0365 / 365) and math.isclose(g[3], (1 + 0.0365 / 365) ** 3)
    assert np.isnan(g[19])                                   # 18 SPY bars follow the last bar
    p = prep_of(d, ZONES)
    assert S.excluded(p, 5, 20, g) is None and S.excluded(p, 5, 30, g) == "past the data end"
    out = np.full((4, S.N_OUT), np.nan)
    out[0, 0], out[0, 9] = 1.05, S.ST_DELISTED
    r = S.plan_returns(out, 0, d, g)
    assert math.isclose(r[0], 1.05 * g[63 - 11] - 1) if np.isfinite(g[52]) else np.isnan(r[0])
    d2 = stock(c)
    d2.suspicious[2] = True
    p2 = prep_of(d2, ZONES)
    assert S.excluded(p2, 5, 3) is None or S.excluded(p2, 5, 3) == "suspicious bars"


def test_start_mask_and_plan_starts():
    n = 900
    c = np.full(n, 100.0)
    d = stock(c)
    d.dates = np.datetime64("2007-01-01") + np.arange(n).astype("timedelta64[D]")
    d.sma200 = np.full(n, 99.0)
    d.member = np.ones(n, bool)
    d.last = n - 1
    p = prep_of(d, ZONES)
    m = S.start_mask(p, S.Config())
    starts, skipped = S.plan_starts(p, S.Config())
    firsts = [t for t in range(1, n) if d.dates[t].astype(object).month != d.dates[t - 1].astype(object).month
              and d.dates[t] >= np.datetime64("2008-02-01")]
    assert [t for t in np.flatnonzero(m >= 0)] == firsts
    assert starts == [t for t in firsts if t >= S.LOOKBACK - 1]
    assert skipped.get("fewer than 756 bars", 0) == sum(t < S.LOOKBACK - 1 for t in firsts)


def test_valuation_flags():
    n = 2000
    d = stock(np.full(n, 100.0))
    d.pe = np.r_[np.full(500, np.nan), np.linspace(10, 30, n - 500)]
    t = n - 1
    ok, a, b = S.valuation_flags(d, t)
    assert ok and not a and not b                          # the latest P/E is the highest of its window
    d.pe[t] = 11.0
    win = d.pe[t - 1260:t]
    q10 = np.quantile(win[np.isfinite(win)], 0.1)
    ok, a, b = S.valuation_flags(d, t)
    assert ok and a and (q10 <= 11.0 <= 1.1 * q10) == b
    d.pe[t] = np.nan
    assert S.valuation_flags(d, t) == (False, False, False)


def test_run_stock_shapes_and_returns():
    n = 1400
    rng = np.random.default_rng(3)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    d = stock(c, l=c * 0.99, h=c * 1.01)
    d.dates = np.datetime64("2005-01-03") + np.arange(n).astype("timedelta64[D]")
    d.sma200 = c * 0.9
    p = prep_of(d, [(c.min() * 0.8, c.min() * 0.82)])
    mk = S.Market(d.dates.copy(), np.full(n, 100.0), np.array([], "datetime64[D]"), np.array([]), d.dates[-1])
    res = S.run_stock(p, mk, S.Config(), groups=("zone", "random", "lump"), starts=[800, 1000])
    z, r, lump = res["groups"]["zone"], res["groups"]["random"], res["groups"]["lump"]
    assert z["R"].shape == (2, 1, 4) and r["R"].shape == (2, 20, 4) and lump["R"].shape == (2, 1, 4)
    assert list(res["plan"]["excl"][:, 3]) == [0, 1]       # 1000 + 504 > the last bar (1399)
    assert np.isnan(z["R"][1, 0, 3]) and np.isfinite(z["R"][1, 0, 2]) and np.isfinite(z["R"][0, 0, 3])
    # the lump group's value at 63 bars is its shares at that close (no rate, no dividend)
    sh = (1 - S.COST) / c[800]
    assert math.isclose(lump["R"][0, 0, 0], sh * c[863] - 1)
