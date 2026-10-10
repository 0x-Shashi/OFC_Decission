"""The certification pack for one Oracle release: one Word summary of where every test stands on
that release, zipped with the evidence document of each test's latest run on it.

    certification_<release>.zip
        Certification <release>.docx       the summary to sign
        evidence/<file>.docx               each test's latest evidence document on this release

Everything comes from the saved run results; nothing is run again. A test not yet run on the
release is listed as such, so the pack shows the gaps as well as the passes.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from quartermaster.evidence.document import (
    _STATUS_COLOR,
    _STATUS_FILL,
    _TEXT_W,
    _cell,
    _Doc,
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

PASSING = ("passed", "healed")


def certification_rows(
    tests: list[dict[str, Any]], results: list[dict[str, Any]], release: str
) -> list[dict[str, Any]]:
    """One row per test: its latest result on `release`, or none. `results` is newest first.

    Tests listed in the tests folder come first, in their order; a test only found in the results
    (removed since, or a manual scenario done by hand) is added after them."""
    latest: dict[str, dict[str, Any]] = {}
    for r in results:
        if r.get("release") == release:
            latest.setdefault(r["test_id"], r)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for t in tests:
        if t.get("problem") or not t.get("id") or t["id"] in seen:
            continue
        seen.add(t["id"])
        rows.append(
            {
                "test_id": t["id"],
                "title": t.get("title") or t["id"],
                "module": t.get("module") or "Other",
                "result": latest.get(t["id"]),
            }
        )
    for tid, r in latest.items():
        if tid not in seen:
            seen.add(tid)
            rows.append({"test_id": tid, "title": r.get("test_title") or tid, "module": "Other", "result": r})
    return rows


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Where a release stands, in numbers, and a fingerprint of exactly which results these are (the run
    behind each test's result). The fingerprint tells whether tests were run again after an approval."""
    by_test: dict[str, str] = {}
    passed = failed = 0
    for row in rows:
        r = row["result"]
        by_test[row["test_id"]] = f"{r.get('run_id') or '-'}:{r['status']}" if r else "-:not-run"
        if r:
            passed += r["status"] in PASSING
            failed += r["status"] not in PASSING
    fingerprint = hashlib.sha256("\n".join(f"{k}|{v}" for k, v in sorted(by_test.items())).encode()).hexdigest()
    return {
        "total": len(rows),
        "passed": passed,
        "failed": failed,
        "not_run": len(rows) - passed - failed,
        "fingerprint": fingerprint,
        "by_test": by_test,
    }


def write_certification_pack(
    tests: list[dict[str, Any]],
    results: list[dict[str, Any]],
    release: str,
    evidence_root: Path,
    *,
    prepared_by: str = "",
    approval: dict[str, Any] | None = None,
    approval_history: list[dict[str, Any]] | None = None,
    tickets: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[str, bytes]:
    """The zip's file name and its bytes. `approval` is where the release's approval stands (see
    service.approvals); its history goes into the pack as approvals.json."""
    rows = certification_rows(tests, results, release)
    safe = _safe(release) or "release"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        names: set[str] = set()
        for row in rows:
            r = row["result"]
            doc = (evidence_root / r["document"]) if r and r.get("document") else None
            if doc is None or not doc.is_file():
                continue
            name = f"evidence/{_safe(row['test_id'])}_{_safe(str(r.get('run_id', '')))}.docx"
            if name in names:
                continue
            names.add(name)
            row["pack_file"] = name
            z.write(doc, name)
        summary = Path(evidence_root) / "_certification" / f"Certification {safe}.docx"
        write_certification_document(
            rows, release, evidence_root, summary, prepared_by=prepared_by, approval=approval, tickets=tickets
        )
        z.write(summary, summary.name)
        if approval_history:
            z.writestr("approvals.json", json.dumps(approval_history, indent=2))
    return f"certification_{safe}.zip", buf.getvalue()


def write_certification_document(
    rows: list[dict[str, Any]],
    release: str,
    evidence_root: Path,
    out: Path,
    *,
    prepared_by: str = "",
    approval: dict[str, Any] | None = None,
    tickets: dict[str, list[dict[str, Any]]] | None = None,
) -> Path:
    doc = _Doc(evidence_root)  # failure pictures are relative to the evidence root
    run = [row for row in rows if row["result"]]
    passed = [row for row in run if row["result"]["status"] in PASSING]
    failed = [row for row in run if row["result"]["status"] not in PASSING]
    not_run = [row for row in rows if not row["result"]]
    status = "failed" if failed else ("skipped" if not_run or not rows else "passed")
    verdict = (
        "SOME TESTS FAILED" if failed else "NOT EVERY TEST HAS RUN YET" if not_run or not rows else "ALL TESTS PASSED"
    )
    envs = sorted({str(row["result"].get("environment_url") or "") for row in run} - {""})
    dates = sorted(str(row["result"].get("at") or "") for row in run if row["result"].get("at"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    parts: list[str] = []
    add = parts.append

    add(_p([_r("Release Certification")], style="Title"))
    add(_p([_r(f"Oracle release {release}")], style="Subtitle"))
    add(
        _p(
            [
                _r("Result: ", bold=True, size=28),
                _r(verdict, bold=True, size=28, color=_STATUS_COLOR.get(status, "15202A")),
            ],
            after=160,
        )
    )
    add(
        _p(
            [
                _r(
                    "This document records how the regression tests went on this Oracle release. For each test "
                    "it gives the result of its latest run on the release. The evidence document of each run is "
                    "in the evidence folder of this pack, with every step and pictures of the screen."
                )
            ],
            after=200,
        )
    )

    add(_p([_r("Summary")], style="Heading1"))
    add(
        _kv_table(
            [
                ("Oracle release", release),
                ("Result", verdict.capitalize()),
                ("Tests", str(len(rows))),
                ("Passed on this release", str(len(passed))),
                ("Failed on this release", str(len(failed))),
                ("Not yet run on this release", str(len(not_run))),
                ("Oracle environment", ", ".join(envs) or "Not recorded"),
                ("Runs between", f"{_when(dates[0])} and {_when(dates[-1])}" if dates else "No runs yet"),
                ("Pack made", _when(now)),
                *([("Made by", prepared_by)] if prepared_by else []),
            ],
            status_row=("Result", status),
        )
    )

    _approval_section(add, approval, release)

    modules: dict[str, list[int]] = {}
    for row in rows:
        m = modules.setdefault(row["module"], [0, 0, 0])
        m[0 if not row["result"] else 1 if row["result"]["status"] in PASSING else 2] += 1
    if len(modules) > 1:
        add(_p([_r("By module")], style="Heading1"))
        mw = [3480, 2200, 2200, 2200]
        mrows = [[_cell(h, w, header=True) for h, w in zip(("Module", "Passed", "Failed", "Not run"), mw, strict=True)]]
        for name, (nr, ok, bad) in sorted(modules.items()):
            mrows.append(
                [_cell(name, mw[0], bold=True), _cell(str(ok), mw[1]), _cell(str(bad), mw[2]), _cell(str(nr), mw[3])]
            )
        add(_table(mrows, mw))

    add(_p([_r("Every test")], style="Heading1"))
    widths = [3480, 1500, 1700, 1500, 1900]
    head = ("Test", "Module", "Result", "Run on", "Evidence")
    trows = [[_cell(h, w, header=True) for h, w in zip(head, widths, strict=True)]]
    for row in rows:
        r = row["result"]
        st = r["status"] if r else "skipped"
        label = (_status_label(st) + (" by hand" if r.get("mode") == "manual" else "")) if r else "Not run"
        trows.append(
            [
                _cell(row["title"], widths[0]),
                _cell(row["module"], widths[1]),
                _cell(label, widths[2], fill=_STATUS_FILL.get(st), color=_STATUS_COLOR.get(st), bold=True),
                _cell(_when(r.get("at")) if r else "", widths[3]),
                _cell(row.get("pack_file", "") if r else "", widths[4]),
            ]
        )
    add(_table(trows, widths))

    if failed:
        add(_page_break())
        add(_p([_r("What failed")], style="Heading1"))
    for n, row in enumerate(failed):
        r = row["result"]
        f = r.get("failed_step") or {}
        if f.get("screenshot") and n:
            add(_page_break())
        add(_p([_r(row["title"])], style="Heading2"))
        add(
            _kv_table(
                [
                    ("Result", _status_label("failed")),
                    ("Failed at", f"Step {f.get('number', '?')}: {f.get('intent', '')}" if f else "Not recorded"),
                    ("What happened", plain_error(f.get("error")) if f else "Not recorded"),
                    ("Run on", _when(r.get("at"))),
                    *(
                        [
                            (
                                "Tickets",
                                ", ".join(f"{t['ref']} ({t['url']})" if t.get("url") else str(t["ref"]) for t in found),
                            )
                        ]
                        if (found := (tickets or {}).get(row["test_id"]))
                        else []
                    ),
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

    if not_run:
        add(_p([_r("Not yet run on this release")], style="Heading1"))
        add(_p([_r("These tests have no run on this release yet, so this pack has no evidence for them:")]))
        for row in not_run:
            add(_p([_r(f"{row['title']} ({row['module']})")], indent=360))

    add(_page_break())
    add(_p([_r("Sign-off")], style="Heading1"))
    add(
        _p(
            [
                _r(
                    f"By signing, the reviewer confirms this document and the evidence it lists are a true record "
                    f"of the regression testing of Oracle release {release}, and accepts the results above."
                )
            ]
        )
    )
    sw = [2200, 3080, 2800, _TEXT_W - 8080]
    sign = [[_cell(h, w, header=True) for h, w in zip(("Role", "Name", "Signature", "Date"), sw, strict=True)]]
    for role in ("Test lead", "Business owner", "Approved by"):
        sign.append([_cell(role, sw[0], bold=True), _cell("", sw[1]), _cell("", sw[2]), _cell("", sw[3])])
    add(_table(sign, sw, row_height=620))

    return write_package(
        out,
        doc,
        document_xml(parts),
        title=f"Release certification {release}",
        creator=prepared_by,
        subject=f"Oracle release {release}",
        created=now,
        footer_left=f"Release certification {release}",
    )


def _approval_section(add: Any, approval: dict[str, Any] | None, release: str) -> None:
    """Who approved the release, when, and on which results (or that nobody has)."""
    add(_p([_r("Approval")], style="Heading1"))
    state = (approval or {}).get("status", "not_approved")
    last = (approval or {}).get("last") or {}
    if state == "not_approved":
        add(
            _p(
                [
                    _r(
                        f"Release {release} has not been approved in Quartermaster yet. Approve it on the Overview "
                        "page and download this pack again to have the approval in it."
                    )
                ],
                after=160,
            )
        )
        return
    results = last.get("results") or {}
    changes = (approval or {}).get("changes") or {}
    shown = {"approved": "Approved", "withdrawn": "Approval withdrawn"}[state]
    rows: list[tuple[str, Any]] = [
        ("Status", shown + (" (results changed since)" if changes.get("changed") else "")),
        ("By" if state == "approved" else "Withdrawn by", last.get("by", "")),
        *([("Role", last["title"])] if last.get("title") else []),
        ("When", _when(last.get("at"))),
        ("Comment" if state == "approved" else "Reason", last.get("comment") or "None given"),
        (
            "Results when it was done",
            f"{results.get('passed', 0)} passed, {results.get('failed', 0)} failed, "
            f"{results.get('not_run', 0)} not run of {results.get('total', 0)} tests",
        ),
        *(
            [("Signed in as", f"{last['signed_in_as']} (a user of Quartermaster)")]
            if last.get("signed_in_as")
            else [("Computer user", f"{last.get('computer_user', '')} (the name above was typed by the approver)")]
        ),
        ("Fingerprint of the results", str(results.get("fingerprint") or "")[:16]),
    ]
    if last.get("acknowledged_open_items"):
        rows.append(("Approved with open items", "Yes: the approver confirmed tests failed or had not run"))
    add(
        _kv_table(
            rows, status_row=("Status", "passed" if state == "approved" and not changes.get("changed") else "failed")
        )
    )
    if changes.get("changed"):
        add(
            _p(
                [
                    _r(
                        f"Tests were run again after this approval ({changes.get('tests')} changed, "
                        f"{changes.get('newly_failing')} now failing), so the results in this document are not "
                        "the ones that were approved. Approve the release again."
                    )
                ],
                after=160,
            )
        )


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")[:60]
