// Quartermaster design system: primitives shared by every page.
// No page builds its own buttons, badges, tables or dialogs; they come from here.

// ------------------------------------------------------------------ DOM

// h("div", {class: "x", onclick: fn}, "text", child, [more]) builds DOM without innerHTML,
// so names and messages from test files are always shown as text.
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

export function s(tag, attrs, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

function setAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (["checked", "disabled", "open", "selected"].includes(k)) el[k] = Boolean(v);
    else el.setAttribute(k, v === true ? "" : v);
  }
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : String(c));
  }
}

// ------------------------------------------------------------------ icons (Lucide-style strokes, drawn here)

const ICONS = {
  overview: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  runs: '<circle cx="12" cy="12" r="9"/><path d="M10 8.5l5 3.5-5 3.5z"/>',
  tests: '<path d="M10 6h10M10 12h10M10 18h10"/><path d="M3.5 6l1.2 1.2L7 5M3.5 12l1.2 1.2L7 11M3.5 18l1.2 1.2L7 17"/>',
  attention: '<path d="M12 3.5l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
  record: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5" fill="currentColor" stroke="none"/>',
  settings: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6L6 6M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  minus: '<path d="M6 12h12"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  loader: '<path d="M12 3a9 9 0 1 0 9 9"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  video: '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3"/>',
  right: '<path d="M9 6l6 6-6 6"/>',
  left: '<path d="M15 6l-6 6 6 6"/>',
  down: '<path d="M6 9l6 6 6-6"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
  wrench: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L4 17l3 3 5.3-5.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-2.5z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
  play: '<path d="M7 5l12 7-12 7z"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
  note: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  eyeoff: '<path d="M3 3l18 18"/><path d="M10.6 5.1A10.5 10.5 0 0 1 12 5c5 0 9 4.5 10 7-.4 1-1.3 2.4-2.6 3.7M6.6 6.6C4.6 7.9 3.3 9.7 2 12c1 2.5 5 7 10 7 1.6 0 3.1-.4 4.4-1.1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  columns: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16"/>',
  sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/><path d="M15 10l-2 2 2 2"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
  palette: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18c1 0 1.5-.8 1.5-1.5 0-1.2-1-1.5-1-2.5s.8-1.5 2-1.5h2A4.5 4.5 0 0 0 21 11c0-4.4-4-8-9-8z"/>',
  ban: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
  skip: '<path d="M5 12h10M11 8l4 4-4 4M19 5v14"/>',
};

export function icon(name, cls) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  el.setAttribute("viewBox", "0 0 24 24");
  el.setAttribute("class", "i" + (cls ? " " + cls : ""));
  el.setAttribute("aria-hidden", "true");
  el.innerHTML = ICONS[name] || "";
  return el;
}

// ------------------------------------------------------------------ status system (one for everything)

// Every status has an icon, a label and a semantic tone; colour is never the only signal.
export const STATUS = {
  passed: {label: "Passed", icon: "check", tone: "success"},
  failed: {label: "Failed", icon: "x", tone: "danger"},
  running: {label: "Running", icon: "loader", tone: "info"},
  queued: {label: "Queued", icon: "clock", tone: "neutral"},
  never: {label: "Never run", icon: "minus", tone: "neutral"},
  healed: {label: "Needs update", icon: "wrench", tone: "warning"},
  blocked: {label: "Blocked", icon: "ban", tone: "danger"},
  error: {label: "Could not run", icon: "attention", tone: "danger"},
  cancelled: {label: "Cancelled", icon: "stop", tone: "neutral"},
  skipped: {label: "Skipped", icon: "skip", tone: "neutral"},
  waiting: {label: "Not started", icon: "minus", tone: "neutral"},
};

export function statusBadge(status, label) {
  const st = STATUS[status] || {label: status, icon: "minus", tone: "neutral"};
  return h("span", {class: `badge tone-${st.tone}`},
    icon(st.icon, status === "running" ? "spin" : ""), label || st.label);
}

export function statusNode(status, fallback) {
  const st = STATUS[status];
  const tone = st ? st.tone : "neutral";
  const show = ["passed", "failed", "healed", "running", "error"].includes(status);
  return h("span", {class: `node ${tone === "neutral" ? "" : tone}`, title: st ? st.label : status},
    show ? icon(st.icon, status === "running" ? "spin" : "") : fallback ?? "", h("span", {class: "sr"}, st ? st.label : status));
}

export function badge(text, tone = "neutral", ic) {
  return h("span", {class: `badge tone-${tone}${ic ? "" : " plain"}`}, ic ? icon(ic) : null, text);
}

// ------------------------------------------------------------------ data and formatting

export async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && /sign in first/i.test(data.error || "") && location.hash !== "#/login") location.hash = "#/login";
  if (!res.ok) throw new Error(data.error || `The service answered ${res.status}`);
  return data;
}

export function remember(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem("qm." + key) || "null");
    localStorage.setItem("qm." + key, JSON.stringify(value));
  } catch (e) { return null; }
  return value;
}

let toastTimer;
export function toast(text) {
  const t = document.getElementById("toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 4200);
}

export function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
  const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 864e5);
  if (days === 0) return `Today ${time}`;
  if (days === 1) return `Yesterday ${time}`;
  return `${d.toLocaleDateString([], {day: "numeric", month: "short", year: days > 300 ? "numeric" : undefined})} ${time}`;
}

export function clock(iso) {
  return iso ? new Date(iso).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "";
}

export function shortDate(iso) {
  return iso ? new Date(iso).toLocaleDateString([], {day: "numeric", month: "short"}) : "";
}

export function took(start, end) {
  if (!start) return "";
  return seconds(((end ? new Date(end) : new Date()) - new Date(start)) / 1000);
}

export function seconds(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec} s`;
  const m = Math.floor(sec / 60);
  return m < 60 ? `${m} min ${sec % 60} s` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function plural(n, word, many) {
  return `${n} ${n === 1 ? word : many || word + "s"}`;
}

// ------------------------------------------------------------------ primitives

export function button(label, {kind = "", ic, onClick, href, title, disabled, size, pressed, attrs} = {}) {
  const cls = ["btn", kind, size, !label ? "icon" : ""].filter(Boolean).join(" ");
  const content = [ic ? icon(ic) : null, label ? h("span", {}, label) : null];
  if (href) return h("a", {class: cls, href, title, "aria-label": !label ? title : null, ...attrs}, content);
  return h("button", {type: "button", class: cls, onclick: onClick, title, disabled, "aria-label": !label ? title : null,
    "aria-pressed": pressed === undefined ? null : String(pressed), ...attrs}, content);
}

export function card({title, sub, actions, body, cls = "", attrs = {}} = {}) {
  return h("section", {class: `card ${cls}`, ...attrs},
    title ? h("div", {class: "card-head"}, h("div", {class: "grow"}, h("h3", {}, title), sub ? h("div", {class: "sub"}, sub) : null),
      actions ? h("div", {class: "row"}, actions) : null) : null,
    body === undefined ? null : h("div", {class: "card-body"}, body));
}

export function emptyState({ic = "check", tone = "neutral", title, text, actions}) {
  return h("div", {class: "empty"}, h("span", {class: `icon-tile tone-${tone}`}, icon(ic)),
    h("div", {class: "grow"}, h("h3", {}, title), text ? h("p", {}, text) : null),
    actions ? h("div", {class: "row"}, actions) : null);
}

export function callout(tone, title, text, extra) {
  const ic = {danger: "attention", warning: "wrench", info: "shield"}[tone] || "shield";
  return h("div", {class: `callout ${tone}`, role: tone === "danger" ? "alert" : null}, icon(ic),
    h("div", {class: "grow"}, title ? h("strong", {}, title) : null, text ? [title ? " " : "", text] : null, extra));
}

export function disclose(summary, ...content) {
  return h("details", {class: "disclose"}, h("summary", {}, icon("right"), summary), content);
}

export function skeleton(lines = 3, height = 14) {
  return h("div", {class: "stack", "aria-hidden": "true", style: "gap:10px"},
    Array.from({length: lines}, (_, i) => h("div", {class: "skel", style: `height:${height}px;width:${[92, 76, 84, 60][i % 4]}%`})));
}

export function segmented(options, value, onchange, label) {
  const box = h("div", {class: "seg", role: "radiogroup", "aria-label": label});
  const draw = (v) => box.replaceChildren(...options.map(([val, text]) =>
    h("button", {type: "button", role: "radio", "aria-checked": String(val === v), tabindex: val === v ? "0" : "-1",
      onclick: () => { draw(val); onchange(val); },
      onkeydown: (e) => {
        const i = options.findIndex(([x]) => x === v);
        const next = e.key === "ArrowRight" ? options[(i + 1) % options.length] : e.key === "ArrowLeft" ? options[(i - 1 + options.length) % options.length] : null;
        if (next) { e.preventDefault(); draw(next[0]); onchange(next[0]); box.querySelector('[aria-checked="true"]').focus(); }
      }}, text)));
  draw(value);
  return box;
}

export function chips(options, value, onchange, label) {
  const box = h("div", {class: "chips", role: "group", "aria-label": label});
  const draw = (v) => box.replaceChildren(...options.map(([val, text, n]) =>
    h("button", {type: "button", class: "chip", "aria-pressed": String(val === v), onclick: () => { draw(val); onchange(val); }},
      text, n === undefined ? null : h("span", {class: "n"}, n))));
  draw(value);
  return box;
}

export function field(label, control, hint, id) {
  if (id) control.id = id;
  return h("div", {}, h("label", {class: "label", for: id || null}, label), control, hint ? h("div", {class: "hint"}, hint) : null);
}

export function input(attrs = {}) {
  return h("input", {type: "text", class: "input", ...attrs});
}

// Accessible table: caption, column headers with scope, optional clickable rows.
export function table({caption, columns, rows, onRow, rowHref}) {
  return h("div", {class: "table-wrap"}, h("table", {class: "table"},
    h("caption", {class: "sr"}, caption),
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", {scope: "col", class: c.cls || null, style: c.width ? `width:${c.width}` : null},
      c.label ? c.label : h("span", {class: "sr"}, c.srLabel || ""))))),
    h("tbody", {}, rows.map((r) => h("tr", {class: onRow ? "click" : null,
      onclick: onRow ? (e) => { if (!e.target.closest("a,button,input,select")) onRow(r); } : null},
      columns.map((c, i) => {
        const cell = c.render(r);
        // when the whole row opens something, its first cell is also a link, for keyboard users
        const content = i === 0 && rowHref ? h("a", {href: rowHref(r), style: "color:inherit;text-decoration:none"}, cell) : cell;
        return h("td", {class: c.cls || null}, content);
      }))))));
}

export function tabs(list, current, onchange) {
  const bar = h("div", {class: "tabs", role: "tablist"});
  const draw = (v) => bar.replaceChildren(...list.map(([id, label, count]) =>
    h("button", {type: "button", role: "tab", id: `tab-${id}`, "aria-selected": String(id === v), tabindex: id === v ? "0" : "-1",
      onclick: () => { draw(id); onchange(id); },
      onkeydown: (e) => {
        const i = list.findIndex(([x]) => x === v);
        const next = e.key === "ArrowRight" ? list[(i + 1) % list.length] : e.key === "ArrowLeft" ? list[(i - 1 + list.length) % list.length] : null;
        if (next) { e.preventDefault(); draw(next[0]); onchange(next[0]); bar.querySelector('[aria-selected="true"]').focus(); }
      }}, label, count === undefined ? null : h("span", {class: "badge plain tone-neutral", style: "height:18px"}, count))));
  draw(current);
  return bar;
}

// ------------------------------------------------------------------ overlays

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

// A modal layer: scrim, focus kept inside, Escape closes, focus returns to where it was.
export function modal(build, {onClose, scrim = true} = {}) {
  const before = document.activeElement;
  const layer = scrim ? h("div", {class: "scrim"}) : null;
  let panel;
  const keys = (e) => {
    if (e.key === "Escape") { e.stopPropagation(); close(); }
    if (e.key === "Tab" && panel) {
      const items = [...panel.querySelectorAll(FOCUSABLE)].filter((x) => x.offsetParent !== null);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  };
  function close() {
    layer?.remove(); panel.remove();
    document.removeEventListener("keydown", keys, true);
    if (before && before.focus) before.focus();
    onClose?.();
  }
  panel = build(close);
  panel.setAttribute("aria-modal", "true");
  if (layer) layer.onclick = close;
  document.addEventListener("keydown", keys, true);
  document.body.append(...[layer, panel].filter(Boolean));
  requestAnimationFrame(() => (panel.querySelector("[autofocus]") || panel.querySelector(FOCUSABLE))?.focus());
  return close;
}

export function drawer({title, sub, body, foot}) {
  return modal((close) => h("div", {class: "drawer", role: "dialog", "aria-labelledby": "drawer-title"},
    h("div", {class: "drawer-head"}, h("div", {}, h("h2", {id: "drawer-title"}, title), sub ? h("div", {class: "meta"}, sub) : null),
      button("", {ic: "x", title: "Close", kind: "ghost", onClick: close})),
    h("div", {class: "drawer-body"}, typeof body === "function" ? body(close) : body),
    foot ? h("div", {class: "drawer-foot"}, foot(close)) : null));
}

// A small panel anchored to a button; closes on outside click or Escape.
export function popover(anchor, build) {
  document.querySelector(".popover")?.remove();
  const rect = anchor.getBoundingClientRect();
  const pop = h("div", {class: "popover", role: "dialog"});
  const close = () => { pop.remove(); document.removeEventListener("mousedown", outside, true); document.removeEventListener("keydown", esc, true); anchor.focus(); };
  const outside = (e) => { if (!pop.contains(e.target) && !anchor.contains(e.target)) close(); };
  const esc = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  pop.append(...[build(close)].flat());
  document.body.append(pop);
  const w = pop.offsetWidth, ph = pop.offsetHeight;
  const left = Math.min(Math.max(8, rect.left), innerWidth - w - 8);
  const top = rect.bottom + 8 + ph > innerHeight ? Math.max(8, rect.top - ph - 8) : rect.bottom + 8;
  pop.style.left = left + "px"; pop.style.top = top + "px";
  setTimeout(() => { document.addEventListener("mousedown", outside, true); document.addEventListener("keydown", esc, true); });
  (pop.querySelector("[autofocus]") || pop.querySelector(FOCUSABLE))?.focus();
  return close;
}

// ------------------------------------------------------------------ tooltip (charts)

export function showTip(e, title, rows) {
  const tip = document.getElementById("tip");
  tip.replaceChildren(h("div", {class: "tt"}, title), ...rows.map((r) =>
    h("div", {class: "tr"}, h("i", {style: `background:${r.color}`}), r.label, h("b", {}, r.value))));
  const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8);
  const y = Math.max(8, e.clientY - tip.offsetHeight - 12);
  tip.style.left = x + "px"; tip.style.top = y + "px";
  tip.classList.add("show");
}

export function hideTip() {
  document.getElementById("tip")?.classList.remove("show");
}

export function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
