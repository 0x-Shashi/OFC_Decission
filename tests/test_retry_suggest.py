"""A step that fails is tried again, a test that only passes that way is flaky, and a step whose item was
not found gets a suggestion (a similar name, or the AI's choice) that a person accepts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import FakeDriver

from quartermaster.ai.providers import AIError
from quartermaster.domain.models import (
    Action,
    Environment,
    HealingProposal,
    Locator,
    LocatorStrategy,
    Step,
    StepStatus,
    TestCase,
)
from quartermaster.runner.engine import run_test
from quartermaster.runner.suggest import make_healer, suggest
from quartermaster.service import insights
from quartermaster.service.heal import accept_update, add_strategy

# ------------------------------------------------------------------ helpers


def case(*steps: Step) -> TestCase:
    return TestCase(id="t", title="t", module="m", product="p", steps=list(steps))


def click(label: str, **options: Any) -> Step:
    return Step(
        action=Action.CLICK, intent=f"Click {label}", target=Locator(strategies=[{"label": label}]), options=options
    )


class Slow(FakeDriver):
    """A page whose item shows up only on the Nth look."""

    def __init__(self, shows_on: int, **kw: Any):
        super().__init__(**kw)
        self.looks = 0
        self.shows_on = shows_on

    def count(self, strategy: LocatorStrategy, value: str) -> int:
        self.looks += 1
        return 1 if self.looks >= self.shows_on else 0


def run(test: TestCase, driver: FakeDriver, env: Environment, **kw: Any) -> Any:
    events: list[dict[str, Any]] = []
    result = run_test(test, env, driver, run_id="R1", retry_wait_s=0, on_event=events.append, **kw)
    return result, events


# ------------------------------------------------------------------ retries


def test_a_step_that_fails_once_and_then_works_passes_and_marks_the_test_flaky(stage_env: Environment) -> None:
    result, events = run(case(click("Save")), Slow(shows_on=2), stage_env, retries=1)
    [step] = result.steps
    assert step.status is StepStatus.PASSED and step.attempts == 2
    assert step.first_error and "Save" in step.first_error
    assert result.status is StepStatus.PASSED and result.flaky
    assert [e["attempt"] for e in events if e["type"] == "step_retry"] == [2]


def test_without_retries_the_same_step_fails(stage_env: Environment) -> None:
    result, events = run(case(click("Save")), Slow(shows_on=2), stage_env, retries=0)
    assert result.status is StepStatus.FAILED and result.steps[0].attempts == 1 and not result.flaky
    assert not [e for e in events if e["type"] == "step_retry"]


def test_a_step_that_keeps_failing_is_tried_as_often_as_allowed_and_then_the_rest_is_skipped(
    stage_env: Environment,
) -> None:
    nav = Step(action=Action.NAVIGATE, intent="go", value="A > B")
    result, _ = run(case(click("Save"), nav), FakeDriver(), stage_env, retries=2)
    assert result.steps[0].attempts == 3 and result.steps[0].status is StepStatus.FAILED
    assert result.steps[1].status is StepStatus.SKIPPED and not result.flaky


def test_a_step_can_set_its_own_retries(stage_env: Environment) -> None:
    result, _ = run(case(click("Save", retries=0)), Slow(shows_on=2), stage_env, retries=3)
    assert result.steps[0].attempts == 1 and result.status is StepStatus.FAILED
    result, _ = run(case(click("Save", retries=3)), Slow(shows_on=4), stage_env, retries=0)
    assert result.steps[0].attempts == 4 and result.status is StepStatus.PASSED


def test_a_click_that_happened_is_never_repeated(stage_env: Environment) -> None:
    class Breaks(FakeDriver):
        def click(self, strategy: LocatorStrategy, value: str) -> None:
            self.calls.append(("click", value))
            raise TimeoutError("the page did not respond after the click")

    d = Breaks({("label", "Save"): 1})
    result, _ = run(case(click("Save")), d, stage_env, retries=3)
    assert result.status is StepStatus.FAILED and result.steps[0].attempts == 1
    assert d.calls.count(("click", "Save")) == 1  # a second Save could save twice


def test_a_rest_call_that_changes_data_is_never_repeated_but_a_read_is(stage_env: Environment) -> None:
    def call(value: str) -> Step:
        return Step(action=Action.API_CALL, intent="call", value=value)

    d = FakeDriver()
    d.api_status = 500
    result, _ = run(case(call("POST /x/y")), d, stage_env, retries=3)
    assert result.steps[0].attempts == 1 and len([c for c in d.calls if c[0] == "api_call"]) == 1
    d = FakeDriver()
    d.api_status = 500
    result, _ = run(case(call("GET /x/y")), d, stage_env, retries=2)
    assert result.steps[0].attempts == 3 and len([c for c in d.calls if c[0] == "api_call"]) == 3


def test_waiting_for_a_process_is_not_repeated(stage_env: Environment) -> None:
    d = FakeDriver()
    d.job_status = "ERROR"
    step = Step(action=Action.WAIT_JOB, intent="wait", value="Load")
    result, _ = run(case(step), d, stage_env, retries=3)
    assert result.steps[0].attempts == 1 and len([c for c in d.calls if c[0] == "wait_job"]) == 1


def test_a_text_check_is_tried_again_because_a_page_may_still_be_filling(stage_env: Environment) -> None:
    class Fills(FakeDriver):
        n = 0

        def text_of(self, strategy: LocatorStrategy, value: str) -> str:
            self.n += 1
            return "Validated" if self.n > 1 else "Pending"

    step = Step(
        action=Action.ASSERT_TEXT, intent="Status", value="Validated", target=Locator(strategies=[{"label": "Status"}])
    )
    result, _ = run(case(step), Fills({("label", "Status"): 1}), stage_env, retries=1)
    assert result.status is StepStatus.PASSED and result.steps[0].attempts == 2 and result.flaky


# ------------------------------------------------------------------ suggestions

SCREEN = {
    "title": "Employment",
    "headings": [],
    "items": [
        {"role": "button", "name": "Search by Name", "full": "Search by Name", "title": "Search by Name"},
        {"role": "link", "name": "Document Records"},
        {"role": "button", "name": "Delete"},
    ],
    "texts": [],
}


class Page:
    def __init__(self, screen: dict[str, Any]):
        self.screen = screen

    def evaluate(self, js: str) -> dict[str, Any]:
        return self.screen


class Pod(FakeDriver):
    def __init__(self, screen: dict[str, Any] = SCREEN, **kw: Any):
        super().__init__(**kw)
        self.page = Page(screen)

    def count(self, strategy: LocatorStrategy, value: str) -> int:
        return 1 if (strategy.value, value) in self.known else 0

    known: set[tuple[str, str]] = {
        ("role", "button:Search by Name"),
        ("role", "link:Document Records"),
        ("role", "button:Delete"),
    }


def looked_for(name: str, action: Action = Action.CLICK, intent: str | None = None) -> Step:
    return Step(
        action=action,
        intent=intent or f"Click {name}",
        target=Locator(strategies=[{"role": f"button:{name}"}, {"text": name}]),
    )


def ask_with(answer: Any) -> Any:
    return lambda system, prompt: answer if isinstance(answer, str) else json.dumps(answer)


def test_a_name_that_looks_like_the_old_one_is_suggested_without_any_ai() -> None:
    p = suggest(2, looked_for("Search: Name"), Pod(), None)
    assert p is not None
    assert p.source == "similar" and p.step_index == 2
    assert p.new == (LocatorStrategy.ROLE, "button:Search by Name")
    assert p.old == (LocatorStrategy.ROLE, "button:Search: Name")


def test_a_redwood_card_is_matched_by_its_title_not_its_long_description() -> None:
    long_name = "Locations Define the places where your workers are based, with their address and time zone"
    card = {"role": "link", "name": long_name[:80], "full": long_name, "title": "Locations"}
    pod = Pod({**SCREEN, "items": [card, {"role": "link", "name": "Positions"}]})
    pod.known = {("role", f"link:{long_name}")}
    p = suggest(1, looked_for("Location"), pod, None)
    assert p is not None and p.source == "similar"
    assert p.new == (LocatorStrategy.ROLE, f"link:{long_name}")  # found by the whole name, as the page names it
    assert p.why == '"Locations" looks like the old name'


def test_when_nothing_is_suggested_the_reason_is_said(capsys: Any) -> None:
    from quartermaster.runner.suggest import suggest_with_reason

    got, reason = suggest_with_reason(0, looked_for("Approve the thing"), Pod(), None)
    assert got is None and "no name among the 3 on the screen looks like" in reason and "no AI" in reason
    got, reason = suggest_with_reason(0, looked_for("Documents"), Pod(), ask_with({"element": None}))
    assert got is None and reason.endswith("the AI chose none")
    make_healer(None)(4, looked_for("Approve the thing"), Pod())
    assert "step 5: no suggested fix (no name among" in capsys.readouterr().err


def test_two_equally_like_names_are_not_a_safe_guess() -> None:
    screen = {
        **SCREEN,
        "items": [{"role": "button", "name": "Search Names"}, {"role": "button", "name": "Search Name"}],
    }
    pod = Pod(screen)
    pod.known = {("role", "button:Search Names"), ("role", "button:Search Name")}
    assert suggest(0, looked_for("Search Nam"), pod, None) is None


def test_without_a_similar_name_and_without_an_ai_there_is_no_suggestion() -> None:
    assert suggest(0, looked_for("Approve the thing"), Pod(), None) is None


def test_the_ai_may_pick_an_element_and_is_never_more_sure_than_80_percent() -> None:
    answer = {"element": 2, "confidence": 0.99, "why": "Same page, renamed."}
    p = suggest(0, looked_for("Documents"), Pod(), ask_with(answer))
    assert p is not None and p.source == "ai"
    assert p.new == (LocatorStrategy.ROLE, "link:Document Records")
    assert p.confidence == 0.8 and p.why == "Same page, renamed."


@pytest.mark.parametrize(
    "answer",
    [
        {"element": None, "confidence": 1},
        {"element": 99, "confidence": 1},
        {"element": 0},
        {"element": "2"},
        {"element": True},
        ["not", "an", "object"],
        "no json at all",
        "",
    ],
)
def test_an_answer_that_does_not_pick_a_real_element_gives_no_suggestion(answer: Any) -> None:
    assert suggest(0, looked_for("Documents"), Pod(), ask_with(answer)) is None


def test_the_ai_cannot_pick_something_that_is_not_found_exactly_once() -> None:
    pod = Pod()
    pod.known = set()  # the page cannot find the chosen element again by any way
    assert suggest(0, looked_for("Documents"), pod, ask_with({"element": 2, "confidence": 0.5})) is None


def test_the_ai_never_suggests_a_button_that_changes_data_unless_the_step_speaks_of_it() -> None:
    assert suggest(0, looked_for("Cancel"), Pod(), ask_with({"element": 3, "confidence": 0.9})) is None
    step = looked_for("Remove", intent="Delete the draft")  # the step itself is about deleting
    p = suggest(0, step, Pod(), ask_with({"element": 3, "confidence": 0.9}))
    assert p is not None and p.new == (LocatorStrategy.ROLE, "button:Delete")


def test_an_ai_that_cannot_be_reached_gives_no_suggestion_and_no_error() -> None:
    def broken(system: str, prompt: str) -> str:
        raise AIError("no connection")

    assert suggest(0, looked_for("Documents"), Pod(), broken) is None


def test_the_ai_is_shown_the_step_and_the_screen_but_not_asked_for_a_value() -> None:
    seen: list[str] = []

    def ask(system: str, prompt: str) -> str:
        seen.append(prompt)
        return '{"element": null}'

    suggest(0, looked_for("Documents"), Pod(), ask)
    assert "Click Documents" in seen[0] and 'link "Document Records"' in seen[0] and "Documents" in seen[0]


def test_a_driver_without_a_page_gives_no_suggestion() -> None:
    assert suggest(0, looked_for("Documents"), FakeDriver(), ask_with({"element": 1})) is None


def test_the_engine_keeps_the_failure_and_adds_the_suggestion(stage_env: Environment) -> None:
    pod = Pod()
    result, _ = run(case(looked_for("Search: Name")), pod, stage_env, healer=make_healer(None))
    assert result.status is StepStatus.FAILED  # a suggestion never makes a step pass
    [p] = result.healing
    assert p.source == "similar" and p.new == (LocatorStrategy.ROLE, "button:Search by Name")
    assert not [c for c in pod.calls if c[0] == "click"]  # and nothing was clicked


def test_a_suggester_that_crashes_does_not_end_the_run(stage_env: Environment) -> None:
    def bad(i: int, step: Step, driver: Any) -> HealingProposal | None:
        raise RuntimeError("boom")

    nav = Step(action=Action.NAVIGATE, intent="go", value="A > B")
    result, _ = run(case(looked_for("Gone"), nav), Pod(), stage_env, healer=bad)
    assert [s.status for s in result.steps] == [StepStatus.FAILED, StepStatus.SKIPPED] and not result.healing


def test_the_suggester_is_asked_once_after_the_last_attempt(stage_env: Environment) -> None:
    asked: list[int] = []

    def counting(i: int, step: Step, driver: Any) -> HealingProposal | None:
        asked.append(i)
        return None

    run(case(looked_for("Gone")), Pod(), stage_env, healer=counting, retries=2)
    assert asked == [0]


# ------------------------------------------------------------------ accepting a suggestion

SPEC = """id: hcm.x
title: X
steps:
  - action: click
    intent: Search
    target:
      strategies:
        # the way it was recorded
        - role: "button:Search: Name"
        - text: "Search: Name"
"""


def strategies(text: str) -> list[dict[str, str]]:
    return yaml.safe_load(text)["steps"][0]["target"]["strategies"]  # type: ignore[no-any-return]


def test_a_suggestion_is_added_on_top_and_the_old_ways_stay_as_a_fallback() -> None:
    new = add_strategy(SPEC, 0, "role", "button:Search by Name")
    assert strategies(new) == [{"role": "button:Search by Name"}, *strategies(SPEC)]
    assert "# the way it was recorded" in new
    assert new.index("Search by Name") < new.index("# the way it was recorded")


def test_values_with_quotes_and_colons_stay_valid_yaml() -> None:
    new = add_strategy(SPEC, 0, "text", 'Say "hi": now # not a comment')
    assert strategies(new)[0] == {"text": 'Say "hi": now # not a comment'}


def test_a_way_the_test_already_knows_is_moved_up_instead_of_added_twice() -> None:
    new = add_strategy(SPEC, 0, "text", "Search: Name")
    assert strategies(new) == [{"text": "Search: Name"}, {"role": "button:Search: Name"}]


def test_accepting_writes_the_file_and_keeps_a_copy(tmp_path: Path) -> None:
    f = tmp_path / "t.yaml"
    f.write_text(SPEC, encoding="utf-8")
    backup = accept_update(f, 0, ["role", "button:Search by Name"], tmp_path / "backups", add=True)
    assert strategies(f.read_text(encoding="utf-8"))[0] == {"role": "button:Search by Name"}
    assert backup.read_text(encoding="utf-8") == SPEC


def test_a_step_without_locators_or_a_missing_step_is_refused() -> None:
    with pytest.raises(ValueError, match="no step 3"):
        add_strategy(SPEC, 2, "text", "x")
    with pytest.raises(ValueError, match="no locators"):
        add_strategy("steps:\n  - action: navigate\n    intent: go\n    value: A\n", 0, "text", "x")


# ------------------------------------------------------------------ what Needs attention and Overview say


def test_a_suggestion_is_attached_to_the_failure_it_belongs_to() -> None:
    spec = yaml.safe_load(SPEC)
    result = {
        "healing": [
            {
                "step_index": 0,
                "source": "similar",
                "confidence": 0.8,
                "why": "x",
                "old": ["role", "button:Search: Name"],
                "new": ["role", "button:Search by Name"],
            }
        ]
    }
    got = insights._suggestion(result, {"number": 1}, spec)
    assert got and got["new_text"] == 'the button named "Search by Name"' and got["source"] == "similar"
    assert insights._suggestion(result, {"number": 2}, spec) is None  # another step
    accepted = yaml.safe_load(add_strategy(SPEC, 0, "role", "button:Search by Name"))
    assert insights._suggestion(result, {"number": 1}, accepted) is None  # already used first


def test_a_test_is_flaky_when_it_needed_a_retry_twice_in_its_last_ten_runs() -> None:
    rows = [{"test_id": "a", "flaky": n in (0, 4)} for n in range(10)] + [{"test_id": "b", "flaky": True}]
    rows += [{"test_id": "a", "flaky": True}] * 5  # older than the last ten: not looked at
    s = insights.stability(rows)
    assert s["a"] == {"runs": 10, "flaky_runs": 2, "flaky": True}
    assert s["b"] == {"runs": 1, "flaky_runs": 1, "flaky": False}


def test_the_stability_summary_gives_the_share_of_runs_that_needed_a_retry() -> None:
    from datetime import datetime, timedelta

    now = datetime.now().astimezone()
    old = (now - timedelta(days=90)).isoformat()
    recent = now.isoformat()
    rows = [{"test_id": "a", "flaky": n < 2, "at": recent} for n in range(10)] + [
        {"test_id": "a", "flaky": True, "at": old}
    ]
    tests = [{"id": "a", "title": "A test", "file": "a.yaml"}]
    s = insights.stability_summary(rows, tests)
    assert s["runs"] == 10 and s["retried"] == 2 and s["rate"] == 20.0
    assert s["flaky_count"] == 1 and s["flaky_tests"][0]["title"] == "A test"
    assert insights.stability_summary([], tests)["rate"] is None


# ------------------------------------------------------------------ the demo tests in examples/demos


def test_the_demo_tests_are_valid_and_only_read() -> None:
    from conftest import EXAMPLES

    from quartermaster.dsl.loader import load_tests

    tests = {t.id: t for t in load_tests(EXAMPLES / "demos")}
    assert set(tests) == {
        "demo.retry",
        "demo.suggest",
        "demo.cleanup",
        "demo.library-one",
        "demo.library-two",
        "demo.test-data",
        "demo.test-data-gap",
        "demo.setup",
        "demo.setup-gap",
    }
    for t in tests.values():
        for step in [*t.setup, *t.steps, *t.cleanup]:
            # nothing here may change data on the pod: no REST call but GET, and no Save, Submit or Delete
            if step.action is Action.API_CALL:
                assert (step.value or "").startswith("GET ")
            assert step.action in (Action.API_CALL, Action.NAVIGATE, Action.CLICK)
    assert tests["demo.cleanup"].cleanup and tests["demo.retry"].steps[0].action is Action.API_CALL
    assert tests["demo.suggest"].steps[1].target is not None
    one, two = tests["demo.library-one"], tests["demo.library-two"]  # the shared steps, with and without a value
    assert (
        one.steps[0].shared == "read-locations" and one.steps[0].value is not None and "limit=1" in one.steps[0].value
    )
    assert two.steps[0].value is not None and "limit=2" in two.steps[0].value and one.cleanup and two.cleanup
    data, gap = tests["demo.test-data"], tests["demo.test-data-gap"]  # test data: a data set, a made value, a gap
    assert data.data == {"rows": "2"} and data.pods["TEST"] == {"rows": "1"} and "pick" in data.generate
    assert gap.pods == {"STAGE": {"location_limit": "1"}} and "location_limit" not in gap.data
    ready, unready = tests["demo.setup"], tests["demo.setup-gap"]  # setup: one that is met, one that is not
    assert ready.setup[0].options["save"] == {"location_id": "items[0].LocationId"}
    assert "locationsV9" in (unready.setup[0].value or "") and "locationsV2" in (unready.steps[0].value or "")
