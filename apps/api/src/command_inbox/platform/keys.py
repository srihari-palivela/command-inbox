"""Per-tenant data keys (envelope encryption).

Each tenant has a random 256-bit data-encryption key (DEK). Only its wrapped form is stored, sealed by a
key-encryption key (KEK) held by the key-management provider. Tenant secrets (mailbox credentials, and later
stored mail) are encrypted with the DEK. Destroying the wrapped DEK crypto-shreds everything under it.

`LocalKms` derives the KEK from ENCRYPTION_KEY (development and single-host installs). `VaultTransitKms` (HashiCorp
Vault Transit, derived keys with the tenant as context) and `AwsKms` (a customer-managed AWS KMS key, which may be
the bank's own: BYOK, with the tenant as encryption context) implement the same two calls. The wrapped key records
which KEK sealed it (`kek_ref`) and is always unwrapped by that provider, so keys can move between providers:
`rewrap_tenant_keys` re-seals every live DEK under the configured KEK. `rotate_tenant_key` starts a new DEK
version; older versions stay readable (retired) for what they sealed.
"""

from __future__ import annotations

import base64
import os
from typing import Any, Protocol

import httpx
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


class VaultTransitKms:
    """Vault Transit with a derived key: the tenant id is the derivation context, so one tenant's wrapped key
    cannot be unwrapped as another's even with the same Transit key."""

    def __init__(
        self, addr: str, token: str, key: str, namespace: str | None = None, client: Any = None
    ) -> None:
        self.addr, self.token, self.key, self.namespace = addr.rstrip("/"), token, key, namespace
        self.ref = f"vault:{key}"
        self._client = client

    def _post(self, op: str, body: dict[str, str]) -> dict[str, Any]:
        headers = {"X-Vault-Token": self.token}
        if self.namespace:
            headers["X-Vault-Namespace"] = self.namespace
        client = self._client or httpx.Client(timeout=10.0)
        try:
            r = client.post(f"{self.addr}/v1/transit/{op}/{self.key}", json=body, headers=headers)
        except httpx.HTTPError as err:
            raise AppError(
                503, "kms_unavailable", f"The key service is unavailable: {type(err).__name__}"
            ) from err
        finally:
            if self._client is None:
                client.close()
        if r.status_code != 200:
            raise AppError(
                503, "kms_unavailable", f"The key service refused the request (HTTP {r.status_code})."
            )
        return dict(r.json()["data"])

    def wrap(self, dek: bytes, *, tenant_id: str) -> str:
        ctx = base64.b64encode(tenant_id.encode()).decode()
        return str(
            self._post("encrypt", {"plaintext": base64.b64encode(dek).decode(), "context": ctx})["ciphertext"]
        )

    def unwrap(self, wrapped: str, *, tenant_id: str) -> bytes:
        ctx = base64.b64encode(tenant_id.encode()).decode()
        return base64.b64decode(self._post("decrypt", {"ciphertext": wrapped, "context": ctx})["plaintext"])


class AwsKms:
    """AWS KMS (customer-managed key); the tenant id is bound as encryption context."""

    def __init__(self, key_id: str, region: str | None = None, client: Any = None) -> None:
        self.key_id = key_id
        self.ref = f"aws:{key_id}"
        if client is None:
            try:
                import boto3  # type: ignore[import-not-found]
            except ModuleNotFoundError as err:  # optional dependency: install the "aws" extra
                raise AppError(503, "kms_not_configured", "AWS KMS needs the boto3 package.") from err
            client = boto3.client("kms", region_name=region)
        self._client = client

    def wrap(self, dek: bytes, *, tenant_id: str) -> str:
        out = self._client.encrypt(KeyId=self.key_id, Plaintext=dek, EncryptionContext={"tenant": tenant_id})
        return base64.b64encode(out["CiphertextBlob"]).decode()

    def unwrap(self, wrapped: str, *, tenant_id: str) -> bytes:
        out = self._client.decrypt(
            CiphertextBlob=base64.b64decode(wrapped),
            KeyId=self.key_id,
            EncryptionContext={"tenant": tenant_id},
        )
        return bytes(out["Plaintext"])


_providers: dict[str, Kms] = {}


def _build(provider: str, key: str | None = None) -> Kms:
    if provider == "local":
        return LocalKms()
    if provider == "vault":
        if not settings.vault_addr or not settings.vault_token:
            raise AppError(503, "kms_not_configured", "Vault Transit is not configured.")
        return VaultTransitKms(
            settings.vault_addr,
            settings.vault_token,
            key or settings.vault_transit_key,
            settings.vault_namespace,
        )
    if provider == "aws":
        key_id = key or settings.aws_kms_key_id
        if not key_id:
            raise AppError(503, "kms_not_configured", "AWS KMS is not configured.")
        return AwsKms(key_id, settings.aws_region)
    raise AppError(503, "kms_not_configured", f"Key provider {provider!r} is not available.")


def kms() -> Kms:
    """The provider new keys are wrapped with."""
    if settings.kms_provider not in _providers:
        _providers[settings.kms_provider] = _build(settings.kms_provider)
    return _providers[settings.kms_provider]


def kms_for(ref: str) -> Kms:
    """The provider that sealed a wrapped key, from its `kek_ref` ("local:kek-v1", "vault:<key>", "aws:<arn>")."""
    if ref not in _providers:
        provider, _, key = ref.partition(":")
        _providers[ref] = _build(provider, None if provider == "local" else key)
    return _providers[ref]


def set_kms_for_tests(ref: str, provider: Kms | None) -> None:
    if provider is None:
        _providers.pop(ref, None)
    else:
        _providers[ref] = provider


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
    return key.version, kms_for(key.kek_ref).unwrap(key.wrapped_key, tenant_id=str(tenant_id))


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


async def rotate_tenant_key(tx: AsyncSession, tenant_id: str) -> int:
    """Start a new DEK version for new data. Older versions are retired, still readable for what they sealed."""
    await ensure_tenant_key(tx, tenant_id)
    await tx.execute(
        update(TenantKey)
        .where(TenantKey.tenant_id == tenant_id, TenantKey.state == "active")
        .values(state="retired")
    )
    await tx.flush()
    return (await ensure_tenant_key(tx, tenant_id)).version


async def rewrap_tenant_keys(tx: AsyncSession, tenant_id: str) -> int:
    """Re-seal every live DEK of the tenant under the configured KEK (moving provider or KEK); returns the
    number re-sealed. The DEKs themselves, and everything they sealed, are unchanged."""
    target = kms()
    keys = (
        await tx.execute(
            select(TenantKey)
            .where(TenantKey.tenant_id == tenant_id, TenantKey.state != "destroyed")
            .with_for_update()
        )
    ).scalars()
    moved = 0
    for k in keys:
        if k.kek_ref == target.ref or k.wrapped_key is None:
            continue
        dek = kms_for(k.kek_ref).unwrap(k.wrapped_key, tenant_id=str(tenant_id))
        k.wrapped_key, k.kek_ref = target.wrap(dek, tenant_id=str(tenant_id)), target.ref
        moved += 1
    await tx.flush()
    return moved
