"""Shared steps: a group of steps written once in `_library` and used by many tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_service_api import add_suite_tests, app, call, finished_suite_run  # noqa: F401, F811  (app is a fixture)

from quartermaster.dsl.library import LibraryError, expand, files_of_tests, library_dir_for
from quartermaster.dsl.loader import SpecError, load_test, load_tests

OPEN = """
library: open-page
title: Open a page
description: Opens a page from the menu.
params:
  page_name: Locations
  wait: null
steps:
  - action: navigate
    intent: Open ${page_name}
    value: Workforce Structures > ${page_name}
  - action: assert_visible
    intent: ${page_name} is open for ${who}
    target:
      strategies:
        - text: ${page_name}
"""

MAKE = """
library: make-location
params: {}
steps:
  - action: api_call
    intent: Create a location
    value: POST /hcmRestApi/resources/11.13.18.05/locationsV2
    options:
      body: {LocationName: "QM-${RUN_ID}"}
      save:
        location_id: LocationId
cleanup:
  - action: api_call
    intent: Remove the location
    value: DELETE /hcmRestApi/resources/11.13.18.05/locationsV2/${location_id}
"""

TEST = """
id: t.shared
title: Uses shared steps
module: HCM
product: HR
data: {who: Pat}
steps:
  - action: navigate
    intent: Home
    value: Home
  - use: open-page
    with: {wait: "5"}
  - action: assert_text
    intent: Last step
    target: {strategies: [{text: x}]}
    value: done
"""


def tree(tmp_path: Path, test: str = TEST, **groups: str) -> Path:
    root = tmp_path / "tests"
    (root / "_library").mkdir(parents=True)
    (root / "hcm").mkdir()
    for name, text in {"open-page": OPEN, "make-location": MAKE, **groups}.items():
        (root / "_library" / f"{name}.yaml").write_text(text)
    path = root / "hcm" / "t.yaml"
    path.write_text(test)
    return path


def test_a_use_step_becomes_the_groups_steps(tmp_path: Path) -> None:
    test = load_test(tree(tmp_path))
    assert [s.intent for s in test.steps] == [
        "Home",
        "Open Locations",  # the group's default
        "Locations is open for ${who}",  # ${who} is test data, filled in at run time
        "Last step",
    ]
    assert [s.shared for s in test.steps] == [None, "open-page", "open-page", None]
    assert test.steps[1].value == "Workforce Structures > Locations"
    assert test.steps[2].target is not None and test.steps[2].target.ordered()[0][1] == "Locations"


def test_the_test_can_hand_in_a_value(tmp_path: Path) -> None:
    path = tree(tmp_path, TEST.replace('with: {wait: "5"}', 'with: {wait: "5", page_name: Jobs}'))
    assert load_test(path).steps[1].intent == "Open Jobs"


def test_a_value_the_group_needs_must_be_given(tmp_path: Path) -> None:
    path = tree(tmp_path, TEST.replace('    with: {wait: "5"}\n', ""))
    with pytest.raises(SpecError, match=r"step 2: 'open-page' needs a value for wait"):
        load_test(path)


def test_a_parameter_the_group_does_not_have_is_refused_and_names_what_it_takes(tmp_path: Path) -> None:
    path = tree(tmp_path, TEST.replace('{wait: "5"}', '{wait: "5", colour: red}'))
    with pytest.raises(SpecError, match=r"has no parameter colour \(it takes: page_name, wait\)"):
        load_test(path)


def test_a_group_that_does_not_exist_names_the_ones_that_do(tmp_path: Path) -> None:
    path = tree(tmp_path, TEST.replace("use: open-page", "use: open-pge"))
    with pytest.raises(SpecError, match=r"no shared group named 'open-pge'.*there is: make-location, open-page"):
        load_test(path)


def test_a_test_without_use_is_unchanged(tmp_path: Path) -> None:
    raw = {"id": "a", "steps": [{"action": "navigate"}]}
    out, origins = expand(raw, tmp_path / "x.yaml")
    assert out is raw and origins == [(None, 0)]


def test_a_group_may_not_use_another_group(tmp_path: Path) -> None:
    nested = "library: outer\nsteps:\n  - use: open-page\n"
    path = tree(tmp_path, TEST.replace("use: open-page", "use: outer"), outer=nested)
    with pytest.raises(SpecError, match="no shared group named 'outer'"):  # the unusable file is not offered
        load_test(path)


def test_other_names_in_a_group_are_test_data_and_the_test_must_have_them(tmp_path: Path) -> None:
    typing = "library: type-name\nsteps:\n  - action: navigate\n    intent: Go\n    value: Person ${who}\n"
    text = TEST.replace("use: open-page", "use: type-name").replace('    with: {wait: "5"}\n', "")
    path = tree(tmp_path, text.replace("data: {who: Pat}", "data: {}"), **{"type-name": typing})
    with pytest.raises(SpecError, match=r"\(from the shared steps 'type-name', so add that name to the test's data\)"):
        load_test(path)
    assert load_test(tree(tmp_path / "ok", text, **{"type-name": typing})).steps[1].value == "Person ${who}"


def test_a_groups_cleanup_is_added_to_the_test_after_its_own_in_reverse_order(tmp_path: Path) -> None:
    text = TEST.replace("data: {who: Pat}", "data: {who: Pat}").replace(
        "  - use: open-page", "  - use: make-location\n  - use: open-page"
    )
    text += "cleanup:\n  - action: navigate\n    intent: Back home\n    value: Home\n"
    test = load_test(tree(tmp_path, text))
    assert [c.intent for c in test.cleanup] == ["Back home", "Remove the location"]
    assert test.cleanup[1].shared == "make-location"
    assert [s.intent for s in test.steps][1] == "Create a location"


def test_a_cleanup_delete_in_a_group_still_has_to_use_a_saved_value(tmp_path: Path) -> None:
    bad = MAKE.replace("locationsV2/${location_id}", "locationsV2/300")
    path = tree(
        tmp_path,
        TEST.replace("use: open-page", "use: make-location").replace('    with: {wait: "5"}\n', ""),
        **{"make-location": bad},
    )
    with pytest.raises(SpecError, match="deletes a fixed address"):
        load_test(path)


def test_group_files_are_not_tests(tmp_path: Path) -> None:
    path = tree(tmp_path)
    root = tmp_path / "tests"
    assert files_of_tests(root) == [path]
    assert [t.id for t in load_tests(root)] == [
        "t.shared"
    ]  # the groups are not loaded as tests (and so not 'duplicate')
    assert library_dir_for(path) == (root / "_library").resolve()


def test_unusable_group_files_are_listed_not_fatal(tmp_path: Path) -> None:
    path = tree(tmp_path, bad="library: bad\nsteps: []\n", worse="library: [x\n", twin=OPEN)
    assert load_test(path).steps[1].shared == "open-page"  # the good groups still work
    from quartermaster.dsl.library import read_groups

    groups, problems = read_groups(library_dir_for(path))
    assert sorted(groups) == ["make-location", "open-page"]
    assert sorted(p.stem for p, _ in problems) == ["bad", "twin", "worse"]
    assert any("also used by" in why for _, why in problems)


def test_the_error_names_the_test_file(tmp_path: Path) -> None:
    path = tree(tmp_path, TEST.replace("use: open-page", "use: nope"))
    with pytest.raises(SpecError) as e:
        load_test(path)
    assert str(path) in str(e.value)
    with pytest.raises(LibraryError):
        expand({"steps": [{"use": "nope", "extra": 1}]}, path)


# ------------------------------------------------------------------ in the service


def test_the_service_lists_groups_their_users_and_the_expanded_step_count(app: Any) -> None:  # noqa: F811
    add_suite_tests(app)
    lib = app.tests_root / "_library"
    lib.mkdir()
    (lib / "open-page.yaml").write_text(OPEN)
    (app.tests_root / "hcm" / "user.yaml").write_text(
        "id: hcm.user\ntitle: A user\nmodule: HCM\nproduct: HR\ndata: {who: Pat}\n"
        'steps:\n  - use: open-page\n    with: {wait: "1"}\n'
    )
    (app.tests_root / "hcm" / "ghost.yaml").write_text(
        "id: hcm.ghost\ntitle: Ghost\nmodule: HCM\nproduct: HR\nsteps:\n  - use: missing-group\n"
    )
    tests = {t["file"]: t for t in call(app, "GET", "/api/tests")}
    assert "_library/open-page.yaml" not in tests
    assert tests["hcm/user.yaml"]["steps"] == 2 and tests["hcm/user.yaml"]["uses"] == ["open-page"]

    lib_view = call(app, "GET", "/api/library")
    (group,) = lib_view["groups"]
    assert group["name"] == "open-page" and group["used_by"] == [{"file": "hcm/user.yaml", "title": "A user"}]
    assert group["params"] == [{"name": "page_name", "default": "Locations"}, {"name": "wait", "default": None}]
    assert (
        lib_view["missing"][0]["name"] == "missing-group" and lib_view["missing"][0]["used_by"][0]["title"] == "Ghost"
    )
    detail = call(app, "GET", "/api/test?file=hcm/user.yaml")
    assert [(s["intent"], s["shared"]) for s in detail["steps_detail"]][0] == ("Open Locations", "open-page")


def test_a_screen_change_in_a_shared_step_is_fixed_in_the_shared_file(app: Any) -> None:  # noqa: F811
    add_suite_tests(app)
    lib = app.tests_root / "_library"
    lib.mkdir()
    (lib / "enter-name.yaml").write_text(
        "library: enter-name\nsteps:\n  - action: fill\n    intent: Enter the name\n    value: ${name}\n"
        '    target:\n      strategies:\n        - label: Name\n        - role: "textbox:Name"\n'
    )
    (app.tests_root / "hcm" / "personal.yaml").write_text(
        "id: hcm.personal-info\ntitle: Personal info\nmodule: HCM\nproduct: HR\ndata: {name: Pat}\n"
        "steps:\n  - use: enter-name\n"
    )
    finished_suite_run(app)
    (update,) = [i for i in call(app, "GET", "/api/attention")["items"] if i["category"] == "ui_change"]
    assert (
        update["fix_file"] == "_library/enter-name.yaml"
        and update["fix_step"] == 0
        and update["shared"] == "enter-name"
    )

    done = call(
        app,
        "POST",
        "/api/test/accept-update",
        {"file": update["fix_file"], "step_index": update["fix_step"], "new": update["new"]},
    )
    assert Path(done["backup"]).is_file()
    assert "- role: textbox:Name" in (lib / "enter-name.yaml").read_text().split("- label")[0].replace('"', "")
    assert "ui_change" not in call(app, "GET", "/api/attention")["counts"]
