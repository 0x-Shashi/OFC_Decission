// To review: what the AI prepared, each scenario with the picture of every step, so a person can
// check many at once and approve the right ones. Also shows the progress of Prepare all.
import {api, badge, button, card, emptyState, h, plural, toast, when} from "./ui.js";
import {loadCommon, runLink, schedule, state} from "./state.js";
import {show} from "./app.js";
import {runScenario, testsViewSwitch} from "./page-manual.js";

const selected = new Set(); // kept across the live refreshes while Prepare all runs

const OUTCOME = {
  waiting: ["Waiting", "neutral"], preparing: ["Preparing now", "info"], prepared: ["Ready to review", "success"],
  stopped: ["Stopped", "danger"], not_started: ["Not started", "neutral"],
};

export async function startPrepareAll(count, leftOut = 0) {
  if (!state.status?.ready) { toast("Set up the pod and its sign-in in Settings first."); return; }
  if (!confirm(`The AI prepares ${plural(count, "scenario")}, one after another, in the browser that opens.\n\n` +
    (leftOut ? `${plural(leftOut, "scenario")} ${leftOut === 1 ? "is" : "are"} left out: test data missing, values to type that the script does not give, or no steps. The AI would stop at those; do them by hand.\n\n` : "") +
    "You do not need to watch. Each one then waits in To review until you approve it. Start?")) return;
  try {
    await api("/api/manual/prepare-all", {});
    location.hash = "#/tests?view=review";
  } catch (err) { toast(err.message); }
}

export async function reviewPage() {
  const [, data, summary] = await Promise.all([loadCommon(), api("/api/manual/review"), api("/api/manual")]);
  if (state.page !== "tests" || state.query.view !== "review") return;
  const batch = data.prepare_all;
  const running = batch.status === "running" || batch.status === "stopping";
  const list = data.scenarios;
  for (const id of [...selected]) if (!list.find((s) => s.id === id)) selected.delete(id);

  const approveBtn = button("Approve selected", {kind: "primary", ic: "check"});
  const allBox = h("input", {type: "checkbox", "aria-label": "Select every scenario"});
  const sync = () => {
    approveBtn.disabled = !selected.size;
    approveBtn.querySelector("span").textContent = selected.size ? `Approve selected (${selected.size})` : "Approve selected";
    allBox.checked = list.length > 0 && selected.size === list.length;
    allBox.indeterminate = selected.size > 0 && selected.size < list.length;
  };
  const boxes = [];
  allBox.onchange = () => {
    for (const s of list) allBox.checked ? selected.add(s.id) : selected.delete(s.id);
    boxes.forEach((b) => { b.checked = allBox.checked; });
    sync();
  };
  approveBtn.onclick = async () => {
    const ids = list.filter((s) => selected.has(s.id)).map((s) => s.id);
    if (!ids.length) return;
    if (!confirm(`Approve ${plural(ids.length, "scenario")}? From then on Run plays ${ids.length === 1 ? "it" : "them"} by ${ids.length === 1 ? "itself" : "themselves"}, exactly as in the pictures.`)) return;
    approveBtn.disabled = true;
    try {
      await api("/api/manual/approve", {ids});
      ids.forEach((id) => selected.delete(id));
      toast(`Approved ${plural(ids.length, "scenario")}. Run now plays ${ids.length === 1 ? "it" : "them"} by ${ids.length === 1 ? "itself" : "themselves"}.`);
      reviewPage();
    } catch (err) { toast(err.message); sync(); }
  };

  const scenarioCard = (s) => {
    const box = h("input", {type: "checkbox", checked: selected.has(s.id), "aria-label": `Select ${s.title}`,
      onchange: (e) => { e.target.checked ? selected.add(s.id) : selected.delete(s.id); sync(); }});
    boxes.push(box);
    const failed = s.steps.filter((st) => st.status !== "passed").length;
    return h("section", {class: "card", style: "padding:14px 16px"},
      h("div", {class: "row", style: "gap:10px;align-items:flex-start"},
        h("label", {style: "padding-top:2px"}, box),
        h("div", {class: "grow", style: "min-width:min(220px, 70%)"},
          h("div", {class: "row", style: "gap:8px"}, h("strong", {}, s.title), h("span", {class: "tag"}, s.ref),
            failed ? badge(`${plural(failed, "step")} not passed`, "danger") : null),
          h("div", {class: "meta"}, `${s.module} · ${s.product} · ${s.prepared.by} · ${when(s.prepared.at)}`)),
        h("div", {class: "row", style: "gap:6px;flex-wrap:nowrap"},
          button("Open the run", {size: "sm", ic: "runs", href: runLink(s.prepared.run_id)}),
          button("Do it by hand", {size: "sm", ic: "file", disabled: running || !state.status.ready,
            title: running ? "Wait until Prepare all has finished" : "The pictures are wrong: do it yourself instead",
            onClick: () => runScenario(s.id, {byHand: true})}))),
      h("ol", {class: "review-steps", "aria-label": `Steps of ${s.title}`}, s.steps.map((st, i) => h("li", {},
        st.picture_url
          ? h("a", {href: st.picture_url, target: "_blank", rel: "noopener", title: `Open the picture of step ${i + 1}`},
            h("img", {src: st.picture_url, alt: `Screen after step ${i + 1}: ${st.name}`, class: "guide-shot", loading: "lazy"}))
          : h("div", {class: "guide-shot review-none"}, "No picture"),
        h("div", {class: "meta", style: "margin-top:4px"}, `${i + 1}. ${st.name}`),
        st.status !== "passed" ? badge("Not passed", "danger") : null))));
  };

  const review = list.length ? [
    h("div", {class: "row", style: "gap:10px;margin-bottom:12px"},
      h("label", {class: "row", style: "gap:8px"}, allBox, "Select all"), h("span", {class: "grow"}), approveBtn),
    h("p", {class: "hint", style: "margin:0 0 12px"}, "Compare each picture with its step. Approve only the scenarios where every picture is right; open the run to see a picture full size."),
    h("div", {class: "stack", style: "gap:12px"}, list.map(scenarioCard)),
  ] : [emptyState({ic: "check", title: "Nothing waits for review",
    text: running ? "Scenarios appear here as the AI finishes them." : "When the AI prepares a scenario, it waits here until a person approves it."})];

  const counts = {automated: state.tests.length, manual: summary.scenarios.length, review: list.length};
  show([{label: "Tests", href: "#/tests"}, {label: "To review"}],
    h("div", {class: "page-head"},
      h("div", {}, h("h1", {}, "Tests"), h("p", {class: "lead"}, "What the AI prepared. Check the pictures, then approve."))),
    h("div", {class: "toolbar"}, testsViewSwitch("review", counts)),
    batch.items.length ? batchCard(batch, running) : null,
    card({title: list.length ? `To review: ${plural(list.length, "scenario")}` : "To review", body: review}));
  sync();
  if (running) schedule(reviewPage, 2500);
}

function batchCard(batch, running) {
  const c = batch.counts || {};
  const finished = (c.prepared || 0) + (c.stopped || 0);
  const now = batch.items.find((i) => i.outcome === "preparing");
  const head = running
    ? `Prepare all: ${finished} of ${batch.total} done${now ? `. Now: ${now.title}` : ""}`
    : `Prepare all finished: ${plural(c.prepared || 0, "scenario")} ready to review${c.stopped ? `, ${c.stopped} stopped` : ""}${c.not_started ? `, ${c.not_started} not started` : ""}`;
  return card({
    title: head,
    sub: running ? "The AI is using the browser. You can leave this page; it goes on." : `Started ${when(batch.started_at).toLowerCase()}.`,
    actions: running ? [
      now ? button("Watch the AI", {size: "sm", ic: "play", href: "#/manual-run"}) : null,
      button(batch.status === "stopping" ? "Stopping…" : "Stop", {size: "sm", ic: "stop", disabled: batch.status === "stopping",
        title: "Stop after the scenario being prepared now", onClick: async () => {
          try { await api("/api/manual/prepare-all/stop", {}); reviewPage(); } catch (err) { toast(err.message); }
        }}),
    ] : null,
    body: h("ul", {class: "stack", style: "gap:6px;margin:0;padding:0;list-style:none"}, batch.items.map((i) => {
      const [label, tone] = OUTCOME[i.outcome] || [i.outcome, "neutral"];
      return h("li", {class: "row", style: "gap:8px;flex-wrap:nowrap;align-items:flex-start"},
        h("span", {style: "min-width:120px"}, badge(label, tone, i.outcome === "preparing" ? "loader" : null)),
        h("div", {class: "grow", style: "min-width:0"}, h("span", {}, i.title), h("span", {class: "meta"}, ` · ${i.ref}`),
          i.why ? h("div", {class: "meta"}, i.why) : null),
        i.run_id ? h("a", {href: runLink(i.run_id), class: "meta"}, "Open the run") : null);
    })),
  });
}
