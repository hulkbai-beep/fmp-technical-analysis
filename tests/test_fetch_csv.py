"""Tests for scripts/fetch_csv.py (no network)."""
import fetch_csv as FC
import levels_backtest as B


def test_bars_csv_sorts_dedupes_and_round_trips(tmp_path):
    rows = [{"date": "2024-01-03", "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 100},
            {"date": "2024-01-02", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 90},
            {"date": "2024-01-03", "open": 2, "high": 3.2, "low": 1, "close": 2.6, "volume": 110},
            {"date": "2024-01-04", "open": None, "high": 3, "low": 1, "close": 2, "volume": 0}]
    df = FC.bars_csv(rows)
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert list(df.index.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-03"]
    assert df.loc["2024-01-03", "Close"] == 2.6
    p = tmp_path / "X.csv"
    df.to_csv(p, float_format="%.6f")
    back = B.load_csv(p)
    assert back["close"].tolist() == [1.5, 2.6]


def test_universe_has_no_duplicates():
    syms = [s for g in FC.BASELINE_UNIVERSE.values() for s in g]
    assert len(syms) == len(set(syms)) == 32
