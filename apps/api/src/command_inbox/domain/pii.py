"""PII masking before any text reaches a model.

Values become typed tokens; the vault lets the executor re-bind them inside the bank's boundary. Cards are
matched before Aadhaar (a 16-digit card starts with an Aadhaar-shaped 12 digits) and card numbers are
Luhn-checked so order numbers are not mistaken for them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


def _luhn(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    total = 0
    for i, n in enumerate(reversed(d)):
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("CARD", re.compile(r"\b(?:\d{4}[ -]){3}\d{4}\b")),
    ("AADHAAR", re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b(?![ -]\d)")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    ("IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("PHONE", re.compile(r"(?:\+91[\s-]?)?\b[6-9]\d{9}\b")),
    ("ACCOUNT", re.compile(r"\b\d{11,18}\b")),
]
TOKEN_RE = re.compile(r"\[[A-Z]+_\d+\]")


@dataclass
class Masked:
    text: str
    vault: dict[str, str] = field(default_factory=dict)


def mask_pii(text: str) -> Masked:
    vault: dict[str, str] = {}
    counters: dict[str, int] = {}
    out = text
    for kind, pattern in PATTERNS:

        def repl(m: re.Match[str], kind: str = kind) -> str:
            value = m.group(0)
            if kind == "CARD" and not _luhn(value):
                return value
            for token, v in vault.items():
                if v == value:
                    return token
            counters[kind] = counters.get(kind, 0) + 1
            token = f"[{kind}_{counters[kind]}]"
            vault[token] = value
            return token

        out = pattern.sub(repl, out)
    return Masked(out, vault)


def unmask(text: str, vault: dict[str, str]) -> str:
    return TOKEN_RE.sub(lambda m: vault.get(m.group(0), m.group(0)), text)
