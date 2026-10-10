"""Per-persona Fusion credentials, read from environment variables injected by the vault.

    QM_FUSION_USER / QM_FUSION_PASSWORD                     default test user
    QM_FUSION_USER_LINE_MANAGER / QM_FUSION_PASSWORD_...    user for persona "Line Manager"

Credentials never appear in test specs, logs or Git.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping


class MissingCredentialsError(LookupError):
    pass


def persona_key(persona: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", persona.upper()).strip("_")


def persona_credentials(persona: str, environ: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Return (user, password) for a persona, falling back to the default test user."""
    env = os.environ if environ is None else environ
    key = persona_key(persona)
    candidates = ([f"_{key}"] if key else []) + [""]
    for suffix in candidates:
        user, pw = env.get(f"QM_FUSION_USER{suffix}"), env.get(f"QM_FUSION_PASSWORD{suffix}")
        if user and pw:
            return user, pw
    wanted = f"QM_FUSION_USER_{key} / QM_FUSION_PASSWORD_{key}" if key else "QM_FUSION_USER / QM_FUSION_PASSWORD"
    raise MissingCredentialsError(f"no credentials for persona {persona!r}: set {wanted}")
