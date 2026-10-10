"""How good is the AI chosen in Settings at suggesting what a missing button, link or field became?

Quartermaster only ever shows the AI's pick as a *suggestion* that a person accepts, and it checks the pick (found
exactly once on the page, not a Save or Delete the step never mentions). Still, a model that often picks the wrong
control is a poor helper, and a person should know that before trusting it. This asks the AI a fixed set of made-up
questions (the golden cases in `suggest_cases.yaml`, with a right answer each, including "it is not on this screen")
and counts:

    correct        picked the right control
    right "none"   said it is not on the screen, and it is not
    missed         said none when the control was there (harmless, just unhelpful)
    wrong          picked something else (the harmful answer)
    unreadable     did not answer in the form asked for
    error          could not be asked (no key, no network): these are left out of the score

Nothing from your pod is sent: the cases are invented screens. It costs as many short questions as there are cases.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from quartermaster.ai.providers import AIError, parse_json
from quartermaster.domain.models import LocatorStrategy, Step

CASES = Path(__file__).with_name("suggest_cases.yaml")
Ask = Callable[[str, str], str]
GOOD_SCORE = 0.8  # share of cases answered right for "good"
USABLE_SCORE = 0.6


class CaseError(ValueError):
    """The golden cases file is wrong."""


@dataclass
class Case:
    id: str
    step: Step
    items: list[dict[str, str]]
    accept: list[str] = field(default_factory=list)  # names that are right; empty: nothing on the screen is right


def load_cases(path: Path = CASES) -> list[Case]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise CaseError(f"{path}: could not be read: {e}") from e
    if not isinstance(raw, list) or not raw:
        raise CaseError(f"{path}: expected a list of cases")
    cases: list[Case] = []
    seen: set[str] = set()
    for n, item in enumerate(raw, 1):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in seen:
            raise CaseError(f"case {n}: needs a unique id")
        seen.add(item["id"])
        try:
            step = Step.model_validate(item.get("step"))
        except ValueError as e:
            raise CaseError(f"case {item['id']}: the step is not valid: {e}") from e
        screen = item.get("screen")
        if (
            not isinstance(screen, list)
            or not screen
            or not all(isinstance(x, dict) and x.get("role") and x.get("name") for x in screen)
        ):
            raise CaseError(f"case {item['id']}: screen is a list of items with a role and a name")
        expect = item.get("expect")
        if not isinstance(expect, dict) or not (bool(expect.get("pick")) ^ bool(expect.get("none"))):
            raise CaseError(f"case {item['id']}: expect is either pick: [names] or none: true")
        accept = [str(x) for x in expect.get("pick") or []]
        names = {str(x["name"]) for x in screen}
        if any(a not in names for a in accept):
            raise CaseError(f"case {item['id']}: a right answer is not on the screen")
        cases.append(Case(item["id"], step, [{k: str(v) for k, v in x.items()} for x in screen], accept))
    return cases


class _Page:
    def __init__(self, items: list[dict[str, str]]) -> None:
        self.items = items

    def evaluate(self, script: str, *args: Any) -> dict[str, Any]:
        return {"items": self.items}


class _Driver:
    """Counts matches the way a page would, so a name that is on the screen twice is not found exactly once."""

    def __init__(self, items: list[dict[str, str]]) -> None:
        self.items = items
        self.page = _Page(items)

    def count(self, strategy: LocatorStrategy, value: str) -> int:
        fields = {"textbox", "combobox", "searchbox", "checkbox", "radio"}
        n = 0
        for it in self.items:
            names = {it["name"].lower(), (it.get("full") or "").lower()} - {""}
            if strategy is LocatorStrategy.LABEL:
                n += it["role"] in fields and it["name"].lower() == value.lower()
            elif strategy is LocatorStrategy.ROLE:
                role, _, name = value.partition(":")
                n += it["role"] == role and name.lower() in names
            elif strategy is LocatorStrategy.TEXT:
                n += value.lower() in {it["name"].lower(), (it.get("title") or "").lower()}
        return n


def run_case(case: Case, ask: Ask) -> dict[str, Any]:
    """Ask once and judge the answer. The pick is read from the AI's raw answer, then it is put through the same checks
    a real suggestion goes through, to see whether it would have reached the person."""
    from quartermaster.runner import suggest as engine

    assert case.step.target is not None
    items = [it for it in case.items if engine._usable(it)]
    pool = engine._compatible(case.step, items)
    raw: list[str] = []
    failed: list[AIError] = []

    def recording(system: str, prompt: str) -> str:
        try:
            answer = ask(system, prompt)
        except AIError as e:  # the engine treats "no answer" as none; here it is an error, not a score
            failed.append(e)
            raise
        raw.append(answer)
        return answer

    out: dict[str, Any] = {"id": case.id, "intent": case.step.intent, "wanted": case.accept or None}
    started = time.monotonic()
    try:
        proposal = engine._ask_ai(
            0, case.step, case.step.target.ordered()[0], _Driver(items), pool, engine._names(case.step), recording
        )
    except AIError as e:
        failed.append(e)
    if failed:
        return {**out, "outcome": "error", "why": str(failed[0])[:200], "ms": 0}
    out["ms"] = round((time.monotonic() - started) * 1000)
    readable, picked = _picked(raw[0] if raw else "", pool)
    if not readable:
        return {**out, "outcome": "unreadable", "picked": None, "reached": False}
    name = picked["name"] if picked else None
    out.update(picked=name, reached=proposal is not None, stopped=bool(picked) and proposal is None)
    if case.accept:
        out["outcome"] = "correct" if name in case.accept else "missed" if name is None else "wrong"
    else:
        out["outcome"] = "right_none" if name is None else "wrong"
    return out


def _picked(raw: str, pool: list[dict[str, str]]) -> tuple[bool, dict[str, str] | None]:
    """(whether the answer can be read, the element the AI chose: None when it chose none)."""
    try:
        answer = parse_json(raw)
    except AIError:
        return False, None
    if not isinstance(answer, dict) or "element" not in answer:
        return False, None
    number = answer["element"]
    if number is None:
        return True, None
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= len(pool):
        return False, None
    return True, pool[number - 1]


def run(
    cases: list[Case], ask: Ask, *, label: str = "", on_case: Callable[[int, int], None] | None = None
) -> dict[str, Any]:
    """Ask every case. Stops early when the AI cannot be reached at all (the first two questions fail)."""
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    for n, case in enumerate(cases, 1):
        results.append(run_case(case, ask))
        if on_case:
            on_case(n, len(cases))
        if len(results) == 2 and all(r["outcome"] == "error" for r in results):
            break
    return report(results, label=label, total=len(cases), seconds=time.monotonic() - started)


def report(results: list[dict[str, Any]], *, label: str, total: int, seconds: float) -> dict[str, Any]:
    count = {
        k: sum(r["outcome"] == k for r in results)
        for k in ("correct", "right_none", "missed", "wrong", "unreadable", "error")
    }
    asked = len(results) - count["error"]
    reached = sum(1 for r in results if r["outcome"] == "wrong" and r.get("reached"))
    stopped = sum(1 for r in results if r["outcome"] == "wrong" and not r.get("reached"))
    score = round((count["correct"] + count["right_none"]) / asked, 2) if asked else 0.0
    if not asked:
        verdict, advice = "not_run", "The AI could not be asked. Check the key and the model with Test the AI."
    elif count["wrong"] == 0 and score >= GOOD_SCORE:
        verdict, advice = "good", "Good: it picked the right control or said none, with no wrong pick."
    elif reached == 0 and score >= USABLE_SCORE:
        verdict, advice = (
            "usable",
            "Usable with care: its wrong picks were stopped by the checks, or it was often unhelpful. "
            "Read each suggestion.",
        )
    else:
        verdict, advice = (
            "weak",
            "Weak: it picked wrong controls that the checks could not rule out. Try a stronger model, or switch "
            "AI suggestions off.",
        )
    return {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "label": label,
        "total": total,
        "asked": asked,
        "counts": count,
        "wrong_reached": reached,
        "wrong_stopped": stopped,
        "score": score,
        "verdict": verdict,
        "advice": advice,
        "seconds": round(seconds, 1),
        "cases": results,
        "finished": len(results) == total,
    }
