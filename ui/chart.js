// Technical-analysis chart for chart.html and the local app (ui/index.html): hand-drawn SVG, no
// libraries. TAChart.render(root, data) draws the header, the main chart (candles, SMAs, anchored
// VWAPs, support/resistance zones, earnings markers, volume-profile sidebar, volume / RSI / RS
// sub-panels), chart 2 (price action after Al Brooks), chart 3 (candlestick patterns) and the side
// panels from the JSON that scripts/chart.py builds. Every number shown comes from that JSON; this
// file only lays it out (weekly candles are precomputed too).
(function (global) {
  "use strict";

  const I18N = global.TA_I18N;
  const t = (key, ...a) => I18N.t(key, ...a);
  const tv = (prefix, value) => I18N.tv(prefix, value);
  const VIS_KEY = "ta-vis";
  const SMA_KEYS = ["sma20", "sma50", "sma200"];
  const MIN_PX_PER_DAY = 3;   // below this the chart switches to weekly candles by itself
  const MIN_VIEW = 10;        // fewest daily bars a zoom can show
  const WEEKLY_MIN_DAYS = 140; // a weekly-default chart shows daily candles for views up to ~6 months
  const RANGES = [["1m", 1], ["3m", 3], ["6m", 6], ["ytd", 0], ["1y", 12]];
  let uid = 0;

  // ------------------------------------------------------------------ formatting
  const isNum = v => typeof v === "number" && isFinite(v);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtPx = v => !isNum(v) ? "–" : Math.abs(v) < 1 ? v.toFixed(4) : v.toFixed(2);
  const fmtNum = (v, d = 1) => isNum(v) ? v.toFixed(d) : "–";
  const sign = v => (v > 0 ? "+" : v < 0 ? "−" : "");
  const fmtSigned = (v, d = 1) => { if (!isNum(v)) return "–"; const r = +v.toFixed(d); return sign(r) + Math.abs(r).toFixed(d); };
  const fmtPct = (v, d = 1, signed = false) => {
    if (!isNum(v)) return "–";
    const r = +(v * 100).toFixed(d);
    return (signed ? sign(r) : r < 0 ? "−" : "") + Math.abs(r).toFixed(d) + "%";
  };
  const fmtPP = v => isNum(v) ? fmtSigned(v * 100, 0) + "pp" : "–";
  const pctCls = v => !isNum(v) || v === 0 ? "" : v > 0 ? "pos" : "neg";
  function fmtVol(v) {
    if (!isNum(v)) return "–";
    const a = Math.abs(v);
    const units = I18N.lang() === "zh" ? [[1e8, "亿"], [1e4, "万"]] : [[1e9, "B"], [1e6, "M"], [1e3, "K"]];
    for (const [d, s] of units) if (a >= d) return (v / d).toFixed(a / d >= 100 ? 0 : 1) + s;
    return String(Math.round(v));
  }
  function niceTicks(lo, hi, n) {
    const raw = (hi - lo) / Math.max(1, n), mag = 10 ** Math.floor(Math.log10(raw || 1));
    const step = [1, 2, 2.5, 5, 10].find(m => m * mag >= raw) * mag, out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }
  const tickLabel = (v, step) => v.toFixed(Math.min(4, (String(+step.toPrecision(6)).split(".")[1] || "").length));
  const measure = (() => {
    const ctx = global.document.createElement("canvas").getContext("2d");
    return (s, px = 11, weight = 400) => {
      if (!ctx) return String(s).length * px * 0.6;
      ctx.font = `${weight} ${px}px system-ui, -apple-system, "Segoe UI", sans-serif`;
      return ctx.measureText(String(s)).width;
    };
  })();

  // ------------------------------------------------------------------ data preparation
  function prepare(data) {
    const daily = (data.candles || []).map(([d, o, h, l, c, v], i) => ({ d, o, h, l, c, v, di: i }));
    const index = new Map(daily.map((b, i) => [b.d, i]));
    const align = pairs => {
      const out = new Array(daily.length).fill(null);
      for (const [d, v] of pairs || []) { const i = index.get(d); if (i != null) out[i] = v; }
      return out;
    };
    const weekly = (data.weekly || []).map(([d, o, h, l, c, v, start, partial]) =>
      ({ d, o, h, l, c, v, start, partial, di: index.get(d) })).filter(w => w.di != null);
    const series = { rvol: align(data.rvol), rsi: align(data.rsi14), rs: data.rs_spy ? align(data.rs_spy) : null };
    SMA_KEYS.forEach(k => { series[k] = align(data[k]); });
    const avwaps = Object.entries(data.avwaps || {}).map(([name, pairs]) => ({ name, key: "avwap_" + name, vals: align(pairs) }));
    // a panned view can put any close on the last-close chip, so the chip is sized for the longest one
    const widestClose = daily.reduce((w, b) => { const s = fmtPx(b.c); return s.length > w.length ? s : w; }, "");
    return { daily, weekly, series, avwaps, widestClose, pa: preparePA(data.price_action, index, align), cs: prepareCS(data.candlesticks, index) };
  }

  // chart 2: per-bar arrays share the candles' order; markers, wedges and ranges get daily indices
  function preparePA(p, index, align) {
    if (!p || !p.available) return null;
    const byBar = new Map(), at = d => index.get(d);
    for (const m of p.markers || []) {
      const i = at(m.date);
      if (i == null) continue;
      if (!byBar.has(i)) byBar.set(i, []);
      byBar.get(i).push(m);
    }
    const wedges = (p.wedges || []).map(w => ({ ...w, pts: w.pushes.map(([d, v]) => [at(d), v]) })).filter(w => w.pts.every(([i]) => i != null));
    const ranges = (p.ranges || []).map(r => ({ ...r, i0: at(r.start), i1: at(r.end) })).filter(r => r.i0 != null && r.i1 != null);
    const mms = (p.measured_moves || []).map(m => ({ ...m, i0: at(m.from_date) ?? 0 }));
    return { ema: align(p.ema), types: p.bar_types || [], states: p.states || [], ai: p.always_in || [], byBar, wedges, ranges, mms };
  }

  // chart 3: markers by their last bar
  function prepareCS(p, index) {
    if (!p || !p.available) return null;
    const byBar = new Map();
    for (const m of p.markers || []) {
      const i = index.get(m.date), i0 = index.get(m.start);
      if (i == null) continue;
      if (!byBar.has(i)) byBar.set(i, []);
      byBar.get(i).push({ ...m, i0: i0 ?? i });
    }
    return { byBar };
  }

  function loadVis() {
    try { return JSON.parse(global.localStorage.getItem(VIS_KEY)) || {}; } catch (e) { return {}; }
  }
  function saveVis(vis) {
    try { global.localStorage.setItem(VIS_KEY, JSON.stringify(vis)); } catch (e) { /* storage blocked */ }
  }

  // ------------------------------------------------------------------ header and side panels
  function headerHtml(d) {
    const m = d.meta || {}, k = d.key_numbers || {};
    const hz = m.horizon ? `${t(m.horizon)} · ${m.primary_horizon} ${I18N.lang() === "zh" ? "日" : "d"}` : "";
    const tile = (key, val, sub = "", cls = "") =>
      `<div class="ta-stat"><div class="k">${esc(key)}</div><div class="v ${cls}">${val}</div>${sub ? `<div class="s">${sub}</div>` : ""}</div>`;
    const ne = k.next_earnings;
    const tiles = [
      tile(t("k_price"), esc(fmtPx(k.price)), `<span class="${pctCls(k.change_pct)}">${esc(fmtPct(k.change_pct, 2, true))}</span>`),
      tile(t("k_atr"), esc(fmtPx(k.atr14)), esc(fmtPct(k.atr_pct, 2))),
      tile(t("k_rsi"), esc(fmtNum(k.rsi14, 1))),
      tile(t("k_rvol"), esc(fmtNum(k.rvol, 2))),
      tile(t("k_adx"), esc(fmtNum(k.adx, 1))),
      tile(t("k_range52"), `${esc(fmtPx(k.low_52w))}–${esc(fmtPx(k.high_52w))}`, "", "sm"),
    ];
    if (isNum(k.expected_move_1sd_pct)) tiles.push(tile(t("k_move", k.expected_move_horizon ?? m.primary_horizon), "±" + esc(fmtPct(k.expected_move_1sd_pct, 1))));
    if (ne) tiles.push(tile(t("k_next_e"), esc(ne.date), isNum(ne.calendar_days) ? esc(t("days_away", ne.calendar_days, ne.trading_days_est)) : ""));
    return `<div class="ta-head"><h1 class="ta-ticker">${esc(m.ticker)}</h1>${m.name ? `<span class="ta-name">${esc(m.name)}</span>` : ""}
        ${hz ? `<span class="ta-badge">${esc(hz)}</span>` : ""}${m.is_etf ? `<span class="ta-badge">${esc(t("etf"))}</span>` : ""}</div>
      <div class="ta-sub">${esc(t("as_of"))} ${esc(m.as_of)} · ${esc(t("source"))}: ${esc(m.data_source)}</div>
      <div class="ta-stats">${tiles.join("")}</div>`;
  }

  function regimeHtml(r) {
    if (!r || !r.available) return card(t("t_regime"), `<p class="ta-small">${esc(t("unavailable"))}: ${esc(r?.reason)}</p>`, "regime");
    const dir = r.direction ? tv("dir_", r.direction_sign) : "–";
    const vol = r.vol_label ? `${tv("vol_", r.vol_label)} · ${t("vol_pctl", fmtNum(r.vol_pctl, 0))}${r.compression ? " · " + t("compression") : ""}` : "–";
    const weekly = r.alignment ? `${r.weekly_direction ? tv("dir_", r.weekly_sign) : "–"} · ${tv("al_", r.alignment)}` : "–";
    const body = `<p class="ta-regime" title="${esc(r.label)}">${esc(I18N.regimeLabel(r.label))}</p>
      <dl class="ta-kv">
        <dt>${esc(t("rg_type"))}</dt><dd>${esc(tv("type_", r.type))}</dd>
        <dt>${esc(t("rg_strength"))}</dt><dd>ADX ${esc(fmtNum(r.adx, 1))}${r.strength ? " · " + esc(tv("st_", r.strength)) : ""}</dd>
        <dt>${esc(t("rg_dir"))}</dt><dd>${esc(dir)}</dd>
        <dt>${esc(t("rg_vol"))}</dt><dd>${esc(vol)}</dd>
        <dt>${esc(t("rg_weekly"))}</dt><dd>${esc(weekly)}</dd>
      </dl>${r.mtf_note && I18N.lang() === "en" ? `<p class="ta-small">${esc(r.mtf_note)}</p>` : ""}`;
    return card(t("t_regime"), body, "regime");
  }

  function scoreHtml(s) {
    if (!s || !s.available) return card(t("t_score"), `<p class="ta-small">${esc(t("unavailable"))}: ${esc(s?.reason)}</p>`, "score");
    const biasCls = /bull/i.test(s.bias || "") ? "pos" : /bear/i.test(s.bias || "") ? "neg" : "";
    const conf = tv("conf_", s.confidence?.level);
    const rows = s.pillars.map(p => {
      const v = isNum(p.score) ? Math.max(-2, Math.min(2, p.score)) : 0, w = Math.abs(v) / 2 * 50;
      const bar = `<i style="left:${v >= 0 ? 50 : 50 - w}%;width:${w}%;background:var(${v >= 0 ? "--up" : "--down"})"></i>`;
      return `<div class="ta-pillar"><div class="ta-prow"><span>${esc(tv("pl_", p.name))}</span>
          <div class="ta-pbar" role="img" aria-label="${esc(tv("pl_", p.name))} ${isNum(p.score) ? p.score : "–"}">${bar}</div>
          <span class="sc ${pctCls(p.score)}">${isNum(p.score) ? sign(p.score) + Math.abs(p.score) : "–"}</span>
          <span class="w">${isNum(p.weight) ? Math.round(p.weight * 100) + "%" : "–"}</span></div>
        ${p.reason ? `<div class="ta-reason">${esc(p.reason)}</div>` : ""}</div>`;
    }).join("");
    const head = `<div class="ta-prow ta-small" aria-hidden="true"><span></span><span style="display:flex;justify-content:space-between"><span>−2</span><span>0</span><span>+2</span></span><span></span><span class="w">${esc(t("weight"))}</span></div>`;
    const list = (title, items) => items.length ? `<h4 class="ta-small" style="margin-top:10px">${esc(title)}</h4><ul class="ta-list">${items.join("")}</ul>` : "";
    const reasons = (s.confidence?.reasons || []).map(x => `<li>${esc(x)}</li>`);
    const conflicts = (s.conflicts || []).map(c =>
      `<li>${esc(c.description)}${c.typical_meaning ? `<div class="ta-reason">${esc(c.typical_meaning)}</div>` : ""}</li>`);
    return card(t("t_score"), `<div class="ta-bias"><span class="b ${biasCls}">${esc(I18N.biasLabel(s.bias) || "–")}</span>
        <span class="ta-sub">${esc(t("wscore"))} <b>${esc(fmtSigned(s.weighted_score, 2))}</b></span>
        <span class="ta-sub">${esc(t("confidence"))} <b>${esc(conf)}</b></span></div>
      ${head}${rows}${list(t("confidence"), reasons)}${list(t("conflicts"), conflicts)}`, "score");
  }

  function activeTable(rows, primary) {
    const body = rows.map(a => {
      const arrow = a.bias > 0 ? `<span class="pos" aria-hidden="true">▲</span> ` : a.bias < 0 ? `<span class="neg" aria-hidden="true">▼</span> ` : "";
      const fired = `${t("th_fired")} ${t("bars_ago", a.bars_ago)} · ${String(a.fired_date || "").slice(5)}`;
      const sample = a.confidence ? ` · ${t("sample")} ${tv("sc_", a.confidence)}` : "";
      return `<tr class="${a.confidence === "insufficient" ? "dim" : ""}"><td class="l wrap">${arrow}${esc(I18N.signalName(a.id, a.name))}
          <div class="ta-sub" title="${esc(a.fired_date)}">${esc(fired + sample)}</div></td>
        <td>${esc(a.n ?? "–")}</td>
        <td>${esc(fmtPct(a.hit_rate, 0))}</td>
        <td class="${pctCls(a.median)}">${esc(fmtPct(a.median, 1, true))}</td>
        <td class="${pctCls(a.edge_median)}">${esc(fmtPct(a.edge_median, 1, true))}<br><span class="ta-sub">${esc(fmtPP(a.edge_hit_rate))}</span></td></tr>`;
    }).join("");
    return `<p class="ta-small" style="margin:0 0 6px">${esc(t("sig_h", primary))}</p>
      <div class="ta-tablewrap"><table class="ta-table ta-sig"><thead><tr><th>${esc(t("th_signal"))}</th>
      <th>${esc(t("th_n"))}</th><th>${esc(t("th_hit"))}</th><th>${esc(t("th_med"))}</th><th>${esc(t("th_edge"))}</th></tr></thead>
      <tbody>${body}</tbody></table></div>`;
  }

  function signalsHtml(sg) {
    const title = t("t_signals");
    if (!sg || !sg.available) return card(title, `<p class="ta-small">${esc(t("unavailable"))}: ${esc(sg?.reason)}</p>`, "signals");
    if (!sg.active.length) return card(title, `<p class="ta-small">${esc(t("sig_none"))}</p>`, "signals");
    return card(title, `${activeTable(sg.active, sg.primary_horizon)}<p class="ta-small">${esc(t("edge_note"))}</p>`, "signals");
  }

  function levelsHtml(d) {
    const lv = d.levels || {};
    if (!lv.available || !(lv.supports.length + lv.resistances.length)) {
      return card(t("t_levels"), `<p class="ta-small">${esc(t("lv_none"))}${lv.reason ? " " + esc(lv.reason) : ""}</p>`);
    }
    const MAX_SRC = 4;
    const row = (tag, x, cls) => {
      const src = (x.sources || []).map(I18N.sourceLabel).filter((v, i, a) => a.indexOf(v) === i);
      const more = src.length > MAX_SRC ? ` +${src.length - MAX_SRC}` : "";
      const zone = x.zone_high - x.zone_low > 1e-9 ? `${fmtPx(x.zone_low)}–${fmtPx(x.zone_high)}` : "";
      return `<tr><td class="${cls}"><b>${tag}</b></td><td>${esc(fmtPx(x.price))}${zone ? `<br><span class="ta-sub">${esc(zone)}</span>` : ""}</td>
        <td>${esc(fmtPct(x.distance_pct, 1, true))}<br><span class="ta-sub">${esc(fmtSigned(x.distance_atr, 1))} ATR</span></td>
        <td class="l wrap" title="${esc(src.join(", "))}">${esc(src.slice(0, MAX_SRC).join(", ") + more)}
          ${isNum(x.touches) ? `<div class="ta-sub">${esc(t("touches", x.touches, x.holds, x.breaks))}</div>` : ""}</td></tr>`;
    };
    const res = lv.resistances.map((x, i) => row("R" + (i + 1), x, "neg")).reverse().join("");
    const sup = lv.supports.map((x, i) => row("S" + (i + 1), x, "pos")).join("");
    const price = `<tr class="price"><td>${esc(t("k_price"))}</td><td>${esc(fmtPx(d.key_numbers?.price))}</td><td></td><td></td></tr>`;
    return card(t("t_levels"), `<div class="ta-tablewrap"><table class="ta-table"><thead><tr><th></th><th>${esc(t("th_level"))}</th>
      <th>${esc(t("th_dist"))}</th><th class="l">${esc(t("th_src"))}</th></tr></thead>
      <tbody>${res}${price}${sup}</tbody></table></div>`);
  }

  function notesHtml(d) {
    const n = d.data_notes || {};
    let h = "";
    if (n.partial_bar) h += `<div class="ta-warn"><b>${esc(t("dn_partial"))}</b>: ${esc(n.partial_bar.reason || n.partial_bar.date)}</div>`;
    if (n.stale) h += `<div class="ta-warn"><b>${esc(t("dn_stale"))}</b>: ${esc(t("stale_txt", n.stale.last_bar, n.stale.expected_session, n.stale.business_days_behind))}</div>`;
    if (n.illiquid) h += `<div class="ta-warn"><b>${esc(t("dn_illiquid"))}</b>: ${esc(n.illiquid)}</div>`;
    const list = (title, xs) => xs && xs.length ? `<h4>${esc(title)}</h4><ul class="ta-list">${xs.map(x => `<li>${x}</li>`).join("")}</ul>` : "";
    const notes = [...(n.notes || []), ...(d.rs_spy_note ? [d.rs_spy_note] : [])];
    h += `<div class="ta-notes">${list(t("dn_skipped"), (n.skipped || []).map(s => `<b>${esc(s.module)}</b>: ${esc(s.reason)}`))}
      ${list(t("dn_notes"), notes.map(esc))}${list(t("dn_approx"), [esc(t("approx"))])}</div>`;
    return card(t("t_notes"), h);
  }

  const card = (title, body, area = "") => `<section class="ta-card${area ? " ta-a-" + area : ""}"><h3>${esc(title)}</h3>${body}</section>`;

  // ------------------------------------------------------------------ legend
  function legendItems(d, prep) {
    const items = SMA_KEYS.map(k => ({ key: k, label: t("lg_" + k), color: `var(--${k})`, sw: "" }));
    for (const a of prep.avwaps) {
      items.push({ key: a.key, label: "AVWAP·" + tv("anchor_", a.name), color: `var(--av-${a.name}, var(--muted))`, sw: "dash",
        title: t("avwap_title", tv("anchor_", a.name), d.avwap_anchors?.[a.name] ?? "") });
    }
    if (d.levels?.supports?.length) items.push({ key: "supports", label: t("lg_supports"), color: "var(--up)", fill: "var(--up-zone)", sw: "box" });
    if (d.levels?.resistances?.length) items.push({ key: "resistances", label: t("lg_resistances"), color: "var(--down)", fill: "var(--down-zone)", sw: "box" });
    if (d.volume_profile?.available) items.push({ key: "vp", label: t("lg_vp"), color: "var(--va)", sw: "box" });
    if ((d.earnings_dates || []).length) items.push({ key: "earnings", label: t("lg_earnings"), color: "var(--text)", sw: "box" });
    return items;
  }

  // ------------------------------------------------------------------ zoom window
  // The visible window is a range of daily bar indices i0..i1 (weekly candles follow its dates).
  function currentView(st) {
    const N = st.prep.daily.length, v = st.view;
    return v && v.i1 < N ? v : { i0: 0, i1: N - 1 };
  }
  // A range button's window ends at the last bar: 1M/3M/6M/1Y back from it (calendar months), or YTD.
  function rangeView(daily, key) {
    const N = daily.length, last = daily[N - 1].d;
    let from;
    if (key === "all") return { i0: 0, i1: N - 1 };
    if (key === "ytd") from = last.slice(0, 4) + "-01-01";
    else {
      const months = (RANGES.find(r => r[0] === key) || [0, 12])[1];
      let y = +last.slice(0, 4), m = +last.slice(5, 7) - months;
      while (m < 1) { m += 12; y -= 1; }
      const dim = new Date(Date.UTC(y, m, 0)).getUTCDate(), dd = Math.min(+last.slice(8, 10), dim);
      from = `${y}-${String(m).padStart(2, "0")}-${String(dd).padStart(2, "0")}`;
      from = from.slice(0, 8) + String(+from.slice(8) + 1).padStart(2, "0");   // the day after: "1M" = 21-23 bars
    }
    let i0 = daily.findIndex(b => b.d >= from);
    if (i0 < 0) i0 = N - 1;
    return { i0: Math.max(0, Math.min(i0, N - Math.min(N, MIN_VIEW))), i1: N - 1 };
  }

  // ------------------------------------------------------------------ time axis (shared by the three charts)
  // returns [grid lines, labels] for the bars at x = cx(i) inside a plot of width PW
  function timeAxis(bars, cx, PW, top, bottom, tf) {
    // time axis: month starts every `mstep` months (1, 2, 3, 6 or 12, whatever fits), so January
    // (labelled with the year) is always a tick; without a January tick the first label carries the year.
    let back = "", axis = "", lastRight = -1e9;
    const n = bars.length;
    const zh = I18N.lang() === "zh", MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const short = tf === "daily" && n <= 90;
    const dayNo = s => Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)) / 864e5;
    const newWeek = i => { const a = dayNo(bars[i - 1].d), b = dayNo(bars[i].d); return b - a >= 7 || (b + 3) % 7 < (a + 3) % 7; };
    const starts = [];
    for (let i = 1; i < n; i++) if (short ? newWeek(i) : bars[i].d.slice(0, 7) !== bars[i - 1].d.slice(0, 7)) starts.push(i);
    const perMonth = starts.length > 1 ? (cx(starts[starts.length - 1]) - cx(starts[0])) / (starts.length - 1) : PW;
    const mstep = short ? 1 : [1, 2, 3, 6, 12].find(s => s * perMonth >= measure(zh ? "12月" : "May", 11) + 16) ?? 12;
    const yearTick = starts.some(i => bars[i].d.slice(5, 7) === "01");
    for (const i of starts) {
      const yv = bars[i].d.slice(0, 4), m = +bars[i].d.slice(5, 7);
      if (short) {
        // week ticks: month/day, with the year at the first tick of a year
        const md = zh ? `${m}/${+bars[i].d.slice(8, 10)}` : `${MON[m - 1]} ${+bars[i].d.slice(8, 10)}`;
        const label = lastRight < 0 || bars[i].d.slice(0, 4) !== bars[i - 1].d.slice(0, 4) ? `${md} ${yv}` : md;
        const x = cx(i), w = measure(label, 11), lx = Math.max(x, w / 2);
        if (lx - w / 2 < lastRight + 10 || lx + w / 2 > PW) continue;
        lastRight = lx + w / 2;
        const xx = Math.round(x) + 0.5;
        back += `<line x1="${xx}" x2="${xx}" y1="${top}" y2="${bottom}" stroke="var(--grid)"/>`;
        axis += `<text x="${lx}" y="${bottom + 15}" text-anchor="middle">${esc(label)}</text>`;
        continue;
      }
      if ((m - 1) % mstep) continue;
      const withYear = !yearTick && lastRight < 0;
      const label = m === 1 ? yv : withYear ? (zh ? `${yv}年${m}月` : `${MON[m - 1]} ${yv}`) : (zh ? `${m}月` : MON[m - 1]);
      const x = cx(i), w = measure(label, 11), lx = Math.max(x, w / 2);
      if (lx - w / 2 < lastRight + 8 || lx + w / 2 > PW) continue;
      lastRight = lx + w / 2;
      const xx = Math.round(x) + 0.5;
      back += `<line x1="${xx}" x2="${xx}" y1="${top}" y2="${bottom}" stroke="var(--grid)"/>`;
      axis += `<text x="${lx}" y="${bottom + 15}" text-anchor="middle">${esc(label)}</text>`;
    }
    return [back, axis];
  }

  // ------------------------------------------------------------------ chart drawing
  function draw(st, W) {
    const d = st.data, prep = st.prep, vis = st.vis, on = k => vis[k] !== false;
    const box = st.box;
    if (!prep.daily.length) { box.innerHTML = `<p class="ta-small">${esc(t("no_data"))}</p>`; return; }
    const vp = d.volume_profile || {};
    const vpOn = on("vp") && vp.available && (vp.bins || []).length > 0;
    const levels = [];
    (d.levels?.resistances || []).forEach((x, i) => levels.push({ ...x, tag: "R" + (i + 1), side: "r" }));
    (d.levels?.supports || []).forEach((x, i) => levels.push({ ...x, tag: "S" + (i + 1), side: "s" }));
    const shown = levels.filter(x => on(x.side === "s" ? "supports" : "resistances"));
    const tagText = x => W < 560 ? x.tag : `${x.tag} ${fmtPx(x.price)}`;
    // axis text uses tabular figures; the canvas measures proportional ones, so measure every digit as 0
    const tabW = s => measure(String(s).replace(/\d/g, "0"), 11, 650);
    const tagW = Math.max(0, ...shown.map(x => tabW(tagText(x) + " ↑") + 12));
    const chipW = tabW(prep.widestClose) + 12;   // last-close chip
    const AX = Math.max(W < 420 ? 48 : 58, Math.ceil(tagW), Math.ceil(chipW)), VPW = vpOn ? (W >= 900 ? 96 : W >= 560 ? 68 : 40) : 0, GAP = vpOn ? 4 : 8;
    const PW = Math.max(80, W - AX - VPW - GAP), XV = PW + GAP, XA = XV + VPW + 6;

    const view = currentView(st), vDays = view.i1 - view.i0 + 1;
    const tooNarrow = PW / vDays < MIN_PX_PER_DAY;
    const weeklyDefault = d.default_timeframe === "weekly" && vDays > WEEKLY_MIN_DAYS;
    const tf = prep.weekly.length && (st.userTf || (weeklyDefault || tooNarrow ? "weekly" : "daily")) === "weekly" ? "weekly" : "daily";
    st.tf = tf;
    st.auto = !st.userTf && tf === "weekly" && d.default_timeframe !== "weekly";
    st.root.querySelectorAll(".ta-tf button").forEach(b => b.classList.toggle("on", b.dataset.tf === tf));
    st.root.querySelectorAll(".ta-range button").forEach(b => {
      const r = rangeView(prep.daily, b.dataset.r);
      b.classList.toggle("on", r.i0 === view.i0 && r.i1 === view.i1);
    });
    const tfNote = st.root.querySelector(".ta-tfnote");
    tfNote.textContent = st.auto ? `(${t("auto")})` : "";
    tfNote.title = st.auto ? t("auto_weekly") : "";
    st.root.querySelector(".ta-wknote").hidden = tf !== "weekly";

    const d0 = prep.daily[view.i0].d, d1 = prep.daily[view.i1].d;
    const bars = tf === "weekly" ? prep.weekly.filter(w => w.d >= d0 && (w.start || w.d) <= d1) : prep.daily.slice(view.i0, view.i1 + 1);
    const n = bars.length, slot = PW / n;
    st.geom = { PW, vDays };
    const cx = i => slot * (i + 0.5);
    const bw = Math.max(1, Math.min(13, Math.round(slot * 0.7)));
    const bx = i => Math.round(cx(i) - bw / 2), wx = i => bx(i) + Math.floor(bw / 2) + 0.5;
    const S = prep.series;
    const lines = [
      ...SMA_KEYS.filter(on).map(k => ({ vals: S[k], color: `var(--${k})`, dash: "", w: 1.6 })),
      ...prep.avwaps.filter(a => on(a.key)).map(a => ({ vals: a.vals, color: `var(--av-${a.name}, var(--muted))`, dash: "6 4", w: 1.3 })),
    ];

    // ---- main price scale: candles, plus visible lines / levels that sit reasonably close
    const viewH = global.innerHeight || 900, HT = 18, HS = W < 560 ? 54 : 68, LANE = 18;
    const fsEl = global.document.fullscreenElement, fs = fsEl?.contains(box);
    // full screen: the price panel takes the screen height (the sub-panels scroll into view below it);
    // the box's offset is taken inside the scrolled full-screen card, so scrolling does not change it
    const boxTop = fs ? box.getBoundingClientRect().top - fsEl.getBoundingClientRect().top + fsEl.scrollTop : 0;
    const top = 8, HM = fs ? Math.round(Math.max(Math.min(W * 0.7, viewH - boxTop - top - 120),
                                                 Math.min(280, Math.max(160, viewH - boxTop - top - LANE - 30))))
      : Math.round(Math.max(280, Math.min(W * 0.5, viewH * 0.66, 760)));
    let lo0 = Infinity, hi0 = -Infinity;
    for (const b of bars) { if (b.l < lo0) lo0 = b.l; if (b.h > hi0) hi0 = b.h; }
    const span0 = hi0 - lo0 || Math.abs(hi0) * 0.02 || 1;
    let lo = lo0, hi = hi0;
    const take = (v, lim) => { if (isNum(v) && v > lo0 - lim * span0 && v < hi0 + lim * span0) { lo = Math.min(lo, v); hi = Math.max(hi, v); } };
    for (const ln of lines) for (const b of bars) take(ln.vals[b.di], 0.5);
    shown.forEach(x => take(x.price, 0.25));
    const pad = (hi - lo) * 0.05 || 1;
    lo -= pad; hi += pad;
    // headroom: the levels above the last close stack upward from its chip, those below it downward,
    // so each side needs room for its tags between the chip and the panel edge
    const last = bars[n - 1], LH = 17, CH = 18, EDGE = 8;
    const isAbove = x => x.price > last.c || (x.price === last.c && x.side === "r");
    const nUp = shown.filter(isAbove).length, room = k => Math.min(0.4, (k ? EDGE + CH + (k - 1) * LH : 10) / HM);
    {
      const a = room(nUp), b = room(shown.length - nUp), u = hi - last.c, v = last.c - lo;
      hi = last.c + Math.max(u, a * v / (1 - a));
      lo = last.c - Math.max(v, b * u / (1 - b));
    }
    const y = v => top + (hi - v) / (hi - lo) * HM;
    const inMain = v => v >= lo && v <= hi;

    const id = st.id, clip = `url(#${id}-clip)`;
    let back = "", zones = "", g = "", fore = "";

    // grid and price axis
    const lastY = y(last.c);
    const tickSet = k => {
      const ts = niceTicks(lo, hi, k), step = ts.length > 1 ? ts[1] - ts[0] : 1;
      return ts.map(v => [Math.round(y(v)) + 0.5, tickLabel(v, step)]).filter(([yy]) => yy >= top && yy <= top + HM);
    };

    // volume profile sidebar (shares the price scale): value-area shading, bins, POC
    if (vpOn) {
      const vmax = Math.max(...vp.bins.map(b => b[2])) || 1;
      if (isNum(vp.val) && isNum(vp.vah)) {
        const y1 = Math.max(top, y(vp.vah)), y2 = Math.min(top + HM, y(vp.val));
        if (y2 > y1) back += `<rect x="${XV}" y="${y1}" width="${VPW}" height="${y2 - y1}" fill="var(--band)"/>`;
      }
      for (const [blo, bhi, vol] of vp.bins) {
        const y1 = y(bhi), y2 = y(blo);
        if (y2 < top || y1 > top + HM) continue;
        const h = Math.max(1, y2 - y1 - (y2 - y1 > 3 ? 1 : 0)), w = Math.max(vol > 0 ? 1 : 0, vol / vmax * (VPW - 2));
        const inVA = isNum(vp.val) && isNum(vp.vah) && blo >= vp.val - 1e-9 && bhi <= vp.vah + 1e-9;
        g += `<rect x="${XV}" y="${y1.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" fill="var(${inVA ? "--va" : "--vp"})"/>`;
      }
      if (isNum(vp.poc) && inMain(vp.poc)) {
        const yp = Math.round(y(vp.poc)) + 0.5;
        back += `<line x1="0" x2="${PW}" y1="${yp}" y2="${yp}" stroke="var(--muted)" stroke-dasharray="1 3" opacity=".8"/>`;
        fore += `<line x1="${XV}" x2="${XV + VPW}" y1="${yp}" y2="${yp}" stroke="var(--text)" stroke-width="1.5"/>` +
          `<text x="${XV + VPW - 2}" y="${yp - 4}" text-anchor="end" class="pt halo">POC</text>`;
      }
    }

    // support / resistance zones: light green / light red bands with a line at the level price, drawn
    // under the moving averages and the candles. Their tags sit on the price axis, never over a candle.
    const tags = [];
    for (const x of shown) {
      const col = x.side === "s" ? "--up" : "--down";
      if (inMain(x.price)) {
        const y1 = y(Math.max(x.zone_high, x.price)), y2 = y(Math.min(x.zone_low, x.price));
        const h = Math.max(4, y2 - y1), yt = (y1 + y2 - h) / 2, yl = Math.round(y(x.price)) + 0.5;
        zones += `<rect x="0" y="${yt.toFixed(1)}" width="${PW}" height="${h.toFixed(1)}" fill="var(${col}-zone)"/>` +
          (h > 6 ? `<rect x="0.5" y="${(yt + 0.5).toFixed(1)}" width="${PW - 1}" height="${(h - 1).toFixed(1)}" fill="none" stroke="var(${col}-edge)"/>` : "") +
          `<line x1="0" x2="${PW}" y1="${yl}" y2="${yl}" stroke="var(${col})" stroke-width="1" opacity=".7"/>`;
        tags.push({ y: y(x.price), at: y(x.price), up: isAbove(x), price: x.price, text: tagText(x), cls: x.side, title: `${x.tag} ${fmtPx(x.price)}` });
      } else {
        const above = x.price > hi;
        tags.push({ y: above ? top + EDGE : top + HM - EDGE, at: null, up: above, price: x.price, text: `${tagText(x)} ${above ? "↑" : "↓"}`, cls: x.side, title: `${x.tag} ${fmtPx(x.price)}` });
      }
    }
    // spread the tags on each side of the last-close chip (which stays put): at least CH from the chip,
    // LH apart, and inside the panel; the headroom above makes the room
    const spread = (list, dir, edge) => {
      const far = (edge - lastY) * dir, s = list.length > 1 ? Math.min(LH, Math.max(0, far - CH) / (list.length - 1)) : LH;
      list.forEach(l => { l.dist = (l.y - lastY) * dir; });
      list.sort((p, q) => Math.abs(p.price - last.c) - Math.abs(q.price - last.c));   // nearest the close first
      list.forEach((l, i) => { l.dist = Math.max(l.dist, i ? list[i - 1].dist + s : CH); });
      for (let i = list.length - 1; i >= 0; i--) list[i].dist = Math.min(list[i].dist, i === list.length - 1 ? far : list[i + 1].dist - s);
      list.forEach(l => { l.y = lastY + dir * l.dist; });
    };
    spread(tags.filter(l => l.up), -1, top + EDGE);
    spread(tags.filter(l => !l.up), 1, top + HM - EDGE);
    for (const l of tags) {
      const c = l.cls === "s" ? "--up" : "--down";
      // a thin line carries the level across the volume-profile sidebar to its tag
      if (l.at != null) {
        fore += `<path d="M${PW},${l.at.toFixed(1)}H${XA - 10}L${XA - 4},${l.y.toFixed(1)}" fill="none" stroke="var(${c})" stroke-width="1" opacity=".55"/>`;
      }
      fore += `<g><title>${esc(l.title)}</title><rect x="${XA - 4}" y="${(l.y - 8).toFixed(1)}" width="${AX - 4}" height="16" rx="3" fill="var(--card)" stroke="var(${c})"/>` +
        `<text class="lvl ${l.cls}" x="${XA}" y="${(l.y + 4).toFixed(1)}">${esc(l.text)}</text></g>`;
    }
    // price ticks: their labels give way to the tags and the chip; when fewer than two would be left
    // (narrow charts, where tags carry no prices), a denser tick set keeps the scale readable
    const busy = [lastY, ...tags.map(l => l.y)], free = ts => ts.filter(([yy]) => busy.every(b => Math.abs(yy - b) > 12));
    const k0 = HM > 480 ? 9 : HM > 320 ? 7 : 5;
    let ticks = tickSet(k0);
    for (let k = k0 + 2; free(ticks).length < 2 && k <= k0 + 10; k += 2) ticks = tickSet(k);
    back = ticks.map(([yy]) => `<line x1="0" x2="${XV + VPW}" y1="${yy}" y2="${yy}" stroke="var(--grid)"/>`).join("") + back;
    for (const [yy, label] of free(ticks)) back += `<text x="${XA}" y="${yy + 4}">${label}</text>`;

    // earnings: dotted lines in the price panel and E badges on a lane under it (time axis)
    const laneY = top + HM + LANE / 2 + 2;
    const viewIndex = date => { let a = 0, b = n - 1; if (!n || date > bars[b].d || date < (bars[0].start || bars[0].d)) return -1; while (a < b) { const m = (a + b) >> 1; if (bars[m].d < date) a = m + 1; else b = m; } return a; };
    let badges = "";
    const pastX = [];
    if (on("earnings")) {
      for (const e of d.earnings_dates || []) {
        const tip = e.kind === "next" ? t("e_next", e.date) + (isNum(e.calendar_days) ? " · " + t("days_away", e.calendar_days, e.trading_days_est) : "") : t("e_past", e.date);
        if (e.kind === "past") {
          const i = viewIndex(e.bar_date || e.date);
          if (i < 0) continue;
          const xx = Math.round(cx(i)) + 0.5;
          back += `<line x1="${xx}" x2="${xx}" y1="${top}" y2="${top + HM}" stroke="var(--faint)" stroke-dasharray="2 3" opacity=".7"/>`;
          pastX.push(xx);
          badges += `<g><title>${esc(tip)}</title><circle cx="${xx}" cy="${laneY}" r="7" fill="var(--card)" stroke="var(--text)"/><text class="e" x="${xx}" y="${laneY + 3.5}" text-anchor="middle">E</text></g>`;
        } else {
          const xx = PW - 8, label = "→" + e.date.slice(5), left = xx - 11 - measure(label, 10, 700);
          const fits = pastX.every(px => px + 9 < left || px - 9 > xx + 8);
          badges += `<g><title>${esc(tip)}</title>${fits ? `<text class="e" x="${xx - 11}" y="${laneY + 3.5}" text-anchor="end">${esc(label)}</text>` : ""}` +
            `<circle cx="${xx}" cy="${laneY}" r="7" fill="var(--card)" stroke="var(--text)" stroke-dasharray="2 2"/><text class="e" x="${xx}" y="${laneY + 3.5}" text-anchor="middle">E</text></g>`;
        }
      }
    }

    // candles
    let candles = "";
    bars.forEach((b, i) => {
      const col = b.c >= b.o ? "var(--up)" : "var(--down)", yo = y(b.o), yc = y(b.c);
      candles += `<line x1="${wx(i)}" x2="${wx(i)}" y1="${y(b.h).toFixed(1)}" y2="${y(b.l).toFixed(1)}" stroke="${col}"/>` +
        `<rect x="${bx(i)}" y="${Math.min(yo, yc).toFixed(1)}" width="${bw}" height="${Math.max(1, Math.abs(yo - yc)).toFixed(1)}" fill="${col}"/>`;
    });

    const path = (vals, yf) => {
      let p = "", pen = false;
      bars.forEach((b, i) => {
        const v = vals[b.di];
        if (!isNum(v)) { pen = false; return; }
        p += (pen ? "L" : "M") + cx(i).toFixed(1) + "," + yf(v).toFixed(1);
        pen = true;
      });
      return p;
    };
    let lineSvg = "";
    for (const ln of lines) {
      const p = path(ln.vals, y);
      if (p) lineSvg += `<path d="${p}" fill="none" stroke="${ln.color}" stroke-width="${ln.w}" stroke-linejoin="round"${ln.dash ? ` stroke-dasharray="${ln.dash}"` : ""}/>`;
    }

    // last close tag on the price axis
    const lastCol = n > 1 && last.c < bars[n - 2].c ? "var(--down)" : "var(--up)";
    fore += `<rect x="${XA - 4}" y="${lastY - 9}" width="${AX - 4}" height="18" rx="4" fill="${lastCol}"/>` +
      `<text class="chip" x="${XA}" y="${lastY + 4}">${esc(fmtPx(last.c))}</text>`;

    // ---- sub-panels: volume, RSI(14), RS vs SPY
    let cur = top + HM + LANE + 8;
    const panels = [];
    const addPanel = key => { const p = { key, ty: cur + 12, y0: cur + HT, h: HS }; cur = p.y0 + HS + 8; panels.push(p); return p; };
    let sub = "";

    const pv = addPanel("volume");
    const vmax = Math.max(0, ...bars.map(b => b.v || 0));
    bars.forEach((b, i) => {
      if (!b.v) return;
      const vh = Math.max(1, b.v / vmax * pv.h);
      sub += `<rect x="${bx(i)}" y="${(pv.y0 + pv.h - vh).toFixed(1)}" width="${bw}" height="${vh.toFixed(1)}" fill="var(${b.c >= b.o ? "--up" : "--down"})" opacity=".55"/>`;
    });
    if (vmax > 0) sub += `<text x="${XA}" y="${pv.y0 + 9}">${esc(fmtVol(vmax))}</text>`;

    const pr = addPanel("rsi");
    const yR = v => pr.y0 + (100 - v) / 100 * pr.h;
    sub += `<rect x="0" y="${yR(70)}" width="${PW}" height="${yR(30) - yR(70)}" fill="var(--band)"/>`;
    for (const v of [30, 70]) {
      sub += `<line x1="0" x2="${PW}" y1="${Math.round(yR(v)) + 0.5}" y2="${Math.round(yR(v)) + 0.5}" stroke="var(--faint)" stroke-dasharray="3 3"/>` +
        `<text x="${XA}" y="${yR(v) + 4}">${v}</text>`;
    }
    const rsiPath = path(S.rsi, yR);
    if (rsiPath) sub += `<path d="${rsiPath}" fill="none" stroke="var(--line)" stroke-width="1.4"/>`;

    if (S.rs) {
      const ps = addPanel("rs");
      const vals = bars.map(b => S.rs[b.di]).filter(isNum);
      if (vals.length) {
        let rlo = Math.min(...vals), rhi = Math.max(...vals);
        const rp = (rhi - rlo) * 0.08 || 1;
        rlo -= rp; rhi += rp;
        const yS = v => ps.y0 + (rhi - v) / (rhi - rlo) * ps.h;
        const rt = niceTicks(rlo, rhi, 3), rstep = rt.length > 1 ? rt[1] - rt[0] : 1;
        for (const v of rt) {
          const yy = Math.round(yS(v)) + 0.5;
          if (yy < ps.y0 + 4 || yy > ps.y0 + ps.h - 2) continue;
          sub += `<line x1="0" x2="${PW}" y1="${yy}" y2="${yy}" stroke="${v === 100 ? "var(--faint)" : "var(--grid)"}"/><text x="${XA}" y="${yy + 4}">${tickLabel(v, rstep)}</text>`;
        }
        sub += `<path d="${path(S.rs, yS)}" fill="none" stroke="var(--line)" stroke-width="1.4"/>`;
      }
    }
    for (const p of panels) {
      back += `<rect x="0" y="${p.y0}" width="${PW}" height="${p.h}" fill="none" stroke="var(--grid)"/>`;
      sub += `<text x="0" y="${p.ty}"><tspan class="pt">${esc(t("p_" + p.key, d.rs_spy?.[0]?.[0] ?? ""))}</tspan><tspan dx="8" class="pv" data-p="${p.key}"></tspan></text>`;
    }
    const bottom = cur - 8;

    const [timeGrid, axis] = timeAxis(bars, cx, PW, top, bottom, tf);
    back += timeGrid;
    const H = bottom + 22;

    const hadFocus = box.contains(global.document.activeElement), keep = { di: st.hoverDi, y: st.hoverY };
    box.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" tabindex="0" aria-label="${esc(t("chart_aria"))}">
      <defs><clipPath id="${id}-clip"><rect x="0" y="${top}" width="${PW}" height="${HM}"/></clipPath></defs>
      ${back}<line x1="0" x2="${PW}" y1="${top + HM + 0.5}" y2="${top + HM + 0.5}" stroke="var(--border)"/>
      <g clip-path="${clip}">${zones}</g><g clip-path="${clip}">${lineSvg}</g><g clip-path="${clip}" shape-rendering="crispEdges">${candles}</g>${g}${sub}${axis}${fore}
      <line class="xh xv" x1="0" x2="0" y1="${top}" y2="${bottom}" visibility="hidden"/>
      <line class="xh xy" x1="0" x2="${PW}" y1="0" y2="0" visibility="hidden"/>
      <g class="xp" visibility="hidden"><rect x="${XA - 4}" y="-9" width="${AX - 4}" height="18" rx="4" fill="var(--text)"/><text x="${XA}" y="4" style="fill:var(--card)"></text></g>
      <rect class="hit" x="0" y="${top}" width="${PW}" height="${bottom - top}" fill="transparent"/>${badges}</svg>`;

    // ---- hover: crosshair, readout, per-panel values; arrow keys move it
    const svg = box.querySelector("svg"), xv = svg.querySelector(".xv"), xy = svg.querySelector(".xy"), xp = svg.querySelector(".xp");
    const readout = st.root.querySelector(".ta-readout");
    const pvals = {};
    svg.querySelectorAll(".pv").forEach(el => { pvals[el.dataset.p] = el; });
    const show = i => {
      const b = bars[i], prev = i ? bars[i - 1].c : null, di = b.di;
      const when = tf === "weekly" ? esc(t("week_of", b.start, b.d)) + (b.partial ? esc(t("partial_week")) : "") : esc(b.d);
      const chg = isNum(prev) ? ` <span class="${pctCls(b.c - prev)}">${esc(fmtPct(b.c / prev - 1, 2, true))}</span>` : "";
      const sw = k => `<span class="sw" style="background:var(--${k})"></span>`;
      readout.innerHTML = `<span><b>${when}</b></span>` +
        ["o", "h", "l", "c"].map(k => `<span>${esc(t(k))} <b>${esc(fmtPx(b[k]))}</b>${k === "c" ? chg : ""}</span>`).join("") +
        `<span>${esc(t("v"))} <b>${esc(fmtVol(b.v))}</b></span>` +
        SMA_KEYS.map(k => `<span>${sw(k)}${k.toUpperCase()} <b>${esc(fmtPx(S[k][di]))}</b></span>`).join("") +
        `<span>RSI <b>${esc(fmtNum(S.rsi[di], 1))}</b></span><span>RVOL <b>${esc(fmtNum(S.rvol[di], 2))}</b></span>` +
        (S.rs ? `<span>RS <b>${esc(fmtNum(S.rs[di], 1))}</b></span>` : "");
      if (pvals.volume) pvals.volume.textContent = `${fmtVol(b.v)} · RVOL ${fmtNum(S.rvol[di], 2)}`;
      if (pvals.rsi) pvals.rsi.textContent = fmtNum(S.rsi[di], 1);
      if (pvals.rs) pvals.rs.textContent = fmtNum(S.rs?.[di], 1);
    };
    const point = (i, py) => {
      st.hover = Math.max(0, Math.min(n - 1, i));
      st.hoverDi = bars[st.hover].di; st.hoverY = py;
      const xx = wx(st.hover);
      xv.setAttribute("x1", xx); xv.setAttribute("x2", xx); xv.setAttribute("visibility", "visible");
      const inPrice = py != null && py >= top && py <= top + HM;
      xy.setAttribute("visibility", inPrice ? "visible" : "hidden");
      xp.setAttribute("visibility", inPrice ? "visible" : "hidden");
      if (inPrice) {
        xy.setAttribute("y1", py); xy.setAttribute("y2", py);
        xp.setAttribute("transform", `translate(0,${py})`);
        xp.querySelector("text").textContent = fmtPx(hi - (py - top) / HM * (hi - lo));
      }
      show(st.hover);
    };
    const leave = () => {
      st.hover = st.hoverDi = st.hoverY = null;
      [xv, xy, xp].forEach(el => el.setAttribute("visibility", "hidden"));
      show(n - 1);
    };
    const fromEvent = e => {
      const r = svg.getBoundingClientRect();
      point(Math.floor((e.clientX - r.left) / slot), e.clientY - r.top);
    };
    const hit = svg.querySelector(".hit");
    hit.addEventListener("pointermove", fromEvent);
    hit.addEventListener("pointerdown", fromEvent);
    hit.addEventListener("pointerleave", leave);
    svg.addEventListener("blur", leave);
    svg.addEventListener("keydown", e => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;   // browser shortcuts: page zoom, history
      if (e.key === "Escape") { leave(); return; }
      if (e.key === "+" || e.key === "=" || e.key === "-" || e.key === "_") { e.preventDefault(); st.zoom(e.key === "+" || e.key === "=" ? 1 / 1.5 : 1.5); return; }
      const step = { ArrowLeft: -1, ArrowRight: 1, PageUp: -10, PageDown: 10, Home: -n, End: n }[e.key];
      if (step == null) return;
      e.preventDefault();
      point((st.hover ?? n - 1) + step, null);
    });
    // after a zoom, pan or toggle the crosshair stays on the same bar (and price height) if it is still in view
    const at = keep.di != null && keep.di >= view.i0 && keep.di <= view.i1 ? bars.findIndex(b => b.di >= keep.di) : -1;
    if (at >= 0) point(at, keep.y); else { st.hover = st.hoverDi = st.hoverY = null; show(n - 1); }
    if (hadFocus) svg.focus({ preventScroll: true });
  }

  // ------------------------------------------------------------------ charts 2 and 3: price action, candlesticks
  // Daily candles over the same window as the main chart (panning or zooming any chart moves all three).
  // Chart 2 colours each bar by its Brooks bar type and marks the setups of scripts/price_action.py;
  // chart 3 marks the patterns of scripts/candlesticks.py. The side panel shows each library's statistics.
  const PA_STATE_COLORS = { 2: "var(--up)", 1: "var(--up-zone)", 0: "var(--tr-strip)", "-1": "var(--down-zone)", "-2": "var(--down)" };

  function patternLegend(kind, P) {
    if (kind === "pa") {
      return [
        { key: "pa_ema", label: t("lg_ema", P.ema_period ?? 20), color: "var(--ema)", sw: "" },
        { key: "pa_counts", label: t("lg_pa_counts"), color: "var(--text)", sw: "box" },
        { key: "pa_wedges", label: t("lg_pa_wedges"), color: "var(--wedge)", sw: "" },
        { key: "pa_ranges", label: t("lg_pa_ranges"), color: "var(--faint)", fill: "var(--tr-zone)", sw: "box" },
        { key: "pa_fb", label: t("lg_pa_fb"), color: "var(--text)", sw: "box" },
        { key: "pa_g20", label: t("lg_pa_g20"), color: "var(--text)", sw: "box" },
        { key: "pa_mm", label: t("lg_pa_mm"), color: "var(--muted)", sw: "dash" },
      ];
    }
    return [
      { key: "cs_bull", label: t("lg_cs_bull"), color: "var(--up)", sw: "box" },
      { key: "cs_bear", label: t("lg_cs_bear"), color: "var(--down)", sw: "box" },
      { key: "cs_doji", label: t("lg_cs_doji"), color: "var(--faint)", sw: "box" },
    ];
  }

  function libraryTable(rows) {
    if (!rows || !rows.length) return "";
    const h = rows[0].horizon;
    const body = rows.map(r => {
      const arrow = r.bias > 0 ? `<span class="pos" aria-hidden="true">▲</span> ` : r.bias < 0 ? `<span class="neg" aria-hidden="true">▼</span> ` : "";
      return `<tr class="${r.confidence === "insufficient" ? "dim" : ""}"><td class="l wrap">${arrow}${esc(I18N.signalName(r.id, r.name))}
          <div class="ta-sub">${esc(r.last_fired ? t("last_seen", r.last_fired) : t("never_seen"))}</div></td>
        <td>${esc(r.n ?? "–")}</td><td>${esc(fmtPct(r.hit_rate, 0))}</td>
        <td class="${pctCls(r.median)}">${esc(fmtPct(r.median, 1, true))}</td>
        <td class="${pctCls(r.edge_median)}">${esc(fmtPct(r.edge_median, 1, true))}<br><span class="ta-sub">${esc(fmtPP(r.edge_hit_rate))}</span></td></tr>`;
    }).join("");
    return `<details class="ta-lib"><summary>${esc(t("lib_title", h))}</summary>
      <div class="ta-tablewrap"><table class="ta-table ta-sig"><thead><tr><th>${esc(t("th_pattern"))}</th>
      <th>${esc(t("th_n"))}</th><th>${esc(t("th_hit"))}</th><th>${esc(t("th_med"))}</th><th>${esc(t("th_edge_all"))}</th></tr></thead>
      <tbody>${body}</tbody></table></div><p class="ta-small">${esc(t("lib_note"))}</p></details>`;
  }

  function paSideHtml(P) {
    const s = P.summary || {}, st = s.state || {}, tr = s.trading_range || {};
    const ago = x => x ? `${x.label ?? ""} · ${t("bars_ago", x.bars_ago)} (${String(x.date || x.confirmed_date || "").slice(5)})` : "–";
    const mm = (P.measured_moves || []).map(m => {
      const arrow = m.side === "up" ? "↑" : "↓";
      const dist = `${fmtPct(m.distance_pct, 1, true)} · ${fmtSigned(m.distance_atr, 1)} ATR`;
      return `<li class="${m.reached ? "dim" : ""}">${esc(t("mm_" + m.kind))} ${arrow} <b>${esc(fmtPx(m.target))}</b> <span class="ta-sub">${esc(dist)}${m.reached ? " · " + esc(t("mm_reached")) : ""}</span></li>`;
    }).join("");
    const wedge = s.last_wedge ? `${tv("wedge_", s.last_wedge.side)} · ${t("bars_ago", s.last_wedge.bars_ago)}` : "–";
    const fb = s.last_failed_breakout ? `${tv("fb_", s.last_failed_breakout.side)} ${fmtPx(s.last_failed_breakout.level)} · ${t("bars_ago", s.last_failed_breakout.bars_ago)}` : "–";
    const gaps = s.gap_bars || {};
    const gap = gaps.bull ? t("gap_bull", gaps.bull) : gaps.bear ? t("gap_bear", gaps.bear) : "–";
    const kv = `<p class="ta-regime">${esc(tv("state_", st.label))}</p>
      <dl class="ta-kv">
        <dt>${esc(t("pa_since"))}</dt><dd>${esc(st.since ?? "–")} · ${esc(t("n_bars", st.bars ?? 0))}</dd>
        <dt>${esc(t("pa_ai"))}</dt><dd>${esc(tv("ai_", s.always_in?.direction))}${s.always_in?.since ? ` · ${esc(t("since", s.always_in.since))}` : ""}</dd>
        <dt>EMA${esc(P.ema_period)}</dt><dd>${esc(fmtPx(s.ema))} · ${esc(tv("slope_", s.ema_slope))} · ${esc(t("vs_ema", fmtSigned(s.close_vs_ema_atr, 1)))}</dd>
        <dt>${esc(t("pa_bar"))}</dt><dd>${esc(tv("bt_", s.bar?.type))}</dd>
        <dt>${esc(t("pa_range"))}</dt><dd>${tr.active ? `${esc(fmtPx(tr.low))}–${esc(fmtPx(tr.high))} · ${esc(fmtNum(tr.height_atr, 1))} ATR` : esc(t("none"))}</dd>
        <dt>${esc(t("pa_gap"))}</dt><dd>${esc(gap)}</dd>
        <dt>${esc(t("pa_hcount"))}</dt><dd>${esc(ago(s.last_high_count))}</dd>
        <dt>${esc(t("pa_lcount"))}</dt><dd>${esc(ago(s.last_low_count))}</dd>
        <dt>${esc(t("pa_wedge"))}</dt><dd>${esc(wedge)}</dd>
        <dt>${esc(t("pa_fb"))}</dt><dd>${esc(fb)}</dd>
      </dl>${mm ? `<h4 class="ta-small" style="margin-top:10px">${esc(t("pa_mm"))}</h4><ul class="ta-list">${mm}</ul>` : ""}`;
    return kv + setupsHtml(P);
  }

  function csSideHtml(P) {
    const recent = (P.recent || []).map(r => `<li><span class="${r.bias > 0 ? "pos" : "neg"}" aria-hidden="true">${r.bias > 0 ? "▲" : "▼"}</span> ${esc(I18N.signalName(r.id, r.id))} <span class="ta-sub">${esc(t("bars_ago", r.bars_ago))} · ${esc(String(r.date).slice(5))}</span></li>`).join("");
    const doji = P.last_doji ? `<p class="ta-small">${esc(t("last_doji", P.last_doji.date, P.last_doji.bars_ago))}</p>` : "";
    return `${recent ? `<h4 class="ta-small" style="margin-top:0">${esc(t("cs_recent"))}</h4><ul class="ta-list plain">${recent}</ul>` : `<p class="ta-small">${esc(t("cs_none_recent"))}</p>`}${doji}` + setupsHtml(P);
  }

  function setupsHtml(P) {
    const active = P.active || [];
    return `<h4 class="ta-small" style="margin-top:12px">${esc(t("setups_active"))}</h4>` +
      (active.length ? activeTable(active, P.primary_horizon) + `<p class="ta-small">${esc(t("edge_note"))}</p>` : `<p class="ta-small">${esc(t("sig_none"))}</p>`) +
      libraryTable(P.library) + `<p class="ta-small">${esc(t("not_scored"))}</p>`;
  }

  function patternCardHtml(st, kind, d) {
    const P = kind === "pa" ? d.price_action : d.candlesticks;
    const title = t(kind === "pa" ? "t_pa" : "t_cs");
    if (!P || !P.available) return `<section class="ta-card ta-${kind}"><h3>${esc(title)}</h3><p class="ta-small">${esc(t("unavailable"))}: ${esc(P?.reason ?? "")}</p></section>`;
    const legend = patternLegend(kind, P).map(it =>
      `<button type="button" class="ta-chip" data-k="${esc(it.key)}" aria-pressed="${st.vis[it.key] !== false}">` +
      `<span class="sw ${it.sw}" style="color:${it.color}${it.fill ? ";background:" + it.fill : ""}"></span>${esc(it.label)}</button>`).join("");
    const strip = kind === "pa" ? `<div class="ta-pkey">${[2, 1, 0, -1, -2].map(c =>
      `<span><i style="background:${PA_STATE_COLORS[c]}"></i>${esc(tv("state_", { 2: "tight_bull_channel", 1: "bull_channel", 0: "trading_range", "-1": "bear_channel", "-2": "tight_bear_channel" }[c]))}</span>`).join("")}</div>` : "";
    return `<section class="ta-card ta-${kind}"><h3>${esc(title)}</h3>
      <div class="ta-pgrid"><div class="ta-pchart">
        <div class="ta-toolbar"><div class="ta-legend">${legend}</div></div>${strip}
        <div class="ta-readout"></div>
        <div class="ta-chartbox"></div>
        <p class="ta-note">${esc(t(kind === "pa" ? "pa_note" : "cs_note"))}</p>
      </div><div class="ta-pside">${kind === "pa" ? paSideHtml(P) : csSideHtml(P)}</div></div></section>`;
  }

  function drawPattern(st, kind) {
    const box = st.pboxes?.[kind], d = st.data, prep = st.prep, pp = prep[kind];
    if (!box || !pp || !prep.daily.length) return;
    const W = Math.floor(box.clientWidth);
    if (!W) return;
    const P = kind === "pa" ? d.price_action : d.candlesticks, on = k => st.vis[k] !== false;
    const view = currentView(st), bars = prep.daily.slice(view.i0, view.i1 + 1), n = bars.length;
    // the price axis is wide enough for its measured-move tags (tabular figures: measure every digit as 0)
    const tagW = kind === "pa" ? Math.max(0, ...pp.mms.map(m => measure("MM " + fmtPx(m.target).replace(/\d/g, "0"), 11, 650) + 12)) : 0;
    const AX = Math.ceil(Math.max(W < 420 ? 52 : 64, tagW)), PW = Math.max(80, W - AX - 8), XA = PW + 8;
    const slot = PW / n, cx = i => slot * (i + 0.5);
    const bw = Math.max(1, Math.min(13, Math.round(slot * 0.7)));
    const bx = i => Math.round(cx(i) - bw / 2), wx = i => bx(i) + Math.floor(bw / 2) + 0.5;
    st.geoms[kind] = { PW };
    const top = 8, HM = Math.round(Math.max(220, Math.min(W * 0.42, (global.innerHeight || 900) * 0.55, 520)));
    const inView = i => i != null && i >= view.i0 && i <= view.i1;

    // price scale: candles, plus the EMA and measured-move targets that sit reasonably close
    let lo0 = Infinity, hi0 = -Infinity;
    for (const b of bars) { if (b.l < lo0) lo0 = b.l; if (b.h > hi0) hi0 = b.h; }
    const span0 = hi0 - lo0 || Math.abs(hi0) * 0.02 || 1;
    let lo = lo0, hi = hi0;
    const take = (v, lim) => { if (isNum(v) && v > lo0 - lim * span0 && v < hi0 + lim * span0) { lo = Math.min(lo, v); hi = Math.max(hi, v); } };
    if (kind === "pa") {
      if (on("pa_ema")) for (const b of bars) take(pp.ema[b.di], 0.5);
      if (on("pa_mm")) for (const m of pp.mms) if (!m.reached) take(m.target, 0.3);
    }
    // room for the marker labels above the highs and below the lows
    const room = (hi - lo) * 34 / HM;
    hi += room + (hi - lo) * 0.03; lo -= room + (hi - lo) * 0.03;
    const y = v => top + (hi - v) / (hi - lo) * HM;

    let back = "", zones = "", lines = "", marks = "", fore = "";
    // price ticks; a label gives way to a measured-move tag on the axis
    const tagYs = kind === "pa" && on("pa_mm") ? pp.mms.filter(m => isNum(m.target) && m.target >= lo && m.target <= hi).map(m => y(m.target)) : [];
    const ts = niceTicks(lo, hi, HM > 360 ? 7 : 5), step = ts.length > 1 ? ts[1] - ts[0] : 1;
    for (const v of ts) {
      const yy = Math.round(y(v)) + 0.5;
      if (yy < top || yy > top + HM) continue;
      back += `<line x1="0" x2="${PW}" y1="${yy}" y2="${yy}" stroke="var(--grid)"/>`;
      if (tagYs.every(ty => Math.abs(ty - yy) > 12)) back += `<text x="${XA}" y="${yy + 4}">${tickLabel(v, step)}</text>`;
    }

    // candles: chart 2 by bar type (trend bars solid, weak bars pale, dojis grey); chart 3 pale, the pattern bars solid
    const csBars = new Set();
    if (kind === "cs") {
      for (const [i, ms] of pp.byBar) for (const m of ms) {
        if (!on(m.bias > 0 ? "cs_bull" : m.bias < 0 ? "cs_bear" : "cs_doji")) continue;
        for (let k = m.i0; k <= i; k++) csBars.add(k);
      }
    }
    let candles = "";
    bars.forEach((b, i) => {
      let col = b.c >= b.o ? "var(--up)" : "var(--down)", op = 1;
      if (kind === "pa") {
        const ty = pp.types[b.di];
        if (ty === 0) col = "var(--faint)";
        else if (Math.abs(ty) === 1) op = 0.45;
      } else if (!csBars.has(b.di)) op = 0.35;
      const yo = y(b.o), yc = y(b.c);
      candles += `<g opacity="${op}"><line x1="${wx(i)}" x2="${wx(i)}" y1="${y(b.h).toFixed(1)}" y2="${y(b.l).toFixed(1)}" stroke="${col}"/>` +
        `<rect x="${bx(i)}" y="${Math.min(yo, yc).toFixed(1)}" width="${bw}" height="${Math.max(1, Math.abs(yo - yc)).toFixed(1)}" fill="${col}"/></g>`;
    });

    // markers: stacked labels above a bar's high (bearish) or below its low (bullish)
    const stack = new Map(), labels = slot >= 7;
    const label = (i, up, text, cls, title, bold) => {
      const key = i + (up ? "u" : "d"), k = stack.get(key) || 0;
      stack.set(key, k + 1);
      const b = bars[i], yy = up ? y(b.h) - 6 - k * 12 : y(b.l) + 13 + k * 12;
      if (!labels) {
        const yt = up ? y(b.h) - 4 - k * 5 : y(b.l) + 4 + k * 5;
        marks += `<g><title>${esc(title)}</title><path d="M${cx(i).toFixed(1)},${yt.toFixed(1)}l-3,${up ? -4 : 4}h6z" class="mk ${cls}"/></g>`;
        return;
      }
      marks += `<g><title>${esc(title)}</title><text class="mk ${cls}${bold ? " b" : ""}" x="${cx(i).toFixed(1)}" y="${yy.toFixed(1)}" text-anchor="middle">${esc(text)}</text></g>`;
    };

    let strips = "", bottom = top + HM;
    if (kind === "pa") {
      // trading ranges: shaded boxes over the bars of each range
      if (on("pa_ranges")) for (const r of pp.ranges) {
        if (r.i1 < view.i0 || r.i0 > view.i1) continue;
        const x1 = Math.max(0, cx(Math.max(r.i0, view.i0) - view.i0) - slot / 2), x2 = Math.min(PW, cx(Math.min(r.i1, view.i1) - view.i0) + slot / 2);
        const y1 = y(r.high), y2 = y(r.low);
        zones += `<g><title>${esc(t("tr_title", fmtPx(r.low), fmtPx(r.high), r.start, r.end))}</title><rect x="${x1.toFixed(1)}" y="${y1.toFixed(1)}" width="${Math.max(1, x2 - x1).toFixed(1)}" height="${Math.max(1, y2 - y1).toFixed(1)}" fill="var(--tr-zone)" stroke="var(--faint)" stroke-dasharray="3 3"/></g>`;
      }
      if (on("pa_ema")) {
        let pth = "", pen = false;
        bars.forEach((b, i) => {
          const v = pp.ema[b.di];
          if (!isNum(v)) { pen = false; return; }
          pth += (pen ? "L" : "M") + cx(i).toFixed(1) + "," + y(v).toFixed(1);
          pen = true;
        });
        if (pth) lines += `<path d="${pth}" fill="none" stroke="var(--ema)" stroke-width="1.6"/>`;
      }
      if (on("pa_wedges")) for (const w of pp.wedges) {
        if (!w.pts.every(([i]) => inView(i))) continue;
        const col = w.side === "top" ? "var(--down)" : "var(--up)", pts = w.pts.map(([i, v]) => [cx(i - view.i0), y(v)]);
        lines += `<polyline points="${pts.map(([a, b]) => a.toFixed(1) + "," + b.toFixed(1)).join(" ")}" fill="none" stroke="var(--wedge)" stroke-width="1.4" stroke-dasharray="5 3"/>`;
        pts.forEach(([a, b], k) => {
          const yy = w.side === "top" ? b - 14 : b + 14;
          fore += `<g><title>${esc(t("wedge_title", tv("wedge_", w.side), k + 1, w.confirmed_date))}</title><circle cx="${a.toFixed(1)}" cy="${yy.toFixed(1)}" r="7" fill="var(--card)" stroke="${col}"/><text class="e" x="${a.toFixed(1)}" y="${(yy + 3.5).toFixed(1)}" text-anchor="middle">${k + 1}</text></g>`;
        });
      }
      for (let di = view.i0; di <= view.i1; di++) {
        for (const m of pp.byBar.get(di) || []) {
          const i = di - view.i0, up = m.side < 0;
          if (/^[HL]\d$/.test(m.kind)) {
            if (!on("pa_counts") || (!labels && !m.setup)) continue;
            label(i, up, m.kind, m.setup ? (up ? "neg" : "pos") : "muted", m.setup ? t(up ? "mk_l2" : "mk_h2") : t("mk_count", m.kind), m.setup);
          } else if (m.kind === "FB") {
            if (!on("pa_fb")) continue;
            label(i, up, "FB", up ? "neg" : "pos", t(up ? "mk_fb_up" : "mk_fb_down", fmtPx(m.level), m.breakout_date), true);
            if (isNum(m.level)) {
              const yl = Math.round(y(m.level)) + 0.5, b0 = Math.max(view.i0, prep.daily.findIndex(b => b.d === m.breakout_date));
              marks += `<line x1="${(cx(b0 - view.i0) - slot * 2).toFixed(1)}" x2="${(cx(i) + slot).toFixed(1)}" y1="${yl}" y2="${yl}" stroke="var(${up ? "--down" : "--up"})" stroke-dasharray="2 2"/>`;
            }
          } else if (m.kind === "G20") {
            if (!on("pa_g20")) continue;
            label(i, up, "20", up ? "neg" : "pos", t(up ? "mk_g20_bear" : "mk_g20_bull"), true);
          }
        }
      }
      // measured-move targets: dashed lines from the swing that set them, tags on the price axis
      if (on("pa_mm")) for (const m of pp.mms) {
        if (!isNum(m.target) || m.target < lo || m.target > hi) continue;
        const yy = Math.round(y(m.target)) + 0.5, x0 = m.i0 >= view.i0 ? cx(m.i0 - view.i0) : 0;
        const c = m.side === "up" ? "--up" : "--down";
        lines += `<line x1="${x0.toFixed(1)}" x2="${PW}" y1="${yy}" y2="${yy}" stroke="var(${c})" stroke-dasharray="6 4" opacity="${m.reached ? 0.35 : 0.9}"/>`;
        fore += `<g><title>${esc(t("mm_title", t("mm_" + m.kind), fmtPx(m.target), m.from_date))}</title><rect x="${XA - 4}" y="${yy - 8}" width="${AX - 4}" height="16" rx="3" fill="var(--card)" stroke="var(${c})"/>` +
          `<text class="lvl ${m.side === "up" ? "s" : "r"}" x="${XA}" y="${yy + 4}">MM ${esc(fmtPx(m.target))}</text></g>`;
      }
      // state and always-in strips under the price panel
      const sy = top + HM + 6, ay = sy + 11;
      bars.forEach((b, i) => {
        const sc = pp.states[b.di], ai = pp.ai[b.di], x = (slot * i).toFixed(2), w = (slot + 0.4).toFixed(2);
        if (sc != null) strips += `<rect x="${x}" y="${sy}" width="${w}" height="8" fill="${PA_STATE_COLORS[sc]}"/>`;
        strips += `<rect x="${x}" y="${ay}" width="${w}" height="5" fill="${ai > 0 ? "var(--up)" : ai < 0 ? "var(--down)" : "var(--faint)"}" opacity=".8"/>`;
      });
      strips += `<text x="${XA}" y="${sy + 8}" class="st">${esc(t("strip_state"))}</text><text x="${XA}" y="${ay + 6}" class="st">${esc(t("strip_ai"))}</text>`;
      bottom = ay + 8;
    } else {
      for (let di = view.i0; di <= view.i1; di++) {
        for (const m of pp.byBar.get(di) || []) {
          const key = m.bias > 0 ? "cs_bull" : m.bias < 0 ? "cs_bear" : "cs_doji";
          if (!on(key)) continue;
          const i = di - view.i0, up = m.bias < 0, cls = m.bias > 0 ? "pos" : m.bias < 0 ? "neg" : "muted";
          if (m.bars > 1 && labels) {    // a bracket under (or over) the pattern's bars
            const x1 = cx(Math.max(m.i0, view.i0) - view.i0) - bw / 2, x2 = cx(i) + bw / 2;
            const lowest = Math.min(...prep.daily.slice(Math.max(m.i0, view.i0), di + 1).map(b => b.l));
            const highest = Math.max(...prep.daily.slice(Math.max(m.i0, view.i0), di + 1).map(b => b.h));
            const yb = up ? y(highest) - 3 : y(lowest) + 3;
            marks += `<path d="M${x1.toFixed(1)},${(yb + (up ? 3 : -3)).toFixed(1)}V${yb.toFixed(1)}H${x2.toFixed(1)}V${(yb + (up ? 3 : -3)).toFixed(1)}" fill="none" class="br ${cls}"/>`;
          }
          label(i, up, I18N.patternShort(m.id), cls, I18N.signalName(m.id, m.id) + " · " + m.date, m.bias !== 0);
        }
      }
    }

    const [timeGrid, axis] = timeAxis(bars, cx, PW, top, bottom, "daily");
    back = timeGrid + back;
    const H = bottom + 22, id = st.id + kind;
    const hadFocus = box.contains(global.document.activeElement), keep = st.phover?.[kind];
    box.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" tabindex="0" aria-label="${esc(t(kind === "pa" ? "pa_aria" : "cs_aria"))}">
      <defs><clipPath id="${id}-clip"><rect x="0" y="${top}" width="${PW}" height="${HM}"/></clipPath></defs>
      ${back}<line x1="0" x2="${PW}" y1="${top + HM + 0.5}" y2="${top + HM + 0.5}" stroke="var(--border)"/>
      <g clip-path="url(#${id}-clip)">${zones}${lines}</g><g clip-path="url(#${id}-clip)" shape-rendering="crispEdges">${candles}</g>
      <g clip-path="url(#${id}-clip)">${marks}</g>${strips}${axis}${fore}
      <line class="xh xv" x1="0" x2="0" y1="${top}" y2="${bottom}" visibility="hidden"/>
      <rect class="hit" x="0" y="${top}" width="${PW}" height="${bottom - top}" fill="transparent"/></svg>`;

    // hover readout and keyboard crosshair
    const svg = box.querySelector("svg"), xv = svg.querySelector(".xv"), readout = box.parentNode.querySelector(".ta-readout");
    const show = i => {
      const b = bars[i], prev = b.di ? prep.daily[b.di - 1].c : null;
      const chg = isNum(prev) ? ` <span class="${pctCls(b.c - prev)}">${esc(fmtPct(b.c / prev - 1, 2, true))}</span>` : "";
      let extra = "";
      if (kind === "pa") {
        const sc = pp.states[b.di];
        extra = `<span>${esc(tv("bt_", ["bear_trend_bar", "weak_bear_bar", "doji", "weak_bull_bar", "bull_trend_bar"][pp.types[b.di] + 2]))}</span>` +
          `<span>${esc(t("strip_state"))} <b>${esc(sc == null ? "–" : tv("state_", ["tight_bear_channel", "bear_channel", "trading_range", "bull_channel", "tight_bull_channel"][sc + 2]))}</b></span>` +
          `<span>${esc(t("strip_ai"))} <b>${esc(tv("ai_", { 1: "long", "-1": "short", 0: "none" }[pp.ai[b.di]]))}</b></span>` +
          `<span>EMA <b>${esc(fmtPx(pp.ema[b.di]))}</b></span>` +
          (pp.byBar.get(b.di) || []).map(m => `<span><b class="${m.side > 0 ? "pos" : "neg"}">${esc(m.kind === "G20" ? t("lg_pa_g20") : m.kind === "FB" ? t(m.side < 0 ? "fb_up" : "fb_down") : m.kind)}</b></span>`).join("");
      } else {
        extra = (pp.byBar.get(b.di) || []).map(m => `<span><b class="${m.bias > 0 ? "pos" : m.bias < 0 ? "neg" : ""}">${esc(I18N.signalName(m.id, m.id))}</b></span>`).join("");
      }
      readout.innerHTML = `<span><b>${esc(b.d)}</b></span>` +
        ["o", "h", "l", "c"].map(k => `<span>${esc(t(k))} <b>${esc(fmtPx(b[k]))}</b>${k === "c" ? chg : ""}</span>`).join("") + extra;
    };
    st.phover = st.phover || {};
    const point = i => {
      i = Math.max(0, Math.min(n - 1, i));
      st.phover[kind] = bars[i].di;
      const xx = wx(i);
      xv.setAttribute("x1", xx); xv.setAttribute("x2", xx); xv.setAttribute("visibility", "visible");
      show(i);
    };
    const leave = () => { st.phover[kind] = null; xv.setAttribute("visibility", "hidden"); show(n - 1); };
    const hit = svg.querySelector(".hit");
    const fromEvent = e => point(Math.floor((e.clientX - svg.getBoundingClientRect().left) / slot));
    hit.addEventListener("pointermove", fromEvent);
    hit.addEventListener("pointerdown", fromEvent);
    hit.addEventListener("pointerleave", leave);
    svg.addEventListener("blur", leave);
    svg.addEventListener("keydown", e => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "Escape") { leave(); return; }
      if (e.key === "+" || e.key === "=" || e.key === "-" || e.key === "_") { e.preventDefault(); st.zoom(e.key === "+" || e.key === "=" ? 1 / 1.5 : 1.5); return; }
      const stepK = { ArrowLeft: -1, ArrowRight: 1, PageUp: -10, PageDown: 10, Home: -n, End: n }[e.key];
      if (stepK == null) return;
      e.preventDefault();
      const cur = st.phover[kind] != null ? bars.findIndex(b => b.di >= st.phover[kind]) : n - 1;
      point(cur + stepK);
    });
    const at = keep != null && inView(keep) ? bars.findIndex(b => b.di >= keep) : -1;
    if (at >= 0) point(at); else { st.phover[kind] = null; show(n - 1); }
    if (hadFocus) svg.focus({ preventScroll: true });
  }

  // ------------------------------------------------------------------ public
  function render(root, data) {
    const st = root._ta || (root._ta = { vis: loadVis(), userTf: null, hover: null, id: "ta" + (++uid) });
    if (st.ro) st.ro.disconnect();
    if (st.data !== data) { st.hover = st.hoverDi = st.hoverY = null; st.view = null; }
    st.root = root; st.data = data; st.prep = prepare(data);
    const legend = legendItems(data, st.prep).map(it =>
      `<button type="button" class="ta-chip" data-k="${esc(it.key)}" aria-pressed="${st.vis[it.key] !== false}"${it.title ? ` title="${esc(it.title)}"` : ""}>` +
      `<span class="sw ${it.sw}" style="color:${it.color}${it.fill ? ";background:" + it.fill : ""}"></span>${esc(it.label)}</button>`).join("");
    const vp = data.volume_profile || {};
    root.innerHTML = `${headerHtml(data)}
      <div class="ta-grid">
        <section class="ta-card ta-main">
          <div class="ta-toolbar"><div class="ta-legend">${legend}</div>
            <div class="ta-seg ta-tf" role="group"><button type="button" data-tf="daily">${esc(t("daily"))}</button><button type="button" data-tf="weekly">${esc(t("weekly"))}</button></div>
            <span class="ta-tfnote"></span></div>
          <div class="ta-toolbar ta-nav">
            <div class="ta-seg ta-range" role="group" aria-label="${esc(t("range_label"))}">${[...RANGES, ...(st.prep.daily.length > 300 ? [["all"]] : [])].map(([k]) =>
              `<button type="button" data-r="${k}">${esc(t("r_" + k))}</button>`).join("")}</div>
            <div class="ta-seg ta-zoom" role="group"><button type="button" data-z="out" title="${esc(t("zoom_out"))}" aria-label="${esc(t("zoom_out"))}">−</button><button type="button" data-z="in" title="${esc(t("zoom_in"))}" aria-label="${esc(t("zoom_in"))}">+</button></div>
            <button type="button" class="ta-fs"${global.document.fullscreenEnabled ? "" : " hidden"}>${esc(t("fs_enter"))}</button>
          </div>
          <div class="ta-readout"></div>
          <div class="ta-chartbox"></div>
          <p class="ta-note ta-wknote" hidden>${esc(t("weekly_note"))}</p>
          <p class="ta-note">${esc(t("approx"))}${vp.available && vp.lookback_bars ? " " + esc(t("vp_window", vp.lookback_bars, vp.window_start ?? "")) : ""}</p>
          <p class="ta-note">${esc(t("kb_hint"))}</p>
        </section>
        ${patternCardHtml(st, "pa", data)}
        ${patternCardHtml(st, "cs", data)}
        <aside class="ta-side">${regimeHtml(data.regime)}${scoreHtml(data.scorecard)}${signalsHtml(data.signals)}</aside>
        <div class="ta-below">${levelsHtml(data)}${notesHtml(data)}</div>
      </div>
      <p class="ta-disclaimer">${esc(I18N.lang() === "zh" ? t("disclaimer") : data.meta?.disclaimer || t("disclaimer"))}</p>`;
    st.box = root.querySelector(".ta-main .ta-chartbox");
    st.pboxes = {};
    st.geoms = {};
    for (const kind of ["pa", "cs"]) st.pboxes[kind] = root.querySelector(`.ta-${kind} .ta-chartbox`);
    const redraw = () => {
      draw(st, Math.floor(st.box.clientWidth));
      for (const kind of ["pa", "cs"]) drawPattern(st, kind);
    };
    root.querySelectorAll(".ta-chip").forEach(b => b.addEventListener("click", () => {
      const k = b.dataset.k;
      st.vis[k] = st.vis[k] === false;
      b.setAttribute("aria-pressed", String(st.vis[k]));
      saveVis(st.vis);
      redraw();
    }));
    root.querySelectorAll(".ta-tf button").forEach(b => b.addEventListener("click", () => { st.userTf = b.dataset.tf; redraw(); }));

    // zoom window: range buttons, + / − (and the + − keys), Ctrl+wheel or pinch, mouse drag to pan,
    // double-click to show everything again
    const N = st.prep.daily.length;
    const setView = (i0, c) => {
      c = Math.max(Math.min(MIN_VIEW, N), Math.min(N, c));
      i0 = Math.max(0, Math.min(N - c, i0));
      st.view = { i0, i1: i0 + c - 1 };
      redraw();
    };
    // c bars from i0 become nc bars, keeping the bar at fraction f of the width in place
    const zoomTo = (i0, c, nc, f) => {
      nc = Math.max(Math.min(MIN_VIEW, N), Math.min(N, nc));
      setView(Math.round(i0 + f * (c - nc)), nc);
    };
    st.zoom = (factor, frac) => {
      if (!N) return;
      const v = currentView(st), c = v.i1 - v.i0 + 1, nc = Math.round(c * factor);
      if (nc !== c) zoomTo(v.i0, c, nc, frac ?? (v.i1 === N - 1 ? 1 : 0.5));
    };
    root.querySelectorAll(".ta-range button").forEach(b => b.addEventListener("click", () => {
      if (!N) return;
      const r = rangeView(st.prep.daily, b.dataset.r);
      setView(r.i0, r.i1 - r.i0 + 1);
    }));
    root.querySelectorAll(".ta-zoom button").forEach(b => b.addEventListener("click", () => st.zoom(b.dataset.z === "in" ? 1 / 1.5 : 1.5)));
    // every chart pans and zooms the same window; geom() gives that chart's plot width
    const bindNav = (box, geom) => {
      const frac = x => Math.max(0, Math.min(1, (x - box.getBoundingClientRect().left) / geom().PW));
      // a trackpad pinch arrives as many small Ctrl+wheel steps: add them up until they move a whole bar
      let wheelZoom = 0;
      box.addEventListener("wheel", e => {
        if (!(e.ctrlKey || e.metaKey) || !geom()) return;      // a plain wheel keeps scrolling the page
        e.preventDefault();
        const dy = e.deltaY * (e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? 400 : 1);
        wheelZoom += Math.max(-1, Math.min(1, dy / 100)) * 0.35;
        const v = currentView(st), c = v.i1 - v.i0 + 1;
        if (Math.round(c * Math.exp(wheelZoom)) === c) return;
        st.zoom(Math.exp(wheelZoom), frac(e.clientX));
        wheelZoom = 0;
      }, { passive: false });
      // the mouse or one finger drags sideways to pan (a vertical swipe still scrolls the page); two fingers pinch
      const pts = new Map();
      let drag = null, pinch = null, frame = 0;
      const later = fn => { cancelAnimationFrame(frame); frame = requestAnimationFrame(fn); };
      const gap = () => { const [a, b] = [...pts.values()]; return Math.hypot(a.x - b.x, a.y - b.y) || 1; };
      box.addEventListener("pointerdown", e => {
        if (!geom() || e.button !== 0) return;
        pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const v = currentView(st), c = v.i1 - v.i0 + 1;
        drag = null; pinch = null;
        if (pts.size === 1) drag = { x: e.clientX, i0: v.i0, c, per: geom().PW / c, moved: false };
        else if (pts.size === 2) {
          pts.forEach((_, id) => box.setPointerCapture(id));   // keeps both fingers through redraws
          const [a, b] = [...pts.values()];
          pinch = { d: gap(), i0: v.i0, c, f: frac((a.x + b.x) / 2) };
        }
      });
      box.addEventListener("pointermove", e => {
        if (!pts.has(e.pointerId)) return;
        if (e.pointerType === "mouse" && !(e.buttons & 1)) { endDrag(e); return; }
        pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (pinch && pts.size === 2) {
          const nc = Math.round(pinch.c * pinch.d / gap());
          later(() => { if (pinch) zoomTo(pinch.i0, pinch.c, nc, pinch.f); });
          return;
        }
        if (!drag) return;
        const k = Math.round((drag.x - e.clientX) / drag.per);
        if (!drag.moved && Math.abs(drag.x - e.clientX) < 4) return;
        if (!drag.moved) { drag.moved = true; box.setPointerCapture(e.pointerId); box.classList.add("dragging"); }
        later(() => { if (drag && drag.i0 + k !== currentView(st).i0) setView(drag.i0 + k, drag.c); });
      });
      const endDrag = e => {
        pts.delete(e.pointerId);
        drag = null; pinch = null;
        box.classList.remove("dragging");
      };
      box.addEventListener("pointerup", endDrag);
      box.addEventListener("pointercancel", endDrag);
      box.addEventListener("dblclick", () => { st.view = null; redraw(); });
    };
    bindNav(st.box, () => st.geom);
    for (const kind of ["pa", "cs"]) if (st.pboxes[kind]) bindNav(st.pboxes[kind], () => st.geoms[kind]);
    const fsBtn = root.querySelector(".ta-fs"), card = root.querySelector(".ta-main");
    fsBtn.addEventListener("click", () => {
      if (global.document.fullscreenElement) global.document.exitFullscreen?.();
      else card.requestFullscreen?.().catch(() => {});
    });
    const fsLabel = () => { fsBtn.textContent = t(global.document.fullscreenElement === card ? "fs_exit" : "fs_enter"); };
    if (st.onFs) global.document.removeEventListener("fullscreenchange", st.onFs);
    st.onFs = () => { fsLabel(); requestAnimationFrame(() => st.onSize && st.onSize(true)); };
    global.document.addEventListener("fullscreenchange", st.onFs);
    let width = "", height = 0;
    // a height-only change on a touch screen outside full screen is the browser toolbar showing or hiding
    // during a scroll: redrawing then would make the page jump
    const coarse = global.matchMedia?.("(pointer: coarse)").matches;
    const boxes = [st.box, ...Object.values(st.pboxes).filter(Boolean)];
    const onSize = force => {
      const w = boxes.map(b => Math.floor(b.clientWidth)).join(","), h = global.innerHeight;
      const newH = h !== height && (!coarse || global.document.fullscreenElement);
      if (st.box.clientWidth && (force === true || w !== width || newH)) { width = w; height = h; redraw(); }
    };
    if (global.ResizeObserver) { st.ro = new ResizeObserver(onSize); boxes.forEach(b => st.ro.observe(b)); }
    if (!st.onWin) { st.onWin = () => st.onSize && st.onSize(); global.addEventListener("resize", st.onWin); }
    st.onSize = onSize;
    onSize();
  }

  // chart.html entry point: language toggle, page title, render, re-render on language change.
  function boot(root, data) {
    const title = () => {
      const m = data.meta || {};
      global.document.title = `${m.ticker || ""} ${t("app_title")} · ${m.as_of || ""}`;
    };
    I18N.bindToggle(global.document.getElementById("ta-lang"));
    I18N.applyStatic(global.document);
    I18N.onChange(() => { title(); render(root, data); });
    title();
    render(root, data);
  }

  global.TAChart = { render, boot };
})(window);
