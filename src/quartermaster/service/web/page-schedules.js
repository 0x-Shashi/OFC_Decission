// Scheduled runs: tests that run by themselves on chosen days at a chosen time, while qm serve runs.
import {api, badge, button, callout, card, drawer, emptyState, field, h, input, plural, remember, table, toast, when} from "./ui.js";
import {loadCommon, runLink, schedule, state} from "./state.js";
import {show} from "./app.js";

let suites = [];  // the saved suites, for the schedule form
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export async function schedulesPage() {
  const [, data, found] = await Promise.all([loadCommon(), api("/api/schedules"), api("/api/suites").catch(() => ({suites: []}))]);
  if (state.page !== "schedules") return;
  const list = data.schedules;
  suites = found.suites;
  const what = (s) => s.suite ? `Suite: ${suites.find((x) => x.name === s.suite)?.title || s.suite}` : s.target === "." ? "All tests" : s.target.endsWith(".yaml") || s.target.endsWith(".yml")
    ? (state.tests.find((t) => t.file === s.target)?.title || s.target) : `Folder ${s.target}`;
  const body = list.length ? table({
    caption: "Scheduled runs",
    rows: list,
    onRow: (s) => openSchedule(s),
    columns: [
      {label: "Name", render: (s) => [h("div", {class: "primary-cell"}, s.name), h("div", {class: "sub"}, what(s))]},
      {label: "When", render: (s) => s.when},
      {label: "Next run", render: (s) => s.enabled ? (s.next_run ? when(s.next_run) : "") : badge("Off", "neutral")},
      {label: "Last run", render: (s) => s.last_run_id
        ? h("a", {href: runLink(s.last_run_id)}, s.last_slot ? when(s.last_slot) : "Open")
        : s.last_error ? h("span", {class: "meta", style: "color:var(--danger)"}, s.last_error) : h("span", {class: "muted"}, "Not yet")},
      {srLabel: "Actions", cls: "actions", render: (s) => h("div", {class: "row", style: "gap:6px;flex-wrap:nowrap;justify-content:flex-end"},
        button("Run now", {size: "sm", ic: "play", disabled: !state.status.ready, onClick: async (e) => {
          e.stopPropagation();
          try { const run = await api("/api/schedules/run", {id: s.id}); location.hash = runLink(run.id); } catch (err) { toast(err.message); }
        }}))},
    ],
  }) : emptyState({ic: "clock", tone: "primary", title: "No scheduled runs yet",
    text: "Run tests by themselves, for example every night at 02:00, or every Monday morning after the release lands on the pod.",
    actions: button("New schedule", {kind: "primary", size: "sm", ic: "plus", onClick: () => openSchedule()})});

  show([{label: "Schedules"}],
    h("div", {class: "page-head"},
      h("div", {}, h("h1", {}, "Schedules"), h("p", {class: "lead"}, "Tests that run by themselves on chosen days, at a chosen time.")),
      list.length ? h("div", {class: "row"}, button("New schedule", {kind: "primary", ic: "plus", onClick: () => openSchedule()})) : null),
    callout("info", "Scheduled runs only start while Quartermaster is running.",
      "Keep qm serve running on a computer that stays on (and is signed in to the pod's network). A run missed while it was stopped starts only if Quartermaster is back within the hour."),
    h("div", {class: "card"}, body));
  // Keep "Next run" and "Last run" current while the page is open (the timer checks every 30 s),
  // but never redraw under a schedule being edited.
  const refresh = () => {
    if (state.page !== "schedules") return;
    if (document.querySelector(".scrim")) schedule(refresh, 5000);
    else schedulesPage();
  };
  schedule(refresh, 15000);
}

function openSchedule(s) {
  const st = state.status;
  const runnable = state.tests.filter((t) => !t.problem);
  const folders = [...new Set(runnable.map((t) => t.folder).filter(Boolean))].sort();
  const name = input({value: s?.name || "", placeholder: "e.g. Nightly regression", maxlength: "80"});
  const target = h("select", {class: "input", "aria-label": "What to test"},
    h("option", {value: "."}, `All tests (${plural(runnable.length, "test")})`),
    suites.map((x) => h("option", {value: `suite:${x.name}`}, `Suite: ${x.title} (${plural(x.tests.length, "test")} now)`)),
    folders.map((f) => h("option", {value: f}, `Folder ${f} (${plural(runnable.filter((t) => t.folder === f).length, "test")})`)),
    runnable.map((t) => h("option", {value: t.file}, `Test: ${t.title || t.file}`)));
  target.value = s?.suite ? `suite:${s.suite}` : s?.target || ".";
  const days = new Set(s?.days || [0, 1, 2, 3, 4]);
  const dayBoxes = h("div", {class: "row", style: "gap:6px", role: "group", "aria-label": "Days"}, DAYS.map((d, i) => {
    const box = h("input", {type: "checkbox", checked: days.has(i), onchange: (e) => { e.target.checked ? days.add(i) : days.delete(i); }});
    return h("label", {class: "chip", style: "gap:6px"}, box, d);
  }));
  const time = input({type: "time", value: s?.time || "02:00", "aria-label": "Time"});
  const enabled = h("input", {type: "checkbox", checked: s ? s.enabled : true});
  const saved = remember("options") || {};
  drawer({
    title: s ? s.name : "New schedule",
    sub: "Runs wait in line with the others and go one at a time.",
    body: () => [
      field("Name", name, "Shown in Runs as “Scheduled: name”.", "sch-name"),
      field("What to test", target, "", "sch-target"),
      h("div", {}, h("div", {class: "label"}, "Days"), dayBoxes),
      field("Time", time, "This computer's clock.", "sch-time"),
      h("label", {class: "switch"}, enabled, "On"),
      h("p", {class: "hint", style: "margin:0"}, `Pictures and video follow Settings, Evidence (now: screenshots ${saved.screenshots || "every-step"}, video ${saved.video || "off"}, highlight clicks ${saved.highlight === false ? "off" : "on"}). The browser is not shown.`),
    ],
    foot: (close) => [
      s ? button("Delete", {kind: "ghost", onClick: async () => {
        if (!confirm(`Delete the schedule “${s.name}”? Its past runs stay in Runs.`)) return;
        try { await api("/api/schedules/delete", {id: s.id}); close(); toast("Schedule deleted."); schedulesPage(); } catch (err) { toast(err.message); }
      }}) : null,
      h("span", {class: "grow"}),
      button("Cancel", {onClick: close}),
      button("Save", {kind: "primary", ic: "check", onClick: async (e) => {
        e.currentTarget.disabled = true;
        try {
          const saved2 = await api("/api/schedules", {id: s?.id, name: name.value.trim(), ...(target.value.startsWith("suite:") ? {suite: target.value.slice(6)} : {target: target.value}), days: [...days].sort(),
            time: time.value, enabled: enabled.checked, options: {screenshots: saved.screenshots || "every-step", video: saved.video || "off",
              highlight: saved.highlight !== false}});
          close();
          toast(saved2.enabled && saved2.next_run ? `Saved. Next run: ${when(saved2.next_run)}.` : "Saved.");
          schedulesPage();
        } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
      }}),
    ],
  });
  if (!st?.ready) toast("Set up the pod and its sign-in in Settings first, or the scheduled runs will fail.");
}
