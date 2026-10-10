"""Pod discovery from Settings: read the names of the pages in the pod's Navigator, and keep them for Release impact.

Off for every pod until a person switches it on (Settings, Pod discovery). It is read only (runner/discovery.py),
it uses the same login and the same production guard as a run, and every look is written to the audit log. The page
names are kept per pod, in the client's data folder, and can be removed there.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from quartermaster.service import atomic

TIMEOUT_S = 300


def qm_discover_command(out: Path) -> list[str]:
    return [sys.executable, "-m", "quartermaster.cli", "discover", "--out", str(out)]


class Discovery:
    def __init__(
        self,
        folder: Path,
        *,
        command: Callable[[Path], list[str]] = qm_discover_command,
        cwd: Path | None = None,
        audit: Callable[[str, str, dict[str, Any]], None] | None = None,
        timeout_s: float = TIMEOUT_S,
    ):
        self._folder = folder
        self._command = command
        self._cwd = cwd
        self._audit = audit or (lambda what, subject, details: None)
        self._timeout = timeout_s
        self._lock = threading.Lock()
        self._states: dict[str, dict[str, Any]] = {}  # pod -> running, or why the last look failed
        # The variables the look gets (the active environment's pod and test user); None: this process's own.
        self.environ: Callable[[], dict[str, str]] | None = None

    # ------------------------------------------------------------------ what is kept

    @staticmethod
    def _slug(key: str) -> str:
        return key or "terminal"

    def _settings(self) -> dict[str, bool]:
        try:
            raw = json.loads((self._folder / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): bool(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

    def enabled(self, key: str) -> bool:
        return self._settings().get(self._slug(key), False)

    def set_enabled(self, key: str, on: bool) -> None:
        with self._lock:
            settings = {**self._settings(), self._slug(key): bool(on)}
            atomic.write_text(self._folder / "settings.json", json.dumps(settings))
        self._audit("Switched pod discovery on" if on else "Switched pod discovery off", "Discovery", {})

    def result(self, key: str) -> dict[str, Any] | None:
        try:
            data = json.loads((self._folder / f"{self._slug(key)}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and isinstance(data.get("pages"), list) else None

    def pages(self, key: str) -> list[str]:
        """The page names Release impact may use: only while discovery is on for this pod."""
        found = self.result(key) if self.enabled(key) else None
        return [str(p) for p in found["pages"]] if found else []

    def forget(self, key: str) -> None:
        (self._folder / f"{self._slug(key)}.json").unlink(missing_ok=True)
        self._audit("Removed the discovered pages", "Discovery", {})

    # ------------------------------------------------------------------ looking

    def view(self, key: str) -> dict[str, Any]:
        with self._lock:
            state = dict(self._states.get(key, {}))
        return {
            "enabled": self.enabled(key),
            "status": state.get("status", "idle"),
            "error": state.get("error"),
            "result": self.result(key),
        }

    def start(self, key: str) -> dict[str, Any]:
        if not self.enabled(key):
            raise ValueError("switch pod discovery on for this pod first")
        with self._lock:
            if self._states.get(key, {}).get("status") == "running":
                raise ValueError("a look at the pod is already running")
            self._states[key] = {"status": "running", "at": _now()}
        self._folder.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._look, args=(key,), name="qm-discovery", daemon=True).start()
        self._audit("Started pod discovery", "Discovery", {"read only": "yes"})
        return self.view(key)

    def _look(self, key: str) -> None:
        with tempfile.TemporaryDirectory(prefix="qm-discovery-") as scratch:
            out = Path(scratch) / "pages.json"
            last = ""
            code = -1
            try:
                proc = subprocess.Popen(
                    self._command(out),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    cwd=self._cwd,
                    env=self.environ() if self.environ else None,
                )
                try:
                    output, _ = proc.communicate(timeout=self._timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    raise _Failed("the pod did not answer in time") from None
                code = proc.returncode
                lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
                last = lines[-1] if lines else ""
                pages = _read_pages(out) if code == 0 else None
                if pages is None:
                    why = last[len("error: ") :] if last.startswith("error: ") else last
                    raise _Failed(why or "the look at the pod did not finish")
                atomic.write_text(self._folder / f"{self._slug(key)}.json", json.dumps(pages, indent=2))
            except _Failed as e:
                with self._lock:
                    self._states[key] = {"status": "failed", "error": str(e), "at": _now()}
                self._audit("Pod discovery failed", "Discovery", {"why": str(e)[:200]})
                return
            except OSError as e:
                with self._lock:
                    self._states[key] = {"status": "failed", "error": f"could not start: {e}", "at": _now()}
                self._audit("Pod discovery failed", "Discovery", {"why": str(e)[:200]})
                return
        with self._lock:
            self._states[key] = {"status": "done", "at": _now()}
        self._audit("Pod discovery finished", "Discovery", {"pages found": str(len(pages["pages"]))})


class _Failed(Exception):
    pass


def _read_pages(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        return None
    return {**data, "pages": [str(p) for p in data["pages"]][:500]}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
