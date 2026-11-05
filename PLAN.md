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
| `economic-calendar?from&to` | ✅ | `date`（**UTC**，如 NFP 13:30 = 美东 8:30；FOMC 19:00/18:00 = 美东 14:00，夏令时正确）`, country, event, impact(High/Medium/Low/None), previous, estimate, actual`。**范围上限约 90 天，且静默截断**：1 年窗口返回 0 行，整年窗口只返回最后约 90 天。**当前套餐不允许较早的 `from`**（`from=2025-11-01` 被拒：Premium Query Parameter；`2026-01-29` 起可用）→ 只能看未来/近期，历史反应统计受限。同一事件的 impact 标签会变（Michigan 消费者信心 7 次 High、4 次 Medium），所以按**事件名**分组 |
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
- 在 `load_all` 里最先取日历（profile/quote 之后），后面的 `validate()` 都用它。（审查后移到 quote 之前，见 10.2 R6）

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
`fmp_client` 的缓存「同一交易时段」规则不改（半日市最多多一次刷新，无害）。——**审查后更正（见 10.2 R6）：** 这个判断只考虑了
13:15–16:15 之间写入的副本，漏了 13:15 之前写入的：半日市中午缓存的盘中 K 线会在 13:15–16:15 被 `validate` 当成完整收盘。
缓存现在与部分 K 线规则用同一个截止时间（当日收盘 + 15 分钟）。

---

## 3. 设计 B：除息日处理

### 3.1 获取与对齐（`fetch.py`）

- `fetch_dividends(client, symbol)` → 按除息日升序的列表：
  `{ex_date, amount(=adjDividend), dividend, frequency, declaration_date, record_date, payment_date}`；
  `adjDividend` 缺失或 ≤ 0 的行丢弃并计数。股票、ETF、SPY 都取（ETF 也派息）。TTL 1 天（审查后改为与 K 线相同的
  「同一交易时段」规则，见 10.2 R6）。
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

- **分组（`config.MACRO_GROUPS`，按事件名的锚定正则匹配，不依赖 FMP 的 impact 标签）：**
  - tier 1：`fomc`（Fed Interest Rate Decision / FOMC Statement / FOMC Economic Projections / Press Conference）、
    `cpi`（Inflation Rate、Core Inflation Rate、CPI s.a / n.s.a）、`jobs`（Non Farm Payrolls、Nonfarm Payrolls Private、
    Unemployment Rate、U-6、Average Hourly Earnings、Government/Manufacturing Payrolls、Participation Rate）、
    `pce`（PCE Price Index、Core PCE、Personal Income/Spending）、`ppi`（Producer Price Index、Core PPI、PPI Ex …）、
    `gdp`（GDP Growth Rate、GDP Price Index、GDP Sales、PCE Prices QoQ）、`retail_sales`
  - tier 2：`fomc_minutes`、`fed_policy`（Jackson Hole Symposium、Monetary Policy Report）、`ism_manufacturing`、
    `ism_services`、`jolts`、`consumer_sentiment`（Michigan Consumer Sentiment、CB Consumer Confidence）
  - tier 3：其他 FMP 标为 High 的美国事件（`other`，标签用原事件名，如耐用品、房屋开工、中期选举）
  - 排除：`MACRO_EXCLUDE`（初请/续请失业金及 4 周均值——每周都有的高频噪音）；锚定正则自然排除
    Cleveland CPI、通胀预期、Atlanta Fed GDPNow、Fed 官员讲话等
- **同组同一美东日期的多行合并为一个事件**（CPI 的 MoM/YoY/Core 多行 → 一个 `cpi`；FOMC 14:00 决议 + 14:30
  记者会 → 一个 `fomc`，时间取最早一行），`releases` 列出原名。tier 3 按「事件名 + 日期」各自成事件（审查后改为按
  「发布 + 日期」合并，见 10.2 R6）。
- metrics 里列出 tier 1–3 全部；合约只放 tier ≤ `MACRO_CONTRACT_MAX_TIER`(=2)；报告逐条写 tier 1、简述 tier 2、
  tier 3 只给数量。
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
    timing, session, trading_days_to}]}`（只含 tier ≤ `MACRO_CONTRACT_MAX_TIER`）
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
| — | 宏观事件历史反应统计 | `economic-calendar`（历史，每 90 天一次调用） | 该股在过去 FOMC/CPI 日的平均绝对波动 vs 平日；**当前套餐不允许较早的 `from`**，需升级套餐或自建事件日期表，单独评估 |
| — | 风险说明里的免责声明 | — | `risk.notes` 里含 `risk.label` 与 `meta.disclaimer`，按模板第 11 节「逐条列出」会把免责声明放到报告中间；应从 notes 中拿掉或让模板跳过它 |
| — | 图表价位提示缺 `weak` | — | `ui/chart.js` / `ui/i18n.js` 的价位悬浮提示仍只显示测试、守住、跌破次数，没有新增的 `weak` |
| — | SPAC 空壳启发式的误报 | `profile`（`ipoDate`） | 原有的 `spac_like_start` 只看最早 126 根收盘价是否集中在 $10 附近，会误报拆股调整后早期收盘价恰好在 $10 附近的老股票（T 自 1990 年起，中位数 10.52）。例如：只有历史起点接近上市日（`ipoDate`）时才判定 |

**不做：** 分析中使用 FMP 自带技术指标（继续只做交叉校验）；分析师目标价、内部人交易、13F、新闻（不是技术面）；
当前套餐受限的接口（`sp500-constituent`、`batch-quote`、COT）。

---

## 10. 进度（第一阶段完成情况）

更新：2026-11-05。第一阶段（A 日历、B 除息、C 宏观、合约 ta-2.1、调用预算、文档）已全部实现，并已提交、推送到
分支 `claude/fmp-endpoints-upgrade`。之后经过一轮多代理审查：6 个独立审查者提出 8 个问题，每个问题由 3 个独立验证者
（反驳 / 复现 / 设计意图）判定，7 个确认并已修复（见 10.2 R6），1 个被否决。离线测试 **819 个全部通过**（基线 465；
审查修复前 774）。

### 10.1 已实现

| 项目 | 文件 | 内容 |
|---|---|---|
| A. NYSE 交易日历 | `scripts/market_calendar.py`；`fetch.py`、`modules.py`、`indicators.py`、`regime.py`、`chart.py` | `MarketCalendar`（2.1 节全部方法，另有向量化的 `missing_sessions_between` 和 `summary()`）；`fetch.load_calendar` 一次请求；`validate` 的部分 K 线（半日市为 13:15）、过期判断（`stale.basis`）、数据缺口（条目加 `missing_sessions`，加 `gaps.basis`），基准也用同一日历校验；到财报的交易日、周线 `partial`；`data_quality.calendar` |
| B. 除息日 | `scripts/dividends.py`；`fetch.py`、`levels.py`、`signal_stats.py`、`modules.py`、`scorecard.py` | `fetch.fetch_dividends` 与 `bars["dividend"]` 列；三处跳空（未回补缺口、`gap_down_atr`、`largest_gap_6m` 锚点）的向下一侧按 `prev - D` 计算（向上跳空不变，见 10.2 R1）；`levels.gap_adjustment`；`dividends` 模块；除息日落在持有期内时 `risk.notes` 加一句提示；`data_quality.dividends` |
| C. 宏观事件 | `scripts/macro.py`；`fetch.py`、`config.py` | `fetch.macro_window` / `fetch_macro`（从 as_of 当天起 89 天，见 10.2 R2）；按事件名分 3 级、同组同一美东日期合并（tier 3 同一发布合并，见 10.2 R6）、UTC→美东、`timing` / `session`、`trading_days_to`；`macro_events` 模块（没有美国行的经济日历视为缺数据） |
| 合约 ta-2.1 | `contract.py`、`contracts/ta-2.1.schema.json` | 第 5 节全部字段（补充见 10.2） |
| 调用预算与缓存 | `fmp_client.py`、`fetch.py`、`analyze.py` | TTL：日历 7 天、宏观 12 小时；股息与 K 线一样走「同一交易时段」规则，其截止时间来自 NYSE 日历（半日市 13:15，见 10.2 R6）；`check` 增加三个接口的探测；未缓存 13 / 11 / 10 次（e2e 测试与实盘一致）；`config_snapshot` 加 `calendar` / `dividends` / `macro`；终端摘要加两行（持有期内的除息日、tier 1 宏观事件） |
| 文档 | `SKILL.md`、`README.md`、`references/methodology.md`、`report_template.md`、`signals.md` | 按第 7 节：调用次数、回复摘要、解读规则、边缘情况、合约 2.1、`check`；methodology 新增 1.7 日历、1.8 除息、模块 K（除息）与 L（宏观），原 K（价格行为）改为 M；report_template 第 9 节改为财报 + 除息 + 宏观（ETF 只跳过财报部分）；signals.md 的跳空规则扣股息 |
| 测试 | `tests/` | 新增 `test_market_calendar.py`（64 个）、`test_dividends.py`（25 个）、`test_macro.py`（171 个）；扩充 fetch、levels、signals、modules、indicators、regime、chart、scorecard、contract、lookahead、e2e；`fakes.py` 默认提供三个接口的数据 |

### 10.2 与计划的差异

- **日历（2.1–2.3）**
  - `MarketCalendar.source` 另有 `"weekdays"`（`weekday_calendar()`，供单测和旧调用方用）；`week_last_session`
    在该周没有交易日时返回 `None`。
  - `load_calendar` 返回 `(calendar, problem)`；规则覆盖到「明年 + 1」，比 FMP 请求多一年。
  - 实测 FMP `holidays-by-exchange` 的 `from` **不含当天**（`from=2026-11-26` 不返回感恩节），这就是 FMP 清单缺
    1990-01-01 的原因，由规则补上。
  - 无日历时 `validate` 的数值和告警不变，只多了说明口径的 `stale.basis` / `gaps.basis`。
- **除息（3.1–3.3）**
  - 解析规则补充：完全重复的行只算一次（FMP 偶尔把同一笔列两次）；同一除息日金额不同的多笔（常规 + 特别股息）
    合并为一条、金额相加。
  - 计划外新增：`data_quality.dividends = {ex_dates, rows_dropped, problem}`；`gap_adjustment.neutralized[*]`
    加 `raw_unfilled`（不扣股息时是否会是未回补缺口）。
  - 计划外新增 `DIVIDEND_ACTIVE_DAYS`（=400）：400 个日历日内没有除息日、也没有已宣布的 → `pays_dividends: false`
    （视为停发）；派息但还没宣布下一次 → `next_ex_dividend` 和持有期标志为 `null`，并写说明。
- **宏观（4.1–4.2）**
  - `fetch_macro` 返回所有国家的行，`US` 过滤放在 `macro.py`（计划写在 fetch 里）。
  - `timing` 多一个 `date_only`：00:00:00 UTC 的行是只有日期的条目（假日、选举、Jackson Hole），不做时区换算、
    没有时间。
  - `counts` 多 `tier3`、`unclassified`；新增 `coverage`（`listed_through`、`horizon_end`、`complete`、
    `complete_stat_horizons`）：实测 FMP 只提前约 6–8 周列出 FOMC 以外的发布（FOMC 提前数月，
    `MACRO_FAR_AHEAD_GROUPS`），所以 position 持有期的列表通常只是下限。
  - 分组正则扩充并改为不区分大小写（`Interest Rate Projection - …` 归入 `fomc`，`Core CPI`，
    `Real Consumer Spending` 归入 `gdp`）。
- **合约（第 5 节）**：另加 `macro.listed_through` 和 `horizons.*.macro_complete`；`calendar_source` 的枚举另含
  `"weekdays"` 与 `null`；`ex_dividend_within_horizon` 对不派息的标的为 `false`、未知时为 `null`；经济日历不可用时
  `macro.events` 和 `macro_within_horizon` 为 `null`（不是 `[]`）。
- **文档**：顺带修正 methodology 原有的两处不准确：`earnings` 的缓存走「同一交易时段」规则（原文写的是 12 小时
  TTL）；`metrics.json` 顶层键列表补上原先漏掉的 `price_action`、`candlesticks`。
- **审查后的决定（2026-11-05）**
  - R1「只消除向下」（改 3.2 节的对称写法）：股息只能解释下跌。除息日 t（股息 D）上，向下跳空须超过股息才算
    （`high[t] < low[t-1] - D`，区间上沿 `low[t-1] - D`）；向上跳空按原始价格算，与以前完全相同（`low[t] > high[t-1]`，
    区间下沿 `high[t-1]`）。`gap_down_atr` 用 `close[t-1] - dividend[t]`，`gap_up_atr` 恢复原规则 `open - close[t-1]`；
    `largest_gap_6m` 锚点：低开扣股息、被股息完全解释时记 0（不会变成向上跳空），高开按原始前收盘。
    `ex_dividend_gaps` 只列向下的原始跳空（`raw_direction` 为兼容保留，恒为 "down"）。ATR、K 线形态、价格行为、
    RSI、均线仍不调整（写在 methodology 第 7 节）。
  - R2 宏观窗口从 as_of 当天开始（改 4.1 节）：`from = as_of`，`to = as_of + MACRO_WINDOW_DAYS - 1`。as_of 收盘后、
    UTC 日期仍是 as_of 的发布（美东夏令时 16:00–19:59，冬令时 16:00–18:59）因此能取到；as_of 收盘及之前的发布
    由 `macro.py` 丢弃。
  - R3 `macro_events.within_horizon` 的条目另含 `time_ny`、`timing`、`session`；合约 `horizons.*.macro_within_horizon`
    另含 `time_ny`、`timing`（schema `macro_brief` 同步）。报告模板第 9 节不再回查 `events`。
  - R4 除息日对齐（改 3.1 节）：`dividend_column(index, dividends, calendar=None)` / `match_to_bars` 有交易所日历时，
    没有 K 线的除息日落到其后（含当天）第一个 NYSE 交易日的 K 线（该股当天须有 K 线），多日休市里的除息日也能
    对上（KO 2001-09-12 → 2001-09-17）；落在交易日、但该股缺 K 线的除息日不匹配。无日历（或 `weekday_calendar()`）
    时仍用 `DIVIDEND_MATCH_DAYS`。`fetch.load_all` 和 `dividends` 模块都传入日历。
  - R5 按现设计保留：上面列出的合约补充字段与 null / false 约定、`DIVIDEND_ACTIVE_DAYS`、`timing` 的 `date_only`、
    `coverage`、`data_quality.dividends`、`GAP_MISSING_SESSIONS` = 1（是否太吵由实盘验证判断）、T 的 2026-10-09
    缺口保留为价位（扣除股息后仍是真跳空）、SKILL.md 的 description 修改（866 字符，低于 1024）。
  - R6 第二轮审查确认的问题与修正（2026-11-05）：
    - **半日市的缓存截止时间**（改 2.3 节最后一句、2.2 节的请求顺序）：`fmp_client` 的「同一交易时段」规则原来固定
      16:15，而 `validate` 在半日市 13:15 起就把当天 K 线当成完整的，所以 13:15 之前缓存的盘中 K 线（股票、SPY、
      板块 ETF、^VIX、quote、earnings）会在 13:15–16:15 被当成收盘价分析，且没有任何警告。现在客户端有可选的
      `complete_from(date)`（标准库实现，不引入 pandas），`fetch.load_all` 用日历设置它（与 `_complete_from` /
      `validate` 同一截止时间：收盘 + `CLOSE_BUFFER_MINUTES`）；没有日历时仍是 `MARKET_CLOSE_BUFFER`。日历请求
      因此移到 quote 之前：请求顺序为 profile、holidays-by-exchange、quote、earnings…（次数不变）。
    - **股息按交易时段缓存**（改 3.1 节「TTL 1 天」）：FMP 每次拆股都会重算 `adjDividend`，和 `/full` 价格一样；
      24 小时 TTL 会让拆股前缓存的股息配上拆股后的 K 线（金额差拆股倍数）。`dividends` 加入 `SESSION_ENDPOINTS`，
      删除 `DIVIDEND_TTL_SECONDS`。代价：隔日重跑多 1 次请求；未缓存 13 / 11 / 10 次、同日重跑 0 次不变。
    - **空的经济日历是缺数据**（改 4.1 节、methodology L）：FMP 对不提供的区间静默返回 `[]`，而从 as_of 起的
      89 天窗口总有美国发布，所以没有美国行的回答不再是「可用、0 个事件」：`fetch_macro` 把它记为问题
      （`meta.skipped["macro_events"]`），`macro.build_macro_events` 对没有可用美国行的输入也返回不可用；合约里
      `macro.events`、`macro_within_horizon`、`macro_complete` 为 `null`。
    - **tier 3 按发布合并**（改 4.2 节）：FMP 把一次发布列成多行（Existing Home Sales 与其 MoM；耐用品的 MoM、
      Ex Transp、Ex Defense），各自标 High，原来每个名字算一个事件，报告里 tier 3 的数量被放大（KO position
      12 个条目约 7–8 次发布）。`macro.release_name` 去掉末尾的 MoM / YoY / QoQ 和 "Ex …" 限定词，同一发布同一天
      合并为一个事件，标签是发布名。
    - **测试补强**：look-ahead 的除息测试增加落在 `gap_down_atr` 上的切点（其中两个的下一根 K 线有股息），并加两组
      只改后续股息的确定性变体；`largest_gap_6m` 增加被股息完全解释、否则就是最大缺口的下跌，以及下一根 K 线的
      股息不影响本根的用例；宏观测试的 fixture 改为 R2 窗口（含 as_of 当天收盘前的真实行、一次发布多行的真实行），
      并补上统计期边界（恰好 h 个交易日）、覆盖边界（持有期恰好止于 `listed_through` / 窗口末日）和 counts 的
      as_of 过滤。每一项都用重新引入缺陷的变体验证过：修正前这些测试会失败。
  - R7 报告端到端检查发现的问题与修正（2026-11-05；部分属于第一阶段之前就有的行为，一并修正）：
    - **52 周 / 历史极值**：收盘创历史新高但离当日最高价超过 0.5 ATR 时，旧规则判为「不在高点」（AAPL 2026-11-04：
      收盘 349.51 是历史最高收盘，但比当日最高价低 0.58 ATR）。`levels.extremes` 现在也认「区间内最高（最低）收盘」，
      并给出 `rule` 说明。只影响说明文字与情景标签，不影响评分。
    - **置信度说明**：原文只数「样本支持的同向 / 反向」信号，漏掉未确认的信号；现在分别列出确认同向、确认反向、
      未确认、样本不足的数量（`factors.unconfirmed`）。置信度等级的计算不变。
    - **价位测试次数**：`touches = holds + breaks + weak`，新增 `weak`（离开时没有 1 ATR 反应），模板的 Tests 列随之改写。
    - **财报冲突项与情景说明**：模板第 5 节中文标题由「矛盾信号」改为「冲突项」（UI 同步），明确冲突项不改变任何
      分数；财报说明改为按数字措辞（中位两日波动、超过 h 日 1σ 的次数与最大值），不再固定写「常常超过 1σ」。
    - **股息对趋势读数的影响**：当 252 根 K 线的总回报比价格回报高 3 个百分点以上（`DIVIDEND_DRIFT_NOTE`），
      `regime.notes` 提示趋势投票用的是未除息调整的价格（HYG：价格 −3.3%，总回报 +2.6%）；方法本身不变。
    - **未宣布的除息日**：`dividends` 新增 `ex_dates_12m` 与 `projected_in_horizon`（按过去 12 个月的间隔估计持有期内
      的除息次数与合计比例，明确标为估计）；`risk.notes` 在估计有除息日落在持有期内时提示，并说明波动带假设零漂移。
    - **宏观覆盖说明**：FOMC 日期覆盖到请求窗口末日（2027-01-31，窗口内最后一次是 2027-01-27），其他发布只列到
      2026-12-31；说明文字原来与列表里的 2027-01-27 FOMC 矛盾，现已一致。
    - 模板第 6 节列出 `levels.gaps`（最多 5 个，标明部分回补 / 除息日）与 `levels.notes`；第 8 节价格行为统计改用
      评分卡自己的双阈值、与全部交易日比较；新增数字取整规则（脚本文字已给出的数字照抄）。

### 10.3 验收进度（第 7 节）

1. 离线测试：**827 passed**（基线 465；审查修复前 774；报告端到端检查前 819）。
2. 真实 API 全清单（2026-11-05 凌晨运行，as_of 2026-11-04；每个标的用全新的空缓存）：AAPL、VZ、T、HYG、COST、KO、SPY、
   JEPI、TSLA、NVDA、BRK.B，每个都跑 swing + `--contract` 和 position。
   - 22 次运行全部退出码 0、`meta.errors` 为空；11 份合约全部通过 ta-2.1 schema。
   - 空缓存调用次数：股票 13（AAPL、VZ、T、COST、KO、NVDA、BRK.B）、HYG 11、SPY 10；历史不足约 19 年的标的每个价格
     接口只需 1 次请求：TSLA 11、JEPI 9。随后的 position 运行全部 0 次（走缓存）。
   - 11 个标的（AAPL 自 1990 年起）都没有数据缺口告警：`GAP_MISSING_SESSIONS = 1` 没有带来噪音。
   - 除息：AAPL 11-12（$0.27，0.08%，0.03 ATR）落在 swing 持有期内 → `risk.notes` 出现提示；KO 12-01 只落在 position 内；
     TSLA、BRK.B 正确识别为不派息；COST 10-30 的向上跳空按 R1 保持原样。
   - 宏观：各标的一致（市场级数据）；FMP 只列到 2026-12-31（FOMC 列到 2027-01-27），position 列表正确标为不完整。
   - BRK.B 周六（11-07）发财报 → 3 个交易日，即周一开盘受影响 ✅。
3. 报告端到端检查：按 SKILL.md 和模板，用实盘数据写了 3 份中文报告（AAPL swing、NVDA swing、HYG position），再由独立
   审查者逐个核对数字与措辞规则：**共核对 2,111 个数字，错误 0 个**。审查者追溯到文档或代码的问题已修复（见 10.2 R7）。
4. 早期验证（保留作记录）：AAPL、T、HYG、SPY 的 swing + `--contract`。
   - 全部退出码 0、无错误，合约通过 ta-2.1 schema；空缓存调用次数 AAPL 13、HYG 11、SPY 10。
   - 日历 `fmp+rules`：355 个休市日、88 个半日市；四个标的均无数据缺口。
   - HYG：窗口内 5 个除息「假跳空」被消除（2026-06-01、07-01、09-01、10-01、11-02），其中 06-01、09-01
     原本会是未回补缺口价位 ✅。
   - T：2026-10-09 扣除 0.2775 的股息后**仍是真跳空**，所以保留为缺口（带 `ex_dividend` 标注，仍是阻力位 R3 的来源之一）。
     验收标准「T 的 2026-10-09 不再是价位」的前提不成立，不是实现问题。
   - 感恩节前后的交易日数：AAPL 下次财报 2027-02-04 为 62 个交易日（按工作日算是 66）✅。
   - 宏观列表包含下一次非农（11-06 08:30）、CPI（11-10 08:30）和 FOMC（12-09 14:00）✅。

### 10.4 未决事项

- 经济日历 TTL 12 小时：同一天相隔 12 小时以上、as_of 相同的重跑会再请求一次经济日历（「同日重跑 0 次」的例外）。
- `GAP_MISSING_SESSIONS` = 1：任何一个缺失的交易日都会告警。实盘四个标的（及 SPY / XLK / ^VIX 基准）均为 0，
  其他标的需在实盘验证中观察。`validate` 不标记落在非交易日的 K 线，可作为以后的数据质量检查。
- T 的 1990 年拆股调整后收盘价（中位数 10.52）触发 SPAC 空壳启发式，是原有的误报，与本次改动无关（已列入第 9 节）。
- e2e 的合成股息很小（$0.50 对约 $4,475 的价格），e2e 里没有被消除的跳空；消除逻辑由 `test_levels.py` 单测和
  HYG 实盘覆盖。
- `fmp_client.py check` 的假日探测用 `from=<去年>-01-01`，因 `from` 不含当天会漏掉那年的 1 月 1 日（只影响显示）。
- 经济日历返回空列表（或没有美国行）时 `macro_events` 不可用并写明原因，但这个回答仍按 12 小时 TTL 缓存：期间重跑
  得到同样结果，`--refresh` 可重试。
- 半日市的缓存修正依赖「FMP 在盘中提供当天未完成的 K 线」这一假设（部分 K 线规则本身也依赖它）；本会话在收盘后
  运行，未能实测。
