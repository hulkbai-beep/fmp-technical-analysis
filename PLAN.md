# 升级计划：用 FMP 接口补强技术面（fmp-technical-analysis）

分支：`claude/fmp-endpoints-upgrade`（基于 `main` @ `baec735`，合约 ta-2.0）
日期：2026-11-04

本计划先列出**已用真实 API 验证过的事实**，再给出分阶段的改动设计。第一阶段（本分支）实现 1、2、4 三项，
其余作为后续阶段的待办。所有设计都遵守本 skill 现有原则：数字只在 Python 里算、因果（不偷看未来）、
缺数据就写 `null` + 原因、事件信息只作背景、**不改变评分卡**。

---

## 0. 已验证的事实（2026-11-04 用 `fmp_client` 实测）

| 接口 | 结果 | 关键字段 / 限制 |
|---|---|---|
| `holidays-by-exchange?exchange=NYSE&from&to` | ✅ 一次调用 1990–2028 共 405 行 | `date, name, isClosed, adjCloseTime`；含特殊休市（2001-09-11…14、2012-10-29/30、2025-01-09 卡特国丧）与半日市（`adjCloseTime` 13:00/13:30）。不带 from/to 只给过去 12 个月 |
| `dividends?symbol=` | ✅ | `date`（除息日）`, adjDividend`（**已按拆股调整**：NVDA 拆股前 0.04→0.004；AAPL 1987 0.1201→0.000536）`, dividend, frequency, declarationDate, paymentDate`；**包含已宣布、尚未到来的除息日**（KO 2026-12-01、AAPL 2026-11-12）；不派息的股票返回 `[]`（TSLA）；含特别股息（COST $15，2023-12-27，`frequency: Special`） |
| `economic-calendar?from&to` | ✅ | `date`（**UTC**，如 NFP 13:30 = 美东 8:30；FOMC 19:00/18:00 = 美东 14:00，夏令时正确）`, country, event, impact(High/Medium/Low/None), previous, estimate, actual`。**范围上限约 90 天，且静默截断**：1 年窗口返回 0 行，整年窗口只返回最后约 90 天 |
| `earnings-calendar` / `earnings` | ⚠️ | **没有**盘前/盘后字段（所以两日反应窗口仍然合理） |
| `aftermarket-quote` / `aftermarket-trade` | ✅ | `bidPrice, askPrice, timestamp` / `price, tradeSize, timestamp`（第二阶段用） |
| `shares-float` | ✅ | `floatShares, freeFloat, outstandingShares`（第三阶段用） |
| `sector-performance-snapshot`、`historical-sector-performance`、`industry-performance-snapshot`、`stock-peers` | ✅ | 第三阶段用 |
| `historical-chart/1hour`、`/5min` | ✅ | 1hour 单次约 3 个月（441 根）（第二/四阶段用） |
| `sp500-constituent`、`batch-quote`、`commitment-of-traders-list` | ❌ | 当前套餐受限：不纳入设计 |

**NYSE 规则日历 vs FMP：** 用 pandas 节假日规则 + 7 个特殊休市日（1994-04-27 尼克松、2001-09-11…14、
2004-06-11 里根、2007-01-02 福特、2012-10-29/30 桑迪、2018-12-05 老布什、2025-01-09 卡特）生成的休市日，
与 FMP 的 354 个休市日（1990–2028）**完全一致**，唯一差异是 1990-01-01（FMP 数据从 1990-02 开始，漏了它）。
FMP 的半日市清单在 2013 年以前不完整，但半日市只影响「今天这根 K 线是否已收完」的判断。

**除息跳空 bug 的规模（`/full` 不含股息调整，除息日机械性低开）：**

| 代码 | 2010 年来除息日上的原始向下跳空 | 扣除股息后仍是真跳空 | 落在除息日的 `gap_down_atr` 信号 | 当前 126 根窗口里落在除息日的未回补缺口 |
|---|---|---|---|---|
| HYG | 102 | 7 | 9 / 40 | 2026-06-01、2026-09-01 |
| T | 45 | 1 | 1 / 35（2026-10-09） | 2026-10-09 |
| VZ | 34 | 4 | 1 / 30（2026-10-09） | — |
| MO | 39 | 2 | 1 / 19 | — |
| JEPI | 36 | 3 | 0 / 7 | — |
| COST | 7 | 0 | 1 / 26 | 2026-10-30 |

即：缺口支撑/阻力和缺口信号的统计里混入了大量「股息假跳空」，高股息股和月度派息 ETF 尤其严重。

---

## 1. 范围

**第一阶段（本分支实现）**

- **A. NYSE 交易日历**（原建议 2）：一个日历对象贯穿数据校验、过期判断、缺口检测、周线完整性、到事件的交易日数。
- **B. 除息日处理**（原建议 1）：消除股息假跳空；新增 `dividends` 模块（下一个除息日是否落在持有期内）。
- **C. 宏观事件日历**（原建议 4）：新增 `macro_events` 模块（FOMC / CPI / 非农 / PCE / PPI / GDP / 零售…落在持有期内的列表）。
- 合约升级为 **ta-2.1**（只增字段），文档、测试、调用预算同步更新。

**不在本阶段：** 见第 7 节待办（盘后报价、财报时点、图表事件标记、板块/行业排名、流通股换手、日内数据）。

---

## 2. 设计 A：NYSE 交易日历

### 2.1 新模块 `scripts/market_calendar.py`

```python
class MarketCalendar:
    closed: frozenset[pd.Timestamp]      # 全天休市（只含工作日，已 normalize）
    early: dict[pd.Timestamp, str]       # 半日市收盘时间 "HH:MM"
    source: str                          # "fmp+rules" 或 "rules"
    problem: str | None                  # FMP 取不到时的原因
    holidays_array: np.ndarray           # datetime64[D]，给 np.busday_count 用

    def is_session(d) -> bool
    def close_time(d) -> str                       # "16:00" 或半日市时间
    def after_close(now_ny) -> bool                # now >= 当日收盘 + CLOSE_BUFFER_MINUTES
    def expected_session(now_ny) -> pd.Timestamp   # 此刻应已收完的最后一个交易日
    def sessions(start, end) -> pd.DatetimeIndex   # [start, end] 内的交易日
    def sessions_between(a, b) -> int              # (a, b] 内的交易日数（b<=a 时为 0）
    def missing_between(a, b) -> int               # a、b 之间（不含两端）的交易日数
    def next_session(d, inclusive=False) / previous_session(d, inclusive=False)
    def add_sessions(d, n) -> pd.Timestamp         # d 之后第 n 个交易日
    def week_last_session(d) -> pd.Timestamp       # d 所在 W-FRI 周的最后一个交易日

def rules_closed_and_early(start_year, end_year) -> tuple[set, dict]   # NYSE 规则 + 特殊休市 + 规则半日市
def from_fmp_rows(rows) -> tuple[set, dict]                             # isClosed → 休市；adjCloseTime → 半日市
def build_calendar(fmp_rows=None, problem=None, start_year=..., end_year=...) -> MarketCalendar
def weekday_calendar() -> MarketCalendar                                # 无节假日（旧行为，测试/兜底用）
```

- **合并规则：休市 = FMP ∪ 规则；半日市 = FMP ∪ 规则（时间优先用 FMP 的）。** 实测两者 1990–2028 一致，
  并集既能补 FMP 的覆盖边缘（1990-01-01），也能自动吸收未来临时宣布的休市（国丧等）。
- 规则：元旦（周日顺延周一，周六不补）、MLK（1998 起）、总统日、耶稣受难日、阵亡将士纪念日、
  六月节（2022 起，就近工作日）、独立日（就近工作日）、劳动节、感恩节、圣诞（就近工作日）；
  半日市：7 月 3 日（周一至周四）、感恩节次日、12 月 24 日（周一至周四），默认 13:00。
- FMP 取不到（受限 / 代理 / 网络）→ 只用规则，`source="rules"`，`problem` 记原因，分析照常进行。

### 2.2 获取（`fetch.py`）

- `load_calendar(client, today)`：**一次**调用 `holidays-by-exchange`，`exchange=NYSE, from=C.HISTORY_START,
  to=<明年 12-31>`。参数一年只变一次，缓存 TTL 7 天（`fmp_client.TTL_BY_ENDPOINT`）→ 实际几乎总是命中缓存。
- 在 `load_all` 里最先取日历（profile/quote 之后），后面的 `validate()` 都用它。

### 2.3 用到日历的地方（全部保留「无日历」时的旧行为，便于单测）

| 位置 | 现在 | 改为 |
|---|---|---|
| `fetch.validate(..., calendar=None)` 部分 K 线 | 16:15 前视为未收完 | 当日收盘时间 + 15 分钟（半日市 13:15） |
| `fetch.validate` 过期判断 | 工作日计数 | NYSE 交易日计数（`stale.business_days_behind` 字段名保留，含义变为交易日，加 `stale.basis`） |
| `fetch.validate` 数据缺口 | 相邻 K 线 > 4 个日历日 | 相邻 K 线之间缺 ≥ `GAP_MISSING_SESSIONS`(=1) 个交易日；条目新增 `missing_sessions`，`gaps.basis` 说明口径；9/11、桑迪等真实休市不再误报 |
| `fetch.expected_session` | 周末回退 | `calendar.expected_session` |
| `modules.build_event_risk` 到财报的交易日 | `np.busday_count` 不含节假日 | `np.busday_count(..., holidays=calendar.holidays_array)`，语义不变 |
| `indicators.resample_weekly(df, calendar=None)` 周线是否完整 | 最后一根不是周五就算不完整（耶稣受难日周会误报） | 最后一根 < 本周最后交易日才算不完整；`regime.build_multi_timeframe`、`chart._weekly` 传入日历 |

`data_quality.calendar = {"source", "exchange": "NYSE", "closed_days": n, "early_closes": n, "problem"}`。
`fmp_client` 的缓存「同一交易时段」规则不改（半日市最多多一次刷新，无害）。

---

## 3. 设计 B：除息日处理

### 3.1 获取与对齐（`fetch.py`）

- `fetch_dividends(client, symbol)` → 按除息日升序的列表：
  `{ex_date, amount(=adjDividend), dividend, frequency, declaration_date, record_date, payment_date}`；
  `adjDividend` 缺失或 ≤ 0 的行丢弃并计数。股票、ETF、SPY 都取（ETF 也派息）。TTL 1 天。
- `dividend_column(index, dividends)` → 与 K 线对齐的 `float` 序列：除息日那根 K 线 = 股息金额，其余 0；
  除息日没有 K 线时落到其后 `DIVIDEND_MATCH_DAYS`(=4) 个日历日内的第一根；同一根上的多笔相加。
- `load_all` 把它作为 **`bars["dividend"]` 列**挂上（取不到股息时全为 0 并记原因）。各模块用
  `bars.get("dividend")`，没有这一列时行为与现在完全相同。

### 3.2 消除股息假跳空（只改「跳空」相关的三处）

除息日 t 上，把前一根 K 线的价格视为「除息后等价价格」：`prev - D[t]`（加法，等价于持股人的总价值不变）。

| 位置 | 改动 |
|---|---|
| `levels.unfilled_gaps(df)` | 向上跳空：`low[t] > high[t-1] - D`，区间 `[high[t-1]-D, low[t]]`；向下跳空：`high[t] < low[t-1] - D`，区间 `[high[t], low[t-1]-D]`。缺口条目加 `ex_dividend`（金额或 null）。新增 `ex_dividend_gaps(df)` 列出「原始算跳空、扣股息后不算」的日期 → `levels.gap_adjustment = {"method", "ex_dividend_bars", "neutralized": [{date, amount, raw_direction}]}` 并写一条 note |
| `signal_stats.events` 的 `gap_up_atr` / `gap_down_atr` | `prev_close = close.shift(1) - D` |
| `modules._vwap_anchors` 的 `largest_gap_6m` | 同上 |

**刻意不改：** ATR / 真实波幅、K 线形态、价格行为、均线、RSI（与主流图表平台一致：图表价格是拆股调整、
非股息调整）。写进 methodology 的「近似」一节。

### 3.3 新模块 `dividends`（`modules.build_dividends`，放在 `event_risk` 之后）

```json
"dividends": {
  "available": true,
  "source": "FMP dividends: ex-dividend dates; adjDividend is split-adjusted, the same basis as the /full bars",
  "pays_dividends": true,
  "last_ex_dividend": {"date", "amount", "amount_pct", "frequency"},
  "next_ex_dividend": {"date", "amount", "amount_pct", "amount_atr", "frequency",
                        "declaration_date", "payment_date", "calendar_days", "trading_days_to"},
  "within_horizon": true, "within_stat_horizons": {"5": false, "10": true, "20": true}, "horizon_days": 10,
  "ex_dates_in_history": 67, "matched_to_bars": 67,
  "gap_basis": "on an ex-dividend bar, gaps compare with the prior bar minus the dividend",
  "notes": []
}
```

- `amount_pct = amount / close`，`amount_atr = amount / ATR14`（这次机械性下跌相对日常波动有多大）。
- 不派息：`available: true, pays_dividends: false`，其余为 null + note。接口失败：`available: false` + 原因。
- **不计算股息率**（那是基本面，skill 已刻意去掉基本面）。
- `scorecard.build_risk` 的 notes：除息日落在持有期内时加一句「除息日开盘价格约机械性下跌 $D（x%，y ATR），
  离现价不到 D 的止损可能仅因除息被触发」。评分卡本身不变。

---

## 4. 设计 C：宏观事件日历

### 4.1 获取（`fetch.py`）

- `fetch_macro(client, as_of)`：一次调用 `economic-calendar`，`from = as_of + 1 天`，`to = from + MACRO_WINDOW_DAYS - 1`
  （=89 天，避开 FMP 的约 90 天静默截断）。窗口按**最长持有期**（position 60 个交易日 ≈ 87 天）取，不随 horizon 变，
  所以 `--contract` 第二次运行完全走缓存。TTL 12 小时。
- 只保留 `country == "US"` 的行交给模块。

### 4.2 新模块 `scripts/macro.py` → `macro_events`（放在 `dividends` 之后）

- **分组（`config.MACRO_GROUPS`，按事件名正则匹配，不依赖 FMP 的 impact 标签）：**
  - tier 1：`fomc`（Fed Interest Rate Decision / FOMC Statement / Economic Projections / Press Conference）、
    `cpi`（Inflation Rate、Core Inflation Rate、CPI）、`jobs`（Non Farm Payrolls、Unemployment Rate、
    Average Hourly Earnings…）、`pce`（PCE Price Index、Core PCE、Personal Income/Spending）、`ppi`、`gdp`、`retail_sales`
  - tier 2：`fomc_minutes`、`ism_manufacturing`、`ism_services`、`jolts`、`consumer_sentiment`（Michigan、CB）、
    以及其他 FMP 标为 High 的美国事件（`other`，如中期选举）
  - 排除：`MACRO_EXCLUDE`（每周初请失业金等高频噪音）
- **同组同一时间戳的多行合并为一个事件**（如 CPI 的 MoM/YoY/Core 四行 → 一个 `cpi` 事件，`releases` 列出原名）。
- **时间：** UTC → 美东。`timing`：`pre_open`（< 09:30）/ `intraday` / `after_close`（≥ 当日收盘）/
  `non_session`（周末/假日）；`session` = 受影响的交易日（盘后与非交易日 → 下一个交易日）；
  `trading_days_to` = (as_of, session] 内的交易日数。只保留 as_of 收盘之后的事件。

```json
"macro_events": {
  "available": true,
  "source": "FMP economic-calendar, country US; FMP times are UTC, shown in New York time",
  "window": {"from": "2026-11-05", "to": "2027-02-01"},
  "horizon_days": 10,
  "events": [{"id": "cpi", "label": "CPI", "tier": 1, "date": "2026-11-12", "time_ny": "08:30",
              "timing": "pre_open", "session": "2026-11-12", "trading_days_to": 6, "calendar_days": 8,
              "releases": ["Inflation Rate YoY (Oct)", "..."], "impact": "High"}],
  "within_horizon": [{"id": "cpi", "label": "CPI", "tier": 1, "date": "2026-11-12", "trading_days_to": 6}],
  "within_stat_horizons": {"5": 0, "10": 2, "20": 4},
  "next": {"fomc": {...} | null, "cpi": {...}, "jobs": {...}},
  "counts": {"tier1": 9, "tier2": 14, "excluded": 12},
  "notes": []
}
```

- 宏观事件**只作背景**：不进评分卡、不加冲突项（position 持有期里几乎总有 FOMC/CPI，作为冲突项会变成恒定噪音）。
  报告里只写「哪天、几点、哪个事件落在持有期内」，不预测结果或方向。

---

## 5. 合约 ta-2.1（只增字段）

- `CONTRACT = "ta-2.1"`，`contracts/ta-2.0.schema.json` → `contracts/ta-2.1.schema.json`（`$id`/`const` 同步）。
- 新增：
  - `data_quality.calendar_source`（`"fmp+rules"` / `"rules"`）
  - 顶层 `dividend`：`{available, reason, pays_dividends, next_ex_date, next_amount, next_amount_pct,
    next_trading_days_to, last_ex_date, last_amount}`
  - 顶层 `macro`：`{available, reason, window_from, window_to, events: [{id, label, tier, date, time_ny,
    timing, session, trading_days_to}]}`
  - `horizons.*.ex_dividend_within_horizon`（bool|null）、`horizons.*.macro_within_horizon`
    （`[{id, label, tier, date, trading_days_to}]`）
- 语义修正（字段名不变）：`event.trading_days_to`、`data_quality.business_days_behind` 现在按 NYSE 交易日计（排除节假日）。

## 6. API 调用预算

| 运行 | 现在 | 之后 | 新增 |
|---|---|---|---|
| 股票（未缓存） | 10 | 13 | 日历 1 + 股息 1 + 宏观 1 |
| ETF | 8 | 11 | 同上 |
| SPY | 7 | 10 | 同上 |
| 同日重跑 / `--contract` 第二个 horizon | 0 | 0 | 日历按年缓存 7 天，宏观窗口与 horizon 无关 |

任何一个新接口受限或失败：对应模块 `available: false` + 原因，记入 `meta.restricted`/`meta.skipped`，其余照常。
`fmp_client.py check` 增加这三个接口的探测。

---

## 7. 文档与测试

**文档：** `SKILL.md`（调用次数、回复摘要里加「持有期内的除息日与关键宏观事件」、解读规则、边缘情况）、
`README.md`（ta-2.1、文件表）、`references/methodology.md`（数据源表、请求数、缓存、校验、新增日历/股息/宏观小节、
近似与局限）、`references/report_template.md`（第 1 节调用次数、第 9 节事件风险加除息与宏观、ETF 不再整节跳过、
第 12 节）、`references/signals.md`（跳空规则扣股息）。

**测试（全部离线）：**
- `tests/test_market_calendar.py`：规则 vs FMP 实测快照（`tests/data/nyse_holidays_fmp.json`，1990–2028）逐日一致；
  感恩节/耶稣受难日/半日市/国丧的各个辅助函数；FMP 失败退回规则。
- `tests/test_macro.py`：分组合并、UTC→美东（含夏令时切换）、timing/session 映射、持有期过滤、排除项、空数据与受限。
- 扩充 `test_fetch.py`（日历版校验、新调用预算 13/11/10、股息解析与对齐、受限兜底）、`test_levels.py`
  （假跳空被消除、超过股息的真跳空保留且区间正确、无股息列时行为不变）、`test_signals.py`、`test_modules.py`、
  `test_contract.py`（ta-2.1 schema）、`test_analyze_e2e.py`（新模块、调用预算、ETF/SPY 路径）。
- `tests/fakes.py` 的 `FakeClient` 默认提供这三个接口的合理数据。

**验收：**
1. 全部离线测试通过（基线 465 个）。
2. 真实 API 跑 AAPL、VZ、T、HYG、COST、KO、SPY、JEPI、TSLA、NVDA、BRK.B（swing + position + `--contract`）：无报错、
   调用次数符合预算、合约通过 schema；T 的 2026-10-09 与 HYG 的除息「缺口」不再是价位；感恩节前后的交易日数正确；
   宏观事件列表包含下一次 FOMC / CPI / 非农。

---

## 8. 执行方式

1. **构建**（多代理流水线）：A 日历模块先行 → B 股息 与 C 宏观 并行（文件不重叠）→ D 集成（fetch/analyze/合约/
   fakes/全部测试）→ E 文档。
2. **审查**：按「正确性与因果」「数据层与缓存」「合约与文档一致性」「日历与时区」「测试质量」分维度并行审查，
   每个发现再由独立的反驳者验证，只修确认的问题。
3. **真实数据验证**：上面的股票清单，逐项核对验收标准；必要时再修一轮。
4. 本地提交到分支；推送 / 开 PR 需要你授权（本会话目前没有该仓库的推送权限）。

---

## 9. 后续阶段（待办）

| 阶段 | 项目 | 接口 | 要点 |
|---|---|---|---|
| 2 | 盘后/盘前价格警告 | `aftermarket-quote`、`aftermarket-trade` | 收盘后运行时，盘后价偏离收盘 > 1 ATR → 数据警告：情景触发价/止损可能开盘即失效；不改评分卡 |
| 2 | 财报时点（盘前/盘后）推断 | `historical-chart/1hour` | 最近几次财报看小时线的大波动在开盘还是前一日收盘后，把反应窗口精确到一天 |
| 2 | 图表事件标记 | — | 在图表上标出除息日、FOMC/CPI 等（chart.js） |
| 3 | 板块轮动排名 | `sector-performance-snapshot`、`historical-sector-performance` 或 11 个板块 ETF | 所属板块在 11 个中排第几、排名在升还是在降 |
| 3 | 行业 / 同行相对强弱 | `industry-performance-snapshot`、`stock-peers` | 个股 3/6 个月收益在同行中的百分位；只在 position 下开启以控制调用 |
| 3 | 流通股换手率 | `shares-float` | 成交量 / 流通股，比 RVOL 更适合小盘股 |
| 4 | 日内数据替代近似 | `historical-chart/1hour`、`/5min` | 真实锚定 VWAP、日内成交量分布、缺口回补；只在 swing 下启用 |
| — | 宏观事件历史反应统计 | `economic-calendar`（历史，每 90 天一次调用） | 该股在过去 FOMC/CPI 日的平均绝对波动 vs 平日；调用量大，需单独评估 |

**不做：** 分析中使用 FMP 自带技术指标（继续只做交叉校验）；分析师目标价、内部人交易、13F、新闻（不是技术面）；
当前套餐受限的接口（`sp500-constituent`、`batch-quote`、COT）。
