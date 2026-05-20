"""CLI: ingest a directory of markdown into a tenant's knowledge base.

    uv run python scripts/ingest.py --slug koi-ventures --source mili-vault \
        --path ~/path/to/mili-vault [--collection vault] [--acl finance ops]

Re-running with the same --source replaces that source's rows (idempotent).
Embedder = OpenAI if OPENAI_API_KEY is set, else the deterministic FakeEmbedder
(dev). Ingest and the running server must use the SAME embedder for search to work.
"""

from __future__ import annotations

import argparse
import asyncio

from kai_mcp_empresa import db
from kai_mcp_empresa.config import Settings
from kai_mcp_empresa.embeddings import build_embedder
from kai_mcp_empresa.identity import tenant_id_for
from kai_mcp_empresa.ingest.pipeline import ingest_documents
from kai_mcp_empresa.ingest.readers import read_markdown_dir


async def run(args: argparse.Namespace) -> None:
    settings = Settings()  # reads .env / env for DATABASE_URL + OPENAI_API_KEY
    tenant_id = tenant_id_for(args.slug)
    documents = read_markdown_dir(args.path, collection=args.collection, acl_tags=args.acl)
    if not documents:
        print(f"no .md documents found at {args.path}")
        return

    embedder = build_embedder(settings)
    pool = await db.get_pool(settings)
    try:
        n = await ingest_documents(pool, tenant_id, args.source, documents, embedder)
    finally:
        await db.close_pool()

    print(f"ingested {n} chunks from {len(documents)} docs")
    print(
        f"  tenant={args.slug} ({tenant_id})  source={args.source}  "
        f"embedder={type(embedder).__name__}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest markdown into a tenant's knowledge base.")
    p.add_argument("--slug", required=True, help="tenant slug, e.g. koi-ventures")
    p.add_argument("--path", required=True, help="directory of .md files")
    p.add_argument("--source", required=True, help="source name; re-ingest replaces this source")
    p.add_argument("--collection", default=None, help="override collection (default: folder name)")
    p.add_argument("--acl", nargs="*", default=None, help="acl tags (default: none = all in tenant)")
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
