"""Playwright driver for Oracle Fusion Cloud (Phase 1 work in progress).

Implemented: session open/close, strategy → Playwright locator mapping, basic actions,
screenshots, navigator-path navigation. Pending (see docs/PLAN.md §11 Phase 1): SSO/MFA
flows, ADF partial-page-render waits, ESS job polling via REST, REST calls with auth.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from quartermaster.domain.models import Environment, LocatorStrategy
from quartermaster.runner.credentials import MissingCredentialsError, persona_credentials, persona_key
from quartermaster.runner.session import ENV as SESSION_ENV
from quartermaster.runner.session import decode_session, encode_session

# The red box is a separate overlay on top of the page: an outline on the element itself is
# often clipped by Redwood field wrappers. Tiny elements (e.g. hidden radio inputs) box their label.
_DRAW_HIGHLIGHT = """el => {
  el.scrollIntoView({block: 'center', inline: 'nearest'});
  let r = el.getBoundingClientRect();
  if (r.width < 4 || r.height < 4) {
    const host = el.closest('label, [role=radiogroup], [role=group]') || el.parentElement;
    if (host) r = host.getBoundingClientRect();
  }
  const box = document.createElement('div');
  box.id = '__qm_highlight';
  Object.assign(box.style, {
    position: 'fixed', left: (r.left - 5) + 'px', top: (r.top - 5) + 'px',
    width: (r.width + 10) + 'px', height: (r.height + 10) + 'px', boxSizing: 'border-box',
    border: '3px solid #d32f2f', borderRadius: '6px', zIndex: '2147483647', pointerEvents: 'none',
  });
  document.body.appendChild(box);
}"""
_REMOVE_HIGHLIGHT = "() => document.getElementById('__qm_highlight')?.remove()"
# Shown in the live browser (and so in the video) just before a click or a value is entered: a red
# box round the item and, for a click, a red dot where it is clicked. It fades by itself and never
# takes the click (pointer-events: none).
_SHOW_ACTION = """(el, kind) => {
  el.scrollIntoView({block: 'center', inline: 'nearest'});
  let r = el.getBoundingClientRect();
  if (r.width < 4 || r.height < 4) {
    const host = el.closest('label, [role=radiogroup], [role=group]') || el.parentElement;
    if (host) r = host.getBoundingClientRect();
  }
  document.getElementById('__qm_action')?.remove();
  const layer = document.createElement('div');
  layer.id = '__qm_action';
  Object.assign(layer.style, {position: 'fixed', inset: '0', zIndex: '2147483647', pointerEvents: 'none',
    transition: 'opacity 0.4s', opacity: '1'});
  const box = document.createElement('div');
  Object.assign(box.style, {position: 'fixed', left: (r.left - 5) + 'px', top: (r.top - 5) + 'px',
    width: (r.width + 10) + 'px', height: (r.height + 10) + 'px', boxSizing: 'border-box',
    border: '3px solid #d32f2f', borderRadius: '6px', background: 'rgba(211, 47, 47, 0.08)',
    boxShadow: '0 0 0 4px rgba(211, 47, 47, 0.25)'});
  layer.appendChild(box);
  if (kind === 'click') {
    const dot = document.createElement('div');
    Object.assign(dot.style, {position: 'fixed', left: (r.left + r.width / 2 - 6) + 'px',
      top: (r.top + r.height / 2 - 6) + 'px', width: '12px', height: '12px', borderRadius: '50%',
      background: 'rgba(211, 47, 47, 0.5)', border: '2px solid rgba(211, 47, 47, 0.9)', boxSizing: 'border-box'});
    layer.appendChild(dot);
  }
  document.body.appendChild(layer);
  setTimeout(() => { layer.style.opacity = '0'; }, 1200);
  setTimeout(() => layer.remove(), 1700);
}"""


class PlaywrightDriver:
    """One browser per run; one fresh browser context (clean cookies) per persona login."""

    def __init__(
        self,
        *,
        headless: bool = True,
        evidence_dir: str = "evidence",
        environ: Mapping[str, str] | None = None,
        context_hook: Callable[[Any], None] | None = None,
        settle_ms: int = 15_000,
        record_video: bool = False,
        action_timeout_ms: int = 60_000,
        highlight: bool = True,
    ):
        self._headless = headless
        # Red marks on what each step uses: in the live browser before a click or an entry, and in the
        # screenshots. Off: no marks at all.
        self._highlight = highlight
        self._evidence = Path(evidence_dir)
        self._environ = environ
        self._context_hook = context_hook  # e.g. proxy/route setup, applied to every persona context
        self._settle_ms = settle_ms  # max wait for an element to appear, or for the network to go quiet
        # Evidence layout inside evidence_dir: screenshots/step-01.png ... and videos/*.webm
        self._record_video = record_video
        # Fusion dev pods can take well over Playwright's 30 s default to answer a click that
        # opens a new page, which shows up as random click timeouts.
        self._action_timeout_ms = action_timeout_ms
        self.videos: list[str] = []
        self.poll_s = 15.0  # how often a scheduled process's status is asked
        self.last_process = ""  # the number of the last scheduled process waited for
        # Optional pinned browser binary (e.g. a preinstalled Chromium in CI containers).
        self._executable = (os.environ if environ is None else environ).get("QM_CHROMIUM_PATH")
        self._url = ""
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self.page: Any = None
        self._inflight: set[Any] = set()

    # ------------------------------------------------------------------ lifecycle

    def open(self, env: Environment, persona: str) -> None:
        from playwright.sync_api import sync_playwright  # optional dependency

        self._url = env.url
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless, executable_path=self._executable)
        self.login_as(persona)

    def login_as(self, persona: str) -> None:
        env = os.environ if self._environ is None else self._environ
        key = persona_key(persona)
        own = bool(key and env.get(f"QM_FUSION_USER_{key}") and env.get(f"QM_FUSION_PASSWORD_{key}"))
        session = env.get(SESSION_ENV, "")
        if session and not own:  # a sign-in done by hand (single sign-on, MFA) is used when there is one
            self._new_page(decode_session(session))
            if self._wait_signed_in(30):
                self._settle()
                return
            if not (env.get("QM_FUSION_USER") and env.get("QM_FUSION_PASSWORD")):
                raise MissingCredentialsError(
                    "the sign-in done by hand has expired. In Settings, click Sign in by hand and sign in again."
                )
        user, password = persona_credentials(persona, self._environ)
        self._new_page()
        self.page.goto(self._url, wait_until="domcontentloaded")
        # Two sign-in pages exist: the classic Fusion one ("User ID" / "Sign In") and the
        # OCI IAM (IDCS) one ("Username" / "Next"). Single sign-on with MFA: see sign_in_by_hand.
        p = self.page
        user_box = p.get_by_label("User ID", exact=True).or_(p.get_by_label("Username", exact=True))
        user_box.wait_for()
        user_box.fill(user)
        p.get_by_label("Password", exact=True).fill(password)
        sign_in = p.get_by_role("button", name="Sign In", exact=True)
        sign_in.or_(p.get_by_role("button", name="Next", exact=True)).click()
        # IDCS posts back to the pod through redirects; wait until we are on the pod again.
        pod_host = urlparse(self._url).hostname
        p.wait_for_url(lambda u: urlparse(u).hostname == pod_host, timeout=120_000)
        self._settle()

    def _new_page(self, cookies: list[dict[str, Any]] | None = None) -> None:
        """A fresh browser context (clean cookies, or the given ones) with one page."""
        if self._context is not None:
            self._context.close()
        size = {"width": 1600, "height": 1000}
        video: dict[str, Any] = {}
        if self._record_video:
            video = {"record_video_dir": str(self._evidence / "videos"), "record_video_size": size}
        self._context = self._browser.new_context(viewport=size, **video)
        if cookies:
            self._context.add_cookies(cookies)
        if self._context_hook is not None:
            self._context_hook(self._context)
        self.page = self._context.new_page()
        self.page.set_default_timeout(self._action_timeout_ms)
        if self._record_video and self.page.video is not None:
            # One video per sign-in; a persona switch starts a new one. Written when the context closes.
            self.videos.append(str(self.page.video.path()))
        self._track_requests(self.page)
        if cookies:
            with suppress(Exception):  # a sign-on page that never finishes loading is checked below
                self.page.goto(self._url, wait_until="domcontentloaded")

    def _signed_in(self) -> bool:
        """On the pod, past any sign-in page."""
        with suppress(Exception):  # the page was navigating
            url = str(self.page.url)
            if urlparse(url).hostname != urlparse(self._url).hostname or re.search(r"sign-?in|login", url, re.I):
                return False
            return int(self.page.locator("input[type=password]").locator("visible=true").count()) == 0
        return False

    def _wait_signed_in(self, timeout_s: float, steady_s: float = 3) -> bool:
        """Wait until the pod shows past its sign-in for `steady_s` in a row (single sign-on passes
        through the pod several times on its way)."""
        deadline = time.monotonic() + timeout_s
        since: float | None = None
        while time.monotonic() < deadline:
            if self._signed_in():
                since = since or time.monotonic()
                if time.monotonic() - since >= steady_s:
                    return True
            else:
                since = None
            self.page.wait_for_timeout(500)
        return False

    def sign_in_by_hand(self, env: Environment, timeout_s: float = 600) -> str:
        """Open a browser on the pod for a person to sign in (single sign-on, MFA), then return the
        pod's session, packed into one line (see runner.session). Nothing is written to disk."""
        from playwright.sync_api import sync_playwright  # optional dependency

        self._url = env.url
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless, executable_path=self._executable)
        self._new_page()
        self.page.goto(self._url, wait_until="domcontentloaded")
        if not self._wait_signed_in(timeout_s):
            raise TimeoutError(f"nobody finished signing in within {int(timeout_s / 60)} minutes")
        return encode_session(self._context.cookies(), urlparse(self._url).hostname or "")

    def navigator_pages(self) -> list[str]:
        """The names of the pages in the pod's Navigator, for pod discovery. Read only: it opens the Navigator and
        its folded groups, and nothing else (see runner/discovery.py)."""
        from quartermaster.runner.discovery import scan_navigator

        def open_navigator() -> bool:
            for strategy, value in (
                (LocatorStrategy.ROLE, "link:Navigator"),
                (LocatorStrategy.CSS, "a[title='Navigator']"),
            ):
                if self.count(strategy, value):
                    self.click(strategy, value)
                    return True
            return False

        return scan_navigator(self.page, open_navigator)

    def _track_requests(self, page: Any) -> None:
        self._inflight = set()
        # Handlers must be plain functions: Playwright sets an attribute on each one, which
        # built-ins like set.discard and bound methods do not allow.
        page.on("request", lambda r: self._inflight.add(r) if r.url.startswith("http") else None)
        page.on("requestfinished", lambda r: self._inflight.discard(r))
        page.on("requestfailed", lambda r: self._inflight.discard(r))

    def _settle(self, quiet_ms: int = 500) -> None:
        """Wait until no http(s) request has been in flight for `quiet_ms`, up to the settle time.

        Playwright's "networkidle" never fires on Redwood pages: they start blob: requests
        (web workers) that never finish. Those are ignored here, and a page that stays busy
        (e.g. long polling) just costs the settle time instead of failing the step.
        """
        deadline = time.monotonic() + self._settle_ms / 1000
        quiet_since: float | None = None
        while time.monotonic() < deadline:
            if self._inflight:
                quiet_since = None
            else:
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic() - quiet_since >= quiet_ms / 1000:
                    return
            self.page.wait_for_timeout(100)  # also lets Playwright deliver the request events

    def close(self) -> None:
        if self._context is not None:
            with suppress(Exception):
                self._context.close()  # finishes writing any video files
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()
        self._browser = self._pw = self._context = self.page = None

    # ------------------------------------------------------------------ locators

    def _locator(self, strategy: LocatorStrategy, value: str) -> Any:
        p = self.page
        if strategy is LocatorStrategy.LABEL:
            return p.get_by_label(value, exact=True)
        if strategy is LocatorStrategy.ROLE:
            # "button:Submit" -> get_by_role("button", name="Submit")
            role, _, name = value.partition(":")
            return p.get_by_role(role.strip(), name=name.strip() or None, exact=bool(name))
        if strategy is LocatorStrategy.TEST_ID:
            return p.get_by_test_id(value)
        if strategy is LocatorStrategy.TEXT:
            # An icon button has no text, only a tooltip (title), e.g. ADF's "Search: Name" next to a
            # list field. The recorder saves that tooltip as its text, so when no text on the page
            # matches, the tooltip is looked for instead. Text that is on the page still wins.
            by_text = p.get_by_text(value, exact=True)
            if by_text.count() == 0:
                by_title = p.get_by_title(value, exact=True)
                if by_title.count():
                    return by_title
            return by_text
        if strategy is LocatorStrategy.CSS:
            return p.locator(value)
        if strategy is LocatorStrategy.XPATH:
            return p.locator(f"xpath={value}")
        raise ValueError(f"unsupported strategy {strategy}")

    def count(self, strategy: LocatorStrategy, value: str) -> int:
        # ADF pages render after the load event (partial page rendering), so give the element a
        # moment to appear before counting. Zero matches still falls through to the next strategy.
        waiting = self._locator(strategy, value)
        if strategy is LocatorStrategy.TEXT:  # the text, or a tooltip by that name (see _locator)
            waiting = self.page.get_by_text(value, exact=True).or_(self.page.get_by_title(value, exact=True))
        with suppress(Exception):  # playwright TimeoutError; the count below reports the 0
            waiting.first.wait_for(state="attached", timeout=self._settle_ms)
        return int(self._locator(strategy, value).count())

    # ------------------------------------------------------------------ actions

    def navigate(self, path: str, timeout_ms: float | None = None) -> None:
        """Open a page via the Navigator, e.g. 'Payables > Invoices'.

        `timeout_ms` limits each click (default: the action timeout), so a path that is not in
        this Navigator fails quickly when an AI is trying it.
        """
        # Classic pages show the Navigator as the ☰ icon with a "Navigator" title. Clicking it
        # again would close the panel, so it is only clicked when the panel is not open already.
        p = self.page
        if p.locator("div.navmenu-header").locator("visible=true").count() == 0:
            p.get_by_role("link", name="Navigator", exact=True).first.click(timeout=timeout_ms)
        with suppress(Exception):  # playwright TimeoutError: no grouped Navigator on this page
            p.locator("div.navmenu-header").first.wait_for(timeout=min(self._settle_ms, timeout_ms or self._settle_ms))
        parts = [part.strip() for part in path.split(">")]
        for part, child in zip(parts, parts[1:], strict=False):
            # Groups are headers that expand in place, and the Navigator remembers which are
            # open. Home-page springboard links with the same names sit underneath the panel.
            child_link = p.get_by_role("link", name=child, exact=True).locator("visible=true")
            header = p.locator(f"div.navmenu-header[title='{part}']")
            if header.count() == 1:
                if child_link.count() == 0:
                    header.click(timeout=timeout_ms)
            else:
                p.get_by_role("link", name=part, exact=True).locator("visible=true").first.click(timeout=timeout_ms)
        p.get_by_role("link", name=parts[-1], exact=True).locator("visible=true").first.click(timeout=timeout_ms)
        self._settle()

    def _show(self, loc: Any, kind: str) -> None:
        """Mark the item a step is about to use, so whoever watches (or the video) sees it."""
        if not self._highlight:
            return
        with suppress(Exception):  # never let the marker stop the step
            loc.first.evaluate(_SHOW_ACTION, kind, timeout=2_000)
            self.page.wait_for_timeout(400)  # long enough to see before the page changes

    def click(self, strategy: LocatorStrategy, value: str) -> None:
        loc = self._locator(strategy, value)
        self._show(loc, "click")
        loc.click()
        self._settle()

    def fill(self, strategy: LocatorStrategy, value: str, text: str) -> None:
        loc = self._locator(strategy, value)
        self._show(loc, "fill")
        if loc.get_attribute("role") == "group" and loc.get_by_role("spinbutton").count():
            self._fill_date(loc, text)
        else:
            loc.fill(text)
        self._settle()

    def _fill_date(self, group: Any, text: str) -> None:
        """Redwood date fields are month / day / year spinbuttons. Each part is typed into its own
        box: typing all the digits from the first box and letting the field move on by itself
        loses digits on a busy pod (1951 became 951, or 1). What the field then shows is read
        back, and typed once more if it is wrong."""
        boxes = group.get_by_role("spinbutton")
        parts = re.findall(r"\d+", text)
        if len(parts) != boxes.count():  # not one number per box: type the digits from the first box
            boxes.first.focus()
            self.page.keyboard.type(re.sub(r"\D", "", text), delay=50)
            self.page.keyboard.press("Tab")
            return
        shown: list[str] = []
        for _ in range(2):
            for i, part in enumerate(parts):
                boxes.nth(i).focus()
                self.page.keyboard.type(part, delay=80)
                self.page.wait_for_timeout(200)  # let the box take the value before the next one
            self.page.keyboard.press("Tab")
            self._settle()
            shown = [str(boxes.nth(i).get_attribute("aria-valuenow") or "") for i in range(len(parts))]
            if not any(shown) or all(s.isdigit() and int(s) == int(p) for s, p in zip(shown, parts, strict=True)):
                return  # right, or a field that does not say its value (nothing to compare)
        raise ValueError(f"the date field shows {'/'.join(v or '?' for v in shown)} instead of {text}")

    def select(self, strategy: LocatorStrategy, value: str, option: str, pick: str | None = None) -> None:
        """Choose `option`; in type-ahead lists, type `option` and choose the suggestion `pick` (default: option)."""
        loc = self._locator(strategy, value)
        self._show(loc, "fill")
        if loc.evaluate("el => el.tagName.toLowerCase()") == "select":
            loc.select_option(label=pick or option)
        else:
            # ADF/Redwood choice lists are inputs with a dropdown. Redwood only searches on real
            # key presses, so type the value, then click the best matching suggestion.
            loc.click()
            loc.fill("")
            loc.press_sequentially(option, delay=100)
            self._settle()
            suggestions = self._suggestions(loc)
            try:
                suggestions.first.wait_for(timeout=self._settle_ms)
            except Exception:  # playwright TimeoutError: no suggestion list, e.g. a plain ADF choice
                loc.press("Enter")
                self._settle()
                return
            texts = suggestions.all_inner_texts()
            best = _best_option(texts, pick or option)
            if best is None:
                # Never fall back to the first suggestion: that silently picks a wrong value.
                offered = "; ".join(" ".join(t.split()) for t in texts[:5])
                raise ValueError(f"no suggestion matches {pick or option!r}; offered: {offered}")
            suggestions.nth(best).click()
        self._settle()

    def _suggestions(self, field: Any) -> Any:
        """Visible suggestions of a type-ahead list.

        Redwood lists show suggestions as rows of a grid in the dropdown named by the field's
        aria-controls; other lists use role=option.
        """
        controls = field.get_attribute("aria-controls")
        if controls:
            drop = self.page.locator(f'[id="{controls}"]')
            return drop.locator("[role=row], [role=option], tbody tr").locator("visible=true")
        return self.page.get_by_role("option").locator("visible=true")

    def text_of(self, strategy: LocatorStrategy, value: str) -> str:
        loc = self._locator(strategy, value)
        if loc.evaluate("el => ['input', 'textarea'].includes(el.tagName.toLowerCase())"):
            return str(loc.input_value())  # e.g. a field filled in automatically
        return str(loc.inner_text())

    def wait_job(self, job_name: str, timeout_s: float) -> str:
        """Wait for a scheduled process (ESS job) to finish and return its final status, e.g.
        SUCCEEDED, WARNING or ERROR.

        `job_name` is the process number, or anything else (the process name, or "last") to use the
        number Oracle shows when a process is submitted ("Process 1234567 was submitted"). The
        status comes from Fusion's ERP integration service (ESSJobStatusRF), asked with the browser's
        signed-in session, every `poll_s` seconds until the process ends or `timeout_s` passes.
        """
        number = job_name.strip() if job_name.strip().isdigit() else self.process_number_on_screen()
        if not number:
            raise ValueError(
                "No process number is shown on the screen. Submit the process first: Oracle then shows "
                "its number (for example 'Process 1234567 was submitted')."
            )
        self.last_process = number
        deadline = time.monotonic() + timeout_s
        status = ""
        while True:
            status = self._process_status(number)
            if status in _PROCESS_DONE:
                return status
            if time.monotonic() >= deadline:
                return f"NOT FINISHED (still {status or 'unknown'} after {int(timeout_s)} s, process {number})"
            time.sleep(self.poll_s)

    def process_number_on_screen(self) -> str:
        """The number of the process just submitted, as Oracle shows it in its confirmation."""
        try:
            text = str(self.page.evaluate("() => document.body.innerText"))
        except Exception:  # the page was navigating
            return ""
        for pattern in _PROCESS_NUMBER:
            found = pattern.findall(text)
            if found:
                return str(found[-1])
        return ""

    def _process_status(self, number: str) -> str:
        parts = urlparse(self._url)
        url = (
            f"{parts.scheme}://{parts.netloc}/fscmRestApi/resources/11.13.18.05/erpintegrations"
            f"?finder=ESSJobStatusRF;requestId={number}&onlyData=true"
        )
        reply = self._context.request.get(url, headers={"Accept": "application/json"}, timeout=60_000)
        if reply.status in (401, 403):
            raise PermissionError(
                f"The pod refused the status check of process {number} (HTTP {reply.status}). The test user "
                "needs access to the ERP integration REST service (erpintegrations)."
            )
        if not reply.ok:
            raise RuntimeError(f"The status check of process {number} failed (HTTP {reply.status}).")
        items = (reply.json() or {}).get("items") or []
        return str(items[0].get("RequestStatus") or "").upper() if items else ""

    def api_call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """Call a REST service of the pod with the browser's signed-in session: the HTTP status and
        the JSON reply (None when the reply is not JSON)."""
        pod = urlparse(self._url)
        asked = urlparse(path)
        if asked.netloc and asked.netloc != pod.netloc:
            raise ValueError(f"a REST step may only call the pod ({pod.netloc}), not {asked.netloc}")
        url = path if asked.netloc else f"{pod.scheme}://{pod.netloc}/{path.lstrip('/')}"
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/vnd.oracle.adf.resourceitem+json"
        reply = self._context.request.fetch(url, method=method, headers=headers, data=body, timeout=60_000)
        try:
            data = reply.json()
        except Exception:  # an HTML error page, or an empty reply to a DELETE
            data = None
        return int(reply.status), data

    def screenshot(self, name: str, highlight: tuple[LocatorStrategy, str] | None = None) -> str | None:
        """Save what the user would see (the browser window) with the step's element boxed in red."""
        if self.page is None:
            return None
        folder = self._evidence / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.png"
        with suppress(Exception):  # the live marker of the step (see _show) is not part of the picture
            self.page.evaluate("() => document.getElementById('__qm_action')?.remove()")
        if highlight is not None and self._highlight:
            with suppress(Exception):  # the element may be gone, e.g. after a click that navigated
                self._locator(*highlight).first.evaluate(_DRAW_HIGHLIGHT, timeout=2_000)
        try:
            try:
                self.page.screenshot(path=str(path), timeout=30_000)  # a busy page gives up sooner than an action
            except Exception:
                # Usually a page still being built after a click: let it finish loading, then try once more.
                with suppress(Exception):
                    self._settle()
                self.page.screenshot(path=str(path), timeout=20_000)
        finally:
            with suppress(Exception):  # never leave the red box behind for the next screenshot
                self.page.evaluate(_REMOVE_HIGHLIGHT)
        return str(path)


# Final states of a scheduled process; anything else (WAIT, READY, RUNNING, BLOCKED...) is not done yet.
_PROCESS_DONE = {"SUCCEEDED", "WARNING", "ERROR", "CANCELLED", "CANCELED", "EXPIRED", "ERROR_MANUAL_RECOVERY"}
# How Oracle shows the number of a process just submitted: "Process 1234567 was submitted.",
# "Your process 1234567 has been submitted", "Request ID: 1234567".
_PROCESS_NUMBER = [
    re.compile(r"\bprocess\s+(\d{3,})\s+(?:was|has been)\s+submitted", re.I),
    re.compile(r"\brequest\s*id\s*[:#]?\s*(\d{3,})", re.I),
    re.compile(r"\bprocess\s+id\s*[:#]?\s*(\d{3,})", re.I),
]


def _best_option(texts: list[str], wanted: str) -> int | None:
    """Pick a suggestion: exact text first, then one starting with the value, then one containing it.

    "Common Set" must choose "Common Set (seeded)" over "BATA US GRADE COMMON SET", and
    "Active" must not choose "Inactive".
    """
    norm = [" ".join(t.split()).casefold() for t in texts]
    w = " ".join(wanted.split()).casefold()
    for test in (lambda t: t == w, lambda t: t.startswith(w), lambda t: w in t):
        for i, t in enumerate(norm):
            if test(t):
                return i
    return None
