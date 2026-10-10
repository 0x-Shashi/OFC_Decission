"""Encrypt the pod passwords typed in Settings before they are saved.

On Windows, Windows itself encrypts them (Data Protection API, the way Chrome and Edge keep saved
passwords): only the same Windows user on the same computer can read them back, so a copy of the
database (a backup, a synced folder, a zip) shows unreadable bytes.

Elsewhere (Mac, Linux, a test server) a random key is kept in a file only this user may read
(~/.quartermaster/vault.key) and the password is encrypted with it: a stream from HMAC-SHA256 in
counter mode, with an HMAC tag so a changed value is refused rather than read wrongly.

Nothing here is ever logged, and a password is never sent back to the web page.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sys
from pathlib import Path

_PREFIX_DPAPI = b"dp1:"
_PREFIX_KEY = b"kf1:"


class VaultError(ValueError):
    pass


def kind() -> str:
    """How passwords are protected on this computer, in words for the Settings page."""
    return (
        "encrypted by Windows for your Windows user"
        if sys.platform == "win32"
        else "encrypted with a key only you can read"
    )


def protect(text: str, key_file: Path | None = None) -> bytes:
    data = text.encode("utf-8")
    if sys.platform == "win32":
        return _PREFIX_DPAPI + _dpapi(data, encrypt=True)
    key = _key(key_file)
    nonce = secrets.token_bytes(16)
    body = _xor(data, _stream(key, nonce, len(data)))
    tag = hmac.new(key, b"tag" + nonce + body, hashlib.sha256).digest()
    return _PREFIX_KEY + nonce + tag + body


def reveal(blob: bytes, key_file: Path | None = None) -> str:
    if blob.startswith(_PREFIX_DPAPI):
        if sys.platform != "win32":
            raise VaultError("this password was saved on Windows; type it again on this computer")
        return _dpapi(blob[len(_PREFIX_DPAPI) :], encrypt=False).decode("utf-8")
    if blob.startswith(_PREFIX_KEY):
        key = _key(key_file)
        nonce, tag, body = blob[4:20], blob[20:52], blob[52:]
        if not hmac.compare_digest(tag, hmac.new(key, b"tag" + nonce + body, hashlib.sha256).digest()):
            raise VaultError("a saved password could not be read on this computer; type it again")
        return _xor(body, _stream(key, nonce, len(body))).decode("utf-8")
    raise VaultError("a saved password is in an unknown form; type it again")


# ------------------------------------------------------------------ key file (not Windows)


def default_key_file() -> Path:
    return Path(os.environ.get("QM_VAULT_KEY_FILE") or Path.home() / ".quartermaster" / "vault.key")


def _key(key_file: Path | None) -> bytes:
    path = key_file or default_key_file()
    try:
        key = path.read_bytes()
        if len(key) == 32:
            return key
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # this user only
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def _stream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, b"enc" + nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b, strict=True))


# ------------------------------------------------------------------ Windows Data Protection API


def _dpapi(data: bytes, *, encrypt: bool) -> bytes:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    data_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    data_out = Blob()
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined,unused-ignore]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
    ui_forbidden = 0x1  # never show a dialog
    if encrypt:
        ok = crypt32.CryptProtectData(
            ctypes.byref(data_in),
            ctypes.c_wchar_p("Quartermaster"),
            None,
            None,
            None,
            ui_forbidden,
            ctypes.byref(data_out),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(data_in), None, None, None, None, ui_forbidden, ctypes.byref(data_out)
        )
    if not ok:
        raise VaultError(
            "Windows could not encrypt the password"
            if encrypt
            else "a saved password could not be read (saved by another Windows user or computer); type it again"
        )
    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        kernel32.LocalFree(data_out.pbData)
