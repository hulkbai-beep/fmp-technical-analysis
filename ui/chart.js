// Technical-analysis chart for chart.html and the local app (ui/index.html): hand-drawn SVG, no
// libraries. TAChart.render(root, data) draws the header, the chart (candles, SMAs, anchored
// VWAPs, support/resistance zones, earnings markers, volume-profile sidebar, volume / RSI / RS
// sub-panels) and the side panels from the JSON that scripts/chart.py builds. Every number shown
// comes from that JSON; this file only lays it out (weekly candles are precomputed too).
(function (global) {
  "use strict";

  const I18N = global.TA_I18N;
  const t = (key, ...a) => I18N.t(key, ...a);
  const tv = (prefix, value) => I18N.tv(prefix, value);
  const VIS_KEY = "ta-vis";
  const SMA_KEYS = ["sma20", "sma50", "sma200"];
  const MIN_PX_PER_DAY = 3;   // below this the chart switches to weekly candles by itself
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
  const tickLabel = (v, step) => step >= 1 ? v.toFixed(0) : step >= 0.1 ? v.toFixed(1) : v.toFixed(2);
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
    return { daily, weekly, series, avwaps };
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

  function signalsHtml(sg) {
    const title = t("t_signals");
    if (!sg || !sg.available) return card(title, `<p class="ta-small">${esc(t("unavailable"))}: ${esc(sg?.reason)}</p>`, "signals");
    if (!sg.active.length) return card(title, `<p class="ta-small">${esc(t("sig_none"))}</p>`, "signals");
    const rows = sg.active.map(a => {
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
    return card(title, `<p class="ta-small" style="margin:0 0 6px">${esc(t("sig_h", sg.primary_horizon))}</p>
      <div class="ta-tablewrap"><table class="ta-table ta-sig"><thead><tr><th>${esc(t("th_signal"))}</th>
      <th>${esc(t("th_n"))}</th><th>${esc(t("th_hit"))}</th><th>${esc(t("th_med"))}</th><th>${esc(t("th_edge"))}</th></tr></thead>
      <tbody>${rows}</tbody></table></div><p class="ta-small">${esc(t("edge_note"))}</p>`, "signals");
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
          ${isNum(x.touches) ? `<div class="ta-sub">${esc(t("touches", x.touches))}</div>` : ""}</td></tr>`;
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
    if (d.levels?.supports?.length) items.push({ key: "supports", label: t("lg_supports"), color: "var(--up)", sw: "box" });
    if (d.levels?.resistances?.length) items.push({ key: "resistances", label: t("lg_resistances"), color: "var(--down)", sw: "box" });
    if (d.volume_profile?.available) items.push({ key: "vp", label: t("lg_vp"), color: "var(--va)", sw: "box" });
    if ((d.earnings_dates || []).length) items.push({ key: "earnings", label: t("lg_earnings"), color: "var(--text)", sw: "box" });
    return items;
  }

  // ------------------------------------------------------------------ chart drawing
  function draw(st, W) {
    const d = st.data, prep = st.prep, vis = st.vis, on = k => vis[k] !== false;
    const box = st.box;
    if (!prep.daily.length) { box.innerHTML = `<p class="ta-small">${esc(t("no_data"))}</p>`; return; }
    const vp = d.volume_profile || {};
    const vpOn = on("vp") && vp.available && (vp.bins || []).length > 0;
    const AX = W < 420 ? 48 : 58, VPW = vpOn ? (W >= 900 ? 96 : W >= 560 ? 68 : 40) : 0, GAP = vpOn ? 4 : 0;
    const PW = Math.max(80, W - AX - VPW - GAP), XV = PW + GAP, XA = XV + VPW + 6;

    const tooNarrow = PW / prep.daily.length < MIN_PX_PER_DAY;
    const tf = prep.weekly.length && (st.userTf || (d.default_timeframe === "weekly" || tooNarrow ? "weekly" : "daily")) === "weekly" ? "weekly" : "daily";
    st.tf = tf;
    st.auto = !st.userTf && tf === "weekly" && d.default_timeframe !== "weekly";
    st.root.querySelectorAll(".ta-seg button").forEach(b => b.classList.toggle("on", b.dataset.tf === tf));
    const tfNote = st.root.querySelector(".ta-tfnote");
    tfNote.textContent = st.auto ? `(${t("auto")})` : "";
    tfNote.title = st.auto ? t("auto_weekly") : "";
    st.root.querySelector(".ta-wknote").hidden = tf !== "weekly";

    const bars = tf === "weekly" ? prep.weekly : prep.daily, n = bars.length, slot = PW / n;
    const cx = i => slot * (i + 0.5);
    const bw = Math.max(1, Math.min(13, Math.round(slot * 0.7)));
    const bx = i => Math.round(cx(i) - bw / 2), wx = i => bx(i) + Math.floor(bw / 2) + 0.5;
    const S = prep.series;
    const lines = [
      ...SMA_KEYS.filter(on).map(k => ({ vals: S[k], color: `var(--${k})`, dash: "", w: 1.6 })),
      ...prep.avwaps.filter(a => on(a.key)).map(a => ({ vals: a.vals, color: `var(--av-${a.name}, var(--muted))`, dash: "6 4", w: 1.3 })),
    ];

    // ---- main price scale: candles, plus visible lines / levels that sit reasonably close
    const top = 8, HM = Math.round(Math.min(440, Math.max(230, W * 0.42)));
    let lo0 = Infinity, hi0 = -Infinity;
    for (const b of bars) { if (b.l < lo0) lo0 = b.l; if (b.h > hi0) hi0 = b.h; }
    const span0 = hi0 - lo0 || Math.abs(hi0) * 0.02 || 1;
    let lo = lo0, hi = hi0;
    const take = (v, lim) => { if (isNum(v) && v > lo0 - lim * span0 && v < hi0 + lim * span0) { lo = Math.min(lo, v); hi = Math.max(hi, v); } };
    for (const ln of lines) for (const b of bars) take(ln.vals[b.di], 0.5);
    const levels = [];
    (d.levels?.resistances || []).forEach((x, i) => levels.push({ ...x, tag: "R" + (i + 1), side: "r" }));
    (d.levels?.supports || []).forEach((x, i) => levels.push({ ...x, tag: "S" + (i + 1), side: "s" }));
    const shown = levels.filter(x => on(x.side === "s" ? "supports" : "resistances"));
    shown.forEach(x => take(x.price, 0.25));
    const pad = (hi - lo) * 0.05 || 1;
    lo -= pad; hi += pad;
    const y = v => top + (hi - v) / (hi - lo) * HM;
    const inMain = v => v >= lo && v <= hi;

    const id = st.id, clip = `url(#${id}-clip)`;
    let back = "", g = "", fore = "";

    // grid and price axis
    const last = bars[n - 1], lastY = y(last.c);
    const ticks = niceTicks(lo, hi, HM > 320 ? 7 : 5), step = ticks.length > 1 ? ticks[1] - ticks[0] : 1;
    for (const v of ticks) {
      const yy = Math.round(y(v)) + 0.5;
      if (yy < top || yy > top + HM) continue;
      back += `<line x1="0" x2="${XV + VPW}" y1="${yy}" y2="${yy}" stroke="var(--grid)"/>`;
      if (Math.abs(yy - lastY) > 12) back += `<text x="${XA}" y="${yy + 4}">${tickLabel(v, step)}</text>`;
    }

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

    // support / resistance zones and lines (behind the candles); labels on top, de-overlapped
    const labels = [];
    for (const x of shown) {
      const col = x.side === "s" ? "--up" : "--down";
      if (inMain(x.price)) {
        const y1 = y(Math.max(x.zone_high, x.price)), y2 = y(Math.min(x.zone_low, x.price));
        const h = Math.max(3, y2 - y1);
        back += `<rect x="0" y="${(y1 + y2 - h) / 2}" width="${PW}" height="${h}" fill="var(${col}-zone)"/>` +
          `<line x1="0" x2="${PW}" y1="${Math.round(y(x.price)) + 0.5}" y2="${Math.round(y(x.price)) + 0.5}" stroke="var(${col})" stroke-width="1" opacity=".75"/>`;
        labels.push({ y: y(x.price), at: y(x.price), text: `${x.tag} ${fmtPx(x.price)}`, cls: x.side });
      } else {
        const above = x.price > hi;
        labels.push({ y: above ? top + 8 : top + HM - 8, at: null, text: `${x.tag} ${fmtPx(x.price)} ${above ? "↑" : "↓"}`, cls: x.side });
      }
    }
    labels.sort((a, b) => a.y - b.y);
    const LH = 15, minY = top + 8, maxY = top + HM - 8;
    labels.forEach((l, i) => { l.y = Math.max(minY, i ? Math.max(l.y, labels[i - 1].y + LH) : l.y); });
    for (let i = labels.length - 1; i >= 0; i--) labels[i].y = Math.min(labels[i].y, i === labels.length - 1 ? maxY : labels[i + 1].y - LH);
    for (const l of labels) {
      const w = measure(l.text, 11, 650) + 8;
      fore += `<rect x="0" y="${l.y - 7.5}" width="${w}" height="15" rx="3" fill="var(--card)" opacity=".88"/>` +
        `<text class="lvl ${l.cls}" x="4" y="${l.y + 4}">${esc(l.text)}</text>`;
      // a label pushed off its line by its neighbours gets a leader tick back to the line
      if (l.at != null && Math.abs(l.y - l.at) > 2) {
        fore += `<path d="M${w},${l.y.toFixed(1)}h4L${w + 12},${l.at.toFixed(1)}" fill="none" stroke="var(${l.cls === "s" ? "--up" : "--down"})" stroke-width="1"/>`;
      }
    }

    // earnings: dotted lines in the price panel and E badges on a lane under it (time axis)
    const LANE = 18, laneY = top + HM + LANE / 2 + 2;
    const viewIndex = date => { let a = 0, b = n - 1; if (!n || date > bars[b].d) return -1; while (a < b) { const m = (a + b) >> 1; if (bars[m].d < date) a = m + 1; else b = m; } return a; };
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
    const HT = 18, HS = W < 560 ? 54 : 68;
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
      sub += `<text x="0" y="${p.ty}"><tspan class="pt">${esc(t("p_" + p.key))}</tspan><tspan dx="8" class="pv" data-p="${p.key}"></tspan></text>`;
    }
    const bottom = cur - 8;

    // time axis: month starts every `mstep` months (1, 2, 3, 6 or 12, whatever fits), so January
    // (labelled with the year) is always a tick; without a January tick the first label carries the year.
    let axis = "", lastRight = -1e9;
    const zh = I18N.lang() === "zh", MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const starts = [];
    for (let i = 1; i < n; i++) if (bars[i].d.slice(0, 7) !== bars[i - 1].d.slice(0, 7)) starts.push(i);
    const perMonth = starts.length > 1 ? (cx(starts[starts.length - 1]) - cx(starts[0])) / (starts.length - 1) : PW;
    const mstep = [1, 2, 3, 6, 12].find(s => s * perMonth >= measure(zh ? "12月" : "May", 11) + 16) ?? 12;
    const yearTick = starts.some(i => bars[i].d.slice(5, 7) === "01");
    for (const i of starts) {
      const yv = bars[i].d.slice(0, 4), m = +bars[i].d.slice(5, 7);
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
    const H = bottom + 22;

    box.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" tabindex="0" aria-label="${esc(t("chart_aria"))}">
      <defs><clipPath id="${id}-clip"><rect x="0" y="${top}" width="${PW}" height="${HM}"/></clipPath></defs>
      ${back}<line x1="0" x2="${PW}" y1="${top + HM + 0.5}" y2="${top + HM + 0.5}" stroke="var(--border)"/>
      <g clip-path="${clip}" shape-rendering="crispEdges">${candles}</g><g clip-path="${clip}">${lineSvg}</g>${g}${sub}${axis}${fore}
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
      st.hover = null;
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
      if (e.key === "Escape") { leave(); return; }
      const step = { ArrowLeft: -1, ArrowRight: 1, PageUp: -10, PageDown: 10, Home: -n, End: n }[e.key];
      if (step == null) return;
      e.preventDefault();
      point((st.hover ?? n - 1) + step, null);
    });
    if (st.hover != null && st.hover < n) point(st.hover, null); else show(n - 1);
  }

  // ------------------------------------------------------------------ public
  function render(root, data) {
    const st = root._ta || (root._ta = { vis: loadVis(), userTf: null, hover: null, id: "ta" + (++uid) });
    if (st.ro) st.ro.disconnect();
    if (st.data !== data) st.hover = null;
    st.root = root; st.data = data; st.prep = prepare(data);
    const legend = legendItems(data, st.prep).map(it =>
      `<button type="button" class="ta-chip" data-k="${esc(it.key)}" aria-pressed="${st.vis[it.key] !== false}"${it.title ? ` title="${esc(it.title)}"` : ""}>` +
      `<span class="sw ${it.sw}" style="color:${it.color}"></span>${esc(it.label)}</button>`).join("");
    const vp = data.volume_profile || {};
    root.innerHTML = `${headerHtml(data)}
      <div class="ta-grid">
        <section class="ta-card ta-main">
          <div class="ta-toolbar"><div class="ta-legend">${legend}</div>
            <div class="ta-seg" role="group"><button type="button" data-tf="daily">${esc(t("daily"))}</button><button type="button" data-tf="weekly">${esc(t("weekly"))}</button></div>
            <span class="ta-tfnote"></span></div>
          <div class="ta-readout"></div>
          <div class="ta-chartbox"></div>
          <p class="ta-note ta-wknote" hidden>${esc(t("weekly_note"))}</p>
          <p class="ta-note">${esc(t("approx"))}${vp.available && vp.lookback_bars ? " " + esc(t("vp_window", vp.lookback_bars, vp.window_start ?? "")) : ""}</p>
          <p class="ta-note">${esc(t("kb_hint"))}</p>
        </section>
        <aside class="ta-side">${regimeHtml(data.regime)}${scoreHtml(data.scorecard)}${signalsHtml(data.signals)}</aside>
        <div class="ta-below">${levelsHtml(data)}${notesHtml(data)}</div>
      </div>
      <p class="ta-disclaimer">${esc(I18N.lang() === "zh" ? t("disclaimer") : data.meta?.disclaimer || t("disclaimer"))}</p>`;
    st.box = root.querySelector(".ta-chartbox");
    const redraw = () => draw(st, Math.floor(st.box.clientWidth));
    root.querySelectorAll(".ta-chip").forEach(b => b.addEventListener("click", () => {
      const k = b.dataset.k;
      st.vis[k] = st.vis[k] === false;
      b.setAttribute("aria-pressed", String(st.vis[k]));
      saveVis(st.vis);
      redraw();
    }));
    root.querySelectorAll(".ta-seg button").forEach(b => b.addEventListener("click", () => { st.userTf = b.dataset.tf; redraw(); }));
    let width = 0;
    const onSize = () => { const w = Math.floor(st.box.clientWidth); if (w && w !== width) { width = w; redraw(); } };
    if (global.ResizeObserver) { st.ro = new ResizeObserver(onSize); st.ro.observe(st.box); } else global.addEventListener("resize", onSize);
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
