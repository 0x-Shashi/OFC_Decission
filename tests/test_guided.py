"""Doing a manual scenario by hand: marks with pictures, the evidence, and the test it leaves behind."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from test_recorder import CHROMIUM, POD, _ev

from quartermaster.domain.models import Environment, EnvironmentKind, StepStatus
from quartermaster.dsl.loader import load_test
from quartermaster.recorder.guided import Guide, guide_steps
from quartermaster.recorder.recorder import Recorder

SCENARIO = {
    "id": "ess-test-script/ESS-001",
    "ref": "ESS-001",
    "title": "My Compensation",
    "cases": [
        {
            "id": "ESS-001",
            "name": "My Compensation",
            "steps": [
                {"action": "Login", "expected": ""},
                {"action": "Me", "expected": "The Me page opens"},
                {"action": "Personal Information", "expected": ""},
                {"action": "Select My Compensation", "expected": "Current Salary is shown"},
            ],
        }
    ],
}


class FakePage:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def screenshot(self, path: str, timeout: float) -> None:
        if self.fail:
            raise TimeoutError("Timeout 15000ms exceeded.\nCall log: ...")
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")


def test_steps_marks_and_result(tmp_path: Path) -> None:
    assert [s["action"] for s in guide_steps({"title": "Only a title"})] == ["Only a title"]
    assert guide_steps({"cases": [{"id": "TC1", "name": "Approve", "steps": []}]})[0]["action"] == "Approve"
    guide = Guide(SCENARIO, tmp_path / "run")
    assert guide.mark("1 pass", FakePage()) == "Step 1 marked passed."
    assert guide.mark("2 fail   Me   tile is missing ", FakePage()) == "Step 2 marked failed."
    assert guide.mark("3 pass", FakePage(fail=True)) == "Step 3 marked passed."
    assert guide.mark("9 pass", FakePage()) == "That step is not in this scenario."
    assert guide.mark("1 maybe", FakePage()) == "Mark a step as pass or fail."
    shown = guide.state()
    assert shown[0]["picture"] == "screenshots/step-01.png" and (tmp_path / "run" / shown[0]["picture"]).is_file()
    assert shown[1]["note"] == "Me tile is missing" and shown[2]["picture"] is None
    assert "could not be captured" in shown[2]["picture_note"] and "status" not in shown[3]

    result = guide.result(
        test_id="t", title="My Compensation", environment="dev", environment_url=POD, release="26C", run_id="r1"
    )
    assert [s.status for s in result.steps] == [
        StepStatus.PASSED,
        StepStatus.FAILED,
        StepStatus.PASSED,
        StepStatus.FAILED,
    ]
    assert result.status is StepStatus.FAILED
    assert result.steps[1].error == "Tester: Marked as failed: Me tile is missing"
    assert result.steps[3].error == "Tester: Not checked: the run was finished before this step."
    assert result.steps[3].intent == "ESS-001: Select My Compensation" and result.steps[3].expected
    assert result.steps[0].evidence and result.steps[0].action == "manual"


def test_finishing_saves_evidence_and_the_test_that_plays_next_time(tmp_path: Path) -> None:
    from quartermaster.cli import _finish_by_hand
    from quartermaster.evidence.document import plain_error

    evidence = tmp_path / "evidence"
    args = argparse.Namespace(
        id="manual.ess-test-script.ess-001",
        title="My Compensation",
        module="HCM",
        product="Global Human Resources",
        persona="",
        process="Manual scenario ESS-001",
        release="26C",
        tester="Tester",
        out=str(tmp_path / "tests" / "manual" / "ess-001.yaml"),
        evidence=str(evidence),
    )
    env = Environment(name="fusion", url=POD, kind=EnvironmentKind.DEV)
    recorder = Recorder(test_id=args.id)
    recorder.guide = Guide(SCENARIO, evidence / args.id / "r1")
    assert _finish_by_hand(args, recorder, env, "r1") == 2  # nothing marked: nothing saved
    assert not (tmp_path / "tests").exists()

    for n in (1, 2, 3, 4):
        recorder.guide.mark(f"{n} pass", FakePage())
    recorder._receive(_ev("click", "Me", ("role", "link:Me")))
    recorder._receive(_ev("assert_visible", "Current Salary", ("text", "Current Salary")))
    assert _finish_by_hand(args, recorder, env, "r1") == 0

    saved = load_test(args.out)
    assert saved.id == args.id and saved.process == "Manual scenario ESS-001" and len(saved.steps) == 2
    record = json.loads((evidence / args.id / "r1" / "run.json").read_text())
    assert (record["mode"], record["status"], record["executed_by"], record["release"]) == (
        "manual",
        "passed",
        "Tester",
        "26C",
    )
    assert record["test_file"] == args.out and len(record["evidence_sha256"]) == 4
    [suite_dir] = (evidence / "_suites").iterdir()
    suite = json.loads((suite_dir / "suite.json").read_text())
    assert suite["runs"][0]["test_id"] == args.id and suite["status"] == "passed"
    doc = next((evidence / args.id / "r1").glob("*_evidence.docx"))
    text = zipfile.ZipFile(doc).read("word/document.xml").decode()
    assert "test done by hand" in text and "marked by the tester" in text
    assert plain_error("Tester: Marked as failed: Me tile is missing") == "Marked as failed: Me tile is missing"


def test_no_clicks_still_keeps_the_evidence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from quartermaster.cli import _finish_by_hand

    evidence = tmp_path / "evidence"
    args = argparse.Namespace(
        id="manual.x.ess-001",
        title="My Compensation",
        module="HCM",
        product="GHR",
        persona="",
        process="",
        release=None,
        tester="",
        out=str(tmp_path / "t.yaml"),
        evidence=str(evidence),
    )
    recorder = Recorder(test_id=args.id)
    recorder.guide = Guide(SCENARIO, evidence / args.id / "r2")
    recorder.guide.mark("1 fail could not sign in", FakePage())
    assert _finish_by_hand(args, recorder, Environment(name="f", url=POD, kind=EnvironmentKind.DEV), "r2") == 0
    out = capsys.readouterr().out
    assert "automatic version was not saved" in out and "Result: failed" in out
    assert not Path(args.out).exists() and (evidence / args.id / "r2" / "run.json").is_file()


def test_commands_reach_the_guide(tmp_path: Path) -> None:
    feed = tmp_path / "feed.json"
    r = Recorder(feed=feed, test_id="manual.x")
    r.command("result 1 pass")  # no guide: ignored
    r.guide = Guide(SCENARIO, tmp_path / "run")
    r._page = FakePage()
    r.command("result 2 fail Nothing happened")
    shown = json.loads(feed.read_text())
    assert r.message == "Step 2 marked failed." and shown["guide"][1]["status"] == "failed"
    assert shown["guide_folder"] == str(tmp_path / "run")


PAGE = """<!doctype html><html><body>
<form id="login">
  <label for="userid">User ID</label><input id="userid">
  <label for="password">Password</label><input id="password" type="password">
  <button type="submit">Sign In</button>
</form>
<script>
const SCREENS = {
  me: '<h1>Me</h1><a href="#" data-go="pi">Personal Information</a>',
  pi: '<h1>Personal Information</h1><a href="#" data-go="comp">My Compensation</a>',
  comp: '<h1>My Compensation</h1><h2>Current Salary</h2><p>120,000 USD</p>',
};
document.getElementById("login").addEventListener("submit", (e) => {
  e.preventDefault();
  document.body.innerHTML = '<nav><a href="#" data-go="me">Me</a></nav><main id="main"><h1>Welcome</h1></main>';
});
document.addEventListener("click", (e) => {
  const go = e.target.closest("[data-go]");
  if (go) { e.preventDefault(); document.getElementById("main").innerHTML = SCREENS[go.dataset.go]; }
});
</script></body></html>"""


def test_by_hand_in_a_real_browser_then_it_plays_by_itself(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    from quartermaster.recorder.recorder import events_to_test, to_yaml
    from quartermaster.runner.engine import run_test
    from quartermaster.runner.playwright_driver import PlaywrightDriver

    environ = {"QM_FUSION_USER": "Mock User", "QM_FUSION_PASSWORD": "not-a-real-secret"}
    if CHROMIUM:
        environ["QM_CHROMIUM_PATH"] = CHROMIUM
    env = Environment(name="mock", url=POD, kind=EnvironmentKind.DEV)

    def serve(context: Any) -> None:
        context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=PAGE))

    driver = PlaywrightDriver(evidence_dir=str(tmp_path), environ=environ, context_hook=serve)
    driver.open(env, "")
    try:
        recorder = Recorder(test_id="manual.ess.ess-001")
        recorder.attach(driver.page)
        recorder.guide = Guide(SCENARIO, tmp_path / "run")
        page = driver.page
        recorder.command("result 1 pass")
        page.get_by_role("link", name="Me").click()
        recorder.command("result 2 pass")
        page.get_by_role("link", name="Personal Information").click()
        recorder.command("result 3 pass")
        page.get_by_role("link", name="My Compensation").click()
        recorder.command("check")  # Add check, then click the value that proves the page opened
        page.get_by_text("Current Salary").click()
        recorder.command("result 4 pass")
        page.wait_for_timeout(200)
    finally:
        driver.close()

    assert all(s.get("status") == "passed" and s.get("picture") for s in recorder.guide.state())
    test = events_to_test(
        recorder.events,
        test_id="manual.ess.ess-001",
        title="My Compensation",
        module="HCM",
        product="Global Human Resources",
    )
    out = tmp_path / "ess-001.yaml"
    out.write_text(to_yaml(test))
    saved = load_test(out)
    # A short value clicked after Add check is checked word for word (long text: only that it is shown).
    assert [s.action.value for s in saved.steps] == ["click", "click", "click", "assert_text"]

    # Next quarter: the same scenario plays by itself.
    play = PlaywrightDriver(evidence_dir=str(tmp_path), environ=environ, context_hook=serve)
    result = run_test(saved, env, play)
    assert result.status is StepStatus.PASSED, [s.error for s in result.steps]
