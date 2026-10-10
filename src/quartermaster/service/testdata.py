"""The Test data page: data sets, who uses them, where a pod has no value, and the values made fresh."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from quartermaster.domain.models import TestCase
from quartermaster.dsl.data import DATA_DIR, DataError, generate_values, merge_sets, pod_values, read_sets
from quartermaster.dsl.library import expand, files_of_tests

SAMPLE_RUN = "A1B2C3D4"  # a made-up run id, so the page can show what a rule gives


def overview(tests_root: Path, pods: list[dict[str, str]]) -> dict[str, Any]:
    """Everything the page shows. `pods` are the environments the client has: [{"name": ..., "kind": ...}]."""
    directory = tests_root / DATA_DIR
    sets, problems = read_sets(directory if directory.is_dir() else None)
    used: dict[str, list[dict[str, str]]] = {}
    made: list[dict[str, Any]] = []
    for f in files_of_tests(tests_root):
        rel = f.relative_to(tests_root).as_posix()
        try:
            spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            continue
        if not isinstance(spec, dict):
            continue
        title = str(spec.get("title") or spec.get("id"))
        wanted = spec.get("data_sets")
        for name in wanted if isinstance(wanted, list) else []:
            if isinstance(name, str):
                used.setdefault(name, []).append({"file": rel, "title": title})
        if spec.get("generate"):
            made.append(_generated(f, spec, rel, title))
    out = [_one(name, path, raw, used.get(name, []), pods, tests_root) for name, (path, raw) in sorted(sets.items())]
    missing = sorted(set(used) - set(sets))
    return {
        "folder": f"{tests_root.name}/{DATA_DIR}",
        "sets": out,
        "problems": [{"file": p.relative_to(tests_root).as_posix(), "problem": why[:300]} for p, why in problems],
        "missing": [{"name": m, "used_by": used[m]} for m in missing],
        "generated": [m for m in made if m],
        "pods": pods,
        "sample_run": SAMPLE_RUN,
    }


def _one(
    name: str,
    path: Path,
    raw: dict[str, Any],
    used_by: list[dict[str, str]],
    pods: list[dict[str, str]],
    tests_root: Path,
) -> dict[str, Any]:
    defaults = {str(k): str(v) for k, v in (raw.get("values") or {}).items()}
    blocks = {str(p): {str(k): str(v) for k, v in (b or {}).items()} for p, b in (raw.get("pods") or {}).items()}
    names = sorted({*defaults, *(k for b in blocks.values() for k in b)})
    # Every pod the client has, with the value each name has there (or none: a test using it stops with a message).
    table: list[dict[str, Any]] = []
    for pod in pods:
        here = {**defaults, **pod_values(blocks, pod["name"], pod["kind"])}
        table.append({"pod": pod["name"], "kind": pod["kind"], "values": {n: here.get(n) for n in names}})
    gaps = sorted({n for row in table for n in names if row["values"][n] is None})
    return {
        "name": name,
        "title": str(raw.get("title") or ""),
        "description": str(raw.get("description") or ""),
        "file": path.relative_to(tests_root).as_posix(),
        "names": names,
        "defaults": defaults,
        "pod_blocks": [{"pod": p, "values": v} for p, v in sorted(blocks.items())],
        "table": table,
        "gaps": [{"name": n, "pods": [r["pod"] for r in table if r["values"][n] is None]} for n in gaps],
        "used_by": used_by,
        "yaml": path.read_text(encoding="utf-8"),
    }


def _generated(path: Path, spec: dict[str, Any], rel: str, title: str) -> dict[str, Any]:
    """What a test's `generate:` rules give for a made-up run, or {} when the test cannot be read."""
    try:
        expanded, _ = expand(spec, path)
        test = TestCase.model_validate(merge_sets(expanded, path))
    except (ValueError, ValidationError, DataError):
        return {}
    values = generate_values(test.generate, SAMPLE_RUN)
    return {
        "file": rel,
        "title": title,
        "values": [
            {"name": k, "rule": describe_rule(r.model_dump(exclude_none=True)), "sample": values[k]}
            for k, r in test.generate.items()
        ],
    }


def describe_rule(rule: dict[str, Any]) -> str:
    """A rule in words, from its YAML (a mapping with one of unique, date, number or choice)."""
    if "unique" in rule:
        extra = (f" after '{rule['prefix']}'" if rule.get("prefix") else "") + (
            f" before '{rule['suffix']}'" if rule.get("suffix") else ""
        )
        return f"{rule['unique']} letters and digits, new each run{extra}"
    if "date" in rule:
        days = rule.get("plus_days", 0)
        when = "today" if not days else f"today {'+' if days > 0 else '-'} {abs(days)} days"
        return f"the date {when}, written as {rule.get('format', '%Y-%m-%d')}"
    if "number" in rule:
        return f"a whole number from {rule['number'][0]} to {rule['number'][1]}"
    return f"one of {', '.join(rule.get('choice', []))}"
