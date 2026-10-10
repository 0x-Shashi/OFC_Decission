"""Record a test by clicking through Fusion; save it as a replayable YAML spec.

Browser events (from capture.js) arrive as dicts. The conversion rules live in
`recorder.steps` and are pure, so they are unit-tested without a browser.
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from quartermaster.domain.models import Priority, TestCase
from quartermaster.recorder.steps import events_to_steps, preview, secret_names

CAPTURE_JS = (Path(__file__).parent / "capture.js").read_text(encoding="utf-8")


def events_to_test(
    events: list[dict[str, Any]],
    *,
    test_id: str,
    title: str,
    module: str,
    product: str,
    persona: str = "",
    priority: Priority = Priority.MEDIUM,
    process: str = "",
    written_steps: list[dict[str, str]] | None = None,
) -> TestCase:
    """Turn captured events into a test (conversion rules: `recorder.steps`). `written_steps`: the
    manual scenario's steps, when the test was made from one."""
    steps, data = events_to_steps(events, secret_prefix(test_id))
    if not steps:
        raise ValueError("no usable actions were recorded")
    return TestCase.model_validate(
        {
            "id": test_id,
            "title": title,
            "module": module,
            "product": product,
            "persona": persona,
            "process": process,
            "priority": priority,
            "tags": ["recorded"],
            "data": data,
            "steps": steps,
            **({"written_steps": written_steps} if written_steps else {}),
        }
    )


def to_yaml(test: TestCase) -> str:
    raw = test.model_dump(mode="json", exclude_defaults=True)
    for step in raw["steps"]:
        if "target" in step:
            step["target"]["strategies"] = [dict(s) for s in step["target"]["strategies"]]
    header = (
        "# Recorded with `qm record`. Review before committing:\n"
        "#  - rename data keys (value1, value2...) to meaningful names\n"
        "#  - make sure there is a check (assert_text / assert_visible) that proves the outcome;\n"
        "#    add them while recording with the Add check button\n"
    )
    secrets = secret_names(raw.get("data") or {})
    if secrets:
        header += "#  - masked values were not saved; set these environment variables before running:\n"
        header += "".join(f"#      {name}\n" for name in secrets)
    return header + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)


def secret_prefix(test_id: str) -> str:
    """Environment variable prefix for a test's masked values, e.g. QM_HCM_VIEW_WORKER."""
    return "QM_" + re.sub(r"[^A-Z0-9]+", "_", test_id.upper()).strip("_")


_ACTIONS = ("click", "fill", "select", "navigate", "assert_text", "assert_visible", "wait_job")
_LABELS = {
    "click": "click",
    "fill": "typed value",
    "select": "choice",
    "navigate": "page opened",
    "assert_text": "check",
    "assert_visible": "check",
    "note": "note",
    "wait_job": "wait for the process",
}


class Recorder:
    """Attaches capture.js to a Playwright page and collects events.

    `stopped` turns true when the person presses Stop recording in the browser toolbar, or a
    "stop" command arrives. Commands (one per line, from the terminal or the web UI):

        pause / resume      nothing is recorded while paused
        check               the next click records a check instead of an action
        undo                forget the last recorded step
        note <text>         what should happen at the last step (its expected result)
        mask                do not save the last typed value; replay reads it from an
                            environment variable instead
        result <n> pass|fail [note]   doing a manual scenario by hand: mark step n (see Guide)
        stop (or an empty line)

    With a `feed` file, the steps so far are written there after every change (the web UI
    shows them while recording). Masked values never reach that file.
    """

    def __init__(self, feed: Path | None = None, test_id: str = "") -> None:
        self.events: list[dict[str, Any]] = []
        self.stopped = False
        self.paused = False
        self.checking = False
        self.message = ""
        self.feed = feed
        self.prefix = secret_prefix(test_id) if test_id else "QM_SECRET"
        self._page: Any = None
        self._paused_total = 0.0
        self._paused_at: float | None = None
        self._paused_since = ""
        self.guide: Any = None  # a Guide when a manual scenario is being done by hand
        self.write_feed()

    def _receive(self, payload: dict[str, Any]) -> None:
        kind = payload.get("kind")
        if kind == "stop":
            self.stopped = True
        elif kind == "checking":
            self.checking = bool(payload.get("on"))
        elif self.paused:
            return
        else:
            if self.guide is not None and "guide_step" not in payload:
                payload["guide_step"] = self.guide.current  # the written step the tester is on
            self.events.append(payload)
            if kind in ("assert_text", "assert_visible"):
                self.checking = False
            self.message = ""
        self.write_feed()

    def attach(self, page: Any) -> None:
        self._page = page
        page.context.expose_binding("__qmRecord", lambda _source, payload: self._receive(payload))
        page.context.add_init_script(CAPTURE_JS)
        page.evaluate(CAPTURE_JS)  # current page too, not only future navigations
        page.on("load", lambda _page: self._sync_page())  # a new page starts un-paused; tell it

    def command(self, line: str) -> None:
        word, _, rest = line.strip().partition(" ")
        word = word.lower()
        if word in ("", "stop", "finish"):
            self.stopped = True
            self.message = "Saving the test."
        elif word == "pause" and not self.paused:
            self.paused, self._paused_at = True, time.monotonic()
            self._paused_since = datetime.now().astimezone().isoformat(timespec="seconds")
            self.checking = False
            self.message = "Paused. Nothing is recorded until you resume."
        elif word == "resume" and self.paused:
            self._paused_total += time.monotonic() - (self._paused_at or time.monotonic())
            self.paused, self._paused_at, self._paused_since = False, None, ""
            self.message = "Recording again."
        elif word == "check" and not self.paused:
            self.checking = not self.checking
            self.message = "Click the value to check in the browser." if self.checking else ""
        elif word == "undo":
            removable = [i for i, e in enumerate(self.events) if e.get("kind") in (*_ACTIONS, "note")]
            if removable:
                gone = self.events.pop(removable[-1])
                self.message = f"Removed the last {_LABELS.get(str(gone.get('kind')), 'step')}."
            else:
                self.message = "Nothing to undo yet."
        elif word == "note":
            text = " ".join(rest.split())[:300]
            if not text:
                self.message = "Write the note first."
            elif not any(e.get("kind") in _ACTIONS for e in self.events):
                self.message = "Record a step first; a note describes what should happen at it."
            else:
                self.events.append({"kind": "note", "value": text})
                self.message = "Note added to the last step."
        elif word == "wait" and not self.paused:
            if not any(e.get("kind") in _ACTIONS for e in self.events):
                self.message = "Submit the process first, then add the wait."
            else:
                event: dict[str, Any] = {"kind": "wait_job", "value": "last"}
                if self.guide is not None:
                    event["guide_step"] = self.guide.current
                self.events.append(event)
                self.message = (
                    "Added: wait for the scheduled process to finish. When the test plays, it reads the process "
                    "number from the screen and waits until the process ends; it passes only if it succeeded."
                )
        elif word == "result" and self.guide is not None:
            self.message = self.guide.mark(rest, self._page)
        elif word == "mask":
            typed = [e for e in self.events if e.get("kind") in ("fill", "select")]
            if typed:
                typed[-1]["sensitive"] = True
                self.message = "The last typed value will not be saved; replay reads it from an environment variable."
            else:
                self.message = "Type a value first, then mask it."
        self._sync_page()
        self.write_feed()

    def _sync_page(self) -> None:
        if self._page is None:
            return
        with suppress(Exception):  # the page is navigating; its load handler tries again
            self._page.evaluate(
                f"() => {{ window.__qmSetPaused && window.__qmSetPaused({json.dumps(self.paused)});"
                f" window.__qmSetChecking && window.__qmSetChecking({json.dumps(self.checking)}); }}"
            )

    def write_feed(self) -> None:
        if self.feed is None:
            return
        snapshot = {
            "steps": preview(self.events, self.prefix),
            "paused": self.paused,
            "paused_since": self._paused_since,  # the current pause, if any
            "paused_seconds": round(self._paused_total, 1),  # earlier, finished pauses
            "checking": self.checking,
            "masked": sum(1 for e in self.events if e.get("sensitive")),
            "message": self.message,
            "stopped": self.stopped,
        }
        if self.guide is not None:
            snapshot["guide"] = self.guide.state()
            snapshot["guide_folder"] = str(self.guide.run_dir)
        self.feed.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.feed.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot), encoding="utf-8")
        for _ in range(20):  # on Windows the reader may have the file open for a moment
            try:
                os.replace(tmp, self.feed)
                return
            except PermissionError:
                time.sleep(0.05)
