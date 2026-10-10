"""A sign-in done by hand (single sign-on, MFA), reused by the runs that follow.

Some pods do not take a user name and password from a test: they send the browser to the company's
single sign-on, often with a code on a phone. A person then signs in once in a browser that
Quartermaster opens (`qm signin`), and the pod's session cookies are handed to the runs.

The cookies are never written to a file. `qm signin` prints them to the process that started it
(`qm serve`), which keeps them in its environment as QM_FUSION_SESSION, like the AI key; the runs
it starts inherit them. They end when the pod's session ends or when `qm serve` stops.
"""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any

ENV = "QM_FUSION_SESSION"
PREFIX = "QM_SESSION "  # the line `qm signin` prints
MAX_LEN = 30_000  # Windows allows 32,767 characters in one environment variable


class SessionError(ValueError):
    pass


def encode_session(cookies: list[dict[str, Any]], pod_host: str) -> str:
    """The pod's cookies (not those of the sign-on service), packed into one line of text."""
    host = pod_host.lower()
    keep = [
        {k: c[k] for k in ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite") if k in c}
        for c in cookies
        if host == str(c.get("domain", "")).lstrip(".").lower()
        or host.endswith("." + str(c.get("domain", "")).lstrip(".").lower())
    ]
    if not keep:
        raise SessionError("the browser has no sign-in for the pod: sign in until the pod's home page shows")
    text = base64.b64encode(zlib.compress(json.dumps(keep).encode("utf-8"), 9)).decode("ascii")
    if len(text) > MAX_LEN:
        raise SessionError("the sign-in is too large to keep; ask the test team")
    return text


def decode_session(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(zlib.decompress(base64.b64decode(text.strip(), validate=True)))
    except (ValueError, zlib.error) as e:
        raise SessionError("the saved sign-in could not be read; sign in by hand again") from e
    if not isinstance(data, list) or not all(isinstance(c, dict) for c in data):
        raise SessionError("the saved sign-in could not be read; sign in by hand again")
    return data
