"""PDF and DOCX to per-page text. One function, so the parser can be swapped.

WHY pypdf AND NOT PyMuPDF. fitz extracts better and faster and is layout-aware,
but it is AGPL-3.0 — a bad licence to ship inside code handed to a company, and
a real question rather than a theoretical one. pypdf is BSD-3 and pure Python,
which also preserves this project's "no new system dependency" property.

pypdf's known weakness is TABLES, and a monograph's dosing table is the single
most safety-relevant thing in it. So rather than assume the trade is acceptable:

  * the generated corpus deliberately contains real tables (dosing, interactions);
  * `parse()` reports per-page character counts, and the ingest CLI flags any
    page whose text looks too thin for its size;
  * `pdfplumber` (MIT, permissive) is the named escalation if those numbers show
    tables coming out mangled — a one-file change, because everything downstream
    depends only on the PageText shape below.

That check is run in etl/ingest_docs.py, not left as a comment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PageText:
    """One page (PDF) or one logical block run (DOCX)."""

    number: int  # 1-based
    text: str


SUPPORTED = {".pdf", ".docx"}


def parse(path: Path) -> list[PageText]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(path)
    if suffix == ".docx":
        return _parse_docx(path)
    raise ValueError(f"unsupported document type {suffix!r}; expected one of {sorted(SUPPORTED)}")


def _parse_pdf(path: Path) -> list[PageText]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[PageText] = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append(PageText(number=i, text=_tidy(text)))
    return pages


def _parse_docx(path: Path) -> list[PageText]:
    """DOCX has no page concept until it is rendered, so everything is page 1.

    Reading `document.element.body` rather than `document.paragraphs` is
    deliberate: paragraphs alone silently skip every table, which in a detailing
    aid is where the approved objection responses live — i.e. exactly the content
    a rep would be retrieving.
    """
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(str(path))
    body = document.element.body
    parts: list[str] = []

    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = Paragraph(child, document).text.strip()
            if text:
                parts.append(text)
        elif tag == "tbl":
            table = Table(child, document)
            for row in table.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                if any(cells):
                    # Pipe-joined so a chunk keeps the row's shape: "objection |
                    # approved response" reads as a pair rather than a run-on.
                    parts.append(" | ".join(cells))
    return [PageText(number=1, text=_tidy("\n".join(parts)))]


def _tidy(text: str) -> str:
    """Collapses the artefacts that make chunk boundaries unreliable."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        # The fictional-document footer is repeated on every page by design; it
        # would otherwise dominate the lexical index and be retrieved as content.
        if stripped.startswith("FICTIONAL DEMONSTRATION DOCUMENT"):
            continue
        if stripped.startswith("Page ") and stripped.removeprefix("Page ").strip().isdigit():
            continue
        out.append(line)
    # Squeeze runs of blank lines to one.
    squeezed: list[str] = []
    for line in out:
        if not line.strip() and squeezed and not squeezed[-1].strip():
            continue
        squeezed.append(line)
    return "\n".join(squeezed).strip()


def extraction_report(pages: list[PageText]) -> dict:
    """Numbers the ingest CLI uses to flag suspicious extraction."""
    counts = [len(p.text) for p in pages]
    return {
        "pages": len(pages),
        "chars": sum(counts),
        "per_page": counts,
        "min_page_chars": min(counts) if counts else 0,
        # A page with almost nothing on it usually means the extractor lost a
        # table or the page is an image. Either way it is worth seeing.
        "thin_pages": [p.number for p in pages if len(p.text) < 200],
    }
