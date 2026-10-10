"""Automatic backups: once a day, a backup zip is saved in the data folder, and the newest few are kept.

It is the same backup as the download in Settings (tests, run history, clients and settings, never the
passwords of the test users, never the sign-in accounts), made without anyone remembering to do it.

A laptop is often switched off at night, so "daily" means: when Quartermaster is running and today's time has
passed and no automatic backup has been made since that time, make one now. Starting Quartermaster in the
morning therefore makes the backup that was missed.

The copies are in `<data folder>/backups/auto/`. A backup that fails (no space, a locked file) is reported in
Settings and the audit log and tried again at the next check; it never stops anything else.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from quartermaster.service import atomic, backup

FOLDER = "auto"
PREFIX = "quartermaster-auto-"
DEFAULTS: dict[str, Any] = {"enabled": True, "time": "02:00", "keep": 7, "evidence": False}
MAX_KEEP = 30
CHECK_EVERY_S = 60
START_DELAY_S = 30  # after starting, wait before the first check so the start-up itself stays quick
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class AutoBackupError(ValueError):
    """A setting is not usable. The message is for the person at the Settings page."""


class AutoBackup:
    def __init__(
        self,
        folders: backup.Folders,
        *,
        audit: Callable[[str, str, dict[str, Any]], None] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ):
        self._folders = folders
        self._audit = audit or (lambda what, subject, details: None)
        self._now = now
        self._file = folders.data / "autobackup.json"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ settings

    @property
    def folder(self) -> Path:
        return self._folders.data / backup.BACKUPS / FOLDER

    def settings(self) -> dict[str, Any]:
        saved: dict[str, Any] = {}
        with suppress(OSError, ValueError):
            loaded = json.loads(self._file.read_text(encoding="utf-8"))
            saved = loaded if isinstance(loaded, dict) else {}
        out = {**DEFAULTS, **{k: saved[k] for k in DEFAULTS if k in saved}}
        out["last_error"] = saved.get("last_error")
        out["last_at"] = saved.get("last_at")
        return out

    def update(self, data: dict[str, Any]) -> dict[str, Any]:
        unknown = set(data) - set(DEFAULTS)
        if unknown:
            raise AutoBackupError(f"unknown settings: {', '.join(sorted(unknown))}")
        new: dict[str, Any] = {}
        if "enabled" in data:
            new["enabled"] = bool(data["enabled"])
        if "evidence" in data:
            new["evidence"] = bool(data["evidence"])
        if "time" in data:
            if not _TIME.match(str(data["time"])):
                raise AutoBackupError("the time must be written like 02:00 (24-hour clock)")
            new["time"] = str(data["time"])
        if "keep" in data:
            try:
                keep = int(data["keep"])
            except (TypeError, ValueError):
                raise AutoBackupError("how many to keep must be a number") from None
            if not 1 <= keep <= MAX_KEEP:
                raise AutoBackupError(f"keep between 1 and {MAX_KEEP} backups")
            new["keep"] = keep
        with self._lock:
            self._save({**self._raw(), **new})
        return self.view()

    def view(self) -> dict[str, Any]:
        return {**self.settings(), "copies": self.copies(), "folder": str(self.folder)}

    # ------------------------------------------------------------------ making them

    def due(self, now: datetime | None = None) -> bool:
        """Is a backup owed: it is on, today's time has passed, and none was made since that time."""
        s = self.settings()
        if not s["enabled"]:
            return False
        now = now or self._now()
        hour, minute = (int(x) for x in str(s["time"]).split(":"))
        slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < slot:
            slot -= timedelta(days=1)  # before today's time: yesterday's is the one that counts
        made = _when(s.get("last_at"))
        return made is None or made < slot

    def run_if_due(self) -> Path | None:
        if not self.due():
            return None
        return self.make()

    def make(self) -> Path | None:
        """Make a backup now. Returns the file, or None when it failed (the reason is kept and shown)."""
        s = self.settings()
        with self._lock:
            try:
                content = backup.create(self._folders, include_evidence=bool(s["evidence"]))
                self.folder.mkdir(parents=True, exist_ok=True)
                path = self.folder / f"{PREFIX}{backup.stamp()}.zip"
                temporary = path.with_suffix(".part")
                temporary.write_bytes(content)
                temporary.replace(path)  # never a half-written zip under the real name
            except (OSError, backup.BackupError) as e:
                why = str(e)[:200]
                self._save({**self._raw(), "last_error": why})
                self._audit("Automatic backup failed", "Backup", {"why": why})
                return None
            self._save({**self._raw(), "last_error": None, "last_at": self._now().isoformat(timespec="seconds")})
            self._prune(int(s["keep"]))
        self._audit("Made an automatic backup", "Backup", {"file": path.name, "kept": str(s["keep"])})
        return path

    def _prune(self, keep: int) -> None:
        for old in sorted(self.folder.glob(f"{PREFIX}*.zip"))[:-keep]:
            old.unlink(missing_ok=True)
        for leftover in self.folder.glob("*.part"):
            leftover.unlink(missing_ok=True)

    # ------------------------------------------------------------------ the copies

    def copies(self) -> list[dict[str, Any]]:
        if not self.folder.is_dir():
            return []
        rows = [
            {
                "name": p.name,
                "bytes": p.stat().st_size,
                "at": datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            }
            for p in self.folder.glob(f"{PREFIX}*.zip")
        ]
        return sorted(rows, key=lambda r: r["name"], reverse=True)

    def read(self, name: str) -> bytes:
        """The bytes of one automatic copy. Only a name from the list: nothing else in the folder is reachable."""
        if name not in {c["name"] for c in self.copies()}:
            raise AutoBackupError("no such automatic backup")
        return (self.folder / name).read_bytes()

    # ------------------------------------------------------------------ the timer

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="qm-autobackup", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def _loop(self) -> None:
        if self._stop.wait(START_DELAY_S):
            return
        while True:
            with suppress(Exception):  # whatever goes wrong, the timer keeps going
                self.run_if_due()
            if self._stop.wait(CHECK_EVERY_S):
                return

    # ------------------------------------------------------------------ the file

    def _raw(self) -> dict[str, Any]:
        with suppress(OSError, ValueError):
            loaded = json.loads(self._file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
        return {}

    def _save(self, data: dict[str, Any]) -> None:
        atomic.write_text(self._file, json.dumps(data, indent=2))


def _when(iso: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(iso)) if iso else None
    except ValueError:
        return None
