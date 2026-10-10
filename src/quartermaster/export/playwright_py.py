"""Export tests as plain Playwright for Python (pytest) files that need nothing from Quartermaster.

`qm export my_tests --out exported` writes one `test_<id>.py` per test, plus `fusion_runtime.py` (the small library the
tests call), `conftest.py`, `pytest.ini`, `requirements.txt` and a README. A team is never locked in: the tests
can leave with them. Shared steps (`use:`) are part of each test once it is loaded, so each exported file is complete.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from quartermaster import __version__
from quartermaster.domain.models import Action, Step, TestCase

TEMPLATES = Path(__file__).parent / "templates"
# name written -> file in templates/ (the conftest is stored as .tmpl so pytest does not take it for its own)
SUPPORT = {
    "fusion_runtime.py": "fusion_runtime.py",
    "conftest.py": "conftest.py.tmpl",
    "requirements.txt": "requirements.txt",
    "README.md": "README.md",
}
_RESERVED = {"skip", "skipif", "xfail", "parametrize", "usefixtures", "filterwarnings", "tryfirst", "trylast"}


class ExportError(ValueError):
    """The export cannot be written. The message is for the person who asked for it."""


def export(tests: list[tuple[TestCase, str]], out: Path, *, overwrite: bool = False) -> list[Path]:
    """Write the tests (each with the path of its YAML file, for the header) and what they need into `out`.
    Returns the files written. Files already there are kept unless `overwrite`: a person may have edited them."""
    if not tests:
        raise ExportError("there is no test to export")
    plan: list[tuple[Path, str]] = []
    names: dict[str, int] = {}
    markers: set[str] = set()
    for test, source in tests:
        base = f"test_{_ident(test.id)}"
        names[base] = names.get(base, 0) + 1
        stem = base if names[base] == 1 else f"{base}_{names[base]}"
        plan.append((out / f"{stem}.py", render_test(test, source, stem)))
        markers |= set(_markers(test))
    for name, template in SUPPORT.items():
        plan.append((out / name, (TEMPLATES / template).read_text(encoding="utf-8")))
    plan.append(
        (
            out / "pytest.ini",
            "[pytest]\nmarkers =\n" + "".join(f"    {m}: tag from Quartermaster\n" for m in sorted(markers))
            if markers
            else "[pytest]\n",
        )
    )
    plan.append((out / ".gitignore", "evidence/\n__pycache__/\n.pytest_cache/\n"))
    clash = [p.name for p, _ in plan if p.exists()]
    if clash and not overwrite:
        shown = ", ".join(clash[:4]) + (f" and {len(clash) - 4} more" if len(clash) > 4 else "")
        raise ExportError(
            f"{out} already has {shown}. Export into an empty folder, or choose to replace what is there "
            "(your own changes to those files would be lost)."
        )
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for path, text in plan:
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def render_test(test: TestCase, source: str = "", function: str = "") -> str:
    """The Python source of one test."""
    name = function or f"test_{_ident(test.id)}"
    lines = [
        f'"""{_doc(test.title)}',
        "",
        f"Test id: {test.id}   Module: {test.module}   Product: {test.product}"
        + (f"   Process: {test.process}" if test.process else ""),
        f"Exported from Quartermaster {__version__}"
        + (f" ({source})" if source else "")
        + ". Plain Playwright: edit freely.",
        '"""',
        "",
        "import pytest",
        "",
    ]
    marks = _markers(test)
    if marks:
        lines += ["pytestmark = [" + ", ".join(f"pytest.mark.{m}" for m in marks) + "]", ""]
    if test.data:
        lines += ["DATA = {"] + [f"    {k!r}: {v!r}," for k, v in test.data.items()] + ["}", ""]
    if test.pods:
        lines += [
            "# Values that differ by pod: the pod's name or kind (DEV, TEST, STAGE), then its values.",
            "PODS = {",
        ]
        lines += [f"    {p!r}: {block!r}," for p, block in test.pods.items()] + ["}", ""]
    if test.generate:
        lines += ["# Made fresh for every run (see generate_values in fusion_runtime.py).", "GENERATE = {"]
        lines += [f"    {k!r}: {r.model_dump(exclude_none=True)!r}," for k, r in test.generate.items()] + ["}", ""]
    lines += ["", f"def {name}(fusion):"]
    body: list[str] = []
    if test.pods or test.generate:
        args = ["DATA" if test.data else "{}"]
        args += ["pods=PODS"] if test.pods else []
        args += ["generate=GENERATE"] if test.generate else []
        body.append(f"fusion.use_data({', '.join(args)})")
    elif test.data:
        body.append("fusion.data.update(DATA)")
    body.append(f"fusion.login({test.persona!r})")
    steps = [_setup_block(i, s) for i, s in enumerate(test.setup, 1)]
    steps += [_step_block(i, s) for i, s in enumerate(test.steps, 1)]
    if test.cleanup:
        body.append("try:")
        for block in steps:
            body += ["    " + ln if ln else ln for ln in block]
        body.append("finally:")
        body.append("    # Cleanup runs even when a step failed. A failure here only prints a warning.")
        for s in test.cleanup:
            body += ["    " + ln for ln in _cleanup_block(s)]
    else:
        for block in steps:
            body += block
    lines += ["    " + ln if ln else ln for ln in body]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------- steps


def _step_block(number: int, step: Step) -> list[str]:
    out: list[str] = []
    if step.expected:
        out.append(f"# Expected: {_oneline(step.expected)}")
    out.append(f"with fusion.step({number}, {step.intent!r}):")
    out += ["    " + ln for ln in _call(step).splitlines()]
    out.append("")
    return out


def _setup_block(number: int, step: Step) -> list[str]:
    """A setup call: if it fails the test stops saying the pod was not ready (and the cleanup still runs)."""
    out = [f"with fusion.setup({number}, {step.intent!r}):"]
    out += ["    " + ln for ln in _call(step).splitlines()]
    out.append("")
    return out


def _cleanup_block(step: Step) -> list[str]:
    texts = [step.value or ""] + [v for _, v in (step.target.ordered() if step.target else [])]
    needs = step.options.get("needs")
    needs_list = [str(n) for n in ([needs] if isinstance(needs, str) else needs or [])]
    call = _call(step)
    one = f"lambda: {call}" if "\n" not in call else f"lambda: ({call.replace(chr(10), ' ')})"
    args = f"{step.intent!r}, {one}, uses={[t for t in texts if t]!r}" + (
        f", needs={needs_list!r}" if needs_list else ""
    )
    return [f"fusion.cleanup({args})"]


def _ways(step: Step) -> str:
    assert step.target is not None
    return "[" + ", ".join(f"({s.value!r}, {v!r})" for s, v in step.target.ordered()) + "]"


def _call(step: Step) -> str:
    """The Python call a step is."""
    a, v, o = step.action, step.value, step.options
    if a is Action.NAVIGATE:
        return f"fusion.navigate({v!r})"
    if a is Action.LOGIN_AS:
        return f"fusion.login({v!r})"
    if a is Action.CLICK:
        return f"fusion.click({_ways(step)})"
    if a is Action.FILL:
        return f"fusion.fill({_ways(step)}, {v!r})"
    if a is Action.SELECT:
        pick = f", pick={o['pick']!r}" if o.get("pick") else ""
        return f"fusion.select({_ways(step)}, {v!r}{pick})"
    if a is Action.ASSERT_VISIBLE:
        return f"fusion.assert_visible({_ways(step)})"
    if a is Action.ASSERT_TEXT:
        return f"fusion.assert_text({_ways(step)}, {v!r})"
    if a is Action.WAIT_JOB:
        extra = f", timeout_s={float(o.get('timeout_s', 900))!r}, expect={str(o.get('expect', 'SUCCEEDED'))!r}"
        return f"fusion.wait_job({v!r}{extra})"
    if a is Action.API_CALL:
        args = [repr(v)]
        if o.get("body") is not None:
            args.append(f"body={o['body']!r}")
        if o.get("expect_status") is not None:
            args.append(f"expect_status={int(o['expect_status'])}")
        if o.get("check"):
            args.append(f"check={dict(o['check'])!r}")
        if o.get("save"):
            args.append(f"save={dict(o['save'])!r}")
        return f"fusion.api_call({', '.join(args)})"
    raise ValueError(f"the action {a.value} cannot be exported")


# ---------------------------------------------------------------------------------------------- names


def _ident(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or "test"


def _markers(test: TestCase) -> list[str]:
    names = [f"priority_{test.priority.value}"]
    for tag in test.tags:
        m = _ident(tag)
        names.append(f"tag_{m}" if m in _RESERVED or m[0].isdigit() else m)
    return list(dict.fromkeys(names))


def _doc(text: str) -> str:
    return _oneline(text).replace('"""', "'''").replace("\\", "/")


def _oneline(text: str) -> str:
    return " ".join(str(text).split())


def describe(written: list[Path]) -> dict[str, Any]:
    tests = [p for p in written if p.name.startswith("test_")]
    return {"tests": len(tests), "files": len(written)}
