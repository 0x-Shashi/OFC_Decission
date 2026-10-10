"""The local web service: a small JSON API plus the web pages, on this computer only.

Standard library only (`http.server`), so `qm serve` needs nothing beyond Quartermaster itself.
It listens on 127.0.0.1 and refuses requests addressed to any other host name, so other
computers and other web sites cannot start runs.

    GET  /api/status                 pod, credentials set or not, environment name and release, folders
    GET  /api/settings, POST /api/settings   {"environment_name", "release"}
    POST /api/attention/dismiss      {"keys"} hide items of Needs attention (they come back if it fails again)
    GET  /api/audit, /api/audit.csv  the audit log: who changed what, when (newest first; the CSV oldest first)
                                     /api/audit?from=&to=&who=&text=&what= picks lines
    GET  /api/audit/verify           is the chain of hashes in the log whole? (which line broke it, if not)
    GET  /api/audit/export?format=csv|jsonl&from=&to=&who=&text=&what=   a zip: the lines, manifest.json, how to check
    GET  /api/environments           clients and their pods (never passwords), and which one runs use
    POST /api/environments/client, /client/delete, /environment, /environment/delete, /user, /user/delete,
         /activate, /check           set them up on the page; passwords are saved encrypted (vault.py)
    GET  /api/signin                 the sign-in done by hand (single sign-on, MFA): none, waiting, done, failed
    POST /api/signin                 open a browser on the pod for a person to sign in; the session is kept
                                     in memory only, and the runs started from here use it
    POST /api/signin/forget          forget that sign-in
    POST /api/check-pod              can this computer reach the pod now?
    POST /api/ai/check               does the AI chosen in Settings answer? (a one-word question)
    POST /api/ai/key                 {"key"} the AI key, kept in memory only until Quartermaster stops ("" forgets it)
    GET  /api/dashboard              pass rate, coverage, release readiness, activity
    GET  /api/attention/sr?run=&test= a draft Oracle service request for a failure the update caused
    GET  /api/certification?release=26A  the release's certification pack (.zip): a Word summary to
                                     sign and each test's latest evidence document on that release
    GET  /api/attention              what needs a person, by kind
    GET  /api/packs                  the test library: packs of ready-made tests (POST /install {"pack"} copies one in)
    GET  /api/suites                 saved suites and the tests each has now (POST: save, /delete, /preview a rule)
    POST /api/runs                   also {"suite": "smoke"}: run the tests a saved suite has right now
    GET  /api/data                   test data: the data sets, who uses them, pods with no value, generated values
    GET  /api/tests                  test files in the tests folder, with their last result
    GET  /api/test?file=<path>       one test: steps in plain words, data, history, the file
    POST /api/test/accept-update     {"file", "step_index", "new"} accept a screen change
    POST /api/test/duplicate         {"file", "id", "title", "folder"?} a copy of a test with its own id and title
    POST /api/data/save              {"name", "title"?, "description"?, "values", "pods"} write a data set from the page
    GET  /api/runs                   run history, newest first
    POST /api/runs                   {"target": "hcm/view_worker.yaml", "options": {...}} queue a run
    GET  /api/runs/<id>              one run, its progress events and (when done) its results
    GET  /api/runs/<id>/events?after=N   progress events from line N on
    POST /api/runs/<id>/cancel
    GET  /api/recording              the recording in progress, or the last one
    POST /api/recording              {"id", "title", "module", "product", "persona", "file"} start recording
    POST /api/recording/<command>    pause, resume, check, undo, note {"text"}, mask, stop
    GET  /api/releases               release feature lists (the releases folder and imported ones)
    GET  /api/releases/plan?name=<file>&budget=<minutes>&opt_in=<feature id>   tests to run, and why
    POST /api/releases/import        {"name", "content" (base64), "release_id", "save"} a feature list
    GET  /api/manual                 imported manual test scripts: their files and scenarios
    GET  /api/manual/scenario?id=<id>   one manual scenario with its test cases and steps
    POST /api/manual/import          {"files": [{"name", "content" (base64), "module", "product"}], "save"}
    POST /api/manual/remove          {"key"} forget one imported workbook
    POST /api/manual/typed           {"id"?, "title", "module", "product", "description", "steps": [{"action",
                                     "expected"}], "fields"} a manual scenario typed here, not imported
    POST /api/manual/typed/delete    {"id"} delete a typed scenario
    POST /api/manual/run             {"id", "by_hand", "prepare", "release", "tester"} run a scenario: it plays
                                     by itself once done by hand (or prepared by AI and approved); otherwise it is
                                     done by hand now, or with "prepare" an AI does it and it waits for review
    GET  /api/schedules              scheduled runs, with when each runs next
    POST /api/schedules              {"id"?, "name", "target", "days": [0-6, Monday 0], "time": "HH:MM",
                                     "enabled", "options"} create or change one
    POST /api/schedules/delete       {"id"}
    POST /api/schedules/run          {"id"} run it now
    POST /api/manual/data            {"id", "values": {step number: text}} test data for steps the script leaves open
    POST /api/manual/approve         {"id"} or {"ids": [...]} a person checked what the AI prepared: Run may now play it
    GET  /api/manual/review          scenarios an AI prepared that wait for review, with each step's picture
    GET  /api/manual/prepare-all     progress of Prepare all
    POST /api/manual/prepare-all     {"ids"?} the AI prepares each scenario that still needs it, one by one
    POST /api/manual/prepare-all/stop   stop after the scenario being prepared now
    POST /api/open                   {"path": "<inside the evidence folder>"} open it in Explorer/Finder
    GET  /files/<path>               a file from the evidence folder (documents, pictures, videos)
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, quote, unquote, urlsplit

import yaml

from quartermaster import packs
from quartermaster.ai import providers as ai_providers
from quartermaster.dsl.data import DataError, merge_sets
from quartermaster.dsl.library import LIBRARY_DIR, LibraryError, expand, files_of_tests, read_groups
from quartermaster.dsl.suites import SuiteError
from quartermaster.evidence.certification import certification_rows, summarize_rows, write_certification_pack
from quartermaster.evidence.document import plain_error
from quartermaster.runner.session import ENV as SESSION_ENV
from quartermaster.service import audit as audit_module
from quartermaster.service import auditexport, insights, suites, testdata, testedit
from quartermaster.service.aieval import AiEval
from quartermaster.service.approvals import ApprovalError, Approvals
from quartermaster.service.audit import AuditLog, computer_user, describe
from quartermaster.service.discovery import Discovery
from quartermaster.service.environments import Environments
from quartermaster.service.heal import accept_update
from quartermaster.service.impact import Releases
from quartermaster.service.manual import ManualScripts, needs_data
from quartermaster.service.notify import Notifier, NotifyError
from quartermaster.service.prepare_all import PrepareAll
from quartermaster.service.recording import COMMANDS, RecordCommandBuilder, Recording, qm_record_command
from quartermaster.service.runner import DEFAULT_OPTIONS, CommandBuilder, RunQueue, qm_run_command
from quartermaster.service.schedules import Schedules
from quartermaster.service.settings import Settings, check_pod
from quartermaster.service.signin import SignIn, qm_signin_command
from quartermaster.service.store import Store
from quartermaster.service.tickets import TicketError, Tickets
from quartermaster.service.triage import sr_draft

WEB_DIR = Path(__file__).parent / "web"
_WEB_FILE = re.compile(r"^/([a-z0-9-]+\.(?:js|css))$")  # the page's own scripts and styles, nothing else
_TYPES = {
    ".txt": "text/plain; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".webm": "video/webm",
}


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Reply:
    status: HTTPStatus
    body: bytes
    content_type: str = "application/json"
    download_name: str | None = None
    set_cookie: str | list[str] | None = None  # whole Set-Cookie values (sign-in and sign-out)
    location: str | None = None  # where to send the browser (with a 302)


def _json(data: Any, status: HTTPStatus = HTTPStatus.OK) -> Reply:
    return Reply(status, json.dumps(data).encode("utf-8"))


class App:
    def __init__(
        self,
        *,
        tests_root: Path,
        evidence_root: Path,
        data_dir: Path,
        run_command: CommandBuilder = qm_run_command,
        record_command: RecordCommandBuilder = qm_record_command,
        cwd: Path | None = None,
        releases_root: Path | None = None,
        signin_command: Callable[[], list[str]] = qm_signin_command,
        environments: Environments | None = None,
        client_id: str = "",
        shared_dir: Path | None = None,
        keys_entered: set[str] | None = None,
        address: Callable[[], str] | None = None,
    ):
        """One workspace. Alone (tests, `qm serve` before clients) it sets up its own Environments.
        Under a Hub, one App per client: `client_id` names the client, `environments` and the
        settings, release lists and AI keys in `shared_dir` are shared by all clients."""
        shared = shared_dir or data_dir
        self.client_id = client_id
        self._local = threading.local()  # who is making the request now: one value per request, never shared
        self.address: Callable[[], str] = address or (lambda: "")  # where this service answers, for links
        self.tests_root = tests_root.resolve()
        self.evidence_root = evidence_root.resolve()
        self.queue = RunQueue(
            Store(data_dir / "qm.db"),
            tests_root=self.tests_root,
            evidence_root=self.evidence_root,
            work_dir=data_dir / "runs",
            command=self._with_ai(run_command),
            cwd=cwd,
        )
        self.recording = Recording(self.tests_root, self.evidence_root, data_dir / "recording", record_command)
        self.backups = data_dir / "backups"
        self.settings = Settings(shared / "settings.json")
        # Clients and their pods, set up in Settings; the active one is what runs use.
        if environments is None:
            environments = Environments(data_dir / "environments.db")
            old = self.settings.get()
            environments.import_from(os.environ, name=old["environment_name"], release=old["release"])
        self.environments = environments
        self.settings.environment = self._env
        self.settings.save_release = lambda r: self.environments.set_release(self._env_key(), r)
        self.settings.save_name = lambda n: self.environments.rename(self._env_key(), n)
        self.pod_check: dict[str, Any] | None = None
        # AI keys pasted in Settings: in memory only, never on disk
        self.keys_entered: set[str] = set() if keys_entered is None else keys_entered
        self.releases = Releases(releases_root, shared / "releases")
        self.manual = ManualScripts(data_dir / "manual")
        self.recording.on_manual_done = self._manual_done
        self.prepare_all = PrepareAll(
            start=lambda sid: self.run_manual({"id": sid, "prepare": True}, batch=True),
            stop_current=self.recording.stop,
        )
        self.recording.on_finished = self.prepare_all.finished
        self.schedules = Schedules(data_dir / "schedules.json", submit=self._scheduled_run)
        self.audit = AuditLog(data_dir / "audit.jsonl")
        self.notifier = Notifier(
            data_dir,
            client=lambda: (
                str((self.environments.client_environment(self.client_id) or {}).get("client") or "")
                if self.client_id
                else str((self.environments.active() or {}).get("client") or "")
            ),
            address=lambda: self.address(),
            audit=self.audit,
        )
        self.queue.on_finished = self.notifier.run_finished
        self.approvals = Approvals(data_dir / "approvals.jsonl", audit=self.audit)
        self.signin = SignIn(signin_command, cwd=cwd)  # single sign-on or MFA: a person signs in once
        self.discovery = Discovery(
            data_dir / "discovery", cwd=cwd, audit=lambda what, subject, details: self.audit.add(what, subject, details)
        )
        self.ai_eval = AiEval(
            data_dir / "ai_eval.json", audit=lambda what, subject, details: self.audit.add(what, subject, details)
        )
        self.tickets = Tickets(
            data_dir / "tickets", audit=lambda what, subject, details: self.audit.add(what, subject, details)
        )
        self.queue.environ = self.recording.environ = self.signin.environ = self.discovery.environ = self.run_environ
        self.queue.defaults = lambda: {"retries": self._retries(), "parallel": self._parallel()}

    def _release_summary(self, release: str) -> dict[str, Any]:
        """Where the tests stand on a release now (what an approval is given on)."""
        results = insights.test_results(self.queue.store.list(limit=500))
        return summarize_rows(certification_rows(self.tests(), results, release))

    def _approvals(self, method: str, query: dict[str, list[str]], data: dict[str, Any]) -> Reply:
        try:
            if method == "GET":
                release = str((query.get("release") or [""])[0]).strip() or self.settings.get()["release"]
                if not release:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "set the Oracle release in Settings first")
                summary = self._release_summary(release)
                return _json(
                    {"state": self.approvals.state(release, summary), "history": self.approvals.history(release)}
                )
            release = str(data.get("release") or self.settings.get()["release"])
            summary = self._release_summary(release)
            action = str(data.get("action") or "")
            if self._user:  # with sign-in on, the approver is who is signed in, whatever the form says
                data = {
                    **data,
                    "name": self._user["full_name"],
                    "title": str(data.get("title") or self._user["title"]),
                    "signed_in_as": self._user["username"],
                }
            if action == "approve":
                self.approvals.approve(release, summary, data)
            elif action == "withdraw":
                self.approvals.withdraw(release, summary, data)
            else:
                raise ApprovalError("choose approve or withdraw")
            return _json(self.approvals.state(release, summary))
        except ApprovalError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e

    def _notifications(self, method: str, route: list[str], data: dict[str, Any]) -> Reply:
        try:
            if method == "GET" and route == ["notifications"]:
                return _json(self.notifier.view())
            if method == "POST" and route == ["notifications"]:
                return _json(self.notifier.update(data))
            if method == "POST" and route == ["notifications", "test"]:
                return _json(self.notifier.send_test(str(data.get("channel") or "")))
        except NotifyError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
        raise ApiError(HTTPStatus.NOT_FOUND, "not found")

    def _who_name(self) -> str:
        """Who is signed in; empty when sign-in is off."""
        return str((self._user or {}).get("full_name") or "")

    def _tickets(self, method: str, route: list[str], query: dict[str, list[str]], data: dict[str, Any]) -> Reply:
        """Ticket links: which tickets track which failing tests, and a ticket text to paste into the tracker."""
        try:
            if method == "GET" and route == ["tickets"]:
                return _json({"settings": self.tickets.settings(), "links": self.tickets.links()})
            if method == "GET" and route == ["tickets", "draft"]:
                run_id, test_id = str((query.get("run") or [""])[0]), str((query.get("test") or [""])[0])
                item = next(
                    (
                        i
                        for i in self.attention()["items"]
                        if i.get("run_id") == run_id and i.get("test_id") == test_id and i.get("step")
                    ),
                    None,
                )
                if item is None:
                    raise ApiError(HTTPStatus.NOT_FOUND, "no failure of that test in Needs attention")
                return _json({**self.tickets.draft(item), "tickets": self.tickets.for_test(test_id)})
            if method == "POST" and route == ["tickets"]:
                test_id, who = str(data.get("test_id") or ""), self._who_name()
                if data.get("action") == "add":
                    links = self.tickets.add(test_id, str(data.get("ref") or ""), str(data.get("run_id") or ""), who)
                elif data.get("action") == "remove":
                    links = self.tickets.remove(test_id, str(data.get("ref") or ""), who)
                else:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "choose add or remove")
                return _json({"tickets": links})
            if method == "POST" and route == ["tickets", "settings"]:
                return _json(self.tickets.update_settings(data))
        except TicketError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
        raise ApiError(HTTPStatus.NOT_FOUND, "not found")

    def _discovery(self, method: str, route: list[str], data: dict[str, Any]) -> Reply:
        """Pod discovery (Settings): off until switched on for this pod; read only; every look is audited."""
        key = self._env_key()
        try:
            if method == "GET" and route == ["discovery"]:
                return _json(self.discovery.view(key))
            if method == "POST" and route == ["discovery"]:
                self.discovery.set_enabled(key, bool(data.get("enabled")))
                return _json(self.discovery.view(key))
            if method == "POST" and route == ["discovery", "run"]:
                return _json(self.discovery.start(key))
            if method == "POST" and route == ["discovery", "forget"]:
                self.discovery.forget(key)
                return _json(self.discovery.view(key))
        except ValueError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
        raise ApiError(HTTPStatus.NOT_FOUND, "not found")

    def _retries(self) -> int:
        """How many times a failed step is tried again (Settings, Evidence); 1 until chosen."""
        value = self.settings.get().get("retries") or "1"
        return int(value) if value in ("0", "1", "2", "3") else 1

    def _parallel(self) -> int:
        """How many tests run at the same time (Settings, Evidence); 1 until chosen."""
        value = self.settings.get().get("parallel") or "1"
        return int(value) if value in ("1", "2", "3", "4") else 1

    def _with_ai(self, build: CommandBuilder) -> CommandBuilder:
        """The command of a run, plus the AI chosen in Settings so it can suggest what a missing item became.
        Only the standard command gets it (the AI key is in the environment of the run, never in the command)."""
        if build is not qm_run_command:
            return build

        def command(target: str, options: dict[str, Any], evidence_root: Path, events: Path) -> list[str]:
            cmd = build(target, options, evidence_root, events)
            settings = self.settings.get()
            config = ai_providers.config_from(settings)
            if settings.get("ai_suggest") != "off" and config.provider and config.problem() is None:
                cmd += ["--ai-suggest", "--ai-provider", config.provider, "--ai-model", config.model]
                cmd += ["--ai-base-url", config.base_url, "--ai-key-env", config.key_env]
                if config.workspace:
                    cmd += ["--ai-workspace", config.workspace]
            return cmd

        return command

    def _env(self) -> dict[str, Any] | None:
        """The environment this workspace's runs use (its client's, or the one in use), or None."""
        if self.client_id:
            return self.environments.client_environment(self.client_id)
        return self.environments.active()

    def _env_key(self) -> str:
        env = self._env()
        return str(env["id"]) if env else ""

    def run_environ(self) -> dict[str, str]:
        """What a run, recording or Prepare gets: the pod, its users and its sign-in by hand."""
        out = self.environments.run_environ(os.environ, self.client_id or None)
        key = self._env_key()
        if key:
            session = self.signin.session(key)
            if session:
                out[SESSION_ENV] = session
        return out

    def start(self) -> None:
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        self.queue.start()
        self.schedules.start()

    def stop(self) -> None:
        self.schedules.stop()
        self.signin.stop()
        self.recording.shutdown()
        self.queue.stop()

    # ------------------------------------------------------------------ routing

    @property
    def _user(self) -> dict[str, Any] | None:
        """The signed-in user making the request being handled in this thread (sign-in on), else None."""
        user: dict[str, Any] | None = getattr(self._local, "user", None)
        return user

    def handle(
        self, method: str, raw_path: str, body: bytes, cookie: str = "", user: dict[str, Any] | None = None
    ) -> Reply:
        """`user`: the signed-in user, when sign-in is on (the hub checked the cookie and the role already)."""
        self._local.user = user
        reply = self._handle(method, raw_path, body)
        if method == "POST" and reply.status < 400 and reply.content_type == "application/json":
            self._audit(raw_path, body, reply)
        return reply

    def _audit(self, raw_path: str, body: bytes, reply: Reply) -> None:
        """Add a successful change to the audit log. A problem here never fails the request."""
        with suppress(Exception):
            route = [p for p in unquote(urlsplit(raw_path).path).split("/") if p][1:]
            data = self._body(body)
            said = describe(route, data, json.loads(reply.body or b"null"))
            if said is not None:
                who = (self._user or {}).get("full_name") or str(data.get("tester") or "").strip()[:60]
                self.audit.add(*said, who=who)

    def _handle(self, method: str, raw_path: str, body: bytes) -> Reply:
        url = urlsplit(raw_path)
        path, query = unquote(url.path), parse_qs(url.query)
        parts = [p for p in path.split("/") if p]
        if method == "GET" and path == "/":
            return self._page("index.html")
        web = _WEB_FILE.match(path)
        if method == "GET" and web and (WEB_DIR / web.group(1)).is_file():
            return self._page(web.group(1))
        if method == "GET" and parts[:1] == ["files"]:
            return self._file("/".join(parts[1:]))
        if parts[:1] != ["api"]:
            raise ApiError(HTTPStatus.NOT_FOUND, "not found")
        data = self._body(body) if method == "POST" else {}
        route = parts[1:]

        if method == "GET" and route == ["status"]:
            return _json(self.status())
        if method == "GET" and route == ["audit"]:
            picked = {k: str((query.get(k) or [""])[0]).strip() for k in ("from", "to", "who", "text", "what")}
            if not any(picked.values()):
                return _json({"entries": self.audit.entries()})
            found = self.audit.select(picked["from"], picked["to"], picked["who"], picked["text"], picked["what"])
            return _json({"entries": found[::-1][: audit_module.MAX_SHOWN], "matching": len(found)})
        if method == "GET" and route == ["audit", "verify"]:
            return _json(self.audit.verify())
        if method == "GET" and route == ["audit", "export"]:
            return self._audit_export(query)
        if method == "GET" and route == ["audit.csv"]:
            name = f"quartermaster-audit-{datetime.now():%Y%m%d}.csv"
            return Reply(HTTPStatus.OK, self.audit.as_csv().encode("utf-8-sig"), "text/csv; charset=utf-8", name)
        if method == "GET" and route == ["tests"]:
            return _json(self.tests())
        if route == ["approvals"]:
            return self._approvals(method, query, data)
        if route[:1] == ["notifications"]:
            return self._notifications(method, route, data)
        if route == ["settings"]:
            if method == "POST":
                try:
                    self.settings.update(data)
                except ValueError as e:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
            return _json(self.settings.get())
        if method == "POST" and route == ["ai", "key"]:
            return _json(self.set_ai_key(str(data.get("key") or "")))
        if route == ["ai", "eval"]:
            if method == "POST":
                try:
                    return _json(self.ai_eval.start(ai_providers.config_from(self.settings.get())))
                except ValueError as e:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
            return _json(self.ai_eval.view())
        if method == "POST" and route == ["ai", "check"]:
            return _json(ai_providers.check(ai_providers.config_from(self.settings.get())))
        if route[:1] == ["signin"]:
            if method == "GET" and route == ["signin"]:
                return _json(self.signin.view(self._env_key()))
            if method == "POST" and route == ["signin"]:
                if not self.pod()["url"]:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "add the pod in Settings, Clients and environments, first")
                try:
                    return _json(self.signin.start(self._env_key()))
                except ValueError as e:
                    raise ApiError(HTTPStatus.CONFLICT, str(e)) from e
            if method == "POST" and route == ["signin", "forget"]:
                return _json(self.signin.forget(self._env_key()))
        if route and route[0] == "discovery":
            return self._discovery(method, route, data)
        if route and route[0] == "tickets":
            return self._tickets(method, route, query, data)
        if method == "POST" and route == ["check-pod"]:
            self.pod_check = check_pod(self.pod()["url"])
            return _json(self.pod_check)
        if method == "GET" and route == ["dashboard"]:
            release = self.settings.get()["release"]
            data = insights.dashboard(self.tests(), self.queue.store.list(limit=500), release)
            if release:
                data["approval"] = self.approvals.state(release, self._release_summary(release))
            return _json(data)
        if method == "GET" and route == ["certification"]:
            release = str((query.get("release") or [""])[0]).strip() or self.settings.get()["release"]
            if not release:
                raise ApiError(HTTPStatus.BAD_REQUEST, "set the Oracle release in Settings first")
            results = insights.test_results(self.queue.store.list(limit=500))
            name, body = write_certification_pack(
                self.tests(),
                results,
                release,
                self.evidence_root,
                approval=self.approvals.state(release, self._release_summary(release)),
                approval_history=self.approvals.history(release),
                tickets=self.tickets.links(),
            )
            return Reply(HTTPStatus.OK, body, "application/zip", name)
        if method == "GET" and route == ["export"]:
            return self.export_tests(str((query.get("file") or [""])[0]))
        if method == "GET" and route == ["attention"]:
            return _json(self.attention())
        if method == "POST" and route == ["attention", "dismiss"]:
            return _json(self.dismiss_attention(data))
        if method == "GET" and route == ["attention", "sr"]:
            return _json(self.sr_draft(str((query.get("run") or [""])[0]), str((query.get("test") or [""])[0])))
        if method == "GET" and route == ["library"]:
            return _json(self.library())
        if method == "GET" and route == ["data"]:
            return _json(self.test_data())
        if route[:1] == ["suites"]:
            return self._suites(method, route[1:], data)
        if route[:1] == ["packs"]:
            return self._packs(method, route[1:], data)
        if method == "GET" and route == ["test"]:
            return _json(self.test_detail(str((query.get("file") or [""])[0])))
        if method == "POST" and route == ["test", "accept-update"]:
            return _json(self.accept_update(data))
        if method == "POST" and route == ["test", "duplicate"]:
            return _json(self.duplicate_test(data), HTTPStatus.CREATED)
        if method == "POST" and route == ["data", "save"]:
            return _json(self.save_data_set(data))
        if route == ["runs"]:
            if method == "GET":
                return _json([self._run_view(r, counts=True) for r in self.queue.store.list()])
            if method == "POST":
                options = dict(data.get("options") or {})
                target = str(data.get("target") or "")
                if data.get("suite"):  # run a saved suite: the tests it has right now
                    try:
                        title, options["only"] = suites.tests_of(self.tests_root, str(data["suite"]), self.tests())
                    except SuiteError as e:
                        raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
                    target, options["label"] = ".", f"Suite: {title}"[:80]
                if self._user and not str(options.get("tester") or "").strip():
                    options["tester"] = self._user["full_name"]  # "Run by" is the signed-in person
                if not str(options.get("release") or "").strip():
                    options["release"] = self.settings.get()["release"]  # the environment's release by default
                try:
                    queued = self.queue.submit(target, options)
                except ValueError as e:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
                return _json(self._run_view(queued), HTTPStatus.CREATED)
        if len(route) >= 2 and route[0] == "runs":
            run = self.queue.store.get(route[1])
            if run is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "no such run")
            if method == "GET" and len(route) == 2:
                return _json(self.run_detail(run))
            if method == "GET" and route[2:] == ["events"]:
                after = int((query.get("after") or ["0"])[0] or 0)
                return _json([_with_plain_error(e) for e in self.queue.events(run["id"], after=max(after, 0))])
            if method == "POST" and route[2:] == ["cancel"]:
                return _json(self._run_view(self.queue.cancel(run["id"]) or run))
        if route[:1] == ["recording"]:
            if method == "GET" and len(route) == 1:
                return _json(self._recording_view())
            if method == "POST" and len(route) == 1:
                try:
                    return _json(self.recording.start(data), HTTPStatus.CREATED)
                except ValueError as e:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
            if method == "POST" and len(route) == 2 and route[1] in COMMANDS:
                try:
                    return _json(self.recording.send(route[1], str(data.get("text") or "")))
                except ValueError as e:
                    raise ApiError(HTTPStatus.CONFLICT, str(e)) from e
        if route[:1] == ["environments"]:
            return self._environments(method, route[1:], data)
        if route[:1] == ["releases"]:
            return self._releases(method, route[1:], query, data)
        if route[:1] == ["schedules"]:
            return self._schedules(method, route[1:], data)
        if route[:1] == ["manual"]:
            return self._manual(method, route[1:], query, data)
        if method == "POST" and route == ["open"]:
            return _json({"opened": str(self._open(str(data.get("path") or "")))})
        raise ApiError(HTTPStatus.NOT_FOUND, "not found")

    def _releases(self, method: str, route: list[str], query: dict[str, list[str]], data: dict[str, Any]) -> Reply:
        try:
            if method == "GET" and not route:
                return _json(self.releases.list())
            if method == "GET" and route == ["plan"]:
                budget = str((query.get("budget") or [""])[0]).strip()
                opt_ins = {o for v in query.get("opt_in") or [] for o in v.split(",") if o}
                name = str((query.get("name") or [""])[0])
                limit = float(budget) if budget else None
                return _json(
                    self.releases.plan(
                        name, self.tests_root, limit, opt_ins, self.manual, self.discovery.pages(self._env_key())
                    )
                )
            if method == "POST" and route == ["import"]:
                return _json(self.releases.import_file(data), HTTPStatus.CREATED if data.get("save") else HTTPStatus.OK)
        except LookupError as e:
            raise ApiError(HTTPStatus.NOT_FOUND, str(e).strip("'\"")) from e
        except ValueError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
        raise ApiError(HTTPStatus.NOT_FOUND, "not found")

    def _environments(self, method: str, route: list[str], data: dict[str, Any]) -> Reply:
        envs = self.environments
        try:
            if method == "GET" and not route:
                return _json(envs.listing())
            if method != "POST":
                raise ApiError(HTTPStatus.NOT_FOUND, "not found")
            key = "/".join(route)
            if key == "client":
                return _json(envs.save_client(data), HTTPStatus.CREATED)
            if key == "client/delete":
                envs.delete_client(str(data.get("id") or ""))
                return _json({"deleted": data.get("id")})
            if key == "environment":
                before = envs.active_id()
                saved = envs.save_environment(data)
                if envs.active_id() != before or saved["id"] == before:
                    self._switched()
                return _json(saved, HTTPStatus.CREATED)
            if key == "environment/delete":
                envs.delete_environment(str(data.get("id") or ""))
                self._switched()
                return _json({"deleted": data.get("id")})
            if key == "user":
                return _json(envs.save_user(data), HTTPStatus.CREATED)
            if key == "user/delete":
                envs.delete_user(str(data.get("environment_id") or ""), str(data.get("persona") or ""))
                return _json({"deleted": data.get("persona")})
            if key == "activate":
                if self.queue.busy() or self.recording.busy():
                    raise ApiError(HTTPStatus.CONFLICT, "wait until the run or recording in progress has finished")
                active = envs.activate(str(data.get("id") or ""))
                self._switched()
                return _json({"active": active.get("id"), "name": active.get("name"), "client": active.get("client")})
            if key == "check":
                env = next(
                    (e for c in envs.listing()["clients"] for e in c["environments"] if e["id"] == data.get("id")), None
                )
                if env is None:
                    raise LookupError("that environment is no longer there")
                result = check_pod(env["url"])
                if env["active"]:
                    self.pod_check = result
                return _json(result)
        except LookupError as e:
            raise ApiError(HTTPStatus.NOT_FOUND, str(e).strip("'\"")) from e
        except ValueError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
        raise ApiError(HTTPStatus.NOT_FOUND, "not found")

    def _switched(self) -> None:
        """Another pod is in use: forget what belonged to the previous one."""
        self.pod_check = None  # a sign-in by hand stays with its pod (see SignIn)

    def _schedules(self, method: str, route: list[str], data: dict[str, Any]) -> Reply:
        try:
            if method == "GET" and not route:
                return _json({"schedules": self.schedules.listing()})
            if method == "POST" and not route:
                return _json(self.schedules.save(data, self.queue._inside_tests, self._check_suite), HTTPStatus.CREATED)
            if method == "POST" and route == ["delete"]:
                self.schedules.delete(str(data.get("id") or ""))
                return _json({"deleted": data.get("id")})
            if method == "POST" and route == ["run"]:
                return _json(self._run_view(self.schedules.run_now(str(data.get("id") or ""))), HTTPStatus.CREATED)
        except LookupError as e:
            raise ApiError(HTTPStatus.NOT_FOUND, str(e).strip("'\"")) from e
        except ValueError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
        raise ApiError(HTTPStatus.NOT_FOUND, "not found")

    def _scheduled_run(self, schedule: dict[str, Any]) -> dict[str, Any]:
        """Queue the run of a schedule, labelled with its name and the release the pod is on."""
        options = {
            "screenshots": "every-step",
            **(schedule.get("options") or {}),
            "label": f"Scheduled: {schedule.get('name', '')}"[:80],
            "release": self.settings.get().get("release", ""),
            "tester": "Scheduled run",
        }
        if schedule.get("suite"):  # the tests the suite has now, not the ones it had when the schedule was made
            try:
                _, options["only"] = suites.tests_of(self.tests_root, str(schedule["suite"]), self.tests())
            except SuiteError as e:
                raise ValueError(str(e)) from e
        run = self.queue.submit(str(schedule.get("target") or "."), options)
        self.audit.add(
            "Started a scheduled run", str(schedule.get("name") or ""), {"run": run.get("id")}, who="Schedule"
        )
        return run

    def _manual(self, method: str, route: list[str], query: dict[str, list[str]], data: dict[str, Any]) -> Reply:
        try:
            if method == "GET" and not route:
                summary = self.manual.summary()
                self._with_results(summary["scenarios"])
                summary["prepare_all"] = self.prepare_all.view()
                return _json(summary)
            if method == "GET" and route == ["scenario"]:
                scenario = dict(self.manual.get(str((query.get("id") or [""])[0])))
                self._with_results([scenario], history=True)
                entered = self.manual.test_data_all().get(scenario["id"], {})
                scenario["test_data"] = entered
                # step number -> "blank" (<> in the script) or "value" (says to type, without what)
                scenario["needs_data"] = {str(n): k for n, k in needs_data(scenario).items()}
                return _json(scenario)
            if method == "POST" and route == ["data"]:
                scenario = self.manual.get(str(data.get("id") or ""))
                raw = data.get("values")
                values: dict[str, Any] = raw if isinstance(raw, dict) else {}
                return _json({"test_data": self.manual.set_test_data(scenario["id"], values)})
            if method == "POST" and route == ["run"]:
                return _json(self.run_manual(data), HTTPStatus.CREATED)
            if method == "POST" and route == ["approve"]:
                if isinstance(data.get("ids"), list):  # Approve selected
                    ids = [self.manual.get(str(i))["id"] for i in data["ids"]]
                    for i in ids:
                        self.manual.approve(i)
                    return _json({"approved": ids})
                return _json(self.manual.approve(self.manual.get(str(data.get("id") or ""))["id"]))
            if method == "GET" and route == ["review"]:
                return _json(self.review_list())
            if method == "GET" and route == ["prepare-all"]:
                return _json(self.prepare_all.view())
            if method == "POST" and route == ["prepare-all"]:
                return _json(self.start_prepare_all(data), HTTPStatus.CREATED)
            if method == "POST" and route == ["prepare-all", "stop"]:
                return _json(self.prepare_all.stop())
            if method == "POST" and route == ["import"]:
                return _json(self.manual.import_files(data))
            if method == "POST" and route == ["remove"]:
                return _json(self.manual.remove(str(data.get("key") or "")))
            if method == "POST" and route == ["typed"]:
                return _json(self.manual.save_typed(data), HTTPStatus.CREATED)
            if method == "POST" and route == ["typed", "delete"]:
                return _json(self.manual.delete_typed(str(data.get("id") or "")))
        except LookupError as e:
            raise ApiError(HTTPStatus.NOT_FOUND, str(e)) from e
        except ValueError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
        raise ApiError(HTTPStatus.NOT_FOUND, "not found")

    def set_ai_key(self, key: str) -> dict[str, Any]:
        """Keep the AI key pasted in Settings for as long as Quartermaster runs. It is put in this
        process's environment, where the AI runs started from here find it, and is never written to
        a file or sent back to the page."""
        config = ai_providers.config_from(self.settings.get())
        if not config.provider:
            raise ApiError(HTTPStatus.BAD_REQUEST, "choose and save an AI provider first")
        if not config.key_env:
            raise ApiError(HTTPStatus.BAD_REQUEST, "this provider needs no key")
        key = key.strip()
        if not key:
            if config.key_env in self.keys_entered:
                os.environ.pop(config.key_env, None)
                self.keys_entered.discard(config.key_env)
            return self._ai_view(self.settings.get())
        if len(key) < 8 or len(key) > 500 or any(c.isspace() for c in key):
            raise ApiError(
                HTTPStatus.BAD_REQUEST, "that does not look like an API key (no spaces, at least 8 characters)"
            )
        os.environ[config.key_env] = key
        self.keys_entered.add(config.key_env)
        return self._ai_view(self.settings.get())

    def _ai_view(self, settings: dict[str, Any]) -> dict[str, Any]:
        """The AI choice for the Settings page: never the key itself, only whether it is set and where from."""
        config = ai_providers.config_from(settings)
        has_key = bool(config.key())
        return {
            "key_source": ("entered" if config.key_env in self.keys_entered else "computer") if has_key else "",
            "provider": config.provider,
            "model": config.model,
            "base_url": config.base_url,
            "key_env": config.key_env,
            "workspace": config.workspace,
            "key_set": bool(config.key()),
            "suggest": settings.get("ai_suggest") != "off",
            "label": config.label if config.provider else "",
            "problem": config.problem() if config.provider else "No AI provider is chosen.",
            "presets": [
                {"id": k, "label": v[0], "format": v[1], "base_url": v[2], "key_env": v[3], "model": v[4]}
                for k, v in ai_providers.PRESETS.items()
            ],
        }

    # ------------------------------------------------------------------ manual scenarios run

    def run_manual(self, data: dict[str, Any], batch: bool = False) -> dict[str, Any]:
        """Once a scenario has been done by hand, Run plays it by itself; before that (or when asked
        with by_hand) the tester does it by hand while Quartermaster takes pictures and records.
        Test data entered in Quartermaster is written into the scenario's steps."""
        scenario = self.manual.with_test_data(self.manual.get(str(data.get("id") or "")))
        guided = (
            data.get("prepare")
            or data.get("by_hand")
            or not (self.tests_root / self.manual.test_file(scenario["id"])).is_file()
        )
        if guided and not batch and self.prepare_all.running:
            raise ValueError("Prepare all is running and uses the browser. Wait for it, or stop it in To review.")
        rel = self.manual.test_file(scenario["id"])
        release = str(data.get("release") or self.settings.get()["release"] or "").strip()
        tester = str(data.get("tester") or "").strip()
        if data.get("prepare"):
            config = ai_providers.config_from(self.settings.get())
            problem = config.problem() if config.provider else "Choose an AI provider in Settings first."
            if problem:
                raise ValueError(problem)
            ai = {
                "provider": config.provider,
                "model": config.model,
                "base_url": config.base_url,
                "key_env": config.key_env,
                "workspace": config.workspace,
            }
            state = self.recording.start_guided(
                scenario, test_id=self.manual.test_id(scenario["id"]), rel_file=rel, release=release, ai=ai
            )
            return {"mode": "prepare", "recording": state}
        if (self.tests_root / rel).is_file() and not data.get("by_hand"):
            review = self.manual.reviews().get(scenario["id"])
            if review and not review.get("approved_at"):
                raise ValueError("An AI prepared this scenario. Check its pictures and approve it before it runs.")
            # the evidence choices remembered from New run (screenshots, video, show the browser)
            raw = data.get("options")
            chosen: dict[str, Any] = raw if isinstance(raw, dict) else {}
            options: dict[str, Any] = {
                k: chosen[k] for k in ("screenshots", "video", "headed", "highlight") if k in chosen
            }
            options.update(label=f"Automatic: {scenario['title']}"[:80], release=release, tester=tester)
            return {"mode": "automatic", "run": self._run_view(self.queue.submit(rel, options))}
        state = self.recording.start_guided(
            scenario, test_id=self.manual.test_id(scenario["id"]), rel_file=rel, release=release, tester=tester
        )
        return {"mode": "by_hand", "recording": state}

    def start_prepare_all(self, data: dict[str, Any]) -> dict[str, Any]:
        """Prepare every scenario that still needs preparing (or the ones asked for), one by one."""
        config = ai_providers.config_from(self.settings.get())
        problem = config.problem() if config.provider else "Choose an AI provider in Settings first."
        if problem:
            raise ValueError(problem)
        if self.prepare_all.running:
            raise ValueError("Prepare all is already running")
        if self.recording.state().get("status") in ("recording", "saving"):
            raise ValueError("a scenario or recording is in progress; finish it first")
        scenarios = self.manual.summary()["scenarios"]
        self._with_results(scenarios)
        # Left out: what plays by itself or waits for review, and what the AI would stop at for
        # certain (test data missing, values to type that the script does not give, no steps).
        todo = [
            s
            for s in scenarios
            if not s["automated"]
            and not s["review"]
            and not s.get("blank_data")
            and not s.get("values_missing")
            and s.get("step_count")
        ]
        if isinstance(data.get("ids"), list):
            wanted = [str(i) for i in data["ids"]]
            todo = sorted((s for s in todo if s["id"] in wanted), key=lambda s: wanted.index(s["id"]))
        return self.prepare_all.begin(todo)

    def review_list(self) -> dict[str, Any]:
        """The scenarios an AI prepared that wait for a person, each with the picture of every step."""
        scenarios = self.manual.summary()["scenarios"]
        self._with_results(scenarios)
        out = []
        for s in scenarios:
            if s["review"] != "needs_review":
                continue
            run = self.queue.store.get(str(s["prepared"]["run_id"]))
            results = self._suite_results(run) if run else []
            folder = results[0]["folder"] if results else ""
            record = insights.read_json(self.evidence_root / folder / "run.json") if folder else None
            steps = [
                {
                    "name": str(st.get("intent") or ""),
                    "status": st.get("status"),
                    "picture_url": self._url_rel(f"{folder}/{st['evidence'][0]}") if st.get("evidence") else None,
                }
                for st in (record or {}).get("steps", [])
            ]
            out.append(
                {
                    **{k: s.get(k) for k in ("id", "title", "ref", "module", "product", "file")},
                    "prepared": s["prepared"],
                    "steps": steps,
                }
            )
        return {"scenarios": out, "prepare_all": self.prepare_all.view()}

    def _manual_done(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """A scenario done by hand has been saved: put it in the run history like any other run."""
        if not state.get("suite_dir"):
            return None
        options = {
            **DEFAULT_OPTIONS,
            "release": state.get("release", ""),
            "tester": state.get("tester", ""),
            "headed": True,
            "label": f"By hand: {state.get('title', '')}"[:80],
        }
        by_ai = state.get("mode") == "ai"
        if by_ai:
            options["label"] = f"Prepared by AI: {state.get('title', '')}"[:80]
            options["tester"] = "AI"
        run = self.queue.store.add_finished(
            str(state.get("file", "")),
            options,
            status="passed" if state.get("result") == "passed" else "failed",
            started_at=state.get("started_at"),
            finished_at=state.get("finished_at"),
            suite_dir=state.get("suite_dir"),
            summary=state.get("summary") or None,
        )
        scenario_id = str(state.get("scenario_id") or "")
        if by_ai and state.get("automated"):
            self.manual.mark_prepared(scenario_id, run["id"], ai_providers.config_from(self.settings.get()).label)
        elif not by_ai and state.get("automated"):
            self.manual.checked_by_hand(scenario_id)
        return run

    def _recording_view(self) -> dict[str, Any]:
        state = self.recording.state()
        feed = state.get("feed") or {}
        folder = self._relative(feed.get("guide_folder"))
        for step in feed.get("guide") or []:
            picture = step.get("picture")
            step["picture_url"] = self._url_rel(f"{folder}/{picture}") if folder and picture else None
        if state.get("run_id"):
            run = self.queue.store.get(str(state["run_id"]))
            results = self._suite_results(run) if run else []
            state["document_url"] = results[0]["document_url"] if results else None
            folder = folder or (results[0]["folder"] if results else None)  # the feed is gone once it is saved
        diary = f"{folder}/ai-diary.txt" if folder else ""
        state["diary_url"] = self._url_rel(diary) if diary and (self.evidence_root / diary).is_file() else None
        return state

    def _with_results(self, scenarios: list[dict[str, Any]], history: bool = False) -> None:
        """Each scenario's own results in Quartermaster (not what the workbook says): the latest, and
        whether it now plays by itself."""
        runs = self.queue.store.list(limit=500)
        results = insights.test_results(runs)
        by_test: dict[str, list[dict[str, Any]]] = {}
        for r in results:
            by_test.setdefault(r["test_id"], []).append(r)
        reviews = self.manual.reviews()
        for s in scenarios:
            review = reviews.get(str(s.get("id", "")))
            s["review"] = None if not review else ("approved" if review.get("approved_at") else "needs_review")
            s["prepared"] = review
            mine = by_test.get(self.manual.test_id(str(s.get("id", ""))), [])
            s["automated"] = (self.tests_root / self.manual.test_file(str(s.get("id", "")))).is_file()
            s["test_file"] = self.manual.test_file(str(s.get("id", "")))
            s["qm_result"] = _result_view(mine[0]) if mine else None
            if history:
                s["qm_history"] = [_result_view(r) for r in mine[:20]]

    # ------------------------------------------------------------------ views

    def pod(self) -> dict[str, Any]:
        """The pod runs use and its default user: the environment in use, else the terminal's variables."""
        env = self._env()
        if env:
            default = next((u for u in self.environments.users(env["id"]) if not u["persona"]), None)
            return {
                "url": env["url"],
                "user": default["username"] if default else "",
                "password_set": bool(default and default["secret"]),
                "client": env["client"],
                "client_id": env["client_id"],
                "environment_id": env["id"],
                "kind": env["kind"],
                "sign_in": env["sign_in"],
                "set_up_in": "settings",
            }
        url = os.environ.get("QM_FUSION_URL", "")
        return {
            "url": url,
            "user": os.environ.get("QM_FUSION_USER", ""),
            "password_set": bool(os.environ.get("QM_FUSION_PASSWORD")),
            "client": "",
            "client_id": "",
            "environment_id": "",
            "kind": os.environ.get("QM_FUSION_KIND", "DEV").upper(),
            "sign_in": "password",
            "set_up_in": "terminal" if url else "",
        }

    def status(self) -> dict[str, Any]:
        pod = self.pod()
        url = pod["url"]
        settings = self.settings.get()
        runs = self.queue.store.list(limit=1)
        return {
            **settings,
            "pod_url": url,
            "pod_host": urlsplit(url).hostname or "",
            "pod_check": self.pod_check,
            "last_run": self._run_view(runs[0]) if runs else None,
            "user": pod["user"],
            "password_set": pod["password_set"],
            "client": pod["client"],
            "client_id": pod["client_id"],
            "environment_id": pod["environment_id"],
            "environment_kind": pod["kind"],
            "sign_in": pod["sign_in"],
            "set_up_in": pod["set_up_in"],
            "tests_folder": str(self.tests_root),
            "releases_folder": str(self.releases.folder or ""),
            "evidence_folder": str(self.evidence_root),
            "default_options": {**DEFAULT_OPTIONS, "retries": self._retries(), "parallel": self._parallel()},
            "ai": self._ai_view(settings),
            "signed_in_by_hand": self.signin.view(pod["environment_id"]),
            "ready": bool(
                url and ((pod["user"] and pod["password_set"]) or self.signin.session(pod["environment_id"]))
            ),
        }

    def tests(self) -> list[dict[str, Any]]:
        runs = self.queue.store.list(limit=500)  # newest first
        last: dict[str, dict[str, Any]] = {}
        for run in runs:
            last.setdefault(run["target"], run)
        latest: dict[str, dict[str, Any]] = {}  # each test's own latest result, whatever run it was part of
        validated: dict[str, str] = {}  # the release each test last passed on
        results = insights.test_results(runs)
        stable = insights.stability(results)
        for r in results:
            latest.setdefault(
                r["test_id"],
                {
                    "status": r["status"],
                    "at": r["at"],
                    "run_id": r["service_run_id"],
                    "release": r["release"],
                    "duration": r.get("duration"),
                    "environment": urlsplit(r.get("environment_url") or "").hostname,
                },
            )
            if r["status"] in insights.PASSING and r["release"]:
                validated.setdefault(r["test_id"], r["release"])
        out = []
        ticket_links = self.tickets.links()
        for f in files_of_tests(self.tests_root):
            rel = f.relative_to(self.tests_root).as_posix()
            item: dict[str, Any] = {
                "file": rel,
                "folder": rel.rpartition("/")[0],
                "updated": datetime.fromtimestamp(f.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            }
            try:
                spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                if not isinstance(spec, dict):
                    raise ValueError("not a test spec")
                item.update(
                    id=str(spec.get("id", "")),
                    title=str(spec.get("title", "")),
                    module=str(spec.get("module", "")),
                    product=str(spec.get("product", "")),
                    process=str(spec.get("process", "")),
                    persona=str(spec.get("persona", "")),
                    priority=str(spec.get("priority", "")),
                    tags=[str(t) for t in spec.get("tags") or []],
                    owner=str(spec.get("owner", "")),
                    steps=len(_expanded(spec, f)[0].get("steps") or []),
                    uses=_uses(spec),
                )
            except (yaml.YAMLError, ValueError, OSError) as e:
                item["problem"] = f"Could not read this file: {e}"
            previous = last.get(rel)
            item["last_run"] = self._run_view(previous) if previous else None
            item["last_result"] = latest.get(item.get("id", ""))
            item["stability"] = stable.get(item.get("id", ""))
            item["release_validated"] = validated.get(item.get("id", ""))
            item["tickets"] = ticket_links.get(item.get("id", ""), [])
            out.append(item)
        return out

    def export_tests(self, rel: str) -> Reply:
        """A zip of the tests (one, or all that can be read) as plain Playwright for Python files (see export/)."""
        import io
        import tempfile
        import zipfile

        from quartermaster.dsl.loader import SpecError, load_test
        from quartermaster.export.playwright_py import ExportError, export

        files = [self._test_file(rel)] if rel else files_of_tests(self.tests_root)
        tests: list[tuple[Any, str]] = []
        for f in files:
            try:
                tests.append((load_test(f), f.relative_to(self.tests_root).as_posix()))
            except (SpecError, OSError, ValueError):
                if rel:
                    raise ApiError(
                        HTTPStatus.BAD_REQUEST, "this test file cannot be read, so it cannot be exported"
                    ) from None
        with tempfile.TemporaryDirectory(prefix="qm-export-") as scratch:
            try:
                written = export(tests, Path(scratch) / "tests")
            except ExportError as e:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for path in written:
                    z.write(path, path.name)
        name = (
            f"{_slug_name(tests[0][0].id)}-playwright.zip"
            if rel
            else f"quartermaster-tests-playwright-{datetime.now():%Y%m%d}.zip"
        )
        self.audit.add("Exported tests as Playwright", rel or "all tests", {"tests": str(len(tests))})
        return Reply(HTTPStatus.OK, buf.getvalue(), "application/zip", name)

    def _suites(self, method: str, route: list[str], data: dict[str, Any]) -> Reply:
        """Saved suites: list, save, delete and preview."""
        try:
            if method == "GET" and not route:
                return _json(suites.listing(self.tests_root, self.tests()))
            if method == "POST" and route == ["preview"]:
                return _json(suites.preview(data, self.tests()))
            if method == "POST" and not route:
                saved = suites.save(self.tests_root, data, self.tests())
                self.audit.add(
                    "Changed a suite" if data.get("name") else "Made a suite",
                    saved["title"],
                    {"tests": str(saved["tests"])},
                )
                return _json(saved, HTTPStatus.CREATED)
            if method == "POST" and route == ["delete"]:
                name = str(data.get("name") or "")
                suites.delete(self.tests_root, name)
                self.audit.add("Deleted a suite", name, {})
                return _json({"deleted": name})
        except LookupError as e:
            raise ApiError(HTTPStatus.NOT_FOUND, str(e).strip("'\"")) from e
        except ValueError as e:  # SuiteError is a ValueError
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
        raise ApiError(HTTPStatus.NOT_FOUND, "not found")

    def _audit_export(self, query: dict[str, list[str]]) -> Reply:
        """The audit log (or the part asked for) as a zip with a manifest, and the export itself goes in the log."""
        asked = {k: str((query.get(k) or [""])[0]).strip() for k in ("format", "from", "to", "who", "text", "what")}
        try:
            data, name, manifest = auditexport.build(
                self.audit,
                fmt=asked["format"] or "csv",
                exported_by=self._who_name() or computer_user(),
                since=asked["from"],
                until=asked["to"],
                who=asked["who"],
                text=asked["text"],
                action=asked["what"],
            )
        except ValueError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
        self.audit.add(
            "Exported the audit log",
            name,
            {
                "format": manifest["format"],
                "entries": manifest["entries"],
                **{f"filter {k}": v for k, v in manifest["filters"].items()},
            },
            who=self._who_name(),
        )
        return Reply(HTTPStatus.OK, data, "application/zip", name)

    def _packs(self, method: str, route: list[str], data: dict[str, Any]) -> Reply:
        """The test library: the packs that come with Quartermaster, and installing one into the tests folder."""
        try:
            if method == "GET" and not route:
                return _json({"folder": f"{self.tests_root.name}/{packs.INSTALL_DIR}", "packs": self._pack_views()})
            if method == "POST" and route == ["install"]:
                pack = packs.catalog.get_pack(str(data.get("pack") or ""))
                done = packs.install(self.tests_root, pack)
                self.audit.add(
                    "Installed a library pack",
                    pack.title,
                    {k: len(v) for k, v in done.items() if isinstance(v, list) and v},
                )
                return _json({**done, "pack": self._pack_views(pack.id)[0]}, HTTPStatus.CREATED)
        except packs.catalog.PackError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
        raise ApiError(HTTPStatus.NOT_FOUND, "not found")

    def _pack_views(self, only: str = "") -> list[dict[str, Any]]:
        out = []
        for pack in packs.list_packs():
            if only and pack.id != only:
                continue
            out.append(
                {
                    "id": pack.id,
                    "title": pack.title,
                    "module": pack.module,
                    "kind": pack.kind,
                    "version": pack.version,
                    "description": pack.description,
                    "needs": pack.needs,
                    "read_only": pack.read_only,
                    "checked": pack.checked,
                    "tests": pack.tests,
                    "installed": packs.pack_status(self.tests_root, pack),
                }
            )
        return out

    def _check_suite(self, name: str) -> None:
        suites.tests_of(self.tests_root, name, self.tests())

    def test_data(self) -> dict[str, Any]:
        """The data sets, who uses them, the pods that lack a value, and the values tests make fresh."""
        pods: list[dict[str, str]] = []
        for client in self.environments.listing()["clients"]:
            if self.client_id and client["id"] != self.client_id:
                continue
            pods += [{"name": e["name"], "kind": e["kind"]} for e in client["environments"]]
        return testdata.overview(self.tests_root, pods)

    def library(self) -> dict[str, Any]:
        """The shared step groups, which tests use each, and the files that could not be used."""
        directory = self.tests_root / LIBRARY_DIR
        groups, problems = read_groups(directory if directory.is_dir() else None)
        used: dict[str, list[dict[str, str]]] = {}
        for f in files_of_tests(self.tests_root):
            try:
                spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except (yaml.YAMLError, OSError):
                continue
            for name in _uses(spec) if isinstance(spec, dict) else []:
                used.setdefault(name, []).append(
                    {
                        "file": f.relative_to(self.tests_root).as_posix(),
                        "title": str(spec.get("title") or spec.get("id")),
                    }
                )
        out = []
        for name, (path, raw) in sorted(groups.items()):
            out.append(
                {
                    "name": name,
                    "title": str(raw.get("title") or ""),
                    "description": str(raw.get("description") or ""),
                    "file": path.relative_to(self.tests_root).as_posix(),
                    "params": [
                        {"name": str(k), "default": None if v is None else str(v)}
                        for k, v in (raw.get("params") or {}).items()
                    ],
                    "steps": [
                        {"action": str(s.get("action", "")), "intent": str(s.get("intent", ""))} for s in raw["steps"]
                    ],
                    "cleanup": len(raw.get("cleanup") or []),
                    "used_by": used.get(name, []),
                    "yaml": path.read_text(encoding="utf-8"),
                }
            )
        missing = sorted(set(used) - set(groups))
        return {
            "folder": f"{self.tests_root.name}/{LIBRARY_DIR}",
            "groups": out,
            "problems": [
                {"file": p.relative_to(self.tests_root).as_posix(), "problem": why[:300]} for p, why in problems
            ],
            "missing": [{"name": m, "used_by": used[m]} for m in missing],
        }

    def test_detail(self, rel: str) -> dict[str, Any]:
        path = self._test_file(rel)
        text = path.read_text(encoding="utf-8")
        item = next((t for t in self.tests() if t["file"] == path.relative_to(self.tests_root).as_posix()), {})
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError:
            loaded = None
        spec: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
        expanded, _ = _expanded(spec, path)
        try:
            folded = merge_sets(expanded, path)  # data sets folded into the test's own data
        except DataError:
            folded = expanded
        data: dict[str, Any] = folded["data"] if isinstance(folded.get("data"), dict) else {}
        pods: dict[str, Any] = folded["pods"] if isinstance(folded.get("pods"), dict) else {}
        rules = folded.get("generate") if isinstance(folded.get("generate"), dict) else {}
        before = [
            {"number": i + 1, "intent": str(c.get("intent", "")), "value": _fill(str(c.get("value") or ""), data)}
            for i, c in enumerate(expanded.get("setup") or [])
            if isinstance(c, dict)
        ]
        steps = []
        for i, step in enumerate(expanded.get("steps") or []):
            if not isinstance(step, dict):
                continue
            target: dict[str, Any] = step["target"] if isinstance(step.get("target"), dict) else {}
            strategies = [s for s in target.get("strategies") or [] if isinstance(s, dict)]
            found_by = [insights.describe([str(k), _fill(str(v), data)]) for s in strategies for k, v in s.items()]
            steps.append(
                {
                    "number": i + 1,
                    "action": step.get("action", ""),
                    "intent": step.get("intent", ""),
                    "value": "" if step.get("value") is None else _fill(str(step.get("value")), data),
                    "found_by": found_by,
                    "shared": step.get("shared"),
                }
            )
        history = insights.test_history(item.get("id", ""), self.queue.store.list(limit=500))
        for h in history:
            h["document_url"] = self._url_rel(h.pop("document"))
        tested = [h for h in history if h["status"] in ("passed", "healed", "failed")]
        passed = sum(h["status"] != "failed" for h in tested)
        return {
            **item,
            "data": data,
            "setup": before,
            "data_sets": [str(n) for n in spec.get("data_sets") or []]
            if isinstance(spec.get("data_sets"), list)
            else [],
            "pods": [
                {"pod": str(p), "values": {str(k): str(v) for k, v in b.items()}}
                for p, b in pods.items()
                if isinstance(b, dict)
            ],
            "generate": [
                {"name": str(k), "rule": testdata.describe_rule(r)} for k, r in rules.items() if isinstance(r, dict)
            ],
            "steps_detail": steps,
            "yaml": text,
            "history": history,
            "pass_rate": round(100 * passed / len(tested)) if tested else None,
        }

    def attention(self) -> dict[str, Any]:
        result = insights.attention(
            self.tests(), self.queue.store.list(limit=500), self.tests_root, self.settings.get()["release"]
        )
        dismissed = self._dismissed()
        links = self.tickets.links()
        kept = []
        for item in result["items"]:
            item["key"] = attention_key(item)
            if item["key"] in dismissed:
                continue
            item["tickets"] = links.get(str(item.get("test_id")), []) if item.get("test_id") else []
            item["picture_url"] = self._url_rel(item.pop("picture", None))
            item["document_url"] = self._url_rel(item.pop("document", None))
            kept.append(item)
        counts: dict[str, int] = {}
        for item in kept:
            counts[item["category"]] = counts.get(item["category"], 0) + 1
        result.update(items=kept, count=len(kept), counts=counts, dismissed=len(result["items"]) - len(kept))
        return result

    @property
    def _dismissed_file(self) -> Path:
        return self.backups.parent / "attention_dismissed.json"

    def _dismissed(self) -> set[str]:
        try:
            data = json.loads(self._dismissed_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return {str(k) for k in data} if isinstance(data, list) else set()

    def dismiss_attention(self, data: dict[str, Any]) -> dict[str, Any]:
        """Hide items from Needs attention. A key names one failure of one run, so the same test
        failing again on a later run shows up again."""
        keys = data.get("keys")
        if not isinstance(keys, list) or not keys:
            raise ApiError(HTTPStatus.BAD_REQUEST, "choose what to dismiss")
        current = {
            attention_key(i)
            for i in insights.attention(
                self.tests(), self.queue.store.list(limit=500), self.tests_root, self.settings.get()["release"]
            )["items"]
        }
        wanted = {str(k) for k in keys} & current  # only what is listed now; old keys are dropped
        kept = (self._dismissed() & current) | wanted
        self._dismissed_file.parent.mkdir(parents=True, exist_ok=True)
        self._dismissed_file.write_text(json.dumps(sorted(kept)), encoding="utf-8")
        return {"dismissed": len(wanted)}

    def sr_draft(self, run_id: str, test_id: str) -> dict[str, str]:
        """A draft Oracle service request for a failed test that Needs attention lists."""
        result = insights.attention(
            self.tests(), self.queue.store.list(limit=500), self.tests_root, self.settings.get()["release"]
        )
        item = next(
            (
                i
                for i in result["items"]
                if i.get("run_id") == run_id and i.get("test_id") == test_id and (i.get("cause") or {}).get("sr")
            ),
            None,
        )
        if item is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "no failure of that test that looks caused by the update")
        record = insights.read_json(self.evidence_root / str(item.get("folder") or "") / "run.json")
        return sr_draft(item, record if isinstance(record, dict) else {})

    def accept_update(self, data: dict[str, Any]) -> dict[str, Any]:
        path = self._test_file(str(data.get("file") or ""))
        try:
            backup = accept_update(
                path,
                int(data.get("step_index", -1)),
                [str(x) for x in data.get("new") or []],
                self.backups,
                add=bool(data.get("add")),
            )
        except ValueError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
        return {"updated": path.relative_to(self.tests_root).as_posix(), "backup": str(backup)}

    def duplicate_test(self, data: dict[str, Any]) -> dict[str, Any]:
        source = self._test_file(str(data.get("file") or ""))
        try:
            made = testedit.duplicate(
                self.tests_root,
                source,
                str(data.get("id") or ""),
                str(data.get("title") or ""),
                str(data.get("folder") or ""),
                self.backups,
            )
        except ValueError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
        rel = made.relative_to(self.tests_root).as_posix()
        self.audit.add("Copied a test", rel, {"from": source.relative_to(self.tests_root).as_posix()})
        return {"file": rel}

    def save_data_set(self, data: dict[str, Any]) -> dict[str, Any]:
        try:
            path = testedit.save_set(self.tests_root, data, self.backups)
        except ValueError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e)) from e
        rel = path.relative_to(self.tests_root).as_posix()
        self.audit.add("Saved a data set", rel, {})
        return {"file": rel}

    def _test_file(self, rel: str) -> Path:
        path = (self.tests_root / rel).resolve()
        if self.tests_root not in path.parents or path.suffix.lower() not in (".yaml", ".yml"):
            raise ApiError(HTTPStatus.FORBIDDEN, "only test files in the tests folder can be opened")
        if not path.is_file():
            raise ApiError(HTTPStatus.NOT_FOUND, "no such test file")
        return path

    def run_detail(self, run: dict[str, Any]) -> dict[str, Any]:
        view = self._run_view(run)
        view["events"] = [_with_plain_error(e) for e in self.queue.events(run["id"])]
        view["results"] = self._suite_results(run)
        log = Path(run["log_path"]) if run.get("log_path") else None
        view["output"] = _tail(log) if log and log.is_file() else ""
        return view

    def _run_view(self, run: dict[str, Any], counts: bool = False) -> dict[str, Any]:
        view = {k: v for k, v in run.items() if k not in ("events_path", "log_path")}
        view["summary_url"] = self._url(run.get("summary"))
        view["suite_folder"] = self._relative(run.get("suite_dir"))
        suite = insights.suite_of(run) or {}
        # what the evidence says, else what was asked for
        view["release"] = str(suite.get("release") or run["options"].get("release") or "")
        view["executed_by"] = str(suite.get("executed_by") or run["options"].get("tester") or "")
        view["environment"] = urlsplit(str(suite.get("environment_url") or "")).hostname or None
        if run.get("status") == "error":
            view["error_plain"] = insights.plain_run_error(run.get("error"))
        if counts:
            view["counts"] = insights.run_counts(run)
        return view

    @staticmethod
    def _setup_view(record: dict[str, Any]) -> dict[str, Any] | None:
        """What the test checked or made before its steps, for the run page (None when it has no setup)."""
        if not record.get("setup"):
            return None
        return {
            "status": record.get("setup_status", ""),
            "steps": [
                {
                    "number": c.get("index", 0) + 1,
                    "intent": c.get("intent", ""),
                    "status": c.get("status", ""),
                    "error": plain_error(c.get("error")),
                    "detail": c.get("error"),
                    "note": c.get("note"),
                }
                for c in record["setup"]
            ],
        }

    @staticmethod
    def _cleanup_view(record: dict[str, Any]) -> dict[str, Any] | None:
        """What the test's cleanup did, for the run page (None when the test has no cleanup)."""
        if not record.get("cleanup"):
            return None
        return {
            "status": record.get("cleanup_status", ""),
            "steps": [
                {
                    "number": c.get("index", 0) + 1,
                    "intent": c.get("intent", ""),
                    "status": c.get("status", ""),
                    "error": plain_error(c.get("error")),
                    "detail": c.get("error"),
                    "note": c.get("note"),
                }
                for c in record["cleanup"]
            ],
        }

    def _suite_results(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        """Each test's result with links to its evidence, from the suite record once the run has ended."""
        if not run.get("suite_dir"):
            return []
        suite_json = Path(run["suite_dir"]) / "suite.json"
        if not suite_json.is_file():
            return []
        suite = json.loads(suite_json.read_text(encoding="utf-8"))
        results = []
        for entry in suite.get("runs", []):
            run_dir = self.evidence_root / entry["run_dir"]
            record_file = run_dir / "run.json"
            record = insights.read_json(record_file) or {}
            failed = entry.get("failed_step") or None
            results.append(
                {
                    **{k: entry.get(k) for k in ("test_id", "test_title", "status", "steps_total", "steps_passed")},
                    "duration": entry.get("duration"),
                    "folder": entry["run_dir"],
                    "document_url": self._url_rel(entry.get("document")),
                    "videos": [self._url_rel(f"{entry['run_dir']}/{v}") for v in record.get("videos", [])],
                    "failed_step": failed
                    and {
                        "number": failed.get("number"),
                        "intent": failed.get("intent"),
                        "error": plain_error(failed.get("error")),
                        "detail": failed.get("error"),
                        "picture_url": self._url_rel(failed.get("screenshot")),
                    },
                    "needs_update": any(h.get("source", "fallback") == "fallback" for h in entry.get("healing") or []),
                    "flaky": bool(entry.get("flaky")),
                    "setup": self._setup_view(record),
                    "cleanup": self._cleanup_view(record),
                    "started_at": record.get("started_at"),
                    "finished_at": record.get("finished_at"),
                    "record_url": self._url_rel(f"{entry['run_dir']}/run.json") if record else None,
                    "steps": [
                        {
                            "number": st.get("index", 0) + 1,
                            "intent": st.get("intent", ""),
                            "action": st.get("action", ""),
                            "by": record.get("mode", "automatic"),  # "manual" or "ai" for manual scenarios
                            "value": st.get("value"),
                            "expected": st.get("expected", ""),
                            "status": st.get("status", ""),
                            "error": plain_error(st.get("error")),
                            "detail": st.get("error"),
                            "compare": insights.expected_observed(st.get("error")),
                            "locator": st.get("locator"),
                            "attempts": st.get("attempts", 1),
                            "first_error": plain_error(st.get("first_error")),
                            "started_at": st.get("started_at"),
                            "seconds": round((st.get("duration_ms") or 0) / 1000, 1),
                            "screenshot_note": st.get("screenshot_note"),
                            "pictures": [self._url_rel(f"{entry['run_dir']}/{p}") for p in st.get("evidence") or []],
                        }
                        for st in record.get("steps", [])
                    ],
                }
            )
        return results

    # ------------------------------------------------------------------ files

    def _inside_evidence(self, rel: str) -> Path:
        path = (self.evidence_root / rel).resolve()
        if path != self.evidence_root and self.evidence_root not in path.parents:
            raise ApiError(HTTPStatus.FORBIDDEN, "only files in the evidence folder can be opened")
        if not path.exists():
            raise ApiError(HTTPStatus.NOT_FOUND, "that file is no longer there")
        return path

    def _file(self, rel: str) -> Reply:
        path = self._inside_evidence(rel)
        if not path.is_file():
            raise ApiError(HTTPStatus.NOT_FOUND, "not a file")
        kind = _TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        download = path.name if path.suffix.lower() == ".docx" else None
        return Reply(HTTPStatus.OK, path.read_bytes(), kind, download)

    def _open(self, rel: str) -> Path:
        path = self._inside_evidence(rel)
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined,unused-ignore]  # Windows only
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
        return path

    def _page(self, name: str) -> Reply:
        kind = {"html": "text/html", "js": "text/javascript", "css": "text/css"}[name.rsplit(".", 1)[1]]
        return Reply(HTTPStatus.OK, (WEB_DIR / name).read_bytes(), f"{kind}; charset=utf-8")

    def _relative(self, path: str | None) -> str | None:
        if not path:
            return None
        try:
            return Path(path).resolve().relative_to(self.evidence_root).as_posix()
        except ValueError:
            return None

    def _url(self, path: str | None) -> str | None:
        return self._url_rel(self._relative(path))

    @staticmethod
    def _url_rel(rel: str | None) -> str | None:
        return f"/files/{quote(rel)}" if rel else None

    @staticmethod
    def _body(body: bytes) -> dict[str, Any]:
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, "the request is not valid JSON") from e
        if not isinstance(data, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "the request must be a JSON object")
        return data


def _result_view(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": r.get("status"),
        "at": r.get("at"),
        "run_id": r.get("service_run_id"),
        "release": r.get("release"),
        "by_hand": r.get("mode") == "manual",
        "how": {"manual": "by_hand", "ai": "prepared"}.get(str(r.get("mode")), "automatic"),
    }


def _fill(text: str, data: dict[str, Any]) -> str:
    """Show ${name} placeholders with the test data they stand for (unknown names stay as written)."""
    return re.sub(r"\$\{(\w+)\}", lambda m: str(data[m.group(1)]) if m.group(1) in data else m.group(0), text)


def _with_plain_error(event: dict[str, Any]) -> dict[str, Any]:
    """Failed steps also carry the problem in everyday words, as the evidence document puts it."""
    return {**event, "plain_error": plain_error(event["error"])} if event.get("error") else event


def _tail(log: Path, n: int = 40) -> str:
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


# ---------------------------------------------------------------------- HTTP


def _slug_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "test"


def _uses(spec: Any) -> list[str]:
    """The shared groups a test file uses, in order, each once."""
    names: list[str] = []
    if isinstance(spec, dict):
        for step in [*(spec.get("steps") or []), *(spec.get("cleanup") or [])]:
            if isinstance(step, dict) and isinstance(step.get("use"), str) and step["use"] not in names:
                names.append(step["use"])
    return names


def _expanded(spec: Any, path: Path) -> tuple[dict[str, Any], list[tuple[str | None, int]]]:
    """The spec with shared steps in place; the file as it is when the shared steps cannot be read."""
    try:
        out, origins = expand(spec, path)
    except LibraryError:
        out, origins = spec, []
    return (out if isinstance(out, dict) else {}), origins


def attention_key(item: dict[str, Any]) -> str:
    """Names one item of Needs attention: one failure of one run, one screen change, one bad file."""
    cat = str(item.get("category", ""))
    if cat == "ui_change":
        return f"ui:{item.get('test_id')}:{item.get('run_id')}:{item.get('step_index')}"
    if cat == "unreadable":
        return f"file:{item.get('file')}:{hashlib.sha1(str(item.get('error')).encode()).hexdigest()[:10]}"
    if cat == "cleanup":
        return f"cleanup:{item.get('test_id')}:{item.get('run_id')}"
    if item.get("test_id"):
        return f"fail:{item.get('test_id')}:{item.get('run_id')}"
    return f"run:{item.get('run_id')}"


def port_of(server: Any) -> int:
    return int(server.server_address[1])


class Handles(Protocol):
    def handle(self, method: str, raw_path: str, body: bytes, cookie: str = "") -> Reply: ...


MAX_BODY = 100 * 1024 * 1024  # the biggest request body the service reads


def _body_length(header: str | None) -> int:
    """The request body size, refusing bad or huge values before reading anything."""
    try:
        length = int(header or 0)
    except ValueError:
        raise ApiError(HTTPStatus.BAD_REQUEST, "bad Content-Length") from None
    if length < 0:
        raise ApiError(HTTPStatus.BAD_REQUEST, "bad Content-Length")
    if length > MAX_BODY:
        raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request is too large")
    return length


def make_server(app: Handles, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "Quartermaster"

        def do_GET(self) -> None:
            self._serve("GET")

        def do_POST(self) -> None:
            self._serve("POST")

        def _serve(self, method: str) -> None:
            try:
                self._check_origin(method)
                length = _body_length(self.headers.get("Content-Length"))
                reply = app.handle(
                    method, self.path, self.rfile.read(length) if length else b"", self.headers.get("Cookie") or ""
                )
            except ApiError as e:
                reply = _json({"error": str(e)}, e.status)
            except Exception as e:  # keep serving; show the problem in the page
                reply = _json({"error": f"{type(e).__name__}: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_response(reply.status)
            self.send_header("Content-Type", reply.content_type)
            self.send_header("Content-Length", str(len(reply.body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'; object-src 'none'")
            if reply.location:
                self.send_header("Location", reply.location)
            for cookie in [reply.set_cookie] if isinstance(reply.set_cookie, str) else reply.set_cookie or []:
                self.send_header("Set-Cookie", cookie)
            if reply.download_name:
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(reply.download_name)}")
            self.end_headers()
            self.wfile.write(reply.body)

        def _check_origin(self, method: str) -> None:
            """Only pages served by this service, on this computer, may use it."""
            port_now = port_of(self.server)
            allowed = {f"127.0.0.1:{port_now}", f"localhost:{port_now}"}
            if self.headers.get("Host") not in allowed:
                raise ApiError(HTTPStatus.FORBIDDEN, f"open Quartermaster at http://127.0.0.1:{port_now}")
            if method == "POST":
                origin = self.headers.get("Origin")
                if origin is not None and urlsplit(origin).netloc not in allowed:
                    raise ApiError(HTTPStatus.FORBIDDEN, "requests from other web sites are not allowed")
                if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                    raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send JSON")

        def log_message(self, format: str, *args: Any) -> None:
            pass  # quiet: the page shows what happens

    return ThreadingHTTPServer((host, port), Handler)
