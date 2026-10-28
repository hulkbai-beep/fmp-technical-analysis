"""Tests for scripts/ladder_sim.py: hand-made bars and zones for each rule of
references/ladder_v3_spec.md (no network, no FMP data)."""
import math

import numpy as np
import pytest

import ladder_sim as S

W4 = np.array([1, 2, 4, 1], dtype=float) / 8


def stock(c, o=None, h=None, l=None, div=None, rate=None, spy=None, last=None, delisted=False):
    c = np.asarray(c, dtype=float)
    n = len(c)
    o = c.copy() if o is None else np.asarray(o, dtype=float)
    h = np.maximum(o, c) if h is None else np.asarray(h, dtype=float)
    l = np.minimum(o, c) if l is None else np.asarray(l, dtype=float)
    dates = np.datetime64("2010-01-04") + np.arange(n).astype("timedelta64[D]")
    d = S.StockData("TEST", dates, o, h, l, c, np.ones(n), np.zeros(n, bool),
                    np.zeros(n) if div is None else np.asarray(div, float),
                    np.zeros(n) if rate is None else np.asarray(rate, float),
                    dates.copy(), np.full(n, 100.0) if spy is None else np.asarray(spy, float),
                    np.ones(n, bool), np.full(n, np.nan), np.full(n, np.nan), np.array([], "datetime64[D]"),
                    n - 1 if last is None else last, delisted, False)
    d.sma200 = np.full(n, 50.0)
    d.sig3m = np.full(n, 0.10)
    return d


def prep_of(d, zones, brk=()):
    """zones: list of (low, high) used on every bar; brk: bars with a confirmed breakout."""
    n = len(d.c)
    z_lo = np.full((n, S.MAX_Z), np.nan)
    z_hi = np.full((n, S.MAX_Z), np.nan)
    for j, (a, b) in enumerate(zones):
        z_lo[:, j], z_hi[:, j] = a, b
    n_z = np.full(n, len(zones), dtype=np.int64)
    b = np.zeros(n, dtype=bool)
    b[list(brk)] = True
    nan = np.full(n, np.nan)
    return S.Prepared(d, z_lo, z_hi, n_z, b, nan.copy(), nan.copy(), (nan, nan, np.zeros(n, np.int64)))


def run(prep, kind=S.ZONE, cfg=None, t=0, **kw):
    return S.run_one(prep, t, kind, cfg or S.Config(), **kw)


def codes(ev, code):
    return ev[ev[:, 1] == code]


ZONES = [(95, 97), (88, 90), (80, 82), (110, 112), (120, 122)]


def test_zone_ladder_rules():
    px, lo = np.empty(6), np.empty(6)
    zl = np.array([99.0, 97.5, 95.0, 90.0, 89.0, 60.0])
    zh = np.array([101.0, 98.5, 97.0, 91.0, 90.5, 64.0])
    # 99-101 holds P (skipped); 98.5 is within 2% of P (skipped); 97 accepted; 91 ok (<= 95.06);
    # 90.5 too close to 91 (skipped); 64 deeper than 35% (stops the walk)
    m = S.zone_ladder(zl, zh, 6, 100.0, 4, px, lo, np.nan, np.nan)
    assert m == 2 and list(px[:2]) == [97.0, 91.0] and list(lo[:2]) == [95.0, 90.0]
    m = S.zone_ladder(zl, zh, 6, 100.0, 1, px, lo, np.nan, np.nan)
    assert m == 1
    # a forced first level (breakout start) skips the 2% test
    m = S.zone_ladder(zl, zh, 6, 100.0, 4, px, lo, 98.0, 99.5)
    assert m == 3 and list(px[:3]) == [99.5, 97.0, 91.0]


def test_orders_live_next_bar_fill_at_open_or_limit():
    # bar 0 is the start (its low under 97 must not fill); bar 1 dips to 96.5; bar 2 gaps to 89 and runs to 81
    c = [100, 98, 85, 86, 87]
    o = [100, 99, 89, 86, 87]
    l = [96, 96.5, 81, 85, 86]
    p = prep_of(stock(c, o=o, l=l), ZONES)
    out, _, ev, _ = run(p)
    f = codes(ev, S.EV_FILL)
    assert [int(x) for x in f[:, 0]] == [1, 2, 2]
    assert list(f[:, 2]) == [97.0, 89.0, 82.0]            # min(open, limit)
    assert list(f[:, 4]) == [0, 1, 2]
    sh = (W4[:3] * (1 - S.COST) / np.array([97.0, 89.0, 82.0])).sum()
    assert math.isclose(f[:, 3].sum(), sh)


def test_replacement_maps_first_unfilled_part_to_level_one():
    c = [100, 98, 99, 104, 130, 131]
    l = [100, 96, 97, 103, 129, 130]
    d = stock(c, l=l)
    zones = [(95, 97), (88, 90), (80, 82), (124, 126), (115, 118), (105, 107)]
    p = prep_of(d, zones, brk=[4])
    out, _, ev, st = run(p, stop_at=4)
    orders = codes(ev, S.EV_ORDER)
    new = orders[orders[:, 0] == 4]
    # at bar 4 (close 130): ladder 126, 118, 107(<=115.6) ...; part 0 filled at 97, parts 1-3 take levels 1-3
    assert list(new[:, 4]) == [1, 2, 3]
    assert list(new[:, 2]) == [126.0, 118.0, 107.0]


def test_stop_needs_three_conditions_and_sells_next_open():
    # fills 97, 89 (open under 90), 82: avgcost 85.8, so condition 1 needs a close <= 72.95;
    # closes under the floor (lowest zone low 80) twice while lagging SPY
    c = [100, 96, 85, 72, 71, 70, 69]
    o = [100, 98, 89, 80, 71, 75, 69]
    l = [100, 96, 81, 72, 70, 67, 68]
    p = prep_of(stock(c, o=o, l=l), ZONES)
    out, floor, ev, _ = run(p)
    assert floor[0] == 80.0 and floor[3] == 80.0
    sig = codes(ev, S.EV_STOP_SIGNAL)
    sale = codes(ev, S.EV_STOP_SALE)
    # bar 3: first close below 80, bar 4: second -> signal at 4, sold at the open of 5
    assert int(sig[0, 0]) == 4 and int(sale[0, 0]) == 5 and sale[0, 2] == 75.0
    # part 3 (no zone level) never had an order; nothing is bought after the stop
    assert len(codes(ev, S.EV_FILL)) == 3
    # with SPY falling as much, condition 3 fails and there is no stop
    p2 = prep_of(stock(c, o=o, l=l, spy=np.array(c) / 100 * 100), ZONES)
    _, _, ev2, _ = run(p2)
    assert len(codes(ev2, S.EV_STOP_SIGNAL)) == 0


def test_scale_out_targets_and_three_sales():
    # one fill at 97, then a run to 150+: targets are the zones above, frozen until hit
    c = [100, 98, 146, 147, 151, 160, 163, 171, 175, 180]
    o = [100, 99, 140, 147, 150, 158, 163, 170, 175, 180]
    l = [100, 96.5, 139, 146, 149, 157, 162, 169, 174, 179]
    zones = [(95, 97), (88, 90), (80, 82), (150, 152), (161, 165), (170, 173)]
    p = prep_of(stock(c, o=o, l=l), zones)
    out, _, ev, _ = run(p)
    start = codes(ev, S.EV_SCALE_START)
    assert int(start[0, 0]) == 2                   # 146 >= 1.5 x 97
    sig = codes(ev, S.EV_SALE_SIGNAL)
    # 150 hit at bar 4 (151), 161 at bar 6 (163), 170 at bar 7 (171)
    assert [int(x) for x in sig[:, 0]] == [4, 6, 7]
    sales = codes(ev, S.EV_SALE)
    assert [int(x) for x in sales[:, 0]] == [5, 7, 8]
    base = codes(ev, S.EV_FILL)[0, 3]
    assert math.isclose(sales[0, 3], base / 3) and math.isclose(sales[1, 3], base / 3)
    assert math.isclose(sales[:, 3].sum(), base)
    # orders were cancelled at the start: the 90 and 82 parts never fill although the lows never got there
    assert len(codes(ev, S.EV_FILL)) == 1
    i = 2                                          # 252-bar horizon not reached; check ended at the end
    assert np.isnan(out[i, 0])


def test_scale_out_sigma_target_above_every_zone():
    c = [100, 98, 150, 151, 166, 170]
    l = [100, 96.5, 149, 150, 165, 169]
    p = prep_of(stock(c, l=l), [(95, 97), (88, 90)])
    _, _, ev, _ = run(p)
    sig = codes(ev, S.EV_SALE_SIGNAL)
    # target = 150 x (1 + 0.10) = 165 -> hit at bar 4 (166)
    assert int(sig[0, 0]) == 4


def test_detector_waits_while_inside_the_zone():
    zl = np.full((12, S.MAX_Z), np.nan)
    zh = np.full((12, S.MAX_Z), np.nan)
    zl[:, 0], zh[:, 0] = 100.0, 104.0
    zl[:, 1], zh[:, 1] = 110.0, 112.0
    nz = np.full(12, 2, dtype=np.int64)
    c = np.array([98, 99, 101, 102, 103, 105, 103, 105, 106, 107, 108, 109], dtype=float)
    brk, blo, bhi, slo, shi, sn = S.detector(c, zl, zh, nz, 0, 0)
    # 105 (n=1), back inside (n=0), 105 (n=1), 106 (n=2): confirmed at bar 8; W then = 110-112
    assert list(np.flatnonzero(brk)) == [8]
    assert (blo[8], bhi[8]) == (100.0, 104.0)
    assert (slo[8], shi[8]) == (110.0, 112.0)
    # after the break of 100-104 the watched zone is 110-112; a close below it (99) re-picks 100-104,
    # which two closes above 104 break again; one close above 112 is not a breakout
    c2 = np.array([98, 101, 99, 101, 105, 106, 99, 105, 106, 109, 111, 113], dtype=float)
    brk2, _, _, slo2, _, _ = S.detector(c2, zl, zh, nz, 0, 0)
    assert list(np.flatnonzero(brk2)) == [5, 8]
    assert slo2[5] == 110.0 and slo2[6] == 100.0 and slo2[11] == 110.0


def test_reference_ladder_follows_breakouts_after_the_zone_group_stops():
    # the stop of the previous test (sold at the open of bar 5), then a rally and a breakout at bar 9
    c = [100, 96, 85, 72, 71, 70, 69, 90, 100, 120, 121, 122]
    o = [100, 98, 89, 80, 71, 75, 69, 90, 100, 120, 121, 122]
    l = [100, 96, 81, 72, 70, 67, 68, 89, 99, 119, 120, 121]
    zones = [(95, 97), (88, 90), (80, 82), (105, 108), (114, 116)]
    p = prep_of(stock(c, o=o, l=l), zones, brk=[9])
    out, floor, ev, _ = run(p)
    assert int(codes(ev, S.EV_STOP_SALE)[0, 0]) == 5
    # at bar 9 (close 120) the new ladder is 116, 108, 97, 90: the part the group never filled (part 3)
    # takes level 1 (zone low 114) although the group has stopped; parts 0-2 keep 95, 88, 80
    _, _, _, st9 = run(p, stop_at=9)
    r_lo = [st9[22 + 7 * j + 6] for j in range(4)]
    assert r_lo == [95.0, 88.0, 80.0, 114.0]
    assert floor[9] == 80.0 and floor[10] == 80.0
    # with nothing filled, the whole ladder moves: R = 114, 105, 95, 88 from the bar after the breakout
    p2 = prep_of(stock([100, 99, 99, 99, 120, 121, 122], l=[100, 98.5, 98.5, 98.5, 119, 120, 121]), zones, brk=[4])
    _, floor2, ev2, _ = run(p2)
    assert floor2[4] == 80.0 and floor2[5] == 88.0


def test_random_pairs_cap_and_half_smallest_gap():
    px = np.array([97.9, 90.0, 82.0, 0, 0, 0])
    lo = np.array([95.0, 88.0, 80.0, 0, 0, 0])
    sma = np.zeros(6, bool)
    U = np.full((S.MAX_ROWS, S.N_U), 0.5)
    gl, gb, gf = np.empty(5), np.empty(5), np.empty(5)
    g = min(1 - 90 / 97.9, 1 - 82 / 90)
    delta = 0.01 + 0.5 * (g / 2 - 0.01)
    note = S.group_levels(S.RANDOM, 100.0, 3, px, lo, sma, 0, U, 1.0, np.nan, 0.1, np.zeros(5),
                          np.zeros(1), np.zeros(1), 0, 98.0, gl, gb, gf)
    assert note == 1 and gl[0] == 98.0
    assert math.isclose(gl[1], 90 * (1 + delta)) and math.isclose(gl[2], 82 * (1 + delta))
    note = S.group_levels(S.RANDOM, 100.0, 3, px, lo, sma, 0, U, -1.0, np.nan, 0.1, np.zeros(5),
                          np.zeros(1), np.zeros(1), 0, 98.0, gl, gb, gf)
    assert note == 0 and math.isclose(gl[0], 97.9 * (1 - delta)) and math.isclose(gb[0], 95 * (1 - delta))
    # one level: delta on [1%, 2%]
    S.group_levels(S.RANDOM, 100.0, 1, px, lo, sma, 0, np.zeros((S.MAX_ROWS, S.N_U)), -1.0, np.nan, 0.1,
                   np.zeros(5), np.zeros(1), np.zeros(1), 0, 98.0, gl, gb, gf)
    assert math.isclose(gl[0], 97.9 * 0.99)


def test_gaps_only_nearest_gap_and_balance():
    zl = np.array([95.0, 91.0, 85.0, 99.0])
    zh = np.array([97.0, 95.0, 89.0, 101.0])
    # level 97 (zone 95-97): up gap (97, 98) under the 2% cap; down: the run 91-97 ends at 91, gap (89, 91)
    x_up = S._gap_level(zl, zh, 4, 97.0, 95.0, 98.0, 100.0, 0.5, 0.5, 0.0)
    x_dn = S._gap_level(zl, zh, 4, 97.0, 95.0, 98.0, 100.0, 0.5, 0.5, 0.999)
    assert math.isclose(x_up, 97.5) and math.isclose(x_dn, 90.0)
    # P(up) = b / (a + b) makes the expected shift 0
    a, b = 97.5 / 97 - 1, 1 - 90.0 / 97
    assert abs((b / (a + b)) * a - (a / (a + b)) * b) < 1e-15
    # no gap on either side -> no order
    zl2, zh2 = np.array([90.0, 97.0, 50.0]), np.array([97.0, 99.0, 90.0])
    assert np.isnan(S._gap_level(zl2, zh2, 3, 97.0, 90.0, 98.0, 100.0, 0.5, 0.5, 0.5))


def test_vol_fixed_lump():
    p = prep_of(stock([100, 99, 98, 97]), ZONES)
    _, _, ev, _ = run(p, kind=S.VOL)
    o = codes(ev, S.EV_ORDER)
    assert np.allclose(o[:, 2], [95.0, 90.0, 85.0])          # 0.5 sigma steps, sigma 10%, m = 3 levels
    _, _, ev, _ = run(p, kind=S.FIXED, fixed_depth=[0.03, 0.1, 0.2, 0.3, 0.4])
    assert np.allclose(codes(ev, S.EV_ORDER)[:, 2], [97.0, 90.0, 80.0])
    out, _, ev, _ = run(p, kind=S.LUMP)
    assert codes(ev, S.EV_LUMP_BUY)[0, 2] == 100.0


def test_dividend_on_previous_close_shares_and_interest():
    c = [100, 96, 96, 96, 96]
    o = [100, 98, 96, 96, 96]
    l = [100, 96, 96, 96, 96]
    div = [0, 0, 0.5, 0, 0]
    rate = [0, 0.001, 0.001, 0.001, 0.001]
    p = prep_of(stock(c, o=o, l=l, div=div, rate=rate), ZONES)
    out, _, ev, st = run(p, stop_at=4)
    sh = W4[0] * (1 - S.COST) / 97.0
    dv = codes(ev, S.EV_DIVIDEND)
    assert int(dv[0, 0]) == 2 and math.isclose(dv[0, 3], sh)
    cash = 1.0 * 1.001 - W4[0]
    cash = cash * 1.001 + 0.5 * sh
    cash = cash * 1.001 * 1.001
    assert math.isclose(st[0], cash)


def test_delisting_liquidates_at_the_last_close():
    c = [100, 96, 95, 94]
    o = [100, 98, 95, 94]
    l = [100, 96, 95, 94]
    p = prep_of(stock(c, o=o, l=l, last=3, delisted=True), ZONES)
    out, _, ev, _ = run(p)
    liq = codes(ev, S.EV_LIQUIDATE)
    sh = W4[0] * (1 - S.COST) / 97.0
    assert int(liq[0, 0]) == 3 and math.isclose(liq[0, 3], sh)
    assert (out[:, 9] == S.ST_DELISTED).all()
    assert math.isclose(out[0, 0], 1 - W4[0] + sh * 94 * (1 - S.COST))


def test_time_limit_single_check():
    n = 50
    c = np.full(n, 99.5)
    p = prep_of(stock(c), ZONES)
    cfg = S.Config(time_limit=True)
    _, _, ev, _ = run(p, cfg=cfg)
    tb = codes(ev, S.EV_TIME_BUY)
    assert len(tb) == 1 and int(tb[0, 0]) == 42 and tb[0, 4] == 0
    d = stock(c)
    d.sma200 = np.full(n, 120.0)                   # below the 200-day average: no buy
    _, _, ev, _ = run(prep_of(d, ZONES), cfg=cfg)
    assert len(codes(ev, S.EV_TIME_BUY)) == 0


def test_close_confirm_needs_the_close_above_the_band():
    c = [100, 94, 96, 96]
    l = [100, 96.5, 96, 95.5]
    p = prep_of(stock(c, l=l), ZONES)
    _, _, ev, _ = run(p, cfg=S.Config(fill_mode=S.CLOSE_CONFIRM))
    f = codes(ev, S.EV_CLOSE_FILL)
    # bar 1 touches 97 but closes 94 < 95: no fill; bar 2 touches and closes 96 >= 95: fill at 96
    assert [int(x) for x in f[:, 0]] == [2] and f[0, 2] == 96.0


def test_sma_level_merges_and_fills_on_a_hold():
    d = stock([100, 99, 96.8, 97], l=[100, 98, 96.0, 96.5])
    d.sma200 = np.full(4, 96.5)                    # within 2% of the 97 zone level: merged
    p = prep_of(d, ZONES)
    _, _, ev, _ = run(p, cfg=S.Config(sma_level=True))
    f = codes(ev, S.EV_CLOSE_FILL)
    assert len(f) == 1 and int(f[0, 0]) == 2 and f[0, 2] == 96.8 and f[0, 4] == 0
    assert len(codes(ev, S.EV_FILL)) == 0          # the merged level is not a limit at 97


def test_earnings_flags():
    d = stock([100, 99, 98, 97, 96, 95])
    d.report_dates = np.array([d.dates[2]])
    g = S.earnings_bars(d)
    assert list(np.flatnonzero(g)) == [2, 3]
    ev = np.array([[3, S.EV_FILL, 97, 1, 0, 0]], float)
    assert S.earnings_flag(ev, g, 5) and not S.earnings_flag(ev, g, 2)
    stop = np.array([[6, S.EV_STOP_SALE, 90, 1, -1, 5]], float)
    g2 = np.zeros(8, bool)
    g2[4] = True                                   # the close before the signal close
    assert S.earnings_flag(stop, g2, 7)


def test_seeds_are_fixed():
    a = S.uniform_table("20261028|X|2010-01-04|random|1")
    b = S.uniform_table("20261028|X|2010-01-04|random|1")
    c = S.uniform_table("20261029|X|2010-01-04|random|1")
    assert np.array_equal(a, b) and not np.array_equal(a, c)
    assert S.placebo_draw("X", 1) == S.placebo_draw("X", 1)


def test_clean_bars_placeholders_and_jumps():
    rows = [{"date": f"2010-01-{i + 1:02d}", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "volume": 1000}
            for i in range(20)]
    rows[3] = {"date": "2010-01-04", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 0}
    rows[10]["close"] = 25.0                       # +150% for one day, then -60%
    adj = [{"date": r["date"], "adjClose": r["close"]} for r in rows]
    df, sus, rep = S.clean_bars(rows, adj, [])
    assert rep["placeholders_removed"] == 1 and len(df) == 19
    # the spike reverses (unverified, bars +-40 suspicious); the fall back holds against 25 (verified)
    assert len(rep["jumps"]) == 2 and not rep["jumps"][0]["verified"] and rep["jumps"][1]["verified"]
    assert sus.all()
    # a move of exactly -50% is not checked (the rule is |C[d]/C[d-1] - 1| > 50%)
    rows[10]["close"] = 20.0
    adj = [{"date": r["date"], "adjClose": r["close"]} for r in rows]
    _, _, rep2 = S.clean_bars(rows, adj, [])
    assert [j["move"] for j in rep2["jumps"]] == [1.0]
    # a split near the move, or adjusted closes that disagree, leave it unverified even if it holds
    rows[10]["close"] = 10.0
    for r in rows[10:]:
        r["close"] = 30.0
    adj = [{"date": r["date"], "adjClose": r["close"]} for r in rows]
    _, sus3, rep3 = S.clean_bars(rows, adj, [])
    assert rep3["jumps"][0]["verified"] and not sus3.any()
    _, sus4, rep4 = S.clean_bars(rows, adj, [{"date": rows[12]["date"]}])
    assert not rep4["jumps"][0]["verified"] and rep4["jumps"][0]["near_split"] and sus4.all()
    adj5 = [{"date": r["date"], "adjClose": 10.0} for r in rows]
    _, _, rep5 = S.clean_bars(rows, adj5, [])
    assert not rep5["jumps"][0]["adjusted_agrees"] and not rep5["jumps"][0]["verified"]


def test_clean_bars_at_the_end_of_a_delisted_stock():
    days = np.datetime64("2010-01-04") + np.arange(60).astype("timedelta64[D]")
    rows = [{"date": str(x), "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "volume": 1000} for x in days]
    rows[-1]["close"] = 4.0                        # -60% on the last bar
    adj = [{"date": r["date"], "adjClose": r["close"]} for r in rows]
    _, sus, rep = S.clean_bars(rows, adj, [])
    assert not rep["jumps"][0]["holds"] and sus[-1] and sus.sum() == 41
    _, sus2, rep2 = S.clean_bars(rows, adj, [], final=True)
    assert rep2["jumps"][0]["holds"] and rep2["jumps"][0]["verified"] and not sus2.any()
    # two bars before the end: the test uses the last close (a recovery to 8 reverses it)
    rows[-1]["close"], rows[-3]["close"] = 10.0, 4.0
    for r in rows[-2:]:
        r["close"] = 4.2
    adj = [{"date": r["date"], "adjClose": r["close"]} for r in rows]
    assert S.clean_bars(rows, adj, [], final=True)[2]["jumps"][0]["verified"]
    rows[-1]["close"] = 8.0
    adj = [{"date": r["date"], "adjClose": r["close"]} for r in rows]
    jumps = S.clean_bars(rows, adj, [], final=True)[2]["jumps"]
    assert not jumps[0]["holds"] and not jumps[0]["verified"]
    # a split near the move still leaves it unverified
    rows[-1]["close"] = 4.0
    adj = [{"date": r["date"], "adjClose": r["close"]} for r in rows]
    assert not S.clean_bars(rows, adj, [{"date": rows[-2]["date"]}], final=True)[2]["jumps"][-1]["verified"]


def test_build_stock_delisted_after_a_collapse(tmp_path):
    """A stock whose bars end with a -60% day and whose profile says it no longer trades: delisted, the move
    verified, no bar suspicious (S1); the same bars of a trading stock leave the move unverified."""
    import json
    days = np.datetime64("2010-01-04") + np.arange(60).astype("timedelta64[D]")
    spy = [{"date": str(x), "close": 100.0} for x in days]
    rows = [{"date": str(x), "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "volume": 1000} for x in days[:40]]
    rows[-1].update(open=6.0, low=3.9, close=4.0)
    for active in (False, True):
        folder = tmp_path / ("trading" if active else "gone")
        files = {"full": rows, "adj": [{"date": r["date"], "adjClose": r["close"]} for r in rows],
                 "profile": [{"isActivelyTrading": active}]}
        for part, data in files.items():
            (folder / part).mkdir(parents=True)
            (folder / part / "X.json").write_text(json.dumps(data))
        mk = S.Market.load(spy, [{"date": "2009-12-31", "month3": 1.0}])
        d = S.build_stock("X", folder, {"series": "X", "spans": [["2000-01-01", "2100-01-01"]]}, mk)
        assert d.last == 39 and d.delisted == (not active) and d.gap_end == active
        assert d.cleaning["jumps"][0]["verified"] == (not active)
        assert d.suspicious.any() == active


def test_plan_starts_count_a_start_on_the_last_bar():
    n = 900
    d = stock(np.full(n, 100.0), last=n - 1, delisted=True)
    d.dates = np.datetime64("2007-01-01") + np.arange(n).astype("timedelta64[D]")
    d.sma200 = np.full(n, 99.0)
    month = d.dates.astype("datetime64[M]")
    firsts = np.flatnonzero(np.r_[True, month[1:] != month[:-1]])
    d.last = int(firsts[-1])                       # the last bar is the first bar of its month
    p = prep_of(d, ZONES)
    starts, skipped = S.plan_starts(p, S.Config())
    assert d.last not in starts and skipped["the stock's last bar"] == 1
    assert starts and max(starts) < d.last


def test_ttm_pe_uses_filing_dates():
    dates = np.datetime64("2010-01-01") + np.arange(400).astype("timedelta64[D]")
    close = np.full(400, 40.0)
    inc = [{"date": f"2009-{m:02d}-28", "filingDate": f"2010-{m:02d}-10", "epsDiluted": 1.0} for m in (3, 6, 9, 12)]
    pe = S.ttm_pe(dates, close, inc)
    i_ok = int(np.searchsorted(dates, np.datetime64("2010-12-11")))
    assert np.isnan(pe[i_ok - 1]) and pe[i_ok] == 10.0
    pe_leak = S.ttm_pe(dates, close, inc, leak=S.LEAK_EPS_PERIOD_END)
    assert pe_leak[5] == 10.0


def test_ttm_pe_placeholder_filing_dates_and_missing_quarters():
    """S13: a filingDate on the period end is a placeholder (known 45 days after a Q1-Q3 end, 90 days after a
    Q4 end); four quarters make a TTM only when their period ends span at most 330 days."""
    dates = np.datetime64("2010-01-01") + np.arange(500).astype("timedelta64[D]")
    close = np.full(500, 40.0)
    q = [("2009-03-31", "Q1"), ("2009-06-30", "Q2"), ("2009-09-30", "Q3"), ("2009-12-31", "Q4")]
    inc = [{"date": p, "period": k, "filingDate": p, "epsDiluted": 1.0} for p, k in q]
    assert S.income_known(inc[0]) == np.datetime64("2009-05-15")
    assert S.income_known(inc[3]) == np.datetime64("2010-03-31")
    pe = S.ttm_pe(dates, close, inc)
    i = int(np.searchsorted(dates, np.datetime64("2010-04-01")))    # date[i-1] = 2010-03-31: Q4 is known
    assert np.isnan(pe[i - 1]) and pe[i] == 10.0
    # Q1-2010 missing: the latest four (2009-06-30 .. 2010-06-30) span 365 days, so no TTM
    gap = inc + [{"date": "2010-06-30", "period": "Q2", "filingDate": "2010-08-02", "epsDiluted": 1.0}]
    pe = S.ttm_pe(dates, close, gap)
    j = int(np.searchsorted(dates, np.datetime64("2010-08-03")))
    assert pe[j - 1] == 10.0 and np.isnan(pe[j])
    late = gap + [{"date": "2010-03-31", "period": "Q1", "filingDate": "2010-09-01", "epsDiluted": 2.0}]
    pe = S.ttm_pe(dates, close, late)
    k = int(np.searchsorted(dates, np.datetime64("2010-09-02")))
    assert np.isnan(pe[k - 1]) and math.isclose(pe[k], 40.0 / 5.0)


def test_report_dates_drop_placeholders_and_mark_missing_quarters():
    """S12: an earnings date equal to an income period end is a placeholder and is dropped; a quarter with no
    report date within 100 days of its period end leaves that span unknown."""
    inc = [{"date": p} for p in ("2010-03-31", "2010-06-30", "2010-09-30", "2010-12-31")]
    earn = [{"date": "2010-04-20"}, {"date": "2010-06-30"}, {"date": "2010-10-21"}, {"date": "2011-01-25"}]
    end = np.datetime64("2011-06-30")
    rep, unknown = S.report_dates(earn, inc, end, end)
    assert [str(x) for x in rep] == ["2010-04-20", "2010-10-21", "2011-01-25"]
    assert [[str(a), str(b)] for a, b in unknown] == [["2010-07-01", "2010-10-08"]]
    # windows that end after the data end, or start after the stock's last bar, are not judged
    rep, unknown = S.report_dates(earn, inc, np.datetime64("2010-10-07"), np.datetime64("2010-10-07"))
    assert len(unknown) == 0
    rep, unknown = S.report_dates(earn, inc, end, np.datetime64("2010-06-30"))
    assert len(unknown) == 0


def test_random_up_draw_caps_a_200_day_level_1():
    """S6, S13: on an up draw a 200-day level 1 is capped at 0.98 P like a zone level 1, and its factor with it."""
    px = np.array([97.5, 90.0, 82.0, 0, 0, 0])
    lo = np.array([np.nan, 88.0, 80.0, 0, 0, 0])
    sma = np.array([True, False, False, False, False, False])
    U = np.full((S.MAX_ROWS, S.N_U), 1.0)
    gl, gb, gf = np.empty(5), np.empty(5), np.empty(5)
    note = S.group_levels(S.RANDOM, 100.0, 3, px, lo, sma, 0, U, 1.0, np.nan, 0.1, np.zeros(5),
                          np.zeros(1), np.zeros(1), 0, 98.0, gl, gb, gf)
    assert note == 1 and gl[0] == 98.0 and math.isclose(gf[0], 98.0 / 97.5)
