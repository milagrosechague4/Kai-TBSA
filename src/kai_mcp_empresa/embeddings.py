"""Embedder abstraction. Dev/tests use a deterministic fake; prod uses OpenAI
(or bge-large local — privacy invariant — once wired)."""

from __future__ import annotations

import hashlib
import struct
from typing import Protocol

from .config import Settings


class Embedder(Protocol):
    dim: int

    async def embed(self, text: str) -> list[float]: ...


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str, dim: int) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.dim = dim

    async def embed(self, text: str) -> list[float]:
        resp = await self._client.embeddings.create(model=self.model, input=text)
        return list(resp.data[0].embedding)


class FakeEmbedder:
    """Deterministic pseudo-embeddings so search/tests work without an API key."""

    def __init__(self, dim: int = 1536) -> None:
        self.dim = dim

    async def embed(self, text: str) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        out: list[float] = []
        counter = 0
        while len(out) < self.dim:
            block = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            for i in range(0, len(block), 4):
                if len(out) >= self.dim:
                    break
                n = struct.unpack(">I", block[i : i + 4])[0]
                out.append((n / 2**32) * 2.0 - 1.0)
            counter += 1
        return out


def build_embedder(settings: Settings) -> Embedder:
    if settings.openai_api_key:
        return OpenAIEmbedder(
            settings.openai_api_key, settings.embedding_model, settings.embedding_dim
        )
    return FakeEmbedder(settings.embedding_dim)
