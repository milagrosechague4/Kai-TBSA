"""Ingest pipeline: chunk → embed → upsert. Idempotent per (tenant_id, source):
re-ingesting a source replaces its rows, never duplicates."""

from __future__ import annotations

import asyncpg

from ..embeddings import Embedder
from .chunker import chunk_text
from .readers import Document


async def ingest_documents(
    pool: asyncpg.Pool,
    tenant_id: str,
    source: str,
    documents: list[Document],
    embedder: Embedder,
    *,
    max_chars: int = 1200,
    overlap: int = 150,
) -> int:
    """Replace all rows for (tenant_id, source) with freshly chunked + embedded
    rows from `documents`. Returns the number of chunks written."""
    written = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM knowledge WHERE tenant_id = $1::uuid AND source = $2",
                tenant_id,
                source,
            )
            for doc in documents:
                chunks = chunk_text(doc.content, max_chars=max_chars, overlap=overlap)
                for i, chunk in enumerate(chunks):
                    title = doc.title if i == 0 else f"{doc.title} ({i + 1})"
                    vec = await embedder.embed(chunk)
                    await conn.execute(
                        """
                        INSERT INTO knowledge
                            (tenant_id, collection, title, content, acl_tags, source, embedding)
                        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
                        """,
                        tenant_id,
                        doc.collection,
                        title,
                        chunk,
                        doc.acl_tags,
                        source,
                        vec,
                    )
                    written += 1
    return written
