"""The audit log: who changed what, written as it happens, without secrets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_service_queue import fake_command

from quartermaster.service.api import ApiError, App
from quartermaster.service.audit import AuditLog, describe


def post(app: App, path: str, body: dict) -> dict:  # type: ignore[type-arg]
    return json.loads(app.handle("POST", path, json.dumps(body).encode()).body)


def test_changes_are_written_as_they_happen_without_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QM_FUSION_URL", "https://abcd-dev2.fa.us6.oraclecloud.com")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "a.yaml").write_text("id: a\ntitle: A\nmodule: HCM\nsteps: [{}]\n")
    app = App(tests_root=tests, evidence_root=tmp_path / "ev", data_dir=tmp_path / ".qm", run_command=fake_command)
    app.audit._who = lambda: "pat"  # the user signed in to this computer

    post(app, "/api/settings", {"release": "26C"})
    run = post(app, "/api/runs", {"target": "a.yaml"})
    post(app, "/api/schedules", {"name": "Nightly", "target": "a.yaml", "days": [0], "time": "02:00"})
    typed = post(
        app,
        "/api/manual/typed",
        {"title": "Address", "module": "HCM", "product": "Core", "steps": [{"action": "Click Me"}]},
    )
    post(app, "/api/manual/data", {"id": typed["id"], "values": {"1": "Password123"}})
    with pytest.raises(ApiError):  # a refused request is not written
        app.handle("POST", "/api/runs", json.dumps({"target": "../outside"}).encode())

    entries = json.loads(app.handle("GET", "/api/audit", b"").body)["entries"]
    assert [e["action"] for e in entries] == [
        "Saved test data",
        "Typed a new scenario",
        "Added a schedule",
        "Started a run",
        "Changed settings",
    ]
    assert all(e["who"] == "pat" for e in entries)
    assert entries[3]["details"] == {"run": run["id"], "release": "26C"} and entries[3]["subject"] == "a.yaml"
    assert entries[0]["details"] == {"steps": ["1"]}
    log = (tmp_path / ".qm" / "audit.jsonl").read_text()
    assert "Password123" not in log  # test data values are not written

    csv = app.handle("GET", "/api/audit.csv", b"")
    assert csv.download_name and csv.download_name.endswith(".csv")
    lines = csv.body.decode("utf-8-sig").splitlines()
    assert lines[0] == "When,Who,What,Subject,Details" and "Changed settings" in lines[1]  # oldest first


def test_secrets_never_reach_the_log() -> None:
    said = describe(["ai", "key"], {"key": "sk-very-secret-key"}, {})
    assert said == ("Entered the AI key", "AI", {})
    assert describe(["ai", "key"], {"key": ""}, {}) == ("Removed the AI key", "AI", {})
    assert describe(["signin"], {}, {"status": "waiting"}) == ("Opened a browser to sign in by hand", "Sign-in", {})
    assert describe(["check-pod"], {}, {}) is None  # changes nothing
    assert describe(["recording", "pause"], {}, {}) is None


def test_who_is_the_tester_when_named(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "a.jsonl", who=lambda: "pat")
    log.add("Ran a manual scenario", "ess/ESS-001", {"how": "by hand", "empty": ""}, who="Sam Tester")
    log.add("Started a scheduled run", "Nightly", who="Schedule")
    (tmp_path / "a.jsonl").open("a").write('{"cut short')  # a line cut short when the computer stopped
    first, second = reversed(log.entries())
    assert (first["who"], first["details"]) == ("Sam Tester", {"how": "by hand"})
    assert second["who"] == "Schedule"
