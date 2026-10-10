// Audit log: who changed what in Quartermaster, and when. Read only; lines are never changed.
import {api, button, callout, drawer, emptyState, field, h, icon, input, plural, table, when} from "./ui.js";
import {can, loadCommon, runLink, state} from "./state.js";
import {show} from "./app.js";

let query = "";

export async function auditPage() {
  const [, data, chain] = await Promise.all([loadCommon(), api("/api/audit"), api("/api/audit/verify").catch(() => null)]);
  if (state.page !== "audit") return;
  const all = data.entries;
  const text = (e) => [e.who, e.action, e.subject, ...Object.entries(e.details || {}).map(([k, v]) => `${k} ${v}`)].join(" ").toLowerCase();
  const body = h("div", {});
  const draw = () => {
    const q = query.toLowerCase();
    const shown = all.filter((e) => !q || text(e).includes(q));
    body.replaceChildren(shown.length ? table({
      caption: "Audit log",
      rows: shown,
      columns: [
        {label: "When", width: "170px", render: (e) => h("span", {title: e.at}, when(e.at))},
        {label: "Who", width: "140px", render: (e) => e.who},
        {label: "What", render: (e) => [h("div", {class: "primary-cell"}, e.action), e.subject ? h("div", {class: "sub", style: "overflow-wrap:anywhere"}, e.subject) : null]},
        {label: "Details", render: (e) => h("div", {class: "meta", style: "overflow-wrap:anywhere"},
          Object.entries(e.details || {}).map(([k, v], i) => [i ? " · " : "", `${k}: `,
            k === "run" ? h("a", {href: runLink(v)}, v) : Array.isArray(v) ? v.join(", ") : String(v)]))},
      ],
    }) : emptyState(all.length
      ? {ic: "search", title: "Nothing matches", text: "Try another word, such as a name, a scenario or a run."}
      : {ic: "file", title: "Nothing recorded yet", text: "Runs started, approvals, test changes, imports, schedules and settings appear here as they happen."}));
  };
  draw();
  show([{label: "Audit log"}],
    h("div", {class: "page-head"},
      h("div", {}, h("h1", {}, "Audit log"), h("p", {class: "lead"}, "Who changed what in Quartermaster, and when. Passwords, keys and test data values are never written here.")),
      h("div", {class: "row"},
        can("approver") ? button("Export", {kind: "primary", ic: "download", onClick: () => openExport(chain)}) : null,
        button("Download CSV", {ic: "download", href: "/api/audit.csv", attrs: {download: ""}}))),
    chainNote(chain),
    all.length ? h("div", {class: "toolbar"}, h("div", {class: "search"}, icon("search"),
      input({type: "search", value: query, placeholder: "Search who, what or a run", "aria-label": "Search the audit log",
        oninput: (e) => { query = e.target.value; draw(); }}))) : null,
    h("div", {class: "card"}, body),
    all.length >= 1000 ? h("p", {class: "hint"}, "The newest 1,000 entries are shown. The CSV has all of them.") : null);
}

// Whether the log is whole: every line is chained to the one before it, so a change to a line shows here.
function chainNote(c) {
  if (!c) return null;
  if (!c.ok) {
    return callout("danger", "The audit log has been changed.",
      `Line ${c.problem.line}: ${c.problem.why}. Lines before it are fine; from there on the chain no longer holds. Keep a copy of the file as it is, and tell whoever looks after this computer.`);
  }
  if (!c.entries) return null;
  const old = c.legacy ? ` ${plural(c.legacy, "older line")} from before this check began ${c.legacy === 1 ? "is" : "are"} covered by the first one.` : "";
  return callout("info", "The audit log is whole.",
    `${plural(c.entries, "line")}, each tied to the one before it, from ${when(c.first_at)} to ${when(c.last_at)}.${old} A change to any line would show here.`);
}

// Export the log (or part of it) as a zip with a manifest an auditor can check.
function openExport(chain) {
  const format = h("select", {class: "input", "aria-label": "Format"},
    h("option", {value: "csv"}, "CSV: for a spreadsheet"),
    h("option", {value: "jsonl"}, "JSON lines: for security tools and qm audit verify"));
  const from = input({type: "date", "aria-label": "From"});
  const to = input({type: "date", "aria-label": "To"});
  const who = input({placeholder: "Part of a name (optional)", "aria-label": "Who"});
  const text = input({placeholder: "A word in the line (optional)", "aria-label": "Word"});
  const count = h("span", {class: "meta", "aria-live": "polite"});
  const link = button("Download the zip", {kind: "primary", ic: "download", href: "/api/audit/export?format=csv", attrs: {download: ""}});
  const query = () => new URLSearchParams(Object.entries({format: format.value, from: from.value, to: to.value, who: who.value.trim(), text: text.value.trim()}).filter(([, v]) => v));
  const refresh = async () => {
    link.setAttribute("href", `/api/audit/export?${query()}`);
    const q = query();
    q.delete("format");
    try {
      const r = [...q.keys()].length ? await api(`/api/audit?${q}`) : null;
      const n = r ? r.matching : chain?.entries ?? 0;
      count.textContent = `${plural(n, "line")} will be in the export${r ? "" : " (all of them)"}.`;
    } catch (e) { count.textContent = e.message; }
  };
  for (const el of [format, from, to, who, text]) el.addEventListener("change", refresh);
  for (const el of [who, text]) el.addEventListener("input", refresh);
  drawer({
    title: "Export the audit log",
    sub: "A zip with the lines, a manifest and how to check it. The export is itself written to the log.",
    body: () => [
      field("Format", format, "", "ax-format"),
      h("div", {class: "fields"}, field("From", from, "", "ax-from"), field("To", to, "", "ax-to")),
      field("Who", who, "", "ax-who"),
      field("Word", text, "", "ax-text"),
      h("p", {class: "hint", style: "margin:0"}, "manifest.json says what is in the file, its SHA-256, and the newest hash of the whole log. Keep the manifest somewhere else: it is what shows later that nothing was cut off the end. HOW_TO_VERIFY.txt has the steps. Passwords, keys and test data values are never in the log."),
    ],
    foot: (close) => [count, h("span", {class: "grow"}), button("Close", {onClick: close}), link],
  });
  refresh();
}
