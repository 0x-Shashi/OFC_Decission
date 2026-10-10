"""Importing release feature lists: Oracle's feature spreadsheet, CSV, and Quartermaster's own format."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from quartermaster.importers.release_sheet import ImportError_, parse_release
from quartermaster.importers.xlsx import SpreadsheetError, read_workbook

EXAMPLES = Path(__file__).parent.parent / "examples"


def make_xlsx(sheets: dict[str, list[list[str]]]) -> bytes:
    """A minimal workbook, as Excel writes it: shared strings for text, numbers as values."""
    strings: list[str] = []

    def cell(ref: str, value: str) -> str:
        if value.replace(".", "", 1).isdigit():
            return f'<c r="{ref}"><v>{value}</v></c>'
        if value not in strings:
            strings.append(value)
        return f'<c r="{ref}" t="s"><v>{strings.index(value)}</v></c>'

    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel_ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        book, rels = [], []
        for n, (name, rows) in enumerate(sheets.items(), start=1):
            xml_rows = "".join(
                f'<row r="{r}">' + "".join(cell(f"{chr(65 + c)}{r}", v) for c, v in enumerate(row) if v) + "</row>"
                for r, row in enumerate(rows, start=1)
            )
            z.writestr(f"xl/worksheets/sheet{n}.xml", f"<worksheet {ns}><sheetData>{xml_rows}</sheetData></worksheet>")
            book.append(f'<sheet name="{escape(name)}" sheetId="{n}" r:id="rId{n}"/>')
            rels.append(f'<Relationship Id="rId{n}" Target="worksheets/sheet{n}.xml" Type="worksheet"/>')
        z.writestr("xl/workbook.xml", f"<workbook {ns} {rel_ns}><sheets>{''.join(book)}</sheets></workbook>")
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels)
            + "</Relationships>",
        )
        z.writestr(
            "xl/sharedStrings.xml",
            f"<sst {ns}>" + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>",
        )
    return out.getvalue()


ORACLE_ROWS = [
    ["Oracle Fusion Cloud Applications 26C - Feature Listing"],
    [],
    ["Update", "Product Family", "Product", "Feature", "Customer Must Take Action to Use", "UI or Process-Based"]
    + ["Redwood"],
    ["26C", "Human Capital Management", "Global Human Resources", "Redwood Hire an Employee", "Yes", "UI", "Y"],
    ["26C", "Enterprise Resource Planning", "Payables", "Invoice approval by line", "", "Process", ""],
    ["26B", "Human Capital Management", "Absence Management", "Old feature from last update", "", "UI", ""],
    ["", "", "", "", "", "", ""],
    ["26C", "", "", "No product here", "", "", ""],
]


def test_reads_an_oracle_feature_spreadsheet() -> None:
    book = make_xlsx({"Cover": [["Read me first"]], "Features": ORACLE_ROWS})
    assert read_workbook(book)["Features"][1][3] == "Feature"  # empty rows are left out
    result = parse_release("26C features.xlsx", book, "26c")
    release = result.release
    assert release["id"] == "26C"
    assert [f["title"] for f in release["features"]] == ["Redwood Hire an Employee", "Invoice approval by line"]
    hire, invoice = release["features"]
    assert hire == {
        "id": "26C-001",
        "module": "HCM",
        "product": "Global Human Resources",
        "title": "Redwood Hire an Employee",
        "description": "",
        "change_type": "UI",
        "opt_in": True,
        "customer_action_required": True,
        "tags": ["redwood"],
    }
    assert invoice["module"] == "Financials" and invoice["change_type"] == "PROCESS" and not invoice["opt_in"]
    assert result.columns["title"] == "Feature" and result.columns["module"] == "Product Family"
    assert any("26B" in s for s in result.skipped) and any("no product" in s for s in result.skipped)


def test_reads_a_csv_with_its_own_ids() -> None:
    csv = (
        b"\xef\xbb\xbfID,Module,Product,Title,Description,Tags\n"
        b"X-1,HCM,Absence Management,Absence approvals,Approve absences,absence; approval\n"
    )
    release = parse_release("list.csv", csv, "26D").release
    assert release["features"][0]["id"] == "X-1"
    assert release["features"][0]["tags"] == ["absence", "approval"]


def test_reads_quartermaster_release_files() -> None:
    result = parse_release("26D_sample.json", (EXAMPLES / "releases" / "26D_sample.json").read_bytes())
    assert result.release["id"] == "26D" and len(result.release["features"]) == result.source_rows > 0


@pytest.mark.parametrize(
    ("name", "content", "release_id", "message"),
    [
        ("a.xlsx", make_xlsx({"S": ORACLE_ROWS}), "", "release id"),
        ("a.csv", b"Name,Other\nx,y\n", "26C", "no features"),  # a title column, but no product
        ("a.csv", b"Colour,Size\nred,big\n", "26C", "no column called Feature"),
        ("a.pdf", b"%PDF", "26C", "use an .xlsx"),
        ("a.json", b"[1, 2]", "", "expected a release"),
    ],
)
def test_explains_what_is_wrong(name: str, content: bytes, release_id: str, message: str) -> None:
    with pytest.raises(ImportError_, match=message):
        parse_release(name, content, release_id)


def test_a_file_that_is_not_a_workbook() -> None:
    with pytest.raises(SpreadsheetError):
        parse_release("a.xlsx", b"not a zip", "26C")
