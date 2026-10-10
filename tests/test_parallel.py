"""Running several tests of a folder at the same time (`qm run --parallel N`)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from conftest import EXAMPLES, FakeDriver
from test_service_api import app, call  # noqa: F401, F811  (app is a fixture)

from quartermaster import cli
from quartermaster.cli import main

LOGIN = (EXAMPLES / "smoke" / "login.yaml").read_text()
PAGE = {("role", "link:Navigator"): 1, ("xpath", "//*[starts-with(normalize-space(text()), 'Welcome,')]"): 1}


class Watch:
    """What the drivers of one run did, in order, and how many were open at once."""

    def __init__(self, barrier: int = 0) -> None:
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.opened: list[tuple[str, int]] = []  # (test, how many were open when it opened)
        self.closed: list[str] = []
        self.barrier = threading.Barrier(barrier, timeout=15) if barrier else None
        self.threads: set[str] = set()


class WatchedDriver(FakeDriver):
    def __init__(self, watch: Watch, run_dir: Path) -> None:
        super().__init__(PAGE, evidence_dir=run_dir)
        self.watch = watch
        self.name = run_dir.parent.name

    def open(self, env: Any, persona: str) -> None:
        w = self.watch
        with w.lock:
            w.active += 1
            w.peak = max(w.peak, w.active)
            w.opened.append((self.name, w.active))
            w.threads.add(threading.current_thread().name)
        if w.barrier is not None and not self.name.endswith("serial"):
            w.barrier.wait()  # only passes when all of them are open at the same time
        super().open(env, persona)

    def close(self) -> None:
        with self.watch.lock:
            self.watch.active -= 1
            self.watch.closed.append(self.name)
        super().close()


def folder(tmp_path: Path, names: list[str]) -> Path:
    specs = tmp_path / "suite"
    specs.mkdir()
    for name in names:
        (specs / f"{name}.yaml").write_text(LOGIN.replace("id: smoke.login", f"id: smoke.{name}"))
    return specs


def setup(monkeypatch: pytest.MonkeyPatch, watch: Watch) -> None:
    monkeypatch.setenv("QM_FUSION_URL", "https://abcd-dev2.fa.us6.oraclecloud.com")
    monkeypatch.setattr(cli, "driver_factory", lambda args, run_dir: WatchedDriver(watch, run_dir))


def test_tests_run_at_the_same_time_and_the_record_keeps_their_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    watch = Watch(barrier=3)  # every test waits for the other two: sequential running would never get past it
    setup(monkeypatch, watch)
    specs = folder(tmp_path, ["a", "b", "c"])
    evidence = tmp_path / "evidence"
    assert main(["run", str(specs), "--evidence", str(evidence), "--parallel", "3"]) == 0
    out = capsys.readouterr().out
    assert watch.peak == 3 and len(watch.threads) == 3 and "3/3 passed" in out
    [suite_dir] = list((evidence / "_suites").iterdir())
    suite = json.loads((suite_dir / "suite.json").read_text())
    assert [r["test_id"] for r in suite["runs"]] == ["smoke.a", "smoke.b", "smoke.c"]  # the order in the folder
    assert suite["status"] == "passed"


def test_by_default_one_test_runs_at_a_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    watch = Watch()
    setup(monkeypatch, watch)
    assert main(["run", str(folder(tmp_path, ["a", "b", "c"])), "--evidence", str(tmp_path / "ev")]) == 0
    assert watch.peak == 1 and len(watch.threads) == 1


def test_no_more_than_four_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    setup(monkeypatch, Watch())
    specs = folder(tmp_path, ["a"])
    for bad in ("5", "0"):
        with pytest.raises(SystemExit):
            main(["run", str(specs), "--parallel", bad])
        assert "--parallel" in capsys.readouterr().err


def test_a_test_tagged_serial_runs_alone_after_the_others(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    watch = Watch(barrier=2)  # the two others meet each other; the serial one is not made to wait
    setup(monkeypatch, watch)
    specs = folder(tmp_path, ["a", "b", "serial"])
    (specs / "serial.yaml").write_text(
        LOGIN.replace("id: smoke.login", "id: smoke.serial").replace("steps:", "tags: [serial]\nsteps:", 1)
    )
    assert main(["run", str(specs), "--evidence", str(tmp_path / "ev"), "--parallel", "4"]) == 0
    me = [n for n, _ in watch.opened]
    assert me[-1].startswith("smoke.serial")  # it opened last
    assert dict(watch.opened)[me[-1]] == 1  # nothing else was open then
    assert watch.closed[-1] == me[-1]


def test_a_failing_test_does_not_stop_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    watch = Watch()
    monkeypatch.setenv("QM_FUSION_URL", "https://abcd-dev2.fa.us6.oraclecloud.com")
    monkeypatch.setattr(
        cli,
        "driver_factory",
        lambda args, run_dir: (
            WatchedDriver(watch, run_dir) if "smoke.b" not in str(run_dir) else FakeDriver(evidence_dir=run_dir)
        ),
    )
    rc = main(
        [
            "run",
            str(folder(tmp_path, ["a", "b", "c"])),
            "--evidence",
            str(tmp_path / "ev"),
            "--parallel",
            "3",
            "--retries",
            "0",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 1 and "2/3 passed" in out and "FAILED  smoke.b" in out and "PASSED  smoke.a" in out


def test_progress_lines_from_several_tests_are_all_valid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup(monkeypatch, Watch())
    events = tmp_path / "events.jsonl"
    names = [f"t{i}" for i in range(8)]
    assert (
        main(
            [
                "run",
                str(folder(tmp_path, names)),
                "--evidence",
                str(tmp_path / "ev"),
                "--events",
                str(events),
                "--parallel",
                "4",
            ]
        )
        == 0
    )
    rows = [json.loads(line) for line in events.read_text().splitlines()]  # a torn line would fail here
    assert rows[0]["type"] == "suite_start" and rows[0]["parallel"] == 4
    assert sum(r["type"] == "run_end" for r in rows) == 8 and rows[-1]["type"] == "suite_end"


def test_a_pod_that_looks_like_production_stops_every_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    setup(monkeypatch, Watch())
    monkeypatch.setenv("QM_FUSION_URL", "https://abcd.fa.us6.oraclecloud.com")
    evidence = tmp_path / "ev"
    assert main(["run", str(folder(tmp_path, ["a", "b", "c"])), "--evidence", str(evidence), "--parallel", "3"]) == 2
    assert "production" in capsys.readouterr().err and not (evidence / "_suites").exists()


def test_several_real_browsers_can_run_in_threads_at_once(tmp_path: Path) -> None:
    """Each thread starts its own Playwright and Chromium, as each test of a parallel run does."""
    pytest.importorskip("playwright")
    import os

    from playwright.sync_api import sync_playwright

    page_file = tmp_path / "p.html"
    page_file.write_text("<!doctype html><title>t</title><p id=x>hello</p>")
    chromium = os.environ.get("QM_CHROMIUM_PATH") or (
        "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
    )
    gate = threading.Barrier(3, timeout=60)  # all three browsers are open together before any closes
    seen: list[str] = []
    problems: list[str] = []

    def one(n: int) -> None:
        try:
            pw = sync_playwright().start()
            try:
                browser = pw.chromium.launch(executable_path=chromium)
                page = browser.new_page()
                page.goto(page_file.as_uri())
                gate.wait()
                seen.append(f"{n}:{page.locator('#x').inner_text()}")
                browser.close()
            finally:
                pw.stop()
        except Exception as e:  # noqa: BLE001 - the test reports whatever went wrong in a thread
            problems.append(f"{n}: {type(e).__name__}: {e}")

    threads = [threading.Thread(target=one, args=(n,)) for n in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert problems == [] and sorted(seen) == ["0:hello", "1:hello", "2:hello"]


# ------------------------------------------------------------------ in the service


def test_the_run_option_is_checked_and_passed_to_the_command() -> None:
    from quartermaster.service.runner import check_options, qm_run_command

    assert check_options({})["parallel"] == 1
    for bad in (0, 5, True, "2", 1.5):
        with pytest.raises(ValueError, match="parallel must be a whole number from 1 to 4"):
            check_options({"parallel": bad})
    one = qm_run_command("hcm", check_options({}), Path("ev"), Path("e.jsonl"))
    three = qm_run_command("hcm", check_options({"parallel": 3}), Path("ev"), Path("e.jsonl"))
    assert "--parallel" not in one and three[three.index("--parallel") + 1] == "3"


def test_the_setting_is_checked_shown_and_used_by_new_runs(app: Any) -> None:  # noqa: F811
    assert call(app, "GET", "/api/status")["default_options"]["parallel"] == 1
    call(app, "POST", "/api/settings", {"parallel": "3"})
    assert call(app, "GET", "/api/status")["default_options"]["parallel"] == 3
    run = call(app, "POST", "/api/runs", {"target": "hcm/pass.yaml"})
    assert run["options"]["parallel"] == 3
    other = call(app, "POST", "/api/runs", {"target": "hcm/pass.yaml", "options": {"parallel": 1}})
    assert other["options"]["parallel"] == 1  # a run may ask for its own
    from quartermaster.service.api import ApiError

    with pytest.raises(ApiError, match="1, 2, 3 or 4"):
        call(app, "POST", "/api/settings", {"parallel": "9"})
