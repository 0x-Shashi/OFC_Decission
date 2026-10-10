"""Sign in by hand (single sign-on, MFA): the session is kept in memory and reused by the runs."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from quartermaster.runner.credentials import MissingCredentialsError
from quartermaster.runner.playwright_driver import PlaywrightDriver
from quartermaster.runner.session import ENV, PREFIX, SessionError, decode_session, encode_session
from quartermaster.service.api import App
from quartermaster.service.signin import SignIn

POD = "abcd-dev2.fa.us6.oraclecloud.com"
COOKIES = [
    {"name": "JSESSIONID", "value": "s1", "domain": POD, "path": "/", "secure": True},
    {"name": "ORA_FND_SESSION", "value": "s2", "domain": ".oraclecloud.com", "path": "/"},
    {"name": "idp", "value": "x", "domain": "login.microsoftonline.com", "path": "/"},  # the sign-on's own
]


def test_only_the_pods_cookies_are_kept() -> None:
    text = encode_session(COOKIES, POD)
    assert "\n" not in text
    assert [c["name"] for c in decode_session(text)] == ["JSESSIONID", "ORA_FND_SESSION"]
    with pytest.raises(SessionError, match="no sign-in for the pod"):
        encode_session(COOKIES[2:], POD)
    with pytest.raises(SessionError, match="sign in by hand again"):
        decode_session("not a session")


def wait_for(sign: SignIn, status: str) -> dict[str, Any]:
    end = time.time() + 20
    while time.time() < end:
        if sign.view()["status"] == status:
            return sign.view()
        time.sleep(0.05)
    raise AssertionError(sign.view())


def test_signing_in_by_hand_keeps_the_session_in_memory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(ENV, raising=False)
    session = encode_session(COOKIES, POD)
    ok = SignIn(lambda: [sys.executable, "-c", f"print('Sign in...'); print({PREFIX + session!r})"])
    assert ok.view() == {"status": "none"}
    assert ok.start()["status"] == "waiting"
    assert wait_for(ok, "done")["at"]
    assert os.environ[ENV] == session
    assert not list(tmp_path.iterdir())  # nothing written
    assert ok.forget() == {"status": "none"} and ENV not in os.environ

    bad = SignIn(lambda: [sys.executable, "-c", "import sys; print('error: nobody finished signing in'); sys.exit(2)"])
    bad.start()
    assert wait_for(bad, "failed")["error"] == "nobody finished signing in"
    assert ENV not in os.environ


def test_a_pod_with_single_sign_on_is_ready_once_signed_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QM_FUSION_URL", f"https://{POD}")
    monkeypatch.delenv("QM_FUSION_USER", raising=False)
    monkeypatch.delenv("QM_FUSION_PASSWORD", raising=False)
    monkeypatch.delenv(ENV, raising=False)
    app = App(tests_root=tmp_path / "t", evidence_root=tmp_path / "e", data_dir=tmp_path / ".qm")
    status = app.handle("GET", "/api/status", b"")
    assert b'"ready": false' in status.body
    session = encode_session(COOKIES, POD)
    pod = app._env_key()  # the pod set in the terminal became the first client's environment
    monkeypatch.setenv(ENV, session)  # a session in the terminal is not taken for this pod
    assert b'"ready": false' in app.handle("GET", "/api/status", b"").body
    app.signin._sessions[pod] = session  # what Sign in by hand keeps, for this pod only
    body = app.handle("GET", "/api/status", b"").body
    assert b'"ready": true' in body and session.encode() not in body  # the page never sees the session
    assert app.run_environ()[ENV] == session


# ---------------------------------------------------------------- the browser side, with a stand-in browser


class Located:
    def __init__(self, n: int) -> None:
        self.n = n

    def locator(self, _: str) -> Located:
        return self

    def count(self) -> int:
        return self.n


class Page:
    def __init__(self, url_after: str, password_box: bool) -> None:
        self.url, self.url_after, self.password_box, self.video = "about:blank", url_after, password_box, None

    def set_default_timeout(self, _: int) -> None: ...

    def on(self, *_: Any) -> None: ...

    def goto(self, url: str, wait_until: str = "") -> None:
        self.url = self.url_after

    def locator(self, _: str) -> Located:
        return Located(1 if self.password_box else 0)

    def wait_for_timeout(self, ms: int) -> None:
        time.sleep(ms / 10000)


class Context:
    def __init__(self, page: Page) -> None:
        self.page, self.cookies_added = page, []

    def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        self.cookies_added = cookies

    def new_page(self) -> Page:
        return self.page

    def cookies(self) -> list[dict[str, Any]]:
        return COOKIES

    def close(self) -> None: ...


class Browser:
    def __init__(self, page: Page) -> None:
        self.contexts: list[Context] = []
        self.page = page

    def new_context(self, **_: Any) -> Context:
        self.contexts.append(Context(self.page))
        return self.contexts[-1]


def driver(page: Page, environ: dict[str, str]) -> tuple[PlaywrightDriver, Browser]:
    d = PlaywrightDriver(environ=environ, settle_ms=0)
    browser = Browser(page)
    d._browser, d._url = browser, f"https://{POD}/fscmUI/faces/FuseWelcome"
    return d, browser


def test_a_run_uses_the_sign_in_done_by_hand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(PlaywrightDriver, "_wait_signed_in", lambda self, timeout_s, steady_s=3: self._signed_in())
    session = encode_session(COOKIES, POD)
    d, browser = driver(Page(f"https://{POD}/fscmUI/faces/FuseWelcome", password_box=False), {ENV: session})
    d.login_as("")  # no user name or password needed
    assert [c["name"] for c in browser.contexts[0].cookies_added] == ["JSESSIONID", "ORA_FND_SESSION"]

    expired = Page("https://login.microsoftonline.com/oauth2/authorize", password_box=True)
    d, _ = driver(expired, {ENV: session})
    with pytest.raises(MissingCredentialsError, match="Sign in by hand and sign in again"):
        d.login_as("")


def test_the_sign_in_is_done_when_the_pod_shows_past_its_sign_in_page(monkeypatch: pytest.MonkeyPatch) -> None:
    page = Page(f"https://{POD}/fscmUI/faces/FuseWelcome", password_box=False)
    d, _ = driver(page, {})
    d._new_page()
    page.url = f"https://{POD}/oam/server/obrareq.cgi?login"
    assert not d._signed_in()
    page.url = f"https://{POD}/fscmUI/faces/FuseWelcome"
    assert d._signed_in() and d._wait_signed_in(5, steady_s=0.05)
    page.password_box = True
    assert not d._signed_in()
