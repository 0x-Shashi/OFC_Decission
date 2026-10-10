// Approving a release: who approved it, when, and on which results (shown on the release card, kept in the
// certification pack). There is no log-in, so the approver types a name; it is remembered on this computer.
import {api, button, callout, drawer, field, h, icon, input, plural, remember, toast, when} from "./ui.js";
import {can, state} from "./state.js";

const counts = (s) => `${s.passed} passed, ${s.failed} failed, ${s.not_run} not run`;

export function approvalBlock(a, redraw) {
  if (!a) return null;
  const last = a.last;
  const rel = a.release;
  let badge, text, actions = [];
  if (a.status === "approved") {
    const changed = a.changes && a.changes.changed;
    badge = h("span", {class: `badge tone-${changed ? "warning" : "success"}`}, icon(changed ? "attention" : "check"), changed ? "Approved, results changed" : "Approved");
    text = [h("strong", {}, `${last.by}${last.title ? ` (${last.title})` : ""}`), ` approved ${rel} ${when(last.at).toLowerCase()} on ${counts(last.results)}.`,
      last.comment ? h("div", {class: "meta"}, `"${last.comment}"`) : null,
      changed ? h("div", {class: "meta", style: "color:var(--warning)"},
        `Tests were run again since: ${plural(a.changes.tests, "test")} changed${a.changes.newly_failing ? `, ${a.changes.newly_failing} now failing` : ""}. Approve it again to cover the new results.`) : null];
    if (changed && can("approver")) actions.push(button("Approve again", {size: "sm", kind: "primary", ic: "check", onClick: () => openApprove(a, redraw)}));
    if (can("approver")) actions.push(button("Withdraw", {size: "sm", kind: "ghost", onClick: () => openWithdraw(a, redraw)}));
  } else if (a.status === "withdrawn") {
    badge = h("span", {class: "badge tone-neutral"}, icon("minus"), "Approval withdrawn");
    text = [h("strong", {}, last.by), ` withdrew the approval ${when(last.at).toLowerCase()}: "${last.comment}"`];
    if (can("approver")) actions.push(button("Approve again", {size: "sm", kind: "primary", ic: "check", onClick: () => openApprove(a, redraw)}));
  } else {
    badge = h("span", {class: "badge tone-neutral"}, icon("minus"), "Not approved");
    text = `Nobody has approved ${rel} yet. ${a.summary.total ? counts(a.summary) + "." : "There are no tests."}`;
    if (can("approver")) actions.push(button(`Approve ${rel}…`, {size: "sm", kind: "primary", ic: "check", disabled: !a.summary.total,
      title: a.summary.total ? "Record that you approve this release" : "There are no tests to approve", onClick: () => openApprove(a, redraw)}));
  }
  actions.push(button("History", {size: "sm", kind: "ghost", onClick: () => openHistory(rel)}));
  return h("div", {class: "stack", style: "gap:8px;border-top:1px solid var(--border);padding-top:12px"},
    h("div", {class: "caption"}, "APPROVAL"),
    h("div", {class: "row", style: "flex-wrap:nowrap;gap:12px;align-items:flex-start"}, badge, h("div", {class: "grow"}, text)),
    h("div", {class: "row"}, actions));
}

function openApprove(a, redraw) {
  const rel = a.release, s = a.summary;
  const saved = remember("approver") || {};
  const me = state.auth?.enabled ? state.auth.user : null; // signed in: the approver is who is signed in
  const name = input({value: me ? me.full_name : saved.name || "", placeholder: "Your full name", maxlength: "80", "aria-label": "Your name", readonly: me ? "readonly" : null});
  const title = input({value: me ? me.title || saved.title || "" : saved.title || "", placeholder: "For example Test manager", maxlength: "80", "aria-label": "Your role"});
  const open = s.failed + s.not_run;
  const comment = h("textarea", {class: "input", rows: "3", maxlength: "1000", "aria-label": "Comment",
    placeholder: open ? "Why is the release approved with tests failed or not run?" : "Optional", style: "width:100%;font:inherit"});
  const sure = h("input", {type: "checkbox"});
  const error = h("div", {class: "meta", role: "alert", style: "color:var(--danger);min-height:18px"});
  drawer({
    title: `Approve release ${rel}`,
    sub: `${counts(s)} of ${plural(s.total, "test")}`,
    body: () => [h("div", {class: "stack"},
      callout("info", "What this does.", "It records your name, the time and the results as they are now. They go into the certification pack. Quartermaster has no log-in, so the name is the one you type here."),
      open ? callout("warning", `${s.failed} failed and ${s.not_run} have not run.`, "You can still approve, but you must say you know and write why.") : null,
      h("div", {class: "fields"}, field("Your name", name, me ? "You are signed in, so this is you." : "", "approve-name"), field("Your role", title, "", "approve-title"),
        h("div", {class: "wide"}, field("Comment", comment, "", "approve-comment"))),
      open ? h("label", {class: "switch"}, sure, `I know ${s.failed} test(s) failed and ${s.not_run} have not run, and I approve ${rel} anyway`) : null,
      error)],
    foot: (close) => [h("span", {class: "grow"}), button("Cancel", {onClick: close}),
      button(`Approve ${rel}`, {kind: "primary", ic: "check", onClick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        try {
          await api("/api/approvals", {action: "approve", release: rel, name: name.value, title: title.value,
            comment: comment.value, acknowledged: sure.checked});
          remember("approver", {name: name.value.trim(), title: title.value.trim()});
          close();
          toast(`Release ${rel} approved. Download the certification pack to have it in the document.`);
          redraw();
        } catch (err) { error.textContent = err.message; btn.disabled = false; }
      }})],
  });
}

function openWithdraw(a, redraw) {
  const rel = a.release;
  const saved = remember("approver") || {};
  const me = state.auth?.enabled ? state.auth.user : null;
  const name = input({value: me ? me.full_name : saved.name || "", placeholder: "Your full name", maxlength: "80", "aria-label": "Your name", readonly: me ? "readonly" : null});
  const reason = h("textarea", {class: "input", rows: "3", maxlength: "1000", "aria-label": "Why", style: "width:100%;font:inherit"});
  const error = h("div", {class: "meta", role: "alert", style: "color:var(--danger);min-height:18px"});
  drawer({
    title: `Withdraw the approval of ${rel}`,
    sub: `Approved by ${a.last.by} ${when(a.last.at).toLowerCase()}`,
    body: () => [h("div", {class: "stack"},
      callout("warning", "The approval stays in the history.", "Withdrawing adds a record; nothing is deleted."),
      field("Your name", name, "", "withdraw-name"), field("Why is it withdrawn?", reason, "", "withdraw-why"), error)],
    foot: (close) => [h("span", {class: "grow"}), button("Cancel", {onClick: close}),
      button("Withdraw the approval", {kind: "danger", onClick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        try {
          await api("/api/approvals", {action: "withdraw", release: rel, name: name.value, comment: reason.value});
          remember("approver", {...saved, name: name.value.trim()});
          close();
          toast(`The approval of ${rel} is withdrawn.`);
          redraw();
        } catch (err) { error.textContent = err.message; btn.disabled = false; }
      }})],
  });
}

async function openHistory(rel) {
  let data;
  try { data = await api(`/api/approvals?release=${encodeURIComponent(rel)}`); } catch (err) { toast(err.message); return; }
  drawer({
    title: `Approvals of ${rel}`,
    sub: "Newest first. Records are only ever added, never changed.",
    body: () => data.history.length
      ? h("div", {class: "stack"}, data.history.map((r) => h("div", {class: "stack", style: "gap:2px;border-bottom:1px solid var(--border);padding-bottom:8px"},
        h("div", {class: "row", style: "gap:8px"},
          h("span", {class: `badge tone-${r.action === "approved" ? "success" : "neutral"}`}, r.action === "approved" ? "Approved" : "Withdrawn"),
          h("strong", {}, r.by), r.title ? h("span", {class: "meta"}, r.title) : null),
        h("div", {class: "meta"}, `${when(r.at)} · ${counts(r.results)} · computer user ${r.computer_user}`),
        r.comment ? h("div", {}, `"${r.comment}"`) : null,
        r.acknowledged_open_items ? h("div", {class: "meta"}, "Approved knowing tests had failed or not run.") : null)))
      : h("p", {class: "meta"}, "No approvals yet."),
    foot: (close) => [h("span", {class: "grow"}), button("Close", {onClick: close})],
  });
}
