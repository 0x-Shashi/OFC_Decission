// Test data: data sets (values that belong to a pod) and values a test makes fresh for every run.
import {api, badge, button, callout, card, disclose, emptyState, h, toast} from "./ui.js";
import {state, testLink} from "./state.js";
import {show} from "./app.js";
import {openSetEditor} from "./page-dataedit.js";

const EXAMPLE = `dataset: hcm-basics
title: Names on the pods
values:                         # used on every pod unless a pod below says otherwise
  business_unit: US1 Business Unit
pods:                           # by the environment's name, or by its kind (DEV, TEST, STAGE)
  DEV2: {business_unit: Vision Operations}
  STAGE: {business_unit: US1 Stage BU}
`;

const USE = `data_sets: [hcm-basics]       # in a test: its values are used as \${business_unit}
generate:                       # made fresh for every run
  invoice_no: {unique: 6, prefix: "INV-"}
  start_date: {date: today, plus_days: 30, format: "%Y-%m-%d"}
  amount: {number: [100, 999]}
  currency: {choice: [USD, EUR, GBP]}
`;

export async function dataPage() {
  const d = await api("/api/data");
  if (state.page !== "data") return;
  const copy = (text) => navigator.clipboard.writeText(text).then(() => toast("Copied"), () => toast("Could not copy"));
  const cell = (v) => v === null ? h("span", {class: "badge danger"}, "no value") : h("code", {}, v);
  const setCard = (s) => card({
    title: s.title || s.name,
    sub: s.title ? s.name : s.file,
    actions: h("div", {class: "row", style: "gap:8px"}, button("Edit values", {size: "sm", ic: "note", onClick: () => openSetEditor(d, s, () => dataPage())}), badge(s.used_by.length ? `used by ${s.used_by.length} ${s.used_by.length === 1 ? "test" : "tests"}` : "not used yet", s.used_by.length ? "info" : "neutral")),
    body: h("div", {class: "stack", style: "gap:10px"},
      s.description ? h("p", {class: "hint", style: "margin:0"}, s.description) : null,
      s.gaps.length ? callout("warning", "Some pods have no value.",
        s.gaps.map((g) => `${g.name} on ${g.pods.join(", ")}`).join("; ") + ". A test that uses it stops there with a message, not a failed step.") : null,
      d.pods.length && s.names.length ? h("div", {class: "table-wrap"}, h("table", {class: "table", "aria-label": `Values of ${s.name} on each pod`},
        h("thead", {}, h("tr", {}, h("th", {}, "Pod"), s.names.map((n) => h("th", {}, n)))),
        h("tbody", {}, s.table.map((r) => h("tr", {},
          h("td", {}, h("div", {class: "primary-cell"}, r.pod), h("div", {class: "sub"}, r.kind)),
          s.names.map((n) => h("td", {}, cell(r.values[n])))))))) :
        h("p", {class: "hint", style: "margin:0"}, d.pods.length ? "This set has no values yet." : "Add a pod in Settings to see what each pod gets."),
      s.used_by.length ? h("div", {}, h("div", {class: "label"}, "Used by"),
        h("div", {class: "row", style: "gap:8px"}, s.used_by.map((t) => h("a", {href: testLink(t.file)}, t.title)))) : null,
      disclose("The file", h("div", {},
        h("div", {class: "row", style: "justify-content:space-between;margin-bottom:8px"},
          h("span", {class: "meta"}, `${s.file}. Change it with Edit values, or in any text editor: every test that uses it follows.`),
          button("Copy", {size: "sm", ic: "copy", onClick: () => copy(s.yaml)})),
        h("pre", {class: "block", style: "max-height:360px"}, s.yaml)))),
  });
  const madeCard = () => card({
    title: "Values made fresh for every run",
    sub: `Shown for a made-up run (${d.sample_run}). A real run has its own id, so its own values.`,
    body: h("div", {class: "stack", style: "gap:14px"}, d.generated.map((g) => h("div", {},
      h("div", {class: "row", style: "gap:8px"}, h("a", {href: testLink(g.file)}, g.title)),
      h("div", {class: "table-wrap"}, h("table", {class: "table", "aria-label": `Values made for ${g.title}`},
        h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "How it is made"), h("th", {}, "For this sample"))),
        h("tbody", {}, g.values.map((v) => h("tr", {}, h("td", {}, h("code", {}, v.name)), h("td", {}, v.rule), h("td", {}, h("code", {}, v.sample)))))))))),
  });
  show([{label: "Test data"}], h("div", {class: "stack", style: "max-width:900px"},
    h("div", {class: "page-head"}, h("div", {},
      h("h1", {}, "Test data"),
      h("p", {class: "lead"}, "The names each pod uses, and the values a test makes fresh for every run, so a test runs on any pod and never uses the same invoice number twice.")),
      button("New data set", {kind: "primary", ic: "plus", onClick: () => openSetEditor(d, null, () => dataPage())})),
    d.missing.length ? callout("danger", "A test uses a data set that does not exist.",
      d.missing.map((m) => `'${m.name}' (used by ${m.used_by.map((t) => t.title).join(", ")})`).join("; ")) : null,
    d.problems.length ? callout("warning", "Some files in the data folder cannot be used.",
      h("ul", {style: "margin:4px 0 0;padding-left:20px"}, d.problems.map((p) => h("li", {}, `${p.file}: ${p.problem}`)))) : null,
    d.sets.length ? d.sets.map(setCard) : card({body: emptyState({ic: "server", title: "No data sets yet",
      text: `Put a YAML file in the ${d.folder} folder. Here is a small one to start from.`,
      actions: button("Copy an example", {size: "sm", ic: "copy", onClick: () => copy(EXAMPLE)})})}),
    d.generated.length ? madeCard() : null,
    card({title: "How to write and use test data", body: h("div", {class: "stack", style: "gap:10px"},
      h("p", {class: "hint", style: "margin:0"}, `1. Save a data set such as hcm-basics.yaml in ${d.folder}:`),
      h("pre", {class: "block"}, EXAMPLE),
      h("p", {class: "hint", style: "margin:0"}, "2. In a test, use it, and ask for values made fresh for every run:"),
      h("pre", {class: "block"}, USE),
      h("p", {class: "hint", style: "margin:0"}, "The test's own `data:` wins over a data set, and its own `pods:` wins over everything. A pod is matched by its name (as written in Settings) or its kind. Never write a password here: use ${env:NAME} for anything secret."))})));
}
