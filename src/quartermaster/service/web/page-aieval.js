// AI quality check (Settings, AI assistant): ask the chosen AI a fixed set of made-up questions and show how it did.
import {api, badge, button, callout, card, disclose, h, toast, when} from "./ui.js";
import {settingsPage} from "./page-settings.js";

const VERDICT = {
  good: ["Good", "success"], usable: ["Usable with care", "warning"], weak: ["Weak", "danger"], not_run: ["Could not ask", "neutral"],
};
const OUTCOME = {
  correct: ["Right pick", "success"], right_none: ["Right: none", "success"], missed: ["Missed it", "neutral"],
  wrong: ["Wrong pick", "danger"], unreadable: ["Unreadable answer", "warning"], error: ["Could not ask", "neutral"],
};

export async function aiEvalCard(ai) {
  const d = await api("/api/ai/eval").catch(() => null);
  if (!d) return null;
  const ready = Boolean(ai.provider) && !ai.problem;
  if (d.running) setTimeout(() => { if (location.hash.startsWith("#/settings")) settingsPage(); }, 2000);
  const last = d.last;
  const c = last?.counts || {};
  const [vword, vtone] = last ? VERDICT[last.verdict] || ["", "neutral"] : ["", "neutral"];
  return card({title: "AI quality check", sub: last ? `${last.label}: ${Math.round(last.score * 100)}% right, ${when(last.at).toLowerCase()}` : "Not run yet",
    actions: last ? badge(vword, vtone) : null,
    body: h("div", {class: "stack"},
      h("p", {class: "hint", style: "margin:0"}, `Asks the AI above ${d.questions} made-up questions of the kind it answers when a test cannot find a button: which control did this become, or is it simply not there? Each has a right answer. A wrong pick is the harmful one. Only invented screens are sent: nothing from your pod or your tests.`),
      d.error ? callout("warning", "The check did not work.", d.error) : null,
      h("div", {class: "row"}, button(d.running ? `Asking... ${d.progress.done} of ${d.progress.total || "?"}` : "Check this AI", {kind: "primary", ic: "check",
        disabled: !ready || d.running, title: ready ? `About a minute, and ${d.questions} short questions to your AI provider` : ai.problem || "Choose an AI above first", onClick: async (e) => {
          e.currentTarget.disabled = true;
          try { await api("/api/ai/eval", {}); toast("Checking. This takes about a minute."); settingsPage(); } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
        }})),
      last ? h("div", {class: "stack", style: "gap:8px"},
        callout(last.verdict === "good" ? "info" : last.verdict === "weak" ? "danger" : "warning", null, last.advice),
        h("dl", {class: "kv"}, h("dt", {}, "Right pick"), h("dd", {}, String(c.correct)), h("dt", {}, "Right: none there"), h("dd", {}, String(c.right_none)),
          h("dt", {}, "Missed it (harmless)"), h("dd", {}, String(c.missed)),
          h("dt", {}, "Wrong pick"), h("dd", {}, `${c.wrong}${c.wrong ? ` (${last.wrong_reached} would have reached you, ${last.wrong_stopped} stopped by the checks)` : ""}`),
          h("dt", {}, "Unreadable"), h("dd", {}, String(c.unreadable)), h("dt", {}, "Could not ask"), h("dd", {}, String(c.error))),
        disclose("Every question", h("div", {class: "stack", style: "gap:6px"}, last.cases.map((r) => h("div", {class: "row", style: "gap:8px"},
          badge(...(OUTCOME[r.outcome] || [r.outcome, "neutral"])), h("span", {}, r.intent),
          r.picked ? h("span", {class: "meta"}, `picked "${r.picked}"`) : null, r.wanted ? h("span", {class: "meta"}, `wanted "${r.wanted.join('" or "')}"`) : h("span", {class: "meta"}, "right answer: none")))))) : null,
      d.history.length > 1 ? disclose("Earlier checks", h("div", {class: "stack", style: "gap:4px"}, d.history.slice(1).map((r) =>
        h("div", {class: "row", style: "gap:8px"}, badge(...(VERDICT[r.verdict] || [r.verdict, "neutral"])), h("span", {}, `${r.label}: ${Math.round(r.score * 100)}% right`), h("span", {class: "meta"}, when(r.at)))))) : null)});
}
