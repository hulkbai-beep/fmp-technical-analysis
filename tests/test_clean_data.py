"""Tests for scripts/clean_data.py and the per-symbol exclusion windows (no network)."""
import numpy as np
import pandas as pd

import clean_data as CD
import levels_backtest as B
from conftest import synthetic_bars
from test_regime_split import hourly_bars

OHLC = ["open", "high", "low", "close"]


def daily_of(h: pd.DataFrame) -> pd.DataFrame:
    d = h.groupby(h.index.normalize()).agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                            "volume": "sum"})
    d.index.name = "date"
    return d


def write(df: pd.DataFrame, path) -> None:
    df[["open", "high", "low", "close", "volume"]].rename_axis("Date").rename(columns=str.capitalize).to_csv(path)


def test_split_fraction():
    assert CD.split_fraction(2.0004) == 2.0 and CD.split_fraction(0.25) == 0.25
    assert CD.split_fraction(1 / 28) == 1 / 28 and CD.split_fraction(1.5) == 1.5
    assert CD.split_fraction(0.836) is None and CD.split_fraction(1.03) is None


def test_clean_daily_placeholder_and_liquidity():
    d = synthetic_bars(n=300)[["open", "high", "low", "close", "volume"]]
    d.iloc[100:160, d.columns.get_loc("volume")] = 1_000.0          # about $0.1M a day
    last = d.index[-1] + pd.offsets.BDay(1)
    d.loc[last] = [90.0, 90.0, 90.0, 90.0, 0.0]
    bars, counts, windows = CD.clean_daily(d)
    assert len(bars) == 300 and counts["placeholders"] == 1 and counts["placeholder_dates"] == f"{last:%Y-%m-%d}"
    assert len(windows) == 1 and windows[0][2] == "illiquid"
    a, b = windows[0][:2]
    assert a == d.index[110] and b == d.index[168]                  # more than half of the 20 bars are thin


def test_align_hourly_basis_mismatch_range_short():
    h = hourly_bars(days=60)
    daily = daily_of(h)
    raw = h.copy()
    days = raw.index.normalize()
    early = days < daily.index[25]                                  # a 2:1 basis difference, then a spin-off one
    raw.loc[early, OHLC] /= 2
    raw.loc[early, "volume"] *= 2
    mid = (days >= daily.index[25]) & (days < daily.index[40])
    raw.loc[mid, OHLC] /= 0.9
    odd = days == daily.index[50]                                   # last close 5% off the daily close
    raw.loc[raw.index[odd][-1], ["close", "high"]] = raw.loc[raw.index[odd][-1], "close"] * 1.05
    spike = days == daily.index[52]
    raw.loc[raw.index[spike][3], "high"] = daily["high"].iat[52] * 1.03
    short = days == daily.index[55]
    raw = raw.drop(raw.index[short][3:])
    clean, s = CD.align_hourly(raw, daily)
    assert (s.loc[daily.index[:25], "factor"] == 2.0).all() and np.allclose(s.loc[daily.index[25:40], "factor"], 0.9)
    assert (s.loc[daily.index[:25], "vol_factor"] == 2.0).all() and (s.loc[daily.index[25:40], "vol_factor"] == 1).all()
    ok = ~clean.index.normalize().isin(daily.index[[50, 52, 55]])
    assert np.allclose(clean.loc[ok, "close"], h.loc[clean.index[ok], "close"])
    assert np.allclose(clean.loc[ok, "volume"], h.loc[clean.index[ok], "volume"])
    st = s["status"]
    assert st[daily.index[50]] == "close mismatch" and st[daily.index[52]] == "range"
    assert st[daily.index[55]] == "short session"
    assert set(st.drop(daily.index[[50, 52, 55]])) == {"ok", "adjusted"}
    w = CD.session_windows(s)
    assert [x[2] for x in w] == ["close mismatch", "range", "short session"]


def test_basic_hourly_removals():
    h = hourly_bars(days=10)
    daily = daily_of(h).iloc[:-1]                                   # last session has no daily bar yet
    raw = h.copy()
    raw.loc[h.index[0] - pd.Timedelta(hours=1)] = raw.iloc[0]       # 08:30 pre-market bar
    raw.loc[h.index[5], "volume"] = 0
    raw.loc[h.index[8], "open"] = raw.loc[h.index[8], "high"] * 1.01
    raw = pd.concat([raw, raw.iloc[[20]]]).sort_index(kind="mergesort")
    out, c = CD.basic_hourly(raw, daily)
    assert c["off_session"] == 1 and c["zero_volume"] == 1 and c["bad_ohlc"] == 1 and c["duplicates"] == 1
    assert c["no_daily_bars"] == 7 and c["no_daily_days"] == f"{h.index[-1]:%Y-%m-%d}"
    assert len(out) == 70 - 7 - 2


def test_jumps_confirmed_and_suspect(tmp_path):
    h = hourly_bars(days=120)
    later = h.index.normalize() >= h.index.normalize().unique()[60]
    h.loc[later, OHLC] *= 1.8                                      # a real +80% day, in both series
    daily = daily_of(h)
    bad = daily.copy()
    bad.loc[bad.index[90], ["high", "close"]] *= 1.7               # only the daily bar jumps
    (tmp_path / "daily").mkdir()
    (tmp_path / "hourly").mkdir()
    write(daily, tmp_path / "daily" / "AAA.csv")
    write(bad, tmp_path / "daily" / "BBB.csv")
    write(h, tmp_path / "hourly" / "AAA.csv")
    write(h, tmp_path / "hourly" / "BBB.csv")
    assert CD.main(["--daily", str(tmp_path / "daily"), "--hourly", str(tmp_path / "hourly"),
                    "--out", str(tmp_path / "o"), "--min-dollar-volume", "0"]) == 0
    text = (tmp_path / "o" / "report.md").read_text(encoding="utf-8")
    assert "## Daily jumps" in text and "confirmed" in text and "suspect" in text
    ex = pd.read_csv(tmp_path / "o" / "exclusions.csv")
    sus = ex[ex["reason"] == "suspect jump"]
    assert list(sus["symbol"]) == ["BBB"] and sus["start"].iat[0] == f"{daily.index[90 - CD.SUSPECT_BARS]:%Y-%m-%d}"
    assert {"close mismatch"} <= set(ex.loc[ex["symbol"] == "BBB", "reason"])
    cleaned = B.load_csv(tmp_path / "o" / "hourly" / "AAA.csv")
    assert len(cleaned) == len(h) and np.allclose(cleaned["close"], h["close"])
    win = B.load_exclusions(tmp_path / "o" / "exclusions.csv")
    assert set(win) == {"BBB"} and len(win["BBB"]) == 1             # hourly-only rows left out
    assert len(B.load_exclusions(tmp_path / "o" / "exclusions.csv", hourly=True)["BBB"]) > 1


def test_excluded_bars_cover_whole_days_and_placebo_pool():
    idx = pd.DatetimeIndex([pd.Timestamp("2021-01-04 09:30") + pd.Timedelta(days=d, hours=k)
                            for d in range(3) for k in range(7)])
    ex = B.excluded_bars(idx, [("2021-01-05", "2021-01-05")], forward=1)
    assert list(np.flatnonzero(ex)) == list(range(6, 14))          # last bar of day 1 sees day 2's first
    asof = [(t, 1.0, 1.0) for t in range(0, 21, 3)]
    pool = B.placebo_pool(asof, ex)
    assert pool == [0, 1, 5, 6] and B.pool_offsets(pool, 1)[0] == pool[(1 + B.PLACEBO_OFFSETS[0]) % 4]
    assert B.file_windows(["x/AAA.csv"], ["2020-02-20:2020-04-30"], None) == [[("2020-02-20", "2020-04-30")]]


def test_levels_walk_exclusion_flag():
    bars = synthetic_bars(n=600)
    win = [(str(bars.index[400].date()), str(bars.index[420].date()))]
    rows = B.walk(bars, step=10, forward=10, windows=win)
    assert rows["excluded"].any() and not rows["excluded"].all()
    plain = B.walk(bars, step=10, forward=10)
    assert not plain["excluded"].any()
    keep = ~rows["excluded"]
    assert rows.loc[keep, "outcome"].fillna("-").equals(plain.loc[keep, "outcome"].fillna("-"))
