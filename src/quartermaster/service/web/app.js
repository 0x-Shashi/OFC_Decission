// Quartermaster web UI: shell and routing. Plain JavaScript modules, no build step.
import {button, h, hideTip, icon, remember, skeleton} from "./ui.js";
import {checkPod, environmentButton, openPalette, openRunDrawer} from "./components.js";
import {can, loadAuth, state} from "./state.js";
import {overviewPage} from "./page-overview.js";
import {runPage, runsPage} from "./page-runs.js";
import {testPage, testsPage} from "./page-tests.js";
import {attentionPage} from "./page-attention.js";
import {libraryPage} from "./page-library.js";
import {dataPage} from "./page-data.js";
import {suitesPage} from "./page-suites.js";
import {packsPage} from "./page-packs.js";
import {recordPage} from "./page-record.js";
import {settingsPage} from "./page-settings.js";
import {impactPage} from "./page-impact.js";
import {manualPage} from "./page-manual.js";
import {reviewPage} from "./page-review.js";
import {schedulesPage} from "./page-schedules.js";
import {auditPage} from "./page-audit.js";
import {setupPage} from "./page-setup.js";
import {manualRunPage} from "./page-manual-run.js";
import {accountPage, loginPage, logout, passwordPage} from "./page-login.js";

const NAV = [
  {section: "Testing"},
  {id: "", label: "Overview", ic: "overview"},
  {id: "runs", label: "Runs", ic: "runs"},
  {id: "tests", label: "Tests", ic: "tests"},
  {id: "suites", label: "Suites", ic: "folder"},
  {id: "packs", label: "Test library", ic: "download"},
  {id: "library", label: "Shared steps", ic: "layers"},
  {id: "data", label: "Test data", ic: "server"},
  {id: "impact", label: "Release impact", ic: "target"},
  {id: "attention", label: "Needs attention", ic: "attention"},
  {id: "schedules", label: "Schedules", ic: "clock"},
  {section: "Create"},
  {id: "record", label: "Record a test", ic: "record", need: "tester"},
  {section: "System"},
  {id: "settings", label: "Settings", ic: "settings", need: "admin"},
  {id: "audit", label: "Audit log", ic: "file"},
];

const root = document.documentElement;
const main = document.getElementById("main");

// ------------------------------------------------------------------ theme

export function setTheme(theme) {
  if (!theme || theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
  remember("theme", theme === "system" ? null : theme);
  drawTheme();
}

export function currentTheme() {
  return root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

export function toggleTheme() {
  setTheme(currentTheme() === "dark" ? "light" : "dark");
}

function drawTheme() {
  const dark = currentTheme() === "dark";
  const b = document.getElementById("theme-btn");
  b.replaceChildren(icon(dark ? "sun" : "moon"));
  b.title = dark ? "Switch to light theme" : "Switch to dark theme";
  b.setAttribute("aria-label", b.title);
}

// ------------------------------------------------------------------ shell

function drawNav() {
  const count = state.attention?.count || 0;
  const user = state.auth?.enabled ? state.auth.user : null;
  const visible = NAV.filter((n) => !n.need || can(n.need));
  // a section whose items are all hidden is hidden too
  const shown = visible.filter((n, i) => !n.section || (visible[i + 1] && !visible[i + 1].section));
  const account = user ? [{section: "Account"}, {id: "account", label: user.full_name, ic: "lock"}] : [];
  document.getElementById("new-run-btn").hidden = !can("tester");
  document.getElementById("nav").replaceChildren(...[...shown, ...account].map((n) => n.section
    ? h("div", {class: "nav-label"}, n.section)
    : h("a", {href: "#/" + n.id, "aria-current": state.page === n.id ? "page" : null, title: n.label},
      icon(n.ic), h("span", {class: "label"}, n.label),
      n.id === "attention" && count ? h("span", {class: "count", "aria-label": `, ${count} items`}, count) : null)),
    ...(user ? [h("a", {href: "#/login", title: "Sign out", onclick: (e) => { e.preventDefault(); logout(); }}, icon("x"), h("span", {class: "label"}, "Sign out"))] : []));
  document.getElementById("env").replaceChildren(state.status ? environmentButton() : h("div", {class: "skel", style: "height:58px"}));
}

function initShell() {
  const collapse = document.getElementById("collapse-btn");
  const drawCollapse = () => {
    const rail = root.classList.contains("rail");
    collapse.replaceChildren(icon("sidebar"), h("span", {class: "label"}, "Collapse sidebar"));
    collapse.title = rail ? "Expand sidebar" : "Collapse sidebar";
    collapse.setAttribute("aria-label", collapse.title);
    collapse.setAttribute("aria-expanded", String(!rail));
  };
  collapse.onclick = () => { root.classList.toggle("rail"); remember("rail", root.classList.contains("rail")); drawCollapse(); };
  drawCollapse();
  const menu = document.getElementById("menu-btn");
  menu.append(icon("menu"));
  menu.onclick = () => document.body.classList.toggle("menu-open");
  document.getElementById("side-scrim").onclick = () => document.body.classList.remove("menu-open");
  const search = document.getElementById("search-btn");
  search.append(icon("search"), h("span", {}, "Search or run a command"), h("kbd", {}, "Ctrl K"));
  search.onclick = () => openPalette(toggleTheme);
  document.getElementById("theme-btn").onclick = toggleTheme;
  drawTheme();
  const newRun = button("New run", {kind: "primary", ic: "plus", onClick: () => openRunDrawer(), title: "New run (N)"});
  newRun.id = "new-run-btn";
  newRun.classList.add("new-run");
  document.getElementById("new-run-btn").replaceWith(newRun);
  document.addEventListener("keydown", (e) => {
    const el = document.activeElement;
    const typing = /INPUT|TEXTAREA|SELECT/.test(el?.tagName || "") || el?.isContentEditable;
    const layered = document.querySelector(".scrim, .viewer, .popover, .palette");
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); if (!layered) openPalette(toggleTheme); return; }
    if (typing || layered || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "/") { e.preventDefault(); openPalette(toggleTheme); }
    else if (e.key.toLowerCase() === "n") { e.preventDefault(); openRunDrawer(); }
  });
  document.addEventListener("qm:common", drawNav);
  // Check the pod once when Quartermaster opens, so the connection shown is a real one.
  document.addEventListener("qm:common", function first() {
    document.removeEventListener("qm:common", first);
    if (state.status?.pod_url && !state.status.pod_check) checkPod({quiet: true}).catch(() => {});
  });
  // A live redraw never throws away what the reader has started typing or choosing on the page.
  main.addEventListener("input", () => { main.dataset.edited = location.hash; });
  main.addEventListener("change", () => { main.dataset.edited = location.hash; });
  document.addEventListener("qm:refresh", () => {
    if (!["", "settings"].includes(state.page) || document.querySelector(".scrim, .popover")) return;
    if (main.dataset.edited === location.hash) return;
    state.liveRedraw = location.hash; // checked again when the page is about to be drawn
    route();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawTheme);
}

// ------------------------------------------------------------------ pages

// Put a page on screen. Crumbs are [{label, href?}]; the last one is the current page.
export function show(crumbs, ...children) {
  const live = state.liveRedraw === location.hash;
  state.liveRedraw = null;
  if (live && main.dataset.edited === location.hash) return; // edited while the redraw was loading
  document.getElementById("crumbs").replaceChildren(...crumbs.flatMap((c, i) => [
    i ? icon("right") : null,
    c.href ? h("a", {href: c.href}, c.label) : h("span", {"aria-current": "page"}, c.label),
  ].filter(Boolean)));
  document.title = `${crumbs[crumbs.length - 1].label} · Quartermaster`;
  const fresh = main.dataset.page !== location.hash;
  main.dataset.page = location.hash;
  main.dataset.edited = ""; // a page drawn anew has no edits yet
  const y = scrollY;
  main.replaceChildren(h("div", {}, children));
  if (fresh) {
    main.classList.remove("enter");
    void main.offsetWidth;
    main.classList.add("enter");
    main.focus({preventScroll: true});
  } else {
    scrollTo(0, y); // a live refresh keeps the reader where they were
  }
}

function loading() {
  main.dataset.page = "";
  main.replaceChildren(h("div", {"aria-busy": "true", "aria-label": "Loading"},
    h("div", {class: "skel", style: "height:28px;width:220px;margin-bottom:24px"}),
    h("div", {class: "grid g-4"}, [1, 2, 3, 4].map(() => h("div", {class: "card", style: "padding:20px"}, skeleton(3)))),
    h("div", {class: "card section", style: "padding:20px"}, skeleton(6))));
}

export async function route() {
  clearTimeout(state.timer);
  hideTip();
  document.querySelector(".popover")?.remove();
  document.body.classList.remove("menu-open");
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  const [page, ...rest] = path.split("/");
  const arg = decodeURIComponent(rest.join("/"));
  if (state.auth?.enabled) { // sign-in is on: sign in first, and choose a new password when one is owed
    const me = state.auth.user;
    if (!me && page !== "login") { location.hash = "#/login"; return; }
    if (me?.must_change && page !== "password") { location.hash = "#/password"; return; }
  }
  const changed = state.page !== page || arg !== state.arg;
  Object.assign(state, {page, arg, query: Object.fromEntries(new URLSearchParams(query || ""))});
  drawNav();
  if (changed) loading();
  try {
    if (page === "runs" && arg) await runPage(arg);
    else if (page === "runs") await runsPage();
    else if (page === "tests" && arg) await testPage(arg);
    else if (page === "tests" && state.query.view === "manual") await manualPage();
    else if (page === "tests" && state.query.view === "review") await reviewPage();
    else if (page === "tests") await testsPage();
    else if (page === "suites") await suitesPage();
    else if (page === "packs") await packsPage();
    else if (page === "library") await libraryPage();
    else if (page === "data") await dataPage();
    else if (page === "attention") await attentionPage();
    else if (page === "impact") await impactPage();
    else if (page === "record") await recordPage();
    else if (page === "manual-run") await manualRunPage();
    else if (page === "settings") await settingsPage();
    else if (page === "schedules") await schedulesPage();
    else if (page === "audit") await auditPage();
    else if (page === "setup") await setupPage();
    else if (page === "login") await loginPage();
    else if (page === "password") await passwordPage(Boolean(state.auth?.user?.must_change));
    else if (page === "account") await accountPage();
    else await overviewPage();
  } catch (e) {
    show([{label: "Problem"}], h("div", {class: "callout danger", role: "alert"}, icon("attention"),
      h("div", {}, h("strong", {}, "Something went wrong. "), e.message, " ",
        h("a", {href: location.hash, onclick: (ev) => { ev.preventDefault(); route(); }}, "Try again"))));
  }
}

initShell();
window.addEventListener("hashchange", route);
loadAuth().then(route);
