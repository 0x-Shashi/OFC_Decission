"""A stand-in for a company's single sign-on provider (OpenID Connect): its address book, its signing keys and its
token address, for the tests of single sign-on. It signs identity tokens with a test-only RSA key (the numbers are below
and protect nothing), written here independently of the code that checks them."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

# A 2048-bit RSA key made only for these tests.
N = int(
    "ad822a9c8c0640c073b37a6c8e7ecc352f153dc925edc4cc73f3dab6164f5057"
    "bf1bf8cd62fc90c879126a0b52a05ae5bb4c1f4fec3061601a8f42bd3d98990a"
    "70f566e59eb0c86239573f83787ab892525affa7f578e4f7daf32b6bc5ea0a09"
    "5ba854d686e42c781c197ff034a3efefb1a5f63243dc77e998d64dc4ba6aa13d"
    "0449b60985ba59275515207548f3dac5bfd69ebaab58eb6c4e0264349d461bb0"
    "e7efe3f97a7dbc9849ef98311f13d2aa347d426e40d3184fe7f2342a202d72c2"
    "c610bb83dd34d585bff7d749123228fe8435631693be5c4611cc4a96d5b28751"
    "23c67ff8ffaecc916290bbf3c55a0bc82895c46dcd86e189930143fb38b8ccf3",
    16,
)
D = int(
    "a1e0c790b6b33ec64f2c2c140bfe10d7adcdcb8f576bb6286a2620efb170de2c"
    "7f88c1601df235c253f2f22d0e31bd9c885a44fc7407cf51b275e67658797e8f"
    "57441d742dd211a2528d2c1ca4d31a50a9b56cc06f2d13b28afc448e9060026d"
    "28aeac385a3197ab97cbd2a970f3626fe6f647f42d8c0bf44d3be3e29f69cc73"
    "6ee6bcf1a87379a2511375e88b8a296cb04b01bc8ba901a35bf53af3dcae930c"
    "f0f7dc7e6c9bbd176397ce798091350985049125f23f4fe13de5e1c7b855e1c5"
    "eac74b58dc481c37ea366cbf10256190a9e24bce221cbebe36499d9139f78163"
    "59ffbcc9908ecc020fbf5aaf25e22674866fe588d9987726778bb04007e9571",
    16,
)
E = 65537
_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")  # DigestInfo of SHA-256

ISSUER = "https://idp.example.com/oauth2/default"
CLIENT_ID = "0oa-qm-client-id"
SECRET = "stand-in-sso-client-secret-value"


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign(signed: bytes, d: int = D, n: int = N) -> bytes:
    """RSA PKCS#1 v1.5 signature with SHA-256."""
    size = (n.bit_length() + 7) // 8
    tail = _PREFIX + hashlib.sha256(signed).digest()
    padded = b"\x00\x01" + b"\xff" * (size - len(tail) - 3) + b"\x00" + tail
    return pow(int.from_bytes(padded, "big"), d, n).to_bytes(size, "big")


def public_key(kid: str = "key-1") -> dict[str, str]:
    return {"kty": "RSA", "kid": kid, "alg": "RS256", "use": "sig", "n": b64u(N.to_bytes(256, "big")), "e": "AQAB"}


class Provider:
    """What the provider answers, and what it was asked. `get` and `post` stand in for the network."""

    def __init__(self, now: Callable[[], float] | None = None, http: bool = False, issuer: str = ISSUER) -> None:
        self.issuer, self.now = issuer, now
        self.identity: dict[str, Any] = {"email": "jane@acme.com", "name": "Jane Doe", "groups": []}
        self.claims: dict[str, Any] = {}  # changes to the token's claims; None takes a claim out
        self.header: dict[str, Any] = {}  # changes to the token's header
        self.keys = [public_key()]
        self.doc: dict[str, Any] = {}  # changes to the address book
        self.corrupt = False  # a signature that does not check out
        self.nonce = ""
        self.asked: list[dict[str, str]] = []
        self.fetched: list[str] = []
        self._codes: dict[str, dict[str, str]] = {}
        self.authorize = "https://idp.example.com/oauth2/default/v1/authorize"
        self.server: ThreadingHTTPServer | None = None
        if http:
            self._serve()

    def _serve(self) -> None:
        stand = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # the sign-in page: it sends the browser straight back as the identity
                q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                code = f"code{len(stand._codes)}"
                stand._codes[code] = {"nonce": q.get("nonce", "")}
                back = q["redirect_uri"] + "?" + urlencode({"code": code, "state": q["state"]})
                self.send_response(302)
                self.send_header("Location", back)
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.authorize = f"http://127.0.0.1:{self.server.server_address[1]}/authorize"

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()

    def discovery(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": self.authorize,
            "token_endpoint": f"{self.issuer}/v1/token",
            "jwks_uri": f"{self.issuer}/v1/keys",
            **self.doc,
        }

    def get(self, url: str) -> dict[str, Any]:
        self.fetched.append(url)
        if url == f"{self.issuer}/.well-known/openid-configuration":
            return self.discovery()
        if url == f"{self.issuer}/v1/keys":
            return {"keys": self.keys}
        raise AssertionError(f"unexpected address {url}")

    def post(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        self.asked.append({"url": url, **form})
        nonce = self._codes.get(form.get("code", ""), {}).get("nonce", self.nonce)
        return {"id_token": self.token(nonce), "access_token": "not-used", "token_type": "Bearer"}

    def token(self, nonce: str) -> str:
        t = self.now() if self.now else time.time()
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": CLIENT_ID,
            "iat": t,
            "exp": t + 3600,
            "nonce": nonce,
            "email_verified": True,
            **self.identity,
        }
        for k, v in self.claims.items():
            if v is None:
                claims.pop(k, None)
            else:
                claims[k] = v
        header = {"alg": "RS256", "typ": "JWT", "kid": "key-1", **self.header}
        seg = lambda obj: b64u(json.dumps(obj).encode())  # noqa: E731
        signed = f"{seg(header)}.{seg(claims)}".encode()
        alg = header["alg"]
        if alg == "RS256":
            signature = sign(signed)
            if self.corrupt:
                signature = bytes([signature[0] ^ 1]) + signature[1:]
        else:
            signature = b"" if str(alg).lower() == "none" else b"x" * 64  # an algorithm the code cannot check itself
        return f"{signed.decode()}.{b64u(signature)}"
