#!/usr/bin/env python3
"""
Simulation runs and the statistics of section 14 of references/ladder_v3_spec.md, on the cached
per-stock runs of scripts/ladder_run.py (`simulate`). Nothing here reads FMP files.

A run of one configuration on one set is a table of plans (all stocks concatenated) with, per group,
R_h for every draw and the outputs of S12. The tests take differences on the same plans (paired) or
on two plan sets resampled from the same blocks (unpaired), with the calendar-block bootstrap of
ladder_stats. The items of S14.3 are defined once (item_specs) and serve both the secondary tests on the
main sample and the one-sided check of S14.4 on the held-out stocks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import ladder_sim as S
import ladder_stats as T

LARGE_CAPS = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA")
H = {63: 0, 126: 1, 252: 2, 504: 3}
EXTREMES = ((np.datetime64("2008-09-15"), np.datetime64("2009-03-31")),
            (np.datetime64("2020-02-20"), np.datetime64("2020-04-30")))
FIELDS = {"deployed": 1, "avgcost": 2, "worst": 3, "stopped": 4, "fills": 5, "scaled": 6}

# configurations of S13 / 14.3: name -> (Config, groups)
CONFIGS = {
    "power": (S.Config(name="power"), ("zone", "random")),
    "main": (S.Config(name="main"), ("zone", "random", "gaps", "vol", "fixed", "lump")),
    "1:2:3:2:2": (S.Config(ratio=(1, 2, 3, 2, 2), name="1:2:3:2:2"), ("zone", "random")),
    "1:2:4": (S.Config(ratio=(1, 2, 4), name="1:2:4"), ("zone", "random")),
    "1:1:1:1": (S.Config(ratio=(1, 1, 1, 1), name="1:1:1:1"), ("zone", "random")),
    "1:1:1:1:1": (S.Config(ratio=(1, 1, 1, 1, 1), name="1:1:1:1:1"), ("zone", "random")),
    "1:1:1": (S.Config(ratio=(1, 1, 1), name="1:1:1"), ("zone", "random")),
    "close confirm": (S.Config(fill_mode=S.CLOSE_CONFIRM, name="close confirm"), ("zone", "random")),
    "breakout start": (S.Config(start_on_breakout=True, name="breakout start"), ("zone", "random")),
    "time limit": (S.Config(time_limit=True, name="time limit"), ("zone", "random")),
    "200-day": (S.Config(sma_level=True, name="200-day"), ("zone", "random")),
    "stop 20%": (S.Config(stop_pct=0.20, name="stop 20%"), ("zone", "random")),
}
PLACEBOS = tuple(range(1, 21))


def file_name(config: str) -> str:
    return config.replace(":", "-").replace(" ", "_").replace("%", "pct")


# ----------------------------------------------------------------------------- saving and loading runs
def save_stock(res: dict, path: Path, drop=()) -> None:
    """One stock's run_stock result as an npz (groups in `drop` are left out: the power step keeps no
    zone outcome)."""
    arr = {f"plan.{k}": np.asarray(v) for k, v in res["plan"].items()}
    for g, r in res["groups"].items():
        if g in drop:
            continue
        for k, v in r.items():
            arr[f"{g}.{k}"] = v if k != "out" else v[..., [1, 2, 3, 4, 5, 6]].astype(np.float32)
    arr["meta"] = np.array(json.dumps({"symbol": res["symbol"], "cfg": res["cfg"], "ratio": res["ratio"],
                                       "start_skipped": res["start_skipped"], "cleaning": res.get("cleaning", {})}))
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **arr)


@dataclass
class Run:
    """All plans of one configuration on one set."""
    config: str
    sym: np.ndarray              # per plan
    t: np.ndarray
    date: np.ndarray
    year: np.ndarray
    c_t: np.ndarray
    excl: np.ndarray             # (N, 4): 0 counted
    hdate: np.ndarray            # (N, 4)
    pe_defined: np.ndarray
    pe_a: np.ndarray
    pe_b: np.ndarray
    g: dict                      # group -> {"R": (N, D, 4), "out": (N, D, 4, 6), "earn", "start_px", "cap",
                                 #           "first_fill", "placements", "capped"}
    skipped: dict
    cleaning: dict               # symbol -> the S1 report (moves over 50%, delisting, data gap)
    report_unknown: np.ndarray | None = None      # (N, 4): bars t..t+h touch a missing report date (S12)

    @property
    def large(self) -> np.ndarray:
        return np.isin(self.sym, LARGE_CAPS)

    def counted(self, h: int) -> np.ndarray:
        return self.excl[:, H[h]] == 0

    def R(self, group: str, h: int) -> np.ndarray:
        """(N, D) returns at horizon h."""
        return self.g[group]["R"][:, :, H[h]]

    def mean_R(self, group: str, h: int) -> np.ndarray:
        """Per plan: the group's return (random, gaps, placebo: the mean over its draws)."""
        return self.R(group, h).mean(axis=1)

    def field(self, group: str, name: str, h: int) -> np.ndarray:
        return self.g[group]["out"][:, :, H[h], list(FIELDS).index(name)].astype(float)

    def extreme(self, h: int) -> np.ndarray:
        """Plans whose bars t .. t+h touch 2008-09-15 .. 2009-03-31 or 2020-02-20 .. 2020-04-30."""
        a, b = self.date, self.hdate[:, H[h]]
        hit = np.zeros(len(a), bool)
        for lo, hi in EXTREMES:
            hit |= (a <= hi) & (b >= lo)
        return hit

    def unknown(self, h: int) -> np.ndarray:
        """Plans whose bars t .. t+h touch a quarter without a report date (S12): no earnings class."""
        if self.report_unknown is None:
            return np.zeros(len(self.t), bool)
        return self.report_unknown[:, H[h]].astype(bool)

    def earnings_flag(self, h: int, groups=("zone", "random", "gaps", "vol", "fixed", "lump")) -> np.ndarray:
        """Plans where any of `groups` (any draw) has an earnings flag by t+h (S12, S13)."""
        f = np.zeros(len(self.t), bool)
        for g in groups:
            if g in self.g:
                f |= (self.g[g]["earn"] <= (self.t + h)[:, None]).any(axis=1)
        return f


def load_run(folder: Path, config: str, symbols: list) -> Run:
    parts = []
    for sym in symbols:
        z = np.load(folder / file_name(config) / f"{sym}.npz", allow_pickle=False)
        parts.append((sym, z))
    groups = sorted({k.split(".")[0] for _, z in parts for k in z.files if "." in k and not k.startswith("plan.")})
    cat = lambda key: np.concatenate([z[key] for _, z in parts]) if parts else np.array([])
    t = cat("plan.t").astype(np.int64)
    date = cat("plan.date").astype("datetime64[D]")
    sym = np.concatenate([np.full(len(z["plan.t"]), s) for s, z in parts])
    keys = ("R", "out", "earn", "start_px", "cap", "first_fill", "placements", "capped")
    g = {gr: {k: cat(f"{gr}.{k}") for k in keys if all(f"{gr}.{k}" in z.files for _, z in parts)} for gr in groups}
    skipped, cleaning = {}, {}
    for s, z in parts:
        meta = json.loads(str(z["meta"]))
        for k, v in meta["start_skipped"].items():
            skipped[k] = skipped.get(k, 0) + v
        cleaning[s] = meta.get("cleaning", {})
    years = date.astype("datetime64[Y]").astype(int) + 1970
    unknown = cat("plan.report_unknown").astype(bool) if all("plan.report_unknown" in z.files for _, z in parts) else None
    return Run(config, sym, t, date, years, cat("plan.c_t"), cat("plan.excl"), cat("plan.hdate").astype("datetime64[D]"),
               cat("plan.pe_defined").astype(bool), cat("plan.pe_a").astype(bool), cat("plan.pe_b").astype(bool), g,
               skipped, cleaning, unknown)


# ----------------------------------------------------------------------------- the statistics
def paired(x: np.ndarray, mask: np.ndarray, years: np.ndarray, width: int = 1) -> dict:
    m = mask & np.isfinite(x)
    return T.mean_diff(x[m], T.Blocks(T.year_block(years[m], width)))


def unpaired(xa, ma, ya, xb, mb, yb, width: int = 1) -> dict:
    ma, mb = ma & np.isfinite(xa), mb & np.isfinite(xb)
    return T.unpaired_diff(xa[ma], T.year_block(ya[ma], width), xb[mb], T.year_block(yb[mb], width))


def theta(run: Run, a: str, b: str, h: int = 252, mask=None, other: Run | None = None, b_group: str | None = None):
    """mean over plans of R_h(a) - R_h(b); `other`: b taken from another run of the same plans (paired)."""
    ob = other or run
    x = run.mean_R(a, h) - ob.mean_R(b_group or b, h)
    m = run.counted(h) & ob.counted(h)
    if mask is not None:
        m &= mask
    return x, m


def main_test(run: Run, se_q80: float | None = None) -> dict:
    """S14.1."""
    x, m = theta(run, "zone", "random", 252)
    main = paired(x, m, run.year)
    x126 = run.mean_R("zone", 126) - run.mean_R("random", 126)
    th126 = float(x126[m].mean())
    m43 = m & ~run.large
    th43 = float(x[m43].mean())
    out = {"theta": main, "theta_126_same_plans": th126, "theta_43": th43,
           "screen": T.screen(main, th126, th43)}
    two = paired(x, m, run.year, width=2)
    out["two_year_blocks"] = two
    out["fragile"] = bool(out["screen"]["passes"] and not two["lo_screen"] > 0)
    out["leave_one_year_out"] = T.leave_one_out(x[m], run.year[m])
    cap_up = (run.g["random"]["cap"][:, 0::2] > 0).any(axis=1)
    out["no_capped_up_draw"] = paired(x, m & ~cap_up, run.year)
    out["capped_up_share_of_plans"] = float(cap_up[m].mean())
    n = m.sum()
    out["per_stock"] = {s: float(x[m & (run.sym == s)].sum() / n) for s in np.unique(run.sym[m])}
    # S14.1 / S12: deployed share is a plain mean; the average cost is taken per draw over the draw's plans
    # with a fill (then averaged over the draws), and per draw on the plans where both zone and it filled
    dep_z = run.field("zone", "deployed", 252)[:, 0]
    acz = run.field("zone", "avgcost", 252)[:, 0] / run.c_t - 1.0
    out["deployed_minus_zone"], out["avgcost_minus_zone"], out["avgcost_minus_zone_common"] = {}, {}, {}
    for gr in run.g:
        if gr == "zone":
            continue
        dep = run.field(gr, "deployed", 252).mean(axis=1)
        out["deployed_minus_zone"][gr] = float(np.mean((dep - dep_z)[m]))
        ac = run.field(gr, "avgcost", 252) / run.c_t[:, None] - 1.0
        out["avgcost_minus_zone"][gr] = float(_nanmean(_nanmean(np.where(m[:, None], ac, np.nan), 0)) -
                                              _nanmean(acz[m]))
        both = m[:, None] & np.isfinite(ac) & np.isfinite(acz)[:, None]
        with np.errstate(invalid="ignore", divide="ignore"):
            cs = np.where(both, ac - acz[:, None], 0.0).sum(axis=0) / both.sum(axis=0)
        out["avgcost_minus_zone_common"][gr] = float(_nanmean(cs))
    out["random_shift"] = start_shift(run, "random", m)
    if "gaps" in run.g:
        out["gaps_shift"] = start_shift(run, "gaps", m)
    if se_q80 is not None:
        out["se_vs_power"] = {"se": main["se"], "se_q80_placebo": se_q80,
                              "power_overstated": bool(main["se"] > 1.25 * se_q80)}
    return out


def _nanmean(a, axis=None):
    a = np.asarray(a, float)
    with np.errstate(invalid="ignore"):
        cnt = np.isfinite(a).sum(axis=axis)
        tot = np.where(np.isfinite(a), a, 0.0).sum(axis=axis)
        return np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan) if axis is not None else (
            float(tot / cnt) if cnt else float("nan"))


def start_depth(run: Run, group: str, mask: np.ndarray) -> float:
    """Part-weighted mean depth 1 - order / C[t] of a group's start orders (each draw over the plans where it
    placed an order, then averaged over the draws)."""
    px = run.g[group]["start_px"]                                   # (N, D, k)
    k = px.shape[2]
    w = run_weights(run, k)
    have = np.isfinite(px)
    ww = np.where(have, w[None, None, :], 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        dep = np.where(have, 1.0 - px / run.c_t[:, None, None], 0.0)
        per = (dep * ww).sum(axis=2) / ww.sum(axis=2)                 # (N, D), NaN without an order
    return float(_nanmean(_nanmean(np.where(mask[:, None], per, np.nan), 0)))


def run_weights(run: Run, k: int) -> np.ndarray:
    """The part weights of a run's ratio (main: 1:2:4:1)."""
    ratio = {"main": (1, 2, 4, 1), "power": (1, 2, 4, 1)}.get(run.config)
    if ratio is None:
        ratio = CONFIGS[run.config][0].ratio
    w = np.asarray(ratio, float)
    if len(w) != k:
        raise ValueError(f"{run.config}: {k} parts stored, ratio {ratio}")
    return w / w.sum()


def start_shift(run: Run, group: str, mask: np.ndarray) -> dict:
    """S6 diagnostics of a group against zone at the start placement: the part-weighted mean signed shift of
    its start orders, the mean depths, and for random the share of up draws whose level 1 was capped (also
    over every placement with an order)."""
    pz = run.g["zone"]["start_px"][:, 0, :]
    pg = run.g[group]["start_px"]
    k = pz.shape[1]
    w = run_weights(run, k)
    both = np.isfinite(pz)[:, None, :] & np.isfinite(pg)
    rel = np.where(both, pg / pz[:, None, :] - 1.0, 0.0)
    ww = np.where(both, w[None, None, :], 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        shift = (rel * ww).sum(axis=2) / ww.sum(axis=2)
    out = {"mean_signed_shift": float(_nanmean(shift[mask])),
           "orders_without_zone_order": float((np.isfinite(pg) & ~np.isfinite(pz)[:, None, :])[mask].mean()),
           "mean_depth": start_depth(run, group, mask), "zone_mean_depth": start_depth(run, "zone", mask)}
    if group == "random":
        up_m = mask & np.isfinite(pz).any(axis=1)                  # plans whose start ladder has a level
        out["up_draws_capped"] = float((run.g["random"]["cap"][:, 0::2] > 0)[up_m].mean())
        if "placements" in run.g["random"]:
            pl, cp = run.g["random"]["placements"][:, 0::2][mask], run.g["random"]["capped"][:, 0::2][mask]
            out["up_placements_capped_all"] = float(cp.sum() / max(1, pl.sum()))
    else:
        out["no_gap_parts"] = float((np.isfinite(pz)[:, None, :] & ~np.isfinite(pg))[mask].mean())
    return out


def power(run: Run) -> dict:
    """S14.2: SE of mean(R_252(placebo k) - R_252(random)) for each k, and MDE80."""
    m = run.counted(252)
    rr = run.mean_R("random", 252)
    se, th = [], []
    for k in range(run.g["placebo"]["R"].shape[1]):
        x = run.g["placebo"]["R"][:, k, H[252]] - rr
        r = paired(x, m, run.year)
        se.append(r["se"])
        th.append(r["theta"])
    G = T.Blocks(run.year[m]).G
    return {"G": G, "se_k": se, "theta_k": th, **T.mde80(np.array(se), G), "plans": int(m.sum())}


METRICS = {"win_rate": ("win_rate", None), "payoff": ("payoff", None), "avgcost": ("mean", "avgcost"),
           "safety_margin": ("mean", "worst"), "stop_rate": ("mean", "stopped")}


def per_draw(run: Run, metric: str, h: int = 252, mask=None, group: str = "random", common: bool = False) -> dict:
    """S12: zone minus a group (each draw as its own group, averaged over the draws) for a metric that is not
    a plain mean, over plans with a fill (common: the plans where both zone and the draw filled)."""
    m = run.counted(h) if mask is None else run.counted(h) & mask
    kind, fld = METRICS[metric]
    if fld is None:
        vz, vd = run.R("zone", h)[:, 0], run.R(group, h)
    elif fld == "avgcost":
        vz = run.field("zone", "avgcost", h)[:, 0] / run.c_t - 1.0
        vd = run.field(group, "avgcost", h) / run.c_t[:, None] - 1.0
    else:
        vz, vd = run.field("zone", fld, h)[:, 0], run.field(group, fld, h)
    fz = run.field("zone", "fills", h)[:, 0] > 0
    fd = run.field(group, "fills", h) > 0
    return T.per_draw_metric(vz[m], fz[m], vd[m], fd[m], T.Blocks(run.year[m]), kind, common=common)


# ----------------------------------------------------------------------------- the items of S14.3
VALUATION_ITEMS = (22, 23, 24, 25)


def item_specs(runs: dict, valuation: bool = True) -> dict:
    """The items of S14.3 at 252 bars as data, number -> (label, kind, args): kind "paired" with
    (x, mask, years), "unpaired" with (xa, ma, ya, xb, mb, yb), "per_draw" with (run, metric). The same
    definitions serve the main sample (secondary) and the held-out runs (confirm). Without the valuation
    variants items 22-25 are left out (m = 23)."""
    main = runs["main"]
    spec = {}
    m = main.counted(252)
    zr = lambda r: r.mean_R("zone", 252)
    for i, (gr, name) in enumerate((("vol", "zone - vol"), ("fixed", "zone - fixed"), ("lump", "zone - lump"),
                                    ("gaps", "zone - gaps only")), start=1):
        x, mm = theta(main, "zone", gr)
        spec[i] = (name, "paired", (x, mm, main.year))
    for i, cfg in ((5, "1:2:3:2:2"), (6, "1:2:4")):
        r = runs[cfg]
        x, mm = theta(r, "zone", "random")
        spec[i] = (f"zone - random at {cfg}", "paired", (x, mm, r.year))
    for i, (a, b) in ((7, ("main", "1:1:1:1")), (8, ("1:2:3:2:2", "1:1:1:1:1")), (9, ("1:2:4", "1:1:1"))):
        ra, rb = runs[a], runs[b]
        _check_same_plans(ra, rb)
        spec[i] = (f"zone at {ra.config if a != 'main' else '1:2:4:1'} - zone at {b}", "paired",
                   (zr(ra) - zr(rb), ra.counted(252) & rb.counted(252), ra.year))
    for i, cfg in ((10, "close confirm"), (14, "time limit"), (16, "200-day"), (18, "stop 20%")):
        r = runs[cfg]
        x, mm = theta(r, "zone", "random")
        spec[i] = (f"{cfg}: zone - random", "paired", (x, mm, r.year))
        _check_same_plans(r, main)
        spec[i + 1] = (f"{cfg} (zone) - main (zone)", "paired", (zr(r) - zr(main), r.counted(252) & m, r.year))
    rb = runs["breakout start"]
    x, mm = theta(rb, "zone", "random")
    spec[12] = ("breakout start: zone - random", "paired", (x, mm, rb.year))
    spec[13] = ("breakout start (zone) - main (zone), unpaired", "unpaired",
                (zr(rb), rb.counted(252), rb.year, zr(main), m, main.year))
    x, mm = theta(main, "zone", "random")
    spec[20] = ("without extremes: zone - random", "paired", (x, mm & ~main.extreme(252), main.year))
    spec[21] = ("without earnings: zone - random", "paired",
                (x, mm & ~main.earnings_flag(252) & ~main.unknown(252), main.year))
    if valuation:
        spec[22] = ("valuation A: zone - random", "paired", (x, mm & main.pe_a, main.year))
        spec[23] = ("valuation B: zone - random", "paired", (x, mm & main.pe_b, main.year))
        for i, flag, name in ((24, main.pe_a, "A"), (25, main.pe_b, "B")):
            z = zr(main)
            spec[i] = (f"{name} starts (zone) - the other starts where {name} can be evaluated (zone), unpaired",
                       "unpaired", (z, m & flag, main.year, z, m & main.pe_defined & ~flag, main.year))
    spec[26] = ("win rate: zone - random", "per_draw", (main, "win_rate"))
    spec[27] = ("payoff ratio: zone - random", "per_draw", (main, "payoff"))
    return spec


def evaluate(kind: str, args: tuple) -> dict:
    """An item's mean difference and its interval (S14.1's method)."""
    if kind == "paired":
        return paired(*args)
    if kind == "unpaired":
        return unpaired(*args)
    return per_draw(*args)


def secondary(runs: dict, valuation: bool = True) -> dict:
    """The items of S14.3 at 252 bars (paired unless marked) with one Holm family at 0.10."""
    items = {}
    for i, (name, kind, args) in item_specs(runs, valuation).items():
        items[i] = {"item": name, **evaluate(kind, args)}
    main = runs["main"]
    x, mm = theta(main, "zone", "random")
    ef, un = main.earnings_flag(252), main.unknown(252)
    items[21]["share_left_out"] = float(ef[mm].mean())
    items[21]["share_left_out_report_date_missing"] = float((un & ~ef)[mm].mean())
    items[21]["zone_only_check"] = paired(x, mm & ~main.earnings_flag(252, ("zone",)) & ~un, main.year)
    pv = {i: v["p"] for i, v in items.items()}
    hol = T.holm(pv, alpha=0.10)
    for i in items:
        items[i]["p_holm"], items[i]["holm_reject"] = hol[i]["p_holm"], hol[i]["reject"]
    return dict(sorted(items.items()))


def _check_same_plans(a: Run, b: Run) -> None:
    if not (np.array_equal(a.t, b.t) and np.array_equal(a.sym, b.sym)):
        raise ValueError(f"{a.config} and {b.config} do not have the same plans")


CONFIRMABLE = (5, 6) + tuple(range(10, 20)) + (22, 23, 24, 25)   # S14.4: the rule items


def confirmation_set(main_result: dict, items: dict) -> list:
    """S14.4: the main rule if it passes the screen, plus the rule items (CONFIRMABLE) with a Holm-adjusted
    p below 0.10 and |theta| >= 1 pp; each with the direction found."""
    out = []
    if main_result["screen"]["passes"]:
        out.append(("main", float(np.sign(main_result["theta"]["theta"]))))
    for i, v in items.items():
        if i not in CONFIRMABLE:
            continue
        if v["p_holm"] < 0.10 and abs(v["theta"]) >= 0.01:
            out.append((i, float(np.sign(v["theta"]))))
    return out


def confirm(runs: dict, conf_set: list, valuation: bool = True) -> dict:
    """S14.4 on the held-out runs: each member of the confirmation set (the main rule or an item number,
    with its direction on the main sample) counts as confirmed when its one-sided lower bound at level
    1 - 0.05/K in that direction is above 0, K = the set's size, with S14.1's interval method."""
    K = len(conf_set)
    spec = item_specs(runs, valuation) if any(i != "main" for i, _ in conf_set) else {}
    out = {}
    for i, sign in conf_set:
        if i != "main" and i not in CONFIRMABLE:
            raise ValueError(f"item {i} cannot be confirmed (S14.4: the main rule and items 5, 6, 10-19, 22-25)")
        if i == "main":
            x, m = theta(runs["main"], "zone", "random", 252)
            kind, args, label = "paired", (x, m, runs["main"].year), "main: zone - random"
        else:
            label, kind, args = spec[i]
        if kind == "paired":
            x, m, years = args
            mm = m & np.isfinite(x)
            r = T.one_sided_lower(x[mm], T.Blocks(T.year_block(years[mm])), sign, K)
        elif kind == "unpaired":
            xa, ma, ya, xb, mb, yb = args
            ma, mb = ma & np.isfinite(xa), mb & np.isfinite(xb)
            r = T.one_sided_lower_unpaired(xa[ma], ya[ma], xb[mb], yb[mb], sign, K)
        else:
            raise ValueError(f"item {i} has no one-sided bound")
        out[str(i)] = {"item": label, "direction": sign, "K": K, **r}
    return out


# ----------------------------------------------------------------------------- descriptive results
def descriptive(runs: dict) -> dict:
    """Other horizons, the time limit on plans whose main zone group had no fill within 42 bars, every
    ratio's zone - random, vol - fixed with both groups' deployed share and depth, the S12 metrics of every
    group against zone, the valuation start counts and the S1 counts (S12, S14.3, S14.5)."""
    main = runs["main"]
    out = {"horizons": {}}
    for h, width in ((63, 1), (126, 1), (252, 1), (504, 2)):
        x, m = theta(main, "zone", "random", h)
        out["horizons"][h] = paired(x, m, main.year, width)
    late = main.g["zone"]["first_fill"][:, 0]
    nofill42 = (late < 0) | (late > S.TIME_LIMIT_BARS)
    r = runs["time limit"]
    _check_same_plans(r, main)
    x, m = theta(r, "zone", "random")
    out["time_limit_no_fill_in_42"] = {"zone - random": paired(x, m & nofill42, r.year),
                                       "time limit (zone) - main (zone)": paired(
                                           r.mean_R("zone", 252) - main.mean_R("zone", 252),
                                           r.counted(252) & main.counted(252) & nofill42, r.year),
                                       "share_of_plans": float(nofill42[main.counted(252)].mean())}
    out["ratios_zone_minus_random"] = {}
    for cfg in ("1:2:3:2:2", "1:2:4", "1:1:1:1", "1:1:1:1:1", "1:1:1"):
        rr = runs[cfg]
        x, m = theta(rr, "zone", "random")
        out["ratios_zone_minus_random"][cfg] = paired(x, m, rr.year)
    x, m = theta(main, "vol", "fixed")
    out["vol_minus_fixed"] = paired(x, m, main.year)
    m = main.counted(252)
    out["mean_R252"] = {g: float(main.mean_R(g, 252)[m].mean()) for g in main.g}
    out["deployed_252"] = {g: float(main.field(g, "deployed", 252).mean(axis=1)[m].mean()) for g in main.g}
    out["start_depth"] = {g: start_depth(main, g, m) for g in main.g if g != "lump"}
    out["metrics_zone_minus_group"] = {
        g: {met: {"own_fills": per_draw(main, met, group=g), "common_subset": per_draw(main, met, group=g, common=True)}
            for met in METRICS} for g in main.g if g != "zone"}
    out["metric_levels"] = {g: metric_levels(main, g, m) for g in main.g}
    out["valuation_starts"] = {"defined": int((m & main.pe_defined).sum()), "A": int((m & main.pe_a).sum()),
                               "B": int((m & main.pe_b).sum()), "plans": int(m.sum())}
    out["step8_choice"] = "vol" if out["mean_R252"].get("vol", -1) >= out["mean_R252"].get("fixed", -1) else "fixed"
    out["cleaning"] = cleaning_counts(main)
    return out


def metric_levels(run: Run, group: str, mask: np.ndarray, h: int = 252) -> dict:
    """A group's own S12 values at h (each draw over its plans with a fill, averaged over the draws)."""
    f = run.field(group, "fills", h) > 0
    f &= mask[:, None]
    R = run.R(group, h)
    vals = {"plans_with_fill": float(f.sum(axis=0).mean())}
    with np.errstate(invalid="ignore", divide="ignore"):
        win = np.where(f, R > 0, False).sum(axis=0) / f.sum(axis=0)
        mw = np.where(f & (R > 0), R, 0).sum(axis=0) / (f & (R > 0)).sum(axis=0)
        ml = np.where(f & ~(R > 0), R, 0).sum(axis=0) / (f & ~(R > 0)).sum(axis=0)
        vals["win_rate"] = float(_nanmean(win))
        vals["payoff"] = float(_nanmean(mw / np.abs(ml)))
        ac = run.field(group, "avgcost", h) / run.c_t[:, None] - 1.0
        vals["avgcost"] = float(_nanmean(_nanmean(np.where(f, ac, np.nan), 0)))
        vals["safety_margin"] = float(_nanmean(_nanmean(np.where(f, run.field(group, "worst", h), np.nan), 0)))
        vals["stop_rate"] = float(_nanmean(_nanmean(np.where(f, run.field(group, "stopped", h), np.nan), 0)))
    return vals


def cleaning_counts(run: Run) -> dict:
    """S1: plans left out by reason and horizon, the moves over 50% with their checks, delisted stocks and
    data gaps."""
    by_h = {}
    for h, i in H.items():
        codes = run.excl[:, i]
        by_h[h] = {"counted": int((codes == 0).sum()),
                   **{S.EXCL_WHY[c]: int((codes == c).sum()) for c in range(1, len(S.EXCL_WHY))}}
    return {"plans_by_horizon": by_h,
            "moves_over_50pct": {s: c.get("jumps", []) for s, c in run.cleaning.items() if c.get("jumps")},
            "delisted": sorted(s for s, c in run.cleaning.items() if c.get("delisted")),
            "data_gap": sorted(s for s, c in run.cleaning.items() if c.get("data_gap")),
            "plans_touching_a_missing_report_date": {h: int((run.unknown(h) & run.counted(h)).sum()) for h in H},
            "starts_skipped": run.skipped}
