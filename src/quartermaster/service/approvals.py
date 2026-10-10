"""Approving a release: a named person says "this release is approved", and it is kept.

The certification pack used to end with a blank table to sign on paper. Now a person approves the
release in Quartermaster (Overview, the release's card), and the pack shows who, when and on what
results. Records are only ever added to a file (`approvals.jsonl`), never changed, so the history of
approvals and withdrawals stays whole. Every one is also in the audit log.

Quartermaster has no log-in of its own, so "who" is the name the approver types, together with the
user signed in to the computer. The pack says so.

Rules:
    - a release with no tests cannot be approved;
    - if tests failed or have not run, the approver must tick that they know and write why the
      release is approved anyway (at least 10 characters);
    - an approval stands until it is withdrawn; if tests are run again afterwards the approval is
      shown as "results changed since" and can be approved again;
    - a withdrawal needs a name and a reason.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from quartermaster.service.audit import AuditLog, computer_user

_RELEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,19}$")
MIN_REASON = 10


class ApprovalError(ValueError):
    """The approval cannot be recorded. The message says why, in words for the page."""


class Approvals:
    def __init__(self, path: Path, audit: AuditLog | None = None, who: Any = computer_user):
        self.path = path
        self._audit = audit
        self._who = who
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ reading

    def history(self, release: str | None = None) -> list[dict[str, Any]]:
        """Records, newest first (of one release, or all)."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and (release is None or rec.get("release") == release):
                out.append(rec)
        return out[::-1]

    def state(self, release: str, summary: dict[str, Any]) -> dict[str, Any]:
        """Where the release stands now. `summary` is what the results say now (see
        certification.summarize_rows)."""
        records = self.history(release)
        last = records[0] if records else None
        if last is None:
            return {"release": release, "status": "not_approved", "last": None, "summary": _public(summary)}
        status = "approved" if last["action"] == "approved" else "withdrawn"
        out: dict[str, Any] = {"release": release, "status": status, "last": _view(last), "summary": _public(summary)}
        if status == "approved":
            out["changes"] = _changes(last.get("results") or {}, summary)
        return out

    # ------------------------------------------------------------------ writing

    def approve(self, release: str, summary: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        release = _release(release)
        name, title, comment = _who(data)
        now = self.state(release, summary)
        if not summary["total"]:
            raise ApprovalError("this release has no tests, so there is nothing to approve")
        if now["status"] == "approved" and not now["changes"]["changed"]:
            by = now["last"]["by"]
            raise ApprovalError(f"release {release} is already approved by {by}. Withdraw that approval first.")
        open_items = summary["failed"] + summary["not_run"]
        if open_items:
            if not data.get("acknowledged"):
                raise ApprovalError(
                    f"{summary['failed']} test(s) failed and {summary['not_run']} have not run: tick the box to say "
                    "you know, and approve anyway"
                )
            if len(comment) < MIN_REASON:
                raise ApprovalError(
                    f"write why release {release} is approved with tests failed or not run "
                    f"(at least {MIN_REASON} letters)"
                )
        return self._add(
            release,
            "approved",
            name,
            title,
            comment,
            summary,
            acknowledged=bool(open_items),
            signed_in_as=str(data.get("signed_in_as") or ""),
        )

    def withdraw(self, release: str, summary: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        release = _release(release)
        name, title, reason = _who(data)
        if self.state(release, summary)["status"] != "approved":
            raise ApprovalError(f"release {release} is not approved, so there is nothing to withdraw")
        if len(reason) < 5:
            raise ApprovalError("write why the approval is withdrawn")
        return self._add(
            release,
            "withdrawn",
            name,
            title,
            reason,
            summary,
            acknowledged=False,
            signed_in_as=str(data.get("signed_in_as") or ""),
        )

    def _add(
        self,
        release: str,
        action: str,
        name: str,
        title: str,
        comment: str,
        summary: dict[str, Any],
        *,
        acknowledged: bool,
        signed_in_as: str = "",
    ) -> dict[str, Any]:
        record = {
            "id": uuid.uuid4().hex[:10],
            "release": release,
            "action": action,
            "by": name,
            "title": title,
            "comment": comment,
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "computer_user": str(self._who()),
            "signed_in_as": signed_in_as,
            "acknowledged_open_items": acknowledged,
            "results": {k: summary[k] for k in ("total", "passed", "failed", "not_run", "fingerprint", "by_test")},
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        if self._audit is not None:
            what = "Approved a release" if action == "approved" else "Withdrew the approval of a release"
            self._audit.add(
                what,
                release,
                {
                    "by": name,
                    "title": title,
                    "comment": comment,
                    "failed": summary["failed"],
                    "not run": summary["not_run"],
                },
                who=name,
            )
        return _view(record)


# ---------------------------------------------------------------------- helpers


def _release(value: str) -> str:
    release = str(value or "").strip()
    if not _RELEASE.match(release):
        raise ApprovalError("name the Oracle release, for example 26C")
    return release


def _who(data: dict[str, Any]) -> tuple[str, str, str]:
    name = " ".join(str(data.get("name") or "").split())
    if len(name) < 2:
        raise ApprovalError("type your name")
    if len(name) > 80:
        raise ApprovalError("the name is too long (80 letters at most)")
    title = " ".join(str(data.get("title") or "").split())[:80]
    comment = " ".join(str(data.get("comment") or "").split())
    if len(comment) > 1000:
        raise ApprovalError("the comment is too long (1000 letters at most)")
    return name, title, comment


def _public(summary: dict[str, Any]) -> dict[str, Any]:
    return {k: summary[k] for k in ("total", "passed", "failed", "not_run")}


def _view(record: dict[str, Any]) -> dict[str, Any]:
    results = record.get("results") or {}
    return {
        **{
            k: record.get(k)
            for k in ("id", "release", "action", "by", "title", "comment", "at", "computer_user", "signed_in_as")
        },
        "acknowledged_open_items": bool(record.get("acknowledged_open_items")),
        "results": {k: results.get(k) for k in ("total", "passed", "failed", "not_run", "fingerprint")},
    }


def _changes(approved: dict[str, Any], now: dict[str, Any]) -> dict[str, Any]:
    """How the results differ from those the release was approved on."""
    before: dict[str, str] = approved.get("by_test") or {}
    after: dict[str, str] = now.get("by_test") or {}
    changed = [t for t in set(before) | set(after) if before.get(t) != after.get(t)]
    newly_failing = [
        t for t in changed if after.get(t, "").endswith(":failed") and not before.get(t, "").endswith(":failed")
    ]
    return {
        "changed": bool(changed) and approved.get("fingerprint") != now.get("fingerprint"),
        "tests": len(changed),
        "newly_failing": len(newly_failing),
    }
