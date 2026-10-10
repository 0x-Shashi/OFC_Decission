"""Test data: values made fresh for every run, and values that belong to a pod.

Two things a test can ask for, both written in plain YAML.

1. Values made fresh for every run, with `generate:`. A test that creates an invoice must not use the same
   invoice number twice, and a start date must not be last year's date:

       generate:
         invoice_no: {unique: 6, prefix: "INV-"}            # INV-7K2Q9X, new each run, the same all through the run
         start_date: {date: today, plus_days: 30, format: "%Y-%m-%d"}
         amount: {number: [100, 999]}
         currency: {choice: [USD, EUR, GBP]}

   The names are used in steps as ${invoice_no}, like any other data. They come from the run's own id, so the
   same run always gets the same values and the evidence shows exactly what was typed.

2. Values that belong to a pod, with data sets. Every pod has its own business units, suppliers and ledgers, so
   one file in the `_data` folder holds what each pod calls them:

       dataset: hcm-basics
       title: Names on the pods
       values:                       # used on every pod unless a pod below says otherwise
         business_unit: US1 Business Unit
       pods:                         # by the environment's name, or by its kind (DEV, TEST, STAGE)
         DEV2: {business_unit: Vision Operations}
         STAGE: {business_unit: US1 Stage BU}

   A test uses it with `data_sets: [hcm-basics]`. The test's own `data:` wins over a data set, and its own `pods:`
   wins over everything. A name that has no value on the pod being tested stops the test with a plain message
   (not a failed step), so a gap in the data is never mistaken for a broken release.
"""

from __future__ import annotations

import hashlib
import random
import re
import string
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from quartermaster.domain.models import GenerateRule

DATA_DIR = "_data"
_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALPHABET = string.ascii_uppercase + string.digits


class DataError(ValueError):
    """A data set is missing, wrong or used wrongly. The message is for the person writing the test."""


# ------------------------------------------------------------------ values made fresh for every run


def generate_values(rules: dict[str, GenerateRule], run_id: str, today: date | None = None) -> dict[str, str]:
    """The value of every `generate:` rule for this run. The same run id always gives the same values."""
    day = today or date.today()
    return {name: _one(name, rule, run_id, day) for name, rule in rules.items()}


def _seed(name: str, run_id: str) -> random.Random:
    return random.Random(hashlib.sha256(f"{run_id}/{name}".encode()).digest())


def _one(name: str, rule: GenerateRule, run_id: str, today: date) -> str:
    rng = _seed(name, run_id)
    if rule.unique is not None:
        body = "".join(rng.choice(_ALPHABET) for _ in range(rule.unique))
        return f"{rule.prefix}{body}{rule.suffix}"
    if rule.date is not None:
        return (today + timedelta(days=rule.plus_days)).strftime(rule.format)
    if rule.number is not None:
        low, high = rule.number
        return str(rng.randint(low, high))
    assert rule.choice is not None  # GenerateRule allows exactly one kind
    return str(rng.choice(rule.choice))


# ------------------------------------------------------------------ data sets


def data_dir_for(path: Path) -> Path | None:
    """The `_data` folder that belongs to a test file: the nearest one in its folder or above."""
    for folder in path.resolve().parents:
        candidate = folder / DATA_DIR
        if candidate.is_dir():
            return candidate
    return None


def read_sets(directory: Path | None) -> tuple[dict[str, tuple[Path, dict[str, Any]]], list[tuple[Path, str]]]:
    """(the readable data sets by name, the files that could not be used with why)."""
    sets: dict[str, tuple[Path, dict[str, Any]]] = {}
    problems: list[tuple[Path, str]] = []
    if directory is None:
        return sets, problems
    for path in sorted(directory.rglob("*.y*ml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            problems.append((path, f"could not be read: {e}"))
            continue
        try:
            name = check_set(raw)
        except DataError as e:
            problems.append((path, str(e)))
            continue
        if name in sets:
            problems.append((path, f"the name '{name}' is also used by {sets[name][0].name}"))
            continue
        sets[name] = (path, raw)
    return sets, problems


def check_set(raw: object) -> str:
    """The data set's name, or DataError saying what is wrong with it."""
    if not isinstance(raw, dict):
        raise DataError("a data set is a YAML mapping with `dataset:` and `values:`")
    name = raw.get("dataset")
    if not isinstance(name, str) or not _NAME.match(name):
        raise DataError("`dataset:` must be a name of letters, digits, dots, dashes and underscores")
    unknown = set(raw) - {"dataset", "title", "description", "values", "pods"}
    if unknown:
        raise DataError(f"unknown keys: {', '.join(sorted(unknown))}")
    _values(raw.get("values"), "values")
    pods = raw.get("pods") or {}
    if not isinstance(pods, dict):
        raise DataError("`pods:` must be a mapping of an environment name (or DEV, TEST, STAGE) to its values")
    for pod, block in pods.items():
        _values(block, f"pods: {pod}")
    if not raw.get("values") and not pods:
        raise DataError("a data set needs `values:` or `pods:`")
    return name


def _values(block: object, where: str) -> dict[str, str]:
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise DataError(f"`{where}` must be a mapping of names to values")
    out: dict[str, str] = {}
    for key, value in block.items():
        if not _KEY.match(str(key)):
            raise DataError(f"`{where}`: '{key}' is not a usable name (letters, digits and underscores)")
        if isinstance(value, dict | list) or value is None:
            raise DataError(f"`{where}`: the value of {key} must be text or a number")
        out[str(key)] = str(value)
    return out


def merge_sets(raw: Any, path: Path) -> Any:
    """The test with its `data_sets:` folded in: the sets' values go under the test's own `data`, and their pod
    values under its own `pods`. Returns the test unchanged when it uses no data set. Raises DataError."""
    if not isinstance(raw, dict):
        return raw
    wanted = raw.get("data_sets")
    if wanted is None and "pods" not in raw:
        return raw
    wanted = [] if wanted is None else wanted
    if not isinstance(wanted, list) or not all(isinstance(n, str) for n in wanted):
        raise DataError("`data_sets:` must be a list of data set names")
    sets, _ = read_sets(data_dir_for(path))
    own_data = _own(raw.get("data"), "data")
    own_pods = raw.get("pods") or {}
    if not isinstance(own_pods, dict):
        raise DataError("`pods:` must be a mapping of an environment name (or DEV, TEST, STAGE) to its values")
    data: dict[str, str] = {}
    pods: dict[str, dict[str, str]] = {}
    for name in wanted:
        if name not in sets:
            known = ", ".join(sorted(sets)) or "none yet"
            raise DataError(f"no data set named '{name}' in the {DATA_DIR} folder (there is: {known})")
        body = sets[name][1]
        data.update(_values(body.get("values"), "values"))
        for pod, block in (body.get("pods") or {}).items():
            pods.setdefault(str(pod), {}).update(_values(block, f"pods: {pod}"))
    out = dict(raw)
    out["data"] = {**data, **own_data}
    merged: dict[str, dict[str, str]] = {}
    for pod in {*pods, *map(str, own_pods)}:
        # the test's own `data` beats a data set's pod value; the test's own pod value beats everything
        from_sets = {k: v for k, v in pods.get(pod, {}).items() if k not in own_data}
        merged[pod] = {**from_sets, **_own(own_pods.get(pod), f"pods: {pod}")}
    out["pods"] = merged
    return out


def _own(block: object, where: str) -> dict[str, str]:
    return _values(block, where)


# ------------------------------------------------------------------ which values apply on a pod


def pod_values(pods: dict[str, dict[str, str]], name: str, kind: str) -> dict[str, str]:
    """The values for the pod `name` of kind `kind` (DEV, TEST, STAGE): the kind's, then the name's own on top.
    Names are compared without regard to capital letters."""
    out: dict[str, str] = {}
    for wanted in (kind, name):
        for pod, block in pods.items():
            if pod.casefold() == wanted.casefold():
                out.update(block)
    return out


def effective_data(data: dict[str, str], pods: dict[str, dict[str, str]], name: str, kind: str) -> dict[str, str]:
    """The test's data as it is on this pod."""
    return {**data, **pod_values(pods, name, kind)}


def pod_only_names(data: dict[str, str], pods: dict[str, dict[str, str]]) -> set[str]:
    """Names that only some pods give: written in a pod's values but with no value for every pod."""
    return {k for block in pods.values() for k in block} - set(data)
