"""AI agents at the edges of the deterministic core (docs/PLAN.md §5.2).

Each agent returns a *proposal* validated against our schema; nothing it produces runs or
is committed without passing validation and, where configured, human approval.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, ValidationError

from quartermaster.ai.llm import LLM, LLMError, OpenAIDecisionsLLM
from quartermaster.domain.models import (
    Action,
    Feature,
    Locator,
    LocatorStrategy,
    Priority,
    RunResult,
    Step,
    TestCase,
)

# --------------------------------------------------------------------------- test author

# The LLM-facing schema is deliberately flat (no dict-keyed unions) so structured output is
# reliable; it is converted into the strict domain model afterwards.


class DraftStrategy(BaseModel):
    strategy: LocatorStrategy
    value: str


class DraftStep(BaseModel):
    action: Action
    intent: str
    target: list[DraftStrategy] = Field(default_factory=list)
    value: str | None = None


class DraftTest(BaseModel):
    id: str
    title: str
    module: str
    product: str
    process: str
    priority: Priority
    tags: list[str]
    data: dict[str, str]
    steps: list[DraftStep]


AUTHOR_SYSTEM = """You write regression tests for Oracle Fusion Cloud Applications.
Return one test using these actions: navigate (value is a Navigator path such as
"Payables > Invoices"), click, fill, select, assert_visible, assert_text, wait_job (value is
the scheduled process name), api_call.
Rules:
- Every step has a short business intent, e.g. "Enter supplier".
- Give each targeted step 2-4 locator strategies, most stable first: label, then role
  ("button:Save and Close"), then text; css/xpath only as a last resort. Never use generated
  ADF ids such as pt1:_FOr1.
- Put test data in `data` and reference it as ${name}. Use unique suffixes for document numbers.
- End with an assertion that proves the business outcome (e.g. invoice status Validated).
- id: lowercase letters, digits, dots, dashes or underscores."""


def draft_test(description: str, llm: LLM, *, module_hint: str = "") -> TestCase:
    prompt = f"Module hint: {module_hint or 'infer'}\n\nDescribe-to-test request:\n{description}"
    draft = llm.structured(AUTHOR_SYSTEM, prompt, DraftTest)
    try:
        return TestCase(
            id=draft.id,
            title=draft.title,
            module=draft.module,
            product=draft.product,
            process=draft.process,
            priority=draft.priority,
            tags=draft.tags,
            data=draft.data,
            steps=[
                Step(
                    action=s.action,
                    intent=s.intent,
                    target=Locator(strategies=[{t.strategy: t.value} for t in s.target], description=s.intent)
                    if s.target
                    else None,
                    value=s.value,
                )
                for s in draft.steps
            ],
        )
    except ValidationError as e:
        raise LLMError(f"drafted test failed validation: {e}") from e


# --------------------------------------------------------------------------- triage


class FailureCategory(StrEnum):
    PRODUCT_CHANGE = "product_change"  # Oracle changed behaviour/UI as documented
    PRODUCT_DEFECT = "product_defect"  # Oracle regression -> raise an SR
    CONFIGURATION = "configuration"  # tenant setup / security / opt-in
    TEST_DATA = "test_data"
    ENVIRONMENT = "environment"  # pod down, timeout, SSO
    SCRIPT = "script"  # our test is wrong


class TriageVerdict(BaseModel):
    category: FailureCategory
    confidence: float = Field(ge=0, le=1)
    summary: str
    related_features: list[str] = Field(default_factory=list)
    suggested_action: str


TRIAGE_SYSTEM = """You triage failed Oracle Fusion regression tests after a quarterly update.
Classify the root cause. Prefer product_change only when a listed release feature plausibly
explains the failure, and cite its id. Say so plainly when the evidence is thin and lower the
confidence accordingly."""


def triage(run: RunResult, test: TestCase, features: list[Feature], llm: LLM) -> TriageVerdict:
    failed = [s for s in run.steps if s.status.value == "failed"]
    lines = [f"Test: {test.id} - {test.title} ({test.module}/{test.product})"]
    lines += [f"Step {s.index} '{s.intent}' failed: {s.error}" for s in failed]
    lines.append("Release features touching this area:")
    lines += [f"- {f.id}: {f.title} [{f.change_type.value}] {f.description}" for f in features]
    return llm.structured(TRIAGE_SYSTEM, "\n".join(lines), TriageVerdict)


# --------------------------------------------------------------------------- OpenAI Decisions triage

_FAILURE_CHOICES = [
    {"value": FailureCategory.PRODUCT_CHANGE,  "description": "Oracle changed behaviour or UI as documented in the release."},
    {"value": FailureCategory.PRODUCT_DEFECT,  "description": "Oracle regression — raise a Service Request."},
    {"value": FailureCategory.CONFIGURATION,   "description": "Tenant setup, security, or opt-in feature not enabled."},
    {"value": FailureCategory.TEST_DATA,        "description": "Missing or wrong test data on this pod."},
    {"value": FailureCategory.ENVIRONMENT,      "description": "Pod down, timeout, or SSO issue."},
    {"value": FailureCategory.SCRIPT,           "description": "The test script itself is wrong."},
]

_SEVERITY_LEVELS = [
    {"label": "Low",      "description": "Cosmetic or informational; the workflow still completes."},
    {"label": "Medium",   "description": "A step fails but a workaround exists."},
    {"label": "High",     "description": "A critical workflow is fully blocked with no workaround."},
]


def triage_with_decisions(
    run: RunResult,
    test: TestCase,
    features: list[Feature],
    decisions: OpenAIDecisionsLLM,
) -> TriageVerdict:
    """Triage a failed test using OpenAI's Decisions API.

    Sends one batched request with three questions:
      - choice  → which failure category applies
      - score   → severity level (0 Low … 2 High)
      - predicate → is a listed release feature the likely cause

    This is ~10x faster than the chat completions path and returns typed,
    probability-weighted answers that are directly actionable.
    """
    failed = [s for s in run.steps if s.status.value == "failed"]
    context_lines = [
        f"Test: {test.id} — {test.title} ({test.module} / {test.product})",
        "Failed steps:",
    ]
    context_lines += [f"  Step {s.index} '{s.intent}': {s.error}" for s in failed]
    if features:
        context_lines.append("Release features that may be related:")
        context_lines += [f"  [{f.id}] {f.title} — {f.description}" for f in features]
    input_text = "\n".join(context_lines)

    answers = decisions.decide(
        input_text,
        questions=[
            {
                "type": "choice",
                "name": "category",
                "instructions": (
                    "What is the root cause of this Oracle Fusion regression test failure?"
                ),
                "choices": _FAILURE_CHOICES,
            },
            {
                "type": "score",
                "name": "severity",
                "instructions": "How severe is this failure for the business?",
                "levels": _SEVERITY_LEVELS,
            },
            {
                "type": "predicate",
                "name": "release_caused",
                "instructions": (
                    "Is a listed Oracle release feature the most likely cause of this failure?"
                ),
            },
        ],
    )

    category = FailureCategory(answers["category"]["choice"])
    severity_score: float = answers["severity"]["score"]
    release_probability: float = answers["release_caused"]["probability"]
    confidence: float = answers["category"].get("confidence", 0.7)

    related = [f.id for f in features] if release_probability > 0.6 else []
    severity_label = _SEVERITY_LEVELS[min(round(severity_score), len(_SEVERITY_LEVELS) - 1)]["label"]

    action_map = {
        FailureCategory.PRODUCT_CHANGE: "Accept the Oracle screen change or update the test step.",
        FailureCategory.PRODUCT_DEFECT: "Raise an Oracle Service Request with this evidence.",
        FailureCategory.CONFIGURATION: "Check tenant configuration and opt-in feature settings.",
        FailureCategory.TEST_DATA: "Update the test data set for this pod.",
        FailureCategory.ENVIRONMENT: "Check pod availability and retry the run.",
        FailureCategory.SCRIPT: "Review and correct the test script.",
    }

    return TriageVerdict(
        category=category,
        confidence=round(confidence, 2),
        summary=(
            f"OpenAI Decisions (gpt-6-luna): {category.value} "
            f"[severity {severity_label}, release_caused p={release_probability:.2f}]"
        ),
        related_features=related,
        suggested_action=action_map[category],
    )
