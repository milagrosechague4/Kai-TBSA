"""Integration: tenant isolation. A server bound to tenant A must never return
tenant B's rows. Skips gracefully if no database is reachable."""

from __future__ import annotations

import os

import asyncpg
import pytest
from conftest import TENANT_A, TENANT_B, make_settings
from fastmcp import Client
from pgvector.asyncpg import register_vector

from kai_mcp_empresa import db
from kai_mcp_empresa.embeddings import FakeEmbedder
from kai_mcp_empresa.server import build_server

DB_URL = os.environ.get("DATABASE_URL", "postgresql://kai:kai@localhost:5433/kai_empresa")


async def _db_reachable() -> bool:
    try:
        conn = await asyncpg.connect(DB_URL)
        await conn.close()
        return True
    except Exception:
        return False


async def _seed(tenant: str, title: str, content: str) -> None:
    emb = FakeEmbedder(1536)
    vec = await emb.embed(f"{title}\n{content}")
    conn = await asyncpg.connect(DB_URL)
    await register_vector(conn)
    await conn.execute(
        "DELETE FROM knowledge WHERE tenant_id=$1::uuid AND collection='scoptest'", tenant
    )
    await conn.execute(
        "INSERT INTO knowledge (tenant_id, collection, title, content, embedding) "
        "VALUES ($1::uuid,'scoptest',$2,$3,$4)",
        tenant,
        title,
        content,
        vec,
    )
    await conn.close()


@pytest.fixture(autouse=True)
async def _reset_pool():
    await db.close_pool()
    yield
    await db.close_pool()


async def test_tenant_isolation_in_search():
    if not await _db_reachable():
        pytest.skip("no database reachable")

    await _seed(TENANT_A, "Alpha secret", "tenant A only content about alpha")
    await _seed(TENANT_B, "Beta secret", "tenant B only content about beta")

    mcp, _ = build_server(make_settings(tenant_id=TENANT_A))
    async with Client(mcp) as client:
        result = await client.call_tool("kai_search", {"query": "secret content", "limit": 50})

    titles = [row["title"] for row in result.data]
    assert "Alpha secret" in titles, "tenant A should see its own row"
    assert "Beta secret" not in titles, "tenant A must NOT see tenant B's row"


async def test_log_interaction_writes_audit_row():
    if not await _db_reachable():
        pytest.skip("no database reachable")

    mcp, _ = build_server(make_settings(tenant_id=TENANT_A))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "log_interaction", {"tool": "kai_search", "outcome": "ok", "latency_ms": 12}
        )
    assert result.data["logged"] is True


async def test_list_tools_are_acl_scoped():
    """A caller without the ACL tag must not see a restricted collection via the
    list tools (regression for the codex P1: list tools were tenant- but not
    ACL-scoped)."""
    if not await _db_reachable():
        pytest.skip("no database reachable")

    emb = FakeEmbedder(1536)
    vp = await emb.embed("public")
    vs = await emb.embed("secret")
    conn = await asyncpg.connect(DB_URL)
    await register_vector(conn)
    await conn.execute(
        "DELETE FROM knowledge WHERE tenant_id=$1::uuid AND collection IN ('acl_public','acl_secret')",
        TENANT_A,
    )
    await conn.execute(
        "INSERT INTO knowledge (tenant_id,collection,title,content,acl_tags,embedding) "
        "VALUES ($1::uuid,'acl_public','Public doc','open to all',$2,$3)",
        TENANT_A, [], vp,
    )
    await conn.execute(
        "INSERT INTO knowledge (tenant_id,collection,title,content,acl_tags,embedding) "
        "VALUES ($1::uuid,'acl_secret','Secret doc','restricted',$2,$3)",
        TENANT_A, ["secret"], vs,
    )
    await conn.close()

    # dev caller has empty ACL → sees the public collection, never the restricted one
    mcp, _ = build_server(make_settings(tenant_id=TENANT_A))
    async with Client(mcp) as client:
        cols = await client.call_tool("kai_list_collections", {})
        names = {r["collection"] for r in cols.data}
        secret_objs = await client.call_tool("kai_list_objects", {"collection": "acl_secret"})

    assert "acl_public" in names
    assert "acl_secret" not in names, "list tools must hide ACL-restricted collections"
    assert secret_objs.data == []

    conn = await asyncpg.connect(DB_URL)
    await conn.execute(
        "DELETE FROM knowledge WHERE tenant_id=$1::uuid AND collection IN ('acl_public','acl_secret')",
        TENANT_A,
    )
    await conn.close()
