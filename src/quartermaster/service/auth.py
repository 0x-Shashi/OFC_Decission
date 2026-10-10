"""Sign-in and roles for Quartermaster itself.

Off by default: Quartermaster then works as it always has, for the person at the computer. It is
switched on in Settings, Users & sign-in, by creating the first administrator. From then on every
request needs a signed-in user, and what a user may do depends on the roles they were given:

    admin      everything: settings, clients and pods, users, backups, notifications
    tester     record, run, import, schedule, accept screen changes, dismiss, switch the pod in use
    approver   approve or withdraw the approval of a release
    (none)     look at everything operational, change nothing: a viewer

A user may have several roles (a tester should not also approve their own release: give those to
different people). Admin includes all the others.

How it is kept safe:
    - passwords are never stored: only a salted scrypt hash (or PBKDF2 where scrypt is missing);
    - the sign-in session is a random token kept in memory only (never in a file), sent in a cookie the
      page's scripts cannot read (HttpOnly, SameSite=Strict); a restart signs everybody out;
    - 5 wrong passwords for a user name lock it for 15 minutes; a wrong user name and a wrong password
      give the same answer and take about the same time;
    - passwords chosen by an administrator (new user, reset) are made by Quartermaster, shown once and
      must be changed at the first sign-in;
    - the last active administrator can never be removed, disabled or demoted;
    - anyone with the files on the computer can always get back in: `qm users` in a terminal.

A person can also sign in with Google (service/google.py): an administrator adds their Google e-mail address
to their account. Google only says who the person is; the roles are still given here, and a Google account
that is not on the list gets nothing.

The users are kept in `users.db` in the data folder, shared by all clients.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any

from quartermaster.service import vault

ROLES = ("admin", "tester", "approver")
IDLE_S = 8 * 3600  # a session ends after this long without a request
LIFETIME_S = 24 * 3600  # and never lasts longer than this
LOCK_AFTER = 5
LOCK_S = 15 * 60
MIN_PASSWORD = 10
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
_OBVIOUS = {
    "password",
    "passw0rd",
    "1234567890",
    "0123456789",
    "qwertyuiop",
    "quartermaster",
    "letmein123",
    "iloveyou12",
}
COOKIE = "qm_session"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
  roles TEXT NOT NULL DEFAULT '', pw TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  must_change INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, last_login TEXT);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
# Added later: databases made before get these columns when opened.
_COLUMNS = {
    "email": "TEXT",  # the Google address, as typed (lower case)
    "email_key": "TEXT",  # the same, in the form Google's address is compared in (see email_key)
    "password_login": "INTEGER NOT NULL DEFAULT 1",  # 0: signs in with Google only
    "origin": "TEXT",  # "sso": made by a first single sign-on, and roles follow the company's groups
}
_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


class AuthError(Exception):
    """Not allowed or not possible. `status` is the HTTP answer; the message is for people."""

    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


def _bad(message: str) -> AuthError:
    return AuthError(HTTPStatus.BAD_REQUEST, message)


# ---------------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    if hasattr(hashlib, "scrypt"):
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return f"scrypt$16384$8$1${_b64(salt)}${_b64(digest)}"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000, dklen=32)
    return f"pbkdf2$600000${_b64(salt)}${_b64(digest)}"


def check_password(password: str, stored: str) -> bool:
    try:
        kind, *rest = stored.split("$")
        if kind == "scrypt" and hasattr(hashlib, "scrypt"):
            n, r, p, salt, digest = rest
            got = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32)
            return hmac.compare_digest(got, _unb64(digest))
        if kind == "pbkdf2":
            rounds, salt, digest = rest
            got = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(rounds), dklen=32)
            return hmac.compare_digest(got, _unb64(digest))
    except (ValueError, TypeError):
        return False
    return False


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def check_new_password(password: str, username: str = "") -> None:
    if len(password) < MIN_PASSWORD:
        raise _bad(f"a password needs at least {MIN_PASSWORD} characters")
    if len(password) > 200:
        raise _bad("that password is too long (200 characters at most)")
    if username and password.lower() == username.lower():
        raise _bad("the password may not be the user name")
    if password.lower() in _OBVIOUS or len(set(password)) < 4:
        raise _bad("that password is too easy to guess: choose a longer one that is less common")


def temporary_password() -> str:
    """A password for an administrator to hand over once: 12 letters and digits, easy to read out."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/l/I
    return "".join(secrets.choice(alphabet) for _ in range(12))


# ---------------------------------------------------------------------- what each role may do


def required_role(method: str, route: list[str]) -> str:
    """The role a request needs: "any" (signed in), "tester", "approver" or "admin". What is not listed
    needs an administrator, so a route added later is closed until someone decides."""
    head = route[0] if route else ""
    if route in (["auth", "google"], ["auth", "sso"], ["auth", "sso", "check"]):
        return "admin"  # the Google or company client ID and secret
    if route == ["tickets", "settings"]:
        return "admin"  # where the tracker is
    if route[:2] == ["audit", "export"]:
        return "approver"  # a copy of the whole record leaves the program
    if method == "GET":
        return "admin" if head in ("users", "backup", "notifications") else "any"
    if head == "approvals":
        return "approver"
    if head == "environments":
        return "tester" if route == ["environments", "activate"] else "admin"
    if head in ("settings", "ai", "notifications", "backup", "users"):
        return "admin"
    if head in (
        "runs",
        "recording",
        "manual",
        "test",
        "attention",
        "tickets",
        "suites",
        "packs",
        "data",
        "schedules",
        "releases",
        "signin",
        "check-pod",
        "open",
    ):
        return "tester"
    return "admin"


def allowed(user: dict[str, Any], need: str) -> bool:
    roles = set(user["roles"])
    return need == "any" or "admin" in roles or need in roles


class Auth:
    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], float] = time.time,
        record: Callable[[str, str, dict[str, Any], str], None] | None = None,
        key_file: Path | None = None,
    ):
        """`record(what, subject, details, who)` is told about sign-ins and changes (the audit log).
        `key_file`: where the key that protects the Google client secret is kept (tests use their own)."""
        self.path = path
        self._key_file = key_file
        self._now = now
        self._record = record
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, Any]] = {}  # token -> {user, born, seen}; in memory only
        self._failures: dict[str, list[float]] = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.executescript(_SCHEMA)
            have = {r["name"] for r in self._db.execute("PRAGMA table_info(users)")}
            for col, decl in _COLUMNS.items():
                if col not in have:
                    self._db.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")  # noqa: S608
            self._db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users (email_key) WHERE email_key IS NOT NULL"
            )

    # ------------------------------------------------------------------ on or off

    @property
    def enabled(self) -> bool:
        with self._lock:
            row = self._db.execute("SELECT value FROM state WHERE key = 'enabled'").fetchone()
        return bool(row) and row["value"] == "1"

    def _set_enabled(self, on: bool) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('enabled', ?)", ("1" if on else "0",))

    def enable(self, username: str, full_name: str, password: str) -> tuple[str, dict[str, Any]]:
        """Turn sign-in on by creating the first administrator, who is signed in at once.
        Returns (session token, user)."""
        with self._lock:
            if self.enabled:
                raise _bad("sign-in is already on")
            if self._count_admins() == 0 or not self._find(username):
                user = self._insert(username, full_name, "", ["admin"], password, must_change=False)
            else:  # turned off earlier and on again: the same account, with the password typed now
                existing = self._find(username)
                assert existing is not None
                if "admin" not in existing["roles"] or not existing["active"]:
                    raise _bad("that user name exists but is not an active administrator")
                check_new_password(password, username)
                self._set_password(existing["id"], password, must_change=False)
                user = self._user(existing["id"])
            with self._db:
                self._db.execute("UPDATE users SET last_login = ? WHERE id = ?", (self._stamp(), user["id"]))
            self._set_enabled(True)
            self._tell("Turned on sign-in", "Users", {"first administrator": user["username"]}, user["full_name"])
            return self._open_session(user), user

    def disable(self, user: dict[str, Any], password: str) -> None:
        with self._lock:
            self._need_password(user["id"], password)
            self._set_enabled(False)
            self._sessions.clear()
            self._tell("Turned off sign-in", "Users", {}, user["full_name"])

    def disable_from_terminal(self) -> None:
        """For `qm users disable-signin`: whoever has the computer's files can always get back in."""
        with self._lock:
            self._set_enabled(False)
            self._sessions.clear()
            self._tell("Turned off sign-in (from the terminal)", "Users", {}, "Terminal")

    # ------------------------------------------------------------------ signing in and out

    def login(self, username: str, password: str) -> tuple[str, dict[str, Any]]:
        key = " ".join(str(username or "").lower().split())[:64]
        with self._lock:
            wait = self._locked_for(key)
            if wait:
                raise AuthError(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    f"too many wrong attempts: try again in {max(1, round(wait / 60))} minute(s)",
                )
            row = self._db.execute("SELECT * FROM users WHERE username = ?", (key,)).fetchone()
        # an unknown name costs the same time as a wrong password, and says the same
        ok = check_password(str(password or ""), row["pw"] if row else hash_password("not a real password"))
        if not (row and ok and row["active"] and row["password_login"]):
            self._fail(key)
            self._tell("Sign-in failed", key, {}, key or "unknown")
            raise AuthError(HTTPStatus.UNAUTHORIZED, "wrong user name or password")
        with self._lock:
            if self._sso_enforced() and "admin" not in _user_row(row)["roles"]:
                self._tell(
                    "Password sign-in refused",
                    row["username"],
                    {"reason": "single sign-on is required"},
                    row["username"],
                )
                raise AuthError(
                    HTTPStatus.FORBIDDEN, "your company requires single sign-on: use the single sign-on button instead"
                )
            self._failures.pop(key, None)
            with self._db:
                self._db.execute("UPDATE users SET last_login = ? WHERE id = ?", (self._stamp(), row["id"]))
            user = self._user(row["id"])
            self._tell("Signed in", user["username"], {}, user["full_name"])
            return self._open_session(user), user

    def logout(self, token: str) -> None:
        with self._lock:
            session = self._sessions.pop(token, None)
        if session:
            user = self._user(session["user"])
            self._tell("Signed out", user["username"], {}, user["full_name"])

    def user_for(self, token: str) -> dict[str, Any] | None:
        """The signed-in user of a session token, or None. Each use keeps the session alive."""
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            now = self._now()
            if now - session["seen"] > IDLE_S or now - session["born"] > LIFETIME_S:
                del self._sessions[token]
                return None
            user = self._find_id(session["user"])
            if user is None or not user["active"]:
                del self._sessions[token]
                return None
            session["seen"] = now
            return user

    def _open_session(self, user: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(32)
        now = self._now()
        self._sessions[token] = {"user": user["id"], "born": now, "seen": now}
        return token

    def _locked_for(self, key: str) -> float:
        recent = [t for t in self._failures.get(key, []) if self._now() - t < LOCK_S]
        self._failures[key] = recent
        if len(recent) >= LOCK_AFTER:
            return LOCK_S - (self._now() - recent[-1])
        return 0

    def _fail(self, key: str) -> None:
        with self._lock:
            if len(self._failures) > 1000:  # a flood of invented names must not grow without end
                self._failures.clear()
            self._failures.setdefault(key, []).append(self._now())

    # ------------------------------------------------------------------ users

    def users(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM users ORDER BY lower(full_name)").fetchall()
        return [_public(_user_row(r)) for r in rows]

    def create(self, admin: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """A new user. By default with a temporary password (returned once, to be handed over); with
        `google_only` (needs a Google e-mail) without any password: they sign in with Google."""
        with self._lock:
            roles = _roles(data.get("roles"))
            email, key = _email(data.get("email"))
            google_only = bool(data.get("google_only"))
            if google_only and not email:
                raise _bad("type the person's Google e-mail address, or give them a password instead")
            username = str(data.get("username") or "").strip() or self._username_from(key)
            password = temporary_password()  # for a Google-only user it is never shown: nobody can use it
            user = self._insert(
                username,
                str(data.get("full_name") or ""),
                str(data.get("title") or ""),
                roles,
                password,
                must_change=not google_only,
                email=email,
                email_key=key,
                password_login=not google_only,
            )
            self._tell(
                "Added a user",
                user["username"],
                {
                    "roles": ", ".join(roles) or "viewer",
                    "google": email or "no",
                    "password": "no" if google_only else "yes",
                },
                admin["full_name"],
            )
        return {"user": _public(user), "password": None if google_only else password}

    def update(self, admin: dict[str, Any], user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            user = self._need_user(user_id)
            changes: dict[str, Any] = {}
            if "full_name" in data:
                changes["full_name"] = _full_name(data["full_name"])
            if "title" in data:
                changes["title"] = " ".join(str(data["title"] or "").split())[:80]
            roles = user["roles"]
            if "roles" in data:
                roles = _roles(data["roles"])
                changes["roles"] = ",".join(roles)
            active = user["active"]
            if "active" in data:
                active = bool(data["active"])
                changes["active"] = int(active)
            email = user["email"]
            if "email" in data:
                email, key = _email(data["email"])
                other = self._find_email(key) if key else None
                if other and other["id"] != user_id:
                    raise _bad(f"that e-mail address already belongs to {other['username']}")
                changes["email"], changes["email_key"] = email or None, key or None
            if "google_only" in data:
                if data["google_only"]:
                    if not email:
                        raise _bad("add the person's Google e-mail address first")
                    changes["password_login"] = 0
                elif user["google_only"]:
                    raise _bad("use Reset password to give this person a password again")
            if self._is_last_admin(user) and ("admin" not in roles or not active):
                raise _bad("this is the last active administrator: make another one first")
            if changes:
                sets = ", ".join(f"{k} = ?" for k in changes)
                with self._db:
                    self._db.execute(f"UPDATE users SET {sets} WHERE id = ?", (*changes.values(), user_id))  # noqa: S608
            if not active:
                self._end_sessions(user_id)
            self._tell(
                "Changed a user",
                user["username"],
                {"roles": ", ".join(roles) or "viewer", "active": "yes" if active else "no"},
                admin["full_name"],
            )
            return _public(self._user(user_id))

    def reset_password(self, admin: dict[str, Any], user_id: str) -> dict[str, Any]:
        """A new temporary password, shown once. The user must choose their own at the next sign-in."""
        with self._lock:
            user = self._need_user(user_id)
            password = temporary_password()
            self._set_password(user_id, password, must_change=True)
            with self._db:
                self._db.execute("UPDATE users SET password_login = 1 WHERE id = ?", (user_id,))
            self._end_sessions(user_id)
            self._tell("Reset a password", user["username"], {}, admin["full_name"])
        return {"user": _public(self._user(user_id)), "password": password}

    def delete(self, admin: dict[str, Any], user_id: str) -> None:
        with self._lock:
            user = self._need_user(user_id)
            if user["id"] == admin["id"]:
                raise _bad("you cannot remove yourself")
            if self._is_last_admin(user):
                raise _bad("this is the last active administrator: make another one first")
            with self._db:
                self._db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self._end_sessions(user_id)
            self._tell("Removed a user", user["username"], {}, admin["full_name"])

    def change_password(self, user: dict[str, Any], current: str, new: str) -> None:
        with self._lock:
            self._need_password(user["id"], current)
            check_new_password(new, user["username"])
            if new == current:
                raise _bad("choose a password you have not used just now")
            self._set_password(user["id"], new, must_change=False)
            self._tell("Changed their own password", user["username"], {}, user["full_name"])

    def set_password_from_terminal(self, username: str) -> str:
        """For `qm users passwd`: a new temporary password for one user. Returns it."""
        with self._lock:
            user = self._find(username)
            if user is None:
                raise _bad(f"there is no user {username!r}")
            password = temporary_password()
            self._set_password(user["id"], password, must_change=True)
            self._end_sessions(user["id"])
            self._tell("Reset a password (from the terminal)", user["username"], {}, "Terminal")
            return password

    def add_from_terminal(self, username: str, full_name: str, roles: list[str]) -> str:
        with self._lock:
            password = temporary_password()
            user = self._insert(username, full_name, "", roles, password, must_change=True)
            self._tell("Added a user (from the terminal)", user["username"], {}, "Terminal")
            return password

    # ------------------------------------------------------------------ the check on every request

    def authorize(self, user: dict[str, Any], method: str, route: list[str]) -> None:
        if user["must_change"]:
            raise AuthError(HTTPStatus.FORBIDDEN, "choose a new password first")
        need = required_role(method, route)
        if not allowed(user, need):
            what = {"admin": "an administrator", "tester": "a tester", "approver": "an approver"}[need]
            raise AuthError(HTTPStatus.FORBIDDEN, f"this needs {what}. Ask an administrator for that role.")

    # ------------------------------------------------------------------ inside

    def _insert(
        self,
        username: str,
        full_name: str,
        title: str,
        roles: list[str],
        password: str,
        *,
        must_change: bool,
        email: str = "",
        email_key: str = "",
        password_login: bool = True,
        origin: str = "",
    ) -> dict[str, Any]:
        name = " ".join(str(username).lower().split())
        if not _USERNAME.match(name):
            raise _bad(
                "the user name is 2 to 32 letters, digits, dots, dashes or underscores, starting with a letter or digit"
            )
        if self._find(name):
            raise _bad(f"there is already a user named {name}")
        other = self._find_email(email_key) if email_key else None
        if other:
            raise _bad(f"that e-mail address already belongs to {other['username']}")
        full = _full_name(full_name)
        if not must_change and password_login:  # a password somebody chose; a temporary one is ours
            check_new_password(password, name)
        uid = secrets.token_hex(6)
        with self._db:
            self._db.execute(
                "INSERT INTO users (id, username, full_name, title, roles, pw, active, must_change, created_at,"
                " email, email_key, password_login) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
                (
                    uid,
                    name,
                    full,
                    " ".join(str(title).split())[:80],
                    ",".join(roles),
                    hash_password(password),
                    int(must_change),
                    self._stamp(),
                    email or None,
                    email_key or None,
                    int(password_login),
                ),
            )
            if origin:
                self._db.execute("UPDATE users SET origin = ? WHERE id = ?", (origin, uid))
        return self._user(uid)

    def _username_from(self, email_key: str) -> str:
        """A user name for someone added by e-mail address alone: the part before the @, made to fit."""
        base = re.sub(r"[^a-z0-9._-]+", ".", email_key.split("@")[0]).strip(".-_")[:28] or "user"
        if not base[0].isalnum():
            base = "u" + base
        name, n = base, 1
        while self._find(name) or not _USERNAME.match(name):
            n += 1
            name = f"{base}{n}"
        return name

    def _find_email(self, key: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM users WHERE email_key = ?", (key,)).fetchone()
        return _user_row(row) if row else None

    # ------------------------------------------------------------------ signing in with Google

    def google_view(self) -> dict[str, Any]:
        """The Google settings for the page: never the client secret, only whether it is set."""
        with self._lock:
            got = self._state("google_")
        return {
            "enabled": got.get("google_enabled") == "1",
            "client_id": got.get("google_client_id", ""),
            "secret_set": bool(got.get("google_secret")),
            "public_url": got.get("google_public_url", ""),
        }

    def google_config(self) -> dict[str, str] | None:
        """What a sign-in with Google needs, with the secret revealed; None when it is off or incomplete."""
        with self._lock:
            got = self._state("google_")
        secret = ""
        if got.get("google_secret"):
            try:
                secret = vault.reveal(base64.b64decode(got["google_secret"]), self._key_file)
            except (vault.VaultError, ValueError):
                secret = ""
        if got.get("google_enabled") != "1" or not got.get("google_client_id") or not secret:
            return None
        return {
            "client_id": got["google_client_id"],
            "client_secret": secret,
            "public_url": got.get("google_public_url", ""),
        }

    def google_update(self, admin: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """Change the Google settings. A secret left out (or None) is kept; "" removes it."""
        unknown = set(data) - {"enabled", "client_id", "client_secret", "public_url"}
        if unknown:
            raise _bad(f"unknown settings: {', '.join(sorted(unknown))}")
        with self._lock:
            got = self._state("google_")
            new: dict[str, str] = {}
            if "client_id" in data:
                cid = " ".join(str(data["client_id"] or "").split())
                if cid and not re.match(r"^[A-Za-z0-9._-]{10,200}$", cid):
                    raise _bad("that does not look like a Google client ID (it ends in .apps.googleusercontent.com)")
                new["google_client_id"] = cid
            if data.get("client_secret") is not None:
                secret = str(data["client_secret"]).strip()
                if secret and not re.match(r"^[\x21-\x7e]{8,200}$", secret):
                    raise _bad("that does not look like a Google client secret")
                new["google_secret"] = (
                    base64.b64encode(vault.protect(secret, self._key_file)).decode("ascii") if secret else ""
                )
            if "public_url" in data:
                url = str(data["public_url"] or "").strip().rstrip("/")
                if url and not _public_url_ok(url):
                    raise _bad(
                        "the address must start with https:// (or http://localhost:8765 on this computer), "
                        "with no path, for example https://qm.example.com"
                    )
                new["google_public_url"] = url
            merged = {**got, **new}
            if data.get("enabled"):
                if not (merged.get("google_client_id") and merged.get("google_secret")):
                    raise _bad("fill in the client ID and the client secret first")
                new["google_enabled"] = "1"
            elif "enabled" in data:
                new["google_enabled"] = "0"
            with self._db:
                for k, v in new.items():
                    self._db.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (k, v))
            self._tell(
                "Changed the Google sign-in settings", "Users", {"changed": ", ".join(sorted(data))}, admin["full_name"]
            )
        return self.google_view()

    def login_google(self, email: str) -> tuple[str, dict[str, Any]]:
        """Sign in the person whose Google account has this (verified) e-mail address. Only someone an administrator
        has added can: anyone else is refused, however real their Google account is."""
        with self._lock:
            _, key = _email(email)
            user = self._find_email(key) if key else None
            if user is None or not user["active"]:
                self._tell("Google sign-in refused", key or "unknown", {"reason": "not on the list"}, key or "unknown")
                raise AuthError(HTTPStatus.FORBIDDEN, "not on the list")
            with self._db:
                self._db.execute("UPDATE users SET last_login = ? WHERE id = ?", (self._stamp(), user["id"]))
            user = self._user(user["id"])
            self._tell("Signed in with Google", user["username"], {}, user["full_name"])
            return self._open_session(user), user

    # ------------------------------------------------------------------ single sign-on with a company provider

    SSO_PROVISION = ("listed", "auto")

    def sso_view(self) -> dict[str, Any]:
        """The single sign-on settings for the page: never the client secret, only whether it is set."""
        with self._lock:
            got = self._state("sso_")
        return {
            "enabled": got.get("sso_enabled") == "1",
            "issuer": got.get("sso_issuer", ""),
            "client_id": got.get("sso_client_id", ""),
            "secret_set": bool(got.get("sso_secret")),
            "public_url": got.get("sso_public_url", ""),
            "label": got.get("sso_label") or "single sign-on",
            "scopes": got.get("sso_scopes") or "openid email profile",
            "groups_claim": got.get("sso_groups_claim") or "groups",
            "domains": [d for d in got.get("sso_domains", "").split(",") if d],
            "provision": got.get("sso_provision") or "listed",
            "default_role": got.get("sso_default_role", ""),
            "role_map": _json_dict(got.get("sso_role_map")),
            "require_group": got.get("sso_require_group") == "1",
            "enforce": got.get("sso_enforce") == "1",
        }

    def sso_config(self) -> dict[str, Any] | None:
        """What a single sign-on needs, with the secret revealed; None when it is off or incomplete."""
        view = self.sso_view()
        with self._lock:
            got = self._state("sso_")
        secret = ""
        if got.get("sso_secret"):
            try:
                secret = vault.reveal(base64.b64decode(got["sso_secret"]), self._key_file)
            except (vault.VaultError, ValueError):
                return None  # a secret that cannot be read (saved by another computer): off, not half working
        if not (view["enabled"] and view["issuer"] and view["client_id"]):
            return None
        return {**view, "client_secret": secret}

    def sso_label(self) -> str | None:
        """The button's words when single sign-on is on, else None."""
        return self.sso_view()["label"] if self.sso_config() is not None else None

    def _sso_enforced(self) -> bool:
        return self.sso_config() is not None and self.sso_view()["enforce"]

    def sso_update(self, admin: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """Change the single sign-on settings. A secret left out (or None) is kept; "" removes it."""
        known = {
            "enabled", "issuer", "client_id", "client_secret", "public_url", "label", "scopes", "groups_claim",
            "domains", "provision", "default_role", "role_map", "require_group", "enforce",
        }  # fmt: skip
        unknown = set(data) - known
        if unknown:
            raise _bad(f"unknown settings: {', '.join(sorted(unknown))}")
        with self._lock:
            got = self._state("sso_")
            new: dict[str, str] = {}
            if "issuer" in data:
                issuer = str(data["issuer"] or "").strip().rstrip("/")
                if issuer and not _issuer_ok(issuer):
                    raise _bad(
                        "the provider address must start with https:// and have no ? or #, for example "
                        "https://login.microsoftonline.com/<tenant>/v2.0 or https://acme.okta.com"
                    )
                new["sso_issuer"] = issuer
            if "client_id" in data:
                cid = " ".join(str(data["client_id"] or "").split())
                if cid and not re.match(r"^[\x21-\x7e]{3,200}$", cid):
                    raise _bad("that does not look like a client ID")
                new["sso_client_id"] = cid
            if data.get("client_secret") is not None:
                secret = str(data["client_secret"]).strip()
                if secret and not re.match(r"^[\x21-\x7e]{6,400}$", secret):
                    raise _bad("that does not look like a client secret")
                new["sso_secret"] = (
                    base64.b64encode(vault.protect(secret, self._key_file)).decode("ascii") if secret else ""
                )
            if "public_url" in data:
                url = str(data["public_url"] or "").strip().rstrip("/")
                if url and not _public_url_ok(url):
                    raise _bad(
                        "the address must start with https:// (or http://localhost:8765 on this computer), "
                        "with no path, for example https://qm.example.com"
                    )
                new["sso_public_url"] = url
            if "label" in data:
                label = " ".join(str(data["label"] or "").split())[:30]
                new["sso_label"] = label
            if "scopes" in data:
                scopes = " ".join(str(data["scopes"] or "").split())
                if scopes and (
                    not re.match(r"^[A-Za-z0-9:._/-]+( [A-Za-z0-9:._/-]+)*$", scopes) or "openid" not in scopes.split()
                ):
                    raise _bad("the scopes are words separated by spaces and must include openid")
                new["sso_scopes"] = scopes
            if "groups_claim" in data:
                claim = str(data["groups_claim"] or "").strip()
                if claim and not re.match(r"^[A-Za-z0-9:._/-]{1,60}$", claim):
                    raise _bad("that does not look like a claim name (it is usually groups or roles)")
                new["sso_groups_claim"] = claim
            if "domains" in data:
                raw = data["domains"]
                items = raw if isinstance(raw, list) else str(raw or "").replace(";", ",").replace(" ", ",").split(",")
                domains = []
                for d in items:
                    d = str(d).strip().lower().lstrip("@")
                    if not d:
                        continue
                    if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", d):
                        raise _bad(f"not an e-mail domain: {d[:60]}")
                    domains.append(d)
                new["sso_domains"] = ",".join(dict.fromkeys(domains))
            if "provision" in data:
                if data["provision"] not in self.SSO_PROVISION:
                    raise _bad("people are either only the ones you list, or made at their first sign-in")
                new["sso_provision"] = str(data["provision"])
            if "default_role" in data:
                role = str(data["default_role"] or "")
                if role not in ("", "tester", "approver"):
                    raise _bad(
                        "new people can get no role (viewer), tester or approver; administrators come from a group"
                    )
                new["sso_default_role"] = role
            if "role_map" in data:
                new["sso_role_map"] = json.dumps(_role_map(data["role_map"]))
            for flag, key in (("require_group", "sso_require_group"), ("enforce", "sso_enforce")):
                if flag in data:
                    new[key] = "1" if data[flag] else "0"
            merged = {**got, **new}
            if data.get("enabled"):
                if not (merged.get("sso_issuer") and merged.get("sso_client_id")):
                    raise _bad("fill in the provider address and the client ID first")
                new["sso_enabled"] = "1"
            elif "enabled" in data:
                new["sso_enabled"] = "0"
            with self._db:
                for k, v in new.items():
                    self._db.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (k, v))
            self._tell(
                "Changed the single sign-on settings", "Users", {"changed": ", ".join(sorted(data))}, admin["full_name"]
            )
        return self.sso_view()

    def login_sso(self, who: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Sign in the person the company's provider has vouched for: {email, name, groups}. Whether they are let in,
        and with which roles, follows the single sign-on settings: the e-mail domains allowed, the groups that give
        roles, whether a first sign-in makes a user. The provider says who; Quartermaster says what they may do."""
        cfg = self.sso_config()
        if cfg is None:
            raise AuthError(HTTPStatus.FORBIDDEN, "single sign-on is not turned on")
        with self._lock:
            _, key = _email(who.get("email"))
            refuse = self._sso_refuse(key or "unknown")
            domain = key.rpartition("@")[2]
            if cfg["domains"] and domain not in cfg["domains"]:
                refuse("the e-mail domain is not allowed", f"{key} is not at an e-mail domain that may sign in here.")
            groups = {str(g).casefold() for g in who.get("groups") or []}
            mapped: list[str] = []
            for group, roles in cfg["role_map"].items():
                if group.casefold() in groups:
                    mapped += [r for r in roles if r not in mapped]
            mapped = [r for r in ROLES if r in mapped]
            if cfg["require_group"] and not mapped:
                refuse(
                    "not in a group that gives access", f"{key} is not in a group that gives access to Quartermaster."
                )
            user = self._find_email(key)
            if user is not None and not user["active"]:
                refuse("the user is switched off", f"{key} cannot sign in: an administrator switched this user off.")
            if user is None:
                if cfg["provision"] != "auto":
                    refuse(
                        "not on the list",
                        f"{key} has not been given access to Quartermaster. Ask an administrator to add that address.",
                    )
                roles = mapped or ([cfg["default_role"]] if cfg["default_role"] else [])
                user = self._insert(
                    self._username_from(key),
                    str(who.get("name") or "").strip() or key.split("@")[0],
                    "",
                    roles,
                    temporary_password(),  # nobody knows it: this person signs in with single sign-on only
                    must_change=False,
                    email=key,
                    email_key=key,
                    password_login=False,
                    origin="sso",
                )
                self._tell(
                    "Created a user from single sign-on",
                    user["username"],
                    {"roles": ", ".join(roles) or "viewer"},
                    user["full_name"],
                )
            elif cfg["role_map"] and mapped and sorted(mapped) != sorted(user["roles"]):
                roles = mapped
                if self._is_last_admin(user) and "admin" not in roles:
                    roles = sorted(
                        {*roles, "admin"}, key=ROLES.index
                    )  # the provider never locks out the last administrator
                    self._tell(
                        "Kept the last administrator",
                        user["username"],
                        {"group roles": ", ".join(mapped)},
                        user["full_name"],
                    )
                with self._db:
                    self._db.execute("UPDATE users SET roles = ? WHERE id = ?", (",".join(roles), user["id"]))
                self._tell(
                    "Changed roles from single sign-on",
                    user["username"],
                    {"from": ", ".join(user["roles"]) or "viewer", "to": ", ".join(roles) or "viewer"},
                    user["full_name"],
                )
            with self._db:
                self._db.execute("UPDATE users SET last_login = ? WHERE id = ?", (self._stamp(), user["id"]))
            user = self._user(user["id"])
            self._tell("Signed in with single sign-on", user["username"], {}, user["full_name"])
            return self._open_session(user), user

    def _sso_refuse(self, who: str) -> Callable[[str, str], None]:
        def refuse(reason: str, message: str) -> None:
            self._tell("Single sign-on refused", who, {"reason": reason}, who)
            raise AuthError(HTTPStatus.FORBIDDEN, message)

        return refuse

    def _state(self, prefix: str) -> dict[str, str]:
        rows = self._db.execute("SELECT key, value FROM state WHERE key LIKE ?", (prefix + "%",)).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def _set_password(self, user_id: str, password: str, *, must_change: bool) -> None:
        with self._db:
            self._db.execute(
                "UPDATE users SET pw = ?, must_change = ? WHERE id = ?",
                (hash_password(password), int(must_change), user_id),
            )

    def _need_password(self, user_id: str, password: str) -> None:
        row = self._db.execute("SELECT pw FROM users WHERE id = ?", (user_id,)).fetchone()
        if not (row and check_password(str(password or ""), row["pw"])):
            raise AuthError(HTTPStatus.FORBIDDEN, "that is not your current password")

    def _end_sessions(self, user_id: str) -> None:
        for token in [t for t, s in self._sessions.items() if s["user"] == user_id]:
            del self._sessions[token]

    def _count_admins(self) -> int:
        return sum(1 for u in self._all() if "admin" in u["roles"] and u["active"])

    def _is_last_admin(self, user: dict[str, Any]) -> bool:
        return "admin" in user["roles"] and user["active"] and self._count_admins() <= 1

    def _all(self) -> list[dict[str, Any]]:
        return [_user_row(r) for r in self._db.execute("SELECT * FROM users").fetchall()]

    def _find(self, username: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT * FROM users WHERE username = ?", (" ".join(str(username).lower().split()),)
        ).fetchone()
        return _user_row(row) if row else None

    def _find_id(self, user_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user_row(row) if row else None

    def _user(self, user_id: str) -> dict[str, Any]:
        user = self._find_id(user_id)
        assert user is not None
        return user

    def _need_user(self, user_id: str) -> dict[str, Any]:
        user = self._find_id(str(user_id))
        if user is None:
            raise AuthError(HTTPStatus.NOT_FOUND, "no such user")
        return user

    def _stamp(self) -> str:
        return datetime.fromtimestamp(self._now()).astimezone().isoformat(timespec="seconds")

    def _tell(self, what: str, subject: str, details: dict[str, Any], who: str) -> None:
        if self._record is not None:
            try:
                self._record(what, subject, details, who)
            except Exception:  # a log that cannot be written must not stop a sign-in
                return


# ---------------------------------------------------------------------- helpers


def _user_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "full_name": row["full_name"],
        "title": row["title"],
        "roles": [r for r in row["roles"].split(",") if r],
        "pw": row["pw"],
        "active": bool(row["active"]),
        "must_change": bool(row["must_change"]),
        "email": row["email"] or "",
        "google_only": not bool(row["password_login"]),
        "origin": row["origin"] or "",
        "created_at": row["created_at"],
        "last_login": row["last_login"],
    }


def _public(user: dict[str, Any]) -> dict[str, Any]:
    """A user as the page may see them: never the password hash."""
    return {k: v for k, v in user.items() if k != "pw"}


def email_key(address: str) -> str:
    """The form an e-mail address is compared in. Gmail ignores dots and anything after a plus in the part before the @,
    so jane.doe+qm@gmail.com and janedoe@gmail.com are one inbox. Other domains are compared as they are."""
    local, _, domain = address.strip().lower().rpartition("@")
    if domain in ("gmail.com", "googlemail.com"):
        return f"{local.split('+')[0].replace('.', '')}@gmail.com"
    return f"{local}@{domain}"


def _email(value: Any) -> tuple[str, str]:
    """(the address as typed in lower case, how it is compared); both empty when none was given."""
    text = " ".join(str(value or "").split()).lower()
    if not text:
        return "", ""
    if len(text) > 254 or not _EMAIL.match(text):
        raise _bad(f"not an e-mail address: {text[:60]}")
    return text, email_key(text)


def _public_url_ok(url: str) -> bool:
    from urllib.parse import urlsplit

    p = urlsplit(url)
    local = p.hostname in ("localhost", "127.0.0.1")
    return (
        bool(p.hostname)
        and not p.path.strip("/")
        and not p.query
        and not p.fragment
        and (p.scheme == "https" or (p.scheme == "http" and local))
    )


def _roles(value: Any) -> list[str]:
    roles = [str(r) for r in (value or [])] if isinstance(value, list | tuple) else []
    bad = [r for r in roles if r not in ROLES]
    if bad:
        raise _bad(f"unknown role: {bad[0]}")
    return [r for r in ROLES if r in roles]


def _issuer_ok(url: str) -> bool:
    from urllib.parse import urlsplit

    p = urlsplit(url)
    local = p.hostname in ("localhost", "127.0.0.1")
    return (
        bool(p.hostname) and not p.query and not p.fragment and (p.scheme == "https" or (p.scheme == "http" and local))
    )


def _json_dict(text: str | None) -> dict[str, list[str]]:
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return {}
    return (
        {str(k): [str(r) for r in v] for k, v in data.items() if isinstance(v, list)} if isinstance(data, dict) else {}
    )


def _role_map(value: Any) -> dict[str, list[str]]:
    """{provider group: [roles]} from what the page sends ({group: role or [roles]}), checked."""
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise _bad("the groups are a list of a group name and a role")
    if len(value) > 50:
        raise _bad("at most 50 groups")
    out: dict[str, list[str]] = {}
    for group, roles in value.items():
        name = " ".join(str(group).split())
        if not name or len(name) > 200:
            raise _bad("a group name is 1 to 200 characters")
        listed = roles if isinstance(roles, list) else [roles]
        out[name] = _roles([r for r in listed if r])
    return {g: r for g, r in out.items() if r}


def _full_name(value: Any) -> str:
    name = " ".join(str(value or "").split())
    if len(name) < 2:
        raise _bad("type the person's full name")
    if len(name) > 80:
        raise _bad("the name is too long (80 letters at most)")
    return name
