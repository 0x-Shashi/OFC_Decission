// Shared steps: groups of steps written once in the _library folder and used by many tests.
import {api, badge, button, callout, card, disclose, emptyState, h, toast} from "./ui.js";
import {state, testLink} from "./state.js";
import {show} from "./app.js";

const EXAMPLE = `library: open-locations
title: Open the Locations page
params:
  page_name: Locations        # a test may change it with: with: {page_name: ...}
steps:
  - action: navigate
    intent: Open \${page_name}
    value: Workforce Structures > \${page_name}
`;

const USE = `steps:
  - use: open-locations
  - action: assert_visible
    intent: The page is open
    target: {strategies: [{text: Locations}]}
`;

export async function libraryPage() {
  const d = await api("/api/library");
  if (state.page !== "library") return;
  const copy = (text) => navigator.clipboard.writeText(text).then(() => toast("Copied"), () => toast("Could not copy"));
  const groupCard = (g) => card({
    title: g.title || g.name,
    sub: g.title ? g.name : g.file,
    actions: badge(g.used_by.length ? `used by ${g.used_by.length} ${g.used_by.length === 1 ? "test" : "tests"}` : "not used yet", g.used_by.length ? "info" : "neutral"),
    body: h("div", {class: "stack", style: "gap:10px"},
      g.description ? h("p", {class: "hint", style: "margin:0"}, g.description) : null,
      h("div", {}, h("div", {class: "label"}, `${g.steps.length} ${g.steps.length === 1 ? "step" : "steps"}${g.cleanup ? `, then ${g.cleanup} cleanup ${g.cleanup === 1 ? "step" : "steps"} added to each test that uses it` : ""}`),
        h("ol", {style: "margin:4px 0 0;padding-left:20px"}, g.steps.map((s) => h("li", {}, s.intent || s.action)))),
      g.params.length ? h("div", {}, h("div", {class: "label"}, "A test can hand in"),
        h("ul", {style: "margin:4px 0 0;padding-left:20px"}, g.params.map((p) => h("li", {}, h("code", {}, p.name), p.default === null ? " (the test must give it)" : ` (if not given: ${p.default})`)))) : null,
      g.used_by.length ? h("div", {}, h("div", {class: "label"}, "Used by"),
        h("div", {class: "row", style: "gap:8px"}, g.used_by.map((t) => h("a", {href: testLink(t.file)}, t.title)))) : null,
      disclose("The file", h("div", {},
        h("div", {class: "row", style: "justify-content:space-between;margin-bottom:8px"},
          h("span", {class: "meta"}, `${g.file}. Edit it in any text editor: every test that uses it follows.`),
          button("Copy", {size: "sm", ic: "copy", onClick: () => copy(g.yaml)})),
        h("pre", {class: "block", style: "max-height:360px"}, g.yaml)))),
  });
  show([{label: "Shared steps"}], h("div", {class: "stack", style: "max-width:900px"},
    h("div", {class: "page-head"}, h("div", {},
      h("h1", {}, "Shared steps"),
      h("p", {class: "lead"}, "Steps written once and used by many tests, such as opening a page or creating a record. Change the shared file and every test that uses it follows."))),
    d.missing.length ? callout("danger", "A test uses shared steps that do not exist.",
      d.missing.map((m) => `'${m.name}' (used by ${m.used_by.map((t) => t.title).join(", ")})`).join("; ")) : null,
    d.problems.length ? callout("warning", "Some files in the shared folder cannot be used.",
      h("ul", {style: "margin:4px 0 0;padding-left:20px"}, d.problems.map((p) => h("li", {}, `${p.file}: ${p.problem}`)))) : null,
    d.groups.length ? d.groups.map(groupCard) : card({body: emptyState({ic: "tests", title: "No shared steps yet",
      text: `Put a YAML file in the ${d.folder} folder. Here is a small one to start from.`,
      actions: button("Copy an example", {size: "sm", ic: "copy", onClick: () => copy(EXAMPLE)})})}),
    card({title: "How to write and use one", body: h("div", {class: "stack", style: "gap:10px"},
      h("p", {class: "hint", style: "margin:0"}, `1. Save a file such as open-locations.yaml in ${d.folder}:`),
      h("pre", {class: "block"}, EXAMPLE),
      h("p", {class: "hint", style: "margin:0"}, "2. In any test, use it with one step:"),
      h("pre", {class: "block"}, USE),
      h("p", {class: "hint", style: "margin:0"}, "A group can also have a `cleanup:` list: it is added to the cleanup of every test that uses it. Other ${names} in a group are test data, so the test needs them in its `data`. A group cannot use another group."))})));
}
