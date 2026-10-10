"""Sign in by hand from Settings, for pods behind single sign-on or MFA.

`qm signin` opens a browser on the pod; the person signs in there. The session it prints is kept
in this process's environment (QM_FUSION_SESSION), never in a file, so the runs, recordings and
Prepare started from here inherit it. It is gone when Quartermaster stops; the pod also ends it
after a while, and then a run says to sign in by hand again.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from quartermaster.runner.session import ENV, PREFIX, SessionError, decode_session


def qm_signin_command() -> list[str]:
    return [sys.executable, "-m", "quartermaster.cli", "signin"]


class SignIn:
    """One sign-in by hand at a time. Each session belongs to one pod (`key`, the environment id):
    a run gets only the session of its own pod. The key "" is the pod set in the terminal, whose
    session goes in this process's environment as before."""

    def __init__(self, command: Callable[[], list[str]] = qm_signin_command, cwd: Path | None = None):
        self._command = command
        self._cwd = cwd
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._key = ""  # the pod the sign-in in progress is for
        self._sessions: dict[str, str] = {}  # pod -> session, in memory only
        self._states: dict[str, dict[str, Any]] = {}
        # The variables `qm signin` gets (the active environment's pod); None: this process's own.
        self.environ: Callable[[], dict[str, str]] | None = None

    def session(self, key: str = "") -> str:
        with self._lock:
            return os.environ.get(ENV, "") if not key else self._sessions.get(key, "")

    def view(self, key: str = "") -> dict[str, Any]:
        with self._lock:
            if key in self._states:
                return dict(self._states[key])
            has = os.environ.get(ENV) if not key else self._sessions.get(key)
            return {"status": "done", "at": None} if has else {"status": "none"}

    def start(self, key: str = "") -> dict[str, Any]:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                raise ValueError("a sign-in is already waiting in the browser that opened")
            self._proc = subprocess.Popen(
                self._command(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                cwd=self._cwd,
                env=self.environ() if self.environ else None,
            )
            self._key = key
            self._states[key] = {"status": "waiting", "at": _now()}
            proc = self._proc
        threading.Thread(target=self._follow, args=(proc, key), name="qm-signin", daemon=True).start()
        return self.view(key)

    def _follow(self, proc: subprocess.Popen[str], key: str) -> None:
        last = ""
        got = False
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith(PREFIX):
                text = line[len(PREFIX) :]
                try:
                    decode_session(text)
                except SessionError as e:
                    last = str(e)
                    continue
                with self._lock:
                    if key:
                        self._sessions[key] = text  # in memory only
                    else:
                        os.environ[ENV] = text  # in memory only; the runs started from here inherit it
                got = True
            elif line:
                last = line
        proc.wait()
        with self._lock:
            if self._proc is not proc:
                return  # cancelled, or a newer sign-in
            if got:
                self._states[key] = {"status": "done", "at": _now()}
            else:
                why = last[len("error: ") :] if last.startswith("error: ") else last
                self._states[key] = {"status": "failed", "at": _now(), "error": why or "the sign-in browser closed"}

    def forget(self, key: str = "") -> dict[str, Any]:
        if self._key == key:
            self.stop()
        with self._lock:
            if key:
                self._sessions.pop(key, None)
            else:
                os.environ.pop(ENV, None)
            self._states[key] = {"status": "none"}
        return self.view(key)

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            state = self._states.get(self._key, {})
            if state.get("status") == "waiting":
                del self._states[self._key]
        if proc is not None and proc.poll() is None:
            proc.terminate()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
