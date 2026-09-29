"""Per-tenant data keys (envelope encryption).

Each tenant has a random 256-bit data-encryption key (DEK). Only its wrapped form is stored, sealed by a
key-encryption key (KEK) held by the key-management provider. Tenant secrets (mailbox credentials, and later
stored mail) are encrypted with the DEK. Destroying the wrapped DEK crypto-shreds everything under it.

`LocalKms` derives the KEK from ENCRYPTION_KEY (development and single-host installs). A bank's cloud KMS or
HSM (AWS KMS, Azure Key Vault, Google Cloud KMS, including a key the bank owns: BYOK) implements the same two
calls; the wrapped key records which KEK sealed it (`kek_ref`), so keys can move between providers.
"""

from __future__ import annotations

import os
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.config import settings
from command_inbox.core.clock import clock
from command_inbox.core.crypto import _b64, _key, _unb64
from command_inbox.core.errors import AppError
from command_inbox.db.models import TenantKey


class Kms(Protocol):
    ref: str

    def wrap(self, dek: bytes, *, tenant_id: str) -> str: ...

    def unwrap(self, wrapped: str, *, tenant_id: str) -> bytes: ...


class LocalKms:
    ref = "local:kek-v1"

    def wrap(self, dek: bytes, *, tenant_id: str) -> str:
        iv = os.urandom(12)
        return _b64(iv) + "." + _b64(AESGCM(_key("tenant-kek")).encrypt(iv, dek, tenant_id.encode()))

    def unwrap(self, wrapped: str, *, tenant_id: str) -> bytes:
        iv, sealed = (_unb64(p) for p in wrapped.split("."))
        return AESGCM(_key("tenant-kek")).decrypt(iv, sealed, tenant_id.encode())


def kms() -> Kms:
    if settings.kms_provider == "local":
        return LocalKms()
    raise AppError(503, "kms_not_configured", f"Key provider {settings.kms_provider!r} is not available.")


async def ensure_tenant_key(tx: AsyncSession, tenant_id: str) -> TenantKey:
    """Idempotent: returns the active key, creating version 1 if the tenant has none."""
    active = (
        await tx.execute(
            select(TenantKey)
            .where(TenantKey.tenant_id == tenant_id, TenantKey.state == "active")
            .with_for_update()
        )
    ).scalar_one_or_none()
    if active is not None:
        return active
    latest = (
        await tx.execute(
            select(TenantKey.version)
            .where(TenantKey.tenant_id == tenant_id)
            .order_by(TenantKey.version.desc())
        )
    ).scalars().first() or 0
    provider = kms()
    key = TenantKey(
        tenant_id=tenant_id,
        version=latest + 1,
        kek_ref=provider.ref,
        wrapped_key=provider.wrap(os.urandom(32), tenant_id=str(tenant_id)),
        state="active",
    )
    tx.add(key)
    await tx.flush()
    return key


async def _dek(tx: AsyncSession, tenant_id: str, version: int | None) -> tuple[int, bytes]:
    q = select(TenantKey).where(TenantKey.tenant_id == tenant_id)
    q = q.where(TenantKey.version == version) if version else q.where(TenantKey.state == "active")
    key = (await tx.execute(q)).scalar_one_or_none()
    if key is None or key.wrapped_key is None:
        raise AppError(409, "tenant_key_unavailable", "This tenant's data key is missing or destroyed.")
    return key.version, kms().unwrap(key.wrapped_key, tenant_id=str(tenant_id))


async def tenant_encrypt(tx: AsyncSession, tenant_id: str, plaintext: str, *, aad: str) -> str:
    """Seal with the tenant's active DEK (created on first use for tenants that predate provisioning).
    Output: t1.<version>.b64(iv).b64(ciphertext+tag)."""
    await ensure_tenant_key(tx, tenant_id)
    version, dek = await _dek(tx, tenant_id, None)
    iv = os.urandom(12)
    sealed = AESGCM(dek).encrypt(iv, plaintext.encode(), f"{tenant_id}|{aad}".encode())
    return f"t1.{version}.{_b64(iv)}.{_b64(sealed)}"


async def tenant_decrypt(tx: AsyncSession, tenant_id: str, payload: str, *, aad: str) -> str:
    tag, version, iv, sealed = payload.split(".")
    if tag != "t1":
        raise ValueError("not a tenant-sealed value")
    _v, dek = await _dek(tx, tenant_id, int(version))
    return AESGCM(dek).decrypt(_unb64(iv), _unb64(sealed), f"{tenant_id}|{aad}".encode()).decode()


async def destroy_tenant_keys(tx: AsyncSession, tenant_id: str) -> int:
    """Crypto-shred: drop every wrapped DEK of the tenant. Irreversible by design."""
    result = await tx.execute(
        update(TenantKey)
        .where(TenantKey.tenant_id == tenant_id, TenantKey.state != "destroyed")
        .values(state="destroyed", wrapped_key=None, destroyed_at=clock.now())
        .returning(TenantKey.version)
    )
    return len(result.all())
