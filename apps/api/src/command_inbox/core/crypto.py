"""Hashing, tokens, AES-256-GCM for secrets at rest, HMAC for webhooks, canonical JSON for hashes."""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import os
import secrets
from datetime import datetime
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from command_inbox.config import settings
from command_inbox.core.clock import iso_ms


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


KEY_VERSION = "k1"


def _root() -> bytes:
    return hashlib.sha256(settings.encryption_key.encode()).digest()


def _key(purpose: str) -> bytes:
    """One key per purpose (HKDF from the root secret), so a leak or misuse in one place stays there."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"command-inbox", info=purpose.encode()).derive(
        _root()
    )


def encrypt(plaintext: str, *, purpose: str, aad: str = "") -> str:
    """AES-256-GCM with a purpose key and associated data (e.g. "org|table|row"), so a ciphertext copied to
    another row or tenant fails to decrypt. Output: k1.b64(iv).b64(ciphertext+tag)."""
    iv = os.urandom(12)
    sealed = AESGCM(_key(purpose)).encrypt(iv, plaintext.encode(), aad.encode() or None)
    return ".".join((KEY_VERSION, _b64(iv), _b64(sealed)))


def decrypt(payload: str, *, purpose: str, aad: str = "") -> str:
    parts = payload.split(".")
    if parts[0] == KEY_VERSION:
        iv, sealed = _unb64(parts[1]), _unb64(parts[2])
        return AESGCM(_key(purpose)).decrypt(iv, sealed, aad.encode() or None).decode()
    # Legacy format from the previous service: iv.tag.ciphertext under the root key, no AAD.
    iv, tag, ct = (_unb64(p) for p in parts)
    return AESGCM(_root()).decrypt(iv, ct + tag, None).decode()


def hmac_hex(secret: str, body: bytes | str) -> str:
    data = body if isinstance(body, bytes) else body.encode()
    return _hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()


def safe_equal(a: str, b: str) -> bool:
    return _hmac.compare_digest(a.encode(), b.encode())


def _js_number(v: float | int) -> str:
    # JSON numbers as JavaScript prints them (1.0 -> "1"), so hashes match across implementations.
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return json.dumps(v)


def canonical_json(value: Any) -> str:
    """Stable JSON: sorted keys, no whitespace; byte-identical to the JavaScript implementation."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _js_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, datetime):
        return json.dumps(iso_ms(value))
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value)
        return (
            "{"
            + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical_json(value[k]) for k in keys)
            + "}"
        )
    return json.dumps(str(value), ensure_ascii=False)
