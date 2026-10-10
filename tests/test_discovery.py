"""Pod discovery: read the page names of the pod's Navigator, read only, off until switched on."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import EXAMPLES, FakeDriver
from test_service_api import app, call  # noqa: F401, F811  (app is a fixture)

from quartermaster import cli
from quartermaster.cli import main
from quartermaster.runner.discovery import DiscoveryError, clean_names, match_pages, scan_navigator
from quartermaster.service.api import ApiError
from quartermaster.service.discovery import Discovery

# ------------------------------------------------------------------ matching features to pages


def test_a_feature_matches_the_pages_whose_whole_name_it_mentions() -> None:
    pages = ["Locations", "Personal Details", "Person Search", "Tools", "Reports", "Jobs", "Home"]
    assert match_pages("Location Page Shows Address on Map", pages) == ["Locations"]
    assert match_pages("New Personal Details Page replaces the old screens", pages) == ["Personal Details"]
    assert match_pages("Faster Person Search for HR", pages) == ["Person Search"]
    assert match_pages("A report about tools", pages) == []  # only general words
    assert match_pages("Details of the person", pages) == []  # part of a name is not the page
    assert match_pages("Anything", []) == []


def test_the_longest_names_come_first_and_only_a_few_are_given() -> None:
    pages = ["Worker", "Worker Goals", "Worker Goals Plans", "Worker Skills", "Worker Notes"]
    got = match_pages("Worker goals plans and worker skills and notes", pages, limit=3)
    assert got[0] == "Worker Goals Plans" and len(got) == 3


def test_buttons_every_page_has_and_doubles_are_not_page_names() -> None:
    raw = ["Navigator", "Show More", "Locations", "locations", "  Person   Search ", "x", "A" * 80, "Home", "Jobs"]
    assert clean_names(raw) == ["Locations", "Person Search", "Jobs"]


# ------------------------------------------------------------------ reading the Navigator


class FakeLocator:
    def __init__(self, page: FakePage, name: str) -> None:
        self.page, self.name = page, name

    @property
    def first(self) -> FakeLocator:
        return self

    def click(self, timeout: int) -> None:
        self.page.clicked.append(self.name)
        if self.name in self.page.unopenable:
            raise TimeoutError("not clickable")
        self.page.open_group(self.name)


class FakePage:
    """The home page, then the Navigator with folded groups that show more links when opened."""

    def __init__(self, groups: dict[str, list[str]], unopenable: set[str] | None = None) -> None:
        self.groups = groups
        self.unopenable = unopenable or set()
        self.state = "home"
        self.opened: set[str] = set()
        self.clicked: list[str] = []

    def open_navigator(self) -> bool:
        self.state = "navigator"
        return True

    def open_group(self, name: str) -> None:
        self.opened.add(name)

    def evaluate(self, script: str) -> list[dict[str, Any]]:
        items = [{"text": "Skip to main content", "folded": False}, {"text": "Navigator", "folded": False}]
        if self.state == "navigator":
            for group, links in self.groups.items():
                items.append({"text": group, "folded": group not in self.opened})
                if group in self.opened:
                    items += [{"text": link, "folded": False} for link in links]
        return items

    def get_by_text(self, name: str, exact: bool) -> FakeLocator:
        return FakeLocator(self, name)

    def wait_for_timeout(self, ms: int) -> None:
        pass


def test_the_navigator_is_opened_and_its_folded_groups_read() -> None:
    page = FakePage({"My Client Groups": ["Person Management", "Locations"], "Tools": ["Reports and Analytics"]})
    names = scan_navigator(page, page.open_navigator)
    assert names == ["My Client Groups", "Person Management", "Locations", "Tools", "Reports and Analytics"]
    assert page.clicked == ["My Client Groups", "Tools"]  # only the folded groups were clicked, nothing else


def test_a_group_that_will_not_open_does_not_stop_the_rest() -> None:
    page = FakePage({"Broken": ["Never seen"], "Fine": ["Jobs"]}, unopenable={"Broken"})
    assert scan_navigator(page, page.open_navigator) == ["Broken", "Fine", "Jobs"]


def test_no_navigator_button_and_an_empty_navigator_are_said_plainly() -> None:
    page = FakePage({})
    with pytest.raises(DiscoveryError, match="Navigator button was not found"):
        scan_navigator(page, lambda: False)
    with pytest.raises(DiscoveryError, match="no pages were listed"):
        scan_navigator(page, page.open_navigator)


def test_at_most_forty_groups_are_opened() -> None:
    page = FakePage({f"Group {i}": [f"Page {i}"] for i in range(60)})
    scan_navigator(page, page.open_navigator)
    assert len(page.clicked) == 40


# ------------------------------------------------------------------ the service


def fake_command(script: str):  # type: ignore[no-untyped-def]
    return lambda out: [sys.executable, "-c", script, str(out)]


OK = (
    "import json,sys; open(sys.argv[1],'w').write(json.dumps("
    "{'at':'2026-10-03T10:00:00+00:00','pod_host':'abcd-dev2.fa.us6.oraclecloud.com','pages':['Locations','Jobs']}))"
    "; print('Found 2 page(s) in the Navigator.')"
)
FAIL = "print('error: the Navigator button was not found on the pod home page'); raise SystemExit(2)"


def wait_for(check: Any, seconds: float = 20) -> Any:
    end = time.time() + seconds
    while time.time() < end:
        if value := check():
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def make(
    tmp_path: Path, script: str = OK, timeout_s: float = 30
) -> tuple[Discovery, list[tuple[str, str, dict[str, Any]]]]:
    told: list[tuple[str, str, dict[str, Any]]] = []
    d = Discovery(
        tmp_path / "discovery",
        command=fake_command(script),
        audit=lambda w, s, x: told.append((w, s, x)),
        timeout_s=timeout_s,
    )
    return d, told


def test_nothing_is_looked_at_until_it_is_switched_on(tmp_path: Path) -> None:
    d, told = make(tmp_path)
    assert d.view("env1") == {"enabled": False, "status": "idle", "error": None, "result": None}
    with pytest.raises(ValueError, match="switch pod discovery on"):
        d.start("env1")
    assert told == []


def test_a_look_keeps_the_pages_and_tells_the_audit_log(tmp_path: Path) -> None:
    d, told = make(tmp_path)
    d.set_enabled("env1", True)
    d.start("env1")
    done = wait_for(lambda: (v := d.view("env1"))["status"] == "done" and v)
    assert done["result"]["pages"] == ["Locations", "Jobs"] and d.pages("env1") == ["Locations", "Jobs"]
    assert [t[0] for t in told] == ["Switched pod discovery on", "Started pod discovery", "Pod discovery finished"]
    assert told[-1][2] == {"pages found": "2"}
    assert d.pages("other-pod") == []  # kept per pod


def test_the_pages_are_not_used_once_it_is_switched_off(tmp_path: Path) -> None:
    d, _ = make(tmp_path)
    d.set_enabled("env1", True)
    d.start("env1")
    wait_for(lambda: d.view("env1")["status"] == "done")
    d.set_enabled("env1", False)
    assert d.pages("env1") == []
    with pytest.raises(ValueError, match="switch pod discovery on"):
        d.start("env1")


def test_a_failed_look_says_why_and_keeps_nothing(tmp_path: Path) -> None:
    d, told = make(tmp_path, FAIL)
    d.set_enabled("env1", True)
    d.start("env1")
    failed = wait_for(lambda: (v := d.view("env1"))["status"] == "failed" and v)
    assert failed["error"] == "the Navigator button was not found on the pod home page" and failed["result"] is None
    assert told[-1][0] == "Pod discovery failed"


def test_a_look_that_takes_too_long_is_stopped(tmp_path: Path) -> None:
    d, _ = make(tmp_path, "import time; time.sleep(30)", timeout_s=0.5)
    d.set_enabled("env1", True)
    d.start("env1")
    failed = wait_for(lambda: (v := d.view("env1"))["status"] == "failed" and v)
    assert "did not answer in time" in failed["error"]


def test_only_one_look_at_a_time_and_the_list_can_be_removed(tmp_path: Path) -> None:
    d, told = make(tmp_path, "import time; time.sleep(1.5)")
    d.set_enabled("env1", True)
    d.start("env1")
    with pytest.raises(ValueError, match="already running"):
        d.start("env1")
    d2, _ = make(tmp_path / "x")
    d2.set_enabled("e", True)
    d2.start("e")
    wait_for(lambda: d2.view("e")["status"] == "done")
    d2.forget("e")
    assert d2.view("e")["result"] is None


# ------------------------------------------------------------------ qm discover


class NavDriver(FakeDriver):
    def __init__(self, pages: list[str] | None = None, error: Exception | None = None) -> None:
        super().__init__()
        self.pages, self.error = pages or [], error

    def navigator_pages(self) -> list[str]:
        if self.error:
            raise self.error
        return self.pages


def test_qm_discover_writes_the_pages_and_closes_the_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    driver = NavDriver(["Locations", "Jobs"])
    monkeypatch.setenv("QM_FUSION_URL", "https://abcd-dev2.fa.us6.oraclecloud.com")
    monkeypatch.setattr(cli, "driver_factory", lambda args, run_dir: driver)
    out = tmp_path / "found" / "pages.json"
    assert main(["discover", "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["pages"] == ["Locations", "Jobs"] and data["pod_host"] == "abcd-dev2.fa.us6.oraclecloud.com"
    assert driver.opened and driver.closed and "Found 2 page(s)" in capsys.readouterr().out
    # read only: it logged in and read the Navigator, and did nothing else on the page
    assert [c[0] for c in driver.calls] == ["open"]


def test_qm_discover_refuses_a_pod_that_looks_like_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    driver = NavDriver(["Locations"])
    monkeypatch.setenv("QM_FUSION_URL", "https://abcd.fa.us6.oraclecloud.com")
    monkeypatch.setattr(cli, "driver_factory", lambda args, run_dir: driver)
    out = tmp_path / "pages.json"
    assert main(["discover", "--out", str(out)]) == 2
    assert "production" in capsys.readouterr().err and not out.exists() and not driver.opened


def test_qm_discover_says_when_the_navigator_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    driver = NavDriver(error=DiscoveryError("the Navigator button was not found on the pod's home page"))
    monkeypatch.setenv("QM_FUSION_URL", "https://abcd-dev2.fa.us6.oraclecloud.com")
    monkeypatch.setattr(cli, "driver_factory", lambda args, run_dir: driver)
    assert main(["discover", "--out", str(tmp_path / "p.json")]) == 2
    assert "Navigator button was not found" in capsys.readouterr().err and driver.closed


# ------------------------------------------------------------------ in the pages of Quartermaster


def test_the_api_switches_it_on_looks_and_release_impact_shows_the_pages(tmp_path: Path) -> None:
    import shutil

    tests = tmp_path / "tests"
    shutil.copytree(EXAMPLES / "tests", tests)
    from quartermaster.service.api import App

    a = App(
        tests_root=tests, evidence_root=tmp_path / "ev", data_dir=tmp_path / ".qm", releases_root=EXAMPLES / "releases"
    )
    assert call(a, "GET", "/api/discovery")["enabled"] is False
    with pytest.raises(ApiError, match="switch pod discovery on"):
        call(a, "POST", "/api/discovery/run", {})
    plain = call(a, "GET", "/api/releases/plan?name=26D_sample.json")
    assert plain["pod_pages"] == 0 and all(f["pod_pages"] == [] for f in plain["features"])

    a.discovery._command = fake_command(OK)  # a stand-in for the browser that reads the pod
    assert call(a, "POST", "/api/discovery", {"enabled": True})["enabled"] is True
    call(a, "POST", "/api/discovery/run", {})
    wait_for(lambda: call(a, "GET", "/api/discovery")["status"] == "done")
    word = next(w for f in plain["features"] for w in f["title"].split() if len(w) >= 5)
    (tmp_path / ".qm" / "discovery").mkdir(exist_ok=True)
    for path in (tmp_path / ".qm" / "discovery").glob("*.json"):
        if path.name != "settings.json":
            data = json.loads(path.read_text())
            data["pages"] = [word, "Home"]
            path.write_text(json.dumps(data))
    shown = call(a, "GET", "/api/releases/plan?name=26D_sample.json")
    assert shown["pod_pages"] == 2 and shown["summary"]["on_pod"] >= 1
    assert any(f["pod_pages"] == [word] for f in shown["features"])
    actions = [e["action"] for e in json.loads(a.handle("GET", "/api/audit", b"").body)["entries"]]
    assert {"Switched pod discovery on", "Started pod discovery", "Pod discovery finished"} <= set(actions)
    call(a, "POST", "/api/discovery", {"enabled": False})
    assert call(a, "GET", "/api/releases/plan?name=26D_sample.json")["pod_pages"] == 0  # off: not used


# ------------------------------------------------------------------ a real browser


FAKE_HOME = """<!doctype html><title>Home</title>
<a href="#home" title="Navigator" id="nav">Navigator</a>
<button onclick="window.saved = true">Save</button>
<div id="panel" hidden>
  <button aria-expanded="false" onclick="
     this.setAttribute('aria-expanded','true'); this.nextElementSibling.hidden = false">My Client Groups</button>
  <ul hidden><li><a href="#a">Person Management</a></li><li><a href="#b">Locations</a></li></ul>
  <button aria-expanded="false" onclick="
     this.setAttribute('aria-expanded','true'); this.nextElementSibling.hidden = false">Tools</button>
  <ul hidden><li><a href="#c">Reports and Analytics</a></li></ul>
</div>
<script>document.getElementById('nav').onclick = (e) => { document.getElementById('panel').hidden = false; };</script>
"""


def test_the_scan_reads_a_real_page_and_clicks_nothing_but_the_menu(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    import os

    from playwright.sync_api import sync_playwright

    page_file = tmp_path / "home.html"
    page_file.write_text(FAKE_HOME)
    chromium = os.environ.get("QM_CHROMIUM_PATH") or (
        "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium)
        try:
            page = browser.new_page()
            page.goto(page_file.as_uri())
            names = scan_navigator(page, lambda: page.locator("#nav").click() or True)
            assert names == ["My Client Groups", "Person Management", "Locations", "Tools", "Reports and Analytics"]
            assert page.evaluate("window.saved") is None  # the Save button was never pressed
        finally:
            browser.close()
