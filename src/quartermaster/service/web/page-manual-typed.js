// A manual scenario typed here instead of imported from Excel: a name, the product, and the steps
// (what to do, what should happen). It is then like an imported scenario: by hand, Prepare, Run.
import {api, button, callout, drawer, field, h, input, toast} from "./ui.js";
import {manualPage, openScenario} from "./page-manual.js";

export function openTypedScenario(s, known = {modules: [], products: []}) {
  const name = input({value: s?.title || "", maxlength: "200", placeholder: "e.g. Update my home address"});
  const module = input({value: s?.module || "", maxlength: "60", placeholder: "e.g. HCM", list: "typed-modules"});
  const product = input({value: s?.product || "", maxlength: "80", placeholder: "e.g. Global Human Resources", list: "typed-products"});
  const about = h("textarea", {class: "input", rows: "2", maxlength: "2000", placeholder: "Optional: what this checks and for whom"});
  about.value = s?.description || "";
  const fields = h("textarea", {class: "input", rows: "3", placeholder: "Optional, one per line, e.g.\nSalary Amount\nSalary Basis"});
  fields.value = (s?.fields || []).join("\n");

  const list = h("ol", {class: "stack", style: "gap:10px;margin:0;padding:0;list-style:none"});
  const renumber = () => [...list.children].forEach((li, i) => { li.querySelector(".step-no").textContent = `${i + 1}.`; });
  const addRow = (action = "", expected = "", after = null) => {
    const what = h("textarea", {class: "input", rows: "2", maxlength: "2000", "aria-label": "What to do",
      placeholder: "What to do, e.g. Click Me, then Personal Information"});
    what.value = action;
    const should = input({value: expected, maxlength: "2000", "aria-label": "What should happen", placeholder: "What should happen (optional)"});
    // Pasting several lines (steps copied from a document) makes one step per line.
    what.addEventListener("paste", (e) => {
      const text = e.clipboardData?.getData("text") || "";
      const lines = text.split(/\r?\n/).map((l) => l.replace(/^\s*(\d+[.)]|[-*•])\s*/, "").trim()).filter(Boolean);
      if (lines.length < 2) return;
      e.preventDefault();
      what.value = lines[0];
      let at = li;
      for (const line of lines.slice(1)) at = addRow(line, "", at);
      renumber();
    });
    const li = h("li", {class: "row", style: "gap:8px;align-items:flex-start;flex-wrap:nowrap"},
      h("span", {class: "step-no meta", style: "width:24px;padding-top:9px;text-align:right"}),
      h("div", {class: "grow stack", style: "gap:6px;min-width:0"}, what, should),
      button("", {size: "sm", kind: "ghost", ic: "x", title: "Remove this step", onClick: () => {
        if (list.children.length > 1) { li.remove(); renumber(); } else { what.value = ""; should.value = ""; }
      }}));
    li._values = () => ({action: what.value.trim(), expected: should.value.trim()});
    if (after) after.after(li); else list.append(li);
    renumber();
    return li;
  };
  const steps = s ? s.cases.flatMap((c) => c.steps) : [];
  if (steps.length) steps.forEach((st) => addRow(st.action, st.expected)); else addRow();

  drawer({
    title: s ? "Change scenario" : "New manual scenario",
    sub: s ? s.title : "Type the steps here instead of importing an Excel workbook.",
    body: () => [
      h("datalist", {id: "typed-modules"}, known.modules.map((m) => h("option", {value: m}))),
      h("datalist", {id: "typed-products"}, known.products.map((p) => h("option", {value: p}))),
      field("Name", name, "", "typed-name"),
      h("div", {class: "grid g-2", style: "gap:12px"}, field("Module", module, "", "typed-module"), field("Product", product, "", "typed-product")),
      field("What it checks", about, "", "typed-about"),
      h("div", {}, h("div", {class: "label"}, "Steps"),
        h("p", {class: "hint", style: "margin:0 0 8px"}, "One action per step, the way you would tell a new tester. Write the values to type (\"Enter 10 Main Street in Address Line 1\"); the AI never makes values up. Pasting several lines makes one step per line."),
        list,
        h("div", {style: "margin-top:8px"}, button("Add step", {size: "sm", ic: "plus", onClick: () => addRow().querySelector("textarea").focus()}))),
      field("Fields to check", fields, "Optional. Prepare checks each one is shown at the end.", "typed-fields"),
      s?.automated ? callout("warning", "This scenario already plays by itself.", "Changing the steps does not change what it plays. After saving, use Prepare again or Do it by hand so it follows the new steps.") : null,
    ],
    foot: (close) => [
      s ? button("Delete", {kind: "ghost", onClick: async () => {
        if (!confirm(`Delete “${s.title}”? Its past runs stay in Runs.`)) return;
        try { await api("/api/manual/typed/delete", {id: s.id}); close(); toast("Scenario deleted."); manualPage(); } catch (err) { toast(err.message); }
      }}) : null,
      h("span", {class: "grow"}),
      button("Cancel", {onClick: close}),
      button("Save", {kind: "primary", ic: "check", onClick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        try {
          const saved = await api("/api/manual/typed", {id: s?.id, title: name.value, module: module.value, product: product.value,
            description: about.value, steps: [...list.children].map((li) => li._values()),
            fields: fields.value.split("\n").map((f) => f.trim()).filter(Boolean)});
          close();
          toast(s ? "Saved." : "Scenario added. Do it by hand or Prepare it like any other.");
          if (location.hash.startsWith("#/tests?view=manual")) await manualPage(); else location.hash = "#/tests?view=manual";
          openScenario(saved.id);
        } catch (err) { toast(err.message); btn.disabled = false; }
      }}),
    ],
  });
  name.focus();
}
