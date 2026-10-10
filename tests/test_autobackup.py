"""Automatic backups: one a day when Quartermaster is running, the newest few kept."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from test_auth import make_hub

from quartermaster.service import backup
from quartermaster.service.api import ApiError
from quartermaster.service.autobackup import AutoBackup, AutoBackupError


class Clock:
    def __init__(self, at: str = "2026-10-03T09:00:00+00:00") -> None:
        self.t = datetime.fromisoformat(at)

    def __call__(self) -> datetime:
        return self.t

    def later(self, **kw: float) -> None:
        self.t += timedelta(**kw)


def folders(tmp_path: Path) -> backup.Folders:
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "t.yaml").write_text("id: a\n")
    (tmp_path / ".qm").mkdir(exist_ok=True)
    (tmp_path / ".qm" / "settings.json").write_text("{}")
    return backup.Folders(tmp_path / "tests", tmp_path / "evidence", tmp_path / ".qm")


def make(tmp_path: Path, clock: Clock | None = None) -> tuple[AutoBackup, Clock, list[tuple[str, str, dict[str, Any]]]]:
    told: list[tuple[str, str, dict[str, Any]]] = []
    clock = clock or Clock()
    auto = AutoBackup(folders(tmp_path), audit=lambda w, s, d: told.append((w, s, d)), now=clock)
    return auto, clock, told


def test_it_is_on_by_default_at_two_in_the_morning_and_keeps_seven(tmp_path: Path) -> None:
    auto, _, _ = make(tmp_path)
    s = auto.settings()
    assert (s["enabled"], s["time"], s["keep"], s["evidence"]) == (True, "02:00", 7, False)
    assert auto.copies() == []


def test_a_backup_is_made_when_the_time_has_passed_and_none_was_made_since(tmp_path: Path) -> None:
    auto, clock, told = make(tmp_path)  # 09:00, nothing made yet: today's 02:00 was missed (the laptop was off)
    path = auto.run_if_due()
    assert path is not None and path.parent == auto.folder and path.name.startswith("quartermaster-auto-")
    assert "tests/t.yaml" in zipfile.ZipFile(path).namelist() and backup.inspect(path.read_bytes())["format"] == 1
    assert told[-1][0] == "Made an automatic backup"
    assert auto.run_if_due() is None  # not twice the same day
    clock.later(hours=16)  # 01:00 the next night: not yet
    assert auto.run_if_due() is None
    clock.later(hours=2)  # 03:00
    assert auto.run_if_due() is not None


def test_before_todays_time_yesterdays_backup_is_the_one_that_counts(tmp_path: Path) -> None:
    auto, clock, _ = make(tmp_path, Clock("2026-10-03T01:00:00+00:00"))
    assert auto.due()  # nothing at all yet
    auto.make()
    assert not auto.due()  # made at 01:00, after yesterday's 02:00
    clock.later(hours=1.5)  # 02:30: today's slot has come, nothing since
    assert auto.due()


def test_it_can_be_switched_off(tmp_path: Path) -> None:
    auto, _, _ = make(tmp_path)
    auto.update({"enabled": False})
    assert not auto.due() and auto.run_if_due() is None and auto.copies() == []


def test_only_the_newest_copies_are_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    auto, _, _ = make(tmp_path)
    auto.update({"keep": 3})
    names = iter(f"20261003-0{i}0000" for i in range(1, 7))
    real = backup.stamp
    monkeypatch.setattr(backup, "stamp", lambda: next(names))
    try:
        for _ in range(5):
            auto.make()
    finally:
        monkeypatch.setattr(backup, "stamp", real)
    assert [c["name"] for c in auto.copies()] == [f"quartermaster-auto-20261003-0{i}0000.zip" for i in (5, 4, 3)]


def test_settings_are_checked_and_saved(tmp_path: Path) -> None:
    auto, _, _ = make(tmp_path)
    for bad, why in (
        ({"time": "25:00"}, "like 02:00"),
        ({"time": "2am"}, "like 02:00"),
        ({"keep": 0}, "between 1 and 30"),
        ({"keep": 31}, "between 1 and 30"),
        ({"keep": "many"}, "must be a number"),
        ({"colour": "red"}, "unknown settings"),
    ):
        with pytest.raises(AutoBackupError, match=why):
            auto.update(bad)
    auto.update({"time": "23:30", "keep": 10, "evidence": True})
    again = AutoBackup(folders(tmp_path)).settings()  # saved in the data folder
    assert (again["time"], again["keep"], again["evidence"]) == ("23:30", 10, True)


def test_a_failed_backup_is_reported_and_tried_again_later(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    auto, _, told = make(tmp_path)
    real = backup.create
    broken = {"on": True}

    def sometimes(*a: Any, **k: Any) -> bytes:
        if broken["on"]:
            raise OSError("No space left on device")
        return real(*a, **k)

    monkeypatch.setattr(backup, "create", sometimes)
    assert auto.run_if_due() is None
    assert "No space left" in auto.settings()["last_error"] and told[-1][0] == "Automatic backup failed"
    assert auto.due()  # still owed
    broken["on"] = False
    assert auto.run_if_due() is not None and auto.settings()["last_error"] is None


def test_the_backup_has_no_passwords_accounts_or_earlier_automatic_copies(tmp_path: Path) -> None:
    auto, _, _ = make(tmp_path)
    (tmp_path / ".qm" / "users.db").write_text("accounts")
    first = auto.make()
    assert first is not None
    second = auto.make()  # a copy made now does not contain the first one
    assert second is not None
    names = zipfile.ZipFile(second).namelist()
    assert not any("users.db" in n or "backups/" in n for n in names)


def test_only_listed_copies_can_be_read(tmp_path: Path) -> None:
    auto, _, _ = make(tmp_path)
    path = auto.make()
    assert path is not None and auto.read(path.name) == path.read_bytes()
    (tmp_path / ".qm" / "secret.txt").write_text("no")
    for name in ("../../secret.txt", "../autobackup.json", "nope.zip", ""):
        with pytest.raises(AutoBackupError, match="no such automatic backup"):
            auto.read(name)


# ------------------------------------------------------------------ in the running service


def test_the_service_shows_changes_runs_downloads_and_restores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = make_hub(tmp_path, monkeypatch)

    def call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        reply = hub.handle(method, path, json.dumps(body).encode() if body is not None else b"")
        return reply

    view = json.loads(call("GET", "/api/backup/auto").body)
    assert view["enabled"] is True and view["copies"] == [] and view["folder"].endswith("auto")
    changed = json.loads(call("POST", "/api/backup/auto", {"keep": 4, "time": "03:15"}).body)
    assert (changed["keep"], changed["time"]) == (4, "03:15")
    with pytest.raises(ApiError, match="between 1 and 30"):
        call("POST", "/api/backup/auto", {"keep": 99})

    ran = json.loads(call("POST", "/api/backup/auto/run").body)
    (copy,) = ran["copies"]
    got = call("GET", f"/api/backup/auto/file?name={copy['name']}")
    assert got.content_type == "application/zip" and got.body[:2] == b"PK" and got.download_name == copy["name"]
    with pytest.raises(ApiError, match="no such automatic backup"):
        call("GET", "/api/backup/auto/file?name=../../vault.key")

    staged = json.loads(call("POST", "/api/backup/auto/restore", {"name": copy["name"]}).body)
    assert staged["pending"]["format"] == 1
    assert json.loads(call("GET", "/api/backup/status").body)["pending"] is not None
    actions = [e["action"] for e in json.loads(call("GET", "/api/audit").body)["entries"]]
    assert {"Made an automatic backup", "Changed the automatic backup settings", "Chose a backup to restore"} <= set(
        actions
    )
