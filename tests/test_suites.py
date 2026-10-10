"""Saved suites: a name for a group of tests, worked out from a rule each time it is used."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import FakeDriver
from test_service_api import app, call  # noqa: F401, F811  (app is a fixture)

from quartermaster import cli
from quartermaster.dsl.library import files_of_tests
from quartermaster.dsl.loader import load_tests
from quartermaster.dsl.suites import SUITES_DIR, SuiteError, check_suite, fits, read_suites, resolve, rules_of
from quartermaster.service import suites as service
from quartermaster.service.api import ApiError
from quartermaster.service.auth import required_role

TESTS: list[dict[str, Any]] = [
    {
        "id": "hcm.view",
        "folder": "hcm",
        "module": "HCM",
        "product": "Core HR",
        "priority": "critical",
        "tags": ["smoke"],
    },
    {
        "id": "hcm.pay",
        "folder": "hcm/payroll",
        "module": "HCM",
        "product": "Payroll",
        "priority": "high",
        "tags": ["Smoke", "slow"],
    },
    {
        "id": "fin.invoice",
        "folder": "fin",
        "module": "Financials",
        "product": "Payables",
        "priority": "high",
        "tags": [],
    },
    {
        "id": "fin.pay",
        "folder": "fin",
        "module": "Financials",
        "product": "Payables",
        "priority": "low",
        "tags": ["flaky"],
    },
    {"id": "", "folder": "fin", "module": "Financials", "tags": ["smoke"]},
    {"id": "broken", "folder": "fin", "tags": ["smoke"], "problem": "Could not read this file"},
]


def ids(raw: dict[str, Any]) -> list[str]:
    return resolve(raw, TESTS).ids


# ------------------------------------------------------------------ the rule


def test_a_line_fits_when_the_test_has_any_of_its_values_and_capitals_do_not_matter() -> None:
    assert ids({"include": [{"tags": ["SMOKE", "nothing"]}]}) == ["hcm.view", "hcm.pay"]
    assert ids({"include": [{"tags": "flaky"}]}) == ["fin.pay"]  # one word is a list of one
    assert ids({"include": [{"modules": ["hcm"]}]}) == ["hcm.view", "hcm.pay"]
    assert ids({"include": [{"priorities": ["Critical", "LOW"]}]}) == ["hcm.view", "fin.pay"]
    assert ids({"include": [{"tests": ["FIN.INVOICE"]}]}) == ["fin.invoice"]


def test_a_folder_includes_what_is_inside_it_but_not_a_folder_with_a_similar_name() -> None:
    assert ids({"include": [{"folders": ["hcm"]}]}) == ["hcm.view", "hcm.pay"]
    assert ids({"include": [{"folders": ["hcm/payroll/"]}]}) == ["hcm.pay"]
    assert not fits({"folders": ["hc"]}, {"folder": "hcm"})  # "hc" is not the folder "hcm"


def test_inside_a_group_every_line_must_fit_and_any_group_will_do() -> None:
    both = {"include": [{"products": ["Payables"], "priorities": ["high"]}]}
    assert ids(both) == ["fin.invoice"]
    either = {"include": [{"products": ["Payables"], "priorities": ["high"]}, {"tags": ["smoke"]}]}
    assert ids(either) == ["hcm.view", "hcm.pay", "fin.invoice"]  # the order of the tests, each once


def test_a_test_that_fits_a_leave_out_group_is_not_in() -> None:
    raw = {"include": [{"modules": ["Financials", "HCM"]}], "exclude": [{"tags": ["flaky"]}, {"tests": ["hcm.pay"]}]}
    assert ids(raw) == ["hcm.view", "fin.invoice"]


def test_a_test_without_an_id_or_that_cannot_be_read_is_never_in() -> None:
    assert ids({"include": [{"tags": ["smoke"]}]}) == ["hcm.view", "hcm.pay"]


def test_warnings_say_when_a_group_or_an_id_finds_nothing() -> None:
    got = resolve({"include": [{"tags": ["smoke"]}, {"tags": ["nope"]}, {"tests": ["gone.test"]}]}, TESTS)
    assert got.ids == ["hcm.view", "hcm.pay"]
    assert got.warnings == ["Group 2 matches no test.", "Group 3 matches no test.", "No test has the id gone.test."]


def test_a_suite_is_worked_out_again_each_time() -> None:
    raw = {"include": [{"tags": ["smoke"]}]}
    later = [*TESTS, {"id": "new.one", "folder": "x", "tags": ["smoke"]}]
    assert "new.one" not in resolve(raw, TESTS).ids and "new.one" in resolve(raw, later).ids


@pytest.mark.parametrize(
    "text, why",
    [
        ("- 1", "YAML mapping"),
        ("suite: has space\ninclude: [{tags: [a]}]", "must be a name"),
        ("suite: x", "at least one group"),
        ("suite: x\ninclude: []", "at least one group"),
        ("suite: x\ninclude: [{tags: [a]}]\nextra: 1", "unknown keys: extra"),
        ("suite: x\ninclude: [{}]", "at least one line"),
        ("suite: x\ninclude: [{colour: [red]}]", "unknown line 'colour'"),
        ("suite: x\ninclude: [{tags: []}]", "list of words"),
        ("suite: x\ninclude: [{tags: [[a]]}]", "list of words"),
        ("suite: x\ninclude: [{tags: ['']}]", "empty value"),
        ("suite: x\ninclude: [{priorities: [urgent]}]", "'urgent' is not a priority"),
        ("suite: x\ninclude: [{tags: [a]}]\nexclude: {tags: [b]}", "must be a list of groups"),
        ("suite: x\ninclude: [{tags: [a]}]\nexclude: [{tags: [b], nope: [c]}]", "exclude group 1: unknown line"),
    ],
)
def test_a_suite_that_does_not_make_sense_says_what_is_wrong(text: str, why: str) -> None:
    with pytest.raises(SuiteError, match=why):
        check_suite(yaml.safe_load(text))


def test_numbers_in_a_line_are_read_as_words() -> None:
    assert rules_of({"tags": [2024, "x"]}, "g") == {"tags": ["2024", "x"]}


# ------------------------------------------------------------------ the folder


def write(root: Path, name: str, text: str) -> None:
    (root / SUITES_DIR).mkdir(parents=True, exist_ok=True)
    (root / SUITES_DIR / name).write_text(text, encoding="utf-8")


def test_the_suites_folder_is_not_a_place_for_tests(tmp_path: Path) -> None:
    (tmp_path / "t.yaml").write_text(
        "id: a\ntitle: A\nmodule: M\nproduct: P\nsteps: [{action: navigate, intent: x, value: y}]\n"
    )
    write(tmp_path, "s.yaml", "suite: s\ninclude: [{tags: [a]}]\n")
    assert files_of_tests(tmp_path) == [tmp_path / "t.yaml"]
    assert [t.id for t in load_tests(tmp_path)] == ["a"]


def test_unreadable_or_repeated_suites_are_listed_as_problems_not_hidden(tmp_path: Path) -> None:
    write(tmp_path, "good.yaml", "suite: good\ninclude: [{tags: [a]}]\n")
    write(tmp_path, "zzz.yaml", "suite: good\ninclude: [{tags: [b]}]\n")
    write(tmp_path, "bad.yaml", "suite: bad\n")
    write(tmp_path, "broken.yaml", "suite: [unclosed\n")
    found, problems = read_suites(tmp_path / SUITES_DIR)
    assert list(found) == ["good"]
    assert sorted(p.name for p, _ in problems) == ["bad.yaml", "broken.yaml", "zzz.yaml"]
    assert any("also used by" in why for _, why in problems)


# ------------------------------------------------------------------ the service


def test_the_service_lists_suites_with_their_tests_choices_and_whether_the_page_can_edit_them(tmp_path: Path) -> None:
    write(
        tmp_path,
        "smoke.yaml",
        "suite: smoke\ntitle: Smoke tests\ninclude: [{tags: [smoke]}]\nexclude: [{tags: [slow]}]\n",
    )
    write(tmp_path, "odd.yaml", "suite: odd\ninclude: [{tags: [smoke]}]\nexclude: [{products: [Payroll]}]\n")
    page = service.listing(tmp_path, TESTS)
    by_name = {s["name"]: s for s in page["suites"]}
    assert [t["id"] for t in by_name["smoke"]["tests"]] == ["hcm.view"]
    assert by_name["smoke"]["editable"] and not by_name["odd"]["editable"]  # a leave-out by product: edit the file
    assert page["choices"]["tags"] == ["flaky", "slow", "smoke"]  # "Smoke" and "smoke" are one tag
    assert page["choices"]["folders"] == ["fin", "hcm", "hcm/payroll"]
    assert page["choices"]["priorities"] == ["critical", "high", "medium", "low"]


def test_a_suite_is_saved_from_the_page_and_read_back(tmp_path: Path) -> None:
    saved = service.save(
        tmp_path,
        {"title": "Payables: what matters", "include": [{"products": ["Payables"], "priorities": ["high"]}]},
        TESTS,
    )
    assert saved == {"name": "payables-what-matters", "title": "Payables: what matters", "tests": 1, "warnings": []}
    text = (tmp_path / SUITES_DIR / "payables-what-matters.yaml").read_text()
    assert yaml.safe_load(text)["include"] == [{"products": ["Payables"], "priorities": ["high"]}]
    again = service.listing(tmp_path, TESTS)["suites"][0]
    assert again["include"] == [{"products": ["Payables"], "priorities": ["high"]}] and again["editable"]


def test_changing_a_suite_keeps_its_name_and_file_and_a_second_new_one_with_the_same_name_is_refused(
    tmp_path: Path,
) -> None:
    rule = {"title": "Smoke", "include": [{"tags": ["smoke"]}]}
    service.save(tmp_path, rule, TESTS)
    with pytest.raises(SuiteError, match="already a suite called 'smoke'"):
        service.save(tmp_path, rule, TESTS)
    service.save(tmp_path, {"name": "smoke", "title": "Smoke, renamed", "include": [{"tags": ["flaky"]}]}, TESTS)
    assert [p.name for p in (tmp_path / SUITES_DIR).iterdir()] == ["smoke.yaml"]
    only = service.listing(tmp_path, TESTS)["suites"][0]
    assert (
        only["title"] == "Smoke, renamed"
        and only["name"] == "smoke"
        and [t["id"] for t in only["tests"]] == ["fin.pay"]
    )


def test_what_the_page_sends_is_checked_before_anything_is_written(tmp_path: Path) -> None:
    for bad, why in [
        ({"title": "", "include": [{"tags": ["a"]}]}, "give the suite a name"),
        ({"title": "!!!", "include": [{"tags": ["a"]}]}, "needs some letters"),
        ({"title": "X", "include": []}, "at least one group"),
        ({"title": "X", "include": [{"priorities": ["urgent"]}]}, "not a priority"),
        ({"title": "X", "include": "tags"}, "must be a list"),
    ]:
        with pytest.raises(SuiteError, match=why):
            service.save(tmp_path, bad, TESTS)
    assert not (tmp_path / SUITES_DIR).exists() or not list((tmp_path / SUITES_DIR).iterdir())


def test_a_rule_can_be_tried_before_it_is_saved_and_a_suite_deleted(tmp_path: Path) -> None:
    got = service.preview({"include": [{"folders": ["fin"]}], "exclude": [{"tags": ["flaky"]}]}, TESTS)
    assert [t["id"] for t in got["tests"]] == ["fin.invoice"]
    with pytest.raises(SuiteError):
        service.preview({"include": []}, TESTS)
    service.save(tmp_path, {"title": "Smoke", "include": [{"tags": ["smoke"]}]}, TESTS)
    service.delete(tmp_path, "smoke")
    assert service.listing(tmp_path, TESTS)["suites"] == []
    with pytest.raises(LookupError):
        service.delete(tmp_path, "smoke")


def test_the_tests_of_a_suite_say_what_is_wrong_when_it_cannot_be_run(tmp_path: Path) -> None:
    write(tmp_path, "smoke.yaml", "suite: smoke\ntitle: Smoke\ninclude: [{tags: [smoke]}]\n")
    write(tmp_path, "empty.yaml", "suite: empty\ntitle: Nothing\ninclude: [{tags: [nope]}]\n")
    assert service.tests_of(tmp_path, "smoke", TESTS) == ("Smoke", ["hcm.view", "hcm.pay"])
    with pytest.raises(SuiteError, match=r"no suite called 'nope' \(there is: empty, smoke\)"):
        service.tests_of(tmp_path, "nope", TESTS)
    with pytest.raises(SuiteError, match="'Nothing' has no tests right now"):
        service.tests_of(tmp_path, "empty", TESTS)


def test_testers_may_save_suites_and_everyone_may_look() -> None:
    assert required_role("POST", ["suites"]) == "tester" and required_role("POST", ["suites", "delete"]) == "tester"
    assert required_role("POST", ["suites", "preview"]) == "tester" and required_role("GET", ["suites"]) == "any"


# ------------------------------------------------------------------ running one


def add_tests(app: Any) -> None:  # noqa: F811
    for name, tags in (("a", "[smoke]"), ("b", "[smoke, slow]"), ("c", "[]")):
        (app.tests_root / "hcm" / f"{name}.yaml").write_text(
            f"id: hcm.{name}\ntitle: Test {name}\nmodule: HCM\nproduct: HR\ntags: {tags}\nsteps: [{{}}, {{}}]\n"
        )


def test_the_pages_save_a_suite_and_run_it_with_the_tests_it_has_now(app: Any) -> None:  # noqa: F811
    add_tests(app)
    saved = call(
        app,
        "POST",
        "/api/suites",
        {"title": "Fast smoke", "include": [{"tags": ["smoke"]}], "exclude": [{"tags": ["slow"]}]},
    )
    assert saved["name"] == "fast-smoke" and saved["tests"] == 1
    run = call(app, "POST", "/api/runs", {"suite": "fast-smoke"})
    assert (
        run["target"] == "." and run["options"]["only"] == ["hcm.a"] and run["options"]["label"] == "Suite: Fast smoke"
    )
    # a test added later that fits the rule joins by itself
    (app.tests_root / "hcm" / "d.yaml").write_text(
        "id: hcm.d\ntitle: Test d\nmodule: HCM\nproduct: HR\ntags: [smoke]\nsteps: [{}, {}]\n"
    )
    assert call(app, "POST", "/api/runs", {"suite": "fast-smoke"})["options"]["only"] == ["hcm.a", "hcm.d"]
    with pytest.raises(ApiError, match="no suite called 'ghost'") as refused:
        app.handle("POST", "/api/runs", b'{"suite": "ghost"}')
    assert refused.value.status == 400
    made = [e for e in call(app, "GET", "/api/audit")["entries"] if e["action"] == "Made a suite"]
    assert made and made[0]["subject"] == "Fast smoke"


def test_a_schedule_runs_the_suite_as_it_is_when_the_schedule_fires(app: Any) -> None:  # noqa: F811
    add_tests(app)
    call(app, "POST", "/api/suites", {"title": "Smoke", "include": [{"tags": ["smoke"]}]})
    made = call(
        app, "POST", "/api/schedules", {"name": "Nightly smoke", "suite": "smoke", "days": [0, 1], "time": "02:00"}
    )
    assert made["suite"] == "smoke" and made["target"] == "."
    (app.tests_root / "hcm" / "d.yaml").write_text(
        "id: hcm.d\ntitle: Test d\nmodule: HCM\nproduct: HR\ntags: [smoke]\nsteps: [{}, {}]\n"
    )
    run = call(app, "POST", "/api/schedules/run", {"id": made["id"]})
    assert (
        run["options"]["only"] == ["hcm.a", "hcm.b", "hcm.d"] and run["options"]["label"] == "Scheduled: Nightly smoke"
    )
    with pytest.raises(ApiError, match="no suite called 'ghost'"):
        app.handle("POST", "/api/schedules", b'{"name": "x", "suite": "ghost", "days": [0], "time": "02:00"}')


# ------------------------------------------------------------------ the command line


def make_folder(tmp_path: Path) -> Path:
    root = tmp_path / "tests"
    (root / "hcm").mkdir(parents=True)
    for name, tags, folder in (("a", "[smoke]", "hcm"), ("b", "[slow]", "hcm"), ("c", "[smoke]", ".")):
        (root / folder / f"{name}.yaml").write_text(
            f"id: t.{name}\ntitle: Test {name}\nmodule: HCM\nproduct: HR\ntags: {tags}\nsteps:\n"
            "  - action: api_call\n    intent: Look\n    value: GET /x\n"
        )
    write(root, "smoke.yaml", "suite: smoke\ntitle: Smoke\ninclude: [{tags: [smoke]}]\n")
    write(root, "hcm.yaml", "suite: hcm\ninclude: [{folders: [hcm]}]\nexclude: [{tags: [slow]}]\n")
    return root


def test_qm_suites_lists_them_with_their_tests(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_folder(tmp_path)
    assert cli.main(["suites", str(root), "--list"]) == 0
    out = capsys.readouterr().out
    assert "hcm" in out and "smoke" in out and "2 tests  Smoke" in out and "    t.a" in out and "    t.c" in out
    empty = tmp_path / "none"
    empty.mkdir()
    assert cli.main(["suites", str(empty)]) == 0 and "No saved suites yet" in capsys.readouterr().out


def test_qm_run_suite_runs_just_the_tests_it_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_folder(tmp_path)
    seen: list[str] = []

    def make(args: Any, run_dir: Path) -> FakeDriver:
        seen.append(run_dir.parent.name)
        return FakeDriver(evidence_dir=run_dir)

    monkeypatch.setattr(cli, "driver_factory", make)
    monkeypatch.setenv("QM_FUSION_URL", "https://abcd-dev2.fa.us6.oraclecloud.com")
    assert cli.main(["run", str(root), "--suite", "hcm", "--evidence", str(tmp_path / "e")]) == 0
    assert seen == ["t.a"]  # the folder hcm, without the slow test; not the smoke test in the top folder
    capsys.readouterr()
    assert cli.main(["run", str(root), "--suite", "ghost", "--evidence", str(tmp_path / "e2")]) == 2
    assert "no suite called 'ghost' (there is: hcm, smoke)" in capsys.readouterr().err
    assert cli.main(["run", str(root), "--suite", "smoke", "--only", "t.a", "--evidence", str(tmp_path / "e3")]) == 2
    assert "not both" in capsys.readouterr().err


def test_the_demo_suite_picks_the_demos_that_should_pass() -> None:
    from conftest import EXAMPLES

    from quartermaster.cli import _suite_ids

    demos = EXAMPLES / "demos"
    tests = load_tests(demos)
    got = _suite_ids(demos, tests, files_of_tests(demos), "demos-that-pass")
    assert sorted(got) == ["demo.cleanup", "demo.library-one", "demo.library-two", "demo.setup", "demo.test-data"]
