// Runs: every run, and one run's execution in detail.
import {
  STATUS, api, button, callout, card, chips, disclose, emptyState, h, icon, input, plural, statusBadge, statusNode, table,
  toast, took, when,
} from "./ui.js";
import {evidenceViewer, executionTimeline, openFolder, openRunDrawer, releaseBadge} from "./components.js";
import {active, loadCommon, runLink, runName, schedule, state, targetModule, testLink} from "./state.js";
import {show} from "./app.js";

const filters = {q: "", status: "all", release: "all"};

export async function runsPage() {
  const [, runs] = await Promise.all([loadCommon(), api("/api/runs")]);
  if (state.page !== "runs" || state.arg) return;
  const releases = [...new Set(runs.map((r) => r.release).filter(Boolean))].sort();
  const body = h("div", {});
  const counts = (key) => runs.filter((r) => key === "all" || r.status === key || (key === "active" && active(r))).length;

  const draw = () => {
    const q = filters.q.toLowerCase();
    const shown = runs.filter((r) =>
      (filters.status === "all" || r.status === filters.status || (filters.status === "active" && active(r))) &&
      (filters.release === "all" || r.release === filters.release) &&
      (!q || [runName(r), r.target, r.executed_by, r.release, r.environment, targetModule(r.target)].join(" ").toLowerCase().includes(q)));
    body.replaceChildren(shown.length ? table({
      caption: "Runs, newest first",
      rows: shown,
      onRow: (r) => { location.hash = runLink(r.id); },
      rowHref: (r) => runLink(r.id),
      columns: [
        {label: "Result", render: (r) => statusBadge(r.status)},
        {label: "Test", render: (r) => [h("div", {class: "primary-cell"}, runName(r)),
          h("div", {class: "sub"}, r.target === "." ? "Every test" : /\.ya?ml$/.test(r.target) ? r.target : `Folder ${r.target}`)]},
        {label: "Module", render: (r) => targetModule(r.target) || "–"},
        {label: "Release", render: (r) => r.release ? h("span", {class: "release"}, r.release) : h("span", {class: "muted"}, "–")},
        {label: "Environment", render: (r) => r.environment ? h("span", {title: r.environment}, r.environment.split(".")[0].toUpperCase()) : h("span", {class: "muted"}, "–")},
        {label: "Started", cls: "nowrap", render: (r) => r.started_at ? when(r.started_at) : h("span", {class: "muted"}, "Queued " + when(r.created_at).toLowerCase())},
        {label: "Duration", cls: "num nowrap", render: (r) => r.started_at ? took(r.started_at, r.finished_at) : "–"},
        {label: "Executed by", render: (r) => r.executed_by || h("span", {class: "muted"}, "–")},
        {label: "Tests passed", render: (r) => r.counts ? [h("span", {class: "num"}, `${r.counts.passed} of ${r.counts.total}`),
          h("div", {class: "mini-meter", "aria-hidden": "true"},
            r.counts.passed ? h("span", {class: "fill-success", style: `width:${(100 * r.counts.passed) / r.counts.total}%`}) : null,
            r.counts.failed ? h("span", {class: "fill-danger", style: `width:${(100 * r.counts.failed) / r.counts.total}%`}) : null)] : h("span", {class: "muted"}, "–")},
        {srLabel: "Evidence", cls: "actions", render: (r) => r.summary_url ? button("", {ic: "download", size: "sm", href: r.summary_url, title: "Download the run summary"}) : null},
      ],
    }) : emptyState(runs.length
      ? {ic: "search", title: "No runs match", text: "Try another search or filter.", actions: button("Clear filters", {size: "sm", onClick: () => { Object.assign(filters, {q: "", status: "all", release: "all"}); runsPage(); }})}
      : {ic: "runs", tone: "primary", title: "No runs yet", text: "Start one and follow it step by step here.", actions: button("New run", {kind: "primary", size: "sm", ic: "plus", onClick: () => openRunDrawer()})}));
  };

  const search = h("div", {class: "search"}, icon("search"), input({type: "search", value: filters.q, placeholder: "Search test, module, release or person",
    "aria-label": "Search runs", oninput: (e) => { filters.q = e.target.value; draw(); }}));
  const releaseSel = releases.length ? h("select", {class: "input", style: "width:auto", "aria-label": "Release",
    onchange: (e) => { filters.release = e.target.value; draw(); }},
  h("option", {value: "all"}, "All releases"), releases.map((r) => h("option", {value: r, selected: filters.release === r}, `Release ${r}`))) : null;
  draw();
  show([{label: "Runs"}],
    h("div", {class: "page-head"}, h("div", {}, h("h1", {}, "Runs"), h("p", {class: "lead"}, "Every run, newest first. Open one to see each step, its screenshots and the evidence.")),
      button("New run", {kind: "primary", ic: "plus", onClick: () => openRunDrawer()})),
    h("div", {class: "toolbar"}, search,
      chips([["all", "All", counts("all")], ["active", "In progress", counts("active")], ["passed", "Passed", counts("passed")],
        ["failed", "Failed", counts("failed")], ["error", "Could not run", counts("error")], ["cancelled", "Cancelled", counts("cancelled")]]
        .filter(([k, , n]) => k === "all" || n), filters.status, (v) => { filters.status = v; draw(); }, "Result"),
      releaseSel),
    h("div", {class: "card"}, body));
  if (runs.some(active)) schedule(runsPage, 2500);
}

// ------------------------------------------------------------------ one run

// Each test's steps as the progress events describe them (before the run's evidence is written).
function progress(events) {
  const tests = new Map();
  const get = (id) => {
    if (!tests.has(id)) {
      const known = state.tests.find((t) => t.id === id);
      tests.set(id, {id, title: known?.title || id, steps: [], status: "waiting", total: known?.steps || 0});
    }
    return tests.get(id);
  };
  for (const e of events) {
    if (e.type === "suite_start") (e.tests || []).forEach(get);
    else if (e.type === "run_start") Object.assign(get(e.test_id), {title: e.title, total: e.steps, status: "running", started: e.at});
    else if (e.type === "step_start") get(e.test_id).steps[e.index] = {number: e.index + 1, intent: e.intent, status: "running", started_at: e.at};
    else if (e.type === "step_end") Object.assign(get(e.test_id).steps[e.index] ||= {number: e.index + 1}, {intent: e.intent, status: e.status, error: e.plain_error, detail: e.error});
    else if (e.type === "run_end") get(e.test_id).status = e.status;
  }
  return [...tests.values()];
}

// What the run is doing right now, in words.
function phase(run, events, live) {
  if (run.status === "queued") return "Waiting for the run before it to finish";
  const last = events[events.length - 1];
  if (!last) return "Starting: opening the browser";
  const running = live.filter((x) => x.status === "running").length;
  if (running > 1) return `${running} tests running at the same time (${live.filter((x) => !["waiting", "running"].includes(x.status)).length} of ${live.length} finished)`;
  const t = live.find((x) => x.id === last.test_id);
  const n = live.findIndex((x) => x.id === last.test_id) + 1;
  const of = live.length > 1 ? ` (test ${n} of ${live.length})` : "";
  switch (last.type) {
    case "suite_start": return "Opening Oracle Fusion";
    case "run_start": return `Signing in to Oracle Fusion${of}`;
    case "step_start": return `Executing step ${last.index + 1} of ${t?.total || "?"}: ${last.intent}${of}`;
    case "step_end": return `Step ${last.index + 1} of ${t?.total || "?"} ${last.status === "failed" ? "failed" : "done"}${of}`;
    case "step_retry": return `Step ${last.index + 1} did not work: trying again (attempt ${last.attempt} of ${last.of})${of}`;
    case "setup_start": case "setup_start_step": case "setup_end": return `Checking that the pod is ready for the test${of}`;
    case "cleanup_start": case "cleanup_start_step": case "cleanup_end": return `Cleaning up the test data on the pod${of}`;
    case "run_end": return `Capturing evidence and writing the Word document${of}`;
    case "test_saved": return n < live.length ? "Starting the next test" : "Writing the run summary";
    default: return "Finishing";
  }
}

export async function runPage(id) {
  if (!state.status) await loadCommon();
  const run = await api(`/api/runs/${encodeURIComponent(id)}`);
  if (location.hash !== runLink(id)) return; // left while loading
  const isActive = active(run);
  const live = progress(run.events);
  // A run that ended part way (stopped, or could not finish) leaves its last test "running" and the
  // rest "waiting" in the live progress: they did not run, so say so.
  if (!isActive) {
    for (const t of live) {
      if (t.status === "running") t.status = run.status === "cancelled" ? "cancelled" : "error";
      else if (t.status === "waiting") t.status = "skipped";
    }
  }
  const liveById = new Map(live.map((t) => [t.id, t]));
  const results = run.results;
  const totalSteps = live.reduce((a, t) => a + Math.max(t.total, t.steps.length), 0);
  const doneSteps = live.reduce((a, t) => a + t.steps.filter((x) => x && x.status !== "running").length, 0);
  const pct = run.status === "queued" ? 0 : isActive ? (totalSteps ? Math.round((100 * doneSteps) / totalSteps) : 2) : 100;
  const passedTests = results.filter((r) => r.status !== "failed").length;
  const module = targetModule(run.target);

  const head = card({body: h("div", {class: "stack", style: "gap:16px"},
    h("div", {class: "row", style: "justify-content:space-between;align-items:flex-start"},
      h("div", {style: "min-width:0"},
        h("div", {class: "row", style: "margin-bottom:6px"}, statusBadge(run.status), module ? h("span", {class: "tag"}, module) : null,
          run.release ? releaseBadge(run.release) : null),
        h("h1", {}, runName(run)),
        h("p", {class: "lead"}, results.length ? `${passedTests} of ${plural(results.length, "test")} passed` :
          isActive ? `${live.filter((t) => !["waiting", "running"].includes(t.status)).length} of ${plural(live.length || 1, "test")} done` : "")),
      h("div", {class: "row"},
        isActive ? button(run.status === "queued" ? "Remove from queue" : "Stop run", {kind: "danger", ic: "stop", onClick: async (e) => {
          e.currentTarget.disabled = true;
          await api(`/api/runs/${encodeURIComponent(id)}/cancel`, {}).catch((err) => toast(err.message));
          runPage(id);
        }}) : null,
        !isActive ? button("Run again", {ic: "refresh", onClick: () => openRunDrawer(run.target === "." ? "." : run.target)}) : null,
        run.suite_folder ? button("Open folder", {ic: "folder", onClick: () => openFolder(run.suite_folder)}) : null,
        run.summary_url ? button("Summary document", {kind: "primary", ic: "download", href: run.summary_url}) : null)),
    isActive ? h("div", {class: "stack", style: "gap:8px"},
      h("div", {class: "phase", role: "status", "aria-live": "polite"}, icon("loader", "spin"), phase(run, run.events, live)),
      h("div", {class: "progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct), "aria-label": "Run progress"},
        h("span", {style: `width:${pct}%`}))) : null,
    h("div", {class: "facts"},
      fact("Oracle release", run.release ? h("span", {class: "release"}, run.release) : "Not recorded"),
      fact("Environment", envLabel(run.environment || (isActive ? state.status.pod_host : ""))),
      fact("Started", run.started_at ? when(run.started_at) : "Not yet"),
      fact("Ended", run.finished_at ? when(run.finished_at) : isActive ? "Running" : "–"),
      fact("Duration", run.started_at ? took(run.started_at, run.finished_at) : "–"),
      fact("Executed by", run.executed_by || "Not recorded"),
      (run.options.parallel || 1) > 1 ? fact("Tests at once", `${run.options.parallel} at the same time`) : null,
      fact("Evidence", `${{"every-step": "Screenshot every step", "on-failure": "Screenshots on failure", off: "No screenshots"}[run.options.screenshots]}${run.options.video !== "off" ? ", video" : ""}`)))});

  // tests of the run: finished results, or live progress
  const items = results.length
    ? results.map((r) => ({key: r.test_id, title: r.test_title || r.test_id, status: r.status, sub: `${r.steps_passed} of ${r.steps_total} steps · ${r.duration}`, result: r}))
    : live.map((t) => ({key: t.id, title: t.title, status: t.status, sub: t.total ? `${t.steps.filter((x) => x && x.status !== "running").length} of ${plural(t.total, "step")}` : "", live: t}));
  const selKey = `run:${id}`;
  if (!items.some((i) => i.key === state.open[selKey])) {
    state.open[selKey] = (items.find((i) => i.status === "failed") || items.find((i) => i.status === "running") || items[0])?.key;
  }
  const selected = items.find((i) => i.key === state.open[selKey]);

  let detail;
  if (!selected) {
    detail = card({body: emptyState(run.status === "queued"
      ? {ic: "clock", title: "Queued", text: "It starts when the run before it finishes."}
      : isActive ? {ic: "loader", tone: "info", title: "Starting", text: "Opening the browser and signing in to Oracle Fusion."}
        : {ic: "attention", tone: "danger", title: "No test results", text: "The run stopped before any test ran. The messages below say why."})});
  } else if (selected.result) {
    const r = selected.result;
    detail = h("section", {class: "card", "aria-label": r.test_title || r.test_id},
      h("div", {class: "card-head"}, h("div", {class: "grow"},
        h("div", {class: "row"}, h("h2", {}, r.test_title || r.test_id), statusBadge(r.status)),
        h("div", {class: "sub"}, `${r.steps_passed} of ${plural(r.steps_total, "step")} passed · ${r.duration}`)),
      h("div", {class: "row"}, r.document_url ? button("Evidence document", {kind: "primary", size: "sm", ic: "file", href: r.document_url}) : null,
        testFile(r.test_id) ? button("Open test", {size: "sm", ic: "tests", href: testLink(testFile(r.test_id))}) : null)),
      h("div", {style: "padding:12px 24px 0", class: "stack"},
        r.failed_step ? callout("danger", `Step ${r.failed_step.number} failed: ${r.failed_step.intent}.`, r.failed_step.error) : null,
        r.flaky ? callout("warning", "Passed, but only after a step was tried again.",
          "A test like this may fail for no real reason (a slow page, for example). Open the steps to see which one.") : null,
        setupNote(r.setup),
        cleanupNote(r.cleanup),
        r.needs_update ? callout("warning", "Needs update.", ["Something on the screen was found in a different way than written. ", h("a", {href: "#/attention"}, "Review it in Needs attention"), "."]) : null),
      evidenceViewer(r, {run}));
  } else {
    const t = selected.live;
    detail = card({title: t.title, sub: t.total ? `${t.steps.filter((x) => x && x.status !== "running").length} of ${plural(t.total, "step")}` : "",
      actions: statusBadge(t.status),
      body: t.status === "waiting" ? emptyState({ic: "clock", title: "Not started", text: "It runs after the tests before it."})
        : t.status === "skipped" ? emptyState({ic: "minus", title: "Did not run", text: "The run ended before this test. Run it again to test it."})
        : executionTimeline(t.steps, {total: t.total})});
  }

  const list = items.length > 1 ? h("nav", {class: "card", "aria-label": "Tests in this run"},
    h("div", {class: "card-head"}, h("h3", {}, `${plural(items.length, "test")}`)),
    h("div", {class: "test-list"}, items.map((i) => h("button", {type: "button", "aria-current": String(i.key === state.open[selKey]),
      onclick: () => { state.open[selKey] = i.key; runPage(id); }},
      h("span", {class: "st"}, statusNode(i.status)),
      h("span", {style: "min-width:0"}, h("span", {class: "ellipsis", style: "display:block;font-weight:500"}, i.title),
        h("span", {class: "meta"}, STATUS[i.status]?.label || i.status, i.sub ? ` · ${i.sub}` : "")))))) : null;

  const outputOpen = document.querySelector("details.run-output")?.open || false;
  show([{label: "Runs", href: "#/runs"}, {label: runName(run)}],
    head,
    run.status === "error" ? h("div", {class: "section"}, callout("danger", "The run could not finish.", run.error_plain || run.error || "",
      run.error_plain && run.error ? disclose("Technical details", h("pre", {class: "block"}, run.error)) : null)) : null,
    h("div", {class: `section ${list ? "split" : ""}`}, list, detail),
    run.output ? h("div", {class: "card section"}, h("div", {class: "card-body"},
      h("details", {class: "disclose run-output", open: outputOpen}, h("summary", {}, icon("right"), "Messages from the run (for the test team)"),
        h("pre", {class: "block"}, run.output)))) : null);

  if (isActive) schedule(() => runPage(id), 1500);
}

// What the test checked or made on the pod before its steps. A failure here is the pod's data, not the release.
function setupNote(c) {
  if (!c) return null;
  const bad = c.steps.find((x) => x.status === "failed");
  if (bad) {
    return callout("warning", "Setup not met: the pod was not ready, so the test steps did not run.",
      "This is about the data on the pod, not about the release.",
      h("ul", {}, h("li", {}, `Setup step ${bad.number} (${bad.intent}): ${bad.error}`)));
  }
  return callout("info", "The pod was ready.", `${plural(c.steps.length, "setup step")} passed before the test began: ${c.steps.map((x) => x.intent).join("; ")}.`);
}

// What the test's cleanup did. It never changes the test's result, but leftovers on the pod matter.
function cleanupNote(c) {
  if (!c) return null;
  const bad = c.steps.filter((x) => x.status === "failed");
  const none = c.steps.every((x) => x.status === "skipped");
  if (bad.length) {
    return callout("warning", "Cleanup did not finish: records from this test may still be on the pod.",
      "The test's result is not affected.",
      h("ul", {}, bad.map((x) => h("li", {}, `Cleanup step ${x.number} (${x.intent}): ${x.error}`))));
  }
  return callout("info", none ? "Nothing to clean up." : "Test data cleaned up.",
    none ? c.steps[0]?.note || "" : `${plural(c.steps.filter((x) => x.status !== "skipped").length, "cleanup step")} done after the test.`);
}

function envLabel(host) {
  return host ? h("span", {title: host}, host.split(".")[0].toUpperCase()) : "Not recorded";
}

function fact(k, v) {
  return h("div", {}, h("div", {class: "k"}, k), h("div", {class: "v"}, v));
}

function testFile(testId) {
  return state.tests.find((t) => t.id === testId)?.file;
}

