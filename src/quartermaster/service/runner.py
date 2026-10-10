"""Run queue: requested runs wait in line and run one at a time, each as its own `qm run` process.

A separate process per run keeps the service responsive (Playwright's browser control must stay
on one thread), keeps a crashing run from taking the service down, and means the web service
runs exactly what `qm run` runs from a terminal. Progress comes back through the run's events
file (`qm run --events`), which the web UI reads to show steps as they happen.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from quartermaster.service.store import FINISHED, Store, now

# What a run may ask for, with defaults. Anything else in a request is rejected.
DEFAULT_OPTIONS: dict[str, Any] = {
    "screenshots": "every-step",
    "video": "off",
    "evidence_doc": True,
    "release": "",
    "tester": "",
    "headed": False,
    "retries": 1,  # when a step fails, try it again this many times (0 to 3; only steps that are safe to repeat)
    "parallel": 1,  # tests run at the same time, each in its own browser (1 to 4; tests tagged "serial" run alone)
    "highlight": True,  # red marks on what each step clicks or fills: live, in the video and in the screenshots
    "only": [],  # test ids: run just these from the folder (e.g. the tests a release puts at risk)
    "label": "",  # a name for the run, shown instead of the folder
}
_CHOICES = {"screenshots": ("off", "on-failure", "every-step"), "video": ("off", "on-failure", "always")}

_TEST_ID = re.compile(r"^[A-Za-z0-9_.-]+$")

CommandBuilder = Callable[[str, dict[str, Any], Path, Path], list[str]]


def qm_run_command(target: str, options: dict[str, Any], evidence_root: Path, events: Path) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "quartermaster.cli",
        "run",
        target,
        "--evidence",
        str(evidence_root),
        "--events",
        str(events),
        "--screenshots",
        options["screenshots"],
        "--video",
        options["video"],
    ]
    if options["evidence_doc"]:
        cmd.append("--evidence-doc")
    if options["headed"]:
        cmd.append("--headed")
    if not options.get("highlight", True):
        cmd.append("--no-highlight")
    cmd += ["--retries", str(options.get("retries", 1))]
    if options.get("parallel", 1) > 1:
        cmd += ["--parallel", str(options["parallel"])]
    if options["release"]:
        cmd += ["--release", options["release"]]
    if options["tester"]:
        cmd += ["--tester", options["tester"]]
    if options.get("only"):
        cmd += ["--only", ",".join(options["only"])]
    return cmd


def check_options(requested: dict[str, Any]) -> dict[str, Any]:
    unknown = set(requested) - set(DEFAULT_OPTIONS)
    if unknown:
        raise ValueError(f"unknown run options: {', '.join(sorted(unknown))}")
    options = {**DEFAULT_OPTIONS, **requested}
    for key, allowed in _CHOICES.items():
        if options[key] not in allowed:
            raise ValueError(f"{key} must be one of {', '.join(allowed)}")
    retries = options["retries"]
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 3:
        raise ValueError("retries must be a whole number from 0 to 3")
    parallel = options["parallel"]
    if isinstance(parallel, bool) or not isinstance(parallel, int) or not 1 <= parallel <= 4:
        raise ValueError("parallel must be a whole number from 1 to 4")
    for key in ("evidence_doc", "headed", "highlight"):
        options[key] = bool(options[key])
    for key in ("release", "tester"):
        options[key] = str(options[key]).strip()[:60]
    options["label"] = str(options["label"]).strip()[:80]
    only = options["only"]
    if not isinstance(only, list) or not all(isinstance(t, str) and _TEST_ID.match(t) for t in only):
        raise ValueError("only must be a list of test ids")
    if len(only) > 1000:
        raise ValueError("too many tests in one run")
    options["only"] = list(dict.fromkeys(only))
    return options


class RunQueue:
    def __init__(
        self,
        store: Store,
        *,
        tests_root: Path,
        evidence_root: Path,
        work_dir: Path,
        command: CommandBuilder = qm_run_command,
        cwd: Path | None = None,
    ):
        self.store = store
        # The variables each run gets (the active environment's pod and users); None: this process's own.
        self.environ: Callable[[], dict[str, str]] | None = None
        # Run options chosen in Settings, used when a request does not give its own (e.g. retries).
        self.defaults: Callable[[], dict[str, Any]] | None = None
        # Told about every run that has ended (passed, failed, error or stopped), with its row from the store.
        self.on_finished: Callable[[dict[str, Any]], None] | None = None
        self.tests_root = tests_root.resolve()
        self.evidence_root = evidence_root.resolve()
        self.work_dir = work_dir
        self.command = command
        self.cwd = cwd
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._current: str | None = None
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ requests

    def submit(self, target: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Queue a run of one test file or a folder of tests inside the tests folder."""
        self._inside_tests(target)
        wanted = {**(self.defaults() if self.defaults else {}), **(options or {})}
        run = self.store.create(target, check_options(wanted))
        self._wake.set()
        return run

    def cancel(self, run_id: str) -> dict[str, Any] | None:
        run = self.store.get(run_id)
        if run is None or run["status"] in FINISHED:
            return run
        if run["status"] == "queued":
            self.store.update(run_id, status="cancelled", finished_at=now())
        else:
            with self._lock:
                if self._current == run_id and self._proc is not None:
                    self._cancelled.add(run_id)
                    self._proc.terminate()  # the worker records it as cancelled once the process has ended
        return self.store.get(run_id)

    def events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        """Progress events of a run, from line `after` on (so a page can poll for new ones)."""
        run = self.store.get(run_id)
        if not run or not run.get("events_path"):
            return []
        path = Path(run["events_path"])
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[after:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                break  # a line still being written; read it next time
        return out

    # ------------------------------------------------------------------ worker

    def start(self) -> None:
        self.store.recover()
        self._thread = threading.Thread(target=self._loop, name="qm-run-queue", daemon=True)
        self._thread.start()

    def busy(self) -> bool:
        """A run is going now."""
        with self._lock:
            return self._proc is not None

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            if self._proc is not None:
                self._proc.terminate()
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            run = self.store.claim_next()
            if run is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            self._execute(run)
            self._tell_finished(run["id"])

    def _execute(self, run: dict[str, Any]) -> None:
        folder = self.work_dir / run["id"]
        folder.mkdir(parents=True, exist_ok=True)
        events, log = folder / "events.jsonl", folder / "output.log"
        self.store.update(run["id"], events_path=str(events), log_path=str(log))
        try:
            cmd = self.command(str(self._inside_tests(run["target"])), run["options"], self.evidence_root, events)
        except ValueError as e:  # the test file was removed while the run waited
            self.store.update(run["id"], status="error", finished_at=now(), error=str(e))
            return
        try:
            with log.open("wb") as out:
                env = self.environ() if self.environ else None
                proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, cwd=self.cwd, env=env)
                with self._lock:
                    self._proc, self._current = proc, run["id"]
                code = proc.wait()
        except OSError as e:
            self.store.update(run["id"], status="error", finished_at=now(), error=f"Could not start the run: {e}")
            return
        finally:
            with self._lock:
                self._proc, self._current = None, None

        end = next((e for e in reversed(self.events(run["id"])) if e.get("type") == "suite_end"), None)
        fields: dict[str, Any] = {"finished_at": now(), "exit_code": code}
        if end:
            fields["suite_dir"] = end.get("suite_dir")
            fields["summary"] = end.get("summary")
        with self._lock:
            cancelled = run["id"] in self._cancelled
            self._cancelled.discard(run["id"])
        if cancelled:
            fields["status"] = "cancelled"
        elif code == 0:
            fields["status"] = "passed"
        elif code == 1 and end:
            fields["status"] = "failed"  # tests ran; at least one failed
        else:
            fields["status"] = "error"
            fields["error"] = _last_lines(log) or f"The run stopped with exit code {code}."
        self.store.update(run["id"], **fields)

    def _tell_finished(self, run_id: str) -> None:
        """What happens after a run (a notification, for one) must never stop the queue."""
        if self.on_finished is None:
            return
        try:
            row = self.store.get(run_id)
            if row is not None and row["status"] in FINISHED:
                self.on_finished(row)
        except Exception as e:
            print(f"warning: after run {run_id}: {type(e).__name__}: {e}", file=sys.stderr)

    # ------------------------------------------------------------------ helpers

    def _inside_tests(self, target: str) -> Path:
        path = (self.tests_root / target).resolve()
        if path != self.tests_root and self.tests_root not in path.parents:
            raise ValueError("tests must be inside the tests folder")
        if not path.exists():
            raise ValueError(f"no test file or folder named {target!r}")
        return path


def _last_lines(log: Path, n: int = 5) -> str:
    try:
        lines = [ln for ln in log.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        return ""
    return "\n".join(lines[-n:])
