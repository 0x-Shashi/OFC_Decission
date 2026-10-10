"""Ticket links: remember which ticket tracks which failing test, and write a ticket text from a failure."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from test_service_api import add_suite_tests, app, call, finished_suite_run  # noqa: F401, F811  (app is a fixture)

from quartermaster.service.api import ApiError
from quartermaster.service.tickets import TicketError, Tickets

JIRA = "https://example.atlassian.net/browse/{key}"
NEW = "https://example.atlassian.net/secure/CreateIssueDetails!init.jspa?summary={title}&description={description}"


def make(tmp_path: Path) -> tuple[Tickets, list[tuple[str, str, dict[str, Any]]]]:
    told: list[tuple[str, str, dict[str, Any]]] = []
    return Tickets(tmp_path / "tickets", audit=lambda w, s, d: told.append((w, s, d))), told


def test_the_tracker_address_is_checked_and_turns_a_number_into_a_link(tmp_path: Path) -> None:
    t, told = make(tmp_path)
    assert t.settings() == {"name": "", "link_template": "", "create_template": ""} and t.url_for("PROJ-1") == ""
    for bad, why in (
        ({"link_template": "javascript:alert({key})"}, "must start with https://"),
        ({"link_template": "ftp://x/{key}"}, "must start with https://"),
        ({"link_template": "https://x/browse/"}, "must contain {key}"),
        ({"create_template": "https://x/new"}, "must contain {title} or {description}"),
        ({"link_template": "https://x/" + "a" * 500 + "{key}"}, "under 400 characters"),
        ({"colour": "red"}, "unknown settings"),
    ):
        with pytest.raises(TicketError, match=why.replace("{", r"\{").replace("}", r"\}")):
            t.update_settings(bad)
    t.update_settings({"name": " Jira ", "link_template": JIRA, "create_template": NEW})
    assert t.settings()["name"] == "Jira" and t.url_for("PROJ-123") == "https://example.atlassian.net/browse/PROJ-123"
    assert t.url_for("a b/c") == "https://example.atlassian.net/browse/a%20b%2Fc"  # never breaks out of the address
    assert told[-1][0] == "Changed the ticket tracker settings"


def test_a_ticket_can_be_typed_as_a_number_or_pasted_as_a_link(tmp_path: Path) -> None:
    t, _ = make(tmp_path)
    assert t.parse("PROJ-123") == ("PROJ-123", "")  # no tracker address set: just the number
    t.update_settings({"link_template": JIRA})
    assert t.parse(" PROJ-123 ") == ("PROJ-123", "https://example.atlassian.net/browse/PROJ-123")
    assert t.parse("https://tracker.example.com/issues/PAY-77?x=1") == (
        "PAY-77",
        "https://tracker.example.com/issues/PAY-77?x=1",
    )
    assert t.parse("https://tracker.example.com/tickets/4812")[0] == "4812"
    for bad in ("", "   ", "javascript:alert(1)", "https://", "ftp://x/y", "no spaces allowed", "<script>", "x" * 50):
        with pytest.raises(TicketError):
            t.parse(bad)
    with pytest.raises(TicketError, match="under 400 characters"):
        t.parse("https://x.example.com/" + "a" * 500)


def test_links_are_added_and_removed_as_lines_and_the_history_stays(tmp_path: Path) -> None:
    t, told = make(tmp_path)
    t.update_settings({"link_template": JIRA})
    assert t.add("hcm.create-location", "PROJ-1", "R1", "Sai Ram")[0]["who"] == "Sai Ram"
    t.add("hcm.create-location", "PROJ-2")
    t.add("hcm.view-worker", "PROJ-9")
    with pytest.raises(TicketError, match="already linked"):
        t.add("hcm.create-location", "PROJ-1")
    assert [x["ref"] for x in t.for_test("hcm.create-location")] == ["PROJ-1", "PROJ-2"]
    assert t.links()["hcm.view-worker"][0]["url"].endswith("/PROJ-9")
    t.remove("hcm.create-location", "PROJ-1", "Sai Ram")
    assert [x["ref"] for x in t.for_test("hcm.create-location")] == ["PROJ-2"]
    with pytest.raises(TicketError, match="not linked"):
        t.remove("hcm.create-location", "PROJ-1")
    t.add("hcm.create-location", "PROJ-1")  # it can come back
    lines = (tmp_path / "tickets" / "links.jsonl").read_text().splitlines()
    assert [json.loads(x)["action"] for x in lines] == ["add", "add", "add", "remove", "add"]
    assert [x[0] for x in told if x[1] == "hcm.create-location"] == [
        "Linked a ticket",
        "Linked a ticket",
        "Removed a ticket link",
        "Linked a ticket",
    ]
    with pytest.raises(TicketError, match="choose the test"):
        t.add("", "PROJ-5")


def test_a_damaged_line_in_the_file_does_not_lose_the_others(tmp_path: Path) -> None:
    t, _ = make(tmp_path)
    t.add("a", "PROJ-1")
    with (tmp_path / "tickets" / "links.jsonl").open("a") as f:
        f.write("{broken\n[1,2]\n")
    t.add("a", "PROJ-2")
    assert [x["ref"] for x in t.for_test("a")] == ["PROJ-1", "PROJ-2"]


ITEM = {
    "test_id": "hcm.create-location",
    "title": "Create a location",
    "step": 2,
    "intent": "Check the city",
    "error": 'The screen showed "Redwood City" but it should show "Redwood Shores".',
    "compare": {"expected": "Redwood Shores", "observed": "Redwood City"},
    "run_release": "26D",
    "last_good_release": "26C",
    "run_id": "R7",
    "cause": {"title": "Probably the 26D update"},
}


def test_a_ticket_text_is_written_from_a_failure(tmp_path: Path) -> None:
    t, _ = make(tmp_path)
    d = t.draft(ITEM)
    assert d["title"] == 'Create a location: step 2 "Check the city" failed' and d["create_url"] == ""
    for line in (
        "Test: Create a location (hcm.create-location)",
        "Failed at: step 2: Check the city",
        "Expected: Redwood Shores",
        "Observed: Redwood City",
        "Oracle release: 26D (passed on 26C)",
        "Likely cause: Probably the 26D update",
        "Run: R7",
        "Attach it to the ticket",
    ):
        assert line in d["description"]
    assert "\n\n\n" not in d["description"]


def test_the_new_ticket_address_is_filled_in_and_encoded(tmp_path: Path) -> None:
    t, _ = make(tmp_path)
    t.update_settings({"name": "Jira", "create_template": NEW})
    d = t.draft({**ITEM, "title": 'Odd & "quoted" title?', "error": "a" * 4000})
    parts = urlsplit(d["create_url"])
    q = parse_qs(parts.query)
    assert parts.netloc == "example.atlassian.net" and q["summary"][0].startswith('Odd & "quoted" title?')
    assert len(q["description"][0]) <= 1500 and d["tracker"] == "Jira"
    assert "&description=" in d["create_url"] and d["create_url"].count("&") == 1  # nothing broke out of the value


# ------------------------------------------------------------------ in the service


def test_failures_carry_their_tickets_and_the_draft_and_links_come_through_the_api(app: Any) -> None:  # noqa: F811
    add_suite_tests(app)
    finished_suite_run(app)
    call(app, "POST", "/api/tickets/settings", {"name": "Jira", "link_template": JIRA, "create_template": NEW})
    (failure,) = [i for i in call(app, "GET", "/api/attention")["items"] if i["category"] == "assertion"]
    assert failure["tickets"] == []

    draft = call(app, "GET", f"/api/tickets/draft?run={failure['run_id']}&test={failure['test_id']}")
    assert "Redwood Shores" in draft["description"] and draft["create_url"].startswith("https://example.atlassian.net/")
    with pytest.raises(ApiError, match="no failure of that test"):
        call(app, "GET", f"/api/tickets/draft?run={failure['run_id']}&test=hcm.view-worker")

    got = call(
        app,
        "POST",
        "/api/tickets",
        {"action": "add", "test_id": failure["test_id"], "ref": "PROJ-5", "run_id": failure["run_id"]},
    )
    assert got["tickets"][0]["url"] == "https://example.atlassian.net/browse/PROJ-5"
    again = [i for i in call(app, "GET", "/api/attention")["items"] if i["category"] == "assertion"][0]
    assert [x["ref"] for x in again["tickets"]] == ["PROJ-5"]
    tests = {t["id"]: t for t in call(app, "GET", "/api/tests") if t.get("id")}
    assert [x["ref"] for x in tests["hcm.create-location"]["tickets"]] == ["PROJ-5"] and tests["hcm.view-worker"][
        "tickets"
    ] == []
    assert call(app, "GET", "/api/tickets")["links"]["hcm.create-location"][0]["ref"] == "PROJ-5"

    for body, why in (
        ({"action": "add", "test_id": failure["test_id"], "ref": "PROJ-5"}, "already linked"),
        (
            {"action": "add", "test_id": failure["test_id"], "ref": "javascript:alert(1)"},
            "a ticket number uses letters",
        ),
        ({"action": "nope", "test_id": "x"}, "choose add or remove"),
    ):
        with pytest.raises(ApiError, match=why):
            call(app, "POST", "/api/tickets", body)
    call(app, "POST", "/api/tickets", {"action": "remove", "test_id": failure["test_id"], "ref": "PROJ-5"})
    assert call(app, "GET", "/api/tickets")["links"] == {}
    actions = [e["action"] for e in json.loads(app.handle("GET", "/api/audit", b"").body)["entries"]]
    assert {"Linked a ticket", "Removed a ticket link", "Changed the ticket tracker settings"} <= set(actions)


def test_the_certification_pack_lists_the_tickets_of_failed_tests(app: Any) -> None:  # noqa: F811
    add_suite_tests(app)
    finished_suite_run(app)
    call(app, "POST", "/api/tickets/settings", {"link_template": JIRA})
    call(app, "POST", "/api/tickets", {"action": "add", "test_id": "hcm.create-location", "ref": "PROJ-5"})
    pack = app.handle("GET", "/api/certification?release=26D", b"")
    z = zipfile.ZipFile(io.BytesIO(pack.body))
    docx = next(n for n in z.namelist() if n.startswith("Certification") and n.endswith(".docx"))
    xml = zipfile.ZipFile(io.BytesIO(z.read(docx))).read("word/document.xml").decode()
    assert "Tickets" in xml and "PROJ-5 (https://example.atlassian.net/browse/PROJ-5)" in xml


def test_only_an_administrator_changes_where_the_tracker_is() -> None:
    from quartermaster.service.auth import required_role

    assert required_role("POST", ["tickets", "settings"]) == "admin"
    assert required_role("POST", ["tickets"]) == "tester" and required_role("GET", ["tickets", "draft"]) == "any"
