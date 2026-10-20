"""
Signal statistics (the quant core): a fixed, pre-registered signal library and this stock's own
historical forward-return statistics for each signal, against unconditional and same-regime
baselines.

Signals are evaluated at the close of day t from bars and indicators at bars <= t (split-adjusted
prices). Forward returns and the maximum adverse excursion (MAE: the worst move against the
signal's bias) are, by design, the only forward-looking quantities; they use dividend-adjusted
prices, falling back to split-adjusted ones where the adjusted series is missing. The library is documented rule by rule in references/signals.md; do
not add or tune rules per ticker.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C

P = C.SIGNAL_PARAMS
SMA_LONG = C.SMA_PERIODS[2]
_SLOPE = P["pullback50_slope_bars"]

SIGNALS = [
    {"id": "uptrend_pullback", "family": 1, "name": "Uptrend pullback", "bias": +1,
     "regime": "above_200", "pillar": "mean_reversion",
     "rule": f"close > sma200 and rsi2 < {P['uptrend_pullback_rsi2']:g}"},
    {"id": "downtrend_oversold", "family": 2, "name": "Downtrend oversold", "bias": +1,
     "regime": "below_200", "pillar": "mean_reversion",
     "rule": f"close < sma200 and rsi14 < {P['downtrend_oversold_rsi14']:g}"},
    {"id": "breakout_55d", "family": 3, "name": "55-day closing-high breakout on volume", "bias": +1,
     "regime": "current_side", "pillar": "momentum",
     "rule": f"close > hh55_prev (highest close of the previous {P['breakout_lookback']} bars) "
             f"and rvol50 >= {P['breakout_rvol']:g}"},
    {"id": "new_52w_high", "family": 4, "name": "New 52-week closing high", "bias": +1,
     "regime": "above_200", "pillar": "momentum",
     "rule": f"close > hh252_prev (highest close of the previous {C.HIGH_52W_BARS} bars) "
             f"and sma200 is defined"},
    {"id": "squeeze_release_up", "family": 5, "name": "Squeeze release up", "bias": +1,
     "regime": "current_side", "pillar": "volatility_setup",
     "rule": f"squeeze_on[t-1] and not squeeze_on[t] and squeeze_bars[t-1] >= {P['squeeze_min_bars']} "
             f"and close > sma20"},
    {"id": "squeeze_release_down", "family": 5, "name": "Squeeze release down", "bias": -1,
     "regime": "current_side", "pillar": "volatility_setup",
     "rule": f"squeeze_on[t-1] and not squeeze_on[t] and squeeze_bars[t-1] >= {P['squeeze_min_bars']} "
             f"and close <= sma20"},
    {"id": "golden_cross", "family": 6, "name": "Golden cross (50/200 SMA)", "bias": +1,
     "regime": "current_side", "pillar": "trend",
     "rule": "sma50[t-1] <= sma200[t-1] and sma50[t] > sma200[t]"},
    {"id": "death_cross", "family": 6, "name": "Death cross (50/200 SMA)", "bias": -1,
     "regime": "current_side", "pillar": "trend",
     "rule": "sma50[t-1] >= sma200[t-1] and sma50[t] < sma200[t]"},
    {"id": "pullback_rising_50", "family": 7, "name": "Pullback to a rising 50 SMA", "bias": +1,
     "regime": "current_side", "pillar": "trend",
     "rule": f"sma50 > sma200 and sma50 > sma50[t-{_SLOPE}] and "
             f"abs(close - sma50) <= {P['pullback50_atr']:g} x atr14"},
    {"id": "extension_2sigma", "family": 8, "name": "Extension > 2 sigma above the 20-day mean", "bias": -1,
     "regime": "current_side", "pillar": "mean_reversion",
     "rule": f"z20 > {P['extension_z']:g}"},
    {"id": "gap_up_atr", "family": 9, "name": "Gap up > 1 ATR on volume", "bias": +1,
     "regime": "current_side", "pillar": "volume_flow",
     "rule": f"open - close[t-1] > {P['gap_atr']:g} x atr14[t-1] and rvol50 >= {P['gap_rvol']:g}"},
    {"id": "gap_down_atr", "family": 9, "name": "Gap down > 1 ATR on volume", "bias": -1,
     "regime": "current_side", "pillar": "volume_flow",
     "rule": f"close[t-1] - open > {P['gap_atr']:g} x atr14[t-1] and rvol50 >= {P['gap_rvol']:g}"},
    {"id": "macd_bull_cross_below_zero", "family": 10, "name": "MACD bullish cross below zero", "bias": +1,
     "regime": "current_side", "pillar": "momentum",
     "rule": f"macd[t-1] <= macd_signal[t-1] and macd[t] > macd_signal[t] and macd[t] < 0 "
             f"(bar index >= {C.SIGNAL_MACD_WARMUP_BARS})"},
    {"id": "macd_bear_cross_above_zero", "family": 10, "name": "MACD bearish cross above zero", "bias": -1,
     "regime": "current_side", "pillar": "momentum",
     "rule": f"macd[t-1] >= macd_signal[t-1] and macd[t] < macd_signal[t] and macd[t] > 0 "
             f"(bar index >= {C.SIGNAL_MACD_WARMUP_BARS})"},
]
SIGNAL_IDS = [s["id"] for s in SIGNALS]
_BY_ID = {s["id"]: s for s in SIGNALS}

DEFINITIONS = {
    "forward_return": "adj_close[t+h] / adj_close[t] - 1 (close where the adjusted pair is missing)",
    "mae": "worst move against the signal's bias: bullish signals min(adj_low[t+1..t+h]) / adj_close[t] - 1 "
           "(positive if no low trades below the entry close), bearish signals max(adj_high[t+1..t+h]) / "
           "adj_close[t] - 1 (negative if no high trades above it); low/high/close where adjusted data is "
           "missing; baselines use the same side as the signal",
    "hit_rate": "share of forward returns > 0, for every signal (bearish signals included)",
    "edge": "signal minus same-regime baseline (median and hit rate); vs_unconditional_median = "
            "signal median minus unconditional median",
    "de_clustering": "after a kept event at t, events at t+1..t+h are ignored (per horizon)",
    "baselines": "every day with a valid t+h outcome, overlapping windows, no de-clustering",
}


def _iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _float(x) -> float | None:
    x = float(x)
    return x if np.isfinite(x) else None


# ---------------------------------------------------------------------------- events
def events(bars: pd.DataFrame, ind: pd.DataFrame) -> pd.DataFrame:
    """One bool column per signal id (SIGNALS order) on bars.index; True when the rule holds at the
    close of that bar. Causal: uses bars/ind at <= t only. Comparisons with NaN are False."""
    close, open_ = bars["close"], bars["open"]
    sma20, sma50, sma200 = ind["sma20"], ind["sma50"], ind["sma200"]
    atr, rvol = ind["atr14"], ind["rvol50"]
    prev_close = close.shift(1)

    sq_on = ind["squeeze_on"].eq(True)
    released = (sq_on.shift(1, fill_value=False) & ~sq_on
                & (ind["squeeze_bars"].shift(1) >= P["squeeze_min_bars"]))

    macd, sig = ind["macd"], ind["macd_signal"]
    warm = pd.Series(np.arange(len(bars)) >= C.SIGNAL_MACD_WARMUP_BARS, index=bars.index)
    macd_up = (macd.shift(1) <= sig.shift(1)) & (macd > sig)
    macd_dn = (macd.shift(1) >= sig.shift(1)) & (macd < sig)

    out = {
        "uptrend_pullback": (close > sma200) & (ind["rsi2"] < P["uptrend_pullback_rsi2"]),
        "downtrend_oversold": (close < sma200) & (ind["rsi14"] < P["downtrend_oversold_rsi14"]),
        "breakout_55d": (close > ind["hh55_prev"]) & (rvol >= P["breakout_rvol"]),
        "new_52w_high": (close > ind["hh252_prev"]) & sma200.notna(),
        "squeeze_release_up": released & (close > sma20),
        "squeeze_release_down": released & (close <= sma20),
        "golden_cross": (sma50.shift(1) <= sma200.shift(1)) & (sma50 > sma200),
        "death_cross": (sma50.shift(1) >= sma200.shift(1)) & (sma50 < sma200),
        "pullback_rising_50": ((sma50 > sma200) & (sma50 > sma50.shift(_SLOPE))
                               & ((close - sma50).abs() <= P["pullback50_atr"] * atr)),
        "extension_2sigma": ind["z20"] > P["extension_z"],
        "gap_up_atr": ((open_ - prev_close) > P["gap_atr"] * atr.shift(1)) & (rvol >= P["gap_rvol"]),
        "gap_down_atr": ((prev_close - open_) > P["gap_atr"] * atr.shift(1)) & (rvol >= P["gap_rvol"]),
        "macd_bull_cross_below_zero": macd_up & (macd < 0) & warm,
        "macd_bear_cross_above_zero": macd_dn & (macd > 0) & warm,
    }
    return pd.DataFrame({k: out[k].astype(bool) for k in SIGNAL_IDS}, index=bars.index)


def regime_mask(bars: pd.DataFrame, ind: pd.DataFrame, regime: str | None) -> pd.Series | None:
    """Days on the named side of the SMA200 ("above_200": close > sma200, "below_200": close < sma200);
    None (no filter) for anything else."""
    if regime == "above_200":
        return bars["close"] > ind["sma200"]
    if regime == "below_200":
        return bars["close"] < ind["sma200"]
    return None


def side_at(bars: pd.DataFrame, ind: pd.DataFrame, pos: int) -> str | None:
    """Side of the SMA200 at bar position pos: "above_200", "below_200", or None if undefined/equal."""
    c, s = float(bars["close"].iloc[pos]), float(ind["sma200"].iloc[pos])
    if not np.isfinite(s) or c == s:
        return None
    return "above_200" if c > s else "below_200"


# ---------------------------------------------------------------------------- outcomes
def _outcome_pair(close: pd.Series, low: pd.Series, high: pd.Series, h: int,
                  bias: int) -> tuple[pd.Series, pd.Series]:
    fwd = close.shift(-h) / close - 1.0
    # rolling(h) at t+h covers t+1..t+h
    worst = high.rolling(h, min_periods=h).max() if bias < 0 else low.rolling(h, min_periods=h).min()
    return fwd, worst.shift(-h) / close - 1.0


def forward_outcomes(bars: pd.DataFrame, h: int, bias: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """Forward return and MAE for every bar (NaN where t+h is outside the data).

    MAE is the worst move against `bias`: the lowest low after entry for bullish (and neutral)
    signals, the highest high for bearish ones. Each measure uses dividend-adjusted prices where
    they are complete for that window and split-adjusted prices otherwise, so both legs of a
    ratio always share one price basis.
    """
    fwd, mae = _outcome_pair(bars["close"], bars["low"], bars["high"], h, bias)
    if "adj_close" in bars.columns:
        missing = pd.Series(np.nan, index=bars.index)
        a_fwd, a_mae = _outcome_pair(bars["adj_close"], bars.get("adj_low", missing),
                                     bars.get("adj_high", missing), h, bias)
        fwd = a_fwd.where(a_fwd.notna(), fwd)
        mae = a_mae.where(a_mae.notna(), mae)
    return fwd.to_numpy(dtype=float, na_value=np.nan), mae.to_numpy(dtype=float, na_value=np.nan)


def decluster(pos: np.ndarray, h: int) -> np.ndarray:
    """Greedy de-clustering of ascending event positions: after a kept event at t, drop t+1..t+h."""
    kept, next_free = [], -1
    for p in np.asarray(pos, dtype=int).tolist():
        if p >= next_free:
            kept.append(p)
            next_free = p + h + 1
    return np.asarray(kept, dtype=int)


def confidence(n: int) -> str:
    """Sample-size label: normal / low / insufficient (insufficient is excluded from scoring)."""
    if n >= C.CONFIDENCE_N_NORMAL:
        return "normal"
    if n >= C.CONFIDENCE_N_LOW:
        return "low"
    return "insufficient"


def summarize(fwd: np.ndarray, mae: np.ndarray) -> dict:
    """n, hit rate (share > 0), median, mean, p25/p75 (linear interpolation) and median MAE."""
    fwd = np.asarray(fwd, dtype=float)
    mae = np.asarray(mae, dtype=float)
    mae = mae[~np.isnan(mae)]
    n = int(fwd.size)
    if n == 0:
        return {"n": 0, "hit_rate": None, "median": None, "mean": None, "p25": None, "p75": None,
                "median_mae": None}
    p25, p50, p75 = np.percentile(fwd, [25, 50, 75])
    return {
        "n": n,
        "hit_rate": _float((fwd > 0).mean()),
        "median": _float(p50),
        "mean": _float(fwd.mean()),
        "p25": _float(p25),
        "p75": _float(p75),
        "median_mae": _float(np.median(mae)) if mae.size else None,
    }


def _bool_array(mask, index) -> np.ndarray:
    return pd.Series(mask, index=index).eq(True).to_numpy()


# ---------------------------------------------------------------------------- statistics
def stats(bars: pd.DataFrame, ind: pd.DataFrame, signal_id: str, horizon: int, regime_mask=None,
          ev: pd.DataFrame | None = None, outcomes: tuple | None = None) -> dict:
    """De-clustered forward-return statistics of one signal at one horizon.

    regime_mask (bool Series/array on bars.index) restricts the events; ev / outcomes accept
    precomputed events(bars, ind) and forward_outcomes(bars, horizon, signal bias).
    """
    if ev is None:
        ev = events(bars, ind)
    fwd, mae = outcomes if outcomes is not None else forward_outcomes(bars, horizon, _BY_ID[signal_id]["bias"])
    fired = ev[signal_id].to_numpy(dtype=bool)
    if regime_mask is not None:
        fired = fired & _bool_array(regime_mask, bars.index)
    kept = decluster(np.flatnonzero(fired & ~np.isnan(fwd)), horizon)
    out = summarize(fwd[kept], mae[kept])
    out["confidence"] = confidence(out["n"])
    out["dates_sample"] = [_iso(d) for d in bars.index[kept[-C.SIGNAL_DATES_SAMPLE:]]]
    return out


def baseline(bars: pd.DataFrame, horizon: int, mask=None, outcomes: tuple | None = None, bias: int = 1) -> dict:
    """Baseline over every day with a valid t+h outcome (optionally only mask days), MAE on the
    side of `bias`. Overlapping windows, no de-clustering."""
    fwd, mae = outcomes if outcomes is not None else forward_outcomes(bars, horizon, bias)
    ok = ~np.isnan(fwd)
    if mask is not None:
        ok &= _bool_array(mask, bars.index)
    return summarize(fwd[ok], mae[ok])


def edge(sig: dict, same: dict, uncond: dict) -> dict:
    """Signal minus baselines; None where either side has no observations."""
    def diff(a, b):
        return None if a is None or b is None else a - b
    return {
        "median": diff(sig["median"], same["median"]),
        "hit_rate": diff(sig["hit_rate"], same["hit_rate"]),
        "vs_unconditional_median": diff(sig["median"], uncond["median"]),
    }


def last_fires(ev: pd.DataFrame) -> dict[str, int | None]:
    """Position of each signal's most recent fire (None if it never fired)."""
    out = {}
    for sid in ev.columns:
        hits = np.flatnonzero(ev[sid].to_numpy(dtype=bool))
        out[sid] = int(hits[-1]) if hits.size else None
    return out


def active_ids(ev: pd.DataFrame, window: int = C.SIGNAL_ACTIVE_BARS) -> list[str]:
    """Signals that fired on any of the last `window` bars, in library order."""
    start = len(ev) - window
    return [sid for sid, pos in last_fires(ev).items() if pos is not None and pos >= start]


# ---------------------------------------------------------------------------- shared by other libraries
class OutcomeCache:
    """Forward outcomes and baselines per (horizon, side[, regime]), computed once per bars/ind.

    Shared by this module and the price-action and candlestick libraries, which measure their
    events the same way."""

    def __init__(self, bars: pd.DataFrame, ind: pd.DataFrame):
        self.bars, self.ind = bars, ind
        self._out: dict[tuple, tuple] = {}
        self._base: dict[tuple, dict] = {}

    def outcomes(self, h: int, side: int) -> tuple:
        if (h, side) not in self._out:
            self._out[(h, side)] = forward_outcomes(self.bars, h, side)
        return self._out[(h, side)]

    def baseline(self, h: int, regime: str | None, side: int) -> dict:
        if (h, regime, side) not in self._base:
            self._base[(h, regime, side)] = baseline(self.bars, h, regime_mask(self.bars, self.ind, regime),
                                                     self.outcomes(h, side))
        return self._base[(h, regime, side)]


def active_rows(bars: pd.DataFrame, ind: pd.DataFrame, ev: pd.DataFrame, specs: dict, horizons: list[int],
                primary: int, notes: list, cache: OutcomeCache | None = None) -> list[dict]:
    """Per-horizon statistics with baselines for every event column of `ev` that fired on the last
    C.SIGNAL_ACTIVE_BARS bars. `specs` maps each id to {name, bias, regime, pillar, rule, family};
    a "current_side" regime takes the SMA200 side on the fire date."""
    cache = cache or OutcomeCache(bars, ind)
    fires, n_bars, out = last_fires(ev), len(bars), []
    for sid in active_ids(ev):
        spec, pos = specs[sid], fires[sid]
        regime = spec["regime"] if spec["regime"] != "current_side" else side_at(bars, ind, pos)
        if regime is None:
            notes.append(f"{sid}: SMA200 side undefined on the fire date; stats use all days.")
        mask = regime_mask(bars, ind, regime)
        side = -1 if spec["bias"] < 0 else 1
        per_h = {}
        for h in horizons:
            sig = stats(bars, ind, sid, h, regime_mask=mask, ev=ev, outcomes=cache.outcomes(h, side))
            uncond, same = cache.baseline(h, None, side), cache.baseline(h, regime, side)
            per_h[h] = {**sig, "unconditional": uncond, "same_regime": same, "edge": edge(sig, same, uncond)}
        conf = per_h[primary]["confidence"]
        out.append({
            "id": sid, "family": spec.get("family"), "name": spec["name"], "bias": spec["bias"],
            "pillar": spec.get("pillar"), "rule": spec["rule"],
            "fired_date": _iso(bars.index[pos]), "bars_ago": n_bars - 1 - pos,
            "regime_used": regime or "all_days", "horizons": per_h,
            "mae_basis": "highest high after entry" if side < 0 else "lowest low after entry",
            "confidence": conf, "scorable": conf != "insufficient",
        })
    return out


def library_rows(bars: pd.DataFrame, ind: pd.DataFrame, ev: pd.DataFrame, specs: list[dict], h: int,
                 cache: OutcomeCache | None = None) -> list[dict]:
    """Every rule of a library at horizon h over all days: last fire, fire count, de-clustered n,
    hit rate and median, and the edge against the unconditional baseline on the same side."""
    cache = cache or OutcomeCache(bars, ind)
    fires, out = last_fires(ev), []
    for spec in specs:
        sid, side = spec["id"], -1 if spec["bias"] < 0 else 1
        sig = stats(bars, ind, sid, h, ev=ev, outcomes=cache.outcomes(h, side))
        uncond = cache.baseline(h, None, side)
        diff = edge(sig, uncond, uncond)
        out.append({
            "id": sid, "name": spec["name"], "bias": spec["bias"],
            "last_fired": _iso(bars.index[fires[sid]]) if fires[sid] is not None else None,
            "fires_total": int(ev[sid].sum()), "horizon": h,
            "n": sig["n"], "hit_rate": sig["hit_rate"], "median": sig["median"], "confidence": sig["confidence"],
            "base_median": uncond["median"], "base_hit_rate": uncond["hit_rate"],
            "edge_median": diff["median"], "edge_hit_rate": diff["hit_rate"],
        })
    return out


# ---------------------------------------------------------------------------- module builder
def _caveats(ticker: str, history: dict) -> list[str]:
    return [
        f"Single stock: statistics come from {ticker}'s own history only, with no cross-sectional confirmation.",
        "In-sample: the rules are pre-registered (references/signals.md) but measured on the same history "
        "they describe; there is no out-of-sample test.",
        "No transaction costs, slippage or financing; entry at the signal-day close is assumed.",
        "Signal events are de-clustered per horizon; the baselines use every eligible day with overlapping "
        "forward windows, so baseline samples are serially correlated and much larger than the signal samples.",
        f"History depth is limited by the data plan: {history['bars']} bars from {history['first_date']} "
        f"to {history['last_date']}.",
        "Signals are evaluated on split-adjusted prices; forward returns and MAE are measured on "
        "dividend-adjusted closes (split-adjusted where the adjusted series is missing).",
        "MAE is the worst move against the signal's bias: the lowest low after entry for bullish signals, "
        "the highest high for bearish ones (a rise is adverse to a bearish read).",
        "Hit rate is the share of positive forward returns for every signal: a bearish signal supports "
        "its hypothesis when its median and hit rate sit below the baseline.",
        "Only events with t+h inside the data count, so fires in the last h bars (including a current "
        "one) are not in their own sample.",
    ]


def build_signal_stats(ctx) -> dict:
    """Module output: active signals with per-horizon stats and baselines, the full library with
    last fire dates, caveats and the history span used."""
    bars, ind = ctx.get("bars"), ctx.get("ind")
    hz = ctx["hz"]
    horizons = [int(h) for h in hz["stat_horizons"]]
    primary = int(hz["primary"])
    if primary not in horizons:
        horizons.append(primary)
    base = {"primary_horizon": primary, "stat_horizons": horizons, "definitions": DEFINITIONS}
    if bars is None or ind is None or len(bars) == 0:
        return {**base, "available": False, "reason": "no price history", "notes": [], "active": [],
                "inactive_ids": list(SIGNAL_IDS), "library": [], "caveats": [], "history": None}

    notes: list[str] = []
    n_bars = len(bars)
    has_adj = "adj_close" in bars.columns
    history = {"first_date": _iso(bars.index[0]), "last_date": _iso(bars.index[-1]), "bars": n_bars,
               "adj_coverage": float(bars["adj_close"].notna().mean()) if has_adj else 0.0}
    if ind["sma200"].notna().sum() == 0:
        notes.append(f"Fewer than {SMA_LONG} bars: rules and regime filters that need the SMA200 cannot be "
                     f"evaluated.")
    if history["adj_coverage"] < 1.0:
        notes.append("Dividend-adjusted prices are missing on part of the history; those windows use "
                     "split-adjusted prices (price return only).")

    ev = events(bars, ind)
    fires = last_fires(ev)
    library = [{"id": s["id"], "name": s["name"],
                "last_fired": _iso(bars.index[fires[s["id"]]]) if fires[s["id"]] is not None else None,
                "fires_total": int(ev[s["id"]].sum())} for s in SIGNALS]
    act = active_ids(ev)
    active = active_rows(bars, ind, ev, _BY_ID, horizons, primary, notes)

    return {
        **base, "available": True, "notes": notes,
        "active": active, "inactive_ids": [s for s in SIGNAL_IDS if s not in act],
        "library": library, "caveats": _caveats(ctx.get("ticker", "this stock"), history),
        "history": history,
    }
