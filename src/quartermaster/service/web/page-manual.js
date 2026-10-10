// Manual scenarios: test scripts imported from Excel, listed next to the automated tests and
// matched to release features. They cannot run by themselves; they tell testers what to do by hand.
import {
  api, badge, button, callout, chips, drawer, emptyState, h, icon, input, plural, popover, remember, statusBadge, table,
  toast, when,
} from "./ui.js";
import {loadCommon, runLink, state} from "./state.js";
import {show} from "./app.js";
import {startPrepareAll} from "./page-review.js";
import {openTypedScenario} from "./page-manual-typed.js";

const filters = {q: "", module: "all"};
const known = {modules: [], products: []}; // suggestions for a new typed scenario
const newScenario = (kind = "") => button("New scenario", {kind, size: kind ? "sm" : undefined, ic: "plus",
  title: "Type a scenario's steps here instead of importing a workbook", onClick: () => openTypedScenario(null, known)});

// Run a scenario: by hand the first time (or when asked), by itself once it has been done by hand.
export async function runScenario(id, {byHand = false, prepare = false} = {}) {
  if (!state.status?.ready) { toast("Set up the pod and its sign-in in Settings first."); return; }
  try {
    const saved = remember("options") || {}; // what New run was last started with
    const options = {screenshots: saved.screenshots || "every-step", video: saved.video || "off", headed: Boolean(saved.headed),
      highlight: saved.highlight !== false};
    const r = await api("/api/manual/run", {id, by_hand: byHand, prepare, options});
    document.querySelector(".scrim")?.click(); // close the scenario panel, if open
    location.hash = r.mode === "automatic" ? runLink(r.run.id) : "#/manual-run";
  } catch (err) { toast(err.message); }
}

function resultCell(s) {
  if (!s.qm_result) return h("span", {class: "muted"}, "Not run yet");
  const r = s.qm_result;
  return h("a", {href: runLink(r.run_id), style: "color:inherit;text-decoration:none", class: "stack", title: "Open the run"},
    statusBadge(r.status), h("span", {class: "sub"}, `${HOW[r.how] || "By itself"} · ${when(r.at)}`));
}

const HOW = {by_hand: "By hand", prepared: "Prepared by AI", automatic: "By itself"};

const aiReady = () => Boolean(state.status?.ai?.provider) && !state.status.ai.problem;

export async function approveScenario(id) {
  try {
    await api("/api/manual/approve", {id});
    toast("Approved. Run now plays it by itself.");
    document.querySelector(".scrim")?.click();
    if (location.hash.startsWith("#/tests")) manualPage(); else location.hash = "#/tests?view=manual";
  } catch (err) { toast(err.message); }
}

// The one action that fits where a scenario is: review a draft, run it, or get it ready.
function actionButtons(s, {size} = {}) {
  const off = !state.status.ready;
  if (s.review === "needs_review") {
    return [button("Review", {size, kind: "primary", ic: "search", title: "Check what the AI did, then approve it", onClick: () => openScenario(s.id)})];
  }
  if (s.automated) return [button("Run", {size, ic: "play", disabled: off, title: `Plays ${s.title} by itself`, onClick: () => runScenario(s.id)})];
  return [
    aiReady() ? button("Prepare", {size, kind: "primary", ic: "target", disabled: off, title: "An AI follows the written steps in the pod; you review it after",
      onClick: () => runScenario(s.id, {prepare: true})}) : null,
    button(aiReady() ? "By hand" : "Run by hand", {size, kind: aiReady() ? "" : "primary", ic: "play", disabled: off,
      title: `Do ${s.title} by hand; next time it plays by itself`, onClick: () => runScenario(s.id, {byHand: true})}),
  ];
}

export function manualLink(id) {
  return "#/tests?view=manual&open=" + encodeURIComponent(id);
}

// The Automated / Manual switch at the top of the Tests page.
export function testsViewSwitch(current, counts) {
  const views = [["automated", "Automated", counts.automated], ["manual", "Manual scenarios", counts.manual]];
  if (counts.review || current === "review") views.push(["review", "To review", counts.review || 0]);
  return chips(views, current, (v) => { location.hash = v === "automated" ? "#/tests" : `#/tests?view=${v}`; }, "Kind of test");
}

export async function manualPage() {
  const [, data] = await Promise.all([loadCommon(), api("/api/manual")]);
  if (state.page !== "tests" || state.query.view !== "manual") return;
  const all = data.scenarios;
  const modules = [...new Set(all.map((s) => s.module).filter(Boolean))].sort();
  known.modules = modules;
  known.products = [...new Set(all.map((s) => s.product).filter(Boolean))].sort();
  if (!modules.includes(filters.module)) filters.module = "all";
  const body = h("div", {});

  const draw = () => {
    const q = filters.q.toLowerCase();
    const shown = all.filter((s) => (filters.module === "all" || s.module === filters.module) &&
      (!q || [s.title, s.ref, s.product, s.module, s.file, s.description].join(" ").toLowerCase().includes(q)));
    body.replaceChildren(shown.length ? table({
      caption: "Manual scenarios",
      rows: shown,
      onRow: (s) => openScenario(s.id),
      columns: [
        {label: "Scenario", render: (s) => [h("div", {class: "primary-cell"}, s.title), h("div", {class: "sub"}, `${s.ref} · ${s.file}`)]},
        {label: "Module", render: (s) => [h("div", {}, s.module), h("div", {class: "sub"}, s.product)]},
        {label: "Cases", cls: "num", render: (s) => s.case_count},
        {label: "Steps", cls: "num", render: (s) => s.step_count},
        {label: "Result", render: resultCell},
        // What someone typed in the workbook's Pass / Fail column is only a note: Quartermaster did not run it.
        {label: "Notes", render: (s) => h("div", {class: "row", style: "gap:4px"},
          s.blank_data ? badge("Test data missing", "warning") : null,
          s.values_missing ? h("span", {title: "These steps say to type something without saying what, e.g. \"Enter required data\". The AI never makes values up, so Prepare stops there: do it by hand, or write the values in the workbook and import it again."},
            badge(`Values not written: ${s.values_missing} ${s.values_missing === 1 ? "step" : "steps"}`, "warning")) : null,
          !s.step_count ? badge("No steps", "neutral") : null,
          s.status ? h("span", {title: `Typed in the workbook${s.tester ? ` by ${s.tester}` : ""}. Not a Quartermaster run.`},
            badge(`Workbook: ${s.status === "passed" ? "Pass" : "Fail"}${s.releases?.length ? ` (${s.releases.join(", ")})` : ""}`, "neutral")) : null)},
        {srLabel: "Run", cls: "actions", render: (s) => h("div", {class: "row", style: "gap:6px;flex-wrap:nowrap;justify-content:flex-end"},
          actionButtons(s, {size: "sm"}))},
      ],
    }) : emptyState(all.length
      ? {ic: "search", title: "No scenarios match", text: "Try another search or module."}
      : {ic: "file", tone: "primary", title: "No manual scripts yet",
        text: "Import your Excel test scripts (a Test Scenarios and Test Cases workbook, or a list of actions with reference numbers). They are read on this computer and matched to Oracle release features.",
        actions: [button("Import manual scripts", {kind: "primary", size: "sm", ic: "download", onClick: () => openManualImport()}), newScenario("ghost")]}));
  };
  draw();

  const filesBtn = data.files.length ? button(`Imported files: ${data.files.length}`, {ic: "folder", attrs: {"aria-haspopup": "dialog"},
    onClick: (e) => popover(e.currentTarget, (close) => h("div", {class: "pop-body", style: "gap:2px;max-width:440px;max-height:60vh;overflow:auto"},
      h("div", {class: "caption", style: "padding:0 8px 6px"}, "IMPORTED WORKBOOKS"),
      data.files.map((f) => h("div", {class: "row", style: "flex-wrap:nowrap;gap:8px;padding:6px 8px;align-items:flex-start"},
        h("div", {class: "grow", style: "min-width:0"}, h("div", {class: "ellipsis", style: "font-weight:500"}, f.file),
          h("div", {class: "meta"}, `${f.module} · ${f.product} · ${plural(f.scenarios, "scenario")}${f.imported_at ? ` · ${when(f.imported_at)}` : ""}`),
          f.warnings.length ? h("div", {class: "meta", style: "color:var(--warning)"}, plural(f.warnings.length, "note")) : null),
        button("Remove", {size: "sm", kind: "ghost", title: `Forget ${f.file}`, onClick: async () => {
          if (!confirm(`Forget ${f.file}? The Excel file itself is not touched.`)) return;
          try { await api("/api/manual/remove", {key: f.key}); toast(`Removed ${f.file}.`); close(); manualPage(); } catch (err) { toast(err.message); }
        }}))),
      h("div", {class: "hint", style: "padding:6px 8px 0"}, "Import a workbook again to replace it.")))}) : null;

  const batch = data.prepare_all || {items: [], counts: {}};
  const batchRunning = batch.status === "running" || batch.status === "stopping";
  const batchDone = (batch.counts.prepared || 0) + (batch.counts.stopped || 0);
  // what Prepare all would do: not playing by itself yet, not waiting for review, and nothing the
  // AI would stop at for certain (test data missing, values to type that are not written, no steps)
  const toPrepare = all.filter((s) => !s.automated && !s.review && !s.blank_data && !s.values_missing && s.step_count);
  const leftOut = all.filter((s) => !s.automated && !s.review && (s.blank_data || s.values_missing || !s.step_count)).length;
  const totals = {cases: all.reduce((a, s) => a + s.case_count, 0), steps: all.reduce((a, s) => a + s.step_count, 0)};
  const typed = all.filter((x) => x.typed).length;
  const workbooks = plural(data.files.length, "workbook");
  const source = !typed ? ` from ${workbooks}` : data.files.length ? ` from ${workbooks} and ${typed} typed here` : ", all typed here";
  show([{label: "Tests", href: "#/tests"}, {label: "Manual scenarios"}],
    h("div", {class: "page-head"},
      h("div", {}, h("h1", {}, "Tests"),
        h("p", {class: "lead"}, all.length
          ? `${plural(all.length, "manual scenario")}, ${plural(totals.cases, "test case")} and ${plural(totals.steps, "step")}${source}.`
          : "Manual test scripts imported from Excel, or typed here.")),
      h("div", {class: "row"}, filesBtn,
        aiReady() && toPrepare.length && !batchRunning ? button(`Prepare all (${toPrepare.length})`, {ic: "target", disabled: !state.status.ready,
          title: "The AI prepares every scenario that does not play by itself yet, one after another; you review them after",
          onClick: () => startPrepareAll(toPrepare.length, leftOut)}) : null,
        newScenario(),
        button("Import manual scripts", {kind: all.length ? "" : "primary", ic: "download", onClick: () => openManualImport()}))),
    h("div", {class: "toolbar"}, testsViewSwitch("manual", {automated: state.tests.length, manual: all.length,
      review: all.filter((s) => s.review === "needs_review").length})),
    batchRunning ? callout("info", "Prepare all is running.", `${batchDone} of ${batch.total} done. The AI is using the browser, so Run by hand and Prepare wait until it has finished. `,
      h("a", {href: "#/tests?view=review"}, "See progress")) : null,
    all.length ? h("div", {class: "toolbar"},
      h("div", {class: "search"}, icon("search"), input({type: "search", value: filters.q, placeholder: "Search scenario, product or file",
        "aria-label": "Search manual scenarios", oninput: (e) => { filters.q = e.target.value; draw(); }})),
      modules.length > 1 ? chips([["all", "All modules"], ...modules.map((m) => [m, m, all.filter((s) => s.module === m).length])],
        filters.module, (v) => { filters.module = v; draw(); }, "Module") : null) : null,
    h("div", {class: "card"}, body));

  if (state.query.open) openScenario(state.query.open);
}

// ------------------------------------------------------------------ one scenario

export async function openScenario(id) {
  let s;
  try { s = await api("/api/manual/scenario?id=" + encodeURIComponent(id)); } catch (e) { toast(e.message); return; }
  // Step numbers as the scenario is done (a case without steps counts as one), and a test data box
  // for each step whose script does not give the value, or that has test data entered already.
  const numbers = new Map();
  let count = 0;
  for (const c of s.cases) { if (!c.steps.length) count += 1; for (const st of c.steps) numbers.set(st, ++count); }
  const entered = s.test_data || {};
  const boxes = new Map();
  const dataBox = (n) => {
    const need = (s.needs_data || {})[n];
    if (!need && !entered[n]) return null;
    const box = input({value: entered[n] || "", maxlength: "500", "aria-label": `Test data for step ${n}`,
      placeholder: need === "blank" ? "The script says <> here: type the value to use" : "e.g. Start Date: 01/10/2026; Supplier: Acme"});
    boxes.set(n, box);
    return h("label", {class: "stack", style: "gap:4px;margin-top:6px"},
      h("span", {class: "caption", style: need && !entered[n] ? "color:var(--warning)" : ""}, need && !entered[n] ? "TEST DATA NEEDED" : "TEST DATA"), box);
  };
  const saveData = button("Save test data", {kind: "primary", size: "sm", ic: "check", onClick: async (e) => {
    e.currentTarget.disabled = true;
    const values = Object.fromEntries([...boxes].map(([n, box]) => [n, box.value.trim()]));
    try {
      await api("/api/manual/data", {id: s.id, values});
      toast("Test data saved. Prepare and Run by hand use it from now on.");
      document.querySelector(".scrim")?.click();
      if (location.hash.startsWith("#/tests?view=manual")) manualPage();
      openScenario(s.id);
    } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
  }});
  const facts = [
    ["Product", `${s.module} · ${s.product}`], [s.typed ? "Source" : "Workbook", s.file], ["Scenario", [s.use_case, s.ref].filter(Boolean).join(" · ")],
    s.tester ? ["Tester in workbook", s.tester] : null, s.minutes ? ["Time in workbook", `${s.minutes} min`] : null,
    s.releases?.length ? ["Workbook says tested in", s.releases.join(", ")] : null,
  ].filter(Boolean);
  drawer({
    title: s.title,
    sub: `${plural(s.case_count, "test case")} · ${plural(s.step_count, "step")}`,
    foot: () => s.review === "needs_review" ? [
      h("span", {class: "grow"}),
      button("Do it by hand", {ic: "file", disabled: !state.status.ready, onClick: () => runScenario(s.id, {byHand: true})}),
      button("Approve", {kind: "primary", ic: "check", onClick: () => approveScenario(s.id)}),
    ] : [
      s.typed ? button("Change", {ic: "wrench", title: "Change the name, product or steps", onClick: () => openTypedScenario(s, known)}) : null,
      h("span", {class: "meta grow"}, s.automated ? (aiReady() ? "Ready: Run plays it by itself." : "Run plays it by itself. To prepare it again, paste the AI key in Settings.") : aiReady() ? "Prepare: an AI does it and you review. Or do it by hand once." : "Do it by hand once; after that it plays by itself."),
      s.automated ? button("Prepare again", {ic: "target", disabled: !state.status.ready || !aiReady(),
        title: aiReady() ? "The AI does it again, for example when the saved steps are wrong or incomplete"
          : `Set up the AI first: ${state.status.ai?.problem || "Settings, AI assistant"}`, onClick: () => {
          if (!confirm("The AI prepares this scenario again. If it finishes, the new version replaces the current one and waits in To review; Run is refused until you approve it. If it stops, the current version stays.")) return;
          runScenario(s.id, {prepare: true});
        }}) : null,
      s.automated ? button("Do it by hand", {ic: "file", disabled: !state.status.ready, onClick: () => runScenario(s.id, {byHand: true})}) : null,
      ...actionButtons(s),
    ],
    body: () => [
      s.review === "needs_review" ? callout("warning", "Prepared by AI. Check it before it runs.",
        [`${s.prepared.by} did the steps ${when(s.prepared.at).toLowerCase()}. Open what it did and compare each picture with the step it belongs to. If every picture is right, Approve. If not, do it by hand. `,
          h("a", {href: runLink(s.prepared.run_id)}, "See what the AI did")]) : null,
      s.review === "approved" ? callout("info", "Prepared by AI and approved.", `Approved ${when(s.prepared.approved_at).toLowerCase()}.`) : null,
      s.qm_history?.length ? h("div", {}, h("div", {class: "caption", style: "margin-bottom:6px"}, "RESULTS IN QUARTERMASTER"),
        h("div", {class: "stack", style: "gap:6px"}, s.qm_history.slice(0, 5).map((r) => h("a", {href: runLink(r.run_id), class: "row", style: "gap:8px;color:inherit"},
          statusBadge(r.status), h("span", {class: "meta"}, `${HOW[r.how] || "By itself"} · ${when(r.at)}${r.release ? ` · ${r.release}` : ""}`))))) : null,
      s.status ? callout("info", `The workbook says ${s.status === "passed" ? "Pass" : "Fail"}.`,
        `Someone typed this in its Pass / Fail column${s.tester ? ` (tester ${s.tester})` : ""}${s.releases?.length ? `, for ${s.releases.join(" and ")}` : ""}. Quartermaster has not run this scenario.`) : null,
      h("dl", {class: "kv"}, facts.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
      s.description ? h("p", {}, s.description) : null,
      s.blank_data ? callout("warning", "Test data missing.", "Some steps say <> where a value should be, such as a user or supplier. Fill these in before testing.") : null,
      s.fields?.length ? h("div", {}, h("div", {class: "caption", style: "margin-bottom:6px"}, "FIELDS TO CHECK"),
        h("ul", {style: "margin:0;padding-left:18px"}, s.fields.map((f) => h("li", {}, f)))) : null,
      Object.keys(s.needs_data || {}).length ? callout("warning", "Some steps need test data.",
        "The script does not give the value to type (or says <>). Type it below each marked step, then Save test data. The AI and the tester then see it as part of the step; the workbook is not changed. Never type a password here: test data is kept as plain text and sent to the AI. Do steps that need a sign-in by hand.") : null,
      ...s.cases.map((c) => h("section", {class: "card", style: "padding:14px 16px"},
        h("div", {class: "row", style: "gap:8px;margin-bottom:4px"}, h("span", {class: "tag"}, c.id), h("strong", {}, c.name)),
        c.description && c.description !== c.name ? h("p", {class: "meta", style: "margin:0 0 8px"}, c.description) : null,
        c.precondition ? h("p", {class: "meta", style: "margin:0 0 8px;white-space:pre-line"}, h("strong", {}, "Before you start: "), c.precondition) : null,
        c.steps.length ? h("ol", {class: "stack", style: "gap:8px;margin:0;padding-left:20px"}, c.steps.map((st) => {
          const n = String(numbers.get(st));
          return h("li", {},
            h("div", {style: "white-space:pre-line"}, st.action),
            st.expected ? h("div", {class: "meta", style: "white-space:pre-line"}, "Expected: ", st.expected) : null,
            dataBox(n));
        })) :
          h("p", {class: "muted", style: "margin:0"}, "No steps written."))),
      boxes.size ? h("div", {class: "row"}, saveData) : null,
    ],
  });
}

// ------------------------------------------------------------------ import

export function openManualImport() {
  let picked = [];
  let preview = null;
  const fileInput = h("input", {type: "file", class: "input", multiple: true, accept: ".xlsx,.xlsm", "aria-describedby": "mi-hint"});
  const result = h("div", {class: "stack", "aria-live": "polite"});
  const saveBtn = button("Save", {kind: "primary", ic: "check", disabled: true});
  const overrides = {}; // file name -> {module, product} typed by the reader
  let closeDrawer = () => {};
  const done = (count) => {
    toast(`Saved ${plural(count, "workbook")}. They stay in Quartermaster until you remove them.`);
    closeDrawer();
    if (location.hash.startsWith("#/tests?view=manual")) manualPage(); else location.hash = "#/tests?view=manual";
  };

  const read = (file) => new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve({name: file.name, content: String(r.result).split(",", 2)[1] || ""});
    r.onerror = () => reject(new Error(`${file.name} could not be read`));
    r.readAsDataURL(file);
  });
  // Workbooks saved already are not sent again.
  const payload = () => picked.filter((f) => !preview?.files.find((r) => r.file === f.name)?.saved)
    .map((f) => ({...f, ...(overrides[f.name] || {})}));

  // Choosing the files saves them at once. Only a workbook whose module or product could not be
  // told from its name (or that cannot be read) keeps the dialog open.
  async function save() {
    saveBtn.disabled = true;
    const before = preview ? preview.files.filter((f) => f.saved) : [];
    result.replaceChildren(h("div", {class: "skel", style: "height:120px"}));
    try {
      const reply = await api("/api/manual/import", {files: payload(), save: true});
      preview = {files: [...before, ...reply.files]};
      const saved = preview.files.filter((f) => f.saved).length;
      if (preview.files.every((f) => f.saved)) done(saved); else drawPreview();
    } catch (err) {
      result.replaceChildren(callout("danger", "These files cannot be used.", err.message));
    }
  }

  function drawPreview() {
    const ok = preview.files.filter((f) => !f.problem);
    const sum = (k) => ok.reduce((a, f) => a + f[k], 0);
    const saved = preview.files.filter((f) => f.saved);
    result.replaceChildren(
      saved.length ? callout("info", `Saved ${plural(saved.length, "workbook")}: ${plural(saved.reduce((a, f) => a + f.scenarios, 0), "scenario")}.`,
        "They stay in Quartermaster until you remove them.") : null,
      ok.length > saved.length ? callout("warning", "Enter the module and product of the workbooks below, then Save.",
        "They decide which Oracle release features a scenario is matched to.") : null,
      ...preview.files.filter((f) => !f.saved).map((f) => f.problem
        ? callout("danger", `${f.file}:`, f.problem)
        : h("section", {class: "card", style: "padding:12px 14px"},
          h("div", {class: "row", style: "gap:8px;flex-wrap:nowrap"}, icon("file"), h("strong", {class: "ellipsis grow"}, f.file),
            f.replaces ? badge("Replaces the earlier import", "neutral") : null),
          h("div", {class: "meta", style: "margin:4px 0 8px"},
            `${plural(f.scenarios, "scenario")} · ${plural(f.cases, "test case")} · ${plural(f.steps, "step")}`,
            f.blank_data ? ` · ${f.blank_data} with test data missing` : ""),
          h("div", {class: "fields"},
            fieldInput("Module", f, "module", "e.g. HCM"), fieldInput("Product", f, "product", "e.g. Payables")),
          f.warnings.length ? h("ul", {style: "margin:8px 0 0;padding-left:18px"}, f.warnings.map((w) => h("li", {class: "meta", style: "color:var(--warning)"}, w))) : null)),
    );
    drawSaveState();
  }

  function fieldInput(label, f, key, placeholder) {
    const value = overrides[f.file]?.[key] ?? f[key];
    const el = input({value, placeholder, "aria-label": `${label} of ${f.file}`, oninput: (e) => {
      overrides[f.file] = {...(overrides[f.file] || {}), [key]: e.target.value.trim()};
      drawSaveState();
    }});
    return h("label", {}, h("span", {class: "label"}, label), el);
  }

  function drawSaveState() {
    const ok = preview.files.filter((f) => !f.problem && !f.saved);
    const ready = ok.filter((f) => (overrides[f.file]?.product ?? f.product) && (overrides[f.file]?.module ?? f.module));
    saveBtn.disabled = !ready.length;
    saveBtn.querySelector("span").textContent = ready.length ? `Save ${plural(ready.length, "workbook")}` : "Save";
  }

  fileInput.onchange = async () => {
    preview = null;
    saveBtn.disabled = true;
    try { picked = await Promise.all([...fileInput.files].map(read)); } catch (err) { result.replaceChildren(callout("danger", err.message, null)); return; }
    if (picked.length) save(); else result.replaceChildren();
  };

  drawer({
    title: "Import manual scripts",
    sub: "Excel test scripts, so release impact can point to the manual scenario that covers a feature.",
    body: (close) => {
      closeDrawer = close;
      saveBtn.onclick = save;
      return [
        h("div", {}, h("div", {class: "label"}, "Workbooks"), fileInput,
          h("div", {class: "hint", id: "mi-hint"}, "Choose one or more .xlsx files. Two layouts are read: a Test Scenarios sheet with a Test Cases sheet (Scenario ID, Test case ID, Test step description, Expected result), or a list of actions with Reference Number and Primary Navigation.")),
        callout("info", "Choosing the files saves them.", "The workbooks are read on this computer and are not changed. Quartermaster keeps what it read, so the scenarios are here next quarter too. Import a workbook again to update it."),
        result,
      ];
    },
    foot: (close) => [h("span", {class: "grow"}), button("Cancel", {onClick: close}), saveBtn],
  });
}
