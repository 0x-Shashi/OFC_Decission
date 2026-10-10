// Clients and environments: the pods Quartermaster tests, set up on the page instead of in a
// terminal. Each client has its environments (DEV, TEST, STAGE pods); each environment has its
// test users. Passwords are typed here, saved encrypted, and never shown again.
import {api, badge, button, callout, card, drawer, emptyState, field, h, icon, input, segmented, toast} from "./ui.js";
import {loadCommon} from "./state.js";

const KIND_TONE = {DEV: "info", TEST: "warning", STAGE: "neutral"};

export async function environmentsCard(onChange) {
  const data = await api("/api/environments");
  const changed = async (message) => {
    if (message) toast(message);
    await loadCommon();
    document.dispatchEvent(new CustomEvent("qm:common"));
    onChange();
  };
  const use = async (env) => {
    try {
      await api("/api/environments/activate", {id: env.id});
      changed(`Now using ${env.name}. Runs, recordings and Prepare go to ${env.host}.`);
    } catch (err) { toast(err.message); }
  };
  const envRow = (client, env) => h("div", {class: "list-item", style: "padding:10px 0;align-items:flex-start"},
    h("span", {class: `icon-tile tone-${env.active ? "success" : "neutral"}`}, icon(env.active ? "check" : "server")),
    h("div", {class: "grow", style: "min-width:0"},
      h("div", {class: "row", style: "gap:8px"}, h("span", {class: "t"}, env.name), badge(env.kind, KIND_TONE[env.kind] || "neutral"),
        env.release ? h("span", {class: "release"}, env.release) : null, env.active ? badge("In use", "success") : null),
      h("div", {class: "meta", style: "overflow-wrap:anywhere"}, env.host),
      h("div", {class: "meta"}, usersText(env))),
    h("div", {class: "row", style: "gap:6px;flex-wrap:nowrap"},
      env.active ? null : button("Use", {size: "sm", ic: "play", title: `Run tests on ${env.name}`, onClick: () => use(env)}),
      button("Edit", {size: "sm", ic: "wrench", onClick: () => openEnvironment(client, env, changed)})));

  const clientBlock = (client) => h("section", {style: "border-top:1px solid var(--border);padding-top:12px;margin-top:12px"},
    h("div", {class: "row", style: "gap:8px"},
      h("h3", {class: "grow", style: "margin:0"}, client.name),
      button("Add environment", {size: "sm", ic: "plus", onClick: () => openEnvironment(client, null, changed)}),
      button("", {size: "sm", kind: "ghost", ic: "wrench", title: `Rename or delete ${client.name}`, onClick: () => openClient(client, changed)})),
    h("div", {class: "meta"}, `Its own tests, evidence, runs and schedules, kept in ${client.folder ? `clients/${client.folder}` : "the default folders"}.`),
    client.environments.length ? client.environments.map((e) => envRow(client, e))
      : h("p", {class: "meta", style: "margin:8px 0 0"}, "No environment yet. Add the client's DEV or TEST pod."));

  return card({
    title: "Clients and environments",
    sub: `The pods Quartermaster tests. Passwords are ${data.protection} and never shown again.`,
    actions: data.clients.length ? button("Add client", {size: "sm", ic: "plus", onClick: () => openClient(null, changed)}) : null,
    body: data.clients.length ? data.clients.map(clientBlock)
      : emptyState({ic: "server", tone: "primary", title: "No pod set up yet",
        text: "Add the client you test for, then its pod: the address, the Oracle release and a test user. Everything is set up here; no terminal needed.",
        actions: button("Add a client", {kind: "primary", size: "sm", ic: "plus", onClick: () => openClient(null, changed)})}),
  });
}

function usersText(env) {
  if (env.sign_in === "sso") return "Single sign-on: sign in by hand when you use it";
  if (!env.users.length) return "No test user yet";
  const main = env.users.find((u) => !u.persona);
  const others = env.users.filter((u) => u.persona).length;
  return [main ? `Signs in as ${main.username}${main.password_set ? "" : " (no password yet)"}` : "No default user",
    others ? `${others} more ${others === 1 ? "persona" : "personas"}` : ""].filter(Boolean).join(" · ");
}

function openClient(client, changed) {
  const name = input({value: client?.name || "", placeholder: "e.g. Acme Corp", maxlength: "80"});
  drawer({
    title: client ? "Client" : "New client",
    sub: "A customer whose Oracle Fusion pods you test.",
    body: () => [field("Name", name, "", "client-name")],
    foot: (close) => [
      client ? button("Delete", {kind: "ghost", onClick: async () => {
        const n = client.environments.length;
        if (!confirm(`Delete ${client.name}${n ? ` and its ${n === 1 ? "environment" : `${n} environments`} with their saved users and passwords` : ""}? Past runs and evidence stay.`)) return;
        try { await api("/api/environments/client/delete", {id: client.id}); close(); changed("Client deleted."); } catch (err) { toast(err.message); }
      }}) : null,
      h("span", {class: "grow"}),
      button("Cancel", {onClick: close}),
      button("Save", {kind: "primary", ic: "check", onClick: async (e) => {
        const b = e.currentTarget;
        b.disabled = true;
        try {
          const saved = await api("/api/environments/client", {id: client?.id, name: name.value});
          close();
          if (client) changed("Saved.");
          else { await changed(); openEnvironment({...saved, environments: []}, null, changed); }
        } catch (err) { toast(err.message); b.disabled = false; }
      }}),
    ],
  });
  name.focus();
}

function openEnvironment(client, env, changed) {
  const name = input({value: env?.name || "", placeholder: "e.g. DEV2", maxlength: "40"});
  const url = input({value: env?.url || "", placeholder: "https://abcd-dev2.fa.us6.oraclecloud.com", maxlength: "300", inputmode: "url"});
  const release = input({value: env?.release || "", placeholder: "e.g. 26C", maxlength: "20"});
  const notProd = h("input", {type: "checkbox", checked: Boolean(env?.not_production)});
  let kind = env?.kind || "DEV";
  let signIn = env?.sign_in || "password";

  // Test users: the default one, and one per persona a test switches to (login_as).
  const users = h("div", {class: "stack", style: "gap:10px"});
  const userRow = (u = {persona: "", username: "", password_set: false}, isDefault = false) => {
    const persona = input({value: isDefault ? "" : prettyPersona(u.persona), placeholder: "e.g. Line Manager", maxlength: "40",
      "aria-label": "Persona", disabled: isDefault});
    const username = input({value: u.username, placeholder: "User name", maxlength: "120", "aria-label": "User name", autocomplete: "off"});
    const password = input({type: "password", placeholder: u.password_set ? "Saved. Type to change" : "Password", maxlength: "200",
      "aria-label": "Password", autocomplete: "new-password"});
    const row = h("div", {class: "grid", style: "grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;align-items:center"},
      isDefault ? h("div", {class: "meta"}, h("strong", {}, "Default user")) : persona, username, password,
      isDefault ? null : button("Remove", {size: "sm", kind: "ghost", onClick: () => { row.dataset.removed = "1"; row.style.display = "none"; }}));
    row._value = () => ({persona: isDefault ? "" : persona.value.trim(), username: username.value.trim(), password: password.value,
      was: u.persona, existed: Boolean(u.username), removed: row.dataset.removed === "1"});
    users.append(row);
  };
  const existing = env?.users || [];
  userRow(existing.find((u) => !u.persona) || undefined, true);
  existing.filter((u) => u.persona).forEach((u) => userRow(u));
  const usersBlock = h("div", {},
    h("div", {class: "label"}, "Test users"),
    h("p", {class: "hint", style: "margin:0 0 8px"}, "The default user signs in for every test. Add a persona for a test that switches user, e.g. an employee submits and the Line Manager approves."),
    users,
    h("div", {style: "margin-top:8px"}, button("Add persona", {size: "sm", ic: "plus", onClick: () => userRow()})));
  const ssoNote = callout("info", "Single sign-on.", "This pod sends you to your company's sign-on page (Microsoft, Okta) or asks for a phone code. Leave the users empty: after saving, use Sign in by hand under In use now.");
  const drawSignIn = () => {
    usersBlock.style.display = signIn === "sso" ? "none" : "";
    ssoNote.style.display = signIn === "sso" ? "" : "none";
  };
  drawSignIn();
  const result = h("div", {class: "meta", role: "status", "aria-live": "polite"});

  const save = async () => {
    const saved = await api("/api/environments/environment", {id: env?.id, client_id: client.id, name: name.value, url: url.value,
      kind, release: release.value, sign_in: signIn, not_production: notProd.checked});
    if (signIn === "password") {
      for (const row of users.children) {
        const v = row._value();
        if (v.removed) {
          if (v.existed) await api("/api/environments/user/delete", {environment_id: saved.id, persona: v.was});
          continue;
        }
        if (!v.username) continue;
        if (v.existed && v.was !== personaKey(v.persona)) await api("/api/environments/user/delete", {environment_id: saved.id, persona: v.was});
        await api("/api/environments/user", {environment_id: saved.id, persona: v.persona, username: v.username, password: v.password});
      }
    }
    return saved;
  };

  drawer({
    title: env ? env.name : "New environment",
    sub: client.name,
    body: () => [
      h("div", {class: "fields"},
        field("Name", name, "How it is shown, e.g. DEV2 or UAT.", "env-name"),
        field("Oracle release", release, "The release this pod is on now. New runs are labelled with it.", "env-release")),
      field("Pod address", url, "Copy it from the browser when the pod's sign-in page is open. Production pods are refused.", "env-url"),
      h("div", {}, h("label", {class: "switch"}, notProd, "This is a test pod, not production"),
        h("div", {class: "hint"}, "Only needed when the address has no dev, test, stage or uat in it. Quartermaster refuses addresses that look like production unless you tick this.")),
      h("div", {}, h("div", {class: "label"}, "Kind"),
        segmented([["DEV", "Development"], ["TEST", "Test"], ["STAGE", "Stage / UAT"]], kind, (v) => { kind = v; }, "Kind")),
      h("div", {}, h("div", {class: "label"}, "Sign-in"),
        segmented([["password", "User name and password"], ["sso", "Single sign-on"]], signIn, (v) => { signIn = v; drawSignIn(); }, "Sign-in")),
      usersBlock, ssoNote,
      h("div", {class: "row", style: "gap:10px"},
        button("Test connection", {size: "sm", ic: "refresh", onClick: async (e) => {
          const b = e.currentTarget;
          b.disabled = true;
          result.textContent = "Checking…";
          try {
            const saved = await save();
            env = {...(env || {}), id: saved.id};
            const r = await api("/api/environments/check", {id: saved.id});
            result.textContent = r.ok ? `Saved. ${r.message}` : `Saved, but: ${r.message}`;
            changed();
          } catch (err) { result.textContent = err.message; }
          b.disabled = false;
        }}), result),
    ],
    foot: (close) => [
      env?.id ? button("Delete", {kind: "ghost", onClick: async () => {
        if (!confirm(`Delete ${env.name} with its saved users and passwords? Past runs and evidence stay.`)) return;
        try { await api("/api/environments/environment/delete", {id: env.id}); close(); changed("Environment deleted."); } catch (err) { toast(err.message); }
      }}) : null,
      h("span", {class: "grow"}),
      button("Cancel", {onClick: close}),
      button("Save", {kind: "primary", ic: "check", onClick: async (e) => {
        const b = e.currentTarget;
        b.disabled = true;
        try { await save(); close(); changed("Saved. Passwords are kept encrypted."); } catch (err) { toast(err.message); b.disabled = false; }
      }}),
    ],
  });
  name.focus();
}

const personaKey = (p) => p.toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_+|_+$/g, "");
const prettyPersona = (key) => key.toLowerCase().split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
