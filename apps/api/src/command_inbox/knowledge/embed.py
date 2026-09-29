"""Embeddings behind one interface.

- `openai_compatible`: any `/v1/embeddings` endpoint. Self-hosted (Text Embeddings Inference or vLLM serving
  bge-m3 / e5-large, so text stays inside the bank's boundary) or a provider's, per tenant policy. The model
  must return 1024 dimensions (bge-m3 and e5-large do; OpenAI's v3 models accept `dimensions: 1024`).
- `hash`: deterministic feature hashing of words and word pairs. Lexical, not semantic: for development and
  tests only; production refuses it.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from typing import Protocol

import httpx

from command_inbox.config import settings
from command_inbox.db.models import EMBEDDING_DIM

_WORD = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    name: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    name = "hash"

    def _one(self, text: str) -> list[float]:
        words = _WORD.findall(text.lower())
        vec = [0.0] * EMBEDDING_DIM
        for feature in words + [f"{a} {b}" for a, b in itertools.pairwise(words)]:
            h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
            vec[h % EMBEDDING_DIM] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]


class OpenAICompatibleEmbedder:
    name = "openai_compatible"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        headers = (
            {"authorization": f"Bearer {settings.embedding_api_key}"} if settings.embedding_api_key else {}
        )
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60) as client:
            for i in range(0, len(texts), 32):
                body: dict[str, object] = {"model": settings.embedding_model, "input": texts[i : i + 32]}
                if settings.embedding_send_dimensions:
                    body["dimensions"] = EMBEDDING_DIM
                r = await client.post(
                    settings.embedding_url.rstrip("/") + "/v1/embeddings", json=body, headers=headers
                )  # type: ignore[union-attr]
                r.raise_for_status()
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                for d in data:
                    if len(d["embedding"]) != EMBEDDING_DIM:
                        raise ValueError(
                            f"the embedding model returned {len(d['embedding'])} dimensions, not {EMBEDDING_DIM}"
                        )
                    out.append(d["embedding"])
        return out


def embedder() -> Embedder:
    return (
        OpenAICompatibleEmbedder() if settings.embedding_provider == "openai_compatible" else HashEmbedder()
    )
