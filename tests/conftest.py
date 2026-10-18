"""Shared test setup: put scripts/ on sys.path and provide synthetic price data (no network)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def synthetic_bars(n=800, seed=7, start="2020-01-02", drift=0.0004, vol=0.015, start_price=100.0):
    """Random-walk daily OHLCV on business days. adj_* = split-adjusted values x a constant 0.98
    (as if a dividend had been paid long ago), so adjusted returns equal unadjusted ones."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n, name="date")
    rets = rng.normal(drift, vol, n)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = close * np.exp(rng.normal(0, vol / 3, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    volume = rng.integers(800_000, 1_200_000, n).astype(float)
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
    for c in ("open", "high", "low", "close"):
        df["adj_" + c] = df[c] * 0.98
    return df


@pytest.fixture
def bars():
    return synthetic_bars()


@pytest.fixture
def make_bars():
    return synthetic_bars
