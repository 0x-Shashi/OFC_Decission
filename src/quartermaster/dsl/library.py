"""Shared steps: a group of steps written once and used by many tests.

A shared group is one YAML file in the `_library` folder of the tests folder:

    library: open-locations        # its name; tests refer to it by this
    title: Open the Locations page
    params:                        # optional: what a test may hand in (null = the test must give it)
      page_name: Locations
    steps:                         # the same kind of steps a test has
      - action: navigate
        intent: Open ${page_name}
        value: Workforce Structures > ${page_name}
    cleanup:                       # optional: added to the cleanup of every test that uses the group
      - ...

A test uses it with a step that has only `use` (and, if wanted, `with`):

    steps:
      - use: open-locations
        with: {page_name: Locations}

When a test is loaded, that step is replaced by the group's steps. Everything after that (the runner, the
evidence, healing) sees ordinary steps, so shared steps behave exactly like steps written in the test.

- `${param}` in a group is replaced by what the test passed in `with` (or the group's default).
- Any other `${name}` in a group is test data: the test using the group must have that name in its `data`.
- A group cannot use another group (no loops, nothing hidden).
- The `_library` folder (like `_data` and `_suites`) is not a place for tests: test discovery skips it.

Each expanded step remembers its group (`shared`), and `expand` also returns where every step physically lives
(its file and its number there), so a suggested fix to a shared step is written to the shared file.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, cast

import yaml

from quartermaster.dsl.data import DATA_DIR
from quartermaster.dsl.suites import SUITES_DIR

LIBRARY_DIR = "_library"
_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_PARAM = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class LibraryError(ValueError):
    """A shared group is missing, wrong or used wrongly. The message is for the person writing the test."""


def files_of_tests(root: Path) -> list[Path]:
    """Every test file under `root`, in a fixed order, without the shared groups, the data sets and the saved suites."""
    skipped = {LIBRARY_DIR, DATA_DIR, SUITES_DIR}
    return sorted(p for p in root.rglob("*.y*ml") if not skipped & set(p.relative_to(root).parts[:-1]))


def library_dir_for(path: Path) -> Path | None:
    """The `_library` folder that belongs to a test file: the nearest one in its folder or above."""
    for folder in path.resolve().parents:
        candidate = folder / LIBRARY_DIR
        if candidate.is_dir():
            return candidate
    return None


def read_groups(directory: Path | None) -> tuple[dict[str, tuple[Path, dict[str, Any]]], list[tuple[Path, str]]]:
    """(the readable groups by name, the files that could not be used with why)."""
    groups: dict[str, tuple[Path, dict[str, Any]]] = {}
    problems: list[tuple[Path, str]] = []
    if directory is None:
        return groups, problems
    for path in sorted(directory.rglob("*.y*ml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            problems.append((path, f"could not be read: {e}"))
            continue
        try:
            name = check_group(raw)
        except LibraryError as e:
            problems.append((path, str(e)))
            continue
        if name in groups:
            problems.append((path, f"the name '{name}' is also used by {groups[name][0].name}"))
            continue
        groups[name] = (path, raw)
    return groups, problems


def check_group(raw: object) -> str:
    """The group's name, or LibraryError saying what is wrong with it."""
    if not isinstance(raw, dict):
        raise LibraryError("a shared group is a YAML mapping with `library:` and `steps:`")
    name = raw.get("library")
    if not isinstance(name, str) or not _NAME.match(name):
        raise LibraryError("`library:` must be a name of letters, digits, dots, dashes and underscores")
    unknown = set(raw) - {"library", "title", "description", "params", "steps", "cleanup"}
    if unknown:
        raise LibraryError(f"unknown keys: {', '.join(sorted(unknown))}")
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps or not all(isinstance(s, dict) for s in steps):
        raise LibraryError("`steps:` must be a list of steps")
    cleanup = raw.get("cleanup") or []
    if not isinstance(cleanup, list) or not all(isinstance(s, dict) for s in cleanup):
        raise LibraryError("`cleanup:` must be a list of steps")
    if any("use" in s for s in [*steps, *cleanup]):
        raise LibraryError("a shared group cannot use another group")
    params = raw.get("params") or {}
    if not isinstance(params, dict) or not all(_PARAM.fullmatch("${" + str(k) + "}") for k in params):
        raise LibraryError("`params:` must be a mapping of names to default values (or empty for 'required')")
    return name


def expand(raw: Any, path: Path) -> tuple[Any, list[tuple[str | None, int]]]:
    """The test with every `use` step replaced by its group's steps.

    Returns (the expanded spec, one (group name or None, number in its own file) per step). A spec
    without `use` comes back unchanged. Raises LibraryError."""
    if not isinstance(raw, dict):
        return raw, []
    steps = raw.get("steps")
    cleanup = raw.get("cleanup")
    uses = any(isinstance(s, dict) and "use" in s for s in [*(steps or []), *(cleanup or [])])
    own: list[tuple[str | None, int]] = [(None, i) for i in range(len(steps))] if isinstance(steps, list) else []
    if not uses:
        return raw, own
    groups, _ = read_groups(library_dir_for(path))
    out = dict(raw)
    origins: list[tuple[str | None, int]] = []
    extra_cleanup: list[dict[str, Any]] = []
    out["steps"] = _expand_list(steps, groups, "step", origins, extra_cleanup, track=True)
    if cleanup is not None or extra_cleanup:
        mine = _expand_list(cleanup or [], groups, "cleanup step", [], [], track=False)
        out["cleanup"] = [*mine, *reversed(extra_cleanup)]
    return out, origins


def _expand_list(
    items: Any,
    groups: dict[str, tuple[Path, dict[str, Any]]],
    what: str,
    origins: list[tuple[str | None, int]],
    extra_cleanup: list[dict[str, Any]],
    *,
    track: bool,
) -> list[Any]:
    result: list[Any] = []
    if not isinstance(items, list):
        return cast(list[Any], items)  # the model validation says what is wrong with it
    for number, item in enumerate(items, 1):
        if not (isinstance(item, dict) and "use" in item):
            result.append(item)
            if track:
                origins.append((None, number - 1))
            continue
        used = _use(item, groups, f"{what} {number}")
        steps, cleanup, name = used
        for index, step in enumerate(steps):
            result.append(step)
            if track:
                origins.append((name, index))
        extra_cleanup.extend(cleanup)
    return result


def _use(
    item: dict[str, Any], groups: dict[str, tuple[Path, dict[str, Any]]], where: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    extra = set(item) - {"use", "with"}
    if extra:
        raise LibraryError(f"{where}: a `use` step may only have `use` and `with` (found {', '.join(sorted(extra))})")
    name = item["use"]
    if not isinstance(name, str) or name not in groups:
        known = ", ".join(sorted(groups)) or "none yet"
        raise LibraryError(f"{where}: no shared group named '{name}' in the {LIBRARY_DIR} folder (there is: {known})")
    group = groups[name][1]
    declared = {str(k): v for k, v in (group.get("params") or {}).items()}
    given = item.get("with") or {}
    if not isinstance(given, dict):
        raise LibraryError(f"{where}: `with` must be a mapping of names to values")
    unknown = sorted(str(k) for k in given if str(k) not in declared)
    if unknown:
        allowed = ", ".join(sorted(declared)) or "no parameters"
        raise LibraryError(f"{where}: '{name}' has no parameter {', '.join(unknown)} (it takes: {allowed})")
    values: dict[str, str] = {}
    for key, default in declared.items():
        value = given.get(key, default)
        if value is None:
            raise LibraryError(f"{where}: '{name}' needs a value for {key}: use `with: {{{key}: ...}}`")
        if isinstance(value, dict | list):
            raise LibraryError(f"{where}: the value of {key} must be text or a number")
        values[key] = str(value)
    steps = [_fill(copy.deepcopy(s), values, name) for s in group["steps"]]
    cleanup = [_fill(copy.deepcopy(s), values, name) for s in group.get("cleanup") or []]
    return steps, cleanup, name


def _fill(step: dict[str, Any], values: dict[str, str], name: str) -> dict[str, Any]:
    """The step with ${param} replaced everywhere in its text. Other ${names} are left for the test's data."""
    filled = _walk(step, values)
    assert isinstance(filled, dict)
    filled["shared"] = name
    return filled


def _walk(node: Any, values: dict[str, str]) -> Any:
    if isinstance(node, str):
        return _PARAM.sub(lambda m: values.get(m.group(1), m.group(0)), node)
    if isinstance(node, list):
        return [_walk(x, values) for x in node]
    if isinstance(node, dict):
        return {k: _walk(v, values) for k, v in node.items()}
    return node
