from __future__ import annotations

from conftest import EXAMPLES

from quartermaster.domain.models import ChangeType, Feature, Release
from quartermaster.dsl.loader import load_release, load_tests
from quartermaster.impact.analyzer import COVERAGE_THRESHOLD, analyze, match, plan, severity


def _data() -> tuple[Release, list]:
    tests = load_tests(EXAMPLES / "tests") + load_tests(EXAMPLES / "unverified")
    return load_release(EXAMPLES / "releases" / "26D_sample.json"), tests


def test_each_feature_is_covered_by_its_own_module() -> None:
    release, tests = _data()
    covers = {i.test.id: i.features for i in analyze(release, tests)}
    assert covers == {
        "hcm.hire-employee": ["HCM-CORE-005"],
        "hcm.promote-employee": ["HCM-CORE-005", "HCM-CMP-006"],
        "hcm.absence-request-approval": ["HCM-ABS-004", "HCM-CMP-006"],
        "ap.create-invoice-po-match": ["FIN-AP-001"],
        "gl.manual-journal-approval": ["FIN-GL-002"],
        "po.create-standard-order": ["PRC-PO-003"],
        "hcm.view-worker": ["HCM-CORE-005"],
        "hcm.view-my-personal-info": ["HCM-CORE-005"],
        "hcm.hire-page-opens": ["HCM-CORE-005"],
        "hcm.create-location": ["HCM-CORE-005"],
    }


def test_reasons_explain_the_match() -> None:
    release, tests = _data()
    hire = next(i for i in analyze(release, tests) if i.test.id == "hcm.hire-employee")
    assert any("same product (Global Human Resources)" in r and "shared tags: hire, redwood" in r for r in hire.reasons)


def test_shared_word_across_modules_is_weak_evidence() -> None:
    release, tests = _data()
    sales_order = next(f for f in release.features if f.id == "SCM-OM-007")
    purchase_order = next(t for t in tests if t.id == "po.create-standard-order")
    score, reasons = match(sales_order, purchase_order)
    assert 0 < score < COVERAGE_THRESHOLD
    assert any("different module" in r for r in reasons)


def test_generic_tag_across_modules_does_not_cover() -> None:
    release, tests = _data()
    redwood_invoice = next(f for f in release.features if f.id == "FIN-AP-001")
    hire = next(t for t in tests if t.id == "hcm.hire-employee")
    score, _ = match(redwood_invoice, hire)  # both tagged "redwood"
    assert score < COVERAGE_THRESHOLD


def test_risk_bounded_and_sorted() -> None:
    release, tests = _data()
    impacts = analyze(release, tests)
    assert all(0 <= i.risk <= 1 for i in impacts)
    assert [i.priority for i in impacts] == sorted((i.priority for i in impacts), reverse=True)


def test_severity_rules() -> None:
    f = Feature(id="F", module="m", product="p", title="t", change_type=ChangeType.UI)
    assert severity(f) == 0.6
    f2 = f.model_copy(update={"customer_action_required": True, "opt_in": True})
    assert severity(f2) == 0.8
    assert abs(severity(f2, {"F"}) - 0.9) < 1e-9
    f3 = f2.model_copy(update={"change_type": ChangeType.BOTH})
    assert severity(f3, {"F"}) == 1.0  # capped


def test_empty_release_has_zero_risk() -> None:
    _, tests = _data()
    impacts = analyze(Release(id="26D"), tests)
    assert all(i.risk == 0 for i in impacts)


def test_plan_respects_budget_but_keeps_critical() -> None:
    release, tests = _data()
    p = plan(analyze(release, tests), budget_minutes=1)
    # Both critical tests are kept even though together they need 14 min.
    assert [i.test.id for i in p.selected] == ["hcm.hire-employee", "ap.create-invoice-po-match"]
    assert len(p.deferred) == 8


def test_plan_fills_budget_by_priority() -> None:
    release, tests = _data()
    p = plan(analyze(release, tests), budget_minutes=25)
    assert [i.test.id for i in p.selected] == [
        "hcm.hire-employee",
        "ap.create-invoice-po-match",
        "hcm.promote-employee",
        "gl.manual-journal-approval",
    ]
    assert p.total_minutes <= 25


def test_plan_without_budget_reports_uncovered_features() -> None:
    release, tests = _data()
    p = plan(analyze(release, tests))
    assert len(p.selected) == 10
    assert [f.id for f in p.uncovered_features(release)] == ["SCM-OM-007"]
