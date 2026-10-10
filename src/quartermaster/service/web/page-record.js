// Record a test: a short form, then a calm, focused recorder workspace.
import {
  api, button, callout, card, emptyState, field, h, icon, input, plural, popover, remember, statusBadge, toast,
} from "./ui.js";
import {openRunDrawer} from "./components.js";
import {loadCommon, schedule, state, testLink} from "./state.js";
import {show} from "./app.js";

let ws = null; // the recorder workspace while a recording is in progress

export async function recordPage() {
  if (!state.status || !ws) await loadCommon();
  const rec = await api("/api/recording");
  if (state.page !== "record") return;
  if ((rec.mode === "manual" || rec.mode === "ai") && (rec.status === "recording" || rec.status === "saving")) { location.hash = "#/manual-run"; return; }
  const crumbs = [{label: "Record a test"}];
  if (rec.status === "recording" || rec.status === "saving") {
    if (!ws || !ws.el.isConnected) {
      ws = workspace(rec);
      show(crumbs, ws.el);
    }
    ws.update(rec);
    schedule(recordPage, 1000);
    return;
  }
  ws = null;
  show(crumbs,
    h("div", {class: "page-head"}, h("div", {}, h("h1", {}, "Record a test"),
      h("p", {class: "lead"}, "Create a test by doing the steps yourself, once, in Oracle Fusion. No code."))),
    outcome(rec),
    h("div", {class: "grid g-main"}, form(rec), howItWorks()));
}

// ------------------------------------------------------------------ before: the form

function slug(module, title) {
  const clean = (x) => x.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return [clean(module), clean(title).split("-").filter(Boolean).slice(0, 5).join("-")].filter(Boolean).join(".");
}

function form() {
  const saved = remember("record") || {};
  const title = input({placeholder: "Search for a worker and open their record"});
  const module = input({placeholder: "HCM", value: saved.module || ""});
  const product = input({placeholder: "Global Human Resources", value: saved.product || ""});
  const id = input({placeholder: "hcm.search-for-a-worker"});
  const persona = input({placeholder: "HR Specialist", value: saved.persona || ""});
  const file = input({placeholder: "recorded/search_for_a_worker.yaml"});
  let touched = false;
  const autoId = () => { if (!touched) id.value = slug(module.value, title.value); };
  title.oninput = autoId; module.oninput = autoId;
  id.oninput = () => { touched = true; };
  const start = button("Start recording", {kind: "primary", ic: "record", disabled: !state.status.ready, onClick: async (e) => {
    const request = {title: title.value.trim(), id: id.value.trim(), module: module.value.trim(), product: product.value.trim(),
      persona: persona.value.trim(), file: file.value.trim()};
    remember("record", {module: request.module, product: request.product, persona: request.persona});
    e.currentTarget.disabled = true;
    try { await api("/api/recording", request); recordPage(); } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
  }});
  return card({title: "New recording", sub: "Sign-in is done for you and is never recorded.",
    body: h("div", {class: "stack"},
      h("div", {class: "fields"},
        h("div", {class: "wide"}, field("Test name", title, "What the test checks, in plain words.", "r-title")),
        field("Module", module, null, "r-module"),
        field("Product", product, null, "r-product"),
        field("Test id", id, "Short and unique; filled in from the name.", "r-id"),
        field("Job role (optional)", persona, "The role the test runs as.", "r-persona"),
        h("div", {class: "wide"}, field("Save as (optional)", file, "Inside the tests folder. An existing test is never overwritten.", "r-file"))),
      h("div", {class: "row"}, start, state.status.ready ? null : h("span", {class: "meta"}, "Set up the pod first, in Settings.")))});
}

function howItWorks() {
  const steps = [
    ["Name the test", "Say what it checks and which module it belongs to."],
    ["Do the steps once", "A browser opens, signed in. Clicks, typing and choices become steps."],
    ["Add checks and notes", "Checks prove the outcome; notes say what should happen at a step."],
    ["Mask what must stay private", "A masked value is never saved; replays read it from this computer."],
    ["Replay every quarter", "Run it after each Oracle update to get fresh evidence."],
  ];
  return card({title: "How it works", body: h("ol", {class: "timeline"}, steps.map(([t, d], i) =>
    h("li", {}, h("span", {class: "node info"}, i + 1), h("div", {}, h("div", {style: "font-weight:500"}, t), h("div", {class: "meta"}, d)), h("span", {}))))});
}

function outcome(rec) {
  if (rec.mode === "manual" || rec.mode === "ai") return null; // manual scenarios have their own page
  if (rec.status === "saved") {
    return h("div", {style: "margin-bottom:16px"}, card({body: h("div", {class: "stack", style: "gap:12px"},
      h("div", {class: "row"}, statusBadge("passed", "Saved"), h("strong", {}, rec.title),
        h("span", {class: "meta"}, `${rec.message || ""} · ${rec.file}`)),
      rec.masked ? callout("info", "Masked values.", rec.masked) : null,
      h("div", {class: "row"},
        button("Run it now", {kind: "primary", ic: "runs", disabled: !state.status.ready, onClick: () => openRunDrawer(rec.file)}),
        button("Open the test", {href: testLink(rec.file)})))}));
  }
  if (rec.status === "error") return h("div", {style: "margin-bottom:16px"}, callout("danger", "The recording was not saved.", rec.message || ""));
  return null;
}

// ------------------------------------------------------------------ during: the workspace

function workspace(rec) {
  const dot = h("span", {class: "rec-dot", "aria-hidden": "true"});
  const stateLabel = h("span", {}, "Recording");
  const timer = h("span", {class: "timer", "aria-label": "Recording time"}, "00:00");
  const message = h("div", {class: "meta", role: "status", "aria-live": "polite", style: "min-height:18px"});
  const steps = h("div", {});
  const count = h("span", {class: "meta"});
  let feed = null;
  let started = rec.started_at;

  const send = async (command, text) => {
    try { await api(`/api/recording/${command}`, text === undefined ? {} : {text}); recordPage(); }
    catch (err) { toast(err.message); }
  };
  const pauseBtn = button("Pause", {ic: "pause", onClick: () => send(feed?.paused ? "resume" : "pause")});
  const checkBtn = button("Add check", {ic: "target", pressed: false, title: "The next click in the browser records a check instead of an action",
    onClick: () => send("check")});
  const waitBtn = button("Wait for process", {ic: "clock", title: "After submitting a scheduled process: the test waits until it finishes, and passes only if it succeeded",
    onClick: () => send("wait")});
  const noteBtn = button("Add note", {ic: "note", title: "What should happen at the last step", attrs: {"aria-haspopup": "dialog"},
    onClick: (e) => popover(e.currentTarget, (close) => {
      const text = h("textarea", {class: "input", rows: "3", placeholder: "e.g. The worker's record opens on the Employment page", autofocus: true,
        "aria-label": "Note"});
      return [h("div", {class: "pop-body"}, h("div", {class: "label"}, "Note for the last step"),
        h("div", {class: "meta"}, "Saved as what should happen at that step, and shown in the evidence document."), text),
      h("div", {class: "pop-foot"}, h("span", {class: "grow"}), button("Cancel", {size: "sm", onClick: close}),
        button("Add note", {size: "sm", kind: "primary", onClick: () => { const v = text.value.trim(); close(); if (v) send("note", v); }}))];
    })});
  const maskBtn = button("Mask value", {ic: "eyeoff", title: "Do not save the last typed value; replays read it from an environment variable",
    onClick: () => send("mask")});
  const undoBtn = button("Undo", {ic: "undo", title: "Forget the last recorded step", onClick: () => send("undo")});
  const finishBtn = button("Finish and save", {kind: "primary", ic: "check", onClick: () => send("stop")});

  const el = h("div", {},
    h("div", {class: "page-head"}, h("div", {}, h("h1", {}, rec.title), h("p", {class: "lead"}, `Saving to ${rec.file}`))),
    h("div", {class: "grid g-main"},
      h("section", {class: "card", "aria-label": "Recorder"},
        h("div", {class: "rec-bar", role: "toolbar", "aria-label": "Recorder controls"},
          h("span", {class: "rec-state"}, dot, stateLabel), timer, h("span", {class: "grow"}),
          pauseBtn, checkBtn, waitBtn, noteBtn, maskBtn, undoBtn, finishBtn),
        h("div", {class: "card-body stack", style: "gap:12px"},
          h("div", {class: "row", style: "justify-content:space-between"}, h("h3", {}, "Recorded steps"), count),
          message, steps)),
      card({title: "In the browser window", body: h("ol", {class: "timeline"}, [
        ["Do the steps", "The browser that opened is already signed in. Work as you normally would."],
        ["Check what matters", "Add check, then click a value on the screen that must be right."],
        ["Keep secrets out", "After typing something private, press Mask value."],
        ["Finish", "Finish and save here, or Stop recording in the browser's toolbar."],
      ].map(([t, d], i) => h("li", {}, h("span", {class: "node"}, i + 1), h("div", {}, h("div", {style: "font-weight:500"}, t), h("div", {class: "meta"}, d)), h("span", {}))))})));

  const tick = () => {
    if (!started) return;
    const end = feed?.paused && feed.paused_since ? new Date(feed.paused_since) : new Date();
    const secs = Math.max(0, Math.floor((end - new Date(started)) / 1000 - (feed?.paused_seconds || 0)));
    timer.textContent = `${String(Math.floor(secs / 60)).padStart(2, "0")}:${String(secs % 60).padStart(2, "0")}`;
  };
  const clockTimer = setInterval(() => { if (!el.isConnected) clearInterval(clockTimer); else tick(); }, 1000);

  const update = (r) => {
    started = r.started_at;
    if (r.status === "saving") {
      stateLabel.textContent = "Saving";
      dot.classList.add("paused");
      [pauseBtn, checkBtn, waitBtn, noteBtn, maskBtn, undoBtn, finishBtn].forEach((b) => { b.disabled = true; });
      message.textContent = "Saving the test and checking it can be read.";
      return;
    }
    feed = r.feed;
    const paused = Boolean(feed?.paused);
    dot.classList.toggle("paused", paused);
    stateLabel.textContent = paused ? "Paused" : "Recording";
    pauseBtn.replaceChildren(icon(paused ? "play" : "pause"), h("span", {}, paused ? "Resume" : "Pause"));
    checkBtn.setAttribute("aria-pressed", String(Boolean(feed?.checking)));
    checkBtn.disabled = paused || !feed;
    maskBtn.disabled = !feed?.steps.some((st) => ["fill", "select"].includes(st.action) && st.value !== "••••••");
    undoBtn.disabled = !feed?.steps.length;
    noteBtn.disabled = !feed?.steps.length;
    waitBtn.disabled = paused || !feed?.steps.length;
    message.textContent = !feed ? "Opening the browser and signing in to Oracle Fusion…" : feed.checking ? "Click the value to check in the browser window." : feed.message || "";
    count.textContent = feed ? plural(feed.steps.length, "step") : "";
    const sig = JSON.stringify(feed?.steps || []);
    if (steps.dataset.sig !== sig) {
      steps.dataset.sig = sig;
      steps.replaceChildren(feed?.steps.length ? h("ol", {class: "timeline"}, feed.steps.map((st) => h("li", {},
        h("span", {class: "node"}, st.number),
        h("div", {style: "min-width:0"},
          h("div", {}, st.intent),
          st.value ? h("div", {class: "meta", style: "margin-top:2px"}, st.value === "••••••" ? [icon("lock"), " Masked: not saved"] : h("code", {}, st.value)) : null,
          st.expected ? h("div", {class: "meta", style: "margin-top:2px"}, icon("note"), " ", st.expected) : null),
        h("span", {})))) : emptyState({ic: "record", tone: "danger", title: feed ? "Waiting for your first step" : "Opening the browser",
        text: feed ? "Do the first step of the test in the browser window." : "It signs in to Oracle Fusion for you."}));
    }
    tick();
  };
  return {el, update};
}

