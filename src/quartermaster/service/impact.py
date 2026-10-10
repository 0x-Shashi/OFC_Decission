"""Release impact for the web UI: the release feature lists on this computer, importing a new one,
and which tests a release puts at risk (the same analysis as `qm plan`).

Release files are read from the releases folder (`qm serve --releases`, by default
examples/releases) and from the imported folder (`.qm/releases`). An imported list with the same
file name as one in the releases folder is the one shown.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from quartermaster.importers.release_sheet import ImportError_, parse_release
from quartermaster.importers.xlsx import SpreadsheetError
from quartermaster.runner.discovery import match_pages
from quartermaster.service.manual import ManualScripts

MAX_UPLOAD = 20 * 1024 * 1024
# Words every manual script uses, which say nothing about which feature it tests.
_SCRIPT_WORDS = frozenset(
    [
        "validate",
        "verify",
        "functionality",
        "check",
        "test",
        "tests",
        "testing",
        "ensure",
        "able",
        "should",
        "page",
        "screen",
        "click",
        "enter",
        "select",
        "login",
        "user",
        "details",
        "process",
        "transactions",
        "transaction",
        "create",
        "created",
        "update",
        "view",
        "information",
        "info",
        "employee",
        "employees",
        "manager",
        "managers",
        "worker",
        "workers",
        "specialist",
        "administrator",
        "admin",
        "self",
        "service",
    ]
)
_SUFFIXES = (".json", ".yaml", ".yml")


class Releases:
    def __init__(self, folder: Path | None, imported: Path):
        self.folder = folder.resolve() if folder else None
        self.imported = imported.resolve()

    # ------------------------------------------------------------------ listing

    def _files(self) -> dict[str, tuple[Path, str]]:
        found: dict[str, tuple[Path, str]] = {}
        for root, source in ((self.folder, "folder"), (self.imported, "imported")):  # imported wins
            if root and root.is_dir():
                for f in sorted(root.iterdir()):
                    if f.is_file() and f.suffix.lower() in _SUFFIXES:
                        found[f.name] = (f, source)
        return found

    def list(self) -> list[dict[str, Any]]:
        out = []
        for name, (path, source) in self._files().items():
            item: dict[str, Any] = {"name": name, "source": source, "file": str(path)}
            try:
                data = _read(path)
                features = data.get("features") or []
                item.update(
                    id=str(data.get("id", "")),
                    features=len(features),
                    modules=sorted({str(f.get("module", "")) for f in features if isinstance(f, dict)} - {""}),
                )
            except (OSError, ValueError, yaml.YAMLError) as e:
                item["problem"] = f"Could not read this file: {e}"
            item["updated"] = path.stat().st_mtime
            out.append(item)
        out.sort(key=lambda r: (str(r.get("id", "")), r["updated"]), reverse=True)  # newest release first
        return out

    def path(self, name: str) -> Path:
        hit = self._files().get(name)
        if hit is None:
            raise LookupError(f"no release list named {name!r}")
        return hit[0]

    # ------------------------------------------------------------------ import

    def import_file(self, data: dict[str, Any]) -> dict[str, Any]:
        """{"name", "content" (base64), "release_id", "save"}: read it, and keep it when save is true."""
        name = Path(str(data.get("name") or "")).name
        try:
            content = base64.b64decode(str(data.get("content") or ""), validate=True)
        except (binascii.Error, ValueError) as e:
            raise ValueError("the file did not arrive whole; choose it again") from e
        if not content:
            raise ValueError("choose a file to import")
        if len(content) > MAX_UPLOAD:
            raise ValueError("the file is larger than 20 MB")
        try:
            result = parse_release(name, content, str(data.get("release_id") or ""))
        except (ImportError_, SpreadsheetError) as e:
            raise ValueError(str(e)) from e
        release = result.release
        target = self.imported / f"{release['id']}.json"
        view: dict[str, Any] = {
            "id": release["id"],
            "features": len(release["features"]),
            "sample": release["features"][:8],
            "columns": result.columns,
            "skipped": result.skipped,
            "rows": result.source_rows,
            "name": target.name,
            "replaces": target.name in self._files(),
            "saved": False,
        }
        if data.get("save"):
            self.imported.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(release, indent=2), encoding="utf-8")
            view["saved"] = True
        return view

    # ------------------------------------------------------------------ plan

    def plan(
        self,
        name: str,
        tests_root: Path,
        budget: float | None,
        opt_ins: set[str],
        manual: ManualScripts | None = None,
        pod_pages: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Which tests to run for this release, why, and which features no test covers. Manual
        scenarios (imported scripts) count as coverage too, and are ranked by risk the same way."""
        # Imported here so the rest of the service does not need the analysis libraries loaded.
        from quartermaster.domain.models import Priority
        from quartermaster.dsl.library import files_of_tests
        from quartermaster.dsl.loader import SpecError, load_release, load_test
        from quartermaster.impact.analyzer import (
            COVERAGE_THRESHOLD,
            MATCH_THRESHOLD,
            _tokens,
            analyze,
            match,
            plan,
            severity,
        )

        try:
            release = load_release(self.path(name))
        except SpecError as e:
            raise ValueError(f"this release list is not valid: {e}") from e
        tests: list[Any] = []
        files: dict[str, str] = {}
        problems: list[dict[str, str]] = []
        for spec in files_of_tests(tests_root):
            rel = spec.relative_to(tests_root).as_posix()
            try:
                t = load_test(spec)
            except (SpecError, OSError, ValueError) as e:
                problems.append({"file": rel, "problem": str(e).replace(str(spec), rel)[:300]})
                continue
            if t.id in files:
                problems.append({"file": rel, "problem": f"the id {t.id} is also used by {files[t.id]}"})
                continue
            tests.append(t)
            files[t.id] = rel

        impacts = analyze(release, tests, opt_ins)
        result = plan(impacts, budget_minutes=budget)
        chosen = {i.test.id for i in result.selected}
        rows = [
            {
                "id": i.test.id,
                "file": files[i.test.id],
                "title": i.test.title,
                "module": i.test.module,
                "product": i.test.product,
                "priority": i.test.priority.value,
                "minutes": i.test.estimated_minutes,
                "risk": i.risk,
                "score": i.priority,
                "selected": i.test.id in chosen,
                "always": i.test.priority is Priority.CRITICAL,
                "features": i.features,
                "reasons": i.reasons,
            }
            for i in [*result.selected, *sorted(result.deferred, key=lambda i: (-i.priority, -i.risk, i.test.id))]
        ]
        manual_tests = manual.tests() if manual else []
        info = {s["id"]: s for s in manual.summary()["scenarios"]} if manual else {}
        manual_words = {
            m.id: _tokens(" ".join([m.title, m.process, *(st.intent for st in m.steps)])) - _SCRIPT_WORDS
            for m in manual_tests
        }

        def match_manual(f: Any, m: Any) -> tuple[float, list[str]]:
            """A manual workbook holds many scenarios for one product, so the same product alone is
            weak evidence: a scenario must also share words with the feature to count."""
            score, why = match(f, m)
            common = (_tokens(f"{f.title} {f.description}") - _SCRIPT_WORDS) & manual_words[m.id]
            if not common:
                return score * 0.4, [*why, "no words in common with the feature: weak evidence"]
            return score, why

        manual_rows: list[dict[str, Any]] = []
        for m in manual_tests:
            survive = 1.0
            reasons: list[str] = []
            covers: list[str] = []
            for f in release.features:
                score, why = match_manual(f, m)
                if score < MATCH_THRESHOLD:
                    continue
                p = score * severity(f, opt_ins)
                survive *= 1 - p
                if score >= COVERAGE_THRESHOLD:
                    covers.append(f.id)
                reasons.append(f"{f.id} '{f.title}' (p={p:.2f}): {'; '.join(why)}")
            risk = round(1 - survive, 4)
            if risk >= 0.05:
                s = info.get(m.id, {})
                manual_rows.append(
                    {
                        "id": m.id,
                        "ref": s.get("ref", ""),
                        "title": m.title,
                        "module": m.module,
                        "product": m.product,
                        "file": s.get("file", ""),
                        "cases": s.get("case_count", 0),
                        "steps": s.get("step_count", 0),
                        "risk": risk,
                        "features": covers,
                        "reasons": reasons,
                    }
                )
        manual_rows.sort(key=lambda r: (-r["risk"], r["id"]))

        features = []
        for f in release.features:
            scored = sorted(((match(f, t)[0], t.id) for t in tests), reverse=True)
            related = [(round(s, 2), tid) for s, tid in scored if s >= MATCH_THRESHOLD]
            best = related[0][0] if related else 0.0
            by_hand = sorted(((round(match_manual(f, m)[0], 2), m.id) for m in manual_tests), reverse=True)
            by_hand = [(s, mid) for s, mid in by_hand if s >= MATCH_THRESHOLD]
            if best >= COVERAGE_THRESHOLD:
                coverage = "covered"
            elif by_hand and by_hand[0][0] >= COVERAGE_THRESHOLD:
                coverage = "manual"
            else:
                coverage = "weak" if related or by_hand else "none"
            features.append(
                {
                    "id": f.id,
                    "title": f.title,
                    "module": f.module,
                    "product": f.product,
                    "change_type": f.change_type.value,
                    "opt_in": f.opt_in,
                    "action_required": f.customer_action_required,
                    "coverage": coverage,
                    "pod_pages": match_pages(f"{f.title} {f.description}", pod_pages) if pod_pages else [],
                    "tests": [{"id": tid, "match": s} for s, tid in related[:5]],
                    "manual": [
                        {"id": mid, "title": info.get(mid, {}).get("title", mid), "match": s} for s, mid in by_hand[:5]
                    ],
                }
            )
        counts = {c: sum(f["coverage"] == c for f in features) for c in ("covered", "manual", "weak", "none")}
        return {
            "name": name,
            "release": release.id,
            "budget": budget,
            "opt_ins": sorted(opt_ins),
            "tests": rows,
            "manual": manual_rows,
            "features": features,
            "problems": problems,
            "summary": {
                "features": len(features),
                **counts,
                "tests": len(tests),
                "selected": len(result.selected),
                "minutes": result.total_minutes,
                "at_risk": sum(i.risk >= 0.05 for i in impacts),
                "manual_scenarios": len(manual_tests),
                "manual_at_risk": len(manual_rows),
                "on_pod": sum(bool(f["pod_pages"]) for f in features),
            },
            "pod_pages": len(pod_pages or []),
        }


def _read(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("not a release list")
    return data
