"""Tell people when a run fails, so nobody has to open the page to find out.

Two ways, both set up in Settings, Notifications, for the client in use (every client has its own
settings, like its tests and runs):

    webhook   a Slack, Microsoft Teams or other service address that takes a message by POST
    email     an SMTP mail server, with the people to write to

When to send: for scheduled runs only (the ones nobody watches) or for every run; and only when
something failed, or after every finished run. A run someone stopped never sends anything.

What is in a message: the client, the run, how many tests passed, and for each failed test its name,
the failed step and the kind of problem. Never passwords or test data. Text the pod itself showed
(what a check expected and found) can hold personal data, so it is left out unless "Include what
went wrong" is switched on.

The webhook address (it works like a password: anyone with it can post to the channel) and the mail
password are saved encrypted, like the pod passwords (see vault.py), and are never sent back to the
web page. Sending runs in the background and never stops a run; every attempt, good or bad, is
listed in Settings and the audit log, so a broken channel is noticed.
"""

from __future__ import annotations

import base64
import json
import re
import smtplib
import ssl
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from quartermaster.service import insights, vault
from quartermaster.service.audit import AuditLog

WHEN = ("scheduled", "all")
WHAT = ("failures", "every")
KINDS = ("slack", "teams", "json")
SECURITY = ("starttls", "ssl", "none")
MAX_FAILED_SHOWN = 8
KEEP_LOG = 30
TIMEOUT_S = 20
_ADDRESS = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")

DEFAULTS: dict[str, Any] = {
    "when": "scheduled",
    "what": "failures",
    "details": False,
    "webhook": {"enabled": False, "kind": "slack", "url": ""},
    "email": {
        "enabled": False,
        "host": "",
        "port": 587,
        "security": "starttls",
        "username": "",
        "password": "",
        "sender": "",
        "to": [],
    },
}


class NotifyError(ValueError):
    """A setting is not usable, or a message could not be sent. The text is for people and holds no secret."""


class Notifier:
    def __init__(
        self,
        folder: Path,
        *,
        client: Callable[[], str] = lambda: "",
        address: Callable[[], str] = lambda: "",
        audit: AuditLog | None = None,
        key_file: Path | None = None,
        background: bool = True,
    ):
        self.path = folder / "notifications.json"
        self.log_path = folder / "notifications.log.jsonl"
        self._client, self._address, self._audit = client, address, audit
        self._key_file = key_file
        self._background = background
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ settings

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        out: dict[str, Any] = json.loads(json.dumps(DEFAULTS))
        for key in ("when", "what", "details"):
            if key in raw:
                out[key] = raw[key]
        for part in ("webhook", "email"):
            if isinstance(raw.get(part), dict):
                out[part].update({k: v for k, v in raw[part].items() if k in out[part]})
        return out

    def view(self) -> dict[str, Any]:
        """The settings for the page: never the webhook address or the password, only whether they are set."""
        cfg = self._load()
        hook, mail = cfg["webhook"], cfg["email"]
        return {
            "when": cfg["when"],
            "what": cfg["what"],
            "details": bool(cfg["details"]),
            "client": self._client(),
            "webhook": {
                "enabled": bool(hook["enabled"]),
                "kind": hook["kind"],
                "url_set": bool(hook["url"]),
                "host": (urlsplit(self._reveal(hook["url"])).hostname or "") if hook["url"] else "",
            },
            "email": {
                "enabled": bool(mail["enabled"]),
                "host": mail["host"],
                "port": mail["port"],
                "security": mail["security"],
                "username": mail["username"],
                "password_set": bool(mail["password"]),
                "sender": mail["sender"],
                "to": list(mail["to"]),
            },
            "log": self.entries(),
        }

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Change some settings. A password or address left out (or None) is kept; "" removes it."""
        unknown = set(changes) - {"when", "what", "details", "webhook", "email"}
        if unknown:
            raise NotifyError(f"unknown settings: {', '.join(sorted(unknown))}")
        cfg = self._load()
        if "when" in changes:
            cfg["when"] = _choice(changes["when"], WHEN, "which runs")
        if "what" in changes:
            cfg["what"] = _choice(changes["what"], WHAT, "what to send")
        if "details" in changes:
            cfg["details"] = bool(changes["details"])
        if "webhook" in changes:
            self._update_webhook(cfg["webhook"], _part(changes["webhook"], "webhook"))
        if "email" in changes:
            self._update_email(cfg["email"], _part(changes["email"], "email"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return self.view()

    def _update_webhook(self, hook: dict[str, Any], new: dict[str, Any]) -> None:
        if "kind" in new:
            hook["kind"] = _choice(new["kind"], KINDS, "the kind of webhook")
        url = new.get("url")
        if url is not None:
            url = str(url).strip()
            if url:
                parts = urlsplit(url)
                if parts.scheme != "https" or not parts.hostname or len(url) > 1000:
                    raise NotifyError("the webhook address must start with https://")
            hook["url"] = self._protect(url) if url else ""
        if "enabled" in new:
            hook["enabled"] = bool(new["enabled"])
        if hook["enabled"] and not hook["url"]:
            raise NotifyError("paste the webhook address first")

    def _update_email(self, mail: dict[str, Any], new: dict[str, Any]) -> None:
        for key, limit in (("host", 253), ("username", 200), ("sender", 200)):
            if key in new:
                mail[key] = " ".join(str(new[key] or "").split())[:limit]
        if "port" in new:
            try:
                port = int(new["port"])
            except (TypeError, ValueError):
                port = 0
            if not 1 <= port <= 65535:
                raise NotifyError("the mail server's port is a number from 1 to 65535 (587 is usual)")
            mail["port"] = port
        if "security" in new:
            mail["security"] = _choice(new["security"], SECURITY, "the connection security")
        if "to" in new:
            raw = new["to"]
            parts = re.split(r"[,;\s]+", raw) if isinstance(raw, str) else raw
            to = [str(a).strip() for a in (parts if isinstance(parts, list) else []) if str(a).strip()]
            bad = [a for a in to if not _ADDRESS.match(a)]
            if bad or len(to) > 20:
                raise NotifyError(f"not an e-mail address: {bad[0]}" if bad else "at most 20 e-mail addresses")
            mail["to"] = to
        password = new.get("password")
        if password is not None:
            mail["password"] = self._protect(str(password)) if str(password) else ""
        if mail["host"] and not _HOST.match(mail["host"]):
            raise NotifyError("the mail server's name may use letters, digits, dots and dashes")
        if mail["sender"] and not _ADDRESS.match(mail["sender"]):
            raise NotifyError(f"not an e-mail address: {mail['sender']}")
        if "enabled" in new:
            mail["enabled"] = bool(new["enabled"])
        if mail["enabled"] and not (mail["host"] and mail["sender"] and mail["to"]):
            raise NotifyError("fill in the mail server, the sender and at least one recipient first")

    def _protect(self, text: str) -> str:
        return base64.b64encode(vault.protect(text, self._key_file)).decode("ascii")

    def _reveal(self, stored: str) -> str:
        try:
            return vault.reveal(base64.b64decode(stored), self._key_file)
        except (vault.VaultError, ValueError):
            return ""

    # ------------------------------------------------------------------ a run has ended

    def run_finished(self, run: dict[str, Any]) -> None:
        """Called when a run ends. Sends what the settings ask for, in the background; never raises."""
        try:
            message = self.compose(run)
        except Exception:  # a message that cannot be made must never disturb the run queue
            return
        if message is None:
            return
        if self._background:
            threading.Thread(target=self._send_all, args=(message, str(run.get("id"))), daemon=True).start()
        else:
            self._send_all(message, str(run.get("id")))

    def compose(self, run: dict[str, Any]) -> dict[str, Any] | None:
        """The message for this run, or None when the settings say it is not worth one."""
        cfg = self._load()
        if not (cfg["webhook"]["enabled"] or cfg["email"]["enabled"]):
            return None
        status = str(run.get("status"))
        if status == "cancelled" or status not in ("passed", "failed", "error"):
            return None  # someone stopped it, or it is not over
        label = str((run.get("options") or {}).get("label") or "")
        if cfg["when"] == "scheduled" and not label.startswith("Scheduled:"):
            return None
        if status == "passed" and cfg["what"] == "failures":
            return None
        return build_message(
            run,
            client=self._client(),
            address=self._address(),
            details=bool(cfg["details"]),
        )

    # ------------------------------------------------------------------ sending

    def send_test(self, channel: str) -> dict[str, Any]:
        """A test message to one channel, now. Returns {"ok", "message"}."""
        if channel not in ("webhook", "email"):
            raise NotifyError("choose webhook or email")
        client = self._client()
        message = {
            "title": "Test message from Quartermaster",
            "status": "test",
            "client": client,
            "lines": [f"This is a test{' for ' + client if client else ''}. If you can read it, notifications work."],
            "facts": {},
            "failed": [],
            "link": self._address(),
        }
        return self._send_one(channel, message, "test", ignore_enabled=True)

    def _send_all(self, message: dict[str, Any], run_id: str) -> None:
        cfg = self._load()
        for channel in ("webhook", "email"):
            if cfg[channel]["enabled"]:
                self._send_one(channel, message, run_id)

    def _send_one(
        self, channel: str, message: dict[str, Any], run_id: str, *, ignore_enabled: bool = False
    ) -> dict[str, Any]:
        cfg = self._load()
        try:
            if channel == "webhook":
                self._post(cfg["webhook"], message)
                said = "Sent to the webhook."
            else:
                self._mail(cfg["email"], message)
                said = f"Sent to {len(cfg['email']['to'])} recipient(s)."
            outcome = {"ok": True, "message": said}
        except NotifyError as e:
            outcome = {"ok": False, "message": str(e)}
        except Exception as e:  # network, mail server, anything: say it, do not stop
            outcome = {"ok": False, "message": f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"[:200]}
        self._record(channel, run_id, outcome)
        return outcome

    def _post(self, hook: dict[str, Any], message: dict[str, Any]) -> None:
        url = self._reveal(hook["url"])
        if not url:
            raise NotifyError("no webhook address is saved")
        self._send_json(url, json.dumps(webhook_payload(hook["kind"], message)).encode("utf-8"))

    def _send_json(self, url: str, body: bytes) -> None:
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:  # noqa: S310 - the https address the user set
                if not 200 <= res.status < 300:
                    raise NotifyError(f"the webhook answered HTTP {res.status}")
        except urllib.error.HTTPError as e:
            raise NotifyError(f"the webhook answered HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise NotifyError(f"could not reach the webhook: {e.reason}") from None

    def _mail(self, mail: dict[str, Any], message: dict[str, Any]) -> None:
        if not (mail["host"] and mail["sender"] and mail["to"]):
            raise NotifyError("the mail server, the sender and the recipients are not filled in")
        msg = EmailMessage()
        msg["Subject"] = f"[Quartermaster] {message['client'] + ': ' if message['client'] else ''}{message['title']}"
        msg["From"], msg["To"] = mail["sender"], ", ".join(mail["to"])
        msg.set_content(plain_text(message))
        secure = mail["security"]
        try:
            if secure == "ssl":
                server: smtplib.SMTP = smtplib.SMTP_SSL(
                    mail["host"], int(mail["port"]), timeout=TIMEOUT_S, context=ssl.create_default_context()
                )
            else:
                server = smtplib.SMTP(mail["host"], int(mail["port"]), timeout=TIMEOUT_S)
            with server:
                if secure == "starttls":
                    server.starttls(context=ssl.create_default_context())
                if mail["username"]:
                    server.login(mail["username"], self._reveal(mail["password"]))
                server.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            raise NotifyError("the mail server refused the user name or password") from None
        except smtplib.SMTPException as e:
            raise NotifyError(f"the mail server said: {str(e)[:150]}") from None
        except OSError as e:
            raise NotifyError(f"could not reach the mail server: {e}") from None

    # ------------------------------------------------------------------ what was tried

    def _record(self, channel: str, run_id: str, outcome: dict[str, Any]) -> None:
        entry = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "channel": channel,
            "run": run_id,
            "ok": bool(outcome["ok"]),
            "message": str(outcome["message"])[:200],
        }
        with self._lock:
            lines = self._lines()[-(KEEP_LOG - 1) :] + [json.dumps(entry)]
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if self._audit is not None and run_id != "test":
            what = "Sent a notification" if entry["ok"] else "A notification could not be sent"
            self._audit.add(what, channel, {"run": run_id, "result": entry["message"]}, who="Quartermaster")

    def _lines(self) -> list[str]:
        try:
            return [ln for ln in self.log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return []

    def entries(self, limit: int = 10) -> list[dict[str, Any]]:
        """Newest first."""
        out = []
        for line in reversed(self._lines()[-limit:]):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


# ---------------------------------------------------------------------- the message


def build_message(run: dict[str, Any], *, client: str, address: str, details: bool) -> dict[str, Any]:
    """What to say about a finished run. Plain data: the channels format it."""
    suite = insights.suite_of(run) or {}
    entries: list[dict[str, Any]] = suite.get("runs", [])
    release = str(suite.get("release") or (run.get("options") or {}).get("release") or "")
    name = str((run.get("options") or {}).get("label") or run.get("target") or "")
    host = urlsplit(str(suite.get("environment_url") or "")).hostname or ""
    status = str(run["status"])
    failed = [e for e in entries if e.get("status") == "failed"]
    flaky = [e for e in entries if e.get("flaky") and e.get("status") != "failed"]
    lines: list[str] = []
    if status == "error":
        title = "A run could not finish"
        lines.append(insights.plain_run_error(run.get("error")) or "The run stopped before it could finish.")
    elif status == "failed" and not failed:  # the run's record could not be read: still say it failed
        title = "A run failed"
        lines.append("At least one test failed. Open the run in Quartermaster to see which.")
    elif failed:
        n = len(failed)
        title = f"{n} of {len(entries)} test{'s' if len(entries) != 1 else ''} failed"
        lines.append(f"{len(entries) - n} passed, {n} failed.")
    elif entries:
        title = f"All {len(entries)} test{'s' if len(entries) != 1 else ''} passed"
        lines.append(title + ".")
    else:
        title = "A run passed"
        lines.append("The run passed.")
    failed_out = []
    for e in failed[:MAX_FAILED_SHOWN]:
        step = e.get("failed_step") or {}
        kind = insights.CATEGORIES.get(insights.classify(step.get("error")), "Failed")
        row = {
            "test": str(e.get("test_title") or e.get("test_id") or ""),
            "step": f"step {step.get('number')}: {step.get('intent')}" if step.get("number") else "",
            "kind": kind,
        }
        if details and step.get("error"):
            from quartermaster.evidence.document import plain_error

            row["what"] = plain_error(step.get("error"))
        failed_out.append(row)
        lines.append(" - " + row["test"] + (f" ({row['step']}; {kind.lower()})" if row["step"] else ""))
        if row.get("what"):
            lines.append("     " + row["what"])
    if len(failed) > MAX_FAILED_SHOWN:
        lines.append(f" - and {len(failed) - MAX_FAILED_SHOWN} more")
    if flaky:
        lines.append(f"{len(flaky)} test{'s' if len(flaky) != 1 else ''} passed only after a step was tried again.")
    facts = {"Run": name, "Release": release, "Environment": host, "Run id": str(run.get("id") or "")}
    link = f"{address.rstrip('/')}/#/runs/{run.get('id')}" if address else ""
    return {
        "title": title,
        "status": status,
        "client": client,
        "lines": lines,
        "facts": {k: v for k, v in facts.items() if v},
        "failed": failed_out,
        "link": link,
        "tests": {"total": len(entries), "failed": len(failed), "passed": len(entries) - len(failed)},
    }


def plain_text(message: dict[str, Any]) -> str:
    head = f"{message['client']}: {message['title']}" if message["client"] else message["title"]
    out = [head, "", *message["lines"], ""]
    out += [f"{k}: {v}" for k, v in message["facts"].items()]
    if message.get("link"):
        out += ["", f"Open it in Quartermaster (on the computer that runs it): {message['link']}"]
    return "\n".join(out).strip() + "\n"


def webhook_payload(kind: str, message: dict[str, Any]) -> dict[str, Any]:
    """The message in the shape each service wants."""
    head = f"{message['client']}: {message['title']}" if message["client"] else message["title"]
    body = "\n".join([*message["lines"], *[f"{k}: {v}" for k, v in message["facts"].items()]])
    if message.get("link"):
        body += f"\n{message['link']}"
    if kind == "slack":
        return {"text": f"*{head}*\n{body}"}
    if kind == "teams":  # a Workflows webhook ("when a Teams webhook request is received"): an Adaptive Card
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {"type": "TextBlock", "text": head, "weight": "Bolder", "size": "Medium", "wrap": True},
                            {"type": "TextBlock", "text": body, "wrap": True},
                        ],
                    },
                }
            ],
        }
    return {  # any other service: plain fields to pick from
        "title": message["title"],
        "client": message["client"],
        "status": message["status"],
        "text": f"{head}\n{body}",
        "tests": message.get("tests"),
        "failed": message["failed"],
        "facts": message["facts"],
        "link": message.get("link", ""),
    }


def _choice(value: Any, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise NotifyError(f"{what} must be one of: {', '.join(allowed)}")
    return str(value)


def _part(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NotifyError(f"{name} settings must be an object")
    unknown = set(value) - set(DEFAULTS[name])
    if unknown:
        raise NotifyError(f"unknown {name} settings: {', '.join(sorted(unknown))}")
    return value
