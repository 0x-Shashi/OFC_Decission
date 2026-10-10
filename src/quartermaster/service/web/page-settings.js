// Settings: clients and environments, sign-in, AI, evidence, storage, appearance and shortcuts.
// Passwords are never shown.
import {api, button, callout, card, chips, field, h, icon, input, remember, segmented, toast, when} from "./ui.js";
import {checkPod, openFolder} from "./components.js";
import {connection, envName, loadCommon, schedule, state} from "./state.js";
import {setTheme, show} from "./app.js";
import {environmentsCard} from "./page-environments.js";
import {notificationsTab} from "./page-notifications.js";
import {usersTab} from "./page-users.js";
import {autoBackupCard} from "./page-autobackup.js";
import {discoveryCard} from "./page-discovery.js";
import {aiEvalCard} from "./page-aieval.js";
import {ticketSettingsCard} from "./page-tickets.js";

export async function settingsPage() {
  await loadCommon();
  if (state.page !== "settings") return;
  const st = state.status;
  const c = connection(st);

  const row = (label, value, ok, extra) => h("div", {class: "list-item", style: "padding:12px 0"},
    h("span", {class: `icon-tile tone-${ok ? "success" : "danger"}`}, icon(ok ? "check" : "x")),
    h("div", {class: "grow"}, h("div", {class: "t"}, label), h("div", {class: "meta", style: "overflow-wrap:anywhere"}, value)), extra);

  // The environment runs use now, and how it signs in.
  const inUse = card({title: "In use now",
    sub: st.client ? `${st.client} · ${envName(st)}` : st.pod_url ? "Set in the terminal (QM_FUSION_URL)" : "Nothing set up yet",
    body: h("div", {},
      row("Pod address", st.pod_url || "No pod yet. Add a client and its environment below.", Boolean(st.pod_url)),
      row("Connection", st.pod_check ? `${c.label} · ${st.pod_check.message} · checked ${when(st.pod_check.checked_at).toLowerCase()}` : "Not checked yet",
        Boolean(st.pod_check?.ok),
        button("Check now", {size: "sm", ic: "refresh", disabled: !st.pod_url, onClick: async (e) => {
          e.currentTarget.disabled = true;
          await checkPod().catch((err) => toast(err.message));
          settingsPage();
        }})),
      st.sign_in === "sso" ? null : row("Signs in as", st.user
        ? `${st.user} · password ${st.password_set ? "saved" : "not saved yet: edit the environment below"}`
        : "No test user yet: edit the environment below.", Boolean(st.user && st.password_set)),
      byHandRow(st))});
  const tab = TABS.some(([id]) => id === state.query.tab) ? state.query.tab : "environments";
  const environments = tab === "environments" ? await environmentsCard(settingsPage) : null;
  const discovery = tab === "environments" ? await discoveryCard(Boolean(st.pod_url)) : null;
  if (state.page !== "settings") return;

  const notifications = tab === "notifications" ? await notificationsTab(settingsPage) : null;
  const users = tab === "users" ? await usersTab(settingsPage) : null;
  if (state.page !== "settings") return;

  const ai = aiCard(st.ai);

  const storage = card({title: "Storage", sub: "Where tests and evidence are kept",
    body: h("div", {class: "stack"}, h("dl", {class: "kv"},
      h("dt", {}, "Tests"), h("dd", {}, h("code", {}, st.tests_folder)),
      h("dt", {}, "Evidence"), h("dd", {}, h("code", {}, st.evidence_folder))),
    h("div", {class: "row"}, button("Open the evidence folder", {size: "sm", ic: "folder", onClick: () => openFolder(".")})))});

  // The same choices as in New run (they are shared): every run started afterwards uses them,
  // including Run on a manual scenario and the runs started from Release impact.
  const opt = {...st.default_options, ...(remember("options") || {})};
  const keep = (key, value) => {
    remember("options", {...(remember("options") || {}), screenshots: opt.screenshots, video: opt.video, headed: Boolean(opt.headed),
      highlight: opt.highlight !== false, [key]: value});
    opt[key] = value;
    toast("Saved. Runs started from now on use it.");
  };
  const headed = h("input", {type: "checkbox", checked: Boolean(opt.headed), onchange: (e) => keep("headed", e.target.checked)});
  const highlight = h("input", {type: "checkbox", checked: opt.highlight !== false, onchange: (e) => keep("highlight", e.target.checked)});
  const evidence = card({title: "Evidence", sub: "Screenshots, video and the browser, for every run you start",
    body: h("div", {class: "stack"},
      h("div", {}, h("div", {class: "label"}, "Screenshots"),
        segmented([["every-step", "Every step"], ["on-failure", "Only failures"], ["off", "None"]], opt.screenshots, (v) => keep("screenshots", v), "Screenshots"),
        h("div", {class: "hint"}, "Screenshots go into the Word evidence document of each test.")),
      h("div", {}, h("div", {class: "label"}, "Video"),
        segmented([["off", "None"], ["on-failure", "Keep on failure"], ["always", "Always"]], opt.video, (v) => keep("video", v), "Video"),
        h("div", {class: "hint"}, "Videos are kept next to the document and shown in the run's Video tab.")),
      h("div", {}, h("div", {class: "label"}, "If a step fails"),
        segmented([["0", "Stop at once"], ["1", "Try once more"], ["2", "Try twice more"]], String(st.default_options.retries ?? 1), async (v) => {
          try { await api("/api/settings", {retries: v}); toast("Saved. Runs started from now on use it."); } catch (err) { toast(err.message); }
        }, "Retries"),
        h("div", {class: "hint"}, "A slow page is not a changed page. A step that fails is tried again after a short wait, before the test fails. Only steps that are safe to repeat are tried again: a click is repeated only when its item was not found (so it was never clicked), and a REST call only when it reads. A test that passes this way is marked Flaky when it keeps happening.")),
      h("div", {}, h("div", {class: "label"}, "Tests at the same time"),
        segmented([["1", "One at a time"], ["2", "2"], ["3", "3"], ["4", "4"]], String(st.default_options.parallel ?? 1), async (v) => {
          try { await api("/api/settings", {parallel: v}); toast("Saved. Runs started from now on use it."); } catch (err) { toast(err.message); }
        }, "Tests at the same time"),
        h("div", {class: "hint"}, "Each test gets its own browser, so a full run finishes sooner. Each browser needs about half a gigabyte of memory, and the pod sees several sign-ins at once, so start with 2. Do not use it if your tests depend on each other or change the same record. Tag such a test serial (tags: [serial]) and it runs alone, last. Watching the browser (below) works best with one at a time.")),
      h("label", {class: "switch"}, headed, "Show the browser while it runs"),
      h("div", {}, h("label", {class: "switch"}, highlight, "Highlight clicks"),
        h("div", {class: "hint"}, "A red box (and a red dot for a click) shows what each step clicks or fills, in the browser and the video, and a red box marks it in the screenshots. Turn off for clean pictures.")),
      h("p", {class: "hint", style: "margin:0"}, "The same choices are in New run; changing them in either place changes both. Prepare and Run by hand always show the browser."))});

  const backups = tab === "general" ? await backupCard() : null;
  const autoBackups = tab === "general" ? await autoBackupCard() : null;
  const tracker = tab === "general" ? await ticketSettingsCard() : null;
  const aiQuality = tab === "ai" ? await aiEvalCard(st.ai) : null;
  if (state.page !== "settings") return;

  const appearance = card({title: "Appearance",
    body: segmented([["system", "Same as this computer"], ["light", "Light"], ["dark", "Dark"]],
      document.documentElement.dataset.theme || "system", setTheme, "Theme")});

  const keys = [["Ctrl K", "Search, go to or run anything"], ["/", "Search"], ["N", "New run"], ["← →", "Previous and next screenshot"],
    ["Esc", "Close a dialog or panel"]];
  const shortcuts = card({title: "Keyboard shortcuts",
    body: h("dl", {class: "kv"}, keys.flatMap(([k, d]) => [h("dt", {}, h("kbd", {}, k)), h("dd", {}, d)]))});

  const content = {
    environments: h("div", {class: "grid g-2"}, h("div", {class: "stack"}, inUse, discovery), h("div", {class: "stack"}, environments)),
    evidence: h("div", {style: "max-width:720px"}, evidence),
    ai: h("div", {class: "stack", style: "max-width:720px"}, ai, aiQuality),
    notifications,
    users,
    general: h("div", {class: "grid g-2"}, h("div", {class: "stack"}, storage, backups, autoBackups, appearance), h("div", {class: "stack"}, tracker, shortcuts)),
  }[tab];
  show([{label: "Settings"}, {label: TABS.find(([id]) => id === tab)[1]}],
    h("div", {class: "page-head"},
      h("div", {}, h("h1", {}, "Settings"), h("p", {class: "lead"}, "Clients and their pods, evidence, the AI assistant, and how Quartermaster looks.")),
      tab === "environments" ? h("div", {class: "row"}, button("Setup guide", {ic: "plus", title: "Add another client step by step", href: "#/setup?new=1"})) : null),
    h("div", {class: "toolbar"}, chips(TABS.map(([id, label]) => [id, label]), tab,
      (v) => { location.hash = v === "environments" ? "#/settings" : `#/settings?tab=${v}`; }, "Settings")),
    content);
}

const TABS = [["environments", "Clients & environments"], ["evidence", "Evidence"], ["ai", "AI assistant"],
  ["notifications", "Notifications"], ["users", "Users & sign-in"], ["general", "General"]];

// Backup and restore: one zip with the tests, run history, clients and settings (never the passwords).
// A restore is only stored here; the next start of Quartermaster applies it, because its databases are
// open while it runs.
async function backupCard() {
  const status = await api("/api/backup/status").catch(() => ({pending: null, copies: []}));
  const withEvidence = h("input", {type: "checkbox", id: "backup-evidence"});
  const download = button("Download a backup", {kind: "primary", ic: "download", onClick: () => {
    location.href = `/api/backup${withEvidence.checked ? "?evidence=1" : ""}`;
    toast("Making the backup. It downloads in a moment.");
  }});
  const pick = h("input", {type: "file", accept: ".zip,application/zip", hidden: true, onchange: async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    if (!confirm(`Restore from ${file.name}? It replaces your tests, run history, clients and settings with what is in the backup, when Quartermaster is started again. A copy of what is here now is kept first.`)) return;
    try {
      const content = await new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
        r.onerror = () => reject(new Error("The file could not be read."));
        r.readAsDataURL(file);
      });
      await api("/api/backup/restore", {content});
      toast("The backup is ready. Close Quartermaster and start it again to finish.");
      settingsPage();
    } catch (err) { toast(err.message); }
  }});
  const waiting = status.pending ? callout("warning", "A restore is waiting.",
    `The backup made ${when(status.pending.created_at).toLowerCase()} replaces what is here the next time Quartermaster starts. Close the black window, then start Quartermaster again.`,
    h("div", {class: "row", style: "margin-top:8px"}, button("Cancel the restore", {size: "sm", onClick: async () => {
      try { await api("/api/backup/cancel", {}); toast("Cancelled."); settingsPage(); } catch (err) { toast(err.message); }
    }}))) : null;
  return card({title: "Backup and restore", sub: "Keep a copy of your tests, run history, clients and settings",
    body: h("div", {class: "stack"}, waiting,
      h("div", {class: "stack", style: "gap:8px"},
        h("div", {class: "row"}, download, button("Restore from a backup", {ic: "folder", onClick: () => pick.click()}), pick),
        h("label", {class: "switch"}, withEvidence, "Also include evidence (screenshots, videos and documents; can be large)")),
      h("p", {class: "hint", style: "margin:0"}, "Passwords of the test users are not in a backup, so it is safe to keep in a shared folder. After a restore on another computer, type those passwords again. The AI key and sign-ins done by hand are never saved."),
      status.copies.length ? h("div", {}, h("div", {class: "label"}, "Copies made before a restore"),
        h("div", {class: "hint"}, `${status.copies.map((c) => c.name).slice(0, 3).join(", ")} in the backups folder inside ${status.folders.data}. To go back, restore one of them (qm restore <file>).`)) : null)});
}

// The AI that prepares manual scenarios. Any provider. The key is pasted here (kept in memory only,
// until Quartermaster stops) or set on the computer; it is never stored or shown again.
function aiCard(ai) {
  const presets = Object.fromEntries(ai.presets.map((p) => [p.id, p]));
  const provider = h("select", {class: "input", "aria-label": "AI provider"},
    h("option", {value: ""}, "None (no AI)"), ai.presets.map((p) => h("option", {value: p.id, selected: p.id === ai.provider}, p.label)));
  const model = input({value: ai.provider ? ai.model : "", placeholder: "The model name, as your provider writes it", maxlength: "120"});
  const key = input({type: "password", autocomplete: "off", placeholder: ai.key_set ? "Saved (hidden). Paste a new key to change it" : "Paste your API key", "aria-label": "API key"});
  const baseUrl = input({value: ai.provider ? ai.base_url : "", placeholder: "https://…", maxlength: "300"});
  const keyEnv = input({value: ai.provider ? ai.key_env : "", placeholder: "e.g. OPENAI_API_KEY", maxlength: "80"});
  const workspace = input({value: ai.workspace || "", placeholder: "e.g. wrkspc_01…", maxlength: "100"});
  const workspaceRow = h("div", {}, field("Workspace ID (Anthropic only)", workspace,
    "Only needed if Anthropic says the key is not scoped to a workspace. Find it in the Anthropic Console, under Workspaces.", "ai-workspace"));
  const drawWorkspace = () => { workspaceRow.style.display = presets[provider.value]?.format === "anthropic" ? "" : "none"; };
  let previous = presets[ai.provider];
  const keyRow = h("div", {});
  const drawKeyRow = () => {
    const p = presets[provider.value];
    const needsKey = provider.value && (keyEnv.value || (p && p.key_env));
    keyRow.style.display = needsKey ? "" : "none";
  };
  provider.onchange = () => {
    const p = presets[provider.value];
    // Fill in what the provider uses, unless the reader typed something of their own.
    for (const [el, k] of [[baseUrl, "base_url"], [keyEnv, "key_env"], [model, "model"]]) {
      if (!el.value || (previous && el.value === previous[k])) el.value = p ? p[k] : "";
    }
    previous = p;
    drawKeyRow();
    drawWorkspace();
  };
  const keyNote = ai.key_source === "entered" ? "A key is saved. It is kept in memory only: paste it again after you restart Quartermaster."
    : ai.key_source === "computer" ? `A key is set on this computer (${ai.key_env}).` : "Kept in memory only, until Quartermaster stops. Never written to a file or shown again.";
  keyRow.append(field("API key", key, keyNote, "ai-key-value"));
  drawKeyRow();
  drawWorkspace();
  const suggest = h("input", {type: "checkbox", checked: ai.suggest !== false});
  const result = h("div", {class: "meta", role: "status", "aria-live": "polite"});
  const save = button("Save", {kind: "primary", onClick: async (e) => {
    e.currentTarget.disabled = true;
    try {
      await api("/api/settings", {ai_suggest: suggest.checked ? "on" : "off", ai_provider: provider.value, ai_model: model.value.trim(), ai_base_url: baseUrl.value.trim(), ai_key_env: keyEnv.value.trim(), ai_workspace: workspace.value.trim()});
      if (key.value.trim()) await api("/api/ai/key", {key: key.value.trim()});
      key.value = "";
      toast("Saved.");
      settingsPage();
    } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
  }});
  const test = button("Test the AI", {ic: "refresh", disabled: !ai.provider || Boolean(ai.problem), title: ai.problem || "Ask the saved AI a one-word question", onClick: async (e) => {
    e.currentTarget.disabled = true;
    result.textContent = "Asking…";
    result.style.color = "";
    try { const r = await api("/api/ai/check", {}); result.textContent = r.message; result.style.color = r.ok ? "var(--success)" : "var(--danger)"; }
    catch (err) { result.textContent = err.message; }
    e.currentTarget.disabled = false;
  }});
  const forget = ai.key_source === "entered" ? button("Forget the key", {kind: "ghost", onClick: async () => {
    try { await api("/api/ai/key", {key: ""}); toast("The key is forgotten."); settingsPage(); } catch (err) { toast(err.message); }
  }}) : null;
  return card({title: "AI assistant", sub: "Prepares manual scenarios automatically. Choose any provider.",
    body: h("div", {class: "stack"},
      h("div", {class: "fields"},
        field("Provider", provider, "Any OpenAI-compatible service works with Other.", "ai-provider"),
        field("Model", model, "The model name shown in your provider's account.", "ai-model"),
        h("div", {class: "wide"}, keyRow)),
      ai.provider ? h("div", {class: "row", style: "gap:8px;flex-wrap:nowrap"},
        h("span", {class: `icon-tile tone-${ai.problem ? "danger" : "success"}`}, icon(ai.problem ? "x" : "check")),
        h("span", {class: "meta"}, ai.problem || `Ready: ${ai.label}.${ai.key_env ? " Key saved (hidden)." : ""}`)) : null,
      h("div", {}, h("label", {class: "switch"}, suggest, "Suggest a fix when a step cannot find its item"),
        h("div", {class: "hint"}, "When a test fails because a button or field is not on the screen any more, the AI is shown the step and the names on the screen and may suggest what it became. You see it in Needs attention and accept or ignore it: nothing changes by itself. Needs the AI above to be ready. Names that look like the old one are suggested without any AI.")),
      h("div", {class: "row"}, save, test, forget), result,
      h("details", {class: "disclose"}, h("summary", {}, icon("right"), "Advanced: web address, key variable, workspace"),
        h("div", {class: "fields", style: "margin-top:12px"},
          h("div", {class: "wide"}, field("Web address", baseUrl, "Filled in for the providers in the list. For Other, use the provider's OpenAI-compatible address.", "ai-url")),
          field("Key variable", keyEnv, "Instead of pasting the key, you can set this variable on the computer before starting qm serve; then it is remembered after a restart.", "ai-key"),
          h("div", {class: "wide"}, workspaceRow))),
      h("p", {class: "hint"}, "What is sent to the AI: the scenario's written steps and the names of the buttons, links and headings on the pod screen. E-mail addresses and long numbers are hidden first. Never typed values, never the pod password. Use a test pod."))});
}

// Single sign-on or MFA: the pod will not take a user name and password from a test, so a person
// signs in once in a browser Quartermaster opens. The session stays in memory, never in a file.
function byHandRow(st) {
  const sh = st.signed_in_by_hand || {status: "none"};
  const start = async (e) => {
    e.currentTarget.disabled = true;
    try { await api("/api/signin", {}); toast("A browser opened on the pod. Sign in there; it closes by itself."); settingsPage(); }
    catch (err) { toast(err.message); e.currentTarget.disabled = false; }
  };
  const text = {
    none: "Not used. For a pod behind single sign-on or MFA, sign in by hand once; runs then use that sign-in.",
    waiting: "Waiting for you to sign in, in the browser that opened…",
    done: `Signed in by hand${sh.at ? ` ${when(sh.at).toLowerCase()}` : ""}. Runs use it until the pod ends the session or Quartermaster stops. It is kept in memory only.`,
    failed: `Did not work: ${sh.error || "the browser closed"}.`,
  }[sh.status] || "";
  const actions = sh.status === "waiting" ? null : [
    button(sh.status === "done" ? "Sign in again" : "Sign in by hand", {size: "sm", ic: "lock", disabled: !st.pod_url, onClick: start}),
    sh.status === "done" ? button("Forget", {size: "sm", kind: "ghost", onClick: async () => {
      try { await api("/api/signin/forget", {}); toast("Forgotten."); settingsPage(); } catch (err) { toast(err.message); }
    }}) : null,
  ];
  if (sh.status === "waiting") schedule(settingsPage, 2000);
  const tone = {done: ["success", "check"], failed: ["danger", "x"], waiting: ["info", "loader"]}[sh.status] || ["neutral", "lock"];
  return h("div", {class: "list-item", style: "padding:12px 0"},
    h("span", {class: `icon-tile tone-${tone[0]}`}, icon(tone[1])),
    h("div", {class: "grow"}, h("div", {class: "t"}, "Single sign-on or MFA"), h("div", {class: "meta", style: "overflow-wrap:anywhere"}, text)),
    actions ? h("div", {class: "row", style: "gap:6px"}, actions) : null);
}
