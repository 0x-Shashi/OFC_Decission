"""suite.json and the regression run summary document. Standard library only."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from test_evidence_document import tiny_png

from quartermaster.evidence.suite import build_suite_record, suite_folder, write_suite_document, write_suite_record

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _run(root: Path, test_id: str, status: str, *, fail_at: int | None = None) -> tuple[dict[str, Any], Path, Path]:
    run_dir = root / test_id / "20260927-100000-AAAA"
    (run_dir / "screenshots").mkdir(parents=True)
    steps = []
    for i in range(3):
        st = "passed" if fail_at is None or i < fail_at else ("failed" if i == fail_at else "skipped")
        shot = []
        if st == "failed":
            (run_dir / "screenshots" / f"step-{i + 1:02d}.png").write_bytes(tiny_png())
            shot = [f"screenshots/step-{i + 1:02d}.png"]
        steps.append(
            {
                "index": i,
                "intent": f"Step {i + 1} of {test_id}",
                "status": st,
                "error": "StepFailure: expected text 'Redwood Shores', found 'Redwood City'"
                if st == "failed"
                else None,
                "evidence": shot,
            }
        )
    doc = run_dir / f"{test_id}_evidence.docx"
    doc.write_bytes(b"placeholder")
    record = {
        "run_id": "20260927-100000-AAAA",
        "test_id": test_id,
        "test_title": f"Title of {test_id}",
        "status": status,
        "environment": "fusion",
        "environment_url": "https://abcd-dev2.fa.us6.oraclecloud.com",
        "release": "26D",
        "executed_by": "Test Person",
        "machine": "laptop",
        "screenshots": "on-failure",
        "video": "off",
        "quartermaster_version": "0.1.0",
        "started_at": "2026-09-27T10:00:00+00:00",
        "finished_at": "2026-09-27T10:01:05+00:00",
        "steps": steps,
        "healing": [{"step_index": 0, "old": ["label", "Name"], "new": ["role", "textbox:Name"]}]
        if status == "healed"
        else [],
    }
    return record, run_dir, doc


def _suite(root: Path) -> dict[str, Any]:
    runs = [
        _run(root, "hcm.view-worker", "passed"),
        _run(root, "hcm.create-location", "failed", fail_at=1),
        _run(root, "hcm.personal-info", "healed"),
    ]
    return build_suite_record(
        runs,
        suite_id="S1",
        evidence_root=root,
        target="my_tests",
        started_at="2026-09-27T10:00:00+00:00",
        finished_at="2026-09-27T10:04:00+00:00",
    )


def test_suite_record_summarises_each_test_with_relative_paths(tmp_path: Path) -> None:
    suite = _suite(tmp_path)
    assert suite["status"] == "failed" and suite["release"] == "26D" and suite["executed_by"] == "Test Person"
    ok, failed, healed = suite["runs"]
    assert ok["run_dir"] == "hcm.view-worker/20260927-100000-AAAA"
    assert ok["document"] == "hcm.view-worker/20260927-100000-AAAA/hcm.view-worker_evidence.docx"
    assert (ok["steps_passed"], ok["steps_total"], ok["duration"]) == (3, 3, "1 min 5 s")
    assert failed["failed_step"] == {
        "number": 2,
        "intent": "Step 2 of hcm.create-location",
        "error": "StepFailure: expected text 'Redwood Shores', found 'Redwood City'",
        "screenshot": "hcm.create-location/20260927-100000-AAAA/screenshots/step-02.png",
    }
    assert healed["healing"] and healed["status"] == "healed"

    folder = suite_folder(tmp_path, "S1")
    assert json.loads(write_suite_record(suite, folder).read_text())["suite_id"] == "S1"
    assert folder == tmp_path / "_suites" / "S1"


def test_summary_document_lists_results_failures_and_evidence(tmp_path: Path) -> None:
    suite = _suite(tmp_path)
    out = write_suite_document(suite, tmp_path, tmp_path / "_suites" / "S1" / "summary.docx")
    z = zipfile.ZipFile(out)
    for name in z.namelist():
        if name.endswith((".xml", ".rels")):
            ET.fromstring(z.read(name))
    text = " ".join(t.text or "" for t in ET.fromstring(z.read("word/document.xml")).iter(f"{W}t"))
    for fragment in (
        "Test Run Summary",
        "SOME TESTS FAILED",
        "This is the summary of an automated test run in Oracle Fusion.",
        "3: 2 passed, 1 failed",
        "Title of hcm.view-worker",
        "3 of 3",
        "What failed",
        "Step 2: Step 2 of hcm.create-location",
        'The screen showed "Redwood City" but it should show "Redwood Shores".',
        "Tests that need an update",
        "hcm.create-location/20260927-100000-AAAA/hcm.create-location_evidence.docx",
        "Reviewed by",
    ):
        assert fragment in text, fragment
    # only the failure screenshot is embedded; each test's own document holds the rest
    assert [n for n in z.namelist() if n.startswith("word/media/")] == ["word/media/image1.png"]


def test_all_passed_suite(tmp_path: Path) -> None:
    runs = [_run(tmp_path, "a.one", "passed"), _run(tmp_path, "a.two", "passed")]
    suite = build_suite_record(
        runs,
        suite_id="S2",
        evidence_root=tmp_path,
        target="t",
        started_at="2026-09-27T10:00:00+00:00",
        finished_at="2026-09-27T10:00:30+00:00",
    )
    assert suite["status"] == "passed"
    out = write_suite_document(suite, tmp_path, tmp_path / "s.docx")
    text = " ".join(t.text or "" for t in ET.fromstring(zipfile.ZipFile(out).read("word/document.xml")).iter(f"{W}t"))
    assert "ALL PASSED" in text and "What failed" not in text and "need an update" not in text
