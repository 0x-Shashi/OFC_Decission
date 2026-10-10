"""Ticket links: tie a failing test to the ticket that tracks it in Jira, ServiceNow, Azure DevOps or any tracker.

Quartermaster does not talk to the tracker, so it needs no login to it and sends nothing to it. It does three things:

- remembers which ticket numbers (or links) belong to which test, shown on the failure, on the test and in the
  certification pack, so nobody raises the same failure twice;
- writes a ready ticket text (title and description) from a failure, for the person to paste, or to open the tracker's
  "new ticket" page with it filled in when the tracker's address is known;
- turns a ticket number into a link with an address pattern the administrator sets once, such as
  https://example.atlassian.net/browse/{key}

The links are an append-only file (`links.jsonl`): adding and removing are both lines, so the history is kept and
the audit log has every change. Only http and https addresses are ever turned into links.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from quartermaster.service import atomic

REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._#-]{0,39}$")
_KEY_IN_URL = re.compile(r"[A-Z][A-Z0-9]+-\d+")
MAX_URL = 400
MAX_TEMPLATE = 400


class TicketError(ValueError):
    """The ticket, the link or a setting is not usable. The message is for the person at the page."""


class Tickets:
    def __init__(self, folder: Path, *, audit: Callable[[str, str, dict[str, Any]], None] | None = None):
        self._folder = folder
        self._audit = audit or (lambda what, subject, details: None)

    # ------------------------------------------------------------------ the tracker's addresses

    def settings(self) -> dict[str, str]:
        try:
            raw = json.loads((self._folder / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        return {k: str(raw.get(k) or "") for k in ("name", "link_template", "create_template")}

    def update_settings(self, data: dict[str, Any]) -> dict[str, str]:
        unknown = set(data) - {"name", "link_template", "create_template"}
        if unknown:
            raise TicketError(f"unknown settings: {', '.join(sorted(unknown))}")
        now = self.settings()
        new = {**now}
        if "name" in data:
            new["name"] = " ".join(str(data["name"] or "").split())[:40]
        if "link_template" in data:
            new["link_template"] = _template(str(data["link_template"] or ""), ("{key}",), "ticket address")
        if "create_template" in data:
            new["create_template"] = _template(
                str(data["create_template"] or ""), ("{title}", "{description}"), "new ticket address", any_of=True
            )
        atomic.write_text(self._folder / "settings.json", json.dumps(new, indent=2))
        self._audit("Changed the ticket tracker settings", "Tickets", {"changed": ", ".join(sorted(data))})
        return new

    def url_for(self, ref: str) -> str:
        """The link of a ticket number, from the address pattern; "" when none is set."""
        template = self.settings()["link_template"]
        return template.replace("{key}", quote(ref, safe="")) if template else ""

    # ------------------------------------------------------------------ the links

    def _events(self) -> list[dict[str, Any]]:
        try:
            lines = (self._folder / "links.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
        return out

    def links(self) -> dict[str, list[dict[str, Any]]]:
        """test id -> the tickets now linked to it, in the order they were added."""
        state: dict[str, dict[str, dict[str, Any]]] = {}
        for e in self._events():
            tests = state.setdefault(str(e.get("test_id")), {})
            if e.get("action") == "add":
                tests[str(e.get("ref"))] = {k: e.get(k) for k in ("ref", "url", "at", "who", "run_id")}
            elif e.get("action") == "remove":
                tests.pop(str(e.get("ref")), None)
        return {t: list(r.values()) for t, r in state.items() if r}

    def for_test(self, test_id: str) -> list[dict[str, Any]]:
        return self.links().get(test_id, [])

    def add(self, test_id: str, ref: str, run_id: str = "", who: str = "") -> list[dict[str, Any]]:
        test_id = str(test_id or "").strip()
        if not test_id:
            raise TicketError("choose the test")
        key, url = self.parse(ref)
        if any(t["ref"] == key for t in self.for_test(test_id)):
            raise TicketError(f"{key} is already linked to this test")
        self._write(
            {
                "at": _now(),
                "who": who,
                "test_id": test_id,
                "action": "add",
                "ref": key,
                "url": url,
                "run_id": str(run_id or "")[:40],
            }
        )
        self._audit("Linked a ticket", test_id, {"ticket": key})
        return self.for_test(test_id)

    def remove(self, test_id: str, ref: str, who: str = "") -> list[dict[str, Any]]:
        if not any(t["ref"] == ref for t in self.for_test(test_id)):
            raise TicketError("that ticket is not linked to this test")
        self._write({"at": _now(), "who": who, "test_id": test_id, "action": "remove", "ref": ref})
        self._audit("Removed a ticket link", test_id, {"ticket": ref})
        return self.for_test(test_id)

    def parse(self, text: str) -> tuple[str, str]:
        """(the ticket's number, its link or "") from what a person typed: a number such as PROJ-123, or a link."""
        text = " ".join(str(text or "").split())
        if not text:
            raise TicketError("type the ticket number or paste its link")
        if "://" in text:
            parts = urlsplit(text)
            if parts.scheme not in ("http", "https") or not parts.netloc or len(text) > MAX_URL:
                raise TicketError("a link must start with https:// (or http://) and be under 400 characters")
            found = _KEY_IN_URL.search(parts.path + "?" + parts.query)
            key = found.group(0) if found else (parts.path.rstrip("/").rsplit("/", 1)[-1] or parts.netloc)[:40]
            return key, text
        if not REF.match(text):
            raise TicketError("a ticket number uses letters, digits and . _ # - only, like PROJ-123")
        return text, self.url_for(text)

    def _write(self, event: dict[str, Any]) -> None:
        self._folder.mkdir(parents=True, exist_ok=True)
        with (self._folder / "links.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    # ------------------------------------------------------------------ a ticket text from a failure

    def draft(self, item: dict[str, Any]) -> dict[str, str]:
        """A title and description for a ticket about one failed test (an item of Needs attention), and the tracker's
        new-ticket address with them filled in when one is set. The person reads it before it goes anywhere."""
        title = f"{item.get('title') or item.get('test_id')}: " + (
            f'step {item["step"]} "{item.get("intent")}" failed' if item.get("step") else "failed"
        )
        lines = [
            f"Test: {item.get('title')} ({item.get('test_id')})",
            f"Failed at: step {item.get('step')}: {item.get('intent')}" if item.get("step") else "",
            f"What happened: {item.get('error')}" if item.get("error") else "",
        ]
        compare = item.get("compare") or {}
        if compare.get("expected") or compare.get("observed"):
            lines += [
                f"Expected: {compare.get('expected') or '(empty)'}",
                f"Observed: {compare.get('observed') or '(empty)'}",
            ]
        release = item.get("run_release") or item.get("current_release")
        if release:
            lines.append(
                f"Oracle release: {release}"
                + (f" (passed on {item['last_good_release']})" if item.get("last_good_release") else "")
            )
        cause = item.get("cause") or {}
        if cause.get("title"):
            lines.append(f"Likely cause: {cause['title']}")
        lines += [
            f"Run: {item.get('run_id')}" if item.get("run_id") else "",
            "",
            "The evidence document of this run shows every step with a picture. Attach it to the ticket.",
            "Check this text before sharing it: it may contain wording from the pod.",
        ]
        description = "\n".join(line for i, line in enumerate(lines) if line or (i and lines[i - 1]))
        template = self.settings()["create_template"]
        url = (
            template.replace("{title}", quote(title[:200], safe="")).replace(
                "{description}", quote(description[:1500], safe="")
            )
            if template
            else ""
        )
        return {"title": title, "description": description, "create_url": url, "tracker": self.settings()["name"]}


def _template(text: str, needs: tuple[str, ...], what: str, *, any_of: bool = False) -> str:
    text = text.strip()
    if not text:
        return ""
    if len(text) > MAX_TEMPLATE or urlsplit(text).scheme not in ("http", "https") or not urlsplit(text).netloc:
        raise TicketError(f"the {what} must start with https:// (or http://) and be under {MAX_TEMPLATE} characters")
    present = [n in text for n in needs]
    if not (any(present) if any_of else all(present)):
        raise TicketError(f"the {what} must contain {' or '.join(needs) if any_of else ' and '.join(needs)}")
    return text


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
