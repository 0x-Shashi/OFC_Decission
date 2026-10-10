"""AI quality check from Settings: ask the chosen AI the golden questions and keep the scores (ai/evals/suggest.py).

It runs in the background (about as many short questions as there are cases, a minute or so) and the last scores are
kept per model, so two models can be compared before one is trusted. Only invented screens are sent to the AI: nothing
from the pod, nothing from the tests.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from quartermaster.ai import providers as ai_providers
from quartermaster.ai.evals import suggest as evals
from quartermaster.ai.providers import AIConfig
from quartermaster.service import atomic

KEEP = 12  # scores kept


class AiEval:
    def __init__(
        self,
        path: Path,
        *,
        cases: list[evals.Case] | None = None,
        audit: Callable[[str, str, dict[str, Any]], None] | None = None,
    ):
        self._path = path
        self._cases = cases
        self._audit = audit or (lambda what, subject, details: None)
        self._lock = threading.Lock()
        self._running = False
        self._progress = (0, 0)
        self._error = ""

    def history(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []

    def view(self) -> dict[str, Any]:
        with self._lock:
            running, progress, error = self._running, self._progress, self._error
        rows = self.history()
        try:
            questions = len(self._cases if self._cases is not None else evals.load_cases())
        except evals.CaseError:
            questions = 0
        return {
            "questions": questions,
            "running": running,
            "progress": {"done": progress[0], "total": progress[1]},
            "error": error,
            "last": rows[0] if rows else None,
            "history": [{k: r.get(k) for k in ("at", "label", "score", "verdict", "asked", "counts")} for r in rows],
        }

    def start(self, config: AIConfig, ask: evals.Ask | None = None) -> dict[str, Any]:
        problem = config.problem()
        if problem and ask is None:
            raise ValueError(problem)
        with self._lock:
            if self._running:
                raise ValueError("the quality check is already running")
            self._running, self._error, self._progress = True, "", (0, 0)
        question = ask or (lambda system, prompt: ai_providers.chat(config, system, prompt, max_tokens=300, timeout=45))
        threading.Thread(target=self._work, args=(config.label, question), name="qm-ai-eval", daemon=True).start()
        self._audit("Started the AI quality check", "AI", {"ai": config.label})
        return self.view()

    def _work(self, label: str, ask: evals.Ask) -> None:
        try:
            cases = self._cases if self._cases is not None else evals.load_cases()

            def progress(done: int, total: int) -> None:
                with self._lock:
                    self._progress = (done, total)

            result = evals.run(cases, ask, label=label, on_case=progress)
            rows = [result, *self.history()][:KEEP]
            atomic.write_text(self._path, json.dumps(rows, indent=1))
            self._audit(
                "Finished the AI quality check",
                "AI",
                {"ai": label, "score": f"{result['score']:.0%}", "verdict": result["verdict"]},
            )
            with self._lock:
                self._error = "" if result["verdict"] != "not_run" else result["advice"]
        except (evals.CaseError, OSError) as e:
            with self._lock:
                self._error = str(e)[:300]
        finally:
            with self._lock:
                self._running = False
