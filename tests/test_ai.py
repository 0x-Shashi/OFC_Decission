from __future__ import annotations

import pytest

from quartermaster.ai.agents import (
    DraftStep,
    DraftStrategy,
    DraftTest,
    FailureCategory,
    TriageVerdict,
    draft_test,
    triage,
)
from quartermaster.ai.llm import FakeLLM, LLMError
from quartermaster.ai.masking import mask
from quartermaster.domain.models import (
    Action,
    Feature,
    LocatorStrategy,
    Priority,
    RunResult,
    StepResult,
    StepStatus,
    TestCase,
)


def test_mask_redacts_identifiers_but_keeps_dates_and_amounts() -> None:
    text = (
        "Mail jane.doe@acme.com, acct 123456789012, SSN 123-45-6789, "
        "IBAN DE89370400440532013000 on 2026-09-26 for 1250.00"
    )
    out = mask(text)
    for secret in ("jane.doe@acme.com", "123456789012", "123-45-6789", "DE89370400440532013000"):
        assert secret not in out
    assert "2026-09-26" in out and "1250.00" in out


def _draft(**overrides: object) -> DraftTest:
    base = dict(
        id="ap.simple",
        title="Simple invoice",
        module="Financials",
        product="Payables",
        process="Procure-to-Pay",
        priority=Priority.HIGH,
        tags=["invoice"],
        data={"supplier": "Lee"},
        steps=[
            DraftStep(action=Action.NAVIGATE, intent="Open invoices", value="Payables > Invoices"),
            DraftStep(
                action=Action.SELECT,
                intent="Enter supplier",
                value="${supplier}",
                target=[DraftStrategy(strategy=LocatorStrategy.LABEL, value="Supplier")],
            ),
        ],
    )
    base.update(overrides)
    return DraftTest(**base)  # type: ignore[arg-type]


def test_draft_test_converts_to_domain_model_and_masks_prompt() -> None:
    llm = FakeLLM(_draft())
    tc = draft_test("Create invoice for bob@x.com", llm)
    assert isinstance(tc, TestCase)
    assert tc.steps[1].target is not None
    assert tc.steps[1].target.ordered() == [(LocatorStrategy.LABEL, "Supplier")]
    assert "bob@x.com" not in llm.prompts[0]


def test_invalid_draft_raises_llm_error() -> None:
    bad = _draft(steps=[DraftStep(action=Action.CLICK, intent="Save")])  # click without target
    with pytest.raises(LLMError, match="failed validation"):
        draft_test("x", FakeLLM(bad))


def test_triage_passes_failure_and_features_to_model() -> None:
    verdict = TriageVerdict(
        category=FailureCategory.PRODUCT_CHANGE,
        confidence=0.8,
        summary="Redwood page replaced classic page",
        related_features=["FIN-AP-001"],
        suggested_action="Accept healing proposal",
    )
    llm = FakeLLM(verdict)
    run = RunResult(
        test_id="t",
        environment="stage",
        steps=[StepResult(index=0, intent="Enter supplier", status=StepStatus.FAILED, error="matched 0")],
    )
    tc = TestCase(
        id="t",
        title="Invoice",
        module="Financials",
        product="Payables",
        steps=[{"action": "navigate", "intent": "go", "value": "A"}],  # type: ignore[list-item]
    )
    feat = Feature(id="FIN-AP-001", module="Financials", product="Payables", title="Redwood invoice")
    assert triage(run, tc, [feat], llm) == verdict
    assert "FIN-AP-001" in llm.prompts[0] and "Enter supplier" in llm.prompts[0]


def test_fake_llm_type_mismatch() -> None:
    with pytest.raises(LLMError, match="expected DraftTest"):
        draft_test("x", FakeLLM(TriageVerdict(category="script", confidence=0.1, summary="", suggested_action="")))
