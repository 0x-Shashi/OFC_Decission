"""The Word evidence document. Uses only the standard library, like the module under test."""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from typing import Any

from quartermaster.evidence.document import write_evidence_document

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def tiny_png(width: int = 16, height: int = 10) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def sample_run(run_dir: Path) -> dict[str, Any]:
    shots = run_dir / "screenshots"
    shots.mkdir(parents=True)
    (shots / "step-01.png").write_bytes(tiny_png())
    (shots / "step-02.png").write_bytes(tiny_png(1600, 1000))
    step = {"duration_ms": 1200.0, "locator": "role=button:Add Location", "started_at": "2026-09-26T19:48:52+00:00"}
    return {
        "schema": 1,
        "run_id": "20260926-194852-5A6C",
        "test_id": "hcm.create-location",
        "test_title": "Fill in a new location up to Submit",
        "status": "failed",
        "environment": "fusion",
        "environment_url": "https://abcd-dev2.fa.us6.oraclecloud.com",
        "release": "26C",
        "persona": "HR Specialist",
        "executed_by": "Test Person",
        "machine": "laptop",
        "started_at": "2026-09-26T19:48:52+00:00",
        "finished_at": "2026-09-26T19:50:40+00:00",
        "screenshots": "every-step",
        "video": "always",
        "videos": ["videos/abc.webm"],
        "test_file": "examples/tests/hcm/create_location.yaml",
        "test_file_sha256": "f" * 64,
        "quartermaster_version": "0.1.0",
        "steps": [
            {
                **step,
                "index": 0,
                "intent": "Click Add Location",
                "action": "click",
                "value": None,
                "expected": "New location screen opens (TS002 step 2).",
                "status": "passed",
                "error": None,
                "evidence": ["screenshots/step-01.png"],
            },
            {
                **step,
                "index": 1,
                "intent": "City is filled in",
                "action": "assert_text",
                "value": "Redwood City",
                "expected": "",
                "status": "failed",
                "error": "StepFailure: expected text 'Redwood City', found ''",
                "evidence": ["screenshots/step-02.png"],
            },
            {
                "index": 2,
                "intent": "Cancel without submitting",
                "action": "click",
                "value": None,
                "expected": "",
                "status": "skipped",
                "error": None,
                "evidence": [],
                "locator": None,
                "started_at": None,
                "duration_ms": 0,
            },
        ],
        "healing": [],
        "evidence_sha256": {"screenshots/step-01.png": "a" * 64, "screenshots/step-02.png": "b" * 64},
    }


def _open(path: Path) -> tuple[zipfile.ZipFile, str]:
    z = zipfile.ZipFile(path)
    for name in z.namelist():  # every part must be well-formed XML
        if name.endswith((".xml", ".rels")):
            ET.fromstring(z.read(name))
    root = ET.fromstring(z.read("word/document.xml"))
    text = " ".join(t.text or "" for t in root.iter(f"{W}t"))
    return z, text


def test_document_holds_steps_expected_results_and_screenshots(tmp_path: Path) -> None:
    run = sample_run(tmp_path)
    out = write_evidence_document(run, tmp_path, tmp_path / "evidence.docx")
    z, text = _open(out)

    media = [n for n in z.namelist() if n.startswith("word/media/")]
    assert len(media) == 2  # one per screenshot
    for fragment in (
        "Test Evidence",
        "FAILED",
        "This document is the record of an automated test run in Oracle Fusion.",
        "1 of 3 passed (1 failed, 1 not done)",
        "Step 1: Click Add Location",
        "New location screen opens (TS002 step 2).",  # expected result from the spec
        'It shows "Redwood City".',  # default expected result when the spec has none
        'The screen was empty where it should show "Redwood City".',  # plain actual result
        "Not done, because an earlier step failed.",
        "Screen after step 1",
        "Reviewed by",
        "Saved in the videos folder next to this document",
    ):
        assert fragment in text, fragment


def test_videos_are_listed_but_never_embedded(tmp_path: Path) -> None:
    run = sample_run(tmp_path)
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos" / "abc.webm").write_bytes(b"not really a video")
    out = write_evidence_document(run, tmp_path, tmp_path / "evidence.docx")
    z, _ = _open(out)
    assert not any(n.endswith(".webm") for n in z.namelist())
    assert "webm" not in z.read("[Content_Types].xml").decode()


def test_every_relationship_points_at_a_part(tmp_path: Path) -> None:
    out = write_evidence_document(sample_run(tmp_path), tmp_path, tmp_path / "evidence.docx")
    z = zipfile.ZipFile(out)
    names = set(z.namelist())
    rels = ET.fromstring(z.read("word/_rels/document.xml.rels"))
    assert all("word/" + r.get("Target", "") in names for r in rels)
    doc = z.read("word/document.xml").decode()
    ids = {r.get("Id") for r in rels}
    for used in ("rIdFooter", "rIdImg1", "rIdImg2"):
        assert used in ids and used in doc


def test_tall_screenshot_is_scaled_to_fit_the_page(tmp_path: Path) -> None:
    run = sample_run(tmp_path)
    (tmp_path / "screenshots" / "step-01.png").write_bytes(tiny_png(100, 1000))  # 1:10 portrait
    out = write_evidence_document(run, tmp_path, tmp_path / "evidence.docx")
    doc = zipfile.ZipFile(out).read("word/document.xml").decode()
    first = doc.index("<wp:extent")
    cy = int(doc[doc.index('cy="', first) + 4 : doc.index('"', doc.index('cy="', first) + 4)])
    assert cy <= 8 * 914400  # at most 8 inches tall


def test_missing_screenshot_is_reported_not_fatal(tmp_path: Path) -> None:
    run = sample_run(tmp_path)
    (tmp_path / "screenshots" / "step-02.png").unlink()
    _, text = _open(write_evidence_document(run, tmp_path, tmp_path / "evidence.docx"))
    assert "Screenshot missing: screenshots/step-02.png" in text


def test_step_pages_use_plain_words(tmp_path: Path) -> None:
    out = write_evidence_document(sample_run(tmp_path), tmp_path, tmp_path / "evidence.docx")
    root = ET.fromstring(zipfile.ZipFile(out).read("word/document.xml"))
    body = list(root.find(f"{W}body") or [])
    before = " ".join(t.text or "" for el in body for t in el.iter(f"{W}t"))
    # no locators, fingerprints, exception names or technical sections anywhere in the document
    for jargon in ("role=", "SHA-256", "StepFailure", "xpath", "Element used", "Technical details", "a" * 64):
        assert jargon not in before, jargon


def test_steps_without_pictures_do_not_get_their_own_page(tmp_path: Path) -> None:
    run = sample_run(tmp_path)
    for step in run["steps"]:
        step["evidence"] = []
    run["screenshots"] = "on-failure"
    out = write_evidence_document(run, tmp_path, tmp_path / "evidence.docx")
    doc = zipfile.ZipFile(out).read("word/document.xml").decode()
    assert doc.count('w:type="page"') == 1  # only the break before sign-off


def test_a_missing_screenshot_is_explained(tmp_path: Path) -> None:
    run = sample_run(tmp_path)
    run["steps"][0]["evidence"] = []
    run["steps"][0]["screenshot_note"] = "The screen could not be captured: the page was still loading."
    _, text = _open(write_evidence_document(run, tmp_path, tmp_path / "evidence.docx"))
    assert "Screen picture" in text and "the page was still loading" in text
    assert "Screen after step 1" not in text


def test_a_click_shows_the_screen_before_it_and_after_it(tmp_path: Path) -> None:
    run = sample_run(tmp_path)
    (tmp_path / "screenshots" / "step-01-before.png").write_bytes(tiny_png())
    run["steps"][0]["evidence"] = ["screenshots/step-01-before.png", "screenshots/step-01.png"]
    _, text = _open(write_evidence_document(run, tmp_path, tmp_path / "evidence.docx"))
    assert text.index("Before step 1: the item about to be clicked is boxed in red") < text.index("Screen after step 1")


def test_a_test_made_from_a_manual_scenario_follows_its_written_steps(tmp_path: Path) -> None:
    shots = tmp_path / "screenshots"
    shots.mkdir()
    for name in ("step-01.png", "step-02-before.png", "step-02.png", "step-03.png"):
        (shots / name).write_bytes(tiny_png())

    def action(i: int, intent: str, action: str, written: int, evidence: list[str]) -> dict[str, Any]:
        return {
            "index": i,
            "intent": intent,
            "action": action,
            "status": "passed",
            "written_step": written,
            "evidence": [f"screenshots/{e}" for e in evidence],
            "started_at": "2026-10-02T09:56:30+05:30",
        }

    run = sample_run(tmp_path / "other")
    run.update(
        status="passed",
        test_title="My Compensation",
        written_steps=[
            {"action": "Login", "expected": ""},
            {"action": "Me", "expected": ""},
            {"action": "Personal Information", "expected": ""},
            {"action": "Select My Compensation", "expected": "Current Salary is shown"},
        ],
        steps=[
            action(0, "Open Me > Personal Information", "navigate", 3, ["step-01.png"]),
            action(1, "Click My Compensation", "click", 4, ["step-02-before.png", "step-02.png"]),
            action(2, "Check Current Salary is shown", "assert_visible", 4, ["step-03.png"]),
        ],
    )
    _, text = _open(write_evidence_document(run, tmp_path, tmp_path / "evidence.docx"))
    assert "4 of 4 passed" in text  # counted by the written steps, not the 3 actions
    assert text.index("Step 1: Login") < text.index("Step 2: Me") < text.index("Step 4: Select My Compensation")
    assert "signed in to Oracle Fusion before the test started" in text
    assert "done as part of step 3 (Open Me > Personal Information)" in text
    assert "Click My Compensation; Check Current Salary is shown" in text
    assert "Step 4, before “Click My Compensation”: the item about to be clicked is boxed in red" in text
