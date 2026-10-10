// Doing a manual scenario by hand: the scenario's steps on one side, the signed-in browser on the
// other. The tester marks each step Pass or Fail; Quartermaster takes a picture each time and
// remembers the clicks, so next time the scenario plays by itself.
import {api, button, callout, card, emptyState, h, icon, statusBadge, toast} from "./ui.js";
import {loadCommon, runLink, schedule, state} from "./state.js";
import {show} from "./app.js";

let ws = null;

export async function manualRunPage() {
  if (!state.status || !ws) await loadCommon();
  const rec = await api("/api/recording");
  if (state.page !== "manual-run") return;
  if (rec.mode !== "manual" && rec.mode !== "ai") {
    ws = null;
    if (rec.status === "recording" || rec.status === "saving") { location.hash = "#/record"; return; }
    show([{label: "Tests", href: "#/tests?view=manual"}, {label: "Run by hand"}],
      h("div", {class: "card"}, emptyState({ic: "file", title: "No scenario is being done by hand",
        text: "Open Manual scenarios and click Run on a scenario.",
        actions: button("Manual scenarios", {kind: "primary", size: "sm", href: "#/tests?view=manual"})})));
    return;
  }
  const crumbs = [{label: "Tests", href: "#/tests?view=manual"}, {label: rec.title}];
  if (rec.status === "recording" || rec.status === "saving") {
    if (!ws || !ws.el.isConnected || ws.started !== rec.started_at) { ws = workspace(rec); show(crumbs, ws.el); }
    ws.update(rec);
    schedule(manualRunPage, 1000);
    return;
  }
  ws = null;
  // During Prepare all the next scenario starts by itself: keep following it.
  const batch = rec.mode === "ai" ? await api("/api/manual/prepare-all") : null;
  if (state.page !== "manual-run") return;
  const batchRunning = batch && (batch.status === "running" || batch.status === "stopping");
  show(crumbs, batchRunning ? h("div", {class: "stack"},
    callout("info", "Prepare all is running.", "The next scenario starts by itself in a moment. ",
      h("a", {href: "#/tests?view=review"}, "See progress and what is ready to review")), outcome(rec)) : outcome(rec));
  if ((rec.status === "saved" && !rec.run_id) || batchRunning) schedule(manualRunPage, 1500);
}

function outcome(rec) {
  if (rec.mode === "ai" && rec.status !== "error") return aiOutcome(rec);
  if (rec.status === "error") {
    return h("div", {class: "stack"},
      h("div", {class: "page-head"}, h("div", {}, h("h1", {}, rec.title), h("p", {class: "lead"}, "Not saved"))),
      callout("danger", "Nothing was saved.", rec.message || ""),
      h("div", {class: "row"}, button("Back to manual scenarios", {href: "#/tests?view=manual"})));
  }
  const passed = rec.result === "passed";
  return h("div", {class: "stack"},
    h("div", {class: "page-head"}, h("div", {}, h("h1", {}, rec.title),
      h("p", {class: "lead"}, `Done by hand${rec.tester ? ` by ${rec.tester}` : ""}${rec.release ? ` on ${rec.release}` : ""}.`))),
    card({body: h("div", {class: "stack", style: "gap:14px"},
      h("div", {class: "row"}, statusBadge(passed ? "passed" : "failed"),
        h("strong", {}, passed ? "Every step passed." : "At least one step failed or was not checked.")),
      rec.automated
        ? callout("info", "Next time it plays by itself.", "Quartermaster remembered your clicks. Click Run on this scenario again and it runs without you, with the same evidence.")
        : callout("warning", "It cannot play by itself yet.", rec.message || "No clicks were recorded."),
      h("div", {class: "row"},
        rec.document_url ? button("Evidence document", {kind: "primary", ic: "download", href: rec.document_url}) : null,
        rec.run_id ? button("Open the run", {ic: "runs", href: runLink(rec.run_id)}) : h("span", {class: "meta"}, "Adding it to the run history…"),
        button("Back to manual scenarios", {href: "#/tests?view=manual"})))}));
}

// After the AI has prepared a scenario: what a person must do next.
function aiOutcome(rec) {
  const links = [
    rec.run_id ? button("See what the AI did", {kind: rec.automated ? "" : "primary", ic: "runs", href: runLink(rec.run_id)}) : h("span", {class: "meta"}, "Adding it to the run history…"),
    rec.document_url ? button("Evidence document", {ic: "download", href: rec.document_url}) : null,
    rec.diary_url ? button("What the AI answered", {kind: "ghost", ic: "file", href: rec.diary_url, attrs: {target: "_blank", rel: "noopener"}}) : null,
  ];
  return h("div", {class: "stack"},
    h("div", {class: "page-head"}, h("div", {}, h("h1", {}, rec.title), h("p", {class: "lead"}, `Prepared by AI${rec.release ? ` on ${rec.release}` : ""}.`))),
    card({body: h("div", {class: "stack", style: "gap:14px"},
      rec.automated
        ? [callout("warning", "Check it before it runs.", "Open what the AI did and compare each picture with its step. If every picture is right, approve it: from then on Run plays it by itself. If something is wrong, do it by hand instead."),
          h("div", {class: "row"}, ...links,
            button("Approve", {kind: "primary", ic: "check", disabled: !rec.run_id, onClick: async () => {
              const {approveScenario} = await import("./page-manual.js");
              approveScenario(rec.scenario_id);
            }}),
            button("Do it by hand", {ic: "file", onClick: async () => {
              const {runScenario} = await import("./page-manual.js");
              runScenario(rec.scenario_id, {byHand: true});
            }}))]
        : [callout("danger", "The AI stopped, so nothing was saved to run.", `${(rec.message || "It could not finish every step").replace(/\.?\s*$/, ".")} Do this scenario by hand: it only needs doing once.`),
          h("div", {class: "row"}, ...links,
            button("Run by hand", {kind: "primary", ic: "play", onClick: async () => {
              const {runScenario} = await import("./page-manual.js");
              runScenario(rec.scenario_id, {byHand: true});
            }}),
            button("Back to manual scenarios", {href: "#/tests?view=manual"}))])}));
}

function workspace(rec) {
  const byAI = rec.mode === "ai";
  const list = h("ol", {class: "guide", "aria-label": "Steps"});
  const message = h("div", {class: "meta", role: "status", "aria-live": "polite", style: "min-height:18px"});
  const progress = h("span", {class: "meta"});
  const send = async (command, text) => {
    try { await api(`/api/recording/${command}`, text === undefined ? {} : {text}); manualRunPage(); }
    catch (err) { toast(err.message); }
  };
  let steps = [];
  const checkBtn = button("Add check", {ic: "target", title: "The next click in the browser checks a value instead of doing an action",
    onClick: () => send("check")});
  const stopBtn = button("Stop", {ic: "stop", title: "Stop the AI. Nothing is saved to run.", onClick: () => send("stop")});
  const waitBtn = button("Wait for process", {ic: "clock", title: "After you submit a scheduled process: the saved test then waits until it finishes, and passes only if it succeeded",
    onClick: () => send("wait")});
  const finishBtn = button("Finish", {kind: "primary", ic: "check", onClick: () => {
    const open = steps.filter((st) => !st.status).length;
    if (open && !confirm(`${open} step${open === 1 ? " is" : "s are"} not marked yet and will count as not checked, so the run will not pass. Finish anyway?`)) return;
    send("stop");
  }});

  const el = h("div", {},
    h("div", {class: "page-head"},
      h("div", {}, h("h1", {}, rec.title),
        h("p", {class: "lead"}, `${byAI ? "Prepared by AI · " : ""}${rec.ref} · ${rec.workbook}${rec.release ? ` · Release ${rec.release}` : ""}`)),
      h("div", {class: "row"}, ...(byAI ? [stopBtn] : [checkBtn, waitBtn, finishBtn]))),
    byAI
      ? callout("info", "The AI is doing the steps in the browser window that opened. You can watch it.",
        "It reads each written step, chooses a button or link on the screen, and takes a picture when the step is done. It stops rather than guess, and never presses Save, Submit or Delete unless the step says so. You check its pictures at the end.")
      : callout("info", "Do each step in the browser window that opened (it is already signed in), then mark it here.",
        "Quartermaster takes a picture of the screen when you mark a step, and remembers your clicks so the scenario can play by itself next time. Add check, then click a value on the screen, proves a page shows what it should."),
    h("section", {class: "card section"},
      h("div", {class: "card-head"}, h("div", {class: "grow"}, h("h3", {}, "Steps"), message), progress),
      h("div", {class: "card-body"}, list)));

  const row = (st, current) => {
    const note = h("input", {class: "input", placeholder: "What went wrong?", "aria-label": `What went wrong at step ${st.number}`});
    const failBox = h("div", {class: "row", style: "gap:8px;margin-top:8px;display:none"}, note,
      button("Mark failed", {size: "sm", kind: "danger", onClick: () => send("result", `${st.number} fail ${note.value.trim()}`)}),
      button("Cancel", {size: "sm", kind: "ghost", onClick: () => { failBox.style.display = "none"; }}));
    note.onkeydown = (e) => { if (e.key === "Enter") send("result", `${st.number} fail ${note.value.trim()}`); };
    const marks = h("div", {class: "row", style: "gap:6px;flex-wrap:nowrap"},
      button("Pass", {size: "sm", kind: st.status === "passed" ? "primary" : "", ic: "check",
        title: `Step ${st.number} worked`, onClick: () => send("result", `${st.number} pass`)}),
      button("Fail", {size: "sm", ic: "x", title: `Step ${st.number} did not work`,
        onClick: () => { failBox.style.display = "flex"; note.focus(); }}));
    return h("li", {class: `guide-step${current ? " current" : ""}${st.status ? ` is-${st.status}` : ""}`, "aria-current": current ? "step" : null},
      h("span", {class: `node ${st.status === "passed" ? "success" : st.status === "failed" ? "danger" : current ? "info" : ""}`},
        st.status === "passed" ? icon("check") : st.status === "failed" ? icon("x") : st.number),
      h("div", {style: "min-width:0", class: "grow"},
        st.case_name && st.case !== st.case_name ? h("div", {class: "caption"}, `${st.case} · ${st.case_name}`) : null,
        h("div", {style: "font-weight:500;white-space:pre-line"}, st.action),
        st.expected ? h("div", {class: "meta", style: "white-space:pre-line"}, "Expected: ", st.expected) : null,
        st.status === "failed" && st.note ? h("div", {class: "meta", style: "color:var(--danger)"}, byAI ? "" : "Failed: ", st.note) : null,
        st.picture_note ? h("div", {class: "meta"}, st.picture_note) : null,
        failBox),
      h("div", {class: "stack", style: "gap:6px;align-items:flex-end"},
        st.picture_url ? h("a", {href: st.picture_url, target: "_blank", rel: "noopener", title: `Picture of step ${st.number}`},
          h("img", {src: st.picture_url, alt: `Screen at step ${st.number}`, class: "guide-shot"})) : null,
        byAI ? null : marks));
  };

  const update = (r) => {
    const feed = r.feed;
    if (r.status === "saving") {
      [checkBtn, waitBtn, finishBtn, stopBtn].forEach((b) => { b.disabled = true; });
      message.textContent = byAI ? "Saving what the AI did…" : "Saving the evidence and the steps you clicked…";
      return;
    }
    steps = feed?.guide || [];
    message.textContent = !feed ? "Opening the browser and signing in to Oracle Fusion…" : feed.checking ? "Click the value to check in the browser window." : feed.message || "";
    checkBtn.setAttribute("aria-pressed", String(Boolean(feed?.checking)));
    checkBtn.disabled = !feed;
    waitBtn.disabled = !feed;
    finishBtn.disabled = !feed;
    const done = steps.filter((st) => st.status).length;
    progress.textContent = steps.length ? `${done} of ${steps.length} ${byAI ? "done" : "marked"}` : "";
    const sig = JSON.stringify(steps);
    if (list.dataset.sig === sig) return;
    list.dataset.sig = sig;
    const current = steps.findIndex((st) => !st.status);
    list.replaceChildren(...(steps.length ? steps.map((st, i) => row(st, i === current)) : [h("li", {}, emptyState({ic: "loader", title: "Opening the browser",
      text: "It signs in to Oracle Fusion for you."}))]));
    list.querySelector(".current")?.scrollIntoView({block: "nearest", behavior: "smooth"});
  };
  return {el, update, started: rec.started_at}; // a new scenario (Prepare all) gets a new workspace
}
