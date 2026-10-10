"""Write a file so that a reader never sees it half written.

The text goes to a side file first, then replaces the real file in one step."""

from __future__ import annotations

import os
import threading
from pathlib import Path


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    side = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.part")
    side.write_text(text, encoding="utf-8")
    side.replace(path)  # one step: the file is the old text or the new text, never in between
