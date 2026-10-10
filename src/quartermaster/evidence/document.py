"""Build the Word (.docx) evidence document for one test run.

Input is the run's `run.json` (see `quartermaster.evidence.run_record`), so a document can be
rebuilt at any time from a saved run. Only screenshots go into the document; videos stay in
the run folder and are listed by path.

A .docx file is a zip of XML parts. It is written here with the standard library only, so
producing evidence needs no extra packages.
"""

from __future__ import annotations

import re
import struct
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

# Page: US Letter, 0.75 inch margins. Word measures in twentieths of a point (twips) and
# pictures in EMU (914400 per inch).
_PAGE_W, _PAGE_H, _MARGIN = 12240, 15840, 1080
_TEXT_W = _PAGE_W - 2 * _MARGIN  # 10080 twips = 7 inches
_EMU_PER_INCH = 914400
_MAX_IMG_W_IN, _MAX_IMG_H_IN = 7.0, 8.0

_STATUS_COLOR = {"passed": "2C7A45", "healed": "A45708", "failed": "A63A32", "skipped": "6B7780"}
_STATUS_FILL = {"passed": "E3F1E7", "healed": "F8ECDD", "failed": "F6E3E1", "skipped": "EEF1F3"}
_ACTUAL = {
    "passed": "As expected.",
    "healed": "As expected. The item was found in a different way than when the test was written, so "
    "the test file should be updated.",
    "skipped": "Not done, because an earlier step failed.",
}

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def write_evidence_document(run: dict[str, Any], run_dir: Path, out: Path) -> Path:
    """Write the evidence document for `run` (the parsed run.json) to `out`."""
    doc = _Doc(run_dir)
    body = doc.build(run)
    return write_package(
        out,
        doc,
        body,
        title=f"Test evidence: {run.get('test_title') or run.get('test_id', '')} ({run.get('run_id', '')})",
        creator=str(run.get("executed_by", "")),
        subject=str(run.get("test_id", "")),
        created=run.get("finished_at") or run.get("started_at"),
        footer_left=f"{run.get('test_id', '')}  |  Run {run.get('run_id', '')}",
    )


def write_package(
    out: Path, doc: _Doc, body: str, *, title: str, creator: str, subject: str, created: str | None, footer_left: str
) -> Path:
    """Zip a document body, its images and the fixed parts into a .docx file."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types())
        z.writestr("_rels/.rels", _root_rels())
        z.writestr("docProps/core.xml", _core(title, creator, subject, created))
        z.writestr("word/document.xml", body)
        z.writestr("word/styles.xml", _styles())
        z.writestr("word/footer1.xml", _footer(footer_left))
        z.writestr("word/_rels/document.xml.rels", doc.rels())
        for name, data in doc.media:
            z.writestr(f"word/media/{name}", data)
    return out


def document_xml(parts: list[str]) -> str:
    """Wrap body parts in the document element with the page setup (US Letter) and footer."""
    sect = (
        f'<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter"/>'
        f'<w:pgSz w:w="{_PAGE_W}" w:h="{_PAGE_H}"/>'
        f'<w:pgMar w:top="{_MARGIN}" w:right="{_MARGIN}" w:bottom="{_MARGIN}" w:left="{_MARGIN}" '
        f'w:header="720" w:footer="500" w:gutter="0"/></w:sectPr>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}" xmlns:r="{_R}" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f"<w:body>{''.join(parts)}{sect}</w:body></w:document>"
    )


# --------------------------------------------------------------------------- document body


class _Doc:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.media: list[tuple[str, bytes]] = []
        self._rels: list[tuple[str, str, str]] = [
            ("rIdStyles", "styles", "styles.xml"),
            ("rIdFooter", "footer", "footer1.xml"),
        ]
        self._pic_id = 0

    def rels(self) -> str:
        base = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
        items = "".join(f'<Relationship Id="{i}" Type="{base}{t}" Target="{target}"/>' for i, t, target in self._rels)
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{items}</Relationships>"
        )

    def build(self, run: dict[str, Any]) -> str:
        steps: list[dict[str, Any]] = run.get("steps", [])
        status = str(run.get("status", "")).lower()
        counts = {k: sum(1 for s in steps if s.get("status") == k) for k in ("passed", "healed", "failed", "skipped")}
        parts: list[str] = []
        add = parts.append

        # --- page 1: what this is, the result, and the list of steps
        add(_p([_r("Test Evidence")], style="Title"))
        add(_p([_r(run.get("test_title") or run.get("test_id", ""))], style="Subtitle"))
        add(
            _p(
                [
                    _r("Result: ", bold=True, size=28),
                    _r(_status_label(status).upper(), bold=True, size=28, color=_STATUS_COLOR.get(status, "15202A")),
                ],
                after=160,
            )
        )
        add(
            _p(
                [
                    _r(
                        "This document is the record of a test done by hand in Oracle Fusion. For each step it shows "
                        "what the tester did, what should happen, whether it did, and a picture of the screen taken "
                        "when the tester marked the step."
                        if run.get("mode") == "manual"
                        else "This document is the record of a test prepared automatically: an AI followed the "
                        "written test script in Oracle Fusion. For each step it shows what was done, what should "
                        "happen, and a picture of the screen. A person should check the pictures before the test "
                        "is trusted."
                        if run.get("mode") == "ai"
                        else "This document is the record of an automated test run in Oracle Fusion. For each step it "
                        "shows what was done, what should happen, what did happen and, where taken, a picture of the "
                        "screen afterwards; a click also has a picture just before it. A red box in a picture marks "
                        "the item the step used."
                    )
                ],
                after=200,
            )
        )

        written = _written_groups(run, steps)
        if written:
            counts = {k: sum(1 for w in written if w["status"] == k) for k in ("passed", "healed", "failed", "skipped")}
        add(_p([_r("About this test")], style="Heading1"))
        passed = counts["passed"] + counts["healed"]
        about = [
            ("What was tested", run.get("test_title") or run.get("test_id", "")),
            ("Result", _status_label(status)),
            ("Steps", f"{passed} of {len(written or steps)} passed" + _step_extras(counts)),
            ("Date and time", _when(run.get("started_at"))),
            ("Time taken", _duration(run.get("started_at"), run.get("finished_at"))),
            ("Run by", run.get("executed_by") or "Not recorded"),
            ("Oracle environment", run.get("environment_url", "")),
            ("Oracle release", run.get("release") or "Not recorded"),
        ]
        if run.get("videos"):
            about.append(("Video of the test", "Saved in the videos folder next to this document"))
        add(_kv_table(about, status_row=("Result", status)))

        if run.get("setup"):
            self._setup(run, add)
        if written:
            self._written_steps(written, add)
        else:
            add(_p([_r("Steps")], style="Heading1"))
            widths = [700, 7380, 2000]
            rows = [
                [
                    _cell("Step", widths[0], header=True),
                    _cell("What was done", widths[1], header=True),
                    _cell("Result", widths[2], header=True),
                ]
            ]
            for s in steps:
                st = s.get("status", "")
                rows.append(
                    [
                        _cell(str(s.get("index", 0) + 1), widths[0]),
                        _cell(s.get("intent", ""), widths[1]),
                        _cell(
                            _status_label(st),
                            widths[2],
                            fill=_STATUS_FILL.get(st),
                            color=_STATUS_COLOR.get(st),
                            bold=True,
                        ),
                    ]
                )
            add(_table(rows, widths))

            # --- one block per step; a step with a picture starts on a new page
            if any(s.get("evidence") for s in steps):
                add(_page_break())
            add(_p([_r("Step details")], style="Heading1"))
            first = True
            for s in steps:
                n = s.get("index", 0) + 1
                st = s.get("status", "")
                shots = s.get("evidence", [])
                if shots and not first:
                    add(_page_break())
                first = False
                add(_p([_r(f"Step {n}: {s.get('intent', '')}")], style="Heading2"))
                rows_kv: list[tuple[str, Any]] = [("Result", _status_label(st))]
                if s.get("action") == "manual" and st == "passed":
                    by = "done by the AI" if run.get("mode") == "ai" else "marked by the tester"
                    rows_kv[0] = ("Result", f"Passed ({by})")
                if s.get("value") and s.get("action") in ("fill", "select", "navigate", "login_as"):
                    rows_kv.append(("Value entered" if s.get("action") in ("fill", "select") else "Opened", s["value"]))
                rows_kv.append(("What should happen", s.get("expected") or _default_expected(s)))
                rows_kv.append(
                    ("What happened", plain_error(s.get("error")) if s.get("error") else _ACTUAL.get(st, ""))
                )
                tries = int(s.get("attempts") or 1)
                if tries > 1:
                    rows_kv.append(("Attempts", _attempts_text(tries, st, s.get("first_error"))))
                if st != "skipped":
                    rows_kv.append(("Time", _clock(s.get("started_at"))))
                if s.get("screenshot_note") and not [r for r in shots if not _is_before(r)]:
                    rows_kv.append(("Screen picture", s["screenshot_note"]))  # says why the picture is missing
                add(_kv_table(rows_kv, status_row=("Result", st)))
                for rel in shots:
                    caption = (
                        f"Before step {n}: the item about to be clicked is boxed in red"
                        if _is_before(rel)
                        else f"Screen after step {n}"
                    )
                    add(self._image(rel, n, "", caption=caption))

        # --- cleanup of the data the test made (never part of the result above)
        if run.get("cleanup"):
            self._cleanup(run, add)

        # --- sign-off, then the technical appendix
        add(_page_break())
        add(_p([_r("Sign-off")], style="Heading1"))
        add(_p([_r("By signing, the reviewer confirms this document is a true record of the test run above.")]))
        sw = [2200, 3080, 2800, 2000]
        sign = [[_cell(h, w, header=True) for h, w in zip(("Role", "Name", "Signature", "Date"), sw, strict=True)]]
        for role, name in (("Run by", run.get("executed_by", "")), ("Reviewed by", ""), ("Approved by", "")):
            sign.append([_cell(role, sw[0], bold=True), _cell(name, sw[1]), _cell("", sw[2]), _cell("", sw[3])])
        add(_table(sign, sw, row_height=620))

        return document_xml(parts)

    def _setup(self, run: dict[str, Any], add: Any) -> None:
        """What was checked or made on the pod before the steps, so the record shows the pod was ready."""
        add(_p([_r("Setup before the test")], style="Heading1"))
        failed = run.get("setup_status") == "failed"
        said = (
            "A setup step did not pass, so the test steps were not run. The pod's data was not ready."
            if failed
            else "Everything the test needed was checked or made on the pod before the steps began."
        )
        add(_p([_r(said)], after=160))
        widths = [700, 4380, 1800, 3200]
        rows = [
            [
                _cell("Step", widths[0], header=True),
                _cell("What was checked or made", widths[1], header=True),
                _cell("Result", widths[2], header=True),
                _cell("Note", widths[3], header=True),
            ]
        ]
        for c in run["setup"]:
            st = c.get("status", "")
            note = plain_error(c["error"]) if c.get("error") else c.get("note") or ""
            label = "Not run" if st == "skipped" else _status_label(st)
            rows.append(
                [
                    _cell(str(c.get("index", 0) + 1), widths[0]),
                    _cell(c.get("intent", ""), widths[1]),
                    _cell(label, widths[2], fill=_STATUS_FILL.get(st), color=_STATUS_COLOR.get(st), bold=True),
                    _cell(note, widths[3]),
                ]
            )
        add(_table(rows, widths))

    def _cleanup(self, run: dict[str, Any], add: Any) -> None:
        """What was removed from the pod after the test, and what could not be."""
        add(_p([_r("Cleanup of test data")], style="Heading1"))
        status = run.get("cleanup_status", "")
        said = {
            "done": "Everything the test made was cleaned up.",
            "partial": "Some cleanup steps failed: records made by this test may still be on the pod.",
            "failed": "The cleanup failed: records made by this test may still be on the pod.",
        }.get(status, "")
        add(_p([_r(said + " This does not change the result of the test above.")], after=160))
        widths = [700, 4380, 1800, 3200]
        rows = [
            [
                _cell("Step", widths[0], header=True),
                _cell("What was done", widths[1], header=True),
                _cell("Result", widths[2], header=True),
                _cell("Note", widths[3], header=True),
            ]
        ]
        for c in run["cleanup"]:
            st = c.get("status", "")
            note = plain_error(c["error"]) if c.get("error") else c.get("note") or ""
            label = "Nothing to clean" if st == "skipped" else _status_label(st)
            rows.append(
                [
                    _cell(str(c.get("index", 0) + 1), widths[0]),
                    _cell(c.get("intent", ""), widths[1]),
                    _cell(label, widths[2], fill=_STATUS_FILL.get(st), color=_STATUS_COLOR.get(st), bold=True),
                    _cell(note, widths[3]),
                ]
            )
        add(_table(rows, widths))

    def _written_steps(self, written: list[dict[str, Any]], add: Any) -> None:
        """The steps of the written test script, each with what was done for it and its pictures."""
        add(_p([_r("Steps")], style="Heading1"))
        add(
            _p(
                [
                    _r(
                        "These are the steps of the written test script. Under each one are the actions "
                        "Quartermaster did for it, with their pictures."
                    )
                ]
            )
        )
        widths = [700, 7380, 2000]
        rows = [
            [
                _cell("Step", widths[0], header=True),
                _cell("What the script says", widths[1], header=True),
                _cell("Result", widths[2], header=True),
            ]
        ]
        for w in written:
            st = w["status"]
            rows.append(
                [
                    _cell(str(w["number"]), widths[0]),
                    _cell(w["action"], widths[1]),
                    _cell(
                        _status_label(st), widths[2], fill=_STATUS_FILL.get(st), color=_STATUS_COLOR.get(st), bold=True
                    ),
                ]
            )
        add(_table(rows, widths))
        if any(a.get("evidence") for w in written for a in w["actions"]):
            add(_page_break())
        add(_p([_r("Step details")], style="Heading1"))
        first = True
        for w in written:
            n, st = w["number"], w["status"]
            shots = [r for a in w["actions"] for r in a.get("evidence", [])]
            if shots and not first:
                add(_page_break())
            first = False
            add(_p([_r(f"Step {n}: {w['action']}")], style="Heading2"))
            how = "; ".join(a.get("intent", "") for a in w["actions"]) or w["note"]
            rows_kv: list[tuple[str, Any]] = [
                ("Result", _status_label(st)),
                ("What should happen", w["expected"] or "Not written in the script."),
                ("What was done", how),
            ]
            failed = next((a for a in w["actions"] if a.get("status") == "failed"), None)
            if failed is not None:
                rows_kv.append(("What happened", plain_error(failed.get("error")) or _ACTUAL["failed"]))
            retried = next((a for a in w["actions"] if int(a.get("attempts") or 1) > 1), None)
            if retried is not None:
                rows_kv.append(
                    (
                        "Attempts",
                        _attempts_text(int(retried["attempts"]), retried.get("status", ""), retried.get("first_error")),
                    )
                )
            add(_kv_table(rows_kv, status_row=("Result", st)))
            for a in w["actions"]:
                for rel in a.get("evidence", []):
                    what = a.get("intent", "")
                    caption = (
                        f"Step {n}, before \u201c{what}\u201d: the item about to be clicked is boxed in red"
                        if _is_before(rel)
                        else f"Step {n}, after \u201c{what}\u201d"
                    )
                    add(self._image(rel, n, "", caption=caption))

    def _image(self, rel: str, step_no: int, sha256: str, caption: str | None = None) -> str:
        path = self.run_dir / rel
        try:
            data = path.read_bytes()
            w_px, h_px = _png_size(data)
        except (OSError, ValueError):
            return _p([_r(f"Screenshot missing: {rel}", italic=True, color="A63A32")])
        self._pic_id += 1
        rid = f"rIdImg{self._pic_id}"
        name = f"image{self._pic_id}.png"
        self.media.append((name, data))
        self._rels.append((rid, "image", f"media/{name}"))
        w_in = _MAX_IMG_W_IN
        h_in = w_in * h_px / w_px
        if h_in > _MAX_IMG_H_IN:
            w_in, h_in = w_in * _MAX_IMG_H_IN / h_in, _MAX_IMG_H_IN
        cx, cy = int(w_in * _EMU_PER_INCH), int(h_in * _EMU_PER_INCH)
        pid = self._pic_id
        drawing = (
            f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{pid}" name="Screenshot {pid}" '
            f'descr="Screenshot after step {step_no}"/>'
            f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{pid}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            f'<a:ln w="9525"><a:solidFill><a:srgbClr val="B8C3C9"/></a:solidFill></a:ln></pic:spPr>'
            f"</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
        )
        caption = caption or f"Figure {step_no}. Screen after step {step_no}. File {rel}"
        if sha256:
            caption += f", SHA-256 {sha256}"
        return f'<w:p><w:pPr><w:spacing w:before="200" w:after="60"/></w:pPr>{drawing}</w:p>' + _p(
            [_r(caption)], style="Caption"
        )


# --------------------------------------------------------------------------- XML helpers


def _r(
    text: str,
    *,
    bold: bool = False,
    italic: bool = False,
    color: str | None = None,
    size: int | None = None,
    mono: bool = False,
) -> str:
    props = ""
    if mono:
        props += '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>'
    if bold:
        props += "<w:b/>"
    if italic:
        props += "<w:i/>"
    if color:
        props += f'<w:color w:val="{color}"/>'
    if size:
        props += f'<w:sz w:val="{size}"/>'
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(str(text))}</w:t></w:r>'


def _p(
    runs: list[str],
    *,
    style: str | None = None,
    before: int | None = None,
    after: int | None = None,
    indent: int | None = None,
) -> str:
    ppr = ""
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if before is not None or after is not None:
        ppr += (
            "<w:spacing"
            + (f' w:before="{before}"' if before is not None else "")
            + (f' w:after="{after}"' if after is not None else "")
            + "/>"
        )
    if indent:
        ppr += f'<w:ind w:left="{indent}"/>'
    return f"<w:p>{f'<w:pPr>{ppr}</w:pPr>' if ppr else ''}{''.join(runs)}</w:p>"


def _page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def _cell(
    text: str,
    width: int,
    *,
    header: bool = False,
    fill: str | None = None,
    color: str | None = None,
    bold: bool = False,
) -> str:
    shade = fill or ("E6EDF0" if header else None)
    tcpr = f'<w:tcW w:w="{width}" w:type="dxa"/>'
    if shade:
        tcpr += f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>'
    run = _r(text, bold=bold or header, color=color, size=18 if header else None)
    return f"<w:tc><w:tcPr>{tcpr}</w:tcPr>{_p([run], after=0)}</w:tc>"


def _table(rows: list[list[str]], widths: list[int], *, row_height: int | None = None) -> str:
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    border = '<w:{0} w:val="single" w:sz="4" w:space="0" w:color="C9D3D8"/>'
    borders = "".join(border.format(b) for b in ("top", "left", "bottom", "right", "insideH", "insideV"))
    trpr = f'<w:trPr><w:trHeight w:val="{row_height}"/></w:trPr>' if row_height else ""
    header_pr = "<w:trPr><w:tblHeader/></w:trPr>"
    body = "".join(
        f"<w:tr>{header_pr if i == 0 and not row_height else trpr}{''.join(r)}</w:tr>" for i, r in enumerate(rows)
    )
    return (
        f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblBorders>{borders}</w:tblBorders>'
        f'<w:tblLayout w:type="fixed"/><w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
        f'<w:bottom w:w="60" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr>'
        f"<w:tblGrid>{grid}</w:tblGrid>{body}</w:tbl>" + _p([], after=120)
    )


def _kv_table(pairs: list[tuple[str, Any]], status_row: tuple[str, str] | None = None) -> str:
    widths = [2600, _TEXT_W - 2600]
    rows = []
    for k, v in pairs:
        if status_row and k == status_row[0]:
            st = status_row[1]
            value = _cell(str(v), widths[1], fill=_STATUS_FILL.get(st), color=_STATUS_COLOR.get(st), bold=True)
        else:
            value = _cell(str(v), widths[1])
        rows.append([_cell(k, widths[0], header=True), value])
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    border = '<w:{0} w:val="single" w:sz="4" w:space="0" w:color="C9D3D8"/>'
    borders = "".join(border.format(b) for b in ("top", "left", "bottom", "right", "insideH", "insideV"))
    body = "".join(f"<w:tr><w:trPr><w:cantSplit/></w:trPr>{''.join(r)}</w:tr>" for r in rows)
    return (
        f'<w:tbl><w:tblPr><w:tblW w:w="{_TEXT_W}" w:type="dxa"/><w:tblBorders>{borders}</w:tblBorders>'
        f'<w:tblLayout w:type="fixed"/><w:tblCellMar><w:top w:w="50" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
        f'<w:bottom w:w="50" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr>'
        f"<w:tblGrid>{grid}</w:tblGrid>{body}</w:tbl>" + _p([], after=60)
    )


# --------------------------------------------------------------------------- package parts


def _content_types() -> str:
    main = "application/vnd.openxmlformats-officedocument.wordprocessingml"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        f'<Override PartName="/word/document.xml" ContentType="{main}.document.main+xml"/>'
        f'<Override PartName="/word/styles.xml" ContentType="{main}.styles+xml"/>'
        f'<Override PartName="/word/footer1.xml" ContentType="{main}.footer+xml"/>'
        '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        "</Types>"
    )


def _root_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
        'Target="docProps/core.xml"/></Relationships>'
    )


def _core(title: str, creator: str, subject: str, created: str | None) -> str:
    created = created or datetime.now().astimezone().isoformat()
    try:
        created_utc = datetime.fromisoformat(created).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        created_utc = created_utc[:-2] + ":" + created_utc[-2:]
    except ValueError:
        pass
    else:
        created = created_utc
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{escape(title)}</dc:title><dc:creator>{escape(creator)}</dc:creator>"
        f"<dc:subject>{escape(subject)}</dc:subject>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{escape(created)}</dcterms:created>'
        "</cp:coreProperties>"
    )


def _footer(left_text: str) -> str:
    def field(code: str) -> str:
        return (
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            f'<w:r><w:instrText xml:space="preserve"> {code} </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        )

    left = escape(left_text)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:ftr xmlns:w="{_W}" xmlns:r="{_R}"><w:p><w:pPr><w:pStyle w:val="Footer"/>'
        f'<w:tabs><w:tab w:val="right" w:pos="{_TEXT_W}"/></w:tabs></w:pPr>'
        f'<w:r><w:t xml:space="preserve">{left}</w:t></w:r><w:r><w:tab/></w:r>'
        f'<w:r><w:t xml:space="preserve">Page </w:t></w:r>{field("PAGE")}'
        f'<w:r><w:t xml:space="preserve"> of </w:t></w:r>{field("NUMPAGES")}</w:p></w:ftr>'
    )


def _styles() -> str:
    def para_style(
        sid: str,
        name: str,
        size: int,
        *,
        bold: bool = False,
        color: str = "15202A",
        before: int = 0,
        after: int = 120,
        outline: int | None = None,
        italic: bool = False,
    ) -> str:
        ol = f'<w:outlineLvl w:val="{outline}"/>' if outline is not None else ""
        return (
            f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/><w:basedOn w:val="Normal"/>'
            '<w:next w:val="Normal"/><w:qFormat/>'
            f'<w:pPr><w:keepNext/><w:spacing w:before="{before}" w:after="{after}"/>'
            f"{ol}</w:pPr><w:rPr>{'<w:b/>' if bold else ''}{'<w:i/>' if italic else ''}"
            f'<w:color w:val="{color}"/><w:sz w:val="{size}"/></w:rPr></w:style>'
        )

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:styles xmlns:w="{_W}">'
        '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri" '
        'w:eastAsia="Calibri"/><w:sz w:val="20"/><w:szCs w:val="20"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>'
        '<w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
        "</w:docDefaults>"
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>'
        '<w:rPr><w:color w:val="15202A"/></w:rPr></w:style>'
        + para_style("Title", "Title", 44, bold=True, color="0E6B70", after=60)
        + para_style("Subtitle", "Subtitle", 28, color="3F4B55", after=160)
        + para_style("Heading1", "heading 1", 28, bold=True, color="0E6B70", before=280, after=120, outline=0)
        + para_style("Heading2", "heading 2", 24, bold=True, before=0, after=120, outline=1)
        + para_style("Caption", "caption", 16, color="56646F", after=120, italic=True)
        + para_style("Footer", "footer", 16, color="56646F", after=0)
        + "</w:styles>"
    )


# --------------------------------------------------------------------------- small helpers


def _png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a PNG file")
    w, h = struct.unpack(">II", data[16:24])
    if not w or not h:
        raise ValueError("empty PNG")
    return w, h


def _when(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y, %H:%M:%S %Z").strip().rstrip(",")
    except ValueError:
        return iso


def _duration(start: str | None, end: str | None) -> str:
    try:
        secs = (datetime.fromisoformat(end or "") - datetime.fromisoformat(start or "")).total_seconds()
    except ValueError:
        return ""
    m, s = divmod(int(round(secs)), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h} h {m} min"
    return f"{m} min {s} s" if m else f"{s} s"


def _attempts_text(tries: int, status: str, first_error: str | None) -> str:
    """A step that was tried more than once: said in the evidence, because a pass on a retry is not a clean pass."""
    if status in ("passed", "healed"):
        said = f"{tries} (passed on attempt {tries})"
        return said + (f". The first attempt failed: {plain_error(first_error)}" if first_error else "")
    return f"{tries} (every attempt failed)"


def _status_label(status: str) -> str:
    return {
        "passed": "Passed",
        "healed": "Passed, test needs an update",
        "failed": "Failed",
        "skipped": "Not done",
    }.get(status, status.capitalize())


def _step_extras(counts: dict[str, int]) -> str:
    extra = []
    if counts.get("failed"):
        extra.append(f"{counts['failed']} failed")
    if counts.get("skipped"):
        extra.append(f"{counts['skipped']} not done")
    return f" ({', '.join(extra)})" if extra else ""


def _clock(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M:%S")
    except ValueError:
        return iso


def plain_error(error: str | None) -> str:
    """Say what went wrong in everyday words. The original message stays in run.json."""
    if not error:
        return ""
    text = " ".join(error.split())
    if text.startswith("Tester: "):  # written by the person who did the test: shown as they wrote it
        return text[len("Tester: ") :]
    m = re.search(r"expected text '(.*)', found '(.*)'$", text)
    m = m or re.search(r'expected text "(.*)", found "(.*)"$', text)
    if m:
        found = m.group(2).strip()
        return (
            f'The screen showed "{found}" but it should show "{m.group(1)}".'
            if found
            else (f'The screen was empty where it should show "{m.group(1)}".')
        )
    matches = [int(n) for n in re.findall(r"matched (\d+)", text)]
    if matches and all(n == 0 for n in matches):
        return "The item could not be found on the screen."
    if matches and all(n >= 2 for n in matches):
        return "More than one matching item was on the screen, so the test could not tell which one to use."
    if matches:
        return "The item could not be found on the screen, or more than one matched."
    if "no suggestion matches" in text:
        return "The value to choose was not among the suggestions the screen offered."
    if "Timeout" in text:
        return "The screen did not respond in time."
    ended = re.search(r"scheduled process ended (.+?), expected (\w+)", text)
    if ended:
        return f"The scheduled process ended with status {ended.group(1)}; it should have ended {ended.group(2)}."
    if "ended" in text and "job" in text:
        return "The scheduled process did not finish successfully."
    if "the API answered" in text or "in the API reply" in text or "the API reply has" in text:
        said = text.split("StepFailure: ", 1)[-1]  # written in plain words by the REST step
        return said[0].upper() + said[1:] + ("" if said.endswith(".") else ".")
    for plain in ("a REST step may only call", "write the request as"):
        if plain in text:
            said = text[text.index(plain) :]
            return said[0].upper() + said[1:]
    for plain in ("No process number is shown", "The pod refused the status check", "The status check of process"):
        if plain in text:
            return text[text.index(plain) :]
    return "The step could not be completed. The test team has the full error message in the run record."


def _default_expected(step: dict[str, Any]) -> str:
    action, value = step.get("action", ""), step.get("value") or ""
    return {
        "navigate": "The page opens.",
        "click": "The click works.",
        "fill": f'"{value}" can be entered.' if value else "The value can be entered.",
        "select": f'"{value}" can be chosen.' if value else "The value can be chosen.",
        "assert_visible": "It is shown on the screen.",
        "assert_text": f'It shows "{value}".' if value else "It shows the expected text.",
        "login_as": f"Signed in as {value}." if value else "Signed in.",
        "wait_job": "The scheduled process finishes successfully.",
        "api_call": "The service answers, and its reply has the expected values.",
    }.get(action, "The step completes.")


def _is_before(rel: str) -> bool:
    """A picture taken just before a click (see the runner), not after the step."""
    return Path(rel).stem.endswith("-before")


_SIGN_IN = re.compile(r"^\s*(log\s*-?\s*in|sign\s*-?\s*in)\b", re.I)
_WORST = ("failed", "skipped", "healed", "passed")


def _written_groups(run: dict[str, Any], steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For a test made from a manual scenario (run["written_steps"]): each written step with the
    actions done for it (step["written_step"]), its result, and, when it needed no action of its
    own, why. Empty for other tests, and for runs done by hand or by the AI (their steps are the
    written steps already)."""
    written = run.get("written_steps") or []
    if not written or run.get("mode") in ("manual", "ai"):
        return []
    groups: list[dict[str, Any]] = [
        {
            "number": i + 1,
            "action": str(w.get("action", "")),
            "expected": str(w.get("expected", "")),
            "actions": [],
        }
        for i, w in enumerate(written)
    ]
    current = 1
    for st in steps:
        n = st.get("written_step")
        if isinstance(n, int) and 1 <= n <= len(groups):
            current = n
        groups[current - 1]["actions"].append(st)  # an action without a number goes with the one before
    for i, g in enumerate(groups):
        if g["actions"]:
            found = {a.get("status") for a in g["actions"]}
            g["status"] = next((k for k in _WORST if k in found), "passed")
            g["note"] = ""
            continue
        later = next((h for h in groups[i + 1 :] if h["actions"]), None)
        if i == 0 and _SIGN_IN.match(g["action"]):
            g["status"], g["note"] = "passed", "Quartermaster signed in to Oracle Fusion before the test started."
        elif later is not None:
            first = later["actions"][0]
            g["status"] = "skipped" if first.get("status") == "skipped" else "passed"
            g["note"] = f"No separate action: done as part of step {later['number']} ({first.get('intent', '')})."
        else:
            g["status"], g["note"] = "passed", "No separate action was needed."
    return groups
