// Ticket links: the tickets that track a failing test, a ticket text to paste into the tracker, and where the tracker is.
import {api, button, callout, card, drawer, field, h, input, toast} from "./ui.js";
import {settingsPage} from "./page-settings.js";

// The tickets as small links (a ticket without a link is plain text). Only http and https addresses become links.
export function ticketChips(tickets) {
  return h("span", {class: "row", style: "gap:6px", "data-tickets": ""}, tickets.map((t) => {
    const safe = typeof t.url === "string" && /^https?:\/\//i.test(t.url);
    return safe ? h("a", {class: "tag", href: t.url, target: "_blank", rel: "noopener noreferrer", title: t.url}, t.ref) : h("span", {class: "tag"}, t.ref);
  }));
}

// Create a ticket from a failure, and link the ticket number that comes back. `done` runs after a change.
export async function openTicket(item, done) {
  let draft;
  try { draft = await api(`/api/tickets/draft?run=${encodeURIComponent(item.run_id)}&test=${encodeURIComponent(item.test_id)}`); }
  catch (err) { toast(err.message); return; }
  const title = input({value: draft.title, "aria-label": "Ticket title"});
  const text = h("textarea", {class: "input", rows: "14", "aria-label": "Ticket description", style: "width:100%;font:inherit;font-size:13px;line-height:1.45"});
  text.value = draft.description;
  const ref = input({placeholder: "For example PROJ-123, or paste its link", "aria-label": "Ticket number or link", autocomplete: "off"});
  const error = h("div", {class: "meta", role: "alert", style: "color:var(--danger);min-height:18px"});
  const list = h("div", {});
  let tickets = draft.tickets;
  const drawList = () => list.replaceChildren(tickets.length ? h("div", {class: "stack", style: "gap:6px"},
    tickets.map((t) => h("div", {class: "row", style: "gap:8px"}, ticketChips([t]), h("span", {class: "meta"}, t.who ? `linked by ${t.who}` : ""),
      button("Remove", {size: "sm", kind: "ghost", onClick: async () => {
        try { tickets = (await api("/api/tickets", {action: "remove", test_id: item.test_id, ref: t.ref})).tickets; drawList(); done(); } catch (err) { error.textContent = err.message; }
      }})))) : h("div", {class: "hint"}, "No ticket linked yet."));
  drawList();
  const link = async () => {
    error.textContent = "";
    try {
      tickets = (await api("/api/tickets", {action: "add", test_id: item.test_id, ref: ref.value, run_id: item.run_id})).tickets;
      ref.value = "";
      drawList();
      toast("Linked.");
      done();
    } catch (err) { error.textContent = err.message; }
  };
  ref.onkeydown = (e) => { if (e.key === "Enter") link(); };
  drawer({
    title: "Ticket for this failure",
    sub: item.title,
    body: () => [h("div", {class: "stack"},
      callout("info", "Nothing is sent anywhere.", "Quartermaster does not talk to your tracker. Copy this text into a new ticket, or open the tracker's page with it filled in, then link the ticket number here so nobody raises it twice."),
      field("Title", title, "", "tk-title"),
      field("Description", text, "Check it before sharing: it may contain wording from the pod. Attach the run's evidence document.", "tk-text"),
      h("div", {class: "row"},
        button("Copy title and description", {ic: "copy", onClick: async () => {
          try { await navigator.clipboard.writeText(`${title.value}\n\n${text.value}`); toast("Copied. Paste it into a new ticket."); } catch { text.select(); toast("Select the text and copy it (Ctrl+C)."); }
        }}),
        draft.create_url ? button(`Open ${draft.tracker || "the tracker"} with it`, {href: draft.create_url, attrs: {target: "_blank", rel: "noopener noreferrer"}})
          : h("span", {class: "hint"}, "To open your tracker from here, an administrator sets its address in Settings, Ticket tracker.")),
      h("div", {}, h("div", {class: "label"}, "Linked tickets"), list),
      field("Link a ticket", ref, "The number it was given in the tracker, or its link.", "tk-ref"),
      error)],
    foot: (close) => [h("span", {class: "grow"}), button("Close", {onClick: close}), button("Link ticket", {kind: "primary", onClick: link})],
  });
}

// Where the tracker is (Settings, General). Administrators only.
export async function ticketSettingsCard() {
  const d = await api("/api/tickets").catch(() => null);
  if (!d) return null;
  const s = d.settings;
  const name = input({value: s.name, maxlength: "40", placeholder: "For example Jira", "aria-label": "Tracker name"});
  const link = input({value: s.link_template, maxlength: "400", placeholder: "https://example.atlassian.net/browse/{key}", "aria-label": "Ticket address"});
  const create = input({value: s.create_template, maxlength: "400", "aria-label": "New ticket address",
    placeholder: "https://example.atlassian.net/secure/CreateIssueDetails!init.jspa?summary={title}&description={description}"});
  const error = h("div", {class: "meta", role: "alert", style: "color:var(--danger);min-height:18px"});
  return card({title: "Ticket tracker", sub: s.link_template || s.create_template ? s.name || "Set" : "Not set",
    body: h("div", {class: "stack"},
      h("p", {class: "hint", style: "margin:0"}, "Optional. Failures get a ticket text to paste into your tracker (Jira, ServiceNow, Azure DevOps, anything), and the ticket numbers you link are shown on the failure, on the test and in the certification pack. Quartermaster never logs in to the tracker and sends nothing to it."),
      field("Name", name, "", "tt-name"),
      field("Ticket address", link, "Where one ticket lives, with {key} for its number. Turns PROJ-123 into a link.", "tt-link"),
      field("New ticket address", create, "Optional. The tracker's page for a new ticket, with {title} and {description} where they go. Not every tracker has one.", "tt-create"),
      error,
      h("div", {class: "row"}, button("Save", {kind: "primary", onClick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        error.textContent = "";
        try { await api("/api/tickets/settings", {name: name.value, link_template: link.value, create_template: create.value}); toast("Saved."); settingsPage(); }
        catch (err) { error.textContent = err.message; btn.disabled = false; }
      }})))});
}
