"""Saved suites: a name for a group of tests, so "the smoke tests" or "everything in Payables" is one click.

A suite is one YAML file in the `_suites` folder of the tests folder. It does not hold tests, only a rule for picking
them, so a test added later that fits the rule joins the suite by itself:

    suite: payables-critical
    title: Payables, the ones that matter
    description: What we run first after every update.
    include:                          # a test is in if it fits ANY of these groups
      - tags: [smoke]
      - products: [Payables]          # inside a group, EVERY line must fit
        priorities: [critical, high]
      - tests: [hcm.view-worker]      # or name tests one by one
    exclude:                          # ...unless it fits ANY of these
      - tags: [flaky]

A line takes a list (or one word): `tags`, `folders` (a folder of the tests folder, with what is inside it), `modules`,
`products`, `priorities` and `tests` (test ids). A line fits when the test has ANY of the values. Capital letters do not
matter. A suite is worked out each time it is used, so the same suite on Monday and on Friday may have different tests.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SUITES_DIR = "_suites"
KEYS = ("tags", "folders", "modules", "products", "priorities", "tests")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
PRIORITIES = ("critical", "high", "medium", "low")


class SuiteError(ValueError):
    """A suite is wrong or missing. The message is for the person writing it."""


@dataclass
class Resolution:
    ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def read_suites(directory: Path | None) -> tuple[dict[str, tuple[Path, dict[str, Any]]], list[tuple[Path, str]]]:
    """(the readable suites by name, the files that could not be used with why)."""
    found: dict[str, tuple[Path, dict[str, Any]]] = {}
    problems: list[tuple[Path, str]] = []
    if directory is None:
        return found, problems
    for path in sorted(directory.rglob("*.y*ml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            problems.append((path, f"could not be read: {e}"))
            continue
        try:
            name = check_suite(raw)
        except SuiteError as e:
            problems.append((path, str(e)))
            continue
        if name in found:
            problems.append((path, f"the name '{name}' is also used by {found[name][0].name}"))
            continue
        found[name] = (path, raw)
    return found, problems


def check_suite(raw: object) -> str:
    """The suite's name, or SuiteError saying what is wrong."""
    if not isinstance(raw, dict):
        raise SuiteError("a suite is a YAML mapping with `suite:` and `include:`")
    name = raw.get("suite")
    if not isinstance(name, str) or not _NAME.match(name):
        raise SuiteError("`suite:` must be a name of letters, digits, dots, dashes and underscores")
    unknown = set(raw) - {"suite", "title", "description", "include", "exclude"}
    if unknown:
        raise SuiteError(f"unknown keys: {', '.join(sorted(unknown))}")
    include = raw.get("include")
    if not isinstance(include, list) or not include:
        raise SuiteError("`include:` must be a list with at least one group")
    for i, rule in enumerate(include, 1):
        rules_of(rule, f"include group {i}")
    exclude = raw.get("exclude") or []
    if not isinstance(exclude, list):
        raise SuiteError("`exclude:` must be a list of groups")
    for i, rule in enumerate(exclude, 1):
        rules_of(rule, f"exclude group {i}")
    return name


def rules_of(rule: object, where: str) -> dict[str, list[str]]:
    """One group as {line: [values]}, checked. A single word is the same as a list of one."""
    if not isinstance(rule, dict) or not rule:
        raise SuiteError(f"{where} must have at least one line such as `tags: [smoke]`")
    out: dict[str, list[str]] = {}
    for key, value in rule.items():
        if key not in KEYS:
            raise SuiteError(f"{where}: unknown line '{key}' (use {', '.join(KEYS)})")
        values = value if isinstance(value, list) else [value]
        if not values or not all(isinstance(v, str | int | float) and not isinstance(v, bool) for v in values):
            raise SuiteError(f"{where}: `{key}` must be a list of words")
        words = [str(v).strip() for v in values]
        if not all(words):
            raise SuiteError(f"{where}: `{key}` has an empty value")
        if key == "priorities":
            bad = [w for w in words if w.lower() not in PRIORITIES]
            if bad:
                raise SuiteError(f"{where}: '{bad[0]}' is not a priority (use {', '.join(PRIORITIES)})")
        out[str(key)] = words
    return out


def fits(rule: Mapping[str, Sequence[str]], test: Mapping[str, Any]) -> bool:
    """Whether a test fits every line of a group. `test` has id, tags, folder, module, product, priority."""
    for key, values in rule.items():
        wanted = {v.casefold() for v in values}
        if key == "tags":
            if not wanted & {str(t).casefold() for t in test.get("tags") or []}:
                return False
        elif key == "folders":
            folder = str(test.get("folder") or "").strip("/").casefold()
            if not any(folder == f.strip("/") or folder.startswith(f.strip("/") + "/") for f in wanted):
                return False
        elif key == "tests":
            if str(test.get("id") or "").casefold() not in wanted:
                return False
        else:  # modules, products, priorities: a single value on the test
            field_name = {"modules": "module", "products": "product", "priorities": "priority"}[key]
            if str(test.get(field_name) or "").casefold() not in wanted:
                return False
    return True


def resolve(raw: Mapping[str, Any], tests: Sequence[Mapping[str, Any]]) -> Resolution:
    """The ids of the tests in the suite, in the order of `tests`, with warnings about rules that find nothing."""
    include = [rules_of(r, f"include group {i}") for i, r in enumerate(raw.get("include") or [], 1)]
    exclude = [rules_of(r, f"exclude group {i}") for i, r in enumerate(raw.get("exclude") or [], 1)]
    usable = [t for t in tests if t.get("id") and not t.get("problem")]
    out = Resolution()
    for i, rule in enumerate(include, 1):
        hits = [t for t in usable if fits(rule, t)]
        if not hits:
            out.warnings.append(f"Group {i} matches no test.")
        if "tests" in rule:
            known = {str(t["id"]).casefold() for t in usable}
            gone = [v for v in rule["tests"] if v.casefold() not in known]
            if gone:
                out.warnings.append(f"No test has the id {', '.join(gone)}.")
    for test in usable:
        if any(fits(r, test) for r in include) and not any(fits(r, test) for r in exclude):
            out.ids.append(str(test["id"]))
    return out


def to_yaml(
    name: str, title: str, description: str, include: list[dict[str, Any]], exclude: list[dict[str, Any]]
) -> str:
    """The text of a suite file, as the page writes it."""
    body: dict[str, Any] = {"suite": name, "title": title}
    if description:
        body["description"] = description
    body["include"] = include
    if exclude:
        body["exclude"] = exclude
    text = yaml.safe_dump(body, sort_keys=False, allow_unicode=True, default_flow_style=None, width=100)
    return "# A saved suite: the tests that fit these groups. Edit it here or on the Suites page.\n" + text
