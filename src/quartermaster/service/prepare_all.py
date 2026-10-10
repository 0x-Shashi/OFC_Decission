"""Prepare all: let the AI prepare many manual scenarios, one after another, without anyone watching.

Each scenario is prepared exactly as with Prepare (one browser at a time). The results wait in
To review, where a person checks the pictures and approves the right ones. Nothing is approved
here.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from quartermaster.service.store import now


class PrepareAll:
    def __init__(self, start: Callable[[str], Any], stop_current: Callable[[], Any]):
        self._start = start  # prepares one scenario (raises ValueError when it cannot start)
        self._stop_current = stop_current
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"status": "idle", "items": []}

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state["status"] in ("running", "stopping")

    def begin(self, scenarios: list[dict[str, Any]]) -> dict[str, Any]:
        if not scenarios:
            raise ValueError(
                "there is nothing to prepare: every scenario plays by itself, waits for review or has test data missing"
            )
        with self._lock:
            if self._state["status"] in ("running", "stopping"):
                raise ValueError("Prepare all is already running")
            self._state = {
                "status": "running",
                "started_at": now(),
                "finished_at": None,
                "items": [
                    {"id": s["id"], "title": s.get("title", ""), "ref": s.get("ref", ""), "outcome": "waiting"}
                    for s in scenarios
                ],
            }
        self._next()
        return self.view()

    def stop(self) -> dict[str, Any]:
        """Stop after (and including) the scenario being prepared now; the rest are not started."""
        with self._lock:
            if self._state["status"] != "running":
                return self.view()
            self._state["status"] = "stopping"
            for item in self._state["items"]:
                if item["outcome"] == "waiting":
                    item["outcome"] = "not_started"
            busy = any(item["outcome"] == "preparing" for item in self._state["items"])
            if not busy:
                self._end()
        if busy:
            with suppress(ValueError):  # it has just ended on its own
                self._stop_current()
        return self.view()

    def finished(self, state: dict[str, Any]) -> None:
        """A session has ended (see Recording.on_finished): note its outcome, then start the next."""
        with self._lock:
            item = next((i for i in self._state["items"] if i["outcome"] == "preparing"), None)
            if item is None or state.get("mode") != "ai" or state.get("scenario_id") != item["id"]:
                return
            if state.get("status") == "saved" and state.get("automated"):
                item.update(outcome="prepared", run_id=state.get("run_id"))
            else:
                item.update(outcome="stopped", run_id=state.get("run_id"), why=state.get("message") or "")
        self._next()

    def view(self) -> dict[str, Any]:
        with self._lock:
            items = [dict(i) for i in self._state["items"]]
            out = {k: v for k, v in self._state.items() if k != "items"}
        counts: dict[str, int] = {}
        for i in items:
            counts[i["outcome"]] = counts.get(i["outcome"], 0) + 1
        return {**out, "items": items, "counts": counts, "total": len(items)}

    def _next(self) -> None:
        while True:
            with self._lock:
                if self._state["status"] != "running":
                    if self._state["status"] == "stopping":
                        self._end()
                    return
                item = next((i for i in self._state["items"] if i["outcome"] == "waiting"), None)
                if item is None:
                    self._end()
                    return
                item["outcome"] = "preparing"
            try:
                self._start(item["id"])
                return  # finished() is called when it ends
            except Exception as e:  # e.g. the AI settings changed: note it and go on with the next
                with self._lock:
                    item.update(outcome="stopped", why=f"It could not start: {e}")

    def _end(self) -> None:  # with the lock held
        self._state["status"] = "done"
        self._state["finished_at"] = now()
