"""Setup steps: service calls that check or make what a test needs on the pod, before its steps."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeDriver
from test_service_api import app  # noqa: F401, F811  (app is a fixture)

from quartermaster.domain.models import Environment, EnvironmentKind, StepStatus
from quartermaster.dsl.loader import SpecError, load_test
from quartermaster.evidence.document import write_evidence_document
from quartermaster.evidence.run_record import build_record
from quartermaster.evidence.suite import _entry
from quartermaster.runner.engine import run_test
from quartermaster.service.insights import classify

SPEC = """
id: ap.invoice
title: Create an invoice
module: Financials
product: Payables
generate:
  ref: {unique: 6, prefix: "INV-"}
setup:
  - action: api_call
    intent: The accounting period is open
    value: GET /fscmRestApi/resources/11.13.18.05/periods?q=Status=Open
    options:
      check: {count: "1"}
  - action: api_call
    intent: Make a supplier for this run
    value: POST /fscmRestApi/resources/11.13.18.05/suppliers
    options:
      body: {Supplier: "QM-${ref}"}
      save: {supplier_id: SupplierId}
steps:
  - action: api_call
    intent: Read the supplier
    value: GET /fscmRestApi/resources/11.13.18.05/suppliers/${supplier_id}
cleanup:
  - action: api_call
    intent: Remove the supplier
    value: DELETE /fscmRestApi/resources/11.13.18.05/suppliers/${supplier_id}
    options: {needs: supplier_id}
"""


def load(tmp_path: Path, text: str = SPEC) -> Any:
    f = tmp_path / "t.yaml"
    f.write_text(text, encoding="utf-8")
    return load_test(f)


class Pod(FakeDriver):
    """A pod with an open period (unless `period_open` is False) that makes a supplier on POST."""

    def __init__(self, *, period_open: bool = True, **kw: Any):
        super().__init__(**kw)
        self.period_open = period_open
        self.attempts: dict[str, int] = {}

    def api_call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        self.calls.append(("api_call", method, path))
        self.attempts[method] = self.attempts.get(method, 0) + 1
        if method == "POST":
            return 201, {"SupplierId": 55}
        if method == "DELETE":
            return 204, None
        if "periods" in path:
            return (200, {"count": 1}) if self.period_open else (200, {"count": 0})
        return 200, {"Supplier": "x"}


def pod(kind: EnvironmentKind = EnvironmentKind.DEV) -> Environment:
    return Environment(name="DEV2", url="https://abcd-dev2.fa.us2.oraclecloud.com", kind=kind)


def calls(d: FakeDriver) -> list[str]:
    return [f"{c[1]} {str(c[2]).split('/')[-1].split('?')[0]}" for c in d.calls if c[0] == "api_call"]


# ------------------------------------------------------------------ in a run


def test_setup_runs_before_the_steps_and_what_it_saves_is_used_by_them_and_the_cleanup(tmp_path: Path) -> None:
    d = Pod()
    result = run_test(load(tmp_path), pod(), d, run_id="R1")
    assert result.status is StepStatus.PASSED
    assert calls(d) == ["GET periods", "POST suppliers", "GET 55", "DELETE 55"]
    assert [s.status for s in result.setup] == [StepStatus.PASSED, StepStatus.PASSED]
    assert result.setup_status == "done" and result.cleanup_status == "done"
    assert [s.intent for s in result.setup] == ["The accounting period is open", "Make a supplier for this run"]
    assert [s.intent for s in result.steps] == ["Read the supplier"]  # setup is not one of the steps


def test_a_value_made_fresh_for_the_run_can_be_used_in_a_setup_call(tmp_path: Path) -> None:
    d = Pod()
    result = run_test(load(tmp_path), pod(), d, run_id="AAAA1111")
    assert result.setup[1].value == "POST /fscmRestApi/resources/11.13.18.05/suppliers"
    posts = [c for c in d.calls if c[1] == "POST"]
    assert len(posts) == 1


def test_a_setup_that_is_not_met_stops_the_test_before_any_step(tmp_path: Path) -> None:
    d = Pod(period_open=False)
    result = run_test(load(tmp_path), pod(), d, run_id="R1")
    assert result.status is StepStatus.FAILED
    assert [s.status for s in result.steps] == [StepStatus.FAILED]
    message = result.steps[0].error or ""
    assert message.startswith("Setup not met: The accounting period is open.")
    assert classify(message) == "test_data"  # data the pod lacks, not a release that broke the test
    assert [s.status for s in result.setup] == [StepStatus.FAILED, StepStatus.SKIPPED]
    assert result.setup[1].note == "An earlier setup step did not pass."
    assert result.setup_status == "failed"
    assert calls(d) == ["GET periods"]  # no supplier was made, no step ran, nothing to clean up
    assert result.cleanup[0].status is StepStatus.SKIPPED and result.cleanup_status == "done"


def test_a_failed_setup_still_runs_the_cleanup_of_what_it_made(tmp_path: Path) -> None:
    spec = SPEC.replace("count: ", "count: ").replace(
        "intent: Make a supplier for this run",
        "intent: Make a supplier for this run\n    options: {}",
    )
    # the second setup call makes the supplier; a third call fails after it: the supplier must still be removed
    third = (
        "  - action: api_call\n    intent: The supplier is active\n"
        "    value: GET /fscmRestApi/resources/11.13.18.05/suppliers/${supplier_id}?q=Status=Active\n"
        "    options:\n      check: {Status: ACTIVE}\n"
    )
    spec = SPEC.replace(
        "steps:\n  - action: api_call\n    intent: Read the supplier",
        third + "steps:\n  - action: api_call\n    intent: Read the supplier",
        1,
    )
    d = Pod()
    result = run_test(load(tmp_path, spec), pod(), d, run_id="R1")
    assert result.setup_status == "failed" and result.steps[0].status is StepStatus.FAILED
    assert calls(d) == ["GET periods", "POST suppliers", "GET 55", "DELETE 55"]
    assert result.cleanup[0].status is StepStatus.PASSED


def test_a_setup_call_that_only_reads_is_tried_again_but_one_that_writes_is_not(tmp_path: Path) -> None:
    d = Pod(period_open=False)
    run_test(load(tmp_path), pod(), d, run_id="R1", retries=1, retry_wait_s=0)
    assert d.attempts["GET"] == 2  # a read is safe to repeat
    writes = SPEC.replace(
        "GET /fscmRestApi/resources/11.13.18.05/periods?q=Status=Open",
        "POST /fscmRestApi/resources/11.13.18.05/periods",
    )
    d2 = Pod(period_open=False)
    run_test(load(tmp_path, writes), pod(), d2, run_id="R1", retries=2, retry_wait_s=0)
    assert d2.attempts["POST"] == 1  # a second POST could make a second record


def test_setup_is_reported_to_the_live_view_in_order(tmp_path: Path) -> None:
    seen: list[str] = []
    run_test(load(tmp_path), pod(), Pod(), run_id="R1", on_event=lambda e: seen.append(e["type"]))
    kinds = [k for k in seen if k != "step_retry"]
    assert kinds[:2] == ["run_start", "setup_start"]
    assert kinds.index("setup_end") < kinds.index("step_start") < kinds.index("cleanup_start")


def test_a_pod_missing_a_data_value_stops_before_setup_uses_it(tmp_path: Path) -> None:
    spec = SPEC.replace("steps:\n", "pods: {STAGE: {gap: '1'}}\nsteps:\n", 1).replace(
        "GET /fscmRestApi/resources/11.13.18.05/suppliers/${supplier_id}\n", "GET /x/${supplier_id}?g=${gap}\n", 1
    )
    d = Pod()
    result = run_test(load(tmp_path, spec), pod(), d, run_id="R1")
    assert (result.steps[0].error or "").startswith("No test data for gap")
    assert calls(d) == [] and result.setup == []  # nothing was sent, not even the setup


def test_a_test_without_setup_is_unchanged(tmp_path: Path) -> None:
    spec = SPEC.split("setup:")[0] + "steps:\n  - action: api_call\n    intent: Look\n    value: GET /x\n"
    result = run_test(load(tmp_path, spec), pod(), Pod(), run_id="R1")
    assert result.setup == [] and result.setup_status == "none" and result.status is StepStatus.PASSED


# ------------------------------------------------------------------ the spec is checked when it loads


def test_only_service_calls_can_be_setup(tmp_path: Path) -> None:
    bad = SPEC.replace(
        "setup:\n",
        "setup:\n  - action: navigate\n    intent: Open a page\n    value: Home\n",
        1,
    )
    with pytest.raises(SpecError, match="only api_call steps can be used in setup"):
        load(tmp_path, bad)


def test_setup_may_not_use_an_undefined_name_or_delete_a_fixed_address(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match=r"setup step 1 uses undefined data placeholder \$\{nope\}"):
        load(tmp_path, SPEC.replace("periods?q=Status=Open", "periods?q=${nope}"))
    delete = SPEC.replace(
        "setup:\n",
        "setup:\n  - action: api_call\n    intent: Remove an old one\n    value: DELETE /fscmRestApi/suppliers/9\n",
        1,
    )
    with pytest.raises(SpecError, match="setup step 1 deletes a fixed address"):
        load(tmp_path, delete)
    leftover = (
        "  - action: api_call\n    intent: Remove last run's leftover\n"
        "    value: DELETE /fscmRestApi/suppliers/${old_id}\n"
    )
    ok = SPEC.replace(
        "  - action: api_call\n    intent: Make a supplier",
        leftover + "  - action: api_call\n    intent: Make a supplier",
        1,
    )
    ok = ok.replace("generate:", "data: {old_id: '7'}\ngenerate:", 1)
    assert len(load(tmp_path, ok).setup) == 3  # a delete by a value is allowed


def test_what_a_setup_step_saves_is_known_to_the_steps_and_later_setup_steps(tmp_path: Path) -> None:
    assert load(tmp_path).setup[1].options["save"] == {"supplier_id": "SupplierId"}
    early = SPEC.replace("periods?q=Status=Open", "suppliers/${supplier_id}")  # used before it is saved
    with pytest.raises(SpecError, match=r"setup step 1 uses undefined data placeholder \$\{supplier_id\}"):
        load(tmp_path, early)


# ------------------------------------------------------------------ evidence and Needs attention


def test_the_run_record_and_the_document_show_the_setup(tmp_path: Path) -> None:
    result = run_test(load(tmp_path), pod(), Pod(), run_id="R1")
    record = build_record(result, run_dir=tmp_path, test_file=None, video_mode="off", videos=[])
    assert record["setup_status"] == "done" and record["setup"][0]["intent"] == "The accounting period is open"
    xml = (
        zipfile.ZipFile(write_evidence_document(record, tmp_path, tmp_path / "e.docx"))
        .read("word/document.xml")
        .decode()
    )
    assert "Setup before the test" in xml and "checked or made on the pod before the steps began" in xml
    plain = run_test(
        load(tmp_path, SPEC.split("setup:")[0] + "steps:\n  - action: api_call\n    intent: L\n    value: GET /x\n"),
        pod(),
        Pod(),
    )
    none = build_record(plain, run_dir=tmp_path, test_file=None, video_mode="off", videos=[])
    assert "setup" not in none and "setup_status" not in none


def test_a_setup_that_is_not_met_is_told_in_the_document_and_is_test_data_in_needs_attention(tmp_path: Path) -> None:
    result = run_test(load(tmp_path), pod(), Pod(period_open=False), run_id="R1")
    record = build_record(result, run_dir=tmp_path, test_file=None, video_mode="off", videos=[])
    assert record["status"] == "failed" and record["setup_status"] == "failed"
    xml = (
        zipfile.ZipFile(write_evidence_document(record, tmp_path, tmp_path / "e.docx"))
        .read("word/document.xml")
        .decode()
    )
    assert "the test steps were not run" in xml and "The pod" in xml
    entry = _entry(record, tmp_path, None, tmp_path)
    assert entry["failed_step"]["error"].startswith("Setup not met")
    assert classify(entry["failed_step"]["error"]) == "test_data"


# ------------------------------------------------------------------ in an exported test


def test_an_exported_test_runs_its_setup_first_and_still_cleans_up(tmp_path: Path) -> None:
    from quartermaster.export.playwright_py import render_test

    text = render_test(load(tmp_path))
    compile(text, "t.py", "exec")
    assert text.index("fusion.setup(1,") < text.index("fusion.setup(2,") < text.index("fusion.step(1,")
    assert text.index("try:") < text.index("fusion.setup(1,") < text.index("finally:")  # the cleanup covers the setup
    assert "with fusion.setup(1, 'The accounting period is open'):" in text


def test_the_exported_runtime_says_the_setup_was_not_met() -> None:
    from test_export import RT

    f = RT.Fusion(None, "https://abcd-dev2.fa.us6.oraclecloud.com")
    with f.setup(1, "The period is open"):
        pass  # a setup that passes says nothing
    with (
        pytest.raises(RT.StepFailure, match=r"Setup not met: The period is open \(setup step 1\)\. KeyError"),
        f.setup(1, "The period is open"),
    ):
        raise KeyError("count")


# ------------------------------------------------------------------ the pages' data


def test_the_test_page_lists_the_setup_and_the_run_page_gets_its_result(app: Any, tmp_path: Path) -> None:  # noqa: F811
    import json

    from quartermaster.service.api import App

    (app.tests_root / "t.yaml").write_text(SPEC, encoding="utf-8")
    detail = json.loads(app.handle("GET", "/api/test?file=t.yaml", b"").body)
    assert [c["intent"] for c in detail["setup"]] == ["The accounting period is open", "Make a supplier for this run"]
    assert detail["setup"][0]["value"].startswith("GET /fscmRestApi")
    failed = run_test(load(tmp_path), pod(), Pod(period_open=False), run_id="R1")
    record = build_record(failed, run_dir=tmp_path, test_file=None, video_mode="off", videos=[])
    view = App._setup_view(record)
    assert view is not None and view["status"] == "failed"
    assert [s["status"] for s in view["steps"]] == ["failed", "skipped"]
    assert view["steps"][1]["note"] == "An earlier setup step did not pass."
    done = run_test(load(tmp_path), pod(), Pod(), run_id="R2")
    ok = App._setup_view(build_record(done, run_dir=tmp_path, test_file=None, video_mode="off", videos=[]))
    assert ok is not None and ok["status"] == "done"
    assert App._setup_view({}) is None
