#!/usr/bin/env python3
"""
Clean the daily and hourly bar files before the support/resistance tests, and list every change.

  python scripts/clean_data.py --daily DIR [--hourly DIR] --out DIR
                               [--min-dollar-volume 1e7] [--max-move 0.5] [--holdout-from DATE]

Writes OUT/daily/<SYMBOL>.csv, OUT/hourly/<SYMBOL>.csv (with --hourly), OUT/exclusions.csv and
OUT/report.md. With --holdout-from DATE the hourly bars before DATE are also written to
OUT/hourly-screen/, so a screen run on that folder cannot read the held-out bars. Errors are removed; real extremes are kept. Bars that are right but should not be
counted (an illiquid stretch, an unverified jump, an hourly session that disagrees with its daily
bar) stay in the files, so indicators run through them as usual, and are listed in exclusions.csv
(symbol, applies, start, end, reason). The test scripts take --exclusions FILE and treat each row
like an --exclude window for that symbol only; `applies` is `all` (daily and hourly tests) or
`hourly`.

Daily (split-adjusted, as the skill uses them):
  - placeholder bars (volume 0 and open = high = low = close) are removed;
  - liquidity: bars whose trailing LIQ_BARS-bar median of close x volume is below
    --min-dollar-volume are excluded (runs of such bars become one window);
  - jumps: a close more than --max-move away from the previous close is checked against the hourly
    sessions of that day and the day before. Both clean (status ok or adjusted below): confirmed and
    kept. Otherwise SUSPECT_BARS trading days either side are excluded. Without hourly data for the
    two days the jump is listed as unverified and kept.

Hourly (fetch_intraday.py files, bar start in US Eastern time):
  - bars starting outside SESSION_STARTS (09:30 .. 15:30) are removed, as are duplicate stamps,
    bars whose open or close lies outside high-low, non-positive prices and zero volume;
  - sessions on days without a daily bar are removed (a session still trading when the data was
    pulled, or a holiday print);
  - basis: ratio = daily close / last hourly close per session, and level = its centred median over
    LEVEL_SESSIONS sessions. FMP's hourly history is not adjusted the same way as the daily one
    everywhere (spin-offs, some splits), and such a difference holds for a long run of sessions.
    When |level - 1| > SPLIT_TOL the session's prices are multiplied by the level (volume divided
    by it only when the level is a split fraction n/m, m <= 4, within SPLIT_SNAP; a spin-off
    changes the price basis, not the share count). A session whose own ratio is more than SPLIT_TOL
    off its level is excluded (`close mismatch`, e.g. a closing auction far from the last trade);
  - after that, a session whose high or low lies outside the daily bar's range by more than
    RANGE_TOL is excluded (`range`);
  - sessions with fewer than MIN_SESSION_BARS bars are excluded (`short session`). Half days have
    4 bars and are kept; sessions with fewer bars than the day's most common count across all
    symbols are counted as `missing bars` and kept;
  - daily bars from the first hourly session on that have no hourly session are counted.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import levels_backtest as B

SESSION_STARTS = tuple(f"{h:02d}:30" for h in range(9, 16))
SPLIT_TOL = 0.02          # session close vs daily close; larger differences are a basis change or a mismatch
SPLIT_SNAP = 0.01         # a level within this of n/m (or m/n), m <= 4, is a split
LEVEL_SESSIONS = 11       # centred median of the ratio over this many sessions
RANGE_TOL = 0.005         # hourly high / low may exceed the daily range by this much (auction prints)
MIN_SESSION_BARS = 4
LIQ_BARS = 20
MIN_DOLLAR_VOLUME = 1e7
MAX_MOVE = 0.5
SUSPECT_BARS = 40
FIELDS = ["open", "high", "low", "close", "volume"]


def split_fraction(x: float) -> float | None:
    """n/m (m <= 4) within SPLIT_SNAP of x or of 1/x, other than 1; else None."""
    if not (np.isfinite(x) and x > 0):
        return None
    for y, inv in ((x, False), (1 / x, True)):
        for m in range(1, 5):
            n = round(y * m)
            if n >= 1 and n != m and abs(y * m / n - 1) <= SPLIT_SNAP:
                return m / n if inv else n / m
    return None


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(first, last) positions of each run of True."""
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        if not v and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(mask) - 1))
    return out


def clean_daily(raw: pd.DataFrame, min_dollar_volume: float = MIN_DOLLAR_VOLUME) -> tuple[pd.DataFrame, dict, list]:
    """(bars, counts, exclusion windows [(start, end, reason)]) without the jump check."""
    flat = (raw["volume"] <= 0) & (raw["open"] == raw["high"]) & (raw["high"] == raw["low"]) & \
        (raw["low"] == raw["close"])
    bars = raw[~flat]
    dv = (bars["close"] * bars["volume"]).rolling(LIQ_BARS, min_periods=LIQ_BARS).median()
    thin = (dv < min_dollar_volume).to_numpy()
    windows = [(bars.index[a], bars.index[b], "illiquid") for a, b in runs(thin)]
    counts = {"bars": len(bars), "placeholders": int(flat.sum()),
              "placeholder_dates": " ".join(f"{d:%Y-%m-%d}" for d in raw.index[flat]),
              "illiquid_bars": int(thin.sum()), "zero_volume": int((bars["volume"] <= 0).sum())}
    return bars, counts, windows


def load_hourly(path) -> pd.DataFrame:
    """Hourly OHLCV as written, nothing dropped yet (basic_hourly does the checks and counts them)."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index(kind="mergesort")[FIELDS].apply(pd.to_numeric, errors="coerce")
    return df.dropna(subset=FIELDS[:4])



def basic_hourly(raw: pd.DataFrame, daily: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Regular-session, internally consistent bars on days with a daily bar; (bars, removed counts)."""
    n = len(raw)
    dup = raw.index.duplicated(keep="last")
    h = raw[~dup]
    off = ~h.index.strftime("%H:%M").isin(SESSION_STARTS)
    h = h[~off]
    o, hi, lo, c, v = (h[k] for k in FIELDS)
    bad = (lo <= 0) | (hi < lo) | (o > hi) | (o < lo) | (c > hi) | (c < lo)
    h = h[~bad]
    zero = h["volume"] <= 0
    h = h[~zero]
    day = h.index.normalize()
    nodaily = ~day.isin(daily.index)
    gone = sorted({f"{d:%Y-%m-%d}" for d in day[nodaily]})
    h = h[~nodaily]
    return h, {"raw_bars": n, "duplicates": int(dup.sum()), "off_session": int(off.sum()),
               "bad_ohlc": int(bad.sum()), "zero_volume": int(zero.sum()), "no_daily_bars": int(nodaily.sum()),
               "no_daily_days": " ".join(gone)}


def align_hourly(h: pd.DataFrame, daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Bars on the daily basis, and per session: bars, ratio, level, factor, status."""
    day = h.index.normalize()
    g = h.groupby(day)
    s = pd.DataFrame({"bars": g.size(), "last_close": g["close"].last(), "high": g["high"].max(),
                      "low": g["low"].min()})
    s["ratio"] = daily["close"].reindex(s.index) / s["last_close"]
    s["level"] = s["ratio"].rolling(LEVEL_SESSIONS, center=True, min_periods=1).median()
    s["factor"] = s["level"].where((s["level"] - 1).abs() > SPLIT_TOL, 1.0)
    s["vol_factor"] = [(split_fraction(f) or 1.0) if f != 1.0 else 1.0 for f in s["factor"]]
    s["status"] = np.where(s["factor"] != 1.0, "adjusted", "ok")
    s.loc[(s["ratio"] / s["level"] - 1).abs() > SPLIT_TOL, "status"] = "close mismatch"
    f = s["factor"].reindex(day).to_numpy()
    vf = s["vol_factor"].reindex(day).to_numpy()
    out = h.copy()
    for k in ("open", "high", "low", "close"):
        out[k] = out[k] * f
    out["volume"] = out["volume"] / vf
    dh, dl = daily["high"].reindex(s.index), daily["low"].reindex(s.index)
    outside = (s["high"] * s["factor"] > dh * (1 + RANGE_TOL)) | (s["low"] * s["factor"] < dl * (1 - RANGE_TOL))
    s.loc[outside & (s["status"] != "close mismatch"), "status"] = "range"
    s.loc[s["bars"] < MIN_SESSION_BARS, "status"] = "short session"
    return out, s


def check_jumps(daily: pd.DataFrame, sessions: pd.DataFrame | None, max_move: float = MAX_MOVE) -> list[dict]:
    """Daily closes more than max_move from the previous close, with the hourly verdict."""
    r = daily["close"].pct_change()
    out = []
    for i in np.flatnonzero((r.abs() > max_move).to_numpy()):
        d, p = daily.index[i], daily.index[i - 1]
        verdict = "unverified"
        if sessions is not None and d in sessions.index and p in sessions.index:
            clean = sessions.loc[[p, d], "status"].isin(["ok", "adjusted"]).all()
            verdict = "confirmed" if clean else "suspect"
        out.append({"date": d, "move": float(r.iloc[i]), "verdict": verdict, "i": int(i)})
    return out


def session_windows(s: pd.DataFrame) -> list[tuple]:
    """Excluded sessions as (start, end, reason), consecutive sessions with the same reason merged."""
    out = []
    bad = s["status"].isin(["close mismatch", "range", "short session"]).to_numpy()
    for a, b in runs(bad):
        seg = s.iloc[a:b + 1]
        start = a
        for j in range(1, len(seg) + 1):
            if j == len(seg) or seg["status"].iat[j] != seg["status"].iat[j - 1]:
                out.append((s.index[start], s.index[a + j - 1], seg["status"].iat[j - 1]))
                start = a + j
    return out


def _md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    head = "| | " + " | ".join(cols) + " |\n|" + "---|" * (len(cols) + 1) + "\n"
    return head + "".join(f"| {k} | " + " | ".join(str(r[c]) for c in cols) + " |\n" for k, r in df.iterrows())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--daily", required=True)
    ap.add_argument("--hourly")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-dollar-volume", type=float, default=MIN_DOLLAR_VOLUME)
    ap.add_argument("--max-move", type=float, default=MAX_MOVE)
    ap.add_argument("--holdout-from", help="also write hourly bars before this date to OUT/hourly-screen")
    a = ap.parse_args(argv)
    out = Path(a.out)
    (out / "daily").mkdir(parents=True, exist_ok=True)
    if a.hourly:
        (out / "hourly").mkdir(parents=True, exist_ok=True)
    excl, drows, hrows, jumps, notes = [], {}, {}, [], []
    hourly = {}
    daily = {}
    for p in sorted(Path(a.daily).glob("*.csv")):
        sym = p.stem.upper()
        bars, counts, windows = clean_daily(B.load_csv(p), a.min_dollar_volume)
        daily[sym] = bars
        drows[sym] = counts
        excl += [(sym, "all", x, y, why) for x, y, why in windows]
    if a.hourly:
        for p in sorted(Path(a.hourly).glob("*.csv")):
            sym = p.stem.upper()
            if sym not in daily:
                notes.append(f"{sym}: hourly file without daily file, skipped")
                continue
            h, counts = basic_hourly(load_hourly(p), daily[sym])
            hourly[sym] = (h, counts)
        per_day = pd.concat([h.index.normalize().value_counts() for h, _ in hourly.values()], axis=1, sort=True)
        mode = per_day.mode(axis=1)[0]
    for sym, bars in daily.items():
        sessions = None
        if sym in hourly:
            h, counts = hourly[sym]
            fixed, s = align_hourly(h, bars)
            sessions = s
            expected = mode.reindex(s.index)
            miss = int(((s["bars"] < expected) & (s["bars"] >= MIN_SESSION_BARS)).sum())
            first = s.index[0]
            no_hourly = int((~bars.index[bars.index >= first].isin(s.index)).sum())
            st = s["status"].value_counts()
            adj = s[s["status"] != "ok"]
            levels = sorted({round(float(x), 4) for x in s.loc[s["factor"] != 1.0, "factor"]})
            hrows[sym] = {"bars": len(fixed), "removed": counts["raw_bars"] - len(fixed),
                          "off_session": counts["off_session"], "duplicates": counts["duplicates"],
                          "bad_ohlc": counts["bad_ohlc"], "zero_volume": counts["zero_volume"],
                          "no_daily_days": counts["no_daily_days"] or "-",
                          "sessions": len(s), "adjusted": int(st.get("adjusted", 0)),
                          "factors": " ".join(f"{x:g}" for x in levels) or "-",
                          "close_mismatch": int(st.get("close mismatch", 0)), "range": int(st.get("range", 0)),
                          "short": int(st.get("short session", 0)), "half_days": int((expected == 4).sum()),
                          "missing_bars": miss, "daily_without_hourly": no_hourly,
                          "first": f"{first:%Y-%m-%d}", "last": f"{s.index[-1]:%Y-%m-%d}"}
            excl += [(sym, "hourly", x, y, why) for x, y, why in session_windows(s)]
            fixed.rename_axis("Date").rename(columns=str.capitalize).to_csv(out / "hourly" / f"{sym}.csv",
                                                                           float_format="%.6f")
            if a.holdout_from:
                (out / "hourly-screen").mkdir(exist_ok=True)
                early = fixed[fixed.index < pd.Timestamp(a.holdout_from)]
                early.rename_axis("Date").rename(columns=str.capitalize).to_csv(
                    out / "hourly-screen" / f"{sym}.csv", float_format="%.6f")
            for d, r in adj[adj["status"] != "adjusted"].iterrows():
                notes.append(f"{sym} {d:%Y-%m-%d}: {r['status']} (bars {r['bars']}, ratio {r['ratio']:.4f}, "
                             f"level {r['level']:.4f})")
        for j in check_jumps(bars, sessions, a.max_move):
            jumps.append({"symbol": sym, **j})
            if j["verdict"] == "suspect":
                lo, hi = max(0, j["i"] - SUSPECT_BARS), min(len(bars) - 1, j["i"] + SUSPECT_BARS)
                excl.append((sym, "all", bars.index[lo], bars.index[hi], "suspect jump"))
        bars.rename_axis("Date").rename(columns=str.capitalize).to_csv(out / "daily" / f"{sym}.csv",
                                                                      float_format="%.6f")
    ex = pd.DataFrame(excl, columns=["symbol", "applies", "start", "end", "reason"])
    for c in ("start", "end"):
        ex[c] = pd.to_datetime(ex[c]).dt.strftime("%Y-%m-%d")
    ex.sort_values(["symbol", "start"]).to_csv(out / "exclusions.csv", index=False)
    rep = ["# Data cleaning", "", f"- daily: {a.daily}", f"- hourly: {a.hourly or 'none'}",
           f"- hourly screen copy: bars before {a.holdout_from}" if a.holdout_from else "- hourly screen copy: none",
           f"- liquidity: trailing {LIQ_BARS}-bar median dollar volume >= {a.min_dollar_volume:,.0f}",
           f"- jumps checked: |close change| > {a.max_move:.0%}", f"- exclusion rows: {len(ex)}", "",
           "## Daily", "", _md(pd.DataFrame(drows).T), ""]
    if jumps:
        jt = pd.DataFrame(jumps).drop(columns="i")
        jt["date"] = pd.to_datetime(jt["date"]).dt.strftime("%Y-%m-%d")
        jt["move"] = jt["move"].map(lambda x: f"{x:+.1%}")
        rep += ["## Daily jumps", "", _md(jt.set_index("symbol")), ""]
    if hrows:
        rep += ["## Hourly", "", _md(pd.DataFrame(hrows).T), ""]
    if len(ex):
        summary = ex.assign(n=1).groupby(["applies", "reason"])["n"].sum().to_frame("windows")
        rep += ["## Exclusion windows by reason", "", _md(summary.reset_index().set_index("reason")), ""]
    if notes:
        rep += ["## Sessions excluded (not adjusted, not ok)", ""] + [f"- {x}" for x in notes] + [""]
    (out / "report.md").write_text("\n".join(rep), encoding="utf-8")
    print(f"wrote {out / 'report.md'}, {out / 'exclusions.csv'} ({len(ex)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
