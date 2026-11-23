// Chinese (default) / English text for the fmp-technical-analysis chart (ui/chart.js), chart.html and
// the local app (ui/index.html). Indicator names (SMA, RSI, MACD, ATR, ADX, RVOL, VWAP, POC ...)
// stay in English in both languages. Labels that come from metrics.json (regime label,
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
      range_label: "时间范围", r_1m: "1个月", r_3m: "3个月", r_6m: "6个月", r_ytd: "今年", r_1y: "1年", r_all: "全部",
      zoom_in: "放大（+）", zoom_out: "缩小（−）", fs_enter: "全屏", fs_exit: "退出全屏",
      auto_weekly: "图表宽度不足以每天一根 K 线，已自动切换为周线",
      weekly_note: "周线模式：指标数值为该周最后一个交易日的日线读数",
      week_of: (a, b) => `${a} ~ ${b} 当周`,
      partial_week: "（本周未结束）",
      o: "开", h: "高", l: "低", c: "收", v: "量",
      lg_sma20: "SMA20", lg_sma50: "SMA50", lg_sma100: "SMA100", lg_sma200: "SMA200", lg_supports: "支撑区间", lg_resistances: "压力区间", lg_current: "当前所在区间", k_zone: "所在区间", lg_trendlines: "趋势线",
      sma100_title: "SMA100（100 日均线）：仅作参考线，不参与均线排列、趋势投票和信号",
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
      t_regime: "市场状态", t_conflicts: "冲突项", t_signals: "信号统计", t_levels: "关键价位", t_notes: "数据说明",
      rg_type: "类型", rg_dir: "方向", rg_strength: "趋势强度", rg_vol: "波动率", rg_weekly: "周线",
      rg_struct: "日线高低点", rg_wstruct: "周线高低点",
      ss_uptrend: "上升", ss_downtrend: "下降", ss_mixed: "不明确",
      sw_higher: "更高", sw_lower: "更低", sw_equal: "持平",
      struct_seq: (h, l) => `高点${h} · 低点${l}`,
      struct_inval: (px, pct) => `失效价 ${px}（${pct}）`,
      struct_title: "道氏理论的读法：最近一个摆动高点和低点都比前一个高是上升结构，都更低是下降结构，其余为不明确。摆动点是 1.5 ATR 的 ZigZag 转折（周线用周线 ATR）。收盘跌破上升结构的最近低点（或升破下降结构的最近高点）即结构失效。仅作读数，不参与状态投票。",
      type_trend: "趋势", type_range: "区间", type_transitional: "过渡", type_vol_extreme: "波动极端",
      st_trending: "趋势中", st_transitional: "过渡", st_range: "无趋势",
      vol_low: "低", vol_normal: "正常", vol_high: "高", vol_extreme: "极端",
      vol_pctl: p => `一年分位 ${p}`, compression: "压缩",
      al_confirms: "周线确认日线", al_contradicts: "周线与日线相反", al_mixed: "周线不明确",
      dir_1: "上升", "dir_-1": "下降", dir_0: "不明",
      cf_none: "没有发现冲突项。",
      sig_h: h => `历史前瞻 ${h} 日收益（本股自身历史）`,
      sig_none: "当前没有活跃信号（最近 3 根 K 线内未触发）。",
      th_signal: "信号", th_fired: "触发", th_n: "n", th_hit: "胜率", th_med: "中位数", th_edge: "超额", sample: "样本",
      bars_ago: n => n === 0 ? "今日" : `${n} 根前`,
      sc_normal: "正常", sc_low: "偏少", sc_insufficient: "不足",
      edge_note: "超额 = 信号中位数 − 同状态基准中位数（括号内为胜率差）；样本不足（n < 15）的信号不足以作为依据。",
      th_level: "价位", th_dist: "距离", th_src: "来源", touches: (n, h, b) => `测试 ${n} 次 · 守住 ${h ?? 0} · 击穿 ${b ?? 0}`,
      since_break: (date, n) => `上次击穿 ${date}，之后守住 ${n ?? 0}`,
      round_fill: "整数关口，这一侧区间不足三个时补位，参考性弱",
      tl_up: "上升趋势线", tl_down: "下降趋势线",
      tl_touches: n => `触及 ${n} 次${n >= 3 ? "（已确认）" : ""}`,
      tl_intact: "完好", tl_broken: d => `${d} 收盘击穿`,
      tl_from: (a, b) => `${a} → ${b}`,
      tl_title: "上升趋势线沿摆动低点画：从窗口内最低的摆动低点出发，穿过让之后所有低点都不低于它的那个低点；下降趋势线沿摆动高点对称画。两点至少相隔 20 根 K 线。价位是趋势线今天的位置。收盘越过趋势线超过 0.25 ATR 记为击穿。仅供参考。",
      at_lvl: tag => `位于${tag === "Z" ? "所在区间" : " " + tag}`,
      lv_none: "没有可用的关键价位。",
      lv_ref: "仅供参考：这些是价格过去成交或转折过的区间，不预示价格下次会在哪里转折。止损和目标用的是波动率区间。",
      dn_partial: "未完成 K 线已排除", dn_stale: "数据可能过期", dn_illiquid: "流动性提示", dn_skipped: "已跳过的模块",
      dn_notes: "其他说明", dn_approx: "近似方法",
      stale_txt: (a, b, n) => `最新 K 线 ${a}，比预期交易日 ${b} 晚 ${n} 个交易日`,
      unavailable: "不可用", none: "无", disclaimer: "仅供信息参考的技术分析，不构成投资建议。",
      no_data: "没有可绘制的 K 线数据。",
      // charts 2 (price action) and 3 (candlestick patterns)
      t_pa: "价格行为（Al Brooks 方法的日线近似）", t_cs: "K 线形态",
      lg_ema: n => `EMA${n}`, lg_pa_counts: "H/L 计数", lg_pa_wedges: "楔形、双顶双底", lg_pa_ranges: "交易区间",
      lg_pa_fb: "区间突破与假突破", lg_pa_g20: "20 根缺口棒", lg_pa_mm: "测量目标位",
      lg_pa_mtr: "主要趋势反转", lg_pa_cx: "高潮、ii/ioi", lg_pa_lines: "趋势线与通道线",
      ch_up: "上升通道线", ch_down: "下降通道线",
      lg_cs_bull: "看涨形态", lg_cs_bear: "看跌形态", lg_cs_doji: "十字星",
      last_seen: d => `上次出现 ${d}`, never_seen: "本股历史上未出现",
      lib_title: h => `全部形态的历史表现（${h} 日，本股自身历史）`, th_pattern: "形态", th_edge_all: "超额",
      lib_note: "超额 = 形态中位数 − 全部交易日中位数（括号内为胜率差）；n < 15 的样本不足以作为证据。看跌形态的超额为负才支持其看法。",
      mm_leg: "等距运动（腿）", mm_range: "等距运动（区间高度）", mm_reached: "已到达",
      wedge_top: "楔形顶", wedge_bottom: "楔形底", fb_up: "向上假突破", fb_down: "向下假突破",
      gap_bull: n => `连续 ${n} 根 K 线最低价高于 EMA`, gap_bear: n => `连续 ${n} 根 K 线最高价低于 EMA`,
      state_tight_bull_channel: "紧密上升通道", state_bull_channel: "上升通道（宽）", state_trading_range: "交易区间",
      state_bear_channel: "下降通道（宽）", state_tight_bear_channel: "紧密下降通道",
      pa_since: "持续", n_bars: n => `${n} 根 K 线`, pa_ai: "Always-in 方向", ai_long: "多头", ai_short: "空头", ai_none: "未定",
      since: d => `自 ${d}`, slope_rising: "上行", slope_falling: "下行", slope_flat: "走平",
      vs_ema: x => `收盘距 EMA ${x} ATR`, pa_bar: "最新 K 线",
      bt_bull_trend_bar: "阳线趋势棒", bt_weak_bull_bar: "弱阳线", bt_doji: "十字/小实体", bt_weak_bear_bar: "弱阴线", bt_bear_trend_bar: "阴线趋势棒",
      pa_range: "区间框", pa_gap: "EMA 缺口棒", pa_hcount: "最近 H 计数", pa_lcount: "最近 L 计数", pa_wedge: "最近楔形",
      pa_fb: "最近区间假突破", pa_mm: "测量目标位",
      pa_pending: (label, k, need) => `最近 ${k} 根 K 线的读数已和当前状态不同（最新读数：${label}），满 ${need} 根才切换。`,
      shape_inside: "内包线", shape_ii: "ii（连续两根内包线）", shape_ioi: "ioi（内包、外包、内包）", shape_outside: "外包线",
      sigbar_bull: "合格的看涨信号 K 线", sigbar_bear: "合格的看跌信号 K 线",
      third_top: "上三分之一", third_middle: "中间三分之一", third_bottom: "下三分之一", tr_close: x => `收盘在区间${x}`, at_third: x => `位于区间${x}`, th_sigbar: "信号 K 线",
      pa_cx: "高潮", cx_buy: "买入高潮", cx_sell: "卖出高潮", cx_now: (side, n) => `${side}进行中 · 连续 ${n} 根趋势棒`,
      pa_trh: "区间内 H 计数", pa_trl: "区间内 L 计数", pa_double: "最近双顶/双底", pa_mtr: "最近主要趋势反转",
      pa_bo: "最近区间突破", pa_mode: "最近 ii/ioi",
      dbl_top: "双顶", dbl_bottom: "双底", mtr_top: "顶部反转", mtr_bottom: "底部反转", bo_up: "向上突破", bo_down: "向下突破",
      var_reversal: "反转型", var_flag: "旗形（顺势）", var_higher_low: "更高的低点", var_lower_high: "更低的高点", var_double: "双顶/双底式测试",
      paren: x => `（${x}）`, sep: "、",
      ft_yes: "有跟进", ft_no: "没有跟进", ft_wait: "等下一根 K 线跟进", bo_back: d => `${d} 收回区间内`,
      broke_up: "已向上突破", broke_down: "已向下突破", broke_both: "同一根 K 线上下都破",
      broke_none: (lo, hi) => `未突破（${lo}–${hi}）`,
      ch_beyond: "收盘已越过通道线", ch_over: n => `越线 ${n} 次`,
      setups_active_pa: "近 3 根 K 线出信号的形态（已触发或等待触发）",
      setups_none_pa: "最近 3 根 K 线没有已触发或等待触发的形态。",
      setups_lapsed: "出了信号但下一根没有触发（不算交易）",
      lapsed_row: (d, px, bull) => `${d} 信号 · 触发价 ${px}（${bull ? "未涨过" : "未跌破"}）`,
      entry_note: "入场规则：看涨形态在信号 K 线的下一根涨过信号 K 线高点时入场，看跌形态在下一根跌破信号 K 线低点时入场；没有触发就不算交易。统计从入场价算起，基准是所有交易日按同样方式入场的结果。",
      en_buy_stop: px => `等待触发：下一根涨过 ${px} 才入场`, en_sell_stop: px => `等待触发：下一根跌破 ${px} 才入场`,
      en_filled: (px, d) => `${d} 已触发，入场价 ${px}`,
      sb_strong: "信号 K 线合格", sb_weak: why => `信号 K 线偏弱${why ? "（" + why + "）" : ""}`,
      "weak_not a bull bar": "不是阳线实体", "weak_not a bear bar": "不是阴线实体",
      "weak_close below the top third": "收盘不在上三分之一", "weak_close above the bottom third": "收盘不在下三分之一",
      "weak_range over 2 ATR": "振幅超过 2 ATR",
      on_line: x => `在${x}上`, lib_trig: (a, b) => `${b} 次信号中触发 ${a} 次`,
      last_doji: (d, n) => `最近一根十字星：${d}（${n === 0 ? "今日" : n + " 根前"}）`,
      cs_recent: "最近出现的形态", cs_none_recent: "近期没有识别到形态。",
      setups_active: "近 3 根 K 线触发的形态", not_scored: "这些形态只作为辅助参考。",
      pa_note: "K 线颜色：实心 = 趋势棒（实体 ≥ 一半振幅），浅色 = 弱棒，灰色 = 十字/小实体。形态标在信号 K 线上，灰色标记 = 下一根没有触发入场。摆动点用 1.5 ATR 的 ZigZag，要等价格反向走出 1.5 ATR 才确认，所以楔形、双顶双底和趋势反转会晚几根出现。下方色条：市场状态（要连续 3 根确认才切换）与 Always-in 方向。全部为日线规则近似，统计来自本股自身历史。时间范围与上方主图同步。",
      cs_note: "实心 K 线为形态所在的 K 线；“前期下跌/上涨”按形态前一根收盘与 5 根前比较。统计来自本股自身历史，时间范围与上方主图同步。",
      tr_title: (a, b, s, e) => `交易区间 ${a}–${b}（${s} ~ ${e}）`,
      wedge_title: (side, k, d) => `${side} 第 ${k} 推（${d} 确认）`,
      mk_h2: "H2：靠近上行 EMA 的第二次回调入场", mk_l2: "L2：靠近下行 EMA 的第二次反弹入场", mk_count: k => `${k} 计数`,
      mk_tr_h2: "区间内 H2：交易区间下三分之一处的第二次回调入场", mk_tr_l2: "区间内 L2：交易区间上三分之一处的第二次反弹入场",
      mk_tr_count: k => `${k} 计数（交易区间内）`,
      mk_fb_up: (lv, d) => `向上假突破：${d} 突破区间上沿 ${lv} 后收回`, mk_fb_down: (lv, d) => `向下假突破：${d} 跌破区间下沿 ${lv} 后收回`,
      mk_bo_up: (lv, d) => `向上突破：${d} 趋势棒收在区间上沿 ${lv} 之上，这一根是跟进 K 线`,
      mk_bo_down: (lv, d) => `向下突破：${d} 趋势棒收在区间下沿 ${lv} 之下，这一根是跟进 K 线`,
      mk_mtr_top: v => `主要趋势反转（顶部）：趋势高点、跌破 EMA、再测试高点（${v}）后的看跌信号 K 线`,
      mk_mtr_bottom: v => `主要趋势反转（底部）：趋势低点、站上 EMA、再测试低点（${v}）后的看涨信号 K 线`,
      mk_dt: v => `双顶（${v}）：第二个高点在这一根确认`, mk_db: v => `双底（${v}）：第二个低点在这一根确认`,
      mk_cx_buy: "买入高潮：连续趋势阳线，收盘高出 EMA 超过 2 ATR", mk_cx_sell: "卖出高潮：连续趋势阴线，收盘低于 EMA 超过 2 ATR",
      mk_mode: k => `${k}：突破模式，高点上方挂买入、低点下方挂卖出`,
      mk_not_triggered: "下一根没有触发入场", mk_pending: "等下一根 K 线触发", mk_triggered: "下一根已触发入场",
      mk_g20_bull: "20 根缺口棒后首次回踩 EMA", mk_g20_bear: "20 根缺口棒后首次反抽 EMA",
      mm_title: (k, px, d) => `${k} 目标 ${px}（自 ${d}）`,
      strip_state: "状态", strip_ai: "AI",
      pa_aria: "价格行为图：按 Brooks 棒型着色的 K 线、EMA20、H/L 计数、楔形、双顶双底、主要趋势反转、交易区间、突破与假突破、趋势线和通道线、测量目标位",
      cs_aria: "K 线形态图：标出吞没、锤子线、晨星等经典形态",
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
      lg_sma20: "SMA20", lg_sma50: "SMA50", lg_sma100: "SMA100", lg_sma200: "SMA200", lg_supports: "Support zones", lg_resistances: "Resistance zones", lg_current: "Zone the close is in", k_zone: "In zone", lg_trendlines: "Trendlines",
      sma100_title: "SMA100 (100-day average): a reference line only, not part of the SMA stack, the trend votes or the signals",
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
      t_regime: "Regime", t_conflicts: "Conflicts", t_signals: "Signal statistics", t_levels: "Key levels", t_notes: "Data notes",
      rg_type: "Type", rg_dir: "Direction", rg_strength: "Trend strength", rg_vol: "Volatility", rg_weekly: "Weekly",
      rg_struct: "Daily swing structure", rg_wstruct: "Weekly swing structure",
      ss_uptrend: "uptrend", ss_downtrend: "downtrend", ss_mixed: "mixed",
      sw_higher: "higher", sw_lower: "lower", sw_equal: "equal",
      struct_seq: (h, l) => `${h} highs · ${l} lows`,
      struct_inval: (px, pct) => `ends at ${px} (${pct})`,
      struct_title: "Dow theory: an uptrend when the last swing high and the last swing low are both higher than the ones before, a downtrend when both are lower, mixed otherwise. Swings are ZigZag turns of 1.5 ATR (weekly ATR on the weekly chart). A close below an uptrend's last swing low (or above a downtrend's last swing high) ends the sequence. A reading only, not a regime vote.",
      type_trend: "Trend", type_range: "Range", type_transitional: "Transitional", type_vol_extreme: "Vol extreme",
      st_trending: "trending", st_transitional: "transitional", st_range: "range",
      vol_low: "low", vol_normal: "normal", vol_high: "high", vol_extreme: "extreme",
      vol_pctl: p => `1y percentile ${p}`, compression: "compression",
      al_confirms: "weekly confirms daily", al_contradicts: "weekly contradicts daily", al_mixed: "weekly unclear",
      dir_1: "up", "dir_-1": "down", dir_0: "mixed",
      cf_none: "No conflicts were flagged.",
      sig_h: h => `Forward ${h}-day returns (this stock's own history)`,
      sig_none: "No active signals (none fired in the last 3 bars).",
      th_signal: "Signal", th_fired: "Fired", th_n: "n", th_hit: "Hit", th_med: "Median", th_edge: "Edge", sample: "sample",
      bars_ago: n => n === 0 ? "today" : n === 1 ? "1 bar ago" : `${n} bars ago`,
      sc_normal: "normal", sc_low: "low", sc_insufficient: "insufficient",
      edge_note: "Edge = signal median − same-regime baseline median (hit-rate difference in brackets); signals with n < 15 are too few to be evidence.",
      th_level: "Level", th_dist: "Distance", th_src: "Sources", touches: (n, h, b) => `${n} test${n === 1 ? "" : "s"} · ${h ?? 0} held · ${b ?? 0} broken`,
      since_break: (date, n) => `last broken ${date}, ${n ?? 0} held since`,
      round_fill: "round number, listed because this side has fewer than three zones; a weak reference",
      tl_up: "Up trendline", tl_down: "Down trendline",
      tl_touches: n => `${n} touches${n >= 3 ? " (confirmed)" : ""}`,
      tl_intact: "intact", tl_broken: d => `broken on a close ${d}`,
      tl_from: (a, b) => `${a} → ${b}`,
      tl_title: "The up trendline runs along the swing lows: from the lowest swing low of the window through the later low that keeps every later low on or above it; the down trendline mirrors it along the swing highs. Its two points are at least 20 bars apart. The price is the line's value today. A close more than 0.25 ATR beyond the line breaks it. Reference only.",
      at_lvl: tag => tag === "Z" ? "at the zone the close is in" : `at ${tag}`,
      lv_none: "No key levels available.",
      lv_ref: "Reference only: zones where price traded or turned before, not a forecast of where it will turn next. Stops and targets use volatility bands.",
      dn_partial: "Partial bar excluded", dn_stale: "Possibly stale data", dn_illiquid: "Liquidity warning", dn_skipped: "Skipped modules",
      dn_notes: "Other notes", dn_approx: "Approximations",
      stale_txt: (a, b, n) => `last bar ${a} is ${n} sessions behind the expected session ${b}`,
      unavailable: "unavailable", none: "none", disclaimer: "Technical analysis for information only, not investment advice.",
      no_data: "No bars to chart.",
      t_pa: "Price action (daily approximation of Al Brooks' methods)", t_cs: "Candlestick patterns",
      lg_ema: n => `EMA${n}`, lg_pa_counts: "H/L counts", lg_pa_wedges: "Wedges, double tops / bottoms", lg_pa_ranges: "Trading ranges",
      lg_pa_fb: "Breakouts, failed breakouts", lg_pa_g20: "20 gap bars", lg_pa_mm: "Measured moves",
      lg_pa_mtr: "Major trend reversals", lg_pa_cx: "Climaxes, ii / ioi", lg_pa_lines: "Trendlines, channel lines",
      ch_up: "Up channel line", ch_down: "Down channel line",
      lg_cs_bull: "Bullish", lg_cs_bear: "Bearish", lg_cs_doji: "Doji",
      last_seen: d => `last seen ${d}`, never_seen: "never seen on this stock",
      lib_title: h => `Every pattern's record (${h} days, this stock's own history)`, th_pattern: "Pattern", th_edge_all: "Edge",
      lib_note: "Edge = pattern median − all-days median (hit-rate difference below); n < 15 is not evidence. A bearish pattern is supported by a negative edge.",
      mm_leg: "Measured move (leg)", mm_range: "Measured move (range height)", mm_reached: "reached",
      wedge_top: "Wedge top", wedge_bottom: "Wedge bottom", fb_up: "Failed breakout up", fb_down: "Failed breakdown",
      gap_bull: n => `${n} bars in a row with the low above the EMA`, gap_bear: n => `${n} bars in a row with the high below the EMA`,
      state_tight_bull_channel: "Tight bull channel", state_bull_channel: "Bull channel (broad)", state_trading_range: "Trading range",
      state_bear_channel: "Bear channel (broad)", state_tight_bear_channel: "Tight bear channel",
      pa_since: "Since", n_bars: n => `${n} bars`, pa_ai: "Always-in", ai_long: "long", ai_short: "short", ai_none: "none",
      since: d => `since ${d}`, slope_rising: "rising", slope_falling: "falling", slope_flat: "flat",
      vs_ema: x => `close ${x} ATR from the EMA`, pa_bar: "Last bar",
      bt_bull_trend_bar: "Bull trend bar", bt_weak_bull_bar: "Weak bull bar", bt_doji: "Doji / small body", bt_weak_bear_bar: "Weak bear bar", bt_bear_trend_bar: "Bear trend bar",
      pa_range: "Range box", pa_gap: "EMA gap bars", pa_hcount: "Last high count", pa_lcount: "Last low count", pa_wedge: "Last wedge",
      pa_fb: "Last failed breakout", pa_mm: "Measured moves",
      pa_pending: (label, k, need) => `The last ${k} bar${k === 1 ? " has" : "s have"} read differently (latest reading: ${label}); the state changes after ${need}.`,
      shape_inside: "inside bar", shape_ii: "ii (two inside bars)", shape_ioi: "ioi (inside, outside, inside)", shape_outside: "outside bar",
      sigbar_bull: "strong bull signal bar", sigbar_bear: "strong bear signal bar",
      third_top: "top third", third_middle: "middle third", third_bottom: "bottom third", tr_close: x => `close in the ${x}`, at_third: x => `in the range's ${x}`, th_sigbar: "Signal bar",
      pa_cx: "Climax", cx_buy: "Buy climax", cx_sell: "Sell climax", cx_now: (side, n) => `${side} under way · ${n} trend bars in a row`,
      pa_trh: "High count in range", pa_trl: "Low count in range", pa_double: "Last double top / bottom", pa_mtr: "Last major trend reversal",
      pa_bo: "Last range breakout", pa_mode: "Last ii / ioi",
      dbl_top: "Double top", dbl_bottom: "Double bottom", mtr_top: "Reversal down", mtr_bottom: "Reversal up", bo_up: "Breakout up", bo_down: "Breakout down",
      var_reversal: "reversal", var_flag: "flag (with trend)", var_higher_low: "higher low", var_lower_high: "lower high", var_double: "double-top / bottom test",
      paren: x => ` (${x})`, sep: ", ",
      ft_yes: "follow-through", ft_no: "no follow-through", ft_wait: "follow-through bar not in yet", bo_back: d => `closed back inside ${d}`,
      broke_up: "broke up", broke_down: "broke down", broke_both: "broke both ways on one bar",
      broke_none: (lo, hi) => `not broken (${lo}–${hi})`,
      ch_beyond: "close beyond the channel line", ch_over: n => `${n} overshoot${n === 1 ? "" : "s"}`,
      setups_active_pa: "Signal bars in the last 3 bars (entered or pending)",
      setups_none_pa: "No setup entered or pending in the last 3 bars.",
      setups_lapsed: "Signal bar, but the next bar did not trigger (no trade)",
      lapsed_row: (d, px, bull) => `signal ${d} · trigger ${px} (${bull ? "not exceeded" : "not undercut"})`,
      entry_note: "Entry rule: a bullish setup is entered when the bar after its signal bar trades above the signal bar's high, a bearish one when it trades below the signal bar's low; no trigger, no trade. Statistics run from the entry price; the baseline is every day entered the same way.",
      en_buy_stop: px => `pending: buy stop above ${px}`, en_sell_stop: px => `pending: sell stop below ${px}`,
      en_filled: (px, d) => `entered ${d} at ${px}`,
      sb_strong: "strong signal bar", sb_weak: why => `weak signal bar${why ? " (" + why + ")" : ""}`,
      "weak_not a bull bar": "not a bull bar", "weak_not a bear bar": "not a bear bar",
      "weak_close below the top third": "close below the top third", "weak_close above the bottom third": "close above the bottom third",
      "weak_range over 2 ATR": "range over 2 ATR",
      on_line: x => `at the ${x.toLowerCase()}`, lib_trig: (a, b) => `${a} of ${b} signal bars triggered`,
      last_doji: (d, n) => `Last doji: ${d} (${n === 0 ? "today" : n + " bars ago"})`,
      cs_recent: "Recent patterns", cs_none_recent: "No recent patterns.",
      setups_active: "Fired in the last 3 bars", not_scored: "Context only.",
      pa_note: "Candle colours: solid = trend bar (body at least half the range), pale = weak bar, grey = doji / small body. Setups are marked on their signal bar; a grey marker means the next bar did not trigger the entry. Swings are a 1.5 ATR ZigZag, confirmed once price has moved 1.5 ATR the other way, so wedges, double tops / bottoms and trend reversals appear a few bars late. Strips below: market state (a change needs 3 bars in a row) and always-in direction. All rules are daily-bar approximations, measured on this stock's own history. The date range follows the main chart.",
      cs_note: "Solid candles belong to a pattern; the prior decline / advance compares the close before the pattern with the close 5 bars earlier. Statistics come from this stock's own history; the date range follows the main chart.",
      tr_title: (a, b, s, e) => `Trading range ${a}–${b} (${s} to ${e})`,
      wedge_title: (side, k, d) => `${side}, push ${k} (confirmed ${d})`,
      mk_h2: "H2: second pullback entry near the rising EMA", mk_l2: "L2: second rally entry near the falling EMA", mk_count: k => `${k} count`,
      mk_tr_h2: "Range H2: second pullback entry in the bottom third of the trading range", mk_tr_l2: "Range L2: second rally entry in the top third of the trading range",
      mk_tr_count: k => `${k} count (inside a trading range)`,
      mk_fb_up: (lv, d) => `Failed breakout: broke above the range high ${lv} on ${d}, closed back inside`, mk_fb_down: (lv, d) => `Failed breakdown: broke below the range low ${lv} on ${d}, closed back inside`,
      mk_bo_up: (lv, d) => `Breakout up: a trend bar closed above the range high ${lv} on ${d}; this is the follow-through bar`,
      mk_bo_down: (lv, d) => `Breakout down: a trend bar closed below the range low ${lv} on ${d}; this is the follow-through bar`,
      mk_mtr_top: v => `Major trend reversal (top): trend high, break below the EMA, test of the high (${v}), then this bear signal bar`,
      mk_mtr_bottom: v => `Major trend reversal (bottom): trend low, break above the EMA, test of the low (${v}), then this bull signal bar`,
      mk_dt: v => `Double top (${v}): the second high is confirmed on this bar`, mk_db: v => `Double bottom (${v}): the second low is confirmed on this bar`,
      mk_cx_buy: "Buy climax: bull trend bars in a row, close more than 2 ATR above the EMA", mk_cx_sell: "Sell climax: bear trend bars in a row, close more than 2 ATR below the EMA",
      mk_mode: k => `${k}: breakout mode, a buy stop above its high and a sell stop below its low`,
      mk_not_triggered: "the next bar did not trigger the entry", mk_pending: "waiting for the next bar to trigger", mk_triggered: "entered on the next bar",
      mk_g20_bull: "First EMA test after 20 bull gap bars", mk_g20_bear: "First EMA test after 20 bear gap bars",
      mm_title: (k, px, d) => `${k} target ${px} (from ${d})`,
      strip_state: "State", strip_ai: "AI",
      pa_aria: "Price-action chart: candles coloured by Brooks bar type, EMA20, H/L counts, wedges, double tops and bottoms, major trend reversals, trading ranges, breakouts and failed breakouts, trendlines with channel lines, and measured moves",
      cs_aria: "Candlestick-pattern chart: engulfing, hammer, stars and other classic patterns",
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
    pa_h2_ema: "H2 回调（靠近上行 EMA20）",
    pa_l2_ema: "L2 反弹（靠近下行 EMA20）",
    pa_wedge_bottom: "楔形底（三推下跌）",
    pa_wedge_top: "楔形顶（三推上涨）",
    pa_failed_breakdown: "交易区间向下假突破",
    pa_failed_breakout: "交易区间向上假突破",
    pa_gap20_bull_test: "20 根多头缺口棒后首次回踩 EMA",
    pa_gap20_bear_test: "20 根空头缺口棒后首次反抽 EMA",
    pa_tr_h2: "交易区间下三分之一的 H2",
    pa_tr_l2: "交易区间上三分之一的 L2",
    pa_double_bottom: "双底",
    pa_double_top: "双顶",
    pa_mtr_bottom: "主要趋势反转（底部，转涨）",
    pa_mtr_top: "主要趋势反转（顶部，转跌）",
    pa_breakout_up: "向上突破交易区间并有跟进",
    pa_breakout_down: "向下突破交易区间并有跟进",
    cs_bullish_engulfing: "看涨吞没", cs_bearish_engulfing: "看跌吞没", cs_hammer: "锤子线", cs_shooting_star: "射击之星",
    cs_morning_star: "晨星", cs_evening_star: "暮星", cs_piercing_line: "刺透形态", cs_dark_cloud_cover: "乌云盖顶",
    cs_bullish_harami: "看涨孕线", cs_bearish_harami: "看跌孕线", cs_three_white_soldiers: "红三兵", cs_three_black_crows: "三只乌鸦",
    doji: "十字星",
  };
  const SIGNALS_EN = {
    cs_bullish_engulfing: "Bullish engulfing", cs_bearish_engulfing: "Bearish engulfing", cs_hammer: "Hammer",
    cs_shooting_star: "Shooting star", cs_morning_star: "Morning star", cs_evening_star: "Evening star",
    cs_piercing_line: "Piercing line", cs_dark_cloud_cover: "Dark cloud cover", cs_bullish_harami: "Bullish harami",
    cs_bearish_harami: "Bearish harami", cs_three_white_soldiers: "Three white soldiers", cs_three_black_crows: "Three black crows",
    doji: "Doji",
  };
  // chart 3 marker labels
  const PATTERN_SHORT = {
    zh: { cs_bullish_engulfing: "吞没", cs_bearish_engulfing: "吞没", cs_hammer: "锤子", cs_shooting_star: "流星",
      cs_morning_star: "晨星", cs_evening_star: "暮星", cs_piercing_line: "刺透", cs_dark_cloud_cover: "乌云",
      cs_bullish_harami: "孕线", cs_bearish_harami: "孕线", cs_three_white_soldiers: "三兵", cs_three_black_crows: "三鸦", doji: "十字" },
    en: { cs_bullish_engulfing: "Eng", cs_bearish_engulfing: "Eng", cs_hammer: "Ham", cs_shooting_star: "SS",
      cs_morning_star: "MS", cs_evening_star: "ES", cs_piercing_line: "PL", cs_dark_cloud_cover: "DC",
      cs_bullish_harami: "Har", cs_bearish_harami: "Har", cs_three_white_soldiers: "3WS", cs_three_black_crows: "3BC", doji: "Doji" },
  };

  const HEAD_ZH = {
    "Strong uptrend": "强势上升趋势", "Strong downtrend": "强势下降趋势", "Uptrend": "上升趋势",
    "Downtrend": "下降趋势", "Mixed": "方向不明", "Range": "区间震荡", "Undetermined": "无法判断",
    "Transitional (uptrend bias)": "过渡期（偏上升）", "Transitional (downtrend bias)": "过渡期（偏下降）",
    "Transitional (mixed)": "过渡期（方向不明）", "Mixed (trending up)": "方向不明（趋势向上）",
    "Mixed (trending down)": "方向不明（趋势向下）",
  };
  const VOL_ZH = { low: "低波动", normal: "波动正常", high: "高波动", extreme: "波动极端" };

  const SOURCE_ZH = {
    pivot_high: "摆动高点", pivot_low: "摆动低点", weekly_pivot_high: "周线高点", weekly_pivot_low: "周线低点",
    round_number: "整数关口", poc: "POC", value_area_low: "VAL", value_area_high: "VAH",
    hvn: "HVN", high_52w: "52周高", low_52w: "52周低", sma50: "SMA50", sma100: "SMA100", sma200: "SMA200",
    demand: "需求区", supply: "供给区", gap_up: "向上缺口", gap_down: "向下缺口",
    gap_up_top: "向上缺口上沿", gap_up_bottom: "向上缺口下沿", gap_down_top: "向下缺口上沿", gap_down_bottom: "向下缺口下沿",
  };
  const SOURCE_EN = {
    pivot_high: "pivot high", pivot_low: "pivot low", weekly_pivot_high: "weekly high", weekly_pivot_low: "weekly low",
    round_number: "round number", poc: "POC", value_area_low: "VAL", value_area_high: "VAH",
    hvn: "HVN", high_52w: "52w high", low_52w: "52w low", sma50: "SMA50", sma100: "SMA100", sma200: "SMA200",
    demand: "demand zone", supply: "supply zone", gap_up: "gap up", gap_down: "gap down",
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

  const signalName = (id, fallback) => lang === "zh" ? (SIGNALS_ZH[id] ?? fallback ?? id) : (SIGNALS_EN[id] ?? fallback ?? id);
  const patternShort = id => PATTERN_SHORT[lang][id] ?? id;

  function sourceLabel(src) {
    if (src.startsWith("avwap_")) return "AVWAP·" + tv("anchor_", src.slice(6));
    return (lang === "zh" ? SOURCE_ZH : SOURCE_EN)[src] ?? src;
  }

  global.TA_I18N = {
    t, tv, setLang, applyStatic, bindToggle, regimeLabel, signalName, patternShort, sourceLabel,
    lang: () => lang,
    onChange: fn => listeners.push(fn),
  };
})(window);
