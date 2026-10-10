"""Settings chosen in the web UI, kept in <data folder>/settings.json.

Only descriptive settings live here: a name for the environment, the Oracle release it is on, and
which AI provider and model to use. The pod address and sign-in stay in environment variables, the
password is never stored, and neither is the AI key: only the name of the variable that holds it.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from quartermaster.ai.providers import check_settings

FIELDS = {
    "environment_name": 40,
    "release": 20,
    "ai_provider": 20,
    "ai_model": 120,
    "ai_base_url": 300,
    "ai_key_env": 80,
    "ai_workspace": 100,
    "retries": 1,  # when a step fails, try it again this many times (0 to 3); "" means 1
    "parallel": 1,  # tests run at the same time (1 to 4); "" means 1
    "ai_suggest": 3,  # "off" turns off the AI's suggestions for steps that cannot find their item; else on
}
_AI = ("ai_provider", "ai_model", "ai_base_url", "ai_key_env", "ai_workspace")
_RELEASE = re.compile(r"^[A-Za-z0-9 ._-]*$")


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        # The environment set up in Settings, when there is one: its name and release win over the
        # ones saved here, and a changed release is saved on it (see environments.py).
        self.environment: Callable[[], dict[str, str] | None] | None = None
        self.save_release: Callable[[str], None] | None = None
        self.save_name: Callable[[str], None] | None = None

    def get(self) -> dict[str, str]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        out = {k: str(raw.get(k) or "") for k in FIELDS}
        env = self.environment() if self.environment else None
        if env:
            out.update(environment_name=env["name"], release=env["release"])
        return out

    def update(self, changes: dict[str, Any]) -> dict[str, str]:
        unknown = set(changes) - set(FIELDS)
        if unknown:
            raise ValueError(f"unknown settings: {', '.join(sorted(unknown))}")
        clean = {k: " ".join(str(v or "").split())[: FIELDS[k]] for k, v in changes.items() if k not in _AI}
        clean.update(check_settings({k: v for k, v in changes.items() if k in _AI}))
        if clean.get("retries", "") not in ("", "0", "1", "2", "3"):
            raise ValueError("retries must be 0, 1, 2 or 3")
        if clean.get("parallel", "") not in ("", "1", "2", "3", "4"):
            raise ValueError("tests at the same time must be 1, 2, 3 or 4")
        if not _RELEASE.match(clean.get("release", "")):
            raise ValueError("the release may use letters, digits, spaces, dots and dashes, e.g. 26C")
        env = self.environment() if self.environment else None
        if env and "release" in clean and self.save_release:
            self.save_release(clean.pop("release"))  # the release belongs to the environment
        if env and "environment_name" in clean:
            name = clean.pop("environment_name")
            if name and self.save_name:
                self.save_name(name)  # renames the environment in use
        with self._lock:
            current = {**self.get(), **clean}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        return current


def check_pod(url: str, timeout: float = 15) -> dict[str, Any]:
    """Can this computer reach the pod? Opens the sign-in page; any answer below 500 means yes."""
    checked = datetime.now().astimezone().isoformat(timespec="seconds")
    if not url:
        return {"ok": False, "checked_at": checked, "message": "No pod address is set (QM_FUSION_URL)."}
    started = time.monotonic()
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "Quartermaster pod check"})
        with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 - the pod address the user set
            code = res.status
    except urllib.error.HTTPError as e:
        code = e.code
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", e)
        return {"ok": False, "checked_at": checked, "message": f"Could not reach the pod: {reason}"}
    ms = round((time.monotonic() - started) * 1000)
    ok = code < 500
    return {
        "ok": ok,
        "checked_at": checked,
        "status_code": code,
        "ms": ms,
        "message": f"The pod answered in {ms} ms." if ok else f"The pod answered with an error ({code}).",
    }
