"""An audit export a person can hand to an auditor: the lines they asked for, a manifest that says what is in the
file and how the log looked when it was made, and plain steps to check it."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime
from typing import Any

from quartermaster import __version__
from quartermaster.service.audit import AuditLog

FORMATS = {"csv": "csv", "jsonl": "jsonl"}

HOW_TO_VERIFY = """How to check this export

1. The file is what the manifest says it is. In manifest.json, "sha256" is the SHA-256 of {file}. Compute it yourself:
     Windows:    certutil -hashfile {file} SHA256
     Mac/Linux:  sha256sum {file}
   The two must be the same.

2. No line was changed. Every line of the log carries "hash" and "prev": the hash covers the line's own content and the
   hash of the line before it. With Quartermaster installed:
     qm audit verify {file}      (a .jsonl export)
   It reports any line whose content no longer matches its hash. A complete export ("complete": true in the manifest)
   is also checked line by line as one chain. An export that was filtered has gaps by design; they are counted.
   The CSV has the same two hashes in its last columns, but only the .jsonl can be checked by the command above.

3. Nothing was cut off the end. The chain alone cannot show that the newest lines were removed. The manifest keeps
   "log.head" (the hash of the newest line) and "log.entries" (how many lines there were). Keep this manifest, or send
   it to someone, and later check the live log against it:
     qm audit verify --against manifest.json
   The old newest line must still be in the log, and the log must not have fewer lines than it had.

Passwords, keys and test data values are never written to the audit log, so they are not in this export.
"""


def build(
    log: AuditLog,
    *,
    fmt: str,
    exported_by: str,
    since: str = "",
    until: str = "",
    who: str = "",
    text: str = "",
    action: str = "",
) -> tuple[bytes, str, dict[str, Any]]:
    """(the zip, its file name, the manifest). Raises ValueError for a format or date that is not understood."""
    if fmt not in FORMATS:
        raise ValueError("the format must be csv or jsonl")
    for day in (since, until):
        if day:
            try:
                datetime.strptime(day, "%Y-%m-%d")
            except ValueError as e:
                raise ValueError(f"the dates must look like 2026-10-31 (got {day!r})") from e
    if since and until and since > until:
        raise ValueError("the first date is after the last date")
    entries = log.select(since=since, until=until, who=who, text=text, action=action)
    state = log.verify()
    stamp = datetime.now().astimezone()
    filters = {k: v for k, v in {"from": since, "to": until, "who": who, "text": text, "what": action}.items() if v}
    name = f"audit.{FORMATS[fmt]}"
    data = (_csv(entries) if fmt == "csv" else _jsonl(entries)).encode("utf-8" if fmt == "jsonl" else "utf-8-sig")
    manifest: dict[str, Any] = {
        "product": "Quartermaster",
        "version": __version__,
        "exported_at": stamp.isoformat(timespec="seconds"),
        "exported_by": exported_by,
        "file": name,
        "format": fmt,
        "filters": filters,
        "entries": len(entries),
        "first_at": entries[0].get("at") if entries else None,
        "last_at": entries[-1].get("at") if entries else None,
        "complete": not filters,
        "sha256": hashlib.sha256(data).hexdigest(),
        "log": {
            "entries": state["entries"],
            "hashed": state["hashed"],
            "legacy": state["legacy"],
            "head": state["head"],
            "chain": "intact"
            if state["ok"]
            else f"broken at line {state['problem']['line']}: {state['problem']['why']}",
        },
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, data)
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        z.writestr("HOW_TO_VERIFY.txt", HOW_TO_VERIFY.format(file=name))
    return buf.getvalue(), f"quartermaster-audit-{stamp:%Y%m%d-%H%M}.zip", manifest


def _jsonl(entries: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)


def _csv(entries: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["When", "Who", "What", "Subject", "Details", "Hash", "Previous hash"])
    for e in entries:
        details = "; ".join(f"{k}: {_text(v)}" for k, v in (e.get("details") or {}).items())
        w.writerow(
            [e.get("at", ""), e.get("who", ""), e.get("action", ""), e.get("subject", ""), details]
            + [e.get("hash", ""), e.get("prev", "")]
        )
    return buf.getvalue()


def _text(value: Any) -> str:
    return ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
