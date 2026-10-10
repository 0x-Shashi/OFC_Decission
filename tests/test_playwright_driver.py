"""The browser driver's screenshot handling, with a stand-in page (no browser needed)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from quartermaster.runner.playwright_driver import PlaywrightDriver


class SlowPage:
    """Fails the first `failures` screenshots, as a page still being built does."""

    def __init__(self, failures: int):
        self.failures = failures
        self.shots = 0
        self.scripts: list[str] = []

    def screenshot(self, path: str, timeout: int) -> None:
        self.shots += 1
        if self.shots <= self.failures:
            raise TimeoutError(f"Page.screenshot: Timeout {timeout}ms exceeded.")
        Path(path).write_bytes(b"png")

    def evaluate(self, script: str, *args: Any) -> None:
        self.scripts.append(script)

    def wait_for_timeout(self, ms: int) -> None:
        pass


def driver(tmp_path: Path, page: SlowPage) -> PlaywrightDriver:
    d = PlaywrightDriver(evidence_dir=str(tmp_path), settle_ms=100)
    d.page = page
    return d


def test_a_slow_screenshot_is_tried_again_after_the_page_settles(tmp_path: Path) -> None:
    page = SlowPage(failures=1)
    path = driver(tmp_path, page).screenshot("step-04")
    assert path is not None and Path(path).read_bytes() == b"png"
    assert page.shots == 2
    assert any("__qm_highlight" in s for s in page.scripts)  # the red box is still cleared


def test_a_screenshot_that_fails_twice_is_reported(tmp_path: Path) -> None:
    page = SlowPage(failures=2)
    with pytest.raises(TimeoutError):
        driver(tmp_path, page).screenshot("step-04")
    assert page.shots == 2
    assert any("__qm_highlight" in s for s in page.scripts)


# ---------------------------------------------------------------------- scheduled processes (ESS)


class Reply:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status, self.ok, self._body = status, 200 <= status < 300, body

    def json(self) -> Any:
        return self._body


class Requests:
    """The browser context's request client: answers each status check with the next reply."""

    def __init__(self, replies: list[Reply]) -> None:
        self.replies, self.urls = replies, []

    def get(self, url: str, headers: dict[str, str], timeout: int) -> Reply:
        self.urls.append(url)
        return self.replies.pop(0)

    def fetch(self, url: str, method: str, headers: dict[str, str], data: Any, timeout: int) -> Reply:
        self.urls.append(url)
        self.sent = (method, headers, data)
        return self.replies.pop(0)


class Context:
    def __init__(self, replies: list[Reply]) -> None:
        self.request = Requests(replies)


class ScreenPage:
    def __init__(self, text: str) -> None:
        self.text = text

    def evaluate(self, script: str) -> str:
        return self.text


def ess_driver(text: str, replies: list[Reply]) -> PlaywrightDriver:
    d = PlaywrightDriver()
    d.page, d._context, d._url, d.poll_s = (
        ScreenPage(text),
        Context(replies),
        "https://abcd-dev2.fa.us6.oraclecloud.com/x",
        0,
    )
    return d


def status(s: str) -> Reply:
    return Reply(200, {"items": [{"RequestStatus": s}]})


@pytest.mark.parametrize(
    "text",
    [
        "Confirmation\nProcess 1234567 was submitted.\nOK",
        "Your process 1234567 has been submitted",
        "Request ID: 1234567",
    ],
)
def test_the_number_of_a_process_just_submitted_is_read_from_the_screen(text: str) -> None:
    assert ess_driver(text, []).process_number_on_screen() == "1234567"


def test_waiting_for_a_process_until_it_ends() -> None:
    d = ess_driver("Process 1234567 was submitted.", [status("WAIT"), status("RUNNING"), status("SUCCEEDED")])
    assert d.wait_job("last", 60) == "SUCCEEDED" and d.last_process == "1234567"
    url = d._context.request.urls[0]  # type: ignore[attr-defined]
    assert url.startswith("https://abcd-dev2.fa.us6.oraclecloud.com/fscmRestApi/resources/")
    assert "finder=ESSJobStatusRF;requestId=1234567" in url
    # a number given in the test is used as it is; a failed process says so
    assert ess_driver("", [status("ERROR")]).wait_job("7654321", 60) == "ERROR"


def test_a_process_that_cannot_be_waited_for_says_why() -> None:
    with pytest.raises(ValueError, match="No process number is shown on the screen"):
        ess_driver("Welcome", []).wait_job("last", 60)
    with pytest.raises(PermissionError, match="refused the status check of process 1234567 \\(HTTP 403\\)"):
        ess_driver("Process 1234567 was submitted.", [Reply(403)]).wait_job("last", 60)
    not_done = ess_driver("Process 1234567 was submitted.", [status("RUNNING")]).wait_job("last", 0)
    assert not_done.startswith("NOT FINISHED (still RUNNING")


# ---------------------------------------------------------------------- REST steps


def test_a_rest_step_calls_the_pod_with_the_signed_in_session() -> None:
    d = ess_driver("", [Reply(200, {"count": 1}), Reply(201, {"LocationId": 5})])
    assert d.api_call("GET", "/hcmRestApi/resources/11.13.18.05/locationsV2") == (200, {"count": 1})
    requests = d._context.request  # type: ignore[attr-defined]
    assert requests.urls[0] == "https://abcd-dev2.fa.us6.oraclecloud.com/hcmRestApi/resources/11.13.18.05/locationsV2"
    assert d.api_call("POST", "hcmRestApi/x", {"LocationName": "HQ"}) == (201, {"LocationId": 5})
    method, headers, body = requests.sent
    assert method == "POST" and body == {"LocationName": "HQ"} and "json" in headers["Content-Type"]
    with pytest.raises(ValueError, match="may only call the pod"):
        d.api_call("GET", "https://example.com/steal")


# ---------------------------------------------------------------------- Redwood date fields

DATE_PAGE = Path(__file__).parent / "fixtures" / "redwood_date.html"


def _date_driver(page: Any) -> PlaywrightDriver:
    d = PlaywrightDriver(settle_ms=300)
    d.page = page
    return d


def test_a_redwood_date_is_typed_box_by_box_and_read_back() -> None:
    pytest.importorskip("playwright")
    import os

    from playwright.sync_api import sync_playwright

    from quartermaster.domain.models import LocatorStrategy

    chromium = os.environ.get("QM_CHROMIUM_PATH") or (
        "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium)
        try:
            page = browser.new_page()
            page.goto(DATE_PAGE.as_uri())
            # All digits from the first box lose the ones typed while the field moves on.
            page.get_by_role("spinbutton").first.focus()
            page.keyboard.type("01011951", delay=50)
            assert page.locator("#full").inner_text() != "January 1, 1951"

            page.goto(DATE_PAGE.as_uri())
            _date_driver(page).fill(LocatorStrategy.ROLE, "group:Effective Start Date", "01/01/1951")
            assert page.locator("#full").inner_text() == "January 1, 1951"

            # A field that does not take the date says so at once, instead of a later check failing.
            page.goto(DATE_PAGE.as_uri() + "#year3")
            page.reload()
            with pytest.raises(ValueError, match="the date field shows 1/1/951 instead of 01/01/1951"):
                _date_driver(page).fill(LocatorStrategy.ROLE, "group:Effective Start Date", "01/01/1951")
        finally:
            browser.close()


# ---------------------------------------------------------------------- highlighting what a step uses

MARK_PAGE = """<!doctype html><button style="margin:80px"
  onclick="window.seen = !!document.getElementById('__qm_action')">Save</button>"""


def test_a_click_is_marked_in_red_unless_highlighting_is_off(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    import os

    from playwright.sync_api import sync_playwright

    from quartermaster.domain.models import LocatorStrategy

    chromium = os.environ.get("QM_CHROMIUM_PATH") or (
        "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium)
        try:
            for on in (True, False):
                page = browser.new_page()
                page.set_content(MARK_PAGE)
                d = PlaywrightDriver(settle_ms=200, highlight=on, evidence_dir=str(tmp_path / str(on)))
                d.page = page
                d.click(LocatorStrategy.ROLE, "button:Save")
                assert page.evaluate("window.seen") is on  # the red mark was on screen when it was clicked
                d.screenshot("after", highlight=(LocatorStrategy.ROLE, "button:Save"))
                assert page.evaluate("!document.getElementById('__qm_action')")  # not left in the picture
                page.close()
        finally:
            browser.close()
