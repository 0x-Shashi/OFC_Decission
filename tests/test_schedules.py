"""Scheduled runs: when they are due, and queuing them (no real runs)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from test_service_api import call
from test_service_queue import fake_command

from quartermaster.service.api import ApiError, App
from quartermaster.service.schedules import Schedules, describe, due_slot, next_slot

WEEKDAYS = {"days": [0, 1, 2, 3, 4], "time": "02:00", "enabled": True}


def test_when_a_schedule_is_due() -> None:
    thursday_0200 = datetime(2026, 10, 1, 2, 0)  # a Thursday
    assert due_slot(WEEKDAYS, thursday_0200) == thursday_0200
    assert due_slot(WEEKDAYS, datetime(2026, 10, 1, 2, 59)) == thursday_0200  # caught up within the hour
    assert due_slot(WEEKDAYS, datetime(2026, 10, 1, 3, 0)) is None  # too late: wait for the next one
    assert due_slot(WEEKDAYS, datetime(2026, 10, 1, 1, 59)) is None
    assert due_slot(WEEKDAYS, datetime(2026, 10, 3, 2, 0)) is None  # a Saturday
    assert due_slot({**WEEKDAYS, "last_slot": "2026-10-01T02:00" + _tz(thursday_0200)}, thursday_0200) is None
    assert due_slot({**WEEKDAYS, "enabled": False}, thursday_0200) is None
    assert next_slot(WEEKDAYS, datetime(2026, 10, 2, 3, 0)) == datetime(2026, 10, 5, 2, 0)  # Friday after 2 -> Monday
    assert describe(WEEKDAYS) == "Mon to Fri at 02:00"
    assert describe({"days": [0, 2], "time": "07:30"}) == "Mon, Wed at 07:30"


def _tz(when: datetime) -> str:
    return when.astimezone().isoformat(timespec="minutes")[16:]


def test_a_due_schedule_is_queued_once(tmp_path: Path) -> None:
    queued: list[dict[str, Any]] = []
    sch = Schedules(tmp_path / "schedules.json", submit=lambda s: queued.append(s) or {"id": f"R{len(queued)}"})
    saved = sch.save({"name": "Nightly", "target": ".", **WEEKDAYS}, check_target=lambda t: None)
    now = datetime(2026, 10, 1, 2, 5)
    assert sch.tick(now) == ["R1"] and sch.tick(now) == []  # not twice for the same time
    [listed] = sch.listing(now)
    assert listed["id"] == saved["id"] and listed["last_run_id"] == "R1" and listed["when"] == "Mon to Fri at 02:00"
    assert json.loads((tmp_path / "schedules.json").read_text())[0]["last_slot"]


def test_schedules_from_the_web_page(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    (tests / "hcm").mkdir(parents=True)
    (tests / "hcm" / "a.yaml").write_text("id: a\n")
    app = App(tests_root=tests, evidence_root=tmp_path / "ev", data_dir=tmp_path / ".qm", run_command=fake_command)
    with pytest.raises(ApiError, match="choose at least one day"):
        app.handle("POST", "/api/schedules", json.dumps({"name": "x", "target": ".", "time": "02:00"}).encode())
    with pytest.raises(ApiError, match="no test file or folder"):
        app.handle("POST", "/api/schedules", json.dumps({"name": "x", "target": "nope", **WEEKDAYS}).encode())
    with pytest.raises(ApiError, match="24-hour"):
        app.handle(
            "POST", "/api/schedules", json.dumps({"name": "x", "target": ".", "days": [1], "time": "2am"}).encode()
        )
    s = call(app, "POST", "/api/schedules", {"name": "HCM nightly", "target": "hcm", **WEEKDAYS})
    assert s["next_run"] and s["when"] == "Mon to Fri at 02:00"
    run = call(app, "POST", "/api/schedules/run", {"id": s["id"]})
    assert run["options"]["label"] == "Scheduled: HCM nightly" and run["target"] == "hcm"
    assert run["options"]["screenshots"] == "every-step"
    changed = call(app, "POST", "/api/schedules", {**s, "enabled": False})
    assert changed["id"] == s["id"] and changed["next_run"] is None
    call(app, "POST", "/api/schedules/delete", {"id": s["id"]})
    assert call(app, "GET", "/api/schedules")["schedules"] == []
