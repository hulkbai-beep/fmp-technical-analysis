// Chinese (default) / English text for the technical-analysis chart (ui/chart.js), chart.html and
// the local app (ui/index.html). Indicator names (SMA, RSI, MACD, ATR, ADX, RVOL, VWAP, POC ...)
// stay in English in both languages. Labels that come from metrics.json (regime label, bias,
// signal ids, level sources) are translated here; free-text reasons and notes stay as written.
(function (global) {
  "use strict";

  const KEY = "ta-lang";

  const STR = {
    zh: {
      app_title: "技术分析", lang_button: "EN", lang_hint: "Switch to English",
      f_symbol: "股票代码", f_horizon: "周期", h_swing: "波段（日线）", h_position: "中线（周线）",
      c_refresh: "忽略缓存", go: "分析",
      loading: sym => `正在分析 ${sym}…（首次约 10-30 秒，之后有缓存会更快）`,
      err_symbol: "请输入有效的股票代码，例如 AAPL、BRK.B、^VIX。",
      notice_proxy: base => `数据来自第三方 FMP 代理（${base}），不是 FMP 官方服务器。重要数字请自行核对。`,
      notice_tls: "本次 HTTPS 证书验证已关闭。",
      as_of: "截至", horizon: "周期", swing: "波段", position: "中线", source: "数据源", etf: "ETF",
      k_price: "收盘价", k_change: "日涨跌", k_atr: "ATR(14)", k_rsi: "RSI(14)", k_rvol: "RVOL",
      k_range52: "52 周区间", k_next_e: "下次财报", k_move: h => `${h} 日 1σ 预期波动`, k_adx: "ADX(14)",
      days_away: (cd, td) => td != null ? `${cd} 天后（约 ${td} 个交易日）` : `${cd} 天后`,
      daily: "日线", weekly: "周线", auto: "自动",
      range_label: "时间范围", r_1m: "1月", r_3m: "3月", r_6m: "6月", r_ytd: "年初至今", r_1y: "1年", r_all: "全部",
      zoom_in: "放大（+）", zoom_out: "缩小（−）", fs_enter: "全屏", fs_exit: "退出全屏",
      auto_weekly: "图表宽度不足以每天一根 K 线，已自动切换为周线",
      weekly_note: "周线模式：指标数值为该周最后一个交易日的日线读数",
      week_of: (a, b) => `${a} ~ ${b} 当周`,
      partial_week: "（本周未结束）",
      o: "开", h: "高", l: "低", c: "收", v: "量",
      lg_sma20: "SMA20", lg_sma50: "SMA50", lg_sma200: "SMA200", lg_supports: "支撑区间", lg_resistances: "压力区间",
      lg_vp: "成交量分布", lg_earnings: "财报",
      anchor_last_earnings: "财报", anchor_high_52w: "52周高点", anchor_low_52w: "52周低点",
      anchor_ytd_start: "年初", anchor_largest_gap_6m: "6个月最大缺口",
      avwap_title: (a, d) => `AVWAP 锚定于${a}（${d}）`,
      p_volume: "成交量", p_rsi: "RSI(14)", p_rs: base => `RS vs SPY（${base} = 100）`, rs_none: "无 RS 线",
      vp_label: "成交量分布", va: "价值区",
      e_past: d => `财报 ${d}`, e_next: d => `下次财报 ${d}`,
      chart_aria: "K 线图：价格、均线、锚定 VWAP、支撑阻力、成交量、RSI 与相对强度",
      approx: "锚定 VWAP 与成交量分布均为日线近似：VWAP = 典型价 (H+L+C)/3 × 日成交量，从锚定日起累计；成交量分布把每根 K 线的成交量平均分配到其最高-最低区间。都不是盘中逐笔数据。",
      vp_window: (n, d) => `成交量分布：最近 ${n} 根 K 线（自 ${d}）`,
      kb_hint: "用鼠标或手指左右拖动可平移，Ctrl+滚轮、触控板或双指捏合可缩放，双击恢复全部；图表获得焦点后 ← → 移动十字线，+ − 缩放",
      // side panels
      t_regime: "市场状态", t_score: "评分卡", t_signals: "信号统计", t_levels: "关键价位", t_notes: "数据说明",
      rg_type: "类型", rg_dir: "方向", rg_strength: "趋势强度", rg_vol: "波动率", rg_weekly: "周线",
      type_trend: "趋势", type_range: "区间", type_transitional: "过渡", type_vol_extreme: "波动极端",
      st_trending: "趋势中", st_transitional: "过渡", st_range: "无趋势",
      vol_low: "低", vol_normal: "正常", vol_high: "高", vol_extreme: "极端",
      vol_pctl: p => `一年分位 ${p}`, compression: "压缩",
      al_confirms: "周线确认日线", al_contradicts: "周线与日线相反", al_mixed: "周线不明确",
      dir_1: "上升", "dir_-1": "下降", dir_0: "不明",
      bias: "整体倾向", wscore: "加权分", confidence: "置信度", weight: "权重",
      conf_high: "高", conf_medium: "中", conf_low: "低",
      conflicts: "相互矛盾的信号",
      pl_trend: "趋势", pl_momentum: "动量", pl_relative_strength: "相对强度", pl_volume_flow: "量能/资金流",
      pl_volatility_setup: "波动结构", pl_mean_reversion: "均值回归",
      sig_h: h => `历史前瞻 ${h} 日收益（本股自身历史）`,
      sig_none: "当前没有活跃信号（最近 3 根 K 线内未触发）。",
      th_signal: "信号", th_fired: "触发", th_n: "n", th_hit: "胜率", th_med: "中位数", th_edge: "超额", sample: "样本",
      bars_ago: n => n === 0 ? "今日" : `${n} 根前`,
      sc_normal: "正常", sc_low: "偏少", sc_insufficient: "不足",
      edge_note: "超额 = 信号中位数 − 同状态基准中位数（括号内为胜率差）；样本不足（n < 15）的信号不计入评分。",
      th_level: "价位", th_dist: "距离", th_src: "来源", touches: n => `触及 ${n} 次`,
      lv_none: "没有可用的关键价位。",
      dn_partial: "未完成 K 线已排除", dn_stale: "数据可能过期", dn_illiquid: "流动性提示", dn_skipped: "已跳过的模块",
      dn_notes: "其他说明", dn_approx: "近似方法",
      stale_txt: (a, b, n) => `最新 K 线 ${a}，比预期交易日 ${b} 晚 ${n} 个交易日`,
      unavailable: "不可用", none: "无", disclaimer: "仅供信息参考的技术分析，不构成投资建议。",
      no_data: "没有可绘制的 K 线数据。",
    },
    en: {
      app_title: "Technical Analysis", lang_button: "中文", lang_hint: "切换到中文",
      f_symbol: "Ticker", f_horizon: "Horizon", h_swing: "Swing (daily)", h_position: "Position (weekly)",
      c_refresh: "Ignore cache", go: "Analyze",
      loading: sym => `Analyzing ${sym}… (first run takes about 10-30 s; faster once cached)`,
      err_symbol: "Enter a valid ticker, e.g. AAPL, BRK.B, ^VIX.",
      notice_proxy: base => `Data comes from a third-party FMP proxy (${base}), not FMP's official server. Verify key figures yourself.`,
      notice_tls: "HTTPS certificate verification was off for this run.",
      as_of: "as of", horizon: "Horizon", swing: "swing", position: "position", source: "Data", etf: "ETF",
      k_price: "Close", k_change: "1-day change", k_atr: "ATR(14)", k_rsi: "RSI(14)", k_rvol: "RVOL",
      k_range52: "52-week range", k_next_e: "Next earnings", k_move: h => `${h}-day 1σ move`, k_adx: "ADX(14)",
      days_away: (cd, td) => td != null ? `in ${cd} days (~${td} sessions)` : `in ${cd} days`,
      daily: "Daily", weekly: "Weekly", auto: "auto",
      range_label: "Range", r_1m: "1M", r_3m: "3M", r_6m: "6M", r_ytd: "YTD", r_1y: "1Y", r_all: "All",
      zoom_in: "Zoom in (+)", zoom_out: "Zoom out (−)", fs_enter: "Full screen", fs_exit: "Exit full screen",
      auto_weekly: "The chart is too narrow for one candle per day, so it switched to weekly candles",
      weekly_note: "Weekly view: indicator values are the daily readings on each week's last session",
      week_of: (a, b) => `week ${a} – ${b}`,
      partial_week: " (week in progress)",
      o: "O", h: "H", l: "L", c: "C", v: "Vol",
      lg_sma20: "SMA20", lg_sma50: "SMA50", lg_sma200: "SMA200", lg_supports: "Support zones", lg_resistances: "Resistance zones",
      lg_vp: "Volume profile", lg_earnings: "Earnings",
      anchor_last_earnings: "earnings", anchor_high_52w: "52w high", anchor_low_52w: "52w low",
      anchor_ytd_start: "YTD", anchor_largest_gap_6m: "6m largest gap",
      avwap_title: (a, d) => `AVWAP anchored at ${a} (${d})`,
      p_volume: "Volume", p_rsi: "RSI(14)", p_rs: base => `RS vs SPY (${base} = 100)`, rs_none: "No RS line",
      vp_label: "Volume profile", va: "Value area",
      e_past: d => `Earnings ${d}`, e_next: d => `Next earnings ${d}`,
      chart_aria: "Candlestick chart: price, moving averages, anchored VWAPs, support and resistance, volume, RSI and relative strength",
      approx: "Anchored VWAPs and the volume profile are daily-bar approximations: VWAP = typical price (H+L+C)/3 × daily volume, cumulated from the anchor bar; the profile spreads each bar's volume evenly across its high-low range. Neither uses intraday data.",
      vp_window: (n, d) => `Volume profile: last ${n} bars (from ${d})`,
      kb_hint: "Drag sideways to pan, Ctrl+wheel or pinch to zoom, double-click to show everything; with the chart focused, ← → move the crosshair and + − zoom",
      t_regime: "Regime", t_score: "Scorecard", t_signals: "Signal statistics", t_levels: "Key levels", t_notes: "Data notes",
      rg_type: "Type", rg_dir: "Direction", rg_strength: "Trend strength", rg_vol: "Volatility", rg_weekly: "Weekly",
      type_trend: "Trend", type_range: "Range", type_transitional: "Transitional", type_vol_extreme: "Vol extreme",
      st_trending: "trending", st_transitional: "transitional", st_range: "range",
      vol_low: "low", vol_normal: "normal", vol_high: "high", vol_extreme: "extreme",
      vol_pctl: p => `1y percentile ${p}`, compression: "compression",
      al_confirms: "weekly confirms daily", al_contradicts: "weekly contradicts daily", al_mixed: "weekly unclear",
      dir_1: "up", "dir_-1": "down", dir_0: "mixed",
      bias: "Bias", wscore: "Weighted score", confidence: "Confidence", weight: "Weight",
      conf_high: "high", conf_medium: "medium", conf_low: "low",
      conflicts: "Conflicting signals",
      pl_trend: "Trend", pl_momentum: "Momentum", pl_relative_strength: "Relative strength", pl_volume_flow: "Volume / flow",
      pl_volatility_setup: "Volatility setup", pl_mean_reversion: "Mean reversion",
      sig_h: h => `Forward ${h}-day returns (this stock's own history)`,
      sig_none: "No active signals (none fired in the last 3 bars).",
      th_signal: "Signal", th_fired: "Fired", th_n: "n", th_hit: "Hit", th_med: "Median", th_edge: "Edge", sample: "sample",
      bars_ago: n => n === 0 ? "today" : n === 1 ? "1 bar ago" : `${n} bars ago`,
      sc_normal: "normal", sc_low: "low", sc_insufficient: "insufficient",
      edge_note: "Edge = signal median − same-regime baseline median (hit-rate difference in brackets); signals with n < 15 are not scored.",
      th_level: "Level", th_dist: "Distance", th_src: "Sources", touches: n => `${n} touch${n === 1 ? "" : "es"}`,
      lv_none: "No key levels available.",
      dn_partial: "Partial bar excluded", dn_stale: "Possibly stale data", dn_illiquid: "Liquidity warning", dn_skipped: "Skipped modules",
      dn_notes: "Other notes", dn_approx: "Approximations",
      stale_txt: (a, b, n) => `last bar ${a} is ${n} sessions behind the expected session ${b}`,
      unavailable: "unavailable", none: "none", disclaimer: "Technical analysis for information only, not investment advice.",
      no_data: "No bars to chart.",
    },
  };

  const SIGNALS_ZH = {
    uptrend_pullback: "上升趋势中的回调（RSI(2) < 10）",
    downtrend_oversold: "下降趋势中的超卖（RSI(14) < 30）",
    breakout_55d: "放量突破 55 日收盘新高",
    new_52w_high: "52 周收盘新高",
    squeeze_release_up: "挤压释放（向上）",
    squeeze_release_down: "挤压释放（向下）",
    golden_cross: "金叉（SMA50 上穿 SMA200）",
    death_cross: "死叉（SMA50 下穿 SMA200）",
    pullback_rising_50: "回踩上行的 SMA50",
    extension_2sigma: "高于 20 日均值 2σ 以上（过度延伸）",
    gap_up_atr: "放量跳空高开 > 1 ATR",
    gap_down_atr: "放量跳空低开 > 1 ATR",
    macd_bull_cross_below_zero: "MACD 零轴下方金叉",
    macd_bear_cross_above_zero: "MACD 零轴上方死叉",
  };

  const BIAS_ZH = { "Bearish": "看空", "Moderately bearish": "偏空", "Neutral": "中性",
    "Moderately bullish": "偏多", "Bullish": "看多" };

  const HEAD_ZH = {
    "Strong uptrend": "强势上升趋势", "Strong downtrend": "强势下降趋势", "Uptrend": "上升趋势",
    "Downtrend": "下降趋势", "Mixed": "方向不明", "Range": "区间震荡", "Undetermined": "无法判断",
    "Transitional (uptrend bias)": "过渡期（偏上升）", "Transitional (downtrend bias)": "过渡期（偏下降）",
    "Transitional (mixed)": "过渡期（方向不明）", "Mixed (trending up)": "方向不明（趋势向上）",
    "Mixed (trending down)": "方向不明（趋势向下）",
  };
  const VOL_ZH = { low: "低波动", normal: "波动正常", high: "高波动", extreme: "波动极端" };

  const SOURCE_ZH = {
    pivot_high: "摆动高点", pivot_low: "摆动低点", poc: "POC", value_area_low: "VAL", value_area_high: "VAH",
    hvn: "HVN", high_52w: "52周高", low_52w: "52周低", sma50: "SMA50", sma200: "SMA200",
    gap_up_top: "向上缺口上沿", gap_up_bottom: "向上缺口下沿", gap_down_top: "向下缺口上沿", gap_down_bottom: "向下缺口下沿",
  };
  const SOURCE_EN = {
    pivot_high: "pivot high", pivot_low: "pivot low", poc: "POC", value_area_low: "VAL", value_area_high: "VAH",
    hvn: "HVN", high_52w: "52w high", low_52w: "52w low", sma50: "SMA50", sma200: "SMA200",
    gap_up_top: "gap-up top", gap_up_bottom: "gap-up bottom", gap_down_top: "gap-down top", gap_down_bottom: "gap-down bottom",
  };

  let lang = "zh";
  try { if (global.localStorage.getItem(KEY) === "en") lang = "en"; } catch (e) { /* storage blocked */ }
  const listeners = [];

  function t(key, ...args) {
    const v = STR[lang][key] ?? STR.en[key];
    return typeof v === "function" ? v(...args) : (v ?? key);
  }

  // Label for a value from metrics.json (t(prefix + value)); unknown values are shown as they are.
  function tv(prefix, value) {
    if (value == null || value === "") return "–";
    return (prefix + value) in STR.en ? t(prefix + value) : String(value);
  }

  function setLang(next) {
    lang = next === "en" ? "en" : "zh";
    try { global.localStorage.setItem(KEY, lang); } catch (e) { /* storage blocked */ }
    applyStatic(global.document);
    listeners.forEach(fn => fn(lang));
  }

  // Fill elements marked data-i18n / data-i18n-ph / data-i18n-title, and the page language.
  function applyStatic(doc) {
    if (!doc) return;
    doc.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    doc.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
    doc.querySelectorAll("[data-i18n-ph]").forEach(el => { el.placeholder = t(el.dataset.i18nPh); });
    doc.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = t(el.dataset.i18nTitle); });
  }

  // A button that switches EN / 中文; its label names the other language.
  function bindToggle(button) {
    if (!button) return;
    const paint = () => { button.textContent = t("lang_button"); button.title = t("lang_hint"); };
    paint();
    listeners.push(paint);
    button.addEventListener("click", () => setLang(lang === "zh" ? "en" : "zh"));
  }

  function regimeLabel(s) {
    if (!s || lang === "en") return s || "";
    const m = String(s).match(/^(.*?)(?: \/ (low|normal|high|extreme) vol)?( \(compression\))?$/);
    if (!m) return s;
    let out = HEAD_ZH[m[1]] ?? m[1];
    if (m[2]) out += " / " + VOL_ZH[m[2]];
    if (m[3]) out += "（压缩）";
    return out;
  }

  const signalName = (id, fallback) => lang === "zh" ? (SIGNALS_ZH[id] ?? fallback ?? id) : (fallback ?? id);
  const biasLabel = s => lang === "zh" ? (BIAS_ZH[s] ?? s) : s;

  function sourceLabel(src) {
    if (src.startsWith("avwap_")) return "AVWAP·" + tv("anchor_", src.slice(6));
    return (lang === "zh" ? SOURCE_ZH : SOURCE_EN)[src] ?? src;
  }

  global.TA_I18N = {
    t, tv, setLang, applyStatic, bindToggle, regimeLabel, signalName, biasLabel, sourceLabel,
    lang: () => lang,
    onChange: fn => listeners.push(fn),
  };
})(window);
