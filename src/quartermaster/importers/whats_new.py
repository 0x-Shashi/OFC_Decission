"""Read an Oracle "What's New" page you saved (or pasted text) into the features of a release.

Quartermaster never goes to Oracle's site itself. You save the What's New page from your browser (File, Save
page as, "Webpage, HTML only") or copy its text, and give it here. Nothing is guessed by an AI:

1. The page is cut into blocks: headings, paragraphs, list items and table rows.
2. A table with a Feature column (Oracle's feature summary) gives the list of features, with the columns it has
   (product, ready for use, customer action). The nearest headings above the table tell the product and module.
3. Each feature's own section (a heading with the feature's name, and the text under it until the next heading
   of the same or a higher level) gives the longer description, which is what helps match features to tests.
   "Customer must take action", "disabled by default" and "steps to enable" mark a feature as opt-in.
4. A page with no such table is read by its headings instead: the deepest headings that have text under them are
   the features, the headings above them the product and module.

What was found, and how, is shown for a person to check before anything is saved (the same preview as the
spreadsheet import), and the description of each feature is the page's own text, never a summary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from quartermaster.importers.release_sheet import (
    _MODULES,
    RELEASE_ID,
    ImportError_,
    ReleaseImport,
    _change_type,
    _find_header,
    _norm,
    _truthy,
)

MAX_DESCRIPTION = 1200
_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "noscript", "svg", "head"}
_BLOCK_TAGS = {"p", "li", "div", "section", "article", "ul", "ol", "br", "tr", "table", "dd", "dt", "pre"}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# Headings that say what a part of the page is, not what product or feature it is about.
_LABELS = {
    "overview",
    "feature summary",
    "features",
    "feature details",
    "details",
    "what s new",
    "whats new",
    "table of contents",
    "contents",
    "revision history",
    "update tasks",
    "key resources",
    "tips and considerations",
    "steps to enable and configure",
    "steps to enable",
    "role information",
    "business benefits",
    "benefits",
    "important actions and considerations",
    "release readiness",
    "documentation",
    "feature information",
}
_DETAIL_LABELS = {
    "steps to enable and configure",
    "steps to enable",
    "tips and considerations",
    "key resources",
    "role information",
    "business benefits",
    "benefits",
    "feature information",
}
_OPT_IN_PHRASES = (
    "customer must take action",
    "disabled by default",
    "opt in",
    "opt-in",
    "must be enabled",
    "needs to be enabled",
)
_NO_ACTION = (
    "don't need to do anything",
    "do not need to do anything",
    "no action is required",
    "no steps are required",
)
_STEPS = {"steps to enable and configure", "steps to enable"}


@dataclass
class Block:
    kind: str  # "h", "p" or "row"
    text: str = ""
    level: int = 0  # headings: 1 to 6
    cells: list[str] = field(default_factory=list)  # rows
    table: int = 0  # rows: which table of the page


class _Page(HTMLParser):
    """Headings, paragraphs and table rows of an HTML page, in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.title = ""
        self._skip = 0
        self._buf: list[str] = []
        self._heading = 0
        self._in_title = False
        self._table = 0
        self._depth = 0  # nested tables are read as their own rows
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in _SKIP_TAGS and tag != "head":
            self._skip += 1
        if self._skip:
            return
        if tag in _HEADINGS:
            self._flush()
            self._heading = int(tag[1])
        elif tag == "table":
            self._flush()
            self._table += 1
            self._depth += 1
        elif tag == "tr":
            self._flush()
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "img" and self._cell is not None:
            alt = dict(attrs).get("alt")
            if alt:
                self._cell.append(f" {alt} ")
        elif tag in _BLOCK_TAGS and self._cell is None:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS and tag != "head":
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in _HEADINGS:
            text = _clean("".join(self._buf))
            self._buf = []
            if text and self._heading:
                self.blocks.append(Block("h", text, self._heading))
            self._heading = 0
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.blocks.append(Block("row", cells=self._row, table=self._table))
            self._row = None
        elif tag == "table":
            self._depth = max(0, self._depth - 1)
        elif tag in _BLOCK_TAGS and self._cell is None:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._skip:
            return
        if self._cell is not None:
            self._cell.append(data)
        else:
            self._buf.append(data)

    def _flush(self) -> None:
        if self._heading:
            return  # a heading is closed by its own end tag
        text = _clean("".join(self._buf))
        self._buf = []
        if text:
            self.blocks.append(Block("p", text))

    def close(self) -> None:
        super().close()
        self._flush()


def _clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def blocks_from_html(html: str) -> tuple[list[Block], str]:
    page = _Page()
    page.feed(html)
    page.close()
    return page.blocks, _clean(page.title)


def blocks_from_text(text: str) -> tuple[list[Block], str]:
    """Pasted text or Markdown: `#` lines are headings, tab-separated lines are table rows, and a short line without a
    full stop that is followed by a longer line is taken as a heading."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    items = [(ln, ln.strip()) for ln in lines if ln.strip()]

    def guess(k: int) -> bool:
        """Short, no full stop, and the next line is longer (or another such line)."""
        line = items[k][1]
        if len(line) > 90 or line.endswith((".", ":", ";", ",")) or "\t" in items[k][0] or line.startswith("#"):
            return False
        if k + 1 >= len(items):
            return False
        nxt = items[k + 1][1]
        return len(nxt) > len(line) + 20 or "\t" in items[k + 1][0] or guess(k + 1)

    blocks: list[Block] = []
    table = 0
    previous_row = False
    for k, (raw, line) in enumerate(items):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            blocks.append(Block("h", _clean(m.group(2)), len(m.group(1))))
            previous_row = False
        elif "\t" in raw:
            if not previous_row:
                table += 1
            blocks.append(Block("row", cells=[_clean(c) for c in raw.split("\t")], table=table))
            previous_row = True
        else:
            previous_row = False
            if guess(k):
                # a name followed by a text is a feature; a name followed by another name is a product or module
                following_is_name = k + 1 < len(items) and guess(k + 1)
                blocks.append(Block("h", line, 3 if following_is_name else 5))
            else:
                blocks.append(Block("p", _clean(line)))
    first = next((b.text for b in blocks if b.kind == "h"), "")
    return blocks, first


# ---------------------------------------------------------------------- reading the blocks


def parse_whats_new(name: str, content: bytes, release_id: str = "") -> ReleaseImport:
    """`name` picks HTML (.html, .htm) or text (.txt, .md); `release_id` is read from the page when not given."""
    text = content.decode("utf-8-sig", errors="replace")
    if name.lower().endswith((".html", ".htm")):
        blocks, title = blocks_from_html(text)
    else:
        blocks, title = blocks_from_text(text)
    if not blocks:
        raise ImportError_("nothing readable was found in that file")
    rid = release_id.strip().upper()
    if not RELEASE_ID.match(rid):
        found = _release_in(title) or _release_in(" ".join(b.text for b in blocks if b.kind == "h")[:2000])
        if not found:
            raise ImportError_("enter the release id this page is for, like 26D (two digits and A to D)")
        rid = found
    return _features(blocks, rid)


def _release_in(text: str) -> str:
    m = re.search(r"(?<![0-9A-Za-z])(\d{2}[A-D])(?![A-Za-z0-9])", text.upper())
    return m.group(1) if m else ""


def _features(blocks: list[Block], rid: str) -> ReleaseImport:
    skipped: list[str] = []
    features: list[dict[str, Any]] = []
    how: dict[str, str] = {}

    tables: dict[int, list[Block]] = {}
    for b in blocks:
        if b.kind == "row":
            tables.setdefault(b.table, []).append(b)

    seen: set[str] = set()
    for number, rows in tables.items():
        cells = [r.cells for r in rows]
        header_at, cols = _find_header(cells)
        if header_at < 0:
            continue
        headers = cells[header_at]
        for f, c in cols.items():
            how.setdefault(f, f"{headers[c]} (table)")
        product, module = _context(blocks, rows[0])
        for n, r in enumerate(rows[header_at + 1 :], start=header_at + 2):
            row = r.cells

            def get(f: str, row: list[str] = row, cols: dict[str, int] = cols) -> str:
                return row[cols[f]].strip() if f in cols and cols[f] < len(row) else ""

            title = _clean(get("title"))
            if not title or _norm(title) in seen:
                continue
            update = get("update").upper()
            if update and rid not in update:
                skipped.append(f"table {number} row {n}: for update {update}, not {rid}")
                continue
            seen.add(_norm(title))
            row_product = get("product") or product
            row_module = _MODULES.get(_norm(get("module") or ""), get("module")) or module or row_product
            if not row_product:
                skipped.append(f"table {number} row {n}: no product found for '{title[:60]}'")
                continue
            action = _needs_action(get("action"), get("ready"))
            features.append(
                {
                    "title": title,
                    "product": row_product,
                    "module": row_module,
                    "description": _clean(get("description")),
                    "opt_in": action,
                    "customer_action_required": action,
                    "change_type": _change_type(get("change_type")) if get("change_type") else "",
                    "tags": ["redwood"] if _truthy(get("redwood")) else [],
                }
            )
    if features:
        how["description"] = "the text under each feature's heading"
        _add_sections(blocks, features)
    else:
        how = {"title": "headings with text under them"}
        features = _from_headings(blocks, skipped)
    if not features:
        raise ImportError_(
            "no features were found. Save the page as HTML (Webpage, HTML only) with its feature tables, "
            "or use the Readiness spreadsheet"
        )
    for i, item in enumerate(features, 1):
        item["change_type"] = item["change_type"] or "UI"
        item["id"] = f"{rid}-{i:03d}"
        item["description"] = item["description"][:MAX_DESCRIPTION]
        item["tags"] = sorted(set(item["tags"]))
    return ReleaseImport({"id": rid, "features": features}, how, skipped, len(features))


def _add_sections(blocks: list[Block], features: list[dict[str, Any]]) -> None:
    """Fill in each feature from the section under the heading that carries its name."""
    by_title = {_norm(f["title"]): f for f in features}
    i = 0
    while i < len(blocks):
        b = blocks[i]
        feature = by_title.get(_norm(b.text)) if b.kind == "h" else None
        if feature is None:
            i += 1
            continue
        body: list[str] = []
        steps: list[str] = []
        in_steps = False
        j = i + 1
        while j < len(blocks) and not (blocks[j].kind == "h" and blocks[j].level <= b.level):
            nxt = blocks[j]
            if nxt.kind == "h":
                in_steps = _norm(nxt.text) in _STEPS
                if _norm(nxt.text) not in _DETAIL_LABELS:
                    body.append(nxt.text)
            elif nxt.kind == "p":
                (steps if in_steps else body).append(nxt.text)
            j += 1
        _apply_text(feature, " ".join(body))
        if steps and not any(p in " ".join(steps).lower() for p in _NO_ACTION):
            feature["opt_in"] = feature["customer_action_required"] = True  # there are steps to follow to use it
        i = j


def _apply_text(feature: dict[str, Any], text: str) -> None:
    if not text:
        return
    low = text.lower()
    own = feature.get("description") or ""
    feature["description"] = _clean(f"{own} {text}" if own and own not in text else text)
    if any(p in low for p in _OPT_IN_PHRASES) and not any(p in low for p in _NO_ACTION):
        feature["opt_in"] = feature["customer_action_required"] = True
    if "redwood" in low or "redwood" in feature["title"].lower():
        feature["tags"] = [*feature["tags"], "redwood"]
    if not feature["change_type"]:
        feature["change_type"] = _kind_of(f"{feature['title']} {text}")


def _needs_action(action: str, ready: str) -> bool:
    """Does the customer have to do something before using the feature? The action column says it when it can; else
    a Ready for use column that is filled in but does not say ready means yes."""
    if action.strip():
        return _truthy(action) or "must take action" in action.lower()
    low = ready.lower()
    return bool(low.strip()) and not (_truthy(ready) or ("ready" in low and "not ready" not in low))


def _kind_of(text: str) -> str:
    """UI, PROCESS, BOTH, REPORT or API from the words of the feature's text."""
    low = text.lower()
    api = bool(re.search(r"\b(rest|soap|api|web service|web services)\b", low))
    report = bool(re.search(r"\b(reports?|analytics|otbi|bi publisher)\b", low))
    process = bool(re.search(r"\b(process|approval|approvals|workflow|scheduled|batch)\b", low))
    screen = bool(re.search(r"\b(page|screen|ui|dashboard|tile|button|field)\b", low))
    if api and process:
        return "BOTH"
    if api:
        return "API"
    if report:
        return "REPORT"
    if process and screen:
        return "BOTH"
    return "PROCESS" if process else "UI"


def _from_headings(blocks: list[Block], skipped: list[str]) -> list[dict[str, Any]]:
    """No feature table: the deepest headings that have text under them are the features."""
    with_text: list[int] = []
    for i, b in enumerate(blocks):
        if b.kind == "h" and _norm(b.text) not in _LABELS and i + 1 < len(blocks) and blocks[i + 1].kind == "p":
            with_text.append(b.level)
    if not with_text:
        return []
    level = max(with_text)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, b in enumerate(blocks):
        if b.kind != "h" or b.level != level or _norm(b.text) in _LABELS or _norm(b.text) in seen:
            continue
        j = i + 1
        body: list[str] = []
        while j < len(blocks) and not (blocks[j].kind == "h" and blocks[j].level <= level):
            if blocks[j].kind == "p":
                body.append(blocks[j].text)
            j += 1
        if not body:
            skipped.append(f"heading '{b.text[:60]}' has no text under it")
            continue
        product, module = _context(blocks, b)
        if not product:
            skipped.append(f"no product heading above '{b.text[:60]}'")
            continue
        seen.add(_norm(b.text))
        feature = {
            "title": b.text,
            "product": product,
            "module": module or product,
            "description": "",
            "opt_in": False,
            "customer_action_required": False,
            "change_type": "",
            "tags": [],
        }
        _apply_text(feature, " ".join(body))
        out.append(feature)
    return out


def _context(blocks: list[Block], at: Block) -> tuple[str, str]:
    """(product, module) from the headings above `at`: the nearest heading that is not a label is the product, and
    the nearest one that names an Oracle pillar is the module."""
    stack: dict[int, str] = {}
    for b in blocks:
        if b is at:
            for lvl in [lvl for lvl in stack if at.kind == "h" and lvl >= at.level]:
                del stack[lvl]  # a heading's own siblings are not its context
            break
        if b.kind == "h":
            for lvl in [lvl for lvl in stack if lvl >= b.level]:
                del stack[lvl]
            stack[b.level] = b.text
    ordered = [stack[lvl] for lvl in sorted(stack, reverse=True)]  # nearest (deepest) first
    useful = [t for t in ordered if _norm(t) not in _LABELS and not _release_title(t)]
    module = next((_MODULES[_norm(t)] for t in useful if _norm(t) in _MODULES), "")
    product = next((t for t in useful if _norm(t) not in _MODULES), "") or (useful[0] if useful else "")
    return product, module


def _release_title(text: str) -> bool:
    return bool(re.search(r"(?<![0-9A-Za-z])\d{2}[A-D](?![A-Za-z0-9])", text.upper())) and len(text) < 80
