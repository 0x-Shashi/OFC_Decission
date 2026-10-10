"""Accept a test update: make the way a step was found last time the first way it is tried.

When a step passes only because a later locator strategy found the element, the run records it
(the test "needs an update"). Accepting moves that strategy to the top of the step's list. The
file is edited as text, so comments and layout stay as they are, and the result is checked: it
must read back exactly as before except for the new order. Otherwise nothing is written.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

_ITEM = re.compile(r"^(\s*)- ")


def promote_strategy(text: str, step_index: int, strategy: str, value: str) -> str:
    """The same YAML text with {strategy: value} moved to the top of that step's strategies."""
    spec = yaml.safe_load(text)
    steps = spec.get("steps") if isinstance(spec, dict) else None
    if not isinstance(steps, list) or not 0 <= step_index < len(steps):
        raise ValueError(f"the test has no step {step_index + 1}")
    wanted = {strategy: value}
    strategies = ((steps[step_index] or {}).get("target") or {}).get("strategies") or []
    if wanted not in strategies:
        raise ValueError(f"step {step_index + 1} has no {strategy} locator {value!r}")
    if strategies[0] == wanted:
        raise ValueError(f"step {step_index + 1} already tries this first")

    lines = text.splitlines(keepends=True)
    start, end = _step_block(lines, step_index)
    head = next((i for i in range(start, end) if lines[i].strip().startswith("strategies:")), None)
    if head is None:
        raise ValueError("the step's locators are not written in the usual way; please edit the file by hand")
    items = _list_items(lines, head + 1, end)
    target = next((it for it in items if _parse_item(lines[it[0] : it[1]]) == wanted), None)
    if target is None or not items:
        raise ValueError("the step's locators are not written in the usual way; please edit the file by hand")
    first = items[0]
    moved = lines[target[0] : target[1]]
    rest = lines[: target[0]] + lines[target[1] :]
    new_lines = rest[: first[0]] + moved + rest[first[0] :]
    new_text = "".join(new_lines)

    expected = dict(spec)
    expected_steps = list(steps)
    step = dict(expected_steps[step_index])
    step["target"] = {**step["target"], "strategies": [wanted] + [s for s in strategies if s != wanted]}
    expected_steps[step_index] = step
    expected["steps"] = expected_steps
    if yaml.safe_load(new_text) != expected:
        raise ValueError("could not update the file safely; please edit it by hand")
    return new_text


def add_strategy(text: str, step_index: int, strategy: str, value: str) -> str:
    """The same YAML text with {strategy: value} added at the top of that step's strategies, the old ways
    kept below it (so a screen that still has them keeps passing). Used for a suggestion."""
    spec = yaml.safe_load(text)
    steps = spec.get("steps") if isinstance(spec, dict) else None
    if not isinstance(steps, list) or not 0 <= step_index < len(steps):
        raise ValueError(f"the test has no step {step_index + 1}")
    wanted = {strategy: value}
    strategies = ((steps[step_index] or {}).get("target") or {}).get("strategies") or []
    if not strategies:
        raise ValueError(f"step {step_index + 1} has no locators to add to")
    if wanted in strategies:
        return promote_strategy(text, step_index, strategy, value)

    lines = text.splitlines(keepends=True)
    start, end = _step_block(lines, step_index)
    head = next((i for i in range(start, end) if lines[i].strip().startswith("strategies:")), None)
    items = _list_items(lines, head + 1, end) if head is not None else []
    if not items:
        raise ValueError("the step's locators are not written in the usual way; please edit the file by hand")
    first = items[0]
    dash = next((_ITEM.match(ln) for ln in lines[first[0] : first[1]] if _ITEM.match(ln)), None)
    if dash is None:
        raise ValueError("the step's locators are not written in the usual way; please edit the file by hand")
    # a double-quoted JSON string is also a valid YAML string
    new_line = f"{dash.group(1)}- {strategy}: {json.dumps(value, ensure_ascii=False)}\n"
    new_text = "".join(lines[: first[0]] + [new_line] + lines[first[0] :])

    expected = dict(spec)
    expected_steps = list(steps)
    step = dict(expected_steps[step_index])
    step["target"] = {**step["target"], "strategies": [wanted, *strategies]}
    expected_steps[step_index] = step
    expected["steps"] = expected_steps
    if yaml.safe_load(new_text) != expected:
        raise ValueError("could not update the file safely; please edit it by hand")
    return new_text


def accept_update(test_file: Path, step_index: int, new: list[str], backups: Path, *, add: bool = False) -> Path:
    """Apply the update to the test file, keeping a copy of the old one. Returns the backup. With `add`,
    the new way is one the test did not know yet (a suggestion): it is added, not just moved up."""
    if len(new) != 2:
        raise ValueError("the update must name a locator strategy and its value")
    text = test_file.read_text(encoding="utf-8")
    change = add_strategy if add else promote_strategy
    updated = change(text, step_index, new[0], new[1])
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"{test_file.stem}.{datetime.now().strftime('%Y%m%d-%H%M%S')}{test_file.suffix}"
    shutil.copy2(test_file, backup)
    test_file.write_text(updated, encoding="utf-8")
    return backup


def _step_block(lines: list[str], index: int) -> tuple[int, int]:
    """Line range of step `index` in the top-level `steps:` list."""
    top = next((i for i, ln in enumerate(lines) if ln.startswith("steps:")), None)
    if top is None:
        raise ValueError("the test has no steps list")
    starts: list[int] = []
    indent: str | None = None
    end = len(lines)
    for i in range(top + 1, len(lines)):
        ln = lines[i]
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if not ln[0].isspace() and not ln.startswith("-"):
            end = i  # the next top-level key
            break
        m = _ITEM.match(ln)
        if m and (indent is None or m.group(1) == indent):
            indent = m.group(1)
            starts.append(i)
    if index >= len(starts):
        raise ValueError(f"the test has no step {index + 1}")
    return starts[index], starts[index + 1] if index + 1 < len(starts) else end


def _list_items(lines: list[str], begin: int, end: int) -> list[tuple[int, int]]:
    """The items of the list that starts at `begin`: (first line, line after the item)."""
    items: list[tuple[int, int]] = []
    indent: str | None = None
    for i in range(begin, end):
        ln = lines[i]
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        m = _ITEM.match(ln)
        if indent is None:
            if not m:
                return []
            indent = m.group(1)
        lead = len(ln) - len(ln.lstrip())
        if m and m.group(1) == indent:
            if items:
                items[-1] = (items[-1][0], _trim(lines, items[-1][0], i))
            items.append((i, i + 1))
        elif lead > len(indent):
            items[-1] = (items[-1][0], i + 1)  # a continuation of the current item
        else:
            break
    if items:
        items[-1] = (items[-1][0], _trim(lines, items[-1][0], items[-1][1]))
    assert indent is not None
    return [(_with_comments(lines, start, begin, indent), stop) for start, stop in items]


def _with_comments(lines: list[str], start: int, begin: int, indent: str) -> int:
    """A comment written just above an item belongs to it and moves with it."""
    while start > begin and lines[start - 1].startswith(indent + "#"):
        start -= 1
    return start


def _trim(lines: list[str], start: int, stop: int) -> int:
    """Leave blank and comment lines between items where they are."""
    while stop > start + 1 and (not lines[stop - 1].strip() or lines[stop - 1].lstrip().startswith("#")):
        stop -= 1
    return stop


def _parse_item(item_lines: list[str]) -> Any:
    first = item_lines[0]
    indent = len(first) - len(first.lstrip())
    text = "".join(ln[indent:] if len(ln) >= indent else ln for ln in item_lines)
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return parsed[0] if isinstance(parsed, list) and parsed else None
