"""Cleanup after a run: steps that remove what the test made, whether it passed or failed."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeDriver
from test_service_api import add_suite_tests, app, call, finished_suite_run  # noqa: F401, F811  (app is a fixture)

from quartermaster.domain.models import Environment, ScreenshotMode, StepStatus
from quartermaster.dsl.loader import SpecError, load_test
from quartermaster.evidence.document import write_evidence_document
from quartermaster.evidence.run_record import build_record
from quartermaster.runner.engine import run_test

SPEC = """
id: hcm.location
title: Create a location
module: HCM
product: Global Human Resources
data:
  name: QM-LOC-${RUN_ID}
steps:
  - action: api_call
    intent: Create the location
    value: POST /hcmRestApi/resources/11.13.18.05/locationsV2
    options:
      body: {LocationName: "${name}"}
      save: {location_id: LocationId}
  - action: api_call
    intent: The location is saved
    value: GET /hcmRestApi/resources/11.13.18.05/locationsV2/${location_id}
    options:
      check: {LocationName: "${name}"}
cleanup:
  - action: api_call
    intent: Remove the location
    value: DELETE /hcmRestApi/resources/11.13.18.05/locationsV2/${location_id}
"""


def _load(tmp_path: Path, text: str = SPEC) -> Any:
    f = tmp_path / "t.yaml"
    f.write_text(text, encoding="utf-8")
    return load_test(f)


class _Pod(FakeDriver):
    """A pod that makes a location on POST, answers GET with its name, and can refuse a DELETE."""

    def __init__(self, *, refuse_delete: bool = False, fail_create: bool = False, **kw: Any):
        super().__init__(**kw)
        self.refuse_delete, self.fail_create = refuse_delete, fail_create

    def api_call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append(("api_call", method, path))
        if method == "POST":
            return (500, None) if self.fail_create else (201, {"LocationId": 77})
        if method == "DELETE":
            return (403, None) if self.refuse_delete else (204, None)
        return 200, {"LocationName": "QM-LOC-R1"}


def _deletes(d: FakeDriver) -> list[tuple[Any, ...]]:
    return [c for c in d.calls if c[0] == "api_call" and c[1] == "DELETE"]


def test_cleanup_runs_after_a_passing_test(tmp_path: Path, stage_env: Environment) -> None:
    d = _Pod()
    result = run_test(_load(tmp_path), stage_env, d, run_id="R1")
    assert result.status is StepStatus.PASSED
    assert _deletes(d) == [("api_call", "DELETE", "/hcmRestApi/resources/11.13.18.05/locationsV2/77")]
    assert [c.status for c in result.cleanup] == [StepStatus.PASSED]
    assert result.cleanup_status == "done"
    assert d.closed


def test_cleanup_runs_after_a_failing_test_and_does_not_change_its_result(
    tmp_path: Path, stage_env: Environment
) -> None:
    text = SPEC.replace('check: {LocationName: "${name}"}', 'check: {LocationName: "something else"}')
    d = _Pod()
    result = run_test(_load(tmp_path, text), stage_env, d, run_id="R1")
    assert result.status is StepStatus.FAILED
    assert len(result.steps) == 2  # cleanup steps are not counted among the test's steps
    assert len(_deletes(d)) == 1 and result.cleanup_status == "done"


def test_nothing_is_deleted_when_the_test_never_made_anything(tmp_path: Path, stage_env: Environment) -> None:
    d = _Pod(fail_create=True)
    result = run_test(_load(tmp_path), stage_env, d, run_id="R1")
    assert result.status is StepStatus.FAILED
    assert _deletes(d) == []  # ${location_id} was never saved: no address with a blank in it
    [c] = result.cleanup
    assert c.status is StepStatus.SKIPPED and "location_id" in (c.note or "")
    assert result.cleanup_status == "done"


def test_a_cleanup_that_fails_is_reported_but_the_test_still_passes(tmp_path: Path, stage_env: Environment) -> None:
    d = _Pod(refuse_delete=True)
    result = run_test(_load(tmp_path), stage_env, d, run_id="R1")
    assert result.status is StepStatus.PASSED
    assert result.cleanup[0].status is StepStatus.FAILED and "403" in (result.cleanup[0].error or "")
    assert result.cleanup_status == "failed"


def test_every_cleanup_step_is_tried_even_after_one_fails(tmp_path: Path, stage_env: Environment) -> None:
    text = SPEC + (
        "  - action: api_call\n"
        "    intent: Remove it again\n"
        "    value: DELETE /hcmRestApi/resources/11.13.18.05/locationsV2/${location_id}\n"
    )
    d = _Pod(refuse_delete=True)
    result = run_test(_load(tmp_path, text), stage_env, d, run_id="R1")
    assert len(_deletes(d)) == 2
    assert result.cleanup_status == "failed"


def test_needs_skips_a_step_when_that_value_was_not_saved(tmp_path: Path, stage_env: Environment) -> None:
    text = SPEC + (
        "  - action: navigate\n"
        "    intent: Go back to the list\n"
        "    value: Workforce Structures > Locations\n"
        "    options: {needs: location_id}\n"
    )
    d = _Pod(fail_create=True)
    result = run_test(_load(tmp_path, text), stage_env, d, run_id="R1")
    assert [c.status for c in result.cleanup] == [StepStatus.SKIPPED, StepStatus.SKIPPED]
    assert not [c for c in d.calls if c[0] == "navigate"]


def test_a_test_without_cleanup_is_unchanged(stage_env: Environment) -> None:
    from quartermaster.domain.models import Action, Step, TestCase

    test = TestCase(
        id="t",
        title="t",
        module="m",
        product="p",
        steps=[Step(action=Action.NAVIGATE, intent="go", value="A > B")],
    )
    result = run_test(test, stage_env, FakeDriver())
    assert result.cleanup == [] and result.cleanup_status == "none"


def test_cleanup_gets_its_own_pictures(tmp_path: Path, stage_env: Environment) -> None:
    d = _Pod()
    result = run_test(_load(tmp_path), stage_env, d, run_id="R1", screenshots=ScreenshotMode.EVERY_STEP)
    assert result.cleanup[0].evidence == ["evidence/cleanup-01.png"]


# ------------------------------------------------------------------ the spec is checked when it loads


def test_a_delete_to_a_fixed_address_is_refused(tmp_path: Path) -> None:
    text = SPEC.replace(
        "DELETE /hcmRestApi/resources/11.13.18.05/locationsV2/${location_id}",
        "DELETE /hcmRestApi/resources/11.13.18.05/locationsV2/300",
    )
    with pytest.raises(SpecError, match="fixed address"):
        _load(tmp_path, text)


def test_cleanup_may_not_use_an_undefined_name(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match="undefined data placeholder"):
        _load(
            tmp_path,
            SPEC.replace("DELETE /hcmRestApi/resources/11.13.18.05/locationsV2/${location_id}", "DELETE /x/${nope}"),
        )


def test_needs_must_name_something_a_step_saves(tmp_path: Path) -> None:
    text = SPEC + "  - action: navigate\n    intent: x\n    value: A > B\n    options: {needs: ghost}\n"
    with pytest.raises(SpecError, match="needs 'ghost'"):
        _load(tmp_path, text)


# ------------------------------------------------------------------ evidence


def test_run_record_and_document_show_the_cleanup(tmp_path: Path, stage_env: Environment) -> None:
    result = run_test(_load(tmp_path), stage_env, _Pod(refuse_delete=True), run_id="R1")
    record = build_record(result, run_dir=tmp_path, test_file=None, video_mode="off", videos=[])
    assert record["status"] == "passed"
    assert record["cleanup_status"] == "failed" and record["cleanup"][0]["intent"] == "Remove the location"
    out = write_evidence_document(record, tmp_path, tmp_path / "evidence.docx")
    xml = zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")
    assert "Cleanup of test data" in xml and "may still be on the pod" in xml


def test_a_run_without_cleanup_has_no_cleanup_in_its_record(tmp_path: Path, stage_env: Environment) -> None:
    text = SPEC.split("cleanup:")[0]
    result = run_test(_load(tmp_path, text), stage_env, _Pod(), run_id="R1")
    record = build_record(result, run_dir=tmp_path, test_file=None, video_mode="off", videos=[])
    assert "cleanup" not in record and "cleanup_status" not in record


# ------------------------------------------------------------------ Needs attention


def test_the_suite_entry_names_the_cleanup_steps_that_failed(tmp_path: Path, stage_env: Environment) -> None:
    from quartermaster.evidence.suite import _entry

    result = run_test(_load(tmp_path), stage_env, _Pod(refuse_delete=True), run_id="R1")
    record = build_record(result, run_dir=tmp_path, test_file=None, video_mode="off", videos=[])
    entry = _entry(record, tmp_path, None, tmp_path)
    assert entry["cleanup_status"] == "failed"
    assert entry["cleanup_failed"][0]["intent"] == "Remove the location" and entry["cleanup_failed"][0]["number"] == 1
    ok = run_test(_load(tmp_path), stage_env, _Pod(), run_id="R2")
    clean = _entry(
        build_record(ok, run_dir=tmp_path, test_file=None, video_mode="off", videos=[]), tmp_path, None, tmp_path
    )
    assert "cleanup_failed" not in clean


def test_a_cleanup_that_did_not_finish_is_in_needs_attention_even_when_the_test_passed(app: Any) -> None:  # noqa: F811
    import json

    add_suite_tests(app)
    run = finished_suite_run(app)
    path = Path(app.queue.store.get(run["id"])["suite_dir"]) / "suite.json"
    suite = json.loads(path.read_text(encoding="utf-8"))
    passed = next(r for r in suite["runs"] if r["status"] == "passed")
    passed["cleanup_status"] = "failed"
    passed["cleanup_failed"] = [
        {"number": 1, "intent": "Remove the location", "error": "The API answered 403 Forbidden."}
    ]
    path.write_text(json.dumps(suite), encoding="utf-8")

    todo = call(app, "GET", "/api/attention")
    (item,) = [i for i in todo["items"] if i["category"] == "cleanup"]
    assert item["test_id"] == passed["test_id"] and item["result"] == "passed" and item["cleanup_status"] == "failed"
    assert item["cleanup_steps"][0]["intent"] == "Remove the location"
    assert item["key"].startswith("cleanup:") and todo["categories"]["cleanup"] == "Cleanup that did not finish"
    assert todo["counts"]["cleanup"] == 1 and "cause" not in item or item.get("cause") is None

    # dismissing it does not hide a test failure, and the failure of the same run is a separate item
    call(app, "POST", "/api/attention/dismiss", {"keys": [item["key"]]})
    after = call(app, "GET", "/api/attention")
    assert "cleanup" not in after["counts"] and after["counts"]["assertion"] == 1
