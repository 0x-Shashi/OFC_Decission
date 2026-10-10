"""Sign-in and roles: off by default, safe passwords and sessions, and what each role may do."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_service_api import add_suite_tests, app, call, finished_suite_run  # noqa: F401  (app is a fixture)
from test_service_queue import fake_command

from quartermaster.service import auth as auth_module
from quartermaster.service.api import ApiError, App
from quartermaster.service.auth import (
    IDLE_S,
    LOCK_S,
    Auth,
    AuthError,
    allowed,
    check_new_password,
    check_password,
    hash_password,
    required_role,
    temporary_password,
)
from quartermaster.service.hub import Hub

GOOD = "correct-horse-battery"


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make(tmp_path: Path) -> tuple[Auth, Clock, list[tuple[str, str, dict[str, Any], str]]]:
    clock = Clock()
    told: list[tuple[str, str, dict[str, Any], str]] = []
    a = Auth(
        tmp_path / "users.db",
        now=clock,
        record=lambda what, subject, details, who: told.append((what, subject, details, who)),
    )
    return a, clock, told


def on(a: Auth) -> tuple[str, dict[str, Any]]:
    return a.enable("sai", "Sai Ram", GOOD)


# ------------------------------------------------------------------ passwords


def test_a_password_is_stored_as_a_salted_hash_that_checks_out() -> None:
    stored = hash_password(GOOD)
    assert GOOD not in stored and stored.startswith(("scrypt$", "pbkdf2$"))
    assert check_password(GOOD, stored) and not check_password(GOOD + "x", stored) and not check_password("", stored)
    assert hash_password(GOOD) != stored  # a new salt each time
    for broken in ("", "nonsense", "scrypt$x$y", "pbkdf2$1$2$3", "md5$a$b"):
        assert not check_password(GOOD, broken)


def test_the_fallback_hash_works_where_scrypt_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(auth_module.hashlib, "scrypt", raising=False)
    stored = hash_password(GOOD)
    assert stored.startswith("pbkdf2$") and check_password(GOOD, stored) and not check_password("no", stored)


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("short", "at least 10"),
        ("a" * 201, "too long"),
        ("sai.ram.user", "may not be the user name"),
        ("Password", "at least 10"),
        ("qwertyuiop", "too easy"),
        ("aaaaaaaaaaaa", "too easy"),
        ("abababababab", "too easy"),
    ],
)
def test_weak_passwords_are_refused(password: str, message: str) -> None:
    with pytest.raises(AuthError, match=message):
        check_new_password(password, "sai.ram.user")


def test_a_good_password_is_accepted_and_temporary_ones_are_readable() -> None:
    check_new_password(GOOD, "sai")
    pw = temporary_password()
    assert len(pw) == 12 and not set(pw) & set("0O1lI")
    assert temporary_password() != pw


# ------------------------------------------------------------------ turning it on, signing in and out


def test_sign_in_is_off_until_the_first_administrator_is_made(tmp_path: Path) -> None:
    a, _, told = make(tmp_path)
    assert not a.enabled and a.users() == []
    token, user = on(a)
    assert a.enabled and user["roles"] == ["admin"] and not user["must_change"]
    assert a.user_for(token)["username"] == "sai"  # type: ignore[index]
    assert "pw" not in a.users()[0]
    with pytest.raises(AuthError, match="already on"):
        on(a)
    assert ("Turned on sign-in", "Users", {"first administrator": "sai"}, "Sai Ram") in told


def test_a_wrong_password_and_an_unknown_name_get_the_same_answer(tmp_path: Path) -> None:
    a, _, told = make(tmp_path)
    on(a)
    messages = set()
    for name, pw in (("sai", "wrong-password-1"), ("nobody", GOOD), ("", ""), ("SAI ", "x")):
        with pytest.raises(AuthError) as e:
            a.login(name, pw)
        messages.add((e.value.status, str(e.value)))
    assert messages == {(401, "wrong user name or password")}
    assert any(t[0] == "Sign-in failed" for t in told)
    assert all(
        GOOD not in json.dumps(t) and "wrong-password" not in json.dumps(t) for t in told
    )  # no password in the log


def test_five_wrong_passwords_lock_the_user_name_for_fifteen_minutes(tmp_path: Path) -> None:
    a, clock, _ = make(tmp_path)
    on(a)
    for _ in range(5):
        with pytest.raises(AuthError, match="wrong user name"):
            a.login("sai", "nope-nope-nope")
    with pytest.raises(AuthError, match="too many wrong attempts") as locked:
        a.login("sai", GOOD)  # even the right one, while locked
    assert locked.value.status == 429
    clock.advance(LOCK_S + 1)
    token, _ = a.login("sai", GOOD)
    assert a.user_for(token) is not None
    with pytest.raises(AuthError, match="wrong user name"):  # the count started again
        a.login("sai", "nope-nope-nope")


def test_a_good_sign_in_clears_the_failures(tmp_path: Path) -> None:
    a, _, _ = make(tmp_path)
    on(a)
    for _ in range(4):
        with pytest.raises(AuthError):
            a.login("sai", "nope-nope-nope")
    a.login("sai", GOOD)
    for _ in range(4):
        with pytest.raises(AuthError, match="wrong user name"):
            a.login("sai", "nope-nope-nope")


def test_sessions_end_when_idle_when_too_old_and_on_sign_out(tmp_path: Path) -> None:
    a, clock, _ = make(tmp_path)
    token, _ = on(a)
    clock.advance(IDLE_S - 60)
    assert a.user_for(token)  # used: kept alive
    clock.advance(IDLE_S - 60)
    assert a.user_for(token)
    clock.advance(IDLE_S + 1)
    assert a.user_for(token) is None  # idle too long
    token, _ = a.login("sai", GOOD)
    for _ in range(5):
        clock.advance(IDLE_S - 60)
        a.user_for(token)
    assert a.user_for(token) is None  # older than a day, however busy it was
    token, _ = a.login("sai", GOOD)
    a.logout(token)
    assert a.user_for(token) is None and a.user_for("") is None and a.user_for("made-up") is None


# ------------------------------------------------------------------ users


def test_a_new_user_gets_a_temporary_password_and_must_choose_their_own(tmp_path: Path) -> None:
    a, _, told = make(tmp_path)
    _, admin = on(a)
    made = a.create(admin, {"username": "Jane.Doe", "full_name": "Jane Doe", "title": "Tester", "roles": ["tester"]})
    assert made["user"]["username"] == "jane.doe" and made["user"]["must_change"] and "pw" not in made["user"]
    token, user = a.login("jane.doe", made["password"])
    assert user["must_change"]
    with pytest.raises(AuthError, match="not your current password"):
        a.change_password(user, "wrong-current", GOOD + "2")
    with pytest.raises(AuthError, match="too easy|at least"):
        a.change_password(user, made["password"], "short")
    with pytest.raises(AuthError, match="not used just now"):
        a.change_password(user, made["password"], made["password"] + "")
    a.change_password(user, made["password"], "a-new-long-password")
    assert not a.user_for(token)["must_change"]  # type: ignore[index]
    with pytest.raises(AuthError):
        a.login("jane.doe", made["password"])  # the temporary one is gone
    a.login("jane.doe", "a-new-long-password")
    assert all(made["password"] not in json.dumps(t) and "a-new-long" not in json.dumps(t) for t in told)


@pytest.mark.parametrize(
    "data",
    [
        {"username": "x", "full_name": "Jane Doe"},
        {"username": "has space", "full_name": "Jane Doe"},
        {"username": "../etc", "full_name": "Jane Doe"},
        {"username": "jane", "full_name": ""},
        {"username": "jane", "full_name": "J"},
        {"username": "jane", "full_name": "Jane Doe", "roles": ["wizard"]},
        {"username": "sai", "full_name": "Another Sai"},
    ],
)
def test_bad_new_users_are_refused(tmp_path: Path, data: dict[str, Any]) -> None:
    a, _, _ = make(tmp_path)
    _, admin = on(a)
    with pytest.raises(AuthError):
        a.create(admin, data)
    assert [u["username"] for u in a.users()] == ["sai"]


def test_roles_and_status_can_change_and_a_disabled_user_is_signed_out(tmp_path: Path) -> None:
    a, _, _ = make(tmp_path)
    _, admin = on(a)
    made = a.create(admin, {"username": "jane", "full_name": "Jane Doe", "roles": ["tester"]})
    token, _ = a.login("jane", made["password"])
    uid = made["user"]["id"]
    assert a.update(admin, uid, {"roles": ["approver", "tester"]})["roles"] == ["tester", "approver"]
    assert a.user_for(token)
    a.update(admin, uid, {"active": False})
    assert a.user_for(token) is None
    with pytest.raises(AuthError, match="wrong user name"):
        a.login("jane", made["password"])
    a.update(admin, uid, {"active": True, "full_name": "Jane Q Doe", "title": "Lead"})
    assert a.users()[0]["full_name"] == "Jane Q Doe" or a.users()[1]["full_name"] == "Jane Q Doe"


def test_a_reset_gives_a_new_temporary_password_and_signs_the_user_out(tmp_path: Path) -> None:
    a, _, _ = make(tmp_path)
    _, admin = on(a)
    made = a.create(admin, {"username": "jane", "full_name": "Jane Doe", "roles": ["tester"]})
    token, _ = a.login("jane", made["password"])
    again = a.reset_password(admin, made["user"]["id"])
    assert again["password"] != made["password"] and a.user_for(token) is None
    with pytest.raises(AuthError):
        a.login("jane", made["password"])
    assert a.login("jane", again["password"])[1]["must_change"]
    with pytest.raises(AuthError, match="no such user"):
        a.reset_password(admin, "nope")


def test_the_last_administrator_cannot_be_lost(tmp_path: Path) -> None:
    a, _, _ = make(tmp_path)
    _, admin = on(a)
    for change in ({"roles": ["tester"]}, {"active": False}):
        with pytest.raises(AuthError, match="last active administrator"):
            a.update(admin, admin["id"], change)
    with pytest.raises(AuthError, match="cannot remove yourself"):
        a.delete(admin, admin["id"])
    second = a.create(admin, {"username": "boss", "full_name": "The Boss", "roles": ["admin"]})["user"]
    a.update(admin, admin["id"], {"roles": ["tester"]})  # fine now: there is another
    _, boss = a.login("boss", a.reset_password(admin, second["id"])["password"])
    with pytest.raises(AuthError, match="last active administrator"):  # boss is the only one now
        a.update(boss, second["id"], {"active": False})
    a.delete(boss, admin["id"])
    assert [u["username"] for u in a.users()] == ["boss"]


def test_signing_in_can_be_turned_off_with_the_password_and_on_again(tmp_path: Path) -> None:
    a, _, _ = make(tmp_path)
    token, admin = on(a)
    with pytest.raises(AuthError, match="not your current password"):
        a.disable(admin, "wrong")
    assert a.enabled
    a.disable(admin, GOOD)
    assert not a.enabled and a.user_for(token) is None
    new_token, again = a.enable("sai", "Sai Ram", "another-long-password")  # the same account, a new password
    assert again["id"] == admin["id"] and a.user_for(new_token)
    with pytest.raises(AuthError):
        a.login("sai", GOOD)
    a.disable_from_terminal()
    assert not a.enabled


# ------------------------------------------------------------------ what each role may do


@pytest.mark.parametrize(
    ("method", "route", "need"),
    [
        ("GET", ["status"], "any"),
        ("GET", ["runs", "x"], "any"),
        ("GET", ["audit"], "any"),
        ("GET", ["certification"], "any"),
        ("GET", ["environments"], "any"),
        ("GET", ["settings"], "any"),
        ("GET", ["users"], "admin"),
        ("GET", ["backup"], "admin"),
        ("GET", ["notifications"], "admin"),
        ("POST", ["runs"], "tester"),
        ("POST", ["runs", "x", "cancel"], "tester"),
        ("POST", ["recording"], "tester"),
        ("POST", ["manual", "run"], "tester"),
        ("POST", ["test", "accept-update"], "tester"),
        ("POST", ["attention", "dismiss"], "tester"),
        ("POST", ["schedules"], "tester"),
        ("POST", ["releases", "import"], "tester"),
        ("POST", ["signin"], "tester"),
        ("POST", ["check-pod"], "tester"),
        ("POST", ["open"], "tester"),
        ("POST", ["environments", "activate"], "tester"),
        ("POST", ["environments", "client"], "admin"),
        ("POST", ["environments", "user"], "admin"),
        ("POST", ["settings"], "admin"),
        ("POST", ["ai", "key"], "admin"),
        ("POST", ["notifications"], "admin"),
        ("POST", ["backup", "restore"], "admin"),
        ("POST", ["users"], "admin"),
        ("POST", ["approvals"], "approver"),
        ("POST", ["something-new"], "admin"),  # not listed: closed until someone decides
    ],
)
def test_each_request_needs_the_right_role(method: str, route: list[str], need: str) -> None:
    assert required_role(method, route) == need


def user(*roles: str, must_change: bool = False) -> dict[str, Any]:
    return {"id": "u", "username": "u", "full_name": "U", "title": "", "roles": list(roles), "must_change": must_change}


def test_roles_open_exactly_what_they_should() -> None:
    viewer, tester, approver, admin = user(), user("tester"), user("approver"), user("admin")
    assert allowed(viewer, "any") and not allowed(viewer, "tester") and not allowed(viewer, "approver")
    assert allowed(tester, "tester") and not allowed(tester, "approver") and not allowed(tester, "admin")
    assert allowed(approver, "approver") and not allowed(approver, "tester") and not allowed(approver, "admin")
    assert all(allowed(admin, n) for n in ("any", "tester", "approver", "admin"))
    assert allowed(user("tester", "approver"), "approver") and allowed(user("tester", "approver"), "tester")


def test_authorize_says_what_is_missing_and_blocks_until_the_password_is_changed(tmp_path: Path) -> None:
    a, _, _ = make(tmp_path)
    with pytest.raises(AuthError, match="needs a tester") as e:
        a.authorize(user(), "POST", ["runs"])
    assert e.value.status == 403
    a.authorize(user("tester"), "POST", ["runs"])
    a.authorize(user(), "GET", ["tests"])
    with pytest.raises(AuthError, match="needs an administrator"):
        a.authorize(user("tester"), "POST", ["settings"])
    with pytest.raises(AuthError, match="choose a new password first"):
        a.authorize(user("admin", must_change=True), "GET", ["status"])


# ------------------------------------------------------------------ in the running service


def make_hub(root: Path, monkeypatch: pytest.MonkeyPatch) -> Hub:
    for var in ("QM_FUSION_URL", "QM_FUSION_USER", "QM_FUSION_PASSWORD", "QM_FUSION_SESSION"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("QM_VAULT_KEY_FILE", str(root / "vault.key"))
    monkeypatch.chdir(root)
    (root / "my_tests").mkdir(exist_ok=True)
    (root / "my_tests" / "t.yaml").write_text("id: t.one\ntitle: One\nmodule: HCM\nsteps: [{}]\n")
    return Hub(
        tests_root=root / "my_tests", evidence_root=root / "evidence", data_dir=root / ".qm", run_command=fake_command
    )


class Client:
    """A browser: it keeps the session cookie the service sets."""

    def __init__(self, hub: Hub) -> None:
        self.hub, self.cookie = hub, ""

    def go(self, method: str, path: str, body: dict[str, Any] | None = None, cookie: str | None = None) -> Any:
        raw = json.dumps(body).encode() if body is not None else b""
        reply = self.hub.handle(method, path, raw, self.cookie if cookie is None else cookie)
        if reply.set_cookie:
            first = reply.set_cookie.split(";")[0]
            self.cookie = "" if first.endswith("=") else first
        return json.loads(reply.body) if reply.content_type == "application/json" else reply

    def asks(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, str]:
        try:
            self.go(method, path, body)
        except ApiError as e:
            return int(e.status), str(e)
        return 200, ""


def started(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Hub, Client]:
    hub = make_hub(tmp_path, monkeypatch)
    hub.start()
    return hub, Client(hub)


def test_nothing_changes_until_sign_in_is_turned_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, c = started(tmp_path, monkeypatch)
    try:
        assert c.go("GET", "/api/auth/status") == {
            "enabled": False,
            "user": None,
            "roles": ["admin", "tester", "approver"],
            "google": False,
            "sso": None,
        }
        assert isinstance(c.go("GET", "/api/status"), dict) and c.go("GET", "/api/users")["enabled"] is False
        assert c.asks("POST", "/api/auth/login", {"username": "a", "password": "b"})[1] == "sign-in is not turned on"
        assert c.asks("POST", "/api/users", {"action": "create"})[0] == 400
    finally:
        hub.stop()


def test_turning_it_on_signs_the_administrator_in_and_closes_the_service_to_everyone_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, c = started(tmp_path, monkeypatch)
    try:
        reply = hub.handle(
            "POST",
            "/api/auth/enable",
            json.dumps({"username": "sai", "full_name": "Sai Ram", "password": GOOD}).encode(),
        )
        cookie = reply.set_cookie or ""
        assert (
            cookie.startswith("qm_session=")
            and "HttpOnly" in cookie
            and "SameSite=Strict" in cookie
            and "Path=/" in cookie
        )
        c.cookie = cookie.split(";")[0]
        assert c.go("GET", "/api/auth/status")["user"]["username"] == "sai"
        assert isinstance(c.go("GET", "/api/status"), dict)

        stranger = Client(hub)
        assert stranger.asks("GET", "/api/status") == (401, "sign in first")
        assert stranger.asks("GET", "/api/tests")[0] == 401 and stranger.asks("POST", "/api/runs", {})[0] == 401
        assert stranger.asks("GET", "/files/x.docx")[0] == 401
        assert stranger.asks("GET", "/api/backup")[0] == 401
        assert stranger.go("GET", "/api/auth/status")["enabled"] is True  # that one is open: the page needs it
        assert hub.handle("GET", "/", b"").content_type.startswith("text/html")  # so is the page that shows the sign-in
        assert (
            Client(hub).asks("POST", "/api/auth/enable", {"username": "x", "full_name": "X Y", "password": GOOD})[0]
            == 400
        )
    finally:
        hub.stop()


def test_a_cookie_that_is_garbage_or_made_up_is_just_not_signed_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, c = started(tmp_path, monkeypatch)
    try:
        c.go("POST", "/api/auth/enable", {"username": "sai", "full_name": "Sai Ram", "password": GOOD})
        for bad in ("", "garbage", "qm_session=", "qm_session=made-up", ";;;===", "qm_session=\x00\x01"):
            with pytest.raises(ApiError) as e:
                hub.handle("GET", "/api/status", b"", bad)
            assert e.value.status == 401
    finally:
        hub.stop()


def test_signing_out_ends_the_session_and_clears_the_cookie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, c = started(tmp_path, monkeypatch)
    try:
        c.go("POST", "/api/auth/enable", {"username": "sai", "full_name": "Sai Ram", "password": GOOD})
        old = c.cookie
        reply = hub.handle("POST", "/api/auth/logout", b"{}", old)
        assert reply.set_cookie and "Max-Age=0" in reply.set_cookie
        assert Client(hub).asks("GET", "/api/status") == (401, "sign in first")
        with pytest.raises(ApiError):
            hub.handle("GET", "/api/status", b"", old)  # the old cookie is dead
        c.cookie = ""
        c.go("POST", "/api/auth/login", {"username": "SAI", "password": GOOD})
        assert isinstance(c.go("GET", "/api/status"), dict)
    finally:
        hub.stop()


def test_each_role_gets_exactly_its_own_part_of_the_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, admin = started(tmp_path, monkeypatch)
    try:
        admin.go("POST", "/api/auth/enable", {"username": "sai", "full_name": "Sai Ram", "password": GOOD})
        people = {}
        for name, roles in (
            ("viewer", []),
            ("tester", ["tester"]),
            ("approver", ["approver"]),
            ("both", ["tester", "approver"]),
        ):
            made = admin.go(
                "POST",
                "/api/users",
                {"action": "create", "username": name, "full_name": name.title() + " Person", "roles": roles},
            )
            c = Client(hub)
            c.go("POST", "/api/auth/login", {"username": name, "password": made["password"]})
            assert c.asks("GET", "/api/status") == (403, "choose a new password first")  # not yet
            c.go("POST", "/api/auth/password", {"current": made["password"], "new": "my-own-long-password"})
            assert c.asks("GET", "/api/status") == (200, "")
            people[name] = c

        def code(who: str, method: str, path: str, body: dict[str, Any] | None = None) -> int:
            return people[who].asks(method, path, body or {})[0]

        # everybody signed in may look; only an administrator reads users, backups and notification settings
        for who in people:
            assert code(who, "GET", "/api/tests") == 200 and code(who, "GET", "/api/audit") == 200
            assert code(who, "GET", "/api/users") == 403 and code(who, "GET", "/api/backup") == 403
            assert code(who, "GET", "/api/notifications") == 403
            assert code(who, "POST", "/api/settings", {"release": "26C"}) == 403
            assert code(who, "POST", "/api/environments/client", {"name": "X"}) == 403
            assert code(who, "POST", "/api/users", {"action": "create"}) == 403
        # running and recording need a tester
        assert code("viewer", "POST", "/api/runs", {"target": "t.yaml"}) == 403
        assert code("approver", "POST", "/api/runs", {"target": "t.yaml"}) == 403
        assert code("tester", "POST", "/api/runs", {"target": "t.yaml"}) == 200
        assert code("both", "POST", "/api/attention/dismiss", {"keys": []}) == 400  # allowed: refused on its merits
        assert code("viewer", "POST", "/api/attention/dismiss", {"keys": []}) == 403
        # approving needs an approver; a tester may not approve
        assert code("tester", "POST", "/api/approvals", {"action": "approve", "release": "26C"}) == 403
        assert code("viewer", "POST", "/api/approvals", {"action": "approve", "release": "26C"}) == 403
        assert (
            code("approver", "POST", "/api/approvals", {"action": "approve", "release": "26C"}) == 400
        )  # allowed: refused on its merits
        # the administrator may do all of it
        assert (
            admin.asks("GET", "/api/users")[0] == 200
            and admin.asks("POST", "/api/settings", {"release": "26C"})[0] == 200
        )
        assert admin.asks("GET", "/api/backup")[0] == 200 and admin.asks("GET", "/api/notifications")[0] == 200
    finally:
        hub.stop()


def test_only_an_administrator_can_manage_users_and_never_the_last_one_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, admin = started(tmp_path, monkeypatch)
    try:
        admin.go("POST", "/api/auth/enable", {"username": "sai", "full_name": "Sai Ram", "password": GOOD})
        users = admin.go("GET", "/api/users")["users"]
        assert [u["username"] for u in users] == ["sai"] and all("pw" not in u for u in users)
        me = users[0]["id"]
        assert admin.asks("POST", "/api/users", {"action": "update", "id": me, "roles": ["tester"]})[1].startswith(
            "this is the last"
        )
        assert admin.asks("POST", "/api/users", {"action": "delete", "id": me})[1] == "you cannot remove yourself"
        assert admin.asks("POST", "/api/users", {"action": "bless"})[0] == 400
        made = admin.go(
            "POST", "/api/users", {"action": "create", "username": "jo", "full_name": "Jo Bloggs", "roles": ["tester"]}
        )
        assert admin.go("POST", "/api/users", {"action": "reset", "id": made["user"]["id"]})["password"]
        assert admin.go("POST", "/api/users", {"action": "update", "id": made["user"]["id"], "roles": ["approver"]})[
            "user"
        ]["roles"] == ["approver"]
        assert admin.go("POST", "/api/users", {"action": "delete", "id": made["user"]["id"]}) == {"ok": True}
        assert admin.asks("POST", "/api/auth/password", {"current": "wrong", "new": "x"})[0] == 403
    finally:
        hub.stop()


def test_turning_sign_in_off_needs_the_administrators_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, admin = started(tmp_path, monkeypatch)
    try:
        admin.go("POST", "/api/auth/enable", {"username": "sai", "full_name": "Sai Ram", "password": GOOD})
        assert admin.asks("POST", "/api/auth/disable", {"password": "wrong"})[0] == 403
        assert admin.go("GET", "/api/auth/status")["enabled"] is True
        admin.go("POST", "/api/auth/disable", {"password": GOOD})
        assert admin.go("GET", "/api/auth/status")["enabled"] is False
        assert Client(hub).asks("GET", "/api/status") == (200, "")  # open again, as it was
    finally:
        hub.stop()


def test_what_is_done_is_written_down_under_the_name_of_who_did_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, admin = started(tmp_path, monkeypatch)
    try:
        admin.go("POST", "/api/auth/enable", {"username": "sai", "full_name": "Sai Ram", "password": GOOD})
        admin.go(
            "POST", "/api/users", {"action": "create", "username": "jo", "full_name": "Jo Bloggs", "roles": ["tester"]}
        )
        audit = (tmp_path / ".qm" / "audit.jsonl").read_text(encoding="utf-8")
        for what in ("Turned on sign-in", "Added a user"):
            assert what in audit
        assert GOOD not in audit and "my-own" not in audit
        lines = [json.loads(ln) for ln in audit.splitlines()]
        assert [e["who"] for e in lines if e["action"] == "Added a user"] == ["Sai Ram"]
    finally:
        hub.stop()


# ---- an App behind sign-in: the signed-in person is who ran it and who approved it


def test_runs_and_approvals_carry_the_signed_in_persons_name(app: App) -> None:  # noqa: F811
    add_suite_tests(app)
    finished_suite_run(app)
    jo = {
        "id": "u1",
        "username": "jo",
        "full_name": "Jo Bloggs",
        "title": "QA lead",
        "roles": ["approver", "tester"],
        "must_change": False,
    }

    def post(path: str, body: dict[str, Any]) -> Any:
        return json.loads(app.handle("POST", path, json.dumps(body).encode(), user=jo).body)

    run = post("/api/runs", {"target": "hcm/pass.yaml"})
    assert run["options"]["tester"] == "Jo Bloggs"  # "Run by" is the signed-in person
    own = post("/api/runs", {"target": "hcm/pass.yaml", "options": {"tester": "Someone Else"}})
    assert own["options"]["tester"] == "Someone Else"  # a name chosen on purpose is kept

    done = post(
        "/api/approvals",
        {
            "action": "approve",
            "release": "26D",
            "name": "Somebody Else",
            "acknowledged": True,
            "comment": "A known data problem here",
        },
    )
    assert done["last"]["by"] == "Jo Bloggs" and done["last"]["title"] == "QA lead"  # the form's name is not trusted
    assert done["last"]["signed_in_as"] == "jo"
    audit = (app.audit.path).read_text(encoding="utf-8")
    who = [json.loads(ln)["who"] for ln in audit.splitlines()]
    assert "Somebody Else" not in who and who.count("Jo Bloggs") >= 2  # the run and the approval

    plain = json.loads(app.handle("POST", "/api/runs", json.dumps({"target": "hcm/pass.yaml"}).encode()).body)
    assert plain["options"]["tester"] == ""  # sign-in off: as before
    assert call(app, "GET", "/api/approvals?release=26D")["state"]["last"]["signed_in_as"] == "jo"


# ------------------------------------------------------------------ getting back in from a terminal


def test_the_terminal_can_list_add_reset_and_turn_sign_in_off(tmp_path: Path, capsys: Any) -> None:
    from quartermaster.cli import main

    data = ["--data", str(tmp_path / ".qm")]
    assert main(["users", "list", *data]) == 0
    assert "no users yet" in capsys.readouterr().out

    a = Auth(tmp_path / ".qm" / "users.db")
    a.enable("sai", "Sai Ram", GOOD)
    assert main(["users", "list", *data]) == 0
    out = capsys.readouterr().out
    assert "Sign-in is ON" in out and "sai" in out and "admin" in out and GOOD not in out

    assert main(["users", "add", "jo", "--name", "Jo Bloggs", "--roles", "tester,approver", *data]) == 0
    first = [ln for ln in capsys.readouterr().out.splitlines() if "Temporary password" in ln][0].split(": ")[1]
    assert a.login("jo", first)[1]["must_change"]  # works, and has to be changed

    assert main(["users", "passwd", "jo", *data]) == 0
    second = [ln for ln in capsys.readouterr().out.splitlines() if "temporary password" in ln][0].split(": ")[1]
    assert second != first
    with pytest.raises(AuthError):
        a.login("jo", first)
    a.login("jo", second)

    assert main(["users", "passwd", "nobody", *data]) == 2
    assert main(["users", "passwd", *data]) == 2
    assert main(["users", "add", "x", "--roles", "wizard", *data]) == 2
    assert main(["users", "add", "jo", "--name", "Jo Again", *data]) == 2  # already there
    capsys.readouterr()

    assert main(["users", "disable-signin", *data]) == 0
    assert not a.enabled
    assert "Sign-in is off" in capsys.readouterr().out
