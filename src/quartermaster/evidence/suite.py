"""One summary for a whole `qm run`: suite.json and, on request, a summary Word document.

    evidence/_suites/<suite id>/
        suite.json                        every test's result and where its evidence is
        suite_<suite id>_summary.docx     the summary document (with --evidence-doc)

Each test keeps its own run folder and evidence document; the summary points to them by path
relative to the evidence root, so the whole evidence folder can be moved or zipped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quartermaster.evidence.document import (
    _STATUS_COLOR,
    _STATUS_FILL,
    _TEXT_W,
    _cell,
    _Doc,
    _duration,
    _kv_table,
    _p,
    _page_break,
    _r,
    _status_label,
    _table,
    _when,
    document_xml,
    plain_error,
    write_package,
)

SCHEMA_VERSION = 1
SUITES_FOLDER = "_suites"


def suite_folder(evidence_root: Path, suite_id: str) -> Path:
    return evidence_root / SUITES_FOLDER / suite_id


def build_suite_record(
    runs: list[tuple[dict[str, Any], Path, Path | None]],
    *,
    suite_id: str,
    evidence_root: Path,
    target: str,
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    """`runs` holds (run record, run folder, evidence document or None) for each test, in run order."""
    entries = [_entry(record, run_dir, doc, evidence_root) for record, run_dir, doc in runs]
    first = runs[0][0] if runs else {}
    return {
        "schema": SCHEMA_VERSION,
        "suite_id": suite_id,
        "status": _overall([e["status"] for e in entries]),
        "target": target,
        "environment": first.get("environment", ""),
        "environment_url": first.get("environment_url", ""),
        "release": first.get("release"),
        "executed_by": first.get("executed_by", ""),
        "machine": first.get("machine", ""),
        "screenshots": first.get("screenshots", ""),
        "video": first.get("video", ""),
        "quartermaster_version": first.get("quartermaster_version", ""),
        "started_at": started_at,
        "finished_at": finished_at,
        "runs": entries,
    }


def write_suite_record(suite: dict[str, Any], suite_dir: Path) -> Path:
    suite_dir.mkdir(parents=True, exist_ok=True)
    out = suite_dir / "suite.json"
    out.write_text(json.dumps(suite, indent=2), encoding="utf-8")
    return out


def write_suite_document(suite: dict[str, Any], evidence_root: Path, out: Path) -> Path:
    doc = _Doc(evidence_root)  # pictures are referenced relative to the evidence root
    runs: list[dict[str, Any]] = suite.get("runs", [])
    status = suite.get("status", "")
    counts = {k: sum(1 for r in runs if r["status"] == k) for k in ("passed", "healed", "failed")}
    parts: list[str] = []
    add = parts.append

    # --- page 1: what this is, the result, one line per test
    add(_p([_r("Test Run Summary")], style="Title"))
    add(_p([_r(f"{len(runs)} test(s), {_when(suite.get('started_at'))}")], style="Subtitle"))
    add(
        _p(
            [
                _r("Result: ", bold=True, size=28),
                _r(_overall_label(status), bold=True, size=28, color=_STATUS_COLOR.get(status, "15202A")),
            ],
            after=160,
        )
    )
    add(
        _p(
            [
                _r(
                    "This is the summary of an automated test run in Oracle Fusion. "
                    "It lists every test and its result. Each test also has its own evidence document with every "
                    "step and pictures of the screen; the list at the end of this summary says where to find them."
                )
            ],
            after=200,
        )
    )

    add(_p([_r("About this run")], style="Heading1"))
    tally = f"{len(runs)}: {counts['passed'] + counts['healed']} passed, {counts['failed']} failed"
    add(
        _kv_table(
            [
                ("Tests run", tally),
                ("Result", _overall_label(status).capitalize()),
                ("Date and time", _when(suite.get("started_at"))),
                ("Time taken", _duration(suite.get("started_at"), suite.get("finished_at"))),
                ("Run by", suite.get("executed_by") or "Not recorded"),
                ("Oracle environment", suite.get("environment_url", "")),
                ("Oracle release", suite.get("release") or "Not recorded"),
                ("Tests taken from", suite.get("target", "")),
            ],
            status_row=("Result", status),
        )
    )

    add(_p([_r("Results")], style="Heading1"))
    widths = [600, 5880, 2000, 1600]
    rows = [[_cell(h, w, header=True) for h, w in zip(("#", "Test", "Result", "Steps passed"), widths, strict=True)]]
    for n, r in enumerate(runs, 1):
        st = r["status"]
        rows.append(
            [
                _cell(str(n), widths[0]),
                _cell(r.get("test_title") or r["test_id"], widths[1]),
                _cell(_status_label(st), widths[2], fill=_STATUS_FILL.get(st), color=_STATUS_COLOR.get(st), bold=True),
                _cell(f"{r['steps_passed']} of {r['steps_total']}", widths[3]),
            ]
        )
    add(_table(rows, widths))

    # --- what went wrong, in plain words, with the picture of the moment it failed
    failed = [r for r in runs if r["status"] == "failed"]
    if failed:
        add(_page_break())
        add(_p([_r("What failed")], style="Heading1"))
    for n, r in enumerate(failed):
        f = r.get("failed_step") or {}
        if f.get("screenshot") and n:
            add(_page_break())
        add(_p([_r(r.get("test_title") or r["test_id"])], style="Heading2"))
        add(
            _kv_table(
                [
                    ("Result", _status_label("failed")),
                    ("Failed at", f"Step {f.get('number', '?')}: {f.get('intent', '')}"),
                    ("What happened", plain_error(f.get("error"))),
                    ("Steps not done", str(max(r["steps_total"] - r["steps_passed"] - 1, 0))),
                ],
                status_row=("Result", "failed"),
            )
        )
        if f.get("screenshot"):
            add(
                doc._image(
                    f["screenshot"], int(f.get("number", 0)), "", caption=f"Screen when step {f.get('number')} failed"
                )
            )

    needs_update = [r for r in runs if any(h.get("source", "fallback") == "fallback" for h in r.get("healing") or [])]
    if needs_update:
        add(_p([_r("Tests that need an update")], style="Heading1"))
        add(
            _p(
                [
                    _r(
                        "These tests passed, but an item on the screen was found in a different way than when the "
                        "test was written. The test team should update these tests so future runs stay reliable:"
                    )
                ]
            )
        )
        for r in needs_update:
            add(_p([_r(r.get("test_title") or r["test_id"])], indent=360))

    unclean = [r for r in runs if r.get("cleanup_status") in ("partial", "failed")]
    if unclean:
        add(_p([_r("Test data that may still be on the pod")], style="Heading1"))
        add(
            _p(
                [
                    _r(
                        "The cleanup of these tests did not finish, so records they made may still be in Oracle "
                        "Fusion. The test's evidence document says which cleanup step failed:"
                    )
                ]
            )
        )
        for r in unclean:
            add(_p([_r(r.get("test_title") or r["test_id"])], indent=360))

    # --- sign-off, where the evidence is, technical details
    add(_page_break())
    add(_p([_r("Sign-off")], style="Heading1"))
    add(
        _p(
            [
                _r(
                    "By signing, the reviewer confirms this summary and the evidence documents it lists are a true "
                    "record of the test run above."
                )
            ]
        )
    )
    sw = [2200, 3080, 2800, 2000]
    sign = [[_cell(h, w, header=True) for h, w in zip(("Role", "Name", "Signature", "Date"), sw, strict=True)]]
    for role, name in (("Run by", suite.get("executed_by", "")), ("Reviewed by", ""), ("Approved by", "")):
        sign.append([_cell(role, sw[0], bold=True), _cell(name, sw[1]), _cell("", sw[2]), _cell("", sw[3])])
    add(_table(sign, sw, row_height=620))

    add(_p([_r("Where to find each test's evidence")], style="Heading1"))
    add(_p([_r("Inside the evidence folder:")]))
    ew = [3400, _TEXT_W - 3400]
    erows = [[_cell("Test", ew[0], header=True), _cell("Evidence document (or run folder)", ew[1], header=True)]]
    for r in runs:
        where = r.get("document") or r["run_dir"]
        erows.append([_cell(r.get("test_title") or r["test_id"], ew[0]), _cell(where, ew[1])])
    add(_table(erows, ew))

    return write_package(
        out,
        doc,
        document_xml(parts),
        title=f"Test run summary ({suite.get('suite_id', '')})",
        creator=str(suite.get("executed_by", "")),
        subject=str(suite.get("target", "")),
        created=suite.get("finished_at") or suite.get("started_at"),
        footer_left=f"Test run summary {suite.get('suite_id', '')}",
    )


def _entry(record: dict[str, Any], run_dir: Path, doc: Path | None, evidence_root: Path) -> dict[str, Any]:
    steps: list[dict[str, Any]] = record.get("steps", [])
    failed = next((s for s in steps if s.get("status") == "failed"), None)
    entry: dict[str, Any] = {
        "test_id": record.get("test_id", ""),
        "test_title": record.get("test_title", ""),
        "status": record.get("status", ""),
        "run_id": record.get("run_id", ""),
        "run_dir": _relative(run_dir, evidence_root),
        "document": _relative(doc, evidence_root) if doc else None,
        "steps_total": len(steps),
        "steps_passed": sum(1 for s in steps if s.get("status") in ("passed", "healed")),
        "duration": _duration(record.get("started_at"), record.get("finished_at")),
        "failed_step": None,
        "healing": record.get("healing", []),
        "mode": record.get("mode", "automatic"),  # "manual": done by hand by a tester
    }
    if record.get("cleanup_status"):
        entry["cleanup_status"] = record["cleanup_status"]
        bad = [c for c in record.get("cleanup", []) if c.get("status") == "failed"]
        if bad:  # which cleanup steps did not work, for Needs attention
            entry["cleanup_failed"] = [
                {"number": c.get("index", 0) + 1, "intent": c.get("intent", ""), "error": c.get("error")} for c in bad
            ]
    if record.get("flaky"):
        entry["flaky"] = True
    if failed:
        shots = failed.get("evidence") or []
        entry["failed_step"] = {
            "number": failed.get("index", 0) + 1,
            "intent": failed.get("intent", ""),
            "error": failed.get("error"),
            # the screen when it failed (a click also has a picture before it, listed first)
            "screenshot": _relative(run_dir / shots[-1], evidence_root) if shots else None,
        }
    return entry


def _overall(statuses: list[str]) -> str:
    if "failed" in statuses:
        return "failed"
    if "healed" in statuses:
        return "healed"
    return "passed" if statuses else ""


def _overall_label(status: str) -> str:
    return {
        "passed": "ALL PASSED",
        "healed": "ALL PASSED, SOME TESTS NEED AN UPDATE",
        "failed": "SOME TESTS FAILED",
    }.get(status, status.upper() or "NO TESTS")


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
