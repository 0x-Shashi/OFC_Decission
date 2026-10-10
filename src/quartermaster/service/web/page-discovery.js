// Pod discovery (Settings, Clients & environments): read the page names in the pod's Navigator. Off until switched on.
import {api, badge, button, callout, card, h, toast, when} from "./ui.js";
import {settingsPage} from "./page-settings.js";

export async function discoveryCard(hasPod) {
  const d = await api("/api/discovery").catch(() => null);
  if (!d) return null;
  const on = h("input", {type: "checkbox", checked: d.enabled, disabled: !hasPod});
  on.onchange = async () => {
    try { await api("/api/discovery", {enabled: on.checked}); settingsPage(); } catch (err) { toast(err.message); on.checked = !on.checked; }
  };
  const running = d.status === "running";
  const names = d.result?.pages || [];
  if (running) setTimeout(() => { if (location.hash.startsWith("#/settings")) settingsPage(); }, 2500);
  return card({title: "Pod discovery", sub: d.enabled ? "On for this pod" : "Off",
    actions: d.status === "failed" ? badge("last look failed", "danger") : null,
    body: h("div", {class: "stack"},
      h("p", {class: "hint", style: "margin:0"}, "Learns which pages your pod really has, so Release impact can say \"this feature is on a page you have\". It signs in as the test user, opens the Navigator and its folded menus, and writes down the page names. It never saves, submits or deletes anything, only test pods are allowed, and every look is in the audit log."),
      h("div", {}, h("label", {class: "switch"}, on, "Allow pod discovery for this pod")),
      d.error ? callout("warning", "The last look did not work.", d.error) : null,
      h("div", {class: "row"},
        button(running ? "Looking at the pod..." : "Look at the pod now", {kind: "primary", ic: "search", disabled: !d.enabled || running || !hasPod, onClick: async (e) => {
          e.currentTarget.disabled = true;
          try { await api("/api/discovery/run", {}); toast("Looking at the pod. This takes a minute."); settingsPage(); } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
        }}),
        names.length ? button("Remove what was found", {onClick: async () => {
          if (!confirm("Remove the list of pages found? Release impact stops showing them until you look again.")) return;
          try { await api("/api/discovery/forget", {}); settingsPage(); } catch (err) { toast(err.message); }
        }}) : null),
      names.length ? h("div", {}, h("div", {class: "label"}, `${names.length} pages found ${when(d.result.at).toLowerCase()} on ${d.result.pod_host || "the pod"}`),
        h("div", {class: "row", style: "gap:6px", id: "discovered-pages"}, names.slice(0, 120).map((n) => h("span", {class: "tag"}, n))),
        names.length > 120 ? h("div", {class: "hint"}, `and ${names.length - 120} more`) : null)
        : h("div", {class: "hint"}, d.enabled ? "Nothing found yet. Click Look at the pod now." : "Nothing is read until you allow it."))});
}
