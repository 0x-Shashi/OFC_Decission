"""Pod discovery: which pages does this pod really have? Read only.

It opens the Navigator (the menu of every page the signed-in test user may open) and writes down the names of what
is listed. Nothing is saved, submitted or deleted: the only clicks are on the Navigator button and on menu groups
that are folded away, to see what is inside them. A person switches it on per pod, in Settings, and every look is
in the audit log with the list of what was found.

The page is read in a way that does not depend on Oracle's exact markup: the links on the home page are noted
first, then the Navigator is opened (and its folded groups), and what is new on the screen is the Navigator's list.

`match_pages` then tells, for a feature of a release, which of those pages it probably touches.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

MAX_PAGES = 500
MAX_EXPANDS = 40
MAX_NAME = 60

# Everything on the screen that is a way to go somewhere or open something: its text, and whether it is folded away.
SCAN_JS = """() => {
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const label = (el) => (el.getAttribute('aria-label') || el.getAttribute('title') || el.innerText || '')
    .replace(/\\s+/g, ' ').trim();
  const found = [];
  for (const el of document.querySelectorAll(
      "a, [role=link], [role=menuitem], [role=treeitem], [aria-expanded]")) {
    if (!shown(el)) continue;
    const text = label(el);
    if (text) found.push({text, folded: el.getAttribute('aria-expanded') === 'false'});
  }
  return found;
}"""

# Names that are part of every page, not pages of the pod.
_NOT_PAGES = {
    "navigator",
    "show more",
    "show less",
    "more",
    "close",
    "search",
    "help",
    "sign out",
    "settings and actions",
    "skip to main content",
    "skip to navigation",
    "notifications",
    "home",
    "back",
    "next",
    "previous",
    "done",
    "cancel",
    "save",
    "expand",
    "collapse",
}


class DiscoveryError(Exception):
    """The pod's pages could not be read. The message is for the person at the Settings page."""


def scan_navigator(page: Any, open_navigator: Callable[[], bool]) -> list[str]:
    """The names of the pages in the Navigator. `page` is a Playwright page; `open_navigator` clicks the Navigator
    button and says whether it found it."""
    before = {item["text"] for item in page.evaluate(SCAN_JS)}
    if not open_navigator():
        raise DiscoveryError("the Navigator button was not found on the pod's home page")
    page.wait_for_timeout(1500)
    seen: dict[str, bool] = {}  # name -> still folded
    expanded: set[str] = set()
    for _ in range(MAX_EXPANDS + 1):
        for item in page.evaluate(SCAN_JS):
            if item["text"] not in before:
                seen[item["text"]] = bool(item["folded"]) and item["text"] not in expanded
        folded = [name for name, is_folded in seen.items() if is_folded and name not in expanded]
        if not folded or len(expanded) >= MAX_EXPANDS:
            break
        name = folded[0]
        expanded.add(name)
        try:  # a folded group: opening it only shows what is inside
            page.get_by_text(name, exact=True).first.click(timeout=3000)
            page.wait_for_timeout(500)
        except Exception:  # noqa: BLE001 - one group that will not open must not stop the rest
            continue
    last = [item["text"] for item in page.evaluate(SCAN_JS) if item["text"] not in before]  # in screen order
    names = clean_names(last + [name for name in seen if name not in last])
    if not names:
        raise DiscoveryError("the Navigator opened but no pages were listed in it")
    return names


def clean_names(raw: list[str]) -> list[str]:
    """Page names without the buttons every page has, doubles or odd lines, in the order found."""
    out: list[str] = []
    done: set[str] = set()
    for text in raw:
        name = " ".join(text.split())
        key = name.lower()
        if not 2 <= len(name) <= MAX_NAME or key in _NOT_PAGES or key in done:
            continue
        done.add(key)
        out.append(name)
    return out[:MAX_PAGES]


# ------------------------------------------------------------------ matching a feature to the pages

_STOP = {"the", "and", "of", "for", "a", "an", "to", "in", "on", "with", "by", "page", "pages", "new", "my", "your"}
_TOO_GENERAL = {"tools", "reports", "setup", "dashboard", "overview", "me", "settings", "analytics", "my team"}


def _words(text: str) -> list[str]:
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", text.lower())]


def match_pages(text: str, pages: Sequence[str], limit: int = 3) -> list[str]:
    """The pages whose whole name appears, word for word, in the feature's text (Locations matches 'Location Page
    Shows Address on Map'). A name of only general words (Tools, Reports) never matches."""
    have = set(_words(text))
    found: list[tuple[int, str]] = []
    for name in pages:
        words = [w for w in _words(name) if w not in _STOP]
        if not words or name.lower() in _TOO_GENERAL or not any(len(w) >= 4 for w in words):
            continue
        if all(w in have for w in words):
            found.append((len(words), name))
    found.sort(key=lambda x: (-x[0], x[1]))
    return [name for _, name in found[:limit]]
