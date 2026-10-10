"""Load and validate YAML test specs and release feature files."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from quartermaster.domain.models import Action, Release, TestCase
from quartermaster.dsl.data import DataError, merge_sets
from quartermaster.dsl.library import LibraryError, expand, files_of_tests


class SpecError(ValueError):
    """A spec file failed to parse or validate. Message names the file."""


_PLACEHOLDER = re.compile(r"\$\{((?:env:)?[A-Za-z_][A-Za-z0-9_]*)\}")

# Values the runner supplies at execution time, e.g. a unique suffix for document numbers.
RUNTIME_VARS = frozenset({"RUN_ID"})

# ${env:NAME} reads a masked value (one that must not be written in the test file) from the
# environment variable NAME when the test runs; evidence shows it as MASK.
SECRET = "env:"
MASK = "••••••"


class MissingSecretError(ValueError):
    """A masked value's environment variable is not set on this computer."""


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise SpecError(f"{path}: invalid YAML: {e}") from e


def load_test(path: str | Path) -> TestCase:
    path = Path(path)
    raw = _read_yaml(path)
    try:
        raw, _ = expand(raw, path)  # shared steps (`use:`) become ordinary steps
    except LibraryError as e:
        raise SpecError(f"{path}: {e}") from e
    try:
        raw = merge_sets(raw, path)  # data sets (`data_sets:`) become the test's own data and pod values
    except DataError as e:
        raise SpecError(f"{path}: {e}") from e
    try:
        test = TestCase.model_validate(raw)
    except ValidationError as e:
        raise SpecError(f"{path}: {e}") from e
    _check_placeholders(test, path)
    return test


def load_tests(directory: str | Path) -> list[TestCase]:
    directory = Path(directory)
    tests = [load_test(p) for p in files_of_tests(directory)]
    seen: dict[str, int] = {}
    for t in tests:
        seen[t.id] = seen.get(t.id, 0) + 1
    dupes = sorted(k for k, n in seen.items() if n > 1)
    if dupes:
        raise SpecError(f"{directory}: duplicate test ids: {', '.join(dupes)}")
    return tests


def load_release(path: str | Path) -> Release:
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8")) if path.suffix == ".json" else _read_yaml(path)
    try:
        return Release.model_validate(raw)
    except ValidationError as e:
        raise SpecError(f"{path}: {e}") from e


def _check_placeholders(test: TestCase, path: Path) -> None:
    """Every ${name} must be declared in the test's `data` block or be a runtime variable."""
    made = set(test.generate)  # values made fresh for every run
    for name in sorted(made):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in RUNTIME_VARS or name in test.data:
            raise SpecError(f"{path}: generate '{name}' must be a new name (letters, digits, underscores)")
    pod_names = {k for block in test.pods.values() for k in block}
    clash = sorted(made & pod_names)
    if clash:
        raise SpecError(f"{path}: '{clash[0]}' is both generated and given a value for a pod")
    known = set(test.data) | pod_names | made | RUNTIME_VARS
    allowed = RUNTIME_VARS | made
    values = [(f"data '{k}'", v) for k, v in test.data.items()]
    values += [(f"pods: {p} '{k}'", v) for p, block in test.pods.items() for k, v in block.items()]
    for where, val in values:
        for name in _PLACEHOLDER.findall(val):
            if name not in allowed and not name.startswith(SECRET):
                raise SpecError(
                    f"{path}: {where} may only reference runtime variables or generated values, not ${{{name}}}"
                )
    for i, step in enumerate(test.setup, 1):
        # Setup runs first and may use what the setup steps before it saved (the id of a supplier it made).
        texts = [step.value or "", *_option_texts(step.options)]
        for name in (n for t in texts for n in _PLACEHOLDER.findall(t)):
            if name not in known and not name.startswith(SECRET):
                raise SpecError(f"{path}: setup step {i} uses undefined data placeholder ${{{name}}}")
        if (step.value or "").lstrip().upper().startswith("DELETE") and not _PLACEHOLDER.search(step.value or ""):
            raise SpecError(
                f"{path}: setup step {i} deletes a fixed address. A DELETE must use a value an earlier setup "
                "step saved (for example ${supplier_id}), so it can only remove what this run made"
            )
        saved = step.options.get("save")
        if isinstance(saved, dict):
            known |= {str(k) for k in saved}
    for i, step in enumerate(test.steps):
        texts = [step.value or ""] + [v for _, v in (step.target.ordered() if step.target else [])]
        for name in (n for t in texts for n in _PLACEHOLDER.findall(t)):
            if name not in known and not name.startswith(SECRET):
                raise SpecError(f"{path}: step {i} {_from(step)}uses undefined data placeholder ${{{name}}}")
        saved = step.options.get("save") if step.action is Action.API_CALL else None
        if isinstance(saved, dict):
            known |= {str(k) for k in saved}  # a REST step keeps values from its reply for the steps after it
    for i, step in enumerate(test.cleanup):
        # Cleanup runs after the steps, so it may use what they saved. A name that was never saved is
        # not an error here: the step is skipped at run time ("nothing was made, nothing to clean").
        texts = [step.value or ""] + [v for _, v in (step.target.ordered() if step.target else [])]
        for name in (n for t in texts for n in _PLACEHOLDER.findall(t)):
            if name not in known and not name.startswith(SECRET):
                raise SpecError(
                    f"{path}: cleanup step {i + 1} {_from(step)}uses undefined data placeholder ${{{name}}}"
                )
        deletes = step.action is Action.API_CALL and (step.value or "").lstrip().upper().startswith("DELETE")
        if deletes and not _PLACEHOLDER.search(step.value or ""):
            raise SpecError(
                f"{path}: cleanup step {i + 1} deletes a fixed address. A cleanup DELETE must use a value "
                "this test saved (for example ${location_id}), so it can only remove what this run made"
            )
        needs = step.options.get("needs")
        for name in [needs] if isinstance(needs, str) else needs or []:
            if str(name) not in known:
                raise SpecError(f"{path}: cleanup step {i + 1} needs '{name}', which no step saves")


def _option_texts(options: dict[str, object]) -> list[str]:
    """The texts in a service step's options where ${name} may stand: the body and what the reply is checked against."""
    out: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for key in ("body", "check"):
        walk(options.get(key))
    return out


def _from(step: object) -> str:
    """ " (from the shared steps 'x') " for a step that came from a shared group, else nothing."""
    shared = getattr(step, "shared", None)
    return f"(from the shared steps '{shared}', so add that name to the test's data) " if shared else ""


def render_value(value: str | None, data: dict[str, str], runtime: dict[str, str] | None = None) -> str | None:
    """Substitute ${name} from test data, then runtime variables (data may reference those)."""
    if value is None:
        return None
    return _PLACEHOLDER.sub(_from_environment, _substitute(value, data, runtime))


def render_text_names(value: str, data: dict[str, str], runtime: dict[str, str] | None = None) -> list[str]:
    """The ${names} left in a text once test data and runtime values are filled in."""
    return _PLACEHOLDER.findall(_substitute(value, data, runtime))


def display_value(value: str | None, data: dict[str, str], runtime: dict[str, str] | None = None) -> str | None:
    """The value as evidence may show it: like render_value, but masked values stay hidden."""
    if value is None:
        return None
    return _PLACEHOLDER.sub(
        lambda m: MASK if m.group(1).startswith(SECRET) else m.group(0), _substitute(value, data, runtime)
    )


def _substitute(value: str, data: dict[str, str], runtime: dict[str, str] | None) -> str:
    runtime = runtime or {}
    value = _PLACEHOLDER.sub(lambda m: data.get(m.group(1), m.group(0)), value)
    return _PLACEHOLDER.sub(lambda m: runtime.get(m.group(1), m.group(0)), value)


def _from_environment(m: re.Match[str]) -> str:
    name = m.group(1)
    if not name.startswith(SECRET):
        return m.group(0)
    var = name[len(SECRET) :]
    if var not in os.environ:
        raise MissingSecretError(f"this test types a masked value: set the environment variable {var} first")
    return os.environ[var]
