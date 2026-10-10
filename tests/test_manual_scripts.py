"""Manual test scripts from Excel: the two layouts, the web API, and matching them to a release."""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

import pytest
from test_release_import import make_xlsx

from quartermaster.importers.manual_scripts import ScriptError, guess_product, parse_scripts
from quartermaster.service.api import ApiError, App

EXAMPLES = Path(__file__).parent.parent / "examples"

SCENARIOS = [
    ["Use Case ID UC001:"],
    ["Verify and Validate end to end functionality of Oracle cloud payable application."],
    ["Use Case ID/Req. ID", "Scenario ID", "Test Scenario", "No. of test cases", "Tester", "Test Status", "Notes"],
    ["UC001", "TS001", "Validate the login functionality", "1", "Sai", "Pass"],
    ["UC001", "TS002", "Create Invoice", "2"],
    ["UC001", "TS009", "Close the period", "1"],
]
CASES = [
    [
        "Scenario ID",
        "Test case ID",
        "Test case Name",
        "Test case description",
        "Test Date",
        "Test step no.",
        "Test step description",
        "Expected result",
        "Actual Result",
        "Status",
    ],
    ["TS001", "TC001", "Valid Login credentials", "Login works", "", "pre-condition", "Fusion is available"],
    ["", "", "", "", "", "step 1", "Launch the Fusion application", "Login page loads"],
    ["", "", "", "", "", "step 2", "Enter the valid user id <> in the user id field", "Field accepts input"],
    ["TS002", "TC001", "Create and validate a standard invoice", "", "", "pre-condition", "Supplier exists"],
    ["", "", "", "", "", "step 1", "Navigate to Payables > Invoices", "Invoices work area opens", "", "Pass"],
    ["", "", "", "", "", "step 2", "Create the invoice and validate it", "Invoice is validated"],
    ["", "TC002", "Create a credit memo", "", "", "step 1", "Create a credit memo invoice", "Credit memo saved"],
    ["TS0010", "TC001", "Cancel an invoice", "", "", "step 1", "Cancel the invoice", "Invoice cancelled"],
]
ACTIONS = [
    ["Please DO NOT Hide columns"],
    ["Employee Self Service - Test Cases", "", "", "", "Index Page"],
    [
        "Actions",
        "",
        "",
        "",
        "Navigation",
        "",
        "",
        "Results",
        "",
        "",
        "Comments",
        "Included in 25 A",
        "Included in 24 D",
    ],
    [
        "Action Name",
        "Action Purpose",
        "Reference Number",
        "Fields that are available to employees for View/Edits",
        "Primary Navigation",
        "Global Search Navigation",
        "Other Navigation",
        "Tester Name",
        "Testing Time",
        "Pass / Fail",
    ],
    ["Actions available to employees", "", "Unique Test case reference number"],
    [
        "My Compensation",
        "View salary",
        "ESS-001",
        "> Current Salary\n> Additional Compensation",
        "> Login\n> Me\n> Personal Information\n> Select My Compensation",
        "",
        "",
        "Tester1",
        "10",
        "Pass",
        "",
        "Y",
        "N",
    ],
    ["Contact Info", "Update phone and address", "ESS-003", "> Phone", "> Login\n> Me\n> Select Contact Info"],
]


def test_reads_a_scenario_workbook() -> None:
    book = make_xlsx({"Test Scenarios": SCENARIOS, "Test Cases": CASES})
    result = parse_scripts("Test Scenario-Test Case- Account payable_V01.xlsx", book)
    assert (result.kind, result.module, result.product) == ("scenarios", "Financials", "Payables")
    by_ref = {s["ref"]: s for s in result.scenarios}
    assert sorted(by_ref) == ["TS001", "TS002", "TS009", "TS010"]  # TS0010 is the same as TS010
    login, invoice = by_ref["TS001"], by_ref["TS002"]
    assert (login["title"], login["tester"], login["status"], login["use_case"]) == (
        "Validate the login functionality",
        "Sai",
        "passed",
        "UC001",
    )
    assert login["cases"][0]["precondition"] == "Fusion is available" and login["step_count"] == 2
    assert login["blank_data"] and login["logins"] == 1
    assert [c["name"] for c in invoice["cases"]] == ["Create and validate a standard invoice", "Create a credit memo"]
    assert invoice["cases"][0]["steps"][0] == {
        "step": "step 1",
        "action": "Navigate to Payables > Invoices",
        "expected": "Invoices work area opens",
        "result": "passed",
    }
    assert by_ref["TS010"]["title"] == "Cancel an invoice"  # not listed: named after its first case
    assert any("TS010" in w and "not listed" in w for w in result.warnings)
    assert any("TS009" in w and "no test cases" in w for w in result.warnings)


def test_reads_an_action_list() -> None:
    result = parse_scripts("ESS_Test_Script.xlsx", make_xlsx({"Sheet1": ACTIONS}))
    assert (result.kind, result.module, result.product) == ("actions", "HCM", "Global Human Resources")
    pay, contact = result.scenarios
    assert (pay["ref"], pay["title"], pay["description"]) == ("ESS-001", "My Compensation", "View salary")
    assert [s["action"] for s in pay["cases"][0]["steps"]] == [
        "Login",
        "Me",
        "Personal Information",
        "Select My Compensation",
    ]
    assert (pay["tester"], pay["minutes"], pay["status"], pay["releases"]) == ("Tester1", 10.0, "passed", ["25A"])
    assert pay["fields"] == ["Current Salary", "Additional Compensation"]
    assert contact["status"] == "" and contact["minutes"] is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Test Scenarios-Test Cases-Core HR ver 2.0.XLSX", ("HCM", "Global Human Resources")),
        ("Test Scenarios-Test Cases-Payroll Interface ver 2.0.xlsx", ("HCM", "Payroll Interface")),
        ("Test Scenario-Test Case-Payroll.xlsx", ("HCM", "Global Payroll")),
        ("Test Scenarios_Test Case_Self-Service Procurement.xlsx", ("Procurement", "Self Service Procurement")),
        ("Test Scenario-Test Case-AR-v01.xlsx", ("Financials", "Receivables")),
        ("Test Scenarios_Test Cases_PPM.xlsx", ("Project Management", "Project Management")),
        ("Quarterly checks.xlsx", ("", "")),
    ],
)
def test_module_and_product_from_the_file_name(name: str, expected: tuple[str, str]) -> None:
    assert guess_product(name) == expected


def test_explains_what_is_wrong() -> None:
    with pytest.raises(ScriptError, match="Excel"):
        parse_scripts("scripts.csv", b"a,b")
    with pytest.raises(ScriptError, match="Test case ID"):
        parse_scripts("x.xlsx", make_xlsx({"Sheet1": [["Colour", "Size"], ["red", "big"]]}))
    unknown = parse_scripts("Quarterly checks.xlsx", make_xlsx({"Test Scenarios": SCENARIOS, "Test Cases": CASES}))
    assert any("enter them" in w for w in unknown.warnings)
    named = parse_scripts("Quarterly checks.xlsx", make_xlsx({"Test Cases": CASES}), "Financials", "Payables")
    assert named.product == "Payables" and not any("enter them" in w for w in named.warnings)


def call(app: App, method: str, path: str, body: dict | None = None) -> dict:
    return json.loads(app.handle(method, path, json.dumps(body or {}).encode()).body)


def test_import_list_open_remove_and_match_to_a_release(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    shutil.copytree(EXAMPLES / "tests", tests)
    app = App(
        tests_root=tests, evidence_root=tmp_path / "ev", data_dir=tmp_path / ".qm", releases_root=EXAMPLES / "releases"
    )
    b64 = lambda raw: base64.b64encode(raw).decode()  # noqa: E731
    files = [
        {
            "name": "Test Scenario-Test Case- Account payable_V01.xlsx",
            "content": b64(make_xlsx({"Test Scenarios": SCENARIOS, "Test Cases": CASES})),
        },
        {"name": "ESS_Test_Script.xlsx", "content": b64(make_xlsx({"Sheet1": ACTIONS}))},
        {"name": "Quarterly checks.xlsx", "content": b64(make_xlsx({"Test Cases": CASES}))},
        {"name": "notes.xlsx", "content": b64(b"not a workbook")},
    ]
    preview = call(app, "POST", "/api/manual/import", {"files": files})
    listed = call(app, "GET", "/api/manual")
    assert preview["saved"] == 0 and (listed["files"], listed["scenarios"]) == ([], [])
    ap, ess, unnamed, broken = preview["files"]
    assert (ap["scenarios"], ap["cases"], ap["steps"], ap["blank_data"]) == (4, 4, 6, 1)
    assert ess["product"] == "Global Human Resources" and broken["problem"]
    assert unnamed["product"] == "" and any("enter them" in w for w in unnamed["warnings"])

    files[2].update(module="Financials", product="Cash Management")
    saved = call(app, "POST", "/api/manual/import", {"files": files, "save": True})
    assert saved["saved"] == 3
    listing = call(app, "GET", "/api/manual")
    assert [f["file"] for f in listing["files"]] == [
        "ESS_Test_Script.xlsx",
        "Quarterly checks.xlsx",
        "Test Scenario-Test Case- Account payable_V01.xlsx",
    ]
    assert len(listing["scenarios"]) == 2 + 3 + 4 and "cases" not in listing["scenarios"][0]
    invoice = call(app, "GET", "/api/manual/scenario?id=test-scenario-test-case-account-payable-v01/TS002")
    assert invoice["title"] == "Create Invoice" and len(invoice["cases"]) == 2

    plan = call(app, "GET", "/api/releases/plan?name=26D_sample.json")
    assert plan["summary"]["manual_scenarios"] == 9
    invoice_feature = next(f for f in plan["features"] if f["id"] == "FIN-AP-001")
    assert invoice_feature["coverage"] == "manual"  # no automated test, but the Payables script covers it
    assert invoice_feature["manual"][0]["title"] in ("Create Invoice", "Cancel an invoice")
    top = plan["manual"][0]
    assert top["product"] == "Payables" and top["features"] == ["FIN-AP-001"] and top["reasons"]
    login = next((m for m in plan["manual"] if m["ref"] == "TS001" and m["product"] == "Payables"), None)
    assert login is None or not login["features"]  # same product only: never counts as coverage

    assert call(app, "POST", "/api/manual/remove", {"key": "quarterly-checks"}) == {"removed": "quarterly-checks"}
    assert len(call(app, "GET", "/api/manual")["files"]) == 2
    for path, body, message in (
        ("/api/manual/remove", {"key": "../x"}, "unknown file"),
        ("/api/manual/remove", {"key": "quarterly-checks"}, "not imported"),
        ("/api/manual/import", {"files": []}, "choose one or more"),
    ):
        with pytest.raises(ApiError, match=message):
            app.handle("POST", path, json.dumps(body).encode())
    with pytest.raises(ApiError, match="no manual scenario"):
        app.handle("GET", "/api/manual/scenario?id=nope", b"")


def test_test_data_entered_in_quartermaster_fills_the_steps_the_script_leaves_open(tmp_path: Path) -> None:
    from quartermaster.service.manual import ManualScripts

    folder = tmp_path / "manual"
    folder.mkdir()
    scenario = {
        "id": "pay/TS001",
        "ref": "TS001",
        "title": "Submit an absence",
        "module": "HCM",
        "product": "Absence Management",
        "blank_data": True,
        "step_count": 3,
        "cases": [
            {
                "id": "TC1",
                "name": "Submit",
                "steps": [
                    {"action": "Enter the start date", "expected": ""},
                    {"action": "Choose the absence type <>", "expected": ""},
                    {"action": "Click Submit", "expected": "It is submitted"},
                ],
            }
        ],
    }
    (folder / "pay.json").write_text(json.dumps({"file": "pay.xlsx", "scenarios": [scenario]}))
    m = ManualScripts(folder)
    [row] = m.summary()["scenarios"]
    assert (row["values_missing"], row["blank_data"]) == (1, True)  # Prepare all leaves it out

    with pytest.raises(ValueError, match="step 9 is not in this scenario"):
        m.set_test_data("pay/TS001", {"9": "x"})
    assert m.set_test_data("pay/TS001", {"1": " 01/10/2026 ", "2": "Vacation", "3": ""}) == {
        "1": "01/10/2026",
        "2": "Vacation",
    }
    [row] = m.summary()["scenarios"]
    assert (row["values_missing"], row["blank_data"]) == (0, False)  # ready for Prepare all now

    steps = m.with_test_data(m.get("pay/TS001"))["cases"][0]["steps"]
    assert steps[0]["action"] == "Enter the start date\nTest data: 01/10/2026"  # what the AI and tester read
    assert steps[2]["action"] == "Click Submit"
    assert m.get("pay/TS001")["cases"][0]["steps"][0]["action"] == "Enter the start date"  # the import is unchanged
    m.set_test_data("pay/TS001", {})
    assert m.test_data_all() == {}


def test_a_scenario_typed_in_quartermaster(tmp_path: Path) -> None:
    app = App(tests_root=tmp_path / "tests", evidence_root=tmp_path / "ev", data_dir=tmp_path / ".qm")
    new = {
        "title": "Update my  home address",
        "module": "HCM",
        "product": "Global Human Resources",
        "steps": [
            {"action": "Click Me, then Personal Information", "expected": ""},
            {"action": "", "expected": ""},  # an empty row is left out
            {"action": "Enter 10 Main Street in Address Line 1", "expected": "The new address is shown"},
        ],
        "fields": ["Address Line 1", " "],
    }
    made = call(app, "POST", "/api/manual/typed", new)
    assert made["id"] == "typed/T001" and made["title"] == "Update my home address" and made["typed"]
    assert (made["step_count"], made["fields"]) == (2, ["Address Line 1"])
    listing = call(app, "GET", "/api/manual")
    assert listing["files"] == []  # typed scenarios are not an imported file
    [row] = listing["scenarios"]
    assert (row["module"], row["product"], row["values_missing"]) == ("HCM", "Global Human Resources", 0)
    opened = call(app, "GET", "/api/manual/scenario?id=typed/T001")
    assert [s["action"] for s in opened["cases"][0]["steps"]][1] == "Enter 10 Main Street in Address Line 1"

    changed = call(app, "POST", "/api/manual/typed", {**new, "id": "typed/T001", "title": "Change my address"})
    assert changed["id"] == "typed/T001" and changed["created_at"] == made["created_at"]
    second = call(app, "POST", "/api/manual/typed", new)
    call(app, "POST", "/api/manual/typed/delete", {"id": second["id"]})
    third = call(app, "POST", "/api/manual/typed", new)
    assert (second["id"], third["id"]) == ("typed/T002", "typed/T003")  # a deleted number is not used again
    assert [s["title"] for s in call(app, "GET", "/api/manual")["scenarios"]] == [
        "Change my address",
        "Update my home address",
    ]

    for bad, message in (
        ({**new, "title": " "}, "give the scenario a name"),
        ({**new, "product": ""}, "enter the module and product"),
        ({**new, "steps": [{"action": "", "expected": ""}]}, "at least one step"),
        ({**new, "steps": [{"action": "", "expected": "Saved"}]}, "what to do"),
        ({**new, "id": "typed/T999"}, "no longer there"),
    ):
        with pytest.raises(ApiError, match=message):
            app.handle("POST", "/api/manual/typed", json.dumps(bad).encode())
