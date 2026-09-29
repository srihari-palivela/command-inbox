"""Tenant data keys: Vault Transit and AWS KMS wrap/unwrap, rotation, and moving keys between providers."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from command_inbox.core.errors import AppError
from command_inbox.db.engine import global_tx, tenant_tx
from command_inbox.platform import keys
from command_inbox.platform.keys import (
    AwsKms,
    LocalKms,
    VaultTransitKms,
    rewrap_tenant_keys,
    rotate_tenant_key,
    tenant_decrypt,
    tenant_encrypt,
)
from tests.integration.admin_support import org_id

pytestmark = pytest.mark.integration


class FakeVault:
    """Transit's encrypt/decrypt with derived keys: ciphertext bound to the context."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        op = request.url.path.split("/")[3]
        self.calls.append(op)
        assert request.headers["X-Vault-Token"] == "t"
        if op == "encrypt":
            blob = base64.b64encode(f"{body['context']}|{body['plaintext']}".encode()).decode()
            return httpx.Response(200, json={"data": {"ciphertext": f"vault:v1:{blob}"}})
        ctx, plaintext = base64.b64decode(body["ciphertext"].removeprefix("vault:v1:")).decode().split("|")
        if ctx != body["context"]:
            return httpx.Response(400, json={"errors": ["context mismatch"]})
        return httpx.Response(200, json={"data": {"plaintext": plaintext}})


class FakeAws:
    def encrypt(self, KeyId: str, Plaintext: bytes, EncryptionContext: dict) -> dict:
        return {
            "CiphertextBlob": json.dumps({"k": KeyId, "c": EncryptionContext, "p": Plaintext.hex()}).encode()
        }

    def decrypt(self, CiphertextBlob: bytes, KeyId: str, EncryptionContext: dict) -> dict:
        d = json.loads(CiphertextBlob)
        assert d["c"] == EncryptionContext and d["k"] == KeyId
        return {"Plaintext": bytes.fromhex(d["p"])}


def test_vault_and_aws_bind_the_tenant():
    vault = VaultTransitKms(
        "https://vault.example", "t", "ci", client=httpx.Client(transport=httpx.MockTransport(FakeVault()))
    )
    dek = b"k" * 32
    w = vault.wrap(dek, tenant_id="a")
    assert w.startswith("vault:v1:") and vault.unwrap(w, tenant_id="a") == dek
    with pytest.raises(AppError):
        vault.unwrap(w, tenant_id="b")
    aws = AwsKms("arn:aws:kms:eu-west-2:1:key/x", client=FakeAws())
    assert aws.unwrap(aws.wrap(dek, tenant_id="a"), tenant_id="a") == dek
    assert aws.ref == "aws:arn:aws:kms:eu-west-2:1:key/x"


async def test_rotation_keeps_old_data_readable_and_rewrap_moves_the_kek(app, monkeypatch):
    oid = await org_id("meridian")
    async with tenant_tx(oid) as tx:
        old = await tenant_encrypt(tx, oid, "secret-before", aad="t")
    async with global_tx() as g:
        v = await rotate_tenant_key(g, oid)
    async with tenant_tx(oid) as tx:
        new = await tenant_encrypt(tx, oid, "secret-after", aad="t")
        assert new.split(".")[1] == str(v) and old.split(".")[1] != str(v)
        assert await tenant_decrypt(tx, oid, old, aad="t") == "secret-before"

    # Move every key from the local KEK to Vault Transit.
    fake = FakeVault()
    vault = VaultTransitKms(
        "https://vault.example", "t", "ci", client=httpx.Client(transport=httpx.MockTransport(fake))
    )
    monkeypatch.setattr(keys.settings, "kms_provider", "vault")
    keys.set_kms_for_tests("vault", vault)
    keys.set_kms_for_tests("vault:ci", vault)
    try:
        async with global_tx() as g:
            moved = await rewrap_tenant_keys(g, oid)
        assert moved >= 2
        async with tenant_tx(oid) as tx:
            assert await tenant_decrypt(tx, oid, old, aad="t") == "secret-before"
            assert await tenant_decrypt(tx, oid, new, aad="t") == "secret-after"
        assert "decrypt" in fake.calls
        # And back, so the rest of the suite keeps the local KEK.
        monkeypatch.setattr(keys.settings, "kms_provider", "local")
        keys._providers.pop("local", None)
        async with global_tx() as g:
            assert await rewrap_tenant_keys(g, oid) == moved
    finally:
        keys.set_kms_for_tests("vault", None)
        keys.set_kms_for_tests("vault:ci", None)
    assert isinstance(keys.kms(), LocalKms)
