"""Tests for scripts/regime_split.py and scripts/vol_bands.py (no network)."""
import numpy as np
import pandas as pd

import regime_split as RS
import vol_bands as VB
from conftest import synthetic_bars


def test_efficiency_ratio_line_and_chop():
    line = pd.Series(np.arange(1.0, 41.0))
    assert RS.efficiency_ratio(line).iloc[-1] == 1.0
    chop = pd.Series([10.0, 11.0] * 20)
    assert RS.efficiency_ratio(chop).iloc[-1] == 0.0
    assert np.isnan(RS.efficiency_ratio(pd.Series([5.0] * 30)).iloc[-1])   # flat path


def test_variance_ratio_random_walk_and_reversal():
    rng = np.random.default_rng(1)
    rw = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 3000))))
    assert abs(RS.variance_ratio(rw).iloc[-1] - 1) < 0.25
    shocks = rng.normal(0, 0.01, 3001)
    rev = pd.Series(100 * np.exp(shocks[1:] - shocks[:-1]))               # MA(1) returns, theta = -1
    assert RS.variance_ratio(rev).iloc[-1] < 0.5


def test_bucket_and_excluded_span():
    assert RS.bucket(15, (20, 30), RS.LABELS["adx"]) == "adx<20"
    assert RS.bucket(30, (20, 30), RS.LABELS["adx"]) == "adx>=30"
    assert RS.bucket(np.nan, (20, 30), RS.LABELS["adx"]) == "n/a"
    idx = pd.bdate_range("2020-01-01", periods=30)
    ex = RS.excluded_bars(idx, [(str(idx[20].date()), str(idx[22].date()))], forward=5)
    # build bar t is out when t..t+9 touches bars 20..22
    assert list(np.flatnonzero(ex)) == list(range(11, 23))
    assert not RS.excluded_bars(idx, [], 5).any()


def test_walk_matched_placebo_and_exclusion():
    bars = synthetic_bars(n=700)
    win = (str(bars.index[500].date()), str(bars.index[520].date()))
    rows = RS.walk(bars, step=10, forward=10, symbol="X", windows=[win])
    assert not rows.empty and rows["excluded"].any() and not rows["excluded"].all()
    assert set(rows["adx"]) <= set(RS.LABELS["adx"]) | {"n/a"}
    excl_dates = set(rows.loc[rows["excluded"], "date"])
    assert all(d <= bars.index[520] for d in excl_dates)
    for v in RS.VARIABLES:
        assert all(len(p) <= 2 for p in rows["placebo_" + v])
    v = RS.verdict(rows[~rows["excluded"]])
    assert list(v.index) == [f"{k}: {b}" for k, b in RS.TESTED.items()] and set(v["pass"]) <= {True, False}


def test_regime_main_report(tmp_path):
    p = tmp_path / "q.csv"
    synthetic_bars(n=700)[["open", "high", "low", "close", "volume"]].rename_axis("Date").to_csv(p)
    assert RS.main([str(p), "--step", "20", "--split", "2021-06-01", "--exclude", "2021-01-04:2021-01-29",
                    "--out", str(tmp_path / "o")]) == 0
    text = (tmp_path / "o" / "report.md").read_text(encoding="utf-8")
    assert "## Pre-registered buckets: main sample" in text and "## By vr (main sample)" in text


def test_vol_bands_calibrated_on_normal_returns(tmp_path):
    bars = synthetic_bars(n=4000, drift=0.0, vol=0.012, seed=3)
    rows = VB.walk(bars, step=3, horizons=(10,), symbol="X")
    s = VB.stats(rows[rows["estimator"] == "rv60"])
    assert abs(s["cover1"] - 0.683) < 0.04 and abs(s["k68"] - 1) < 0.08
    assert 0.24 < s["touch_dn1"] < 0.36
    p = tmp_path / "q.csv"
    bars[["open", "high", "low", "close", "volume"]].rename_axis("Date").to_csv(p)
    assert VB.main([str(p), "--horizons", "5", "--split", "2030-01-01", "--exclude", "2021-01-04:2021-02-26",
                    "--out", str(tmp_path / "o")]) == 0
    text = (tmp_path / "o" / "report.md").read_text(encoding="utf-8")
    assert "## Excluded windows only" in text and "rv20 / 5" in text


def test_fetch_intraday_chunks_and_dedupes():
    import fetch_intraday as FI

    class Stub:
        def __init__(self):
            self.calls = []

        def get(self, endpoint, **p):
            self.calls.append((endpoint, p["from"], p["to"]))
            day = pd.Timestamp(p["from"])
            # newest first, one bar repeated across chunks
            return [{"date": f"{day:%Y-%m-%d} 10:30:00", "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 9},
                    {"date": f"{day:%Y-%m-%d} 09:30:00", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 5},
                    {"date": "2024-01-02 09:30:00", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 5}]

    c = Stub()
    df, problems = FI.fetch_symbol(c, "X", "1hour", "2024-01-02", "2024-04-30")
    assert [x[1] for x in c.calls] == ["2024-01-02", "2024-03-03"] and c.calls[0][0] == "historical-chart/1hour"
    assert not problems and list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index.is_monotonic_increasing and not df.index.duplicated().any() and len(df) == 4
