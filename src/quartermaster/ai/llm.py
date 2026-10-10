"""LLM provider abstraction.

Agents depend on the `LLM` protocol only, so tests use `FakeLLM` and a tenant can run with
AI disabled. Every prompt passes through PII masking before it leaves the process.

The primary decision-making engine is OpenAIDecisionsLLM, which calls OpenAI's Decisions API
(POST /v1/decisions, model gpt-6-luna). It returns typed answers — choice, predicate, or score —
about 10x faster than the chat completions path, with no output tokens charged.
AnthropicLLM is kept as a fallback for structured generation tasks.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel

from quartermaster.ai.masking import mask

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "gpt-6-luna"
OPENAI_DECISIONS_MODEL = "gpt-6-luna"
OPENAI_DECISIONS_URL = "https://api.openai.com/v1/decisions"

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def structured(self, system: str, prompt: str, schema: type[T]) -> T: ...


class AnthropicLLM:
    """Anthropic via the official Anthropic SDK, with schema-validated structured output."""

    def __init__(self, model: str = DEFAULT_MODEL, effort: Effort = "high"):
        import anthropic  # optional dependency: pip install quartermaster[ai]

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self._model = model
        self._effort = effort

    def structured(self, system: str, prompt: str, schema: type[T]) -> T:
        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": mask(prompt)}],
                output_format=schema,
                thinking={"type": "adaptive"},
                output_config={"effort": self._effort},
                # Server-side fallback: if a request is declined, the API retries it on
                # Anthropic's recommended fallback model instead of returning a refusal.
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
                extra_body={"fallbacks": "default"},
            )
        except self._anthropic.RateLimitError as e:
            raise LLMError("Rate limit reached; retry later") from e
        except self._anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise LLMError("could not reach the Anthropic API") from e

        if response.stop_reason == "refusal":
            raise LLMError("model declined the request")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise LLMError(f"no structured output (stop_reason={response.stop_reason})")
        return response.parsed_output


class OpenAIDecisionsLLM:
    """Uses OpenAI's Decisions API (POST /v1/decisions, gpt-6-luna) for fast typed decision-making.

    The Decisions API evaluates text or images and returns typed answers — choice, predicate, or
    score — about 10x faster than the Responses API, with no output-token charges. It is the
    primary decision-making engine for Quartermaster: failure triage, severity scoring, and
    fix-suggestion confidence all go through here.

    The OPENAI_API_KEY environment variable must be set.
    """

    def __init__(self, api_key: str | None = None, model: str = OPENAI_DECISIONS_MODEL):
        self._key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._model = model
        if not self._key:
            raise LLMError(
                "OPENAI_API_KEY is not set. Add your OpenAI API key in Settings, AI assistant."
            )

    # ---------------------------------------------------------------------- public helpers

    def decide(
        self,
        input_text: str,
        questions: list[dict[str, Any]],
        *,
        timeout: float = 30,
    ) -> dict[str, Any]:
        """Call POST /v1/decisions and return the answers dict keyed by question name."""
        body = {
            "model": self._model,
            "input": mask(input_text),
            "questions": questions,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._key}",
            "User-Agent": "Quartermaster",
        }
        req = urllib.request.Request(
            OPENAI_DECISIONS_URL,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310
                data = json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            raise LLMError(f"OpenAI Decisions API error ({e.code}): {detail}") from e
        except (urllib.error.URLError, OSError) as e:
            raise LLMError(f"Could not reach OpenAI Decisions API: {getattr(e, 'reason', e)}") from e
        return {a["name"]: a for a in data.get("answers", [])}

    def choice(self, input_text: str, name: str, instructions: str, choices: list[dict[str, str]]) -> str:
        """Return the chosen value from a fixed set (e.g. failure category)."""
        answers = self.decide(
            input_text,
            [{"type": "choice", "name": name, "instructions": instructions, "choices": choices}],
        )
        return str(answers[name]["choice"])

    def predicate(self, input_text: str, name: str, instructions: str) -> float:
        """Return probability (0–1) that a condition is true (e.g. 'does this fix apply?')."""
        answers = self.decide(
            input_text,
            [{"type": "predicate", "name": name, "instructions": instructions}],
        )
        return float(answers[name]["probability"])

    def score(self, input_text: str, name: str, instructions: str, levels: list[dict[str, str]]) -> float:
        """Return a probability-weighted score across ordered severity levels."""
        answers = self.decide(
            input_text,
            [{"type": "score", "name": name, "instructions": instructions, "levels": levels}],
        )
        return float(answers[name]["score"])

    # ---------------------------------------------------------------------- LLM protocol

    def structured(self, system: str, prompt: str, schema: type[T]) -> T:
        """Satisfy the LLM protocol by routing a structured request through the Decisions API.

        The system prompt is prepended to the prompt as context and the schema's field names
        become choice questions — each field maps to a choice among its allowed values.
        For simple string fields, a single choice question is created per field.
        """
        full_input = f"{system}\n\n{prompt}"
        fields = schema.model_fields
        questions = []
        for field_name, field_info in fields.items():
            annotation = str(field_info.annotation)
            questions.append({
                "type": "choice",
                "name": field_name,
                "instructions": f"What is the {field_name}? Field description: {field_info.description or field_name}",
                "choices": [{"value": field_name, "description": annotation}],
            })
        answers = self.decide(full_input, questions)
        values = {name: ans.get("choice", "") for name, ans in answers.items()}
        return schema.model_validate(values)


class FakeLLM:
    """Deterministic stand-in for tests: returns queued responses and records prompts."""

    def __init__(self, *responses: BaseModel):
        self._responses: deque[BaseModel] = deque(responses)
        self.prompts: list[str] = []

    def structured(self, system: str, prompt: str, schema: type[T]) -> T:
        self.prompts.append(mask(prompt))
        if not self._responses:
            raise LLMError("FakeLLM has no queued responses")
        out = self._responses.popleft()
        if not isinstance(out, schema):
            raise LLMError(f"FakeLLM queued {type(out).__name__}, caller expected {schema.__name__}")
        return out
