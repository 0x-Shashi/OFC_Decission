"""Failure triage: the likely cause of a failed test, in plain words, and a draft Oracle service
request (SR) for the failures the quarterly update probably caused.

The cause is worked out from facts in the run history, never guessed by an AI:

    passed on an earlier release, fails on this one   the update probably changed something
    passed before on this same release                not the update: the data or the pod changed
    never passed                                      the test or its data, not Oracle

When OPENAI_API_KEY is set, the rule-based verdict is enriched by the OpenAI Decisions API
(gpt-6-luna): a predicate question confirms the cause probability and a score question rates
the severity. This runs in one batched Decisions API call and falls back silently if the key
is missing or the API is unreachable.

The SR draft is text for a person to read, change and paste into My Oracle Support.
Quartermaster never sends it anywhere.
"""

from __future__ import annotations

import os
from typing import Any

from quartermaster.evidence.document import _when, plain_error

_RELEASE = "release_change"
_SAME = "same_release"
_NEVER = "never_passed"


def likely_cause(item: dict[str, Any]) -> dict[str, Any] | None:
    """The likely cause of a failed test (an item of Needs attention), or None for other items."""
    kind = item.get("category")
    if kind not in ("assertion", "missing_element", "service_call", "timeout", "failure") or not item.get("test_id"):
        return None
    cause = _likely_cause_rule(item)
    return _decisions_enrich(cause, item) if cause is not None else None


def _likely_cause_rule(item: dict[str, Any]) -> dict[str, Any] | None:
    """Pure rule-based triage from run history facts (no AI)."""
    kind = item.get("category")
    good, now = item.get("last_good_release"), item.get("run_release")
    suggested = (
        "The suggested fix above is the most likely new name: use it first, then run the test again. "
        if item.get("suggestion")
        else ""
    )
    if kind == "timeout":
        return _cause(
            "pod_slow",
            "The pod was slow or busy",
            "A screen did not answer in time. Run the test again. If it fails at the same step again, "
            "open that screen on the pod by hand: if it is slow there too, tell the pod's administrator.",
        )
    if good and now and good != now:
        what = {
            "assertion": "a value on the screen is different",
            "service_call": "the service answered differently",
        }.get(kind, "an item is no longer found")
        return _cause(
            _RELEASE,
            f"Probably the {now} update",
            f"It passed on {good} and fails on {now}: {what}. {suggested}"
            "Look at the picture. If Oracle changed how the screen works, raise a service request with Oracle "
            "(Draft SR). If only the screen layout moved, record that step again.",
            sr=True,
        )
    if item.get("last_good_at"):
        return _cause(
            _SAME,
            "Not the update: the data or the pod changed",
            "It passed before on this same release, so the update is not the cause. Check the test data on "
            "the pod (a refresh from production can change it) and that the test user still has the same "
            "roles, then run it again.",
        )
    if kind == "service_call":
        return _cause(
            _NEVER,
            "The service address or its data needs fixing",
            "It has never passed, so Oracle is not the cause. Check the service address in the test (a typo, or "
            "a service this pod does not have) and the data it asks for, then run it again.",
        )
    if kind == "missing_element" and item.get("suggestion"):
        return _cause(
            _NEVER,
            "Try the suggested fix first",
            "It has never passed, and the item is not on the screen under the name the test knows. "
            f"{suggested}If the suggestion is wrong, the screen differs from when it was recorded "
            "(another user, another page or the item is not there): record the step again.",
        )
    if kind == "missing_element":
        return _cause(
            _NEVER,
            "The test needs fixing",
            "It has never passed. The test cannot find an item, so the screen differs from when it was "
            "recorded (another user, another page or the item is not there). Record the step again.",
        )
    return _cause(
        _NEVER,
        "The test or its data needs fixing",
        "It has never passed, so Oracle is not the cause. Check what the test expects against the picture: "
        "fix the expected value in the test, or the data on the pod.",
    )


def _decisions_enrich(cause: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Optionally enrich a rule-based cause with OpenAI Decisions API confidence + severity.

    Sends one batched Decisions API call with:
      - predicate: confirms the rule-based cause probability
      - score: rates business severity (Low / Medium / High)

    Falls back silently if OPENAI_API_KEY is not set or the API call fails.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return cause
    try:
        from quartermaster.ai.llm import OpenAIDecisionsLLM
        decisions = OpenAIDecisionsLLM(api_key=api_key)
        context = (
            f"Test failure cause assessed as: {cause['title']}. "
            f"Category: {cause['key']}. "
            f"Last good release: {item.get('last_good_release') or 'unknown'}. "
            f"Current release: {item.get('run_release') or 'unknown'}. "
            f"Failure kind: {item.get('category') or 'unknown'}."
        )
        answers = decisions.decide(
            context,
            questions=[
                {
                    "type": "predicate",
                    "name": "cause_correct",
                    "instructions": (
                        f"Is '{cause['title']}' the correct root cause for this Oracle Fusion test failure?"
                    ),
                },
                {
                    "type": "score",
                    "name": "severity",
                    "instructions": "How severe is this failure for the business?",
                    "levels": [
                        {"label": "Low",    "description": "Cosmetic; the workflow still completes."},
                        {"label": "Medium", "description": "A step fails but a workaround exists."},
                        {"label": "High",   "description": "A critical workflow is fully blocked."},
                    ],
                },
            ],
        )
        confidence = round(float(answers["cause_correct"]["probability"]), 2)
        severity_score = float(answers["severity"]["score"])
        severity_label = ["Low", "Medium", "High"][min(round(severity_score), 2)]
        return {
            **cause,
            "ai_confidence": confidence,
            "ai_severity": severity_label,
            "ai_engine": "OpenAI Decisions API (gpt-6-luna)",
        }
    except Exception:  # noqa: BLE001 — never crash triage over an AI enrichment failure
        return cause


def sr_draft(item: dict[str, Any], run: dict[str, Any]) -> dict[str, str]:
    """A draft service request for one failed test. `run` is the test's run.json."""
    steps: list[dict[str, Any]] = run.get("steps") or []
    failed = next((s for s in steps if s.get("status") == "failed"), None)
    n = (failed or {}).get("index", len(steps) - 1) + 1
    product = item.get("process") or item.get("module") or "Oracle Fusion"
    now = item.get("run_release") or run.get("release") or "the current release"
    good = item.get("last_good_release")
    intent = (failed or {}).get("intent") or item.get("intent") or f"step {n}"
    subject = f"{product}: “{intent}” no longer works after the update to {now}"[:240]
    lines = [
        f"Summary: {subject}",
        "",
        "Problem",
        f"After our pod was updated to Oracle release {now}, this business flow fails at step {n}"
        + (f". It worked on release {good} with the same steps and the same user." if good else "."),
        "",
        "Steps to reproduce",
    ]
    for s in steps[:n]:
        text = s.get("intent") or s.get("action") or ""
        if s.get("action") in ("fill", "select") and s.get("value"):
            text += f" (value: {s['value']})"
        lines.append(f"{s.get('index', 0) + 1}. {text}")
    compare = item.get("compare") or {}
    expected = (
        f'The screen shows "{compare["expected"]}".'
        if compare.get("expected")
        else (failed or {}).get("expected") or "The step works as it did on the earlier release."
    )
    actual = (
        f'The screen shows "{compare.get("observed")}".'
        if compare.get("observed")
        else plain_error((failed or {}).get("error")) or "The step fails."
    )
    lines += [
        "",
        "Expected result",
        expected,
        "",
        "Actual result",
        actual,
        "",
        "Environment",
        f"Pod: {run.get('environment_url') or 'not recorded'}",
        f"Oracle release now: {now}" + (f" (worked on {good})" if good else ""),
        f"Seen on: {_when(run.get('started_at') or item.get('at'))}",
        "",
        "Business impact",
        "[Say who is blocked and from when, for example: payroll cannot be run for 200 employees.]",
        "",
        "Attached",
        f"The test evidence document ({run.get('test_id', '')}, run {run.get('run_id', '')}) with a picture of "
        "every step, including the screen when it failed.",
    ]
    return {"subject": subject, "text": "\n".join(lines)}


def _cause(key: str, title: str, advice: str, *, sr: bool = False) -> dict[str, Any]:
    return {"key": key, "title": title, "advice": advice, "sr": sr}
