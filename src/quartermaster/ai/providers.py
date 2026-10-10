"""Talk to any AI model the user chooses in Settings, with the standard library only.

OpenAI is the primary AI engine in Quartermaster: the Decisions API (POST /v1/decisions,
model gpt-6-luna) powers all decision-making — failure triage, severity scoring, and
fix-suggestion confidence — about 10x faster than the chat completions path.

Other providers (Anthropic, OpenRouter, Google Gemini, DeepSeek, Mistral, Groq, Ollama)
are available for chat completions via the same OpenAI-compatible interface; only the
web address, model name, and key differ.

The key is never stored: the settings hold the *name* of the environment variable that has it
(for example OPENAI_API_KEY), set like the pod password. Every prompt is masked first.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from quartermaster.ai.masking import mask

# id: (label, format, web address, environment variable for the key, suggested model)
# A suggested model is only given where we are sure of its name; otherwise the user types the
# model their account offers.
PRESETS: dict[str, tuple[str, str, str, str, str]] = {
    "openai": (
        "OpenAI (Decisions API — recommended)",
        "openai",
        "https://api.openai.com/v1",
        "OPENAI_API_KEY",
        "gpt-6-luna",   # primary model for the Decisions API
    ),
    "anthropic": (
        "Anthropic",
        "anthropic",
        "https://api.anthropic.com/v1",
        "ANTHROPIC_API_KEY",
        "sonnet-5-5",
    ),
    "openrouter": ("OpenRouter (many models)", "openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", ""),
    "gemini": (
        "Google Gemini",
        "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "GEMINI_API_KEY",
        "",
    ),
    "deepseek": ("DeepSeek", "openai", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", ""),
    "mistral": ("Mistral", "openai", "https://api.mistral.ai/v1", "MISTRAL_API_KEY", ""),
    "groq": ("Groq", "openai", "https://api.groq.com/openai/v1", "GROQ_API_KEY", ""),
    "ollama": ("Ollama (on this computer, no key)", "openai", "http://localhost:11434/v1", "", ""),
    "custom": ("Other (OpenAI-compatible)", "openai", "", "QM_AI_API_KEY", ""),
}
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,80}$")
_WORKSPACE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


class AIError(RuntimeError):
    """The AI could not be reached or did not answer usefully. The message is for people."""


class _Refused(AIError):
    """The provider rejected the request itself (HTTP 400); `detail` says which part."""

    def __init__(self, message: str, detail: str):
        super().__init__(message)
        self.detail = detail


# Newer OpenAI models (reasoning models such as the gpt-5 family and o-series) reject max_tokens
# (they want max_completion_tokens, which also counts their thinking) and any temperature but the
# default. What a model rejected once is remembered while Quartermaster runs.
_ADAPTED: dict[tuple[str, str], set[str]] = {}


@dataclass(frozen=True)
class AIConfig:
    provider: str
    model: str
    base_url: str
    key_env: str
    workspace: str = ""  # Anthropic only: the workspace ID, for a key that is not tied to one

    @property
    def label(self) -> str:
        return f"{PRESETS.get(self.provider, ('AI',))[0]}, {self.model}"

    @property
    def format(self) -> str:
        return PRESETS[self.provider][1] if self.provider in PRESETS else "openai"

    def key(self, environ: dict[str, str] | None = None) -> str:
        env = os.environ if environ is None else environ
        return env.get(self.key_env, "") if self.key_env else ""

    def problem(self, environ: dict[str, str] | None = None) -> str:
        """Why this configuration cannot be used yet, in plain words ('' when it can)."""
        if self.provider not in PRESETS:
            return "Choose an AI provider in Settings."
        if not self.model:
            return "Enter the model name in Settings (as your AI provider names it)."
        if not self.base_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            return "The provider's web address must start with https:// (or be on this computer)."
        if self.key_env and not self.key(environ):
            return f"No API key yet. Paste it in Settings, AI assistant (or set {self.key_env} on this computer)."
        return ""


def config_from(settings: dict[str, Any]) -> AIConfig:
    provider = str(settings.get("ai_provider") or "")
    preset = PRESETS.get(provider, ("", "openai", "", "", ""))
    return AIConfig(
        provider=provider,
        model=str(settings.get("ai_model") or preset[4]),
        base_url=str(settings.get("ai_base_url") or preset[2]).rstrip("/"),
        key_env=str(settings.get("ai_key_env") or preset[3]),  # Ollama needs none
        workspace=str(settings.get("ai_workspace") or "") if preset[1] == "anthropic" else "",
    )


def check_settings(changes: dict[str, Any]) -> dict[str, str]:
    """Validate the AI part of a settings change."""
    out: dict[str, str] = {}
    if "ai_provider" in changes:
        p = str(changes["ai_provider"] or "")
        if p and p not in PRESETS:
            raise ValueError(f"unknown AI provider {p!r}")
        out["ai_provider"] = p
    if "ai_model" in changes:
        out["ai_model"] = " ".join(str(changes["ai_model"] or "").split())[:120]
    if "ai_base_url" in changes:
        url = str(changes["ai_base_url"] or "").strip()[:300]
        if url and not re.match(r"^https?://[^\s]+$", url):
            raise ValueError("the AI web address must start with https://")
        out["ai_base_url"] = url
    if "ai_key_env" in changes:
        name = str(changes["ai_key_env"] or "").strip()
        if name and not _ENV.match(name):
            raise ValueError("the key setting is the NAME of an environment variable, e.g. OPENAI_API_KEY")
        out["ai_key_env"] = name
    if "ai_workspace" in changes:
        ws = str(changes["ai_workspace"] or "").strip()
        if ws and not _WORKSPACE.match(ws):
            raise ValueError("the workspace ID has letters, digits, _ and - only, e.g. wrkspc_01AbC...")
        out["ai_workspace"] = ws
    return out


def chat(config: AIConfig, system: str, prompt: str, *, max_tokens: int = 800, timeout: float = 90) -> str:
    """One question, one answer (text). Personal details in the prompt are masked first."""
    problem = config.problem()
    if problem:
        raise AIError(problem)
    prompt = mask(prompt)
    key = config.key()
    if config.format == "anthropic":
        url = f"{config.base_url}/messages"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        if config.workspace:
            headers["anthropic-workspace-id"] = config.workspace
        body: dict[str, Any] = {
            "model": config.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
    else:
        url = f"{config.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        body = {
            "model": config.model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
    data = _post_adapting(config, url, body, headers, timeout)
    try:
        if config.format == "anthropic":
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        return str(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as e:
        raise AIError(f"The AI answered in an unexpected form: {str(data)[:200]}") from e


def _post_adapting(config: AIConfig, url: str, body: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
    """Send, and when the model rejects a setting it does not support, send again without it."""
    adapted = _ADAPTED.setdefault((config.base_url, config.model), set())
    for _ in range(3):
        if "max_completion_tokens" in adapted and "max_tokens" in body:
            # room for the model's thinking as well as its short answer
            body["max_completion_tokens"] = max(int(body.pop("max_tokens")), 4000)
        if "no_temperature" in adapted:
            body.pop("temperature", None)
        try:
            return _post(url, body, headers, timeout)
        except _Refused as e:
            if "max_tokens" in body and "max_completion_tokens" in e.detail:
                adapted.add("max_completion_tokens")
            elif "temperature" in body and "temperature" in e.detail:
                adapted.add("no_temperature")
            else:
                raise
    return _post(url, body, headers, timeout)


def _post(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "Quartermaster", **headers},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 - the address the user chose
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            if e.code in (429, 500, 502, 503, 529) and attempt < 2:
                time.sleep(2 * (attempt + 1))  # busy: try again shortly
                continue
            if e.code in (401, 403):
                raise AIError("The AI provider refused the key. Check the key and that it is for this provider.") from e
            if "scoped to a workspace" in detail:
                raise AIError(
                    "Anthropic wants to know which workspace this key is for. Either make a key inside a"
                    " workspace in the Anthropic Console, or enter the Workspace ID in Settings, AI"
                    " assistant, Advanced."
                ) from e
            if e.code == 400:
                raise _Refused(f"The AI provider answered with an error (400): {detail}", detail) from e
            if e.code == 404:
                raise AIError(f"The AI provider does not know this model or address ({detail}).") from e
            raise AIError(f"The AI provider answered with an error ({e.code}): {detail}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            if attempt < 2:
                time.sleep(2)
                continue
            raise AIError(f"Could not reach the AI provider: {getattr(e, 'reason', e)}") from e
    raise AIError("The AI provider did not answer.")


def parse_json(text: str) -> dict[str, Any]:
    """The first JSON object in an answer (models sometimes wrap it in words or ```json fences)."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(value, dict):
                        return value
                    break
        start = text.find("{", start + 1)
    raise AIError(f"The AI did not answer with the expected JSON: {text[:200]}")


def check(config: AIConfig) -> dict[str, Any]:
    """Settings' "Test the AI" button: one tiny question, and how long it took."""
    started = time.monotonic()
    try:
        answer = chat(config, "Reply with one word.", "Say OK.", max_tokens=10, timeout=30)
    except AIError as e:
        return {"ok": False, "message": str(e)}
    ms = round((time.monotonic() - started) * 1000)
    return {"ok": True, "message": f"{config.label} answered in {ms} ms ({answer.strip()[:20]!r})."}
