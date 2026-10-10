// Overview: the Oracle release testing control centre.
import {api, button, callout, card, h, plural, statusBadge, toast, when} from "./ui.js";
import {
  checkPod, metric, moduleCoverage, openRunDrawer, recentActivity, releaseComparison, releaseReadiness, stackedBar,
} from "./components.js";
import {active, connection, envName, loadCommon, runLink, runName, schedule, state} from "./state.js";
import {show} from "./app.js";

export async function overviewPage() {
  const [, dash, runs] = await Promise.all([loadCommon(), api("/api/dashboard"), api("/api/runs")]);
  if (state.page !== "") return;
  const st = state.status;
  if (!st.pod_url && !st.set_up_in) { // nothing set up yet: the setup guide first
    location.replace("#/setup");
    return;
  }
  const att = state.attention;
  const latestSummary = runs.find((r) => r.summary_url);

  const setRelease = async (release) => {
    try {
      await api("/api/settings", {release});
      toast(release ? `Release set to ${release}.` : "Release cleared.");
      overviewPage();
    } catch (e) { toast(e.message); }
  };

  const SHORT = {assertion: ["failed check", "failed checks"], missing_element: ["item not found", "items not found"],
    service_call: ["service call failed", "service calls failed"],
    timeout: ["timeout", "timeouts"], authentication: ["sign-in problem", "sign-in problems"], test_data: ["test data not ready", "test data not ready"], failure: ["failure", "failures"],
    could_not_run: ["run that could not start", "runs that could not start"], cleanup: ["cleanup not finished", "cleanups not finished"], ui_change: ["screen change", "screen changes"],
    unreadable: ["unreadable file", "unreadable files"]};
  const topCats = Object.entries(att.counts || {}).sort((a, b) => b[1] - a[1]).slice(0, 2)
    .map(([k, n]) => plural(n, ...(SHORT[k] || ["item", "items"]))).join(" · ");

  const metrics = h("div", {class: "grid g-4"},
    metric({ic: "shield", label: "Pass rate", accent: true,
      value: dash.pass_rate === null ? "–" : dash.pass_rate, unit: dash.pass_rate === null ? "" : "%",
      bar: dash.tested ? stackedBar([{n: dash.passing, cls: "fill-success", label: "Passed"}, {n: dash.failing, cls: "fill-danger", label: "Failed"}],
        `${dash.passing} passed, ${dash.failing} failed on their last run`) : null,
      foot: dash.tested ? `${dash.passing} of ${plural(dash.tested, "test")} passed on their last run` : "No test has run yet"}),
    metric({ic: "layers", label: "Test coverage", href: "#/tests",
      value: dash.coverage === null ? "–" : dash.coverage, unit: dash.coverage === null ? "" : "%",
      bar: dash.tests ? stackedBar([{n: dash.tested, cls: "fill-primary", label: "Run at least once"}, {n: dash.never_run, cls: "fill-neutral", label: "Never run"}],
        `${dash.tested} of ${dash.tests} tests run at least once`) : null,
      foot: `${dash.tested} of ${plural(dash.tests, "test")} run · ${dash.never_run} never run`}),
    metric({ic: "attention", label: "Needs attention", href: "#/attention", value: att.count,
      foot: att.count ? topCats : "Nothing needs attention"}),
    metric({ic: "calendar", label: "Runs this week", href: "#/runs", value: dash.runs_this_week,
      foot: `${plural(dash.tests_run_this_week, "test")} run · ${plural(dash.documents, "evidence document")} in all`}));

  const c = connection(st);
  const last = dash.last_run;
  const envCard = card({title: "Environment", sub: "Where tests run",
    actions: button("", {ic: "refresh", size: "sm", title: "Check the connection now", onClick: async (e) => {
      e.currentTarget.disabled = true;
      await checkPod().catch((err) => toast(err.message));
      overviewPage();
    }}),
    body: h("dl", {class: "kv"},
      h("dt", {}, "Pod"), h("dd", {}, h("div", {style: "font-weight:500"}, envName(st) || "Not set"), h("div", {class: "meta"}, st.pod_host || "QM_FUSION_URL is not set")),
      h("dt", {}, "Oracle release"), h("dd", {}, st.release ? h("span", {class: "release"}, st.release) : h("a", {href: "#/settings"}, "Set the release")),
      h("dt", {}, "Connection"), h("dd", {}, h("span", {class: "row", style: "gap:6px"}, h("span", {class: `dot ${c.dot}`}), c.label),
        st.pod_check ? h("div", {class: "meta"}, `Checked ${when(st.pod_check.checked_at).toLowerCase()}`) : null),
      h("dt", {}, "Signs in as"), h("dd", {}, st.user || "Not set"),
      h("dt", {}, "Last execution"), h("dd", {}, last ? h("a", {href: runLink(last.id), class: "row", style: "gap:6px"}, statusBadge(last.status), when(last.at)) : "None yet"))});

  const stable = dash.stability || {};
  const stabilityCard = !stable.runs ? null : card({title: "Stability", sub: `Test runs in the last ${stable.days} days`,
    body: h("div", {class: "stack", style: "gap:10px"},
      h("div", {class: "row", style: "gap:10px;align-items:baseline"},
        h("span", {style: "font-size:28px;font-weight:600"}, `${stable.rate}%`),
        h("span", {class: "meta"}, `${stable.retried} of ${plural(stable.runs, "test run")} only passed after a step was tried again. Aim for under 2%.`)),
      stable.flaky_tests.length ? h("div", {}, h("div", {class: "label"}, `Flaky tests (${stable.flaky_count})`),
        h("ul", {style: "margin:4px 0 0;padding-left:18px"}, stable.flaky_tests.map((t) => h("li", {},
          t.file ? h("a", {href: `#/tests/${encodeURIComponent(t.file)}`}, t.title) : t.title,
          h("span", {class: "meta"}, ` · needed a retry in ${t.flaky_runs} of its last ${t.runs} runs`))))) : h("div", {class: "meta"}, "No flaky tests.")) });

  const firstRun = !runs.length ? callout("info", "Get started.", [
    st.ready ? "The pod is set up. " : h("span", {}, "First finish the pod's sign-in (", h("a", {href: "#/settings"}, "Settings"), "). "),
    state.tests.length ? `${plural(state.tests.length, "test")} ready: ` : "Record your first test, ",
    state.tests.length ? h("a", {href: "#", onclick: (e) => { e.preventDefault(); openRunDrawer("."); }}, "run them all") : h("a", {href: "#/record"}, "Record a test"),
    ". Every run makes a Word evidence document per test and a summary.",
  ]) : null;

  show([{label: "Overview"}],
    h("div", {class: "page-head"},
      h("div", {}, h("h1", {}, "Overview"),
        h("p", {class: "lead"}, "Oracle Fusion release testing", envName(st) ? ` on ${envName(st)}` : "", st.release ? ` · Release ${st.release}` : "")),
      h("div", {class: "row"},
        latestSummary ? button("Latest summary", {ic: "download", href: latestSummary.summary_url, title: `Summary of ${runName(latestSummary)}`}) : null,
        button("Release impact", {ic: "target", href: "#/impact", title: "Which tests a new Oracle update puts at risk"}),
        button("Run all tests", {kind: "primary", ic: "runs", disabled: !st.ready, onClick: () => openRunDrawer(".")}))),
    firstRun ? h("div", {style: "margin-bottom:16px"}, firstRun) : null,
    metrics,
    h("div", {class: "grid g-main section"}, releaseReadiness(dash.readiness, setRelease, dash.approval, overviewPage), envCard),
    releaseComparison(dash.releases) ? h("div", {class: "section"}, releaseComparison(dash.releases)) : null,
    stabilityCard ? h("div", {class: "section"}, stabilityCard) : null,
    h("div", {class: "grid g-main section"}, recentActivity(dash.activity), moduleCoverage(dash.modules)));

  if (runs.some(active)) schedule(overviewPage, 4000);
}

