"""Number formatting that matches what the previous (JavaScript) service put on the wire."""

from __future__ import annotations

import math


def js_round(x: float) -> int:
    """Math.round: halves round towards +infinity (Python's round() is banker's rounding)."""
    return math.floor(x + 0.5)


def js_str(x: float) -> str:
    """String(n): integral floats print without a trailing '.0'."""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def js_fixed(x: float, digits: int) -> str:
    """Number.prototype.toFixed, half away from zero on the decimal value."""
    scaled = abs(x) * 10**digits
    n = math.floor(scaled + 0.5 + 1e-9)
    sign = "-" if x < 0 and n != 0 else ""
    if digits == 0:
        return f"{sign}{n}"
    s = str(n).rjust(digits + 1, "0")
    return f"{sign}{s[:-digits]}.{s[-digits:]}"


def en_in(n: int) -> str:
    """toLocaleString('en-IN') for integers: 12,34,567 grouping."""
    sign, s = ("-", str(-n)) if n < 0 else ("", str(n))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return sign + ",".join([*groups, tail])


def num(x: float) -> int | float:
    """Integral floats become ints so JSON matches (12 rather than 12.0)."""
    return int(x) if isinstance(x, float) and x.is_integer() else x


def grouped(n: int, locale: str) -> str:
    """An integer in the tenant's digit grouping: Indian lakh grouping for *-IN locales, thousands otherwise."""
    return en_in(n) if locale.endswith("-IN") else f"{n:,}"
