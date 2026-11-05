"""Look-ahead test: changing or removing the data after day t must not change any indicator value,
any signal, price-action reading or setup, or candlestick pattern at dates <= t. No network."""
import numpy as np
import pandas as pd
import pytest

from conftest import synthetic_bars

I = pytest.importorskip("indicators", reason="indicators.py is not importable yet; the integrator runs this test")
import candlesticks as CS  # noqa: E402
import price_action as PA  # noqa: E402
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


PA_SERIES = ("ema", "types", "state", "always_in", "h_cnt", "h_ext", "l_cnt", "l_ext", "gap_bull", "gap_bear",
             "tr", "rh", "rl")


@pytest.fixture(scope="module")
def full_patterns(full):
    bars, ind, _ = full
    return PA.analyze_frame(bars, ind), PA.events(bars, ind), CS.pattern_frame(bars, ind)


def test_fixture_exercises_the_pattern_libraries(full_patterns):
    _, pa_ev, cs_fr = full_patterns
    assert pa_ev.to_numpy().any(axis=0).sum() >= 6, pa_ev.sum().to_dict()
    assert cs_fr.to_numpy().any(axis=0).sum() >= 6, cs_fr.sum().to_dict()


@pytest.mark.parametrize("t", CUTS)
def test_patterns_have_no_lookahead(full, full_patterns, t):
    bars, _, _ = full
    fr_full, pa_full, cs_full = full_patterns
    for variant in (bars.iloc[:t + 1], scramble_after(bars, t, seed=t)):
        ind = I.compute_all(variant)
        fr = PA.analyze_frame(variant, ind)
        for key in PA_SERIES:
            np.testing.assert_array_equal(np.asarray(fr[key])[:t + 1], np.asarray(fr_full[key])[:t + 1], err_msg=key)
        pd.testing.assert_frame_equal(PA.events(variant, ind).iloc[:t + 1], pa_full.iloc[:t + 1])
        pd.testing.assert_frame_equal(CS.pattern_frame(variant, ind).iloc[:t + 1], cs_full.iloc[:t + 1])
        known = lambda ws: [w for w in ws if w["confirm"] <= t]  # noqa: E731
        assert known(fr["wedges"]) == known(fr_full["wedges"])
        assert [f for f in fr["fails"] if f["fail"] <= t] == [f for f in fr_full["fails"] if f["fail"] <= t]


# A dividend read one bar early can only change row t, and only by switching off a gap down at t, so the
# dividend cuts sit on gap_down_atr bars of the fixture (D[t] = 0): GAP_DOWN_CUTS with no dividend on t+1, which
# a later dividend made huge would cancel, and AHEAD_CUTS, where the fixture pays at least the drop on t+1.
GAP_DOWN_CUTS = (267, 340)
AHEAD_CUTS = (345, 423)


@pytest.fixture(scope="module")
def full_dividends(full):
    """The fixture bars with a dividend column (as fetch attaches it): 2% of the close every 63 bars; on every
    other gap_down_atr fire the whole opening drop, which the gap-down rule (close[t-1] - D[t]) then no longer
    counts; and on the bar after each AHEAD_CUTS fire that fire's drop, which only a rule reading the next bar's
    dividend would take out of it."""
    bars, ind, ev = full
    drop = (bars["close"].shift(1) - bars["open"]).to_numpy()
    div = np.zeros(len(bars))
    div[5::63] = bars["close"].to_numpy()[5::63] * 0.02
    downs = np.flatnonzero(ev["gap_down_atr"].to_numpy())[::2]
    div[downs] = drop[downs]
    ahead = np.array(AHEAD_CUTS)
    div[ahead + 1] = np.maximum(div[ahead + 1], drop[ahead])
    with_div = bars.assign(dividend=div)
    return with_div, S.events(with_div, ind)


@pytest.mark.parametrize("t", CUTS + GAP_DOWN_CUTS + AHEAD_CUTS)
def test_dividend_adjusted_gap_rules_have_no_lookahead(full, full_dividends, t):
    _, ind, ev_plain = full
    bars, ev_full = full_dividends
    div = bars["dividend"].to_numpy()
    if t in GAP_DOWN_CUTS + AHEAD_CUTS:
        # the cut can catch a leak: facts of the fixture, not of the rule under test
        drop = bars["close"].iloc[t - 1] - bars["open"].iloc[t]
        assert ev_plain["gap_down_atr"].iloc[t] and div[t] == 0
        assert div[t + 1] >= drop if t in AHEAD_CUTS else div[t + 1] == 0
    later = np.arange(len(bars)) > t
    variants = [(bars.iloc[:t + 1], None),                                  # the history ends at t
                (scramble_after(bars, t, seed=t), None),                    # every column after t scrambled
                (bars.assign(dividend=np.where(later, 1e9, div)), ind),     # only the later dividends: huge ...
                (bars.assign(dividend=np.where(later, 0.0, div)), ind)]     # ... or none
    for variant, variant_ind in variants:
        ev = S.events(variant, I.compute_all(variant) if variant_ind is None else variant_ind)
        pd.testing.assert_frame_equal(ev.iloc[:t + 1], ev_full.iloc[:t + 1])


def test_the_dividend_fixture_changes_a_gap_event(full, full_dividends):
    _, _, ev_plain = full
    _, ev_div = full_dividends
    changed = (ev_plain != ev_div).sum()
    assert changed[changed > 0].index.tolist() == ["gap_down_atr"]    # a dividend never changes gap_up_atr
