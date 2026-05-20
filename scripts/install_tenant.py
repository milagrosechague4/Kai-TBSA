"""Provision a tenant's identity layer from a spec JSON: users + who_am_i
snapshots + rules. Idempotent (safe to re-run). Knowledge + connectors are a
separate step (need the tenant's data + connector creds).

Usage:
    uv run python scripts/install_tenant.py installs/koi/tenant.json

The tenant_id is derived deterministically from the slug, so it's stable across
runs. Set KAI_TENANT_ID to the printed value to run the server for this tenant.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import uuid

import asyncpg

DSN = os.environ.get("DATABASE_URL", "postgresql://kai:kai@localhost:5433/kai_empresa")


def tenant_id_for(slug: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "kai-tenant:" + slug))


async def install(spec_path: str) -> None:
    spec = json.loads(pathlib.Path(spec_path).read_text())
    slug = spec["slug"]
    tid = tenant_id_for(slug)

    conn = await asyncpg.connect(DSN)
    try:
        for u in spec["users"]:
            await conn.execute(
                """
                INSERT INTO users (tenant_id, user_id, role, acl_tags)
                VALUES ($1::uuid, $2, $3, $4)
                ON CONFLICT (tenant_id, user_id)
                DO UPDATE SET role = EXCLUDED.role, acl_tags = EXCLUDED.acl_tags
                """,
                tid, u["user_id"], u.get("role"), u.get("acl_tags", []),
            )
            await conn.execute(
                """
                INSERT INTO who_am_i_snapshots (tenant_id, user_id, version, profile)
                VALUES ($1::uuid, $2, 1, $3::jsonb)
                ON CONFLICT (tenant_id, user_id, version)
                DO UPDATE SET profile = EXCLUDED.profile
                """,
                tid, u["user_id"], json.dumps(u["profile"]),
            )

        await conn.execute("DELETE FROM rules WHERE tenant_id = $1::uuid", tid)
        for r in spec.get("rules", []):
            await conn.execute(
                "INSERT INTO rules (tenant_id, scope, body, active) VALUES ($1::uuid, $2, $3, true)",
                tid, r["scope"], r["body"],
            )

        n_users = await conn.fetchval(
            "SELECT count(*) FROM users WHERE tenant_id = $1::uuid", tid
        )
        n_profiles = await conn.fetchval(
            "SELECT count(DISTINCT user_id) FROM who_am_i_snapshots WHERE tenant_id = $1::uuid", tid
        )
        n_rules = await conn.fetchval(
            "SELECT count(*) FROM rules WHERE tenant_id = $1::uuid", tid
        )
        n_knowledge = await conn.fetchval(
            "SELECT count(*) FROM knowledge WHERE tenant_id = $1::uuid", tid
        )
    finally:
        await conn.close()

    print(f"Installed tenant: {spec['name']} (slug={slug})")
    print(f"  KAI_TENANT_ID={tid}")
    print(f"  users={n_users}  who_am_i_profiles={n_profiles}  rules={n_rules}  knowledge_rows={n_knowledge}")
    if spec.get("connectors_needed"):
        print(f"  connectors pending (need creds): {', '.join(spec['connectors_needed'])}")


if __name__ == "__main__":
    spec = sys.argv[1] if len(sys.argv) > 1 else "installs/koi/tenant.json"
    asyncio.run(install(spec))
