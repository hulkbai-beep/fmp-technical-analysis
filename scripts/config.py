"""
Every parameter, threshold and weight the technical-analysis skill uses.

Change values here, never inline in the other scripts. references/methodology.md explains
the reasoning behind the defaults.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------- data
HISTORY_START = "1990-01-01"      # earliest date requested for the analysed symbol
EOD_ROW_CAP = 5000                # FMP returns at most this many rows per EOD request (newest first)
CHUNK_YEARS = 18                  # calendar years per EOD request (~4,540 sessions, below the cap)
MAX_CHUNKS = 3                    # safety stop for the chunked history fetch
BENCHMARK_YEARS = 5               # history requested for SPY, the sector ETF and ^VIX (one call each)
CACHE_SUBDIR = "fmp-technical"    # ~/.cache/<this> unless FMP_CACHE_DIR is set

MARKET_TZ = "America/New_York"
MARKET_CLOSE_BUFFER = "16:15"     # before this NY time, a bar dated today is treated as partial
STALE_BDAYS = 2                   # warn if the last bar is more than this many business days behind
GAP_CALENDAR_DAYS = 4             # consecutive bars further apart than this are flagged as a data gap

ILLIQUID_MIN_AVG_SHARES = 200_000       # average daily shares below this -> illiquid warning
ILLIQUID_MIN_AVG_DOLLARS = 5_000_000    # average daily dollar volume below this -> illiquid warning

SPY = "SPY"
VIX = "^VIX"                      # the caret matters: "VIX" is a different instrument on FMP
SECTOR_ETF = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}

# ---------------------------------------------------------------------------- horizons
HORIZONS = {
    "swing": {
        "stat_horizons": [5, 10, 20],   # forward-return horizons (trading days) for signal stats
        "primary": 10,                  # horizon used for scoring and scenarios
        "levels_lookback": 126,         # bars (~6 months) for pivots, volume profile, gaps
        "chart_bars": 252,              # bars shown in the chart (~12 months)
        "chart_timeframe": "daily",
        "weekly_emphasis": False,
    },
    "position": {
        "stat_horizons": [20, 60],
        "primary": 60,
        "levels_lookback": 504,         # ~2 years
        "chart_bars": 504,
        "chart_timeframe": "weekly",
        "weekly_emphasis": True,
    },
}
DEFAULT_HORIZON = "swing"
TRADING_DAYS_PER_YEAR = 252

# ---------------------------------------------------------------------------- indicators
SMA_PERIODS = (20, 50, 200)
SLOPE_BARS = 20                   # slope = SMA today / SMA SLOPE_BARS bars ago - 1
RSI_PERIOD = 14
RSI_SHORT_PERIOD = 2
ATR_PERIOD = 14
ADX_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BB_PERIOD, BB_STD = 20, 2.0       # population standard deviation (ddof=0)
KC_PERIOD, KC_ATR_PERIOD, KC_MULT = 20, 20, 1.5   # Keltner: EMA(20) +/- 1.5 x ATR(20)
ZSCORE_PERIOD = 20
RVOL_PERIOD = 50                  # volume / mean volume of the previous 50 sessions
REALIZED_VOL_PERIOD = 20          # stdev of daily log returns x sqrt(252)
PERCENTILE_WINDOW = 252           # trailing window for "percentile vs. the trailing year"
UPDOWN_VOLUME_PERIOD = 50
DIVERGENCE_LOOKBACK = 20          # bars compared for OBV / A-D divergence checks

# ---------------------------------------------------------------------------- momentum / RS
MOMENTUM_LONG = 252               # 12-1 momentum: close[t-21] / close[t-252] - 1
MOMENTUM_SKIP = 21
RETURN_WINDOWS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
RS_WINDOWS = {"3m": 63, "6m": 126}
RS_EXTREME_LOOKBACK = 252         # RS line "new high / low" vs. this many bars
BETA_WINDOW = 252
HIGH_52W_BARS = 252

# ---------------------------------------------------------------------------- regime
ADX_TREND = 25.0                  # ADX above this -> trending
ADX_RANGE = 20.0                  # ADX below this -> range; in between -> transitional
VOL_LOW_PCTL = 20.0               # realized vol / ATR% percentile below this -> low vol
VOL_HIGH_PCTL = 80.0              # above this -> high vol
VOL_EXTREME_PCTL = 95.0           # above this -> volatility extreme (regime type "vol_extreme")
COMPRESSION_BW_PCTL = 10.0        # Bollinger bandwidth percentile below this -> "compression"

# ---------------------------------------------------------------------------- levels
FRACTAL_K = 2                     # pivot high: highest high of the k bars on each side
PROFILE_BINS = 60                 # volume-profile price bins over the lookback range
VALUE_AREA_SHARE = 0.70
HVN_FACTOR = 1.5                  # bin volume > 1.5 x mean bin volume and a local peak -> HVN
LVN_FACTOR = 0.5                  # bin volume < 0.5 x mean bin volume and a local trough -> LVN
LEVEL_MERGE_ATR = 0.5             # merge level candidates closer than this many ATRs
TOUCH_ATR = 0.25                  # a bar "touches" a level if its high-low range comes this close
LEVELS_PER_SIDE = 3
LARGEST_GAP_LOOKBACK = 126        # bars searched for the largest gap (anchored VWAP anchor)

# ---------------------------------------------------------------------------- multi-timeframe
WEEKLY_RULE = "W-FRI"
WEEKLY_SMA_FAST, WEEKLY_SMA_SLOW = 10, 40
WEEKLY_RSI_PERIOD = 14

# ---------------------------------------------------------------------------- event risk
EARNINGS_REACTIONS_MAX = 12
EARNINGS_REACTIONS_MIN = 8

# ---------------------------------------------------------------------------- signal statistics
SIGNAL_ACTIVE_BARS = 3            # "active" = fired on the last bar or within the 2 bars before it
SIGNAL_PARAMS = {
    "uptrend_pullback_rsi2": 10.0,
    "downtrend_oversold_rsi14": 30.0,
    "breakout_lookback": 55,
    "breakout_rvol": 1.5,
    "squeeze_min_bars": 5,
    "pullback50_atr": 1.0,
    "pullback50_slope_bars": 20,
    "extension_z": 2.0,
    "gap_atr": 1.0,
    "gap_rvol": 2.0,
}
CONFIDENCE_N_NORMAL = 30          # n >= 30 -> "normal"
CONFIDENCE_N_LOW = 15             # 15..29 -> "low"; below 15 -> "insufficient" (not scored)
SIGNAL_EDGE_MIN = 0.005           # median edge vs. same-regime baseline needed to nudge a pillar (0.5%)
SIGNAL_HIT_EDGE_MIN = 0.05        # and hit-rate edge (5 percentage points)

# ---------------------------------------------------------------------------- scorecard
PILLARS = ("trend", "momentum", "relative_strength", "volume_flow", "volatility_setup", "mean_reversion")
REGIME_WEIGHTS = {
    "trend":        {"trend": 0.25, "momentum": 0.25, "relative_strength": 0.20,
                     "volume_flow": 0.15, "volatility_setup": 0.10, "mean_reversion": 0.05},
    "range":        {"trend": 0.10, "momentum": 0.10, "relative_strength": 0.10,
                     "volume_flow": 0.20, "volatility_setup": 0.20, "mean_reversion": 0.30},
    "transitional": {"trend": 0.20, "momentum": 0.20, "relative_strength": 0.15,
                     "volume_flow": 0.15, "volatility_setup": 0.15, "mean_reversion": 0.15},
    "vol_extreme":  {"trend": 0.15, "momentum": 0.10, "relative_strength": 0.10,
                     "volume_flow": 0.20, "volatility_setup": 0.25, "mean_reversion": 0.20},
}
# position mode: weekly emphasis shifts weight from mean reversion to trend
POSITION_WEIGHT_SHIFT = {"mean_reversion": -0.05, "trend": +0.05}
BIAS_STEPS = [            # (upper bound of weighted score, label), checked in order
    (-1.2, "Bearish"),
    (-0.4, "Moderately bearish"),
    (0.4, "Neutral"),
    (1.2, "Moderately bullish"),
    (float("inf"), "Bullish"),
]

# ---------------------------------------------------------------------------- scenarios / risk
TARGET_ZONE_ATR = 0.25            # target zones = level +/- this many ATRs
TRIGGER_RVOL = 1.5                # "on above-average volume" in scenario triggers
STOP_ATR_MULT = 2.0               # volatility stop distance
STRUCTURE_STOP_BUFFER_ATR = 0.5   # structural stop = nearest support - this many ATRs
ACCOUNT_SIZE = 100_000
RISK_PER_TRADE = 0.01

DISCLAIMER = "This is technical analysis for information only, not investment advice."
