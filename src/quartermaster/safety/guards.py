"""Hard safety rails. The runner calls `assert_safe_target` before opening any session."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from urllib.parse import urlparse

from quartermaster.domain.models import Environment, EnvironmentKind


class UnsafeEnvironmentError(RuntimeError):
    pass


# Oracle non-prod pods conventionally carry one of these markers in the hostname
# (e.g. "abcd-test.fa.us2.oraclecloud.com", "abcd-dev1.fa...").
_NONPROD_HOST = re.compile(r"-(dev|test|stage|stg|uat|sit|qa)\d*\.", re.IGNORECASE)


def confirmed_hosts(environ: Mapping[str, str] | None = None) -> set[str] | None:
    """Pod hosts a person confirmed in Settings are test pods although their name has no dev/test
    marker (QM_FUSION_ALLOWED_HOSTS, set by qm serve for the environment in use), or None."""
    raw = (os.environ if environ is None else environ).get("QM_FUSION_ALLOWED_HOSTS", "")
    hosts = {h.strip().lower() for h in raw.split(",") if h.strip()}
    return hosts or None


def assert_safe_target(env: Environment, allowed_hosts: set[str] | None = None) -> None:
    """Refuse to run against production or against hosts outside the tenant allow-list.

    Two independent checks so a mislabelled environment can't slip through:
    the declared kind, and the hostname itself.
    """
    parsed = urlparse(env.url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise UnsafeEnvironmentError(f"{env.name}: URL must be https with a hostname, got {env.url!r}")
    host = parsed.hostname.lower()

    if env.kind is EnvironmentKind.PROD:
        raise UnsafeEnvironmentError(f"{env.name}: refusing to run tests against a PROD environment")

    if allowed_hosts is not None:
        if host not in {h.lower() for h in allowed_hosts}:
            raise UnsafeEnvironmentError(f"{env.name}: host {host} is not in the tenant allow-list")
        return  # explicitly allow-listed by the tenant admin

    if host.endswith("oraclecloud.com") and not _NONPROD_HOST.search(host):
        raise UnsafeEnvironmentError(
            f"{env.name}: host {host} looks like a production pod (no dev/test/stage marker); "
            "add it to the allow-list explicitly if this is a non-prod pod"
        )
