"""Run history in a small SQLite file (standard library only).

One row per requested run. The run's own evidence (run.json, suite.json, documents) stays in
the evidence folder; this table only remembers what was asked for, its state and where the
evidence went, so the web UI can list and reopen past runs.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    target      TEXT NOT NULL,
    options     TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    exit_code   INTEGER,
    suite_dir   TEXT,
    summary     TEXT,
    events_path TEXT,
    log_path    TEXT,
    error       TEXT
)
"""
# queued -> running -> passed | failed | error;  queued or running -> cancelled
STATUSES = ("queued", "running", "passed", "failed", "error", "cancelled")
FINISHED = ("passed", "failed", "error", "cancelled")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute(_SCHEMA)

    def create(self, target: str, options: dict[str, Any]) -> dict[str, Any]:
        run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2).upper()}"
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO runs (id, target, options, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
                (run_id, target, json.dumps(options), now()),
            )
        return self.get(run_id) or {}

    def add_finished(self, target: str, options: dict[str, Any], **fields: Any) -> dict[str, Any]:
        """Remember a run that has already ended (a manual scenario done by hand), never queued."""
        run = self.create_with_status(target, options, str(fields.pop("status", "error")))
        self.update(run["id"], **fields)
        return self.get(run["id"]) or {}

    def create_with_status(self, target: str, options: dict[str, Any], status: str) -> dict[str, Any]:
        if status not in FINISHED:
            raise ValueError(f"not a finished status: {status}")
        run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2).upper()}"
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO runs (id, target, options, status, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, target, json.dumps(options), status, now()),
            )
        return self.get(run_id) or {}

    def update(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        unknown = set(fields) - {
            "status",
            "started_at",
            "finished_at",
            "exit_code",
            "suite_dir",
            "summary",
            "events_path",
            "log_path",
            "error",
        }
        if unknown:
            raise ValueError(f"unknown run fields: {sorted(unknown)}")
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, self._db:
            self._db.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _row(row) if row else None

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            # rowid is the insertion order; ids end in a random code, so they do not sort by time
            rows = self._db.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [_row(r) for r in rows]

    def claim_next(self) -> dict[str, Any] | None:
        """Mark the oldest queued run as running and return it (None when nothing is waiting)."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT id FROM runs WHERE status = 'queued' ORDER BY rowid LIMIT 1"  # first in, first out
            ).fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE runs SET status = 'running', started_at = ? WHERE id = ?", (now(), row["id"]))
        return self.get(row["id"])

    def recover(self) -> None:
        """Runs left 'running' by a service that stopped unexpectedly can never finish."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE runs SET status = 'error', finished_at = ?, error = 'The service stopped during this run.' "
                "WHERE status = 'running'",
                (now(),),
            )


def _row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["options"] = json.loads(d["options"] or "{}")
    return d
