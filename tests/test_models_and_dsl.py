from __future__ import annotations

from pathlib import Path

import pytest
from conftest import EXAMPLES
from pydantic import ValidationError

from quartermaster.domain.models import Action, Locator, Release, Step
from quartermaster.dsl.loader import SpecError, load_release, load_test, load_tests, render_value


def test_example_specs_load() -> None:
    tests = load_tests(EXAMPLES / "tests") + load_tests(EXAMPLES / "unverified")
    assert {t.id for t in load_tests(EXAMPLES / "tests")} == {
        "hcm.view-worker",
        "hcm.view-my-personal-info",
        "hcm.hire-page-opens",
        "hcm.create-location",
    }
    assert {t.id for t in tests} == {
        "ap.create-invoice-po-match",
        "gl.manual-journal-approval",
        "po.create-standard-order",
        "hcm.hire-employee",
        "hcm.absence-request-approval",
        "hcm.promote-employee",
        "hcm.view-worker",
        "hcm.view-my-personal-info",
        "hcm.hire-page-opens",
        "hcm.create-location",
    }


def test_example_release_loads() -> None:
    r = load_release(EXAMPLES / "releases" / "26D_sample.json")
    assert r.id == "26D" and len(r.features) == 7


def test_release_id_format_enforced() -> None:
    with pytest.raises(ValidationError):
        Release(id="2026-Q4")


def test_click_requires_target() -> None:
    with pytest.raises(ValidationError, match="requires a target"):
        Step(action=Action.CLICK, intent="Save")


def test_fill_requires_value() -> None:
    loc = Locator(strategies=[{"label": "Amount"}])
    with pytest.raises(ValidationError, match="requires a value"):
        Step(action=Action.FILL, intent="Amount", target=loc)


def test_locator_strategy_must_have_single_key() -> None:
    with pytest.raises(ValidationError, match="exactly one key"):
        Locator(strategies=[{"label": "A", "text": "B"}])


def test_unknown_field_rejected(tmp_path: Path) -> None:
    p = tmp_path / "t.yaml"
    p.write_text(
        "id: x\ntitle: t\nmodule: m\nproduct: p\nstepz: []\nsteps:\n  - {action: navigate, intent: go, value: A}\n"
    )
    with pytest.raises(SpecError, match="stepz"):
        load_test(p)


def test_undefined_placeholder_rejected(tmp_path: Path) -> None:
    p = tmp_path / "t.yaml"
    p.write_text(
        "id: x\ntitle: t\nmodule: m\nproduct: p\nsteps:\n  - {action: navigate, intent: go, value: '${nope}'}\n"
    )
    with pytest.raises(SpecError, match="undefined data placeholder"):
        load_test(p)


def test_data_may_only_reference_runtime_vars(tmp_path: Path) -> None:
    p = tmp_path / "t.yaml"
    p.write_text(
        "id: x\ntitle: t\nmodule: m\nproduct: p\ndata: {a: '${b}', b: '1'}\n"
        "steps:\n  - {action: navigate, intent: go, value: A}\n"
    )
    with pytest.raises(SpecError, match="runtime variables"):
        load_test(p)


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    body = "id: same\ntitle: t\nmodule: m\nproduct: p\nsteps:\n  - {action: navigate, intent: go, value: A}\n"
    (tmp_path / "a.yaml").write_text(body)
    (tmp_path / "b.yaml").write_text(body)
    with pytest.raises(SpecError, match="duplicate test ids: same"):
        load_tests(tmp_path)


def test_invalid_yaml_reports_file(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("id: [unclosed\n")
    with pytest.raises(SpecError, match="bad.yaml"):
        load_test(p)


def test_render_value_nested_runtime() -> None:
    data = {"inv": "QM-${RUN_ID}"}
    assert render_value("${inv}", data, {"RUN_ID": "AB12"}) == "QM-AB12"
    assert render_value(None, data) is None


def test_undefined_placeholder_in_locator_rejected(tmp_path: Path) -> None:
    p = tmp_path / "t.yaml"
    p.write_text(
        "id: x\ntitle: t\nmodule: m\nproduct: p\nsteps:\n"
        "  - {action: click, intent: open, target: {strategies: [{text: '${nope}'}]}}\n"
    )
    with pytest.raises(SpecError, match="undefined data placeholder"):
        load_test(p)


def test_login_as_requires_value() -> None:
    with pytest.raises(ValidationError, match="requires a value"):
        Step(action=Action.LOGIN_AS, intent="switch")


def test_masked_values_come_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from quartermaster.dsl.loader import MissingSecretError, display_value

    p = tmp_path / "t.yaml"
    p.write_text(
        "id: x\ntitle: t\nmodule: m\nproduct: p\ndata: {pin: '${env:QM_X_1}'}\n"
        "steps:\n  - {action: fill, intent: pin, value: '${pin}', target: {strategies: [{label: PIN}]}}\n"
    )
    test = load_test(p)  # env references are allowed in data
    with pytest.raises(MissingSecretError, match="QM_X_1"):
        render_value("${pin}", test.data)
    monkeypatch.setenv("QM_X_1", "1234")
    assert render_value("${pin}", test.data) == "1234"
    assert display_value("${pin}", test.data) == "••••••"  # what evidence shows
