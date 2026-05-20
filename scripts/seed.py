"""Seed the dev tenant with sample knowledge + a who-am-I profile so the server
demos end-to-end without an OpenAI key (uses the deterministic FakeEmbedder)."""

from __future__ import annotations

import asyncio
import json

from kai_mcp_empresa import db
from kai_mcp_empresa.config import Settings
from kai_mcp_empresa.embeddings import build_embedder

KNOWLEDGE = [
    ("playbooks", "Refund policy", "Refunds are issued within 14 days for unused items in original packaging."),
    ("playbooks", "Onboarding checklist", "New client onboarding: kickoff call, access setup, first report in week 1."),
    ("metrics", "Q1 portfolio summary", "Q1 revenue across portfolio companies grew 18% QoQ; churn held under 3%."),
]

PROFILE = {
    "nombre": "Dev User",
    "rol": "Founder",
    "empresa": "Dev Tenant",
    "tone_preferido": ["directo", "es-AR"],
    "tools_principales": ["notion", "slack"],
}


async def main() -> None:
    settings = Settings(_env_file=None, auth_disabled=True)
    embedder = build_embedder(settings)
    pool = await db.get_pool(settings)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM knowledge WHERE tenant_id=$1::uuid", settings.tenant_id)
        for collection, title, content in KNOWLEDGE:
            vec = await embedder.embed(f"{title}\n{content}")
            await conn.execute(
                "INSERT INTO knowledge (tenant_id, collection, title, content, embedding) "
                "VALUES ($1::uuid,$2,$3,$4,$5)",
                settings.tenant_id,
                collection,
                title,
                content,
                vec,
            )
        await conn.execute(
            "DELETE FROM who_am_i_snapshots WHERE tenant_id=$1::uuid AND user_id=$2",
            settings.tenant_id,
            settings.dev_user_id,
        )
        await conn.execute(
            "INSERT INTO who_am_i_snapshots (tenant_id, user_id, version, profile) "
            "VALUES ($1::uuid,$2,1,$3::jsonb)",
            settings.tenant_id,
            settings.dev_user_id,
            json.dumps(PROFILE),
        )
    await db.close_pool()
    print(f"seeded {len(KNOWLEDGE)} knowledge rows + 1 profile for tenant {settings.tenant_id}")


if __name__ == "__main__":
    asyncio.run(main())
