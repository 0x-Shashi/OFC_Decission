// Needs attention: the failure and maintenance workspace.
import {api, badge, button, callout, chips, clock, disclose, drawer, emptyState, h, icon, toast, when} from "./ui.js";
import {openRunDrawer, openViewer} from "./components.js";
import {openTicket, ticketChips} from "./page-tickets.js";
import {loadCommon, runLink, state, testLink} from "./state.js";
import {show} from "./app.js";

const ORDER = ["assertion", "missing_element", "service_call", "timeout", "authentication", "test_data", "failure", "could_not_run", "cleanup", "ui_change", "unreadable"];
const LOOK = {
  assertion: {ic: "x", tone: "danger", short: "Check did not match"},
  missing_element: {ic: "target", tone: "danger", short: "Item not found"},
  service_call: {ic: "server", tone: "danger", short: "Service call failed"},
  timeout: {ic: "clock", tone: "danger", short: "Screen did not respond"},
  authentication: {ic: "lock", tone: "danger", short: "Sign-in problem"},
  test_data: {ic: "file", tone: "warning", short: "Test data not ready"},
  failure: {ic: "x", tone: "danger", short: "Failed"},
  could_not_run: {ic: "attention", tone: "danger", short: "Could not run"},
  cleanup: {ic: "attention", tone: "warning", short: "Cleanup did not finish"},
  ui_change: {ic: "wrench", tone: "warning", short: "Oracle screen changed"},
  unreadable: {ic: "file", tone: "danger", short: "Unreadable file"},
};
let current = "all";

export async function attentionPage() {
  await loadCommon();
  if (state.page !== "attention") return;
  const a = state.attention;
  const list = h("div", {class: "stack", style: "gap:12px"});
  const draw = () => {
    const items = a.items.filter((i) => current === "all" || i.category === current)
      .sort((x, y) => ORDER.indexOf(x.category) - ORDER.indexOf(y.category));
    list.replaceChildren(...items.map(item));
  };
  draw();
  show([{label: "Needs attention"}],
    h("div", {class: "page-head"},
      h("div", {}, h("h1", {}, "Needs attention"),
        h("p", {class: "lead"}, "Failures to look into, Oracle screen changes to accept, and anything that stopped a run.")),
      h("div", {class: "row", style: "gap:8px"},
        h("span", {class: "meta"}, `Last checked ${clock(a.checked_at)}${a.dismissed ? ` · ${a.dismissed} dismissed` : ""}`),
        a.count ? button("Dismiss all shown", {size: "sm", ic: "x", onClick: () => {
          const keys = a.items.filter((i) => current === "all" || i.category === current).map((i) => i.key);
          if (confirm(`Dismiss ${keys.length === 1 ? "this item" : `these ${keys.length} items`}? Each comes back only if it fails again on a later run.`)) dismiss(keys, "Dismissed.");
        }}) : null)),
    a.count ? [
      h("div", {class: "toolbar"}, chips([["all", "All", a.count],
        ...ORDER.filter((k) => a.counts[k]).map((k) => [k, a.categories[k], a.counts[k]])], current, (v) => { current = v; draw(); }, "Kind")),
      list,
    ] : h("div", {class: "card"}, emptyState({ic: "check", tone: "success", title: "All clear",
      text: `No tests currently need attention. Last checked ${clock(a.checked_at)}.`,
      actions: button("Run all tests", {size: "sm", ic: "runs", disabled: !state.status.ready, onClick: () => openRunDrawer(".")})})));
}

function item(i) {
  const look = LOOK[i.category] || LOOK.failure;
  const head = h("div", {class: "row", style: "align-items:flex-start;flex-wrap:nowrap;gap:12px"},
    h("span", {class: `icon-tile tone-${look.tone}`}, icon(look.ic)),
    h("div", {class: "grow"},
      h("div", {class: "row", style: "gap:8px"},
        i.file && i.category !== "unreadable" && i.test_id ? h("a", {href: testLink(i.file), style: "color:inherit"}, h("h3", {}, i.title)) : h("h3", {}, i.title),
        badge(look.short, look.tone)),
      h("div", {class: "meta"}, [i.module, i.process].filter(Boolean).join(" › ") || (i.file ? i.file : ""))),
    h("div", {class: "row"}, actions(i)));

  let body = null;
  if (i.category === "ui_change") {
    body = h("div", {class: "stack", style: "gap:8px"},
      h("div", {}, `Step ${i.step}: ${i.intent}`),
      h("div", {class: "compare"},
        h("div", {}, h("span", {class: "caption"}, "Written to find"), i.old_text),
        h("div", {}, h("span", {class: "caption"}, "Found last time by"), i.new_text)),
      h("p", {class: "meta"}, "The test passed by trying its second way. Accepting makes that the first way, so it keeps passing after the quarterly update. A copy of the old file is kept."),
      i.shared ? h("p", {class: "meta"}, `This step is in the shared steps '${i.shared}', so accepting changes every test that uses it.`) : null);
  } else if (i.category === "cleanup") {
    const steps = i.cleanup_steps || [];
    body = h("div", {class: "stack", style: "gap:8px"},
      callout("warning", "Records from this test may still be on the pod.",
        `The test ${i.result === "passed" ? "passed" : i.result === "failed" ? "failed" : "finished"}, but its cleanup ${i.cleanup_status === "failed" ? "did not work" : "only partly worked"}. Leftovers can make the next run fail (for example a name that already exists). Remove them by hand on the pod, or fix the cleanup step and run the test again.`),
      steps.length ? h("ul", {style: "margin:0;padding-left:20px"}, steps.map((x) => h("li", {}, `Cleanup step ${x.number} (${x.intent}): ${x.error}`)))
        : h("div", {}, i.error),
      i.detail ? disclose("Technical details", h("pre", {class: "block"}, i.detail)) : null);
  } else if (i.category === "unreadable") {
    body = h("pre", {class: "block"}, i.error);
  } else if (i.step) {
    body = h("div", {class: "stack", style: "gap:8px"},
      h("div", {}, h("strong", {}, `Failed at step ${i.step}: `), i.intent),
      h("div", {}, i.error),
      i.category === "test_data" ? callout("info", "The test data was not ready, which is not a broken release.",
        "The steps did not run. Fix the data (add the value for this pod to the data set, or make the pod ready), then run the test again.") : null,
      i.compare ? h("div", {class: "compare"},
        h("div", {}, h("span", {class: "caption"}, "Expected"), i.compare.expected || "(empty)"),
        h("div", {}, h("span", {class: "caption"}, "Observed"), i.compare.observed || "(empty)")) : null,
      i.suggestion ? callout("info", "Suggested fix.",
        h("span", {}, `${i.suggestion.source === "ai" ? "The AI thinks" : "A similar name on the screen suggests"} the step should find `,
          h("strong", {}, i.suggestion.new_text), ` instead of ${i.suggestion.old_text}.`,
          i.suggestion.why ? ` ${i.suggestion.why}${/[.!?]$/.test(i.suggestion.why) ? "" : "."}` : "",
          " It is only a suggestion: the test is unchanged until you accept it, and the old ways stay as a fallback. Run the test again afterwards to check it.",
          i.suggestion.shared ? ` This step is in the shared steps '${i.suggestion.shared}': accepting changes every test that uses it.` : "")) : null,
      i.cause ? callout(i.cause.sr ? "warning" : "info", `Likely cause: ${i.cause.title}.`, i.cause.advice) : null,
      i.detail && i.detail !== i.error ? disclose("Technical details", h("pre", {class: "block"}, i.detail)) : null);
  } else {
    body = h("div", {class: "stack", style: "gap:8px"}, h("div", {}, i.error),
      i.category === "authentication" ? h("p", {class: "meta"}, "Check the user name and password on this computer (Settings), then run it again.") : null,
      i.detail && i.detail !== i.error ? disclose("Messages from the run", h("pre", {class: "block"}, i.detail)) : null);
  }

  const facts = i.category === "unreadable" ? null : h("div", {class: "facts", style: "margin-top:4px"},
    i.test_id ? fact("Last successful release", i.last_good_release ? h("span", {class: "release"}, i.last_good_release)
      : i.last_good_at ? "Not recorded" : "Never passed") : null,
    fact("Current release", i.current_release ? h("span", {class: "release"}, i.current_release) : h("a", {href: "#/settings"}, "Not set")),
    i.run_release !== undefined ? fact("Release of this run", i.run_release ? h("span", {class: "release"}, i.run_release) : "Not recorded") : null,
    i.at ? fact(i.category === "ui_change" ? "Found" : "Happened", when(i.at)) : null);

  const evidence = (i.picture_url || i.document_url) ? h("div", {class: "row", style: "gap:12px"},
    i.picture_url ? h("img", {class: "thumb", src: i.picture_url, alt: `Screen when step ${i.step} failed`, style: "width:120px;height:75px",
      onclick: () => openViewer([{src: i.picture_url, caption: `Step ${i.step}: ${i.intent}`, sub: i.title}])}) : null,
    i.document_url ? button("Evidence document", {size: "sm", ic: "file", href: i.document_url}) : null) : null;

  const tickets = (i.tickets || []).length ? h("div", {class: "row", style: "gap:8px"}, h("span", {class: "meta"}, "Tickets"), ticketChips(i.tickets)) : null;
  return h("article", {class: "card", "aria-label": `${look.short}: ${i.title}`},
    h("div", {class: "card-body stack", style: "gap:12px"}, head, body, tickets, evidence, facts));
}

async function dismiss(keys, what) {
  try {
    await api("/api/attention/dismiss", {keys});
    toast(`${what} If it fails again on a later run, it comes back.`);
    attentionPage();
  } catch (err) { toast(err.message); }
}

function actions(i) {
  const out = [];
  if (i.category === "ui_change") {
    out.push(button("Accept update", {kind: "primary", size: "sm", ic: "check", onClick: async (e) => {
      e.currentTarget.disabled = true;
      try {
        await api("/api/test/accept-update", {file: i.fix_file || i.file, step_index: i.fix_step ?? i.step_index, new: i.new});
        toast("Test updated. A copy of the old file was kept.");
        attentionPage();
      } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
    }}));
  }
  if (i.suggestion) {
    out.push(button("Use the suggestion", {kind: "primary", size: "sm", ic: "check", onClick: async (e) => {
      e.currentTarget.disabled = true;
      try {
        await api("/api/test/accept-update", {file: i.suggestion.fix_file || i.file, step_index: i.suggestion.fix_step ?? i.suggestion.step_index, new: i.suggestion.new, add: true});
        toast("Test updated. A copy of the old file was kept. Run the test again to check it.");
        attentionPage();
      } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
    }}));
  }
  if (i.cause?.sr) out.push(button("Draft SR", {size: "sm", ic: "file", title: "A draft service request for Oracle, to copy into My Oracle Support", onClick: () => openSr(i)}));
  if (i.test_id && i.step && i.run_id && i.category !== "ui_change" && i.category !== "cleanup") {
    out.push(button((i.tickets || []).length ? `Ticket (${i.tickets.length})` : "Ticket", {size: "sm", ic: "file", title: "Write a ticket for this failure, and link the ticket number",
      onClick: () => openTicket(i, () => attentionPage())}));
  }
  if (i.run_id) out.push(button("See the run", {size: "sm", href: runLink(i.run_id)}));
  if (i.category === "authentication") out.push(button("Settings", {size: "sm", href: "#/settings"}));
  if (i.category === "test_data") out.push(button("Open test data", {size: "sm", href: "#/data"}));
  if (i.file && i.category !== "ui_change" && i.category !== "unreadable") {
    out.push(button("Run again", {size: "sm", kind: i.category === "ui_change" ? "" : "primary", ic: "runs", disabled: !state.status.ready, onClick: () => openRunDrawer(i.file)}));
  }
  if (i.category === "unreadable") out.push(button("Open the test", {size: "sm", href: testLink(i.file)}));
  out.push(button("Dismiss", {size: "sm", kind: "ghost", ic: "x", title: "Remove it from this list; it comes back only if it fails again on a later run",
    onClick: () => dismiss([i.key], "Dismissed.")}));
  return out;
}

// A draft Oracle service request: Quartermaster only writes the text; the person checks it and pastes it.
async function openSr(i) {
  let draft;
  try { draft = await api(`/api/attention/sr?run=${encodeURIComponent(i.run_id)}&test=${encodeURIComponent(i.test_id)}`); } catch (err) { toast(err.message); return; }
  const text = h("textarea", {class: "input", rows: "22", "aria-label": "Service request text", style: "width:100%;font:inherit;font-size:13px;line-height:1.45"});
  text.value = draft.text;
  drawer({
    title: "Draft service request",
    sub: i.title,
    body: () => [
      callout("info", "Quartermaster does not send this.", "Read it, fill in the business impact, remove anything private, then paste it into a new service request in My Oracle Support. Attach the evidence document."),
      text,
    ],
    foot: (close) => [
      i.document_url ? button("Evidence document", {ic: "file", href: i.document_url}) : null,
      h("span", {class: "grow"}),
      button("Close", {onClick: close}),
      button("Copy", {kind: "primary", ic: "check", onClick: async () => {
        try { await navigator.clipboard.writeText(text.value); toast("Copied. Paste it into My Oracle Support."); }
        catch { text.select(); toast("Select all the text and copy it (Ctrl+C)."); }
      }}),
    ],
  });
}

function fact(k, v) {
  return h("div", {}, h("div", {class: "k"}, k), h("div", {class: "v"}, v));
}

