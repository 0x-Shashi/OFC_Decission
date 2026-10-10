"""Single sign-on with a company identity provider (OpenID Connect: Okta, Microsoft Entra ID, Keycloak, Auth0, Google
Workspace and the like), standard library only. The same "authorization code" flow with PKCE as Sign in with Google.

    1. The person clicks the single sign-on button: `start` sends them to the provider's sign-in page with a one-time
       `state`, a `nonce` and a PKCE challenge, and sets a short-lived cookie that ties this sign-in to their browser.
    2. The provider signs them in (with the company's password rules, MFA and so on) and sends them back with a `code`.
    3. `callback` checks the `state` and the cookie, swaps the `code` for an identity token by talking to the provider
       directly over HTTPS, and checks the token: issued by the configured provider, for this app, not expired, the
       nonce, and (for RS256, which nearly every provider uses) the signature, against the provider's published keys.
    4. Quartermaster is then asked who that is: the e-mail address, the name and the groups the provider vouches for
       decide whether they are let in and which roles they get (see `Auth.login_sso`).

The provider is found from one address, its issuer, by reading `<issuer>/.well-known/openid-configuration`. Nothing
about the provider is built in, so a company only types the issuer, the client ID and the client secret.

A token signed with an algorithm other than RS256 cannot be checked here. It came straight from the provider's token
address, in answer to our own request, over a verified HTTPS connection, which the OpenID Connect specification allows
trusting; every other check still applies. A token that says it is not signed at all is always refused.

Nothing secret is put in a page, a redirect or a message: the client secret stays on the server (encrypted at rest, see
auth.py) and errors say what failed without repeating what was sent.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlsplit

from quartermaster.service.auth import Auth, AuthError

STATE_TTL_S = 600
MAX_PENDING = 50
DISCOVERY_TTL_S = 600
CALLBACK = "/api/auth/sso/callback"
BINDER_COOKIE = "qm_sso"

Get = Callable[[str], dict[str, Any]]
Post = Callable[[str, dict[str, str]], dict[str, Any]]

# DigestInfo for SHA-256 (RFC 8017, section 9.2): what a PKCS#1 v1.5 signature wraps the hash in
_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


class SsoError(Exception):
    """The sign-in did not work. The message is for the person at the sign-in page and holds no secret."""


class SsoSignIn:
    def __init__(
        self,
        auth: Auth,
        *,
        now: Callable[[], float] = time.time,
        get: Get | None = None,
        post: Post | None = None,
    ):
        self._auth = auth
        self._now = now
        self._get = get or _get_json
        self._post = post or _post_form
        self._pending: dict[str, dict[str, Any]] = {}  # state -> what the callback must find again; in memory only
        self._found: dict[str, tuple[float, dict[str, Any]]] = {}  # issuer -> (when read, its documents)
        self._lock = threading.Lock()

    def redirect_uri(self, fallback: str = "") -> str:
        """Where the provider sends the person back to. This exact address is given in the provider's console."""
        cfg = self._auth.sso_view()
        base = cfg["public_url"] or fallback.replace("127.0.0.1", "localhost")
        return base.rstrip("/") + CALLBACK

    # ------------------------------------------------------------------ finding the provider

    def discover(self, issuer: str, fresh: bool = False) -> dict[str, Any]:
        """The provider's address book (authorization and token addresses, its keys), read once in a while."""
        issuer = issuer.rstrip("/")
        with self._lock:
            hit = self._found.get(issuer)
        if hit and not fresh and self._now() - hit[0] < DISCOVERY_TTL_S:
            return hit[1]
        doc = self._get(f"{issuer}/.well-known/openid-configuration")
        if str(doc.get("issuer") or "").rstrip("/") != issuer:
            raise SsoError("The provider says it is somebody else than the address you typed. Check the address.")
        for key in ("authorization_endpoint", "token_endpoint"):
            if not _address_ok(str(doc.get(key) or "")):
                raise SsoError(f"The provider gave no usable {key.replace('_', ' ')}. Check the address.")
        keys: list[dict[str, Any]] = []
        if doc.get("jwks_uri"):
            if not _address_ok(str(doc["jwks_uri"])):
                raise SsoError("The provider's key address is not a secure address.")
            got = self._get(str(doc["jwks_uri"])).get("keys")
            keys = [k for k in got if isinstance(k, dict)] if isinstance(got, list) else []
        found = {
            "authorization_endpoint": doc["authorization_endpoint"],
            "token_endpoint": doc["token_endpoint"],
            "keys": keys,
        }
        with self._lock:
            self._found[issuer] = (self._now(), found)
        return found

    def check(self, issuer: str) -> dict[str, Any]:
        """What the Settings page shows after "Check the provider": whether it answers and what it offers."""
        found = self.discover(issuer, fresh=True)
        algs = sorted({str(k.get("alg") or "RS256") for k in found["keys"] if k.get("kty") == "RSA"})
        return {
            "ok": True,
            "authorization_endpoint": found["authorization_endpoint"],
            "token_endpoint": found["token_endpoint"],
            "keys": len(found["keys"]),
            "signature_checked": bool(found["keys"]) and "RS256" in algs,
        }

    # ------------------------------------------------------------------ the sign-in

    def start(self, fallback: str = "") -> tuple[str, str]:
        """(the provider's address to send the person to, the cookie value that ties this to their browser)."""
        cfg = self._auth.sso_config()
        if cfg is None:
            raise SsoError("Single sign-on is not turned on.")
        found = self.discover(cfg["issuer"])
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier, binder = secrets.token_urlsafe(48), secrets.token_urlsafe(16)
        redirect = self.redirect_uri(fallback)
        with self._lock:
            now = self._now()
            for old in [k for k, v in self._pending.items() if now - v["born"] > STATE_TTL_S]:
                del self._pending[old]
            while len(self._pending) >= MAX_PENDING:  # a flood of started sign-ins must not grow without end
                del self._pending[min(self._pending, key=lambda k: self._pending[k]["born"])]
            self._pending[state] = {
                "verifier": verifier,
                "nonce": nonce,
                "binder": binder,
                "born": now,
                "redirect": redirect,
            }
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        query = urlencode(
            {
                "client_id": cfg["client_id"],
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": cfg["scopes"],
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        sep = "&" if "?" in found["authorization_endpoint"] else "?"
        return f"{found['authorization_endpoint']}{sep}{query}", binder

    def callback(self, params: dict[str, str], binder: str) -> tuple[str, dict[str, Any]]:
        """Finish a sign-in. Returns (session token, user), or raises SsoError."""
        cfg = self._auth.sso_config()
        if cfg is None:
            raise SsoError("Single sign-on is not turned on.")
        if params.get("error"):  # the person said no, or the provider refused
            raise SsoError("The single sign-on was cancelled or refused by the provider.")
        state, code = params.get("state", ""), params.get("code", "")
        with self._lock:
            mine = self._pending.pop(state, None)  # one use only
        if not (state and code and mine) or self._now() - mine["born"] > STATE_TTL_S:
            raise SsoError("That sign-in has expired or was already used. Click the single sign-on button again.")
        if not (binder and hmac.compare_digest(binder, mine["binder"])):
            raise SsoError("That sign-in was started in a different browser. Click the single sign-on button again.")
        found = self.discover(cfg["issuer"])
        form = {
            "code": code,
            "client_id": cfg["client_id"],
            "redirect_uri": mine["redirect"],
            "grant_type": "authorization_code",
            "code_verifier": mine["verifier"],
        }
        if cfg["client_secret"]:
            form["client_secret"] = cfg["client_secret"]
        reply = self._post(found["token_endpoint"], form)
        who = self._identity(str(reply.get("id_token") or ""), cfg, mine["nonce"], found["keys"])
        try:
            return self._auth.login_sso(who)
        except AuthError as e:
            raise SsoError(str(e)) from None

    def _identity(self, token: str, cfg: dict[str, Any], nonce: str, keys: list[dict[str, Any]]) -> dict[str, Any]:
        """Who the provider's identity token says this is: {email, name, groups}."""
        try:
            head_b, body_b, sig_b = token.split(".")
            header, payload = json.loads(_unb64(head_b)), json.loads(_unb64(body_b))
        except (ValueError, TypeError):
            raise SsoError("The provider's answer could not be read. Try again.") from None
        if not isinstance(payload, dict) or not isinstance(header, dict):
            raise SsoError("The provider's answer could not be read. Try again.")
        alg = str(header.get("alg") or "")
        if alg.lower() == "none" or not alg:
            raise SsoError("The provider's answer was not signed, so it was refused.")
        if alg == "RS256":
            self._check_signature(f"{head_b}.{body_b}".encode(), _unb64(sig_b), str(header.get("kid") or ""), keys)
        if str(payload.get("iss") or "").rstrip("/") != cfg["issuer"].rstrip("/"):
            raise SsoError("The provider's answer came from somebody else than the provider you set up.")
        audience = payload.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if cfg["client_id"] not in audiences or (len(audiences) > 1 and payload.get("azp") != cfg["client_id"]):
            raise SsoError("The provider's answer was not meant for Quartermaster.")
        exp = payload.get("exp")
        if not isinstance(exp, int | float) or exp < self._now() - 60:
            raise SsoError("The provider's answer has expired. Try again.")
        if not hmac.compare_digest(str(payload.get("nonce") or ""), nonce):
            raise SsoError("The provider's answer did not match this sign-in. Try again.")
        email = str(payload.get("email") or "")
        if not email:  # Microsoft Entra ID often sends the address only as the user name
            name = str(payload.get("preferred_username") or payload.get("upn") or "")
            email = name if "@" in name else ""
        if not email:
            raise SsoError("The provider did not say the person's e-mail address. Ask for the email scope.")
        if payload.get("email_verified") in (False, "false"):
            raise SsoError("The provider has not verified that e-mail address.")
        raw = payload.get(cfg["groups_claim"])
        groups = [str(g) for g in raw] if isinstance(raw, list) else [str(raw)] if isinstance(raw, str) and raw else []
        return {"email": email, "name": str(payload.get("name") or ""), "groups": groups}

    def _check_signature(self, signed: bytes, signature: bytes, kid: str, keys: list[dict[str, Any]]) -> None:
        candidates = [k for k in keys if k.get("kty") == "RSA" and (not kid or k.get("kid") == kid)]
        if not candidates:
            raise SsoError("The provider's signing key was not found. Try again, or check the provider.")
        for key in candidates:
            try:
                n, e = int.from_bytes(_unb64(str(key["n"])), "big"), int.from_bytes(_unb64(str(key["e"])), "big")
            except (KeyError, ValueError, TypeError):
                continue
            if verify_rs256(signed, signature, n, e):
                return
        raise SsoError("The provider's answer has a signature that does not check out, so it was refused.")


# ---------------------------------------------------------------------- signatures


def verify_rs256(signed: bytes, signature: bytes, n: int, e: int) -> bool:
    """RSASSA-PKCS1-v1_5 with SHA-256 (RFC 8017): the signature to the public exponent must be the padded hash."""
    size = (n.bit_length() + 7) // 8
    if len(signature) != size or size < 64 or e < 3 or e % 2 == 0:
        return False
    sig = int.from_bytes(signature, "big")
    if sig >= n:
        return False
    got = pow(sig, e, n).to_bytes(size, "big")
    digest = hashlib.sha256(signed).digest()
    tail = _SHA256_PREFIX + digest
    expected = b"\x00\x01" + b"\xff" * (size - len(tail) - 3) + b"\x00" + tail
    return hmac.compare_digest(got, expected)


def _unb64(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _address_ok(url: str) -> bool:
    p = urlsplit(url)
    local = p.hostname in ("localhost", "127.0.0.1")
    return bool(p.hostname) and (p.scheme == "https" or (p.scheme == "http" and local))


# ---------------------------------------------------------------------- talking to the provider


def _get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:  # noqa: S310 - an https address an administrator chose
            data = json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SsoError(f"The provider answered {e.code} at {urlsplit(url).netloc}. Check the address.") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise SsoError(
            f"Could not reach {urlsplit(url).netloc}. Check the address and the internet connection."
        ) from None
    if not isinstance(data, dict):
        raise SsoError("The provider's answer could not be read. Check the address.")
    return data


def _post_form(url: str, form: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=urlencode(form).encode(),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:  # noqa: S310 - the provider's token address, https
            data = json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            why = str(json.loads(e.read() or b"{}").get("error") or e.code)
        except ValueError:
            why = str(e.code)
        raise SsoError(
            f"The provider refused the sign-in ({why[:60]}). Check the client ID and secret in Settings."
        ) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise SsoError("Could not reach the provider. Check the internet connection and try again.") from None
    if not isinstance(data, dict):
        raise SsoError("The provider's answer could not be read. Try again.")
    return data
