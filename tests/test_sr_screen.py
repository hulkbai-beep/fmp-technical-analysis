"""Tests for scripts/sr_screen.py (no network)."""
import pandas as pd

import sr_screen as SS
from conftest import synthetic_bars
from test_levels_backtest import frame


def test_nearest_respects_side_width_and_reach():
    assert SS.nearest([90, 97, 99.9, 105], 100.0, 1.0, "support") == 97   # 90 out of reach, 99.9 holds the close
    assert SS.nearest([90, 95, 105], 100.0, 1.0, "resistance") is None       # 105 is 5 ATR away
    assert SS.nearest([100.2, 103], 100.0, 1.0, "resistance") == 103          # 100.2's zone holds the close


def test_stop_outcome():
    bars = frame([11, 10.5, 10.4], [10, 9.6, 9.0], [10.5, 10, 9.5])
    assert SS.stop_outcome(bars, 0, 9.5, "long", 1) == "held"
    assert SS.stop_outcome(bars, 0, 9.5, "long", 5) == "broken"
    assert SS.stop_outcome(bars, 0, 10.45, "short", 5) == "broken"


def test_groups_and_main_report(tmp_path):
    assert SS.group("gap_up_top") == "gap" and SS.group("avwap_52w_high") == "anchored_vwap"
    assert SS.group("pivot_low") == "pivot" and SS.group("demand") == "supply_demand"
    p = tmp_path / "q.csv"
    synthetic_bars(n=420)[["open", "high", "low", "close", "volume"]].rename_axis("Date").to_csv(p)
    assert SS.main([str(p), "--step", "20", "--split", "2021-01-01", "--out", str(tmp_path / "o")]) == 0
    rows = pd.read_csv(tmp_path / "o" / "rows.csv")
    assert {"source", "stop"} == set(rows["kind"]) and "weekly_pivot" in set(rows["source"])
    assert "## Structural stop" in (tmp_path / "o" / "report.md").read_text(encoding="utf-8")
