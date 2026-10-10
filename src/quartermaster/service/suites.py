"""Saved suites for the pages and the run queue: list them, save and delete them, preview a rule, and work out the
tests a suite has right now. The rules themselves are in dsl/suites.py."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from quartermaster.dsl.suites import (
    KEYS,
    SUITES_DIR,
    SuiteError,
    check_suite,
    read_suites,
    resolve,
    rules_of,
    to_yaml,
)
from quartermaster.service import atomic

_SLUG = re.compile(r"[^a-z0-9]+")


def suite_items(tests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The facts about each test that a suite looks at (from the test list of the web service)."""
    keep = ("id", "title", "file", "folder", "module", "product", "priority", "tags", "problem")
    return [{k: t.get(k) for k in keep} for t in tests]


def slug(text: str) -> str:
    return _SLUG.sub("-", text.casefold()).strip("-")[:60]


def listing(tests_root: Path, tests: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything the Suites page shows."""
    directory = tests_root / SUITES_DIR
    found, problems = read_suites(directory if directory.is_dir() else None)
    items = suite_items(tests)
    titles = {str(t["id"]): str(t.get("title") or t["id"]) for t in items if t.get("id")}
    out = []
    for name, (path, raw) in sorted(found.items(), key=lambda kv: str(kv[1][1].get("title") or kv[0]).casefold()):
        got = resolve(raw, items)
        include = [rules_of(r, "include") for r in raw["include"]]
        exclude = [rules_of(r, "exclude") for r in raw.get("exclude") or []]
        out.append(
            {
                "name": name,
                "title": str(raw.get("title") or name),
                "description": str(raw.get("description") or ""),
                "file": path.relative_to(tests_root).as_posix(),
                "include": include,
                "exclude": exclude,
                "editable": _editable(include, exclude),
                "tests": [{"id": i, "title": titles.get(i, i)} for i in got.ids],
                "warnings": got.warnings,
                "yaml": path.read_text(encoding="utf-8"),
            }
        )
    return {
        "folder": f"{tests_root.name}/{SUITES_DIR}",
        "suites": out,
        "problems": [{"file": p.relative_to(tests_root).as_posix(), "problem": why[:300]} for p, why in problems],
        "choices": _choices(items),
    }


def _editable(include: list[dict[str, list[str]]], exclude: list[dict[str, list[str]]]) -> bool:
    """The page can edit a suite written the way it writes them: include groups of lines (or only named tests), and
    leave out only by tag or by named test. Anything else is edited in the file."""
    keeps = all("tests" not in g or set(g) == {"tests"} for g in include)
    drops = all(set(g) in ({"tags"}, {"tests"}) for g in exclude)
    return keeps and drops


def _choices(items: list[dict[str, Any]]) -> dict[str, list[str]]:
    ok = [t for t in items if t.get("id") and not t.get("problem")]

    def distinct(values: Any) -> list[str]:
        first: dict[str, str] = {}  # "Smoke" and "smoke" are one choice, as a suite sees them
        for v in values:
            if v:
                first.setdefault(str(v).casefold(), str(v))
        return sorted(first.values(), key=str.casefold)

    return {
        "tags": distinct(tag for t in ok for tag in t.get("tags") or []),
        "folders": distinct(t.get("folder") for t in ok),
        "modules": distinct(t.get("module") for t in ok),
        "products": distinct(t.get("product") for t in ok),
        "priorities": ["critical", "high", "medium", "low"],
    }


def preview(data: dict[str, Any], tests: list[dict[str, Any]]) -> dict[str, Any]:
    """The tests a rule picks now, before it is saved."""
    raw = {"suite": "preview", "include": data.get("include") or [], "exclude": data.get("exclude") or []}
    check_suite(raw)
    items = suite_items(tests)
    got = resolve(raw, items)
    titles = {str(t["id"]): str(t.get("title") or t["id"]) for t in items if t.get("id")}
    return {"tests": [{"id": i, "title": titles.get(i, i)} for i in got.ids], "warnings": got.warnings}


def save(tests_root: Path, data: dict[str, Any], tests: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a suite, or change the one with this name. The file is written all at once."""
    title = " ".join(str(data.get("title") or "").split())[:80]
    if not title:
        raise SuiteError("give the suite a name")
    old = str(data.get("name") or "").strip()
    name = old or slug(title)
    if not name:
        raise SuiteError("the name needs some letters or digits")
    directory = tests_root / SUITES_DIR
    found, _ = read_suites(directory if directory.is_dir() else None)
    if not old and name in found:
        raise SuiteError(f"there is already a suite called '{name}': change that one, or use another name")
    include = _groups(data.get("include"), "include")
    exclude = _groups(data.get("exclude"), "exclude")
    description = " ".join(str(data.get("description") or "").split())[:300]
    text = to_yaml(name, title, description, include, exclude)
    import yaml

    check_suite(yaml.safe_load(text))
    path = found[old][0] if old and old in found else directory / f"{name}.yaml"
    atomic.write_text(path, text)
    items = suite_items(tests)
    got = resolve(yaml.safe_load(text), items)
    return {"name": name, "title": title, "tests": len(got.ids), "warnings": got.warnings}


def _groups(value: object, where: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SuiteError(f"`{where}` must be a list of groups")
    out = []
    for i, rule in enumerate(value, 1):
        checked = rules_of(rule, f"{where} group {i}")
        out.append({k: checked[k] for k in KEYS if k in checked})
    return out


def delete(tests_root: Path, name: str) -> Path:
    directory = tests_root / SUITES_DIR
    found, _ = read_suites(directory if directory.is_dir() else None)
    if name not in found:
        raise LookupError(f"no suite called '{name}'")
    path = found[name][0]
    path.unlink()
    return path


def tests_of(tests_root: Path, name: str, tests: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """(the suite's title, its test ids right now). Raises SuiteError when it is missing or has no test."""
    directory = tests_root / SUITES_DIR
    found, _ = read_suites(directory if directory.is_dir() else None)
    if name not in found:
        known = ", ".join(sorted(found)) or "none yet"
        raise SuiteError(f"no suite called '{name}' (there is: {known})")
    raw = found[name][1]
    got = resolve(raw, suite_items(tests))
    title = str(raw.get("title") or name)
    if not got.ids:
        raise SuiteError(f"the suite '{title}' has no tests right now")
    return title, got.ids
