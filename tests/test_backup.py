"""Backup and restore: one zip with what cannot be made again, and never the passwords."""

from __future__ import annotations

import base64
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

import pytest
from test_service_queue import fake_command

from quartermaster.service import backup
from quartermaster.service.api import ApiError
from quartermaster.service.hub import Hub

ACME = "https://acme-dev2.fa.us6.oraclecloud.com"
GLOBEX = "https://globex-test.fa.us2.oraclecloud.com"
SPEC = "id: {id}\ntitle: {id}\nmodule: HCM\nsteps: [{{}}]\n"


def call(hub: Hub, path: str, body: dict[str, Any] | None = None) -> Any:
    method = "POST" if body is not None else "GET"
    return json.loads(hub.handle(method, path, json.dumps(body).encode() if body is not None else b"").body)


def make_hub(root: Path, monkeypatch: pytest.MonkeyPatch) -> Hub:
    for var in ("QM_FUSION_URL", "QM_FUSION_USER", "QM_FUSION_PASSWORD", "QM_FUSION_SESSION"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("QM_VAULT_KEY_FILE", str(root / "vault.key"))
    monkeypatch.chdir(root)
    (root / "my_tests").mkdir(exist_ok=True)
    return Hub(
        tests_root=root / "my_tests",
        evidence_root=root / "evidence",
        data_dir=root / ".qm",
        run_command=fake_command,
    )


def add_client(hub: Hub, client: str, url: str, user: str) -> str:
    c = call(hub, "/api/environments/client", {"name": client})
    e = call(hub, "/api/environments/environment", {"client_id": c["id"], "name": "DEV", "url": url, "kind": "DEV"})
    call(hub, "/api/environments/user", {"environment_id": e["id"], "username": user, "password": f"{user}-pw"})
    return str(c["id"])


def passwords(hub: Hub) -> dict[str, bool]:
    return {
        u["username"]: u["password_set"]
        for c in call(hub, "/api/environments")["clients"]
        for e in c["environments"]
        for u in e["users"]
    }


def folder_of(root: Path) -> Path:
    """The folder of the (only) client that does not use the default folders."""
    return next(p for p in (root / "clients").iterdir())


def folders(root: Path) -> backup.Folders:
    return backup.Folders(root / "my_tests", root / "evidence", root / ".qm")


def names(content: bytes) -> list[str]:
    return zipfile.ZipFile(io.BytesIO(content)).namelist()


# ------------------------------------------------------------------ the zip


def test_a_backup_holds_tests_data_and_clients_but_no_evidence_unless_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = make_hub(tmp_path, monkeypatch)
    add_client(hub, "Acme", ACME, "acme-user")
    second = add_client(hub, "Globex", GLOBEX, "globex-user")
    hub.start()
    hub.stop()
    (tmp_path / "my_tests" / "mine.yaml").write_text(SPEC.format(id="mine.one"))
    (folder_of(tmp_path) / "tests").mkdir(parents=True, exist_ok=True)
    (folder_of(tmp_path) / "tests" / "theirs.yaml").write_text(SPEC.format(id="theirs.one"))
    (tmp_path / "evidence" / "mine.one").mkdir(parents=True)
    (tmp_path / "evidence" / "mine.one" / "shot.png").write_bytes(b"png")
    folder = folder_of(tmp_path)
    (folder / "evidence").mkdir(exist_ok=True)
    (folder / "evidence" / "shot.png").write_bytes(b"png")
    (tmp_path / ".qm" / "backups").mkdir(exist_ok=True)
    (tmp_path / ".qm" / "backups" / "old.zip").write_bytes(b"zip")
    assert second

    plain = names(backup.create(folders(tmp_path)))
    assert "backup.json" in plain and "tests/mine.yaml" in plain and "data/environments.db" in plain
    assert any(n.startswith("clients/") and n.endswith("/tests/theirs.yaml") for n in plain)  # a second client's tests
    assert not [n for n in plain if "shot.png" in n]  # evidence is left out unless asked for
    assert not [n for n in plain if "backups/" in n]  # earlier safety copies are never part of a backup
    full = names(backup.create(folders(tmp_path), include_evidence=True))
    assert "evidence/mine.one/shot.png" in full and any(n.endswith("evidence/shot.png") for n in full)


def test_passwords_are_not_in_the_backup_but_stay_in_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = make_hub(tmp_path, monkeypatch)
    add_client(hub, "Acme", ACME, "acme-user")
    assert passwords(hub) == {"acme-user": True}
    content = backup.create(folders(tmp_path))
    copy = tmp_path / "copy.db"
    copy.write_bytes(zipfile.ZipFile(io.BytesIO(content)).read("data/environments.db"))
    assert sqlite3.connect(str(copy)).execute("SELECT count(*) FROM users WHERE secret IS NOT NULL").fetchone() == (0,)
    assert sqlite3.connect(str(copy)).execute("SELECT count(*) FROM users").fetchone() == (1,)
    assert passwords(hub) == {"acme-user": True}  # the live database was not touched
    assert b"acme-user-pw" not in content


# ------------------------------------------------------------------ restoring


def test_restore_on_the_same_computer_brings_back_files_and_keeps_passwords(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = make_hub(tmp_path, monkeypatch)
    add_client(hub, "Acme", ACME, "acme-user")
    hub.stop()
    (tmp_path / "my_tests" / "keep.yaml").write_text(SPEC.format(id="keep.one"))
    content = backup.create(folders(tmp_path))

    (tmp_path / "my_tests" / "keep.yaml").unlink()  # a mistake after the backup
    (tmp_path / "my_tests" / "later.yaml").write_text(SPEC.format(id="later.one"))
    info = backup.restore(folders(tmp_path), content)

    assert info["format"] == backup.FORMAT
    assert (tmp_path / "my_tests" / "keep.yaml").is_file()
    assert not (tmp_path / "my_tests" / "later.yaml").exists()  # replaced, as it was when backed up
    again = make_hub(tmp_path, monkeypatch)
    assert passwords(again) == {"acme-user": True}
    safety = list((tmp_path / ".qm" / "backups").glob("before-restore-*.zip"))
    assert len(safety) == 1 and "tests/later.yaml" in names(safety[0].read_bytes())  # the undo copy


def test_restore_on_a_new_computer_brings_back_clients_but_asks_for_passwords_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    hub = make_hub(old, monkeypatch)
    add_client(hub, "Acme", ACME, "acme-user")
    hub.stop()
    content = backup.create(folders(old))

    fresh = make_hub(new, monkeypatch)
    fresh.stop()
    backup.restore(folders(new), content)
    again = make_hub(new, monkeypatch)
    assert passwords(again) == {"acme-user": False}  # the user is there, the password must be typed again


def test_restore_keeps_evidence_that_the_backup_does_not_hold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = make_hub(tmp_path, monkeypatch)
    add_client(hub, "Acme", ACME, "acme-user")
    add_client(hub, "Globex", GLOBEX, "globex-user")
    hub.start()
    hub.stop()
    content = backup.create(folders(tmp_path))
    (tmp_path / "evidence").mkdir(exist_ok=True)
    (tmp_path / "evidence" / "proof.txt").write_text("proof")
    client = folder_of(tmp_path)
    (client / "tests").mkdir(exist_ok=True)
    (client / "tests" / "theirs.yaml").write_text(SPEC.format(id="theirs.one"))
    content = backup.create(folders(tmp_path))
    (client / "evidence").mkdir(exist_ok=True)
    (client / "evidence" / "proof.txt").write_text("proof")
    backup.restore(folders(tmp_path), content)
    assert (tmp_path / "evidence" / "proof.txt").read_text() == "proof"
    assert (client / "evidence" / "proof.txt").read_text() == "proof"
    assert (client / "tests" / "theirs.yaml").is_file()


def test_the_safety_copies_are_pruned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = make_hub(tmp_path, monkeypatch)
    hub.stop()
    content = backup.create(folders(tmp_path))
    saved = tmp_path / ".qm" / backup.BACKUPS
    saved.mkdir(parents=True, exist_ok=True)
    for n in range(8):
        (saved / f"before-restore-2020010{n}-000000.zip").write_bytes(b"x")
    backup.restore(folders(tmp_path), content)
    assert len(list(saved.glob("before-restore-*.zip"))) == backup.KEEP_COPIES


# ------------------------------------------------------------------ a bad backup changes nothing


def _zip(files: dict[str, str], manifest: dict[str, Any] | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("backup.json", json.dumps(manifest or {"format": backup.FORMAT}))
        for name, text in files.items():
            z.writestr(name, text)
    return out.getvalue()


@pytest.mark.parametrize(
    "name",
    [
        "../evil.txt",
        "tests/../../evil.txt",
        "/etc/evil",
        "tests\\..\\evil",
        "C:/evil",
        "notes.txt",
        "other/x.txt",
        "tests/",
    ],
)
def test_a_file_that_could_land_outside_the_folders_is_refused(name: str) -> None:
    with pytest.raises(backup.BackupError, match="does not restore to"):
        backup.inspect(_zip({name: "x"}))


def test_things_that_are_not_our_backups_are_refused() -> None:
    with pytest.raises(backup.BackupError, match="not a zip"):
        backup.inspect(b"hello")
    other = io.BytesIO()
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("tests/a.yaml", "x")
    with pytest.raises(backup.BackupError, match="backup.json is missing"):
        backup.inspect(other.getvalue())
    with pytest.raises(backup.BackupError, match="different version"):
        backup.inspect(_zip({}, {"format": 99}))


def test_a_refused_restore_leaves_everything_as_it_was(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_hub(tmp_path, monkeypatch).stop()
    (tmp_path / "my_tests" / "mine.yaml").write_text(SPEC.format(id="mine.one"))
    with pytest.raises(backup.BackupError):
        backup.restore(folders(tmp_path), _zip({"../evil.txt": "x"}))
    assert (tmp_path / "my_tests" / "mine.yaml").is_file()
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / ".qm" / backup.BACKUPS).exists()  # it did not even get as far as the safety copy


# ------------------------------------------------------------------ the web page


def test_the_page_downloads_a_backup_and_stages_a_restore_for_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = make_hub(tmp_path, monkeypatch)
    add_client(hub, "Acme", ACME, "acme-user")
    hub.start()
    try:
        reply = hub.handle("GET", "/api/backup", b"")
        assert reply.content_type == "application/zip" and (reply.download_name or "").startswith(
            "quartermaster-backup-"
        )
        assert call(hub, "/api/backup/status")["pending"] is None

        (tmp_path / "my_tests" / "later.yaml").write_text(SPEC.format(id="later.one"))
        staged = call(hub, "/api/backup/restore", {"content": base64.b64encode(reply.body).decode()})
        assert staged["pending"]["format"] == backup.FORMAT
        assert (tmp_path / "my_tests" / "later.yaml").is_file()  # nothing is replaced while it runs
        assert call(hub, "/api/backup/status")["pending"] is not None
        call(hub, "/api/backup/cancel", {})
        assert call(hub, "/api/backup/status")["pending"] is None
    finally:
        hub.stop()


def test_the_page_refuses_a_file_that_is_not_a_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = make_hub(tmp_path, monkeypatch)
    hub.start()
    try:
        with pytest.raises(ApiError, match="not a zip") as e:
            hub.handle(
                "POST", "/api/backup/restore", json.dumps({"content": base64.b64encode(b"hello").decode()}).encode()
            )
        assert e.value.status == 400
        with pytest.raises(ApiError, match="could not be read"):
            hub.handle("POST", "/api/backup/restore", json.dumps({"content": "%%%"}).encode())
    finally:
        hub.stop()


def test_a_waiting_restore_is_applied_at_the_next_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = make_hub(tmp_path, monkeypatch)
    hub.stop()
    (tmp_path / "my_tests" / "mine.yaml").write_text(SPEC.format(id="mine.one"))
    content = backup.create(folders(tmp_path))
    (tmp_path / "my_tests" / "mine.yaml").unlink()
    backup.stage(folders(tmp_path), content)

    said = backup.apply_pending(folders(tmp_path))
    assert said and "Restored" in said
    assert (tmp_path / "my_tests" / "mine.yaml").is_file()
    assert backup.pending(folders(tmp_path)) is None
    assert backup.apply_pending(folders(tmp_path)) is None  # nothing waits any more


# ------------------------------------------------------------------ sign-in accounts stay out of backups


def test_sign_in_accounts_are_not_in_a_backup_and_a_restore_leaves_them_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quartermaster.service.auth import Auth

    hub = make_hub(tmp_path, monkeypatch)
    hub.auth.enable("sai", "Sai Ram", "correct-horse-battery")
    hub.stop()
    content = backup.create(folders(tmp_path))
    assert not [n for n in names(content) if "users.db" in n]  # no password hashes in a zip that gets shared

    backup.restore(folders(tmp_path), content)  # replaces the data folder, but not who may sign in
    again = Auth(tmp_path / ".qm" / "users.db")
    assert again.enabled and [u["username"] for u in again.users()] == ["sai"]

    other = _zip({"data/users.db": "not a database", "data/settings.json": "{}"})  # a zip from elsewhere
    backup.restore(folders(tmp_path), other)
    assert [u["username"] for u in Auth(tmp_path / ".qm" / "users.db").users()] == ["sai"]
