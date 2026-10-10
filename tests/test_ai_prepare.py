"""Preparing a manual scenario with an AI: any provider, the safety rules, and a real browser run.
No real AI is called: a local stand-in server answers like the providers do, or a scripted 'AI'."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from test_guided import PAGE, SCENARIO, FakePage
from test_recorder import CHROMIUM, POD

from quartermaster.ai import providers
from quartermaster.ai.providers import AIConfig, AIError, chat, check_settings, config_from, parse_json
from quartermaster.domain.models import Environment, EnvironmentKind, StepStatus
from quartermaster.dsl.loader import load_test
from quartermaster.recorder.autopilot import Autopilot
from quartermaster.recorder.guided import Guide
from quartermaster.recorder.recorder import Recorder

# ---------------------------------------------------------------------- providers


class FakeProvider(BaseHTTPRequestHandler):
    seen: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeProvider.seen.append(
            {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}
        )
        if self.headers.get("x-api-key") == "unscoped" and not self.headers.get("anthropic-workspace-id"):
            data = json.dumps(
                {
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "This API key is not scoped to a workspace, so this request must include"
                        " the anthropic-workspace-id header with the ID of the workspace to use.",
                    },
                }
            ).encode()
            self.send_response(400)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if body.get("model") == "reasoner" and ("max_tokens" in body or "temperature" in body):
            # like OpenAI's newer models: one unsupported parameter at a time
            param = "max_tokens" if "max_tokens" in body else "temperature"
            message = (
                "Unsupported parameter: 'max_tokens' is not supported with this model."
                " Use 'max_completion_tokens' instead."
                if param == "max_tokens"
                else "Unsupported value: 'temperature' does not support 0 with this model."
            )
            data = json.dumps({"error": {"message": message, "param": param, "code": "unsupported_parameter"}}).encode()
            self.send_response(400)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.headers.get("x-api-key") == "bad" or self.headers.get("Authorization") == "Bearer bad":
            self.send_response(401)
            self.end_headers()
            return
        if self.path.endswith("/messages"):
            reply = {"content": [{"type": "text", "text": "OK from anthropic"}]}
        else:
            reply = {"choices": [{"message": {"content": "OK from " + body["model"]}}]}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    srv = HTTPServer(("127.0.0.1", 0), FakeProvider)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakeProvider.seen.clear()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def test_any_openai_compatible_provider_and_anthropic(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_KEY", "secret-key")
    openai_like = AIConfig("openrouter", "some/model", server, "MY_KEY")
    assert chat(openai_like, "sys", "Mail jane@example.com, card 4111 1111 1111 1111") == "OK from some/model"
    sent = FakeProvider.seen[-1]
    assert sent["path"] == "/v1/chat/completions" and sent["headers"]["authorization"] == "Bearer secret-key"
    user = sent["body"]["messages"][1]["content"]
    assert "jane@example.com" not in user and "4111" not in user  # masked before it leaves this computer

    anthropic_cfg = AIConfig("anthropic", "sonnet-5-5", server, "MY_KEY")
    assert chat(anthropic_cfg, "sys", "hello") == "OK from anthropic"
    assert (
        FakeProvider.seen[-1]["path"] == "/v1/messages"
        and FakeProvider.seen[-1]["headers"]["x-api-key"] == "secret-key"
    )

    local = AIConfig("ollama", "llama", server, "")  # no key needed
    assert chat(local, "sys", "hi") == "OK from llama" and "authorization" not in FakeProvider.seen[-1]["headers"]

    monkeypatch.setenv("MY_KEY", "bad")
    with pytest.raises(AIError, match="refused the key"):
        chat(openai_like, "sys", "hi")
    assert providers.check(openai_like)["ok"] is False


def test_newer_openai_models_without_max_tokens_or_temperature(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_KEY", "secret-key")
    reasoner = AIConfig("openai", "reasoner", server, "MY_KEY")
    assert chat(reasoner, "sys", "hi", max_tokens=10) == "OK from reasoner"
    sent = FakeProvider.seen[-1]["body"]
    assert "max_tokens" not in sent and "temperature" not in sent and sent["max_completion_tokens"] >= 4000
    calls = len(FakeProvider.seen)
    assert chat(reasoner, "sys", "again") == "OK from reasoner"
    assert len(FakeProvider.seen) == calls + 1  # remembered: no rejected request the second time
    older = AIConfig("openai", "older-model", server, "MY_KEY")
    assert chat(older, "sys", "hi") == "OK from older-model"
    assert FakeProvider.seen[-1]["body"]["max_tokens"] == 800  # other models are sent the same as before


def test_an_anthropic_key_not_tied_to_a_workspace(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_KEY", "unscoped")
    anthropic_cfg = AIConfig("anthropic", "sonnet-5-5", server, "MY_KEY")
    with pytest.raises(AIError, match="Workspace ID in Settings"):
        chat(anthropic_cfg, "sys", "hi")
    config = config_from(
        {"ai_provider": "anthropic", "ai_base_url": server, "ai_key_env": "MY_KEY", "ai_workspace": "wrkspc_01X"}
    )
    assert chat(config, "sys", "hi") == "OK from anthropic"
    assert FakeProvider.seen[-1]["headers"]["anthropic-workspace-id"] == "wrkspc_01X"
    # only Anthropic uses it; other providers never get the header
    assert config_from({"ai_provider": "openai", "ai_workspace": "wrkspc_01X"}).workspace == ""
    assert check_settings({"ai_workspace": " wrkspc_01X "}) == {"ai_workspace": "wrkspc_01X"}
    with pytest.raises(ValueError, match="workspace ID"):
        check_settings({"ai_workspace": "wrk space"})


def test_settings_choose_a_provider_but_never_store_a_key() -> None:
    assert config_from({"ai_provider": "anthropic"}).model == "sonnet-5-5"
    gemini = config_from({"ai_provider": "gemini", "ai_model": "my-model"})
    assert (
        gemini.base_url.startswith("https://generativelanguage.googleapis.com") and gemini.key_env == "GEMINI_API_KEY"
    )
    assert "GEMINI_API_KEY" in gemini.problem({})
    assert gemini.problem({"GEMINI_API_KEY": "k"}) == ""
    assert "model" in config_from({"ai_provider": "openai"}).problem({"OPENAI_API_KEY": "k"})
    assert config_from({"ai_provider": "ollama", "ai_model": "llama"}).problem({}) == ""
    assert "https" in AIConfig("custom", "m", "ftp://x", "").problem({})
    with pytest.raises(ValueError, match="NAME of an environment variable"):
        check_settings({"ai_key_env": "sk-123 my key"})
    with pytest.raises(ValueError, match="unknown AI provider"):
        check_settings({"ai_provider": "skynet"})
    assert parse_json('Sure!\n```json\n{"do": "done", "why": "ok"}\n```') == {"do": "done", "why": "ok"}
    with pytest.raises(AIError):
        parse_json("no json here")


def test_settings_api_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_service_api import call

    from quartermaster.service.api import App

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    (tmp_path / "tests").mkdir()
    app = App(tests_root=tmp_path / "tests", evidence_root=tmp_path / "ev", data_dir=tmp_path / ".qm")
    ai = call(app, "GET", "/api/status")["ai"]
    assert ai["provider"] == "" and "No AI" in ai["problem"] and len(ai["presets"]) >= 8
    call(app, "POST", "/api/settings", {"ai_provider": "deepseek", "ai_model": "deepseek-chat"})
    ai = call(app, "GET", "/api/status")["ai"]
    assert (ai["key_env"], ai["key_set"]) == ("DEEPSEEK_API_KEY", False) and "DEEPSEEK_API_KEY" in ai["problem"]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-very-secret-123")
    ai = call(app, "GET", "/api/status")["ai"]
    assert ai["key_set"] is True and ai["problem"] == ""
    assert "sk-very-secret-123" not in json.dumps(call(app, "GET", "/api/status"))
    assert "sk-very-secret-123" not in (tmp_path / ".qm" / "settings.json").read_text()


# ---------------------------------------------------------------------- the AI loop (no browser)


class Locator:
    def __init__(self, page: ScriptedPage, key: str) -> None:
        self.page, self.key = page, key

    def count(self) -> int:
        if self.key in self.page.counts:
            return self.page.counts[self.key]
        # a text not set up: on the page when the current screen shows it
        shown = self.page.screens[self.page.screen].get("texts", [])
        return 1 if self.key.startswith("text:") and self.key[5:] in shown else 0

    @property
    def first(self) -> Locator:
        return self

    def click(self, timeout: float) -> None:
        self.page.done.append(("click", self.key))
        self.page.screen = self.page.after.get(self.key, self.page.screen)

    def fill(self, value: str, timeout: float) -> None:
        self.page.done.append(("fill", self.key, value))


class ScriptedPage(FakePage):
    def __init__(self, screens: dict[str, dict[str, Any]], start: str, counts: dict[str, int]) -> None:
        super().__init__()
        self.screens, self.screen, self.counts = screens, start, counts
        self.after = {f"role:link:{n}": n for n in screens}
        self.done: list[tuple[str, ...]] = []
        self.toggles: dict[str, dict[str, Any]] = {}  # section heading -> its toggle (see TOGGLE_JS)

    def evaluate(self, script: str, arg: Any = None) -> Any:
        if "setAttribute('data-qm-toggle'" in script:
            return self.toggles.get(arg)
        if "innerText.length" in script:
            return sum(len(t) for t in self.screens[self.screen]["texts"])
        return self.screens[self.screen]

    def locator(self, css: str) -> Locator:
        return Locator(self, f"css:{css}")

    def get_by_role(self, role: str, name: str, exact: bool) -> Locator:
        return Locator(self, f"role:{role}:{name}")

    def get_by_text(self, text: str, exact: bool) -> Locator:
        return Locator(self, f"text:{text}")

    def get_by_label(self, text: str, exact: bool) -> Locator:
        return Locator(self, f"label:{text}")


def screen(*links: str, texts: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"title": "", "headings": [], "items": [{"role": "link", "name": n} for n in links], "texts": list(texts)}


def scripted_ai(answers: list[dict[str, Any]]) -> Any:
    prompts: list[str] = []

    def ask(system: str, prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps(answers.pop(0))

    ask.prompts = prompts  # type: ignore[attr-defined]
    return ask


def test_the_ai_does_each_step_and_adds_checks(tmp_path: Path) -> None:
    screens = {
        "home": screen("Me", "Delete"),
        "Me": screen("Personal Information"),
        "Personal Information": screen("My Compensation"),
        "My Compensation": screen("Me", texts=("My Compensation", "Current Salary")),
    }
    counts = {f"role:link:{n}": 1 for n in screens} | {"text:Current Salary": 1}
    page = ScriptedPage(screens, "home", counts)
    recorder = Recorder(test_id="t")
    guide = Guide(SCENARIO, tmp_path / "run")
    ask = scripted_ai(
        [
            {"do": "done", "why": "already signed in"},
            {"do": "click", "element": 1},
            {"do": "done"},
            {"do": "click", "element": 1},
            {"do": "done"},
            {"do": "click", "element": 1},
            {"do": "done"},
            {"check": [2]},
        ]
    )
    assert Autopilot(page, guide, recorder, ask).run() is True
    assert [d[1] for d in page.done] == ["role:link:Me", "role:link:Personal Information", "role:link:My Compensation"]
    assert [e["kind"] for e in recorder.events] == ["click", "click", "click", "assert_visible"]
    assert recorder.events[0]["candidates"][0] == {"strategy": "role", "value": "link:Me"}
    assert recorder.events[-1]["candidates"] == [{"strategy": "text", "value": "Current Salary"}]
    assert all(s["status"] == "passed" and s["picture"] for s in guide.state())
    assert '[1] link "Me"' in ask.prompts[0] and "Step 1 of 4: Login" in ask.prompts[0]


def test_done_is_not_taken_while_what_the_step_selects_was_not_clicked(tmp_path: Path) -> None:
    screens = {
        "home": screen("Me"),
        "Me": screen("Personal Information"),
        "Personal Information": screen("My Compensation"),
        "My Compensation": screen("Me", texts=("Current Salary",)),
    }
    counts = {f"role:link:{n}": 1 for n in screens} | {"text:Current Salary": 1}
    page = ScriptedPage(screens, "home", counts)
    recorder = Recorder(test_id="t")
    ask = scripted_ai(
        [{"do": "done"}]
        + [{"do": "click", "element": 1}, {"do": "done"}] * 2
        + [{"do": "done", "why": "the card is shown"}, {"do": "click", "element": 1}, {"do": "done"}]
        + [{"check": [1]}]
    )
    assert Autopilot(page, Guide(SCENARIO, tmp_path / "run"), recorder, ask).run() is True
    assert page.done[-1] == ("click", "role:link:My Compensation")  # clicked after being told
    assert 'step asks for "My Compensation"' in ask.prompts[6]


COMP_FIELDS = [
    "Current Salary (Salary,Adjustment,Start Date,Annual Salary)",
    "Additional Compensation (Plan,Option,Amount)",
    "Personal Contribution(N/A)",
]


def test_the_fields_to_check_are_read_from_the_script() -> None:
    from quartermaster.recorder.autopilot import _field_groups

    assert _field_groups([*COMP_FIELDS, "Legal Employer"]) == [
        ("Current Salary", ["Salary", "Adjustment", "Start Date", "Annual Salary"]),
        ("Additional Compensation", ["Plan", "Option", "Amount"]),
        ("Legal Employer", []),
    ]


def comp_run(tmp_path: Path, last: dict[str, Any]) -> tuple[Autopilot, ScriptedPage, Recorder, Guide]:
    """My Compensation with the script's fields to check; `last` is the Compensation page."""
    screens = {
        "home": screen("Me"),
        "Me": screen("Personal Information"),
        "Personal Information": screen("My Compensation"),
        "My Compensation": last,
        "open": screen(
            texts=("Current Salary", "Salary", "Annual Salary", "Additional Compensation", "Plan", "Amount")
        ),
    }
    page = ScriptedPage(screens, "home", {f"role:link:{n}": 1 for n in screens})
    # the arrow next to the heading opens the section (a click on the heading text does nothing)
    page.toggles["Additional Compensation"] = {"expanded": "false", "role": "button", "name": "Additional Compensation"}
    page.counts["role:button:Additional Compensation"] = 1
    page.after['css:[data-qm-toggle="1"]'] = "open"
    recorder = Recorder(test_id="t")
    guide = Guide({**SCENARIO, "fields": COMP_FIELDS}, tmp_path / "run")
    answers = [{"do": "done"}] + [{"do": "click", "element": 1}, {"do": "done"}] * 3
    return Autopilot(page, guide, recorder, scripted_ai(answers)), page, recorder, guide


def test_the_fields_the_script_lists_are_checked_and_a_closed_section_is_opened(tmp_path: Path) -> None:
    closed = screen(texts=("Current Salary", "Salary", "Annual Salary", "Additional Compensation"))
    pilot, page, recorder, _ = comp_run(tmp_path, closed)
    assert pilot.run() is True, pilot.reason
    checks = [e["intent"] for e in recorder.events if e["kind"] == "assert_visible"]
    assert checks == ["Salary", "Annual Salary", "Plan", "Amount"]  # not just the section headings
    assert {
        "kind": "click",
        "intent": "Open Additional Compensation",
        "candidates": [{"strategy": "role", "value": "button:Additional Compensation"}],
        "guide_step": 4,  # done for the last written step, "Select My Compensation"
    } in recorder.events


def test_a_page_without_the_fields_to_check_stops_instead_of_passing(tmp_path: Path) -> None:
    # Current Salary shows "There's nothing here so far": the test user has no salary on the pod
    empty = screen(texts=("Current Salary", "There's nothing here so far.", "Additional Compensation"))
    pilot, page, recorder, guide = comp_run(tmp_path, empty)
    page.after['css:[data-qm-toggle="1"]'] = "My Compensation"  # and the section stays empty
    assert pilot.run() is False
    assert "does not show Current Salary (Salary, Adjustment, Start Date, Annual Salary)" in pilot.reason
    assert "test user may have no data" in pilot.reason
    assert guide.state()[-1]["status"] == "failed"


def test_an_empty_section_is_left_open_for_the_picture_and_the_diary_says_why(tmp_path: Path) -> None:
    empty = screen(texts=("Current Salary", "There's nothing here so far.", "Additional Compensation"))
    pilot, page, recorder, guide = comp_run(tmp_path, empty)
    page.screens["empty open"] = screen(texts=(*empty["texts"], "No additional compensation."))
    page.after['css:[data-qm-toggle="1"]'] = "empty open"  # it opens, but holds no fields
    assert pilot.run() is False and "test user may have no data" in pilot.reason
    assert page.screen == "empty open"  # left open: the picture shows that it is empty
    diary = (tmp_path / "run" / "ai-diary.txt").read_text(encoding="utf-8")
    assert "Section Additional Compensation: opened, but its fields are not in it" in diary


def test_a_section_that_was_open_is_not_left_closed(tmp_path: Path) -> None:
    empty = screen(texts=("Current Salary", "There's nothing here so far.", "Additional Compensation"))
    pilot, page, recorder, guide = comp_run(tmp_path, empty)
    # Current Salary's toggle does not say whether it is open; clicking it closes the section
    page.toggles["Current Salary"] = {"expanded": None, "role": "button", "name": "Collapse"}
    page.toggles.pop("Additional Compensation")
    page.screens["closed"] = screen(texts=("Current Salary", "Additional Compensation"))
    page.after['css:[data-qm-toggle="1"]'] = "closed"
    page.after["css:reopen"] = "My Compensation"
    clicks: list[str] = []
    original = Locator.click

    def click(self: Locator, timeout: float) -> None:
        clicks.append(self.key)
        if self.key == 'css:[data-qm-toggle="1"]' and len(clicks) > 1 and page.screen == "closed":
            page.screen = "My Compensation"  # the second click opens it again
            return
        original(self, timeout)

    Locator.click = click  # type: ignore[method-assign]
    try:
        assert pilot.run() is False
    finally:
        Locator.click = original  # type: ignore[method-assign]
    assert page.screen == "My Compensation"  # opened again: the picture shows it as it was
    diary = (tmp_path / "run" / "ai-diary.txt").read_text(encoding="utf-8")
    assert "Section Current Salary: it was open already" in diary


def test_a_scenario_always_gets_a_check_even_when_the_ai_chooses_none(tmp_path: Path) -> None:
    screens = {
        "home": screen("Me"),
        "Me": screen("Personal Information"),
        "Personal Information": screen("My Compensation"),
        "My Compensation": {**screen("Me", texts=("Salary",)), "headings": ["My Compensation"]},
    }
    # "Salary" is on the page twice, so a replay could not use it; the page heading is used instead
    counts = {f"role:link:{n}": 1 for n in screens} | {"text:Salary": 2, "text:My Compensation": 1}
    page = ScriptedPage(screens, "home", counts)
    recorder = Recorder(test_id="t")
    guide = Guide(SCENARIO, tmp_path / "run")
    answers = [{"do": "done"}] + [{"do": "click", "element": 1}, {"do": "done"}] * 3 + [{"check": [1]}]
    assert Autopilot(page, guide, recorder, scripted_ai(answers)).run() is True
    assert recorder.events[-1] == {
        "kind": "assert_visible",
        "intent": "My Compensation",
        "candidates": [{"strategy": "text", "value": "My Compensation"}],
        "guide_step": 4,
    }
    assert "Checks added: My Compensation" in (tmp_path / "run" / "ai-diary.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("answers", "why"),
    [
        ([{"do": "done"}, {"do": "click", "element": 2}], 'click "Delete", which can change data'),
        ([{"do": "done"}, {"do": "fill", "element": 1, "value": "12345"}], "does not give the value"),
        ([{"do": "done"}] + [{"do": "click", "element": 9}] * 5, "not finished after 5 actions"),  # told, then gives up
        ([{"do": "done"}, {"do": "stuck", "why": "needs a supplier name"}], "needs a supplier name"),
        ([{"do": "done"}] + [{"do": "click", "element": 1}] * 5, "not finished after 5 actions"),
    ],
)
def test_the_ai_stops_rather_than_guess(tmp_path: Path, answers: list[dict[str, Any]], why: str) -> None:
    page = ScriptedPage({"home": screen("Me", "Delete")}, "home", {"role:link:Me": 1, "role:link:Delete": 1})
    page.after = {}  # clicking changes nothing, so the step never completes
    guide = Guide(SCENARIO, tmp_path / "run")
    pilot = Autopilot(page, guide, Recorder(test_id="t"), scripted_ai(answers))
    assert pilot.run() is False and why in pilot.reason
    marks = guide.state()
    assert marks[0]["status"] == "passed" and marks[1]["status"] == "failed" and "status" not in marks[2]
    result = guide.result(test_id="t", title="x", environment="e", environment_url=POD, release=None, run_id="r")
    assert result.status is StepStatus.FAILED
    diary = (tmp_path / "run" / "ai-diary.txt").read_text(encoding="utf-8")  # why it stopped, for a person
    assert "Step 2:" in diary and "AI answered:" in diary and "STOPPED:" in diary and why in diary


def test_stop_from_the_web_page(tmp_path: Path) -> None:
    page = ScriptedPage({"home": screen("Me")}, "home", {"role:link:Me": 1})
    pilot = Autopilot(
        page, Guide(SCENARIO, tmp_path / "r"), Recorder(test_id="t"), scripted_ai([]), should_stop=lambda: True
    )
    assert pilot.run() is False and "stopped by the tester" in pilot.reason


def test_prepare_saves_a_draft_only_when_every_step_was_done(tmp_path: Path) -> None:
    import argparse

    from quartermaster.cli import _finish_by_hand

    evidence = tmp_path / "ev"
    env = Environment(name="f", url=POD, kind=EnvironmentKind.DEV)

    def args(name: str) -> argparse.Namespace:
        return argparse.Namespace(
            id="manual.x.ess-001",
            title="My Compensation",
            module="HCM",
            product="GHR",
            persona="",
            process="",
            release="26C",
            tester="AI (OpenAI, m)",
            out=str(tmp_path / f"{name}.yaml"),
            evidence=str(evidence),
        )

    rec = Recorder(test_id="manual.x.ess-001")
    rec.guide = Guide(SCENARIO, evidence / "manual.x.ess-001" / "r1")
    rec.guide.mark("1 pass", FakePage())
    rec.guide.mark("2 fail The AI could not do this step: x", FakePage())
    rec.events.append({"kind": "click", "intent": "Me", "candidates": [{"strategy": "text", "value": "Me"}]})
    assert _finish_by_hand(args("partial"), rec, env, "r1", mode="ai", save_test=False) == 0
    assert not (tmp_path / "partial.yaml").exists()
    record = json.loads((evidence / "manual.x.ess-001" / "r1" / "run.json").read_text())
    assert record["mode"] == "ai" and record["status"] == "failed" and record["executed_by"].startswith("AI")


# ---------------------------------------------------------------------- a real browser


def browser_ai(system: str, prompt: str) -> str:
    """A scripted stand-in for an AI that reads the screen like a real one would."""
    if '"check"' in system:
        texts = re.findall(r"^\[(\d+)\] (.+)$", prompt, re.M)
        return json.dumps({"check": [int(n) for n, t in texts if t == "Current Salary"]})
    step = re.search(r"^Step \d+ of \d+: (.+)$", prompt, re.M).group(1)  # type: ignore[union-attr]
    if step == "Login" or "nothing yet" not in prompt:
        return json.dumps({"do": "done"})
    wanted = step.removeprefix("Select ").strip()
    for n, _role, name in re.findall(r'^\[(\d+)\] (\w+) "(.+)"$', prompt, re.M):
        if name == wanted:
            return json.dumps({"do": "click", "element": int(n), "why": f"the step says {wanted}"})
    return json.dumps({"do": "stuck", "why": "not on the screen"})


def test_prepared_in_a_real_browser_then_it_plays_by_itself(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    from quartermaster.recorder.recorder import events_to_test, to_yaml
    from quartermaster.runner.engine import run_test
    from quartermaster.runner.playwright_driver import PlaywrightDriver

    environ = {"QM_FUSION_USER": "Mock User", "QM_FUSION_PASSWORD": "not-a-real-secret"}
    if CHROMIUM:
        environ["QM_CHROMIUM_PATH"] = CHROMIUM
    env = Environment(name="mock", url=POD, kind=EnvironmentKind.DEV)

    def serve(context: Any) -> None:
        context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=PAGE))

    driver = PlaywrightDriver(evidence_dir=str(tmp_path), environ=environ, context_hook=serve)
    driver.open(env, "")
    recorder = Recorder(test_id="manual.ess.ess-001")
    recorder.guide = Guide(SCENARIO, tmp_path / "run")
    try:
        assert Autopilot(driver.page, recorder.guide, recorder, browser_ai, settle=driver._settle).run() is True
    finally:
        driver.close()

    assert all(s["status"] == "passed" and s["picture"] for s in recorder.guide.state())
    test = events_to_test(
        recorder.events,
        test_id="manual.ess.ess-001",
        title="My Compensation",
        module="HCM",
        product="Global Human Resources",
    )
    out = tmp_path / "ess-001.yaml"
    out.write_text(to_yaml(test))
    saved = load_test(out)
    assert [s.action.value for s in saved.steps] == ["click", "click", "click", "assert_visible"]
    assert "not-a-real-secret" not in out.read_text()

    play = PlaywrightDriver(evidence_dir=str(tmp_path), environ=environ, context_hook=serve)
    result = run_test(saved, env, play)
    assert result.status is StepStatus.PASSED, [s.error for s in result.steps]


def test_a_key_pasted_in_settings_is_kept_in_memory_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_service_api import call

    from quartermaster.service.api import ApiError, App

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # also undoes what the test sets
    (tmp_path / "tests").mkdir()
    app = App(tests_root=tmp_path / "tests", evidence_root=tmp_path / "ev", data_dir=tmp_path / ".qm")
    with pytest.raises(ApiError, match="choose and save an AI provider first"):
        call(app, "POST", "/api/ai/key", {"key": "sk-pasted-secret-1"})
    call(app, "POST", "/api/settings", {"ai_provider": "openai", "ai_model": "gpt-x"})
    assert "Paste it in Settings" in call(app, "GET", "/api/status")["ai"]["problem"]
    with pytest.raises(ApiError, match="does not look like an API key"):
        call(app, "POST", "/api/ai/key", {"key": "sk pasted with spaces"})

    ai = call(app, "POST", "/api/ai/key", {"key": "  sk-pasted-secret-1 "})
    assert (ai["key_set"], ai["key_source"], ai["problem"]) == (True, "entered", "")
    assert "sk-pasted-secret-1" not in json.dumps(call(app, "GET", "/api/status"))
    assert all(b"sk-pasted-secret-1" not in f.read_bytes() for f in (tmp_path / ".qm").rglob("*") if f.is_file())

    ai = call(app, "POST", "/api/ai/key", {"key": ""})  # forget it
    assert (ai["key_set"], ai["key_source"]) == (False, "")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-computer")
    assert call(app, "GET", "/api/status")["ai"]["key_source"] == "computer"
    call(app, "POST", "/api/ai/key", {"key": ""})  # a key set on the computer is not removed from here
    assert call(app, "GET", "/api/status")["ai"]["key_set"] is True


class BrokenLocator(Locator):
    def click(self, timeout: float) -> None:
        raise TimeoutError("Locator.click: Timeout 8000ms exceeded.\nCall log: ...")


def test_a_failed_click_is_reported_and_the_navigator_is_used(tmp_path: Path) -> None:
    screens = {
        "home": screen("Expand Me", "Me"),
        "Personal Information": screen("My Compensation"),
        "My Compensation": screen(texts=("Current Salary",)),
    }
    page = ScriptedPage(
        screens, "home", {"role:link:Expand Me": 1, "role:link:My Compensation": 1, "text:Current Salary": 1}
    )
    page.get_by_role = lambda role, name, exact: (BrokenLocator if name == "Expand Me" else Locator)(  # type: ignore[method-assign]
        page, f"role:{role}:{name}"
    )
    opened: list[str] = []

    def navigate(path: str) -> None:
        if path == "Wrong > Path":
            raise RuntimeError("no such Navigator entry")
        opened.append(path)
        page.screen = "Personal Information"

    recorder = Recorder(test_id="t")
    ask = scripted_ai(
        [
            {"do": "done"},
            {"do": "click", "element": 1},  # "Expand Me" times out...
            {"do": "navigate", "value": "Wrong > Path"},  # ...a wrong path fails too...
            {"do": "navigate", "value": "Me > Personal Information"},  # ...then the Navigator works
            {"do": "done"},
            {"do": "done"},  # step 3 was done by the navigate
            {"do": "click", "element": 1},
            {"do": "done"},
            {"check": [1]},
        ]
    )
    assert Autopilot(page, Guide(SCENARIO, tmp_path / "r"), recorder, ask, navigate=navigate).run() is True
    assert opened == ["Me > Personal Information"]
    assert 'click link "Expand Me" DID NOT WORK (Locator.click: Timeout 8000ms exceeded.)' in ask.prompts[2]
    assert 'navigate "Wrong > Path" DID NOT WORK (no such Navigator entry)' in ask.prompts[3]
    assert [e["kind"] for e in recorder.events] == ["navigate", "click", "assert_visible"]
    assert recorder.events[0] == {
        "kind": "navigate",
        "value": "Me > Personal Information",
        "guide_step": 2,
    }  # opened for "Me"


NAVIGATOR_PAGE = """<!doctype html><html><body>
<form id="login">
  <label for="userid">User ID</label><input id="userid">
  <label for="password">Password</label><input id="password" type="password">
  <button type="submit">Sign In</button>
</form>
<script>
document.getElementById("login").addEventListener("submit", (e) => {
  e.preventDefault();
  document.body.innerHTML = `
    <a href="#" title="Navigator" aria-label="Navigator" id="nav">&#9776;</a>
    <div id="panel" style="display:none">
      <div class="navmenu-header" title="Me">Me <button aria-label="Expand Me" style="width:0;height:0"></button></div>
      <div id="me-items" style="display:none"><a href="#" id="pi">Personal Information</a></div>
    </div>
    <a href="#" style="display:none">Personal Information</a>
    <main id="main"><h1>Welcome</h1></main>`;
  const $ = (id) => document.getElementById(id);
  $("nav").onclick = (ev) => {  // like Fusion: a second click closes the Navigator
    ev.preventDefault();
    $("panel").style.display = $("panel").style.display === "none" ? "" : "none";
  };
  document.querySelector(".navmenu-header").onclick = () => {  // a group header opens and closes
    $("me-items").style.display = $("me-items").style.display === "none" ? "" : "none";
  };
  $("pi").onclick = (ev) => {
    ev.preventDefault();
    $("panel").style.display = "none";
    // like Redwood's Personal Info cards: links holding a title and a long description, without ids,
    // and (as on a real pod) the same card twice; next to a box that reacts to a click
    const card = '<a href="#"><div><span>My Compensation</span></div>' +
      '<div>View your compensation details, such as salary and personal contributions.</div></a>';
    $("main").innerHTML = '<h1>Personal Information</h1><div class="cards">' + card + card + '</div>' +
      '<div style="cursor:pointer"><span>Contact Info</span></div>';
    document.querySelectorAll(".cards a").forEach((a) => a.onclick = (e2) => {
      e2.preventDefault();
      $("main").innerHTML = '<h1>My Compensation</h1><h2>Current Salary</h2>';
    });
  };
});
</script></body></html>"""


def navigator_ai(system: str, prompt: str) -> str:
    """Like an AI that knows Fusion: opens pages with the Navigator, clicks what is on the screen."""
    if '"check"' in system:
        texts = re.findall(r"^\[(\d+)\] (.+)$", prompt, re.M)
        return json.dumps({"check": [int(n) for n, t in texts if t == "Current Salary"]})
    step = re.search(r"^Step \d+ of \d+: (.+)$", prompt, re.M).group(1)  # type: ignore[union-attr]
    headings = re.search(r"^Headings: (.*)$", prompt, re.M).group(1)  # type: ignore[union-attr]
    if step == "Login" or (step == "Personal Information" and "Personal Information" in headings):
        return json.dumps({"do": "done"})
    if step == "Me":
        if "nothing yet" in prompt:  # a first guess with words that are not in this Navigator
            return json.dumps({"do": "navigate", "value": "About Me > Personal Info", "why": "a guess"})
        if 'opened the Navigator group "Me"' in prompt:
            return json.dumps({"do": "done", "why": "the step only names the group"})
        if "DID NOT WORK" in prompt and "groups are: Me." in prompt:
            return json.dumps({"do": "navigate", "value": "Me", "why": "the step names the group Me"})
        return json.dumps({"do": "done"})
    if "nothing yet" not in prompt:
        return json.dumps({"do": "done"})
    for n, _role, name in re.findall(r'^\[(\d+)\] (\w+) "(.+)"$', prompt, re.M):
        if name.startswith(step.removeprefix("Select ").strip()):  # a card's name is cut short in the list
            return json.dumps({"do": "click", "element": int(n)})
    return json.dumps({"do": "stuck", "why": "not on the screen"})


def test_prepared_with_the_navigator_in_a_real_browser_then_it_plays_by_itself(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    from quartermaster.recorder.recorder import events_to_test, to_yaml
    from quartermaster.runner.engine import run_test
    from quartermaster.runner.playwright_driver import PlaywrightDriver

    environ = {"QM_FUSION_USER": "Mock User", "QM_FUSION_PASSWORD": "not-a-real-secret"}
    if CHROMIUM:
        environ["QM_CHROMIUM_PATH"] = CHROMIUM
    env = Environment(name="mock", url=POD, kind=EnvironmentKind.DEV)

    def serve(context: Any) -> None:
        context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=NAVIGATOR_PAGE))

    driver = PlaywrightDriver(evidence_dir=str(tmp_path), environ=environ, context_hook=serve)
    driver.open(env, "")
    recorder = Recorder(test_id="manual.ess.ess-001")
    recorder.guide = Guide(SCENARIO, tmp_path / "run")
    try:
        pilot = Autopilot(
            driver.page,
            recorder.guide,
            recorder,
            navigator_ai,
            settle=driver._settle,
            navigate=lambda path: driver.navigate(path, timeout_ms=2_000),
        )
        assert pilot.run() is True, pilot.reason
        diary = (tmp_path / "run" / "ai-diary.txt").read_text(encoding="utf-8")
        assert "Did not work" in diary and "Navigator groups: Me" in diary
        assert "Opened the Navigator group Me; its pages: Personal Information" in diary
    finally:
        driver.close()

    assert [e["kind"] for e in recorder.events] == ["navigate", "click", "assert_visible"]
    # the page was clicked in the open group, and is remembered as a Navigator path for replays
    assert recorder.events[0] == {"kind": "navigate", "value": "Me > Personal Information", "guide_step": 3}
    test = events_to_test(
        recorder.events,
        test_id="manual.ess.ess-001",
        title="My Compensation",
        module="HCM",
        product="Global Human Resources",
    )
    out = tmp_path / "ess-001.yaml"
    out.write_text(to_yaml(test))
    play = PlaywrightDriver(evidence_dir=str(tmp_path), environ=environ, context_hook=serve)
    result = run_test(load_test(out), env, play)
    assert result.status is StepStatus.PASSED, [s.error for s in result.steps]
