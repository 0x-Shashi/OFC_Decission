"""Suggest a fix for a step whose item was not found on the screen.

When every way a test knows to find something fails (Oracle renamed a button, say), the step fails
as before. Afterwards, while the page is still open, two things are tried to find what it might
have become:

1. a name on the page that looks very like the old one ("Search by Name" for "Search: Name"): no
   AI, nothing leaves this computer;
2. only when an AI is set up in Settings: the AI is shown the step and the names of the buttons,
   links and fields on the screen (personal details masked, like in Prepare) and picks one, or says
   there is none.

Either way the result is only a *suggestion*: the step still fails, nothing is changed in the test,
and the choice is checked here (it must be found exactly once on the page, by the same code that
replays tests, and must not be a Save, Delete or similar button the step never mentions). A person
accepts or ignores it in Needs attention.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from difflib import SequenceMatcher
from typing import Any

from quartermaster.ai.providers import AIError, parse_json
from quartermaster.domain.models import HealingProposal, LocatorStrategy, Step

Ask = Callable[[str, str], str]
SIMILAR_MIN = 0.75  # how alike two names must be (0 to 1) to be suggested without an AI
AI_CONFIDENCE_MAX = 0.8  # an AI is never sure enough to say more than this
_FIELDS = {"textbox", "combobox", "searchbox", "checkbox", "radio"}

SYSTEM = """You help repair a regression test for Oracle Fusion Cloud. A test step could not find its \
button, link or field because the screen changed (Oracle updates the product every quarter). You are \
shown the step and a numbered list of what is on the screen now. Choose the ONE element that is almost \
certainly the same thing under a new name or place. If you are not sure, or the step's item is simply \
not on this screen, choose none: a wrong suggestion is worse than none.
Answer with JSON only: {"element": <number or null>, "confidence": <0 to 1>, "why": "<one short sentence>"}"""


def _decisions_confidence(step_intent: str, old_name: str, new_name: str, why: str) -> float | None:
    """Use OpenAI Decisions API predicate to validate a suggested fix.

    Asks: 'Is NEW_NAME the correct replacement for OLD_NAME in this Oracle Fusion step?'
    Returns the probability (0-1) if the API is available, or None to fall back to raw confidence.
    Falls back silently on any error.
    """
    import os
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None
    try:
        from quartermaster.ai.llm import OpenAIDecisionsLLM
        decisions = OpenAIDecisionsLLM(api_key=api_key)
        context = (
            f"Oracle Fusion test step: '{step_intent}'. "
            f"The step previously looked for: '{old_name}'. "
            f"The AI suggests the replacement is: '{new_name}'. "
            f"Reason given: {why}"
        )
        return decisions.predicate(
            context,
            "fix_correct",
            f"Is '{new_name}' the correct replacement for '{old_name}' in this Oracle Fusion test step?",
        )
    except Exception:  # noqa: BLE001
        return None


def make_healer(ask: Ask | None) -> Callable[[int, Step, Any], HealingProposal | None]:
    """The function the engine calls for a step whose item was not found. `ask` is None without an AI.
    When nothing is suggested, one line says why: it is in the run's messages (for the test team)."""

    def healer(index: int, step: Step, driver: Any) -> HealingProposal | None:
        proposal, reason = suggest_with_reason(index, step, driver, ask)
        if proposal is None:
            print(f"note: step {index + 1}: no suggested fix ({reason})", file=sys.stderr)
        return proposal

    return healer


def suggest(index: int, step: Step, driver: Any, ask: Ask | None) -> HealingProposal | None:
    return suggest_with_reason(index, step, driver, ask)[0]


def suggest_with_reason(index: int, step: Step, driver: Any, ask: Ask | None) -> tuple[HealingProposal | None, str]:
    """The suggestion, or None and the reason in a few words."""
    page = getattr(driver, "page", None)
    if step.target is None:
        return None, "the step has no item to look for"
    if page is None:
        return None, "no browser page to look at"
    from quartermaster.recorder.autopilot import SNAPSHOT_JS

    screen = page.evaluate(SNAPSHOT_JS)
    items = [it for it in (screen.get("items") if isinstance(screen, dict) else None) or [] if _usable(it)]
    if not items:
        return None, "no buttons, links or fields were found on the screen"
    old = step.target.ordered()[0]
    written = _names(step)
    pool = _compatible(step, items)

    why = f"no name among the {len(pool)} on the screen looks like {' or '.join(repr(w) for w in written) or 'it'}"
    found = _similar(written, pool)
    if found is not None:
        item, score = found
        way = _unique_way(driver, item)
        if way is None:
            why = f'"{item["name"]}" looks like it but cannot be found exactly once on the page'
        elif _risky(item, step):
            why = f'"{item["name"]}" looks like it but changes data, so it is not suggested'
        else:
            shown = item.get("title") or item["name"]
            return _proposal(
                index, step, old, way, round(score * 0.9, 2), "similar", f'"{shown}" looks like the old name'
            ), ""
    if ask is None:
        return None, why + "; no AI is set up to ask"
    proposal = _ask_ai(index, step, old, driver, pool, written, ask)
    return (proposal, "") if proposal is not None else (None, why + "; the AI chose none")


# ------------------------------------------------------------------ the AI


def _ask_ai(
    index: int,
    step: Step,
    old: tuple[LocatorStrategy, str],
    driver: Any,
    pool: list[dict[str, str]],
    written: list[str],
    ask: Ask,
) -> HealingProposal | None:
    prompt = "\n".join(
        [
            f"Step: {step.intent}",
            f"What should happen: {step.expected or 'not written'}",
            "It looked for: " + (" | ".join(written) or "(a technical address)"),
            "",
            "On the screen now:",
            *(f'[{n}] {it["role"]} "{it["name"]}"' for n, it in enumerate(pool, 1)),
        ]
    )
    try:
        answer = parse_json(ask(SYSTEM, prompt))
    except AIError:
        return None  # no answer is not a problem: the step has already failed on its own
    if not isinstance(answer, dict):
        return None
    number = answer.get("element")
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= len(pool):
        return None
    item = pool[number - 1]
    way = _unique_way(driver, item)
    if way is None or _risky(item, step):
        return None
    try:
        confidence = min(float(answer.get("confidence", 0.5)), AI_CONFIDENCE_MAX)
    except (TypeError, ValueError):
        confidence = 0.5
    why = " ".join(str(answer.get("why") or "").split())[:200]
    # Ask the Decisions API to validate this suggestion (predicate) — replaces raw model confidence
    validated = _decisions_confidence(step.intent, " or ".join(written), item["name"], why)
    if validated is not None:
        if validated < 0.5:
            return None  # Decisions API not convinced — suppress the suggestion
        confidence = min(round(validated, 2), AI_CONFIDENCE_MAX)
    return _proposal(index, step, old, way, max(0.0, round(confidence, 2)), "ai", why or "chosen by the AI")


# ------------------------------------------------------------------ checks


def _proposal(
    index: int,
    step: Step,
    old: tuple[LocatorStrategy, str],
    way: tuple[LocatorStrategy, str],
    confidence: float,
    source: str,
    why: str,
) -> HealingProposal:
    return HealingProposal(
        step_index=index, intent=step.intent, old=old, new=way, confidence=confidence, source=source, why=why
    )


def _usable(item: Any) -> bool:
    return isinstance(item, dict) and bool(item.get("name")) and bool(item.get("role"))


def _names(step: Step) -> list[str]:
    """The names the step looked for (its labels, link names and texts; not technical addresses)."""
    out: list[str] = []
    for kind, value in step.target.ordered() if step.target else []:
        if kind is LocatorStrategy.ROLE:
            out.append(value.partition(":")[2].strip() or value)
        elif kind in (LocatorStrategy.LABEL, LocatorStrategy.TEXT):
            out.append(value)
    return [n for n in dict.fromkeys(out) if n.strip()]


def _compatible(step: Step, items: list[dict[str, str]]) -> list[dict[str, str]]:
    """Fields for a step that types, anything else for a step that clicks or checks."""
    from quartermaster.domain.models import Action

    if step.action in (Action.FILL, Action.SELECT):
        return [it for it in items if it["role"] in _FIELDS] or items
    return items


def _similar(written: list[str], pool: list[dict[str, str]]) -> tuple[dict[str, str], float] | None:
    """The one name on the page that is clearly the most like what the step looked for."""
    scored: list[tuple[float, dict[str, str]]] = []
    for item in pool:
        # a Redwood card's link holds its title and a description: the title is what the step was written for
        shown = [t for t in (item["name"], item.get("title")) if t]
        score = max((_like(w, t) for w in written for t in shown), default=0.0)
        if score >= SIMILAR_MIN:
            scored.append((score, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.05:
        return None  # two names are equally alike: not a safe guess
    return scored[0][1], scored[0][0]


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())


def _like(a: str, b: str) -> float:
    """How alike two names are, from 0 to 1, ignoring case and punctuation."""
    return SequenceMatcher(None, _plain(a), _plain(b)).ratio()


def _unique_way(driver: Any, item: dict[str, str]) -> tuple[LocatorStrategy, str] | None:
    """A way to find this element again that finds exactly one thing on the page, as replays do."""
    role, name = item["role"], item["name"]
    full = item.get("full") or name
    ways: list[tuple[LocatorStrategy, str]] = []
    if role in _FIELDS:
        ways.append((LocatorStrategy.LABEL, name))
    if role not in ("text", "heading"):
        for n in dict.fromkeys([full, name]):
            ways.append((LocatorStrategy.ROLE, f"{role}:{n}"))
    for n in dict.fromkeys([name, item.get("title") or name]):
        ways.append((LocatorStrategy.TEXT, n))
    for strategy, value in ways:
        try:
            if driver.count(strategy, value) == 1:
                return strategy, value
        except Exception:  # a way the page cannot use is just not this one
            continue
    return None


def _risky(item: dict[str, str], step: Step) -> bool:
    """A button that changes data (Save, Delete...) is never suggested unless the step already speaks of it."""
    from quartermaster.recorder.autopilot import _RISKY

    word = _RISKY.search(item["name"])
    if word is None:
        return False
    text = f"{step.intent} {step.expected} " + " ".join(_names(step))
    return word.group(1).lower() not in text.lower()
