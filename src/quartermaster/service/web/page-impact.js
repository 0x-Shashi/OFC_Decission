// Release impact: which tests a new Oracle update puts at risk, why, and which of its features no test covers.
import {
  api, badge, button, callout, chips, disclose, drawer, emptyState, h, input, plural, popover, remember, table, tabs, toast,
} from "./ui.js";
import {metric, stackedBar} from "./components.js";
import {loadCommon, runLink, state, testLink} from "./state.js";
import {show} from "./app.js";
import {manualLink, openManualImport, openScenario} from "./page-manual.js";

const COVERAGE = {
  covered: {label: "Covered", tone: "success", ic: "check", text: "An automated test exercises this feature."},
  manual: {label: "Manual only", tone: "info", ic: "file", text: "Only a manual script covers this feature. A candidate to automate."},
  weak: {label: "Weak", tone: "warning", ic: "minus", text: "Only loosely related tests; none really exercises it."},
  none: {label: "No test", tone: "danger", ic: "x", text: "No test relates to this feature. A candidate for a new test."},
};
const PRIORITY_TONE = {critical: "danger", high: "warning", medium: "neutral", low: "neutral"};

let view = {tab: "tests", features: "all", manual: "covers", picked: null, pickedFor: ""};

function impactLink(name, extra = {}) {
  const q = new URLSearchParams({release: name, ...extra});
  for (const [k, v] of [...q]) if (!v) q.delete(k);
  return "#/impact?" + q.toString();
}

export async function impactPage() {
  const [, releases] = await Promise.all([loadCommon(), api("/api/releases")]);
  if (state.page !== "impact") return;
  const usable = releases.filter((r) => !r.problem);
  const name = state.query.release && usable.some((r) => r.name === state.query.release) ? state.query.release
    : usable.some((r) => r.name === remember("impactRelease")) ? remember("impactRelease") : usable[0]?.name;
  const head = (actions) => h("div", {class: "page-head"},
    h("div", {}, h("h1", {}, "Release impact"),
      h("p", {class: "lead"}, "Which tests a new Oracle update puts at risk, and which of its features no test covers yet.")),
    h("div", {class: "row"}, actions));
  const importBtn = button("Import feature list", {ic: "download", onClick: () => openImport(), kind: name ? "" : "primary"});

  if (!name) {
    show([{label: "Release impact"}], head(importBtn),
      h("div", {class: "card"}, emptyState({ic: "target", tone: "primary", title: "No release feature list yet",
        text: "Import the feature list of an Oracle update (the spreadsheet from Oracle Cloud Readiness, or any sheet with Feature and Product columns). Quartermaster then shows which of your tests the update puts at risk.",
        actions: button("Import feature list", {kind: "primary", size: "sm", ic: "download", onClick: () => openImport()})})),
      releases.length ? h("div", {class: "section"}, problems(releases)) : null);
    return;
  }
  remember("impactRelease", name);
  const budget = state.query.budget || "";
  const optIns = (state.query.opt_in || "").split(",").filter(Boolean);
  const plan = await api(`/api/releases/plan?name=${encodeURIComponent(name)}&budget=${encodeURIComponent(budget)}&opt_in=${encodeURIComponent(optIns.join(","))}`);
  if (state.page !== "impact") return;

  // The reader's own choice of tests survives redraws of the same plan; a new plan starts from its selection.
  const key = `${name}|${budget}|${optIns.join(",")}`;
  if (view.pickedFor !== key) view = {...view, picked: new Set(plan.tests.filter((t) => t.selected).map((t) => t.id)), pickedFor: key};
  const picked = view.picked;
  const s = plan.summary;

  const releaseSel = h("select", {class: "input", "aria-label": "Release feature list", style: "width:auto;min-width:180px",
    onchange: (e) => { view.pickedFor = ""; location.hash = impactLink(e.target.value); }},
  usable.map((r) => h("option", {value: r.name, selected: r.name === name},
    `${r.id} · ${plural(r.features, "feature")}${r.source === "imported" ? "" : " (example)"}`)));

  const metrics = h("div", {class: "grid g-4"},
    metric({ic: "layers", label: "Features in " + plan.release, value: s.features, accent: true,
      bar: stackedBar([{n: s.covered, cls: "fill-success", label: "Covered"}, {n: s.manual, cls: "fill-info", label: "Manual only"},
        {n: s.weak, cls: "fill-warning", label: "Weak"}, {n: s.none, cls: "fill-danger", label: "No test"}],
      `${s.covered} covered, ${s.manual} manual only, ${s.weak} weak, ${s.none} with no test`),
      foot: [`${s.covered} covered`, s.manual ? `${s.manual} manual only` : "", `${s.weak} weak`, `${s.none} with no test`].filter(Boolean).join(" · ")}),
    metric({ic: "attention", label: "Tests at risk", value: s.at_risk, foot: `of ${plural(s.tests, "test")} relate to this update`}),
    metric({ic: "runs", label: "Suggested run", value: s.selected, unit: s.selected === 1 ? " test" : " tests",
      foot: `about ${minutes(s.minutes)}${plan.budget ? ` · limit ${minutes(plan.budget)}` : ""}`}),
    metric({ic: "x", label: "Features with no test", value: s.none,
      foot: s.none ? "Candidates for new tests" : "Every feature has a related test"}));
  const manualNote = s.manual_scenarios ? null : h("div", {class: "section"}, callout("info", "Manual scripts are not counted yet.",
    ["Import your Excel test scripts and this page also shows which manual scenarios cover each feature. ",
      h("a", {href: "#", onclick: (e) => { e.preventDefault(); openManualImport(); }}, "Import manual scripts")]));

  const optInFeatures = plan.features.filter((f) => f.opt_in);
  const budgetInput = input({type: "number", min: "1", step: "1", value: budget, placeholder: "No limit", style: "width:110px",
    "aria-label": "Time limit in minutes"});
  const applyBudget = () => { if (budgetInput.value !== budget) location.hash = impactLink(name, {budget: budgetInput.value, opt_in: optIns.join(",")}); };
  budgetInput.onchange = applyBudget;
  budgetInput.onkeydown = (e) => { if (e.key === "Enter") applyBudget(); };
  const optInBtn = optInFeatures.length ? button(`Opt-ins switched on: ${optIns.length}`, {ic: "settings",
    attrs: {"aria-haspopup": "dialog"},
    onClick: (e) => popover(e.currentTarget, () => h("div", {class: "pop-body", style: "gap:2px;max-width:380px"},
      h("div", {class: "caption", style: "padding:0 8px 6px"}, "OPT-IN FEATURES YOU HAVE SWITCHED ON"),
      optInFeatures.map((f) => h("label", {class: "check-row"}, h("input", {type: "checkbox", checked: optIns.includes(f.id), onchange: (ev) => {
        const next = ev.target.checked ? [...optIns, f.id] : optIns.filter((x) => x !== f.id);
        location.hash = impactLink(name, {budget, opt_in: next.join(",")});
      }}), h("span", {class: "ellipsis"}, f.title))),
      h("div", {class: "hint", style: "padding:6px 8px 0"}, "A switched-on opt-in raises the risk of the tests near it.")))}) : null;

  const panel = h("div", {class: "tab-panel", role: "tabpanel"});
  const runBar = h("div", {class: "row", style: "gap:12px"});
  const drawRunBar = () => {
    const chosen = plan.tests.filter((t) => picked.has(t.id));
    const mins = chosen.reduce((a, t) => a + t.minutes, 0);
    runBar.replaceChildren(
      h("span", {class: "meta"}, `${plural(chosen.length, "test")} chosen · about ${minutes(mins)}`),
      button(`Run ${plural(chosen.length, "test")}`, {kind: "primary", ic: "play", disabled: !state.status.ready || !chosen.length,
        title: state.status.ready ? null : "Set up the pod in Settings first", onClick: (e) => runChosen(e, plan, chosen)}));
  };

  const draw = () => {
    panel.setAttribute("aria-labelledby", `tab-${view.tab}`);
    if (view.tab === "tests") panel.replaceChildren(testsTable(plan, picked, drawRunBar));
    else if (view.tab === "features") panel.replaceChildren(featuresTable(plan, draw));
    else if (view.tab === "manual") panel.replaceChildren(manualTable(plan, draw));
    else panel.replaceChildren(problems(plan.problems.map((p) => ({name: p.file, problem: p.problem}))));
    drawRunBar();
  };
  draw();

  show([{label: "Release impact"}],
    head([releaseSel, importBtn]),
    metrics,
    manualNote,
    h("div", {class: "card section"},
      h("div", {class: "toolbar", style: "padding:12px 16px 0;margin:0"},
        tabs([["tests", "Tests to run", plan.tests.length], ["features", "Features", plan.features.length],
          ...(s.manual_scenarios ? [["manual", "Manual scenarios", plan.manual.filter((m) => m.features.length).length]] : []),
          ...(plan.problems.length ? [["problems", "Unreadable tests", plan.problems.length]] : [])], view.tab, (v) => { view.tab = v; draw(); }),
        h("span", {class: "grow"}),
        h("label", {class: "row", style: "gap:8px"}, h("span", {class: "meta"}, "Time limit (minutes)"), budgetInput),
        optInBtn),
      panel,
      h("div", {class: "row", style: "justify-content:space-between;padding:12px 16px;border-top:1px solid var(--border)"},
        h("span", {class: "hint", style: "margin:0"}, "Suggested by product, module, tags and shared words, weighted by priority. Critical tests are always suggested."),
        runBar)));
}

function minutes(n) {
  return `${Math.round(n * 10) / 10} min`;
}

function testsTable(plan, picked, onChange) {
  if (!plan.tests.length) {
    return emptyState({ic: "tests", title: "No tests to compare", text: `Add test files to ${state.status.tests_folder}, or record one.`,
      actions: button("Record a test", {size: "sm", href: "#/record"})});
  }
  const all = h("input", {type: "checkbox", "aria-label": "Choose all tests"});
  const syncAll = () => {
    all.checked = plan.tests.every((t) => picked.has(t.id));
    all.indeterminate = !all.checked && plan.tests.some((t) => picked.has(t.id));
  };
  const boxes = [];
  all.onchange = () => {
    plan.tests.forEach((t) => (all.checked ? picked.add(t.id) : picked.delete(t.id)));
    boxes.forEach((b) => { b.checked = all.checked; });
    onChange();
  };
  syncAll();
  return table({
    caption: `Tests for release ${plan.release}, most at risk first`,
    rows: plan.tests,
    columns: [
      {label: all, width: "36px", render: (t) => {
        const box = h("input", {type: "checkbox", checked: picked.has(t.id), "aria-label": `Run ${t.title}`,
          onchange: (e) => { e.target.checked ? picked.add(t.id) : picked.delete(t.id); syncAll(); onChange(); }});
        boxes.push(box);
        return box;
      }},
      {label: "Test", render: (t) => [h("a", {href: testLink(t.file), class: "primary-cell", style: "color:inherit"}, t.title),
        h("div", {class: "sub"}, t.file)]},
      {label: "Module", render: (t) => [h("div", {}, t.module), h("div", {class: "sub"}, t.product)]},
      {label: "Priority", render: (t) => h("div", {class: "stack", style: "gap:4px;align-items:flex-start"},
        badge(t.priority, PRIORITY_TONE[t.priority] || "neutral"),
        t.selected ? h("span", {class: "meta"}, t.always ? "Always run" : "Suggested") : null)},
      {label: "Risk", render: (t) => h("div", {class: "row", style: "flex-wrap:nowrap;gap:8px;min-width:120px"},
        h("div", {class: "bar grow", role: "img", "aria-label": `Risk ${Math.round(t.risk * 100)}%`},
          h("span", {class: t.risk >= 0.5 ? "fill-danger" : t.risk >= 0.2 ? "fill-warning" : "fill-neutral", style: `width:${Math.round(t.risk * 100)}%`})),
        h("span", {class: "num meta", style: "width:36px;text-align:right"}, `${Math.round(t.risk * 100)}%`))},
      {label: "Min", cls: "num", render: (t) => t.minutes},
      {label: "Why", width: "240px", render: (t) => t.reasons.length
        ? disclose(t.features.length ? `Covers ${t.features.join(", ")}` : `Loosely related to ${plural(t.reasons.length, "feature")}`,
          h("ul", {class: "stack", style: "gap:6px;margin:6px 0 0;padding-left:18px"}, t.reasons.map((r) => h("li", {class: "meta"}, r))))
        : h("span", {class: "muted"}, t.always ? "Critical: always run" : "Nothing in this update relates to it")},
    ],
  });
}

function featuresTable(plan, redraw) {
  const counts = Object.fromEntries(Object.keys(COVERAGE).map((k) => [k, plan.features.filter((f) => f.coverage === k).length]));
  const onPod = plan.features.filter((f) => (f.pod_pages || []).length).length;
  const shown = plan.features.filter((f) => view.features === "all" || (view.features === "pod" ? (f.pod_pages || []).length : f.coverage === view.features));
  const byId = Object.fromEntries(plan.tests.map((t) => [t.id, t]));
  return h("div", {},
    h("div", {class: "toolbar", style: "padding:12px 16px 0;margin:0"},
      chips([["all", "All", plan.features.length], ["none", "No test", counts.none], ["weak", "Weak", counts.weak],
        ...(counts.manual ? [["manual", "Manual only", counts.manual]] : []), ["covered", "Covered", counts.covered],
        ...(plan.pod_pages ? [["pod", "On your pod", onPod]] : [])],
        view.features, (v) => { view.features = v; redraw(); }, "Coverage")),
    shown.length ? table({
      caption: `Features of release ${plan.release}`,
      rows: shown,
      columns: [
        {label: "Feature", render: (f) => [h("div", {class: "primary-cell"}, f.title), h("div", {class: "sub"}, f.id),
          (f.pod_pages || []).length ? h("div", {class: "sub", title: "Pages found in your pod's Navigator by pod discovery"}, `On your pod: ${f.pod_pages.join(", ")}`) : null]},
        {label: "Product", render: (f) => [h("div", {}, f.product), h("div", {class: "sub"}, f.module)]},
        {label: "Change", render: (f) => h("div", {class: "row", style: "gap:4px"}, h("span", {class: "tag"}, f.change_type),
          f.opt_in ? badge("Opt-in", "info") : null, f.action_required ? badge("Action needed", "warning") : null)},
        {label: "Coverage", render: (f) => h("span", {title: COVERAGE[f.coverage].text}, badge(COVERAGE[f.coverage].label, COVERAGE[f.coverage].tone, COVERAGE[f.coverage].ic))},
        {label: "Related tests", render: (f) => f.tests.length || f.manual?.length
          ? h("div", {class: "stack", style: "gap:2px"},
            f.tests.slice(0, 3).map((t) => byId[t.id]
              ? h("a", {href: testLink(byId[t.id].file), class: "ellipsis", style: "max-width:260px"}, byId[t.id].title) : h("span", {}, t.id)),
            f.tests.length > 3 ? h("span", {class: "meta"}, `and ${f.tests.length - 3} more`) : null,
            (f.manual || []).slice(0, f.tests.length ? 1 : 2).map((m) => h("a", {href: manualLink(m.id), class: "ellipsis", style: "max-width:260px",
              onclick: (e) => { e.preventDefault(); openScenario(m.id); }}, h("span", {class: "tag", style: "margin-right:6px"}, "Manual"), m.title)))
          : h("span", {class: "muted"}, "None")},
      ],
    }) : emptyState({ic: "check", title: "Nothing here", text: "No feature has this coverage."}));
}

function manualTable(plan, redraw) {
  if (!plan.manual.length) return emptyState({ic: "check", title: "No manual scenario relates to this update"});
  const covering = plan.manual.filter((m) => m.features.length);
  const rows = view.manual === "covers" ? covering : plan.manual;
  return h("div", {},
    h("div", {class: "toolbar", style: "padding:12px 16px 0;margin:0"},
      chips([["covers", "Cover a feature", covering.length], ["all", "Also loosely related", plan.manual.length]],
        view.manual, (v) => { view.manual = v; redraw(); }, "Which manual scenarios"),
      h("span", {class: "meta"}, "Run these by hand for this update, most at risk first.")),
    rows.length ? manualRows(plan, rows) : emptyState({ic: "check", title: "No manual scenario covers a feature of this update",
      text: "Some are loosely related; choose Also loosely related to see them."}));
}

function manualRows(plan, rows) {
  return table({
    caption: `Manual scenarios for release ${plan.release}, most at risk first`,
    rows,
    onRow: (m) => openScenario(m.id),
    columns: [
      {label: "Scenario", render: (m) => [h("div", {class: "primary-cell"}, m.title), h("div", {class: "sub"}, `${m.ref} · ${m.file}`)]},
      {label: "Module", render: (m) => [h("div", {}, m.module), h("div", {class: "sub"}, m.product)]},
      {label: "Cases", cls: "num", render: (m) => m.cases},
      {label: "Steps", cls: "num", render: (m) => m.steps},
      {label: "Risk", render: (m) => h("div", {class: "row", style: "flex-wrap:nowrap;gap:8px;min-width:120px"},
        h("div", {class: "bar grow", role: "img", "aria-label": `Risk ${Math.round(m.risk * 100)}%`},
          h("span", {class: m.risk >= 0.5 ? "fill-danger" : m.risk >= 0.2 ? "fill-warning" : "fill-neutral", style: `width:${Math.round(m.risk * 100)}%`})),
        h("span", {class: "num meta", style: "width:36px;text-align:right"}, `${Math.round(m.risk * 100)}%`))},
      {label: "Why", width: "240px", render: (m) => disclose(m.features.length ? `Covers ${m.features.join(", ")}` : "Loosely related",
        h("ul", {class: "stack", style: "gap:6px;margin:6px 0 0;padding-left:18px"}, m.reasons.map((r) => h("li", {class: "meta"}, r))))},
    ],
  });
}

function problems(list) {
  const bad = list.filter((r) => r.problem);
  if (!bad.length) return emptyState({ic: "check", title: "Every file could be read"});
  return callout("warning", `${plural(bad.length, "file")} could not be read and ${bad.length === 1 ? "is" : "are"} left out.`, null,
    h("ul", {style: "margin:6px 0 0;padding-left:18px"}, bad.map((r) => h("li", {}, h("code", {}, r.name), ": ", r.problem))));
}

async function runChosen(e, plan, chosen) {
  const saved = remember("options") || {};
  const options = {screenshots: saved.screenshots || state.status.default_options.screenshots, video: saved.video || "off",
    headed: Boolean(saved.headed), highlight: saved.highlight !== false, tester: saved.tester || "", evidence_doc: true,
    only: chosen.map((t) => t.id), label: `Release ${plan.release} impact · ${plural(chosen.length, "test")}`};
  e.currentTarget.disabled = true;
  try {
    const run = await api("/api/runs", {target: ".", options});
    location.hash = runLink(run.id);
  } catch (err) {
    toast(err.message);
    e.currentTarget.disabled = false;
  }
}

// ------------------------------------------------------------------ import

export function openImport() {
  let file = null, content = "", preview = null;
  const fileInput = h("input", {type: "file", class: "input", accept: ".xlsx,.xlsm,.csv,.json,.yaml,.yml,.html,.htm,.txt,.md", "aria-describedby": "imp-hint"});
  const releaseId = input({placeholder: "e.g. 26C", style: "max-width:140px", maxlength: "3", "aria-describedby": "imp-rel-hint"});
  const paste = h("textarea", {class: "input", rows: "5", "aria-label": "Pasted What's New text", id: "imp-paste",
    placeholder: "Or paste the text of the What's New page here", style: "width:100%;font:inherit;font-size:13px"});
  const result = h("div", {class: "stack", "aria-live": "polite"});
  const saveBtn = button("Save feature list", {kind: "primary", ic: "check", disabled: true});
  const checkBtn = button("Check file", {ic: "search", disabled: true});

  fileInput.onchange = () => {
    file = fileInput.files[0] || null;
    preview = null;
    saveBtn.disabled = true;
    result.replaceChildren();
    checkBtn.disabled = !file;
    if (!file) return;
    const guess = file.name.toUpperCase().match(/(?:^|[^0-9])(\d{2}[A-D])(?![A-Z])/);
    if (guess && !releaseId.value) releaseId.value = guess[1];
    const reader = new FileReader();
    reader.onload = () => { content = String(reader.result).split(",", 2)[1] || ""; check(); };
    reader.onerror = () => result.replaceChildren(callout("danger", "The file could not be read.", null));
    reader.readAsDataURL(file);
  };
  paste.oninput = () => {
    preview = null;
    saveBtn.disabled = true;
    checkBtn.disabled = !(file || paste.value.trim());
  };
  // what is sent: the chosen file, or else the pasted text as a text file
  const source = () => {
    if (file) return {name: file.name, content};
    const bytes = new TextEncoder().encode(paste.value);
    let binary = "";
    bytes.forEach((b) => { binary += String.fromCharCode(b); });
    return {name: "pasted.txt", content: btoa(binary)};
  };
  releaseId.oninput = () => { saveBtn.disabled = true; preview = null; };
  releaseId.onchange = () => { if (content) check(); };

  async function check() {
    checkBtn.disabled = true;
    result.replaceChildren(h("div", {class: "skel", style: "height:80px"}));
    try {
      preview = await api("/api/releases/import", {...source(), release_id: releaseId.value.trim()});
      if (!releaseId.value) releaseId.value = preview.id;
      result.replaceChildren(previewView(preview));
      saveBtn.disabled = false;
    } catch (err) {
      result.replaceChildren(callout("danger", "This file cannot be used yet.", err.message));
    } finally {
      checkBtn.disabled = !(file || paste.value.trim());
    }
  }
  checkBtn.onclick = check;

  drawer({
    title: "Import a feature list",
    sub: "The features of one Oracle update, so Quartermaster can match them to your tests.",
    body: (close) => {
      saveBtn.onclick = async () => {
        saveBtn.disabled = true;
        try {
          const saved = await api("/api/releases/import", {...source(), release_id: releaseId.value.trim(), save: true});
          toast(`Saved ${plural(saved.features, "feature")} of release ${saved.id}.`);
          close();
          view.pickedFor = "";
          const target = impactLink(saved.name);
          if (location.hash === target) impactPage(); else location.hash = target;
        } catch (err) { toast(err.message); saveBtn.disabled = false; }
      };
      return [
        field2("File", fileInput, h("span", {id: "imp-hint"}, "An Excel sheet (.xlsx) or CSV with a Feature column, ideally also Product, Product Family and Customer Must Take Action. Or a What's New page saved from your browser (.html: File, Save page as, Webpage HTML only). Or a Quartermaster release file (.json or .yaml).")),
        field2("Or paste text", paste, "Copy the text of the What's New page and paste it. A saved .html page keeps its tables and headings, so it reads better."),
        field2("Oracle release", releaseId, h("span", {id: "imp-rel-hint"}, "The update these features are in. Rows for other updates are left out.")),
        callout("info", "Nothing is saved until you choose Save.", "The file stays on this computer; it is read here and kept in the Quartermaster data folder."),
        result,
      ];
    },
    foot: (close) => [h("span", {class: "grow"}), button("Cancel", {onClick: close}), checkBtn, saveBtn],
  });
}

function field2(label, control, hint) {
  return h("div", {}, h("div", {class: "label"}, label), control, h("div", {class: "hint"}, hint));
}

function previewView(p) {
  const NAMES = {title: "Feature", description: "Description", product: "Product", module: "Module", id: "Feature id", update: "Update",
    action: "Customer action", ready: "Ready to use", change_type: "Change type", tags: "Tags", redwood: "Redwood"};
  const cols = Object.entries(p.columns || {});
  return h("div", {class: "stack", style: "gap:12px"},
    callout("info", `${plural(p.features, "feature")} found for release ${p.id}.`,
      p.replaces ? `Saving replaces the ${p.name} list already here.` : `It will be saved as ${p.name}.`),
    cols.length ? h("div", {}, h("div", {class: "caption", style: "margin-bottom:6px"}, "COLUMNS USED"),
      h("dl", {class: "kv"}, cols.flatMap(([k, v]) => [h("dt", {}, NAMES[k] || k), h("dd", {}, h("code", {}, v))]))) : null,
    p.skipped.length ? disclose(`${plural(p.skipped.length, "row")} left out`,
      h("ul", {style: "margin:6px 0 0;padding-left:18px"}, p.skipped.slice(0, 50).map((s) => h("li", {class: "meta"}, s)),
        p.skipped.length > 50 ? h("li", {class: "meta"}, `and ${p.skipped.length - 50} more`) : null)) : null,
    h("div", {}, h("div", {class: "caption", style: "margin-bottom:6px"}, "FIRST FEATURES"),
      h("ul", {class: "stack", style: "gap:6px;margin:0;padding-left:18px"}, p.sample.map((f) => h("li", {},
        h("span", {style: "font-weight:500"}, f.title),
        h("span", {class: "meta"}, ` · ${f.product} · ${f.module}${f.opt_in ? " · opt-in" : ""} · ${f.change_type}`),
        f.description ? disclose("What the page says", h("p", {class: "meta", style: "margin:4px 0"}, f.description)) : null)))),
    h("p", {class: "hint", style: "margin:0"}, "Read from the page's own text, nothing summarised or guessed. Check that the products and the opt-in marks are right before saving."));
}
