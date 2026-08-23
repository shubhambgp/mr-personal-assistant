"""Writes the synthetic literature corpus: Markdown sources, then PDF/DOCX.

    python -m etl.generate_literature

Markdown is written first because it reviews well in a pull request, but only the
rendered PDF and DOCX are committed: `docs_parse.SUPPORTED` is {.pdf, .docx}, so
the Markdown is never ingested and would be a second copy of committed content.
The binaries are what CI and the retrieval eval run against, deliberately — the
parser's weaknesses only show up on real files, not on a text fixture.

The .md files this writes are git-ignored, so running it leaves the tree clean.

EVERYTHING HERE IS FICTIONAL. See etl/literature/brands.py for why that matters
and how it is signalled on every page.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from etl.literature.aids import DETAILING_AIDS
from etl.literature.brands import BRANDS
from etl.literature.render import (
    FICTION_NOTICE,
    markdown_to_docx,
    markdown_to_pdf,
    monograph_markdown,
)

OUT = Path(__file__).resolve().parents[1] / "data" / "literature"


def aid_markdown(aid: dict) -> str:
    head = [f"# {aid['title']}", "", f"> {FICTION_NOTICE}", ""]
    meta = "**Qorvexa Healthcare**"
    if aid.get("brand"):
        meta += f" · {aid['brand']} ({aid['molecule']})"
    head += [meta, ""]
    return "\n".join(head) + aid["body"].strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, str, str, int]] = []

    for brand, spec in BRANDS.items():
        slug = f"{brand.lower()}-smpc"
        markdown = monograph_markdown(brand, spec)
        (out / f"{slug}.md").write_text(markdown)
        pages = markdown_to_pdf(markdown, out / f"{slug}.pdf", f"{brand} SmPC (fictional)")
        rows.append((f"{slug}.pdf", "monograph", brand, pages))

    for aid in DETAILING_AIDS:
        markdown = aid_markdown(aid)
        (out / f"{aid['slug']}.md").write_text(markdown)
        if aid["format"] == "docx":
            markdown_to_docx(markdown, out / f"{aid['slug']}.docx", aid["title"])
            rows.append((f"{aid['slug']}.docx", "detailing_aid", aid.get("brand") or "-", 0))
        else:
            pages = markdown_to_pdf(markdown, out / f"{aid['slug']}.pdf", aid["title"])
            rows.append((f"{aid['slug']}.pdf", "sop", "-", pages))

    print(f"wrote {len(rows)} documents to {out}\n")
    print(f"  {'file':38} {'type':14} {'brand':12} pages")
    for name, kind, brand, pages in rows:
        print(f"  {name:38} {kind:14} {brand:12} {pages or '-'}")
    total = sum(p for *_rest, p in rows)
    print(f"\n  {len(rows)} files, {total} PDF pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
