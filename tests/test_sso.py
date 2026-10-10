"""Single sign-on with a company provider: only a good answer from the configured provider, and what it may do comes
from the settings: who is listed, which groups give which roles, whether a first sign-in makes a user."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import oidc_stub as idp
import pytest
from test_auth import GOOD, Client, make_hub

from quartermaster.service.auth import Auth, AuthError
from quartermaster.service.sso import BINDER_COOKIE, SsoError, SsoSignIn, verify_rs256


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


def setup(tmp_path: Path, **settings: Any) -> tuple[Auth, SsoSignIn, idp.Provider, Clock, dict[str, Any]]:
    clock = Clock()
    told: list[tuple[str, str, dict[str, Any], str]] = []
    auth = Auth(
        tmp_path / "users.db",
        now=clock,
        record=lambda what, subject, details, who: told.append((what, subject, details, who)),
        key_file=tmp_path / "vault.key",
    )
    _, admin = auth.enable("sai", "Sai Ram", GOOD)
    auth.sso_update(
        admin,
        {
            "issuer": idp.ISSUER + "/",
            "client_id": idp.CLIENT_ID,
            "client_secret": idp.SECRET,
            "public_url": "http://localhost:8765",
            "enabled": True,
            **settings,
        },
    )
    provider = idp.Provider(now=clock)
    sso = SsoSignIn(auth, now=clock, get=provider.get, post=provider.post)
    return auth, sso, provider, clock, {"admin": admin, "told": told}


def begin(sso: SsoSignIn, provider: idp.Provider) -> tuple[dict[str, str], str]:
    """Click the button: the parameters the provider was sent, and the cookie value for the browser."""
    url, binder = sso.start()
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert url.startswith(provider.authorize + "?")
    provider.nonce = q["nonce"]
    return q, binder


def sign_in(sso: SsoSignIn, provider: idp.Provider) -> tuple[str, dict[str, Any]]:
    q, binder = begin(sso, provider)
    return sso.callback({"code": "c0de", "state": q["state"]}, binder)


def add_jane(auth: Auth, admin: dict[str, Any], email: str = "jane@acme.com", **more: Any) -> dict[str, Any]:
    return auth.create(
        admin, {"full_name": "Jane Doe", "email": email, "google_only": True, "roles": ["tester"], **more}
    )["user"]  # type: ignore[no-any-return]


# ------------------------------------------------------------------ the signature


def test_a_signature_is_checked_against_the_public_key() -> None:
    msg = b"header.payload"
    good = idp.sign(msg)
    assert verify_rs256(msg, good, idp.N, idp.E)
    assert not verify_rs256(b"header.payloae", good, idp.N, idp.E)  # another message
    assert not verify_rs256(msg, bytes([good[0] ^ 1]) + good[1:], idp.N, idp.E)  # a changed signature
    assert not verify_rs256(msg, good[1:], idp.N, idp.E) and not verify_rs256(msg, b"", idp.N, idp.E)
    assert not verify_rs256(msg, good, idp.N + 2, idp.E)  # another key
    assert not verify_rs256(
        msg, (idp.N + 1).to_bytes(256, "big"), idp.N, idp.E
    )  # a number that is not below the modulus
    assert not verify_rs256(msg, good, idp.N, 2) and not verify_rs256(msg, good, idp.N, 1)  # exponents that are no key


# ------------------------------------------------------------------ the settings


def test_the_settings_are_checked_and_the_secret_is_never_given_back(tmp_path: Path) -> None:
    auth, _, _, _, ctx = setup(tmp_path)
    view = auth.sso_view()
    assert (
        view["enabled"] and view["issuer"] == idp.ISSUER and view["secret_set"] and view["client_id"] == idp.CLIENT_ID
    )
    assert (
        idp.SECRET not in json.dumps(view)
        and view["label"] == "single sign-on"
        and view["scopes"] == "openid email profile"
    )
    assert (
        view["provision"] == "listed" and view["role_map"] == {} and not view["require_group"] and not view["enforce"]
    )
    assert idp.SECRET not in (tmp_path / "users.db").read_bytes().decode("latin-1")  # encrypted on disk
    assert auth.sso_config()["client_secret"] == idp.SECRET  # type: ignore[index]
    assert idp.SECRET not in json.dumps(ctx["told"]) and ctx["told"][-1][:2] == (
        "Changed the single sign-on settings",
        "Users",
    )
    for bad, why in [
        ({"issuer": "http://idp.example.com"}, "must start with https"),
        ({"issuer": "https://idp.example.com/?a=b"}, "must start with https"),
        ({"client_id": "has space"}, "client ID"),
        ({"client_secret": "x y"}, "client secret"),
        ({"public_url": "http://qm.example.com"}, "must start with https"),
        ({"scopes": "email profile"}, "must include openid"),
        ({"scopes": "openid bad;scope"}, "words separated"),
        ({"groups_claim": "a b"}, "claim name"),
        ({"domains": "not a domain"}, "not an e-mail domain"),
        ({"provision": "everyone"}, "only the ones you list"),
        ({"default_role": "admin"}, "administrators come from a group"),
        ({"role_map": {"g": "boss"}}, "unknown role"),
        ({"role_map": ["g"]}, "list of a group name"),
        ({"surprise": 1}, "unknown settings"),
    ]:
        with pytest.raises(AuthError, match=why):
            auth.sso_update(ctx["admin"], bad)


def test_it_cannot_be_turned_on_half_set_up_and_a_secret_that_is_left_out_is_kept(tmp_path: Path) -> None:
    clock = Clock()
    auth = Auth(tmp_path / "users.db", now=clock, key_file=tmp_path / "vault.key")
    _, admin = auth.enable("sai", "Sai Ram", GOOD)
    with pytest.raises(AuthError, match="provider address and the client ID"):
        auth.sso_update(admin, {"enabled": True})
    auth.sso_update(
        admin, {"issuer": idp.ISSUER, "client_id": idp.CLIENT_ID, "client_secret": idp.SECRET, "enabled": True}
    )
    auth.sso_update(
        admin,
        {
            "label": "Acme login",
            "domains": "Acme.com; @partner.org",
            "role_map": {"qm-admins": "admin", "qm-qa": ["tester", "approver"]},
        },
    )
    view = auth.sso_view()
    assert view["secret_set"] and view["label"] == "Acme login" and view["domains"] == ["acme.com", "partner.org"]
    assert view["role_map"] == {"qm-admins": ["admin"], "qm-qa": ["tester", "approver"]}
    auth.sso_update(admin, {"client_secret": ""})  # an empty secret removes it: a public client (PKCE only) is allowed
    assert not auth.sso_view()["secret_set"] and auth.sso_config()["client_secret"] == ""  # type: ignore[index]
    auth.sso_update(admin, {"enabled": False})
    assert auth.sso_config() is None and auth.sso_label() is None


# ------------------------------------------------------------------ the way there


def test_the_person_is_sent_to_the_provider_with_a_one_time_state_and_a_proof_key(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path)
    add_jane(auth, ctx["admin"])
    q, binder = begin(sso, provider)
    assert q["client_id"] == idp.CLIENT_ID and q["response_type"] == "code" and q["scope"] == "openid email profile"
    assert q["redirect_uri"] == "http://localhost:8765/api/auth/sso/callback" and q["code_challenge_method"] == "S256"
    assert len(q["state"]) >= 24 and len(q["nonce"]) >= 24 and len(binder) >= 16 and idp.SECRET not in json.dumps(q)
    q2, binder2 = begin(sso, provider)
    assert q2["state"] != q["state"] and binder2 != binder

    sso.callback({"code": "c0de", "state": q2["state"]}, binder2)
    asked = provider.asked[-1]  # what Quartermaster said to the token address, server to server
    assert asked["url"] == f"{idp.ISSUER}/v1/token" and asked["code"] == "c0de" and asked["client_secret"] == idp.SECRET
    assert asked["client_id"] == idp.CLIENT_ID and asked["grant_type"] == "authorization_code"
    proof = base64.urlsafe_b64encode(hashlib.sha256(asked["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert proof == q2["code_challenge"]


def test_the_provider_is_found_from_its_address_and_asked_only_once_in_a_while(tmp_path: Path) -> None:
    auth, sso, provider, clock, ctx = setup(tmp_path, scopes="openid email groups")
    for _ in range(3):
        begin(sso, provider)
    assert provider.fetched.count(f"{idp.ISSUER}/.well-known/openid-configuration") == 1
    assert begin(sso, provider)[0]["scope"] == "openid email groups"
    clock.t += 700  # ten minutes pass: read again, so a changed key is found
    begin(sso, provider)
    assert provider.fetched.count(f"{idp.ISSUER}/.well-known/openid-configuration") == 2


def test_a_provider_that_says_it_is_somebody_else_or_gives_an_unsafe_address_is_refused(tmp_path: Path) -> None:
    _, sso, provider, _, _ = setup(tmp_path)
    provider.doc = {"issuer": "https://evil.example.com"}
    with pytest.raises(SsoError, match="somebody else than the address"):
        sso.start()
    provider.doc = {"token_endpoint": "http://idp.example.com/token"}  # not https, and not this computer
    with pytest.raises(SsoError, match="token endpoint"):
        sso.discover(idp.ISSUER, fresh=True)
    provider.doc = {"jwks_uri": "http://idp.example.com/keys"}
    with pytest.raises(SsoError, match="key address is not a secure address"):
        sso.discover(idp.ISSUER, fresh=True)
    provider.doc = {}
    assert sso.check(idp.ISSUER) == {
        "ok": True,
        "authorization_endpoint": provider.authorize,
        "token_endpoint": f"{idp.ISSUER}/v1/token",
        "keys": 1,
        "signature_checked": True,
    }


def test_starting_when_it_is_off_says_so(tmp_path: Path) -> None:
    auth, sso, _, _, ctx = setup(tmp_path)
    auth.sso_update(ctx["admin"], {"enabled": False})
    with pytest.raises(SsoError, match="not turned on"):
        sso.start()
    with pytest.raises(SsoError, match="not turned on"):
        sso.callback({}, "")


# ------------------------------------------------------------------ signing in


def test_a_person_the_administrator_added_is_signed_in_with_the_roles_they_have(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path)
    jane = add_jane(auth, ctx["admin"])
    token, user = sign_in(sso, provider)
    assert user["id"] == jane["id"] and user["roles"] == ["tester"] and auth.user_for(token)["id"] == jane["id"]  # type: ignore[index]
    assert ctx["told"][-1][:2] == ("Signed in with single sign-on", "jane")


def test_a_person_who_is_not_on_the_list_is_refused_when_users_are_only_the_listed_ones(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path)
    provider.identity["email"] = "stranger@acme.com"
    with pytest.raises(SsoError, match="has not been given access"):
        sign_in(sso, provider)
    assert ctx["told"][-1] == (
        "Single sign-on refused",
        "stranger@acme.com",
        {"reason": "not on the list"},
        "stranger@acme.com",
    )
    assert not [u for u in auth.users() if u["email"] == "stranger@acme.com"]


def test_a_switched_off_user_cannot_sign_in(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path)
    jane = add_jane(auth, ctx["admin"])
    auth.update(ctx["admin"], jane["id"], {"active": False})
    with pytest.raises(SsoError, match="switched this user off"):
        sign_in(sso, provider)


def test_only_the_allowed_e_mail_domains_may_sign_in(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path, domains="acme.com", provision="auto")
    provider.identity["email"] = "someone@gmail.com"
    with pytest.raises(SsoError, match="not at an e-mail domain that may sign in"):
        sign_in(sso, provider)
    provider.identity["email"] = "someone@ACME.com"
    assert sign_in(sso, provider)[1]["email"] == "someone@acme.com"


def test_a_first_sign_in_makes_the_user_when_the_company_wants_that(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path, provision="auto", default_role="tester")
    provider.identity.update(email="new.person@acme.com", name="New Person")
    token, user = sign_in(sso, provider)
    assert user["full_name"] == "New Person" and user["username"] == "new.person" and user["roles"] == ["tester"]
    assert user["origin"] == "sso" and user["google_only"] is True and user["must_change"] is False
    assert ("Created a user from single sign-on", "new.person", {"roles": "tester"}, "New Person") in ctx["told"]
    assert sign_in(sso, provider)[1]["id"] == user["id"]  # the second time it is the same person
    with pytest.raises(AuthError, match="wrong user name or password"):
        auth.login("new.person", "anything-at-all-123")  # nobody has a password for them


def test_the_groups_the_provider_vouches_for_give_the_roles(tmp_path: Path) -> None:
    roles = {"qm-admins": "admin", "QM-Testers": "tester", "qm-approvers": ["approver"]}
    auth, sso, provider, _, ctx = setup(tmp_path, provision="auto", role_map=roles, default_role="")
    provider.identity.update(email="amy@acme.com", name="Amy Admin", groups=["staff", "qm-admins", "qm-testers"])
    user = sign_in(sso, provider)[1]
    assert user["roles"] == ["admin", "tester"]  # the group names are matched without regard to capital letters
    provider.identity["groups"] = ["qm-approvers"]
    assert sign_in(sso, provider)[1]["roles"] == ["approver"]  # the roles follow the provider at every sign-in
    assert (
        "Changed roles from single sign-on",
        "amy",
        {"from": "admin, tester", "to": "approver"},
        "Amy Admin",
    ) in ctx["told"]
    provider.identity["groups"] = ["staff"]
    assert sign_in(sso, provider)[1]["roles"] == ["approver"]  # no group at all: what they had is kept, not wiped


def test_the_provider_never_takes_the_last_administrator_away(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path, role_map={"qm-testers": "tester"})
    auth.update(ctx["admin"], ctx["admin"]["id"], {"email": "sai@acme.com"})
    provider.identity.update(email="sai@acme.com", groups=["qm-testers"])
    user = sign_in(sso, provider)[1]
    assert "admin" in user["roles"] and "tester" in user["roles"]
    assert any(t[0] == "Kept the last administrator" for t in ctx["told"])


def test_a_company_can_ask_for_a_group_before_anyone_is_let_in(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path, provision="auto", role_map={"qm-users": "tester"}, require_group=True)
    provider.identity.update(email="out@acme.com", groups=["staff"])
    with pytest.raises(SsoError, match="not in a group that gives access"):
        sign_in(sso, provider)
    assert not [u for u in auth.users() if u["email"] == "out@acme.com"]
    provider.identity["groups"] = ["qm-users"]
    assert sign_in(sso, provider)[1]["roles"] == ["tester"]


def test_the_groups_claim_may_be_called_anything_and_a_single_group_may_be_text(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(
        tmp_path, provision="auto", role_map={"qm-users": "tester"}, groups_claim="roles"
    )
    provider.identity.update(email="r@acme.com", roles="qm-users", groups=["ignored"])
    assert sign_in(sso, provider)[1]["roles"] == ["tester"]


def test_an_address_in_the_user_name_is_used_when_the_provider_sends_no_email(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path, provision="auto")
    provider.identity.pop("email")
    provider.claims["preferred_username"] = "pat@acme.com"
    assert sign_in(sso, provider)[1]["email"] == "pat@acme.com"
    provider.claims = {"email": None, "preferred_username": "not-an-address"}
    with pytest.raises(SsoError, match="did not say the person's e-mail address"):
        sign_in(sso, provider)


# ------------------------------------------------------------------ a bad answer


@pytest.mark.parametrize(
    "change, why",
    [
        ({"claims": {"iss": "https://evil.example.com"}}, "somebody else than the provider"),
        ({"claims": {"aud": "another-app"}}, "not meant for Quartermaster"),
        ({"claims": {"aud": [idp.CLIENT_ID, "other"]}}, "not meant for Quartermaster"),  # two audiences, no azp
        ({"claims": {"exp": 1.0}}, "has expired"),
        ({"claims": {"nonce": "somebody-elses"}}, "did not match this sign-in"),
        ({"claims": {"email_verified": False}}, "not verified"),
        ({"corrupt": True}, "signature that does not check out"),
        ({"header": {"alg": "none"}}, "was not signed"),
        ({"header": {"kid": "unknown-key"}}, "signing key was not found"),
    ],
)
def test_an_answer_that_does_not_check_out_is_refused(tmp_path: Path, change: dict[str, Any], why: str) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path)
    add_jane(auth, ctx["admin"])
    for k, v in change.items():
        setattr(provider, k, v)
    with pytest.raises(SsoError, match=why):
        sign_in(sso, provider)


def test_two_audiences_are_fine_when_the_token_says_it_was_for_this_app(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path)
    add_jane(auth, ctx["admin"])
    provider.claims = {"aud": [idp.CLIENT_ID, "other"], "azp": idp.CLIENT_ID}
    assert sign_in(sso, provider)[1]["username"] == "jane"


def test_a_token_signed_another_way_is_trusted_only_for_what_can_be_checked(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path)
    add_jane(auth, ctx["admin"])
    provider.header = {"alg": "ES256"}  # straight from the token address over https; the rest is still checked
    assert sign_in(sso, provider)[1]["username"] == "jane"
    provider.claims = {"aud": "another-app"}
    with pytest.raises(SsoError, match="not meant for Quartermaster"):
        sign_in(sso, provider)


def test_a_key_the_provider_has_changed_is_found_again_after_a_while(tmp_path: Path) -> None:
    auth, sso, provider, clock, ctx = setup(tmp_path)
    add_jane(auth, ctx["admin"])
    provider.keys = [idp.public_key("old-key")]
    provider.header = {"kid": "key-1"}
    with pytest.raises(SsoError, match="signing key was not found"):
        sign_in(sso, provider)
    provider.keys = [idp.public_key("key-1")]
    clock.t += 700
    assert sign_in(sso, provider)[1]["username"] == "jane"


def test_a_garbled_or_missing_token_is_refused_politely(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path)
    for reply in ({}, {"id_token": "a.b"}, {"id_token": "not.json.here"}):
        q, binder = begin(sso, provider)
        provider.post = lambda url, form, r=reply: r  # type: ignore[method-assign]
        sso._post = provider.post
        with pytest.raises(SsoError, match="could not be read"):
            sso.callback({"code": "c", "state": q["state"]}, binder)


# ------------------------------------------------------------------ the way back


def test_a_sign_in_is_used_once_in_the_same_browser_within_ten_minutes(tmp_path: Path) -> None:
    auth, sso, provider, clock, ctx = setup(tmp_path)
    add_jane(auth, ctx["admin"])
    q, binder = begin(sso, provider)
    sso.callback({"code": "c0de", "state": q["state"]}, binder)
    with pytest.raises(SsoError, match="expired or was already used"):
        sso.callback({"code": "c0de", "state": q["state"]}, binder)
    q, binder = begin(sso, provider)
    with pytest.raises(SsoError, match="different browser"):
        sso.callback({"code": "c0de", "state": q["state"]}, "somebody-elses-cookie")
    q, binder = begin(sso, provider)
    clock.t += 601
    with pytest.raises(SsoError, match="expired or was already used"):
        sso.callback({"code": "c0de", "state": q["state"]}, binder)
    with pytest.raises(SsoError, match="expired or was already used"):
        sso.callback({"code": "c0de", "state": "made-up"}, binder)
    q, binder = begin(sso, provider)
    with pytest.raises(SsoError, match="cancelled or refused"):
        sso.callback({"error": "access_denied", "state": q["state"]}, binder)


def test_a_flood_of_started_sign_ins_does_not_grow_without_end(tmp_path: Path) -> None:
    _, sso, provider, _, _ = setup(tmp_path)
    for _ in range(120):
        sso.start()
    assert len(sso._pending) <= 50


def test_the_provider_refusing_the_code_is_told_plainly(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path)

    def refuse(url: str, form: dict[str, str]) -> dict[str, Any]:
        raise SsoError("The provider refused the sign-in (invalid_client). Check the client ID and secret in Settings.")

    sso._post = refuse
    q, binder = begin(sso, provider)
    with pytest.raises(SsoError, match="invalid_client"):
        sso.callback({"code": "c", "state": q["state"]}, binder)


# ------------------------------------------------------------------ requiring it


def test_when_it_is_required_only_administrators_may_still_use_a_password(tmp_path: Path) -> None:
    auth, sso, provider, _, ctx = setup(tmp_path)
    jo = auth.create(ctx["admin"], {"username": "jo", "full_name": "Jo Tester", "roles": ["tester"]})
    auth.change_password(auth.user_for(auth.login("jo", jo["password"])[0]), jo["password"], "jo-own-long-password")  # type: ignore[arg-type]
    assert auth.login("jo", "jo-own-long-password")[1]["username"] == "jo"
    auth.sso_update(ctx["admin"], {"enforce": True})
    with pytest.raises(AuthError, match="requires single sign-on"):
        auth.login("jo", "jo-own-long-password")
    assert any(t[0] == "Password sign-in refused" for t in ctx["told"])
    assert auth.login("sai", GOOD)[1]["username"] == "sai"  # the way back in when the provider is down
    with pytest.raises(AuthError, match="wrong user name or password"):
        auth.login("jo", "a-wrong-password-123")  # a wrong password is still just wrong
    auth.sso_update(ctx["admin"], {"enabled": False})  # turned off, nobody is locked out by a rule about it
    assert auth.login("jo", "jo-own-long-password")[1]["username"] == "jo"


# ------------------------------------------------------------------ in the running service


def hub_with_sso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Client, idp.Provider]:
    hub = make_hub(tmp_path, monkeypatch)
    hub.start()
    provider = idp.Provider()
    hub.sso = SsoSignIn(hub.auth, get=provider.get, post=provider.post)
    hub.address = "http://127.0.0.1:8765"
    admin = Client(hub)
    admin.go("POST", "/api/auth/enable", {"username": "sai", "full_name": "Sai Ram", "password": GOOD})
    return hub, admin, provider


def configure(admin: Client, **more: Any) -> dict[str, Any]:
    body = {"issuer": idp.ISSUER, "client_id": idp.CLIENT_ID, "client_secret": idp.SECRET, "enabled": True, **more}
    return admin.go("POST", "/api/auth/sso", body)  # type: ignore[no-any-return]


def test_the_page_sets_up_single_sign_on_checks_the_provider_and_never_sees_the_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, admin, provider = hub_with_sso(tmp_path, monkeypatch)
    try:
        assert admin.go("GET", "/api/auth/status")["sso"] is None  # no button until it is set up
        view = admin.go("GET", "/api/auth/sso")
        assert view["enabled"] is False and view["redirect_uri"] == "http://localhost:8765/api/auth/sso/callback"
        done = configure(admin, label="Acme login")
        assert done["enabled"] and done["secret_set"] and done["issuer"] == idp.ISSUER
        assert idp.SECRET not in json.dumps(admin.go("GET", "/api/auth/sso"))
        assert Client(hub).go("GET", "/api/auth/status")["sso"] == "Acme login"  # the sign-in page shows the button
        audit = (tmp_path / ".qm" / "audit.jsonl").read_text(encoding="utf-8")
        assert "Changed the single sign-on settings" in audit and idp.SECRET not in audit
        found = admin.go("POST", "/api/auth/sso/check", {})
        assert found["ok"] and found["keys"] == 1 and found["signature_checked"] is True
        provider.doc = {"issuer": "https://other.example.com"}
        hub.sso._found.clear()
        bad = admin.go("POST", "/api/auth/sso/check", {})
        assert bad["ok"] is False and "somebody else" in bad["message"]
        assert admin.asks("POST", "/api/auth/sso", {"issuer": "http://not-secure.example.com"})[0] == 400
    finally:
        hub.stop()


def test_only_an_administrator_may_read_or_change_the_single_sign_on_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, admin, _ = hub_with_sso(tmp_path, monkeypatch)
    try:
        made = admin.go(
            "POST", "/api/users", {"action": "create", "username": "jo", "full_name": "Jo Tester", "roles": ["tester"]}
        )
        jo = Client(hub)
        jo.go("POST", "/api/auth/login", {"username": "jo", "password": made["password"]})
        jo.go("POST", "/api/auth/password", {"current": made["password"], "new": "jo-own-long-password"})
        assert jo.asks("GET", "/api/auth/sso")[0] == 403
        assert jo.asks("POST", "/api/auth/sso", {"enabled": False})[0] == 403
        assert jo.asks("POST", "/api/auth/sso/check", {})[0] == 403
        assert Client(hub).asks("GET", "/api/auth/sso")[0] == 401
    finally:
        hub.stop()


def test_single_sign_on_through_the_service_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, admin, provider = hub_with_sso(tmp_path, monkeypatch)
    try:
        configure(admin, provision="auto", role_map={"qm-testers": "tester"}, default_role="")
        provider.identity.update(email="jane@acme.com", name="Jane Doe", groups=["qm-testers"])
        browser = Client(hub)  # a person who is not signed in

        go = hub.handle("GET", "/api/auth/sso/start", b"")
        assert go.status == 302 and (go.location or "").startswith(provider.authorize + "?")
        cookie = go.set_cookie if isinstance(go.set_cookie, str) else ""
        assert cookie.startswith(BINDER_COOKIE + "=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie
        assert "Path=/api/auth/sso" in cookie and "Max-Age=600" in cookie
        q = {k: v[0] for k, v in parse_qs(urlsplit(go.location or "").query).items()}
        provider.nonce = q["nonce"]
        binder = cookie.split(";")[0]

        back = hub.handle("GET", f"/api/auth/sso/callback?code=c0de&state={q['state']}", b"", binder)
        assert back.status == 302 and back.location == "/#/"
        cookies = back.set_cookie if isinstance(back.set_cookie, list) else []
        session = next(c for c in cookies if c.startswith("qm_session=") and "Max-Age=0" not in c)
        assert "HttpOnly" in session and "SameSite=Strict" in session
        assert any(c.startswith(BINDER_COOKIE + "=;") for c in cookies)  # the one-time cookie is cleared
        browser.cookie = session.split(";")[0]
        me = browser.go("GET", "/api/auth/status")["user"]
        assert me["full_name"] == "Jane Doe" and me["email"] == "jane@acme.com" and me["roles"] == ["tester"]
        assert isinstance(browser.go("GET", "/api/status"), dict)
        users = {u["username"]: u for u in admin.go("GET", "/api/users")["users"]}
        assert users["jane"]["origin"] == "sso"
        audit = (tmp_path / ".qm" / "audit.jsonl").read_text(encoding="utf-8")
        assert "Created a user from single sign-on" in audit and "Signed in with single sign-on" in audit
    finally:
        hub.stop()


def test_a_refused_sign_in_goes_back_to_the_sign_in_page_with_a_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, admin, provider = hub_with_sso(tmp_path, monkeypatch)
    try:
        configure(admin)  # users are only the listed ones, and Jane is not listed
        provider.identity["email"] = "jane@acme.com"
        go = hub.handle("GET", "/api/auth/sso/start", b"")
        q = {k: v[0] for k, v in parse_qs(urlsplit(go.location or "").query).items()}
        provider.nonce = q["nonce"]
        binder = (go.set_cookie if isinstance(go.set_cookie, str) else "").split(";")[0]
        back = hub.handle("GET", f"/api/auth/sso/callback?code=c0de&state={q['state']}", b"", binder)
        assert back.status == 302 and (back.location or "").startswith("/#/login?error=")
        assert "has%20not%20been%20given%20access" in (back.location or "")
        assert "qm_session=" not in str(back.set_cookie) or "Max-Age=0" in str(back.set_cookie)
        off = hub.handle("GET", "/api/auth/sso/start", b"")  # and a start with nothing set up says so, in the same way
        admin.go("POST", "/api/auth/sso", {"enabled": False})
        off = hub.handle("GET", "/api/auth/sso/start", b"")
        assert off.status == 302 and "not%20turned%20on" in (off.location or "")
    finally:
        hub.stop()
