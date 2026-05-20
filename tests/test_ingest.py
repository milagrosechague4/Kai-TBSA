"""Ingest tests: chunker + markdown reader (unit), pipeline roundtrip (integration,
skips without a DB)."""

from __future__ import annotations

import os

import asyncpg
import pytest
from conftest import make_settings

from kai_mcp_empresa import db
from kai_mcp_empresa.embeddings import FakeEmbedder
from kai_mcp_empresa.ingest.chunker import chunk_text
from kai_mcp_empresa.ingest.pipeline import ingest_documents
from kai_mcp_empresa.ingest.readers import Document, read_markdown_dir

DB_URL = os.environ.get("DATABASE_URL", "postgresql://kai:kai@localhost:5433/kai_empresa")
TEST_TENANT = "00000000-0000-0000-0000-0000000000ee"


def test_chunk_text_packs_short_text_into_one():
    assert chunk_text("hello world") == ["hello world"]
    assert chunk_text("") == []


def test_chunk_text_splits_and_bounds():
    text = "\n\n".join(f"paragraph {i} " + "x" * 100 for i in range(20))
    chunks = chunk_text(text, max_chars=300, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) <= 300 + 50 for c in chunks)


def test_read_markdown_dir(tmp_path):
    (tmp_path / "a.md").write_text("# Title A\n\nbody a")
    (tmp_path / "b.md").write_text("no heading, body b")
    (tmp_path / "empty.md").write_text("   ")
    docs = read_markdown_dir(tmp_path, collection="c1")
    titles = sorted(d.title for d in docs)
    assert titles == ["Title A", "b"]  # empty skipped, title from heading or stem
    assert all(d.collection == "c1" for d in docs)


async def _db_reachable() -> bool:
    try:
        c = await asyncpg.connect(DB_URL)
        await c.close()
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
async def _reset_pool():
    await db.close_pool()
    yield
    await db.close_pool()


async def test_ingest_pipeline_roundtrip_and_idempotent():
    if not await _db_reachable():
        pytest.skip("no database reachable")

    settings = make_settings(tenant_id=TEST_TENANT)
    pool = await db.get_pool(settings)
    docs = [
        Document(
            title="Refund policy",
            content="Refunds within 14 days.\n\nOnly unused items in original packaging.",
            collection="policies",
        )
    ]

    n1 = await ingest_documents(pool, TEST_TENANT, "unittest", docs, FakeEmbedder(1536))
    assert n1 >= 1
    count1 = await pool.fetchval(
        "SELECT count(*) FROM knowledge WHERE tenant_id=$1::uuid AND source='unittest'", TEST_TENANT
    )
    assert count1 == n1

    # re-ingest replaces, never duplicates
    n2 = await ingest_documents(pool, TEST_TENANT, "unittest", docs, FakeEmbedder(1536))
    count2 = await pool.fetchval(
        "SELECT count(*) FROM knowledge WHERE tenant_id=$1::uuid AND source='unittest'", TEST_TENANT
    )
    assert count2 == n2 == n1

    await pool.execute(
        "DELETE FROM knowledge WHERE tenant_id=$1::uuid AND source='unittest'", TEST_TENANT
    )
