// Tests: every test file, with configurable columns; one test in plain words.
import {
  api, button, callout, card, chips, drawer, emptyState, field, h, icon, input, plural, popover, remember, statusBadge, table, tabs, toast, when,
} from "./ui.js";
import {openRunDrawer, releaseBadge} from "./components.js";
import {loadCommon, runLink, state, testLink} from "./state.js";
import {show} from "./app.js";
import {openManualImport, testsViewSwitch} from "./page-manual.js";
import {ticketChips} from "./page-tickets.js";

const filters = {q: "", status: "all", module: "all"};

function lastStatus(t) {
  if (t.problem) return "error";
  if (!t.last_result) return "never";
  return t.last_result.status;
}

// Core columns are always shown; the rest are the reader's choice, remembered on this computer.
const OPTIONAL = [
  ["process", "Process", (t) => t.process || dash()],
  ["persona", "Job role", (t) => t.persona || dash()],
  ["owner", "Owner", (t) => t.owner || dash()],
  ["release", "Release validated", (t) => t.release_validated ? h("span", {class: "release"}, t.release_validated) : dash()],
  ["environment", "Environment", (t) => t.last_result?.environment ? t.last_result.environment.split(".")[0].toUpperCase() : dash()],
  ["last_run", "Last run", (t) => t.last_result ? h("a", {href: runLink(t.last_result.run_id)}, when(t.last_result.at)) : dash()],
  ["duration", "Duration", (t) => t.last_result?.duration || dash()],
  ["tags", "Tags", (t) => t.tags?.length ? h("div", {class: "row", style: "gap:4px"}, t.tags.map((g) => h("span", {class: "tag"}, g))) : dash()],
  ["updated", "Updated", (t) => t.updated ? when(t.updated) : dash()],
];
const DEFAULT_COLUMNS = ["process", "last_run"];

function dash() {
  return h("span", {class: "muted"}, "–");
}

export async function testsPage() {
  const [, manual] = await Promise.all([loadCommon(), api("/api/manual").catch(() => ({scenarios: []}))]);
  if (state.page !== "tests" || state.arg) return;
  if (state.query.module) filters.module = state.query.module;
  const tests = state.tests;
  const modules = [...new Set(tests.map((t) => t.module).filter(Boolean))].sort();
  let columns = remember("testColumns") || DEFAULT_COLUMNS;
  const body = h("div", {});
  const count = (k) => tests.filter((t) => k === "all" || lastStatus(t) === k || (k === "passed" && lastStatus(t) === "healed")).length;

  const draw = () => {
    const q = filters.q.toLowerCase();
    const shown = tests.filter((t) =>
      (filters.module === "all" || t.module === filters.module) &&
      (filters.status === "all" || lastStatus(t) === filters.status || (filters.status === "passed" && lastStatus(t) === "healed")) &&
      (!q || [t.title, t.file, t.id, t.module, t.product, t.process, t.persona, t.owner, ...(t.tags || [])].join(" ").toLowerCase().includes(q)));
    const extra = OPTIONAL.filter(([k]) => columns.includes(k));
    body.replaceChildren(shown.length ? table({
      caption: "Tests",
      rows: shown,
      onRow: (t) => { location.hash = testLink(t.file); },
      rowHref: (t) => testLink(t.file),
      columns: [
        {label: "Test", render: (t) => [h("div", {class: "primary-cell"}, t.title || t.file), h("div", {class: "sub"}, t.file),
          t.problem ? h("div", {class: "sub", style: "color:var(--danger)"}, t.problem) : null]},
        {label: "Module", render: (t) => [h("div", {}, t.module || "–"), t.product ? h("div", {class: "sub"}, t.product) : null]},
        {label: "Steps", cls: "num", render: (t) => t.steps ?? "–"},
        {label: "Last result", render: (t) => t.stability?.flaky
          ? h("span", {class: "row", style: "gap:6px"}, statusBadge(lastStatus(t)),
            h("span", {class: "tag", title: `Needed a retry to pass in ${t.stability.flaky_runs} of its last ${t.stability.runs} runs`}, "Flaky"))
          : statusBadge(lastStatus(t))},
        ...extra.map(([k, label, render]) => ({label, render, cls: ["last_run", "updated", "duration"].includes(k) ? "nowrap" : null})),
        {srLabel: "Run", cls: "actions", render: (t) => button("Run", {size: "sm", ic: "runs", disabled: !state.status.ready || Boolean(t.problem),
          title: `Run ${t.title || t.file}`, onClick: () => openRunDrawer(t.file)})},
      ],
    }) : emptyState(tests.length
      ? {ic: "search", title: "No tests match", text: "Try another search or filter.", actions: button("Clear filters", {size: "sm", onClick: () => {
        Object.assign(filters, {q: "", status: "all", module: "all"}); location.hash = "#/tests"; testsPage(); }})}
      : {ic: "tests", tone: "primary", title: "No tests yet", text: `Record one, or add YAML test files to ${state.status.tests_folder}.`,
        actions: button("Record a test", {kind: "primary", size: "sm", ic: "record", href: "#/record"})}));
  };

  const columnsBtn = button("Columns", {ic: "columns", onClick: (e) => popover(e.currentTarget, () => h("div", {class: "pop-body", style: "gap:2px"},
    h("div", {class: "caption", style: "padding:0 8px 6px"}, "SHOW COLUMNS"),
    OPTIONAL.map(([k, label]) => h("label", {class: "check-row"}, h("input", {type: "checkbox", checked: columns.includes(k), onchange: (ev) => {
      columns = ev.target.checked ? [...columns, k] : columns.filter((c) => c !== k);
      remember("testColumns", columns);
      draw();
    }}), label)))), attrs: {"aria-haspopup": "dialog"}});

  draw();
  show([{label: "Tests"}],
    h("div", {class: "page-head"},
      h("div", {}, h("h1", {}, "Tests"), h("p", {class: "lead"}, `${plural(tests.length, "test")} in `, h("code", {}, state.status.tests_folder))),
      h("div", {class: "row"}, manual.scenarios.length ? null : button("Import manual scripts", {ic: "download", onClick: () => openManualImport()}),
        button("Record a test", {ic: "record", href: "#/record"}),
        button("Export", {ic: "download", href: "/api/export", title: "Download all tests as plain Playwright files that need no Quartermaster"}),
        button("Run all", {kind: "primary", ic: "runs", disabled: !state.status.ready, onClick: () => openRunDrawer(".")}))),
    manual.scenarios.length ? h("div", {class: "toolbar"}, testsViewSwitch("automated", {automated: tests.length, manual: manual.scenarios.length,
      review: manual.scenarios.filter((s) => s.review === "needs_review").length})) : null,
    h("div", {class: "toolbar"},
      h("div", {class: "search"}, icon("search"), input({type: "search", value: filters.q, placeholder: "Search name, module, process, role or tag",
        "aria-label": "Search tests", oninput: (e) => { filters.q = e.target.value; draw(); }})),
      chips([["all", "All", count("all")], ["failed", "Failing", count("failed")], ["passed", "Passing", count("passed")],
        ["never", "Never run", count("never")], ["error", "Unreadable", count("error")]].filter(([k, , n]) => k === "all" || n),
      filters.status, (v) => { filters.status = v; draw(); }, "Last result"),
      h("span", {class: "grow"}), columnsBtn),
    modules.length > 1 ? h("div", {class: "toolbar"}, chips([["all", "All modules"], ...modules.map((m) => [m, m, tests.filter((t) => t.module === m).length])],
      filters.module, (v) => { filters.module = v; draw(); }, "Module")) : null,
    h("div", {class: "card"}, body));
}

// ------------------------------------------------------------------ one test

const ACTIONS = {navigate: "Open", click: "Click", fill: "Type", select: "Choose", assert_text: "Check", assert_visible: "Check",
  login_as: "Sign in as", wait_job: "Wait for", api_call: "Call"};

export async function testPage(file) {
  const [, t] = await Promise.all([loadCommon(), api("/api/test?file=" + encodeURIComponent(file))]);
  if (location.hash !== testLink(file)) return;
  let tab = remember("testTab") || "steps";
  const panel = h("div", {class: "tab-panel", role: "tabpanel"});
  const draw = () => {
    remember("testTab", tab);
    panel.setAttribute("aria-labelledby", `tab-${tab}`);
    if (tab === "steps") {
      panel.replaceChildren(...((t.setup || []).length ? [callout("info", "Checked or made on the pod first.",
        "If one of these fails, the steps below do not run and the pod's data is blamed, not the release.",
        h("ol", {style: "margin:6px 0 0;padding-left:20px"}, t.setup.map((c) => h("li", {}, c.intent, c.value ? [" ", h("code", {}, c.value)] : null))))] : []),
        h("ol", {class: "timeline"}, t.steps_detail.map((st) => h("li", {},
        h("span", {class: "node"}, st.number),
        h("div", {style: "min-width:0"},
          h("div", {class: "row", style: "gap:8px"}, h("span", {class: "tag"}, ACTIONS[st.action] || st.action), h("span", {}, st.intent),
            st.shared ? h("a", {class: "tag", href: "#/library", title: "This step comes from shared steps"}, `shared: ${st.shared}`) : null),
          st.value ? h("div", {class: "meta", style: "margin-top:4px"}, st.action === "navigate" ? "Page " : "Value ", h("code", {}, st.value)) : null,
          st.found_by.length ? h("div", {class: "meta", style: "margin-top:4px"}, "Found by ", st.found_by[0],
            st.found_by.length > 1 ? `, or else ${st.found_by.slice(1).join(", or ")}` : "") : null),
        h("span", {})))));
    } else if (tab === "data") {
      const keys = Object.keys(t.data || {});
      const more = (t.pods || []).length || (t.generate || []).length;
      panel.replaceChildren(keys.length || more ? h("div", {class: "stack"}, keys.length ? h("dl", {class: "kv"}, keys.flatMap((k) => [h("dt", {}, k),
        h("dd", {}, String(t.data[k]).startsWith("${env:") ? h("span", {class: "row", style: "gap:6px"}, icon("lock"), "Masked; read from ", h("code", {}, String(t.data[k]).slice(6, -1))) : String(t.data[k]))])) : null,
      h("p", {class: "hint"}, "Steps use these values as ${name}. Change them in the file to test with other data."),
      (t.data_sets || []).length ? h("p", {class: "hint"}, "Data sets used: ", t.data_sets.map((n, i) => [i ? ", " : "", h("a", {href: "#/data"}, n)]), ".") : null,
      (t.pods || []).length ? h("div", {}, h("div", {class: "label"}, "Different on some pods"),
        h("dl", {class: "kv"}, t.pods.flatMap((p) => [h("dt", {}, p.pod), h("dd", {}, Object.entries(p.values).map(([k, v]) => `${k} = ${v}`).join(", "))]))) : null,
      (t.generate || []).length ? h("div", {}, h("div", {class: "label"}, "Made fresh for every run"),
        h("dl", {class: "kv"}, t.generate.flatMap((g) => [h("dt", {}, g.name), h("dd", {}, g.rule)]))) : null) :
        emptyState({ic: "tests", title: "No test data", text: "Values this test types are written in its steps."}));
    } else if (tab === "history") {
      panel.replaceChildren(t.history.length ? table({
        caption: "Run history of this test", rows: t.history, onRow: (r) => { location.hash = runLink(r.run_id); }, rowHref: (r) => runLink(r.run_id),
        columns: [
          {label: "Result", render: (r) => statusBadge(r.status)},
          {label: "When", render: (r) => when(r.at)},
          {label: "Release", render: (r) => r.release ? h("span", {class: "release"}, r.release) : dash()},
          {label: "Steps passed", cls: "num", render: (r) => `${r.steps_passed} of ${r.steps_total}`},
          {label: "Duration", render: (r) => r.duration || "–"},
          {srLabel: "Evidence", cls: "actions", render: (r) => r.document_url ? button("", {size: "sm", ic: "file", href: r.document_url, title: "Evidence document"}) : null},
        ]}) : emptyState({ic: "clock", title: "Not run yet", text: "Run it to start its history.",
        actions: button("Run it", {size: "sm", kind: "primary", ic: "runs", disabled: !state.status.ready, onClick: () => openRunDrawer(t.file)})}));
    } else {
      panel.replaceChildren(h("div", {class: "row", style: "justify-content:space-between;margin-bottom:8px"},
        h("span", {class: "meta"}, "The test as written. Edit it in any text editor."),
        button("Copy", {size: "sm", ic: "copy", onClick: () => navigator.clipboard.writeText(t.yaml).then(() => toast("Copied"), () => toast("Could not copy"))})),
      h("pre", {class: "block", style: "max-height:560px"}, t.yaml));
    }
  };
  draw();
  const last = t.history[0];
  show([{label: "Tests", href: "#/tests"}, {label: t.title || t.file}],
    h("div", {class: "page-head"},
      h("div", {style: "min-width:0"},
        h("div", {class: "row", style: "margin-bottom:6px"}, t.module ? h("span", {class: "tag"}, t.module) : null, t.process ? h("span", {class: "tag"}, t.process) : null,
          t.release_validated ? releaseBadge(t.release_validated, {prefix: "Validated on"}) : null),
        h("h1", {}, t.title || t.file),
        h("p", {class: "lead"}, [t.product, t.persona ? `runs as ${t.persona}` : "", t.priority ? `${t.priority} priority` : ""].filter(Boolean).join(" · ") || t.file),
        (t.tickets || []).length ? h("div", {class: "row", style: "gap:8px;margin-top:6px"}, h("span", {class: "meta"}, "Tickets"), ticketChips(t.tickets)) : null),
      h("div", {class: "row"},
        t.problem ? null : button("Make a copy", {ic: "copy", onClick: () => openCopy(t), title: "Copy this test under a new id, for another client or another case"}),
        t.problem ? null : button("Export", {ic: "download", href: `/api/export?file=${encodeURIComponent(t.file)}`, title: "Download this test as a plain Playwright file that needs no Quartermaster"}),
        button("Run this test", {kind: "primary", ic: "runs", disabled: !state.status.ready || Boolean(t.problem), onClick: () => openRunDrawer(t.file)}))),
    t.problem ? h("div", {style: "margin-bottom:16px"}, callout("danger", "This file could not be read.", t.problem)) : null,
    h("div", {class: "grid g-4"},
      card({body: h("div", {class: "stack", style: "gap:6px"}, h("span", {class: "meta"}, "Last result"), last ? statusBadge(last.status) : statusBadge("never"),
        h("span", {class: "meta"}, last ? when(last.at) : "Never run"))}),
      card({body: h("div", {class: "stack", style: "gap:2px"}, h("span", {class: "meta"}, "Passed"),
        h("span", {style: "font-size:24px;font-weight:600", class: "num"}, t.pass_rate === null ? "–" : `${t.pass_rate}%`), h("span", {class: "meta"}, `of ${plural(t.history.length, "run")}`))}),
      card({body: h("div", {class: "stack", style: "gap:2px"}, h("span", {class: "meta"}, "Steps"),
        h("span", {style: "font-size:24px;font-weight:600", class: "num"}, t.steps ?? 0), h("span", {class: "meta"}, t.id))}),
      card({body: h("div", {class: "stack", style: "gap:2px"}, h("span", {class: "meta"}, "File"),
        h("span", {style: "font-weight:500;overflow-wrap:anywhere"}, t.file), h("span", {class: "meta"}, t.updated ? `Updated ${when(t.updated).toLowerCase()}` : ""))})),
    h("div", {class: "card section"},
      tabs([["steps", "Steps", t.steps_detail.length], ["data", "Test data"], ["history", "History", t.history.length], ["file", "File"]], tab, (v) => { tab = v; draw(); }),
      panel));
}

// A copy of a test with its own id and title. The values that differ by client live in the data set, not here.
function openCopy(t) {
  const dir = t.file.includes("/") ? t.file.slice(0, t.file.lastIndexOf("/")) : "";
  const id = input({value: `${t.id}-copy`, autofocus: true});
  const title = input({value: `${t.title} (copy)`});
  const folder = input({value: dir});
  const error = h("div", {});
  drawer({
    title: "Make a copy of this test",
    sub: "Everything is copied, including the data sets it uses. Change the values in Test data.",
    body: () => h("div", {class: "stack", style: "gap:12px"},
      field("Id (letters, digits, dashes)", id, "Must not be used by another test."),
      field("Title", title),
      field("Folder", folder, "Inside the tests folder. Leave it as it is to keep the copy next to the original."),
      error),
    foot: (close) => h("div", {class: "row"}, button("Make the copy", {kind: "primary", onClick: async () => {
      error.replaceChildren();
      try {
        const r = await api("/api/test/duplicate", {file: t.file, id: id.value, title: title.value, folder: folder.value});
        close(); toast("Copied"); location.hash = testLink(r.file);
      } catch (e) { error.replaceChildren(callout("danger", "Could not copy.", e.message)); }
    }}), button("Cancel", {onClick: close})),
  });
}
