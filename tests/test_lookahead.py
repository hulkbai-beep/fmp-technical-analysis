"""Look-ahead test: changing or removing the data after day t must not change any indicator value
or any signal at dates <= t. No network."""
import numpy as np
import pandas as pd
import pytest

from conftest import synthetic_bars

I = pytest.importorskip("indicators", reason="indicators.py is not importable yet; the integrator runs this test")
import signal_stats as S  # noqa: E402

N = 900
CUTS = (199, 251, 333, 480, 612, 777, N - 2)


def eventful_bars(n=N, seed=16):
    """synthetic_bars with volume spikes and opening gaps, so every rule family fires somewhere."""
    bars = synthetic_bars(n, seed=seed, vol=0.02)
    rng = np.random.default_rng(seed + 1)
    spike = rng.random(n) < 0.08
    gap = rng.choice([-1.0, 0.0, 1.0], size=n, p=[0.04, 0.92, 0.04])
    prev_close = bars["close"].shift(1).fillna(bars["close"].iloc[0]).to_numpy()
    open_ = np.where(gap != 0, prev_close * (1 + 0.06 * gap), bars["open"].to_numpy())
    close = bars["close"].to_numpy()
    out = bars.assign(
        open=open_,
        high=np.maximum(bars["high"].to_numpy(), np.maximum(open_, close)),
        low=np.minimum(bars["low"].to_numpy(), np.minimum(open_, close)),
        volume=bars["volume"].to_numpy() * np.where(spike | (gap != 0), 3.0, 1.0),
    )
    for c in ("open", "high", "low", "close"):
        out["adj_" + c] = out[c] * 0.98
    return out


def scramble_after(bars, t, seed):
    """Copy of bars whose rows after position t are wild random values (no OHLC consistency)."""
    rng = np.random.default_rng(seed)
    out = bars.copy()
    k = len(bars) - t - 1
    for col in out.columns:
        vals = rng.uniform(0.0, 5e8, k) if col == "volume" else np.exp(rng.uniform(-3.0, 9.0, k))
        out.iloc[t + 1:, out.columns.get_loc(col)] = vals
    return out


@pytest.fixture(scope="module")
def full():
    bars = eventful_bars()
    ind = I.compute_all(bars)
    return bars, ind, S.events(bars, ind)


def test_fixture_exercises_every_signal(full):
    _, _, ev = full
    never = [sid for sid in S.SIGNAL_IDS if not ev[sid].any()]
    assert never == [], f"signals that never fire on the look-ahead fixture: {never}"


@pytest.mark.parametrize("t", CUTS)
def test_no_lookahead(full, t):
    bars, ind_full, ev_full = full
    head = bars.iloc[:t + 1]
    wild = scramble_after(bars, t, seed=t)
    assert not np.allclose(wild.iloc[t + 1:].to_numpy(), bars.iloc[t + 1:].to_numpy())

    ind_head, ind_wild = I.compute_all(head), I.compute_all(wild)
    ev_head, ev_wild = S.events(head, ind_head), S.events(wild, ind_wild)

    ref_ind = ind_full.iloc[:t + 1]
    pd.testing.assert_frame_equal(ind_head, ref_ind, check_exact=True, obj=f"ind on bars[:{t + 1}]")
    pd.testing.assert_frame_equal(ind_wild.iloc[:t + 1], ref_ind, check_exact=True,
                                  obj=f"ind with bars after {t} scrambled")

    ref_ev = ev_full.iloc[:t + 1]
    pd.testing.assert_frame_equal(ev_head, ref_ev, obj=f"events on bars[:{t + 1}]")
    pd.testing.assert_frame_equal(ev_wild.iloc[:t + 1], ref_ev, obj=f"events with bars after {t} scrambled")
    assert list(ind_head.columns) == list(I.IND_COLUMNS)
