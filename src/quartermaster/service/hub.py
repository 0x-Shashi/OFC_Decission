"""Clients kept apart: one workspace (App) per client, and the web page talks to the one in use.

    first client          the default folders: my_tests, evidence and .qm (what was there before)
    every other client    clients/<name>-<id>/tests and clients/<name>-<id>/evidence, and its run
                          history, manual scripts, schedules and audit log in .qm/clients/<id>

Each workspace has its own tests, manual scripts, evidence, runs, schedules, Needs attention and
audit log, and runs on its own client's pod with its own users, so nothing of one client can turn
up in another's evidence. All workspaces keep running, so every client's schedules run even when
another client is in use. Shared by all: the clients and their pods (environments.db), the
release feature lists, the AI settings and the evidence options.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import threading
from http import HTTPStatus
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from quartermaster.service import backup
from quartermaster.service.api import ApiError, App, Reply
from quartermaster.service.audit import AuditLog
from quartermaster.service.auth import COOKIE, ROLES, Auth, AuthError
from quartermaster.service.autobackup import AutoBackup, AutoBackupError
from quartermaster.service.environments import Environments
from quartermaster.service.google import BINDER_COOKIE, GoogleError, GoogleSignIn
from quartermaster.service.settings import Settings
from quartermaster.service.sso import BINDER_COOKIE as SSO_COOKIE
from quartermaster.service.sso import SsoError, SsoSignIn


def _me(user: dict[str, Any] | None) -> dict[str, Any] | None:
    """The signed-in user as the page may see them."""
    if user is None:
        return None
    return {k: user[k] for k in ("id", "username", "full_name", "title", "roles", "must_change", "email")}


def _reply(data: Any) -> Reply:
    return Reply(HTTPStatus.OK, json.dumps(data).encode("utf-8"))


class Hub:
    def __init__(
        self,
        *,
        tests_root: Path,
        evidence_root: Path,
        data_dir: Path,
        seed_tests: Path | None = None,
        google: GoogleSignIn | None = None,
        sso: SsoSignIn | None = None,
        **app_options: Any,
    ):
        self.tests_root = tests_root
        self.evidence_root = evidence_root
        self.data_dir = data_dir
        self.seed_tests = seed_tests  # copied into a new client's tests folder, like the first start
        self._options = app_options
        self.environments = Environments(data_dir / "environments.db")
        old = Settings(data_dir / "settings.json").get()
        self.environments.import_from(os.environ, name=old["environment_name"], release=old["release"])
        self.audit = AuditLog(data_dir / "audit.jsonl")
        self.autobackup = AutoBackup(
            backup.Folders(tests_root, evidence_root, data_dir),
            audit=lambda what, subject, details: self.audit.add(what, subject, details, who="Quartermaster"),
        )
        # Sign-in and roles (off until switched on in Settings); one set of users for all clients.
        self.auth = Auth(
            data_dir / "users.db",
            record=lambda what, subject, details, who: self.audit.add(what, subject, details, who=who),
        )
        self.address = ""  # where this service answers (set by qm serve), for the links in notifications
        self.google = google or GoogleSignIn(self.auth)  # "Continue with Google" (tests give it a stand-in Google)
        self.sso = sso or SsoSignIn(self.auth)  # the company's single sign-on (tests give it a stand-in provider)
        self._keys: set[str] = set()  # AI keys pasted in Settings, shared by all clients, in memory only
        self._apps: dict[str, App] = {}
        self._lock = threading.RLock()
        self._started = False

    # ------------------------------------------------------------------ workspaces

    def folders(self, client_id: str) -> tuple[Path, Path, Path]:
        """Tests, evidence and data folders of a client."""
        folder = self.environments.client_folder(client_id) if client_id else ""
        if not folder:  # the first client, or no client set up yet
            return self.tests_root, self.evidence_root, self.data_dir
        base = self.tests_root.resolve().parent / "clients" / folder
        return base / "tests", base / "evidence", self.data_dir / "clients" / client_id

    def _workspace(self, client_id: str) -> App:
        app = self._apps.get(client_id)
        if app is not None:
            return app
        tests, evidence, data = self.folders(client_id)
        if not tests.exists():
            if self.seed_tests and self.seed_tests.is_dir():
                shutil.copytree(self.seed_tests, tests)
            else:
                tests.mkdir(parents=True)
        app = App(
            tests_root=tests,
            evidence_root=evidence,
            data_dir=data,
            environments=self.environments,
            client_id=client_id,
            shared_dir=self.data_dir,
            keys_entered=self._keys,
            address=lambda: self.address,
            **self._options,
        )
        self._apps[client_id] = app
        if self._started:
            app.start()
        return app

    def sync(self) -> None:
        """One workspace per client: start the new ones, stop those of deleted clients."""
        with self._lock:
            wanted = {str(c["id"]) for c in self.environments.clients()} or {""}
            for cid in [c for c in self._apps if c not in wanted]:
                self._apps.pop(cid).stop()  # first, so two workspaces never share the same folders
            for cid in sorted(wanted):
                self._workspace(cid)

    @property
    def app(self) -> App:
        """The workspace of the client in use."""
        with self._lock:
            self.sync()
            return self._workspace(self.environments.active_client_id())

    def workspaces(self) -> dict[str, App]:
        with self._lock:
            self.sync()
            return dict(self._apps)

    # ------------------------------------------------------------------ the web service

    def handle(self, method: str, raw_path: str, body: bytes, cookie: str = "") -> Reply:
        path = urlsplit(raw_path).path
        route = [p for p in path.split("/") if p][1:] if path.startswith("/api") else []
        try:
            if route[:1] in (["auth"], ["users"]):
                return self._accounts(
                    method, route, body, self._token(cookie), parse_qs(urlsplit(raw_path).query), cookie
                )
            user = None
            # the web page itself stays open (it shows the sign-in); data and files need a signed-in user
            if self.auth.enabled and path.startswith(("/api/", "/files/")):
                user = self.auth.user_for(self._token(cookie))
                if user is None:
                    raise ApiError(HTTPStatus.UNAUTHORIZED, "sign in first")
                self.auth.authorize(user, method, route)
            if path.startswith("/api/backup"):
                return self._backup(method, raw_path, body)
            reply = self.app.handle(method, raw_path, body, user=user)
        except AuthError as e:
            raise ApiError(e.status, str(e)) from e
        if method == "POST" and raw_path.startswith("/api/environments"):
            self.sync()  # a client added or deleted
        return reply

    # ------------------------------------------------------------------ sign-in, users

    @staticmethod
    def _token(cookie: str, name: str = COOKIE) -> str:
        try:
            jar: SimpleCookie = SimpleCookie(cookie)
        except Exception:  # a cookie header that is not one
            return ""
        return jar[name].value if name in jar else ""

    def _back_to_login(self, message: str, cookie: str = BINDER_COOKIE, path: str = "/api/auth/google") -> Reply:
        """Send the browser to the sign-in page with a message (a sign-in with Google ends in a redirect, not JSON)."""
        gone = f"{cookie}=; HttpOnly; SameSite=Lax; Path={path}; Max-Age=0"
        return Reply(HTTPStatus.FOUND, b"", "text/plain", set_cookie=gone, location=f"/#/login?error={quote(message)}")

    def _google_redirect_uri(self) -> str:
        return self.google.redirect_uri(self.address)

    def _session_cookie(self, token: str) -> str:
        return (
            f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400"
            if token
            else (f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
        )

    def _accounts(
        self, method: str, route: list[str], body: bytes, token: str, query: dict[str, list[str]], cookie: str
    ) -> Reply:
        """Sign in, sign out, change a password, and the users (administrators only)."""
        auth = self.auth
        try:
            data = json.loads(body or b"{}") if method == "POST" else {}
        except ValueError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, "the request is not valid JSON") from e
        if not isinstance(data, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "the request must be a JSON object")
        key = "/".join(route)
        user = auth.user_for(token) if auth.enabled else None

        if method == "GET" and key == "auth/status":
            google = auth.enabled and auth.google_config() is not None
            sso = auth.sso_label() if auth.enabled else None
            return _reply(
                {"enabled": auth.enabled, "user": _me(user), "roles": list(ROLES), "google": google, "sso": sso}
            )
        if method == "GET" and key == "auth/sso/start":
            if not auth.enabled:
                return self._back_to_login("Sign-in is not turned on.", SSO_COOKIE, "/api/auth/sso")
            try:
                go, binder = self.sso.start(self.address)
            except SsoError as e:
                return self._back_to_login(str(e), SSO_COOKIE, "/api/auth/sso")
            mine = f"{SSO_COOKIE}={binder}; HttpOnly; SameSite=Lax; Path=/api/auth/sso; Max-Age=600"
            return Reply(HTTPStatus.FOUND, b"", "text/plain", set_cookie=mine, location=go)
        if method == "GET" and key == "auth/sso/callback":
            if not auth.enabled:
                return self._back_to_login("Sign-in is not turned on.", SSO_COOKIE, "/api/auth/sso")
            try:
                new, _ = self.sso.callback({k: v[0] for k, v in query.items() if v}, self._token(cookie, SSO_COOKIE))
            except SsoError as e:
                return self._back_to_login(str(e), SSO_COOKIE, "/api/auth/sso")
            gone = f"{SSO_COOKIE}=; HttpOnly; SameSite=Lax; Path=/api/auth/sso; Max-Age=0"
            return Reply(
                HTTPStatus.FOUND, b"", "text/plain", set_cookie=[self._session_cookie(new), gone], location="/#/"
            )
        if method == "GET" and key == "auth/google/start":
            if not auth.enabled:
                return self._back_to_login("Sign-in is not turned on.")
            try:
                go, binder = self.google.start(self.address)
            except GoogleError as e:
                return self._back_to_login(str(e))
            mine = f"{BINDER_COOKIE}={binder}; HttpOnly; SameSite=Lax; Path=/api/auth/google; Max-Age=600"
            return Reply(HTTPStatus.FOUND, b"", "text/plain", set_cookie=mine, location=go)
        if method == "GET" and key == "auth/google/callback":
            if not auth.enabled:
                return self._back_to_login("Sign-in is not turned on.")
            try:
                new, _ = self.google.callback(
                    {k: v[0] for k, v in query.items() if v}, self._token(cookie, BINDER_COOKIE)
                )
            except GoogleError as e:
                return self._back_to_login(str(e))
            gone = f"{BINDER_COOKIE}=; HttpOnly; SameSite=Lax; Path=/api/auth/google; Max-Age=0"
            return Reply(
                HTTPStatus.FOUND, b"", "text/plain", set_cookie=[self._session_cookie(new), gone], location="/#/"
            )
        if method == "POST" and key == "auth/login":
            if not auth.enabled:
                raise ApiError(HTTPStatus.BAD_REQUEST, "sign-in is not turned on")
            new, who = auth.login(str(data.get("username") or ""), str(data.get("password") or ""))
            return Reply(HTTPStatus.OK, json.dumps({"user": _me(who)}).encode(), set_cookie=self._session_cookie(new))
        if method == "POST" and key == "auth/logout":
            auth.logout(token)
            return Reply(HTTPStatus.OK, b'{"ok": true}', set_cookie=self._session_cookie(""))
        if method == "POST" and key == "auth/enable":
            if auth.enabled:
                raise ApiError(HTTPStatus.BAD_REQUEST, "sign-in is already on")
            new, who = auth.enable(
                str(data.get("username") or ""), str(data.get("full_name") or ""), str(data.get("password") or "")
            )
            return Reply(HTTPStatus.OK, json.dumps({"user": _me(who)}).encode(), set_cookie=self._session_cookie(new))

        if not auth.enabled:
            if method == "GET" and key == "users":
                return _reply({"enabled": False, "users": [], "roles": list(ROLES)})
            raise ApiError(HTTPStatus.BAD_REQUEST, "sign-in is not turned on")
        if user is None:
            raise ApiError(HTTPStatus.UNAUTHORIZED, "sign in first")
        if method == "POST" and key == "auth/password":  # allowed while a new password is still owed
            auth.change_password(user, str(data.get("current") or ""), str(data.get("new") or ""))
            return _reply({"user": _me(auth.user_for(token))})
        auth.authorize(user, method, route)
        if key == "auth/google":  # the Google client ID and secret (administrators)
            if method == "POST":
                auth.google_update(user, data)
            return _reply({**auth.google_view(), "redirect_uri": self._google_redirect_uri()})
        if key == "auth/sso":  # the company provider's settings (administrators)
            if method == "POST":
                auth.sso_update(user, data)
            return _reply({**auth.sso_view(), "redirect_uri": self.sso.redirect_uri(self.address)})
        if method == "POST" and key == "auth/sso/check":  # does the provider answer? (administrators)
            try:
                found = self.sso.check(str(data.get("issuer") or auth.sso_view()["issuer"]))
            except SsoError as e:
                return _reply({"ok": False, "message": str(e)})
            self.audit.add("Checked the single sign-on provider", "Users", {}, who=user["full_name"])
            return _reply(found)
        if method == "POST" and key == "auth/disable":
            if "admin" not in user["roles"]:
                raise AuthError(HTTPStatus.FORBIDDEN, "this needs an administrator")
            auth.disable(user, str(data.get("password") or ""))
            return Reply(HTTPStatus.OK, b'{"ok": true}', set_cookie=self._session_cookie(""))
        if key == "users" and method == "GET":
            return _reply({"enabled": True, "users": auth.users(), "roles": list(ROLES)})
        if key == "users" and method == "POST":
            action = str(data.get("action") or "")
            if action == "create":
                return _reply(auth.create(user, data))
            if action == "update":
                return _reply({"user": auth.update(user, str(data.get("id") or ""), data)})
            if action == "reset":
                return _reply(auth.reset_password(user, str(data.get("id") or "")))
            if action == "delete":
                auth.delete(user, str(data.get("id") or ""))
                return _reply({"ok": True})
            raise ApiError(HTTPStatus.BAD_REQUEST, "choose create, update, reset or delete")
        raise ApiError(HTTPStatus.NOT_FOUND, "not found")

    # ------------------------------------------------------------------ backup and restore

    def backup_folders(self) -> backup.Folders:
        return backup.Folders(self.tests_root, self.evidence_root, self.data_dir)

    def _backup(self, method: str, raw_path: str, body: bytes) -> Reply:
        """Backup and restore cover every client, so they belong to the hub and not to one workspace."""
        url = urlsplit(raw_path)
        route = url.path[len("/api/backup") :].strip("/")
        folders = self.backup_folders()
        try:
            if method == "GET" and route == "":
                evidence = (parse_qs(url.query).get("evidence") or [""])[0] == "1"
                content = backup.create(folders, include_evidence=evidence)
                self.audit.add("Downloaded a backup", "Backup", {"evidence": "included" if evidence else "left out"})
                return Reply(HTTPStatus.OK, content, "application/zip", f"quartermaster-backup-{backup.stamp()}.zip")
            if method == "GET" and route == "status":
                return _reply(
                    {
                        "pending": backup.pending(folders),
                        "copies": backup.saved_copies(folders),
                        "folders": {"tests": str(folders.tests), "data": str(folders.data)},
                    }
                )
            if method == "POST" and route == "restore":
                try:
                    content = base64.b64decode(str(json.loads(body or b"{}").get("content") or ""), validate=True)
                except (ValueError, binascii.Error, AttributeError) as e:
                    raise backup.BackupError("The file could not be read. Choose the backup zip again.") from e
                info = backup.stage(folders, content)
                self.audit.add("Chose a backup to restore", "Backup", {"made": info.get("created_at")})
                return _reply({"pending": info})
            if method == "POST" and route == "cancel":
                backup.cancel(folders)
                return _reply({"pending": None})
            if route.startswith("auto"):
                return self._auto_backup(method, route, url.query, body, folders)
        except (backup.BackupError, AutoBackupError) as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
        raise ApiError(HTTPStatus.NOT_FOUND, "not found")

    def _auto_backup(self, method: str, route: str, query: str, body: bytes, folders: backup.Folders) -> Reply:
        auto = self.autobackup
        if method == "GET" and route == "auto":
            return _reply(auto.view())
        if method == "POST" and route == "auto":
            data = json.loads(body or b"{}")
            if not isinstance(data, dict):
                raise AutoBackupError("send the settings as a JSON object")
            view = auto.update(data)
            self.audit.add("Changed the automatic backup settings", "Backup", {"changed": ", ".join(sorted(data))})
            return _reply(view)
        if method == "POST" and route == "auto/run":
            if auto.make() is None:
                raise AutoBackupError(f"The backup could not be made: {auto.settings()['last_error']}")
            return _reply(auto.view())
        name = str((parse_qs(query).get("name") or [""])[0])
        if method == "GET" and route == "auto/file":
            content = auto.read(name)
            self.audit.add("Downloaded an automatic backup", "Backup", {"file": name})
            return Reply(HTTPStatus.OK, content, "application/zip", name)
        if method == "POST" and route == "auto/restore":
            wanted = str(json.loads(body or b"{}").get("name") or "")
            info = backup.stage(folders, auto.read(wanted))
            self.audit.add("Chose a backup to restore", "Backup", {"made": info.get("created_at"), "file": wanted})
            return _reply({"pending": info})
        raise ApiError(HTTPStatus.NOT_FOUND, "not found")

    def start(self) -> None:
        with self._lock:
            self.sync()  # made but not started yet
            self._started = True
            self.autobackup.start()
            for app in self._apps.values():
                app.start()

    def stop(self) -> None:
        with self._lock:
            self._started = False
            self.autobackup.stop()
            for app in self._apps.values():
                app.stop()
