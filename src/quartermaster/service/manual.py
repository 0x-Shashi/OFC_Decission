"""Manual test scripts kept on this computer: imported from Excel, listed in the web UI, and matched
to release features alongside the automated tests.

Each imported workbook is kept as one JSON file in the data folder (`.qm/manual`). The workbook
itself is not kept or changed. Importing a workbook with the same file name again replaces it.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from quartermaster.importers.manual_scripts import ScriptError, finish_scenario, new_scenario, parse_scripts, slug
from quartermaster.importers.xlsx import SpreadsheetError

MAX_UPLOAD = 20 * 1024 * 1024
MAX_FILES = 50
TYPED = "typed"  # scenarios typed in Quartermaster are kept in typed.json, like one more workbook
MAX_TYPED_STEPS = 200


@dataclass(frozen=True)
class _Step:
    intent: str


@dataclass(frozen=True)
class ManualTest:
    """A manual scenario in the shape release matching reads (see impact.analyzer.Matchable)."""

    id: str
    module: str
    product: str
    title: str
    process: str = ""
    tags: tuple[str, ...] = ()
    steps: tuple[_Step, ...] = field(default=())


class ManualScripts:
    def __init__(self, folder: Path):
        self.folder = folder.resolve()

    def _files(self) -> list[Path]:
        return sorted(self.folder.glob("*.json")) if self.folder.is_dir() else []

    def _load(self, path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def scenarios(self) -> list[dict[str, Any]]:
        """Every scenario of every imported workbook, with its cases and steps."""
        out: list[dict[str, Any]] = []
        for path in self._files():
            data = self._load(path)
            if data:
                out.extend(s for s in data.get("scenarios", []) if isinstance(s, dict))
        return out

    def summary(self) -> dict[str, Any]:
        """The list for the web page: scenarios without their steps, and the imported files."""
        files: list[dict[str, Any]] = []
        scenarios: list[dict[str, Any]] = []
        for path in self._files():
            data = self._load(path)
            if not data:
                continue
            items = [s for s in data.get("scenarios", []) if isinstance(s, dict)]
            entered = self.test_data_all()
            if path.stem != TYPED:  # typed scenarios are changed one by one, not as a file
                files.append(
                    {
                        "key": path.stem,
                        "file": data.get("file", path.stem),
                        "module": data.get("module", ""),
                        "product": data.get("product", ""),
                        "imported_at": data.get("imported_at"),
                        "scenarios": len(items),
                        "warnings": data.get("warnings", []),
                    }
                )
            for s in items:
                need = needs_data(s, entered.get(str(s.get("id")), {}))
                scenarios.append(
                    {
                        **{k: v for k, v in s.items() if k not in ("cases", "fields")},
                        # what still needs a value nobody has given (in the workbook or in Quartermaster)
                        "values_missing": sum(1 for kind in need.values() if kind == "value"),
                        "blank_data": any(kind == "blank" for kind in need.values()),
                    }
                )
        return {"files": files, "scenarios": scenarios}

    @staticmethod
    def test_id(scenario_id: str) -> str:
        """The id of the automated test a scenario becomes once done by hand, e.g. manual.ess-test-script.ess-001."""
        key, _, ref = scenario_id.partition("/")
        return f"manual.{slug(key)}.{slug(ref)}"

    @staticmethod
    def test_file(scenario_id: str) -> str:
        """Where that test is kept, inside the tests folder."""
        key, _, ref = scenario_id.partition("/")
        return f"manual/{slug(key)}/{slug(ref)}.yaml"

    def get(self, scenario_id: str) -> dict[str, Any]:
        for s in self.scenarios():
            if s.get("id") == scenario_id:
                return s
        raise LookupError(f"no manual scenario {scenario_id}")

    def tests(self) -> list[ManualTest]:
        """Scenarios for release matching: the scenario and its test case names, not every step,
        so a long script does not match everything by sheer number of words."""
        return [
            ManualTest(
                id=str(s["id"]),
                module=str(s.get("module", "")),
                product=str(s.get("product", "")),
                title=str(s.get("title", "")),
                process=str(s.get("description", "")),
                steps=tuple(_Step(str(c.get("name", ""))) for c in s.get("cases", [])),
            )
            for s in self.scenarios()
            if s.get("id")
        ]

    def import_files(self, data: dict[str, Any]) -> dict[str, Any]:
        """{"files": [{"name", "content" (base64), "module", "product"}], "save"}: read each workbook,
        and keep them when save is true. One unreadable file does not stop the others."""
        files = data.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("choose one or more Excel files")
        if len(files) > MAX_FILES:
            raise ValueError(f"import at most {MAX_FILES} files at a time")
        results = []
        for item in files:
            item = item if isinstance(item, dict) else {}
            name = Path(str(item.get("name") or "")).name
            view: dict[str, Any] = {"file": name}
            try:
                content = base64.b64decode(str(item.get("content") or ""), validate=True)
                if len(content) > MAX_UPLOAD:
                    raise ValueError("the file is larger than 20 MB")
                parsed = parse_scripts(name, content, str(item.get("module") or ""), str(item.get("product") or ""))
            except (binascii.Error, ScriptError, SpreadsheetError, ValueError) as e:
                view["problem"] = str(e) if not isinstance(e, binascii.Error) else "the file did not arrive whole"
                results.append(view)
                continue
            key = slug(Path(name).stem)
            target = self.folder / f"{key}.json"
            for s in parsed.scenarios:
                s["id"] = f"{key}/{s['ref']}"  # unique even when two workbooks cover one product
            view.update(
                key=key,
                kind=parsed.kind,
                module=parsed.module,
                product=parsed.product,
                scenarios=len(parsed.scenarios),
                cases=sum(s["case_count"] for s in parsed.scenarios),
                steps=sum(s["step_count"] for s in parsed.scenarios),
                blank_data=sum(bool(s["blank_data"]) for s in parsed.scenarios),
                warnings=parsed.warnings,
                replaces=target.is_file(),
                sample=[s["title"] for s in parsed.scenarios[:5]],
                saved=False,
            )
            if data.get("save") and parsed.module and parsed.product:
                self.folder.mkdir(parents=True, exist_ok=True)
                record = {
                    "file": parsed.file,
                    "kind": parsed.kind,
                    "module": parsed.module,
                    "product": parsed.product,
                    "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "warnings": parsed.warnings,
                    "scenarios": parsed.scenarios,
                }
                target.write_text(json.dumps(record, indent=1), encoding="utf-8")
                view["saved"] = True
            results.append(view)
        return {"files": results, "saved": sum(r.get("saved", False) for r in results)}

    # ------------------------------------------------------------------ scenarios typed in Quartermaster

    def save_typed(self, data: dict[str, Any]) -> dict[str, Any]:
        """{"id" (to change one), "title", "module", "product", "description", "steps": [{"action",
        "expected"}], "fields"}: a new manual scenario typed in the web UI instead of imported."""
        title = " ".join(str(data.get("title") or "").split())[:200]
        module = " ".join(str(data.get("module") or "").split())[:60]
        product = " ".join(str(data.get("product") or "").split())[:80]
        if not title:
            raise ValueError("give the scenario a name")
        if not (module and product):
            raise ValueError("enter the module and product, for example HCM and Global Human Resources")
        given = data.get("steps")
        raw: list[Any] = given if isinstance(given, list) else []
        steps: list[dict[str, str]] = []
        for item in raw:
            item = item if isinstance(item, dict) else {}
            action = str(item.get("action") or "").strip()[:2000]
            expected = str(item.get("expected") or "").strip()[:2000]
            if action or expected:
                steps.append({"step": f"step {len(steps) + 1}", "action": action, "expected": expected, "result": ""})
        if not steps:
            raise ValueError("write at least one step")
        if len(steps) > MAX_TYPED_STEPS:
            raise ValueError(f"write at most {MAX_TYPED_STEPS} steps")
        if any(not st["action"] for st in steps):
            raise ValueError("every step needs what to do, not only what should happen")
        listed = data.get("fields")
        fields_in: list[Any] = listed if isinstance(listed, list) else []
        fields = [" ".join(str(f).split())[:200] for f in fields_in if str(f).strip()][:100]
        description = str(data.get("description") or "").strip()[:2000]

        record = self._load(self.folder / f"{TYPED}.json") or {
            "file": "Typed in Quartermaster",
            "kind": TYPED,
            "module": "",
            "product": "",
            "warnings": [],
            "scenarios": [],
        }
        items = [x for x in record.get("scenarios", []) if isinstance(x, dict)]
        old = next((x for x in items if x.get("id") == data.get("id")), None) if data.get("id") else None
        if data.get("id") and old is None:
            raise LookupError("that typed scenario is no longer there")
        if old:
            ref = str(old["ref"])
        else:
            # never reused, so a new scenario does not take over a deleted one's saved test
            used = {str(x.get("ref")) for x in items}
            n = int(record.get("last_number") or 0) + 1
            while f"T{n:03d}" in used:
                n += 1
            ref, record["last_number"] = f"T{n:03d}", n
        scenario = new_scenario(ref, title, "")
        scenario["description"] = description
        scenario["cases"].append(
            {"id": ref, "name": title, "description": description, "precondition": "", "steps": steps}
        )
        scenario["fields"] = fields
        finish_scenario(scenario)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        scenario.update(
            id=f"{TYPED}/{ref}",
            file=record["file"],
            module=module,
            product=product,
            typed=True,
            created_at=old.get("created_at", now) if old else now,
            changed_at=now,
        )
        items = [scenario if x is old else x for x in items] if old else [*items, scenario]
        record.update(scenarios=items, imported_at=now)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{TYPED}.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
        return scenario

    def delete_typed(self, scenario_id: str) -> dict[str, Any]:
        path = self.folder / f"{TYPED}.json"
        record = self._load(path) or {}
        items = [x for x in record.get("scenarios", []) if isinstance(x, dict)]
        kept = [x for x in items if x.get("id") != scenario_id]
        if len(kept) == len(items):
            raise LookupError("that typed scenario is no longer there")
        record["scenarios"] = kept
        path.write_text(json.dumps(record, indent=1), encoding="utf-8")
        return {"removed": scenario_id}

    # ------------------------------------------------------------------ drafts prepared by AI

    @property
    def _review_file(self) -> Path:
        return self.folder / "_review" / "prepared.json"  # a sub-folder: not read as a workbook

    def reviews(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._review_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_reviews(self, data: dict[str, dict[str, Any]]) -> None:
        self._review_file.parent.mkdir(parents=True, exist_ok=True)
        self._review_file.write_text(json.dumps(data, indent=1), encoding="utf-8")

    def mark_prepared(self, scenario_id: str, run_id: str, by: str) -> None:
        """An AI prepared this scenario: it waits for a person to check its pictures and approve it."""
        data = self.reviews()
        data[scenario_id] = {
            "run_id": run_id,
            "by": by,
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "approved_at": None,
        }
        self._save_reviews(data)

    def approve(self, scenario_id: str) -> dict[str, Any]:
        data = self.reviews()
        if scenario_id not in data:
            raise LookupError("this scenario has no prepared version waiting for review")
        data[scenario_id]["approved_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self._save_reviews(data)
        return data[scenario_id]

    def checked_by_hand(self, scenario_id: str) -> None:
        """Done by a person: whatever an AI prepared before has been replaced, nothing to review."""
        data = self.reviews()
        if data.pop(scenario_id, None) is not None:
            self._save_reviews(data)

    # ------------------------------------------------------------------ test data entered here

    @property
    def _data_file(self) -> Path:
        return self._review_file.parent / "test_data.json"

    def test_data_all(self) -> dict[str, dict[str, str]]:
        """Values entered in Quartermaster for steps whose script does not give them, per scenario:
        {scenario id: {step number: text}}. Kept on this computer; the workbook is not changed."""
        try:
            data = json.loads(self._data_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def set_test_data(self, scenario_id: str, values: dict[str, Any]) -> dict[str, str]:
        scenario = self.get(scenario_id)
        count = len(numbered_steps(scenario))
        clean: dict[str, str] = {}
        for key, text in (values or {}).items():
            number = str(key).strip()
            value = " ".join(str(text or "").split())[:500]
            if not number.isdigit() or not 1 <= int(number) <= count:
                raise ValueError(f"step {key} is not in this scenario")
            if value:
                clean[number] = value
        data = self.test_data_all()
        if clean:
            data[scenario_id] = clean
        else:
            data.pop(scenario_id, None)
        self._data_file.parent.mkdir(parents=True, exist_ok=True)
        self._data_file.write_text(json.dumps(data, indent=1), encoding="utf-8")
        return clean

    def with_test_data(self, scenario: dict[str, Any]) -> dict[str, Any]:
        """The scenario with the values entered here written into its steps ("Test data: ..."), as
        the AI and the tester then read them."""
        entered = self.test_data_all().get(str(scenario.get("id")), {})
        if not entered:
            return scenario
        out: dict[str, Any] = json.loads(json.dumps(scenario))
        for number, case_index, step_index in numbered_steps(out):
            text = entered.get(str(number))
            if text and step_index is not None:
                st = out["cases"][case_index]["steps"][step_index]
                st["action"] = f"{st.get('action', '')}\nTest data: {text}"
        return out

    def remove(self, key: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z0-9-]+", key or ""):
            raise ValueError("unknown file")
        path = self.folder / f"{key}.json"
        if not path.is_file():
            raise LookupError("that file is not imported")
        path.unlink()
        return {"removed": key}


_TYPES = re.compile(r"\b(enter|type|fill(?:\s+in)?|input|provide|key\s+in)\b", re.I)
_GIVES = re.compile(r"\d|\bas\s+\S|[:=]\s*\S|\be\.g\.", re.I)


def numbered_steps(scenario: dict[str, Any]) -> list[tuple[int, int, int | None]]:
    """(step number, case index, step index) in the order a scenario is done (see guided.guide_steps:
    a case without steps counts as one step, with step index None)."""
    out: list[tuple[int, int, int | None]] = []
    for ci, case in enumerate(scenario.get("cases") or []):
        steps = case.get("steps") or []
        if not steps:
            out.append((len(out) + 1, ci, None))
        for si in range(len(steps)):
            out.append((len(out) + 1, ci, si))
    return out


def needs_data(scenario: dict[str, Any], entered: dict[str, str] | None = None) -> dict[int, str]:
    """The steps that need a value nobody has given yet, by step number: "blank" where the script
    says <> (test data missing), "value" where it says to type something without saying what
    ("Enter required data", "enter the date"). The AI never makes values up, so Prepare stops at
    them. Names in quotes are usually field names, not values, so they do not count as a value."""
    entered = entered or {}
    out: dict[int, str] = {}
    for number, ci, si in numbered_steps(scenario):
        if si is None or entered.get(str(number)):
            continue
        st = scenario["cases"][ci]["steps"][si]
        action, expected = str(st.get("action", "")), str(st.get("expected", ""))
        if re.search(r"<\s*>", f"{action} {expected}"):
            out[number] = "blank"
        elif _TYPES.search(action) and not _GIVES.search(action):
            out[number] = "value"
    return out


def values_missing(scenario: dict[str, Any]) -> int:
    """How many steps say to type something without saying what (see needs_data)."""
    return sum(1 for kind in needs_data(scenario).values() if kind == "value")
