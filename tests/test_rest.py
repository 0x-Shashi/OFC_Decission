"""REST steps: reading the request, paths in the JSON reply, and the checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from quartermaster.dsl.loader import SpecError, load_test
from quartermaster.runner.rest import check_reply, json_get, parse_request


def test_requests_and_paths() -> None:
    assert parse_request("get   /hcmRestApi/x?q=A B") == ("GET", "/hcmRestApi/x?q=A B")
    assert parse_request("/fscmRestApi/y") == ("GET", "/fscmRestApi/y")
    with pytest.raises(ValueError, match="METHOD /path"):
        parse_request("show me the workers")
    reply = {"items": [{"Name": "Pat", "Emails": [{"Address": "p@x"}]}], "count": 1}
    assert json_get(reply, "items[0].Emails[0].Address") == "p@x" and json_get(reply, "count") == 1
    for missing in ("items[1].Name", "items[0].Phone", "count.x"):
        with pytest.raises(KeyError):
            json_get(reply, missing)


def test_checks_say_what_is_wrong() -> None:
    same = lambda text: text  # noqa: E731
    reply = {"hasMore": False, "items": [{"Id": ""}]}
    wrong, kept = check_reply(200, reply, {"check": {"hasMore": False, "items[0].Id": "*"}}, same)
    assert wrong == ['"items[0].Id" is empty in the API reply'] and kept == {}
    wrong, kept = check_reply(500, reply, {"check": {"hasMore": "true"}}, same)
    assert wrong == ["the API answered HTTP 500"]  # the reply is not looked at
    wrong, kept = check_reply(201, {"Id": 7}, {"expect_status": 201, "save": {"id": "Id"}}, same)
    assert (wrong, kept) == ([], {"id": "7"})


SPEC = """\
id: t
title: t
module: HCM
product: Core
steps:
  - action: api_call
    intent: Find
    value: GET /hcmRestApi/x
    options:
      save:
        person_id: items[0].PersonId
  - action: navigate
    intent: Open
    value: /person/${NAME}
"""


def test_a_value_kept_from_a_reply_may_be_used_by_later_steps(tmp_path: Path) -> None:
    spec = tmp_path / "t.yaml"
    spec.write_text(SPEC.replace("NAME", "person_id"))
    assert load_test(spec).steps[1].value == "/person/${person_id}"
    spec.write_text(SPEC.replace("NAME", "someone"))
    with pytest.raises(SpecError, match="undefined data placeholder"):
        load_test(spec)


def test_evidence_says_what_the_api_answered_in_plain_words() -> None:
    from quartermaster.evidence.document import plain_error

    said = plain_error('StepFailure: "count" is "0" in the API reply, expected "1"')
    assert said == '"count" is "0" in the API reply, expected "1".'
    assert plain_error("StepFailure: the API answered HTTP 403") == "The API answered HTTP 403."
