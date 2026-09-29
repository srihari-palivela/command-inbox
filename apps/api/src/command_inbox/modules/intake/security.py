"""Webhook authentication and sender authentication for inbound mail.

Webhook signature (the relay → `POST /v1/intake/messages`):

    X-CI-Timestamp: <unix seconds>
    X-CI-Signature: v1=<hex HMAC-SHA256(INTAKE_WEBHOOK_SECRET, "<timestamp>." + raw request body)>

The HMAC is computed over the **raw bytes** received (never a re-serialisation of parsed JSON), and the
timestamp is part of the signed material, so a captured request cannot be replayed once it is more than
`REPLAY_WINDOW_SEC` (5 minutes) old or new. Inside the window an exact replay is harmless: the provider
message id (or, when absent, one derived from the signature) dedupes it at `inbound_messages`.
The previous service's format (`x-ci-signature: hex(HMAC(secret, JSON.stringify(body)))`, no timestamp)
is refused: nothing in this repository sends it, and it has no replay protection.

Sender authentication: the receiving MTA's `Authentication-Results` (RFC 8601) verdicts for DKIM, SPF and
DMARC. A sender is *verified* when DMARC passes, or — when no DMARC verdict is present — when DKIM or SPF
passes for a domain aligned with the From address. Anything else (including no header) is unverified, and
an unverified sender never reaches the Auto lane (`domain.lane.decide_lane(sender_verified=False)`).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from command_inbox.config import settings
from command_inbox.core.clock import clock
from command_inbox.core.crypto import hmac_hex, safe_equal
from command_inbox.core.errors import AppError

REPLAY_WINDOW_SEC = 300
TIMESTAMP_HEADER = "x-ci-timestamp"
SIGNATURE_HEADER = "x-ci-signature"


def _reject(title: str) -> AppError:
    return AppError(401, "unauthenticated", title)


def sign(raw: bytes, timestamp: int, secret: str | None = None) -> str:
    """The `X-CI-Signature` value for a body sent at `timestamp` (used by relays and tests)."""
    return "v1=" + hmac_hex(secret or settings.intake_webhook_secret, f"{timestamp}.".encode() + raw)


def verify_webhook(raw: bytes, timestamp: str | None, signature: str | None) -> None:
    """Raise a 401 problem unless the raw body carries a fresh, valid signature."""
    if not signature:
        raise _reject("Invalid signature")
    if not timestamp or not timestamp.isdigit():
        raise _reject("Missing or invalid signature timestamp")
    ts = int(timestamp)
    if abs(clock.now().timestamp() - ts) > REPLAY_WINDOW_SEC:
        raise _reject("Signature timestamp is outside the replay window")
    expected = sign(raw, ts)
    offered = [s.strip() for s in signature.split(",") if s.strip()]
    if not any(safe_equal(s if s.startswith("v1=") else "v1=" + s, expected) for s in offered):
        raise _reject("Invalid signature")


@dataclass(frozen=True, slots=True)
class SenderAuth:
    dkim: str
    spf: str
    dmarc: str
    verified: bool
    source: str  # "header" | "payload" | "none" | "simulator"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


UNVERIFIED = SenderAuth("none", "none", "none", False, "none")
SIMULATED = SenderAuth("pass", "pass", "pass", True, "simulator")

_RESULT = re.compile(r"^\s*(dkim|spf|dmarc)\s*=\s*([a-z]+)\b(.*)$", re.I | re.S)
_PROP = re.compile(r"\b(header\.d|header\.i|header\.from|smtp\.mailfrom)\s*=\s*([^\s;()]+)", re.I)


def _aligned(domain: str, from_domain: str) -> bool:
    domain = domain.lower().lstrip("@").rsplit("@", 1)[-1]
    return bool(domain) and (from_domain == domain or from_domain.endswith("." + domain))


def parse_authentication_results(value: str | None, from_email: str, source: str = "header") -> SenderAuth:
    """Reduce one or more `Authentication-Results` values to a DKIM/SPF/DMARC verdict for the From domain."""
    if not value or not value.strip():
        return UNVERIFIED
    from_domain = from_email.lower().rsplit("@", 1)[-1]
    verdict = {"dkim": "none", "spf": "none", "dmarc": "none"}
    aligned_pass = False
    # Each header is "authserv-id; method=result props; method=result props"; several may be joined by newlines.
    for part in re.split(r"[;\n]", value):
        m = _RESULT.match(part)
        if not m:
            continue
        method, result, rest = m.group(1).lower(), m.group(2).lower(), m.group(3)
        if verdict[method] != "pass":
            verdict[method] = result
        if result == "pass" and method in ("dkim", "spf"):
            for key, dom in _PROP.findall(rest):
                if key.lower() in ("header.d", "header.i", "smtp.mailfrom") and _aligned(dom, from_domain):
                    aligned_pass = True
    if verdict["dmarc"] == "pass":
        verified = True
    elif verdict["dmarc"] not in ("none",):
        verified = False  # an explicit DMARC fail/quarantine/reject overrides any other pass
    else:
        verified = aligned_pass
    return SenderAuth(verdict["dkim"], verdict["spf"], verdict["dmarc"], verified, source)
