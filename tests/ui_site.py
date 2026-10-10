"""A running Quartermaster with sample data, for the tests that drive its web pages in a real browser.

Kept apart from the tests so it needs no browser: `build_site` makes a Hub with a few tests, a finished run (one
failure, one screen change, one cleanup that did not finish), a shared-steps group and a stand-in Google."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import oidc_stub
from test_google import jwt
from test_service_api import add_suite_tests, finished_suite_run
from test_service_queue import fake_command

from quartermaster.service.api import App, make_server, port_of
from quartermaster.service.google import GoogleSignIn
from quartermaster.service.hub import Hub
from quartermaster.service.sso import SsoSignIn

CLIENT = "1234567890-abcdefghijklmnop.apps.googleusercontent.com"
SECRET = "GOCSPX-stand-in-client-secret"


class StandInGoogle:
    """Google's sign-in page and token address: it sends the browser straight back, as whoever `email` is."""

    def __init__(self) -> None:
        self.email = "jane@gmail.com"
        self._codes: dict[str, dict[str, str]] = {}
        stand = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                code = f"code{len(stand._codes)}"
                stand._codes[code] = {"nonce": q.get("nonce", ""), "email": stand.email}
                back = q["redirect_uri"] + "?" + urlencode({"code": code, "state": q["state"]})
                self.send_response(302)
                self.send_header("Location", back)
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.auth_url = f"http://127.0.0.1:{self.server.server_address[1]}/auth"

    def token(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        """What Google's token address answers to the code the browser brought back."""
        asked = self._codes[form["code"]]
        claims = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT,
            "exp": 4_000_000_000,
            "nonce": asked["nonce"],
            "email": asked["email"],
            "email_verified": True,
        }
        return {"id_token": jwt(claims)}

    def stop(self) -> None:
        self.server.shutdown()


@dataclass
class Site:
    url: str  # http://localhost:<port>
    hub: Hub
    google: StandInGoogle
    idp: oidc_stub.Provider  # a company's single sign-on provider
    tmp: Path
    saved_env: dict[str, str | None]

    @property
    def app(self) -> App:
        return self.hub.app


def build_site(tmp: Path) -> Site:
    """A Hub (nothing running yet) with sample data. Call `serve` to start it."""
    # a pod set up the way the pages expect, with made-up logins; put back when the site stops
    wanted = {
        "QM_FUSION_URL": "https://abcd-dev2.fa.us6.oraclecloud.com",
        "QM_FUSION_USER": "tester",
        "QM_FUSION_PASSWORD": "not-a-real-password",
        "QM_VAULT_KEY_FILE": str(tmp / "vault.key"),
    }
    saved = {k: os.environ.get(k) for k in [*wanted, "QM_FUSION_SESSION"]}
    os.environ.pop("QM_FUSION_SESSION", None)
    os.environ.update(wanted)
    tests = tmp / "tests"
    (tests / "hcm").mkdir(parents=True)
    google = StandInGoogle()
    hub = Hub(tests_root=tests, evidence_root=tmp / "evidence", data_dir=tmp / ".qm", run_command=fake_command)
    hub.google = GoogleSignIn(
        hub.auth, auth_url=google.auth_url, token_url="http://unused.test/token", post=google.token
    )
    idp = oidc_stub.Provider(http=True)
    hub.sso = SsoSignIn(hub.auth, get=idp.get, post=idp.post)
    app = hub.app
    add_suite_tests(app)
    app.audit.add("Opened the sample site", "Sample", {"for": "the browser tests"}, who="Test Person")
    (tests / "_library").mkdir()
    (tests / "_library" / "open-locations.yaml").write_text(
        "library: open-locations\ntitle: Open the Locations page\nparams:\n  page_name: Locations\nsteps:\n"
        "  - action: navigate\n    intent: Open ${page_name}\n    value: Workforce Structures > ${page_name}\n"
    )
    (tests / "_suites").mkdir()
    (tests / "_suites" / "hcm-tests.yaml").write_text(
        "suite: hcm-tests\ntitle: Everything in HCM\ndescription: The HCM folder.\n"
        "include:\n  - folders: [hcm]\nexclude:\n  - tags: [skip-me]\n"
    )
    (tests / "_data").mkdir()
    (tests / "_data" / "pod-names.yaml").write_text(
        "dataset: pod-names\ntitle: Names on the pods\nvalues:\n  business_unit: US1 Business Unit\n"
        "pods:\n  STAGE: {business_unit: US1 Stage BU, special: Stage only}\n"
    )
    (tests / "hcm" / "uses_data.yaml").write_text(
        "id: hcm.uses-data\ntitle: A test with test data\nmodule: HCM\nproduct: HR\n"
        "data_sets: [pod-names]\ngenerate:\n  ref: {unique: 6, prefix: 'REF-'}\n"
        "steps:\n  - action: api_call\n    intent: Look\n    value: GET /x?bu=${business_unit}&r=${ref}\n"
    )
    (tests / "hcm" / "with_setup.yaml").write_text(
        "id: hcm.with-setup\ntitle: A test with setup\nmodule: HCM\nproduct: HR\n"
        "setup:\n  - action: api_call\n    intent: The pod has a location\n"
        "    value: GET /hcmRestApi/resources/11.13.18.05/locationsV2?limit=1\n"
        "steps:\n  - action: api_call\n    intent: Look\n    value: GET /hcmRestApi/resources/11.13.18.05/locationsV2\n"
    )
    (tests / "hcm" / "shared_user.yaml").write_text(
        "id: hcm.shared-user\ntitle: A test with shared steps\nmodule: HCM\nproduct: HR\n"
        "steps:\n  - use: open-locations\n"
    )
    run = finished_suite_run(app)
    path = Path(app.queue.store.get(run["id"])["suite_dir"]) / "suite.json"
    suite = json.loads(path.read_text(encoding="utf-8"))
    passed = next(r for r in suite["runs"] if r["status"] == "passed")
    passed["cleanup_status"] = "failed"
    passed["cleanup_failed"] = [{"number": 1, "intent": "Remove the location", "error": "The API answered HTTP 403."}]
    path.write_text(json.dumps(suite), encoding="utf-8")
    # the passed test did a setup first: its run record says so (the run page shows it)
    record_file = app.evidence_root / passed["run_dir"] / "run.json"
    record = json.loads(record_file.read_text(encoding="utf-8")) if record_file.is_file() else {}
    record["setup_status"] = "done"
    record["setup"] = [
        {"index": 0, "intent": "The accounting period is open", "status": "passed", "action": "api_call"}
    ]
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(json.dumps(record), encoding="utf-8")
    return Site("", hub, google, idp, tmp, saved)


@contextmanager
def serve(site: Site) -> Iterator[Site]:
    """Start the Hub on a free port. The address uses `localhost`, which is what Google sign-in needs."""
    site.hub.start()
    server = make_server(site.hub, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site.url = f"http://localhost:{port_of(server)}"
    site.hub.address = f"http://127.0.0.1:{port_of(server)}"
    try:
        yield site
    finally:
        server.shutdown()
        site.hub.stop()
        site.google.stop()
        site.idp.stop()
        for key, value in site.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
