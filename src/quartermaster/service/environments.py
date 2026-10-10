"""Clients and their Oracle Fusion environments, set up in Settings instead of a terminal.

    client        a customer, e.g. "Acme Corp"
    environment   one of its pods: name, address, kind (DEV / TEST / STAGE), Oracle release,
                  sign-in type (password, or single sign-on done by hand)
    user          a test user of that pod: the default one, and one per persona ("Line Manager"),
                  with its password encrypted (see vault.py)

One environment is active: runs, recordings and Prepare started from the web page use it. It is
kept in <data folder>/environments.db. A production pod can never be added (safety.guards).

When nothing is set up yet but the pod is set in the terminal (QM_FUSION_URL, QM_FUSION_USER,
QM_FUSION_PASSWORD), that pod is added as the first client, so a setup that works today keeps
working. With no environment at all, runs still read those variables as before.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from quartermaster.domain.models import Environment, EnvironmentKind
from quartermaster.runner.credentials import persona_key
from quartermaster.safety.guards import UnsafeEnvironmentError, assert_safe_target
from quartermaster.service import vault

KINDS = ("DEV", "TEST", "STAGE")
SIGN_IN = ("password", "sso")
_RELEASE = re.compile(r"^[A-Za-z0-9 ._-]*$")
_PERSONA_VAR = re.compile(r"^QM_FUSION_USER_([A-Z0-9_]+)$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS environments (
  id TEXT PRIMARY KEY, client_id TEXT NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL,
  release TEXT NOT NULL DEFAULT '', sign_in TEXT NOT NULL DEFAULT 'password', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  environment_id TEXT NOT NULL, persona TEXT NOT NULL, username TEXT NOT NULL, secret BLOB,
  PRIMARY KEY (environment_id, persona));
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
# Added later: databases made before get the column when opened.
_COLUMNS = {
    "environments": {"not_production": "INTEGER NOT NULL DEFAULT 0"},
    # where the client's tests, evidence and history are kept ("" = the default folders)
    "clients": {"folder": "TEXT NOT NULL DEFAULT ''"},
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


class Environments:
    def __init__(self, path: Path, key_file: Path | None = None):
        self.path = path
        self._key_file = key_file  # tests use their own; real use: vault.default_key_file()
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.executescript(_SCHEMA)
            for table, columns in _COLUMNS.items():
                have = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}
                for col, decl in columns.items():
                    if col not in have:
                        self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    # ------------------------------------------------------------------ reading

    def listing(self) -> dict[str, Any]:
        """Everything for the Settings page. Passwords are never included, only whether one is saved."""
        with self._lock:
            clients = [dict(r) for r in self._db.execute("SELECT * FROM clients ORDER BY lower(name)")]
            envs = [dict(r) for r in self._db.execute("SELECT * FROM environments ORDER BY lower(name)")]
            users = [dict(r) for r in self._db.execute("SELECT * FROM users ORDER BY persona")]
        active = self.active_id()
        for env in envs:
            env["host"] = urlsplit(env["url"]).hostname or ""
            env["active"] = env["id"] == active
            env["users"] = [
                {"persona": u["persona"], "username": u["username"], "password_set": bool(u["secret"])}
                for u in users
                if u["environment_id"] == env["id"]
            ]
        for c in clients:
            c["environments"] = [e for e in envs if e["client_id"] == c["id"]]
        return {"clients": clients, "active": active, "protection": vault.kind()}

    def active_id(self) -> str:
        with self._lock:
            row = self._db.execute("SELECT value FROM state WHERE key = 'active'").fetchone()
        return str(row["value"]) if row else ""

    def active(self) -> dict[str, Any] | None:
        """The environment runs use, with its client's name, or None when none is set up."""
        env_id = self.active_id()
        if not env_id:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT e.*, c.name AS client FROM environments e JOIN clients c ON c.id = e.client_id WHERE e.id = ?",
                (env_id,),
            ).fetchone()
        return dict(row) if row else None

    def _state(self, key: str) -> str:
        with self._lock:
            row = self._db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else ""

    def _environment(self, env_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT e.*, c.name AS client FROM environments e JOIN clients c ON c.id = e.client_id WHERE e.id = ?",
                (env_id,),
            ).fetchone()
        return dict(row) if row else None

    def active_client_id(self) -> str:
        """The client being worked on: the one of the environment in use, else the first client."""
        env = self.active()
        if env:
            return str(env["client_id"])
        clients = self.clients()
        return str(clients[0]["id"]) if clients else ""

    def clients(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM clients ORDER BY created_at")]

    def home_client(self) -> str:
        """The first client: it keeps the default folders (what was there before clients existed)."""
        return self._state("home")

    def client_folder(self, client_id: str) -> str:
        with self._lock:
            row = self._db.execute("SELECT folder FROM clients WHERE id = ?", (client_id,)).fetchone()
        return str(row["folder"]) if row else ""

    def client_environment(self, client_id: str) -> dict[str, Any] | None:
        """The environment a client's runs use: the one last chosen for it, else its first one."""
        env = self._environment(self._state(f"env:{client_id}"))
        if env and env["client_id"] == client_id:
            return env
        with self._lock:
            row = self._db.execute(
                "SELECT id FROM environments WHERE client_id = ? ORDER BY created_at LIMIT 1", (client_id,)
            ).fetchone()
        return self._environment(str(row["id"])) if row else None

    def users(self, env_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM users WHERE environment_id = ?", (env_id,))]

    def run_environ(self, base: Mapping[str, str], client_id: str | None = None) -> dict[str, str]:
        """The variables a run, recording or Prepare gets: the pod of the environment in use (of
        `client_id` when given), its kind and its users with their passwords. Users and a sign-in set
        in the terminal for another pod are left out so two clients never mix. With no environment
        set up, `base` is returned unchanged."""
        env = self.client_environment(client_id) if client_id else self.active()
        out = dict(base)
        if env is None:
            return out
        for key in list(out):
            if key.startswith(("QM_FUSION_USER", "QM_FUSION_PASSWORD", "QM_FUSION_SESSION")):
                del out[key]
        out["QM_FUSION_URL"] = env["url"]
        out["QM_FUSION_KIND"] = env["kind"]
        out["QM_ENV_NAME"] = str(env.get("name") or "")  # the pod's own test data is chosen by this name or kind
        out.pop("QM_FUSION_ALLOWED_HOSTS", None)
        if env.get("not_production"):  # confirmed in Settings: runs accept this one host only
            out["QM_FUSION_ALLOWED_HOSTS"] = urlsplit(env["url"]).hostname or ""
        for u in self.users(env["id"]):
            suffix = f"_{u['persona']}" if u["persona"] else ""
            out[f"QM_FUSION_USER{suffix}"] = u["username"]
            if u["secret"]:
                # saved by another Windows user: left out, and the run says the password is not set
                with suppress(vault.VaultError):
                    out[f"QM_FUSION_PASSWORD{suffix}"] = vault.reveal(bytes(u["secret"]), self._key_file)
        return out

    # ------------------------------------------------------------------ changing

    def save_client(self, data: dict[str, Any]) -> dict[str, Any]:
        name = _text(data.get("name"), 80)
        if not name:
            raise ValueError("give the client a name")
        with self._lock, self._db:
            same = self._db.execute(
                "SELECT name FROM clients WHERE lower(name) = lower(?) AND id != ?", (name, data.get("id") or "")
            ).fetchone()
            if same:
                raise ValueError(f"there is already a client called {same['name']}")
            if data.get("id"):
                if not self._db.execute("UPDATE clients SET name = ? WHERE id = ?", (name, data["id"])).rowcount:
                    raise LookupError("that client is no longer there")
                cid = str(data["id"])
            else:
                cid = uuid.uuid4().hex[:10]
                home = self._db.execute("SELECT value FROM state WHERE key = 'home'").fetchone()
                folder = "" if home is None else f"{_slug(name)}-{cid[:4]}"
                self._db.execute(
                    "INSERT INTO clients (id, name, created_at, folder) VALUES (?, ?, ?, ?)",
                    (cid, name, _now(), folder),
                )
                if home is None:
                    self._db.execute("INSERT INTO state VALUES ('home', ?)", (cid,))
        return {"id": cid, "name": name}

    def delete_client(self, client_id: str) -> None:
        with self._lock:
            envs = [r["id"] for r in self._db.execute("SELECT id FROM environments WHERE client_id = ?", (client_id,))]
        for env_id in envs:
            self.delete_environment(env_id)
        with self._lock, self._db:
            if not self._db.execute("DELETE FROM clients WHERE id = ?", (client_id,)).rowcount:
                raise LookupError("that client is no longer there")

    def save_environment(self, data: dict[str, Any]) -> dict[str, Any]:
        name = _text(data.get("name"), 40)
        url = str(data.get("url") or "").strip()
        kind = str(data.get("kind") or "DEV").upper()
        release = _text(data.get("release"), 20)
        sign_in = str(data.get("sign_in") or "password")
        if not name:
            raise ValueError("give the environment a name, e.g. DEV2")
        if kind == "PROD":
            raise ValueError("Quartermaster never tests a production pod")
        if kind not in KINDS:
            raise ValueError(f"the kind must be one of {', '.join(KINDS)}")
        if sign_in not in SIGN_IN:
            raise ValueError("the sign-in must be password or sso")
        if not _RELEASE.match(release):
            raise ValueError("the release may use letters, digits, spaces, dots and dashes, e.g. 26C")
        url = _pod_url(url)
        confirmed = bool(data.get("not_production"))
        host = urlsplit(url).hostname or ""
        try:
            assert_safe_target(
                Environment(name=name, url=url, kind=EnvironmentKind(kind)), {host} if confirmed else None
            )
        except UnsafeEnvironmentError as e:
            if "looks like a production pod" in str(e):
                raise ValueError(
                    f"{host} looks like a production pod: its name has no dev, test, stage or uat. If it is a "
                    "test pod, tick 'This is a test pod, not production' and save again"
                ) from e
            raise ValueError(str(e).split(": ", 1)[-1]) from e
        with self._lock, self._db:
            if not self._db.execute("SELECT 1 FROM clients WHERE id = ?", (data.get("client_id") or "",)).fetchone():
                raise LookupError("choose the client this environment belongs to")
            if data.get("id"):
                done = self._db.execute(
                    "UPDATE environments SET client_id = ?, name = ?, url = ?, kind = ?, release = ?, sign_in = ?, "
                    "not_production = ? WHERE id = ?",
                    (data["client_id"], name, url, kind, release, sign_in, int(confirmed), data["id"]),
                ).rowcount
                if not done:
                    raise LookupError("that environment is no longer there")
                env_id = str(data["id"])
            else:
                env_id = uuid.uuid4().hex[:10]
                self._db.execute(
                    "INSERT INTO environments (id, client_id, name, url, kind, release, sign_in, created_at, "
                    "not_production) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (env_id, data["client_id"], name, url, kind, release, sign_in, _now(), int(confirmed)),
                )
            has_active = self._db.execute("SELECT 1 FROM state WHERE key = 'active'").fetchone()
            if not has_active:  # the first environment is the one used
                self._db.execute("INSERT INTO state VALUES ('active', ?)", (env_id,))
            self._db.execute(  # a client's first environment is the one its runs use
                "INSERT OR IGNORE INTO state VALUES (?, ?)", (f"env:{data['client_id']}", env_id)
            )
        return {"id": env_id, "name": name, "url": url}

    def delete_environment(self, env_id: str) -> None:
        with self._lock, self._db:
            if not self._db.execute("DELETE FROM environments WHERE id = ?", (env_id,)).rowcount:
                raise LookupError("that environment is no longer there")
            self._db.execute("DELETE FROM users WHERE environment_id = ?", (env_id,))
            self._db.execute("DELETE FROM state WHERE key = 'active' AND value = ?", (env_id,))
            self._db.execute("DELETE FROM state WHERE key LIKE 'env:%' AND value = ?", (env_id,))

    def set_release(self, env_id: str, release: str) -> None:
        release = _text(release, 20)
        if not _RELEASE.match(release):
            raise ValueError("the release may use letters, digits, spaces, dots and dashes, e.g. 26C")
        with self._lock, self._db:
            self._db.execute("UPDATE environments SET release = ? WHERE id = ?", (release, env_id))

    def rename(self, env_id: str, name: str) -> None:
        name = _text(name, 40)
        if name:
            with self._lock, self._db:
                self._db.execute("UPDATE environments SET name = ? WHERE id = ?", (name, env_id))

    def activate(self, env_id: str) -> dict[str, Any]:
        with self._lock, self._db:
            if not self._db.execute("SELECT 1 FROM environments WHERE id = ?", (env_id,)).fetchone():
                raise LookupError("that environment is no longer there")
            self._db.execute("INSERT OR REPLACE INTO state VALUES ('active', ?)", (env_id,))
            self._db.execute(
                "INSERT OR REPLACE INTO state SELECT 'env:' || client_id, id FROM environments WHERE id = ?", (env_id,)
            )
        return self.active() or {}

    def save_user(self, data: dict[str, Any]) -> dict[str, Any]:
        """A test user of an environment. An empty password keeps the saved one."""
        env_id = str(data.get("environment_id") or "")
        persona = persona_key(str(data.get("persona") or ""))
        username = str(data.get("username") or "").strip()[:120]
        password = str(data.get("password") or "")
        if not username:
            raise ValueError("enter the user name")
        if len(password) > 200:
            raise ValueError("that password is too long")
        with self._lock:
            if not self._db.execute("SELECT 1 FROM environments WHERE id = ?", (env_id,)).fetchone():
                raise LookupError("that environment is no longer there")
            old = self._db.execute(
                "SELECT secret FROM users WHERE environment_id = ? AND persona = ?", (env_id, persona)
            ).fetchone()
        secret = vault.protect(password, self._key_file) if password else (old["secret"] if old else None)
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?)", (env_id, persona, username, secret))
        return {"persona": persona, "username": username, "password_set": bool(secret)}

    def delete_user(self, env_id: str, persona: str) -> None:
        with self._lock, self._db:
            done = self._db.execute(
                "DELETE FROM users WHERE environment_id = ? AND persona = ?", (env_id, persona_key(persona))
            ).rowcount
        if not done:
            raise LookupError("that user is no longer there")

    def import_from(self, environ: Mapping[str, str], name: str = "", release: str = "") -> bool:
        """Add the pod set in the terminal as the first client, once, when nothing is set up yet."""
        url = environ.get("QM_FUSION_URL", "").strip()
        with self._lock:
            empty = not self._db.execute("SELECT 1 FROM environments").fetchone()
        if not url or not empty:
            return False
        try:
            _pod_url(url)  # refused addresses leave nothing behind
            client = self.save_client({"name": "My first client"})
            host = urlsplit(url).hostname or "pod"
            kind = environ.get("QM_FUSION_KIND", "DEV").upper()
            env = self.save_environment(
                {
                    "client_id": client["id"],
                    "name": name or host.split(".")[0].upper(),
                    "url": url,
                    "kind": kind if kind in KINDS else "DEV",
                    "release": release,
                }
            )
        except (ValueError, LookupError):
            # e.g. a pod address the checks refuse: leave it to be set up on the page
            with self._lock, self._db:
                self._db.execute(
                    "DELETE FROM clients WHERE name = 'My first client' AND id NOT IN "
                    "(SELECT client_id FROM environments)"
                )
            return False
        for var, user in environ.items():
            m = _PERSONA_VAR.match(var)
            persona = m.group(1) if m else ("" if var == "QM_FUSION_USER" else None)
            if persona is None or not user:
                continue
            suffix = f"_{persona}" if persona else ""
            self.save_user(
                {
                    "environment_id": env["id"],
                    "persona": persona,
                    "username": user,
                    "password": environ.get(f"QM_FUSION_PASSWORD{suffix}", ""),
                }
            )
        return True


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "client"


def _pod_url(url: str) -> str:
    """The pod address as typed, or just its host name: always https://host/ ..."""
    if url and "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("enter the pod address, e.g. https://abcd-dev2.fa.us6.oraclecloud.com")
    return url if parts.path not in ("", "/") else f"https://{parts.netloc}/"
