"""Read the cell text of an .xlsx workbook with the standard library only.

An .xlsx file is a zip of XML parts. This reads every sheet's cells as text (shared strings,
inline strings and plain values; formulas give their last calculated value) and is enough for
spreadsheets of features or test cases. It does not evaluate formulas or read formatting.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

_M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CELL = re.compile(r"^([A-Z]+)")


class SpreadsheetError(ValueError):
    """The file is not a readable .xlsx workbook."""


def _column(ref: str) -> int:
    m = _CELL.match(ref)
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_workbook(source: Path | bytes) -> dict[str, list[list[str]]]:
    """Every sheet, by name, as rows of cell text (trailing empty cells dropped, empty rows skipped)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source)
        names = set(z.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{{{_M}}}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_M}}}t")))
        book = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise SpreadsheetError(f"not a readable .xlsx workbook ({e})") from e
    targets = {r.get("Id"): r.get("Target", "") for r in rels}
    sheets: dict[str, list[list[str]]] = {}
    for sheet in book.iter(f"{{{_M}}}sheet"):
        target = targets.get(sheet.get(f"{{{_R}}}id"), "").lstrip("/")
        part = target if target.startswith("xl/") else f"xl/{target}"
        if part not in names:
            continue
        rows: list[list[str]] = []
        for row in ET.fromstring(z.read(part)).iter(f"{{{_M}}}row"):
            cells: dict[int, str] = {}
            for c in row.findall(f"{{{_M}}}c"):
                kind, v = c.get("t"), c.find(f"{{{_M}}}v")
                if kind == "s" and v is not None and v.text is not None:
                    text = shared[int(v.text)]
                elif kind == "inlineStr":
                    text = "".join(t.text or "" for t in c.iter(f"{{{_M}}}t"))
                else:
                    text = v.text if v is not None and v.text is not None else ""
                if text.strip():
                    cells[_column(c.get("r", "A1"))] = text.strip()
            if cells:
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        sheets[sheet.get("name", f"Sheet{len(sheets) + 1}")] = rows
    return sheets
