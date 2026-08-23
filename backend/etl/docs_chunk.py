"""Section-aware chunking.

Fixed-size chunking is simpler and would be wrong here. The whole value of
retrieval in this app is a citation a rep can check while standing in front of a
doctor: "Cardevia SmPC §4.2.1 Renal impairment, p1" is checkable, and "chunk 43"
is not. So chunks are cut on real section boundaries and each one carries the
section heading and page range it came from.

Sections are detected from the numbering the corpus actually uses — the SmPC
convention (4.1, 4.2, 4.2.1, 6.4) for monographs and simple ordinals for
detailing aids. That is deliberate rather than a guess: the numbering is what a
reader would cite, so it is also what a chunk should be labelled with.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from etl.docs_parse import PageText

#: "4.2 Posology and method of administration", "4.2.1 Renal impairment",
#: "1. Key messages". Requires a following space and some text, so a bare table
#: cell like "20" or a dose like "10 mg once daily" cannot be mistaken for one.
_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+([A-Z][^\n]{3,90})$")

#: Target chunk size in *characters*. A ~4 chars/token heuristic makes this
#: roughly 700-900 tokens, which is the range this corpus's sections mostly fall
#: into anyway — most sections are one chunk, which is the point.
TARGET_CHARS = 3200
OVERLAP_CHARS = 400
#: Below this a "section" has no body worth indexing — a stray heading, or a
#: page artefact. Kept deliberately low: a short section is still a perfectly
#: citable one ("4.3 Contraindications: Hypersensitivity to substituted
#: benzimidazoles.") and, now that each chunk carries a contextual header, a
#: short chunk is just as retrievable as a long one.
#:
#: This was 60, which merged Gastroliv's brief 4.3 forward into 4.5 and — worse —
#: carried the 4.3 LABEL with it, so the clopidogrel interaction would have been
#: cited as a contraindication. Precise citation is the whole point of
#: section-aware chunking, so the threshold now only catches genuinely empty
#: sections.
MIN_CHARS = 25


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    section: str | None
    page_from: int
    page_to: int
    content: str

    @property
    def token_estimate(self) -> int:
        """Approximate, and named so. Chunk boundaries do not need token-exact
        counts, and a real tokeniser here would be a dependency bought for a
        number that is only ever reported."""
        return max(1, len(self.content) // 4)


def chunk_pages(pages: list[PageText]) -> list[Chunk]:
    lines: list[tuple[int, str]] = []
    for page in pages:
        for line in page.text.split("\n"):
            lines.append((page.number, line))

    # 1. Group lines into sections on heading boundaries.
    sections: list[tuple[str | None, list[tuple[int, str]]]] = []
    current_title: str | None = None
    current: list[tuple[int, str]] = []

    for page_number, line in lines:
        stripped = line.strip()
        match = _HEADING.match(stripped)
        if match:
            if current:
                sections.append((current_title, current))
            number, title = match.groups()
            current_title = f"{number} {title.strip()}"
            current = []
            continue
        if stripped:
            current.append((page_number, stripped))
    if current:
        sections.append((current_title, current))

    # 2. Emit chunks, splitting only sections that are genuinely too long.
    chunks: list[Chunk] = []
    carry: list[tuple[int, str]] = []
    carry_title: str | None = None

    for title, body in sections:
        body = carry + body
        # If a previous section was too small to stand alone, its content is now
        # part of this chunk — so say so in the label rather than letting either
        # title silently stand for both. A citation that names the wrong section
        # is worse than one that names two.
        if carry_title and title and carry_title != title:
            title = f"{carry_title}; {title}"
        elif carry_title:
            title = carry_title
        carry, carry_title = [], None

        text = "\n".join(t for _p, t in body)
        if len(text) < MIN_CHARS:
            # Too small to stand alone — attach it to the next section instead
            # of emitting a chunk whose whole content is a heading.
            carry, carry_title = body, title
            continue

        for piece in _split(body):
            content = "\n".join(t for _p, t in piece)
            pages_in = [p for p, _t in piece]
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    section=title,
                    page_from=min(pages_in),
                    page_to=max(pages_in),
                    content=content,
                )
            )

    if carry:
        content = "\n".join(t for _p, t in carry)
        pages_in = [p for p, _t in carry]
        chunks.append(
            Chunk(
                ordinal=len(chunks),
                section=carry_title,
                page_from=min(pages_in),
                page_to=max(pages_in),
                content=content,
            )
        )
    return chunks


def _split(body: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """Splits one over-long section on line boundaries, with overlap.

    Overlap matters at a section boundary: a dose and the sentence qualifying it
    can end up either side of a cut, and a chunk carrying half of that is worse
    than a slightly larger chunk.
    """
    total = sum(len(t) + 1 for _p, t in body)
    if total <= TARGET_CHARS:
        return [body]

    pieces: list[list[tuple[int, str]]] = []
    start = 0
    while start < len(body):
        size = 0
        end = start
        while end < len(body) and size < TARGET_CHARS:
            size += len(body[end][1]) + 1
            end += 1
        pieces.append(body[start:end])
        if end >= len(body):
            break
        # Step back far enough to overlap by ~OVERLAP_CHARS.
        back = 0
        overlap = 0
        while end - back - 1 > start and overlap < OVERLAP_CHARS:
            overlap += len(body[end - back - 1][1]) + 1
            back += 1
        start = end - back
    return pieces
