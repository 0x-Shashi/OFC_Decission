"""Reading a saved Oracle What's New page (or pasted text) into the features of a release."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from conftest import EXAMPLES
from test_service_api import call  # noqa: F401  (a helper)

from quartermaster.importers.release_sheet import ImportError_, parse_release
from quartermaster.importers.whats_new import parse_whats_new
from quartermaster.service.api import ApiError, App

SAMPLE = (EXAMPLES / "releases" / "26D_whats_new_sample.html").read_bytes()
TEXT = (EXAMPLES / "releases" / "26D_whats_new_sample.txt").read_bytes()


def by_title(result: Any) -> dict[str, dict[str, Any]]:
    return {f["title"]: f for f in result.release["features"]}


def test_a_saved_page_gives_its_features_with_product_module_and_text() -> None:
    result = parse_release("whats_new.html", SAMPLE)  # the release id is read from the page
    assert result.release["id"] == "26D"
    got = by_title(result)
    assert list(got) == [
        "Redwood Worker Search",
        "New Personal Details Page",
        "Location Page Shows Address on Map",
        "Invoice Approval by Line",
    ]
    worker = got["Redwood Worker Search"]
    assert (worker["product"], worker["module"]) == ("Global Human Resources", "HCM")
    assert "finds a person by name" in worker["description"] and "stays available until 27A" in worker["description"]
    assert "redwood" in worker["tags"]
    assert (
        got["Invoice Approval by Line"]["product"] == "Payables"
        and got["Invoice Approval by Line"]["module"] == "Financials"
    )
    assert [f["id"] for f in result.release["features"]] == ["26D-001", "26D-002", "26D-003", "26D-004"]
    assert result.columns["title"] == "Feature (table)" and result.skipped == []


def test_opt_in_comes_from_the_action_column_and_the_text() -> None:
    got = by_title(parse_release("w.html", SAMPLE))
    assert got["New Personal Details Page"]["opt_in"] and got["New Personal Details Page"]["customer_action_required"]
    assert not got["Redwood Worker Search"]["opt_in"] and not got["Location Page Shows Address on Map"]["opt_in"]


def test_the_kind_of_change_comes_from_the_words_of_the_feature() -> None:
    got = by_title(parse_release("w.html", SAMPLE))
    assert got["Invoice Approval by Line"]["change_type"] == "BOTH"  # the approval process and a REST service
    assert got["Location Page Shows Address on Map"]["change_type"] == "UI"


def page(body: str, title: str = "What's New 26D") -> bytes:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>".encode()


def test_menus_scripts_and_footers_are_not_features() -> None:
    html = page(
        "<nav><h2>Site menu</h2><p>Home</p></nav><script>var x='<h3>No</h3>'</script>"
        "<h2>Procurement</h2><h3>Purchasing</h3><h4>Buyer Cart</h4><p>The cart is easier to read.</p>"
        "<footer><h4>Copyright</h4><p>Oracle</p></footer>"
    )
    got = by_title(parse_release("w.html", html))
    assert list(got) == ["Buyer Cart"]
    assert (got["Buyer Cart"]["product"], got["Buyer Cart"]["module"]) == ("Purchasing", "Procurement")


def test_a_steps_section_that_says_nothing_is_needed_is_not_opt_in() -> None:
    html = page(
        "<h2>Human Capital Management</h2><h3>Core HR</h3>"
        "<table><tr><th>Feature</th></tr><tr><td>Quick Actions</td></tr><tr><td>New Report Page</td></tr></table>"
        "<h4>Quick Actions</h4><p>Faster.</p><h5>Steps to Enable and Configure</h5>"
        "<p>You don't need to do anything to enable this feature.</p>"
        "<h4>New Report Page</h4><p>A report.</p><h5>Steps to Enable and Configure</h5>"
        "<p>Give the role the privilege Run Report, then add the page to the navigator.</p>"
    )
    got = by_title(parse_release("w.html", html))
    assert not got["Quick Actions"]["opt_in"] and got["New Report Page"]["opt_in"]
    assert got["New Report Page"]["change_type"] == "REPORT"
    assert "privilege" not in got["Quick Actions"]["description"]


def test_a_page_without_a_feature_table_is_read_by_its_headings() -> None:
    html = page(
        "<h2>Financials</h2><h3>Payables</h3><h4>Faster invoices</h4><p>Invoices are entered faster.</p>"
        "<h4>Supplier alerts</h4><p>Alerts. Customer must take action to turn them on.</p><h4>Empty</h4>"
    )
    result = parse_release("w.html", html)
    got = by_title(result)
    assert list(got) == ["Faster invoices", "Supplier alerts"] and got["Supplier alerts"]["opt_in"]
    assert got["Faster invoices"]["product"] == "Payables" and got["Faster invoices"]["module"] == "Financials"
    assert result.columns == {"title": "headings with text under them"}


def test_rows_for_another_update_are_left_out_and_said_so() -> None:
    html = page(
        "<h2>Procurement</h2><h3>Purchasing</h3><table><tr><th>Feature</th><th>Update</th></tr>"
        "<tr><td>A</td><td>26D</td></tr><tr><td>B</td><td>26C</td></tr></table>"
    )
    result = parse_release("w.html", html)
    assert [f["title"] for f in result.release["features"]] == ["A"] and "26C, not 26D" in result.skipped[0]


def test_the_release_id_is_asked_for_when_the_page_does_not_say() -> None:
    html = page("<h2>Procurement</h2><h3>Purchasing</h3><h4>Cart</h4><p>Text.</p>", title="Release notes")
    with pytest.raises(ImportError_, match="enter the release id"):
        parse_release("w.html", html)
    assert parse_release("w.html", html, "26c").release["id"] == "26C"  # given by the person: any case


def test_a_page_with_no_features_says_what_to_do() -> None:
    with pytest.raises(ImportError_, match="no features were found"):
        parse_release("w.html", page("<h2>Hello</h2><p>Nothing here.</p>"), "26D")
    with pytest.raises(ImportError_, match="nothing readable"):
        parse_whats_new("w.html", b"   ", "26D")


def test_pasted_text_works_with_markdown_headings_and_plain_lines() -> None:
    got = by_title(parse_release("pasted.txt", TEXT))
    assert list(got) == ["Redwood Worker Search", "New Personal Details Page"]
    assert got["New Personal Details Page"]["product"] == "Global Human Resources"  # not the sibling above it
    assert got["New Personal Details Page"]["opt_in"] and not got["Redwood Worker Search"]["opt_in"]


def test_pasted_tab_separated_rows_are_a_table() -> None:
    text = (
        "26D\nHuman Capital Management\nGlobal Human Resources\n"
        "Feature\tCustomer Must Take Action to Use\nNew Page\tYes\nOld Page\tNo\n"
    )
    got = by_title(parse_release("t.txt", text.encode()))
    assert got["New Page"]["opt_in"] and not got["Old Page"]["opt_in"]
    assert got["New Page"]["product"] == "Global Human Resources"


def test_the_other_formats_still_work_and_the_error_lists_the_new_ones() -> None:
    with pytest.raises(ImportError_, match=r"\.html \(a saved What's New page\)"):
        parse_release("x.pdf", b"%PDF")
    assert (
        parse_release("26D_sample.json", (EXAMPLES / "releases" / "26D_sample.json").read_bytes()).release["id"]
        == "26D"
    )


def test_the_import_in_the_service_previews_then_saves_it_for_release_impact(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    a = App(tests_root=tests, evidence_root=tmp_path / "ev", data_dir=tmp_path / ".qm")
    upload = {"name": "whats_new_26D.html", "content": base64.b64encode(SAMPLE).decode(), "release_id": ""}
    preview = call(a, "POST", "/api/releases/import", upload)
    assert preview["id"] == "26D" and preview["features"] == 4 and not preview["saved"]
    assert preview["sample"][0]["description"].startswith("Search for workers faster")
    saved = call(a, "POST", "/api/releases/import", {**upload, "save": True})
    assert saved["saved"] and saved["name"] == "26D.json"
    assert call(a, "GET", "/api/releases/plan?name=26D.json")["summary"]["features"] == 4
    with pytest.raises(ApiError, match="no features were found"):
        call(a, "POST", "/api/releases/import", {**upload, "content": base64.b64encode(page("<p>x</p>")).decode()})
