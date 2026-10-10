"""The run record (run.json): what was tested, where, by whom, and the result of every step.

One run gets one folder:

    evidence/<test id>/<run id>/
        run.json                         this record
        screenshots/step-01.png ...      when screenshots are on
        videos/*.webm                    when video is on (never put in the document)
        <test id>_<run id>_evidence.docx when the evidence document is requested

Paths inside run.json are relative to the run folder, so the folder can be moved or zipped
and still be read. Each screenshot and the test file carry a SHA-256 fingerprint, so anyone
can check later that a file is the one produced by the run.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import platform
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from quartermaster import __version__
from quartermaster.domain.models import RunResult

SCHEMA_VERSION = 1


def new_run_id() -> str:
    """Sortable and unique enough for folder names, e.g. 20260926-183012-4F2A."""
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2).upper()}"


def run_folder(evidence_root: Path, test_id: str, run_id: str) -> Path:
    return evidence_root / test_id / run_id


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def build_record(
    result: RunResult,
    *,
    run_dir: Path,
    test_file: Path | None,
    video_mode: str,
    videos: list[str],
    executed_by: str | None = None,
) -> dict[str, Any]:
    raw = result.model_dump(mode="json")
    hashes: dict[str, str] = {}
    for step in [*raw["steps"], *raw["setup"], *raw["cleanup"]]:
        rel_paths = []
        for p in step.get("evidence", []):
            rel = _relative(Path(p), run_dir)
            rel_paths.append(rel)
            full = run_dir / rel
            if full.is_file():
                hashes[rel] = sha256_of(full)
        step["evidence"] = rel_paths
    kept_videos = [_relative(Path(v), run_dir) for v in videos if Path(v).is_file()]
    return {
        "schema": SCHEMA_VERSION,
        **{k: raw[k] for k in ("run_id", "test_id", "test_title")},
        "status": result.status.value,
        "environment": raw["environment"],
        "environment_url": raw["environment_url"],
        "release": raw["release"],
        "persona": raw["persona"],
        "executed_by": executed_by or _current_user(),
        "machine": platform.node(),
        "started_at": raw["started_at"],
        "finished_at": raw["finished_at"],
        "screenshots": raw["screenshots"],
        "video": video_mode,
        "videos": kept_videos,
        "test_file": str(test_file) if test_file else "",
        "test_file_sha256": sha256_of(test_file) if test_file and test_file.is_file() else "",
        "quartermaster_version": __version__,
        "steps": raw["steps"],
        "healing": raw["healing"],
        "flaky": result.flaky,  # passed, but only after a step was tried again
        "evidence_sha256": hashes,
        **({"written_steps": raw["written_steps"]} if raw.get("written_steps") else {}),
        **({"setup": raw["setup"], "setup_status": result.setup_status} if raw["setup"] else {}),
        **({"cleanup": raw["cleanup"], "cleanup_status": result.cleanup_status} if raw["cleanup"] else {}),
    }


def write_record(record: dict[str, Any], run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "run.json"
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return out


def _relative(path: Path, run_dir: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no login name available (some containers)
        return ""
