"""Six-Tool read subset. All queries are scoped by the tenant derived from the
token (never from arguments) and filtered by the caller's ACL tags."""

from __future__ import annotations

from fastmcp import FastMCP

from .. import db
from ..config import Settings
from ..embeddings import Embedder
from ..identity import current_context


def register_read_tools(mcp: FastMCP, settings: Settings, embedder: Embedder) -> None:
    @mcp.tool
    async def kai_search(query: str, limit: int = 8) -> list[dict]:
        """Semantic search over the company knowledge base.

        Returns the most relevant knowledge entries for a natural-language query,
        scoped to the caller's tenant and filtered by their ACL tags.
        """
        ctx = current_context(settings)
        vec = await embedder.embed(query)
        pool = await db.get_pool(settings)
        rows = await pool.fetch(
            """
            SELECT id::text, collection, title, content, acl_tags,
                   1 - (embedding <=> $1) AS score
            FROM knowledge
            WHERE tenant_id = $2::uuid
              AND (cardinality(acl_tags) = 0 OR acl_tags && $3::text[])
            ORDER BY embedding <=> $1
            LIMIT $4
            """,
            vec,
            ctx.tenant_id,
            list(ctx.acl_tags),
            limit,
        )
        return [dict(r) for r in rows]

    @mcp.tool
    async def kai_fetch(id: str) -> dict | None:
        """Fetch a single knowledge entry by id (tenant + ACL scoped).

        Returns null if the entry does not exist or is not visible to the caller.
        """
        ctx = current_context(settings)
        pool = await db.get_pool(settings)
        row = await pool.fetchrow(
            """
            SELECT id::text, collection, title, content, acl_tags
            FROM knowledge
            WHERE id = $1::uuid AND tenant_id = $2::uuid
              AND (cardinality(acl_tags) = 0 OR acl_tags && $3::text[])
            """,
            id,
            ctx.tenant_id,
            list(ctx.acl_tags),
        )
        return dict(row) if row else None

    @mcp.tool
    async def kai_list_collections() -> list[dict]:
        """List the knowledge collections (groupings) available in the company."""
        ctx = current_context(settings)
        pool = await db.get_pool(settings)
        rows = await pool.fetch(
            """
            SELECT collection, count(*) AS objects
            FROM knowledge
            WHERE tenant_id = $1::uuid
            GROUP BY collection
            ORDER BY collection
            """,
            ctx.tenant_id,
        )
        return [dict(r) for r in rows]

    @mcp.tool
    async def kai_list_objects(collection: str, limit: int = 50) -> list[dict]:
        """List objects (entries) within a knowledge collection (tenant scoped)."""
        ctx = current_context(settings)
        pool = await db.get_pool(settings)
        rows = await pool.fetch(
            """
            SELECT id::text, title
            FROM knowledge
            WHERE tenant_id = $1::uuid AND collection = $2
            ORDER BY title
            LIMIT $3
            """,
            ctx.tenant_id,
            collection,
            limit,
        )
        return [dict(r) for r in rows]
