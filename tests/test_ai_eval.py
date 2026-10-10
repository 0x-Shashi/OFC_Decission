"""The AI quality check: golden questions with right answers, scored."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from test_service_api import app, call  # noqa: F401, F811  (app is a fixture)

from quartermaster.ai.evals import suggest as evals
from quartermaster.ai.providers import AIConfig, AIError
from quartermaster.cli import main

CASES = evals.load_cases()


def answers(case: evals.Case, wrong: bool = False) -> str:
    """What a perfect AI says to this case (or the first thing on the screen that is not right)."""
    pool = [it for it in case.items if it["name"]]
    if case.accept and not wrong:
        number = next(n for n, it in enumerate(pool, 1) if it["name"] in case.accept)
    elif wrong:
        number = next((n for n, it in enumerate(pool, 1) if it["name"] not in case.accept), 1)
    else:
        return json.dumps({"element": None, "confidence": 0.9, "why": "not on the screen"})
    return json.dumps({"element": number, "confidence": 0.9, "why": "it is the same thing"})


def oracle(wrong: bool = False) -> evals.Ask:
    by_prompt: dict[str, evals.Case] = {}
    for c in CASES:
        by_prompt[f"Step: {c.step.intent}"] = c

    def ask(system: str, prompt: str) -> str:
        case = by_prompt[prompt.splitlines()[0]]
        return answers(case, wrong)

    return ask


def test_the_golden_cases_are_valid_and_each_has_a_right_answer_on_its_screen() -> None:
    assert len(CASES) >= 12 and len({c.id for c in CASES}) == len(CASES)
    assert sum(1 for c in CASES if not c.accept) >= 4  # several cases where the right answer is "none"
    assert sum(1 for c in CASES if c.accept) >= 8
    for c in CASES:
        assert c.step.target is not None and c.items


def test_a_perfect_ai_gets_everything_right_and_no_screen_text_is_from_a_pod() -> None:
    report = evals.run(CASES, oracle())
    assert report["score"] == 1.0 and report["verdict"] == "good" and report["finished"]
    assert report["counts"]["wrong"] == report["counts"]["missed"] == report["counts"]["unreadable"] == 0
    assert report["counts"]["correct"] == sum(bool(c.accept) for c in CASES)
    assert report["counts"]["right_none"] == sum(not c.accept for c in CASES)


def test_an_ai_that_always_says_none_is_safe_but_not_helpful() -> None:
    report = evals.run(CASES, lambda s, p: '{"element": null, "confidence": 0, "why": "unsure"}')
    c = report["counts"]
    assert c["wrong"] == 0 and c["missed"] == sum(bool(x.accept) for x in CASES) and c["correct"] == 0
    assert report["verdict"] == "weak" and report["score"] < evals.USABLE_SCORE  # right only where the answer is none


def test_an_ai_that_picks_wrong_things_is_weak_and_the_harm_is_counted() -> None:
    report = evals.run(CASES, oracle(wrong=True))
    assert report["counts"]["wrong"] >= 8 and report["verdict"] == "weak"
    assert report["wrong_reached"] + report["wrong_stopped"] == report["counts"]["wrong"]
    assert report["wrong_reached"] >= 1  # some wrong picks would have reached the person


def test_the_checks_stop_a_dangerous_or_ambiguous_pick_and_the_report_says_so() -> None:
    by_id = {c.id: c for c in CASES}
    delete = by_id["only-a-dangerous-button-is-left"]  # picks "Delete Worker" for a step that goes back
    r = evals.run_case(delete, lambda s, p: '{"element": 1, "confidence": 0.9, "why": "x"}')
    assert r["outcome"] == "wrong" and r["picked"] == "Delete Worker" and r["stopped"] is True and r["reached"] is False
    twin = by_id["two-equal-buttons"]  # two identical buttons: cannot be found exactly once
    r = evals.run_case(twin, lambda s, p: '{"element": 1, "confidence": 0.9, "why": "x"}')
    assert r["outcome"] == "wrong" and r["reached"] is False
    fine = by_id["renamed-button"]
    r = evals.run_case(fine, lambda s, p: '{"element": 1, "confidence": 0.9, "why": "x"}')
    assert r["outcome"] == "correct" and r["reached"] is True


def test_save_is_not_blocked_when_the_step_itself_says_save() -> None:
    save = {c.id: c for c in CASES}["save-is-allowed-when-the-step-says-so"]
    r = evals.run_case(save, lambda s, p: '{"element": 1, "confidence": 0.9, "why": "x"}')
    assert r["outcome"] == "correct" and r["reached"] is True


@pytest.mark.parametrize(
    "answer",
    [
        "I think it is the first one.",
        "",
        "[1]",
        '{"confidence": 0.5}',
        '{"element": "one"}',
        '{"element": 99}',
        '{"element": true}',
    ],
)
def test_answers_in_the_wrong_form_are_counted_as_unreadable_not_as_picks(answer: str) -> None:
    r = evals.run_case(CASES[0], lambda s, p: answer)
    assert r["outcome"] == "unreadable" and r["picked"] is None


def test_a_reply_wrapped_in_text_or_a_code_block_is_still_read() -> None:
    r = evals.run_case(CASES[0], lambda s, p: '```json\n{"element": 1, "confidence": 0.8, "why": "same"}\n```')
    assert r["outcome"] == "correct"


def test_an_ai_that_cannot_be_reached_stops_early_and_is_not_scored() -> None:
    calls = []

    def down(system: str, prompt: str) -> str:
        calls.append(1)
        raise AIError("No API key yet.")

    report = evals.run(CASES, down)
    assert len(calls) == 2 and report["verdict"] == "not_run" and report["asked"] == 0 and not report["finished"]
    assert "Test the AI" in report["advice"]


def test_one_hiccup_does_not_stop_the_check() -> None:
    state = {"n": 0}
    good = oracle()

    def flaky(system: str, prompt: str) -> str:
        state["n"] += 1
        if state["n"] == 1:
            raise AIError("timeout")
        return good(system, prompt)

    report = evals.run(CASES, flaky)
    assert report["counts"]["error"] == 1 and report["asked"] == len(CASES) - 1 and report["score"] == 1.0


def test_bad_case_files_are_refused_with_a_reason(tmp_path: Path) -> None:
    def write(text: str) -> Path:
        p = tmp_path / "c.yaml"
        p.write_text(text)
        return p

    step = "step: {action: click, intent: x, target: {strategies: [{text: x}]}}"
    for text, why in (
        ("[]", "expected a list"),
        ("- {id: a}", "the step is not valid"),
        (f"- {{id: a, {step}, screen: [], expect: {{none: true}}}}", "screen is a list"),
        (f"- {{id: a, {step}, screen: [{{role: link, name: Y}}], expect: {{pick: [Z]}}}}", "right answer is not on"),
        (f"- {{id: a, {step}, screen: [{{role: link, name: Y}}], expect: {{}}}}", r"pick: \[names\] or none: true"),
    ):
        with pytest.raises(evals.CaseError, match=why):
            evals.load_cases(write(text))


# ------------------------------------------------------------------ the command and the service


def test_the_command_needs_an_ai_and_prints_the_score(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["eval-ai"]) == 2
    assert "choose an AI" in capsys.readouterr().err
    monkeypatch.setenv("QM_TEST_KEY", "k")
    good = oracle()
    from quartermaster.ai import providers

    monkeypatch.setattr(providers, "chat", lambda config, system, prompt, **kw: good(system, prompt))
    rc = main(
        ["eval-ai", "--ai-provider", "openai", "--ai-model", "m", "--ai-key-env", "QM_TEST_KEY", "--min-score", "0.9"]
    )
    out = capsys.readouterr().out
    assert rc == 0 and "100%" in out and "Good:" in out and "CORRECT" in out


def wait_for(check: Any, seconds: float = 20) -> Any:
    end = time.time() + seconds
    while time.time() < end:
        if value := check():
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_the_service_runs_the_check_keeps_the_scores_and_audits_it(tmp_path: Path) -> None:
    from quartermaster.service.aieval import AiEval

    audit: list[tuple[str, str, dict[str, Any]]] = []
    ev = AiEval(tmp_path / "ai_eval.json", audit=lambda w, s, d: audit.append((w, s, d)))
    assert ev.view()["last"] is None and ev.view()["questions"] == len(CASES)
    config = AIConfig("openai", "model-a", "https://api.example.com/v1", "SOME_KEY")
    with pytest.raises(ValueError, match="No API key yet"):
        ev.start(config)  # no key, no question asked
    ev.start(config, ask=oracle())
    done = wait_for(lambda: (v := ev.view())["last"] and not v["running"] and v)
    assert done["last"]["verdict"] == "good" and done["last"]["label"].endswith("model-a")
    assert [a[0] for a in audit] == ["Started the AI quality check", "Finished the AI quality check"]
    assert audit[-1][2]["score"] == "100%"
    ev.start(AIConfig("openai", "model-b", "https://api.example.com/v1", "SOME_KEY"), ask=oracle(wrong=True))
    two = wait_for(
        lambda: (v := ev.view())["last"] and v["last"]["label"].endswith("model-b") and not v["running"] and v
    )
    assert [h["label"].split(", ")[-1] for h in two["history"]] == ["model-b", "model-a"]
    assert two["last"]["verdict"] == "weak"


def test_only_one_check_runs_at_a_time(tmp_path: Path) -> None:
    from quartermaster.service.aieval import AiEval

    ev = AiEval(tmp_path / "e.json")
    gate: list[Any] = []

    def slow(system: str, prompt: str) -> str:
        time.sleep(0.3)
        return '{"element": null}'

    config = AIConfig("openai", "m", "https://api.example.com/v1", "")
    ev.start(config, ask=slow)
    with pytest.raises(ValueError, match="already running"):
        ev.start(config, ask=slow)
    wait_for(lambda: not ev.view()["running"], 30)
    assert gate == []


def test_the_api_refuses_without_an_ai_and_shows_the_state(app: Any) -> None:  # noqa: F811
    from quartermaster.service.api import ApiError

    assert call(app, "GET", "/api/ai/eval")["last"] is None
    with pytest.raises(ApiError, match="Choose an AI provider"):
        call(app, "POST", "/api/ai/eval", {})
