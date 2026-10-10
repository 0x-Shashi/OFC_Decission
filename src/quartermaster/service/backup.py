"""Backup and restore: one zip with everything that cannot be made again.

What goes in:

    tests/      the tests folder (the first client's tests, its recordings and manual scenarios)
    data/       the data folder (.qm): run history, imported manual scripts and release lists,
                schedules, audit log, settings, and the clients and their pods
    clients/    every other client's tests (and their evidence, when asked for)
    evidence/   the evidence folder, only when asked for (screenshots and videos can be large)

What never goes in: the passwords of the test users (they are removed from the copy of the pods
database), the key that protects them, the sign-in accounts of Quartermaster's own users (users.db: a
restore leaves them as they are, and a shared zip never holds anyone's password hash), and anything
Quartermaster keeps in memory only (the AI key and the sign-ins done by hand). So the zip is safe to
keep in a shared folder. After a restore on another computer, type the test users' passwords again
and add the people who sign in; on the same computer both are kept.

Restoring replaces what is there, so it cannot be done while Quartermaster is running (its
databases are open). The web page therefore only stores the zip and asks for a restart, and the
next start applies it (`apply_pending`). Before anything is replaced, the current state is saved
as `data/backups/before-restore-<time>.zip`, so a restore can itself be undone.
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from quartermaster import __version__

FORMAT = 1
MANIFEST = "backup.json"
BACKUPS = "backups"  # inside the data folder: the safety copies. Never part of a backup.
KEEP_COPIES = 5  # safety copies kept
PENDING = "restore-pending.zip"  # inside the data folder: a restore waiting for the next start
KEEP_OUT = "users.db"  # sign-in accounts: never in a backup, and a restore leaves them as they are
GROUPS = ("tests", "data", "clients", "evidence")
MAX_BYTES = 2 * 1024**3  # largest backup made or restored (the evidence of a big client may need a manual copy)
_SKIP_FILES = {PENDING}
_SKIP_SUFFIXES = ("-wal", "-shm", "-journal")


class BackupError(ValueError):
    """The backup or restore cannot be done. The message says why, in words for the Settings page."""


@dataclass(frozen=True)
class Folders:
    tests: Path
    evidence: Path
    data: Path

    @property
    def clients(self) -> Path:
        return self.tests.resolve().parent / "clients"

    def of(self, group: str) -> Path:
        return {"tests": self.tests, "data": self.data, "clients": self.clients, "evidence": self.evidence}[group]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ------------------------------------------------------------------ making a backup


def create(folders: Folders, *, include_evidence: bool = False, keep_passwords: bool = False) -> bytes:
    """The backup zip. `keep_passwords` is only for the safety copy made before a restore, which stays on
    this computer."""
    groups = [g for g in GROUPS if g != "evidence" or include_evidence]
    with tempfile.TemporaryDirectory(prefix="qm-backup-") as tmp:
        scratch = Path(tmp)
        out = scratch / "backup.zip"
        total = 0
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            counts: dict[str, int] = {}
            for group in groups:
                root = folders.of(group)
                n = 0
                for path in _files(group, root, include_evidence, folders):
                    rel = path.relative_to(root).as_posix()
                    source = path
                    if path.suffix == ".db" and path.parent == folders.data:
                        source = _copy_database(path, scratch, strip_passwords=not keep_passwords)
                    total += source.stat().st_size
                    if total > MAX_BYTES:
                        raise BackupError(
                            "This backup is bigger than 2 GB. Leave Evidence out, and copy the evidence folder "
                            "yourself if you need it."
                        )
                    z.write(source, f"{group}/{rel}")
                    n += 1
                counts[group] = n
            z.writestr(
                MANIFEST,
                json.dumps(
                    {
                        "format": FORMAT,
                        "created_at": _now(),
                        "quartermaster_version": __version__,
                        "includes_evidence": include_evidence,
                        "passwords": "kept" if keep_passwords else "left out",
                        "files": counts,
                    },
                    indent=2,
                ),
            )
        return out.read_bytes()


def _files(group: str, root: Path, include_evidence: bool, folders: Folders) -> list[Path]:
    if not root.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root)
        if group == "data" and len(rel.parts) == 1 and (path.name in _SKIP_FILES or path.name.startswith(KEEP_OUT)):
            continue
        if path.name.endswith(_SKIP_SUFFIXES):
            continue
        if any(part in ("__pycache__", ".git") for part in rel.parts):
            continue
        if group == "data" and rel.parts[0] == BACKUPS:
            continue
        if group == "clients" and not include_evidence and len(rel.parts) > 1 and rel.parts[1] == "evidence":
            continue
        found.append(path)
    return found


def _copy_database(path: Path, scratch: Path, *, strip_passwords: bool) -> Path:
    """A consistent copy of a database that may be open, without the saved passwords."""
    target = scratch / f"copy-{path.name}"
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
            if strip_passwords:
                columns = [r[1] for r in dst.execute("PRAGMA table_info(users)")]
                if "secret" in columns:
                    dst.execute("UPDATE users SET secret = NULL")
                    dst.commit()
        finally:
            dst.close()
    finally:
        src.close()
    return target


# ------------------------------------------------------------------ reading one


def inspect(content: bytes) -> dict[str, Any]:
    """Check a backup zip and say what is in it. Raises BackupError when it is not one of ours."""
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as e:
        raise BackupError("That file is not a zip file.") from e
    with z:
        try:
            manifest = json.loads(z.read(MANIFEST))
        except (KeyError, ValueError) as e:
            raise BackupError("That zip is not a Quartermaster backup (backup.json is missing).") from e
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise BackupError("That backup was made by a different version of Quartermaster. Update and try again.")
        total = 0
        for info in z.infolist():
            if info.filename == MANIFEST:
                continue
            _checked_name(info.filename)
            total += info.file_size
        if total > MAX_BYTES:
            raise BackupError("That backup is bigger than 2 GB, which is more than Quartermaster restores.")
        manifest["bytes"] = total
        return dict(manifest)


def _checked_name(name: str) -> tuple[str, str]:
    """(group, path inside it) of a file in the zip. Anything that could land outside the folders is refused."""
    parts = name.split("/")
    bad = (
        name.startswith(("/", "\\"))
        or "\\" in name
        or ":" in parts[0]
        or any(p in ("", ".", "..") for p in parts)
        or len(parts) < 2
        or parts[0] not in GROUPS
    )
    if bad:
        raise BackupError(f"That zip holds a file in a place Quartermaster does not restore to: {name[:80]}")
    return parts[0], "/".join(parts[1:])


# ------------------------------------------------------------------ restoring


def stage(folders: Folders, content: bytes) -> dict[str, Any]:
    """Keep a checked backup for the next start. The web page uses this."""
    info = inspect(content)
    folders.data.mkdir(parents=True, exist_ok=True)
    (folders.data / PENDING).write_bytes(content)
    return info


def pending(folders: Folders) -> dict[str, Any] | None:
    path = folders.data / PENDING
    if not path.is_file():
        return None
    try:
        return inspect(path.read_bytes())
    except BackupError:
        return None


def cancel(folders: Folders) -> None:
    (folders.data / PENDING).unlink(missing_ok=True)


def saved_copies(folders: Folders) -> list[dict[str, Any]]:
    """The safety copies made before restores, newest first."""
    folder = folders.data / BACKUPS
    if not folder.is_dir():
        return []
    rows = [
        {
            "name": p.name,
            "bytes": p.stat().st_size,
            "at": datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
        }
        for p in folder.glob("*.zip")
    ]
    return sorted(rows, key=lambda r: str(r["at"]), reverse=True)


def apply_pending(folders: Folders) -> str | None:
    """At start: restore the waiting backup. Returns a sentence about it, or None when nothing waited."""
    path = folders.data / PENDING
    if not path.is_file():
        return None
    content = path.read_bytes()
    try:
        restore(folders, content)
    except BackupError as e:
        path.unlink(missing_ok=True)
        return f"The waiting backup was not restored: {e}"
    path.unlink(missing_ok=True)
    return (
        "Restored the backup you chose. The copy of what was here before is in the backups folder of the data folder."
    )


def restore(folders: Folders, content: bytes) -> dict[str, Any]:
    """Replace what is in the folders with the backup. Nothing may be running."""
    info = inspect(content)  # the whole zip is checked before anything is changed
    secrets = _read_passwords(folders.data / "environments.db")
    safety = create(folders, keep_passwords=True)
    (folders.data / BACKUPS).mkdir(parents=True, exist_ok=True)
    (folders.data / BACKUPS / f"before-restore-{stamp()}.zip").write_bytes(safety)
    for old in sorted((folders.data / BACKUPS).glob("before-restore-*.zip"))[:-KEEP_COPIES]:
        old.unlink(missing_ok=True)

    included = {name.split("/", 1)[0] for name in zipfile.ZipFile(io.BytesIO(content)).namelist()} - {MANIFEST}
    for group in GROUPS:
        if group != "evidence" or "evidence" in included:
            _clear(group, folders, keep_evidence=not info.get("includes_evidence"))
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        for entry in z.infolist():
            if entry.filename == MANIFEST or entry.is_dir():
                continue
            group, rel = _checked_name(entry.filename)
            if group == "data" and rel.startswith(KEEP_OUT):
                continue  # a zip from elsewhere must not replace who may sign in
            target = folders.of(group) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(entry) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    _put_passwords_back(folders.data / "environments.db", secrets)
    return info


def _clear(group: str, folders: Folders, *, keep_evidence: bool) -> None:
    """Empty a folder before the backup is put in it, keeping what a backup never holds."""
    root = folders.of(group)
    if not root.is_dir():
        return
    for child in list(root.iterdir()):
        if group == "data" and (child.name in (BACKUPS, PENDING) or child.name.startswith(KEEP_OUT)):
            continue
        if group == "clients" and keep_evidence and child.is_dir():
            for sub in list(child.iterdir()):
                if sub.name != "evidence":
                    _remove(sub)
            continue
        _remove(child)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _read_passwords(db: Path) -> dict[tuple[str, str], bytes]:
    """The saved passwords now (as the encrypted bytes), to put back after the restore."""
    if not db.is_file():
        return {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return {
                (str(e), str(p)): bytes(s)
                for e, p, s in con.execute("SELECT environment_id, persona, secret FROM users WHERE secret IS NOT NULL")
            }
        finally:
            con.close()
    except sqlite3.Error:
        return {}


def _put_passwords_back(db: Path, secrets: dict[tuple[str, str], bytes]) -> None:
    """On the same computer the passwords typed before the restore still work: keep them."""
    if not secrets or not db.is_file():
        return
    try:
        con = sqlite3.connect(str(db))
        try:
            for (env, persona), secret in secrets.items():
                con.execute(
                    "UPDATE users SET secret = ? WHERE environment_id = ? AND persona = ? AND secret IS NULL",
                    (secret, env, persona),
                )
            con.commit()
        finally:
            con.close()
    except sqlite3.Error:
        return
