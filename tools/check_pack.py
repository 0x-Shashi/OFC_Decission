"""Run the REST tests of a library pack against a test pod, with Quartermaster's own engine, and say which pass.

    QM_FUSION_URL=https://... QM_FUSION_USER=... QM_FUSION_PASSWORD=... \\
        python tools/check_pack.py src/quartermaster/packs/hcm-services [--write]

It needs no browser: the REST steps are plain HTTP calls with the user and password from the environment (basic
authentication, which Oracle Fusion accepts for REST). Only GET is allowed: a library test never changes the pod.
`--write` records the result in the pack's pack.yaml (`checked:`), which the Test library page shows.

Nothing secret is printed: the pod's address, the user and the password stay out of the output.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from quartermaster.domain.models import Environment, EnvironmentKind, StepStatus
from quartermaster.dsl.loader import load_tests
from quartermaster.runner.engine import run_test


class UrllibDriver:
    """Just enough of a driver for tests made only of REST steps."""

    def __init__(self, base: str, user: str, password: str) -> None:
        self.base = base.rstrip("/")
        self.auth = base64.b64encode(f"{user}:{password}".encode()).decode()

    def open(self, env: Environment, persona: str) -> None:
        pass

    def close(self) -> None:
        pass

    def api_call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if method != "GET":
            raise RuntimeError(f"a library test may only read from the pod (GET), not {method}")
        url = path if path.startswith("http") else self.base + path
        if not url.startswith(self.base):
            raise RuntimeError("only the pod's own address is called")
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {self.auth}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:  # noqa: S310 - the pod's own https address
                raw = res.read()
                return res.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            return e.code, None
        except (urllib.error.URLError, OSError) as e:  # the network dropped: the step fails, and may be tried again
            raise RuntimeError(f"could not reach the pod ({type(e).__name__})") from None

    def screenshot(self, name: str, highlight: Any = None) -> str | None:
        return None  # there is no screen: the steps are REST calls

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"this check runs REST steps only (not {name})")


def check(pack: Path, driver: UrllibDriver, env: Environment) -> dict[str, Any]:
    results = []
    for test in load_tests(pack / "tests"):
        result = run_test(test, env, driver, retries=1, retry_wait_s=2.0)  # a read may be tried again, as in a real run
        failed = next((s for s in result.steps if s.status is StepStatus.FAILED), None)
        results.append({"id": test.id, "passed": failed is None, "why": (failed.error or "")[:160] if failed else ""})
    return {"passed": sum(r["passed"] for r in results), "of": len(results), "tests": results}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("packs", nargs="+", type=Path)
    ap.add_argument("--write", action="store_true", help="record the result in each pack.yaml")
    args = ap.parse_args(argv)
    try:
        url, user, password = (
            os.environ["QM_FUSION_URL"],
            os.environ["QM_FUSION_USER"],
            os.environ["QM_FUSION_PASSWORD"],
        )
    except KeyError as e:
        print(f"error: set {e.args[0]} (a test pod, never production)", file=sys.stderr)
        return 2
    env = Environment(name="pod", url=url, kind=EnvironmentKind.DEV)
    driver = UrllibDriver(url, user, password)
    bad = 0
    for pack in args.packs:
        got = check(pack, driver, env)
        print(f"{pack.name}: {got['passed']} of {got['of']} passed")
        for r in got["tests"]:
            if not r["passed"]:
                print(f"    FAILED {r['id']}: {r['why']}")
                bad += 1
        if args.write:
            meta_path = pack / "pack.yaml"
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
            meta["checked"] = {"date": date.today().isoformat(), "passed": got["passed"], "of": got["of"]}
            meta_path.write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
