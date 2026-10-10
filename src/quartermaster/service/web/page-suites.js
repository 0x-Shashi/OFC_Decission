// Saved suites: a name for a group of tests, worked out from a rule each time it is used.
import {api, badge, button, callout, card, disclose, drawer, emptyState, field, h, input, plural, toast} from "./ui.js";
import {loadCommon, state, testLink} from "./state.js";
import {openRunDrawer} from "./components.js";
import {show} from "./app.js";

const EXAMPLE = `suite: payables-critical
title: Payables, the ones that matter
include:                      # a test is in if it fits ANY group
  - tags: [smoke]
  - products: [Payables]      # inside a group, EVERY line must fit
    priorities: [critical, high]
  - tests: [hcm.view-worker]  # or name tests one by one
exclude:                      # ...unless it fits ANY of these
  - tags: [flaky]
`;

const LINES = [["tags", "Tags"], ["folders", "Folders"], ["modules", "Modules"], ["products", "Products"], ["priorities", "Priorities"]];
const WORD = {tags: "tagged", folders: "in the folder", modules: "in the module", products: "in the product", priorities: "with priority", tests: "named"};

const or = (values) => values.join(" or ");
// A group in words: "tagged smoke", "in the product Payables and with priority critical or high".
export const words = (group) => Object.entries(group).map(([k, v]) => `${WORD[k]} ${or(v)}`).join(" and ");

export async function suitesPage() {
  const [, d] = await Promise.all([loadCommon(), api("/api/suites")]);
  if (state.page !== "suites") return;
  const copy = (text) => navigator.clipboard.writeText(text).then(() => toast("Copied"), () => toast("Could not copy"));
  const suiteCard = (s) => card({
    title: s.title,
    sub: s.title === s.name ? s.file : s.name,
    actions: [badge(plural(s.tests.length, "test"), s.tests.length ? "info" : "warning"),
      button("Run", {size: "sm", ic: "play", disabled: !state.status.ready || !s.tests.length, onClick: () => openRunDrawer(`suite:${s.name}`)})],
    body: h("div", {class: "stack", style: "gap:10px"},
      s.description ? h("p", {class: "hint", style: "margin:0"}, s.description) : null,
      s.warnings.length ? callout("warning", "Check this suite.", s.warnings.join(" ")) : null,
      h("div", {}, h("div", {class: "label"}, "Includes tests"),
        h("ul", {style: "margin:4px 0 0;padding-left:20px"}, s.include.map((g) => h("li", {}, words(g))))),
      s.exclude.length ? h("div", {}, h("div", {class: "label"}, "Except tests"),
        h("ul", {style: "margin:4px 0 0;padding-left:20px"}, s.exclude.map((g) => h("li", {}, words(g))))) : null,
      s.tests.length ? disclose(`The ${plural(s.tests.length, "test")} it has now`,
        h("ul", {style: "margin:4px 0 0;padding-left:20px"}, s.tests.map((t) => {
          const file = state.tests.find((x) => x.id === t.id)?.file;
          return h("li", {}, file ? h("a", {href: testLink(file)}, t.title) : t.title);
        }))) : null,
      disclose("The file", h("div", {},
        h("div", {class: "row", style: "justify-content:space-between;margin-bottom:8px"},
          h("span", {class: "meta"}, `${s.file}. ${s.editable ? "Edit it here or in any text editor." : "It uses rules this page cannot edit: change it in a text editor."}`),
          button("Copy", {size: "sm", ic: "copy", onClick: () => copy(s.yaml)})),
        h("pre", {class: "block", style: "max-height:320px"}, s.yaml))),
      h("div", {class: "row", style: "gap:8px"},
        button("Change", {size: "sm", ic: "wrench", disabled: !s.editable, onClick: () => openEditor(d, s)}),
        button("Delete", {size: "sm", kind: "ghost", onClick: async () => {
          if (!confirm(`Delete the suite “${s.title}”? The tests themselves stay.`)) return;
          try { await api("/api/suites/delete", {name: s.name}); toast("Suite deleted."); suitesPage(); } catch (err) { toast(err.message); }
        }}))),
  });
  show([{label: "Suites"}], h("div", {class: "stack", style: "max-width:900px"},
    h("div", {class: "page-head"}, h("div", {},
      h("h1", {}, "Suites"),
      h("p", {class: "lead"}, "A name for a group of tests, such as the smoke tests or everything in Payables. A suite is a rule, so a new test that fits it joins by itself.")),
      button("New suite", {kind: "primary", ic: "plus", onClick: () => openEditor(d, null)})),
    d.problems.length ? callout("warning", "Some files in the suites folder cannot be used.",
      h("ul", {style: "margin:4px 0 0;padding-left:20px"}, d.problems.map((p) => h("li", {}, `${p.file}: ${p.problem}`)))) : null,
    d.suites.length ? d.suites.map(suiteCard) : card({body: emptyState({ic: "folder", title: "No suites yet",
      text: "Make one with New suite, or put a YAML file in " + d.folder + ". Here is a small one to start from.",
      actions: button("Copy an example", {size: "sm", ic: "copy", onClick: () => copy(EXAMPLE)})})}),
    card({title: "In a text file", body: h("div", {class: "stack", style: "gap:10px"},
      h("pre", {class: "block"}, EXAMPLE),
      h("p", {class: "hint", style: "margin:0"}, "Lines are tags, folders, modules, products, priorities and tests (ids). A line fits when the test has any of its values; a group fits when every line fits; a test is in when any group fits. Capital letters do not matter. Run one from a terminal with qm run my_tests --suite payables-critical."))})));
}

// A row of chips that switch values on and off in a Set.
function pick(values, set, onchange, label) {
  const box = h("div", {class: "chips", role: "group", "aria-label": label});
  const draw = () => box.replaceChildren(...values.map((v) => h("button", {type: "button", class: "chip", "aria-pressed": String(set.has(v)),
    onclick: () => { set.has(v) ? set.delete(v) : set.add(v); draw(); onchange(); }}, v)));
  draw();
  return box;
}

function openEditor(d, s) {
  const named = new Set(), leaveTags = new Set(), leaveTests = new Set();
  const groups = [];
  const set = (list) => new Set(list || []);
  for (const g of s?.include || []) {
    if (Object.keys(g).length === 1 && g.tests) g.tests.forEach((t) => named.add(t));
    else groups.push(Object.fromEntries(LINES.map(([k]) => [k, set(g[k])])));
  }
  for (const g of s?.exclude || []) (g.tags ? g.tags : []).forEach((t) => leaveTags.add(t)), (g.tests ? g.tests : []).forEach((t) => leaveTests.add(t));
  if (!groups.length && !named.size) groups.push(Object.fromEntries(LINES.map(([k]) => [k, new Set()])));
  const title = input({value: s?.title || "", placeholder: "e.g. Payables smoke tests", maxlength: "80"});
  const description = input({value: s?.description || "", placeholder: "What it is for (optional)", maxlength: "300"});
  const preview = h("div", {class: "stack", style: "gap:6px", "aria-live": "polite"});
  const groupsBox = h("div", {class: "stack", style: "gap:12px"});
  const runnable = state.tests.filter((t) => !t.problem && t.id);
  const check = (set, id) => h("input", {type: "checkbox", checked: set.has(id), onchange: (e) => { e.target.checked ? set.add(id) : set.delete(id); refresh(); }});
  const list = (set) => h("div", {style: "max-height:160px;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:6px 10px"},
    runnable.map((t) => h("label", {class: "row", style: "gap:8px;padding:2px 0"}, check(set, t.id), t.title || t.id, h("span", {class: "meta"}, t.id))));
  const build = () => {
    const include = groups.map((g) => Object.fromEntries(LINES.filter(([k]) => g[k].size).map(([k]) => [k, [...g[k]]]))).filter((g) => Object.keys(g).length);
    if (named.size) include.push({tests: [...named]});
    const exclude = [];
    if (leaveTags.size) exclude.push({tags: [...leaveTags]});
    if (leaveTests.size) exclude.push({tests: [...leaveTests]});
    return {include, exclude};
  };
  let timer = 0, latest = 0;
  const refresh = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const mine = ++latest;
      const rule = build();
      if (!rule.include.length) { preview.replaceChildren(h("p", {class: "hint", style: "margin:0"}, "Pick something to include and the tests it finds are listed here.")); return; }
      try {
        const r = await api("/api/suites/preview", rule);
        if (mine !== latest) return;
        preview.replaceChildren(h("div", {class: "label"}, `${plural(r.tests.length, "test")} fit now`),
          ...(r.warnings.length ? [h("p", {class: "hint", style: "margin:0"}, r.warnings.join(" "))] : []),
          h("ul", {style: "margin:0;padding-left:20px;max-height:140px;overflow:auto"}, r.tests.map((t) => h("li", {}, t.title))));
      } catch (e) { preview.replaceChildren(h("p", {class: "hint", style: "margin:0"}, e.message)); }
    }, 200);
  };
  const drawGroups = () => {
    groupsBox.replaceChildren(...groups.map((g, i) => h("div", {class: "card", style: "padding:12px"},
      h("div", {class: "row", style: "justify-content:space-between"},
        h("div", {class: "label"}, groups.length > 1 ? `Group ${i + 1}: tests that fit all of these` : "Tests that fit all of these"),
        groups.length > 1 ? button("Remove", {size: "sm", kind: "ghost", onClick: () => { groups.splice(i, 1); drawGroups(); refresh(); }}) : null),
      ...LINES.filter(([k]) => d.choices[k].length).map(([k, label]) => h("div", {}, h("div", {class: "meta"}, `${label} (any of)`),
        pick(d.choices[k], g[k], refresh, label))))));
  };
  drawGroups();
  drawer({
    title: s ? s.title : "New suite",
    sub: "A rule, not a list: a test added later that fits it joins by itself.",
    body: () => [
      field("Name", title, "", "su-title"),
      field("What it is for", description, "", "su-desc"),
      h("div", {}, h("div", {class: "label"}, "Include"), h("p", {class: "hint", style: "margin:0 0 6px"}, "Choose in one or more groups. A test is in the suite when it fits any group. Inside a group it must fit every line you choose."),
        groupsBox, button("Add another group", {size: "sm", ic: "plus", onClick: () => { groups.push(Object.fromEntries(LINES.map(([k]) => [k, new Set()]))); drawGroups(); }})),
      h("div", {}, h("div", {class: "label"}, "Also these tests, one by one"), list(named)),
      d.choices.tags.length ? h("div", {}, h("div", {class: "label"}, "Leave out tests with these tags"), pick(d.choices.tags, leaveTags, refresh, "Leave out tags")) : null,
      h("div", {}, h("div", {class: "label"}, "Leave out these tests"), list(leaveTests)),
      card({title: "What it has now", body: preview}),
    ],
    foot: (close) => [h("span", {class: "grow"}), button("Cancel", {onClick: close}),
      button("Save", {kind: "primary", ic: "check", onClick: async (e) => {
        e.currentTarget.disabled = true;
        try {
          const rule = build();
          const r = await api("/api/suites", {name: s?.name, title: title.value.trim(), description: description.value.trim(), ...rule});
          close();
          toast(`Saved. ${plural(r.tests, "test")} fit now.`);
          suitesPage();
        } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
      }})],
  });
  refresh();
}
