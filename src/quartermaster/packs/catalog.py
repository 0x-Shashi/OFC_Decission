"""Reading the packs that ship with Quartermaster, and installing them into a tests folder."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from quartermaster.dsl.suites import SUITES_DIR
from quartermaster.service import atomic

PACKS_DIR = Path(__file__).parent
INSTALL_DIR = "library"  # inside the tests folder: library/<pack>/<test>.yaml
MARKER = ".pack.json"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


class PackError(ValueError):
    """A pack is missing or wrong. The message is for a person."""


@dataclass
class Pack:
    id: str
    title: str
    module: str
    kind: str
    version: int
    description: str
    needs: str
    read_only: bool
    checked: dict[str, Any] | None
    folder: Path
    tests: list[dict[str, str]] = field(default_factory=list)  # id, title, product, process, file

    def files(self) -> dict[str, str]:
        """The text of each test file of the pack, by file name."""
        return {t["file"]: (self.folder / "tests" / t["file"]).read_text(encoding="utf-8") for t in self.tests}


def list_packs(directory: Path = PACKS_DIR) -> list[Pack]:
    """Every pack, in the order of their titles. A pack folder that cannot be read raises PackError."""
    packs = [_read(p) for p in sorted(directory.iterdir()) if (p / "pack.yaml").is_file()]
    return sorted(packs, key=lambda p: p.title.casefold())


def get_pack(name: str, directory: Path = PACKS_DIR) -> Pack:
    for pack in list_packs(directory):
        if pack.id == name:
            return pack
    known = ", ".join(p.id for p in list_packs(directory)) or "none"
    raise PackError(f"no pack called '{name}' (there is: {known})")


def _read(folder: Path) -> Pack:
    meta = yaml.safe_load((folder / "pack.yaml").read_text(encoding="utf-8"))
    if not isinstance(meta, dict) or meta.get("pack") != folder.name or not _ID.match(folder.name):
        raise PackError(f"{folder.name}: pack.yaml must say `pack: {folder.name}`")
    tests = []
    for path in sorted((folder / "tests").glob("*.y*ml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tests.append(
            {
                "id": str(spec.get("id", "")),
                "title": str(spec.get("title", "")),
                "product": str(spec.get("product", "")),
                "process": str(spec.get("process", "")),
                "file": path.name,
            }
        )
    checked = meta.get("checked")
    return Pack(
        id=str(meta["pack"]),
        title=str(meta.get("title") or meta["pack"]),
        module=str(meta.get("module") or ""),
        kind=str(meta.get("kind") or ""),
        version=int(meta.get("version") or 1),
        description=str(meta.get("description") or ""),
        needs=str(meta.get("needs") or ""),
        read_only=bool(meta.get("read_only")),
        checked=checked if isinstance(checked, dict) else None,
        folder=folder,
        tests=tests,
    )


# ------------------------------------------------------------------ what is in a tests folder


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _marker(dest: Path) -> dict[str, Any]:
    try:
        data = json.loads((dest / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def suite_name(pack: Pack) -> str:
    return f"library-{pack.id}"


def pack_status(tests_root: Path, pack: Pack) -> dict[str, Any] | None:
    """None when the pack is not installed; else its installed version, which files you changed, and whether an
    install now would add or update something."""
    dest = tests_root / INSTALL_DIR / pack.id
    marker = _marker(dest)
    if not marker:
        return None
    seen: dict[str, str] = marker.get("files") or {}
    shipped = {name: _sha(text) for name, text in pack.files().items()}
    changed, news = [], 0
    for name, sha in shipped.items():
        path = dest / name
        if not path.is_file():
            news += 1  # a file of the pack that is not there (new in the pack, or deleted by you)
            continue
        have = _sha(path.read_text(encoding="utf-8"))
        if have != seen.get(name, sha):
            changed.append(name)
        elif have != sha:
            news += 1  # the pack has a newer text of a file you have not changed
    return {
        "version": int(marker.get("version") or 1),
        "changed": sorted(changed),
        "update_available": bool(news) or int(marker.get("version") or 1) < pack.version,
        "suite": suite_name(pack),
    }


# ------------------------------------------------------------------ installing


def install(tests_root: Path, pack: Pack) -> dict[str, Any]:
    """Copy the pack into `<tests>/library/<pack>/`. Nothing you have changed is lost: a file that differs from what
    was installed is kept and reported. Returns {added, updated, kept, unchanged, removed, suite}."""
    dest = tests_root / INSTALL_DIR / pack.id
    marker = _marker(dest)
    seen: dict[str, str] = marker.get("files") or {}
    keep: dict[str, str] = {}
    report: dict[str, Any] = {"added": [], "updated": [], "kept": [], "unchanged": [], "removed": []}
    for name, text in pack.files().items():
        sha = _sha(text)
        path = dest / name
        if not path.is_file():
            atomic.write_text(path, text)
            report["added"].append(name)
            keep[name] = sha
            continue
        have = _sha(path.read_text(encoding="utf-8"))
        if have == sha:
            report["unchanged"].append(name)
            keep[name] = sha
        elif have == seen.get(name):  # the file is as it was installed: the pack has a newer text
            atomic.write_text(path, text)
            report["updated"].append(name)
            keep[name] = sha
        else:  # you changed it (or it was there before the pack): leave it as it is
            report["kept"].append(name)
            keep[name] = seen.get(name, "")
    for name, old in seen.items():  # a file the pack no longer has
        if name in keep:
            continue
        path = dest / name
        if path.is_file() and _sha(path.read_text(encoding="utf-8")) == old:
            path.unlink()
            report["removed"].append(name)
        elif path.is_file():
            report["kept"].append(name)
            keep[name] = old
    atomic.write_text(dest / MARKER, json.dumps({"pack": pack.id, "version": pack.version, "files": keep}, indent=1))
    report["suite"] = _make_suite(tests_root, pack)
    return report


def _make_suite(tests_root: Path, pack: Pack) -> str:
    """The suite that runs the pack's tests: made once, never overwritten (you may have changed it)."""
    from quartermaster.dsl.suites import to_yaml

    name = suite_name(pack)
    path = tests_root / SUITES_DIR / f"{name}.yaml"
    if not path.exists():
        text = to_yaml(
            name,
            f"{pack.title} (library)",
            f"The tests of the library pack {pack.id}.",
            [{"folders": [f"{INSTALL_DIR}/{pack.id}"]}],
            [],
        )
        atomic.write_text(path, text)
    return name
