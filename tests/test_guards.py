from __future__ import annotations

import pytest

from quartermaster.domain.models import Environment, EnvironmentKind
from quartermaster.safety.guards import UnsafeEnvironmentError, assert_safe_target


def env(url: str, kind: EnvironmentKind = EnvironmentKind.TEST) -> Environment:
    return Environment(name="e", url=url, kind=kind)


def test_nonprod_pod_allowed() -> None:
    assert_safe_target(env("https://abcd-test.fa.us2.oraclecloud.com"))
    assert_safe_target(env("https://abcd-dev2.fa.em2.oraclecloud.com"))


def test_prod_kind_blocked() -> None:
    with pytest.raises(UnsafeEnvironmentError, match="PROD"):
        assert_safe_target(env("https://abcd-test.fa.us2.oraclecloud.com", EnvironmentKind.PROD))


def test_prod_looking_host_blocked_even_if_labelled_test() -> None:
    with pytest.raises(UnsafeEnvironmentError, match="looks like a production pod"):
        assert_safe_target(env("https://abcd.fa.us2.oraclecloud.com"))


def test_allow_list_overrides_heuristic() -> None:
    host = "abcd.fa.us2.oraclecloud.com"
    assert_safe_target(env(f"https://{host}"), allowed_hosts={host})


def test_host_outside_allow_list_blocked() -> None:
    with pytest.raises(UnsafeEnvironmentError, match="allow-list"):
        assert_safe_target(env("https://abcd-test.fa.us2.oraclecloud.com"), allowed_hosts={"other.example.com"})


def test_http_rejected() -> None:
    with pytest.raises(UnsafeEnvironmentError, match="https"):
        assert_safe_target(env("http://abcd-test.fa.us2.oraclecloud.com"))
