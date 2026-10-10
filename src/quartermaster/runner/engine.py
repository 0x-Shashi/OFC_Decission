"""Deterministic step executor.

The engine only knows the `Driver` protocol. `PlaywrightDriver` talks to a real Fusion pod;
tests use an in-memory fake. Business flows are sequential, so the first failed step skips
the rest.
"""

from __future__ import annotations

import sys
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from quartermaster.domain.models import (
    Action,
    Environment,
    HealingProposal,
    Locator,
    LocatorStrategy,
    RunResult,
    ScreenshotMode,
    Step,
    StepResult,
    StepStatus,
    TestCase,
)
from quartermaster.dsl.data import effective_data, generate_values, pod_only_names
from quartermaster.dsl.loader import SECRET, display_value, render_text_names, render_value
from quartermaster.locators.resolver import Resolution, ResolutionError, resolve
from quartermaster.runner.rest import check_reply, parse_request, render_body
from quartermaster.safety.guards import assert_safe_target, confirmed_hosts


class Driver(Protocol):
    def open(self, env: Environment, persona: str) -> None: ...
    def login_as(self, persona: str) -> None: ...
    def close(self) -> None: ...
    def count(self, strategy: LocatorStrategy, value: str) -> int: ...
    def navigate(self, path: str) -> None: ...
    def click(self, strategy: LocatorStrategy, value: str) -> None: ...
    def fill(self, strategy: LocatorStrategy, value: str, text: str) -> None: ...
    def select(self, strategy: LocatorStrategy, value: str, option: str, pick: str | None = None) -> None: ...
    def text_of(self, strategy: LocatorStrategy, value: str) -> str: ...
    def wait_job(self, job_name: str, timeout_s: float) -> str: ...
    def api_call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]: ...
    def screenshot(self, name: str, highlight: tuple[LocatorStrategy, str] | None = None) -> str | None: ...


# Called when every locator strategy fails; may return an AI-proposed replacement.
Healer = Callable[[int, Step, Driver], HealingProposal | None]


class StepFailure(AssertionError):
    pass


def run_test(
    test: TestCase,
    env: Environment,
    driver: Driver,
    *,
    allowed_hosts: set[str] | None = None,
    healer: Healer | None = None,
    retries: int = 0,
    retry_wait_s: float = 2.0,
    run_id: str | None = None,
    screenshots: ScreenshotMode = ScreenshotMode.ON_FAILURE,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> RunResult:
    """Run one test. `on_event`, if given, hears about progress as it happens (for live views):
    run_start, step_start, step_retry, step_end and run_end, each a small JSON-ready dict.

    `retries`: how many more times a step that failed is tried (a slow page is not a changed page),
    after `retry_wait_s` seconds each time (`qm run` uses 1). A step may set its own with `options: {retries: N}`.
    Only steps that are safe to repeat are retried (see `_may_retry`)."""
    assert_safe_target(env, allowed_hosts if allowed_hosts is not None else confirmed_hosts())
    run_id = run_id or uuid.uuid4().hex[:8].upper()

    def emit(kind: str, **fields: Any) -> None:
        if on_event is not None:
            on_event({"type": kind, "test_id": test.id, "run_id": run_id, "at": _now(), **fields})

    # What the test's data is on this pod, and the values made fresh for this run (see dsl/data.py).
    data_problem = _missing_data(test, env)
    test = test.model_copy(update={"data": effective_data(test.data, test.pods, env.name, env.kind.value)})
    runtime = {"RUN_ID": run_id, **generate_values(test.generate, run_id)}
    results: list[StepResult] = []
    healing: list[HealingProposal] = []
    started_at = _now()

    emit("run_start", title=test.title, steps=len(test.steps))
    driver.open(env, test.persona)
    setup: list[StepResult] = []
    cleanup: list[StepResult] = []
    try:
        failed = False
        if not data_problem:  # setup would use the value that is not there
            setup = _run_setup(test, driver, runtime, retries, retry_wait_s, emit)
            bad = next((r for r in setup if r.status is StepStatus.FAILED), None)
            if bad is not None:
                data_problem = f"Setup not met: {bad.intent}. {bad.error}"
        for i, step in enumerate(test.steps):
            base: dict[str, Any] = {
                "index": i,
                "intent": step.intent,
                "action": step.action.value,
                "value": display_value(step.value, test.data, runtime),  # masked values stay hidden
                "expected": step.expected,
                "written_step": step.written_step,
            }
            if data_problem and i == 0:  # no step is tried with a value that is not there
                results.append(StepResult(**base, status=StepStatus.FAILED, error=data_problem, started_at=_now()))
                emit("step_end", index=i, intent=step.intent, status="failed", error=data_problem, evidence=[])
                failed = True
                continue
            if failed:
                results.append(StepResult(**base, status=StepStatus.SKIPPED))
                emit("step_end", index=i, intent=step.intent, status=StepStatus.SKIPPED.value, error=None, evidence=[])
                continue
            emit("step_start", index=i, intent=step.intent)
            start = time.perf_counter()
            step_started = _now()
            status, error = StepStatus.PASSED, None
            evidence: list[str] = []
            seen: dict[str, Resolution] = {}

            def before_click(res: Resolution, i: int = i, evidence: list[str] = evidence) -> None:
                # A picture just before a click, with the item about to be clicked boxed in red: the
                # one taken after it often shows a new page, where that item is gone.
                try:
                    shot = driver.screenshot(f"step-{i + 1:02d}-before", (res.strategy, res.value))
                except Exception:  # a busy page: the picture after the step still comes
                    return
                if shot:
                    evidence.append(shot)

            wants_before = step.action is Action.CLICK and screenshots is ScreenshotMode.EVERY_STEP
            tries = 1 + _retries_for(step, retries)
            attempt = 0
            first_error: str | None = None
            not_found: ResolutionError | None = None
            while True:
                attempt += 1
                status, error, not_found = StepStatus.PASSED, None, None
                evidence.clear()
                seen.clear()
                try:
                    res = _execute(step, test.data, runtime, driver, seen, before_click if wants_before else None)
                    if res is not None and res.healed:
                        status = StepStatus.HEALED
                        healing.append(
                            HealingProposal(
                                step_index=i,
                                intent=step.intent,
                                # Template values (not rendered data), so the patch applies to the spec.
                                old=step.target.ordered()[0],  # type: ignore[union-attr]
                                new=step.target.ordered()[res.index],  # type: ignore[union-attr]
                                confidence=res.confidence,
                            )
                        )
                except ResolutionError as e:
                    status, error, not_found = StepStatus.FAILED, str(e), e
                except Exception as e:  # driver errors, assertion failures, timeouts
                    status, error = StepStatus.FAILED, f"{type(e).__name__}: {e}"
                if status is not StepStatus.FAILED or attempt >= tries or not _may_retry(step, not_found is not None):
                    break
                first_error = first_error or error
                emit("step_retry", index=i, intent=step.intent, attempt=attempt + 1, of=tries, error=error)
                time.sleep(retry_wait_s)
            if not_found is not None and healer is not None:
                try:  # a suggestion is a bonus: whatever goes wrong in it must not end the run
                    proposal = healer(i, step, driver)
                except Exception as e:
                    print(
                        f"warning: step {i + 1}: no suggestion ({type(e).__name__}: {e})".splitlines()[0],
                        file=sys.stderr,
                    )
                    proposal = None
                if proposal is not None:
                    healing.append(proposal)

            used = seen.get("res")
            if status is StepStatus.FAILED:
                failed = True
            screenshot_note = None
            if _wants_screenshot(screenshots, status):
                # Taken after the step, with the element it used outlined when still on screen.
                try:
                    shot = driver.screenshot(f"step-{i + 1:02d}", (used.strategy, used.value) if used else None)
                except Exception as e:  # a page too busy to capture must not end the run; the step keeps its result
                    shot = None
                    screenshot_note = "The screen could not be captured: the page was still loading."
                    print(
                        f"warning: step {i + 1}: no screenshot ({type(e).__name__}: {e})".splitlines()[0],
                        file=sys.stderr,
                    )
                if shot:
                    evidence.append(shot)
            results.append(
                StepResult(
                    **base,
                    status=status,
                    duration_ms=round((time.perf_counter() - start) * 1000, 2),
                    error=error,
                    evidence=evidence,
                    locator=f"{used.strategy.value}={used.value}" if used else None,
                    started_at=step_started,
                    screenshot_note=screenshot_note,
                    attempts=attempt,
                    first_error=first_error if status is not StepStatus.FAILED else None,
                )
            )
            emit("step_end", index=i, intent=step.intent, status=status.value, error=error, evidence=evidence)
        cleanup = _run_cleanup(test, driver, runtime, screenshots, emit)
    finally:
        driver.close()

    result = RunResult(
        test_id=test.id,
        environment=env.name,
        steps=results,
        healing=healing,
        cleanup=cleanup,
        setup=setup,
        run_id=run_id,
        test_title=test.title,
        persona=test.persona,
        environment_url=env.url,
        release=env.release,
        started_at=started_at,
        finished_at=_now(),
        screenshots=screenshots,
        written_steps=test.written_steps,
    )
    emit("run_end", status=result.status.value)
    return result


def _missing_data(test: TestCase, env: Environment) -> str | None:
    """A sentence when the test uses a value that some pods give but this pod does not, else None."""
    mine = effective_data(test.data, test.pods, env.name, env.kind.value)
    wanted = pod_only_names(test.data, test.pods) - set(mine)
    if not wanted:
        return None
    text = " ".join(s.model_dump_json() for s in [*test.steps, *test.cleanup])
    missing = sorted(n for n in wanted if "${" + n + "}" in text)
    if not missing:
        return None
    return (
        f"No test data for {', '.join(missing)} on {env.name} ({env.kind.value}). Other pods have a value, this one "
        "does not: add it to the data set (or the test's `pods:`) under this pod's name or kind"
    )


def _run_setup(
    test: TestCase,
    driver: Driver,
    runtime: dict[str, str],
    retries: int,
    retry_wait_s: float,
    emit: Callable[..., None],
) -> list[StepResult]:
    """Run the test's setup calls in order. The first one that fails stops the setup (the rest are marked skipped):
    the steps never run on a pod that is not ready. A call that only reads may be tried again, like any step."""
    out: list[StepResult] = []
    if not test.setup:
        return out
    emit("setup_start", steps=len(test.setup))
    stopped = False
    for i, step in enumerate(test.setup):
        base: dict[str, Any] = {
            "index": i,
            "intent": step.intent,
            "action": step.action.value,
            "value": display_value(step.value, test.data, runtime),
            "expected": step.expected,
        }
        if stopped:
            out.append(StepResult(**base, status=StepStatus.SKIPPED, note="An earlier setup step did not pass."))
            emit("setup_end", index=i, intent=step.intent, status="skipped", error=None)
            continue
        emit("setup_start_step", index=i, intent=step.intent)
        start = time.perf_counter()
        started = _now()
        tries = 1 + _retries_for(step, retries)
        attempt, status, error = 0, StepStatus.PASSED, None
        while True:
            attempt += 1
            status, error = StepStatus.PASSED, None
            try:
                _execute(step, test.data, runtime, driver, {})
            except Exception as e:
                status, error = StepStatus.FAILED, f"{type(e).__name__}: {e}"
            if status is not StepStatus.FAILED or attempt >= tries or not _may_retry(step, False):
                break
            emit("step_retry", index=i, intent=step.intent, attempt=attempt + 1, of=tries, error=error)
            time.sleep(retry_wait_s)
        out.append(
            StepResult(
                **base,
                status=status,
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
                error=error,
                started_at=started,
                attempts=attempt,
            )
        )
        emit("setup_end", index=i, intent=step.intent, status=status.value, error=error)
        stopped = status is StepStatus.FAILED
    return out


def _retries_for(step: Step, default: int) -> int:
    """How many more times this step may be tried: its own setting, else the run's (0 to 3)."""
    own = step.options.get("retries")
    wanted = own if isinstance(own, int) and not isinstance(own, bool) else default
    return max(0, min(int(wanted), 3))


def _may_retry(step: Step, not_found: bool) -> bool:
    """Whether trying a failed step again is safe. A click is only repeated when its item was not found
    (so it was never clicked: a second Save could save twice); a REST call only when it reads (GET);
    waiting for a scheduled process already waits as long as it should."""
    if step.action is Action.CLICK:
        return not_found
    if step.action is Action.WAIT_JOB:
        return False
    if step.action is Action.API_CALL:
        first = (step.value or "").split(maxsplit=1)[:1]
        return not first or first[0].upper() == "GET" or first[0].startswith(("/", "http"))
    return True


def _run_cleanup(
    test: TestCase,
    driver: Driver,
    runtime: dict[str, str],
    screenshots: ScreenshotMode,
    emit: Callable[..., None],
) -> list[StepResult]:
    """Run the test's cleanup steps. Each one is tried on its own: a failure is recorded and the next
    step still runs. A step that needs something the test never saved (because it failed before
    making it) is skipped, so nothing is ever deleted by an address with a blank in it."""
    out: list[StepResult] = []
    if not test.cleanup:
        return out
    emit("cleanup_start", steps=len(test.cleanup))
    for i, step in enumerate(test.cleanup):
        base: dict[str, Any] = {
            "index": i,
            "intent": step.intent,
            "action": step.action.value,
            "value": display_value(step.value, test.data, runtime),
            "expected": step.expected,
        }
        needs = step.options.get("needs")
        missing = [str(n) for n in ([needs] if isinstance(needs, str) else needs or []) if str(n) not in runtime]
        texts = [step.value or ""] + [v for _, v in (step.target.ordered() if step.target else [])]
        blank = [n for t in texts for n in _unsaved(t, test.data, runtime)]
        if missing or blank:
            name = (missing or blank)[0]
            note = f"'{name}' was never saved, so the test did not get as far as making it."
            out.append(StepResult(**base, status=StepStatus.SKIPPED, note=note))
            emit("cleanup_end", index=i, intent=step.intent, status="skipped", error=None)
            continue
        emit("cleanup_start_step", index=i, intent=step.intent)
        start = time.perf_counter()
        started = _now()
        status, error = StepStatus.PASSED, None
        evidence: list[str] = []
        seen: dict[str, Resolution] = {}
        try:
            _execute(step, test.data, runtime, driver, seen)
        except Exception as e:  # a failed cleanup is reported, never raised: the test's result stands
            status, error = StepStatus.FAILED, str(e) if isinstance(e, ResolutionError) else f"{type(e).__name__}: {e}"
        used = seen.get("res")
        shot_note = None
        if _wants_screenshot(screenshots, status):
            try:
                shot = driver.screenshot(f"cleanup-{i + 1:02d}", (used.strategy, used.value) if used else None)
            except Exception:  # a busy page must not lose the cleanup's result
                shot, shot_note = None, "The screen could not be captured."
            if shot:
                evidence.append(shot)
        out.append(
            StepResult(
                **base,
                status=status,
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
                error=error,
                evidence=evidence,
                locator=f"{used.strategy.value}={used.value}" if used else None,
                started_at=started,
                screenshot_note=shot_note,
            )
        )
        emit("cleanup_end", index=i, intent=step.intent, status=status.value, error=error)
    return out


def _unsaved(text: str, data: dict[str, str], runtime: dict[str, str]) -> list[str]:
    """Names in ${...} that are still unfilled after the test's data and what its steps saved."""
    left = render_text_names(text, data, runtime)
    return [n for n in left if not n.startswith(SECRET)]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _wants_screenshot(mode: ScreenshotMode, status: StepStatus) -> bool:
    if mode is ScreenshotMode.EVERY_STEP:
        return True
    return mode is ScreenshotMode.ON_FAILURE and status is StepStatus.FAILED


def _execute(
    step: Step,
    data: dict[str, str],
    runtime: dict[str, str],
    driver: Driver,
    seen: dict[str, Resolution],
    before_click: Callable[[Resolution], None] | None = None,
) -> Resolution | None:
    value = render_value(step.value, data, runtime)
    a = step.action

    if a is Action.LOGIN_AS:
        driver.login_as(value)  # type: ignore[arg-type]
        return None
    if a is Action.NAVIGATE:
        driver.navigate(value)  # type: ignore[arg-type]
        return None
    if a is Action.WAIT_JOB:
        final = driver.wait_job(value, float(step.options.get("timeout_s", 900)))  # type: ignore[arg-type]
        expected = step.options.get("expect", "SUCCEEDED")
        if final != expected:
            raise StepFailure(f"the scheduled process ended {final}, expected {expected}")
        return None
    if a is Action.API_CALL:

        def fill_in(text: str) -> str:
            return render_value(text, data, runtime) or ""

        method, path = parse_request(value or "")
        status, reply = driver.api_call(method, path, render_body(step.options.get("body"), fill_in))
        wrong, kept = check_reply(status, reply, step.options, fill_in)
        if wrong:
            raise StepFailure("; ".join(wrong))
        runtime.update(kept)  # ${name} in later steps
        return None

    assert step.target is not None  # guaranteed by Step validation
    target = Locator(
        strategies=[{s: render_value(v, data, runtime) or v} for s, v in step.target.ordered()],
        description=step.target.description,
    )
    res = resolve(target, driver)
    seen["res"] = res  # kept even if the action below fails, for the report and screenshot
    s, v = res.strategy, res.value
    if a is Action.CLICK:
        if before_click is not None:
            before_click(res)
        driver.click(s, v)
    elif a is Action.FILL:
        driver.fill(s, v, value)  # type: ignore[arg-type]
    elif a is Action.SELECT:
        # `pick`: the suggestion to choose when it differs from the text typed (e.g. from a recording).
        pick = render_value(step.options.get("pick"), data, runtime)
        driver.select(s, v, value, pick=pick)  # type: ignore[arg-type]
    elif a is Action.ASSERT_VISIBLE:
        pass  # resolving to exactly one element is the assertion
    elif a is Action.ASSERT_TEXT:
        actual = driver.text_of(s, v)
        # Oracle pages pad text with tabs and line breaks, so compare with whitespace collapsed.
        if value is None or " ".join(value.split()) not in " ".join(actual.split()):
            raise StepFailure(f"expected text {value!r}, found {' '.join(actual.split())!r}")
    return res
