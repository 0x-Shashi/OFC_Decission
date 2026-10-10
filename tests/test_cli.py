from __future__ import annotations

import pytest
from conftest import EXAMPLES

from quartermaster.cli import main


def test_cli_validate(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(EXAMPLES / "tests")]) == 0
    assert "4 test spec(s) valid" in capsys.readouterr().out  # checked against a pod
    assert main(["validate", str(EXAMPLES / "unverified")]) == 0
    assert "6 test spec(s) valid" in capsys.readouterr().out  # examples, not yet checked


def test_cli_plan(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "plan",
            "--release",
            str(EXAMPLES / "releases" / "26D_sample.json"),
            "--tests",
            str(EXAMPLES / "unverified"),
            "--explain",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "ap.create-invoice-po-match" in out
    assert "SCM-OM-007" in out  # reported as uncovered


def test_cli_bad_spec_returns_2(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "x.yaml").write_text("id: [\n")
    assert main(["validate", str(tmp_path)]) == 2
    assert "invalid YAML" in capsys.readouterr().err


def test_cli_run_uses_driver_and_reports(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from conftest import FakeDriver

    from quartermaster import cli

    monkeypatch.setenv("QM_FUSION_URL", "https://abcd-dev2.fa.us6.oraclecloud.com")
    page = {("css", "a[title='Navigator']"): 1, ("xpath", "//*[starts-with(normalize-space(text()), 'Welcome,')]"): 1}
    monkeypatch.setattr(cli, "driver_factory", lambda args, run_dir: FakeDriver(page))
    report = tmp_path / "r.json"
    rc = main(
        ["run", str(EXAMPLES / "smoke" / "login.yaml"), "--report", str(report), "--evidence", str(tmp_path / "ev")]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "HEALED  smoke.login" in out  # role locator missing on fake page, css fallback used
    assert json.loads(report.read_text())[0]["test_id"] == "smoke.login"


def test_cli_run_requires_url(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("QM_FUSION_URL", raising=False)
    assert main(["run", str(EXAMPLES / "smoke")]) == 2
    assert "QM_FUSION_URL" in capsys.readouterr().err


def test_cli_run_refuses_prod_looking_pod(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from conftest import FakeDriver

    from quartermaster import cli

    monkeypatch.setenv("QM_FUSION_URL", "https://abcd.fa.us6.oraclecloud.com")
    monkeypatch.setattr(cli, "driver_factory", lambda args, run_dir: FakeDriver())
    assert main(["run", str(EXAMPLES / "smoke")]) == 2
    assert "production" in capsys.readouterr().err


def test_cli_run_failure_exit_code(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from conftest import FakeDriver

    from quartermaster import cli

    monkeypatch.setenv("QM_FUSION_URL", "https://abcd-dev2.fa.us6.oraclecloud.com")
    monkeypatch.setattr(cli, "driver_factory", lambda args, run_dir: FakeDriver())
    assert main(["run", str(EXAMPLES / "smoke"), "--evidence", str(tmp_path)]) == 1
    assert "FAILED  smoke.login" in capsys.readouterr().out


def test_cli_run_writes_run_folder_record_and_word_document(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json
    import zipfile

    from conftest import FakeDriver

    from quartermaster import cli

    monkeypatch.setenv("QM_FUSION_URL", "https://abcd-dev2.fa.us6.oraclecloud.com")
    page = {("role", "link:Navigator"): 1, ("xpath", "//*[starts-with(normalize-space(text()), 'Welcome,')]"): 1}
    monkeypatch.setattr(cli, "driver_factory", lambda args, run_dir: FakeDriver(page, evidence_dir=run_dir))
    evidence = tmp_path / "evidence"
    rc = main(
        [
            "run",
            str(EXAMPLES / "smoke" / "login.yaml"),
            "--evidence",
            str(evidence),
            "--screenshots",
            "every-step",
            "--evidence-doc",
            "--release",
            "26C",
            "--tester",
            "Test Person",
        ]
    )
    assert rc == 0, capsys.readouterr()

    [run_dir] = list((evidence / "smoke.login").iterdir())  # evidence/<test id>/<run id>/
    record = json.loads((run_dir / "run.json").read_text())
    assert record["status"] == "passed" and record["release"] == "26C" and record["executed_by"] == "Test Person"
    assert record["screenshots"] == "every-step" and record["video"] == "off"
    assert [s["evidence"] for s in record["steps"]] == [["screenshots/step-01.png"], ["screenshots/step-02.png"]]
    assert set(record["evidence_sha256"]) == {"screenshots/step-01.png", "screenshots/step-02.png"}
    assert len(record["test_file_sha256"]) == 64

    [doc] = list(run_dir.glob("*_evidence.docx"))
    assert doc.name == f"smoke.login_{record['run_id']}_evidence.docx"
    assert len([n for n in zipfile.ZipFile(doc).namelist() if n.startswith("word/media/")]) == 2

    # the document can be rebuilt later from the saved folder
    rebuilt = tmp_path / "again.docx"
    assert main(["document", str(run_dir), "--out", str(rebuilt)]) == 0
    assert rebuilt.exists()

    # a summary is written even for a single test
    [summary] = list((evidence / "_suites").glob("*/suite_*_summary.docx"))
    assert zipfile.is_zipfile(summary)


def test_cli_run_of_a_folder_writes_suite_record_and_summary(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json
    import zipfile

    from conftest import FakeDriver

    from quartermaster import cli

    specs = tmp_path / "suite"
    specs.mkdir()
    login = (EXAMPLES / "smoke" / "login.yaml").read_text()
    (specs / "a.yaml").write_text(login)
    (specs / "b.yaml").write_text(login.replace("id: smoke.login", "id: smoke.login-again"))

    monkeypatch.setenv("QM_FUSION_URL", "https://abcd-dev2.fa.us6.oraclecloud.com")
    page = {("role", "link:Navigator"): 1, ("xpath", "//*[starts-with(normalize-space(text()), 'Welcome,')]"): 1}
    monkeypatch.setattr(cli, "driver_factory", lambda args, run_dir: FakeDriver(page, evidence_dir=run_dir))
    evidence = tmp_path / "evidence"
    assert main(["run", str(specs), "--evidence", str(evidence), "--evidence-doc", "--release", "26D"]) == 0
    out = capsys.readouterr().out

    [suite_dir] = list((evidence / "_suites").iterdir())
    suite = json.loads((suite_dir / "suite.json").read_text())
    assert suite["status"] == "passed" and suite["release"] == "26D"
    assert [r["test_id"] for r in suite["runs"]] == ["smoke.login", "smoke.login-again"]
    assert all(r["document"] and (evidence / r["document"]).is_file() for r in suite["runs"])
    [summary] = list(suite_dir.glob("suite_*_summary.docx"))
    assert "Summary document:" in out and zipfile.is_zipfile(summary)

    rebuilt = tmp_path / "again.docx"
    assert main(["document", str(suite_dir), "--out", str(rebuilt)]) == 0
    assert zipfile.is_zipfile(rebuilt)


def test_cli_run_only_some_tests_of_a_folder(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from conftest import FakeDriver

    from quartermaster import cli

    specs = tmp_path / "suite"
    specs.mkdir()
    login = (EXAMPLES / "smoke" / "login.yaml").read_text()
    for name in ("a", "b", "c"):
        (specs / f"{name}.yaml").write_text(login.replace("id: smoke.login", f"id: smoke.{name}"))
    monkeypatch.setenv("QM_FUSION_URL", "https://abcd-dev2.fa.us6.oraclecloud.com")
    page = {("role", "link:Navigator"): 1, ("xpath", "//*[starts-with(normalize-space(text()), 'Welcome,')]"): 1}
    monkeypatch.setattr(cli, "driver_factory", lambda args, run_dir: FakeDriver(page, evidence_dir=run_dir))
    evidence = str(tmp_path / "evidence")
    assert main(["run", str(specs), "--evidence", evidence, "--only", "smoke.c,smoke.a"]) == 0
    out = capsys.readouterr().out
    assert "smoke.a" in out and "smoke.c" in out and "smoke.b" not in out and "2/2 passed" in out

    assert main(["run", str(specs), "--evidence", evidence, "--only", "smoke.a,smoke.nope"]) == 2
    assert "no test with id smoke.nope" in capsys.readouterr().err


def test_serve_keeps_your_tests_apart_from_the_examples(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    from quartermaster.cli import DEFAULT_TESTS, _tests_folder

    shutil.copytree(EXAMPLES / "tests", tmp_path / "examples" / "tests")
    monkeypatch.chdir(tmp_path)
    folder = _tests_folder(DEFAULT_TESTS)  # first start: created with copies of the examples
    assert folder == tmp_path.joinpath(DEFAULT_TESTS).relative_to(tmp_path)
    assert sorted(p.name for p in (tmp_path / DEFAULT_TESTS / "hcm").iterdir()) == sorted(
        p.name for p in (EXAMPLES / "tests" / "hcm").iterdir()
    )
    assert "Created my_tests" in capsys.readouterr().out
    (tmp_path / DEFAULT_TESTS / "mine.yaml").write_text("id: x\n")
    assert _tests_folder(DEFAULT_TESTS) is not None and (tmp_path / DEFAULT_TESTS / "mine.yaml").is_file()  # kept
    assert "Created" not in capsys.readouterr().out

    assert _tests_folder("examples/tests") is not None
    assert "next to the examples" in capsys.readouterr().out
    assert _tests_folder("nowhere") is None
    assert "no tests folder" in capsys.readouterr().err


def test_starting_again_opens_the_quartermaster_already_running(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import threading
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from quartermaster.cli import _answers

    class Running(BaseHTTPRequestHandler):
        server_version = "Quartermaster"

        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Running)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    monkeypatch.chdir(tmp_path)  # no tests folder here: a second start must not need one
    try:
        assert _answers(f"http://127.0.0.1:{port}")
        assert main(["serve", "--port", str(port)]) == 0
        assert opened == [f"http://127.0.0.1:{port}"]
        assert "already running" in capsys.readouterr().out
        assert not (tmp_path / ".qm").exists()  # nothing set up by the second start
    finally:
        server.shutdown()
        server.server_close()
    assert not _answers(f"http://127.0.0.1:{port}")


def test_start_files_and_the_desktop_icon(capsys: pytest.CaptureFixture[str]) -> None:
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    start = (root / "Start Quartermaster.bat").read_text()
    assert 'cd /d "%~dp0"' in start and "quartermaster.cli serve" in start and "quartermaster.cli shortcut" in start
    assert "playwright install chromium" in start  # the first start installs the browser too
    assert "git pull" in (root / "Update Quartermaster.bat").read_text()
    assert "*.bat text eol=crlf" in (root / ".gitattributes").read_text()  # double-click needs Windows line ends
    if sys.platform != "win32":
        assert main(["shortcut"]) == 0
        assert "Windows only" in capsys.readouterr().out
