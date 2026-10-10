// Settings, Users & sign-in (administrators): turn sign-in on, add people, give them roles.
import {api, badge, button, callout, card, drawer, field, h, icon, input, toast, when} from "./ui.js";
import {loadAuth, state} from "./state.js";
import {rolesText} from "./page-login.js";
import {route} from "./app.js";

const ROLE_INFO = [
  ["admin", "Administrator", "Everything: settings, clients and pods, users, backups, notifications."],
  ["tester", "Tester", "Record, run, import and schedule tests, accept screen changes, switch the pod in use."],
  ["approver", "Approver", "Approve or withdraw the approval of a release. Keep this apart from the testers who ran the tests."],
];

export async function usersTab(redraw) {
  const d = await api("/api/users");
  if (!d.enabled) return turnOnCard();
  const me = state.auth?.user;
  const g = await api("/api/auth/google");
  const sso = await api("/api/auth/sso");
  const googleOn = g.enabled || sso.enabled;  // a person added by e-mail address may sign in without a password
  const how = [g.enabled ? "Google" : "", sso.enabled ? sso.label : ""].filter(Boolean).join(" or ");
  const roleChecks = (have) => ROLE_INFO.map(([id, label, help]) => {
    const box = h("input", {type: "checkbox", checked: have.includes(id), "data-role": id});
    return h("div", {}, h("label", {class: "switch"}, box, label), h("div", {class: "hint"}, help));
  });
  const picked = (host) => [...host.querySelectorAll("input[data-role]")].filter((b) => b.checked).map((b) => b.dataset.role);

  const addUser = () => {
    const name = input({maxlength: "80", placeholder: "Full name", "aria-label": "Full name"});
    const user = input({maxlength: "32", placeholder: "For example jane.doe", autocomplete: "off", "aria-label": "User name"});
    const title = input({maxlength: "80", placeholder: "For example Test manager", "aria-label": "Role or job title"});
    const mail = input({maxlength: "120", type: "email", placeholder: "For example jane.doe@gmail.com", autocomplete: "off", "aria-label": "Google e-mail address"});
    const only = h("input", {type: "checkbox", checked: googleOn, disabled: !googleOn});
    const checks = h("div", {class: "stack", style: "gap:10px"}, roleChecks(["tester"]));
    const error = h("div", {class: "meta", role: "alert", style: "color:var(--danger);min-height:18px"});
    drawer({title: "Add a user", sub: googleOn ? `With an e-mail address they can sign in with ${how}. Otherwise they get a temporary password` : "They get a temporary password to change at their first sign-in",
      body: () => [h("div", {class: "stack"}, field("Full name", name, "", "u-name"), field("User name", user, "Letters, digits, dots and dashes.", "u-user"),
        field("Job title (optional)", title, "Shown next to their name when they approve a release.", "u-title"),
        field("Google e-mail address (optional)", mail, "The Gmail address they sign in with. Only addresses you add here can sign in.", "u-mail"),
        googleOn ? h("div", {}, h("label", {class: "switch"}, only, `No password: only ${how}`), h("div", {class: "hint"}, `They sign in with the ${how} button and have no password to lose.`)) : null,
        h("div", {}, h("div", {class: "label"}, "Roles"), checks, h("div", {class: "hint"}, "With no role a person can only look.")), error)],
      foot: (close) => [h("span", {class: "grow"}), button("Cancel", {onClick: close}),
        button("Add the user", {kind: "primary", onClick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          try {
            const r = await api("/api/users", {action: "create", full_name: name.value, username: user.value, title: title.value, roles: picked(checks), email: mail.value, google_only: only.checked && !!mail.value.trim()});
            close();
            if (r.password) showPassword(r.user.full_name, r.user.username, r.password, redraw);
            else { toast(`${r.user.full_name} can now sign in with Google.`); redraw(); }
          } catch (err) { error.textContent = err.message; btn.disabled = false; }
        }})]});
  };

  const edit = (u) => {
    const name = input({value: u.full_name, maxlength: "80", "aria-label": "Full name"});
    const title = input({value: u.title, maxlength: "80", "aria-label": "Job title"});
    const checks = h("div", {class: "stack", style: "gap:10px"}, roleChecks(u.roles));
    const mail = input({value: u.email || "", maxlength: "120", type: "email", placeholder: "name@gmail.com", "aria-label": "Google e-mail address"});
    const active = h("input", {type: "checkbox", checked: u.active});
    const error = h("div", {class: "meta", role: "alert", style: "color:var(--danger);min-height:18px"});
    drawer({title: u.full_name, sub: `User name ${u.username}`,
      body: () => [h("div", {class: "stack"}, field("Full name", name, "", "e-name"), field("Job title", title, "", "e-title"),
        field("Google e-mail address", mail, u.google_only ? "This person signs in with Google only. Use Reset password to give them a password." : "Lets this person use Continue with Google.", "e-mail"),
        h("div", {}, h("div", {class: "label"}, "Roles"), checks),
        h("div", {}, h("label", {class: "switch"}, active, "Can sign in"), h("div", {class: "hint"}, "Switch off to stop someone signing in without removing their name from the history.")),
        error)],
      foot: (close) => [
        button("Reset password", {kind: "ghost", onClick: async () => {
          if (!confirm(`Make a new temporary password for ${u.full_name}? Their old one stops working and they are signed out.`)) return;
          try { const r = await api("/api/users", {action: "reset", id: u.id}); close(); showPassword(u.full_name, u.username, r.password, redraw); }
          catch (err) { error.textContent = err.message; }
        }}),
        u.id === me?.id ? null : button("Remove", {kind: "ghost", onClick: async () => {
          if (!confirm(`Remove ${u.full_name}? What they did stays in the history.`)) return;
          try { await api("/api/users", {action: "delete", id: u.id}); close(); toast("Removed."); redraw(); } catch (err) { error.textContent = err.message; }
        }}),
        h("span", {class: "grow"}), button("Cancel", {onClick: close}),
        button("Save", {kind: "primary", onClick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          try {
            await api("/api/users", {action: "update", id: u.id, full_name: name.value, title: title.value, roles: picked(checks), active: active.checked, email: mail.value});
            close();
            toast("Saved.");
            await loadAuth();
            redraw();
          } catch (err) { error.textContent = err.message; btn.disabled = false; }
        }})]});
  };

  const rows = d.users.map((u) => h("div", {class: "row", style: "flex-wrap:nowrap;gap:12px;padding:10px 0;border-bottom:1px solid var(--border)"},
    h("div", {class: "grow", style: "min-width:0"},
      h("div", {class: "row", style: "gap:8px"}, h("strong", {}, u.full_name), u.id === me?.id ? badge("you", "info") : null,
        u.origin === "sso" ? badge("Single sign-on", "info") : u.email ? badge(u.google_only ? "Google only" : "Google", "info") : null, !u.active ? badge("cannot sign in", "neutral") : null, u.must_change ? badge("must choose a password", "warning") : null),
      h("div", {class: "meta"}, `${u.username}${u.email ? ` · ${u.email}` : ""}${u.title ? ` · ${u.title}` : ""} · ${rolesText(u.roles)} · ${u.last_login ? `last signed in ${when(u.last_login).toLowerCase()}` : "never signed in"}`)),
    button("Change", {size: "sm", onClick: () => edit(u)})));

  const pw = input({type: "password", autocomplete: "current-password", "aria-label": "Your password", placeholder: "Your password"});
  const off = card({title: "Turn off sign-in", sub: "Back to a Quartermaster without a sign-in",
    body: h("div", {class: "stack"}, h("p", {class: "hint", style: "margin:0"}, "Anyone who opens Quartermaster on this computer can then do everything again. The users stay saved, in case you turn it on again."),
      h("div", {class: "row"}, pw, button("Turn off sign-in", {kind: "danger", onClick: async () => {
        try { await api("/api/auth/disable", {password: pw.value}); await loadAuth(); toast("Sign-in is off."); route(); redraw(); } catch (err) { toast(err.message); }
      }})))});

  return h("div", {class: "stack", style: "max-width:820px"},
    card({title: "Users", sub: `${d.users.length} ${d.users.length === 1 ? "person" : "people"} can sign in`,
      actions: button("Add a user", {kind: "primary", size: "sm", ic: "plus", onClick: addUser}),
      body: h("div", {}, rows)}),
    ssoCard(sso, redraw),
    googleCard(g, redraw),
    card({title: "What each role allows", body: h("div", {class: "stack", style: "gap:8px"},
      ROLE_INFO.map(([, label, help]) => h("div", {}, h("strong", {}, label), h("div", {class: "meta"}, help))),
      h("div", {}, h("strong", {}, "Viewer (no role)"), h("div", {class: "meta"}, "Look at everything operational. Change nothing.")),
      h("p", {class: "hint", style: "margin:0"}, "A person may have several roles. Every sign-in, and every change to a user, is in the audit log. Locked out? On the computer running Quartermaster, a terminal command (qm users) adds a user, resets a password or turns sign-in off."))}),
    off);
}

function turnOnCard() {
  const name = input({maxlength: "80", placeholder: "Your full name", autocomplete: "name", "aria-label": "Your full name"});
  const user = input({maxlength: "32", placeholder: "For example sai", autocomplete: "username", "aria-label": "Your user name"});
  const pw = input({type: "password", autocomplete: "new-password", "aria-label": "Password"});
  const again = input({type: "password", autocomplete: "new-password", "aria-label": "Password again"});
  const error = h("div", {class: "meta", role: "alert", style: "color:var(--danger);min-height:18px"});
  return h("div", {class: "stack", style: "max-width:620px"},
    callout("info", "Sign-in is off.", "Anyone who opens Quartermaster on this computer can do everything: change settings, delete clients, run tests and approve releases. That is fine for one person. Turn it on when a team uses it."),
    card({title: "Turn on sign-in", sub: "You become the first administrator",
      body: h("div", {class: "stack"},
        h("p", {class: "hint", style: "margin:0"}, "After this, everyone signs in with a user name and password, and what they may do depends on their roles. You add the others afterwards. Passwords are never stored: only a salted hash of each."),
        h("div", {class: "fields"}, field("Your full name", name, "", "on-name"), field("Your user name", user, "", "on-user"),
          field("Choose a password", pw, "At least 10 characters.", "on-pw"), field("Password again", again, "", "on-again")),
        error,
        h("div", {class: "row"}, button("Turn on sign-in", {kind: "primary", ic: "lock", onClick: async (e) => {
          if (pw.value !== again.value) { error.textContent = "the two passwords are not the same"; return; }
          if (!confirm("Turn on sign-in? From now on everybody must sign in. You will be signed in as the administrator. Remember this password: to get back in without it you need a terminal on this computer (qm users).")) return;
          const btn = e.currentTarget;
          btn.disabled = true;
          try {
            const r = await api("/api/auth/enable", {username: user.value, full_name: name.value, password: pw.value});
            state.auth = {enabled: true, user: r.user, roles: state.auth?.roles || []};
            toast("Sign-in is on. You are signed in.");
            route();
          } catch (err) { error.textContent = err.message; btn.disabled = false; }
        }})))}));
}

// The temporary password of a new or reset user. It is shown here once and never again.
function showPassword(fullName, username, password, redraw) {
  drawer({title: `Give ${fullName} this password`, sub: `User name ${username}`,
    body: () => [h("div", {class: "stack"},
      callout("warning", "This is the only time it is shown.", "Tell it to them in person or by a phone call, not in a message that stays. At their first sign-in they must choose their own."),
      h("div", {style: "font:600 22px ui-monospace,Consolas,monospace;letter-spacing:.06em;padding:14px;border:1px solid var(--border);border-radius:8px;text-align:center;user-select:all"}, password))],
    foot: (close) => [h("span", {class: "grow"}),
      button("Copy", {ic: "copy", onClick: async () => { try { await navigator.clipboard.writeText(password); toast("Copied."); } catch (e) { toast("Select the password and copy it (Ctrl+C)."); } }}),
      button("Done", {kind: "primary", onClick: () => { close(); redraw(); }})]});
}

// Sign in with Google: the client ID and secret from the Google Cloud console. The secret is never shown again.
function googleCard(g, redraw) {
  const id = input({value: g.client_id, maxlength: "200", placeholder: "123456-abc.apps.googleusercontent.com", autocomplete: "off", "aria-label": "Google client ID"});
  const secret = input({type: "password", maxlength: "200", autocomplete: "new-password", "aria-label": "Google client secret",
    placeholder: g.secret_set ? "Saved. Type a new one to replace it" : "GOCSPX-..."});
  const url = input({value: g.public_url, maxlength: "200", placeholder: "http://localhost:8765", autocomplete: "off", "aria-label": "Public address"});
  const on = h("input", {type: "checkbox", checked: g.enabled});
  const error = h("div", {class: "meta", role: "alert", style: "color:var(--danger);min-height:18px"});
  const redirect = h("div", {style: "font:600 13px ui-monospace,Consolas,monospace;padding:8px 10px;border:1px solid var(--border);border-radius:8px;user-select:all;word-break:break-all"}, g.redirect_uri);
  return card({title: "Sign in with Google", sub: g.enabled ? "On: people can use Continue with Google" : "Off",
    body: h("div", {class: "stack"},
      h("p", {class: "hint", style: "margin:0"}, "Google only says who a person is. What they may do comes from the roles you give them here, and a Google account you have not added below is refused. Steps to get the client ID and secret are in the README, section Sign in with Google."),
      field("Client ID", id, "From Google Cloud console, APIs & Services, Credentials.", "g-id"),
      field("Client secret", secret, "Typed here only. Stored encrypted on this computer, never shown again.", "g-secret"),
      field("Public address (optional)", url, "Leave empty on your own computer. On a server use its https address, for example https://qm.example.com.", "g-url"),
      h("div", {}, h("div", {class: "label"}, "Paste this exact address into Google as the Authorized redirect URI"), redirect),
      h("div", {}, h("label", {class: "switch"}, on, "Turn on Continue with Google")),
      error,
      h("div", {class: "row"}, button("Save", {kind: "primary", onClick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        error.textContent = "";
        try {
          const body = {client_id: id.value, public_url: url.value, enabled: on.checked};
          if (secret.value) body.client_secret = secret.value;
          await api("/api/auth/google", body);
          await loadAuth();
          toast("Saved.");
          redraw();
        } catch (err) { error.textContent = err.message; btn.disabled = false; }
      }})))});
}

// Single sign-on with the company's provider (Okta, Microsoft Entra ID, Keycloak and the like). The secret is never shown again.
function ssoCard(c, redraw) {
  const field2 = (label, control, hint, id) => field(label, control, hint, id);
  const issuer = input({value: c.issuer, maxlength: "300", placeholder: "https://acme.okta.com or https://login.microsoftonline.com/<tenant>/v2.0", autocomplete: "off", "aria-label": "Provider address"});
  const id = input({value: c.client_id, maxlength: "200", autocomplete: "off", "aria-label": "SSO client ID"});
  const secret = input({type: "password", maxlength: "400", autocomplete: "new-password", "aria-label": "SSO client secret",
    placeholder: c.secret_set ? "Saved. Type a new one to replace it" : "The client secret"});
  const label = input({value: c.label === "single sign-on" ? "" : c.label, maxlength: "30", placeholder: "For example Acme login", "aria-label": "Button text"});
  const url = input({value: c.public_url, maxlength: "200", placeholder: "http://localhost:8765", autocomplete: "off", "aria-label": "SSO public address"});
  const domains = input({value: c.domains.join(", "), maxlength: "300", placeholder: "acme.com (empty: any)", autocomplete: "off", "aria-label": "Allowed e-mail domains"});
  const claim = input({value: c.groups_claim, maxlength: "60", autocomplete: "off", "aria-label": "Groups claim"});
  const scopes = input({value: c.scopes, maxlength: "200", autocomplete: "off", "aria-label": "Scopes"});
  const pick = (opts, value, aria) => { const el = h("select", {class: "input", "aria-label": aria}, opts.map(([v, t]) => h("option", {value: v}, t))); el.value = value; return el; };
  const provision = pick([["listed", "Only people I add under Users"], ["auto", "Make a person at their first sign-in"]], c.provision, "Who gets in");
  const defaultRole = pick([["", "Viewer (no role)"], ["tester", "Tester"], ["approver", "Approver"]], c.default_role, "Role for new people");
  const on = h("input", {type: "checkbox", checked: c.enabled});
  const needGroup = h("input", {type: "checkbox", checked: c.require_group});
  const enforce = h("input", {type: "checkbox", checked: c.enforce});
  const rows = [];
  const map = h("div", {class: "stack", style: "gap:6px"});
  const addRow = (group = "", role = "tester") => {
    const row = {group: input({value: group, maxlength: "200", placeholder: "Group name at the provider", "aria-label": "Group"}), role: pick([["admin", "Administrator"], ["tester", "Tester"], ["approver", "Approver"]], role, "Role of the group")};
    rows.push(row);
    draw();
  };
  const draw = () => map.replaceChildren(...rows.map((r, i) => h("div", {class: "row", style: "gap:8px;flex-wrap:nowrap"}, r.group, r.role,
    button("", {ic: "x", kind: "ghost", size: "sm", title: "Remove this group", onClick: () => { rows.splice(i, 1); draw(); }}))));
  for (const [group, roles] of Object.entries(c.role_map)) for (const role of roles) addRow(group, role);
  draw();
  const error = h("div", {class: "meta", role: "alert", style: "color:var(--danger);min-height:18px"});
  const result = h("div", {class: "meta", "aria-live": "polite"});
  const redirect = h("div", {style: "font:600 13px ui-monospace,Consolas,monospace;padding:8px 10px;border:1px solid var(--border);border-radius:8px;user-select:all;word-break:break-all"}, c.redirect_uri);
  const roleMap = () => { const out = {}; for (const r of rows) if (r.group.value.trim()) (out[r.group.value.trim()] ||= []).push(r.role.value); return out; };
  return card({title: "Single sign-on (company)", sub: c.enabled ? `On: people can use Sign in with ${c.label}` : "Off",
    body: h("div", {class: "stack"},
      h("p", {class: "hint", style: "margin:0"}, "Sign in with the company's own login (OpenID Connect: Okta, Microsoft Entra ID, Keycloak, Auth0, Google Workspace). The provider says who a person is; what they may do comes from the groups you map to roles below, or from the roles you give them under Users. Steps are in the README, section Single sign-on."),
      field2("Provider address", issuer, "The issuer: Quartermaster reads the rest from its .well-known/openid-configuration.", "sso-issuer"),
      h("div", {class: "fields"}, field2("Client ID", id, "From the app you registered at the provider.", "sso-id"),
        field2("Client secret", secret, "Typed here only. Stored encrypted, never shown again. Leave empty for a public client (PKCE only).", "sso-secret")),
      h("div", {}, h("div", {class: "label"}, "Register this exact address at the provider as the redirect (sign-in) URI"), redirect),
      field2("Button text", label, "Shown as Sign in with ...; empty: single sign-on.", "sso-label"),
      field2("Public address (optional)", url, "Empty on your own computer. On a server, its https address, for example https://qm.example.com.", "sso-url"),
      h("div", {}, h("div", {class: "label"}, "Who gets in"),
        h("div", {class: "stack", style: "gap:8px"}, provision, h("div", {class: "meta"}, "Role for a new person with no group below:"), defaultRole,
          field2("E-mail domains allowed", domains, "Separate with commas. Empty: any address the provider vouches for.", "sso-domains"))),
      h("div", {}, h("div", {class: "label"}, "Groups to roles"),
        h("p", {class: "hint", style: "margin:0 0 6px"}, "At every sign-in a person's roles follow the groups the provider sends. The last administrator is never taken away."),
        map, button("Add a group", {size: "sm", ic: "plus", onClick: () => addRow()}),
        h("div", {class: "fields", style: "margin-top:8px"}, field2("Groups claim", claim, "Usually groups; roles at some providers.", "sso-claim"), field2("Scopes", scopes, "Must include openid. Add groups if the provider needs it.", "sso-scopes"))),
      h("label", {class: "switch"}, needGroup, "Only people in one of these groups may sign in"),
      h("div", {}, h("label", {class: "switch"}, enforce, "Require single sign-on: only administrators may still use a password"),
        h("div", {class: "hint"}, "Administrators keep their password as the way back in if the provider is down.")),
      h("div", {}, h("label", {class: "switch"}, on, "Turn on single sign-on")),
      error, result,
      h("div", {class: "row"}, button("Save", {kind: "primary", onClick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true; error.textContent = "";
        try {
          const body = {issuer: issuer.value, client_id: id.value, public_url: url.value, enabled: on.checked, label: label.value, scopes: scopes.value,
            groups_claim: claim.value, domains: domains.value, provision: provision.value, default_role: defaultRole.value, role_map: roleMap(),
            require_group: needGroup.checked, enforce: enforce.checked};
          if (secret.value) body.client_secret = secret.value;
          await api("/api/auth/sso", body);
          await loadAuth();
          toast("Saved.");
          redraw();
        } catch (err) { error.textContent = err.message; btn.disabled = false; }
      }}), button("Check the provider", {onClick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true; result.textContent = "Asking the provider...";
        try {
          const r = await api("/api/auth/sso/check", {issuer: issuer.value});
          result.textContent = r.ok ? `The provider answers. ${r.keys} signing ${r.keys === 1 ? "key" : "keys"} found${r.signature_checked ? ", so the signature of every sign-in is checked." : ", but none Quartermaster can check: the rest of each answer is still checked."}` : r.message;
        } catch (err) { result.textContent = err.message; }
        btn.disabled = false;
      }})))});
}
