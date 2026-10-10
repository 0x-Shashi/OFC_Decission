"""Accepting a test update edits the YAML as text: comments and layout stay, and the result is checked."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from quartermaster.service.heal import accept_update, promote_strategy

SPEC = """id: hcm.x
title: X
steps:
  - action: fill
    intent: Enter employee number
    value: ${number}
    target:
      strategies:
        # the label first: it is the most readable
        - label: Employee Number
        # a long one
        - xpath: >-
            //div[@id='a']
            //input
        - role: "textbox:Employee Number"
  - action: click
    intent: Search
    target:
      strategies:
        - role: "button:Search"
data:
  number: "20258"
"""


def strategies(text: str, step: int = 0) -> list[dict[str, str]]:
    return yaml.safe_load(text)["steps"][step]["target"]["strategies"]  # type: ignore[no-any-return]


def test_the_healed_locator_moves_to_the_top_and_nothing_else_changes() -> None:
    new = promote_strategy(SPEC, 0, "role", "textbox:Employee Number")
    assert strategies(new)[0] == {"role": "textbox:Employee Number"}
    assert strategies(new)[1:] == strategies(SPEC)[:2]
    before, after = yaml.safe_load(SPEC), yaml.safe_load(new)
    before["steps"][0]["target"]["strategies"] = after["steps"][0]["target"]["strategies"]
    assert before == after  # everything else reads back the same
    assert "# the label first" in new and new.count("\n") == SPEC.count("\n")


def test_a_comment_moves_with_its_locator_and_multi_line_values_stay_whole() -> None:
    new = promote_strategy(SPEC, 0, "xpath", "//div[@id='a'] //input")
    assert strategies(new)[0] == {"xpath": "//div[@id='a'] //input"}
    assert new.index("# a long one") < new.index("- xpath") < new.index("# the label first") < new.index("- label")


@pytest.mark.parametrize(
    ("step", "kind", "value", "message"),
    [
        (0, "label", "Employee Number", "already tries this first"),
        (0, "css", "#x", "has no css locator"),
        (5, "role", "x", "has no step 6"),
    ],
)
def test_requests_that_do_not_fit_the_file_are_refused(step: int, kind: str, value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        promote_strategy(SPEC, step, kind, value)


def test_accepting_keeps_a_backup(tmp_path: Path) -> None:
    test_file = tmp_path / "x.yaml"
    test_file.write_text(SPEC, encoding="utf-8")
    backup = accept_update(test_file, 0, ["role", "textbox:Employee Number"], tmp_path / "backups")
    assert backup.read_text(encoding="utf-8") == SPEC
    assert strategies(test_file.read_text(encoding="utf-8"))[0] == {"role": "textbox:Employee Number"}
