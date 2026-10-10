"""Clients and environments set up on the page; passwords saved encrypted, never shown again."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quartermaster.service import vault
from quartermaster.service.api import ApiError, App
from quartermaster.service.environments import Environments

POD = "https://acme-dev2.fa.us6.oraclecloud.com"


def test_passwords_are_encrypted_and_a_changed_value_is_refused(tmp_path: Path) -> None:
    key = tmp_path / "vault.key"
    blob = vault.protect("Secret#123", key)
    assert b"Secret#123" not in blob and vault.reveal(blob, key) == "Secret#123"
    assert oct(key.stat().st_mode & 0o777) == "0o600"  # this user only
    with pytest.raises(vault.VaultError):
        vault.reveal(blob[:-1] + bytes([blob[-1] ^ 1]), key)
    with pytest.raises(vault.VaultError):
        vault.reveal(blob, tmp_path / "another.key")  # another computer's key


def setup(envs: Environments) -> str:
    client = envs.save_client({"name": "Acme Corp"})
    env = envs.save_environment(
        {"client_id": client["id"], "name": "DEV2", "url": POD, "kind": "DEV", "release": "26C"}
    )
    envs.save_user({"environment_id": env["id"], "username": "test.user", "password": "Secret#123"})
    envs.save_user({"environment_id": env["id"], "persona": "Line Manager", "username": "mgr", "password": "Mgr#1"})
    return env["id"]


def test_an_environment_with_its_users(tmp_path: Path) -> None:
    envs = Environments(tmp_path / "environments.db", key_file=tmp_path / "vault.key")
    env_id = setup(envs)
    listing = envs.listing()
    [client] = listing["clients"]
    [env] = client["environments"]
    assert env["active"] and env["host"] == "acme-dev2.fa.us6.oraclecloud.com" and env["url"] == POD + "/"
    assert env["users"] == [
        {"persona": "", "username": "test.user", "password_set": True},
        {"persona": "LINE_MANAGER", "username": "mgr", "password_set": True},
    ]
    assert "Secret" not in json.dumps(listing)
    assert b"Secret#123" not in (tmp_path / "environments.db").read_bytes()  # only encrypted on disk

    # an empty password keeps the saved one
    envs.save_user({"environment_id": env_id, "username": "test.user2", "password": ""})
    terminal = {
        "QM_FUSION_URL": "https://other-test.fa.us2.oraclecloud.com",
        "QM_FUSION_USER_OTHER": "x",
        "PATH": "/bin",
    }
    run = envs.run_environ(terminal)
    assert run["QM_FUSION_URL"] == POD + "/" and run["QM_FUSION_KIND"] == "DEV" and run["PATH"] == "/bin"
    assert run["QM_ENV_NAME"] == env["name"]  # the pod's own test data is chosen by this name or its kind
    assert (run["QM_FUSION_USER"], run["QM_FUSION_PASSWORD"]) == ("test.user2", "Secret#123")
    assert (run["QM_FUSION_USER_LINE_MANAGER"], run["QM_FUSION_PASSWORD_LINE_MANAGER"]) == ("mgr", "Mgr#1")
    assert "QM_FUSION_USER_OTHER" not in run  # another client's user never comes along


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"kind": "PROD"}, "never tests a production pod"),
        ({"url": "https://acme.fa.us6.oraclecloud.com"}, "looks like a production pod"),
        ({"url": "http://acme-dev2.fa.us6.oraclecloud.com"}, "enter the pod address"),
        ({"name": ""}, "give the environment a name"),
        ({"release": "<b>"}, "the release may use"),
    ],
)
def test_a_production_or_wrong_pod_is_refused(tmp_path: Path, change: dict[str, str], message: str) -> None:
    envs = Environments(tmp_path / "environments.db", key_file=tmp_path / "vault.key")
    client = envs.save_client({"name": "Acme"})
    with pytest.raises(ValueError, match=message):
        envs.save_environment({"client_id": client["id"], "name": "DEV2", "url": POD, "kind": "DEV", **change})


def test_the_pod_set_in_the_terminal_becomes_the_first_client(tmp_path: Path) -> None:
    envs = Environments(tmp_path / "environments.db", key_file=tmp_path / "vault.key")
    terminal = {
        "QM_FUSION_URL": POD + "/fscmUI/faces/FuseWelcome",
        "QM_FUSION_USER": "test.user",
        "QM_FUSION_PASSWORD": "Secret#123",
        "QM_FUSION_USER_LINE_MANAGER": "mgr",
        "QM_FUSION_PASSWORD_LINE_MANAGER": "Mgr#1",
    }
    assert envs.import_from(terminal, name="EIIV DEV2", release="26C")
    assert not envs.import_from(terminal)  # only once
    active = envs.active()
    assert active and (active["client"], active["name"], active["release"]) == ("My first client", "EIIV DEV2", "26C")
    assert active["url"].endswith("/fscmUI/faces/FuseWelcome")
    assert envs.run_environ({})["QM_FUSION_PASSWORD_LINE_MANAGER"] == "Mgr#1"

    refused = Environments(tmp_path / "other.db", key_file=tmp_path / "vault.key")
    for url in ("http://127.0.0.1:8766/", "https://acme.fa.us6.oraclecloud.com"):  # not https; production
        assert not refused.import_from({"QM_FUSION_URL": url, "QM_FUSION_USER": "u", "QM_FUSION_PASSWORD": "p"})
    assert refused.listing()["clients"] == []  # nothing half made


def call(app: App, path: str, body: dict | None = None) -> dict:  # type: ignore[type-arg]
    method = "POST" if body is not None else "GET"
    return json.loads(app.handle(method, path, json.dumps(body).encode() if body is not None else b"").body)


def test_set_up_on_the_page_and_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("QM_FUSION_URL", "QM_FUSION_USER", "QM_FUSION_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("QM_VAULT_KEY_FILE", str(tmp_path / "vault.key"))
    app = App(tests_root=tmp_path / "tests", evidence_root=tmp_path / "ev", data_dir=tmp_path / ".qm")
    status = call(app, "/api/status")
    assert not status["ready"] and status["set_up_in"] == ""

    acme = call(app, "/api/environments/client", {"name": "Acme Corp"})
    dev = call(
        app, "/api/environments/environment", {"client_id": acme["id"], "name": "DEV2", "url": POD, "kind": "DEV"}
    )
    call(
        app, "/api/environments/user", {"environment_id": dev["id"], "username": "test.user", "password": "Secret#123"}
    )
    status = call(app, "/api/status")
    assert status["ready"] and (status["client"], status["environment_name"], status["user"]) == (
        "Acme Corp",
        "DEV2",
        "test.user",
    )
    assert status["password_set"] and "Secret" not in json.dumps(status)

    globex = call(app, "/api/environments/client", {"name": "Globex"})
    test = call(
        app,
        "/api/environments/environment",
        {
            "client_id": globex["id"],
            "name": "TEST1",
            "url": "globex-test.fa.us2.oraclecloud.com",
            "kind": "TEST",
        },
    )
    assert call(app, "/api/status")["client"] == "Acme Corp"  # adding one does not switch to it
    call(app, "/api/environments/activate", {"id": test["id"]})
    status = call(app, "/api/status")
    assert (status["client"], status["pod_host"], status["ready"]) == (
        "Globex",
        "globex-test.fa.us2.oraclecloud.com",
        False,
    )
    call(app, "/api/settings", {"release": "26D"})
    assert call(app, "/api/status")["release"] == "26D"  # the release belongs to the environment
    assert app.queue.environ and app.queue.environ()["QM_FUSION_URL"] == "https://globex-test.fa.us2.oraclecloud.com/"

    with pytest.raises(ApiError, match="there is already a client called Acme Corp"):
        call(app, "/api/environments/client", {"name": "acme corp"})
    log = (tmp_path / ".qm" / "audit.jsonl").read_text()
    assert "Saved a test user" in log and "Switched environment" in log and "Secret" not in log


def test_a_test_pod_without_a_test_marker_needs_a_tick(tmp_path: Path) -> None:
    from quartermaster.domain.models import Environment, EnvironmentKind
    from quartermaster.safety.guards import UnsafeEnvironmentError, assert_safe_target, confirmed_hosts

    envs = Environments(tmp_path / "environments.db", key_file=tmp_path / "vault.key")
    client = envs.save_client({"name": "Initech"})
    pod = {"client_id": client["id"], "name": "QA", "url": "https://initech-qa1.fa.em2.oraclecloud.com", "kind": "TEST"}
    envs.save_environment(pod)  # qa is a test marker
    plain = {**pod, "name": "Sandbox", "url": "https://initechsbx.fa.em2.oraclecloud.com"}
    with pytest.raises(ValueError, match="tick 'This is a test pod, not production'"):
        envs.save_environment(plain)
    saved = envs.save_environment({**plain, "not_production": True})
    envs.activate(saved["id"])
    run = envs.run_environ({"QM_FUSION_ALLOWED_HOSTS": "someone-else.example.com"})
    assert run["QM_FUSION_ALLOWED_HOSTS"] == "initechsbx.fa.em2.oraclecloud.com"
    # the run accepts that host only, never a production kind
    hosts = confirmed_hosts(run)
    assert_safe_target(
        Environment(name="x", url="https://initechsbx.fa.em2.oraclecloud.com", kind=EnvironmentKind.TEST), hosts
    )
    with pytest.raises(UnsafeEnvironmentError):
        assert_safe_target(
            Environment(name="x", url="https://other.fa.em2.oraclecloud.com", kind=EnvironmentKind.TEST), hosts
        )
    with pytest.raises(UnsafeEnvironmentError):
        assert_safe_target(
            Environment(name="x", url="https://initechsbx.fa.em2.oraclecloud.com", kind=EnvironmentKind.PROD), hosts
        )
