"""Conversion rules from recorded events to steps (pure; no browser, no models)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from quartermaster.recorder.steps import events_to_steps, preview, secret_names


def ev(kind: str, intent: str = "", *cands: tuple[str, str], **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "intent": intent, "candidates": [{"strategy": s, "value": v} for s, v in cands], **extra}


def test_navigator_click_becomes_one_navigate_step() -> None:
    steps, data = events_to_steps(
        [
            ev("navigate", value="My Client Groups > Workforce Structures"),
            ev("navigate", value="My Client Groups > Workforce Structures"),  # double click: kept once
        ]
    )
    assert steps == [
        {
            "action": "navigate",
            "intent": "Open My Client Groups > Workforce Structures",
            "value": "My Client Groups > Workforce Structures",
        }
    ]
    assert data == {}


def test_type_ahead_list_keeps_the_suggestion_picked() -> None:
    steps, data = events_to_steps(
        [
            ev(
                "select",
                "Postal Code",
                ("label", "Postal Code"),
                ("role", "combobox:Postal Code"),
                value="94065",
                pick="94065 Redwood Shores, San Mateo, CA",
            ),
            ev("select", "Status", ("role", "combobox:Status"), value="Active", pick="Active"),
        ]
    )
    assert steps[0]["value"] == "${value1}" and data["value1"] == "94065"
    assert steps[0]["options"] == {"pick": "94065 Redwood Shores, San Mateo, CA"}
    assert "options" not in steps[1]  # picked exactly what was typed


def test_redwood_date_is_a_fill_of_the_group() -> None:
    steps, data = events_to_steps(
        [ev("fill", "Effective Start Date", ("role", "group:Effective Start Date"), value="01/01/1951")]
    )
    assert steps == [
        {
            "action": "fill",
            "intent": "Enter Effective Start Date",
            "target": {"strategies": [{"role": "group:Effective Start Date"}]},
            "value": "${value1}",
        }
    ]
    assert data == {"value1": "01/01/1951"}


def test_checks_become_assert_steps() -> None:
    steps, data = events_to_steps(
        [
            ev("assert_text", "City", ("label", "City"), value="Redwood Shores"),
            ev("assert_visible", "Summary", ("text", "Summary")),
        ]
    )
    assert [s["action"] for s in steps] == ["assert_text", "assert_visible"]
    assert steps[0]["intent"] == "Check City" and data == {"value1": "Redwood Shores"}
    assert steps[1]["intent"] == "Check Summary is shown" and "value" not in steps[1]


def test_repeated_edits_keep_the_last_value_and_order_is_kept() -> None:
    steps, data = events_to_steps(
        [
            ev("fill", "Name", ("role", "textbox:Name"), value="QM"),
            ev("fill", "Name", ("role", "textbox:Name"), value="QM Location"),
            ev("click", "Submit", ("role", "button:Submit")),
            ev("stop"),  # toolbar stop is not a step
        ]
    )
    assert [s["intent"] for s in steps] == ["Enter Name", "Click Submit"]
    assert data == {"value1": "QM Location"}


def test_events_without_usable_locators_are_dropped() -> None:
    steps, _ = events_to_steps([ev("click", "icon"), ev("click", "x", ("bogus", "y")), ev("navigate", value="")])
    assert steps == []


def test_a_note_becomes_the_expected_result_of_the_step_before_it() -> None:
    steps, _ = events_to_steps(
        [
            {"kind": "note", "value": "ignored: no step yet"},
            ev("click", "Search", ("role", "button:Search")),
            {"kind": "note", "value": "The results   list the worker."},
            {"kind": "note", "value": "One row only."},
        ]
    )
    assert [s["intent"] for s in steps] == ["Click Search"]
    assert steps[0]["expected"] == "The results list the worker. One row only."


def test_a_masked_value_is_never_saved() -> None:
    events = [
        ev("fill", "User", ("label", "User"), value="bob"),
        ev("fill", "PIN", ("label", "PIN"), value="1234", sensitive=True),
        ev("fill", "PIN", ("label", "PIN"), value="12345"),  # typed again: stays masked
        ev("fill", "City", ("label", "City"), value="Leeds"),
    ]
    steps, data = events_to_steps(events, "QM_HCM_X")
    assert data == {"value1": "bob", "secret1": "${env:QM_HCM_X_1}", "value2": "Leeds"}
    assert [s["value"] for s in steps] == ["${value1}", "${secret1}", "${value2}"]
    assert "1234" not in str((steps, data))
    shown = preview(events, "QM_HCM_X")
    assert [s["value"] for s in shown] == ["bob", "••••••", "Leeds"] and "1234" not in str(shown)
    assert secret_names(data) == ["QM_HCM_X_1"]


def test_a_test_from_a_manual_scenario_keeps_its_written_steps(tmp_path: Path) -> None:
    from quartermaster.dsl.loader import load_test
    from quartermaster.recorder.recorder import events_to_test, to_yaml

    events = [
        {"kind": "navigate", "value": "Me > Personal Information", "guide_step": 3},
        {
            "kind": "click",
            "intent": "My Compensation",
            "candidates": [{"strategy": "text", "value": "My Compensation"}],
            "guide_step": 4,
        },
    ]
    written = [{"action": "Login", "expected": ""}, {"action": "Select My Compensation", "expected": "It opens"}]
    test = events_to_test(
        events,
        test_id="manual.ess.ess-001",
        title="My Compensation",
        module="HCM",
        product="Global Human Resources",
        written_steps=written,
    )
    out = tmp_path / "t.yaml"
    out.write_text(to_yaml(test))
    again = load_test(out)
    assert [s.written_step for s in again.steps] == [3, 4] and again.written_steps == written


def test_a_wait_for_a_scheduled_process_is_saved_as_a_step() -> None:
    events = [
        {"kind": "click", "intent": "Submit", "candidates": [{"strategy": "role", "value": "button:Submit"}]},
        {"kind": "wait_job", "value": "last", "guide_step": 5},
    ]
    steps, _ = events_to_steps(events)
    assert steps[1] == {
        "action": "wait_job",
        "intent": "Wait for the scheduled process to finish",
        "value": "last",
        "options": {"timeout_s": 1800},
        "written_step": 5,
    }


def test_wait_for_process_is_added_after_an_action_while_recording() -> None:
    from quartermaster.recorder.recorder import Recorder

    rec = Recorder(test_id="t")
    rec.command("wait")
    assert rec.events == [] and "Submit the process first" in rec.message
    rec.events.append({"kind": "click", "intent": "Submit", "candidates": [{"strategy": "text", "value": "Submit"}]})
    rec.command("wait")
    assert rec.events[-1] == {"kind": "wait_job", "value": "last"} and "waits until the process ends" in rec.message
