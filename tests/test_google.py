"""Sign in with Google: only people an administrator added, only with a good answer from Google."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from test_auth import GOOD, Client, make_hub  # noqa: F401  (Client and make_hub are helpers)

from quartermaster.service.api import ApiError
from quartermaster.service.auth import Auth, AuthError, email_key
from quartermaster.service.google import BINDER_COOKIE, GoogleError, GoogleSignIn
from quartermaster.service.hub import Hub

CLIENT_ID = "1234567890-abcdefghijklmnop.apps.googleusercontent.com"
SECRET = "GOCSPX-a-very-secret-client-secret"
FAKE_AUTH = "https://fake-google.test/auth"


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


def jwt(payload: dict[str, Any]) -> str:
    def seg(obj: Any) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{seg({'alg': 'RS256'})}.{seg(payload)}.signature"


class FakeGoogle:
    """What Google would answer at its token address, and what it was asked."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.email = "jane@gmail.com"
        self.override: dict[str, Any] = {}
        self.asked: list[dict[str, str]] = []
        self.nonce = ""
        self.raises: Exception | None = None
        self.reply: dict[str, Any] | None = None

    def __call__(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        self.asked.append({"url": url, **form})
        if self.raises:
            raise self.raises
        if self.reply is not None:
            return self.reply
        claims = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "exp": self.clock.t + 3600,
            "nonce": self.nonce,
            "email": self.email,
            "email_verified": True,
            **self.override,
        }
        return {"id_token": jwt(claims), "access_token": "not-used"}


def setup(tmp_path: Path) -> tuple[Auth, GoogleSignIn, FakeGoogle, Clock, dict[str, Any]]:
    clock = Clock()
    told: list[tuple[str, str, dict[str, Any], str]] = []
    auth = Auth(
        tmp_path / "users.db",
        now=clock,
        record=lambda what, subject, details, who: told.append((what, subject, details, who)),
        key_file=tmp_path / "vault.key",
    )
    _, admin = auth.enable("sai", "Sai Ram", GOOD)
    auth.google_update(
        admin,
        {"client_id": CLIENT_ID, "client_secret": SECRET, "public_url": "http://localhost:8765", "enabled": True},
    )
    fake = FakeGoogle(clock)
    google = GoogleSignIn(auth, auth_url=FAKE_AUTH, token_url="https://fake-google.test/token", now=clock, post=fake)
    return auth, google, fake, clock, {"admin": admin, "told": told}


def begin(google: GoogleSignIn, fake: FakeGoogle) -> tuple[dict[str, str], str]:
    """Click "Continue with Google": the parameters Google was sent, and the cookie value for the browser."""
    url, binder = google.start()
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert url.startswith(FAKE_AUTH + "?")
    fake.nonce = q["nonce"]
    return q, binder


def add_jane(auth: Auth, admin: dict[str, Any], email: str = "jane@gmail.com", **more: Any) -> dict[str, Any]:
    made = auth.create(
        admin, {"full_name": "Jane Doe", "email": email, "google_only": True, "roles": ["tester"], **more}
    )
    return made["user"]  # type: ignore[no-any-return]


# ------------------------------------------------------------------ the way there


def test_the_person_is_sent_to_google_with_a_one_time_state_and_a_proof_key(tmp_path: Path) -> None:
    auth, google, fake, _, ctx = setup(tmp_path)
    add_jane(auth, ctx["admin"])
    q, binder = begin(google, fake)
    assert q["client_id"] == CLIENT_ID and q["response_type"] == "code" and q["scope"] == "openid email"
    assert q["redirect_uri"] == "http://localhost:8765/api/auth/google/callback"
    assert q["code_challenge_method"] == "S256" and q["prompt"] == "select_account"
    assert len(q["state"]) >= 24 and len(q["nonce"]) >= 24 and len(binder) >= 16
    assert SECRET not in json.dumps(q)  # the secret never goes through the browser
    q2, binder2 = begin(google, fake)
    assert q2["state"] != q["state"] and q2["nonce"] != q["nonce"] and binder2 != binder

    google.callback({"code": "c0de", "state": q2["state"]}, binder2)
    asked = fake.asked[-1]  # what Quartermaster said to Google's token address, server to server
    assert asked["code"] == "c0de" and asked["client_id"] == CLIENT_ID and asked["client_secret"] == SECRET
    assert asked["redirect_uri"] == q2["redirect_uri"] and asked["grant_type"] == "authorization_code"
    proof = base64.urlsafe_b64encode(hashlib.sha256(asked["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert proof == q2["code_challenge"]  # the verifier belongs to the challenge Google was given


def test_the_redirect_address_comes_from_the_setting_or_from_this_computer(tmp_path: Path) -> None:
    auth, google, _, _, ctx = setup(tmp_path)
    assert google.redirect_uri("http://127.0.0.1:8765") == "http://localhost:8765/api/auth/google/callback"
    auth.google_update(ctx["admin"], {"public_url": "https://qm.example.com/"})
    assert google.redirect_uri() == "https://qm.example.com/api/auth/google/callback"
    auth.google_update(ctx["admin"], {"public_url": ""})
    assert google.redirect_uri("http://127.0.0.1:8765") == "http://localhost:8765/api/auth/google/callback"


# ------------------------------------------------------------------ signing in


def test_a_person_the_administrator_added_is_signed_in(tmp_path: Path) -> None:
    auth, google, fake, _, ctx = setup(tmp_path)
    jane = add_jane(auth, ctx["admin"])
    assert jane["google_only"] and jane["email"] == "jane@gmail.com" and jane["roles"] == ["tester"]
    q, binder = begin(google, fake)
    token, user = google.callback({"code": "c0de", "state": q["state"]}, binder)
    assert user["username"] == jane["username"] and user["full_name"] == "Jane Doe" and user["roles"] == ["tester"]
    assert auth.user_for(token)["id"] == jane["id"]  # type: ignore[index]
    assert not user["must_change"]
    assert ("Signed in with Google", jane["username"], {}, "Jane Doe") in ctx["told"]
    assert all(SECRET not in json.dumps(t) and "c0de" not in json.dumps(t) for t in ctx["told"])


def test_a_google_account_that_was_not_added_gets_nothing(tmp_path: Path) -> None:
    auth, google, fake, _, ctx = setup(tmp_path)
    add_jane(auth, ctx["admin"])
    fake.email = "stranger@gmail.com"  # a real Google account, but nobody asked for it
    q, binder = begin(google, fake)
    with pytest.raises(GoogleError, match="stranger@gmail.com has not been given access"):
        google.callback({"code": "c", "state": q["state"]}, binder)
    assert any(t[0] == "Google sign-in refused" and t[1] == "stranger@gmail.com" for t in ctx["told"])
    assert len(auth.users()) == 2  # nothing was created: just the admin and Jane


def test_a_switched_off_person_cannot_sign_in_with_google(tmp_path: Path) -> None:
    auth, google, fake, _, ctx = setup(tmp_path)
    jane = add_jane(auth, ctx["admin"])
    auth.update(ctx["admin"], jane["id"], {"active": False})
    q, binder = begin(google, fake)
    with pytest.raises(GoogleError, match="has not been given access"):
        google.callback({"code": "c", "state": q["state"]}, binder)


def test_gmail_dots_and_plus_tags_do_not_matter_but_other_domains_are_exact(tmp_path: Path) -> None:
    auth, google, fake, _, ctx = setup(tmp_path)
    add_jane(auth, ctx["admin"], email="janedoe@gmail.com")
    for given in ("Jane.Doe@gmail.com", "jane.doe+quartermaster@gmail.com", "JANEDOE@googlemail.com"):
        fake.email = given
        q, binder = begin(google, fake)
        assert google.callback({"code": "c", "state": q["state"]}, binder)[1]["full_name"] == "Jane Doe"
    add_jane(auth, ctx["admin"], email="a.b@corp.example", full_name="Ann Bee")
    fake.email = "ab@corp.example"  # not Gmail: dots count
    q, binder = begin(google, fake)
    with pytest.raises(GoogleError, match="has not been given access"):
        google.callback({"code": "c", "state": q["state"]}, binder)


@pytest.mark.parametrize(
    ("given", "key"),
    [
        ("Jane.Doe+x@Gmail.com", "janedoe@gmail.com"),
        ("jane@googlemail.com", "jane@gmail.com"),
        ("a.b@corp.example", "a.b@corp.example"),
        ("A+tag@corp.example", "a+tag@corp.example"),
    ],
)
def test_how_addresses_are_compared(given: str, key: str) -> None:
    assert email_key(given) == key


# ------------------------------------------------------------------ what must not work


def test_a_sign_in_can_be_finished_once_only_in_time_and_in_the_same_browser(tmp_path: Path) -> None:
    auth, google, fake, clock, ctx = setup(tmp_path)
    add_jane(auth, ctx["admin"])

    q, binder = begin(google, fake)
    google.callback({"code": "c", "state": q["state"]}, binder)
    with pytest.raises(GoogleError, match="expired or was already used"):  # the same state again
        google.callback({"code": "c", "state": q["state"]}, binder)
    with pytest.raises(GoogleError, match="expired or was already used"):  # a state nobody made
        google.callback({"code": "c", "state": "made-up"}, binder)
    with pytest.raises(GoogleError, match="expired or was already used"):  # no code
        q2, b2 = begin(google, fake)
        google.callback({"state": q2["state"]}, b2)

    q3, b3 = begin(google, fake)
    with pytest.raises(GoogleError, match="different browser"):  # someone else's browser finishes it
        google.callback({"code": "c", "state": q3["state"]}, "another-browsers-cookie")
    q4, b4 = begin(google, fake)
    with pytest.raises(GoogleError, match="different browser"):  # no cookie at all
        google.callback({"code": "c", "state": q4["state"]}, "")
    q5, b5 = begin(google, fake)
    clock.t += 601
    with pytest.raises(GoogleError, match="expired"):
        google.callback({"code": "c", "state": q5["state"]}, b5)
    q6, b6 = begin(google, fake)
    with pytest.raises(GoogleError, match="cancelled"):  # the person pressed Cancel at Google
        google.callback({"error": "access_denied", "state": q6["state"]}, b6)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"aud": "someone-elses-app.apps.googleusercontent.com"}, "not meant for Quartermaster"),
        ({"aud": ["x", "y"]}, "not meant for Quartermaster"),
        ({"iss": "https://evil.example.com"}, "not meant for Quartermaster"),
        ({"exp": 1_000}, "expired"),
        ({"exp": "soon"}, "expired"),
        ({"nonce": "not-the-one"}, "did not match this sign-in"),
        ({"email_verified": False}, "not verified"),
        ({"email_verified": "false"}, "not verified"),
        ({"email": ""}, "not verified"),
    ],
)
def test_an_answer_from_google_that_is_not_right_signs_nobody_in(
    tmp_path: Path, override: dict[str, Any], message: str
) -> None:
    auth, google, fake, _, ctx = setup(tmp_path)
    add_jane(auth, ctx["admin"])
    q, binder = begin(google, fake)
    fake.override = override
    with pytest.raises(GoogleError, match=message):
        google.callback({"code": "c", "state": q["state"]}, binder)
    assert not any(t[0] == "Signed in with Google" for t in ctx["told"])


def test_a_good_answer_may_name_its_issuer_either_way_and_list_the_audience(tmp_path: Path) -> None:
    auth, google, fake, _, ctx = setup(tmp_path)
    add_jane(auth, ctx["admin"])
    for override in ({"iss": "accounts.google.com"}, {"aud": [CLIENT_ID, "other"]}, {"email_verified": "true"}):
        q, binder = begin(google, fake)
        fake.override = override
        assert google.callback({"code": "c", "state": q["state"]}, binder)[1]["full_name"] == "Jane Doe"


@pytest.mark.parametrize(
    "reply",
    [
        {},
        {"id_token": ""},
        {"id_token": "no-dots"},
        {"id_token": "a.b.c"},
        {"id_token": "a." + "!!!" + ".c"},
        {"id_token": jwt([1])},
    ],
)
def test_an_answer_that_cannot_be_read_signs_nobody_in(tmp_path: Path, reply: dict[str, Any]) -> None:
    auth, google, fake, _, ctx = setup(tmp_path)
    add_jane(auth, ctx["admin"])
    q, binder = begin(google, fake)
    fake.reply = reply
    with pytest.raises(GoogleError, match="could not be read|not meant|not verified|expired"):
        google.callback({"code": "c", "state": q["state"]}, binder)


def test_google_refusing_or_not_answering_is_said_in_words_without_the_secret(tmp_path: Path) -> None:
    auth, google, fake, _, ctx = setup(tmp_path)
    add_jane(auth, ctx["admin"])
    q, binder = begin(google, fake)
    fake.raises = GoogleError(
        "Google refused the sign-in (invalid_client). Check the client ID and secret in Settings."
    )
    with pytest.raises(GoogleError, match="invalid_client") as e:
        google.callback({"code": "c", "state": q["state"]}, binder)
    assert SECRET not in str(e.value)


def test_the_real_post_turns_google_errors_into_plain_words() -> None:
    from quartermaster.service.google import _post_form

    with pytest.raises(GoogleError, match="Could not reach Google"):
        _post_form("http://127.0.0.1:9/none", {"a": "b"})


def test_too_many_started_sign_ins_are_not_all_kept(tmp_path: Path) -> None:
    _, google, _, _, _ = setup(tmp_path)
    for _ in range(80):
        google.start()
    assert len(google._pending) <= 50


def test_nothing_starts_while_google_is_off_or_incomplete(tmp_path: Path) -> None:
    auth, google, _, _, ctx = setup(tmp_path)
    auth.google_update(ctx["admin"], {"enabled": False})
    with pytest.raises(GoogleError, match="not turned on"):
        google.start()
    with pytest.raises(GoogleError, match="not turned on"):
        google.callback({"code": "c", "state": "s"}, "b")
    assert auth.google_config() is None


# ------------------------------------------------------------------ settings and users


def test_the_google_settings_are_checked_and_the_secret_is_kept_encrypted_and_hidden(tmp_path: Path) -> None:
    auth, _, _, _, ctx = setup(tmp_path)
    admin = ctx["admin"]
    view = auth.google_view()
    assert view == {"enabled": True, "client_id": CLIENT_ID, "secret_set": True, "public_url": "http://localhost:8765"}
    assert SECRET not in json.dumps(view)
    raw = (tmp_path / "users.db").read_bytes()
    assert SECRET.encode() not in raw  # not readable in the file
    auth.google_update(admin, {"client_id": CLIENT_ID})  # the secret is kept when it is left out
    assert auth.google_config()["client_secret"] == SECRET  # type: ignore[index]
    for bad, message in (
        ({"client_id": "x"}, "client ID"),
        ({"client_id": "has space and ??? chars here"}, "client ID"),
        ({"client_secret": "short"}, "client secret"),
        ({"public_url": "http://qm.example.com"}, "must start with https://"),
        ({"public_url": "https://qm.example.com/some/path"}, "no path"),
        ({"public_url": "ftp://qm.example.com"}, "must start with https://"),
        ({"surprise": 1}, "unknown settings"),
    ):
        with pytest.raises(AuthError, match=message):
            auth.google_update(admin, bad)
    auth.google_update(admin, {"public_url": "http://127.0.0.1:8765"})  # this computer is fine without https
    auth.google_update(admin, {"client_secret": "", "enabled": False})
    assert not auth.google_view()["secret_set"] and auth.google_config() is None
    with pytest.raises(AuthError, match="fill in the client ID and the client secret"):
        auth.google_update(admin, {"enabled": True})


def test_a_user_can_be_added_by_google_address_alone(tmp_path: Path) -> None:
    auth, _, _, _, ctx = setup(tmp_path)
    admin = ctx["admin"]
    made = auth.create(
        admin, {"full_name": "Jane Doe", "email": "Jane.Doe@gmail.com", "google_only": True, "roles": ["approver"]}
    )
    user = made["user"]
    assert (
        made["password"] is None and user["username"] == "janedoe" and user["google_only"] and not user["must_change"]
    )
    assert user["email"] == "jane.doe@gmail.com"  # as typed, in lower case
    again = auth.create(admin, {"full_name": "Other Jane", "email": "jane.doe2@example.com", "google_only": True})[
        "user"
    ]
    assert again["username"] == "jane.doe2"
    with pytest.raises(AuthError, match="already belongs to janedoe"):
        auth.create(admin, {"full_name": "Third", "email": "j.a.n.e.doe+x@gmail.com", "google_only": True})
    with pytest.raises(AuthError, match="Google e-mail address"):
        auth.create(admin, {"full_name": "No Mail", "username": "nomail", "google_only": True})
    with pytest.raises(AuthError, match="not an e-mail address"):
        auth.create(admin, {"full_name": "Bad", "email": "nonsense", "google_only": True})


def test_a_google_only_person_has_no_password_to_sign_in_with(tmp_path: Path) -> None:
    auth, _, _, _, ctx = setup(tmp_path)
    admin = ctx["admin"]
    jane = add_jane(auth, ctx["admin"], username="jane")
    with pytest.raises(AuthError, match="wrong user name or password"):
        auth.login("jane", GOOD)  # whatever is tried
    with pytest.raises(AuthError, match="Reset password"):
        auth.update(admin, jane["id"], {"google_only": False})
    reset = auth.reset_password(admin, jane["id"])  # an administrator can give them a password again
    token, user = auth.login("jane", reset["password"])
    assert user["must_change"]
    assert not [u for u in auth.users() if u["username"] == "jane"][0]["google_only"]
    assert auth.user_for(token)


def test_a_person_can_have_both_a_password_and_a_google_address(tmp_path: Path) -> None:
    auth, _, _, _, ctx = setup(tmp_path)
    admin = ctx["admin"]
    made = auth.create(admin, {"full_name": "Jo Both", "username": "jo", "roles": ["tester"]})
    assert made["password"] and not made["user"]["email"]
    uid = made["user"]["id"]
    auth.update(admin, uid, {"email": "jo@gmail.com"})
    assert auth.login("jo", made["password"])[1]["email"] == "jo@gmail.com"
    assert auth.login_google("Jo@gmail.com")[1]["username"] == "jo"
    auth.update(admin, uid, {"google_only": True})
    with pytest.raises(AuthError):
        auth.login("jo", made["password"])
    auth.update(admin, uid, {"email": ""})  # no address, no Google
    with pytest.raises(AuthError, match="not on the list"):
        auth.login_google("jo@gmail.com")
    with pytest.raises(AuthError, match="add the person's Google e-mail address first"):
        auth.update(admin, uid, {"google_only": True})
    auth.create(admin, {"full_name": "Dup", "email": "a@gmail.com", "google_only": True})
    with pytest.raises(AuthError, match="already belongs to"):
        auth.update(admin, uid, {"email": "A@gmail.com"})


def test_users_made_before_google_existed_still_work(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "users.db"
    db = sqlite3.connect(str(path))
    db.executescript(
        "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,"
        " full_name TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',"
        " roles TEXT NOT NULL DEFAULT '', pw TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,"
        " must_change INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, last_login TEXT);"
        "CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
    )
    from quartermaster.service.auth import hash_password

    db.execute(
        "INSERT INTO users (id, username, full_name, roles, pw, created_at)"
        " VALUES ('1','old','Old Admin','admin',?, 'x')",
        (hash_password(GOOD),),
    )
    db.execute("INSERT INTO state VALUES ('enabled','1')")
    db.commit()
    db.close()
    auth = Auth(path, key_file=tmp_path / "vault.key")
    token, user = auth.login("old", GOOD)
    assert user["email"] == "" and not user["google_only"] and auth.user_for(token)


# ------------------------------------------------------------------ in the running service


def hub_with_google(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Hub, Client, FakeGoogle]:
    hub = make_hub(tmp_path, monkeypatch)
    hub.start()
    fake = FakeGoogle(Clock())
    fake.clock = Clock()
    hub.google = GoogleSignIn(hub.auth, auth_url=FAKE_AUTH, token_url="https://fake-google.test/token", post=fake)
    hub.address = "http://127.0.0.1:8765"
    admin = Client(hub)
    admin.go("POST", "/api/auth/enable", {"username": "sai", "full_name": "Sai Ram", "password": GOOD})
    return hub, admin, fake


def configure(admin: Client) -> dict[str, Any]:
    return admin.go("POST", "/api/auth/google", {"client_id": CLIENT_ID, "client_secret": SECRET, "enabled": True})  # type: ignore[no-any-return]


def test_the_page_sets_up_google_and_never_sees_the_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, admin, _ = hub_with_google(tmp_path, monkeypatch)
    try:
        assert admin.go("GET", "/api/auth/status")["google"] is False  # no button until it is set up
        view = admin.go("GET", "/api/auth/google")
        assert view["enabled"] is False and view["redirect_uri"] == "http://localhost:8765/api/auth/google/callback"
        done = configure(admin)
        assert done["enabled"] and done["secret_set"] and done["client_id"] == CLIENT_ID
        assert SECRET not in json.dumps(admin.go("GET", "/api/auth/google"))
        assert Client(hub).go("GET", "/api/auth/status")["google"] is True  # the sign-in page now shows the button
        audit = (tmp_path / ".qm" / "audit.jsonl").read_text(encoding="utf-8")
        assert "Changed the Google sign-in settings" in audit and SECRET not in audit and CLIENT_ID not in audit
        assert admin.asks("POST", "/api/auth/google", {"client_id": "x"})[0] == 400
    finally:
        hub.stop()


def test_only_an_administrator_may_read_or_change_the_google_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, admin, _ = hub_with_google(tmp_path, monkeypatch)
    try:
        made = admin.go(
            "POST", "/api/users", {"action": "create", "username": "jo", "full_name": "Jo Tester", "roles": ["tester"]}
        )
        jo = Client(hub)
        jo.go("POST", "/api/auth/login", {"username": "jo", "password": made["password"]})
        jo.go("POST", "/api/auth/password", {"current": made["password"], "new": "jo-own-long-password"})
        assert jo.asks("GET", "/api/auth/google")[0] == 403
        assert jo.asks("POST", "/api/auth/google", {"enabled": False})[0] == 403
        assert Client(hub).asks("GET", "/api/auth/google")[0] == 401
    finally:
        hub.stop()


def test_continue_with_google_through_the_service_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, admin, fake = hub_with_google(tmp_path, monkeypatch)
    try:
        configure(admin)
        admin.go(
            "POST",
            "/api/users",
            {
                "action": "create",
                "full_name": "Jane Doe",
                "email": "jane@gmail.com",
                "google_only": True,
                "roles": ["tester"],
            },
        )
        browser = Client(hub)  # a person who is not signed in

        go = hub.handle("GET", "/api/auth/google/start", b"")
        assert go.status == 302 and (go.location or "").startswith(FAKE_AUTH + "?")
        cookie = go.set_cookie if isinstance(go.set_cookie, str) else ""
        assert cookie.startswith(BINDER_COOKIE + "=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie
        assert "Path=/api/auth/google" in cookie and "Max-Age=600" in cookie
        q = {k: v[0] for k, v in parse_qs(urlsplit(go.location or "").query).items()}
        fake.nonce = q["nonce"]
        binder = cookie.split(";")[0]

        back = hub.handle("GET", f"/api/auth/google/callback?code=c0de&state={q['state']}", b"", binder)
        assert back.status == 302 and back.location == "/#/"
        cookies = back.set_cookie if isinstance(back.set_cookie, list) else []
        session = next(c for c in cookies if c.startswith("qm_session=") and "Max-Age=0" not in c)
        assert "HttpOnly" in session and "SameSite=Strict" in session
        assert any(c.startswith(BINDER_COOKIE + "=;") for c in cookies)  # the one-time cookie is cleared
        browser.cookie = session.split(";")[0]
        me = browser.go("GET", "/api/auth/status")["user"]
        assert me["full_name"] == "Jane Doe" and me["email"] == "jane@gmail.com" and me["roles"] == ["tester"]
        assert isinstance(browser.go("GET", "/api/status"), dict)
        assert browser.asks("GET", "/api/users")[0] == 403  # a tester, as given
    finally:
        hub.stop()


def test_a_refused_google_sign_in_goes_back_to_the_sign_in_page_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, admin, fake = hub_with_google(tmp_path, monkeypatch)
    try:
        configure(admin)
        fake.email = "stranger@gmail.com"
        go = hub.handle("GET", "/api/auth/google/start", b"")
        q = {k: v[0] for k, v in parse_qs(urlsplit(go.location or "").query).items()}
        fake.nonce = q["nonce"]
        binder = (go.set_cookie if isinstance(go.set_cookie, str) else "").split(";")[0]
        back = hub.handle("GET", f"/api/auth/google/callback?code=c&state={q['state']}", b"", binder)
        assert back.status == 302 and (back.location or "").startswith("/#/login?error=")
        assert "stranger%40gmail.com" in (back.location or "") and "has%20not%20been%20given%20access" in (
            back.location or ""
        )
        assert not any(
            "qm_session=" in c and "Max-Age=0" not in c
            for c in ([back.set_cookie] if isinstance(back.set_cookie, str) else back.set_cookie or [])
        )
        wrong = hub.handle("GET", f"/api/auth/google/callback?code=c&state={q['state']}", b"", binder)  # used already
        assert "expired%20or%20was%20already%20used" in (wrong.location or "")
    finally:
        hub.stop()


def test_google_is_not_offered_when_it_is_off_or_sign_in_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, admin, _ = hub_with_google(tmp_path, monkeypatch)
    try:
        start = hub.handle("GET", "/api/auth/google/start", b"")  # not set up
        assert start.status == 302 and "Sign%20in%20with%20Google%20is%20not%20turned%20on" in (start.location or "")
        configure(admin)
        admin.go("POST", "/api/auth/disable", {"password": GOOD})
        off = hub.handle("GET", "/api/auth/google/start", b"")
        assert "Sign-in%20is%20not%20turned%20on" in (off.location or "")
        assert "Sign-in%20is%20not%20turned%20on" in (
            hub.handle("GET", "/api/auth/google/callback?code=c&state=s", b"").location or ""
        )
        with pytest.raises(ApiError):
            hub.handle("GET", "/api/auth/google", b"")  # sign-in is off: nothing to configure here
    finally:
        hub.stop()
