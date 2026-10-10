"""Approving a release: who, when and on which results, kept and shown in the certification pack."""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import pytest
from test_service_api import add_suite_tests, app, call, finished_suite_run  # noqa: F401  (app is a fixture)

from quartermaster.evidence.certification import summarize_rows
from quartermaster.service.api import ApiError, App
from quartermaster.service.approvals import ApprovalError, Approvals
from quartermaster.service.audit import AuditLog


def rows(*results: tuple[str, str | None, str | None]) -> list[dict[str, Any]]:
    """(test id, run id, status or None for not run) -> certification rows."""
    return [
        {"test_id": t, "title": t, "module": "HCM", "result": {"run_id": run, "status": st} if st else None}
        for t, run, st in results
    ]


CLEAN = rows(("a", "r1", "passed"), ("b", "r2", "passed"), ("c", "r3", "healed"))
OPEN = rows(("a", "r1", "passed"), ("b", "r2", "failed"), ("c", None, None))


def make(tmp_path: Path) -> Approvals:
    return Approvals(tmp_path / "approvals.jsonl", audit=AuditLog(tmp_path / "audit.jsonl"), who=lambda: "pvsai")


def ask(name: str = "Jane Doe", **more: Any) -> dict[str, Any]:
    return {"name": name, "title": "Test manager", "comment": "", **more}


# ------------------------------------------------------------------ the results behind an approval


def test_the_summary_counts_and_fingerprints_exactly_which_results_these_are() -> None:
    s = summarize_rows(OPEN)
    assert (s["total"], s["passed"], s["failed"], s["not_run"]) == (3, 1, 1, 1)
    assert s["by_test"] == {"a": "r1:passed", "b": "r2:failed", "c": "-:not-run"}
    assert summarize_rows(list(reversed(OPEN)))["fingerprint"] == s["fingerprint"]  # the order does not matter
    again = rows(("a", "r1", "passed"), ("b", "r9", "failed"), ("c", None, None))  # b was run again
    assert summarize_rows(again)["fingerprint"] != s["fingerprint"]
    assert summarize_rows([])["total"] == 0


# ------------------------------------------------------------------ approving


def test_a_clean_release_is_approved_and_kept(tmp_path: Path) -> None:
    a = make(tmp_path)
    s = summarize_rows(CLEAN)
    assert a.state("26C", s)["status"] == "not_approved"
    rec = a.approve("26C", s, ask())
    assert rec["by"] == "Jane Doe" and rec["title"] == "Test manager" and rec["computer_user"] == "pvsai"
    assert rec["results"]["passed"] == 3 and rec["action"] == "approved"
    state = a.state("26C", s)
    assert state["status"] == "approved" and state["last"]["by"] == "Jane Doe" and not state["changes"]["changed"]
    assert [r["action"] for r in a.history("26C")] == ["approved"] and a.history("26D") == []
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "Approved a release" in audit and "Jane Doe" in audit


def test_the_approver_must_give_a_name(tmp_path: Path) -> None:
    a = make(tmp_path)
    for bad in ("", " ", "J", "x" * 81):
        with pytest.raises(ApprovalError, match="name"):
            a.approve("26C", summarize_rows(CLEAN), ask(bad))
    assert a.history() == []


def test_a_release_without_tests_cannot_be_approved(tmp_path: Path) -> None:
    with pytest.raises(ApprovalError, match="no tests"):
        make(tmp_path).approve("26C", summarize_rows([]), ask())


@pytest.mark.parametrize("release", ["", " ", "../x", "26C/../x", "a" * 21])
def test_a_release_name_that_is_not_one_is_refused(tmp_path: Path, release: str) -> None:
    with pytest.raises(ApprovalError, match="Oracle release"):
        make(tmp_path).approve(release, summarize_rows(CLEAN), ask())


def test_failed_or_not_run_tests_need_a_tick_and_a_reason(tmp_path: Path) -> None:
    a = make(tmp_path)
    s = summarize_rows(OPEN)
    with pytest.raises(ApprovalError, match="tick the box"):
        a.approve("26C", s, ask(comment="The failure is a known data problem"))
    with pytest.raises(ApprovalError, match="at least 10 letters"):
        a.approve("26C", s, ask(acknowledged=True, comment="ok"))
    assert a.history() == []
    rec = a.approve("26C", s, ask(acknowledged=True, comment="The failure is a known data problem"))
    assert rec["acknowledged_open_items"] is True and rec["results"]["failed"] == 1


def test_a_clean_approval_does_not_need_a_reason(tmp_path: Path) -> None:
    rec = make(tmp_path).approve("26C", summarize_rows(CLEAN), ask())
    assert rec["comment"] == "" and rec["acknowledged_open_items"] is False


def test_an_approved_release_cannot_be_approved_twice(tmp_path: Path) -> None:
    a = make(tmp_path)
    s = summarize_rows(CLEAN)
    a.approve("26C", s, ask())
    with pytest.raises(ApprovalError, match="already approved by Jane Doe"):
        a.approve("26C", s, ask("Someone Else"))
    assert len(a.history()) == 1


def test_running_tests_again_shows_the_approval_as_out_of_date_and_allows_approving_again(tmp_path: Path) -> None:
    a = make(tmp_path)
    a.approve("26C", summarize_rows(CLEAN), ask())
    later = summarize_rows(
        rows(("a", "r1", "passed"), ("b", "r7", "failed"), ("c", "r3", "healed"))
    )  # b run again, fails
    state = a.state("26C", later)
    assert state["status"] == "approved"
    assert state["changes"] == {"changed": True, "tests": 1, "newly_failing": 1}
    a.approve("26C", later, ask("Jane Doe", acknowledged=True, comment="b is a known data problem"))
    assert a.state("26C", later)["changes"]["changed"] is False
    assert [r["action"] for r in a.history("26C")] == ["approved", "approved"]


def test_a_test_added_after_the_approval_counts_as_a_change(tmp_path: Path) -> None:
    a = make(tmp_path)
    a.approve("26C", summarize_rows(CLEAN), ask())
    more = summarize_rows([*CLEAN, *rows(("d", None, None))])
    assert a.state("26C", more)["changes"]["changed"] and a.state("26C", more)["changes"]["tests"] == 1


# ------------------------------------------------------------------ withdrawing


def test_an_approval_can_be_withdrawn_with_a_reason_and_approved_again(tmp_path: Path) -> None:
    a = make(tmp_path)
    s = summarize_rows(CLEAN)
    with pytest.raises(ApprovalError, match="not approved"):
        a.withdraw("26C", s, ask(comment="because"))
    a.approve("26C", s, ask())
    with pytest.raises(ApprovalError, match="why"):
        a.withdraw("26C", s, ask("Sam Lee", comment="no"))
    with pytest.raises(ApprovalError, match="name"):
        a.withdraw("26C", s, {"name": "", "comment": "a real reason"})
    a.withdraw("26C", s, ask("Sam Lee", comment="A late fix went in"))
    state = a.state("26C", s)
    assert state["status"] == "withdrawn" and state["last"]["by"] == "Sam Lee"
    assert "A late fix went in" in state["last"]["comment"]
    a.approve("26C", s, ask("Jane Doe"))
    assert [r["action"] for r in a.history("26C")] == ["approved", "withdrawn", "approved"]  # newest first
    assert "Withdrew the approval" in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


def test_records_are_only_ever_added_never_changed(tmp_path: Path) -> None:
    a = make(tmp_path)
    s = summarize_rows(CLEAN)
    a.approve("26C", s, ask())
    first = (tmp_path / "approvals.jsonl").read_text(encoding="utf-8")
    a.withdraw("26C", s, ask("Sam Lee", comment="A late fix went in"))
    a.approve("26C", s, ask())
    now = (tmp_path / "approvals.jsonl").read_text(encoding="utf-8")
    assert now.startswith(first) and len(now.splitlines()) == 3


def test_a_damaged_line_in_the_file_does_not_hide_the_rest(tmp_path: Path) -> None:
    a = make(tmp_path)
    a.approve("26C", summarize_rows(CLEAN), ask())
    with (tmp_path / "approvals.jsonl").open("a", encoding="utf-8") as f:
        f.write("{not json\n")
    assert a.state("26C", summarize_rows(CLEAN))["status"] == "approved"


def test_each_release_has_its_own_approval(tmp_path: Path) -> None:
    a = make(tmp_path)
    a.approve("26C", summarize_rows(CLEAN), ask())
    assert a.state("26D", summarize_rows(CLEAN))["status"] == "not_approved"


# ------------------------------------------------------------------ in the running service


def test_the_page_approves_a_release_and_the_pack_says_so(app: App) -> None:  # noqa: F811
    add_suite_tests(app)
    finished_suite_run(app)  # ran on 26D: 2 passed (one healed), 1 failed; hcm/pass.yaml has not run
    call(app, "POST", "/api/settings", {"release": "26D"})

    got = call(app, "GET", "/api/approvals?release=26D")
    assert got["state"]["status"] == "not_approved" and got["history"] == []
    assert got["state"]["summary"] == {"total": 4, "passed": 2, "failed": 1, "not_run": 1}
    assert call(app, "GET", "/api/dashboard")["approval"]["status"] == "not_approved"

    with pytest.raises(ApiError, match="tick the box"):
        call(app, "POST", "/api/approvals", {"action": "approve", "release": "26D", "name": "Jane Doe"})
    with pytest.raises(ApiError, match="choose approve or withdraw"):
        call(app, "POST", "/api/approvals", {"action": "bless", "release": "26D", "name": "Jane Doe"})
    done = call(
        app,
        "POST",
        "/api/approvals",
        {
            "action": "approve",
            "release": "26D",
            "name": "Jane Doe",
            "title": "Test manager",
            "acknowledged": True,
            "comment": "The location test is a known data problem",
        },
    )
    assert done["status"] == "approved" and done["last"]["by"] == "Jane Doe"
    assert call(app, "GET", "/api/dashboard")["approval"]["last"]["title"] == "Test manager"

    reply = app.handle("GET", "/api/certification?release=26D", b"")
    pack = zipfile.ZipFile(io.BytesIO(reply.body))
    assert "approvals.json" in pack.namelist()
    assert json.loads(pack.read("approvals.json"))[0]["by"] == "Jane Doe"
    word = zipfile.ZipFile(io.BytesIO(pack.read("Certification 26D.docx"))).read("word/document.xml").decode()
    text = " ".join(re.sub(r"<[^>]+>", " ", word).split())
    assert "Approval" in text and "Jane Doe" in text and "Test manager" in text
    assert "known data problem" in text and "2 passed, 1 failed, 1 not run of 4 tests" in text
    assert "Approved with open items" in text and "typed by the approver" in text

    call(
        app,
        "POST",
        "/api/approvals",
        {"action": "withdraw", "release": "26D", "name": "Sam Lee", "comment": "Retest needed"},
    )
    pack = zipfile.ZipFile(io.BytesIO(app.handle("GET", "/api/certification?release=26D", b"").body))
    text = " ".join(
        re.sub(
            r"<[^>]+>",
            " ",
            zipfile.ZipFile(io.BytesIO(pack.read("Certification 26D.docx"))).read("word/document.xml").decode(),
        ).split()
    )
    assert "Approval withdrawn" in text and "Retest needed" in text


def test_a_pack_for_a_release_nobody_approved_says_so(app: App) -> None:  # noqa: F811
    add_suite_tests(app)
    finished_suite_run(app)
    pack = zipfile.ZipFile(io.BytesIO(app.handle("GET", "/api/certification?release=26D", b"").body))
    assert "approvals.json" not in pack.namelist()
    text = " ".join(
        re.sub(
            r"<[^>]+>",
            " ",
            zipfile.ZipFile(io.BytesIO(pack.read("Certification 26D.docx"))).read("word/document.xml").decode(),
        ).split()
    )
    assert "has not been approved in Quartermaster yet" in text


def test_the_page_needs_a_release_and_refuses_what_cannot_be_recorded(app: App) -> None:  # noqa: F811
    with pytest.raises(ApiError, match="set the Oracle release"):
        call(app, "GET", "/api/approvals")
    with pytest.raises(ApiError, match="tick the box"):  # the one test has not run on 27A
        call(app, "POST", "/api/approvals", {"action": "approve", "release": "27A", "name": "Jane Doe"})
    with pytest.raises(ApiError, match="Oracle release"):
        call(app, "POST", "/api/approvals", {"action": "approve", "release": "../x", "name": "Jane Doe"})
    assert "approval" not in call(app, "GET", "/api/dashboard")  # no release chosen: nothing to approve
