// The test library: ready-made packs of tests that come with Quartermaster, copied into your tests folder.
import {api, badge, button, callout, card, disclose, emptyState, h, plural, toast} from "./ui.js";
import {loadCommon, state} from "./state.js";
import {openRunDrawer} from "./components.js";
import {show} from "./app.js";

export async function packsPage() {
  const [, d] = await Promise.all([loadCommon(), api("/api/packs")]);
  if (state.page !== "packs") return;
  const checked = (c) => c ? badge(`checked on a pod: ${c.passed} of ${c.of} passed (${c.date})`, c.passed === c.of ? "success" : "warning")
    : badge("not checked on a pod yet", "neutral");
  const installNote = (p) => {
    const i = p.installed;
    if (!i) return null;
    return h("p", {class: "hint", style: "margin:0"},
      `Installed (version ${i.version}) in ${d.folder}/${p.id}. `,
      i.changed.length ? `You have changed ${plural(i.changed.length, "file")}; ${i.changed.length === 1 ? "it is" : "they are"} kept as they are when you update. ` : "",
      i.update_available ? "A newer version of the pack is available: Update adds and updates what you have not changed." : "Up to date.");
  };
  const packCard = (p) => card({
    title: p.title,
    sub: `${p.id} · ${plural(p.tests.length, "test")}`,
    actions: [badge(p.module, "info"), p.read_only ? badge("read only", "success") : null].filter(Boolean),
    body: h("div", {class: "stack", style: "gap:10px"},
      h("div", {class: "row", style: "gap:8px"}, checked(p.checked)),
      h("p", {style: "margin:0"}, p.description),
      p.needs ? callout("info", "What the test user needs.", p.needs) : null,
      disclose(`The ${plural(p.tests.length, "test")}`,
        h("ul", {style: "margin:4px 0 0;padding-left:20px"}, p.tests.map((t) => h("li", {}, t.title, h("span", {class: "meta"}, ` · ${t.product}`))))),
      installNote(p),
      h("div", {class: "row", style: "gap:8px"},
        button(p.installed ? (p.installed.update_available ? "Update" : "Install again") : "Install", {size: "sm", kind: p.installed && !p.installed.update_available ? "" : "primary", ic: "download", onClick: async (e) => {
          e.currentTarget.disabled = true;
          try {
            const r = await api("/api/packs/install", {pack: p.id});
            const said = ["added", "updated", "unchanged", "kept", "removed"].filter((k) => r[k].length).map((k) => `${r[k].length} ${k === "kept" ? "kept as you have them" : k}`).join(", ");
            toast(`${p.title}: ${said || "nothing to do"}.`);
            packsPage();
          } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
        }}),
        p.installed ? button("Run these tests", {size: "sm", ic: "play", disabled: !state.status.ready, onClick: () => openRunDrawer(`suite:${p.installed.suite}`)}) : null,
        p.installed ? h("a", {class: "btn sm", href: "#/suites"}, "Suites") : null)),
  });
  show([{label: "Test library"}], h("div", {class: "stack", style: "max-width:900px"},
    h("div", {class: "page-head"}, h("div", {},
      h("h1", {}, "Test library"),
      h("p", {class: "lead"}, "Ready-made tests that come with Quartermaster. Install a pack and its tests appear in your tests folder, where they are ordinary tests: run them, schedule them, edit them, put them in suites. A suite for the pack is made too."))),
    callout("info", "What these tests do, and what they do not.",
      "They only ask the pod questions (GET): nothing is ever created, changed or deleted. Each one checks that an Oracle REST service still answers and still returns the fields integrations rely on, which is where a quarterly update most often breaks something quietly. They do not click through screens. \"Checked on a pod\" means the pack was run on one test pod on that date, and says how many passed there; your pod, release and test user can differ, so run it once on yours and look at what fails."),
    d.packs.length ? d.packs.map(packCard) : card({body: emptyState({ic: "folder", title: "No packs", text: "This copy of Quartermaster has no test library."})})));
}
