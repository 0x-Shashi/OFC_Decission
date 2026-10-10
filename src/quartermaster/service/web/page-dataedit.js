// Edit the values of a data set in a table, so nobody has to open a text editor.
// Rows are names (business_unit, ledger), columns are "Every pod" and then each pod.
import {api, button, callout, drawer, h, input, toast} from "./ui.js";

export function openSetEditor(d, set, reload) {
  const isNew = !set;
  const podNames = [...new Set([...d.pods.map((p) => p.name), ...(set ? set.pod_blocks.map((b) => b.pod) : [])])];
  const columns = ["", ...podNames]; // "" is the default for every pod
  const start = {"": set ? set.defaults : {}};
  for (const b of set ? set.pod_blocks : []) start[b.pod] = b.values;
  const names = set ? [...set.names] : [""];
  const cells = new Map(); // "name|pod" -> input
  const nameInputs = [];
  const nameBox = isNew ? input({"aria-label": "Name of the data set", placeholder: "for example hcm-basics", autofocus: true}) : null;
  const titleBox = input({"aria-label": "Title", value: set ? set.title : "", placeholder: "for example Names on the pods"});
  const error = h("div", {});
  const grid = h("tbody", {});
  const addRow = (name) => {
    const nameBox2 = input({style: "min-width:150px", "aria-label": "Name of a value", value: name, placeholder: "business_unit"});
    nameInputs.push(nameBox2);
    const boxes = [];
    nameBox2.addEventListener("input", () => boxes.forEach(([pod, box]) => box.setAttribute("aria-label", `${nameBox2.value || "new value"} on ${pod || "every pod"}`)));
    const tds = columns.map((pod) => {
      const box = input({style: "min-width:150px", "aria-label": `${name || "new value"} on ${pod || "every pod"}`, value: (start[pod] || {})[name] || ""});
      cells.set(nameInputs.length - 1 + "|" + pod, box);
      boxes.push([pod, box]);
      return h("td", {}, box);
    });
    grid.append(h("tr", {}, h("td", {}, nameBox2), tds));
  };
  names.forEach(addRow);
  const collect = () => {
    const values = {}, pods = {};
    nameInputs.forEach((n, i) => {
      const key = n.value.trim();
      if (!key) return;
      columns.forEach((pod) => {
        const v = cells.get(i + "|" + pod).value.trim();
        if (!v) return;
        if (pod === "") values[key] = v;
        else (pods[pod] ||= {})[key] = v;
      });
    });
    return {values, pods};
  };
  const save = async (close) => {
    error.replaceChildren();
    try {
      const body = {name: set ? set.name : nameBox.value.trim(), title: titleBox.value.trim(), description: set ? set.description : "", ...collect()};
      await api("/api/data/save", body);
      close(); toast("Saved"); reload();
    } catch (e) { error.replaceChildren(callout("danger", "Could not save.", e.message)); }
  };
  drawer({
    title: isNew ? "New data set" : `Edit ${set.title || set.name}`,
    sub: "Leave a cell empty for no value. A test that needs a value a pod does not have stops with a clear message.",
    body: () => h("div", {class: "stack", style: "gap:12px"},
      isNew ? h("div", {}, h("label", {class: "label"}, "Name (letters, digits, dashes)"), nameBox) : null,
      h("div", {}, h("label", {class: "label"}, "Title"), titleBox),
      h("div", {class: "table-wrap"}, h("table", {class: "table", "aria-label": "Values of the data set"},
        h("thead", {}, h("tr", {}, h("th", {}, "Value name"), columns.map((p) => h("th", {}, p || "Every pod")))), grid)),
      h("div", {}, button("Add a value", {size: "sm", ic: "plus", onClick: () => addRow("")})),
      d.pods.length ? null : h("p", {class: "hint", style: "margin:0"}, "No pods yet: add them in Settings to get a column for each."),
      error),
    foot: (close) => h("div", {class: "row"}, button("Save", {kind: "primary", onClick: () => save(close)}), button("Cancel", {onClick: close})),
  });
}
