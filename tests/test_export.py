"""Exporting tests as plain Playwright for Python files that need nothing from Quartermaster."""

from __future__ import annotations

import importlib.util
import io
import re
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from conftest import EXAMPLES
from test_service_api import app, call  # noqa: F401, F811  (app is a fixture)

from quartermaster.cli import main
from quartermaster.dsl.loader import load_test, load_tests
from quartermaster.export.playwright_py import TEMPLATES, ExportError, export, render_test


def runtime() -> ModuleType:
    """The runtime copied next to every export, loaded from its file (Playwright is needed only for a browser)."""
    spec = importlib.util.spec_from_file_location("fusion_runtime_under_test", TEMPLATES / "fusion_runtime.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RT = runtime()

SPEC = """
id: hcm.everything
title: 'Uses "every" action \\ with odd characters'
module: HCM
product: Global Human Resources
process: Workforce Structures
persona: HR Specialist
priority: high
tags: [smoke, skip, 2fast, Redwood Search]
data:
  name: QM-${RUN_ID}
  secret: ${env:QM_SECRET}
steps:
  - action: navigate
    intent: Open Locations
    value: My Client Groups > Locations
  - action: click
    intent: Click Add
    expected: The form opens
    target: {strategies: [{role: "button:Add"}, {css: "#add"}]}
  - action: fill
    intent: Type the name
    value: ${name}
    target: {strategies: [{label: Name}]}
  - action: select
    intent: Choose the status
    value: Act
    options: {pick: Active}
    target: {strategies: [{label: Status}]}
  - action: assert_visible
    intent: The form shows
    target: {strategies: [{text: New}]}
  - action: assert_text
    intent: The name shows
    value: ${name}
    target: {strategies: [{text: Name value}]}
  - action: api_call
    intent: Make it
    value: POST /hcmRestApi/resources/11.13.18.05/locationsV2
    options:
      body: {LocationName: "${name}"}
      expect_status: 201
      check: {LocationName: "${name}"}
      save: {location_id: LocationId}
  - action: wait_job
    intent: Wait for the process
    value: last
    options: {timeout_s: 60, expect: WARNING}
  - action: login_as
    intent: Switch user
    value: HR Manager
cleanup:
  - action: api_call
    intent: Remove it
    value: DELETE /hcmRestApi/resources/11.13.18.05/locationsV2/${location_id}
  - action: navigate
    intent: Go home
    value: Home
"""


def load_spec(tmp_path: Path, text: str = SPEC) -> Any:
    path = tmp_path / "t.yaml"
    path.write_text(text)
    return load_test(path)


class Calls:
    """Stands in for `fusion`: remembers what the exported test asked it to do."""

    def __init__(self, fail_on: str = "") -> None:
        self.log: list[tuple[Any, ...]] = []
        self.data: dict[str, str] = {}
        self.fail_on = fail_on

    def _do(self, name: str, *args: Any, **kw: Any) -> None:
        self.log.append((name, *args, *(sorted(kw.items()))))
        if name == self.fail_on:
            raise RuntimeError(f"{name} broke")

    def step(self, number: int, intent: str) -> Any:
        import contextlib

        @contextlib.contextmanager
        def ctx() -> Any:
            self.log.append(("step", number, intent))
            yield

        return ctx()

    def cleanup(self, intent: str, action: Any, uses: list[str], needs: list[str] | None = None) -> None:
        self.log.append(("cleanup", intent, tuple(uses), tuple(needs or ())))
        try:
            action()
        except Exception as e:
            self.log.append(("cleanup failed", str(e)))

    def __getattr__(self, name: str) -> Any:
        return lambda *a, **k: self._do(name, *a, **k)


def run_generated(source: str, fake: Calls) -> None:
    source = re.sub(r"^pytestmark = .*$", "", source, flags=re.M)  # the marks are checked on their own
    namespace: dict[str, Any] = {}
    exec(compile(source, "<exported>", "exec"), namespace)
    [fn] = [v for k, v in namespace.items() if k.startswith("test_") and callable(v)]
    fn(fake)


def test_every_action_becomes_a_readable_call_in_order(tmp_path: Path) -> None:
    src = render_test(load_spec(tmp_path), "t.yaml")
    fake = Calls()
    run_generated(src, fake)
    steps = [e for e in fake.log if e[0] != "step" and e[0] != "cleanup"]
    assert steps[0] == ("login", "HR Specialist")
    assert ("navigate", "My Client Groups > Locations") in steps
    assert ("click", [("role", "button:Add"), ("css", "#add")]) in steps
    assert ("fill", [("label", "Name")], "${name}") in steps
    assert ("select", [("label", "Status")], "Act", ("pick", "Active")) in steps
    assert ("assert_text", [("text", "Name value")], "${name}") in steps
    api = next(e for e in steps if e[0] == "api_call")
    assert api[1] == "POST /hcmRestApi/resources/11.13.18.05/locationsV2"
    assert ("body", {"LocationName": "${name}"}) in api and ("expect_status", 201) in api
    assert ("save", {"location_id": "LocationId"}) in api and ("check", {"LocationName": "${name}"}) in api
    assert ("wait_job", "last", ("expect", "WARNING"), ("timeout_s", 60.0)) in steps
    assert ("login", "HR Manager") in steps
    assert [e[1:3] for e in fake.log if e[0] == "step"][:2] == [(1, "Open Locations"), (2, "Click Add")]


def test_the_test_data_the_docstring_and_the_marks_are_written_for_a_person(tmp_path: Path) -> None:
    src = render_test(load_spec(tmp_path), "hcm/t.yaml")
    compile(src, "<exported>", "exec")  # odd characters in the title did not break the file
    assert src.startswith('"""Uses "every" action / with odd characters')
    assert (
        "Test id: hcm.everything   Module: HCM   Product: Global Human Resources   Process: Workforce Structures" in src
    )
    assert "(hcm/t.yaml)" in src and "edit freely" in src
    assert "'name': 'QM-${RUN_ID}'," in src and "${env:QM_SECRET}" in src  # a secret is a reference, never a value
    assert "# Expected: The form opens" in src
    for mark in ("priority_high", "smoke", "tag_skip", "tag_2fast", "redwood_search"):  # reserved and odd tags are safe
        assert f"pytest.mark.{mark}" in src


def test_the_cleanup_runs_in_finally_even_when_a_step_fails_and_never_hides_the_failure(tmp_path: Path) -> None:
    src = render_test(load_spec(tmp_path))
    assert "try:" in src and "finally:" in src
    fake = Calls(fail_on="fill")
    with pytest.raises(RuntimeError, match="fill broke"):
        run_generated(src, fake)
    cleanups = [e for e in fake.log if e[0] == "cleanup"]
    assert [c[1] for c in cleanups] == ["Remove it", "Go home"]  # both ran after the failure
    assert "${location_id}" in cleanups[0][2][0]  # the saved name is what the runtime checks before deleting
    calls = [e for e in fake.log if e[0] == "api_call"]  # the REST step in the middle did not run, only the cleanup's
    assert len(calls) == 1 and calls[0][1].startswith("DELETE ")


def test_a_test_without_cleanup_has_no_try_block(tmp_path: Path) -> None:
    text = "id: a.b\ntitle: T\nmodule: M\nproduct: P\nsteps:\n  - {action: navigate, intent: Go, value: Home}\n"
    src = render_test(load_spec(tmp_path, text))
    assert (
        "try:" not in src
        and "finally" not in src
        and "DATA" not in src
        and "pytestmark = [pytest.mark.priority_medium]" in src
    )


def test_shared_steps_are_part_of_the_exported_test(tmp_path: Path) -> None:
    (tmp_path / "_library").mkdir()
    (tmp_path / "_library" / "open.yaml").write_text(
        "library: open\nparams: {page: Locations}\nsteps:\n"
        "  - {action: navigate, intent: 'Open ${page}', value: 'Home > ${page}'}\n"
        "cleanup:\n  - {action: navigate, intent: Back, value: Home}\n"
    )
    text = "id: a.b\ntitle: T\nmodule: M\nproduct: P\nsteps:\n  - use: open\n    with: {page: Jobs}\n"
    src = render_test(load_spec(tmp_path, text))
    assert "fusion.navigate('Home > Jobs')" in src and "fusion.cleanup('Back'" in src and "use:" not in src


def test_all_the_example_tests_export_to_python_that_compiles() -> None:
    n = 0
    for folder in ("tests", "unverified", "smoke", "demos", "recorded"):
        path = EXAMPLES / folder
        if not path.is_dir():
            continue
        for test in load_tests(path):
            compile(render_test(test, f"{folder}/x.yaml"), f"<{test.id}>", "exec")
            n += 1
    assert n >= 15


def test_files_are_written_and_none_is_replaced_without_asking(tmp_path: Path) -> None:
    tests = [(load_spec(tmp_path), "t.yaml")]
    out = tmp_path / "out"
    written = export(tests, out)
    names = sorted(p.name for p in written)
    assert names == [
        ".gitignore",
        "README.md",
        "conftest.py",
        "fusion_runtime.py",
        "pytest.ini",
        "requirements.txt",
        "test_hcm_everything.py",
    ]
    assert (out / "pytest.ini").read_text().count("smoke: tag from Quartermaster") == 1
    assert "playwright" in (out / "requirements.txt").read_text()
    (out / "test_hcm_everything.py").write_text("# my own edits\n")
    with pytest.raises(ExportError, match="already has"):
        export(tests, out)
    assert (out / "test_hcm_everything.py").read_text() == "# my own edits\n"  # untouched
    export(tests, out, overwrite=True)
    assert "my own edits" not in (out / "test_hcm_everything.py").read_text()
    with pytest.raises(ExportError, match="no test"):
        export([], tmp_path / "none")


def test_two_tests_with_names_that_clash_get_different_files(tmp_path: Path) -> None:
    a = load_spec(
        tmp_path, "id: a.b-c\ntitle: A\nmodule: M\nproduct: P\nsteps:\n  - {action: navigate, intent: x, value: y}\n"
    )
    b = load_spec(
        tmp_path, "id: a_b.c\ntitle: B\nmodule: M\nproduct: P\nsteps:\n  - {action: navigate, intent: x, value: y}\n"
    )
    written = export([(a, "a.yaml"), (b, "b.yaml")], tmp_path / "o")
    files = sorted(p.name for p in written if p.name.startswith("test_"))
    assert files == ["test_a_b_c.py", "test_a_b_c_2.py"]
    assert "def test_a_b_c_2(fusion)" in (tmp_path / "o" / "test_a_b_c_2.py").read_text()


def test_the_conftest_and_the_readme_that_come_with_it_are_what_the_tests_need(tmp_path: Path) -> None:
    out = tmp_path / "o"
    export([(load_spec(tmp_path), "t.yaml")], out)
    conftest = (out / "conftest.py").read_text()
    compile(conftest, "conftest.py", "exec")
    assert "def fusion(" in conftest and "QM_FUSION_URL" in conftest and "QM_CHROMIUM_PATH" in conftest
    readme = (out / "README.md").read_text()
    assert "pytest -v" in readme and "QM_STORAGE_STATE" in readme and "What is not here" in readme
    assert "from quartermaster" not in (out / "fusion_runtime.py").read_text()  # nothing of Quartermaster is needed


# ------------------------------------------------------------------ the runtime that comes with it


def fusion() -> Any:
    return RT.Fusion(None, "https://abcd-dev2.fa.us6.oraclecloud.com", name="my test!")


def test_text_is_filled_in_from_data_what_steps_saved_run_id_and_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    f = fusion()
    f.data["name"] = "Pat"
    f.saved["location_id"] = "77"
    monkeypatch.setenv("QM_SECRET_X", "s3cret")
    assert f.render("Hi ${name} #${location_id} ${RUN_ID} ${env:QM_SECRET_X} ${unknown}") == (
        f"Hi Pat #77 {f.run_id} s3cret ${{unknown}}"
    )
    assert re.fullmatch(r"[0-9A-F]{8}", f.run_id) and f.name == "my_test_"
    assert f.unsaved("/x/${location_id}/${other}") == ["other"] and f.unsaved("${env:QM_SECRET_X}") == []
    monkeypatch.delenv("QM_SECRET_X")
    with pytest.raises(RT.StepFailure, match="set the environment variable QM_SECRET_X"):
        f.render("${env:QM_SECRET_X}")


def test_a_pod_that_looks_like_production_is_refused() -> None:
    RT.assert_test_pod("https://abcd-dev2.fa.us6.oraclecloud.com", {})
    RT.assert_test_pod("https://example.com", {})
    for bad, why in (
        ("http://abcd-dev2.fa.example.com", "https"),
        ("https://abcd.fa.us6.oraclecloud.com", "production"),
    ):
        with pytest.raises(RT.StepFailure, match=why):
            RT.assert_test_pod(bad, {})
    RT.assert_test_pod(
        "https://abcd.fa.us6.oraclecloud.com", {"QM_FUSION_ALLOWED_HOSTS": "abcd.fa.us6.oraclecloud.com"}
    )
    with pytest.raises(RT.StepFailure, match="not in QM_FUSION_ALLOWED_HOSTS"):
        RT.assert_test_pod("https://abcd-dev2.fa.us6.oraclecloud.com", {"QM_FUSION_ALLOWED_HOSTS": "other.example.com"})
    with pytest.raises(RT.StepFailure, match="production"):
        RT.Fusion(None, "https://abcd.fa.us6.oraclecloud.com")


def test_each_persona_has_its_own_sign_in_and_falls_back_to_the_default() -> None:
    env = {
        "QM_FUSION_USER": "u",
        "QM_FUSION_PASSWORD": "p",
        "QM_FUSION_USER_HR_MANAGER": "m",
        "QM_FUSION_PASSWORD_HR_MANAGER": "mp",
    }
    assert RT.credentials("HR Manager", env) == ("m", "mp") and RT.credentials("Other", env) == ("u", "p")
    assert RT.credentials("", env) == ("u", "p")
    with pytest.raises(RT.StepFailure, match="QM_FUSION_USER and QM_FUSION_PASSWORD"):
        RT.credentials("", {})


def test_reply_values_are_found_by_path_and_suggestions_picked_carefully() -> None:
    reply = {"items": [{"LocationId": 7, "Active": True, "Tags": []}], "count": 1}
    assert (
        RT.json_get(reply, "items[0].LocationId") == 7 and RT.as_text(RT.json_get(reply, "items[0].Active")) == "true"
    )
    for bad in ("items[1]", "nope", "count.x"):
        with pytest.raises(KeyError):
            RT.json_get(reply, bad)
    assert RT.best_option(["Inactive", "Active", "Actively"], "Active") == 1  # not "Inactive"
    assert RT.best_option(["Common Set (seeded)", "BATA US GRADE COMMON SET"], "Common Set") == 0
    assert RT.best_option(["Other"], "Active") is None


class Reply:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status, self.ok, self._body = status, 200 <= status < 300, body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no json")
        return self._body


class Context:
    """The browser context's REST client: answers the calls in order."""

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.sent: list[dict[str, Any]] = []
        self.request = self

    def fetch(self, url: str, **kw: Any) -> Reply:
        self.sent.append({"url": url, **kw})
        return self.replies.pop(0)

    def get(self, url: str, **kw: Any) -> Reply:
        self.sent.append({"url": url, **kw})
        return self.replies.pop(0)


def test_a_rest_step_checks_the_reply_and_keeps_values_for_later_steps() -> None:
    f = fusion()
    f.data["name"] = "QM-1"
    f.context = Context(Reply(201, {"LocationId": 77, "LocationName": "QM-1"}))
    f.api_call(
        "POST /hcmRestApi/x",
        body={"LocationName": "${name}"},
        expect_status=201,
        check={"LocationName": "${name}", "LocationId": "*"},
        save={"location_id": "LocationId"},
    )
    sent = f.context.sent[0]
    assert sent["url"] == "https://abcd-dev2.fa.us6.oraclecloud.com/hcmRestApi/x" and sent["method"] == "POST"
    assert sent["data"] == {"LocationName": "QM-1"} and "Content-Type" in sent["headers"]
    assert f.saved == {"location_id": "77"}
    assert f.render("/x/${location_id}") == "/x/77"


def test_a_rest_step_says_what_is_wrong_in_plain_words() -> None:
    for reply, kw, why in (
        (Reply(403), {}, "the API answered HTTP 403"),
        (Reply(200, {}), {"expect_status": 201}, "answered HTTP 200, expected 201"),
        (Reply(200, {"a": 1}), {"check": {"b": "1"}}, 'has no "b"'),
        (Reply(200, {"a": 1}), {"check": {"a": "2"}}, '"a" is "1" in the API reply, expected "2"'),
        (Reply(200, {"a": []}), {"check": {"a": "*"}}, "is empty"),
        (Reply(200, {"a": 1}), {"save": {"x": "zzz"}}, 'no "zzz" to keep as x'),
    ):
        f = fusion()
        f.context = Context(reply)
        with pytest.raises(RT.StepFailure, match=why):
            f.api_call("GET /x", **kw)
        assert f.saved == {}  # nothing is kept from a reply that was wrong


def test_a_rest_step_only_calls_the_pod_and_needs_a_method() -> None:
    f = fusion()
    f.context = Context(Reply(200, {}))
    with pytest.raises(RT.StepFailure, match="may only call the pod"):
        f.api_call("GET https://evil.example.com/x")
    with pytest.raises(RT.StepFailure, match="write the request as METHOD"):
        f.api_call("fetch things")
    f.api_call("/hcmRestApi/y")  # no method: a GET
    assert f.context.sent[0]["method"] == "GET"


def test_cleanup_skips_what_was_never_made_and_never_fails_the_test(capsys: pytest.CaptureFixture[str]) -> None:
    f = fusion()
    ran: list[str] = []
    f.cleanup("Remove it", lambda: ran.append("a"), uses=["DELETE /x/${location_id}"])
    assert ran == [] and "cleanup skipped (Remove it): 'location_id' was never saved" in capsys.readouterr().out
    f.saved["location_id"] = "5"
    f.cleanup("Remove it", lambda: ran.append("b"), uses=["DELETE /x/${location_id}"], needs=["location_id"])
    assert ran == ["b"]

    def broken() -> None:
        raise RuntimeError("403 forbidden")

    f.cleanup("Remove it", broken, uses=[])  # does not raise
    assert "CLEANUP FAILED (Remove it): RuntimeError: 403 forbidden" in capsys.readouterr().out


def test_a_failing_step_is_named_in_the_error() -> None:
    f = fusion()
    with pytest.raises(RT.StepFailure, match=r"step 3 \(Click Add\): ValueError: boom"), f.step(3, "Click Add"):
        raise ValueError("boom")


# ------------------------------------------------------------------ in a real browser


def test_the_runtime_finds_items_one_at_a_time_and_fills_a_page(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    import os

    from playwright.sync_api import sync_playwright

    page_file = tmp_path / "p.html"
    page_file.write_text(
        """<!doctype html><title>t</title>
<button id=a onclick="document.getElementById('out').textContent='added'">Add</button>
<button>Twin</button><button>Twin</button>
<label>Name <input id=n></label>
<label for=s>Status</label><select id=s><option>Active</option><option>Inactive</option></select>
<p id=out>nothing yet</p>"""
    )
    chromium = os.environ.get("QM_CHROMIUM_PATH") or (
        "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium)
        try:
            f = RT.Fusion(browser, "https://abcd-dev2.fa.us6.oraclecloud.com", evidence=tmp_path / "evidence")
            f.settle_ms = 500
            f._new_page()
            f.page.goto(page_file.as_uri())
            f.click(
                [("role", "button:Missing"), ("role", "button:Add")]
            )  # the first way finds none: the second is used
            f.assert_text([("css", "#out")], "added")
            with pytest.raises(RT.StepFailure, match="matched 2"):
                f.click([("role", "button:Twin")])  # two matches are never clicked
            f.fill([("label", "Name")], "Pat ${x}")
            assert f.page.input_value("#n") == "Pat ${x}"
            f.data["x"] = "Q"
            f.fill([("label", "Name")], "Pat ${x}")
            assert f.page.input_value("#n") == "Pat Q"
            f.select([("label", "Status")], "Inactive")
            assert f.page.input_value("#s") == "Inactive"
            with pytest.raises(RT.StepFailure, match=r"step 2 \(Click Twin\)"), f.step(2, "Click Twin"):
                f.click([("role", "button:Twin")])
            assert (tmp_path / "evidence" / f"{f.name}-step-02.png").is_file()  # the failure left a picture
            f.close()
        finally:
            browser.close()


# ------------------------------------------------------------------ the command and the pages


def test_the_command_writes_the_export_and_refuses_to_replace_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "exp"
    assert main(["export", str(EXAMPLES / "tests"), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "Wrote 4 test(s)" in text and "pytest -v" in text
    assert (out / "test_hcm_view_worker.py").is_file() and (out / "fusion_runtime.py").is_file()
    assert main(["export", str(EXAMPLES / "tests"), "--out", str(out)]) == 2
    assert "already has" in capsys.readouterr().err
    assert main(["export", str(EXAMPLES / "tests"), "--out", str(out), "--force"]) == 0
    capsys.readouterr()
    assert main(["export", str(EXAMPLES / "tests"), "--out", str(tmp_path / "one"), "--only", "hcm.view-worker"]) == 0
    assert [p.name for p in (tmp_path / "one").glob("test_*.py")] == ["test_hcm_view_worker.py"]
    capsys.readouterr()
    assert main(["export", str(EXAMPLES / "tests"), "--out", str(tmp_path / "x"), "--only", "nope"]) == 2
    assert "no test with id nope" in capsys.readouterr().err
    assert (
        main(["export", str(EXAMPLES / "tests" / "hcm" / "view_worker.yaml"), "--out", str(tmp_path / "single")]) == 0
    )


def test_the_service_gives_a_zip_of_all_tests_or_of_one(app: Any) -> None:  # noqa: F811
    from quartermaster.service.api import ApiError

    (app.tests_root / "hcm" / "broken.yaml").write_text("id: [unclosed\n")
    (app.tests_root / "hcm" / "good.yaml").write_text(
        "id: hcm.good\ntitle: Good\nmodule: HCM\nproduct: HR\nsteps:\n  - {action: navigate, intent: Go, value: Home}\n"
    )
    everything = app.handle("GET", "/api/export", b"")
    assert everything.content_type == "application/zip" and everything.download_name.endswith(".zip")
    names = zipfile.ZipFile(io.BytesIO(everything.body)).namelist()
    assert (
        "test_hcm_good.py" in names and "fusion_runtime.py" in names and "conftest.py" in names
    )  # the broken file is left out
    one = app.handle("GET", "/api/export?file=hcm/good.yaml", b"")
    assert one.download_name == "hcm.good-playwright.zip"
    assert [n for n in zipfile.ZipFile(io.BytesIO(one.body)).namelist() if n.startswith("test_")] == [
        "test_hcm_good.py"
    ]
    with pytest.raises(ApiError, match="cannot be read"):
        app.handle("GET", "/api/export?file=hcm/broken.yaml", b"")
    with pytest.raises(ApiError, match="only test files in the tests folder"):
        app.handle("GET", "/api/export?file=../x.yaml", b"")
    actions = [e["action"] for e in call(app, "GET", "/api/audit")["entries"]]
    assert "Exported tests as Playwright" in actions
