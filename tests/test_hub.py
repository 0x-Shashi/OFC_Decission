"""Clients kept apart: each has its own tests, runs, scenarios and audit log, and runs on its own pod."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_service_queue import fake_command, wait_for

from quartermaster.service.hub import Hub

ACME = "https://acme-dev2.fa.us6.oraclecloud.com"
GLOBEX = "https://globex-test.fa.us2.oraclecloud.com"


def call(hub: Hub, path: str, body: dict[str, Any] | None = None) -> Any:
    method = "POST" if body is not None else "GET"
    return json.loads(hub.handle(method, path, json.dumps(body).encode() if body is not None else b"").body)


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    for var in ("QM_FUSION_URL", "QM_FUSION_USER", "QM_FUSION_PASSWORD", "QM_FUSION_SESSION"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("QM_VAULT_KEY_FILE", str(tmp_path / "vault.key"))
    monkeypatch.chdir(tmp_path)
    seed = tmp_path / "examples"
    seed.mkdir()
    (seed / "example.yaml").write_text("id: ex.one\ntitle: An example\nmodule: HCM\nsteps: [{}]\n")
    (tmp_path / "my_tests").mkdir()
    (tmp_path / "my_tests" / "mine.yaml").write_text("id: mine.one\ntitle: Mine\nmodule: HCM\nsteps: [{}]\n")
    h = Hub(
        tests_root=tmp_path / "my_tests",
        evidence_root=tmp_path / "evidence",
        data_dir=tmp_path / ".qm",
        seed_tests=seed,
        run_command=fake_command,
    )
    h.start()
    yield h
    h.stop()


def add(hub: Hub, client: str, name: str, url: str, user: str) -> tuple[str, str]:
    c = call(hub, "/api/environments/client", {"name": client})
    e = call(hub, "/api/environments/environment", {"client_id": c["id"], "name": name, "url": url, "kind": "DEV"})
    call(hub, "/api/environments/user", {"environment_id": e["id"], "username": user, "password": f"{user}-pw"})
    return c["id"], e["id"]


def test_each_client_has_its_own_workspace(hub: Hub, tmp_path: Path) -> None:
    assert [t["id"] for t in call(hub, "/api/tests")] == ["mine.one"]  # nothing set up: the default folders
    acme, _ = add(hub, "Acme Corp", "DEV2", ACME, "acme.user")
    globex, globex_env = add(hub, "Globex", "TEST1", GLOBEX, "globex.user")

    # the first client keeps the default folders; another starts with copies of the examples
    assert hub.folders(acme) == (tmp_path / "my_tests", tmp_path / "evidence", tmp_path / ".qm")
    tests, evidence, data = hub.folders(globex)
    assert tests.parent.parent == tmp_path / "clients" and tests.parent.name.startswith("globex-")
    assert data == tmp_path / ".qm" / "clients" / globex

    # Acme is in use: its tests, its run
    assert call(hub, "/api/status")["client"] == "Acme Corp"
    run = call(hub, "/api/runs", {"target": "mine.yaml"})
    wait_for(hub.app.queue, run["id"], ("passed",))
    call(
        hub, "/api/manual/typed", {"title": "Acme only", "module": "HCM", "product": "Core", "steps": [{"action": "x"}]}
    )

    # switch to Globex: none of Acme's tests, runs or scenarios
    call(hub, "/api/environments/activate", {"id": globex_env})
    status = call(hub, "/api/status")
    assert (status["client"], status["pod_host"], status["user"]) == (
        "Globex",
        "globex-test.fa.us2.oraclecloud.com",
        "globex.user",
    )
    assert [t["id"] for t in call(hub, "/api/tests")] == ["ex.one"]
    assert call(hub, "/api/runs") == []
    assert call(hub, "/api/manual")["scenarios"] == []
    entries = json.loads(hub.handle("GET", "/api/audit", b"").body)["entries"]
    assert all("Acme only" not in json.dumps(e) for e in entries)

    # back to Acme: all still there
    acme_env = call(hub, "/api/environments")["clients"][0]["environments"][0]["id"]
    call(hub, "/api/environments/activate", {"id": acme_env})
    assert [r["id"] for r in call(hub, "/api/runs")] == [run["id"]]
    assert [s["title"] for s in call(hub, "/api/manual")["scenarios"]] == ["Acme only"]


def test_each_client_runs_on_its_own_pod_even_when_another_is_in_use(hub: Hub) -> None:
    acme, acme_env = add(hub, "Acme Corp", "DEV2", ACME, "acme.user")
    globex, globex_env = add(hub, "Globex", "TEST1", GLOBEX, "globex.user")
    spaces = hub.workspaces()
    assert spaces[acme].run_environ()["QM_FUSION_URL"] == ACME + "/"
    globex_run = spaces[globex].run_environ()  # e.g. a Globex schedule firing while Acme is in use
    assert (globex_run["QM_FUSION_URL"], globex_run["QM_FUSION_USER"]) == (GLOBEX + "/", "globex.user")
    assert globex_run["QM_FUSION_PASSWORD"] == "globex.user-pw"

    # a sign-in by hand belongs to its pod only
    spaces[acme].signin._sessions[acme_env] = "acme-session"
    assert spaces[acme].run_environ()["QM_FUSION_SESSION"] == "acme-session"
    assert "QM_FUSION_SESSION" not in spaces[globex].run_environ()
    assert globex_env


def test_a_deleted_client_stops_its_workspace(hub: Hub) -> None:
    add(hub, "Acme Corp", "DEV2", ACME, "acme.user")
    globex, _ = add(hub, "Globex", "TEST1", GLOBEX, "globex.user")
    assert globex in hub.workspaces()
    call(hub, "/api/environments/client/delete", {"id": globex})
    assert globex not in hub.workspaces()
