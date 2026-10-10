"""Scheduled runs: run tests by themselves on chosen days at a chosen time, for example every
night at 02:00, or every Monday after the release lands on the pod.

Schedules are kept in one JSON file in the data folder. A timer in `qm serve` checks them every 30
seconds and queues a run when one is due; the run is then like any other (label "Scheduled: name").
Schedules only run while `qm serve` is running: a time missed while it was stopped is caught up
only within the next hour, so a laptop opened in the morning does not start last night's run late.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
CATCH_UP = timedelta(hours=1)
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class Schedules:
    def __init__(self, path: Path, submit: Callable[[dict[str, Any]], dict[str, Any]]):
        self.path = path
        self._submit = submit  # queues a run for a schedule, returns the run
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ stored schedules

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []

    def _save(self, items: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items, indent=1), encoding="utf-8")

    def listing(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now()
        return [{**s, "next_run": _iso(next_slot(s, now)), "when": describe(s)} for s in self._load()]

    def save(
        self,
        data: dict[str, Any],
        check_target: Callable[[str], Any],
        check_suite: Callable[[str], Any] | None = None,
    ) -> dict[str, Any]:
        """Create a schedule, or change the one with this id. It runs a folder or test file (`target`) or, when
        `suite` names a saved suite, the tests that suite has at the time it runs."""
        name = " ".join(str(data.get("name") or "").split())[:80]
        if not name:
            raise ValueError("give the schedule a name")
        suite = str(data.get("suite") or "").strip()
        target = "." if suite else str(data.get("target") or ".").strip() or "."
        check_target(target)  # inside the tests folder, and there
        if suite and check_suite is not None:
            check_suite(suite)  # a suite of that name exists
        days = sorted({int(d) for d in data.get("days") or [] if str(d).isdigit() and 0 <= int(d) <= 6})
        if not days:
            raise ValueError("choose at least one day")
        time = str(data.get("time") or "").strip()
        if not _TIME.match(time):
            raise ValueError("the time must be like 02:00 (24-hour clock)")
        given = data.get("options")
        options: dict[str, Any] = given if isinstance(given, dict) else {}
        kept = {k: options[k] for k in ("screenshots", "video", "highlight") if k in options}
        with self._lock:
            items = self._load()
            old = next((s for s in items if s.get("id") == data.get("id")), None)
            schedule = {
                "id": old["id"] if old else uuid.uuid4().hex[:10],
                "name": name,
                "target": target,
                "suite": suite,
                "days": days,
                "time": time,
                "enabled": bool(data.get("enabled", True)),
                "options": kept,
                "created_at": old.get("created_at") if old else _iso(datetime.now()),
                "last_slot": old.get("last_slot") if old else None,
                "last_run_id": old.get("last_run_id") if old else None,
            }
            items = [schedule if s is old else s for s in items] if old else [*items, schedule]
            self._save(items)
        return {**schedule, "next_run": _iso(next_slot(schedule, datetime.now())), "when": describe(schedule)}

    def delete(self, schedule_id: str) -> None:
        with self._lock:
            items = self._load()
            kept = [s for s in items if s.get("id") != schedule_id]
            if len(kept) == len(items):
                raise LookupError("no such schedule")
            self._save(kept)

    def run_now(self, schedule_id: str) -> dict[str, Any]:
        schedule = next((s for s in self._load() if s.get("id") == schedule_id), None)
        if schedule is None:
            raise LookupError("no such schedule")
        return self._submit(schedule)

    # ------------------------------------------------------------------ the timer

    def tick(self, now: datetime | None = None) -> list[str]:
        """Queue the runs that are due now; returns their ids."""
        now = now or datetime.now()
        started: list[str] = []
        with self._lock:
            items = self._load()
            changed = False
            for s in items:
                slot = due_slot(s, now)
                if slot is None:
                    continue
                s["last_slot"] = _iso(slot)  # also when queuing fails: never retried in a loop
                changed = True
                try:
                    run = self._submit(s)
                except (ValueError, LookupError) as e:  # e.g. the folder was removed
                    s["last_error"] = str(e)
                    continue
                s["last_run_id"], s["last_error"] = run.get("id"), None
                started.append(str(run.get("id")))
            if changed:
                self._save(items)
        return started

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="qm-schedules", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(30):
            with suppress(Exception):  # a broken file must not stop the timer; the page shows the schedules
                self.tick()


def due_slot(schedule: dict[str, Any], now: datetime) -> datetime | None:
    """The time this schedule should have started at, when that is now or within the last hour and it
    has not run for it yet."""
    if not schedule.get("enabled", True) or not _TIME.match(str(schedule.get("time", ""))):
        return None
    hour, minute = (int(x) for x in str(schedule["time"]).split(":"))
    for back in (0, 1):  # today, or yesterday just before midnight
        day = (now - timedelta(days=back)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day.weekday() in schedule.get("days", []) and day <= now < day + CATCH_UP:
            return None if schedule.get("last_slot") == _iso(day) else day
    return None


def next_slot(schedule: dict[str, Any], now: datetime) -> datetime | None:
    if not schedule.get("enabled", True) or not _TIME.match(str(schedule.get("time", ""))):
        return None
    hour, minute = (int(x) for x in str(schedule["time"]).split(":"))
    for ahead in range(8):
        day = (now + timedelta(days=ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day > now and day.weekday() in schedule.get("days", []):
            return day
    return None


def describe(schedule: dict[str, Any]) -> str:
    """ "Every day at 02:00", "Mon to Fri at 06:30", "Mon, Wed at 07:00"."""
    days = sorted(schedule.get("days") or [])
    if days == list(range(7)):
        text = "Every day"
    elif days == list(range(5)):
        text = "Mon to Fri"
    elif days == [5, 6]:
        text = "Weekends"
    else:
        text = ", ".join(DAY_NAMES[d] for d in days)
    return f"{text} at {schedule.get('time', '')}"


def _iso(when: datetime | None) -> str | None:
    return when.astimezone().isoformat(timespec="minutes") if when else None
