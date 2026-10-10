"""Run queue and run history. Standard library only: a stand-in plays `qm run`."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from quartermaster.service.runner import RunQueue, check_options, qm_run_command
from quartermaster.service.store import Store

# Stand-in for `qm run`: writes progress events like the real one and exits 0 (passed),
# 1 (a test failed) or 2 (could not run), depending on the test file's name.
FAKE_QM = r"""
import json, sys, time
target, events = sys.argv[1], sys.argv[2]
name = target.replace('\\', '/').rsplit('/', 1)[-1]
def emit(e):
    with open(events, 'a') as f: f.write(json.dumps(e) + '\n')
if name.startswith('crash'):
    print('error: something went wrong before any test ran'); sys.exit(2)
emit({'type': 'suite_start', 'tests': [name]})
emit({'type': 'step_start', 'index': 0, 'intent': 'Open page'})
if name.startswith('slow'): time.sleep(30)
status = 'failed' if name.startswith('fail') else 'passed'
emit({'type': 'step_end', 'index': 0, 'status': status})
end = {'type': 'suite_end', 'status': status, 'suite_dir': 'evidence/_suites/S1'}
emit(dict(end, summary='evidence/_suites/S1/s.docx'))
sys.exit(1 if status == 'failed' else 0)
"""


def fake_command(target: str, options: dict[str, Any], evidence_root: Path, events: Path) -> list[str]:
    return [sys.executable, "-c", FAKE_QM, target, str(events)]


@pytest.fixture
def queue(tmp_path: Path):  # type: ignore[no-untyped-def]
    tests = tmp_path / "my_tests"
    tests.mkdir()
    for name in ("pass.yaml", "fail.yaml", "crash.yaml", "slow.yaml"):
        (tests / name).write_text("id: x\n")
    q = RunQueue(
        Store(tmp_path / "qm.db"),
        tests_root=tests,
        evidence_root=tmp_path / "evidence",
        work_dir=tmp_path / "work",
        command=fake_command,
    )
    q.start()
    yield q
    q.stop()


def wait_for(queue: RunQueue, run_id: str, status: tuple[str, ...], timeout: float = 20) -> dict[str, Any]:
    end = time.time() + timeout
    while time.time() < end:
        run = queue.store.get(run_id)
        if run and run["status"] in status:
            return run
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not reach {status}: {queue.store.get(run_id)}")


def test_runs_finish_with_the_right_status_and_evidence_location(queue: RunQueue) -> None:
    ok = queue.submit("pass.yaml")
    bad = queue.submit("fail.yaml")
    broken = queue.submit("crash.yaml")
    assert ok["status"] == "queued" and ok["options"]["screenshots"] == "every-step"

    ok = wait_for(queue, ok["id"], ("passed",))
    assert ok["exit_code"] == 0 and ok["suite_dir"] == "evidence/_suites/S1" and ok["summary"].endswith(".docx")
    assert wait_for(queue, bad["id"], ("failed",))["exit_code"] == 1
    broken = wait_for(queue, broken["id"], ("error",))
    assert "something went wrong" in broken["error"]

    # history, newest first; events can be read from any line on
    assert [r["id"] for r in queue.store.list()] == [broken["id"], bad["id"], ok["id"]]
    events = queue.events(ok["id"])
    assert [e["type"] for e in events] == ["suite_start", "step_start", "step_end", "suite_end"]
    assert queue.events(ok["id"], after=3) == events[3:]


def test_runs_go_one_at_a_time_and_can_be_cancelled(queue: RunQueue) -> None:
    slow = queue.submit("slow.yaml")
    waiting = queue.submit("pass.yaml")
    wait_for(queue, slow["id"], ("running",))
    time.sleep(0.5)
    assert queue.store.get(waiting["id"])["status"] == "queued"  # waits for the running one

    assert queue.cancel(waiting["id"])["status"] == "cancelled"  # queued: never starts
    queue.cancel(slow["id"])  # running: its process is stopped
    assert wait_for(queue, slow["id"], ("cancelled",))["finished_at"]
    time.sleep(1)
    assert queue.store.get(waiting["id"])["started_at"] is None


def test_requests_are_checked(queue: RunQueue) -> None:
    with pytest.raises(ValueError, match="inside the tests folder"):
        queue.submit("../outside.yaml")
    with pytest.raises(ValueError, match="no test file"):
        queue.submit("missing.yaml")
    with pytest.raises(ValueError, match="unknown run options"):
        queue.submit("pass.yaml", {"shell": "rm -rf /"})
    with pytest.raises(ValueError, match="screenshots must be one of"):
        check_options({"screenshots": "sometimes"})


def test_a_run_left_running_by_a_stopped_service_is_marked_as_error(tmp_path: Path) -> None:
    store = Store(tmp_path / "qm.db")
    run = store.create("pass.yaml", check_options({}))
    store.claim_next()
    store.recover()
    assert store.get(run["id"])["status"] == "error"


def test_the_real_command_line() -> None:
    options = check_options({"video": "always", "release": "26D", "tester": "Sai", "headed": True})
    evidence, events = Path("/evidence"), Path("/work/1/events.jsonl")
    cmd = qm_run_command("/tests/my_tests", options, evidence, events)
    assert cmd[1:5] == ["-m", "quartermaster.cli", "run", "/tests/my_tests"]
    joined = " ".join(cmd)
    for part in (
        f"--events {events}",  # the OS's own path form: backslashes on Windows
        "--screenshots every-step",
        "--video always",
        "--evidence-doc",
        "--headed",
        "--release 26D",
        "--tester Sai",
        f"--evidence {evidence}",
    ):
        assert part in joined, part
    assert json.dumps(options)  # stored as JSON in the history


def test_a_run_of_chosen_tests() -> None:
    options = check_options({"only": ["hcm.view-worker", "hcm.hire-page-opens", "hcm.view-worker"], "label": " 26C "})
    assert options["only"] == ["hcm.view-worker", "hcm.hire-page-opens"] and options["label"] == "26C"
    cmd = qm_run_command("/tests", options, Path("/evidence"), Path("/events.jsonl"))
    assert cmd[-2:] == ["--only", "hcm.view-worker,hcm.hire-page-opens"]
    assert "--only" not in qm_run_command("/tests", check_options({}), Path("/evidence"), Path("/events.jsonl"))
    for bad in ("hcm.view-worker", ["a b"], [1]):
        with pytest.raises(ValueError, match="list of test ids"):
            check_options({"only": bad})


def test_highlighting_is_on_unless_turned_off() -> None:
    on = qm_run_command("/tests", check_options({}), Path("/evidence"), Path("/events.jsonl"))
    off = qm_run_command("/tests", check_options({"highlight": False}), Path("/evidence"), Path("/events.jsonl"))
    assert "--no-highlight" not in on and "--no-highlight" in off
