"""Turn recorded browser events into test steps, as plain data.

Pure and dependency-free, so the rules can be checked anywhere; `recorder.events_to_test`
wraps the result in the domain model. Events come from capture.js:

    click           {"kind": "click", "intent", "candidates"}
    fill / select   {..., "value"}; a select may carry "pick", the suggestion clicked in a
                    type-ahead list when it differs from what was typed
    navigate        {"kind": "navigate", "value": "My Client Groups > Workforce Structures"}
    assert_text     {..., "value"}: a check the person added while recording
    assert_visible  {...}: a check that the element is shown
    note            {"kind": "note", "value": "..."}: what should happen at the step before it;
                    saved as that step's expected result

A fill or select marked {"sensitive": true} (masked while recording) is not written into the
test: its data entry reads ${env:<prefix>_<n>}, an environment variable set before replaying.
"""

from __future__ import annotations

from typing import Any

_KINDS = {"click", "fill", "select", "navigate", "assert_text", "assert_visible", "note", "wait_job"}
MASK = "••••••"
_VALUED = {"fill", "select", "assert_text"}
_STRATEGIES = {"label", "role", "test_id", "text", "css", "xpath"}


def _strategies(event: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {c["strategy"]: c["value"]}
        for c in event.get("candidates", [])
        if c.get("strategy") in _STRATEGIES and c.get("value")
    ]


def _key(event: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple((c.get("strategy", ""), c.get("value", "")) for c in event.get("candidates", []))


def events_to_steps(
    events: list[dict[str, Any]], secret_prefix: str = "QM_SECRET"
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return (steps, data). Typed and checked values become data entries (value1, value2...)
    so they can be changed without touching the steps; masked ones become secret1, secret2..."""
    kept: list[dict[str, Any]] = []
    for ev in events:
        kind = ev.get("kind")
        if kind not in _KINDS:
            continue
        last = kept[-1] if kept else None
        # Editing the same field twice in a row keeps only the final value.
        if last and kind in ("fill", "select") and last["kind"] == kind and _key(last) == _key(ev):
            kept[-1] = {**ev, "sensitive": bool(last.get("sensitive") or ev.get("sensitive"))}  # masked stays masked
            continue
        if last and kind == "navigate" and last["kind"] == "navigate" and last.get("value") == ev.get("value"):
            continue
        kept.append(ev)

    steps: list[dict[str, Any]] = []
    data: dict[str, str] = {}
    secrets = 0
    for n, ev in enumerate(kept, 1):
        kind = ev["kind"]
        if kind == "note":
            text = " ".join(str(ev.get("value") or "").split())
            if text and steps:
                steps[-1]["expected"] = f"{steps[-1].get('expected', '')} {text}".strip()
            continue
        if kind == "wait_job":
            # wait for the scheduled process just submitted (its number is read from the screen)
            steps.append(
                _written(
                    {
                        "action": "wait_job",
                        "intent": "Wait for the scheduled process to finish",
                        "value": str(ev.get("value") or "last"),
                        "options": {"timeout_s": int(ev.get("timeout_s") or 1800)},
                    },
                    ev,
                )
            )
            continue
        if kind == "navigate":
            path = str(ev.get("value") or "").strip()
            if path:
                steps.append(_written({"action": "navigate", "intent": f"Open {path}", "value": path}, ev))
            continue
        strategies = _strategies(ev)
        if not strategies:
            continue  # nothing we can reliably find again
        name = ev.get("intent") or f"step {n}"
        step: dict[str, Any] = {"action": kind, "target": {"strategies": strategies}}
        if kind in _VALUED and ev.get("sensitive") and kind != "assert_text":
            secrets += 1
            key = f"secret{secrets}"
            data[key] = f"${{env:{secret_prefix}_{secrets}}}"
            step["value"] = f"${{{key}}}"
        elif kind in _VALUED:
            key = f"value{len(data) - secrets + 1}"
            data[key] = str(ev.get("value", ""))
            step["value"] = f"${{{key}}}"
        if kind == "fill":
            step["intent"] = f"Enter {name}"
        elif kind == "select":
            step["intent"] = f"Choose {name}"
            pick = str(ev.get("pick") or "").strip()
            if pick and pick != str(ev.get("value", "")).strip():
                step["options"] = {"pick": pick}
        elif kind == "assert_text":
            step["intent"] = f"Check {name}"
        elif kind == "assert_visible":
            step["intent"] = f"Check {name} is shown"
        else:
            step["intent"] = f"Click {name}"
        steps.append(_written(step, ev))
    return steps, data


def _written(step: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Keep the number of the written step (of a manual scenario) the action was done for."""
    n = event.get("guide_step")
    if isinstance(n, int) and n >= 1:
        step["written_step"] = n
    return step


def preview(events: list[dict[str, Any]], secret_prefix: str = "QM_SECRET") -> list[dict[str, Any]]:
    """The steps as they would be saved now, for showing while recording (masked values hidden)."""
    steps, data = events_to_steps(events, secret_prefix)
    out = []
    for n, step in enumerate(steps, 1):
        value = step.get("value")
        if isinstance(value, str) and value.startswith("${") and value[2:-1] in data:
            value = data[value[2:-1]]
            value = MASK if value.startswith("${env:") else value
        out.append(
            {
                "number": n,
                "action": step["action"],
                "intent": step["intent"],
                "value": value,
                "expected": step.get("expected", ""),
            }
        )
    return out


def secret_names(data: dict[str, str]) -> list[str]:
    """Environment variables a recorded test needs for its masked values."""
    return [v[6:-1] for v in data.values() if v.startswith("${env:") and v.endswith("}")]
