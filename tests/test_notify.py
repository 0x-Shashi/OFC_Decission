"""Notifications: who is told, when, and what is in the message (never secrets or test data)."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from test_service_queue import fake_command

from quartermaster.service.audit import AuditLog
from quartermaster.service.notify import (
    Notifier,
    NotifyError,
    build_message,
    plain_text,
    webhook_payload,
)

SECRET_URL = "https://hooks.example.com/services/T000/B000/SECRETSECRET"
PASSWORD = "mail-p4ssw0rd-xyz"


def make(tmp_path: Path, **kw: Any) -> Notifier:
    return Notifier(
        tmp_path / ".qm",
        client=lambda: "Acme Corp",
        address=lambda: "http://127.0.0.1:8765",
        audit=AuditLog(tmp_path / ".qm" / "audit.jsonl"),
        key_file=tmp_path / "vault.key",
        background=False,
        **kw,
    )


def capture(n: Notifier) -> list[tuple[str, dict[str, Any]]]:
    """Stand-in for the network: what would have been posted."""
    sent: list[tuple[str, dict[str, Any]]] = []
    n._send_json = lambda url, body: sent.append((url, json.loads(body)))  # type: ignore[method-assign]
    return sent


def suite(tmp_path: Path, entries: list[dict[str, Any]]) -> str:
    folder = tmp_path / "suite"
    folder.mkdir(exist_ok=True)
    (folder / "suite.json").write_text(
        json.dumps(
            {
                "release": "26C",
                "environment_url": "https://acme-dev2.fa.us6.oraclecloud.com",
                "runs": entries,
            }
        ),
        encoding="utf-8",
    )
    return str(folder)


def entry(title: str, status: str = "passed", **more: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"test_id": title.lower().replace(" ", "."), "test_title": title, "status": status}
    if status == "failed":
        out["failed_step"] = {
            "number": 2,
            "intent": "Open Locations",
            "error": "ResolutionError: could not resolve 'link:Location': role='link:Location' matched 0",
        }
    return {**out, **more}


def run_row(
    tmp_path: Path, status: str, entries: list[dict[str, Any]] | None = None, label: str = ""
) -> dict[str, Any]:
    return {
        "id": "20261003-0101-AB12",
        "target": "my_tests",
        "status": status,
        "options": {"label": label, "release": "26C"},
        "suite_dir": suite(tmp_path, entries or []) if entries is not None else None,
        "error": "The service stopped during this run" if status == "error" else None,
    }


def enable_webhook(n: Notifier, **more: Any) -> None:
    n.update({"webhook": {"enabled": True, "kind": "slack", "url": SECRET_URL}, **more})


# ------------------------------------------------------------------ the settings


def test_the_defaults_notify_nobody_and_say_so(tmp_path: Path) -> None:
    v = make(tmp_path).view()
    assert v["when"] == "scheduled" and v["what"] == "failures" and v["details"] is False
    assert not v["webhook"]["enabled"] and not v["email"]["enabled"] and v["log"] == []
    assert make(tmp_path).compose(run_row(tmp_path, "failed", [entry("A", "failed")], "Scheduled: x")) is None


def test_secrets_are_saved_encrypted_and_never_shown(tmp_path: Path) -> None:
    n = make(tmp_path)
    n.update({"webhook": {"enabled": True, "url": SECRET_URL}})
    n.update(
        {"email": {"host": "mail.example.com", "sender": "qm@example.com", "to": "a@example.com", "password": PASSWORD}}
    )
    shown = json.dumps(n.view())
    assert "SECRETSECRET" not in shown and PASSWORD not in shown
    assert n.view()["webhook"]["url_set"] and n.view()["webhook"]["host"] == "hooks.example.com"
    assert n.view()["email"]["password_set"]
    on_disk = (tmp_path / ".qm" / "notifications.json").read_text(encoding="utf-8")
    assert "SECRETSECRET" not in on_disk and PASSWORD not in on_disk and "hooks.example.com" not in on_disk


def test_a_setting_left_out_is_kept_and_an_empty_one_removes_it(tmp_path: Path) -> None:
    n = make(tmp_path)
    enable_webhook(n)
    n.update({"webhook": {"kind": "teams"}})  # the address is kept
    assert n.view()["webhook"]["url_set"] and n.view()["webhook"]["kind"] == "teams"
    n.update({"webhook": {"enabled": False, "url": ""}})
    assert not n.view()["webhook"]["url_set"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"webhook": {"url": "http://hooks.example.com/x"}}, "https://"),
        ({"webhook": {"url": "https://"}}, "https://"),
        ({"webhook": {"enabled": True}}, "paste the webhook address first"),
        ({"webhook": {"kind": "fax"}}, "kind of webhook"),
        ({"webhook": {"password": "x"}}, "unknown webhook settings"),
        ({"email": {"port": 0}}, "port"),
        ({"email": {"port": "abc"}}, "port"),
        ({"email": {"security": "maybe"}}, "security"),
        ({"email": {"to": "not-an-address"}}, "not an e-mail address"),
        ({"email": {"sender": "nobody"}}, "not an e-mail address"),
        ({"email": {"host": "bad host!"}}, "mail server's name"),
        ({"email": {"enabled": True}}, "fill in the mail server"),
        ({"when": "sometimes"}, "which runs"),
        ({"what": "x"}, "what to send"),
        ({"unknown": 1}, "unknown settings"),
        ({"email": "text"}, "must be an object"),
    ],
)
def test_settings_that_cannot_work_are_refused(tmp_path: Path, change: dict[str, Any], message: str) -> None:
    n = make(tmp_path)
    with pytest.raises(NotifyError, match=message):
        n.update(change)
    assert not (tmp_path / ".qm" / "notifications.json").exists()  # nothing half-saved


def test_recipients_can_be_typed_with_commas_or_spaces(tmp_path: Path) -> None:
    n = make(tmp_path)
    n.update({"email": {"to": "a@example.com, b@example.com; c@example.com"}})
    assert n.view()["email"]["to"] == ["a@example.com", "b@example.com", "c@example.com"]


# ------------------------------------------------------------------ who is told, and when


def test_scheduled_runs_only_by_default_and_only_failures(tmp_path: Path) -> None:
    n = make(tmp_path)
    enable_webhook(n)
    failed = [entry("A", "failed"), entry("B")]
    assert n.compose(run_row(tmp_path, "failed", failed, "Scheduled: Nightly")) is not None
    assert n.compose(run_row(tmp_path, "failed", failed, "")) is None  # a person started it and is watching
    assert n.compose(run_row(tmp_path, "passed", [entry("A")], "Scheduled: Nightly")) is None  # nothing to report


def test_every_run_and_every_result_can_be_asked_for(tmp_path: Path) -> None:
    n = make(tmp_path)
    enable_webhook(n, when="all", what="every")
    assert n.compose(run_row(tmp_path, "failed", [entry("A", "failed")])) is not None
    ok = n.compose(run_row(tmp_path, "passed", [entry("A"), entry("B")]))
    assert ok and ok["title"] == "All 2 tests passed"


def test_a_stopped_run_never_sends_anything(tmp_path: Path) -> None:
    n = make(tmp_path)
    enable_webhook(n, when="all", what="every")
    assert n.compose(run_row(tmp_path, "cancelled", [])) is None
    assert n.compose(run_row(tmp_path, "running", [])) is None


def test_a_run_that_could_not_finish_is_reported_in_plain_words(tmp_path: Path) -> None:
    n = make(tmp_path)
    enable_webhook(n)
    m = n.compose(run_row(tmp_path, "error", None, "Scheduled: Nightly"))
    assert m and m["title"] == "A run could not finish"
    assert "Quartermaster stopped" in " ".join(m["lines"]) or "stopped" in " ".join(m["lines"])


def test_a_failed_run_whose_record_cannot_be_read_still_says_it_failed(tmp_path: Path) -> None:
    n = make(tmp_path)
    enable_webhook(n)
    m = n.compose(run_row(tmp_path, "failed", None, "Scheduled: Nightly"))
    assert m and m["title"] == "A run failed"


def test_nothing_is_composed_when_no_channel_is_on(tmp_path: Path) -> None:
    n = make(tmp_path)
    n.update({"when": "all"})
    assert n.compose(run_row(tmp_path, "failed", [entry("A", "failed")])) is None


# ------------------------------------------------------------------ what the message says


def test_the_message_names_the_failed_tests_and_steps(tmp_path: Path) -> None:
    run = run_row(tmp_path, "failed", [entry("Search a worker", "failed"), entry("Hire", "passed"), entry("Pay")])
    m = build_message(run, client="Acme Corp", address="http://127.0.0.1:8765/", details=False)
    assert m["title"] == "1 of 3 tests failed"
    text = plain_text(m)
    assert "Acme Corp: 1 of 3 tests failed" in text and "Search a worker" in text
    assert "step 2: Open Locations" in text and "items not found on the screen" in text
    assert "Release: 26C" in text and "Environment: acme-dev2.fa.us6.oraclecloud.com" in text
    assert m["link"] == "http://127.0.0.1:8765/#/runs/20261003-0101-AB12"


def test_what_the_pod_showed_stays_out_unless_asked_for(tmp_path: Path) -> None:
    bad = entry("Check salary", "failed")
    bad["failed_step"]["error"] = "StepFailure: expected text 'Jane Doe', found 'John Roe'"
    run = run_row(tmp_path, "failed", [bad])
    quiet = plain_text(build_message(run, client="", address="", details=False))
    assert "Jane Doe" not in quiet and "John Roe" not in quiet  # could be somebody's personal data
    loud = plain_text(build_message(run, client="", address="", details=True))
    assert "Jane Doe" in loud


def test_a_long_list_is_cut_and_a_test_that_needed_a_retry_is_mentioned(tmp_path: Path) -> None:
    entries = [entry(f"Test {n}", "failed") for n in range(12)] + [entry("Slow one", flaky=True)]
    m = build_message(run_row(tmp_path, "failed", entries), client="", address="", details=False)
    text = plain_text(m)
    assert text.count("Open Locations") == 8 and "and 4 more" in text
    assert "1 test passed only after a step was tried again" in text


def test_each_service_gets_the_message_in_its_own_shape(tmp_path: Path) -> None:
    m = build_message(run_row(tmp_path, "failed", [entry("A", "failed")]), client="Acme", address="", details=False)
    slack = webhook_payload("slack", m)
    assert list(slack) == ["text"] and slack["text"].startswith("*Acme: 1 of 1 test failed*")
    teams = webhook_payload("teams", m)
    card = teams["attachments"][0]["content"]
    assert teams["type"] == "message" and card["type"] == "AdaptiveCard" and card["body"][0]["text"].startswith("Acme")
    other = webhook_payload("json", m)
    assert other["status"] == "failed" and other["failed"][0]["test"] == "A" and other["tests"]["failed"] == 1
    json.dumps([slack, teams, other])  # all of it can be sent


# ------------------------------------------------------------------ sending


def test_a_failed_run_is_posted_to_the_webhook_and_recorded(tmp_path: Path) -> None:
    n = make(tmp_path)
    sent = capture(n)
    enable_webhook(n)
    n.run_finished(run_row(tmp_path, "failed", [entry("A", "failed")], "Scheduled: Nightly"))
    [(url, payload)] = sent
    assert url == SECRET_URL and "1 of 1 test failed" in payload["text"]
    [log] = n.entries()
    assert log["ok"] and log["channel"] == "webhook" and log["run"] == "20261003-0101-AB12"
    audit = (tmp_path / ".qm" / "audit.jsonl").read_text(encoding="utf-8")
    assert "Sent a notification" in audit and "SECRETSECRET" not in audit


def test_a_webhook_that_refuses_is_recorded_and_never_raises(tmp_path: Path) -> None:
    n = make(tmp_path)

    def refuse(url: str, body: bytes) -> None:
        raise NotifyError("the webhook answered HTTP 404")

    n._send_json = refuse  # type: ignore[method-assign]
    enable_webhook(n)
    n.run_finished(run_row(tmp_path, "failed", [entry("A", "failed")], "Scheduled: Nightly"))
    [log] = n.entries()
    assert not log["ok"] and "HTTP 404" in log["message"]
    assert "could not be sent" in (tmp_path / ".qm" / "audit.jsonl").read_text(encoding="utf-8")


def test_a_run_row_that_cannot_be_read_never_raises(tmp_path: Path) -> None:
    n = make(tmp_path)
    enable_webhook(n, when="all")
    n.run_finished({"status": "failed"})  # no id, no options
    n.run_finished({})


def test_the_log_keeps_only_the_latest_attempts(tmp_path: Path) -> None:
    n = make(tmp_path)
    capture(n)
    enable_webhook(n)
    for _ in range(40):
        n.send_test("webhook")
    assert len(n._lines()) == 30 and len(n.entries()) == 10


def test_a_test_message_goes_out_even_when_the_channel_is_off(tmp_path: Path) -> None:
    n = make(tmp_path)
    sent = capture(n)
    n.update({"webhook": {"url": SECRET_URL}})  # saved, not switched on
    out = n.send_test("webhook")
    assert out["ok"] and "Test message" in sent[0][1]["text"]
    assert not (tmp_path / ".qm" / "audit.jsonl").exists()  # a test is not an event worth auditing
    with pytest.raises(NotifyError, match="webhook or email"):
        n.send_test("pigeon")


def test_a_test_with_nothing_saved_says_what_is_missing(tmp_path: Path) -> None:
    n = make(tmp_path)
    assert n.send_test("webhook") == {"ok": False, "message": "no webhook address is saved"}
    assert not n.send_test("email")["ok"]


# ---- the real network code, against local stand-ins


class Hook:
    """A tiny web server that answers every POST with one status."""

    def __init__(self, status: int):
        from http.server import BaseHTTPRequestHandler, HTTPServer

        outer = self
        self.bodies: list[bytes] = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                outer.bodies.append(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                self.send_response(status)
                self.end_headers()

            def log_message(self, *a: Any) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def test_the_real_post_checks_the_answer(tmp_path: Path) -> None:
    n = make(tmp_path)
    ok, refuses = Hook(200), Hook(404)
    try:
        n._send_json(ok.url, b'{"text": "hi"}')
        assert json.loads(ok.bodies[0]) == {"text": "hi"}
        with pytest.raises(NotifyError, match="HTTP 404"):
            n._send_json(refuses.url, b"{}")
    finally:
        ok.close()
        refuses.close()
    with pytest.raises(NotifyError, match="could not reach the webhook"):
        n._send_json("http://127.0.0.1:9/none", b"{}")


class Smtp:
    """A tiny mail server that keeps what it is sent (no log-in, no encryption)."""

    def __init__(self) -> None:
        self.mails: list[str] = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._talk, args=(conn,), daemon=True).start()

    def _talk(self, conn: socket.socket) -> None:
        f = conn.makefile("rwb")

        def say(text: str) -> None:
            f.write((text + "\r\n").encode())
            f.flush()

        say("220 test ready")
        data: list[str] = []
        reading = False
        for raw in f:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if reading:
                if line == ".":
                    self.mails.append("\n".join(data))
                    data, reading = [], False
                    say("250 queued")
                else:
                    data.append(line[1:] if line.startswith("..") else line)
                continue
            cmd = line.split(" ", 1)[0].upper()
            if cmd in ("EHLO", "HELO"):
                say("250 hello")
            elif cmd == "DATA":
                reading = True
                say("354 go on")
            elif cmd == "QUIT":
                say("221 bye")
                break
            else:
                say("250 ok")
        conn.close()

    def close(self) -> None:
        self.sock.close()


def test_the_real_mail_goes_to_every_recipient_with_the_message(tmp_path: Path) -> None:
    n = make(tmp_path)
    server = Smtp()
    try:
        n.update(
            {
                "email": {
                    "enabled": True,
                    "host": "127.0.0.1",
                    "port": server.port,
                    "security": "none",
                    "sender": "qm@example.com",
                    "to": "a@example.com, b@example.com",
                }
            }
        )
        n.run_finished(run_row(tmp_path, "failed", [entry("Search a worker", "failed")], "Scheduled: Nightly"))
        for _ in range(50):
            if server.mails:
                break
            time.sleep(0.05)
    finally:
        server.close()
    [mail] = server.mails
    assert "Subject: [Quartermaster] Acme Corp: 1 of 1 test failed" in mail
    assert "To: a@example.com, b@example.com" in mail and "Search a worker" in mail
    [log] = n.entries()
    assert log["ok"] and log["channel"] == "email" and "2 recipient" in log["message"]


def test_a_mail_server_that_cannot_be_reached_is_said_in_words(tmp_path: Path) -> None:
    n = make(tmp_path)
    n.update(
        {
            "email": {
                "host": "127.0.0.1",
                "port": 9,
                "security": "none",
                "sender": "qm@example.com",
                "to": "a@example.com",
            }
        }
    )
    out = n.send_test("email")
    assert not out["ok"] and "could not reach the mail server" in out["message"]


# ------------------------------------------------------------------ in the running service


def test_the_run_queue_tells_the_notifier_when_a_run_ends(tmp_path: Path) -> None:
    from quartermaster.service.runner import RunQueue
    from quartermaster.service.store import Store

    tests = tmp_path / "my_tests"
    tests.mkdir()
    (tests / "fail.yaml").write_text("id: x\n")
    n = make(tmp_path)
    sent = capture(n)
    enable_webhook(n, when="all")
    q = RunQueue(
        Store(tmp_path / "qm.db"),
        tests_root=tests,
        evidence_root=tmp_path / "evidence",
        work_dir=tmp_path / "work",
        command=fake_command,
    )
    q.on_finished = n.run_finished
    q.start()
    try:
        q.submit("fail.yaml")
        for _ in range(200):
            if sent:
                break
            time.sleep(0.1)
    finally:
        q.stop()
    assert len(sent) == 1 and "failed" in sent[0][1]["text"]


def test_a_notifier_that_breaks_never_stops_the_queue(tmp_path: Path) -> None:
    from quartermaster.service.runner import RunQueue
    from quartermaster.service.store import Store

    tests = tmp_path / "my_tests"
    tests.mkdir()
    (tests / "pass.yaml").write_text("id: x\n")
    seen: list[str] = []

    def broken(run: dict[str, Any]) -> None:
        seen.append(run["status"])
        raise RuntimeError("boom")

    q = RunQueue(
        Store(tmp_path / "qm.db"),
        tests_root=tests,
        evidence_root=tmp_path / "evidence",
        work_dir=tmp_path / "work",
        command=fake_command,
    )
    q.on_finished = broken
    q.start()
    try:
        first, second = q.submit("pass.yaml"), q.submit("pass.yaml")
        end = time.time() + 20
        while time.time() < end and len(seen) < 2:
            time.sleep(0.1)
    finally:
        q.stop()
    assert seen == ["passed", "passed"] and first["id"] != second["id"]


def test_the_page_reads_and_changes_the_settings_without_ever_seeing_a_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quartermaster.service.api import ApiError
    from quartermaster.service.hub import Hub

    for var in ("QM_FUSION_URL", "QM_FUSION_USER", "QM_FUSION_PASSWORD", "QM_FUSION_SESSION"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("QM_VAULT_KEY_FILE", str(tmp_path / "vault.key"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "my_tests").mkdir()
    hub = Hub(
        tests_root=tmp_path / "my_tests",
        evidence_root=tmp_path / "evidence",
        data_dir=tmp_path / ".qm",
        run_command=fake_command,
    )
    hub.start()
    try:

        def call(path: str, body: dict[str, Any] | None = None) -> Any:
            method = "POST" if body is not None else "GET"
            reply = hub.handle(method, path, json.dumps(body).encode() if body is not None else b"")
            return json.loads(reply.body)

        assert call("/api/notifications")["webhook"]["enabled"] is False
        saved = call(
            "/api/notifications", {"webhook": {"enabled": True, "kind": "teams", "url": SECRET_URL}, "when": "all"}
        )
        assert saved["webhook"]["url_set"] and saved["when"] == "all"
        assert "SECRETSECRET" not in json.dumps(call("/api/notifications"))
        audit = (tmp_path / ".qm" / "audit.jsonl").read_text(encoding="utf-8")
        assert "Changed notification settings" in audit and "SECRETSECRET" not in audit
        with pytest.raises(ApiError, match="https://"):
            hub.handle("POST", "/api/notifications", json.dumps({"webhook": {"url": "http://x.example.com"}}).encode())
        with pytest.raises(ApiError):
            hub.handle("GET", "/api/notifications/nothing", b"")
    finally:
        hub.stop()
