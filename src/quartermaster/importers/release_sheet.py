"""Turn a release feature list (Oracle's Readiness feature-listing spreadsheet, a CSV, or
Quartermaster's own JSON/YAML) into a release file `qm plan` and the web UI can use.

Columns are found by their header names, so the order does not matter and extra columns are
ignored. The result says which column was used for what, and which rows were skipped, so a
person can check the import instead of trusting it.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from quartermaster.importers.xlsx import read_workbook

RELEASE_ID = re.compile(r"^\d{2}[A-D]$")

# What each field may be called in a spreadsheet header (compared without case or punctuation).
HEADERS: dict[str, tuple[str, ...]] = {
    "title": ("feature", "feature name", "feature title", "title", "name"),
    "description": ("description", "feature description", "summary", "details", "business benefit"),
    "product": ("product", "product name", "offering", "application", "app"),
    "module": ("module", "pillar", "product family", "family", "product area", "area"),
    "id": ("id", "feature id", "reference", "ref", "feature number", "key"),
    "update": ("update", "release", "version", "quarterly update"),
    "action": (
        "customer action required",
        "customer must take action",
        "customer must take action to use",
        "customer must take action to use disabled by default",
        "opt in",
        "optin",
        "disabled by default",
        "requires setup",
        "setup required",
        "action required",
    ),
    "ready": ("ready for use", "ready for use by end users", "enabled by default"),
    "change_type": ("change type", "ui or process", "ui or process based", "type", "impact type"),
    "tags": ("tags", "keywords", "labels"),
    "redwood": ("redwood",),
}
_TRUE = {"y", "yes", "x", "true", "1", "✓", "✔", "●", "•", "checked", "required"}
# Oracle's pillar names, shortened to the module names tests use.
_MODULES = {
    "human capital management": "HCM",
    "hcm": "HCM",
    "human resources": "HCM",
    "enterprise resource planning": "Financials",
    "erp": "Financials",
    "financials": "Financials",
    "procurement": "Procurement",
    "project management": "Project Management",
    "supply chain management": "SCM",
    "supply chain and manufacturing": "SCM",
    "scm": "SCM",
}


class ImportError_(ValueError):  # noqa: N801 - "ImportError" is a Python built-in
    """The file could not be turned into a release."""


@dataclass
class ReleaseImport:
    release: dict[str, Any]
    columns: dict[str, str] = field(default_factory=dict)  # field -> header used
    skipped: list[str] = field(default_factory=list)  # why rows were left out
    source_rows: int = 0


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _truthy(value: str) -> bool:
    return _norm(value) in _TRUE or value.strip() in _TRUE


def _change_type(text: str) -> str:
    t = text.lower()
    if "api" in t or "rest" in t or "service" in t:
        return "API"
    if "report" in t or "analytic" in t:
        return "REPORT"
    if "process" in t and "ui" in t:
        return "BOTH"
    if "process" in t:
        return "PROCESS"
    return "UI"


def parse_release(name: str, content: bytes, release_id: str = "") -> ReleaseImport:
    """`name` is the file name (its extension picks the format); `release_id` is needed for spreadsheets."""
    suffix = Path(name).suffix.lower()
    if suffix in (".json", ".yaml", ".yml"):
        return _own_format(content, suffix, release_id)
    if suffix == ".csv":
        text = content.decode("utf-8-sig", errors="replace")
        return _from_rows([row for row in csv.reader(io.StringIO(text)) if any(c.strip() for c in row)], release_id)
    if suffix in (".xlsx", ".xlsm"):
        sheets = read_workbook(content)
        best = max(sheets.values(), key=lambda rows: _header_score(_find_header(rows)[1]), default=[])
        return _from_rows(best, release_id)
    if suffix in (".html", ".htm", ".txt", ".md"):  # a saved What's New page, or text copied from it
        from quartermaster.importers.whats_new import parse_whats_new

        return parse_whats_new(name, content, release_id)
    raise ImportError_("use an .xlsx, .csv, .json, .yaml, .html (a saved What's New page) or .txt file")


def _own_format(content: bytes, suffix: str, release_id: str) -> ReleaseImport:
    try:
        data = json.loads(content) if suffix == ".json" else yaml.safe_load(content)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise ImportError_(f"the file could not be read: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        raise ImportError_("expected a release with an id and a list of features")
    rid = str(data.get("id") or release_id).strip().upper()
    if not RELEASE_ID.match(rid):
        raise ImportError_("the release id must look like 26C (two digits and A to D)")
    return ReleaseImport({"id": rid, "features": data["features"]}, source_rows=len(data["features"]))


def _find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    """The first row (of the first 20) that names a feature column, mapped field -> column index."""
    best: tuple[int, dict[str, int]] = (-1, {})
    for i, row in enumerate(rows[:20]):
        found: dict[str, int] = {}
        for col, cell in enumerate(row):
            key = _norm(cell)
            for fieldname, names in HEADERS.items():
                if fieldname not in found and key in names:
                    found[fieldname] = col
        if "title" in found and _header_score(found) > _header_score(best[1]):
            best = (i, found)
    return best


def _header_score(found: dict[str, int]) -> int:
    return len(found) + (5 if "title" in found else 0)


def _from_rows(rows: list[list[str]], release_id: str) -> ReleaseImport:
    rid = release_id.strip().upper()
    if not RELEASE_ID.match(rid):
        raise ImportError_("enter the release id this list is for, like 26C (two digits and A to D)")
    header_at, cols = _find_header(rows)
    if header_at < 0:
        raise ImportError_("no column called Feature (or Title) was found in the first 20 rows")
    header = rows[header_at]

    def get(row: list[str], f: str) -> str:
        return row[cols[f]].strip() if f in cols and cols[f] < len(row) else ""

    features: list[dict[str, Any]] = []
    skipped: list[str] = []
    for n, row in enumerate(rows[header_at + 1 :], start=header_at + 2):
        title = " ".join(get(row, "title").split())
        if not title:
            continue  # a blank or section row
        update = get(row, "update").upper()
        if update and rid not in update:
            skipped.append(f"row {n}: for update {update}, not {rid}")
            continue
        product = get(row, "product") or get(row, "module")
        module_raw = get(row, "module") or product
        module = _MODULES.get(_norm(module_raw), module_raw)
        if not product:
            skipped.append(f"row {n}: no product for '{title[:60]}'")
            continue
        action = _truthy(get(row, "action")) or (bool(get(row, "ready")) and not _truthy(get(row, "ready")))
        tags = [t.strip() for t in re.split(r"[,;]", get(row, "tags")) if t.strip()]
        if _truthy(get(row, "redwood")):
            tags.append("redwood")
        features.append(
            {
                "id": get(row, "id") or f"{rid}-{len(features) + 1:03d}",
                "module": module,
                "product": product,
                "title": title,
                "description": " ".join(get(row, "description").split()),
                "change_type": _change_type(get(row, "change_type")),
                "opt_in": action,
                "customer_action_required": action,
                "tags": tags,
            }
        )
    if not features:
        raise ImportError_("no features were found under the header row")
    used = {f: header[c] for f, c in cols.items()}
    return ReleaseImport({"id": rid, "features": features}, used, skipped, len(rows) - header_at - 1)
