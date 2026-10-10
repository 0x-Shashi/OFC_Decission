from __future__ import annotations

import pytest
from conftest import EXAMPLES, FakeDriver

from quartermaster.domain.models import (
    Action,
    Environment,
    EnvironmentKind,
    HealingProposal,
    Locator,
    LocatorStrategy,
    ScreenshotMode,
    Step,
    StepStatus,
    TestCase,
)
from quartermaster.dsl.loader import load_test
from quartermaster.locators.resolver import ResolutionError, resolve
from quartermaster.runner.engine import run_test
from quartermaster.safety.guards import UnsafeEnvironmentError


def _tc(*steps: Step, data: dict[str, str] | None = None) -> TestCase:
    return TestCase(id="t", title="t", module="m", product="p", steps=list(steps), data=data or {})


def _click(*strategies: dict[str, str]) -> Step:
    return Step(action=Action.CLICK, intent="Save", target=Locator(strategies=list(strategies)))


# ------------------------------------------------------------------ resolver


def test_resolver_prefers_primary() -> None:
    d = FakeDriver({("label", "Save"): 1, ("text", "Save"): 1})
    r = resolve(Locator(strategies=[{"label": "Save"}, {"text": "Save"}]), d)
    assert r.strategy is LocatorStrategy.LABEL and not r.healed and r.confidence == 1.0


def test_resolver_skips_ambiguous_matches() -> None:
    d = FakeDriver({("text", "Save"): 2, ("role", "button:Save"): 1})
    r = resolve(Locator(strategies=[{"text": "Save"}, {"role": "button:Save"}]), d)
    assert r.strategy is LocatorStrategy.ROLE and r.healed
    assert "matched 2" in r.attempts[0]


def test_resolver_raises_when_nothing_matches() -> None:
    with pytest.raises(ResolutionError, match="matched 0"):
        resolve(Locator(strategies=[{"label": "Nope"}]), FakeDriver())


# ------------------------------------------------------------------ engine


def test_example_invoice_runs_end_to_end(stage_env: Environment) -> None:
    test = load_test(EXAMPLES / "unverified" / "erp" / "ap_create_invoice.yaml")
    d = FakeDriver(
        {
            ("role", "link:Create Invoice"): 1,
            ("label", "Business Unit"): 1,
            ("label", "Supplier"): 1,
            ("label", "Number"): 1,
            ("label", "Amount"): 1,
            ("label", "Identifying PO"): 1,
            ("role", "button:Validate"): 1,
            ("label", "Validation Status"): 1,
        },
        texts={"Validation Status": "Validated"},
    )
    result = run_test(test, stage_env, d, run_id="R1")
    assert result.status is StepStatus.PASSED, [s.error for s in result.steps]
    assert ("fill", "label", "Number", "QM-INV-R1") in d.calls
    assert d.opened and d.closed


def test_fallback_marks_step_healed_and_proposes_patch(stage_env: Environment) -> None:
    d = FakeDriver({("text", "Save"): 1})
    result = run_test(_tc(_click({"label": "Save"}, {"text": "Save"})), stage_env, d)
    assert result.status is StepStatus.HEALED
    [p] = result.healing
    assert p.old == (LocatorStrategy.LABEL, "Save") and p.new == (LocatorStrategy.TEXT, "Save")
    assert p.source == "fallback"


def test_failure_skips_remaining_steps_and_captures_evidence(stage_env: Environment) -> None:
    nav = Step(action=Action.NAVIGATE, intent="go", value="Payables > Invoices")
    result = run_test(_tc(_click({"label": "Missing"}), nav), stage_env, FakeDriver())
    assert [s.status for s in result.steps] == [StepStatus.FAILED, StepStatus.SKIPPED]
    assert result.steps[0].evidence == ["evidence/step-01.png"]
    assert result.steps[1].evidence == []  # skipped steps get no screenshot
    assert result.status is StepStatus.FAILED


def test_screenshot_modes(stage_env: Environment) -> None:
    steps = (_click({"label": "Save"}), Step(action=Action.NAVIGATE, intent="go", value="A > B"))
    page = {("label", "Save"): 1}

    every = run_test(_tc(*steps), stage_env, FakeDriver(page), screenshots=ScreenshotMode.EVERY_STEP)
    # a click also gets a picture just before it, with what it clicks boxed in red
    assert [s.evidence for s in every.steps] == [
        ["evidence/step-01-before.png", "evidence/step-01.png"],
        ["evidence/step-02.png"],
    ]

    on_failure = run_test(_tc(*steps), stage_env, FakeDriver(page))  # the default
    assert [s.evidence for s in on_failure.steps] == [[], []]

    off = run_test(_tc(_click({"label": "Missing"})), stage_env, FakeDriver(), screenshots=ScreenshotMode.OFF)
    assert off.steps[0].status is StepStatus.FAILED and off.steps[0].evidence == []


def test_a_screenshot_that_fails_does_not_end_the_run(stage_env: Environment) -> None:
    class SlowPage(FakeDriver):
        def screenshot(self, name: str, highlight: tuple[LocatorStrategy, str] | None = None) -> str | None:
            if name == "step-01":
                raise TimeoutError("Page.screenshot: Timeout 30000ms exceeded.")
            return super().screenshot(name, highlight)

    steps = (_click({"label": "Save"}), Step(action=Action.NAVIGATE, intent="go", value="A > B"))
    result = run_test(_tc(*steps), stage_env, SlowPage({("label", "Save"): 1}), screenshots=ScreenshotMode.EVERY_STEP)
    assert [s.status for s in result.steps] == [StepStatus.PASSED, StepStatus.PASSED]  # the run went on
    assert [s.evidence for s in result.steps] == [["evidence/step-01-before.png"], ["evidence/step-02.png"]]
    assert result.steps[0].screenshot_note == "The screen could not be captured: the page was still loading."
    assert result.steps[1].screenshot_note is None


def test_step_results_record_what_was_done(stage_env: Environment) -> None:
    fill = Step(
        action=Action.FILL,
        intent="Enter name",
        value="QM ${RUN_ID}",
        expected="Name accepts the value",
        target=Locator(strategies=[{"label": "Name"}]),
    )
    d = FakeDriver({("label", "Name"): 1})
    result = run_test(_tc(fill), stage_env, d, run_id="R1", screenshots=ScreenshotMode.EVERY_STEP)
    [s] = result.steps
    assert (s.action, s.value, s.expected, s.locator) == ("fill", "QM R1", "Name accepts the value", "label=Name")
    assert s.started_at and result.started_at and result.finished_at
    assert (result.run_id, result.test_title, result.environment_url) == ("R1", "t", stage_env.url)
    # the screenshot outlines the element the step used
    assert d.highlights == [("step-01", (LocatorStrategy.LABEL, "Name"))]


def test_failed_check_still_reports_the_element_it_looked_at(stage_env: Environment) -> None:
    check = Step(
        action=Action.ASSERT_TEXT, intent="City", value="Redwood City", target=Locator(strategies=[{"label": "City"}])
    )
    d = FakeDriver({("label", "City"): 1}, texts={"City": "Menlo Park"})
    [s] = run_test(_tc(check), stage_env, d).steps
    assert s.status is StepStatus.FAILED and s.locator == "label=City"
    assert d.highlights == [("step-01", (LocatorStrategy.LABEL, "City"))]


def test_ai_healer_called_on_total_resolution_failure(stage_env: Environment) -> None:
    seen: list[int] = []

    def healer(i: int, step: Step, driver: object) -> HealingProposal:
        seen.append(i)
        return HealingProposal(
            step_index=i,
            intent=step.intent,
            old=(LocatorStrategy.LABEL, "Missing"),
            new=(LocatorStrategy.ROLE, "button:Save"),
            confidence=0.7,
            source="ai",
        )

    result = run_test(_tc(_click({"label": "Missing"})), stage_env, FakeDriver(), healer=healer)
    assert seen == [0]
    assert result.healing[0].source == "ai"
    assert result.status is StepStatus.FAILED  # AI proposals never auto-pass a run


def test_assert_text_mismatch_fails(stage_env: Environment) -> None:
    step = Step(
        action=Action.ASSERT_TEXT, intent="status", value="Validated", target=Locator(strategies=[{"label": "S"}])
    )
    d = FakeDriver({("label", "S"): 1}, texts={"S": "Needs Revalidation"})
    result = run_test(_tc(step), stage_env, d)
    assert "expected text 'Validated'" in (result.steps[0].error or "")


def test_wait_job_checks_final_status(stage_env: Environment) -> None:
    step = Step(action=Action.WAIT_JOB, intent="import", value="Import Payables Invoices")
    d = FakeDriver()
    d.job_status = "ERROR"
    result = run_test(_tc(step), stage_env, d)
    assert result.status is StepStatus.FAILED and "ended ERROR" in (result.steps[0].error or "")


def test_api_call_checks_status(stage_env: Environment) -> None:
    step = Step(action=Action.API_CALL, intent="get", value="GET /invoices", options={"expect_status": 201})
    result = run_test(_tc(step), stage_env, FakeDriver())
    assert "the API answered HTTP 200, expected 201" in (result.steps[0].error or "")
    d = FakeDriver()
    d.api_status = 404
    plain = Step(action=Action.API_CALL, intent="get", value="GET /invoices")
    assert "the API answered HTTP 404" in (run_test(_tc(plain), stage_env, d).steps[0].error or "")


def test_api_call_checks_the_reply_and_keeps_values_for_later_steps(stage_env: Environment) -> None:
    look = Step(
        action=Action.API_CALL,
        intent="The location is in the REST API",
        value="GET /hcmRestApi/resources/11.13.18.05/locationsV2?q=LocationName='${name}'",
        options={
            "check": {"count": 1, "items[0].ActiveStatus": "A", "items[0].LocationId": "*"},
            "save": {"location_id": "items[0].LocationId"},
        },
    )
    change = Step(
        action=Action.API_CALL,
        intent="Rename it",
        value="PATCH /hcmRestApi/resources/11.13.18.05/locationsV2/${location_id}",
        options={"body": {"LocationName": "${name} 2", "Active": True}},
    )
    fill = Step(
        action=Action.FILL, intent="Search", value="${location_id}", target=Locator(strategies=[{"label": "Id"}])
    )
    d = FakeDriver({("label", "Id"): 1})
    d.api_reply = {"count": 1, "items": [{"ActiveStatus": "A", "LocationId": 300100}]}
    result = run_test(_tc(look, change, fill, data={"name": "HQ"}), stage_env, d)
    assert [s.status for s in result.steps] == [StepStatus.PASSED] * 3
    assert ("api_call", "GET", "/hcmRestApi/resources/11.13.18.05/locationsV2?q=LocationName='HQ'") in d.calls
    assert (
        "api_call",
        "PATCH",
        "/hcmRestApi/resources/11.13.18.05/locationsV2/300100",
        {"LocationName": "HQ 2", "Active": True},
    ) in d.calls
    assert ("fill", "label", "Id", "300100") in d.calls

    d.api_reply = {"count": 0, "items": []}
    failed = run_test(_tc(look, data={"name": "HQ"}), stage_env, d).steps[0]
    assert failed.status is StepStatus.FAILED
    assert '"count" is "0" in the API reply, expected "1"' in (failed.error or "")
    assert 'the API reply has no "items[0].ActiveStatus"' in (failed.error or "")


def test_runner_refuses_prod_before_opening_browser() -> None:
    prod = Environment(name="prod", url="https://abcd.fa.us2.oraclecloud.com", kind=EnvironmentKind.PROD)
    d = FakeDriver()
    with pytest.raises(UnsafeEnvironmentError):
        run_test(_tc(_click({"label": "x"})), prod, d)
    assert not d.opened


def test_absence_flow_switches_persona_and_renders_locator_placeholders(stage_env: Environment) -> None:
    test = load_test(EXAMPLES / "unverified" / "hcm" / "absence_request_approval.yaml")
    d = FakeDriver(
        {
            ("label", "Type"): 1,
            ("label", "Start Date"): 1,
            ("label", "End Date"): 1,
            ("label", "Comments"): 1,
            ("role", "button:Submit"): 1,
            ("text", "QM absence R7"): 1,  # locator built from ${comment}
            ("role", "button:Approve"): 1,
            ("label", "Status"): 1,
        },
        texts={"Status": "Approved"},
    )
    result = run_test(test, stage_env, d, run_id="R7")
    assert result.status is StepStatus.PASSED, [s.error for s in result.steps]
    assert d.calls[0] == ("open", "Employee")
    assert ("login_as", "Line Manager") in d.calls
    assert ("click", "text", "QM absence R7") in d.calls


def test_healing_proposal_keeps_template_value(stage_env: Environment) -> None:
    step = Step(
        action=Action.CLICK,
        intent="open",
        target=Locator(strategies=[{"label": "Gone"}, {"text": "${name}"}]),
    )
    d = FakeDriver({("text", "Bob"): 1})
    result = run_test(_tc(step, data={"name": "Bob"}), stage_env, d)
    assert result.healing[0].new == (LocatorStrategy.TEXT, "${name}")
