"""What the overview, test pages and "Needs attention" show, worked out from the run history.

Every finished run points at its suite.json; each suite entry points at a run.json. Nothing here
is stored twice and nothing is estimated: every number is counted from that evidence (cached
until the file changes). Where the evidence does not say something (a run without an Oracle
release, say), the result says so rather than guessing.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from quartermaster.dsl.library import LibraryError, expand, library_dir_for, read_groups
from quartermaster.evidence.document import plain_error
from quartermaster.service.triage import likely_cause

_cache: dict[str, tuple[float, Any]] = {}
PASSING = ("passed", "healed")


def read_json(path: Path) -> Any:
    """A JSON file, re-read only when it changes (suite and run records never change once written)."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = str(path)
    hit = _cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    _cache[key] = (mtime, data)
    return data


def suite_of(run: dict[str, Any]) -> dict[str, Any] | None:
    if not run.get("suite_dir"):
        return None
    suite = read_json(Path(run["suite_dir"]) / "suite.json")
    return suite if isinstance(suite, dict) else None


def test_results(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per test per finished run, newest run first, with the run's Oracle release."""
    rows = []
    for run in runs:
        suite = suite_of(run)
        if suite is None:
            continue
        for entry in suite.get("runs", []):
            rows.append(
                {
                    **entry,
                    "service_run_id": run["id"],
                    "at": run.get("started_at") or run["created_at"],
                    "release": str(suite.get("release") or run.get("options", {}).get("release") or ""),
                    "environment_url": suite.get("environment_url", ""),
                }
            )
    return rows


STABILITY_RUNS = 10  # a test's last runs looked at
FLAKY_AFTER = 2  # a test is flaky when this many of them needed a step tried again to pass


def stability(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per test: how many of its last runs only passed because a step was tried again (newest runs first
    in `results`). A test that needed it twice or more in its last 10 runs is called flaky."""
    seen: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        rows = seen.setdefault(r["test_id"], [])
        if len(rows) < STABILITY_RUNS:
            rows.append(r)
    out: dict[str, dict[str, Any]] = {}
    for test_id, rows in seen.items():
        retried = sum(1 for r in rows if r.get("flaky"))
        out[test_id] = {"runs": len(rows), "flaky_runs": retried, "flaky": retried >= FLAKY_AFTER}
    return out


def stability_summary(results: list[dict[str, Any]], tests: list[dict[str, Any]], days: int = 30) -> dict[str, Any]:
    """The share of test runs in the last days that needed a retry to pass, and the flaky tests."""
    since = datetime.now().astimezone() - timedelta(days=days)
    recent = [r for r in results if _when(r["at"]) >= since]
    retried = sum(1 for r in recent if r.get("flaky"))
    titles = {t["id"]: t for t in tests if t.get("id")}
    flaky = sorted(
        (
            {
                "test_id": tid,
                "title": titles[tid].get("title") or tid,
                "file": titles[tid].get("file"),
                **s,
            }
            for tid, s in stability(results).items()
            if s["flaky"] and tid in titles
        ),
        key=lambda x: (-x["flaky_runs"], x["title"]),
    )
    return {
        "days": days,
        "runs": len(recent),
        "retried": retried,
        "rate": round(100 * retried / len(recent), 1) if recent else None,
        "flaky_tests": flaky[:10],
        "flaky_count": len(flaky),
    }


def run_counts(run: dict[str, Any]) -> dict[str, int] | None:
    """How many tests of a finished run passed and failed."""
    suite = suite_of(run)
    if suite is None:
        return None
    statuses = [e.get("status") for e in suite.get("runs", [])]
    passed = sum(s in PASSING for s in statuses)
    return {"total": len(statuses), "passed": passed, "failed": len(statuses) - passed}


def latest_by_test(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for r in results:
        latest.setdefault(r["test_id"], r)
    return latest


def dashboard(tests: list[dict[str, Any]], runs: list[dict[str, Any]], release: str) -> dict[str, Any]:
    results = test_results(runs)
    usable = [t for t in tests if not t.get("problem") and t.get("id")]
    known = {t["id"] for t in usable}
    current = {tid: r for tid, r in latest_by_test(results).items() if tid in known}
    passing = sum(r["status"] in PASSING for r in current.values())
    failing = sum(r["status"] == "failed" for r in current.values())

    finished = [r for r in runs if r.get("suite_dir") and r["status"] in ("passed", "failed")]
    activity = []
    for run in reversed(finished[:30]):  # oldest first
        counts = run_counts(run)
        suite = suite_of(run) or {}
        if counts and counts["total"]:
            activity.append(
                {
                    "run_id": run["id"],
                    "at": run.get("started_at"),
                    "finished_at": run.get("finished_at"),
                    "target": run["target"],
                    "label": str((run.get("options") or {}).get("label") or ""),
                    "status": run["status"],
                    "release": str(suite.get("release") or ""),
                    **counts,
                }
            )

    week_ago = datetime.now().astimezone() - timedelta(days=7)
    modules: dict[str, dict[str, int]] = {}
    for t in usable:
        m = modules.setdefault(t.get("module") or "Other", {"tests": 0, "passing": 0, "failing": 0, "not_run": 0})
        m["tests"] += 1
        last = current.get(t["id"])
        if last is None:
            m["not_run"] += 1
        elif last["status"] == "failed":
            m["failing"] += 1
        else:
            m["passing"] += 1

    return {
        "tests": len(usable),
        "tested": len(current),
        "never_run": len(usable) - len(current),
        "coverage": round(100 * len(current) / len(usable)) if usable else None,
        "passing": passing,
        "failing": failing,
        "pass_rate": round(100 * passing / len(current)) if current else None,
        "runs_this_week": sum(1 for r in runs if _when(r.get("created_at")) >= week_ago),
        "tests_run_this_week": sum(1 for r in results if _when(r["at"]) >= week_ago),
        "documents": sum(1 for r in results if r.get("document")),
        "activity": activity,
        "modules": [{"module": k, **v} for k, v in sorted(modules.items())],
        "last_run": _last_run(runs),
        "readiness": readiness(usable, results, release),
        "releases": release_comparison(usable, results),
        "stability": stability_summary(results, usable),
    }


def readiness(tests: list[dict[str, Any]], results: list[dict[str, Any]], release: str) -> dict[str, Any]:
    """Where each test stands for one Oracle release.

    validated  passed on this release
    failing    failed on its latest run on this release
    baselined  not yet run on this release, but passed on an earlier one
    awaiting   not yet run on this release and never passed before
    """
    if not release:
        return {"release": "", "total": len(tests)}
    on_release = latest_by_test([r for r in results if r["release"] == release])
    passed_before = {r["test_id"] for r in results if r["status"] in PASSING and r["release"] != release}
    counts = {"validated": 0, "failing": 0, "baselined": 0, "awaiting": 0}
    modules: dict[str, dict[str, int]] = {}
    for t in tests:
        r = on_release.get(t["id"])
        state = (
            ("validated" if r["status"] in PASSING else "failing")
            if r
            else ("baselined" if t["id"] in passed_before else "awaiting")
        )
        counts[state] += 1
        m = modules.setdefault(t.get("module") or "Other", dict.fromkeys(("total", *counts), 0))
        m["total"] += 1
        m[state] += 1
    return {
        "release": release,
        "total": len(tests),
        **counts,
        "modules": [{"module": k, **v} for k, v in sorted(modules.items())],
    }


def release_comparison(tests: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pass rate of each Oracle release: the latest result of each test run on that release."""
    known = {t["id"] for t in tests}
    first_seen: dict[str, str] = {}
    for r in reversed(results):  # oldest first
        if r["release"]:
            first_seen.setdefault(r["release"], r["at"])
    out = []
    for release in sorted(first_seen, key=lambda k: first_seen[k]):
        on_release = latest_by_test([r for r in results if r["release"] == release])
        latest = {k: v for k, v in on_release.items() if k in known}
        passed = sum(r["status"] in PASSING for r in latest.values())
        out.append(
            {
                "release": release,
                "tested": len(latest),
                "passed": passed,
                "failed": len(latest) - passed,
                "pass_rate": round(100 * passed / len(latest)) if latest else None,
            }
        )
    return out


def _last_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for run in runs:
        if run["status"] not in ("queued",):
            return {
                "id": run["id"],
                "status": run["status"],
                "at": run.get("started_at") or run["created_at"],
                "target": run["target"],
            }
    return None


def test_history(test_id: str, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "run_id": r["service_run_id"],
            "at": r["at"],
            "status": r["status"],
            "release": r["release"],
            "steps_passed": r.get("steps_passed"),
            "steps_total": r.get("steps_total"),
            "duration": r.get("duration"),
            "document": r.get("document"),
            "folder": r.get("run_dir"),
        }
        for r in test_results(runs)
        if r["test_id"] == test_id
    ]


# ---------------------------------------------------------------------- needs attention

CATEGORIES = {
    "assertion": "Checks that did not match",
    "missing_element": "Items not found on the screen",
    "timeout": "Screens that did not respond",
    "authentication": "Sign-in problems",
    "test_data": "Test data or setup that was not ready",
    "service_call": "Service calls that failed",
    "failure": "Other failures",
    "could_not_run": "Runs that could not start",
    "cleanup": "Cleanup that did not finish",
    "ui_change": "Oracle screen changes",
    "unreadable": "Test files that could not be read",
}


def classify(error: str | None) -> str:
    """Which kind of failure an error message describes (from its wording, nothing more)."""
    text = error or ""
    low = text.lower()
    if low.startswith(("no test data for", "setup not met")):
        return "test_data"  # data or a setup check the pod did not meet: not a broken release
    if "missingcredentials" in low or "sign in" in low or "sign-in" in low or "password" in low or "login" in low:
        return "authentication"
    if "the api answered" in low or "the api reply" in low or "in the api reply" in low:
        return "service_call"  # a REST step: the service said no, or its reply was not as expected
    if "expected text" in low or low.startswith("stepfailure") or "assert" in low:
        return "assertion"
    if re.search(r"matched \d+", low) or "resolutionerror" in low or "no suggestion matches" in low:
        return "missing_element"
    if "timeout" in low:
        return "timeout"
    return "failure"


def plain_run_error(error: str | None) -> str:
    """Why a whole run could not finish, in everyday words; "" when the message is not a known one."""
    low = " ".join((error or "").split()).lower()
    if any(
        m in low
        for m in (
            "connection closed while reading from the driver",
            "browser has been closed",
            "target page, context or browser has been closed",
            "the service stopped during this run",
        )
    ):
        return (
            "The browser or Quartermaster stopped while the run was going. This happens when qm serve is "
            "stopped with Ctrl+C (it also stops the run it started), when the test browser window is closed, "
            "or when the computer goes to sleep. Click Run again."
        )
    if "set qm_fusion_url" in low:
        return "No pod is set up. Add the client and its environment in Settings, Clients and environments."
    if "no credentials for persona" in low:
        return (
            "The user or password for this test is not saved. In Settings, Clients and environments, edit the "
            "environment and add the user (a persona if the test switches user)."
        )
    return ""


def expected_observed(error: str | None) -> dict[str, str] | None:
    m = re.search(r"expected text '(.*)', found '(.*)'$", " ".join((error or "").split()))
    return {"expected": m.group(1), "observed": m.group(2)} if m else None


def attention(
    tests: list[dict[str, Any]], runs: list[dict[str, Any]], tests_root: Path, release: str
) -> dict[str, Any]:
    """Everything that needs a person: failures by kind, screen changes to accept, runs that could not
    start and files that could not be read."""
    by_id = {t["id"]: t for t in tests if t.get("id")}
    results = test_results(runs)
    latest = latest_by_test(results)
    last_good: dict[str, dict[str, Any]] = {}
    for r in results:
        if r["status"] in PASSING:
            last_good.setdefault(r["test_id"], r)

    items: list[dict[str, Any]] = []
    for test_id, r in latest.items():
        test = by_id.get(test_id)
        if test is None:
            continue  # the test file was removed or renamed
        good = last_good.get(test_id)
        base = {
            "test_id": test_id,
            "title": test.get("title") or test_id,
            "file": test["file"],
            "module": test.get("module", ""),
            "process": test.get("process", ""),
            "at": r["at"],
            "run_id": r["service_run_id"],
            "run_release": r["release"],
            "current_release": release,
            "last_good_release": good["release"] if good else None,
            "last_good_at": good["at"] if good else None,
            "document": r.get("document"),
            "folder": r.get("run_dir"),
        }
        spec, origins = _load_spec(tests_root / test["file"])
        where = _Where(tests_root, test["file"], origins)
        if r["status"] == "failed":
            f = r.get("failed_step") or {}
            raw = f.get("error")
            items.append(
                {
                    **base,
                    "category": classify(raw),
                    "step": f.get("number"),
                    "intent": f.get("intent"),
                    "error": plain_error(raw),
                    "detail": raw,
                    "picture": f.get("screenshot"),
                    "compare": expected_observed(raw),
                }
            )
            suggestion = _suggestion(r, f, spec, where)
            if suggestion:
                items[-1]["suggestion"] = suggestion
            items[-1]["cause"] = likely_cause(items[-1])  # after the suggestion: it changes the advice
        if r.get("cleanup_status") in ("partial", "failed"):
            # whatever the test result: records it made may still be on the pod
            steps = r.get("cleanup_failed") or []
            first = steps[0] if steps else {}
            items.append(
                {
                    **base,
                    "category": "cleanup",
                    "result": r["status"],
                    "cleanup_status": r["cleanup_status"],
                    "step": first.get("number"),
                    "intent": first.get("intent"),
                    "error": plain_error(first.get("error")) if first else "A cleanup step failed.",
                    "detail": first.get("error"),
                    "cleanup_steps": [
                        {"number": c.get("number"), "intent": c.get("intent"), "error": plain_error(c.get("error"))}
                        for c in steps
                    ],
                }
            )
        for h in r.get("healing") or []:
            if h.get("source", "fallback") != "fallback":
                continue  # a suggestion for a failed step is shown on that failure (see _suggestion)
            strategies = _strategies(spec, h.get("step_index", -1))
            new = [str(x) for x in h.get("new") or []]
            if len(new) != 2 or not strategies or strategies[0] == {new[0]: new[1]}:
                continue  # already updated in the file (or the step changed since)
            if {new[0]: new[1]} not in strategies:
                continue
            old = [str(x) for x in h.get("old") or []]
            items.append(
                {
                    **base,
                    "category": "ui_change",
                    "step_index": h["step_index"],
                    **where.fix(h["step_index"]),
                    "step": h["step_index"] + 1,
                    "intent": h.get("intent", ""),
                    "old": old,
                    "new": new,
                    "old_text": describe(old),
                    "new_text": describe(new),
                }
            )

    # a run that stopped before any test ran, unless a later run of the same tests got going
    seen_targets: set[str] = set()
    for run in runs:
        if run["status"] in ("queued", "running", "cancelled"):
            continue
        if run["target"] in seen_targets:
            continue
        seen_targets.add(run["target"])
        if run["status"] == "error":
            message = run.get("error") or ""
            items.append(
                {
                    "category": "authentication" if classify(message) == "authentication" else "could_not_run",
                    "title": _target_title(run["target"], tests),
                    "file": run["target"] if run["target"].endswith((".yaml", ".yml")) else None,
                    "at": run.get("finished_at") or run["created_at"],
                    "run_id": run["id"],
                    "error": _last_error_line(message),
                    "detail": message,
                    "current_release": release,
                }
            )

    items += [
        {"category": "unreadable", "title": t["file"], "file": t["file"], "error": t["problem"]}
        for t in tests
        if t.get("problem")
    ]
    counts: dict[str, int] = {}
    for it in items:
        counts[it["category"]] = counts.get(it["category"], 0) + 1
    return {
        "items": items,
        "count": len(items),
        "counts": counts,
        "categories": CATEGORIES,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _suggestion(
    result: dict[str, Any], failed: dict[str, Any], spec: dict[str, Any], where: _Where | None = None
) -> dict[str, Any] | None:
    """What a failed step's item may have become, if the run found out (see runner.suggest) and the test
    does not already try it first."""
    number = failed.get("number")
    if not isinstance(number, int):
        return None
    index = number - 1
    for h in result.get("healing") or []:
        if h.get("source") not in ("similar", "ai") or h.get("step_index") != index:
            continue
        new = [str(x) for x in h.get("new") or []]
        old = [str(x) for x in h.get("old") or []]
        strategies = _strategies(spec, index)
        if len(new) != 2 or not strategies or strategies[0] == {new[0]: new[1]}:
            continue
        return {
            "step_index": index,
            **(where.fix(index) if where else {}),
            "source": h["source"],
            "confidence": h.get("confidence"),
            "why": h.get("why") or "",
            "old": old,
            "new": new,
            "old_text": describe(old),
            "new_text": describe(new),
        }
    return None


def describe(strategy: list[str]) -> str:
    """How a test finds something on the screen, in words a business user can follow."""
    if len(strategy) != 2:
        return ""
    kind, value = strategy
    if kind == "label":
        return f'the field labelled "{value}"'
    if kind == "role" and ":" in value:
        role, name = value.split(":", 1)
        return f'the {role} named "{name}"'
    if kind == "text":
        return f'the text "{value}"'
    if kind == "test_id":
        return f'the element with test id "{value}"'
    return f"a technical page address ({kind})"


def _target_title(target: str, tests: list[dict[str, Any]]) -> str:
    if target in ("", "."):
        return "All tests"
    t = next((t for t in tests if t["file"] == target), None)
    return (t or {}).get("title") or (target if target.endswith((".yaml", ".yml")) else f"All tests in {target}")


def _last_error_line(message: str) -> str:
    lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
    errors = [ln for ln in lines if ln.lower().startswith("error:")]
    line = errors[-1][6:].strip() if errors else (lines[-1] if lines else "")
    return line or "The run stopped before any test ran."


def _load_spec(path: Path) -> tuple[dict[str, Any], list[tuple[str | None, int]]]:
    """The test as raw YAML with its shared steps in place, and where each step is written (see dsl/library)."""
    try:
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}, []
    try:
        spec, origins = expand(spec, path)
    except LibraryError:
        return {}, []
    return (spec, origins) if isinstance(spec, dict) else ({}, [])


class _Where:
    """Which file, and which step in it, a step of a test is written in: the test, or a shared group."""

    def __init__(self, tests_root: Path, test_file: str, origins: list[tuple[str | None, int]]):
        self.tests_root, self.test_file, self.origins = tests_root, test_file, origins
        self._groups: dict[str, Path] | None = None

    def fix(self, index: int) -> dict[str, Any]:
        """`fix_file` and `fix_step` for editing that step, plus `shared` (the group) when it is not the test's own."""
        if not 0 <= index < len(self.origins):
            return {"fix_file": self.test_file, "fix_step": index}
        group, number = self.origins[index]
        if group is None:
            return {"fix_file": self.test_file, "fix_step": number}
        if self._groups is None:
            found, _ = read_groups(library_dir_for(self.tests_root / self.test_file))
            self._groups = {name: path for name, (path, _) in found.items()}
        path = self._groups.get(group)
        file = path.relative_to(self.tests_root.resolve()).as_posix() if path else self.test_file
        return {"fix_file": file, "fix_step": number, "shared": group}


def _strategies(spec: dict[str, Any], index: int) -> list[dict[str, str]]:
    steps = spec.get("steps") or []
    if not 0 <= index < len(steps) or not isinstance(steps[index], dict):
        return []
    target = steps[index].get("target") or {}
    found = target.get("strategies") if isinstance(target, dict) else None
    return [{str(k): str(v) for k, v in s.items()} for s in found or [] if isinstance(s, dict)]


def _when(iso: str | None) -> datetime:
    try:
        return datetime.fromisoformat(iso) if iso else datetime.min.replace(tzinfo=datetime.now().astimezone().tzinfo)
    except ValueError:
        return datetime.min.replace(tzinfo=datetime.now().astimezone().tzinfo)
