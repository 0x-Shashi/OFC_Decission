"""A small runtime for the Playwright tests exported from Quartermaster. It needs only Playwright and pytest.

Everything here is plain Playwright for Python, written to be read and changed. It does what Quartermaster's own runner
does for Oracle Fusion Cloud pages, so an exported test behaves the same way:

- every item is found by a list of ways in order, and the first way that finds exactly ONE element is used (zero matches
  and several matches are both skipped: clicking the wrong one of two "Submit" buttons is worse than failing);
- the Navigator is used to open a page ("Workforce Structures > Locations");
- Redwood date boxes and type-ahead lists are filled the way a person fills them;
- REST calls use the browser's signed-in session, and values can be kept from a reply for later steps (`${saved}`);
- a scheduled process (ESS) can be waited for;
- it refuses a pod that looks like production.

Not included: Quartermaster's retries, its suggested fixes for renamed items, Word evidence documents and schedules.
Failures save a screenshot in `evidence/`.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import random
import re
import string
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE")
_PLACEHOLDER = re.compile(r"\$\{((?:env:)?[A-Za-z_][A-Za-z0-9_]*)\}")
_NONPROD_HOST = re.compile(r"-(dev|test|stage|stg|uat|sit|qa)\d*\.", re.IGNORECASE)
_PART = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
_PROCESS_DONE = {"SUCCEEDED", "WARNING", "ERROR", "CANCELLED", "CANCELED", "EXPIRED", "ERROR_MANUAL_RECOVERY"}
_PROCESS_NUMBER = [
    re.compile(r"\bprocess\s+(\d{3,})\s+(?:was|has been)\s+submitted", re.I),
    re.compile(r"\brequest\s*id\s*[:#]?\s*(\d{3,})", re.I),
    re.compile(r"\bprocess\s+id\s*[:#]?\s*(\d{3,})", re.I),
]


class StepFailure(AssertionError):
    """A step did not do what the test says."""


# ---------------------------------------------------------------------------------------------- plain helpers


def persona_key(persona: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", persona.upper()).strip("_")


def credentials(persona: str = "", environ: dict[str, str] | None = None) -> tuple[str, str]:
    """(user, password) of a persona: QM_FUSION_USER_<PERSONA> and ..._PASSWORD_<PERSONA>, else the default user."""
    env = os.environ if environ is None else environ
    key = persona_key(persona)
    for suffix in ([f"_{key}"] if key else []) + [""]:
        user, password = env.get(f"QM_FUSION_USER{suffix}"), env.get(f"QM_FUSION_PASSWORD{suffix}")
        if user and password:
            return user, password
    wanted = f"QM_FUSION_USER_{key} and QM_FUSION_PASSWORD_{key}" if key else "QM_FUSION_USER and QM_FUSION_PASSWORD"
    raise StepFailure(f"no sign-in for persona {persona!r}: set {wanted}")


def assert_test_pod(url: str, environ: dict[str, str] | None = None) -> None:
    """Refuse anything but a test pod: https, and a name with dev, test, stage... in it unless the host is allowed."""
    env = os.environ if environ is None else environ
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        raise StepFailure(f"QM_FUSION_URL must be an https address, got {url!r}")
    allowed = {h.strip().lower() for h in env.get("QM_FUSION_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if allowed:
        if host not in allowed:
            raise StepFailure(f"{host} is not in QM_FUSION_ALLOWED_HOSTS")
        return
    if host.endswith("oraclecloud.com") and not _NONPROD_HOST.search(host):
        raise StepFailure(
            f"{host} looks like a production pod (no dev, test or stage in its name). If it is a test pod, "
            "set QM_FUSION_ALLOWED_HOSTS to its host name."
        )


def generate_values(rules: dict[str, dict[str, Any]], run_id: str, today: date | None = None) -> dict[str, str]:
    """Values made fresh for a run: `unique` (letters and digits), `date` (today plus some days), `number`, `choice`.
    The same run id always gives the same values, so a run is easy to read back."""
    day = today or date.today()
    out: dict[str, str] = {}
    for name, rule in rules.items():
        rng = random.Random(hashlib.sha256(f"{run_id}/{name}".encode()).digest())
        if rule.get("unique") is not None:
            alphabet = string.ascii_uppercase + string.digits
            body = "".join(rng.choice(alphabet) for _ in range(int(rule["unique"])))
            out[name] = f"{rule.get('prefix', '')}{body}{rule.get('suffix', '')}"
        elif rule.get("date") is not None:
            out[name] = (day + timedelta(days=int(rule.get("plus_days", 0)))).strftime(rule.get("format", "%Y-%m-%d"))
        elif rule.get("number") is not None:
            out[name] = str(rng.randint(int(rule["number"][0]), int(rule["number"][1])))
        else:
            out[name] = str(rng.choice(rule["choice"]))
    return out


def pod_identity(url: str, environ: dict[str, str] | None = None) -> tuple[str, str]:
    """(the pod's name, its kind: DEV, TEST or STAGE or empty). QM_ENV_NAME and QM_FUSION_KIND say so when set;
    otherwise the name is the first part of the host (abcd-dev2) and the kind is read from it."""
    env = os.environ if environ is None else environ
    host = (urlparse(url).hostname or "").lower()
    name = env.get("QM_ENV_NAME") or host.split(".")[0]
    kind = (env.get("QM_FUSION_KIND") or "").upper()
    if not kind:
        word = re.search(r"-(dev|test|stage|stg|uat|sit|qa)\d*(?:\.|$)", host)
        kind = {"dev": "DEV", "stg": "STAGE", "stage": "STAGE", "test": "TEST"}.get(word.group(1) if word else "", "")
    return name, kind


def json_get(data: Any, path: str) -> Any:
    """A value in a JSON reply by its path, for example items[0].PersonNumber. Raises KeyError when absent."""
    here = data
    for name, index in _PART.findall(path.strip()):
        if name:
            if not isinstance(here, dict) or name not in here:
                raise KeyError(path)
            here = here[name]
        else:
            i = int(index)
            if not isinstance(here, list) or i >= len(here):
                raise KeyError(path)
            here = here[i]
    return here


def as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def best_option(texts: list[str], wanted: str) -> int | None:
    """Pick a suggestion: exact text first, then one starting with the value, then one containing it."""
    norm = [" ".join(t.split()).casefold() for t in texts]
    w = " ".join(wanted.split()).casefold()
    for test in (lambda t: t == w, lambda t: t.startswith(w), lambda t: w in t):
        for i, t in enumerate(norm):
            if test(t):
                return i
    return None


# ---------------------------------------------------------------------------------------------- the runtime


class Fusion:
    """One test's browser, its test data and what its steps saved. The `fusion` fixture makes one per test."""

    def __init__(self, browser: Any, base_url: str, name: str = "test", evidence: str | Path = "evidence") -> None:
        assert_test_pod(base_url)
        self.browser = browser
        self.url = base_url
        self.name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        self.evidence = Path(evidence)
        self.run_id = uuid.uuid4().hex[:8].upper()
        self.data: dict[str, str] = {}
        self.generated: dict[str, str] = {}
        self._pod_only: set[str] = set()  # names that only some pods give
        self.saved: dict[str, str] = {}
        self.settle_ms = 15_000
        self.poll_s = 15.0
        self.context: Any = None
        self.page: Any = None
        self._inflight: set[Any] = set()

    # ------------------------------------------------------------------ test data

    def use_data(
        self,
        data: dict[str, str],
        pods: dict[str, dict[str, str]] | None = None,
        generate: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """The test's data as it is on this pod (a pod's own values by its name or kind, on top of `data`), and
        the values made fresh for this run."""
        name, kind = pod_identity(self.url)
        mine: dict[str, str] = {}
        for wanted in (kind, name):  # the kind first, the pod's own name on top
            for pod, block in (pods or {}).items():
                if wanted and pod.casefold() == wanted.casefold():
                    mine.update(block)
        self.data.update({**data, **mine})
        self._pod_only = {k for block in (pods or {}).values() for k in block} - set(data)
        self.generated = generate_values(generate or {}, self.run_id)
        self._pod = f"{name} ({kind or 'kind not known'})"

    # ------------------------------------------------------------------ text with ${...}

    def render(self, text: str | None) -> str:
        """The text with ${name} filled in from the test data, what steps saved, ${RUN_ID} and ${env:NAME}."""
        if text is None:
            return ""

        def fill(m: re.Match[str]) -> str:
            name = m.group(1)
            if name.startswith("env:"):
                var = name[4:]
                if var not in os.environ:
                    raise StepFailure(f"set the environment variable {var} (the test types a masked value)")
                return os.environ[var]
            for source in (self.data, self.generated, self.saved, {"RUN_ID": self.run_id}):
                if name in source:
                    return str(source[name])
            if name in self._pod_only:
                raise StepFailure(
                    f"No test data for {name} on {self._pod}. Other pods have a value, this one does not: add it "
                    "to PODS in the test under this pod's name or kind"
                )
            return m.group(0)

        return _PLACEHOLDER.sub(fill, text)

    def unsaved(self, text: str) -> list[str]:
        return [n for n in _PLACEHOLDER.findall(self.render(text)) if not n.startswith("env:")]

    def _render_all(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.render(value)
        if isinstance(value, dict):
            return {k: self._render_all(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._render_all(v) for v in value]
        return value

    # ------------------------------------------------------------------ sign in

    def login(self, persona: str = "") -> None:
        """Sign in as a persona in a fresh browser window (clean cookies). QM_STORAGE_STATE, a file saved with
        `context.storage_state()`, is used instead of a password when set (single sign-on)."""
        self._new_page(os.environ.get("QM_STORAGE_STATE") or None)
        p = self.page
        if os.environ.get("QM_STORAGE_STATE"):
            p.goto(self.url, wait_until="domcontentloaded")
            self._settle()
            return
        user, password = credentials(persona)
        p.goto(self.url, wait_until="domcontentloaded")
        # Two sign-in pages exist: the classic one ("User ID" / "Sign In") and the OCI IAM one ("Username" / "Next").
        user_box = p.get_by_label("User ID", exact=True).or_(p.get_by_label("Username", exact=True))
        user_box.wait_for()
        user_box.fill(user)
        p.get_by_label("Password", exact=True).fill(password)
        sign_in = p.get_by_role("button", name="Sign In", exact=True)
        sign_in.or_(p.get_by_role("button", name="Next", exact=True)).click()
        host = urlparse(self.url).hostname
        p.wait_for_url(lambda u: urlparse(u).hostname == host, timeout=120_000)
        self._settle()

    login_as = login  # a step "login_as persona" is the same thing

    def _new_page(self, storage_state: str | None = None) -> None:
        if self.context is not None:
            self.context.close()
        kwargs: dict[str, Any] = {"viewport": {"width": 1600, "height": 1000}}
        if storage_state:
            kwargs["storage_state"] = storage_state
        self.context = self.browser.new_context(**kwargs)
        self.page = self.context.new_page()
        self.page.set_default_timeout(60_000)
        self._inflight = set()
        # plain functions: Playwright sets an attribute on each handler
        self.page.on("request", lambda r: self._inflight.add(r) if r.url.startswith("http") else None)
        self.page.on("requestfinished", lambda r: self._inflight.discard(r))
        self.page.on("requestfailed", lambda r: self._inflight.discard(r))

    def close(self) -> None:
        with contextlib.suppress(Exception):
            if self.context is not None:
                self.context.close()
        self.context = self.page = None

    def _settle(self, quiet_ms: int = 500) -> None:
        """Wait until no http(s) request has been in flight for `quiet_ms` (Redwood never reaches "networkidle")."""
        deadline = time.monotonic() + self.settle_ms / 1000
        quiet_since: float | None = None
        while time.monotonic() < deadline:
            if self._inflight:
                quiet_since = None
            else:
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic() - quiet_since >= quiet_ms / 1000:
                    return
            self.page.wait_for_timeout(100)

    # ------------------------------------------------------------------ finding items

    def _locator(self, way: str, value: str) -> Any:
        p = self.page
        if way == "label":
            return p.get_by_label(value, exact=True)
        if way == "role":  # "button:Submit" -> get_by_role("button", name="Submit")
            role, _, name = value.partition(":")
            return p.get_by_role(role.strip(), name=name.strip() or None, exact=bool(name))
        if way == "test_id":
            return p.get_by_test_id(value)
        if way == "text":  # an icon button has only a tooltip: look for it when no text matches
            by_text = p.get_by_text(value, exact=True)
            if by_text.count() == 0:
                by_title = p.get_by_title(value, exact=True)
                if by_title.count():
                    return by_title
            return by_text
        if way == "css":
            return p.locator(value)
        if way == "xpath":
            return p.locator(f"xpath={value}")
        raise ValueError(f"unknown way to find an item: {way}")

    def _count(self, way: str, value: str) -> int:
        waiting = self._locator(way, value)
        if way == "text":
            waiting = self.page.get_by_text(value, exact=True).or_(self.page.get_by_title(value, exact=True))
        with contextlib.suppress(Exception):  # a timeout: the count below says 0
            waiting.first.wait_for(state="attached", timeout=self.settle_ms)
        return int(self._locator(way, value).count())

    def find(self, ways: list[tuple[str, str]]) -> Any:
        """The element that the first way finding exactly one element points to. Raises StepFailure naming every try."""
        tried: list[str] = []
        for way, value in ways:
            value = self.render(value)
            n = self._count(way, value)
            if n == 1:
                return self._locator(way, value)
            tried.append(f"{way}={value!r} matched {n}")
        raise StepFailure("could not find the item: " + "; ".join(tried))

    # ------------------------------------------------------------------ steps

    def navigate(self, path: str) -> None:
        """Open a page through the Navigator, for example 'Payables > Invoices'."""
        p = self.page
        path = self.render(path)
        if p.locator("div.navmenu-header").locator("visible=true").count() == 0:
            p.get_by_role("link", name="Navigator", exact=True).first.click()
        with contextlib.suppress(Exception):
            p.locator("div.navmenu-header").first.wait_for(timeout=self.settle_ms)
        parts = [part.strip() for part in path.split(">")]
        for part, child in zip(parts, parts[1:], strict=False):
            child_link = p.get_by_role("link", name=child, exact=True).locator("visible=true")
            header = p.locator(f"div.navmenu-header[title='{part}']")
            if header.count() == 1:
                if child_link.count() == 0:
                    header.click()
            else:
                p.get_by_role("link", name=part, exact=True).locator("visible=true").first.click()
        p.get_by_role("link", name=parts[-1], exact=True).locator("visible=true").first.click()
        self._settle()

    def click(self, ways: list[tuple[str, str]]) -> None:
        self.find(ways).click()
        self._settle()

    def fill(self, ways: list[tuple[str, str]], text: str) -> None:
        loc = self.find(ways)
        text = self.render(text)
        if loc.get_attribute("role") == "group" and loc.get_by_role("spinbutton").count():
            self._fill_date(loc, text)
        else:
            loc.fill(text)
        self._settle()

    def _fill_date(self, group: Any, text: str) -> None:
        """Redwood dates are month / day / year boxes: each part goes into its own box, then what shows is read back."""
        boxes = group.get_by_role("spinbutton")
        parts = re.findall(r"\d+", text)
        if len(parts) != boxes.count():
            boxes.first.focus()
            self.page.keyboard.type(re.sub(r"\D", "", text), delay=50)
            self.page.keyboard.press("Tab")
            return
        shown: list[str] = []
        for _ in range(2):
            for i, part in enumerate(parts):
                boxes.nth(i).focus()
                self.page.keyboard.type(part, delay=80)
                self.page.wait_for_timeout(200)
            self.page.keyboard.press("Tab")
            self._settle()
            shown = [str(boxes.nth(i).get_attribute("aria-valuenow") or "") for i in range(len(parts))]
            if not any(shown) or all(s.isdigit() and int(s) == int(p) for s, p in zip(shown, parts, strict=True)):
                return
        raise StepFailure(f"the date field shows {'/'.join(v or '?' for v in shown)} instead of {text}")

    def select(self, ways: list[tuple[str, str]], option: str, pick: str | None = None) -> None:
        """Choose `option`; in a type-ahead list, type it and choose the suggestion `pick` (default: option)."""
        loc = self.find(ways)
        option = self.render(option)
        pick = self.render(pick) if pick else None
        if loc.evaluate("el => el.tagName.toLowerCase()") == "select":
            loc.select_option(label=pick or option)
        else:
            loc.click()
            loc.fill("")
            loc.press_sequentially(option, delay=100)
            self._settle()
            controls = loc.get_attribute("aria-controls")
            if controls:
                drop = self.page.locator(f'[id="{controls}"]')
                suggestions = drop.locator("[role=row], [role=option], tbody tr").locator("visible=true")
            else:
                suggestions = self.page.get_by_role("option").locator("visible=true")
            try:
                suggestions.first.wait_for(timeout=self.settle_ms)
            except Exception:  # no suggestion list, for example a plain ADF choice
                loc.press("Enter")
                self._settle()
                return
            texts = suggestions.all_inner_texts()
            best = best_option(texts, pick or option)
            if best is None:  # never the first suggestion: that silently picks a wrong value
                offered = "; ".join(" ".join(t.split()) for t in texts[:5])
                raise StepFailure(f"no suggestion matches {pick or option!r}; offered: {offered}")
            suggestions.nth(best).click()
        self._settle()

    def assert_visible(self, ways: list[tuple[str, str]]) -> None:
        self.find(ways)  # finding exactly one element is the check

    def assert_text(self, ways: list[tuple[str, str]], text: str) -> None:
        loc = self.find(ways)
        want = self.render(text)
        if loc.evaluate("el => ['input', 'textarea'].includes(el.tagName.toLowerCase())"):
            actual = str(loc.input_value())
        else:
            actual = str(loc.inner_text())
        if " ".join(want.split()) not in " ".join(actual.split()):  # Oracle pads text with tabs and line breaks
            raise StepFailure(f"expected text {want!r}, found {' '.join(actual.split())!r}")

    # ------------------------------------------------------------------ REST and scheduled processes

    def api_call(
        self,
        request: str,
        *,
        body: Any = None,
        expect_status: int | None = None,
        check: dict[str, Any] | None = None,
        save: dict[str, str] | None = None,
    ) -> Any:
        """'GET /path' or 'POST /path' with the signed-in session. Checks the reply and keeps values for later steps."""
        method, path = self._parse_request(self.render(request))
        pod = urlparse(self.url)
        asked = urlparse(path)
        if asked.netloc and asked.netloc != pod.netloc:
            raise StepFailure(f"a REST step may only call the pod ({pod.netloc}), not {asked.netloc}")
        url = path if asked.netloc else f"{pod.scheme}://{pod.netloc}/{path.lstrip('/')}"
        headers = {"Accept": "application/json"}
        data = self._render_all(body)
        if data is not None:
            headers["Content-Type"] = "application/vnd.oracle.adf.resourceitem+json"
        reply = self.context.request.fetch(url, method=method, headers=headers, data=data, timeout=60_000)
        try:
            payload = reply.json()
        except Exception:  # an HTML error page, or an empty reply to a DELETE
            payload = None
        status = int(reply.status)
        wrong: list[str] = []
        if expect_status is not None and status != int(expect_status):
            wrong.append(f"the API answered HTTP {status}, expected {int(expect_status)}")
        elif expect_status is None and not 200 <= status < 300:
            wrong.append(f"the API answered HTTP {status}")
        if not wrong:
            for key, want in (check or {}).items():
                want_text = self.render(as_text(want))
                try:
                    got = json_get(payload, key)
                except KeyError:
                    wrong.append(f'the API reply has no "{key}"')
                    continue
                if want_text == "*":
                    if as_text(got) == "" or got in ([], {}):
                        wrong.append(f'"{key}" is empty in the API reply')
                elif as_text(got) != want_text:
                    wrong.append(f'"{key}" is "{as_text(got)}" in the API reply, expected "{want_text}"')
            kept: dict[str, str] = {}
            for name, key in (save or {}).items():
                try:
                    kept[name] = as_text(json_get(payload, key))
                except KeyError:
                    wrong.append(f'the API reply has no "{key}" to keep as {name}')
            if not wrong:
                self.saved.update(kept)
        if wrong:
            raise StepFailure("; ".join(wrong))
        return payload

    @staticmethod
    def _parse_request(value: str) -> tuple[str, str]:
        text = " ".join(value.split())
        method, _, rest = text.partition(" ")
        if method.upper() in METHODS and rest:
            return method.upper(), rest.strip()
        if text.startswith(("/", "http")):
            return "GET", text
        raise StepFailure(f"write the request as METHOD /path, for example GET /hcmRestApi/..., not {value!r}")

    def wait_job(self, process: str, timeout_s: float = 900, expect: str = "SUCCEEDED") -> None:
        """Wait for a scheduled process to end. `process` is its number, or anything else to use the number Oracle shows
        on the screen after a process is submitted ("Process 1234567 was submitted")."""
        process = self.render(process).strip()
        number = process if process.isdigit() else self._process_number_on_screen()
        if not number:
            raise StepFailure("no process number is shown on the screen: submit the process first")
        deadline = time.monotonic() + timeout_s
        while True:
            status = self._process_status(number)
            if status in _PROCESS_DONE:
                break
            if time.monotonic() >= deadline:
                raise StepFailure(f"process {number} was still {status or 'unknown'} after {int(timeout_s)} s")
            time.sleep(self.poll_s)
        if status != expect:
            raise StepFailure(f"the scheduled process ended {status}, expected {expect}")

    def _process_number_on_screen(self) -> str:
        try:
            text = str(self.page.evaluate("() => document.body.innerText"))
        except Exception:
            return ""
        for pattern in _PROCESS_NUMBER:
            found = pattern.findall(text)
            if found:
                return str(found[-1])
        return ""

    def _process_status(self, number: str) -> str:
        pod = urlparse(self.url)
        url = (
            f"{pod.scheme}://{pod.netloc}/fscmRestApi/resources/11.13.18.05/erpintegrations"
            f"?finder=ESSJobStatusRF;requestId={number}&onlyData=true"
        )
        reply = self.context.request.get(url, headers={"Accept": "application/json"}, timeout=60_000)
        if reply.status in (401, 403):
            raise StepFailure(
                f"the pod refused the status check of process {number} (HTTP {reply.status}): the test user needs "
                "access to the erpintegrations REST service"
            )
        if not reply.ok:
            raise StepFailure(f"the status check of process {number} failed (HTTP {reply.status})")
        items = (reply.json() or {}).get("items") or []
        return str(items[0].get("RequestStatus") or "").upper() if items else ""

    # ------------------------------------------------------------------ step wrapper, cleanup, evidence

    @contextlib.contextmanager
    def step(self, number: int, intent: str) -> Iterator[None]:
        """Names the step in the failure and saves a screenshot of the screen when it fails."""
        try:
            yield
        except Exception as e:
            self.screenshot(f"step-{number:02d}")
            raise StepFailure(f"step {number} ({intent}): {type(e).__name__}: {e}") from e

    @contextlib.contextmanager
    def setup(self, number: int, intent: str) -> Iterator[None]:
        """A call that checks or makes what the test needs on the pod. When it fails the test stops with "Setup not
        met": the pod's data was not ready, which is not a sign that the release broke the test."""
        try:
            yield
        except Exception as e:
            raise StepFailure(f"Setup not met: {intent} (setup step {number}). {type(e).__name__}: {e}") from e

    def cleanup(self, intent: str, action: Callable[[], Any], uses: list[str], needs: list[str] | None = None) -> None:
        """Run a cleanup step after the test, whatever happened. A failure is printed and never fails the test, and a
        step that needs something the test never saved (it failed before making it) is skipped."""
        missing = [n for n in (needs or []) if n not in self.saved]
        blank = [n for text in uses for n in self.unsaved(text)]
        if missing or blank:
            print(f"cleanup skipped ({intent}): {(missing or blank)[0]!r} was never saved")
            return
        try:
            action()
        except Exception as e:
            print(f"CLEANUP FAILED ({intent}): {type(e).__name__}: {e} - check the pod for leftovers")

    def screenshot(self, name: str) -> str | None:
        if self.page is None:
            return None
        self.evidence.mkdir(parents=True, exist_ok=True)
        path = self.evidence / f"{self.name}-{name}.png"
        with contextlib.suppress(Exception):
            self.page.screenshot(path=str(path), timeout=30_000)
            return str(path)
        return None
