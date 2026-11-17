/* ESGF local catalogue - static site (no build step, no dependencies).
 * Reads data/local.json, data/compare.json, data/meta.json written by `stac_site`.
 * All data-derived text is inserted with textContent (never innerHTML).
 */
"use strict";

// ------------------------------------------------------------------ config

const FILTER_FACETS = [
  ["collection", "Collection"], ["domain", "Domain"], ["frequency", "Frequency"],
  ["experiment", "Experiment"], ["driving_model", "Driving model"], ["model", "Model (RCM)"],
  ["variable", "Variable"], ["member", "Member"],
];
const MATRIX_FACETS = [
  ["model", "Model (RCM)"], ["driving_model/model", "Driving model / RCM"],
  ["driving_model", "Driving model"], ["variable", "Variable"], ["experiment", "Experiment"],
  ["domain", "Domain"], ["frequency", "Frequency"], ["member", "Member"], ["collection", "Collection"],
];
const PAGE_SIZE = 100;
const STATUS_ORDER = ["complete", "partial", "missing"];
const STATUS_LABEL = { complete: "complete", partial: "partial", missing: "missing", ok: "up", down: "down" };
// Sequential blue ramp (dataviz reference); dark mode runs dark->light so "low" recedes into the surface.
const RAMP_LIGHT = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
const RAMP_DARK = ["#0d366b", "#184f95", "#256abf", "#2a78d6", "#5598e7", "#86b6ef", "#b7d3f6"];

const DATA = { local: [], compare: [], meta: null };
const STATE = {
  tab: "holdings",
  filters: {},            // facet -> value (holdings + matrix)
  q: "",
  sort: { key: "variable", dir: 1 },
  page: 0,
  mrows: "model", mcols: "variable", mvalue: "years",
  wname: "", wstatus: "incomplete", wvar: "", wq: "",
  wsort: { key: "status", dir: 1 },
  wpage: 0,
};

// ------------------------------------------------------------------ helpers

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "style") Object.assign(node.style, v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const $ = (id) => document.getElementById(id);
const fromTable = (t) => (t && t.rows ? t.rows.map((r) => Object.fromEntries(t.columns.map((c, i) => [c, r[i]]))) : []);
const uniq = (xs) => [...new Set(xs)];
const num = (v) => (typeof v === "number" ? v : v === "" || v == null ? null : Number(v));
const fmtInt = (n) => (n == null ? "–" : Math.round(n).toLocaleString("en-US"));
const fmtGB = (gb) => (gb == null ? "–" : gb >= 1000 ? `${(gb / 1000).toFixed(2)} TB` : `${gb.toFixed(gb < 10 ? 2 : 1)} GB`);
const fmtYears = (y) => (y == null ? "–" : Number.isInteger(y) ? `${y}y` : `${y.toFixed(1)}y`);

function monthIndex(ym) {
  const m = /^(\d{4})-(\d{2})/.exec(ym || "");
  return m ? Number(m[1]) * 12 + Number(m[2]) - 1 : null;
}

function facetValue(row, facet) {
  if (facet === "driving_model/model") return `${row.driving_model || "–"} / ${row.model || "–"}`;
  return row[facet] || "";
}

function unionMonths(intervals) {
  const iv = intervals.slice().sort((a, b) => a[0] - b[0]);
  let total = 0, cs = null, ce = null;
  for (const [s, e] of iv) {
    if (ce === null || s > ce + 1) { if (ce !== null) total += ce - cs + 1; cs = s; ce = e; }
    else ce = Math.max(ce, e);
  }
  if (ce !== null) total += ce - cs + 1;
  return total;
}

function isDark() {
  const t = document.documentElement.dataset.theme;
  if (t) return t === "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function inkFor(hex) {
  const c = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => {
    const v = parseInt(c.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.35 ? "#0b0b0b" : "#ffffff";
}

function sortRows(rows, sort) {
  const { key, dir } = sort;
  return rows.slice().sort((a, b) => {
    let x = a[key], y = b[key];
    if (key === "status") { x = STATUS_ORDER.indexOf(x); y = STATUS_ORDER.indexOf(y); }
    const nx = typeof x === "number", ny = typeof y === "number";
    if (x === null || x === "" || x === undefined) return 1;
    if (y === null || y === "" || y === undefined) return -1;
    if (nx && ny) return (x - y) * dir;
    return String(x).localeCompare(String(y), "en", { numeric: true }) * dir;
  });
}

function downloadCSV(filename, rows, columns) {
  const esc = (v) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const text = [columns.join(","), ...rows.map((r) => columns.map((c) => esc(r[c])).join(","))].join("\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  el("a", { href: url, download: filename }).click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function statusBadge(status) {
  const cls = { complete: "st-complete", partial: "st-partial", missing: "st-missing", ok: "st-ok" }[status] || "st-down";
  return el("span", { class: `status ${cls}` }, el("i", { text: STATUS_LABEL[status] || status || "?" }));
}

// ------------------------------------------------------------------ URL state (shareable links)

function writeHash() {
  const p = new URLSearchParams();
  if (STATE.tab !== "holdings") p.set("tab", STATE.tab);
  for (const [k, v] of Object.entries(STATE.filters)) if (v) p.set(`f.${k}`, v);
  if (STATE.q) p.set("q", STATE.q);
  if (STATE.tab === "matrix") { p.set("rows", STATE.mrows); p.set("cols", STATE.mcols); p.set("value", STATE.mvalue); }
  if (STATE.tab === "watchlist") {
    if (STATE.wname) p.set("watch", STATE.wname);
    if (STATE.wstatus !== "incomplete") p.set("status", STATE.wstatus);
  }
  const hash = p.toString();
  history.replaceState(null, "", hash ? `#${hash}` : location.pathname + location.search);
}

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  for (const [k, v] of p) {
    if (k.startsWith("f.")) STATE.filters[k.slice(2)] = v;
  }
  STATE.tab = p.get("tab") || STATE.tab;
  STATE.q = p.get("q") || "";
  STATE.mrows = p.get("rows") || STATE.mrows;
  STATE.mcols = p.get("cols") || STATE.mcols;
  STATE.mvalue = p.get("value") || STATE.mvalue;
  STATE.wname = p.get("watch") || "";
  STATE.wstatus = p.get("status") || STATE.wstatus;
}

// ------------------------------------------------------------------ tooltip

const tooltip = () => $("tooltip");
function showTooltip(evt, build) {
  const tt = tooltip();
  tt.replaceChildren(...build());
  tt.hidden = false;
  const r = evt.target.getBoundingClientRect();
  const x = evt.clientX ?? r.right, y = evt.clientY ?? r.bottom;
  const w = tt.offsetWidth, h = tt.offsetHeight;
  tt.style.left = `${Math.min(x + 14, window.innerWidth - w - 8)}px`;
  tt.style.top = `${y + h + 20 > window.innerHeight ? y - h - 10 : y + 14}px`;
}
const hideTooltip = () => { tooltip().hidden = true; };

// ------------------------------------------------------------------ filters (holdings + matrix)

function filteredLocal(except) {
  const q = STATE.q.trim().toLowerCase();
  return DATA.local.filter((r) => {
    for (const [k, v] of Object.entries(STATE.filters)) {
      if (!v || k === except) continue;
      if ((r[k] || "") !== v) return false;
    }
    if (q && !Object.values(r).some((v) => String(v ?? "").toLowerCase().includes(q))) return false;
    return true;
  });
}

function renderFilters() {
  const box = $("filters");
  box.hidden = !(STATE.tab === "holdings" || STATE.tab === "matrix");
  if (box.hidden) return;
  const controls = [];
  for (const [facet, label] of FILTER_FACETS) {
    const all = uniq(DATA.local.map((r) => r[facet] || "")).filter(Boolean);
    if (all.length <= 1 && !STATE.filters[facet]) continue;
    // Faceted counts: rows matching every *other* active filter.
    const counts = {};
    for (const r of filteredLocal(facet)) counts[r[facet] || ""] = (counts[r[facet] || ""] || 0) + 1;
    const sel = el("select", {
      "aria-label": label,
      onchange: (e) => { STATE.filters[facet] = e.target.value; STATE.page = 0; render(); },
    }, el("option", { value: "", text: "All" }),
    all.sort((a, b) => a.localeCompare(b, "en", { numeric: true }))
      .map((v) => el("option", { value: v, text: `${v} (${counts[v] || 0})`, selected: STATE.filters[facet] === v })));
    controls.push(el("label", {}, label, sel));
  }
  const search = el("input", {
    type: "search", placeholder: "Search any field…", value: STATE.q, "aria-label": "Search",
    oninput: (e) => {
      STATE.q = e.target.value; STATE.page = 0;
      clearTimeout(renderFilters.t); renderFilters.t = setTimeout(() => { render(); refocus("search"); }, 200);
    },
  });
  search.dataset.focus = "search";
  controls.push(el("label", {}, "Search", search));
  controls.push(el("button", {
    class: "btn", type: "button", text: "Reset",
    onclick: () => { STATE.filters = {}; STATE.q = ""; STATE.page = 0; render(); },
  }));
  const n = filteredLocal().length;
  controls.push(el("span", { class: "count", text: `${fmtInt(n)} of ${fmtInt(DATA.local.length)} datasets` }));
  box.replaceChildren(...controls);
}

function refocus(key) {
  const node = document.querySelector(`[data-focus="${key}"]`);
  if (node) { node.focus(); const v = node.value; node.value = ""; node.value = v; }
}

// ------------------------------------------------------------------ holdings table

let TIME_AXIS = null;  // [firstMonth, lastMonth] over all local rows

function timelineCell(row) {
  const s = monthIndex(row.start), e = monthIndex(row.end);
  if (s === null || e === null || !TIME_AXIS) {
    return el("span", { class: "tl-text", text: row.frequency === "fx" ? "fixed (no time axis)" : "unknown period" });
  }
  const span = TIME_AXIS[1] - TIME_AXIS[0] + 1;
  return el("div", { class: "tl", title: `${row.start} – ${row.end}` },
    el("span", { style: { left: `${((s - TIME_AXIS[0]) / span) * 100}%`, width: `${Math.max(1, ((e - s + 1) / span) * 100)}%` } }));
}

function renderTable(container, { rows, columns, sort, page, onSort, onPage, csvName, empty }) {
  const total = rows.length;
  const sorted = sortRows(rows, sort);
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const p = Math.min(page, pages - 1);
  const slice = sorted.slice(p * PAGE_SIZE, (p + 1) * PAGE_SIZE);

  const thead = el("thead", {}, el("tr", {}, columns.map((c) => el("th", {
    scope: "col", tabindex: c.sortKey === false ? null : 0,
    "aria-sort": sort.key === (c.sortKey ?? c.key) ? (sort.dir > 0 ? "ascending" : "descending") : null,
    onclick: c.sortKey === false ? null : () => onSort(c.sortKey ?? c.key),
    onkeydown: c.sortKey === false ? null : (e) => { if (e.key === "Enter") onSort(c.sortKey ?? c.key); },
  }, c.header ?? c.label))));
  const tbody = el("tbody", {}, slice.map((r) => el("tr", {}, columns.map((c) => {
    const v = c.render ? c.render(r) : r[c.key];
    return el("td", { class: c.cls, title: c.title ? c.title(r) : null }, v instanceof Node ? v : String(v ?? ""));
  }))));

  const pager = el("div", { class: "pager" },
    el("button", { class: "btn", type: "button", disabled: p === 0, text: "‹ Prev", onclick: () => onPage(p - 1) }),
    el("span", { text: `Page ${p + 1} / ${pages}` }),
    el("button", { class: "btn", type: "button", disabled: p >= pages - 1, text: "Next ›", onclick: () => onPage(p + 1) }),
    el("span", { class: "spacer" }),
    el("button", {
      class: "btn", type: "button", text: `Download CSV (${fmtInt(total)} rows)`,
      onclick: () => downloadCSV(csvName, sorted, columns.filter((c) => c.csv !== false).flatMap((c) => c.csvKeys || [c.key])),
    }));
  container.replaceChildren(total
    ? el("table", { class: "data" }, thead, tbody)
    : el("div", { class: "empty", text: empty || "No rows match the current filters." }), pager);
}

function renderHoldings() {
  const multiColl = uniq(DATA.local.map((r) => r.collection)).length > 1;
  const axis = TIME_AXIS
    ? el("div", { class: "tl-axis" }, el("span", { text: Math.floor(TIME_AXIS[0] / 12) }), el("span", { text: "Period" }), el("span", { text: Math.floor(TIME_AXIS[1] / 12) }))
    : "Period";
  const columns = [
    multiColl && { key: "collection", label: "Collection" },
    { key: "domain", label: "Domain" },
    { key: "driving_model", label: "Driving model" },
    { key: "model", label: "Model (RCM)" },
    { key: "experiment", label: "Experiment" },
    { key: "member", label: "Member" },
    { key: "frequency", label: "Freq." },
    { key: "variable", label: "Variable" },
    { key: "period", sortKey: "start", header: axis, render: timelineCell, csvKeys: ["start", "end"] },
    { key: "n_years", label: "Years", cls: "num", render: (r) => (r.n_years == null ? "–" : fmtYears(r.n_years)) },
    { key: "size_gb", label: "Size", cls: "num", render: (r) => fmtGB(num(r.size_gb)) },
    { key: "version", label: "Version" },
    { key: "leaf_dir", label: "Path", cls: "path", title: (r) => r.leaf_dir },
  ].filter(Boolean);
  renderTable($("holdings-table"), {
    rows: filteredLocal(), columns, sort: STATE.sort, page: STATE.page, csvName: "esgf-holdings.csv",
    onSort: (key) => { STATE.sort = { key, dir: STATE.sort.key === key ? -STATE.sort.dir : 1 }; render(); },
    onPage: (p) => { STATE.page = p; render(); },
  });
}

// ------------------------------------------------------------------ coverage matrix

function renderMatrixControls() {
  const select = (label, value, options, onchange) => el("label", {}, label, el("select", { "aria-label": label, onchange: (e) => { onchange(e.target.value); render(); } },
    options.map(([v, l]) => el("option", { value: v, text: l, selected: v === value }))));
  $("matrix-controls").replaceChildren(
    select("Rows", STATE.mrows, MATRIX_FACETS, (v) => { STATE.mrows = v; }),
    select("Columns", STATE.mcols, MATRIX_FACETS, (v) => { STATE.mcols = v; }),
    select("Cell value", STATE.mvalue, [["years", "Years covered (union)"], ["datasets", "Number of datasets"], ["size", "Size on disk"]], (v) => { STATE.mvalue = v; }),
  );
}

function renderMatrix() {
  renderMatrixControls();
  const rows = filteredLocal();
  const box = $("matrix"), legend = $("matrix-legend");
  if (!rows.length) {
    box.replaceChildren(el("div", { class: "empty", text: "No datasets match the current filters." }));
    legend.replaceChildren();
    return;
  }
  const cells = new Map();
  for (const r of rows) {
    const key = `${facetValue(r, STATE.mrows)}\u0000${facetValue(r, STATE.mcols)}`;
    let c = cells.get(key);
    if (!c) cells.set(key, (c = { rowv: facetValue(r, STATE.mrows), colv: facetValue(r, STATE.mcols), rows: [], iv: [], size: 0 }));
    c.rows.push(r);
    c.size += num(r.size_gb) || 0;
    const s = monthIndex(r.start), e = monthIndex(r.end);
    if (s !== null && e !== null) c.iv.push([s, e]);
  }
  for (const c of cells.values()) {
    c.months = unionMonths(c.iv);
    c.value = STATE.mvalue === "datasets" ? c.rows.length : STATE.mvalue === "size" ? c.size : (c.iv.length ? c.months / 12 : null);
  }
  const rowKeys = uniq(rows.map((r) => facetValue(r, STATE.mrows))).sort((a, b) => a.localeCompare(b, "en", { numeric: true }));
  const colKeys = uniq(rows.map((r) => facetValue(r, STATE.mcols))).sort((a, b) => a.localeCompare(b, "en", { numeric: true }));
  const max = Math.max(1e-9, ...[...cells.values()].map((c) => c.value ?? 0));
  const ramp = isDark() ? RAMP_DARK : RAMP_LIGHT;
  const colorFor = (v) => ramp[Math.min(ramp.length - 1, Math.floor((v / max) * ramp.length))];
  const label = (c) => (c.value == null ? `${c.rows.length} ds` : STATE.mvalue === "years" ? fmtYears(Math.round(c.value * 10) / 10)
    : STATE.mvalue === "size" ? fmtGB(c.value) : String(c.value));

  const grid = el("div", { class: "matrix", role: "grid", style: { gridTemplateColumns: `auto repeat(${colKeys.length}, minmax(56px, auto))` } });
  grid.append(el("div"), ...colKeys.map((k) => el("div", { class: "colhead", text: k || "–" })));
  for (const rk of rowKeys) {
    grid.append(el("div", { class: "rowhead", text: rk || "–" }));
    for (const ck of colKeys) {
      const c = cells.get(`${rk}\u0000${ck}`);
      if (!c) { grid.append(el("div", { class: "cell none", "aria-label": `${rk} × ${ck}: no data` })); continue; }
      const bg = c.value == null ? "var(--empty-cell)" : colorFor(c.value);
      const tip = () => {
        const starts = c.rows.map((r) => r.start).filter(Boolean).sort();
        const ends = c.rows.map((r) => r.end).filter(Boolean).sort();
        return [
          el("div", { class: "tt-value", text: label(c) }),
          el("div", { class: "tt-label", text: `${rk} × ${ck}` }),
          el("div", { text: `${c.rows.length} dataset(s) · ${fmtGB(c.size)}` }),
          el("div", { text: starts.length ? `Envelope ${starts[0]} – ${ends[ends.length - 1]} · union ${fmtYears(Math.round(c.months / 1.2) / 10)}` : "No time axis (fixed fields)" }),
          el("div", { class: "tt-label", text: "Click to list these datasets" }),
        ];
      };
      grid.append(el("button", {
        type: "button", class: "cell", style: { background: bg, color: c.value == null ? "var(--text-secondary)" : inkFor(bg) },
        "aria-label": `${rk} × ${ck}: ${label(c)}, ${c.rows.length} datasets`, text: label(c),
        onpointermove: (e) => showTooltip(e, tip), onpointerleave: hideTooltip,
        onfocus: (e) => showTooltip(e, tip), onblur: hideTooltip,
        onclick: () => drillDown(rk, ck),
      }));
    }
  }
  box.replaceChildren(grid);
  const valueName = { years: "years covered", datasets: "datasets", size: "size" }[STATE.mvalue];
  legend.replaceChildren(
    el("span", { text: "0" }),
    el("span", { class: "ramp", style: { background: `linear-gradient(to right, ${ramp.join(",")})` } }),
    el("span", { text: `${STATE.mvalue === "size" ? fmtGB(max) : STATE.mvalue === "years" ? fmtYears(Math.round(max * 10) / 10) : Math.round(max)} ${valueName}` }),
    el("span", { style: { display: "inline-block", width: "18px", height: "12px", borderRadius: "2px", background: "var(--empty-cell)", marginLeft: "12px" } }),
    el("span", { text: "no data" }));
}

function drillDown(rowValue, colValue) {
  hideTooltip();
  for (const [facet, value] of [[STATE.mrows, rowValue], [STATE.mcols, colValue]]) {
    if (facet === "driving_model/model") {
      const [dm, m] = value.split(" / ");
      STATE.filters.driving_model = dm === "–" ? "" : dm;
      STATE.filters.model = m === "–" ? "" : m;
    } else {
      STATE.filters[facet] = value;
    }
  }
  STATE.page = 0;
  setTab("holdings");
}

// ------------------------------------------------------------------ watchlist

function statusStack(counts, total) {
  const bar = el("div", { class: "stack", role: "img", "aria-label": STATUS_ORDER.map((s) => `${counts[s]} ${s}`).join(", ") });
  for (const s of STATUS_ORDER) {
    if (!counts[s]) continue;
    bar.append(el("span", { class: `sw-${s}`, style: { flex: `${counts[s] / total} 0 2px` } }));
  }
  return bar;
}

function renderWatchCards() {
  const watches = DATA.meta.watchlist || [];
  if (!watches.length) {
    $("watch-cards").replaceChildren(el("div", { class: "card" },
      el("h3", { text: "No watchlist configured" }),
      el("p", { class: "desc", text: "Add [[watch]] entries to watchlist.toml and rebuild the snapshot with stac_site." })));
    return;
  }
  $("watch-cards").replaceChildren(...watches.map((w) => {
    const total = w.datasets || 0;
    const pct = total ? Math.round((w.counts.complete / total) * 100) : 0;
    const card = el("div", { class: "card" },
      el("h3", { text: w.name }),
      w.description && el("p", { class: "desc", text: w.description }),
      el("div", { class: "chips" }, el("span", { class: "chip", text: w.collection }),
        Object.entries(w.filters).map(([k, v]) => el("span", { class: "chip", text: `${k}: ${v}` }))));
    if (!w.evaluated) {
      card.append(el("div", { class: "warn", text: `Not evaluated in this snapshot${w.error ? ` (${w.error})` : ""}.` }));
      return card;
    }
    card.append(
      el("div", {}, el("span", { class: "pct", text: `${pct}%` }), ` complete · ${fmtInt(total)} datasets on ESGF`),
      total ? statusStack(w.counts, total) : el("div", { class: "desc", text: "ESGF returned no datasets for these filters." }),
      el("div", { class: "stack-legend" }, STATUS_ORDER.map((s) => el("span", {}, statusBadge(s), ` ${fmtInt(w.counts[s])}`))),
      w.error && el("div", { class: "warn", text: w.error }),
      el("button", { class: "btn", type: "button", style: { marginTop: "10px" }, text: "Show incomplete datasets",
        onclick: () => { STATE.wname = w.name; STATE.wstatus = "incomplete"; STATE.wpage = 0; render(); $("watch-table").scrollIntoView({ behavior: "smooth" }); } }));
    return card;
  }));
}

function filteredCompare() {
  const q = STATE.wq.trim().toLowerCase();
  return DATA.compare.filter((r) => {
    if (STATE.wname && r.watch !== STATE.wname) return false;
    if (STATE.wstatus === "incomplete" && r.status === "complete") return false;
    if (STATUS_ORDER.includes(STATE.wstatus) && r.status !== STATE.wstatus) return false;
    if (STATE.wvar && r.variable !== STATE.wvar) return false;
    if (q && !Object.values(r).some((v) => String(v ?? "").toLowerCase().includes(q))) return false;
    return true;
  });
}

function renderWatchlist() {
  renderWatchCards();
  const watches = (DATA.meta.watchlist || []).map((w) => w.name);
  const vars = uniq(DATA.compare.filter((r) => !STATE.wname || r.watch === STATE.wname).map((r) => r.variable)).sort();
  const sel = (label, value, options, onchange) => el("label", {}, label, el("select", { "aria-label": label, onchange: (e) => { onchange(e.target.value); STATE.wpage = 0; render(); } },
    options.map(([v, l]) => el("option", { value: v, text: l, selected: v === value }))));
  const rows = filteredCompare();
  const wsearch = el("input", {
    type: "search", placeholder: "Search…", value: STATE.wq, "aria-label": "Search watchlist rows",
    oninput: (e) => { STATE.wq = e.target.value; STATE.wpage = 0; clearTimeout(renderWatchlist.t); renderWatchlist.t = setTimeout(() => { render(); refocus("wsearch"); }, 200); },
  });
  wsearch.dataset.focus = "wsearch";
  $("watch-filters").replaceChildren(
    sel("Watch", STATE.wname, [["", "All watches"], ...watches.map((w) => [w, w])], (v) => { STATE.wname = v; STATE.wvar = ""; }),
    sel("Status", STATE.wstatus, [["incomplete", "Partial + missing"], ["all", "All"], ["complete", "Complete"], ["partial", "Partial"], ["missing", "Missing"]], (v) => { STATE.wstatus = v; }),
    sel("Variable", STATE.wvar, [["", "All"], ...vars.map((v) => [v, v])], (v) => { STATE.wvar = v; }),
    el("label", {}, "Search", wsearch),
    el("span", { class: "count", text: `${fmtInt(rows.length)} dataset(s)` }));
  const columns = [
    !STATE.wname && watches.length > 1 && { key: "watch", label: "Watch" },
    { key: "status", label: "Status", render: (r) => statusBadge(r.status) },
    { key: "domain", label: "Domain" },
    { key: "driving_model", label: "Driving model" },
    { key: "model", label: "Model (RCM)" },
    { key: "experiment", label: "Experiment" },
    { key: "member", label: "Member" },
    { key: "frequency", label: "Freq." },
    { key: "variable", label: "Variable" },
    { key: "period", sortKey: "start", label: "Period (ESGF)", render: (r) => (r.start ? `${r.start} – ${r.end}` : "–"), csvKeys: ["start", "end"] },
    { key: "files", sortKey: "files_local", label: "Files local/ESGF", cls: "num", render: (r) => `${fmtInt(num(r.files_local))} / ${fmtInt(num(r.files_remote))}`, csvKeys: ["files_local", "files_remote"] },
    { key: "size", sortKey: "size_remote_gb", label: "Size local/ESGF", cls: "num", render: (r) => `${fmtGB(num(r.size_local_gb))} / ${fmtGB(num(r.size_remote_gb))}`, csvKeys: ["size_local_gb", "size_remote_gb"] },
    { key: "version", label: "Version" },
    { key: "item_id", label: "ESGF dataset id", cls: "path", title: (r) => r.item_id },
  ].filter(Boolean);
  renderTable($("watch-table"), {
    rows, columns, sort: STATE.wsort, page: STATE.wpage, csvName: "esgf-watchlist.csv",
    empty: DATA.compare.length ? "No datasets match - everything selected may already be complete." : "No comparison data in this snapshot.",
    onSort: (key) => { STATE.wsort = { key, dir: STATE.wsort.key === key ? -STATE.wsort.dir : 1 }; render(); },
    onPage: (p) => { STATE.wpage = p; render(); },
  });
}

// ------------------------------------------------------------------ nodes

function renderNodes() {
  const h = DATA.meta.health || { nodes: [] };
  $("nodes-intro").textContent = h.timestamp
    ? `ESGF STAC and file-server reachability from the last stac_health run (${h.timestamp}).`
    : "No health.csv was available when this snapshot was built.";
  const kinds = { stac: "STAC search", fs: "File server" };
  renderTable($("nodes-table"), {
    rows: h.nodes, sort: { key: "status", dir: -1 }, page: 0, csvName: "esgf-health.csv",
    columns: [
      { key: "status", label: "Status", render: (r) => statusBadge(r.status) },
      { key: "kind", label: "Kind", render: (r) => kinds[r.kind] || r.kind },
      { key: "name", label: "Host" },
      { key: "detail", label: "Detail" },
    ],
    onSort: () => {}, onPage: () => {}, empty: "No node health data.",
  });
}

// ------------------------------------------------------------------ tiles, tabs, render

function renderTiles() {
  const t = DATA.meta.totals, h = DATA.meta.health || {};
  const watches = (DATA.meta.watchlist || []).filter((w) => w.evaluated && w.datasets);
  const wTotal = watches.reduce((a, w) => a + w.datasets, 0);
  const wDone = watches.reduce((a, w) => a + w.counts.complete, 0);
  const tile = (label, value, note) => el("div", { class: "tile" }, el("div", { class: "label", text: label }), el("div", { class: "value" }, value), note && el("div", { class: "note", text: note }));
  const nodesUp = h.up + h.down
    ? el("span", { class: `status ${h.down ? "st-warn" : "st-ok"}` }, el("i", { text: `${h.up}/${h.up + h.down}` }))
    : "–";
  $("tiles").replaceChildren(
    tile("Datasets on disk", fmtInt(t.datasets), t.older_versions ? `+ ${fmtInt(t.older_versions)} older version(s)` : t.collections.join(", ")),
    tile("Total size", fmtGB(t.size_gb), "newest versions only"),
    tile("Models", fmtInt(t.models)),
    tile("Variables", fmtInt(t.variables)),
    tile("Watchlist complete", wTotal ? `${Math.round((wDone / wTotal) * 100)}%` : "–", wTotal ? `${fmtInt(wDone)} of ${fmtInt(wTotal)} ESGF datasets` : "no watchlist data"),
    tile("Nodes reachable", nodesUp, h.down ? `${h.down} down` : h.up ? "all up" : "no health data"),
  );
}

function setTab(tab) {
  STATE.tab = tab;
  render();
}

function render() {
  hideTooltip();
  for (const b of document.querySelectorAll(".tab")) {
    const on = b.dataset.tab === STATE.tab;
    b.setAttribute("aria-selected", on);
    b.tabIndex = on ? 0 : -1;
    $(`panel-${b.dataset.tab}`).hidden = !on;
  }
  renderFilters();
  if (STATE.tab === "holdings") renderHoldings();
  else if (STATE.tab === "matrix") renderMatrix();
  else if (STATE.tab === "watchlist") renderWatchlist();
  else renderNodes();
  writeHash();
}

function initChrome() {
  const tabs = [...document.querySelectorAll(".tab")];
  for (const b of tabs) {
    b.addEventListener("click", () => setTab(b.dataset.tab));
    b.addEventListener("keydown", (e) => {
      const i = tabs.indexOf(b);
      const j = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length : -1;
      if (j >= 0) { setTab(tabs[j].dataset.tab); tabs[j].focus(); }
    });
  }
  $("theme-toggle").addEventListener("click", () => {
    const next = isDark() ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (e) { /* private mode */ }
    render();
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
  window.addEventListener("scroll", hideTooltip, { passive: true });
}

async function load() {
  initChrome();
  try {
    const get = async (name) => {
      const r = await fetch(`data/${name}`, { cache: "no-cache" });
      if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
      return r.json();
    };
    const [local, compare, meta] = await Promise.all([get("local.json"), get("compare.json"), get("meta.json")]);
    DATA.local = fromTable(local);
    DATA.compare = fromTable(compare);
    DATA.meta = meta;
  } catch (err) {
    $("generated").textContent = "Could not load the snapshot.";
    $("tiles").replaceChildren(el("div", { class: "card" },
      el("h3", { text: "No data" }),
      el("p", { class: "desc", text: `${err.message}. Run "pixi run stac_site" and serve docs/ over HTTP (python -m http.server -d docs) - browsers block fetch() from file:// pages.` })));
    document.querySelector("nav.tabs").hidden = true;
    return;
  }
  const starts = DATA.local.map((r) => monthIndex(r.start)).filter((x) => x !== null);
  const ends = DATA.local.map((r) => monthIndex(r.end)).filter((x) => x !== null);
  if (starts.length) TIME_AXIS = [Math.min(...starts) - (Math.min(...starts) % 12), Math.max(...ends) + 11 - (Math.max(...ends) % 12)];
  const when = new Date(DATA.meta.generated_at);
  $("generated").textContent = `Snapshot ${isNaN(when) ? DATA.meta.generated_at : when.toISOString().slice(0, 16).replace("T", " ")} UTC`;
  readHash();
  renderTiles();
  render();
}

load();
