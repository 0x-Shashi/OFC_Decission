// The setup guide: four short steps from nothing to a pod Quartermaster can test. Opens by itself
// when nothing is set up yet; Settings can start it again to add another client.
import {api, button, callout, card, field, h, icon, input, segmented, toast} from "./ui.js";
import {loadCommon, state} from "./state.js";
import {show} from "./app.js";

const STEPS = ["Client", "Pod", "Sign-in", "Check"];
// What was typed so far, kept while moving back and forth (never the password once saved).
const draft = {step: 0, client: "", clientId: "", envId: "", envName: "", url: "", kind: "DEV", release: "", notProd: false,
  signIn: "password", user: "", password: "", result: null, error: ""};

export async function setupPage() {
  await loadCommon();
  if (state.page !== "setup") return;
  if (state.query.new === "1") { // started again from Settings, for another client
    Object.assign(draft, {step: 0, client: "", clientId: "", envId: "", envName: "", url: "", release: "", notProd: false,
      user: "", password: "", result: null, error: ""});
    history.replaceState(null, "", "#/setup");
    state.query = {};
  }

  const stepper = h("ol", {class: "row", style: "gap:8px;list-style:none;margin:0 0 20px;padding:0;flex-wrap:wrap", "aria-label": "Steps"},
    STEPS.map((name, i) => h("li", {class: "row", style: "gap:6px", "aria-current": i === draft.step ? "step" : null},
      h("span", {class: `icon-tile tone-${i < draft.step ? "success" : i === draft.step ? "primary" : "neutral"}`, style: "width:28px;height:28px"},
        i < draft.step ? icon("check") : h("span", {style: "font-weight:600"}, String(i + 1))),
      h("span", {style: i === draft.step ? "font-weight:600" : "", class: i === draft.step ? "" : "meta"}, name),
      i < STEPS.length - 1 ? h("span", {class: "meta", "aria-hidden": "true"}, "›") : null)));

  const go = (step) => { draft.step = step; draft.error = ""; setupPage(); };
  const error = draft.error ? callout("danger", "Please check.", draft.error) : null;
  let body;
  let next;

  if (draft.step === 0) {
    const name = input({value: draft.client, placeholder: "e.g. Acme Corp", maxlength: "80"});
    body = [h("p", {class: "lead", style: "margin-top:0"}, "Which customer do you test for? Each client gets its own tests, evidence and history."),
      field("Client name", name, "", "setup-client")];
    next = () => {
      if (!name.value.trim()) { draft.error = "Type the client's name."; return setupPage(); }
      if (name.value.trim() !== draft.client) draft.clientId = "";
      draft.client = name.value.trim();
      go(1);
    };
    setTimeout(() => name.focus());
  } else if (draft.step === 1) {
    const envName = input({value: draft.envName, placeholder: "e.g. DEV2", maxlength: "40"});
    const url = input({value: draft.url, placeholder: "https://abcd-dev2.fa.us6.oraclecloud.com", maxlength: "300", inputmode: "url"});
    const release = input({value: draft.release, placeholder: "e.g. 26C", maxlength: "20"});
    const notProd = h("input", {type: "checkbox", checked: draft.notProd});
    body = [h("p", {class: "lead", style: "margin-top:0"}, `Which Oracle Fusion pod of ${draft.client} do you test? Use a test pod, never production.`),
      field("Pod address", url, "Open the pod's sign-in page in your browser and copy the address from the top.", "setup-url"),
      h("div", {class: "fields"}, field("Name", envName, "How it is shown, e.g. DEV2 or UAT.", "setup-env"),
        field("Oracle release", release, "The release it is on now, e.g. 26C. You can add it later.", "setup-release")),
      h("div", {}, h("div", {class: "label"}, "Kind"),
        segmented([["DEV", "Development"], ["TEST", "Test"], ["STAGE", "Stage / UAT"]], draft.kind, (v) => { draft.kind = v; }, "Kind")),
      h("div", {}, h("label", {class: "switch"}, notProd, "This is a test pod, not production"),
        h("div", {class: "hint"}, "Only needed when the address has no dev, test, stage or uat in it."))];
    next = () => {
      Object.assign(draft, {envName: envName.value.trim(), url: url.value.trim(), release: release.value.trim(), notProd: notProd.checked});
      if (!draft.url) { draft.error = "Paste the pod address."; return setupPage(); }
      if (!draft.envName) draft.envName = (draft.url.replace(/^https?:\/\//, "").split(".")[0] || "DEV").toUpperCase();
      go(2);
    };
    setTimeout(() => url.focus());
  } else if (draft.step === 2) {
    const user = input({value: draft.user, placeholder: "User name", maxlength: "120", autocomplete: "off"});
    const password = input({type: "password", placeholder: draft.password ? "Typed (hidden)" : "Password", maxlength: "200", autocomplete: "new-password"});
    const userBox = h("div", {class: "fields"}, field("Test user", user, "A test user of the pod, never a real person's account.", "setup-user"),
      field("Password", password, "Saved encrypted on this computer, never shown again.", "setup-password"));
    const ssoNote = callout("info", "Single sign-on.", "After setup, Quartermaster opens a browser on the pod: sign in the way you always do, including the phone code. Runs then use that sign-in.");
    const draw = () => { userBox.style.display = draft.signIn === "password" ? "" : "none"; ssoNote.style.display = draft.signIn === "sso" ? "" : "none"; };
    body = [h("p", {class: "lead", style: "margin-top:0"}, "How does the pod sign you in?"),
      segmented([["password", "User name and password"], ["sso", "Company sign-on (Microsoft, Okta) or a phone code"]], draft.signIn,
        (v) => { draft.signIn = v; draw(); }, "Sign-in"),
      userBox, ssoNote,
      h("p", {class: "hint", style: "margin:0"}, "Tests that switch user (for example a Line Manager who approves) need that user too: add it later in Settings, Clients and environments.")];
    draw();
    next = () => {
      draft.user = user.value.trim();
      if (password.value) draft.password = password.value;
      if (draft.signIn === "password" && (!draft.user || !draft.password)) { draft.error = "Type the test user and its password."; return setupPage(); }
      go(3);
      save();
    };
    setTimeout(() => user.focus());
  } else {
    const r = draft.result;
    body = r === null ? [h("p", {class: "lead"}, "Saving and checking the pod…")]
      : r.saveError ? [callout("danger", "Could not save.", r.saveError)]
        : [r.ok ? callout("info", "All set.", `${draft.client} · ${draft.envName} is saved and in use. ${r.message}`)
          : callout("warning", "Saved, but the pod did not answer.", `${r.message.replace(/\.?$/, ".")} Check the address, or that this computer can reach the pod (VPN), then click Check again.`),
        draft.signIn === "sso" ? h("p", {}, "Next, sign in by hand once: ", button("Sign in by hand", {size: "sm", ic: "lock", onClick: async () => {
          try { await api("/api/signin", {}); toast("A browser opened on the pod. Sign in there; it closes by itself."); } catch (err) { toast(err.message); }
        }})) : null,
        h("p", {class: "meta"}, "The password is kept encrypted on this computer. You can change anything later in Settings.")];
  }

  const back = draft.step > 0 && draft.step < 3 ? button("Back", {onClick: () => go(draft.step - 1)}) : null;
  const actions = draft.step < 3
    ? [back, h("span", {class: "grow"}), button(draft.step === 2 ? "Save and check" : "Next", {kind: "primary", ic: draft.step === 2 ? "check" : "right", onClick: next})]
    : draft.result?.saveError
      ? [button("Back", {onClick: () => go(draft.result.step ?? 1)}), h("span", {class: "grow"})]
      : [draft.result && !draft.result.ok ? button("Check again", {ic: "refresh", onClick: save}) : null, h("span", {class: "grow"}),
        button("Go to the overview", {kind: "primary", ic: "right", disabled: draft.result === null, onClick: () => {
          Object.assign(draft, {step: 0, password: "", result: null});
          location.hash = "#/";
        }})];

  show([{label: "Set up"}],
    h("div", {class: "page-head"}, h("div", {}, h("h1", {}, "Set up Quartermaster"),
      h("p", {class: "lead"}, "Four short steps. Everything can be changed later in Settings."))),
    h("div", {style: "max-width:720px"}, card({body: h("div", {class: "stack", style: "gap:16px"}, stepper, error, ...[body].flat(),
      h("div", {class: "row", style: "gap:8px;border-top:1px solid var(--border);padding-top:16px"}, actions))})));
}

// Save the client, its pod and its user, then check the pod answers. A step that fails sends the
// reader back to the step to fix; nothing is saved twice.
async function save() {
  draft.result = null;
  setupPage();
  let step = 0;
  try {
    if (!draft.clientId) draft.clientId = (await api("/api/environments/client", {name: draft.client})).id;
    step = 1;
    const env = await api("/api/environments/environment", {id: draft.envId || undefined, client_id: draft.clientId, name: draft.envName,
      url: draft.url, kind: draft.kind, release: draft.release, sign_in: draft.signIn, not_production: draft.notProd});
    draft.envId = env.id;
    await api("/api/environments/activate", {id: env.id});
    step = 2;
    if (draft.signIn === "password") {
      await api("/api/environments/user", {environment_id: env.id, username: draft.user, password: draft.password});
      draft.password = ""; // saved encrypted on the computer; not kept in the page
    }
    draft.result = await api("/api/environments/check", {id: env.id});
  } catch (err) {
    draft.result = {saveError: err.message, step};
  }
  await loadCommon();
  document.dispatchEvent(new CustomEvent("qm:common"));
  if (state.page === "setup") setupPage();
}
