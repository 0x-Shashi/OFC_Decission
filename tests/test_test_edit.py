"""Copy a test, and write a data set from the page's table."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from quartermaster.dsl.loader import load_test
from quartermaster.service import testedit

TEST = """# a note that must survive
id: view-worker
title: "View a worker"
module: HCM
product: Global Human Resources
steps:
  - intent: Find the unit
    action: api_call
    value: GET /hcmRestApi/resources/11.13.18.05/locationsV2?q=Name=${business_unit}
data_sets: [hcm-basics]
"""


def _root(tmp_path: Path) -> Path:
    root = tmp_path.resolve()
    (root / "hcm").mkdir()
    (root / "hcm" / "view.yaml").write_text(TEST, encoding="utf-8")
    return root


def test_duplicate_keeps_the_rest_and_the_comments(tmp_path: Path) -> None:
    root = _root(tmp_path)
    testedit.save_set(root, {"name": "hcm-basics", "values": {"business_unit": "US1"}}, tmp_path / "b")
    made = testedit.duplicate(
        root, root / "hcm" / "view.yaml", "view-worker-acme", "View a worker (Acme)", "hcm", tmp_path / "b"
    )
    text = made.read_text(encoding="utf-8")
    assert "# a note that must survive" in text and "data_sets: [hcm-basics]" in text
    test = load_test(made)
    assert test.id == "view-worker-acme" and test.title == "View a worker (Acme)"


def test_duplicate_refuses_bad_input(tmp_path: Path) -> None:
    root = _root(tmp_path)
    src = root / "hcm" / "view.yaml"
    for new_id, title, folder in [
        ("bad id", "t", "hcm"),
        ("ok", "", "hcm"),
        ("view-worker", "t", "hcm"),  # id already used
        ("ok", "t", ".."),  # outside
        ("ok", "t", "_data"),  # not a test folder
    ]:
        with pytest.raises(ValueError):
            testedit.duplicate(root, src, new_id, title, folder, tmp_path / "b")


def test_save_set_writes_and_backs_up(tmp_path: Path) -> None:
    root = _root(tmp_path)
    body = {
        "name": "hcm-basics",
        "title": "Names",
        "values": {"business_unit": "US1", "x": " "},
        "pods": {"DEV2": {"business_unit": "Vision"}, "TEST": {}},
    }
    path = testedit.save_set(root, body, tmp_path / "b")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw == {
        "dataset": "hcm-basics",
        "title": "Names",
        "values": {"business_unit": "US1"},
        "pods": {"DEV2": {"business_unit": "Vision"}},
    }
    body["values"] = {"business_unit": "US2"}
    testedit.save_set(root, body, tmp_path / "b")
    assert len(list((tmp_path / "b").glob("hcm-basics.*"))) == 1  # the first version was kept


def test_save_set_refuses_bad_names(tmp_path: Path) -> None:
    root = _root(tmp_path)
    with pytest.raises(ValueError):
        testedit.save_set(root, {"name": "bad name", "values": {"a": "1"}}, tmp_path / "b")
    with pytest.raises(ValueError):
        testedit.save_set(root, {"name": "ok", "values": {"1bad": "1"}}, tmp_path / "b")
    with pytest.raises(ValueError):
        testedit.save_set(root, {"name": "ok", "values": {}}, tmp_path / "b")
