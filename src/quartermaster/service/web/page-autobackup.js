// Automatic backups (Settings, General): one backup a day, the newest few kept in the data folder.
import {api, badge, button, card, field, h, input, toast, when} from "./ui.js";
import {settingsPage} from "./page-settings.js";

const size = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

export async function autoBackupCard() {
  const a = await api("/api/backup/auto").catch(() => null);
  if (!a) return null;
  const on = h("input", {type: "checkbox", checked: a.enabled});
  const time = input({type: "time", value: a.time, "aria-label": "Time of day"});
  const keep = input({type: "number", min: "1", max: "30", value: String(a.keep), "aria-label": "How many to keep", style: "width:90px"});
  const evidence = h("input", {type: "checkbox", checked: a.evidence});
  const error = h("div", {class: "meta", role: "alert", style: "color:var(--danger);min-height:18px"});
  const save = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    error.textContent = "";
    try {
      await api("/api/backup/auto", {enabled: on.checked, time: time.value, keep: Number(keep.value), evidence: evidence.checked});
      toast("Saved.");
      settingsPage();
    } catch (err) { error.textContent = err.message; btn.disabled = false; }
  };
  const rows = a.copies.map((c) => h("div", {class: "row", style: "gap:12px;padding:6px 0;border-bottom:1px solid var(--border)"},
    h("div", {class: "grow"}, h("div", {}, when(c.at)), h("div", {class: "meta"}, `${c.name} · ${size(c.bytes)}`)),
    button("Download", {size: "sm", ic: "download", href: `/api/backup/auto/file?name=${encodeURIComponent(c.name)}`}),
    button("Restore", {size: "sm", onClick: async (ev) => {
      const btn = ev.currentTarget;
      if (!confirm(`Restore the backup of ${when(c.at).toLowerCase()}? It replaces your tests, run history, clients and settings with what is in it, when Quartermaster is started again. A copy of what is here now is kept first.`)) return;
      btn.disabled = true;
      try { await api("/api/backup/auto/restore", {name: c.name}); toast("The backup is ready. Close Quartermaster and start it again to finish."); settingsPage(); }
      catch (err) { toast(err.message); btn.disabled = false; }
    }})));
  return card({title: "Automatic backups", sub: a.enabled ? `On: once a day after ${a.time}` : "Off",
    actions: a.last_error ? badge("last one failed", "danger") : null,
    body: h("div", {class: "stack"},
      h("p", {class: "hint", style: "margin:0"}, "The same backup as Download a backup, made for you. If the computer is off at that time, it is made when Quartermaster is next running. Nothing leaves this computer."),
      a.last_error ? h("div", {class: "meta", style: "color:var(--danger)"}, `The last try failed: ${a.last_error} It is tried again within a minute.`) : null,
      h("div", {class: "fields"},
        h("div", {}, h("label", {class: "switch"}, on, "Make a backup every day")),
        field("After", time, "The earliest time of day it is made.", "ab-time"),
        field("Keep the newest", keep, "Older ones are deleted (1 to 30).", "ab-keep"),
        h("div", {}, h("label", {class: "switch"}, evidence, "Also include evidence"), h("div", {class: "hint"}, "Screenshots, videos and documents. Can be large; a backup over 2 GB is refused."))),
      error,
      h("div", {class: "row"}, button("Save", {kind: "primary", onClick: save}),
        button("Back up now", {onClick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          try { await api("/api/backup/auto/run", {}); toast("Backup made."); settingsPage(); } catch (err) { toast(err.message); btn.disabled = false; }
        }})),
      a.copies.length ? h("div", {}, h("div", {class: "label"}, `Saved copies (${a.copies.length})`), h("div", {}, rows),
        h("div", {class: "hint"}, `In ${a.folder}. To restore from a terminal: qm restore <file>.`)) : h("div", {class: "hint"}, `No automatic backup yet. They will be in ${a.folder}.`))});
}
