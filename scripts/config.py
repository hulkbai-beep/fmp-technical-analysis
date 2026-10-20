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
CHUNK_FIT_SHARE = 0.97            # take the whole remaining span in one request when its estimated sessions
                                  # fit within this share of EOD_ROW_CAP (AAPL from 1990: 2 requests, not 3)
HISTORY_EXHAUSTED_DAYS = 10       # short chunk starting > this many days after its `from` -> no older history
BENCHMARK_YEARS = 5               # history requested for SPY, the sector ETF and ^VIX (one call each)
CACHE_SUBDIR = "fmp-technical"    # ~/.cache/<this> unless FMP_CACHE_DIR is set

MARKET_TZ = "America/New_York"
MARKET_CLOSE_BUFFER = "16:15"     # before this NY time, a bar dated today is treated as partial
STALE_BDAYS = 2                   # warn if the last bar is more than this many business days behind
GAP_CALENDAR_DAYS = 4             # consecutive bars further apart than this are flagged as a data gap

SPAC_CHECK_BARS = 126               # first bars checked for a pre-merger SPAC shell (~6 months)
SPAC_MIN_BARS = 63                  # fewer bars than this: no check
SPAC_MEDIAN_BAND = (9.5, 11.5)      # a SPAC shell trades near its $10 trust value ...
SPAC_PRICE_BAND = (9.0, 12.0)       # ... with most closes inside this band
SPAC_MIN_SHARE_IN_BAND = 0.6        # (a deal-announcement spike may push some closes outside it)

ILLIQUID_MIN_AVG_SHARES = 200_000       # average daily shares below this -> illiquid warning
ILLIQUID_MIN_AVG_DOLLARS = 5_000_000    # average daily dollar volume below this -> illiquid warning
LIQUIDITY_BARS = 50                     # bars averaged for the illiquidity check

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
MOMENTUM_LONG = 252               # 12-1 momentum: P[t-21] / P[t-252] - 1 on the total-return index
MOMENTUM_SKIP = 21
RETURN_WINDOWS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
RS_WINDOWS = {"3m": 63, "6m": 126}
RS_EXTREME_LOOKBACK = 252         # RS line "new high / low" vs. this many bars
BETA_WINDOW = 252
HIGH_52W_BARS = 252

# ---------------------------------------------------------------------------- regime
ADX_TREND = 25.0                  # ADX at or above this -> trending
ADX_RANGE = 20.0                  # ADX below this -> range; in between -> transitional
VOL_LOW_PCTL = 20.0               # realized vol / ATR% percentile below this -> low vol
VOL_HIGH_PCTL = 80.0              # above this -> high vol
VOL_EXTREME_PCTL = 95.0           # above this -> volatility extreme (regime type "vol_extreme")
COMPRESSION_BW_PCTL = 10.0        # Bollinger bandwidth percentile below this -> "compression"

# ---------------------------------------------------------------------------- mean-reversion states
# shared by modules.py (state labels) and scorecard.py (mean-reversion pillar); strict inequalities
MR_RSI14_OVERBOUGHT = 70.0        # RSI(14) above this -> "overbought"
MR_RSI14_OVERSOLD = 30.0          # RSI(14) below this -> "oversold"
MR_RSI2_OVERBOUGHT = 90.0         # RSI(2) above this -> "overbought"
MR_RSI2_OVERSOLD = 10.0           # RSI(2) below this -> "oversold"
MR_Z_EXTENDED = 2.0               # z20 beyond +-this -> stretched (= %B outside 0..1 with 2-sigma bands)
MR_DIST_SMA50_ATR = 2.0           # |close - SMA50| / ATR beyond this -> stretched (scorecard)

# ---------------------------------------------------------------------------- levels
FRACTAL_K = 2                     # pivot high: high strictly above the highs of the k bars on each side
PROFILE_BINS = 60                 # volume-profile price bins over the lookback range
VALUE_AREA_SHARE = 0.70
HVN_FACTOR = 1.5                  # bin volume > 1.5 x mean bin volume and a local peak -> HVN
LVN_FACTOR = 0.5                  # bin volume < 0.5 x mean bin volume and a local trough -> LVN
LEVEL_MERGE_ATR = 0.5             # merge level candidates closer than this many ATRs
TOUCH_ATR = 0.25                  # a bar "touches" a level if its high or low is within this many ATRs of the zone
LEVELS_PER_SIDE = 3
LEVEL_SOURCE_BONUS = 0.5          # level score bonus per extra distinct source type in a merged cluster
LEVEL_MIN_SCORE = 0.25            # a cluster scoring below this is too weak to be S1-S3 / R1-R3 (52-week extremes exempt)
LEVEL_AVWAP_MIN_BARS = 10         # an anchored VWAP is a level candidate only once its anchor is this many bars old
GAP_ZONE_MAX_ATR = 1.0            # an unfilled gap at most this many ATRs wide is one level zone; wider: two edges
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
SIGNAL_MACD_WARMUP_BARS = MACD_SLOW + MACD_SIGNAL   # MACD crosses before this bar index are ignored (EMA warm-up)
SIGNAL_DATES_SAMPLE = 10          # most recent de-clustered event dates listed per statistic

# ---------------------------------------------------------------------------- price action (price_action.py, chart 2)
# Mechanical daily-bar versions of Al Brooks' price-action ideas; references/patterns.md has the rules.
PA_EMA_PERIOD = 20                # Brooks' 20-bar EMA
PA_TREND_BAR_BODY = 0.5           # body >= this share of the bar's range -> trend bar
PA_DOJI_BODY = 0.25               # body <= this share of the range -> doji (anything between: weak bar)
PA_STATE_BARS = 20                # bars used to classify the market state (channel / trading range)
PA_EMA_SLOPE_BARS = 10            # EMA rising = EMA today > EMA this many bars ago
PA_TIGHT_SHARE = 0.8              # share of bars entirely beyond the EMA (lows above / highs below) -> tight channel
PA_CHANNEL_SHARE = 0.6            # share of closes on one side of the EMA -> (broad) channel
PA_RANGE_BARS = 20                # trading-range window
PA_RANGE_MAX_ATR = 5.0            # window high - low at most this many ATRs ...
PA_RANGE_MIN_CROSSES = 3          # ... and closes crossing the EMA at least this many times -> trading range
PA_BREAKOUT_FAIL_BARS = 2         # a range breakout that closes back inside within this many more bars has failed
PA_PULLBACK_EMA_ATR = 0.5         # an H2 / L2 needs the pullback to reach within this many ATRs of the EMA
PA_COUNT_MAX = 4                  # pullback counts beyond H4 / L4 are not labelled
PA_GAP_BARS = 20                  # "20 gap bars": this many bars in a row not touching the EMA
PA_PIVOT_K = 2                    # swing pivots for wedges and measured moves (confirmed k bars later)
PA_WEDGE_MAX_BARS = 60            # three pushes inside this many bars
PA_WEDGE_MIN_SPACING = 3          # bars between consecutive pushes, at least
PA_MM_MAX_AGE = 120               # measured moves only from swings whose first point is at most this many bars old
PA_ALWAYS_IN_BARS = 2             # consecutive strong trend bars that flip the always-in direction

# ---------------------------------------------------------------------------- candlestick patterns (candlesticks.py, chart 3)
CS_TREND_BARS = 5                 # prior trend: close[t-k-1] vs close[t-k-1-this] before a k-bar pattern
CS_LONG_BODY_ATR = 0.6            # long body: body >= this many ATRs (ATR of the bar before the pattern)
CS_MIN_RANGE_ATR = 0.5            # single-bar shapes need a range of at least this many ATRs
CS_SHADOW_BODY = 2.0              # hammer / shooting star: long shadow >= this many bodies ...
CS_SMALL_SHADOW = 0.25            # ... and the other shadow <= this share of the range
CS_DOJI_BODY = 0.1                # doji: body <= this share of the range
CS_STAR_BODY = 0.3                # star: middle body <= this share of the first bar's body
CS_SOLDIER_SHADOW = 0.25          # three soldiers / crows: closing shadow <= this share of the range

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
BIAS_STEPS = [            # (bound, label) in ascending order; a score exactly on a bound takes the label
                          # nearer Neutral, so +-0.4 is Neutral and +-1.2 is Moderately bullish/bearish
    (-1.2, "Bearish"),
    (-0.4, "Moderately bearish"),
    (0.4, "Neutral"),
    (1.2, "Moderately bullish"),
    (float("inf"), "Bullish"),
]
FLOW_UPDOWN_HIGH = 1.2            # 50-day up/down volume ratio above this -> +1 flow vote
FLOW_UPDOWN_LOW = 0.8             # below this -> -1 flow vote
FLOW_AVWAP_MAJORITY = 0.5         # close above (below) more than this share of the anchored VWAPs -> +1 (-1)
SQUEEZE_RELEASE_RECENT_BARS = SIGNAL_ACTIVE_BARS   # a squeeze release with bars_ago < this is "just released"
SIGNAL_NUDGE_MAX = 1              # cap on the net signal nudge per pillar
CONF_AGREEMENT_HIGH = 0.7         # pillar agreement (weight share) needed for "high" confidence
CONF_AGREEMENT_LOW = 0.5          # below this -> "low" confidence

# ---------------------------------------------------------------------------- scenarios / risk
TARGET_ZONE_ATR = 0.25            # target zones = level +/- this many ATRs
TRIGGER_RVOL = 1.5                # "on above-average volume" in scenario triggers
STOP_ATR_MULT = 2.0               # volatility stop distance
STRUCTURE_STOP_BUFFER_ATR = 0.5   # structural stop = nearest support - this many ATRs
ACCOUNT_SIZE = 100_000
RISK_PER_TRADE = 0.01
NO_LEVEL_TRIGGER_ATR = 1.0        # trigger = close +- this many ATRs when no level or 52-week extreme lies beyond it
SCENARIO_PROJECTION_ATR = (1.0, 2.0)   # target projections (ATRs from the trigger) when no level lies beyond it
INVALIDATION_MIN_ATR = 0.5        # invalidation sits at least this many ATRs back through the trigger

# ---------------------------------------------------------------------------- chart (chart.html)
CHART_PRICE_DECIMALS = 4          # decimals kept for prices (candles, SMAs, AVWAPs, levels)
CHART_VALUE_SIG_DIGITS = 6        # significant digits kept for other floats (at least CHART_PRICE_DECIMALS decimals)

# ---------------------------------------------------------------------------- output (analyze.py)
METRICS_SIG_DIGITS = 6            # metrics.json floats are rounded to this many significant digits

# ---------------------------------------------------------------------------- cross-check (crosscheck.py, optional)
CROSSCHECK_WINDOW = 250           # compare the last N dates both series have
CROSSCHECK_RSI_WARMUP = 250       # bars skipped at the start of each RSI series before comparing
CROSSCHECK_SMA_TOL = 0.0001       # SMA(50) tolerance as a share of the close (0.01%)
CROSSCHECK_RSI_TOL = 0.5          # RSI(14) tolerance in points

DISCLAIMER = "This is technical analysis for information only, not investment advice."
