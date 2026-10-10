// What every page needs to know, loaded once per navigation and shared.
import {api} from "./ui.js";

export const state = {status: null, tests: [], attention: null, timer: null, page: "", query: {}, open: {},
  auth: {enabled: false, user: null, roles: []}};

// Is sign-in on, and who is signed in. (Off: nobody signs in, and everything is allowed, as it always was.)
export async function loadAuth() {
  try { state.auth = await api("/api/auth/status"); } catch (e) { state.auth = {enabled: false, user: null, roles: []}; }
  return state.auth;
}

// May the person at the page do this? "any" (signed in), "tester", "approver" or "admin". The service checks
// again on every request; this only keeps buttons out of sight that would be refused.
export function can(role) {
  if (!state.auth?.enabled) return true;
  const user = state.auth.user;
  if (!user) return false;
  return role === "any" || user.roles.includes("admin") || user.roles.includes(role);
}

export async function loadCommon() {
  const [status, tests, attention] = await Promise.all([
    api("/api/status"), api("/api/tests"), api("/api/attention").catch(() => ({count: 0, items: [], counts: {}, categories: {}})),
  ]);
  Object.assign(state, {status, tests, attention});
  document.dispatchEvent(new CustomEvent("qm:common"));
  return state;
}

export function schedule(fn, ms) {
  clearTimeout(state.timer);
  state.timer = setTimeout(fn, ms);
}

export function testLink(file) {
  return "#/tests/" + encodeURIComponent(file);
}

export function runLink(id) {
  return "#/runs/" + encodeURIComponent(id);
}

export function testName(target) {
  if (!target || target === ".") return "All tests";
  const t = state.tests.find((x) => x.file === target);
  if (t && t.title) return t.title;
  return /\.ya?ml$/.test(target) ? target : `All tests in ${target}`;
}

// A run's name: the name it was given (e.g. a release impact run), else what it ran.
export function runName(run) {
  return run.options?.label || testName(run.target);
}

// The module a run covered: one test's module, the modules in a folder, or all.
export function targetModule(target) {
  const inScope = !target || target === "." ? state.tests : state.tests.filter((t) => t.file === target || t.folder === target || t.folder.startsWith(target + "/"));
  const mods = [...new Set(inScope.map((t) => t.module).filter(Boolean))];
  return mods.length === 1 ? mods[0] : mods.length ? "Several" : "";
}

export function active(run) {
  return run.status === "queued" || run.status === "running";
}

export function envName(status) {
  if (!status) return "";
  if (status.environment_name) return status.environment_name;
  return (status.pod_host || "").split(".")[0].toUpperCase();
}

// Connection state as the service last saw it; "Not checked" until a check has run.
export function connection(status) {
  if (!status || !status.pod_url) return {tone: "bad", label: "Not set up", dot: "bad"};
  if (status.sign_in === "sso") {
    if (status.signed_in_by_hand?.status !== "done") return {tone: "bad", label: "Sign in by hand", dot: "bad"};
  } else if (!status.user || !status.password_set) return {tone: "bad", label: "Sign-in not set", dot: "bad"};
  const c = status.pod_check;
  if (!c) return {tone: "unknown", label: "Not checked yet", dot: "unknown"};
  return c.ok ? {tone: "ok", label: "Connected", dot: "ok"} : {tone: "bad", label: "Unreachable", dot: "bad"};
}
