// Settings, Notifications: tell people when a run fails (a webhook for Slack or Teams, and/or e-mail).
// The webhook address and the mail password are saved encrypted and never come back to this page.
import {api, button, callout, card, field, h, icon, input, segmented, toast, when} from "./ui.js";

export async function notificationsTab(redraw) {
  const n = await api("/api/notifications");

  const say = (el, ok, text) => { el.textContent = text; el.style.color = ok ? "var(--success)" : "var(--danger)"; };
  const post = async (body) => api("/api/notifications", body);

  // ---- when to send
  let whenRuns = n.when, whatResult = n.what;
  const details = h("input", {type: "checkbox", checked: n.details});
  const whenCard = card({title: "When to send", sub: `For ${n.client || "the client in use"}. Every client has its own settings.`,
    body: h("div", {class: "stack"},
      h("div", {}, h("div", {class: "label"}, "Which runs"),
        segmented([["scheduled", "Scheduled runs only"], ["all", "Every run"]], whenRuns, (v) => { whenRuns = v; }, "Which runs"),
        h("div", {class: "hint"}, "Scheduled runs are the ones nobody watches. A run you start yourself is on your screen already. A run you stop never sends anything.")),
      h("div", {}, h("div", {class: "label"}, "What to send"),
        segmented([["failures", "Only when something failed"], ["every", "After every finished run"]], whatResult, (v) => { whatResult = v; }, "What to send")),
      h("div", {}, h("label", {class: "switch"}, details, "Include what went wrong"),
        h("div", {class: "hint"}, "Off: a message names the failed tests, the step and the kind of problem. On: it also says what the check expected and found. That text comes from the pod and can hold personal data, so leave it off when messages go outside your team. A message never holds passwords or test data.")),
      h("div", {class: "row"}, button("Save", {kind: "primary", onClick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        try { await post({when: whenRuns, what: whatResult, details: details.checked}); toast("Saved."); } catch (err) { toast(err.message); }
        btn.disabled = false;
      }})))});

  // ---- webhook
  const hook = n.webhook;
  const hookOn = h("input", {type: "checkbox", checked: hook.enabled});
  const kind = h("select", {class: "input", "aria-label": "Kind of webhook"},
    [["slack", "Slack"], ["teams", "Microsoft Teams"], ["json", "Other (sends JSON)"]].map(([v, l]) => h("option", {value: v, selected: v === hook.kind}, l)));
  const url = input({type: "password", autocomplete: "off", "aria-label": "Webhook address",
    placeholder: hook.url_set ? `Saved (hidden), on ${hook.host}. Paste a new address to change it` : "https://…"});
  const hookResult = h("div", {class: "meta", role: "status", "aria-live": "polite"});
  const saveHook = async () => {
    const body = {enabled: hookOn.checked, kind: kind.value};
    if (url.value.trim()) body.url = url.value.trim();
    await post({webhook: body});
    url.value = "";
  };
  const hookCard = card({title: "Slack, Teams or another service", sub: "Posts a short message to a channel",
    body: h("div", {class: "stack"},
      h("label", {class: "switch"}, hookOn, "Send to this webhook"),
      h("div", {class: "fields"}, field("Service", kind, "", "hook-kind"),
        h("div", {class: "wide"}, field("Webhook address", url,
          "Slack: Apps, Incoming Webhooks. Teams: Workflows, \"When a Teams webhook request is received\". Anyone with this address can post to the channel, so it is saved encrypted and never shown again.", "hook-url"))),
      h("div", {class: "row"},
        button("Save", {kind: "primary", onClick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          try { await saveHook(); toast("Saved."); redraw(); } catch (err) { say(hookResult, false, err.message); btn.disabled = false; }
        }}),
        button("Send a test message", {ic: "refresh", onClick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          say(hookResult, true, "Sending…");
          try { await saveHook(); const r = await api("/api/notifications/test", {channel: "webhook"}); say(hookResult, r.ok, r.message); }
          catch (err) { say(hookResult, false, err.message); }
          btn.disabled = false;
        }}),
        hook.url_set ? button("Remove the address", {kind: "ghost", onClick: async () => {
          try { await post({webhook: {enabled: false, url: ""}}); toast("Removed."); redraw(); } catch (err) { toast(err.message); }
        }}) : null),
      hookResult)});

  // ---- e-mail
  const mail = n.email;
  const mailOn = h("input", {type: "checkbox", checked: mail.enabled});
  const host = input({value: mail.host, placeholder: "smtp.example.com", maxlength: "253", "aria-label": "Mail server"});
  const port = input({value: String(mail.port), maxlength: "5", "aria-label": "Port", style: "width:90px"});
  const security = h("select", {class: "input", "aria-label": "Connection security"},
    [["starttls", "STARTTLS (usual, port 587)"], ["ssl", "SSL (port 465)"], ["none", "None (only inside your network)"]]
      .map(([v, l]) => h("option", {value: v, selected: v === mail.security}, l)));
  const user = input({value: mail.username, autocomplete: "off", placeholder: "Leave empty if the server needs none", maxlength: "200", "aria-label": "User name"});
  const pass = input({type: "password", autocomplete: "off", "aria-label": "Mail password",
    placeholder: mail.password_set ? "Saved (hidden). Type to change it" : ""});
  const sender = input({value: mail.sender, placeholder: "quartermaster@yourcompany.com", maxlength: "200", "aria-label": "Sent from"});
  const to = input({value: mail.to.join(", "), placeholder: "name@yourcompany.com, other@yourcompany.com", maxlength: "1000", "aria-label": "Send to"});
  const mailResult = h("div", {class: "meta", role: "status", "aria-live": "polite"});
  const saveMail = async () => {
    const body = {enabled: mailOn.checked, host: host.value, port: port.value, security: security.value,
      username: user.value, sender: sender.value, to: to.value};
    if (pass.value) body.password = pass.value;
    await post({email: body});
    pass.value = "";
  };
  const mailCard = card({title: "E-mail", sub: "Sends a short message from your own mail server",
    body: h("div", {class: "stack"},
      h("label", {class: "switch"}, mailOn, "Send e-mail"),
      h("div", {class: "fields"},
        field("Mail server", host, "", "mail-host"), field("Port", port, "", "mail-port"),
        h("div", {class: "wide"}, field("Connection", security, "", "mail-security")),
        field("User name", user, "", "mail-user"), field("Password", pass, "Saved encrypted, never shown again.", "mail-pass"),
        field("Sent from", sender, "", "mail-from"), field("Send to", to, "One or more addresses, separated by commas.", "mail-to")),
      h("div", {class: "row"},
        button("Save", {kind: "primary", onClick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          try { await saveMail(); toast("Saved."); redraw(); } catch (err) { say(mailResult, false, err.message); btn.disabled = false; }
        }}),
        button("Send a test message", {ic: "refresh", onClick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          say(mailResult, true, "Sending…");
          try { await saveMail(); const r = await api("/api/notifications/test", {channel: "email"}); say(mailResult, r.ok, r.message); }
          catch (err) { say(mailResult, false, err.message); }
          btn.disabled = false;
        }})),
      mailResult)});

  // ---- what was sent
  const recent = card({title: "Recent messages", sub: "Every attempt, so a channel that stopped working is noticed",
    body: n.log.length ? h("div", {class: "stack", style: "gap:8px"}, n.log.map((e) => h("div", {class: "row", style: "gap:8px;flex-wrap:nowrap;align-items:flex-start"},
      h("span", {class: `icon-tile tone-${e.ok ? "success" : "danger"}`}, icon(e.ok ? "check" : "x")),
      h("div", {class: "grow"}, h("div", {}, `${e.channel === "email" ? "E-mail" : "Webhook"} · ${e.run === "test" ? "test message" : `run ${e.run}`}`),
        h("div", {class: "meta"}, `${when(e.at)} · ${e.message}`)))))
      : h("p", {class: "meta", style: "margin:0"}, "Nothing has been sent yet.")});

  const quiet = !n.webhook.enabled && !n.email.enabled
    ? callout("info", "Nothing is switched on.", "Turn on a webhook or e-mail below. Until then, a failed run is only seen when someone opens Quartermaster.") : null;
  return h("div", {class: "stack"}, quiet,
    h("div", {class: "grid g-2"}, h("div", {class: "stack"}, whenCard, recent), h("div", {class: "stack"}, hookCard, mailCard)));
}
