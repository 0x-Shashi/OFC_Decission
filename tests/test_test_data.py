"""Test data: values made fresh for every run, and data sets whose values belong to a pod."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import FakeDriver

from quartermaster.domain.models import Environment, EnvironmentKind, GenerateRule, StepStatus
from quartermaster.dsl.data import (
    DATA_DIR,
    DataError,
    check_set,
    effective_data,
    generate_values,
    merge_sets,
    pod_values,
)
from quartermaster.dsl.library import files_of_tests
from quartermaster.dsl.loader import SpecError, load_test, load_tests
from quartermaster.runner.engine import run_test
from quartermaster.service import testdata
from quartermaster.service.insights import classify


def rule(**kw: Any) -> GenerateRule:
    return GenerateRule.model_validate(kw)


# ------------------------------------------------------------------ values made fresh for every run


def test_a_unique_value_is_new_each_run_and_the_same_all_through_one_run() -> None:
    rules = {"invoice": rule(unique=8, prefix="INV-", suffix="-X")}
    one = generate_values(rules, "AAAA1111")["invoice"]
    assert one == generate_values(rules, "AAAA1111")["invoice"]
    assert one != generate_values(rules, "BBBB2222")["invoice"]
    assert one.startswith("INV-") and one.endswith("-X") and len(one) == len("INV-") + 8 + len("-X")
    body = one[4:-2]
    assert body.isalnum() and body == body.upper()


def test_two_names_in_one_run_do_not_get_the_same_value() -> None:
    values = generate_values({"a": rule(unique=12), "b": rule(unique=12)}, "RUN1")
    assert values["a"] != values["b"]


def test_a_date_is_counted_from_today_in_the_format_asked_for() -> None:
    today = date(2026, 10, 3)
    rules = {
        "later": rule(date="today", plus_days=30, format="%Y-%m-%d"),
        "earlier": rule(date="today", plus_days=-3, format="%d-%b-%Y"),
        "now": rule(date="today"),
    }
    got = generate_values(rules, "R", today=today)
    assert got == {"later": "2026-11-02", "earlier": "30-Sep-2026", "now": "2026-10-03"}


def test_a_number_and_a_choice_stay_inside_what_was_asked() -> None:
    for run in ("R1", "R2", "R3", "R4", "R5", "R6"):
        got = generate_values({"n": rule(number=[100, 105]), "c": rule(choice=["USD", "EUR"])}, run)
        assert 100 <= int(got["n"]) <= 105
        assert got["c"] in ("USD", "EUR")
    assert generate_values({"n": rule(number=[7, 7])}, "R") == {"n": "7"}
    assert rule(choice=[1, 2, 3]).choice == ["1", "2", "3"]  # numbers in YAML are fine


@pytest.mark.parametrize(
    "bad, why",
    [
        ({}, "exactly one"),
        ({"unique": 6, "number": [1, 2]}, "exactly one"),
        ({"unique": 3}, "greater than or equal to 4"),
        ({"date": "today", "prefix": "X"}, "prefix and suffix go with"),
        ({"unique": 6, "plus_days": 2}, "plus_days and format go with"),
        ({"number": [9, 1]}, "must not be larger"),
        ({"date": "yesterday"}, "today"),
        ({"choice": []}, "at least 1"),
        ({"unique": 6, "color": "red"}, "color"),
    ],
)
def test_a_rule_that_does_not_make_sense_is_refused(bad: dict[str, Any], why: str) -> None:
    with pytest.raises(ValueError, match=why):
        rule(**bad)


# ------------------------------------------------------------------ data sets


SET = """
dataset: hcm-basics
title: Names on the pods
values:
  business_unit: US1 Business Unit
  ledger: Primary
pods:
  DEV2: {business_unit: Vision Operations}
  STAGE: {business_unit: US1 Stage BU, ledger: Stage Ledger}
"""


def _tests(tmp_path: Path, spec: str, sets: dict[str, str] | None = None) -> Path:
    root = tmp_path / "tests"
    root.mkdir(parents=True, exist_ok=True)
    (root / "t.yaml").write_text(spec, encoding="utf-8")
    for name, text in (sets or {}).items():
        (root / DATA_DIR).mkdir(exist_ok=True)
        (root / DATA_DIR / name).write_text(text, encoding="utf-8")
    return root


def test_a_data_set_is_checked_and_says_what_is_wrong() -> None:
    assert check_set(yaml.safe_load(SET)) == "hcm-basics"
    bad = {
        "a mapping": "- 1",
        "name": "dataset: has space\nvalues: {a: b}",
        "unknown": "dataset: x\nvalues: {a: b}\nextra: 1",
        "needs values": "dataset: x",
        "value": "dataset: x\nvalues: {a: [1]}",
        "usable": "dataset: x\nvalues: {'a b': c}",
        "pods": "dataset: x\npods: [DEV]",
        "pod values": "dataset: x\npods: {DEV: 5}",
    }
    for text in bad.values():
        with pytest.raises(DataError):
            check_set(yaml.safe_load(text))


def test_a_pod_is_matched_by_its_name_or_its_kind_and_the_name_wins() -> None:
    pods = {"dev": {"a": "kind", "b": "kind"}, "Dev2": {"a": "name"}, "STAGE": {"a": "stage"}}
    assert pod_values(pods, "DEV2", "DEV") == {"a": "name", "b": "kind"}
    assert pod_values(pods, "other", "DEV") == {"a": "kind", "b": "kind"}
    assert pod_values(pods, "other", "TEST") == {}
    assert effective_data({"a": "base", "z": "z"}, pods, "other", "STAGE") == {"a": "stage", "z": "z"}


TEST = """
id: t.one
title: One
module: HCM
product: HR
data_sets: [hcm-basics]
steps:
  - action: api_call
    intent: Look
    value: GET /x?bu=${business_unit}&l=${ledger}
"""


def test_a_test_loads_with_its_data_set_folded_in(tmp_path: Path) -> None:
    root = _tests(tmp_path, TEST, {"hcm-basics.yaml": SET})
    t = load_test(root / "t.yaml")
    assert t.data == {"business_unit": "US1 Business Unit", "ledger": "Primary"}
    assert t.pods["DEV2"] == {"business_unit": "Vision Operations"}
    assert t.pods["STAGE"]["ledger"] == "Stage Ledger"
    assert files_of_tests(root) == [root / "t.yaml"]  # the _data folder is not a place for tests
    assert [x.id for x in load_tests(root)] == ["t.one"]


def test_the_tests_own_data_and_pods_win_over_the_data_set(tmp_path: Path) -> None:
    spec = TEST.replace(
        "data_sets: [hcm-basics]", "data_sets: [hcm-basics]\ndata: {ledger: Mine}\npods: {STAGE: {business_unit: Own}}"
    )
    t = load_test(_tests(tmp_path, spec, {"hcm-basics.yaml": SET}) / "t.yaml")
    assert t.data["ledger"] == "Mine"
    # the set's STAGE ledger must not beat the test's own ledger; the test's own STAGE value beats the set's
    assert t.pods["STAGE"] == {"business_unit": "Own"}
    assert t.pods["DEV2"] == {"business_unit": "Vision Operations"}


def test_a_missing_data_set_is_named_with_what_there_is(tmp_path: Path) -> None:
    root = _tests(tmp_path, TEST.replace("hcm-basics", "nope"), {"hcm-basics.yaml": SET})
    with pytest.raises(SpecError, match=r"no data set named 'nope'.*there is: hcm-basics"):
        load_test(root / "t.yaml")
    with pytest.raises(DataError, match="list of data set names"):
        merge_sets({"data_sets": "hcm-basics"}, tmp_path / "t.yaml")


def test_a_name_nobody_defines_is_still_an_error_but_pod_and_generated_names_are_known(tmp_path: Path) -> None:
    root = _tests(tmp_path, TEST.replace("&l=${ledger}", "&l=${nobody}"), {"hcm-basics.yaml": SET})
    with pytest.raises(SpecError, match="undefined data placeholder"):
        load_test(root / "t.yaml")
    spec = (
        TEST.replace("data_sets: [hcm-basics]", "generate:\n  ref: {unique: 6}\npods: {STAGE: {only_stage: x}}")
        .replace("&l=${ledger}", "&r=${ref}&s=${only_stage}")
        .replace("bu=${business_unit}", "bu=1")
    )
    assert load_test(_tests(tmp_path, spec) / "t.yaml").generate["ref"].unique == 6


def test_generated_names_are_checked(tmp_path: Path) -> None:
    base = TEST.replace("data_sets: [hcm-basics]\n", "").replace("&bu=${business_unit}&l=${ledger}", "")
    base = base.replace("bu=${business_unit}&l=${ledger}", "x=1")
    for extra, why in [
        ("data: {ref: a}\ngenerate:\n  ref: {unique: 6}", "must be a new name"),
        ("generate:\n  RUN_ID: {unique: 6}", "must be a new name"),
        ("generate:\n  ref: {unique: 6}\npods: {STAGE: {ref: a}}", "both generated"),
        (
            "generate:\n  ref: {unique: 6}\ndata: {code: '${ref}-${other}'}",
            "may only reference runtime variables or generated",
        ),
    ]:
        with pytest.raises(SpecError, match=why):
            load_test(_tests(tmp_path, base.replace("steps:", extra + "\nsteps:", 1)) / "t.yaml")
    ok = base.replace("steps:", "generate:\n  ref: {unique: 6}\ndata: {code: 'C-${ref}-${RUN_ID}'}\nsteps:", 1)
    assert load_test(_tests(tmp_path, ok) / "t.yaml").data["code"] == "C-${ref}-${RUN_ID}"


# ------------------------------------------------------------------ in a run


def env(name: str, kind: EnvironmentKind) -> Environment:
    return Environment(name=name, url="https://abcd-dev2.fa.us2.oraclecloud.com", kind=kind)


def _paths(d: FakeDriver) -> list[str]:
    return [str(c[2]) for c in d.calls if c[0] == "api_call"]


def test_a_run_uses_the_values_of_the_pod_it_runs_on(tmp_path: Path) -> None:
    t = load_test(_tests(tmp_path, TEST, {"hcm-basics.yaml": SET}) / "t.yaml")
    for name, kind, expected in [
        ("DEV2", EnvironmentKind.DEV, "bu=Vision Operations&l=Primary"),
        ("other", EnvironmentKind.DEV, "bu=US1 Business Unit&l=Primary"),
        ("pod", EnvironmentKind.STAGE, "bu=US1 Stage BU&l=Stage Ledger"),
    ]:
        d = FakeDriver()
        result = run_test(t, env(name, kind), d, run_id="R1")
        assert result.status is StepStatus.PASSED
        assert _paths(d) == [f"/x?{expected}"]
        assert result.steps[0].value == f"GET /x?{expected}"  # the evidence shows what was used


def test_a_generated_value_is_the_same_in_every_step_of_a_run_and_new_in_the_next(tmp_path: Path) -> None:
    spec = """
id: t.gen
title: Gen
module: HCM
product: HR
generate:
  ref: {unique: 8, prefix: "R-"}
steps:
  - action: api_call
    intent: First
    value: GET /a/${ref}
  - action: api_call
    intent: Second
    value: GET /b/${ref}
"""
    t = load_test(_tests(tmp_path, spec) / "t.yaml")
    pod = env("pod", EnvironmentKind.DEV)
    first, second = FakeDriver(), FakeDriver()
    run_test(t, pod, first, run_id="AAAA0001")
    result = run_test(t, pod, second, run_id="BBBB0002")
    one, two = _paths(first), _paths(second)
    assert one[0].split("/")[-1] == one[1].split("/")[-1] and one[0].split("/")[-1].startswith("R-")
    assert one[0].split("/")[-1] != two[0].split("/")[-1]
    assert result.steps[0].value == f"GET {two[0]}"  # also in the evidence


def test_a_pod_without_a_value_stops_the_test_with_a_plain_message(tmp_path: Path) -> None:
    spec = TEST.replace("data_sets: [hcm-basics]", "pods: {STAGE: {location_limit: '1'}}").replace(
        "bu=${business_unit}&l=${ledger}", "limit=${location_limit}"
    )
    t = load_test(_tests(tmp_path, spec) / "t.yaml")
    d = FakeDriver()
    result = run_test(t, env("DEV2", EnvironmentKind.DEV), d, run_id="R1")
    assert result.status is StepStatus.FAILED
    assert result.steps[0].status is StepStatus.FAILED
    message = result.steps[0].error or ""
    assert message.startswith("No test data for location_limit on DEV2 (DEV)")
    assert classify(message) == "test_data"
    assert _paths(d) == []  # nothing was sent to the pod with a value that is not there
    ok = FakeDriver()
    assert run_test(t, env("pod", EnvironmentKind.STAGE), ok, run_id="R1").status is StepStatus.PASSED
    assert _paths(ok) == ["/x?limit=1"]


def test_the_stopped_test_marks_its_other_steps_as_not_run(tmp_path: Path) -> None:
    spec = TEST.replace("data_sets: [hcm-basics]", "pods: {STAGE: {gap: '1'}}").replace(
        "bu=${business_unit}&l=${ledger}", "g=${gap}"
    )
    spec += "  - action: api_call\n    intent: Later\n    value: GET /later\n"
    result = run_test(load_test(_tests(tmp_path, spec) / "t.yaml"), env("DEV2", EnvironmentKind.DEV), FakeDriver())
    assert [s.status for s in result.steps] == [StepStatus.FAILED, StepStatus.SKIPPED]


def test_a_value_that_no_step_uses_does_not_stop_the_test(tmp_path: Path) -> None:
    spec = TEST.replace("data_sets: [hcm-basics]", "pods: {STAGE: {unused: '1'}}").replace(
        "bu=${business_unit}&l=${ledger}", "x=1"
    )
    result = run_test(load_test(_tests(tmp_path, spec) / "t.yaml"), env("DEV2", EnvironmentKind.DEV), FakeDriver())
    assert result.status is StepStatus.PASSED


# ------------------------------------------------------------------ the page


def test_the_page_lists_sets_who_uses_them_the_gaps_and_the_generated_values(tmp_path: Path) -> None:
    spec = TEST.replace(
        "data_sets: [hcm-basics]", "data_sets: [hcm-basics, ghost]\ngenerate:\n  ref: {unique: 6, prefix: 'A-'}"
    )
    root = _tests(tmp_path, spec, {"hcm-basics.yaml": SET, "broken.yaml": "dataset: bad name\nvalues: {a: b}"})
    pods = [{"name": "DEV2", "kind": "DEV"}, {"name": "QA", "kind": "TEST"}, {"name": "S1", "kind": "STAGE"}]
    page = testdata.overview(root, pods)
    (one,) = page["sets"]
    assert one["name"] == "hcm-basics" and one["used_by"][0]["title"] == "One"
    by_pod = {r["pod"]: r["values"] for r in one["table"]}
    assert by_pod["DEV2"] == {"business_unit": "Vision Operations", "ledger": "Primary"}
    assert by_pod["S1"]["ledger"] == "Stage Ledger" and by_pod["QA"]["business_unit"] == "US1 Business Unit"
    assert one["gaps"] == []  # the set gives every name a default
    assert page["missing"][0]["name"] == "ghost"
    assert (
        page["problems"][0]["file"].endswith("broken.yaml")
        and "`dataset:` must be a name" in page["problems"][0]["problem"]
    )
    assert page["generated"] == []  # a test that names a data set that is not there cannot be previewed


def test_a_name_only_some_pods_give_is_shown_as_a_gap(tmp_path: Path) -> None:
    only = "dataset: only\npods:\n  STAGE: {special: s}\n  DEV2: {special: d}\n"
    root = _tests(tmp_path, TEST.replace("hcm-basics", "only"), {"only.yaml": only})
    page = testdata.overview(root, [{"name": "DEV2", "kind": "DEV"}, {"name": "QA", "kind": "TEST"}])
    (one,) = page["sets"]
    assert one["gaps"] == [{"name": "special", "pods": ["QA"]}]


def test_the_sample_values_and_the_rule_in_words(tmp_path: Path) -> None:
    spec = """
id: t.gen
title: Gen
module: HCM
product: HR
generate:
  ref: {unique: 6, prefix: "A-"}
  due: {date: today, plus_days: 30}
  amount: {number: [1, 9]}
  currency: {choice: [USD, EUR]}
steps:
  - action: api_call
    intent: Look
    value: GET /a/${ref}/${due}/${amount}/${currency}
"""
    page = testdata.overview(_tests(tmp_path, spec), [])
    (made,) = page["generated"]
    rules = {v["name"]: v for v in made["values"]}
    assert rules["ref"]["rule"] == "6 letters and digits, new each run after 'A-'"
    assert rules["due"]["rule"] == "the date today + 30 days, written as %Y-%m-%d"
    assert rules["amount"]["rule"] == "a whole number from 1 to 9"
    assert rules["currency"]["rule"] == "one of USD, EUR"
    assert rules["ref"]["sample"].startswith("A-") and rules["currency"]["sample"] in ("USD", "EUR")


# ------------------------------------------------------------------ in an exported test


def test_the_exported_runtime_makes_the_same_values_as_quartermaster() -> None:
    from test_export import RT

    rules = {
        "u": rule(unique=10, prefix="P-", suffix="-S"),
        "d": rule(date="today", plus_days=-9, format="%d/%m/%Y"),
        "n": rule(number=[5, 50]),
        "c": rule(choice=["a", "b", "c"]),
    }
    plain = {k: r.model_dump(exclude_none=True) for k, r in rules.items()}
    today = date(2026, 1, 31)
    for run in ("A1B2C3D4", "ZZZZ9999", "00000000"):
        assert RT.generate_values(plain, run, today) == generate_values(rules, run, today)


def test_the_exported_runtime_knows_which_pod_it_is_on() -> None:
    from test_export import RT

    assert RT.pod_identity("https://abcd-dev2.fa.us6.oraclecloud.com", {}) == ("abcd-dev2", "DEV")
    assert RT.pod_identity("https://abcd-stage1.fa.us6.oraclecloud.com", {}) == ("abcd-stage1", "STAGE")
    assert RT.pod_identity("https://abcd-test.fa.us6.oraclecloud.com", {}) == ("abcd-test", "TEST")
    said = {"QM_ENV_NAME": "DEV2", "QM_FUSION_KIND": "stage"}
    assert RT.pod_identity("https://abcd-dev2.fa.us6.oraclecloud.com", said) == ("DEV2", "STAGE")


def test_an_exported_test_uses_its_pod_values_its_generated_values_and_names_a_gap() -> None:
    from test_export import RT

    f = RT.Fusion(None, "https://abcd-dev2.fa.us6.oraclecloud.com")
    f.use_data(
        {"unit": "Base"},
        pods={
            "DEV": {"unit": "Dev unit", "only_dev": "d"},
            "abcd-DEV2": {"unit": "Mine"},
            "STAGE": {"only_stage": "s"},
        },
        generate={"ref": {"unique": 6, "prefix": "R-"}},
    )
    assert f.render("${unit}|${only_dev}") == "Mine|d"
    assert f.render("${ref}") == f.render("${ref}") and f.render("${ref}").startswith("R-")
    with pytest.raises(RT.StepFailure, match=r"No test data for only_stage on abcd-dev2 \(DEV\)"):
        f.render("x=${only_stage}")
    assert f.render("${nobody}") == "${nobody}"  # an unknown name is left alone, as before


def test_an_exported_test_file_asks_for_its_pod_values_and_generated_values(tmp_path: Path) -> None:
    from quartermaster.export.playwright_py import render_test

    spec = TEST.replace("data_sets: [hcm-basics]", "data_sets: [hcm-basics]\ngenerate:\n  ref: {unique: 6}")
    root = _tests(tmp_path, spec, {"hcm-basics.yaml": SET})
    text = render_test(load_test(root / "t.yaml"))
    compile(text, "t.py", "exec")  # it is valid Python
    assert "PODS = {" in text and "GENERATE = {" in text and "'DEV2': {'business_unit': 'Vision Operations'}" in text
    assert "fusion.use_data(DATA, pods=PODS, generate=GENERATE)" in text
    plain = render_test(
        load_test(
            _tests(
                tmp_path / "p",
                TEST.replace("data_sets: [hcm-basics]\n", "data: {a: b}\n")
                .replace("${business_unit}", "${a}")
                .replace("&l=${ledger}", ""),
            )
            / "t.yaml"
        )
    )
    assert "fusion.data.update(DATA)" in plain and "use_data" not in plain  # nothing changes for a test without them


def test_qm_run_uses_the_pod_name_it_is_given_to_choose_the_test_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from quartermaster import cli

    root = _tests(tmp_path, TEST, {"hcm-basics.yaml": SET})
    seen: list[FakeDriver] = []

    def make(args: Any, run_dir: Path) -> FakeDriver:
        seen.append(FakeDriver(evidence_dir=run_dir))
        return seen[-1]

    monkeypatch.setattr(cli, "driver_factory", make)
    monkeypatch.setenv("QM_FUSION_URL", "https://abcd-dev2.fa.us6.oraclecloud.com")
    monkeypatch.delenv("QM_FUSION_KIND", raising=False)
    for name, expected in (("DEV2", "bu=Vision Operations&l=Primary"), ("OTHER", "bu=US1 Business Unit&l=Primary")):
        monkeypatch.setenv("QM_ENV_NAME", name)  # what the web service sets from the pod's name in Settings
        assert cli.main(["run", str(root), "--evidence", str(tmp_path / f"e-{name}")]) == 0
        assert _paths(seen[-1]) == [f"/x?{expected}"]
    capsys.readouterr()
