// Injected into every page during `qm record`. Captures user clicks and field changes and
// sends each one, with several candidate locators, to Python via window.__qmRecord.
// Oracle-specific handling: Navigator clicks become one "navigate" step, Redwood date fields
// and type-ahead lists become "fill" / "select" steps, and a small toolbar lets the person add
// checks and stop the recording from the browser.
(() => {
  if (window.__qmInstalled) return;
  window.__qmInstalled = true;

  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const short = (s) => (s && s.length <= 60 ? s : "");
  // ADF generates ids like "pt1:_FOr1:1:_FONSr2:0:..." that change between releases.
  const stableId = (id) => id && !id.includes(":") && !/\d{3,}/.test(id);

  const labelOf = (el) => {
    if (el.labels && el.labels.length) return clean(el.labels[0].innerText);
    const by = el.getAttribute("aria-labelledby");
    if (by) {
      const t = by.split(/\s+/).map((i) => document.getElementById(i)).filter(Boolean).map((n) => n.innerText).join(" ");
      if (clean(t)) return clean(t);
    }
    return clean(el.getAttribute("aria-label")) || "";
  };

  const roleOf = (el) => {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "button" || ["submit", "button", "reset"].includes(type)) return "button";
    if (tag === "a" && el.hasAttribute("href")) return "link";
    if (tag === "select") return "combobox";
    if (tag === "textarea" || (tag === "input" && ["", "text", "search", "email", "number", "tel", "date"].includes(type))) return "textbox";
    if (type === "checkbox" || type === "radio") return type;
    return "";
  };

  const nameOf = (el) =>
    clean(el.getAttribute("aria-label")) || labelOf(el) || short(clean(el.innerText)) || clean(el.value) || clean(el.getAttribute("title"));

  // When a label appears more than once on the page (City in the main address and again in tax
  // details), anchor the field to the nearest section heading above it, e.g. "the first City
  // after the Addresses heading". Only kept if it finds exactly this field.
  const scopedXPath = (el) => {
    const lab = el.id && el.labels && el.labels[0];
    const text = lab && clean(lab.innerText);
    if (!text || text.includes("'")) return "";
    const heads = [...document.querySelectorAll("h1, h2, h3, h4")].filter(
      (h) => h.compareDocumentPosition(lab) & Node.DOCUMENT_POSITION_FOLLOWING
    );
    for (let i = heads.length - 1; i >= 0; i--) {
      const ht = clean(heads[i].innerText);
      if (!ht || ht.includes("'")) continue;
      const tag = heads[i].tagName.toLowerCase();
      const xp = `//*[@id=//${tag}[normalize-space(.)='${ht}']/following::label[normalize-space(.)='${text}'][1]/@for]`;
      try {
        const r = document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        if (r.snapshotLength === 1 && r.snapshotItem(0) === el) return xp;
      } catch (e) {
        /* not a usable expression */
      }
    }
    return "";
  };
  const labelRepeats = (text) => [...document.querySelectorAll("label")].filter((l) => clean(l.innerText) === text).length > 1;

  const nthOfSame = (el) => {
    const text = clean(el.innerText);
    if (!text || text.length > 60 || text.includes("'")) return "";
    const tag = el.tagName.toLowerCase();
    const xp = `//${tag}[normalize-space(.)='${text}']`;
    try {
      const r = document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      if (r.snapshotLength < 2) return "";
      const visible = [...Array(r.snapshotLength).keys()].filter((i) => r.snapshotItem(i).getClientRects().length);
      if (visible.length < 2) return "";
      for (let i = 0; i < r.snapshotLength; i++) if (r.snapshotItem(i) === el) return `(${xp})[${i + 1}]`;
    } catch (e) {
      /* not a usable expression */
    }
    return "";
  };

  const candidates = (el) => {
    const out = [];
    const add = (strategy, value) => {
      if (value && !out.some((c) => c.strategy === strategy && c.value === value)) out.push({ strategy, value });
    };
    const isField = ["input", "select", "textarea"].includes(el.tagName.toLowerCase());
    if (isField && labelOf(el) && labelRepeats(labelOf(el))) add("xpath", scopedXPath(el));
    // Same name more than once on the page (e.g. one worker with two work relationships gives two
    // identical name links in the results): pin the one that was used by its position.
    if (!isField) add("xpath", nthOfSame(el));
    const role = roleOf(el);
    const name = nameOf(el);
    // A list field's label also names its dropdown, so for lists the role is the sharper locator.
    if (role === "combobox" && name) add("role", `${role}:${name}`);
    if (isField) add("label", labelOf(el));
    if (role && name) add("role", `${role}:${name}`);
    if (el.dataset && el.dataset.testid) add("test_id", el.dataset.testid);
    if (!isField) add("text", short(clean(el.innerText)));
    if (!isField && !clean(el.innerText)) add("text", clean(el.getAttribute("title")));
    // Ids are a last resort: Oracle generates many that look stable but change between sessions.
    if (!out.length && stableId(el.id) && document.querySelectorAll(`#${CSS.escape(el.id)}`).length === 1) add("css", `#${el.id}`);
    return out;
  };

  const CLICKABLE =
    "button, a, [role=button], [role=link], [role=menuitem], [role=tab], [role=option], " +
    "input[type=submit], input[type=button], input[type=checkbox], input[type=radio]";

  const send = (payload) => {
    try {
      window.__qmRecord(payload);
    } catch (e) {
      /* binding not ready */
    }
  };

  // A text field only fires "change" when it loses focus, which can be *after* the user's
  // next action. Track typing as it happens and flush it before any other recorded event,
  // so steps are saved in the order the user performed them.
  let pending = null;
  const sentValue = new WeakMap(); // element -> last value recorded, to skip duplicate changes
  const sendField = (el) => {
    const ev = fieldEvent(el);
    if (sentValue.get(el) === ev.value) return;
    sentValue.set(el, ev.value);
    send(ev);
  };
  const fieldEvent = (el) => {
    const tag = el.tagName.toLowerCase();
    return {
      kind: tag === "select" ? "select" : "fill",
      intent: labelOf(el) || nameOf(el),
      candidates: candidates(el),
      value: tag === "select" ? clean(el.options[el.selectedIndex]?.text) : el.value,
      url: location.href,
    };
  };
  const flush = () => {
    if (pending) {
      sendField(pending);
      pending = null;
    }
    flushDate();
  };
  const recordable = (el) => {
    const tag = (el.tagName || "").toLowerCase();
    if (!["input", "select", "textarea"].includes(tag)) return false;
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (["checkbox", "radio", "submit", "button"].includes(type)) return false; // captured as clicks
    return type !== "password"; // never record secrets
  };
  const isCombo = (el) => el.tagName && el.tagName.toLowerCase() === "input" && el.getAttribute("role") === "combobox";

  // ---------------------------------------------------------------- type-ahead lists
  // Redwood and ADF lists are inputs with role=combobox: the person types, then clicks a
  // suggestion (role=option) rendered elsewhere in the page. Record one "select" step with
  // what was typed and the suggestion picked, so replay types the same text and picks the same
  // suggestion. If no suggestion is clicked (Enter or Tab), the final field value is used.
  let lastCombo = null;
  const comboTyped = new WeakMap(); // combobox -> text typed, until a suggestion is picked
  // A suggestion of the list last typed into: Redwood renders them as grid rows inside the
  // dropdown named by the field's aria-controls; other lists use role=option.
  const suggestionRow = (target) => {
    if (!lastCombo || !document.contains(lastCombo) || !target.closest) return null;
    const row = target.closest("[role=option], [role=row], tr");
    if (!row) return null;
    const drop = document.getElementById(lastCombo.getAttribute("aria-controls") || "");
    if (drop ? drop.contains(row) : row.getAttribute("role") === "option") return row;
    return null;
  };
  const sendSelect = (el, typed, pick) => {
    flush();
    send({ kind: "select", intent: labelOf(el) || nameOf(el), candidates: candidates(el), value: typed || pick, pick, url: location.href });
    sentValue.set(el, el.value);
  };

  // ---------------------------------------------------------------- Redwood date fields
  // A date is a group of month / day / year spinbuttons that fire keyup and focusout but no
  // input or change events. Record a "fill" of the whole group when focus leaves it changed.
  const dateGroup = (el) => {
    const g = el.closest && el.closest("[role=group]");
    return g && g.querySelector("[role=spinbutton]") ? g : null;
  };
  const dateValue = (g) =>
    [...g.querySelectorAll("[role=spinbutton]")]
      .map((s) => {
        const v = s.getAttribute("aria-valuenow") || "";
        return /year/i.test(s.getAttribute("aria-label") || "") ? v.padStart(4, "0") : v.padStart(2, "0");
      })
      .join("/");
  let dateEl = null;
  let dateStart = "";
  const flushDate = () => {
    if (!dateEl) return;
    const g = dateEl;
    dateEl = null;
    const value = dateValue(g);
    if (value === dateStart) return;
    const label = labelOf(g);
    send({ kind: "fill", intent: label || "date", candidates: label ? [{ strategy: "role", value: `group:${label}` }] : [], value, url: location.href });
  };

  // ---------------------------------------------------------------- Navigator
  // A click on a Navigator item becomes "navigate: Group > Item", which replay opens through
  // the menu. Opening the menu and expanding groups are part of that step, so are not recorded.
  const navPath = (el) => {
    if (!el.closest('[id*="_UISnvr"]')) return null;
    const item = clean(el.innerText) || clean(el.getAttribute("title"));
    if (!item) return null;
    for (let n = el.parentElement, depth = 0; n && depth < 12; n = n.parentElement, depth++) {
      const headers = n.querySelectorAll("div.navmenu-header");
      if (headers.length === 1) return `${clean(headers[0].getAttribute("title"))} > ${item}`;
      if (headers.length > 1) break; // a top-level item, not inside a group
    }
    return item;
  };
  const navChrome = (el) =>
    !!(el.closest("div.navmenu-header") || (el.tagName === "A" && clean(el.getAttribute("title")) === "Navigator") ||
      (el.closest('[id*="_UISnvr"]') && clean(el.innerText) === "Show More"));

  // ---------------------------------------------------------------- checks
  // While "Add check" is on, the next click records a check on the clicked element instead of
  // acting on it: its text (or field value) must match on replay, or, for long text, it must be shown.
  let checking = false;
  const checkEvent = (target) => {
    let el = target;
    for (let i = 0; i < 4 && el && el !== document.body; i++, el = el.parentElement) {
      const cands = candidates(el);
      if (!cands.length) continue;
      const isField = ["input", "textarea", "select"].includes(el.tagName.toLowerCase());
      const g = dateGroup(el);
      const text = g ? clean(g.innerText) : isField ? clean(el.value) : clean(el.innerText);
      const intent = (g && labelOf(g)) || labelOf(el) || short(text) || nameOf(el) || "element";
      const where = g && labelOf(g) ? [{ strategy: "role", value: `group:${labelOf(g)}` }] : cands;
      if (text && text.length <= 120) return { kind: "assert_text", intent, candidates: where, value: text, url: location.href };
      return { kind: "assert_visible", intent, candidates: where, url: location.href };
    }
    return null;
  };
  const inToolbar = (el) => !!(el && el.closest && el.closest("#__qm_toolbar"));
  const swallow = (e) => {
    if (checking && !inToolbar(e.target)) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  };
  ["pointerdown", "mousedown", "pointerup", "mouseup"].forEach((t) => document.addEventListener(t, swallow, true));

  // ---------------------------------------------------------------- listeners
  document.addEventListener(
    "input",
    (e) => {
      const el = e.target;
      if (!recordable(el) || el.tagName.toLowerCase() === "select") return;
      if (isCombo(el)) {
        lastCombo = el;
        comboTyped.set(el, el.value);
        return;
      }
      if (pending && pending !== el) flush();
      pending = el;
    },
    true
  );

  document.addEventListener(
    "change",
    (e) => {
      const el = e.target;
      if (!recordable(el) || isCombo(el)) return;
      if (pending && pending !== el) flush();
      pending = null;
      sendField(el);
    },
    true
  );

  document.addEventListener(
    "focusin",
    (e) => {
      const el = e.target;
      if (isCombo(el)) lastCombo = el;
      const g = dateGroup(el);
      if (g && g !== dateEl) {
        flushDate();
        dateEl = g;
        dateStart = dateValue(g);
      }
    },
    true
  );

  document.addEventListener(
    "focusout",
    (e) => {
      const el = e.target;
      if (dateEl && dateGroup(el) === dateEl && !(e.relatedTarget && dateEl.contains(e.relatedTarget))) {
        setTimeout(flushDate, 0); // after the field has committed the last digit
      }
      if (isCombo(el) && comboTyped.has(el)) {
        // Typed but no suggestion was clicked: wait briefly, the list may still be committing.
        setTimeout(() => {
          if (!comboTyped.has(el)) return;
          const typed = comboTyped.get(el);
          comboTyped.delete(el);
          if (clean(el.value)) sendSelect(el, typed, clean(el.value));
        }, 300);
      }
    },
    true
  );

  document.addEventListener(
    "click",
    (e) => {
      if (inToolbar(e.target)) return;
      if (checking) {
        e.preventDefault();
        e.stopImmediatePropagation();
        flush();
        const ev = checkEvent(e.target);
        if (ev) send(ev);
        setChecking(false);
        return;
      }
      const row = suggestionRow(e.target);
      if (row) {
        const combo = lastCombo;
        const typed = comboTyped.get(combo) || "";
        comboTyped.delete(combo);
        lastCombo = null;
        sendSelect(combo, typed, clean(row.innerText));
        return;
      }
      const el = e.target.closest && e.target.closest(CLICKABLE);
      if (!el) return;
      if (navChrome(el)) return;
      const path = navPath(el);
      if (path) {
        flush();
        send({ kind: "navigate", intent: path, value: path, url: location.href });
        return;
      }
      flush();
      send({ kind: "click", intent: nameOf(el), candidates: candidates(el), url: location.href });
    },
    true
  );

  window.addEventListener("pagehide", flush, true);

  // ---------------------------------------------------------------- toolbar
  let setChecking = (on) => {
    checking = on;
  };
  // Paused from the Quartermaster web page: nothing is recorded until it resumes.
  let setPaused = () => {};
  const buildToolbar = () => {
    if (window.top !== window || document.getElementById("__qm_toolbar")) return;
    const host = document.createElement("div");
    host.id = "__qm_toolbar";
    host.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;";
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        .bar { display:flex; align-items:center; gap:8px; padding:8px 10px; border-radius:10px;
               background:#15202a; color:#fff; font:13px system-ui, sans-serif; box-shadow:0 4px 14px rgba(0,0,0,.3); }
        .dot { width:10px; height:10px; border-radius:50%; background:#e53935; }
        button { font:inherit; border:0; border-radius:6px; padding:5px 10px; cursor:pointer; }
        .check { background:#e1efef; color:#0e6b70; }
        .check.on { background:#f6c343; color:#15202a; }
        .stop { background:#e53935; color:#fff; }
        .msg { opacity:.85; }
      </style>
      <div class="bar"><span class="dot"></span><span class="msg">Recording</span>
        <button class="check" type="button">Add check</button>
        <button class="stop" type="button">Stop recording</button></div>`;
    const msg = root.querySelector(".msg");
    const checkBtn = root.querySelector(".check");
    const dot = root.querySelector(".dot");
    let paused = false;
    setChecking = (on) => {
      if (checking !== on) send({ kind: "checking", on }); // keeps the web page in step
      checking = on;
      checkBtn.classList.toggle("on", on);
      checkBtn.textContent = on ? "Cancel check" : "Add check";
      msg.textContent = on ? "Click the value to check" : paused ? "Paused" : "Recording";
    };
    setPaused = (on) => {
      paused = on;
      if (on) setChecking(false);
      dot.style.background = on ? "#9aa3ad" : "#e53935";
      msg.textContent = on ? "Paused" : "Recording";
      checkBtn.disabled = on;
    };
    checkBtn.addEventListener("click", () => setChecking(!checking));
    root.querySelector(".stop").addEventListener("click", () => {
      flush();
      send({ kind: "stop" });
      msg.textContent = "Stopped. The test is being saved.";
      checkBtn.disabled = true;
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && checking) setChecking(false); }, true);
    document.body.appendChild(host);
  };
  window.__qmSetChecking = (on) => setChecking(!!on);
  window.__qmSetPaused = (on) => setPaused(!!on);
  if (document.body) buildToolbar();
  else document.addEventListener("DOMContentLoaded", buildToolbar);
})();
