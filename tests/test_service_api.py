"""The local web service: API, recording and the HTTP guards. Stand-ins play `qm run` and `qm record`."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_evidence_suite import _suite
from test_service_queue import fake_command

from quartermaster.evidence.suite import suite_folder, write_suite_document, write_suite_record
from quartermaster.service.api import ApiError, App, make_server, port_of

# Stand-in for `qm record`: takes commands on its input until "stop" (the web UI's Finish button),
# keeping them in its feed file so a test can see they arrived, then saves a test file.
FAKE_RECORD = r"""
import json, os, sys
out, feed = sys.argv[1], sys.argv[2]
if out.endswith('no_pod.yaml'):
    print('error: set QM_FUSION_URL to the non-prod pod URL'); sys.exit(2)
commands = []
while True:
    line = sys.stdin.readline().strip()
    if line in ('', 'stop'):
        break
    commands.append(line)
    json.dump({'steps': [], 'paused': 'pause' in commands, 'commands': commands}, open(feed, 'w'))
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, 'w').write('id: x\ntitle: Recorded\nsteps: []\n')
print('Saved 0 step(s) to ' + out + '. Replay with: qm run ' + out)
"""


def fake_record(out: Path, fields: dict[str, str], evidence_root: Path, feed: Path) -> list[str]:
    return [sys.executable, "-c", FAKE_RECORD, str(out), str(feed)]


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[App]:
    monkeypatch.setenv("QM_FUSION_URL", "https://abcd-dev2.fa.us6.oraclecloud.com")
    monkeypatch.setenv("QM_FUSION_USER", "tester")
    monkeypatch.setenv("QM_FUSION_PASSWORD", "secret")
    tests = tmp_path / "tests"
    (tests / "hcm").mkdir(parents=True)
    (tests / "hcm" / "pass.yaml").write_text("id: hcm.pass\ntitle: A passing test\nmodule: HCM\nsteps: [{}, {}]\n")
    (tests / "broken.yaml").write_text("id: [unclosed\n")
    a = App(
        tests_root=tests,
        evidence_root=tmp_path / "evidence",
        data_dir=tmp_path / ".qm",
        run_command=fake_command,
        record_command=fake_record,
    )
    a.start()
    yield a
    a.stop()


def call(app: App, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    reply = app.handle(method, path, json.dumps(body).encode() if body is not None else b"")
    return json.loads(reply.body)


def wait(check: Any, timeout: float = 20) -> Any:
    end = time.time() + timeout
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_status_never_shows_the_password(app: App) -> None:
    status = call(app, "GET", "/api/status")
    assert status["ready"] and status["user"] == "tester" and status["password_set"] is True
    assert "secret" not in json.dumps(status)


def test_tests_are_listed_with_their_last_result(app: App) -> None:
    tests = {t["file"]: t for t in call(app, "GET", "/api/tests")}
    assert tests["hcm/pass.yaml"]["title"] == "A passing test" and tests["hcm/pass.yaml"]["steps"] == 2
    assert tests["hcm/pass.yaml"]["folder"] == "hcm" and tests["hcm/pass.yaml"]["last_run"] is None
    assert "Could not read this file" in tests["broken.yaml"]["problem"]

    run = call(app, "POST", "/api/runs", {"target": "hcm/pass.yaml"})
    wait(lambda: call(app, "GET", f"/api/runs/{run['id']}")["status"] == "passed")
    tests = {t["file"]: t for t in call(app, "GET", "/api/tests")}
    assert tests["hcm/pass.yaml"]["last_run"]["status"] == "passed"


def test_a_run_can_be_started_and_followed(app: App) -> None:
    run = call(app, "POST", "/api/runs", {"target": "hcm/pass.yaml", "options": {"tester": "Sai"}})
    assert run["status"] == "queued" and run["options"]["tester"] == "Sai"
    assert "events_path" not in run and "log_path" not in run  # server paths stay on the server
    done = wait(lambda: (r := call(app, "GET", f"/api/runs/{run['id']}"))["status"] == "passed" and r)
    assert [e["type"] for e in done["events"]][0] == "suite_start"
    assert call(app, "GET", f"/api/runs/{run['id']}/events?after=2") == done["events"][2:]
    assert [r["id"] for r in call(app, "GET", "/api/runs")] == [run["id"]]


def test_bad_requests_get_a_plain_answer(app: App) -> None:
    for body, message in (
        ({"target": "../outside.yaml"}, "inside the tests folder"),
        ({"target": "hcm/pass.yaml", "options": {"shell": "x"}}, "unknown run options"),
    ):
        with pytest.raises(ApiError, match=message):
            call(app, "POST", "/api/runs", body)
    with pytest.raises(ApiError, match="no such run"):
        call(app, "GET", "/api/runs/nope")
    with pytest.raises(ApiError, match="not valid JSON"):
        app.handle("POST", "/api/runs", b"{nope")


def finished_suite_run(app: App) -> dict[str, Any]:
    """A finished run of three tests: hcm.view-worker passed, hcm.create-location failed at step 2 and
    hcm.personal-info passed only because step 1 was found by its role instead of its label."""
    root = app.evidence_root
    suite = _suite(root)
    folder = suite_folder(root, "S1")
    write_suite_record(suite, folder)
    summary = write_suite_document(suite, root, folder / "summary.docx")
    run = app.queue.store.create("hcm", {"screenshots": "every-step", "video": "off"})
    app.queue.store.update(run["id"], status="failed", suite_dir=str(folder), summary=str(summary))
    return run


def add_suite_tests(app: App) -> None:
    """Test files for the three tests in finished_suite_run."""
    hcm = app.tests_root / "hcm"
    (hcm / "worker.yaml").write_text("id: hcm.view-worker\ntitle: View a worker\nmodule: HCM\nsteps: [{}]\n")
    (hcm / "location.yaml").write_text("id: hcm.create-location\ntitle: Create a location\nmodule: HCM\nsteps: [{}]\n")
    (hcm / "personal.yaml").write_text(
        "id: hcm.personal-info\ntitle: Personal info\nmodule: HCM\ndata: {name: Pat}\nsteps:\n"
        "  - action: fill\n    intent: Enter the name\n    value: ${name}\n    target:\n      strategies:\n"
        '        - label: Name\n        - role: "textbox:Name"\n'
    )


def test_finished_run_links_to_its_evidence_in_plain_words(app: App) -> None:
    run = finished_suite_run(app)

    detail = call(app, "GET", f"/api/runs/{run['id']}")
    assert detail["summary_url"] == "/files/_suites/S1/summary.docx" and detail["suite_folder"] == "_suites/S1"
    ok, failed, healed = detail["results"]
    assert ok["document_url"].endswith("hcm.view-worker_evidence.docx") and ok["folder"].startswith("hcm.view-worker/")
    assert failed["failed_step"]["error"] == 'The screen showed "Redwood City" but it should show "Redwood Shores".'
    assert failed["failed_step"]["detail"].startswith("StepFailure")
    assert healed["needs_update"] and not ok["needs_update"]

    picture = app.handle("GET", failed["failed_step"]["picture_url"], b"")
    assert picture.content_type == "image/png" and picture.body.startswith(b"\x89PNG")
    doc = app.handle("GET", detail["summary_url"], b"")
    assert doc.download_name == "summary.docx" and doc.body.startswith(b"PK")


def test_only_evidence_files_can_be_fetched_or_opened(app: App, tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("no")
    for path in ("/files/../secret.txt", "/files/..%2Fsecret.txt", "/files/%2E%2E/secret.txt"):
        with pytest.raises(ApiError, match="only files in the evidence folder"):
            app.handle("GET", path, b"")
    with pytest.raises(ApiError, match="only files in the evidence folder"):
        call(app, "POST", "/api/open", {"path": str(tmp_path)})


def test_recording_starts_stops_and_saves(app: App) -> None:
    request = {"id": "hcm.search-worker", "title": "Search", "module": "HCM", "product": "Global HR"}
    state = call(app, "POST", "/api/recording", request)
    assert state["status"] == "recording" and state["file"] == "recorded/hcm_search-worker.yaml"
    with pytest.raises(ApiError, match="already in progress"):
        call(app, "POST", "/api/recording", {**request, "id": "hcm.other"})

    for command, body in (("pause", {}), ("note", {"text": "The worker's\npage   opens"}), ("mask", {})):
        call(app, "POST", f"/api/recording/{command}", body)
    feed = wait(lambda: (s := call(app, "GET", "/api/recording"))["feed"] and len(s["feed"]["commands"]) == 3 and s)
    assert feed["feed"]["commands"] == ["pause", "note The worker's page opens", "mask"]  # one line each
    assert feed["feed"]["paused"] is True
    call(app, "POST", "/api/recording/stop", {})
    saved = wait(lambda: (s := call(app, "GET", "/api/recording"))["status"] == "saved" and s)
    assert saved["message"] == "Saved 0 step(s)."
    assert (app.tests_root / "recorded" / "hcm_search-worker.yaml").is_file()

    with pytest.raises(ApiError, match="already exists"):  # never overwrite a test
        call(app, "POST", "/api/recording", request)


def test_recording_requests_are_checked_and_failures_explained(app: App) -> None:
    base = {"id": "hcm.x", "title": "T", "module": "HCM", "product": "P"}
    for change, message in (
        ({"title": ""}, "title is required"),
        ({"id": "Has Spaces"}, "id may use lower-case"),
        ({"file": "../../elsewhere.yaml"}, "inside the tests folder"),
        ({"shell": "x"}, "unknown fields"),
    ):
        with pytest.raises(ApiError, match=message):
            call(app, "POST", "/api/recording", {**base, **change})

    call(app, "POST", "/api/recording", {**base, "file": "no_pod.yaml"})
    failed = wait(lambda: (s := call(app, "GET", "/api/recording"))["status"] == "error" and s)
    assert failed["message"] == "set QM_FUSION_URL to the non-prod pod URL"
    with pytest.raises(ApiError, match="no recording is in progress"):
        call(app, "POST", "/api/recording/pause", {})


def test_http_guards(app: App) -> None:
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port_of(server)}"

    def request(path: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(base + path, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    try:
        status, page = request("/")
        assert status == 200 and b"<title>Quartermaster</title>" in page
        assert request("/api/status", headers={"Host": "evil.example"})[0] == 403  # DNS rebinding
        body = json.dumps({"target": "hcm/pass.yaml"}).encode()
        as_json = {"Content-Type": "application/json"}
        assert request("/api/runs", body, {**as_json, "Origin": "http://evil.example"})[0] == 403  # other sites
        assert request("/api/runs", b"target=hcm", {"Content-Type": "application/x-www-form-urlencoded"})[0] == 415
        assert request("/api/runs", body, {**as_json, "Origin": base})[0] == 201
        assert request("/nothing")[0] == 404
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_counts_each_tests_latest_result(app: App) -> None:
    add_suite_tests(app)
    finished_suite_run(app)
    dash = call(app, "GET", "/api/dashboard")
    assert (dash["tests"], dash["tested"], dash["passing"], dash["failing"], dash["pass_rate"]) == (4, 3, 2, 1, 67)
    assert (dash["coverage"], dash["never_run"]) == (75, 1)
    assert dash["activity"][-1] == {**dash["activity"][-1], "total": 3, "passed": 2, "failed": 1, "release": "26D"}
    assert dash["readiness"] == {"release": "", "total": 4}  # no release chosen yet
    assert dash["releases"] == [{"release": "26D", "tested": 3, "passed": 2, "failed": 1, "pass_rate": 67}]
    hcm = next(m for m in dash["modules"] if m["module"] == "HCM")
    assert hcm == {"module": "HCM", "tests": 4, "passing": 2, "failing": 1, "not_run": 1}
    tests = {t["file"]: t for t in call(app, "GET", "/api/tests")}
    assert tests["hcm/location.yaml"]["last_result"]["status"] == "failed"  # it ran as part of a folder
    assert tests["hcm/pass.yaml"]["last_result"] is None
    assert tests["hcm/personal.yaml"]["release_validated"] == "26D"


def test_release_readiness_counts_each_test_once(app: App) -> None:
    add_suite_tests(app)
    finished_suite_run(app)  # ran on 26D
    call(app, "POST", "/api/settings", {"release": "26D", "environment_name": "EIIV DEV2"})
    ready = call(app, "GET", "/api/dashboard")["readiness"]
    assert {k: ready[k] for k in ("release", "total", "validated", "failing", "baselined", "awaiting")} == {
        "release": "26D",
        "total": 4,
        "validated": 2,
        "failing": 1,
        "baselined": 0,
        "awaiting": 1,
    }
    call(app, "POST", "/api/settings", {"release": "26E"})
    ready = call(app, "GET", "/api/dashboard")["readiness"]
    assert (ready["validated"], ready["baselined"], ready["awaiting"]) == (0, 2, 2)  # passed on 26D only
    status = call(app, "GET", "/api/status")
    assert (status["release"], status["environment_name"]) == ("26E", "EIIV DEV2")
    run = call(app, "POST", "/api/runs", {"target": "hcm/pass.yaml"})
    assert run["options"]["release"] == "26E"  # new runs are labelled with the environment's release
    with pytest.raises(ApiError, match="release may use"):
        call(app, "POST", "/api/settings", {"release": "<b>"})
    with pytest.raises(ApiError, match="unknown settings"):
        call(app, "POST", "/api/settings", {"password": "x"})


def test_certification_pack_for_a_release(app: App) -> None:
    import io
    import re
    import zipfile

    add_suite_tests(app)
    finished_suite_run(app)  # ran on 26D
    reply = app.handle("GET", "/api/certification?release=26D", b"")
    assert reply.content_type == "application/zip" and reply.download_name == "certification_26D.zip"
    pack = zipfile.ZipFile(io.BytesIO(reply.body))
    names = pack.namelist()
    assert "Certification 26D.docx" in names
    assert len([n for n in names if n.startswith("evidence/") and n.endswith(".docx")]) == 3
    word = zipfile.ZipFile(io.BytesIO(pack.read("Certification 26D.docx"))).read("word/document.xml").decode()
    text = " ".join(re.sub(r"<[^>]+>", " ", word).split())
    assert "SOME TESTS FAILED" in text and "Oracle release 26D" in text
    assert "Create a location" in text and "Redwood Shores" in text  # what failed, in plain words
    assert "Not yet run on this release" in text and "A passing test" in text  # hcm/pass.yaml has no run

    with pytest.raises(ApiError, match="set the Oracle release"):
        app.handle("GET", "/api/certification", b"")
    empty = zipfile.ZipFile(io.BytesIO(app.handle("GET", "/api/certification?release=27A", b"").body))
    assert empty.namelist() == ["Certification 27A.docx"]


def test_needs_attention_explains_and_drafts(app: App) -> None:
    add_suite_tests(app)
    # an earlier run on 26C where every test passed, then the 26D run where one failed
    root = app.evidence_root
    first = finished_suite_run(app)
    suite = json.loads((suite_folder(root, "S1") / "suite.json").read_text())
    earlier = json.loads(json.dumps(suite))
    earlier["release"] = "26C"
    for e in earlier["runs"]:
        e["status"], e["failed_step"] = "passed", None
    write_suite_record(earlier, suite_folder(root, "S0"))
    app.queue.store.update(first["id"], suite_dir=str(suite_folder(root, "S0")), status="passed")
    later = app.queue.store.create("hcm", {"screenshots": "every-step", "video": "off"})
    app.queue.store.update(later["id"], status="failed", suite_dir=str(suite_folder(root, "S1")))

    items = call(app, "GET", "/api/attention")["items"]
    failed = next(i for i in items if i.get("test_id") == "hcm.create-location" and i.get("step"))
    assert failed["cause"]["key"] == "release_change" and failed["cause"]["sr"]
    draft = call(app, "GET", f"/api/attention/sr?run={later['id']}&test=hcm.create-location")
    assert "after the update to 26D" in draft["subject"] and "It worked on release 26C" in draft["text"]
    assert 'The screen shows "Redwood City".' in draft["text"]
    with pytest.raises(ApiError, match="no failure"):
        call(app, "GET", f"/api/attention/sr?run={later['id']}&test=hcm.view-worker")


def test_pod_check_reports_what_happened(app: App) -> None:
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Pod(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302 if self.path == "/" else 503)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    pod = HTTPServer(("127.0.0.1", 0), Pod)
    threading.Thread(target=pod.serve_forever, daemon=True).start()
    try:
        from quartermaster.service.settings import check_pod

        assert check_pod("")["ok"] is False
        up = check_pod(f"http://127.0.0.1:{pod.server_address[1]}/")
        assert up["ok"] is True and up["message"].startswith("The pod answered")
        down = check_pod(f"http://127.0.0.1:{pod.server_address[1]}/broken")
        assert down["ok"] is False and down["status_code"] == 503
        assert check_pod("http://127.0.0.1:1/", timeout=2)["message"].startswith("Could not reach the pod")
    finally:
        pod.shutdown()
        pod.server_close()


def test_needs_attention_and_accepting_an_update(app: App) -> None:
    add_suite_tests(app)
    finished_suite_run(app)
    todo = call(app, "GET", "/api/attention")
    assert todo["counts"] == {"assertion": 1, "ui_change": 1, "unreadable": 1} and todo["count"] == 3
    (failure,) = [i for i in todo["items"] if i["category"] == "assertion"]
    assert failure["test_id"] == "hcm.create-location" and failure["step"] == 2
    assert failure["error"] == 'The screen showed "Redwood City" but it should show "Redwood Shores".'
    assert failure["compare"] == {"expected": "Redwood Shores", "observed": "Redwood City"}
    assert failure["picture_url"].endswith("step-02.png") and failure["last_good_release"] is None
    (update,) = [i for i in todo["items"] if i["category"] == "ui_change"]
    assert (update["file"], update["step"], update["new"]) == ("hcm/personal.yaml", 1, ["role", "textbox:Name"])
    assert update["old_text"] == 'the field labelled "Name"' and update["new_text"] == 'the textbox named "Name"'
    assert update["last_good_release"] == "26D"
    assert [i["file"] for i in todo["items"] if i["category"] == "unreadable"] == ["broken.yaml"]

    done = call(app, "POST", "/api/test/accept-update", {"file": update["file"], "step_index": 0, "new": update["new"]})
    assert Path(done["backup"]).is_file()
    assert "ui_change" not in call(app, "GET", "/api/attention")["counts"]  # the file now tries the role first
    with pytest.raises(ApiError, match="already tries this first"):
        call(app, "POST", "/api/test/accept-update", {"file": update["file"], "step_index": 0, "new": update["new"]})
    with pytest.raises(ApiError, match="only test files in the tests folder"):
        call(app, "POST", "/api/test/accept-update", {"file": "../x.yaml", "step_index": 0, "new": ["a", "b"]})


def test_needs_attention_items_can_be_dismissed_until_they_fail_again(app: App) -> None:
    add_suite_tests(app)
    first = finished_suite_run(app)
    todo = call(app, "GET", "/api/attention")
    (failure,) = [i for i in todo["items"] if i["category"] == "assertion"]
    assert call(app, "POST", "/api/attention/dismiss", {"keys": [failure["key"], "made-up"]}) == {"dismissed": 1}
    after = call(app, "GET", "/api/attention")
    assert "assertion" not in after["counts"] and after["count"] == 2 and after["dismissed"] == 1
    entries = json.loads(app.handle("GET", "/api/audit", b"").body)["entries"]
    assert entries[0]["action"] == "Dismissed from Needs attention"

    # the same test failing on a later run shows up again
    again = app.queue.store.create("hcm", {"screenshots": "every-step", "video": "off"})
    app.queue.store.update(again["id"], status="failed", suite_dir=app.queue.store.get(first["id"])["suite_dir"])
    back = call(app, "GET", "/api/attention")
    assert back["counts"].get("assertion") == 1 and back["dismissed"] == 0
    with pytest.raises(ApiError, match="choose what to dismiss"):
        call(app, "POST", "/api/attention/dismiss", {"keys": []})


def test_a_run_that_could_not_finish_says_why_in_plain_words() -> None:
    from quartermaster.service.insights import plain_run_error

    crashed = "Exception: BrowserContext.new_page: Connection closed while reading from the driver"
    assert plain_run_error(crashed).startswith("The browser or Quartermaster stopped while the run was going.")
    assert "No pod is set up" in plain_run_error("error: set QM_FUSION_URL to the non-prod pod URL")
    assert plain_run_error("something new") == ""


def test_test_detail_speaks_plainly(app: App) -> None:
    add_suite_tests(app)
    finished_suite_run(app)
    detail = call(app, "GET", "/api/test?file=hcm/personal.yaml")
    (step,) = detail["steps_detail"]
    assert step["value"] == "Pat"  # test data shown instead of ${name}
    assert step["found_by"] == ['the field labelled "Name"', 'the textbox named "Name"']
    assert detail["history"][0]["status"] == "healed" and detail["pass_rate"] == 100
    assert detail["yaml"].startswith("id: hcm.personal-info")
    with pytest.raises(ApiError, match="no such test file"):
        call(app, "GET", "/api/test?file=hcm/missing.yaml")
    with pytest.raises(ApiError, match="only test files"):
        call(app, "GET", "/api/test?file=../../etc/passwd")


def test_a_run_that_could_not_start_needs_attention(app: App) -> None:
    (app.tests_root / "crash.yaml").write_text("id: crash\ntitle: Crash\nmodule: HCM\nsteps: [{}]\n")
    run = call(app, "POST", "/api/runs", {"target": "crash.yaml"})
    wait(lambda: call(app, "GET", f"/api/runs/{run['id']}")["status"] == "error")
    (item,) = [i for i in call(app, "GET", "/api/attention")["items"] if i["category"] == "could_not_run"]
    assert item["title"] == "Crash" and item["error"] == "something went wrong before any test ran"
    assert item["run_id"] == run["id"]


def test_release_impact_lists_imports_and_plans(tmp_path: Path) -> None:
    import base64
    import shutil

    from conftest import EXAMPLES
    from test_release_import import ORACLE_ROWS, make_xlsx

    tests = tmp_path / "tests"
    shutil.copytree(EXAMPLES / "tests", tests)
    (tests / "broken.yaml").write_text("id: [unclosed\n")
    a = App(
        tests_root=tests,
        evidence_root=tmp_path / "ev",
        data_dir=tmp_path / ".qm",
        releases_root=EXAMPLES / "releases",
    )

    [sample] = call(a, "GET", "/api/releases")
    assert sample["name"] == "26D_sample.json" and sample["id"] == "26D" and sample["source"] == "folder"

    plan = call(a, "GET", "/api/releases/plan?name=26D_sample.json")
    assert plan["release"] == "26D" and plan["summary"]["features"] == len(plan["features"])
    rows = {r["id"]: r for r in plan["tests"]}
    assert set(rows) == {"hcm.view-worker", "hcm.view-my-personal-info", "hcm.hire-page-opens", "hcm.create-location"}
    assert rows["hcm.view-worker"]["file"] == "hcm/view_worker.yaml" and rows["hcm.view-worker"]["reasons"]
    assert all(r["features"] == ["HCM-CORE-005"] for r in rows.values())
    coverage = {f["id"]: f["coverage"] for f in plan["features"]}
    assert coverage["HCM-CORE-005"] == "covered" and coverage["SCM-OM-007"] == "none"
    assert plan["problems"][0]["file"] == "broken.yaml"
    tight = call(a, "GET", "/api/releases/plan?name=26D_sample.json&budget=1")
    assert tight["budget"] == 1 and tight["summary"]["selected"] < plan["summary"]["selected"]

    content = base64.b64encode(make_xlsx({"F": ORACLE_ROWS})).decode()
    upload = {"name": "26C.xlsx", "content": content, "release_id": "26C"}
    preview = call(a, "POST", "/api/releases/import", upload)
    assert preview["features"] == 2 and not preview["saved"] and not (tmp_path / ".qm" / "releases").exists()
    saved = call(a, "POST", "/api/releases/import", {**upload, "save": True})
    assert saved["saved"] and saved["name"] == "26C.json"
    assert [r["name"] for r in call(a, "GET", "/api/releases")] == ["26D_sample.json", "26C.json"]
    assert call(a, "GET", "/api/releases/plan?name=26C.json")["summary"]["features"] == 2

    for path, body, message in (
        ("/api/releases/plan?name=nope.json", None, "no release list named"),
        ("/api/releases/plan?name=26C.json&budget=lots", None, "could not convert"),
        ("/api/releases/import", {**upload, "release_id": ""}, "release id"),
        ("/api/releases/import", {**upload, "content": "%%%"}, "did not arrive whole"),
    ):
        with pytest.raises(ApiError, match=message):
            a.handle("POST" if body else "GET", path, json.dumps(body or {}).encode())
