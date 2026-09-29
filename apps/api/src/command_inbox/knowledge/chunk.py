"""Structure-aware chunking: pieces of about 600 tokens with ~80 tokens of overlap, never mixing sections.

Paragraph boundaries are preferred; a paragraph longer than a chunk is split by sentences. The section path
and page travel with each chunk so a citation can say where the answer came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from command_inbox.knowledge.parse import Block

TARGET_TOKENS = 600
OVERLAP_TOKENS = 80
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def tokens(text: str) -> int:
    """A cheap, model-agnostic estimate (words × 1.3), good enough for sizing."""
    return int(len(text.split()) * 1.3) + 1


@dataclass(slots=True)
class Chunk:
    ordinal: int
    section: str
    page: int | None
    text: str
    tokens: int


def _units(text: str) -> list[str]:
    units: list[str] = []
    for para in re.split(r"\n\s*\n|\n(?=[-*•] )", text):
        para = para.strip()
        if not para:
            continue
        if tokens(para) <= TARGET_TOKENS:
            units.append(para)
        else:
            units += [s.strip() for s in _SENTENCE.split(para) if s.strip()]
    return units


def chunk(blocks: list[Block]) -> list[Chunk]:
    out: list[Chunk] = []
    for b in blocks:
        current: list[str] = []
        size = 0
        for u in _units(b.text):
            t = tokens(u)
            if current and size + t > TARGET_TOKENS:
                out.append(Chunk(len(out), b.section, b.page, "\n\n".join(current), size))
                # Overlap: carry the tail of the previous chunk into the next.
                tail: list[str] = []
                carried = 0
                for prev in reversed(current):
                    if carried + tokens(prev) > OVERLAP_TOKENS:
                        break
                    tail.insert(0, prev)
                    carried += tokens(prev)
                current, size = tail, carried
            current.append(u)
            size += t
        if current:
            out.append(Chunk(len(out), b.section, b.page, "\n\n".join(current), size))
    return out
