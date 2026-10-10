"""A manual scenario done by hand while Quartermaster watches.

The tester follows the scenario's written steps in a browser that is already signed in, and
marks each step Pass or Fail. At that moment Quartermaster takes a picture of the screen. The
clicks are recorded at the same time (see `Recorder`), so the next run of the scenario can
play by itself.

The result is an ordinary run result, so the run record, the Word evidence document and the
run history are the same as for an automated run. A step the tester did not mark counts as
failed ("not checked"): a run that was not finished must never read as passed.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from quartermaster.domain.models import RunResult, ScreenshotMode, StepResult, StepStatus

# Errors written by a person start with this, and are shown as they are (see evidence.document.plain_error).
TESTER = "Tester: "
NOT_CHECKED = "Not checked: the run was finished before this step."


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def guide_steps(scenario: dict[str, Any]) -> list[dict[str, str]]:
    """The scenario's steps in order, each with the test case it belongs to."""
    steps: list[dict[str, str]] = []
    for case in scenario.get("cases") or []:
        written = case.get("steps") or []
        if not written:  # a case with a name but no steps is still one thing to check
            written = [{"action": case.get("name") or case.get("id", ""), "expected": case.get("description", "")}]
        for st in written:
            steps.append(
                {
                    "case": str(case.get("id", "")),
                    "case_name": str(case.get("name", "")),
                    "action": " ".join(str(st.get("action", "")).split()),
                    "expected": " ".join(str(st.get("expected", "")).split()),
                }
            )
    if not steps:
        steps.append({"case": "", "case_name": "", "action": str(scenario.get("title", "")), "expected": ""})
    return steps


class Guide:
    def __init__(self, scenario: dict[str, Any], run_dir: Path):
        self.scenario = scenario
        self.steps = guide_steps(scenario)
        self.run_dir = run_dir
        self.results: dict[int, dict[str, Any]] = {}
        self.started_at = _now()

    @classmethod
    def load(cls, path: Path, run_dir: Path) -> Guide:
        return cls(json.loads(path.read_text(encoding="utf-8")), run_dir)

    def mark(self, line: str, page: Any) -> str:
        """`<step number> pass|fail [note]` from the web page. Returns a message for the tester."""
        number, _, rest = line.strip().partition(" ")
        status, _, note = rest.strip().partition(" ")
        if not number.isdigit() or not 1 <= int(number) <= len(self.steps):
            return "That step is not in this scenario."
        if status not in ("pass", "fail"):
            return "Mark a step as pass or fail."
        index = int(number) - 1
        picture, picture_note = self._picture(index, page)
        self.results[index] = {
            "status": "passed" if status == "pass" else "failed",
            "note": " ".join(note.split())[:500],
            "at": _now(),
            "picture": picture,
            "picture_note": picture_note,
        }
        return f"Step {number} marked {'passed' if status == 'pass' else 'failed'}."

    def _picture(self, index: int, page: Any) -> tuple[str | None, str | None]:
        if page is None:
            return None, "No browser window was open."
        path = self.run_dir / "screenshots" / f"step-{index + 1:02d}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(path), timeout=15000)
        except Exception as e:  # a page still loading must not lose the tester's result
            return None, f"The screen could not be captured: {str(e).splitlines()[0][:120]}"
        return path.relative_to(self.run_dir).as_posix(), None

    def state(self) -> list[dict[str, Any]]:
        """The steps with their marks, for the web page (pictures relative to the run folder)."""
        return [{**st, "number": i + 1, **self.results.get(i, {})} for i, st in enumerate(self.steps)]

    @property
    def current(self) -> int:
        """The number of the step being done now: the first one not marked yet (or the last)."""
        return next((i + 1 for i in range(len(self.steps)) if i not in self.results), len(self.steps))

    @property
    def marked(self) -> int:
        return len(self.results)

    def result(
        self, *, test_id: str, title: str, environment: str, environment_url: str, release: str | None, run_id: str
    ) -> RunResult:
        steps = []
        for i, st in enumerate(self.steps):
            done = self.results.get(i)
            if done is None:
                status, error, when = StepStatus.FAILED, f"{TESTER}{NOT_CHECKED}", None
            elif done["status"] == "failed":
                why = done["note"] or "no reason given"
                status, error, when = StepStatus.FAILED, f"{TESTER}Marked as failed: {why}", done["at"]
            else:
                status, error, when = StepStatus.PASSED, None, done["at"]
            picture = done.get("picture") if done else None
            intent = st["action"] if not st["case"] else f"{st['case']}: {st['action']}"
            steps.append(
                StepResult(
                    index=i,
                    intent=intent,
                    status=status,
                    error=error,
                    evidence=[str(self.run_dir / picture)] if picture else [],
                    action="manual",
                    expected=st["expected"],
                    started_at=when,
                    screenshot_note=done.get("picture_note") if done else None,
                )
            )
        return RunResult(
            test_id=test_id,
            environment=environment,
            steps=steps,
            run_id=run_id,
            test_title=title,
            environment_url=environment_url,
            release=release,
            started_at=self.started_at,
            finished_at=_now(),
            screenshots=ScreenshotMode.EVERY_STEP,
        )
