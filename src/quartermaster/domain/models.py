"""Core domain model for Quartermaster.

Everything that crosses a module boundary is one of these Pydantic models, so it is
validated once at the edge (YAML load, LLM output, API input) and trusted afterwards.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


# --------------------------------------------------------------------------- environments


class EnvironmentKind(StrEnum):
    DEV = "DEV"
    TEST = "TEST"
    STAGE = "STAGE"
    PROD = "PROD"


class Environment(_Strict):
    name: str
    url: str
    kind: EnvironmentKind
    release: str | None = Field(default=None, description="Pod release, e.g. '26D'")


# --------------------------------------------------------------------------- releases


class ChangeType(StrEnum):
    UI = "UI"
    PROCESS = "PROCESS"
    BOTH = "BOTH"
    REPORT = "REPORT"
    API = "API"


class Feature(_Strict):
    """One row of Oracle's quarterly 'What's New' feature list."""

    id: str
    module: str = Field(description="Pillar/module, e.g. 'Financials', 'Procurement'")
    product: str = Field(description="Product within module, e.g. 'Payables'")
    title: str
    description: str = ""
    change_type: ChangeType = ChangeType.UI
    opt_in: bool = False
    customer_action_required: bool = False
    tags: list[str] = Field(default_factory=list)


class Release(_Strict):
    id: str = Field(pattern=r"^\d{2}[A-D]$", description="Oracle update id, e.g. '26D'")
    features: list[Feature] = Field(default_factory=list)


# --------------------------------------------------------------------------- tests


class LocatorStrategy(StrEnum):
    LABEL = "label"
    ROLE = "role"
    TEST_ID = "test_id"
    TEXT = "text"
    CSS = "css"
    XPATH = "xpath"


class Locator(_Strict):
    """An element described by several strategies, tried in the listed order."""

    strategies: list[dict[LocatorStrategy, str]] = Field(min_length=1)
    description: str = ""

    @field_validator("strategies")
    @classmethod
    def _one_key_each(cls, v: list[dict[LocatorStrategy, str]]) -> list[dict[LocatorStrategy, str]]:
        for s in v:
            if len(s) != 1:
                raise ValueError("each locator strategy must have exactly one key, e.g. {label: 'Supplier'}")
        return v

    def ordered(self) -> list[tuple[LocatorStrategy, str]]:
        return [next(iter(s.items())) for s in self.strategies]


class Action(StrEnum):
    NAVIGATE = "navigate"  # Fusion navigator path, e.g. "Payables > Invoices"
    CLICK = "click"
    FILL = "fill"
    SELECT = "select"
    ASSERT_VISIBLE = "assert_visible"
    ASSERT_TEXT = "assert_text"
    WAIT_JOB = "wait_job"  # ESS scheduled process
    LOGIN_AS = "login_as"  # switch persona mid-test, e.g. employee submits, manager approves
    API_CALL = "api_call"


_TARGETED = {Action.CLICK, Action.FILL, Action.SELECT, Action.ASSERT_VISIBLE, Action.ASSERT_TEXT}
_VALUED = {
    Action.NAVIGATE,
    Action.FILL,
    Action.SELECT,
    Action.ASSERT_TEXT,
    Action.WAIT_JOB,
    Action.API_CALL,
    Action.LOGIN_AS,
}


class Step(_Strict):
    action: Action
    intent: str = Field(description="Human-readable purpose; used by the healer and reports")
    target: Locator | None = None
    value: str | None = None
    expected: str = Field(default="", description="Expected result, shown in the evidence document")
    options: dict[str, Any] = Field(default_factory=dict)
    # For a test made from a manual scenario: the number of the written step this action does.
    written_step: int | None = Field(default=None, ge=1)
    # The shared group this step came from (see dsl/library.py); set when a test is loaded, not written.
    shared: str | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> Step:
        if self.action in _TARGETED and self.target is None:
            raise ValueError(f"action '{self.action.value}' requires a target")
        if self.action in _VALUED and self.value is None:
            raise ValueError(f"action '{self.action.value}' requires a value")
        return self


class Priority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


PRIORITY_WEIGHT: dict[Priority, float] = {
    Priority.CRITICAL: 1.0,
    Priority.HIGH: 0.8,
    Priority.MEDIUM: 0.5,
    Priority.LOW: 0.3,
}


class GenerateRule(_Strict):
    """How one value is made fresh for every run (see dsl/data.py). Exactly one of unique, date, number, choice."""

    unique: int | None = Field(default=None, ge=4, le=16, description="Letters and digits, new each run")
    prefix: str = Field(default="", max_length=40)
    suffix: str = Field(default="", max_length=40)
    date: Literal["today"] | None = None
    plus_days: int = Field(default=0, ge=-3650, le=3650)
    format: str = Field(default="%Y-%m-%d", max_length=40)
    number: tuple[int, int] | None = None
    choice: list[str] | None = Field(default=None, min_length=1, max_length=50)

    @field_validator("choice", mode="before")
    @classmethod
    def _scalars_as_text(cls, value: Any) -> Any:
        if isinstance(value, list) and all(isinstance(v, str | int | float) and not isinstance(v, bool) for v in value):
            return [str(v) for v in value]
        return value

    @model_validator(mode="after")
    def _one_kind(self) -> GenerateRule:
        kinds = [k for k in ("unique", "date", "number", "choice") if getattr(self, k) is not None]
        if len(kinds) != 1:
            raise ValueError("give exactly one of unique, date, number or choice")
        used = self.model_fields_set
        if kinds[0] != "unique" and used & {"prefix", "suffix"}:
            raise ValueError("prefix and suffix go with `unique`")
        if kinds[0] != "date" and used & {"plus_days", "format"}:
            raise ValueError("plus_days and format go with `date`")
        if self.number is not None and self.number[0] > self.number[1]:
            raise ValueError("number: the first value must not be larger than the second")
        if self.date is not None:
            try:
                text = datetime(2000, 1, 2).strftime(self.format)
            except ValueError as e:
                raise ValueError(f"format is not a date format: {e}") from e
            if not text.strip():
                raise ValueError("format must produce some text, for example %Y-%m-%d")
        return self


class TestCase(_Strict):
    __test__ = False  # stop pytest from collecting this class

    id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    title: str
    module: str
    product: str
    process: str = ""
    persona: str = ""
    priority: Priority = Priority.MEDIUM
    tags: list[str] = Field(default_factory=list)
    estimated_minutes: float = Field(default=5.0, gt=0)
    data: dict[str, str] = Field(default_factory=dict)
    # Data sets used (files in `_data`). Their values are folded into `data` and `pods` when the test is loaded.
    data_sets: list[str] = Field(default_factory=list)
    # Values that differ by pod: a pod's name or kind (DEV, TEST, STAGE) to the values it uses instead of `data`.
    pods: dict[str, dict[str, str]] = Field(default_factory=dict)
    # Values made fresh for every run, used in steps as ${name}.
    generate: dict[str, GenerateRule] = Field(default_factory=dict)
    # Service calls that run first, after sign-in: check that the pod is ready (a period is open, a supplier exists)
    # or make what the test needs. A call that fails stops the test with "Setup not met": the pod's data is not ready,
    # which is not the same as a release that broke the test. Values they save are used by the steps and the cleanup.
    setup: list[Step] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)
    # Steps that remove what the test made on the pod. They run after the steps above, whether those
    # passed or failed. A cleanup that fails is reported but never changes the test's result.
    cleanup: list[Step] = Field(default_factory=list)
    # For a test made from a manual scenario: its written steps ({"action", "expected"}), so the
    # evidence follows the script the tester knows rather than the recorded clicks.
    written_steps: list[dict[str, str]] = Field(default_factory=list)

    @field_validator("setup")
    @classmethod
    def _setup_is_service_calls(cls, steps: list[Step]) -> list[Step]:
        for i, step in enumerate(steps, 1):
            if step.action is not Action.API_CALL:
                raise ValueError(
                    f"setup step {i}: only api_call steps can be used in setup (found {step.action.value})"
                )
        return steps


# --------------------------------------------------------------------------- results


class StepStatus(StrEnum):
    PASSED = "passed"
    HEALED = "healed"
    FAILED = "failed"
    SKIPPED = "skipped"


class HealingProposal(_Strict):
    step_index: int
    intent: str
    old: tuple[LocatorStrategy, str]
    new: tuple[LocatorStrategy, str]
    confidence: float = Field(ge=0, le=1)
    # "fallback": a later way saved in the test found it. "similar": a name on the page that looks
    # like the old one. "ai": the AI chose it. The last two are only suggestions for a failed step.
    source: str = "fallback"
    why: str = ""  # in words, for a suggestion


class StepResult(_Strict):
    index: int
    intent: str
    status: StepStatus
    duration_ms: float = 0
    error: str | None = None
    evidence: list[str] = Field(default_factory=list)
    # Recorded for the evidence document and run history.
    action: str = ""
    value: str | None = None  # rendered value, e.g. the text typed
    expected: str = ""
    locator: str | None = None  # the locator actually used, e.g. "role=button:Search"
    started_at: str | None = None  # ISO 8601 with time zone
    screenshot_note: str | None = None  # why a screenshot that was asked for is missing
    written_step: int | None = None  # see Step.written_step
    note: str | None = None  # why a cleanup step was not done
    attempts: int = 1  # more than 1: the step failed and was tried again
    first_error: str | None = None  # why the first attempt failed, when a later one passed


class ScreenshotMode(StrEnum):
    OFF = "off"
    ON_FAILURE = "on-failure"
    EVERY_STEP = "every-step"


class RunResult(_Strict):
    test_id: str
    environment: str
    steps: list[StepResult]
    healing: list[HealingProposal] = Field(default_factory=list)
    cleanup: list[StepResult] = Field(default_factory=list)  # kept apart from `steps`: never part of the result
    setup: list[StepResult] = Field(default_factory=list)  # what was checked or made before the steps
    run_id: str = ""
    test_title: str = ""
    persona: str = ""
    environment_url: str = ""
    release: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    screenshots: ScreenshotMode = ScreenshotMode.ON_FAILURE
    written_steps: list[dict[str, str]] = Field(default_factory=list)  # see TestCase.written_steps

    @property
    def status(self) -> StepStatus:
        statuses = {s.status for s in self.steps}
        if StepStatus.FAILED in statuses:
            return StepStatus.FAILED
        if StepStatus.HEALED in statuses:
            return StepStatus.HEALED
        return StepStatus.PASSED

    @property
    def flaky(self) -> bool:
        """The test passed, but a step needed another attempt: it may fail for no real reason."""
        return any(s.attempts > 1 and s.status in (StepStatus.PASSED, StepStatus.HEALED) for s in self.steps)

    @property
    def setup_status(self) -> str:
        """One word: none (no setup in the test), done, or failed (a step did not pass, so the steps never ran)."""
        if not self.setup:
            return "none"
        return "failed" if any(c.status is StepStatus.FAILED for c in self.setup) else "done"

    @property
    def cleanup_status(self) -> str:
        """One word: none (no cleanup in the test), done, partial (some steps failed) or failed."""
        if not self.cleanup:
            return "none"
        done = [c for c in self.cleanup if c.status is not StepStatus.FAILED]
        return "done" if len(done) == len(self.cleanup) else "partial" if done else "failed"
