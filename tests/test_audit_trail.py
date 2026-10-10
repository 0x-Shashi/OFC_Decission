"""The audit trail: lines chained by hash, so a change shows; and an export an auditor can check."""

from __future__ import annotations

import io
import json
import threading
import zipfile
from pathlib import Path
from typing import Any

import pytest
from test_service_api import app, call  # noqa: F401, F811  (app is a fixture)

from quartermaster import cli
from quartermaster.service import auditexport
from quartermaster.service.api import ApiError
from quartermaster.service.audit import AuditLog, check_export, check_lines, digest
from quartermaster.service.auth import required_role


def make(tmp_path: Path, n: int = 5) -> AuditLog:
    log = AuditLog(tmp_path / "audit.jsonl", who=lambda: "pat")
    for i in range(n):
        log.add(f"Did thing {i}", f"subject {i}", {"n": i})
    return log


def lines(log: AuditLog) -> list[str]:
    return log.path.read_text(encoding="utf-8").splitlines()


def put(log: AuditLog, text: list[str]) -> None:
    log.path.write_text("\n".join(text) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ the chain


def test_each_line_names_the_hash_of_the_line_before_it(tmp_path: Path) -> None:
    log = make(tmp_path)
    entries = [json.loads(x) for x in lines(log)]
    assert entries[0]["prev"] == "start"
    assert all(entries[i]["prev"] == entries[i - 1]["hash"] for i in range(1, 5))
    assert all(e["hash"] == digest(e["prev"], e) for e in entries)
    got = log.verify()
    assert got["ok"] and got["entries"] == 5 and got["hashed"] == 5 and got["legacy"] == 0
    assert got["head"] == entries[-1]["hash"] and got["problem"] is None


def test_an_empty_or_missing_log_is_whole(tmp_path: Path) -> None:
    got = AuditLog(tmp_path / "none.jsonl").verify()
    assert got["ok"] and got["entries"] == 0 and got["head"] is None


def test_lines_written_before_hashing_are_covered_by_the_first_hash(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    old = [
        json.dumps(
            {"at": f"2026-01-0{i}T10:00:00+00:00", "who": "old", "action": f"Old {i}", "subject": "", "details": {}}
        )
        for i in (1, 2)
    ]
    path.write_text("\n".join(old) + "\n", encoding="utf-8")
    log = AuditLog(path, who=lambda: "pat")
    log.add("New one", "x")
    log.add("New two", "x")
    got = log.verify()
    assert got["ok"] and got["legacy"] == 2 and got["hashed"] == 2
    put(log, [old[0].replace("Old 1", "Edited"), *lines(log)[1:]])  # an old line changed afterwards
    broken = log.verify()
    assert not broken["ok"] and broken["problem"] == {"line": 3, "why": "the lines before the chain began were changed"}


def test_a_log_that_stopped_in_the_middle_of_a_line_goes_on_without_breaking(tmp_path: Path) -> None:
    log = make(tmp_path, 2)
    with log.path.open("a", encoding="utf-8") as f:
        f.write('{"at": "2026-10-03T10:00:00+00:00", "who": "pat", "act')  # the computer stopped here
    log.add("After the stop", "x")
    got = log.verify()
    assert got["ok"] and got["entries"] == 3
    assert [e["action"] for e in log.entries()][0] == "After the stop"


def test_many_writers_at_once_keep_one_whole_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    logs = [AuditLog(path, who=lambda n=n: f"w{n}") for n in range(4)]  # four objects, one file (a Hub and an App)

    def write(log: AuditLog) -> None:
        for i in range(25):
            log.add("Wrote", "x", {"i": i})

    threads = [threading.Thread(target=write, args=(lg,)) for lg in logs]
    [t.start() for t in threads]
    [t.join() for t in threads]
    got = logs[0].verify()
    assert got["ok"] and got["entries"] == 100


# ------------------------------------------------------------------ somebody changes the file


def test_a_changed_line_is_found(tmp_path: Path) -> None:
    log = make(tmp_path)
    rows = lines(log)
    rows[2] = rows[2].replace("subject 2", "subject TWO")
    put(log, rows)
    assert log.verify()["problem"] == {"line": 3, "why": "this line was changed"}


def test_a_removed_line_is_found_at_the_line_after_it(tmp_path: Path) -> None:
    log = make(tmp_path)
    rows = lines(log)
    del rows[2]
    put(log, rows)
    got = log.verify()
    assert not got["ok"] and got["problem"]["line"] == 3
    assert got["problem"]["why"] == "the line before it was changed, removed or moved"


def test_lines_put_in_another_order_are_found(tmp_path: Path) -> None:
    log = make(tmp_path)
    rows = lines(log)
    rows[1], rows[2] = rows[2], rows[1]
    put(log, rows)
    assert not log.verify()["ok"]


def test_a_line_added_by_hand_is_found(tmp_path: Path) -> None:
    log = make(tmp_path)
    rows = lines(log)
    forged = json.dumps(
        {"at": "2026-10-03T10:00:00+00:00", "who": "x", "action": "Approved a release", "subject": "26D", "details": {}}
    )
    put(log, [*rows[:3], forged, *rows[3:]])
    assert log.verify()["problem"] == {"line": 4, "why": "a line without a hash was added after the chain began"}
    entry = json.loads(rows[1])
    entry["who"] = "someone else"  # a forged line cannot borrow another line's hash either
    put(log, [rows[0], json.dumps(entry), *rows[2:]])
    assert log.verify()["problem"]["line"] == 2


def test_the_first_line_cannot_be_dropped(tmp_path: Path) -> None:
    log = make(tmp_path)
    put(log, lines(log)[1:])
    assert log.verify()["problem"] == {"line": 1, "why": "the first line does not start the log"}


def test_lines_cut_off_the_end_are_not_seen_by_the_chain_but_by_an_earlier_export(tmp_path: Path) -> None:
    log = make(tmp_path)
    _, _, manifest = auditexport.build(log, fmt="jsonl", exported_by="pat")
    put(log, lines(log)[:-2])  # the newest two lines removed
    assert log.verify()["ok"]  # nothing in the chain points at them
    kept = manifest["log"]
    assert "not in the log any more" in log.holds(kept["head"], kept["entries"])
    full = make(tmp_path / "other")
    _, _, m2 = auditexport.build(full, fmt="jsonl", exported_by="pat")
    assert full.holds(m2["log"]["head"], m2["log"]["entries"]) == ""
    full.add("More", "x")
    assert full.holds(m2["log"]["head"], m2["log"]["entries"]) == ""  # a log that grew is consistent


# ------------------------------------------------------------------ picking lines


def test_lines_are_picked_by_date_person_action_and_words(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl", who=lambda: "pat")
    log.add("Started a run", "hcm/a.yaml", {"release": "26D"})
    log.add("Approved a release", "26D", {}, who="Sam Approver")
    log.add("Signed in", "sam", {}, who="Sam Approver")
    today = log.entries()[0]["at"][:10]
    assert [e["action"] for e in log.select()] == ["Started a run", "Approved a release", "Signed in"]  # oldest first
    assert [e["action"] for e in log.select(who="sam")] == ["Approved a release", "Signed in"]
    assert [e["action"] for e in log.select(action="approved")] == ["Approved a release"]
    assert [e["action"] for e in log.select(text="26d")] == ["Started a run", "Approved a release"]
    assert len(log.select(since=today, until=today)) == 3
    assert log.select(since="2999-01-01") == [] and log.select(until="2000-01-01") == []


# ------------------------------------------------------------------ the export


def unzip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_an_export_is_a_zip_with_the_lines_a_manifest_and_how_to_check_it(tmp_path: Path) -> None:
    log = make(tmp_path)
    data, name, manifest = auditexport.build(log, fmt="jsonl", exported_by="Sam Approver")
    files = unzip(data)
    assert sorted(files) == ["HOW_TO_VERIFY.txt", "audit.jsonl", "manifest.json"]
    assert name.startswith("quartermaster-audit-") and name.endswith(".zip")
    assert json.loads(files["manifest.json"]) == manifest
    assert manifest["entries"] == 5 and manifest["complete"] is True and manifest["filters"] == {}
    assert manifest["exported_by"] == "Sam Approver" and manifest["format"] == "jsonl"
    import hashlib

    assert manifest["sha256"] == hashlib.sha256(files["audit.jsonl"]).hexdigest()
    assert manifest["log"]["chain"] == "intact" and manifest["log"]["head"] == log.verify()["head"]
    assert (
        manifest["first_at"]
        and manifest["last_at"]
        and "qm audit verify audit.jsonl" in files["HOW_TO_VERIFY.txt"].decode()
    )
    assert check_export(files["audit.jsonl"])["ok"]


def test_a_csv_export_has_the_hashes_in_its_last_two_columns(tmp_path: Path) -> None:
    data, _, manifest = auditexport.build(make(tmp_path, 2), fmt="csv", exported_by="pat")
    text = unzip(data)["audit.csv"].decode("utf-8-sig").splitlines()
    assert text[0] == "When,Who,What,Subject,Details,Hash,Previous hash" and len(text) == 3
    assert text[1].endswith(",start") and manifest["file"] == "audit.csv"


def test_a_filtered_export_says_it_is_not_complete_and_still_checks(tmp_path: Path) -> None:
    log = make(tmp_path, 6)
    data, _, manifest = auditexport.build(log, fmt="jsonl", exported_by="pat", text="thing 1")
    assert manifest["entries"] == 1 and manifest["complete"] is False and manifest["filters"] == {"text": "thing 1"}
    assert manifest["log"]["entries"] == 6  # how big the whole log was
    one = check_export(unzip(data)["audit.jsonl"])
    assert one["ok"] and one["entries"] == 1
    gap = [json.loads(x) for x in lines(log)]
    both = "\n".join(json.dumps(gap[i]) for i in (1, 4)).encode()
    got = check_export(both)
    assert got["ok"] and got["gaps"] == 1 and got["complete"] is False  # lines missing between the two: counted


def test_a_changed_export_is_found(tmp_path: Path) -> None:
    data, _, _ = auditexport.build(make(tmp_path), fmt="jsonl", exported_by="pat")
    raw = unzip(data)["audit.jsonl"].decode().replace("subject 3", "subject 4").encode()
    got = check_export(raw)
    assert not got["ok"] and got["problem"] == {"line": 4, "why": "this line was changed"}


def test_an_export_is_refused_for_a_format_or_date_it_does_not_understand(tmp_path: Path) -> None:
    log = make(tmp_path, 1)
    for kwargs, why in [
        ({"fmt": "xml"}, "csv or jsonl"),
        ({"fmt": "csv", "since": "yesterday"}, "dates must look like"),
        ({"fmt": "csv", "until": "2026-13-45"}, "dates must look like"),
        ({"fmt": "csv", "since": "2026-10-05", "until": "2026-10-01"}, "after the last date"),
    ]:
        with pytest.raises(ValueError, match=why):
            auditexport.build(log, exported_by="pat", **kwargs)


def test_check_lines_works_on_bytes_of_a_log(tmp_path: Path) -> None:
    log = make(tmp_path, 3)
    assert check_lines(log.path.read_bytes())["ok"]
    assert check_lines(b"")["ok"] and check_lines(b"not json\n")["ok"]


# ------------------------------------------------------------------ the service


def test_the_pages_filter_check_and_export_the_log_and_the_export_is_itself_recorded(app: Any) -> None:  # noqa: F811
    call(app, "POST", "/api/settings", {"release": "26C"})
    call(app, "POST", "/api/settings", {"release": "26D"})
    everything = call(app, "GET", "/api/audit")["entries"]
    assert len(everything) == 2 and "hash" in everything[0] and everything[0]["prev"] == everything[1]["hash"]
    assert call(app, "GET", "/api/audit?text=26D")["matching"] == 1
    assert call(app, "GET", "/api/audit?who=nobody-at-all")["entries"] == []
    state = call(app, "GET", "/api/audit/verify")
    assert state["ok"] and state["entries"] == 2

    reply = app.handle("GET", "/api/audit/export?format=jsonl&text=26D", b"")
    assert reply.content_type == "application/zip" and reply.download_name.endswith(".zip")
    files = unzip(reply.body)
    manifest = json.loads(files["manifest.json"])
    assert manifest["entries"] == 1 and manifest["filters"] == {"text": "26D"} and manifest["complete"] is False
    last = call(app, "GET", "/api/audit")["entries"][0]
    assert last["action"] == "Exported the audit log" and last["details"]["entries"] == 1
    assert last["details"]["filter text"] == "26D" and last["subject"] == reply.download_name
    assert call(app, "GET", "/api/audit/verify")["ok"]  # the export is part of the chain too

    with pytest.raises(ApiError, match="csv or jsonl") as bad:
        app.handle("GET", "/api/audit/export?format=pdf", b"")
    assert bad.value.status == 400
    old = app.handle("GET", "/api/audit.csv", b"").body.decode("utf-8-sig").splitlines()[0]
    assert old == "When,Who,What,Subject,Details"  # the plain download is as it was


def test_only_approvers_and_administrators_may_export() -> None:
    assert required_role("GET", ["audit", "export"]) == "approver"
    assert required_role("GET", ["audit", "verify"]) == "any" and required_role("GET", ["audit"]) == "any"


# ------------------------------------------------------------------ the command line


def test_qm_audit_checks_exports_and_holds_the_log_against_an_earlier_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / ".qm"
    log = AuditLog(data / "audit.jsonl", who=lambda: "pat")
    for i in range(4):
        log.add(f"Did {i}", "x")
    assert cli.main(["audit", "verify", "--data", str(data)]) == 0
    assert "OK: 4 lines, the chain is whole" in capsys.readouterr().out

    out = tmp_path / "copy.zip"
    assert cli.main(["audit", "export", "--data", str(data), "--out", str(out), "--text", "Did 2"]) == 0
    assert "Wrote" in capsys.readouterr().out
    assert cli.main(["audit", "verify", str(out)]) == 0
    assert "each is what its hash says" in capsys.readouterr().out

    full = tmp_path / "full.zip"
    assert cli.main(["audit", "export", "--data", str(data), "--out", str(full)]) == 0
    capsys.readouterr()
    assert cli.main(["audit", "verify", "--data", str(data), "--against", str(full)]) == 0
    assert "Consistent with the manifest" in capsys.readouterr().out

    rows = lines(log)
    put(log, rows[:-1])  # the newest line cut off
    assert cli.main(["audit", "verify", "--data", str(data)]) == 0  # the chain itself cannot see it
    capsys.readouterr()
    assert cli.main(["audit", "verify", "--data", str(data), "--against", str(full)]) == 1
    assert "NOT CONSISTENT" in capsys.readouterr().out

    rows[1] = rows[1].replace("Did 1", "Did one")
    put(log, rows)
    assert cli.main(["audit", "verify", "--data", str(data)]) == 1
    assert "BROKEN at line 2: this line was changed" in capsys.readouterr().out
    assert cli.main(["audit", "export", "--data", str(data), "--format", "csv", "--from", "bad"]) == 2
    assert "dates must look like" in capsys.readouterr().err
