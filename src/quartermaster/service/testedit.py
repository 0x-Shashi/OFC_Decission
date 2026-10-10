"""Copy a test, and edit the values of a data set, without opening a text editor.

Each client has its own tests folder, and so its own `_data` folder. A test says `${business_unit}`; the data set
says what that is on each pod. So the same test serves every client, and only the data set differs."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from quartermaster.dsl.data import _KEY, DATA_DIR, DataError, check_set, read_sets
from quartermaster.dsl.loader import load_test
from quartermaster.service import atomic

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_TOP = re.compile(r"^(id|title):.*$")


def _backup(path: Path, backups: Path) -> None:
    if path.is_file():
        backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backups / f"{path.stem}.{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")


def _quoted(text: str) -> str:
    return str(yaml.safe_dump(text, default_flow_style=True, width=10**6)).splitlines()[0]


def duplicate(tests_root: Path, source: Path, new_id: str, title: str, folder: str, backups: Path) -> Path:
    """A copy of a test with its own id and title. Everything else, comments included, stays as written."""
    new_id = new_id.strip()
    title = title.strip()
    if not _ID.match(new_id):
        raise ValueError("the id may use letters, digits, dots, dashes and underscores")
    if not title:
        raise ValueError("give the copy a title")
    target_dir = (tests_root / (folder.strip() or source.parent.relative_to(tests_root).as_posix())).resolve()
    if tests_root != target_dir and tests_root not in target_dir.parents:
        raise ValueError("the folder must be inside the tests folder")
    if target_dir.name.startswith("_"):
        raise ValueError("pick a test folder, not one that starts with an underscore")
    target = target_dir / f"{new_id}.yaml"
    if target.exists():
        raise ValueError(f"{target.relative_to(tests_root).as_posix()} already exists")
    if any(p.stem == new_id or (load_id(p) == new_id) for p in tests_root.rglob("*.y*ml") if _is_test(p, tests_root)):
        raise ValueError(f"a test with the id '{new_id}' already exists")
    lines = source.read_text(encoding="utf-8").splitlines()
    done: set[str] = set()
    for i, ln in enumerate(lines):
        m = _TOP.match(ln)
        if m and m.group(1) not in done:
            done.add(m.group(1))
            lines[i] = f"{m.group(1)}: {_quoted(new_id if m.group(1) == 'id' else title)}"
    if done != {"id", "title"}:
        raise ValueError("the test needs a top-level id and title to be copied")
    text = "\n".join(lines) + "\n"
    atomic.write_text(target, text)
    try:
        load_test(target)
    except Exception as e:  # a copy that cannot be read is not left behind
        target.unlink(missing_ok=True)
        raise ValueError(f"the copy could not be read: {e}") from e
    return target


def _is_test(path: Path, root: Path) -> bool:
    return not any(part.startswith("_") for part in path.relative_to(root).parts[:-1])


def load_id(path: Path) -> str:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ""
    return str(raw.get("id") or "") if isinstance(raw, dict) else ""


def save_set(tests_root: Path, data: dict[str, Any], backups: Path) -> Path:
    """Write a data set from the page's table. A set that exists is kept as a backup first."""
    name = str(data.get("name") or "").strip()
    raw: dict[str, Any] = {"dataset": name}
    if str(data.get("title") or "").strip():
        raw["title"] = str(data["title"]).strip()
    if str(data.get("description") or "").strip():
        raw["description"] = str(data["description"]).strip()
    raw["values"] = _clean(data.get("values"), "values")
    pods = data.get("pods") or {}
    if not isinstance(pods, dict):
        raise ValueError("pods must be a list of pod names with their values")
    raw["pods"] = {str(p): _clean(v, str(p)) for p, v in pods.items() if _clean(v, str(p))}
    if not raw["pods"]:
        del raw["pods"]
    if not raw["values"]:
        del raw["values"]
    try:
        check_set(raw)
    except DataError as e:
        raise ValueError(str(e)) from e
    directory = tests_root / DATA_DIR
    existing, _ = read_sets(directory if directory.is_dir() else None)
    path = existing[name][0] if name in existing else directory / f"{name}.yaml"
    _backup(path, backups)
    atomic.write_text(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=10**6))
    return path


def _clean(block: object, where: str) -> dict[str, str]:
    """Names with a value; a blank value means no value, so it is left out."""
    if not isinstance(block, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in block.items():
        key = str(key).strip()
        if not _KEY.match(key):
            raise ValueError(
                f"'{key}' in {where}: a name uses letters, digits and underscores, and starts with a letter"
            )
        if str(value).strip():
            out[key] = str(value).strip()
    return out
