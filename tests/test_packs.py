"""The test library: packs of ready-made tests that come with Quartermaster."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_service_api import app, call  # noqa: F401, F811  (app is a fixture)

from quartermaster import cli, packs
from quartermaster.domain.models import Action
from quartermaster.dsl.loader import load_tests
from quartermaster.dsl.suites import SUITES_DIR, read_suites, resolve
from quartermaster.packs import catalog
from quartermaster.service.api import ApiError
from quartermaster.service.auth import required_role

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ what ships


def test_the_packs_that_ship_are_whole_and_say_what_they_are_for() -> None:
    found = packs.list_packs()
    assert [p.id for p in found] == sorted(
        ["hcm-services", "financials-services", "procurement-services", "supply-chain-services", "sales-services"],
        key=lambda i: next(p.title.casefold() for p in found if p.id == i),
    )
    for pack in found:
        assert pack.title and pack.module and pack.description and pack.needs and pack.version >= 1
        assert pack.read_only and pack.kind == "rest" and len(pack.tests) >= 5
        if pack.checked:  # the pack says when it was last run on a pod, and how it went
            assert set(pack.checked) == {"date", "passed", "of"} and pack.checked["of"] == len(pack.tests)
            assert 0 <= pack.checked["passed"] <= pack.checked["of"]
    assert sum(len(p.tests) for p in found) >= 60


def test_every_library_test_is_valid_unique_and_only_reads_from_the_pod() -> None:
    seen: set[str] = set()
    for pack in packs.list_packs():
        for test in load_tests(pack.folder / "tests"):  # each file is a valid test (and a duplicate id is refused)
            assert test.id.startswith("lib.") and test.id not in seen
            seen.add(test.id)
            assert {"library", pack.id, "rest", "read-only"} <= set(test.tags)
            assert test.module and test.product and test.process and not test.cleanup and not test.setup
            for step in test.steps:  # the promise on the page: nothing is created, changed or deleted
                assert step.action is Action.API_CALL
                assert (step.value or "").startswith("GET /"), f"{test.id}: {step.value}"
            first = test.steps[0].options.get("check") or {}
            assert "count" in first and "hasMore" in first  # at least: the service answers with a list
    assert len(seen) == sum(len(p.tests) for p in packs.list_packs())


def test_a_second_step_reads_the_record_the_first_found() -> None:
    texts = {
        t.id: t for p in packs.list_packs() for t in load_tests(p.folder / "tests")
    }  # lib.hcm.public-workers: list, then read one by its id
    two = texts["lib.hcm.public-workers"].steps
    assert (
        len(two) == 2
        and "${first_id}" in (two[1].value or "")
        and two[0].options["save"] == {"first_id": "items[0].PersonId"}
    )


# ------------------------------------------------------------------ installing


def pack(name: str = "hcm-services") -> catalog.Pack:
    return catalog.get_pack(name)


def test_installing_copies_the_tests_makes_the_suite_and_says_it_is_installed(tmp_path: Path) -> None:
    p = pack()
    assert packs.pack_status(tmp_path, p) is None
    done = packs.install(tmp_path, p)
    assert len(done["added"]) == len(p.tests) and not done["updated"] and not done["kept"] and not done["unchanged"]
    folder = tmp_path / "library" / "hcm-services"
    assert sorted(f.name for f in folder.glob("*.yaml")) == sorted(t["file"] for t in p.tests)
    assert len(load_tests(tmp_path)) == len(p.tests)  # they are tests like any other
    suites, problems = read_suites(tmp_path / SUITES_DIR)
    assert list(suites) == ["library-hcm-services"] and not problems and done["suite"] == "library-hcm-services"
    items = [{"id": t.id, "folder": "library/hcm-services", "tags": t.tags} for t in load_tests(tmp_path)]
    assert len(resolve(suites["library-hcm-services"][1], items).ids) == len(p.tests)  # the suite has them all
    status = packs.pack_status(tmp_path, p)
    assert status == {"version": 1, "changed": [], "update_available": False, "suite": "library-hcm-services"}


def test_installing_again_changes_nothing_and_never_loses_your_edits(tmp_path: Path) -> None:
    p = pack()
    packs.install(tmp_path, p)
    again = packs.install(tmp_path, p)
    assert len(again["unchanged"]) == len(p.tests) and not again["added"] and not again["kept"]
    mine = tmp_path / "library" / "hcm-services" / "workers.yaml"
    mine.write_text(mine.read_text().replace("priority: medium", "priority: critical"), encoding="utf-8")
    status = packs.pack_status(tmp_path, p)
    assert status and status["changed"] == ["workers.yaml"] and not status["update_available"]
    kept = packs.install(tmp_path, p)
    assert kept["kept"] == ["workers.yaml"] and "priority: critical" in mine.read_text()  # your edit stays
    (tmp_path / "library" / "hcm-services" / "jobs.yaml").unlink()  # a deleted file comes back with an install
    back = packs.install(tmp_path, p)
    assert back["added"] == ["jobs.yaml"] and back["kept"] == ["workers.yaml"]


def test_an_existing_suite_of_the_same_name_is_not_overwritten(tmp_path: Path) -> None:
    (tmp_path / SUITES_DIR).mkdir()
    mine = tmp_path / SUITES_DIR / "library-hcm-services.yaml"
    mine.write_text("suite: library-hcm-services\ntitle: Mine\ninclude: [{tags: [smoke]}]\n", encoding="utf-8")
    packs.install(tmp_path, pack())
    assert "title: Mine" in mine.read_text()


def newer(
    tmp_path: Path,
    p: catalog.Pack,
    change: dict[str, str],
    drop: tuple[str, ...] = (),
    add: dict[str, str] | None = None,
) -> catalog.Pack:
    """The pack as a newer release of Quartermaster would ship it."""
    folder = tmp_path / "shipped" / p.id
    shutil.copytree(p.folder, folder)
    for name, text in change.items():
        (folder / "tests" / name).write_text(text, encoding="utf-8")
    for name in drop:
        (folder / "tests" / name).unlink()
    for name, text in (add or {}).items():
        (folder / "tests" / name).write_text(text, encoding="utf-8")
    meta = yaml.safe_load((folder / "pack.yaml").read_text())
    meta["version"] = p.version + 1
    (folder / "pack.yaml").write_text(yaml.safe_dump(meta), encoding="utf-8")
    return catalog._read(folder)


def test_an_update_adds_new_tests_updates_the_ones_you_did_not_change_and_keeps_the_ones_you_did(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "tests"
    p = pack()
    packs.install(tests, p)
    shipped = (p.folder / "tests" / "jobs.yaml").read_text()
    mine = tests / "library" / "hcm-services" / "grades.yaml"
    mine.write_text(mine.read_text() + "# my note\n", encoding="utf-8")
    new = newer(
        tmp_path,
        p,
        change={
            "jobs.yaml": shipped + "# a newer check\n",
            "grades.yaml": (p.folder / "tests" / "grades.yaml").read_text() + "# theirs\n",
        },
        drop=("positions.yaml",),
        add={"extra.yaml": shipped.replace("lib.hcm.jobs", "lib.hcm.extra")},
    )
    assert packs.pack_status(tests, new)["update_available"] is True  # type: ignore[index]
    done = packs.install(tests, new)
    assert done["added"] == ["extra.yaml"] and done["updated"] == ["jobs.yaml"]
    assert done["kept"] == ["grades.yaml"] and done["removed"] == ["positions.yaml"]
    folder = tests / "library" / "hcm-services"
    assert "# a newer check" in (folder / "jobs.yaml").read_text() and "# my note" in mine.read_text()
    assert not (folder / "positions.yaml").exists() and (folder / "extra.yaml").is_file()
    status = packs.pack_status(tests, new)
    assert status and status["version"] == 2 and status["changed"] == ["grades.yaml"] and not status["update_available"]


def test_a_file_dropped_from_the_pack_that_you_changed_is_kept(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    p = pack()
    packs.install(tests, p)
    mine = tests / "library" / "hcm-services" / "positions.yaml"
    mine.write_text(mine.read_text() + "# mine\n", encoding="utf-8")
    done = packs.install(tests, newer(tmp_path, p, {}, drop=("positions.yaml",)))
    assert done["kept"] == ["positions.yaml"] and mine.is_file() and not done["removed"]


def test_an_unknown_pack_says_which_there_are() -> None:
    with pytest.raises(catalog.PackError, match=r"no pack called 'nope' \(there is: financials-services, hcm-services"):
        catalog.get_pack("nope")


def test_a_pack_folder_that_does_not_say_its_own_name_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "odd-pack"
    (folder / "tests").mkdir(parents=True)
    (folder / "pack.yaml").write_text("pack: something-else\n", encoding="utf-8")
    with pytest.raises(catalog.PackError, match="must say `pack: odd-pack`"):
        packs.list_packs(tmp_path)


# ------------------------------------------------------------------ the service


def test_the_pages_list_packs_and_install_one_and_the_installed_tests_can_be_run_as_a_suite(app: Any) -> None:  # noqa: F811
    page = call(app, "GET", "/api/packs")
    assert page["folder"].endswith("/library") and len(page["packs"]) == 5
    hcm = next(p for p in page["packs"] if p["id"] == "hcm-services")
    assert hcm["installed"] is None and hcm["checked"]["of"] == 23 and hcm["tests"][0]["title"] and hcm["read_only"]

    done = call(app, "POST", "/api/packs/install", {"pack": "hcm-services"})
    assert len(done["added"]) == 23 and done["suite"] == "library-hcm-services"
    assert (
        done["pack"]["installed"]["suite"] == "library-hcm-services"
        and not done["pack"]["installed"]["update_available"]
    )
    listed = {t["id"] for t in call(app, "GET", "/api/tests") if t.get("id", "").startswith("lib.hcm.")}
    assert len(listed) == 23
    audit = [e for e in call(app, "GET", "/api/audit")["entries"] if e["action"] == "Installed a library pack"]
    assert audit and audit[0]["subject"] == "HCM services (read only)" and audit[0]["details"] == {"added": 23}

    run = call(app, "POST", "/api/runs", {"suite": "library-hcm-services"})  # the suite made with it runs all 23
    assert len(run["options"]["only"]) == 23 and run["options"]["label"].startswith("Suite: HCM services")
    with pytest.raises(ApiError, match="no pack called") as bad:
        app.handle("POST", "/api/packs/install", json.dumps({"pack": "nope"}).encode())
    assert bad.value.status == 400


def test_testers_may_install_and_everyone_may_look() -> None:
    assert required_role("POST", ["packs", "install"]) == "tester" and required_role("GET", ["packs"]) == "any"


# ------------------------------------------------------------------ the command line


def test_qm_packs_lists_and_installs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tests = tmp_path / "tests"
    assert cli.main(["packs", "list", "--tests", str(tests)]) == 0
    out = capsys.readouterr().out
    assert "hcm-services" in out and "23 tests  not installed, last checked" in out and "23 of 23 passed" in out
    assert cli.main(["packs", "install", "hcm-services", "sales-services", "--tests", str(tests)]) == 0
    assert "hcm-services: 23 added. Suite: library-hcm-services" in capsys.readouterr().out
    assert cli.main(["packs", "list", "--tests", str(tests)]) == 0
    assert "installed v1" in capsys.readouterr().out
    mine = tests / "library" / "sales-services" / "accounts.yaml"
    mine.write_text(mine.read_text() + "# mine\n", encoding="utf-8")
    assert cli.main(["packs", "install", "sales-services", "--tests", str(tests)]) == 0
    assert "kept your version of accounts.yaml" in capsys.readouterr().out
    assert cli.main(["packs", "install", "--all", "--tests", str(tmp_path / "all")]) == 0
    assert len(list((tmp_path / "all" / "library").iterdir())) == 5
    capsys.readouterr()
    assert cli.main(["packs", "install", "--tests", str(tests)]) == 2 and "name a pack" in capsys.readouterr().err
    assert (
        cli.main(["packs", "install", "nope", "--tests", str(tests)]) == 2
        and "no pack called" in capsys.readouterr().err
    )


# ------------------------------------------------------------------ the tool that checks a pack on a pod


def load_tool() -> Any:
    spec = importlib.util.spec_from_file_location("check_pack_tool", ROOT / "tools" / "check_pack.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_checking_tool_only_reads_and_only_from_the_pod(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = load_tool()
    driver = tool.UrllibDriver("https://pod.example.com", "user", "secret")
    with pytest.raises(RuntimeError, match="only read from the pod"):
        driver.api_call("POST", "/x")
    with pytest.raises(RuntimeError, match="only the pod's own address"):
        driver.api_call("GET", "https://evil.example.com/x")
    assert driver.screenshot("x") is None
    for var in ("QM_FUSION_URL", "QM_FUSION_USER", "QM_FUSION_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    assert tool.main([str(ROOT / "src" / "quartermaster" / "packs" / "hcm-services")]) == 2
