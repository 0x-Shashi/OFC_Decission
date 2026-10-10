// Sign-in, choosing a new password, and "my account". Only used when sign-in is turned on (Settings, Users & sign-in).
import {api, button, callout, card, field, h, input, toast} from "./ui.js";
import {loadAuth, state} from "./state.js";
import {route, show} from "./app.js";

const ROLE_WORDS = {admin: "Administrator", tester: "Tester", approver: "Approver"};

export const rolesText = (roles) => (roles.length ? roles.map((r) => ROLE_WORDS[r] || r).join(", ") : "Viewer (look only)");

export async function loginPage() {
  const me = state.auth?.user;
  if (me?.must_change) return passwordPage(true);
  if (me) { location.hash = "#/"; return; }
  document.body.classList.add("signed-out");
  const name = input({autocomplete: "username", "aria-label": "User name", placeholder: "Your user name", autofocus: true});
  const pass = input({type: "password", autocomplete: "current-password", "aria-label": "Password", placeholder: "Your password"});
  const error = h("div", {class: "meta", role: "alert", style: "color:var(--danger);min-height:18px"});
  const sent = new URLSearchParams((location.hash.split("?")[1] || "")).get("error");
  if (sent) error.textContent = sent.slice(0, 300); // a plain sentence from the service when Google sign-in did not work
  const label = state.auth?.sso;
  const others = [
    label ? button(`Sign in with ${label}`, {kind: "primary", href: "/api/auth/sso/start", attrs: {id: "login-sso"}}) : null,
    state.auth?.google ? button("Continue with Google", {kind: label ? "" : "primary", href: "/api/auth/google/start", attrs: {id: "login-google"}}) : null,
  ].filter(Boolean);
  const google = others.length
    ? h("div", {class: "stack"}, ...others, h("div", {class: "hint", style: "text-align:center"}, "or use a user name and password"))
    : null;
  const go = async () => {
    error.textContent = "";
    try {
      const r = await api("/api/auth/login", {username: name.value, password: pass.value});
      state.auth = {...state.auth, enabled: true, user: r.user};
      document.body.classList.remove("signed-out");
      if (location.hash === "#/login" || location.hash === "") location.hash = r.user.must_change ? "#/password" : "#/";
      route();
    } catch (err) { error.textContent = err.message; pass.value = ""; pass.focus(); }
  };
  pass.onkeydown = name.onkeydown = (e) => { if (e.key === "Enter") go(); };
  show([{label: "Sign in"}], h("div", {style: "max-width:380px;margin:60px auto"},
    card({title: "Sign in to Quartermaster", sub: "Oracle Fusion release testing",
      body: h("div", {class: "stack"}, google, field("User name", name, "", "login-name"), field("Password", pass, "", "login-pass"), error,
        button("Sign in", {kind: "primary", onClick: go}),
        h("p", {class: "hint", style: "margin:0"}, "No account yet, or forgotten your password? Ask a Quartermaster administrator."))})));
  name.focus();
}

// A new password: forced at the first sign-in after an administrator made or reset the account, or chosen later.
export async function passwordPage(forced = false) {
  const me = state.auth?.user;
  if (!me) { location.hash = "#/login"; return; }
  const current = input({type: "password", autocomplete: "current-password", "aria-label": "Current password"});
  const next = input({type: "password", autocomplete: "new-password", "aria-label": "New password"});
  const again = input({type: "password", autocomplete: "new-password", "aria-label": "New password again"});
  const error = h("div", {class: "meta", role: "alert", style: "color:var(--danger);min-height:18px"});
  const save = async () => {
    if (next.value !== again.value) { error.textContent = "the two new passwords are not the same"; return; }
    try {
      await api("/api/auth/password", {current: current.value, new: next.value});
      await loadAuth();
      toast("Password changed.");
      location.hash = "#/";
      route();
    } catch (err) { error.textContent = err.message; }
  };
  show([{label: forced ? "Choose a new password" : "Change password"}], h("div", {style: "max-width:420px;margin:40px auto"},
    card({title: forced ? "Choose your own password" : "Change your password",
      sub: forced ? `Welcome, ${me.full_name}. The password you were given is only for the first sign-in.` : me.full_name,
      body: h("div", {class: "stack"},
        field(forced ? "The password you were given" : "Current password", current, "", "pw-current"),
        field("New password", next, "At least 10 characters. Not your user name, and not a common one.", "pw-new"),
        field("New password again", again, "", "pw-again"), error,
        h("div", {class: "row"}, button("Save the new password", {kind: "primary", onClick: save}),
          forced ? button("Sign out", {kind: "ghost", onClick: logout}) : null))})));
}

export async function accountPage() {
  const me = state.auth?.user;
  if (!me) { location.hash = "#/login"; return; }
  show([{label: "My account"}], h("div", {class: "stack", style: "max-width:560px"},
    h("div", {class: "page-head"}, h("div", {}, h("h1", {}, me.full_name), h("p", {class: "lead"}, me.title || "Signed in to Quartermaster"))),
    card({title: "Who you are", body: h("dl", {class: "kv"},
      h("dt", {}, "User name"), h("dd", {}, me.username), h("dt", {}, "Roles"), h("dd", {}, rolesText(me.roles)),
      h("dt", {}, "What that allows"), h("dd", {}, roleHelp(me.roles)))}),
    h("div", {class: "row"}, button("Change my password", {href: "#/password"}), button("Sign out", {kind: "ghost", onClick: logout}))));
}

function roleHelp(roles) {
  if (roles.includes("admin")) return "Everything: settings, clients, users, backups and notifications, as well as running tests and approving releases.";
  const can = [];
  if (roles.includes("tester")) can.push("record, run and import tests, schedule them, and accept screen changes");
  if (roles.includes("approver")) can.push("approve or withdraw the approval of a release");
  return can.length ? `Look at everything, and ${can.join("; ")}.` : "Look at everything, but change nothing.";
}

export async function logout() {
  try { await api("/api/auth/logout", {}); } catch (err) { /* already signed out */ }
  state.auth = {...state.auth, user: null};
  document.body.classList.add("signed-out");
  location.hash = "#/login";
  route();
}
