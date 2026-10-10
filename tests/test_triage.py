"""Failure triage: the likely cause from the run history, and the draft Oracle service request."""

from __future__ import annotations

from typing import Any

from quartermaster.service.triage import likely_cause, sr_draft


def failure(**extra: Any) -> dict[str, Any]:
    return {"category": "assertion", "test_id": "t", "run_release": "26D", **extra}


def test_the_cause_comes_from_the_run_history() -> None:
    update = likely_cause(failure(last_good_release="26C", last_good_at="x"))
    assert update and update["key"] == "release_change" and update["sr"] and "passed on 26C" in update["advice"]
    same = likely_cause(failure(last_good_release="26D", last_good_at="x"))
    assert same and same["key"] == "same_release" and not same["sr"]
    never = likely_cause(failure(category="missing_element"))
    assert never and never["key"] == "never_passed" and "Record the step again" in never["advice"]
    slow = likely_cause(failure(category="timeout", last_good_release="26C"))
    assert slow and slow["key"] == "pod_slow" and not slow["sr"]
    assert likely_cause({"category": "ui_change", "test_id": "t"}) is None
    assert likely_cause({"category": "could_not_run"}) is None


def test_service_request_draft_reads_like_a_bug_report() -> None:
    run = {
        "test_id": "hcm.create-location",
        "run_id": "R1",
        "environment_url": "https://pod.example.com",
        "started_at": "2026-09-27T10:00:00+00:00",
        "steps": [
            {"index": 0, "intent": "Open Locations", "action": "navigate", "status": "passed"},
            {"index": 1, "intent": "Enter the name", "action": "fill", "value": "HQ", "status": "passed"},
            {
                "index": 2,
                "intent": "Check the city",
                "action": "assert_text",
                "status": "failed",
                "error": "StepFailure: expected text 'Redwood Shores', found 'Redwood City'",
            },
            {"index": 3, "intent": "Submit", "action": "click", "status": "skipped"},
        ],
    }
    item = failure(
        module="HCM",
        process="Locations",
        last_good_release="26C",
        compare={"expected": "Redwood Shores", "observed": "Redwood City"},
    )
    draft = sr_draft(item, run)
    assert draft["subject"] == "Locations: “Check the city” no longer works after the update to 26D"
    text = draft["text"]
    assert "1. Open Locations\n2. Enter the name (value: HQ)\n3. Check the city\n" in text and "Submit" not in text
    assert 'Expected result\nThe screen shows "Redwood Shores".' in text
    assert 'Actual result\nThe screen shows "Redwood City".' in text
    assert "It worked on release 26C" in text and "Pod: https://pod.example.com" in text
