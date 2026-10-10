"""The summary of a nightly run, for the page of a GitHub Actions run (see .github/workflows/nightly-pod.yml).

A nightly run goes to a log that anyone who can see the repository can read, and a public repository's is public. So the
summary says which tests passed and failed and at which step (the step's own wording, written by whoever made the test),
and leaves out what the pod showed (expected and observed values, error text) unless `details` is switched on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def summarize(report: list[dict[str, Any]] | None, *, details: bool = False, release: str = "") -> str:
    """Markdown for the summary page. `report` is what `qm run --report` writes (None: the run never got that far)."""
    title = "## Nightly run against the pod" + (f" (Oracle release {release})" if release else "")
    if report is None:
        return (
            f"{title}\n\n**The run did not finish.** No result was written. Check that the pod is reachable from "
            "GitHub (an IP allow-list may block it) and that the three secrets are set. The job log says which step "
            "stopped.\n"
        )
    if not report:
        return f"{title}\n\nNo tests ran. Check the folder named in `QM_NIGHTLY_TESTS`.\n"
    failed = [r for r in report if _status(r) == "failed"]
    flaky = [
        r for r in report if _status(r) != "failed" and any(int(s.get("attempts") or 1) > 1 for s in r.get("steps", []))
    ]
    unclean = [r for r in report if _cleanup(r) in ("partial", "failed")]
    lines = [
        title,
        "",
        f"**{len(report) - len(failed)} of {len(report)} tests passed.**"
        + (f" {len(failed)} failed." if failed else " Nothing failed.")
        + (f" {len(flaky)} passed only after a step was tried again." if flaky else "")
        + (f" {len(unclean)} left test data on the pod (cleanup did not finish)." if unclean else ""),
        "",
        "| Test | Result | Where it failed |",
        "|---|---|---|",
    ]
    for r in sorted(report, key=lambda x: (_status(x) != "failed", str(x.get("test_id")))):
        status = _status(r)
        word = "Failed" if status == "failed" else "Passed after a retry" if r in flaky else "Passed"
        lines.append(f"| {_cell(r.get('test_title') or r.get('test_id'))} | {word} | {_where(r, details)} |")
    if unclean:
        lines += ["", "Cleanup did not finish for: " + ", ".join(_cell(r.get("test_id")) for r in unclean) + "."]
    if not details and failed:
        lines += [
            "",
            "What the pod showed is left out of this page. Set the variable `QM_NIGHTLY_DETAILS` to `on` to add it.",
        ]
    return "\n".join(lines) + "\n"


def read_report(path: str | Path) -> list[dict[str, Any]] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else None


def _status(result: dict[str, Any]) -> str:
    steps = result.get("steps", [])
    if any(s.get("status") == "failed" for s in steps):
        return "failed"
    return "passed" if steps else "failed"


def _cleanup(result: dict[str, Any]) -> str:
    steps = result.get("cleanup", [])
    if not steps:
        return "none"
    bad = [s for s in steps if s.get("status") == "failed"]
    return "done" if not bad else "failed" if len(bad) == len(steps) else "partial"


def _where(result: dict[str, Any], details: bool) -> str:
    step = next((s for s in result.get("steps", []) if s.get("status") == "failed"), None)
    if step is None:
        return "" if result.get("steps") else "no steps ran"
    text = f"step {int(step.get('index', 0)) + 1}: {_cell(step.get('intent'))}"
    if details and step.get("error"):
        text += f" ({_cell(step['error'])[:300]})"
    return text


def _cell(value: Any) -> str:
    """Text for one table cell: one line, and no characters that would break the table."""
    return " ".join(str(value or "").split()).replace("|", "/").replace("`", "'")
