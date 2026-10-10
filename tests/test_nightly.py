"""The nightly run against the real pod: its summary page, and the workflow file that starts it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import EXAMPLES

from quartermaster.cli import main
from quartermaster.nightly import read_report, summarize

ROOT = EXAMPLES.parent
WORKFLOW = ROOT / ".github" / "workflows" / "nightly-pod.yml"


def result(test_id: str, *statuses: str, attempts: int = 1, cleanup: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "test_id": test_id,
        "test_title": f"Title of {test_id}",
        "steps": [
            {
                "index": i,
                "intent": f"Step {i + 1} of {test_id}",
                "status": s,
                "error": f"the pod said SECRET-{i}" if s == "failed" else None,
                "attempts": attempts,
            }
            for i, s in enumerate(statuses)
        ],
        "cleanup": [{"index": i, "status": s} for i, s in enumerate(cleanup)],
    }


REPORT = [
    result("a.fine", "passed", "passed"),
    result("b.broken", "passed", "failed", "skipped"),
    result("c.flaky", "passed", "passed", attempts=2),
    result("d.messy", "passed", cleanup=("failed",)),
]


def test_the_summary_says_what_passed_and_where_a_test_failed() -> None:
    text = summarize(REPORT, release="26D")
    assert "## Nightly run against the pod (Oracle release 26D)" in text
    assert "**3 of 4 tests passed.** 1 failed." in text and "1 passed only after a step was tried again" in text
    assert "1 left test data on the pod" in text
    assert "| Title of b.broken | Failed | step 2: Step 2 of b.broken |" in text
    assert "| Title of c.flaky | Passed after a retry |  |" in text
    assert text.index("b.broken") < text.index("a.fine")  # failures first
    assert "Cleanup did not finish for: d.messy." in text


def test_what_the_pod_showed_is_left_out_unless_asked_for() -> None:
    assert "SECRET-1" not in summarize(REPORT) and "QM_NIGHTLY_DETAILS" in summarize(REPORT)
    assert "SECRET-1" in summarize(REPORT, details=True)


def test_awkward_text_cannot_break_the_table() -> None:
    odd = result("x", "failed")
    odd["steps"][0]["intent"] = "Open | the `page`\nnow"
    row = [ln for ln in summarize([odd]).splitlines() if ln.startswith("| Title of x")][0]
    assert row.count("|") == 4 and "`" not in row and "\n" not in row


def test_a_run_that_never_finished_and_an_empty_run_are_said_plainly() -> None:
    assert "The run did not finish" in summarize(None) and "IP allow-list" in summarize(None)
    assert "No tests ran" in summarize([])


def test_the_report_is_read_from_the_file_the_run_writes(tmp_path: Path) -> None:
    good = tmp_path / "r.json"
    good.write_text(json.dumps(REPORT))
    assert len(read_report(good) or []) == 4
    assert read_report(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert read_report(bad) is None
    bad.write_text('{"a": 1}')
    assert read_report(bad) is None


def test_the_command_prints_the_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps(REPORT))
    assert main(["nightly-summary", str(report), "--release", "26D"]) == 0
    out = capsys.readouterr().out
    assert "3 of 4 tests passed" in out and "SECRET-1" not in out
    assert main(["nightly-summary", str(report), "--details"]) == 0
    assert "SECRET-1" in capsys.readouterr().out
    assert main(["nightly-summary", str(tmp_path / "none.json")]) == 0
    assert "did not finish" in capsys.readouterr().out


# ------------------------------------------------------------------ the workflow file


def workflow() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_the_nightly_run_does_nothing_until_it_is_switched_on() -> None:
    wf = workflow()
    triggers = wf[True]  # YAML reads the key `on` as True
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    job = wf["jobs"]["run"]
    assert "vars.QM_NIGHTLY == 'on'" in job["if"] and "workflow_dispatch" in job["if"]


def test_the_pod_login_is_only_given_to_the_steps_that_need_it_and_never_printed() -> None:
    wf = workflow()
    job = wf["jobs"]["run"]
    assert not any("secrets." in str(v) for v in job["env"].values())  # not for every step
    needing = [s["name"] for s in job["steps"] if "secrets." in str(s.get("env", {}))]
    assert needing == ["Check the secrets are set", "Run the tests against the pod"]
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "echo $QM_FUSION" not in text and 'echo "$QM_FUSION' not in text and "PASSWORD}" not in text.split("run:")[1]
    assert wf["permissions"] == {"contents": "read"}


def test_the_log_holds_nothing_the_pod_showed_unless_asked_and_evidence_is_opt_in() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    steps = {s.get("name", s.get("uses")): s for s in workflow()["jobs"]["run"]["steps"]}
    run = steps["Run the tests against the pod"]["run"]
    assert "> run.log 2>&1" in run  # the output goes to a file, not the log
    assert run.count("cat run.log") == 1 and 'if [ "$DETAILS" = "on" ]' in run.split("cat run.log")[0].splitlines()[-1]
    assert 'screenshots "$shots"' in run and 'shots="off"' in run  # no pictures unless switched on
    upload = steps["Keep the evidence (only when switched on)"]
    assert "env.EVIDENCE == 'on'" in upload["if"] and "retention-days" in upload["with"]
    assert "private repository" in text


def test_the_workflow_runs_a_folder_that_exists() -> None:
    assert (ROOT / "examples" / "smoke").is_dir()  # the default folder of the nightly run
    assert "examples/smoke" in WORKFLOW.read_text(encoding="utf-8")
