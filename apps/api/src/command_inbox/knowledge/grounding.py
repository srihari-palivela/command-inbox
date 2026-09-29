"""The grounding post-check: every factual sentence of a draft must be supported by the sources it cites.

A sentence is supported when enough of its content words (stemmed crudely, stop words ignored) appear in the
cited chunks. Greetings, thanks and sign-offs are exempt. Unsupported sentences are flagged on the draft for
the person approving it; they are not silently removed.
"""

from __future__ import annotations

import re

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[a-z0-9]+")
_CITE = re.compile(r"\[\d+\]")
STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "we",
        "you",
        "your",
        "our",
        "i",
        "my",
        "me",
        "us",
        "they",
        "them",
        "he",
        "she",
        "his",
        "her",
        "will",
        "would",
        "can",
        "could",
        "should",
        "may",
        "might",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "not",
        "no",
        "yes",
        "so",
        "than",
        "then",
        "there",
        "here",
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "why",
        "how",
        "all",
        "any",
        "each",
        "more",
        "most",
        "other",
        "some",
        "such",
        "only",
        "own",
        "same",
        "too",
        "very",
        "just",
        "also",
    ]
)
EXEMPT = re.compile(
    r"^(dear|hello|hi|thank|thanks|kind regards|warm regards|regards|best|sincerely|yours|please let us know)",
    re.I,
)
SUPPORT = 0.5


def _stem(w: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(w) > len(suffix) + 2 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def _terms(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.lower()) if w not in STOP and len(w) > 2}


def unsupported_sentences(body: str, sources: list[str]) -> list[str]:
    source_terms = _terms(" ".join(sources))
    out = []
    for sentence in _SENTENCE.split(body):
        s = _CITE.sub("", sentence).strip()
        if not s or EXEMPT.match(s) or len(_terms(s)) < 3:
            continue
        terms = _terms(s)
        if len(terms & source_terms) / len(terms) < SUPPORT:
            out.append(s)
    return out
