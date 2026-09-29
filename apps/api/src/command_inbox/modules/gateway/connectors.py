"""Execution connectors.

Each real connector (Finacle, cards, payments hub, SWIFT) implements `ExecutionConnector` per bank. The
sandbox connector gives the same idempotency semantics: executing the same key twice returns the first
result and changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from command_inbox.core.crypto import sha256


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    idempotency_key: str
    system: str
    endpoint: str
    code: str
    fields: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    external_ref: str
    already_applied: bool


class ExecutionConnector(Protocol):
    async def execute(self, req: ExecutionRequest) -> ExecutionResult: ...

    async def lookup(self, idempotency_key: str) -> ExecutionResult | None:
        """Has an effect with this key already reached the core system?"""
        ...


class SandboxConnector:
    def __init__(self) -> None:
        self._applied: dict[str, str] = {}

    async def lookup(self, idempotency_key: str) -> ExecutionResult | None:
        ref = self._applied.get(idempotency_key)
        return ExecutionResult(ref, True) if ref else None

    async def execute(self, req: ExecutionRequest) -> ExecutionResult:
        existing = await self.lookup(req.idempotency_key)
        if existing:
            return existing
        ref = f"{req.endpoint}-{sha256(req.idempotency_key)[:8].upper()}"
        self._applied[req.idempotency_key] = ref
        return ExecutionResult(ref, False)


connector: ExecutionConnector = SandboxConnector()
