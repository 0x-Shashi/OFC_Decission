"""Sign in with Google (the standard "authorization code" flow, with PKCE), standard library only.

    1. The person clicks "Continue with Google": `start` sends them to Google with a one-time `state`, a
       `nonce` and a PKCE challenge, and sets a short-lived cookie that ties this sign-in to their browser.
    2. Google asks them to choose an account and agree, then sends them back to Quartermaster with a `code`.
    3. `callback` checks the `state` (used once, within 10 minutes) and the cookie, swaps the `code` for an
       identity token by talking to Google directly over HTTPS (with the client secret and the PKCE verifier),
       checks what the token says (issued by Google, for this app, not expired, the nonce, e-mail verified),
       and finally asks Quartermaster whether that e-mail address belongs to a user.

Google only proves who the person is. What they may do comes from the roles an administrator gave them, and
a Google account that is not on the list is refused: having a Gmail address is never enough.

The identity token comes straight from Google's token address in answer to our own request, over a verified
HTTPS connection, with our secret. Google's documentation allows trusting it without checking its signature
in that case, so the signature is not checked; everything else in it is.

Nothing secret is ever put in a page, a redirect or a message: the client secret stays on the server (encrypted
at rest, see auth.py) and errors say what failed without repeating what was sent.
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
from urllib.parse import urlencode

from quartermaster.service.auth import Auth, AuthError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
STATE_TTL_S = 600
MAX_PENDING = 50
CALLBACK = "/api/auth/google/callback"
BINDER_COOKIE = "qm_oauth"

Post = Callable[[str, dict[str, str]], dict[str, Any]]


class GoogleError(Exception):
    """The sign-in did not work. The message is for the person at the sign-in page and holds no secret."""


class GoogleSignIn:
    def __init__(
        self,
        auth: Auth,
        *,
        auth_url: str = AUTH_URL,
        token_url: str = TOKEN_URL,
        now: Callable[[], float] = time.time,
        post: Post | None = None,
    ):
        self._auth = auth
        self._auth_url, self._token_url = auth_url, token_url
        self._now = now
        self._post = post or _post_form
        self._pending: dict[str, dict[str, Any]] = {}  # state -> what the callback must find again; in memory only
        self._lock = threading.Lock()

    def redirect_uri(self, fallback: str = "") -> str:
        """Where Google sends the person back to. This exact address must be given in the Google console."""
        cfg = self._auth.google_view()
        base = cfg["public_url"] or fallback.replace("127.0.0.1", "localhost")
        return base.rstrip("/") + CALLBACK

    def start(self, fallback: str = "") -> tuple[str, str]:
        """(the Google address to send the person to, the value of the cookie that ties this to their browser)."""
        cfg = self._auth.google_config()
        if cfg is None:
            raise GoogleError("Sign in with Google is not turned on.")
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
                "scope": "openid email",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return f"{self._auth_url}?{query}", binder

    def callback(self, params: dict[str, str], binder: str) -> tuple[str, dict[str, Any]]:
        """Finish a sign-in. Returns (session token, user), or raises GoogleError."""
        cfg = self._auth.google_config()
        if cfg is None:
            raise GoogleError("Sign in with Google is not turned on.")
        if params.get("error"):  # the person said no, or Google refused
            raise GoogleError("Google sign-in was cancelled.")
        state, code = params.get("state", ""), params.get("code", "")
        with self._lock:
            mine = self._pending.pop(state, None)  # one use only
        if not (state and code and mine) or self._now() - mine["born"] > STATE_TTL_S:
            raise GoogleError("That sign-in has expired or was already used. Click Continue with Google again.")
        if not (binder and hmac.compare_digest(binder, mine["binder"])):
            raise GoogleError("That sign-in was started in a different browser. Click Continue with Google again.")
        reply = self._post(
            self._token_url,
            {
                "code": code,
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "redirect_uri": mine["redirect"],
                "grant_type": "authorization_code",
                "code_verifier": mine["verifier"],
            },
        )
        email = self._identity(str(reply.get("id_token") or ""), cfg["client_id"], mine["nonce"])
        try:
            return self._auth.login_google(email)
        except AuthError:
            raise GoogleError(
                f"{email} has not been given access to Quartermaster. Ask an administrator to add that address."
            ) from None

    def _identity(self, id_token: str, client_id: str, nonce: str) -> str:
        """The verified e-mail address in Google's identity token."""
        try:
            payload = json.loads(_unb64(id_token.split(".")[1]))
        except (IndexError, ValueError):
            raise GoogleError("Google's answer could not be read. Try again.") from None
        if not isinstance(payload, dict):
            raise GoogleError("Google's answer could not be read. Try again.")
        audience = payload.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if payload.get("iss") not in ISSUERS or client_id not in audiences:
            raise GoogleError("Google's answer was not meant for Quartermaster.")
        exp = payload.get("exp")
        if not isinstance(exp, int | float) or exp < self._now() - 60:
            raise GoogleError("Google's answer has expired. Try again.")
        if not hmac.compare_digest(str(payload.get("nonce") or ""), nonce):
            raise GoogleError("Google's answer did not match this sign-in. Try again.")
        email = str(payload.get("email") or "")
        if not email or payload.get("email_verified") not in (True, "true"):
            raise GoogleError("Google has not verified that e-mail address.")
        return email


def _unb64(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _post_form(url: str, form: dict[str, str]) -> dict[str, Any]:
    """POST a form to Google and read its JSON answer."""
    req = urllib.request.Request(
        url,
        data=urlencode(form).encode(),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:  # noqa: S310 - Google's token address, https
            data = json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            why = str(json.loads(e.read() or b"{}").get("error") or e.code)
        except ValueError:
            why = str(e.code)
        raise GoogleError(
            f"Google refused the sign-in ({why[:60]}). Check the client ID and secret in Settings."
        ) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise GoogleError("Could not reach Google. Check the internet connection and try again.") from None
    if not isinstance(data, dict):
        raise GoogleError("Google's answer could not be read. Try again.")
    return data
