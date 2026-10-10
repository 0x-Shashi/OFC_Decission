"""The audit log: who did what in Quartermaster, and when.

Each line also carries two hashes, so a change to the log shows: `prev` is the hash of the line before it and `hash`
covers `prev` and the line's own content (SHA-256). Editing, removing, adding or reordering a line breaks the chain from
that line on, and `verify` says where. Lines written before this existed have no hash; the first hashed line starts from
a hash of everything before it, so those are covered too. The chain alone cannot show that the newest lines were cut
off: an export records the newest hash and the count, and a later check can be held against them.

Every change made through the web service is added as one line to `.qm/audit.jsonl`: runs started
and cancelled, approvals, accepted screen changes, test data saved, imports, schedules, settings
and sign-ins. Lines are only ever added. Secrets never go in: not the AI key, not the sign-in done
by hand, not the test data values (only which steps were changed).

"Who" is the person named in the request when there is one (the tester of a run by hand), else the
user signed in to this computer, since Quartermaster has no log-in of its own.
"""

from __future__ import annotations

import csv
import getpass
import hashlib
import io
import json
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_SHOWN = 1000
START = "start"  # what the first line of a new log is chained to
_LOCKS: dict[str, threading.Lock] = {}  # one lock for each file, whoever opens it in this process
_LOCKS_GUARD = threading.Lock()

Described = tuple[str, str, dict[str, Any]]  # what was done, to what, details


def computer_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no user name on this computer (some containers)
        return "unknown"


def digest(prev: str, entry: dict[str, Any]) -> str:
    """The hash of a line: the previous hash and the line's own content, written the same way every time."""
    body = {k: v for k, v in entry.items() if k not in ("prev", "hash")}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{prev}\n{text}".encode()).hexdigest()


class AuditLog:
    def __init__(self, path: Path, who: Callable[[], str] = computer_user):
        self.path = path
        self._who = who
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(path.resolve()), threading.Lock())

    def add(self, action: str, subject: str, details: dict[str, Any] | None = None, who: str = "") -> None:
        entry: dict[str, Any] = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "who": who or self._who(),
            "action": action,
            "subject": subject,
            "details": {k: v for k, v in (details or {}).items() if v not in (None, "", [], {})},
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            entry["prev"] = self._head()
            entry["hash"] = digest(entry["prev"], entry)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(("\n" if self._open_line() else "") + json.dumps(entry, ensure_ascii=False) + "\n")

    def _open_line(self) -> bool:
        """Whether the file ends in the middle of a line (the computer stopped while writing)."""
        try:
            with self.path.open("rb") as f:
                f.seek(0, 2)
                if f.tell() == 0:
                    return False
                f.seek(-1, 2)
                return f.read(1) != b"\n"
        except OSError:
            return False

    def _head(self) -> str:
        """The hash the next line is chained to."""
        try:
            raw = self.path.read_bytes()
        except OSError:
            return START
        if not raw.strip():
            return START
        for line in reversed(raw.splitlines()):
            try:
                last = json.loads(line)
            except ValueError:
                continue
            if isinstance(last, dict) and isinstance(last.get("hash"), str):
                return str(last["hash"])
            break
        whole = raw if raw.endswith(b"\n") else raw + b"\n"  # the newline the next write adds if one is missing
        return hashlib.sha256(whole).hexdigest()  # the lines before the chain began: all of them are covered

    def entries(self, limit: int = MAX_SHOWN) -> list[dict[str, Any]]:
        """Newest first."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except ValueError:
                continue  # a line cut short when the computer stopped
            if isinstance(item, dict):
                out.append(item)
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------ checking and picking

    def verify(self) -> dict[str, Any]:
        """Walk the whole log and say whether the chain is whole. `ok` is False at the first line that was changed,
        removed, added or moved; `problem` says which line and why. Lines cut short by a stopped computer are skipped
        (they were never part of the chain)."""
        try:
            raw = self.path.read_bytes()
        except OSError:
            raw = b""
        return check_lines(raw)

    def holds(self, head: str, entries: int) -> str:
        """ "" when the log still has the line with hash `head` and at least `entries` lines, else why not. For checking
        the log against the manifest of an earlier export: the chain cannot show that its newest lines were cut off."""
        try:
            raw = self.path.read_bytes()
        except OSError:
            return "the audit log cannot be read"
        found = False
        count = 0
        for line in raw.splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                count += 1
                found = found or e.get("hash") == head
        if not found:
            return "the newest line of that export is not in the log any more"
        if count < entries:
            return f"the log has {count} lines, the export said it had {entries}"
        return ""

    def select(
        self, since: str = "", until: str = "", who: str = "", text: str = "", action: str = ""
    ) -> list[dict[str, Any]]:
        """Entries oldest first, from the date `since` to `until` (inclusive, YYYY-MM-DD), by `who` (any part of the
        name), with `action` in what was done, and `text` anywhere in the line."""
        out = []
        for e in reversed(self.entries(limit=10**9)):
            day = str(e.get("at", ""))[:10]
            if (since and day < since) or (until and day > until):
                continue
            if who and who.casefold() not in str(e.get("who", "")).casefold():
                continue
            if action and action.casefold() not in str(e.get("action", "")).casefold():
                continue
            if text and text.casefold() not in entry_text(e).casefold():
                continue
            out.append(e)
        return out

    def as_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["When", "Who", "What", "Subject", "Details"])
        for e in reversed(self.entries(limit=10**9)):  # oldest first, like a ledger
            details = "; ".join(f"{k}: {_text(v)}" for k, v in (e.get("details") or {}).items())
            w.writerow([e.get("at", ""), e.get("who", ""), e.get("action", ""), e.get("subject", ""), details])
        return buf.getvalue()


def entry_text(e: dict[str, Any]) -> str:
    """All the words of an entry, for searching."""
    details = " ".join(f"{k} {_text(v)}" for k, v in (e.get("details") or {}).items())
    return f"{e.get('who', '')} {e.get('action', '')} {e.get('subject', '')} {details}"


def check_lines(raw: bytes) -> dict[str, Any]:
    """Check the chain of a log (or of a complete export) given as bytes."""
    out: dict[str, Any] = {"ok": True, "entries": 0, "hashed": 0, "legacy": 0, "head": None, "problem": None}
    lines = raw.splitlines(keepends=True)
    prev: str | None = None  # the hash the next hashed line must name
    seen_chain = False
    offset = 0
    first_at = last_at = None
    for number, line in enumerate(lines, 1):
        start, offset = offset, offset + len(line)
        try:
            e = json.loads(line)
        except ValueError:
            continue  # cut short: not part of the chain
        if not isinstance(e, dict):
            continue
        out["entries"] += 1
        first_at = first_at or e.get("at")
        last_at = e.get("at") or last_at
        if "hash" not in e:
            if seen_chain:
                return _broken(out, number, "a line without a hash was added after the chain began")
            out["legacy"] += 1
            continue
        out["hashed"] += 1
        if not seen_chain:
            seen_chain = True
            anchor = hashlib.sha256(raw[:start]).hexdigest() if start else START
            if e.get("prev") != anchor:
                what = (
                    "the lines before the chain began were changed"
                    if start
                    else "the first line does not start the log"
                )
                return _broken(out, number, what)
        elif e.get("prev") != prev:
            return _broken(out, number, "the line before it was changed, removed or moved")
        if digest(str(e.get("prev")), e) != e.get("hash"):
            return _broken(out, number, "this line was changed")
        prev = str(e["hash"])
    out.update(head=prev, first_at=first_at, last_at=last_at)
    return out


def check_export(raw: bytes) -> dict[str, Any]:
    """Check an exported .jsonl file on its own. Every hashed line must be what its hash says. Lines that follow each
    other must chain; a gap is normal for an export that was filtered, and is counted, not blamed."""
    out: dict[str, Any] = {"ok": True, "entries": 0, "hashed": 0, "legacy": 0, "gaps": 0, "head": None, "problem": None}
    prev: str | None = None
    for number, line in enumerate(raw.splitlines(), 1):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        out["entries"] += 1
        if "hash" not in e:
            out["legacy"] += 1
            prev = None
            continue
        out["hashed"] += 1
        if digest(str(e.get("prev")), e) != e.get("hash"):
            return _broken(out, number, "this line was changed")
        if prev is not None and e.get("prev") != prev:
            out["gaps"] += 1  # lines are missing between these two (a filtered export, or lines removed)
        prev = str(e["hash"])
    out["head"] = prev
    out["complete"] = out["gaps"] == 0
    return out


def _broken(out: dict[str, Any], line: int, why: str) -> dict[str, Any]:
    out.update(ok=False, problem={"line": line, "why": why})
    return out


def _target(target: Any) -> str:
    return "All tests" if str(target or ".") == "." else str(target)


def _text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def describe(route: list[str], data: dict[str, Any], reply: Any) -> Described | None:
    """How a successful POST to /api/<route> reads in the log, or None when it changes nothing."""
    reply = reply if isinstance(reply, dict) else {}
    key = "/".join(route)
    given = data.get("options")
    options: dict[str, Any] = given if isinstance(given, dict) else {}
    if key == "settings":
        return "Changed settings", "Settings", {k: v for k, v in data.items() if isinstance(v, str | int | bool)}
    if key == "notifications":  # never the webhook address or the password: only which settings changed
        return "Changed notification settings", "Notifications", {"changed": ", ".join(sorted(data))}
    if key == "notifications/test":
        return "Sent a test notification", str(data.get("channel") or ""), {"result": reply.get("message")}
    if key == "ai/key":
        return ("Entered the AI key" if str(data.get("key") or "").strip() else "Removed the AI key"), "AI", {}
    if key == "environments/client":
        return ("Changed a client" if data.get("id") else "Added a client"), str(reply.get("name") or ""), {}
    if key == "environments/client/delete":
        return "Deleted a client", str(data.get("id") or ""), {}
    if key == "environments/environment":
        return (
            ("Changed an environment" if data.get("id") else "Added an environment"),
            str(reply.get("name") or ""),
            {"pod": reply.get("url"), "kind": data.get("kind"), "release": data.get("release")},
        )
    if key == "environments/environment/delete":
        return "Deleted an environment", str(data.get("id") or ""), {}
    if key == "environments/user":  # never the password: only whether a new one was typed
        return (
            "Saved a test user",
            str(reply.get("username") or ""),
            {"persona": reply.get("persona") or "default", "new password": "yes" if data.get("password") else "no"},
        )
    if key == "environments/user/delete":
        return "Removed a test user", str(data.get("persona") or "default"), {}
    if key == "environments/activate":
        return "Switched environment", f"{reply.get('client', '')} · {reply.get('name', '')}", {}
    if key == "attention/dismiss":
        return "Dismissed from Needs attention", f"{reply.get('dismissed', 0)} item(s)", {}
    if key == "signin":
        return "Opened a browser to sign in by hand", "Sign-in", {}
    if key == "signin/forget":
        return "Forgot the sign-in done by hand", "Sign-in", {}
    if key == "test/accept-update":
        return (
            "Accepted an Oracle screen change",
            str(data.get("file") or ""),
            {
                "step": int(data.get("step_index", -1)) + 1,
                "now finds it by": " ".join(str(x) for x in data.get("new") or []),
            },
        )
    if key == "runs":
        return (
            "Started a run",
            _target(data.get("target")),
            {
                "run": reply.get("id"),
                "release": (reply.get("options") or {}).get("release"),
                "label": options.get("label"),
            },
        )
    if len(route) == 3 and route[0] == "runs" and route[2] == "cancel":
        return "Cancelled a run", route[1], {}
    if key == "recording":
        return (
            "Started recording a test",
            str(data.get("title") or data.get("out") or ""),
            {"module": data.get("module")},
        )
    if key == "releases/import" and data.get("save"):
        return "Imported a release feature list", str(data.get("name") or ""), {}
    if key == "schedules":
        return (
            ("Changed a schedule" if data.get("id") else "Added a schedule"),
            str(reply.get("name") or ""),
            {
                "what": _target(reply.get("target")),
                "when": reply.get("when"),
                "on": "yes" if reply.get("enabled") else "no",
            },
        )
    if key == "schedules/delete":
        return "Deleted a schedule", str(data.get("id") or ""), {}
    if key == "schedules/run":
        return "Started a schedule now", str(data.get("id") or ""), {"run": reply.get("id")}
    if key == "manual/data":
        raw = data.get("values")
        values: dict[str, Any] = raw if isinstance(raw, dict) else {}
        return (
            "Saved test data",
            str(data.get("id") or ""),
            {"steps": sorted(values, key=lambda n: int(n) if str(n).isdigit() else 0)},
        )
    if key == "manual/run":
        how = "Prepare (AI)" if data.get("prepare") else "by hand" if data.get("by_hand") else "by itself"
        return "Ran a manual scenario", str(data.get("id") or ""), {"how": how, "tester": data.get("tester")}
    if key == "manual/approve":
        listed = data.get("ids")
        ids: list[Any] = listed if isinstance(listed, list) else [data.get("id")]
        return "Approved what the AI prepared", ", ".join(str(i) for i in ids), {}
    if key == "manual/prepare-all":
        return "Started Prepare all", "Manual scenarios", {"scenarios": reply.get("total")}
    if key == "manual/prepare-all/stop":
        return "Stopped Prepare all", "Manual scenarios", {}
    if key == "manual/import" and data.get("save"):
        files = [r.get("file") for r in reply.get("files") or [] if isinstance(r, dict) and r.get("saved")]
        return "Imported manual scripts", ", ".join(str(f) for f in files), {}
    if key == "manual/remove":
        return "Removed imported manual scripts", str(data.get("key") or ""), {}
    if key == "manual/typed":
        return (
            ("Changed a typed scenario" if data.get("id") else "Typed a new scenario"),
            str(reply.get("id") or ""),
            {
                "title": reply.get("title"),
                "steps": reply.get("step_count"),
            },
        )
    if key == "manual/typed/delete":
        return "Deleted a typed scenario", str(data.get("id") or ""), {}
    return None
