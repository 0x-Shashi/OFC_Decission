"""Read manual test scripts from Excel into scenarios Quartermaster can list and match to releases.

Two layouts are understood, both found by their header names:

- Scenario workbooks: a "Test Scenarios" sheet (Use Case ID, Scenario ID, Test Scenario, Tester,
  Test Status) and a "Test Cases" sheet (Scenario ID, Test case ID, Test case Name, Test case
  description, Test step no., Test step description, Expected result). Ids are written once and
  left blank on the rows below, so each blank id means "same as above".
- Action sheets, such as an Employee Self Service list: one row per action with Action Name,
  Action Purpose, Reference Number and Primary Navigation (plus Tester Name, Testing Time and
  Pass / Fail when filled in).

The module and product come from the file name (for example "Account payable" is Financials,
Payables) and can be overridden when importing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from quartermaster.importers.xlsx import read_workbook

# File name words -> (module, product). The first match wins, so specific names come first.
PRODUCTS: list[tuple[str, str, str]] = [
    (r"payroll interface", "HCM", "Payroll Interface"),
    (r"payroll", "HCM", "Global Payroll"),
    (r"recruit", "HCM", "Recruiting"),
    (r"talent review|succession", "HCM", "Talent Management"),
    (r"absence", "HCM", "Absence Management"),
    (r"benefit", "HCM", "Benefits"),
    (r"career|goal", "HCM", "Career and Goal Management"),
    (r"compensation", "HCM", "Compensation"),
    (r"performance", "HCM", "Performance Management"),
    (r"time and labor|time & labor", "HCM", "Time and Labor"),
    (r"core hr", "HCM", "Global Human Resources"),
    (r"self.?service procurement", "Procurement", "Self Service Procurement"),
    (r"supplier portal", "Procurement", "Supplier Portal"),
    (r"procurement|purchas", "Procurement", "Purchasing"),
    (r"\bess\b|employee self.?service|self.?service", "HCM", "Global Human Resources"),
    (r"payable", "Financials", "Payables"),
    (r"receivable|\bar\b", "Financials", "Receivables"),
    (r"general ledger|\bgl\b", "Financials", "General Ledger"),
    (r"expense", "Financials", "Expenses"),
    (r"cash management", "Financials", "Cash Management"),
    (r"\bppm\b|project", "Project Management", "Project Management"),
]
_RESULT = {"pass": "passed", "passed": "passed", "fail": "failed", "failed": "failed"}
_RELEASE = re.compile(r"\b(\d{2})\s?([A-D])\b")


class ScriptError(ValueError):
    """The workbook is not in a layout Quartermaster understands."""


@dataclass
class ScriptImport:
    file: str
    kind: str  # "scenarios" or "actions"
    module: str
    product: str
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def guess_product(name: str) -> tuple[str, str]:
    words = _norm(Path(name).stem.replace("_", " ").replace("-", " "))
    for pattern, module, product in PRODUCTS:
        if re.search(pattern, words):
            return module, product
    return "", ""


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "scripts"


def _columns(row: list[str], wanted: dict[str, tuple[str, ...]]) -> dict[str, int]:
    found: dict[str, int] = {}
    for i, cell in enumerate(row):
        key = _norm(cell)
        for name, options in wanted.items():
            if name not in found and key in options:
                found[name] = i
    return found


def _header(rows: list[list[str]], wanted: dict[str, tuple[str, ...]], need: str) -> tuple[int, dict[str, int]]:
    for i, row in enumerate(rows[:20]):
        cols = _columns(row, wanted)
        if need in cols and len(cols) >= 3:
            return i, cols
    return -1, {}


def _cell(row: list[str], cols: dict[str, int], name: str) -> str:
    i = cols.get(name)
    return row[i].strip() if i is not None and i < len(row) else ""


def _ref(text: str) -> str:
    """Scenario ids written TS10, TS010 or TS0010 are the same scenario: TS010."""
    m = re.fullmatch(r"([A-Z]*)0*(\d+)", text.strip().upper())
    return f"{m.group(1)}{int(m.group(2)):03d}" if m else text.strip().upper()


def _result(text: str) -> str:
    return _RESULT.get(_norm(text), "")


SCENARIO_COLS = {
    "use_case": ("use case id req id", "use case id", "req id", "requirement id"),
    "scenario": ("scenario id", "test scenario id"),
    "title": ("test scenario", "scenario", "scenario name", "test scenario name"),
    "cases": ("no of test cases", "number of test cases"),
    "tester": ("tester", "tester name"),
    "status": ("test status", "status"),
    "notes": ("notes", "comments"),
}
CASE_COLS = {
    "scenario": ("scenario id", "test scenario id"),
    "case": ("test case id", "case id"),
    "name": ("test case name", "case name"),
    "description": ("test case description", "case description"),
    "step": ("test step no", "step no", "step number", "step"),
    "action": ("test step description", "step description", "action"),
    "expected": ("expected result", "expected"),
    "status": ("status", "pass fail"),
}
ACTION_COLS = {
    "name": ("action name", "action"),
    "purpose": ("action purpose", "purpose"),
    "ref": ("reference number", "reference", "ref"),
    "fields": ("fields that are available to employees for view edits", "fields"),
    "navigation": ("primary navigation", "navigation"),
    "tester": ("tester name", "tester"),
    "minutes": ("testing time", "time"),
    "status": ("pass fail", "result", "status"),
}


def parse_scripts(name: str, content: bytes, module: str = "", product: str = "") -> ScriptImport:
    """`name` is the file name; module and product override what the name suggests."""
    if Path(name).suffix.lower() not in (".xlsx", ".xlsm"):
        raise ScriptError("use an Excel workbook (.xlsx)")
    sheets = read_workbook(content)
    guessed = guess_product(name)
    module, product = module.strip() or guessed[0], product.strip() or guessed[1]
    by_name = {_norm(k): v for k, v in sheets.items()}
    cases_sheet = next((v for k, v in by_name.items() if "case" in k), None)
    if cases_sheet is not None and _header(cases_sheet, CASE_COLS, "case")[0] >= 0:
        scenarios_sheet = next((v for k, v in by_name.items() if "scenario" in k), [])
        result = ScriptImport(Path(name).name, "scenarios", module, product)
        _read_scenarios(result, scenarios_sheet, cases_sheet)
    else:
        sheet = next((rows for rows in sheets.values() if _header(rows, ACTION_COLS, "ref")[0] >= 0), None)
        if sheet is None:
            raise ScriptError(
                "no sheet with Test case ID (scenario workbook) or Reference Number (action list) was found"
            )
        result = ScriptImport(Path(name).name, "actions", module, product)
        _read_actions(result, sheet)
    if not result.scenarios:
        raise ScriptError("the workbook has the right columns but no scenarios under them")
    if not (module and product):
        result.warnings.append("the module and product could not be told from the file name; enter them")
    prefix = slug(product or Path(name).stem)
    for s in result.scenarios:
        s.update(id=f"{prefix}.{s['ref']}", file=result.file, module=module, product=product)
    return result


def _read_scenarios(result: ScriptImport, scen_rows: list[list[str]], case_rows: list[list[str]]) -> None:
    at, cols = _header(scen_rows, SCENARIO_COLS, "scenario")
    scenarios: dict[str, dict[str, Any]] = {}
    use_case = ""
    for row in scen_rows[at + 1 :] if at >= 0 else []:
        ref = _ref(_cell(row, cols, "scenario"))
        use_case = _cell(row, cols, "use_case") or use_case
        if not re.match(r"^[A-Z]*\d+$", ref):
            continue
        scenarios[ref] = _scenario(ref, _cell(row, cols, "title"), use_case)
        scenarios[ref].update(tester=_cell(row, cols, "tester"), status=_result(_cell(row, cols, "status")))

    at, cols = _header(case_rows, CASE_COLS, "case")
    unlisted: list[str] = []
    scen_ref = ""
    current: dict[str, Any] | None = None
    for row in case_rows[at + 1 :]:
        ref = _ref(_cell(row, cols, "scenario"))
        if ref:
            scen_ref, current = ref, None
        if not scen_ref:
            continue
        if scen_ref not in scenarios:
            scenarios[scen_ref] = _scenario(scen_ref, "", "")
            unlisted.append(scen_ref)
        case_id = _cell(row, cols, "case").upper()
        if case_id:
            current = {
                "id": case_id,
                "name": _cell(row, cols, "name"),
                "description": _cell(row, cols, "description"),
                "precondition": "",
                "steps": [],
            }
            scenarios[scen_ref]["cases"].append(current)
        if current is None:
            continue
        step, action, expected = _cell(row, cols, "step"), _cell(row, cols, "action"), _cell(row, cols, "expected")
        if _norm(step).startswith("pre"):
            current["precondition"] = action
        elif action or expected:
            current["steps"].append(
                {"step": step, "action": action, "expected": expected, "result": _result(_cell(row, cols, "status"))}
            )
    empty = [s["ref"] for s in scenarios.values() if not s["cases"]]
    if unlisted:
        result.warnings.append(
            f"{_some(unlisted)} {'has' if len(unlisted) == 1 else 'have'} test cases but "
            f"{'is' if len(unlisted) == 1 else 'are'} not listed in the Test Scenarios sheet "
            "(named after the first test case instead)"
        )
    if empty:
        verb = "is" if len(empty) == 1 else "are"
        result.warnings.append(f"{_some(empty)} {verb} listed but no test cases were found")
    for s in scenarios.values():
        if not s["title"]:
            s["title"] = s["cases"][0]["name"] if s["cases"] else s["ref"]
        _finish(s)
        result.scenarios.append(s)


def _read_actions(result: ScriptImport, rows: list[list[str]]) -> None:
    at, cols = _header(rows, ACTION_COLS, "ref")
    above = rows[at - 1] if at > 0 else []
    releases = {i: f"{m.group(1)}{m.group(2)}" for i, c in enumerate(above) if (m := _RELEASE.search(c.upper()))}
    for row in rows[at + 1 :]:
        ref, title = _cell(row, cols, "ref").upper(), _cell(row, cols, "name")
        if not title or not re.match(r"^[A-Z]+-?\d+$", ref):
            continue  # an explanation row or a blank line
        nav = [ln.lstrip("> ").strip() for ln in _cell(row, cols, "navigation").splitlines() if ln.strip("> ").strip()]
        s = _scenario(ref, title, "")
        s["description"] = _cell(row, cols, "purpose")
        s["cases"].append(
            {
                "id": ref,
                "name": title,
                "description": s["description"],
                "precondition": "",
                "steps": [
                    {"step": f"step {n}", "action": a, "expected": "", "result": ""} for n, a in enumerate(nav, start=1)
                ],
            }
        )
        s["fields"] = [ln.lstrip("> ").strip() for ln in _cell(row, cols, "fields").splitlines() if ln.strip("> ")]
        s.update(tester=_cell(row, cols, "tester"), status=_result(_cell(row, cols, "status")))
        minutes = _cell(row, cols, "minutes")
        s["minutes"] = float(minutes) if re.fullmatch(r"\d+(\.\d+)?", minutes) else None
        s["releases"] = sorted(r for i, r in releases.items() if i < len(row) and _norm(row[i]) in ("y", "yes"))
        _finish(s)
        result.scenarios.append(s)


def new_scenario(ref: str, title: str, use_case: str) -> dict[str, Any]:
    """An empty scenario, as read from a workbook (also used for scenarios typed in Quartermaster)."""
    return _scenario(ref, title, use_case)


def finish_scenario(s: dict[str, Any]) -> None:
    """Count a scenario's cases, steps, blank test data and sign-ins."""
    _finish(s)


def _some(refs: list[str]) -> str:
    return ", ".join(refs) if len(refs) <= 4 else f"{', '.join(refs[:3])} and {len(refs) - 3} more"


def _scenario(ref: str, title: str, use_case: str) -> dict[str, Any]:
    return {
        "ref": ref,
        "title": " ".join(title.split()),
        "use_case": use_case,
        "description": "",
        "cases": [],
        "tester": "",
        "status": "",
        "minutes": None,
        "releases": [],
    }


def _finish(s: dict[str, Any]) -> None:
    steps = [st for c in s["cases"] for st in c["steps"]]
    s["case_count"] = len(s["cases"])
    s["step_count"] = len(steps)
    # "<>" or "< >" is a placeholder for test data nobody has filled in yet
    s["blank_data"] = any(re.search(r"<\s*>", f"{st['action']} {st['expected']}") for st in steps)
    s["logins"] = sum(bool(re.search(r"\blog ?in\b|\blaunch\b", st["action"].lower())) for st in steps)
