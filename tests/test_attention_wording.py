"""What Needs attention says: a failed service call is not a "check that did not match", and the advice
does not tell you to record a step again when a suggested fix is waiting."""

from __future__ import annotations

from typing import Any

import pytest

from quartermaster.service.insights import CATEGORIES, classify
from quartermaster.service.triage import likely_cause


@pytest.mark.parametrize(
    "error",
    [
        "StepFailure: the API answered HTTP 404",
        "the API answered HTTP 500, expected 200",
        'StepFailure: the API reply has no "items[0].LocationId"',
        'StepFailure: "count" is "2" in the API reply, expected "1"',
        'StepFailure: "items[0].LocationId" is empty in the API reply',
    ],
)
def test_a_failed_rest_step_is_a_service_call_not_a_check(error: str) -> None:
    assert classify(error) == "service_call"
    assert "service_call" in CATEGORIES


def test_other_failures_are_still_sorted_as_before() -> None:
    assert classify("StepFailure: expected text 'Validated', found 'Pending'") == "assertion"
    assert classify("could not resolve 'link:X': role='link:X' matched 0") == "missing_element"
    assert classify("TimeoutError: Timeout 30000ms exceeded") == "timeout"
    assert classify("MissingCredentialsError: sign in failed") == "authentication"
    assert classify("RuntimeError: boom") == "failure"


def item(kind: str, **more: Any) -> dict[str, Any]:
    return {"category": kind, "test_id": "t", "run_release": "26C", **more}


def test_a_service_call_that_never_passed_points_at_the_address_not_the_picture() -> None:
    cause = likely_cause(item("service_call"))
    assert cause and cause["title"] == "The service address or its data needs fixing"
    assert "picture" not in cause["advice"] and "service address" in cause["advice"]


def test_a_service_call_that_passed_on_an_earlier_release_says_the_service_answered_differently() -> None:
    cause = likely_cause(item("service_call", last_good_release="26B", last_good_at="2026-07-01"))
    assert cause and "the service answered differently" in cause["advice"]


def test_with_a_suggestion_waiting_the_advice_says_to_try_it_first() -> None:
    suggestion = {"new_text": 'the link named "Locations"'}
    never = likely_cause(item("missing_element", suggestion=suggestion))
    assert never and never["title"] == "Try the suggested fix first"
    assert "suggested fix above" in never["advice"] and "record the step again" in never["advice"]
    later = likely_cause(item("missing_element", suggestion=suggestion, last_good_release="26B", last_good_at="x"))
    assert later and later["advice"].count("suggested fix above") == 1 and later["sr"]


def test_without_a_suggestion_the_advice_is_as_before() -> None:
    cause = likely_cause(item("missing_element"))
    assert cause and cause["title"] == "The test needs fixing" and "suggested" not in cause["advice"]
