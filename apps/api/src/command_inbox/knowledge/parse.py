"""Document parsers: file bytes → blocks of text with their section path and page.

Formats: PDF (text layer, per page), DOCX (headings become sections, tables become rows), XLSX (one section
per sheet, one line per row), HTML (h1–h3 become sections), Markdown, plain text. Scanned PDFs without a
text layer yield nothing and fail parsing with a clear reason (OCR is a later addition).

This module is only ever run inside `sandbox.py`'s resource-limited subprocess: parsers of hostile files are
where attacks land.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import ClassVar

MAX_TEXT = 2_000_000  # characters kept per document

TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/html": "html",
    "text/markdown": "md",
    "text/plain": "txt",
}
EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".html": "html",
    ".htm": "html",
    ".md": "md",
    ".txt": "txt",
}


@dataclass(slots=True)
class Block:
    section: str
    text: str
    page: int | None = None


def kind_of(filename: str, content_type: str) -> str | None:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return EXTENSIONS.get(ext) or TYPES.get(content_type.split(";")[0].strip().lower())


def _pdf(data: bytes) -> list[Block]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return [Block("", (page.extract_text() or "").strip(), i + 1) for i, page in enumerate(reader.pages)]


def _docx(data: bytes) -> list[Block]:
    import docx

    d = docx.Document(io.BytesIO(data))
    blocks: list[Block] = []
    path: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            blocks.append(Block(" › ".join(path), "\n".join(buf)))
            buf.clear()

    body = d.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = next((x for x in d.paragraphs if x._p is child), None)
            if p is None:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            text = p.text.strip()
            m = re.match(r"Heading (\d)", style)
            if m and text:
                flush()
                level = int(m.group(1))
                path[:] = [*path[: level - 1], text]
            elif text:
                buf.append(text)
        elif tag == "tbl":
            t = next((x for x in d.tables if x._tbl is child), None)
            if t is not None:
                for row in t.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        buf.append(" | ".join(cells))
    flush()
    return blocks


def _xlsx(data: bytes) -> list[Block]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    blocks = []
    for ws in wb.worksheets:
        lines = []
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v).strip() for v in row]
            if any(cells):
                lines.append(" | ".join(cells))
        if lines:
            blocks.append(Block(ws.title, "\n".join(lines)))
    return blocks


class _Html(HTMLParser):
    """Text by section. Scripts, styles and elements hidden with inline `display:none` are dropped: hidden
    text never reaches a model."""

    SKIP: ClassVar[frozenset[str]] = frozenset({"script", "style", "noscript", "template"})
    VOID: ClassVar[frozenset[str]] = frozenset(
        {"br", "img", "hr", "input", "meta", "link", "wbr", "col", "area"}
    )

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[Block] = []
        self.path: list[str] = []
        self.buf: list[str] = []
        self.heading: int | None = None
        self.head_text: list[str] = []
        self.stack: list[tuple[str, bool]] = []  # (tag, hidden)

    @property
    def hidden(self) -> bool:
        return any(h for _, h in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        style = (dict(attrs).get("style") or "").replace(" ", "").lower()
        if tag not in self.VOID:
            self.stack.append((tag, tag in self.SKIP or "display:none" in style))
        if self.hidden:
            return
        if tag in ("h1", "h2", "h3"):
            self.flush()
            self.heading, self.head_text = int(tag[1]), []
        if tag in ("p", "br", "li", "tr", "div"):
            self.buf.append("\n")

    def handle_endtag(self, tag: str) -> None:
        was_hidden = self.hidden
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if not was_hidden and self.heading and tag == f"h{self.heading}":
            title = " ".join("".join(self.head_text).split())
            self.path[:] = [*self.path[: self.heading - 1], title]
            self.heading = None

    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        (self.head_text if self.heading else self.buf).append(data)

    def flush(self) -> None:
        text = re.sub(r"\n\s*\n+", "\n", "".join(self.buf)).strip()
        if text:
            self.blocks.append(Block(" › ".join(self.path), text))
        self.buf = []


def _html(data: bytes) -> list[Block]:
    p = _Html()
    p.feed(data.decode("utf-8", "replace"))
    p.flush()
    return p.blocks


def _md(data: bytes) -> list[Block]:
    blocks: list[Block] = []
    path: list[str] = []
    buf: list[str] = []
    for line in data.decode("utf-8", "replace").splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            if "".join(buf).strip():
                blocks.append(Block(" › ".join(path), "\n".join(buf).strip()))
            buf = []
            level = len(m.group(1))
            path[:] = [*path[: level - 1], m.group(2).strip()]
        else:
            buf.append(line)
    if "".join(buf).strip():
        blocks.append(Block(" › ".join(path), "\n".join(buf).strip()))
    return blocks


def _txt(data: bytes) -> list[Block]:
    text = data.decode("utf-8", "replace").strip()
    return [Block("", text)] if text else []


PARSERS = {"pdf": _pdf, "docx": _docx, "xlsx": _xlsx, "html": _html, "md": _md, "txt": _txt}


def parse(kind: str, data: bytes) -> list[Block]:
    blocks = [b for b in PARSERS[kind](data) if b.text.strip()]
    total, out = 0, []
    for b in blocks:
        if total >= MAX_TEXT:
            break
        b.text = b.text[: MAX_TEXT - total]
        total += len(b.text)
        out.append(b)
    return out
