// Quartermaster components, built from the primitives in ui.js.
import {
  STATUS, api, badge, button, callout, card, clock, disclose, drawer, emptyState, h, hideTip, icon, modal, plural, popover,
  remember, s, segmented, shortDate, showTip, statusNode, tabs, toast, token, when,
} from "./ui.js";
import {approvalBlock} from "./page-approval.js";
import {connection, envName, runLink, runName, state, testLink, testName} from "./state.js";

// ------------------------------------------------------------------ badges

export function releaseBadge(release, {prefix = "Release"} = {}) {
  if (!release) return h("span", {class: "tag muted", title: "No Oracle release was recorded"}, "No release");
  return h("span", {class: "tag", title: `Oracle release ${release}`}, prefix ? h("span", {}, prefix) : null, h("span", {class: "release"}, release));
}

export function environmentBadge(status = state.status) {
  const c = connection(status);
  return h("span", {class: "tag", title: `${c.label}: ${status?.pod_url || "no pod set"}`},
    h("span", {class: `dot ${c.dot}`, style: "margin-right:6px"}), envName(status) || "No pod");
}

// ------------------------------------------------------------------ environment control (sidebar)

export function environmentButton() {
  const st = state.status;
  const c = connection(st);
  const btn = h("button", {type: "button", class: "env-btn", "aria-haspopup": "dialog",
    "aria-label": `Environment ${envName(st) || "not set up"}, ${c.label}`, title: "Environment",
    onclick: () => openEnvironment(btn)},
    h("span", {class: `dot ${c.dot}`}),
    h("span", {class: "env-text grow", style: "min-width:0"},
      h("span", {class: "meta ellipsis", style: "display:block"}, st?.client ? `${st.client} · ${c.label}` : c.label),
      h("span", {class: "env-name ellipsis", style: "display:block"}, envName(st) || "No pod set"),
      h("span", {class: "meta", style: "display:block"}, st?.release ? ["Release ", h("span", {class: "release"}, st.release)] : "Release not set")),
    h("span", {class: "env-text"}, icon("down")));
  return btn;
}

function openEnvironment(anchor) {
  popover(anchor, (close) => {
    const st = state.status;
    const c = connection(st);
    const status = h("dd", {}, h("span", {class: "row", style: "gap:6px"}, h("span", {class: `dot ${c.dot}`}), c.label));
    const checked = h("dd", {}, st.pod_check ? `${when(st.pod_check.checked_at)} · ${st.pod_check.message}` : "Never");
    const check = button("Check now", {ic: "refresh", size: "sm", onClick: async (e) => {
      const b = e.currentTarget;
      b.disabled = true;
      try { await checkPod(); close(); openEnvironment(anchor); } catch (err) { toast(err.message); b.disabled = false; }
    }});
    // The other environments, to switch to without leaving the page.
    const others = h("div", {class: "stack", style: "gap:2px"});
    api("/api/environments").then((data) => {
      const choices = data.clients.flatMap((c) => c.environments.filter((e) => !e.active).map((e) => ({...e, client: c.name})));
      if (!choices.length) return;
      others.append(h("div", {class: "caption", style: "padding:8px 0 4px"}, "SWITCH TO"),
        ...choices.map((e) => h("button", {type: "button", class: "btn ghost sm", style: "justify-content:flex-start;width:100%", onclick: async () => {
          try {
            await api("/api/environments/activate", {id: e.id});
            close();
            toast(`Now using ${e.client} · ${e.name}.`);
            location.reload();
          } catch (err) { toast(err.message); }
        }}, icon("server"), h("span", {class: "ellipsis"}, `${e.client} · ${e.name}`), h("span", {class: "meta"}, ` ${e.kind}`))));
    }).catch(() => {});
    return [
      h("div", {class: "pop-body"},
        h("div", {class: "row"}, h("span", {class: "icon-tile tone-primary"}, icon("server")),
          h("div", {class: "grow"}, h("h3", {}, envName(st) || "No pod set"), h("div", {class: "meta"}, st.client || "Oracle Fusion environment"))),
        h("dl", {class: "kv"},
          h("dt", {}, "Pod"), h("dd", {}, st.pod_url || "Not set up yet"),
          h("dt", {}, "Release"), h("dd", {}, st.release ? h("span", {class: "release"}, st.release) : "Not set"),
          h("dt", {}, "Signs in as"), h("dd", {}, st.user || "Not set"),
          h("dt", {}, "Connection"), status,
          h("dt", {}, "Last check"), checked),
        others),
      h("div", {class: "pop-foot"}, check, h("span", {class: "grow"}),
        button("Manage environments", {size: "sm", href: "#/settings", attrs: {onclick: close}})),
    ];
  });
}

export async function checkPod({quiet = false} = {}) {
  const result = await api("/api/check-pod", {});
  state.status.pod_check = result;
  document.dispatchEvent(new CustomEvent("qm:common"));
  document.dispatchEvent(new CustomEvent("qm:refresh")); // pages showing the connection redraw
  if (!quiet) toast(result.ok ? `Connected. ${result.message}` : result.message);
  return result;
}

// ------------------------------------------------------------------ new run

// The one place a run is started: all tests, a folder or one test, with its evidence options.
export async function openRunDrawer(preset) {
  const st = state.status;
  if (!st) return;
  let suites = [];
  try { suites = (await api("/api/suites")).suites.filter((x) => x.tests.length); } catch { /* the run drawer works without suites */ }
  const opt = {...st.default_options, ...(remember("options") || {}), evidence_doc: true};
  const runnable = state.tests.filter((t) => !t.problem);
  const folders = [...new Set(runnable.map((t) => t.folder).filter(Boolean))].sort();
  let scope = preset ? (preset.startsWith("suite:") ? "suite" : /\.ya?ml$/.test(preset) ? "one" : preset === "." ? "all" : "folder") : "all";
  const suiteSel = h("select", {class: "input", "aria-label": "Suite"}, suites.map((x) =>
    h("option", {value: x.name}, `${x.title} · ${plural(x.tests.length, "test")}`)));
  if (scope === "suite") suiteSel.value = preset.slice(6);
  const folderSel = h("select", {class: "input", "aria-label": "Folder"}, folders.map((f) =>
    h("option", {value: f}, `${f} · ${plural(runnable.filter((t) => t.folder === f).length, "test")}`)));
  const testSel = h("select", {class: "input", "aria-label": "Test"}, runnable.map((t) => h("option", {value: t.file}, t.title || t.file)));
  if (scope === "folder") folderSel.value = preset;
  if (scope === "one") testSel.value = preset;
  const pick = h("div", {});
  const count = h("span", {class: "meta"});
  const redraw = () => {
    pick.replaceChildren(scope === "folder" ? folderSel : scope === "one" ? testSel : scope === "suite" ? suiteSel :
      h("div", {class: "hint"}, `Every test in ${st.tests_folder}`));
    const n = scope === "all" ? runnable.length : scope === "folder" ? runnable.filter((t) => t.folder === folderSel.value).length
      : scope === "suite" ? (suites.find((x) => x.name === suiteSel.value)?.tests.length || 0) : 1;
    count.textContent = `${plural(n, "test")} will run`;
  };
  folderSel.onchange = redraw;
  suiteSel.onchange = redraw;
  redraw();
  const release = h("input", {class: "input", value: opt.release || st.release || "", placeholder: "e.g. 26C", "aria-describedby": "rel-hint"});
  const tester = h("input", {class: "input", value: opt.tester || "", placeholder: "Shown as Run by"});
  const headed = h("input", {type: "checkbox", checked: opt.headed});
  const highlight = h("input", {type: "checkbox", checked: opt.highlight !== false});

  drawer({
    title: "New run",
    sub: "Runs wait in line and go one at a time.",
    body: (close) => [
      st.ready ? null : callout("danger", "The pod or its sign-in is not set up.", h("a", {href: "#/settings", onclick: close}, "Open Settings")),
      h("div", {}, h("div", {class: "label"}, "What to test"),
        segmented([["all", "All tests"], ["folder", "A folder"], ["one", "One test"], ...(suites.length ? [["suite", "A suite"]] : [])], scope, (v) => { scope = v; redraw(); }, "What to test"),
        h("div", {style: "margin-top:8px"}, pick)),
      h("div", {}, h("div", {class: "label"}, "Screenshots"),
        segmented([["every-step", "Every step"], ["on-failure", "Only failures"], ["off", "None"]], opt.screenshots, (v) => { opt.screenshots = v; }, "Screenshots"),
        h("div", {class: "hint"}, "Screenshots go into the Word evidence document of each test.")),
      h("div", {}, h("div", {class: "label"}, "Video"),
        segmented([["off", "None"], ["on-failure", "Keep on failure"], ["always", "Always"]], opt.video, (v) => { opt.video = v; }, "Video"),
        h("div", {class: "hint"}, "Videos are kept next to the document, not inside it.")),
      h("div", {class: "fields"},
        h("div", {}, h("label", {class: "label", for: "nr-rel"}, "Oracle release"), Object.assign(release, {id: "nr-rel"}),
          h("div", {class: "hint", id: "rel-hint"}, st.release ? `This environment is on ${st.release}.` : "Set it once in Settings.")),
        h("div", {}, h("label", {class: "label", for: "nr-by"}, "Executed by"), Object.assign(tester, {id: "nr-by"}))),
      h("label", {class: "switch"}, headed, "Show the browser while it runs"),
      h("label", {class: "switch"}, highlight, "Highlight clicks (red marks on what each step uses)"),
    ],
    foot: (close) => [count, h("span", {class: "grow"}), button("Cancel", {onClick: close}),
      button("Start run", {kind: "primary", ic: "play", disabled: !st.ready || !runnable.length, onClick: async (e) => {
        const target = scope === "all" ? "." : scope === "folder" ? folderSel.value : testSel.value;
        const options = {screenshots: opt.screenshots, video: opt.video, headed: headed.checked, highlight: highlight.checked,
          release: release.value.trim(), tester: tester.value.trim(), evidence_doc: true};
        remember("options", {...options, release: ""});
        e.currentTarget.disabled = true;
        try {
          const run = await api("/api/runs", scope === "suite" ? {suite: suiteSel.value, options} : {target, options});
          close();
          location.hash = runLink(run.id);
        } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
      }})],
  });
}

// ------------------------------------------------------------------ command palette

export function openPalette(setTheme) {
  if (document.querySelector(".palette")) return;
  const modules = [...new Set(state.tests.map((t) => t.module).filter(Boolean))].sort();
  const items = [
    {group: "Commands", label: "New run", ic: "plus", hint: "N", go: () => openRunDrawer()},
    {group: "Commands", label: "Run all tests", ic: "runs", go: () => openRunDrawer(".")},
    {group: "Commands", label: "Record a test", ic: "record", go: () => { location.hash = "#/record"; }},
    {group: "Commands", label: "Import a release feature list", ic: "download", go: () => import("./page-impact.js").then((m) => m.openImport())},
    {group: "Commands", label: "Import manual test scripts", ic: "download", go: () => import("./page-manual.js").then((m) => m.openManualImport())},
    {group: "Pages", label: "Go to Manual scenarios", ic: "file", go: () => { location.hash = "#/tests?view=manual"; }},
    {group: "Commands", label: "Open Needs attention", ic: "attention", go: () => { location.hash = "#/attention"; }},
    {group: "Commands", label: "Check the pod connection", ic: "refresh", go: () => checkPod().catch((e) => toast(e.message))},
    {group: "Commands", label: "Open the evidence folder", ic: "folder", go: () => api("/api/open", {path: "."}).catch((e) => toast(e.message))},
    {group: "Commands", label: "Switch light or dark", ic: "moon", go: setTheme},
    ...[["", "Overview", "overview"], ["runs", "Runs", "runs"], ["tests", "Tests", "tests"], ["impact", "Release impact", "target"],
      ["attention", "Needs attention", "attention"], ["schedules", "Schedules", "clock"],
      ["record", "Record a test", "record"], ["settings", "Settings", "settings"], ["audit", "Audit log", "file"]]
      .map(([id, label, ic]) => ({group: "Pages", label: `Go to ${label}`, ic, go: () => { location.hash = "#/" + id; }})),
    ...modules.map((m) => ({group: "Modules", label: `${m} tests`, ic: "layers", hint: plural(state.tests.filter((t) => t.module === m).length, "test"),
      go: () => { location.hash = "#/tests?module=" + encodeURIComponent(m); }})),
    ...state.tests.map((t) => ({group: "Tests", label: t.title || t.file, ic: "file", hint: t.module || t.folder,
      go: () => { location.hash = testLink(t.file); }})),
  ];
  let sel = 0, shown = items;
  const input = h("input", {type: "text", role: "combobox", "aria-expanded": "true", "aria-controls": "palette-list",
    "aria-label": "Search commands, tests, runs and evidence", placeholder: "Search commands, tests, runs, modules and evidence…", autofocus: true});
  const list = h("ul", {id: "palette-list", role: "listbox", "aria-label": "Results"});
  const draw = () => {
    const q = input.value.trim().toLowerCase();
    shown = q ? items.filter((i) => `${i.label} ${i.hint || ""} ${i.group}`.toLowerCase().includes(q)) : items;
    sel = Math.min(sel, Math.max(shown.length - 1, 0));
    const rows = [];
    let group = "";
    shown.slice(0, 60).forEach((it, n) => {
      if (it.group !== group) { group = it.group; rows.push(h("li", {class: "group", role: "presentation"}, group)); }
      rows.push(h("li", {role: "option", id: `pal-${n}`, "aria-selected": String(n === sel),
        onmousemove: () => { if (sel !== n) { sel = n; draw(); } }, onclick: () => { close(); it.go(); }},
        icon(it.ic), h("span", {class: "ellipsis"}, it.label), it.hint ? h("span", {class: "hint2"}, it.hint) : null));
    });
    list.replaceChildren(...(rows.length ? rows : [h("li", {class: "group", role: "presentation"}, "Nothing matches")]));
    input.setAttribute("aria-activedescendant", `pal-${sel}`);
    list.querySelector('[aria-selected="true"]')?.scrollIntoView({block: "nearest"});
  };
  input.oninput = () => { sel = 0; draw(); };
  input.onkeydown = (e) => {
    if (e.key === "ArrowDown") { sel = Math.min(sel + 1, shown.length - 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); draw(); e.preventDefault(); }
    else if (e.key === "Enter" && shown[sel]) { e.preventDefault(); close(); shown[sel].go(); }
  };
  const close = modal(() => h("div", {class: "dialog palette", role: "dialog", "aria-label": "Command palette"},
    h("div", {style: "position:relative"}, h("span", {class: "search-ico"}, icon("search")), input), list,
    h("div", {class: "palette-foot"}, h("span", {}, h("kbd", {}, "↑"), " ", h("kbd", {}, "↓"), " move"),
      h("span", {}, h("kbd", {}, "Enter"), " open"), h("span", {}, h("kbd", {}, "Esc"), " close"))));
  draw();
  // recent runs and their evidence arrive a moment later; the palette works straight away
  api("/api/runs").then((runs) => {
    const failed = runs.find((r) => r.status === "failed" || r.status === "error");
    if (failed) items.splice(2, 0, {group: "Commands", label: "Open the latest failed run", ic: "x", hint: when(failed.created_at),
      go: () => { location.hash = runLink(failed.id); }});
    items.push(...runs.slice(0, 8).map((r) => ({group: "Runs", label: runName(r), ic: "runs",
      hint: `${STATUS[r.status]?.label || r.status} · ${when(r.created_at)}`, go: () => { location.hash = runLink(r.id); }})));
    items.push(...runs.filter((r) => r.summary_url).slice(0, 6).map((r) => ({group: "Evidence", label: `Summary: ${runName(r)}`,
      ic: "download", hint: when(r.created_at), go: () => { location.href = r.summary_url; }})));
    if (list.isConnected) draw();
  }).catch(() => {});
}

// ------------------------------------------------------------------ evidence

// Full-size screenshots with previous/next, a thumbnail strip, and the step each belongs to.
export function openViewer(pictures, start = 0) {
  let n = start;
  const img = h("img", {alt: ""});
  const title = h("div", {class: "grow", style: "min-width:0"});
  const counter = h("span", {class: "meta", style: "color:#b1b7c3"});
  const original = button("", {ic: "external", title: "Open the original", size: "sm"});
  const strip = h("div", {class: "viewer-strip", role: "tablist", "aria-label": "Screenshots"});
  const draw = () => {
    const p = pictures[n];
    img.src = p.src; img.alt = p.caption;
    title.replaceChildren(h("div", {style: "font-weight:600"}, p.caption), p.sub ? h("div", {class: "meta", style: "color:#b1b7c3"}, p.sub) : null);
    counter.textContent = `${n + 1} of ${pictures.length}`;
    original.onclick = () => window.open(p.src, "_blank");
    strip.querySelectorAll("button").forEach((b, i) => b.setAttribute("aria-current", String(i === n)));
    strip.children[n]?.scrollIntoView({inline: "nearest", block: "nearest"});
  };
  strip.append(...pictures.map((p, i) => h("button", {type: "button", title: p.caption, onclick: () => { n = i; draw(); }},
    h("img", {src: p.src, alt: "", loading: "lazy"}))));
  const go = (d) => { n = (n + d + pictures.length) % pictures.length; draw(); };
  modal((close) => {
    const box = h("div", {class: "viewer", role: "dialog", "aria-label": "Screenshots",
      onkeydown: (e) => { if (e.key === "ArrowRight") go(1); else if (e.key === "ArrowLeft") go(-1); }, tabindex: "-1"},
      h("div", {class: "viewer-bar"}, title, counter, original, button("", {ic: "x", title: "Close", size: "sm", onClick: close})),
      h("div", {class: "viewer-stage"}, img,
        pictures.length > 1 ? h("button", {class: "viewer-nav prev", "aria-label": "Previous", onclick: () => go(-1)}, icon("left")) : null,
        pictures.length > 1 ? h("button", {class: "viewer-nav next", "aria-label": "Next", onclick: () => go(1)}, icon("right")) : null),
      pictures.length > 1 ? strip : null);
    return box;
  }, {scrim: false});
  draw();
}

function expectedText(st) {
  if (st.expected) return st.expected;
  if (st.action === "assert_text" && st.value) return `It shows "${st.value}".`;
  if (st.action === "assert_visible") return "It is shown on the screen.";
  return "";
}

const ACTIONS = {navigate: "Opened", click: "Clicked", fill: "Typed", select: "Chose", assert_text: "Checked", assert_visible: "Checked",
  login_as: "Signed in as", wait_job: "Waited for", api_call: "Called"};

// One step of an execution: what was done, what should happen, what happened, its screenshot,
// and (folded away) the technical detail. Technical noise stays hidden unless asked for.
export function stepResult(st, {pictures = [], context = ""} = {}) {
  const done = ["passed", "healed", "failed"].includes(st.status);
  const expected = expectedText(st);
  const happened = st.status === "failed" ? st.error || "The step did not complete."
    : st.status === "healed" ? "Done, but the item was found in a different way than written."
      : st.status === "skipped" ? "Not done, because an earlier step failed."
        : st.status === "running" ? "In progress…" : done ? "As expected." : "";
  const shots = st.pictures || [];
  const after = shots.filter((src) => !isBefore(src));
  const thumb = after[after.length - 1] || shots[0];  // the screen after the step; a click also has one before it
  const openShot = (src) => openViewer(pictures, Math.max(0, pictures.findIndex((p) => p.src === src)));
  const technical = [
    st.locator ? h("div", {}, h("span", {class: "caption"}, "FOUND WITH "), h("code", {}, st.locator)) : null,
    st.started_at ? h("div", {class: "meta"}, `Started ${new Date(st.started_at).toLocaleTimeString()}`) : null,
    st.detail && st.detail !== st.error ? h("pre", {class: "block"}, st.detail) : null,
  ].filter(Boolean);
  const summary = h("summary", {},
    h("div", {style: "min-width:0"},
      h("div", {class: st.status === "skipped" || st.status === "waiting" ? "step-muted" : ""}, st.intent || "Step"),
      st.status === "failed" && st.error ? h("div", {class: "meta", style: "color:var(--danger)"}, st.error) : null,
      st.status === "healed" ? h("div", {class: "meta", style: "color:var(--warning)"}, "Needs update: found in a different way than written") : null,
      st.attempts > 1 && st.status !== "failed" ? h("div", {class: "meta", style: "color:var(--warning)"},
        `Passed on attempt ${st.attempts}: the first try did not work${st.first_error ? ` (${st.first_error})` : ""}`) : null,
      st.attempts > 1 && st.status === "failed" ? h("div", {class: "meta"}, `Tried ${st.attempts} times`) : null),
    h("div", {class: "row", style: "gap:12px"},
      thumb ? h("img", {class: "thumb", src: thumb, alt: `Screenshot after step ${st.number}`, loading: "lazy",
        onclick: (e) => { e.preventDefault(); openShot(thumb); }}) : null,
      h("span", {class: "meta num", style: "min-width:44px;text-align:right"}, st.status === "skipped" ? "Skipped" : st.seconds ? `${st.seconds} s` : "")));
  const detail = h("div", {class: "step-detail"},
    h("dl", {class: "kv"},
      st.action ? [h("dt", {}, "What was done"), h("dd", {}, st.action === "manual"
        ? (st.by === "ai" ? "Done by the AI, following the written step" : "Done by the tester, following the written step")
        : [ACTIONS[st.action] || st.action, st.value ? [" ", h("code", {}, st.value)] : ""])] : null,
      expected ? [h("dt", {}, "What should happen"), h("dd", {}, expected)] : null,
      happened ? [h("dt", {}, "What happened"), h("dd", {}, happened)] : null),
    st.compare ? h("div", {class: "compare"},
      h("div", {}, h("span", {class: "caption"}, "Expected"), st.compare.expected || "(empty)"),
      h("div", {}, h("span", {class: "caption"}, "Observed"), st.compare.observed || "(empty)")) : null,
    !after.length && st.screenshot_note ? h("div", {class: "meta row", style: "gap:6px"}, icon("image"), st.screenshot_note) : null,
    shots.length ? h("div", {class: "row", style: "align-items:flex-start"}, shots.map((src) => h("figure", {class: "shot-fig"},
      h("img", {class: "shot", src, loading: "lazy", alt: `${isBefore(src) ? "Before" : "Screenshot after"} step ${st.number}${context}`,
        onclick: () => openShot(src)}),
      h("figcaption", {class: "meta"}, isBefore(src) ? "Before: what it clicks, boxed in red" : "After the step")))) : null,
    technical.length ? disclose("Technical details", h("div", {class: "stack", style: "gap:6px;margin-top:8px"}, technical)) : null);
  const open = st.status === "failed";
  return h("li", {}, statusNode(st.status, st.number),
    h("details", {class: "step-row", open}, summary, detail), h("span", {}));
}

export function executionTimeline(steps, {total = 0, pictures = [], context = ""} = {}) {
  const list = steps.filter(Boolean);
  const items = list.map((st) => stepResult(st, {pictures, context}));
  const left = total - list.length;
  if (left > 0) items.push(h("li", {}, h("span", {class: "node"}, "…"), h("div", {class: "step-muted"}, `${plural(left, "more step")} to go`), h("span", {})));
  return h("ol", {class: "timeline", "aria-label": "Execution timeline"}, items);
}

// Everything one test's run produced, in one place: steps, screenshots, video, documents, context.
export function evidenceViewer(r, {run} = {}) {
  const pictures = r.steps.flatMap((st) => st.pictures.map((src) => ({src, caption: `Step ${st.number}: ${st.intent}${isBefore(src) ? " (before the click)" : ""}`,
    sub: `${r.test_title || r.test_id} · ${STATUS[st.status]?.label || st.status}${st.started_at ? " · " + new Date(st.started_at).toLocaleTimeString() : ""}`})));
  const key = `ev:${r.folder}`;
  let tab = state.open[key] || "steps";
  const panel = h("div", {class: "tab-panel", role: "tabpanel"});
  const draw = () => {
    state.open[key] = tab;
    panel.setAttribute("aria-labelledby", `tab-${tab}`);
    if (tab === "steps") panel.replaceChildren(executionTimeline(r.steps, {total: r.steps_total, pictures}));
    else if (tab === "pictures") panel.replaceChildren(pictures.length ? h("div", {class: "gallery"}, pictures.map((p, i) =>
      h("button", {type: "button", onclick: () => openViewer(pictures, i)}, h("img", {src: p.src, alt: p.caption, loading: "lazy"}),
        h("span", {class: "caption"}, p.caption)))) : emptyState({ic: "image", title: "No screenshots", text: "This run took screenshots only when a step failed, or none."}));
    else if (tab === "video") panel.replaceChildren(r.videos.length ? h("div", {class: "stack"}, r.videos.map((v) =>
      h("video", {class: "player", src: v, controls: true, preload: "metadata"}))) : emptyState({ic: "video", title: "No video", text: "Choose Video when starting a run to keep one."}));
    else if (tab === "documents") panel.replaceChildren(h("div", {class: "stack", style: "gap:8px"},
      r.document_url ? evidenceLink("Evidence document (Word)", "Every step with its screenshot and sign-off", r.document_url, "file") : null,
      run?.summary_url ? evidenceLink("Run summary (Word)", "All tests of this run", run.summary_url, "file") : null,
      r.record_url ? evidenceLink("Run record (JSON)", "For the test team: every step, timing and file fingerprints", r.record_url, "copy") : null,
      h("div", {class: "row", style: "margin-top:4px"}, button("Open the folder", {ic: "folder", size: "sm", onClick: () => openFolder(r.folder)}))));
    else panel.replaceChildren(h("dl", {class: "kv"},
      h("dt", {}, "Test"), h("dd", {}, r.test_title || r.test_id),
      h("dt", {}, "Oracle release"), h("dd", {}, run?.release ? h("span", {class: "release"}, run.release) : "Not recorded"),
      h("dt", {}, "Environment"), h("dd", {}, run?.environment || "Not recorded"),
      h("dt", {}, "Started"), h("dd", {}, r.started_at ? new Date(r.started_at).toLocaleString() : "–"),
      h("dt", {}, "Finished"), h("dd", {}, r.finished_at ? new Date(r.finished_at).toLocaleString() : "–"),
      h("dt", {}, "Duration"), h("dd", {}, r.duration || "–"),
      h("dt", {}, "Executed by"), h("dd", {}, run?.executed_by || "Not recorded"),
      h("dt", {}, "Evidence folder"), h("dd", {}, h("code", {}, r.folder))));
  };
  draw();
  return [tabs([["steps", "Steps", r.steps_total], ["pictures", "Screenshots", pictures.length], ["video", "Video", r.videos.length],
    ["documents", "Documents"], ["details", "Details"]], tab, (v) => { tab = v; draw(); }), panel];
}

function evidenceLink(title, sub, href, ic) {
  return h("a", {class: "list-item", href, style: "border:1px solid var(--border);border-radius:var(--r-card)"},
    h("span", {class: "icon-tile tone-primary"}, icon(ic)), h("div", {class: "grow"}, h("div", {class: "t"}, title), h("div", {class: "meta"}, sub)),
    icon("download"));
}

export function openFolder(path) {
  api("/api/open", {path}).catch((e) => toast(e.message));
}

// ------------------------------------------------------------------ overview components

export function metric({ic, label, value, unit, foot, bar, href, accent}) {
  const body = h("div", {class: "metric"},
    h("span", {class: "label"}, icon(ic), label),
    h("span", {class: "value"}, value, unit ? h("small", {}, unit) : null),
    bar || null,
    h("span", {class: "foot"}, foot));
  return href ? h("a", {class: `card${accent ? " accent" : ""}`, href}, body) : h("div", {class: `card${accent ? " accent" : ""}`}, body);
}

export function stackedBar(parts, label) {
  const total = parts.reduce((a, p) => a + p.n, 0) || 1;
  return h("div", {class: "bar", role: "img", "aria-label": label},
    parts.filter((p) => p.n).map((p) => h("span", {class: p.cls, style: `width:${(100 * p.n) / total}%`, title: `${p.label}: ${p.n}`})));
}

// Where each test stands for the environment's Oracle release.
export function releaseReadiness(r, onSetRelease, approval, redraw) {
  if (!r.release) {
    const inp = h("input", {class: "input", placeholder: "e.g. 26C", style: "max-width:140px", "aria-label": "Oracle release"});
    return card({title: "Release readiness", sub: "Which tests are validated on the Oracle release this pod runs",
      body: h("div", {class: "stack", style: "gap:12px"},
        h("p", {class: "muted"}, "Tell Quartermaster which Oracle release this pod is on. New runs are labelled with it, and this card then shows how far testing of that release has got."),
        h("div", {class: "row"}, inp, button("Save", {kind: "primary", onClick: () => onSetRelease(inp.value.trim())})))});
  }
  const parts = [
    {key: "validated", label: `Passed on ${r.release}`, cls: "fill-success", ic: "check", tone: "success", n: r.validated},
    {key: "failing", label: `Failed on ${r.release}`, cls: "fill-danger", ic: "x", tone: "danger", n: r.failing},
    {key: "baselined", label: "Passed on an earlier release, not yet run on this one", cls: "fill-info", ic: "layers", tone: "info", n: r.baselined},
    {key: "awaiting", label: "Never passed yet", cls: "fill-neutral", ic: "minus", tone: "neutral", n: r.awaiting},
  ];
  const pct = r.total ? Math.round((100 * r.validated) / r.total) : 0;
  return card({title: h("span", {}, h("span", {class: "release"}, r.release), " release readiness"),
    sub: `${r.validated} of ${plural(r.total, "test")} validated on ${r.release} (${pct}%)`,
    actions: [certificationButton(r.release), button("Run the rest", {size: "sm", ic: "runs", onClick: () => openRunDrawer(".")})],
    body: h("div", {class: "stack", style: "gap:14px"},
      stackedBar(parts, parts.map((p) => `${p.label}: ${p.n}`).join(", ")),
      approvalBlock(approval, redraw),
      h("div", {class: "grid g-2", style: "gap:8px 16px"}, parts.map((p) => h("div", {class: "row", style: "flex-wrap:nowrap"},
        h("span", {class: `badge tone-${p.tone}`}, icon(p.ic), h("span", {class: "num"}, p.n)), h("span", {class: "meta", style: "color:var(--text-2)"}, p.label)))),
      r.modules && r.modules.length > 1 ? h("div", {class: "stack", style: "gap:8px"}, h("div", {class: "caption"}, "BY MODULE"),
        r.modules.map((m) => h("div", {class: "row", style: "flex-wrap:nowrap;gap:12px"},
          h("span", {style: "width:110px", class: "ellipsis"}, m.module),
          h("div", {class: "grow"}, stackedBar(parts.map((p) => ({...p, n: m[p.key]})), `${m.module}: ${m.validated} of ${m.total} validated`)),
          h("span", {class: "meta num", style: "width:44px;text-align:right"}, `${m.validated}/${m.total}`)))) : null)});
}

export function releaseComparison(releases) {
  if (releases.length < 2) return null;
  return card({title: "Release comparison", sub: "Pass rate of the latest run of each test on each release",
    body: h("div", {class: "stack", style: "gap:10px"}, releases.map((r) => h("div", {class: "row", style: "flex-wrap:nowrap;gap:12px"},
      h("span", {class: "release", style: "width:64px"}, r.release),
      h("div", {class: "grow"}, stackedBar([{n: r.passed, cls: "fill-success", label: "Passed"}, {n: r.failed, cls: "fill-danger", label: "Failed"}],
        `${r.release}: ${r.passed} passed, ${r.failed} failed`)),
      h("span", {class: "num", style: "width:48px;text-align:right;font-weight:600"}, r.pass_rate === null ? "–" : `${r.pass_rate}%`),
      h("span", {class: "meta num", style: "width:88px"}, `${r.passed} of ${plural(r.tested, "test")}`),
      button("", {size: "sm", kind: "ghost", ic: "download", title: `Certification pack for ${r.release}`,
        href: certificationLink(r.release), attrs: {download: ""}}))))});
}

const certificationLink = (release) => `/api/certification?release=${encodeURIComponent(release)}`;

// A zip: a Word summary of every test's result on the release, to sign, and each test's evidence document.
function certificationButton(release) {
  return button("Certification pack", {size: "sm", ic: "download", href: certificationLink(release),
    title: `Download a Word summary of every test on ${release}, to sign, with each test's evidence document`, attrs: {download: ""}});
}

export function moduleCoverage(modules) {
  return card({title: "Module coverage", sub: "Last result of each test, by module",
    body: modules.length ? h("div", {class: "stack", style: "gap:12px"},
      h("div", {class: "legend"}, [["fill-success", "Passing"], ["fill-danger", "Failing"], ["fill-neutral", "Not run"]].map(([c, l]) =>
        h("span", {}, h("i", {class: c}), l))),
      modules.map((m) => h("a", {class: "row", href: "#/tests?module=" + encodeURIComponent(m.module), style: "flex-wrap:nowrap;gap:12px;color:inherit;text-decoration:none"},
        h("span", {style: "width:110px;font-weight:500", class: "ellipsis"}, m.module),
        h("div", {class: "grow"}, stackedBar([{n: m.passing, cls: "fill-success", label: "Passing"}, {n: m.failing, cls: "fill-danger", label: "Failing"},
          {n: m.not_run, cls: "fill-neutral", label: "Not run"}], `${m.module}: ${m.passing} passing, ${m.failing} failing, ${m.not_run} not run`)),
        h("span", {class: "meta num", style: "width:120px;text-align:right"}, `${m.tests - m.not_run} of ${plural(m.tests, "test")} run`)))) :
      emptyState({ic: "tests", title: "No tests yet", text: "Record one to get started.", actions: button("Record a test", {href: "#/record", size: "sm"})})});
}

// Few runs: a chronological list. Enough runs: a pass-rate chart with failures marked.
export function recentActivity(activity) {
  const recent = [...activity].reverse();
  if (!activity.length) {
    return card({title: "Recent activity", body: emptyState({ic: "runs", title: "No finished runs yet",
      text: "Results appear here as soon as a run finishes.", actions: button("New run", {kind: "primary", size: "sm", ic: "plus", onClick: () => openRunDrawer()})})});
  }
  const list = h("ol", {class: "timeline"}, recent.slice(0, 6).map((a) => h("li", {},
    statusNode(a.failed ? "failed" : "passed"),
    h("a", {href: runLink(a.run_id), style: "color:inherit;min-width:0"},
      h("div", {class: "ellipsis", style: "font-weight:500"}, a.label || testName(a.target)),
      h("div", {class: "meta"}, `${a.passed} of ${plural(a.total, "test")} passed`, a.release ? ` · ${a.release}` : "")),
    h("span", {class: "meta"}, when(a.at)))));
  if (activity.length < 8) {
    return card({title: "Recent activity", sub: `${plural(activity.length, "finished run")}. A trend chart appears once there are 8.`,
      actions: button("All runs", {size: "sm", href: "#/runs"}), body: list});
  }
  return card({title: "Pass rate over recent runs", sub: "Each point is one run; failed runs are marked. Select one to open it.",
    actions: button("All runs", {size: "sm", href: "#/runs"}), body: h("div", {class: "stack"}, passRateChart(activity), list)});
}

export function passRateChart(activity) {
  const narrow = document.getElementById("main").clientWidth < 700;
  const W = narrow ? 360 : 720, H = 200, L = 36, R = 12, T = 16, B = 26;
  const pts = activity.slice(-30);
  const x = (i) => L + (pts.length === 1 ? (W - L - R) / 2 : ((W - L - R) * i) / (pts.length - 1));
  const y = (v) => T + (H - T - B) * (1 - v / 100);
  const svg = s("svg", {viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Pass rate of the last ${pts.length} runs`});
  for (const v of [0, 25, 50, 75, 100]) {
    svg.append(s("line", {class: "gridline", x1: L, x2: W - R, y1: y(v), y2: y(v)}),
      s("text", {class: "axis", x: L - 8, y: y(v) + 4, "text-anchor": "end"}, `${v}%`));
  }
  const rate = (a) => Math.round((100 * a.passed) / a.total);
  svg.append(s("path", {d: pts.map((a, i) => `${i ? "L" : "M"}${x(i)},${y(rate(a))}`).join(" "), fill: "none",
    stroke: "var(--primary)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}));
  const sameDay = new Set(pts.map((a) => shortDate(a.at))).size === 1;
  pts.forEach((a, i) => {
    const failed = a.failed > 0;
    svg.append(s("circle", {cx: x(i), cy: y(rate(a)), r: failed ? 5 : 4, fill: failed ? "var(--danger)" : "var(--primary)",
      stroke: "var(--surface)", "stroke-width": 2}));
    if (failed) svg.append(s("path", {d: `M${x(i) - 2},${y(rate(a)) - 2} l4,4 M${x(i) + 2},${y(rate(a)) - 2} l-4,4`, stroke: "#fff", "stroke-width": 1.5}));
    if (i === 0 || i === pts.length - 1 || (!narrow && i % Math.ceil(pts.length / 6) === 0)) {
      svg.append(s("text", {class: "axis", x: x(i), y: H - 6, "text-anchor": "middle"}, sameDay ? clock(a.at) : shortDate(a.at)));
    }
    const slot = (W - L - R) / Math.max(pts.length - 1, 1);
    const hit = s("rect", {class: "hit", x: x(i) - slot / 2, y: T, width: slot, height: H - T - B, tabindex: 0,
      "aria-label": `${when(a.at)}: ${rate(a)}% passed, ${a.passed} of ${a.total}`});
    const show = (e) => showTip(e, `${when(a.at)}${a.release ? " · " + a.release : ""}`, [
      {color: token("--primary"), label: "Pass rate", value: `${rate(a)}%`},
      {color: token("--success"), label: "Passed", value: a.passed},
      {color: token("--danger"), label: "Failed", value: a.failed}]);
    hit.addEventListener("pointermove", show);
    hit.addEventListener("focus", () => { const b = hit.getBoundingClientRect(); show({clientX: b.x + b.width / 2, clientY: b.y + 40}); });
    hit.addEventListener("pointerleave", hideTip);
    hit.addEventListener("blur", hideTip);
    hit.addEventListener("click", () => { hideTip(); location.hash = runLink(a.run_id); });
    hit.addEventListener("keydown", (e) => { if (e.key === "Enter") location.hash = runLink(a.run_id); });
    svg.append(hit);
  });
  const last = pts[pts.length - 1];
  svg.append(s("text", {class: "val", x: x(pts.length - 1), y: y(rate(last)) - 10, "text-anchor": "end"}, `${rate(last)}%`));
  return h("div", {class: "chart"}, svg);
}


// A picture taken just before a click (step-03-before.png), not after the step.
export function isBefore(src) {
  return /-before\.png$/i.test(String(src).split("?")[0]);
}
