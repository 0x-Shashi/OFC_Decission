from __future__ import annotations

import pytest

from quartermaster.runner.credentials import MissingCredentialsError, persona_credentials, persona_key

ENV = {
    "QM_FUSION_USER": "default.user",
    "QM_FUSION_PASSWORD": "pw0",
    "QM_FUSION_USER_LINE_MANAGER": "mgr.user",
    "QM_FUSION_PASSWORD_LINE_MANAGER": "pw1",
}


def test_persona_key() -> None:
    assert persona_key("Line Manager") == "LINE_MANAGER"
    assert persona_key(" HR-Specialist ") == "HR_SPECIALIST"
    assert persona_key("") == ""


def test_persona_specific_credentials() -> None:
    assert persona_credentials("Line Manager", ENV) == ("mgr.user", "pw1")


def test_falls_back_to_default_user() -> None:
    assert persona_credentials("Employee", ENV) == ("default.user", "pw0")
    assert persona_credentials("", ENV) == ("default.user", "pw0")


def test_missing_credentials_names_variables() -> None:
    with pytest.raises(MissingCredentialsError, match="QM_FUSION_USER_EMPLOYEE"):
        persona_credentials("Employee", {})
