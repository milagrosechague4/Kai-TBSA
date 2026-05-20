"""End-to-end verification of the v1 success criteria against a live database.

Waits for Postgres (localhost:5433), seeds the dev tenant, then checks:
  - kai_search returns tenant-scoped, ACL-filtered knowledge (pgvector)
  - tenant isolation: tenant A never sees tenant B rows
  - who_am_i returns the profile; log_interaction persists an audit row
  - the server serves over Streamable HTTP, lists 6 tools, invokes OK (Inspector equivalent)
  - auth-enabled server returns 401 to an unauthenticated request

Run: uv run python scripts/full_verify.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time

import asyncpg
from pgvector.asyncpg import register_vector

DB = "postgresql://kai:kai@localhost:5433/kai_empresa"
HOST, PORT, AUTH_PORT = "127.0.0.1", 8080, 8090
URL = f"http://{HOST}:{PORT}/mcp"
TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000002"
DEV_USER = "00000000-0000-0000-0000-0000000000aa"

report: dict[str, object] = {}


async def wait_db(timeout: int = 260) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            c = await asyncpg.connect(DB, timeout=3)
            await c.close()
            return True
        except Exception:
            await asyncio.sleep(4)
    return False


async def seed() -> None:
    from kai_mcp_empresa.embeddings import FakeEmbedder

    emb = FakeEmbedder(1536)
    c = await asyncpg.connect(DB)
    await register_vector(c)
    await c.execute("DELETE FROM knowledge WHERE collection IN ('playbooks','metrics','scoptest')")
    rows = [
        ("playbooks", "Refund policy", "Refunds within 14 days for unused items."),
        ("playbooks", "Onboarding checklist", "Kickoff call, access setup, first report week 1."),
        ("metrics", "Q1 portfolio summary", "Q1 revenue grew 18% QoQ."),
    ]
    for col, title, content in rows:
        v = await emb.embed(f"{title}\n{content}")
        await c.execute(
            "INSERT INTO knowledge(tenant_id,collection,title,content,embedding) "
            "VALUES($1::uuid,$2,$3,$4,$5)",
            TENANT_A, col, title, content, v,
        )
    vb = await emb.embed("Beta secret only for tenant B")
    await c.execute(
        "INSERT INTO knowledge(tenant_id,collection,title,content,embedding) "
        "VALUES($1::uuid,'scoptest','Beta secret','only for tenant B',$2)",
        TENANT_B, vb,
    )
    await c.execute("DELETE FROM who_am_i_snapshots WHERE tenant_id=$1::uuid", TENANT_A)
    await c.execute(
        "INSERT INTO who_am_i_snapshots(tenant_id,user_id,version,profile) "
        "VALUES($1::uuid,$2,1,$3::jsonb)",
        TENANT_A, DEV_USER, json.dumps({"nombre": "Dev User", "rol": "Founder"}),
    )
    await c.close()


async def inproc_checks() -> None:
    from fastmcp import Client

    from kai_mcp_empresa import db
    from kai_mcp_empresa.config import Settings
    from kai_mcp_empresa.server import build_server

    s = Settings(_env_file=None, auth_disabled=True, tenant_id=TENANT_A, database_url=DB)
    mcp, _ = build_server(s)
    async with Client(mcp) as c:
        res = await c.call_tool("kai_search", {"query": "refund policy", "limit": 10})
        titles = [r.get("title") for r in res.data]
        report["kai_search_titles"] = titles
        report["kai_search_returns_rows"] = bool(titles)
        report["tenant_isolation (B hidden)"] = "Beta secret" not in titles

        who = await c.call_tool("who_am_i", {})
        report["who_am_i_profile"] = who.data.get("nombre")

        conn = await asyncpg.connect(DB)
        n0 = await conn.fetchval(
            "SELECT count(*) FROM interactions WHERE tenant_id=$1::uuid", TENANT_A
        )
        log = await c.call_tool("log_interaction", {"tool": "kai_search", "latency_ms": 7})
        n1 = await conn.fetchval(
            "SELECT count(*) FROM interactions WHERE tenant_id=$1::uuid", TENANT_A
        )
        await conn.close()
        report["log_interaction_persists"] = log.data.get("logged") is True and n1 == n0 + 1
    await db.close_pool()


def start_server(port: int, auth: bool) -> subprocess.Popen:
    env = {
        **__import__("os").environ,
        "DATABASE_URL": DB,
        "KAI_TENANT_ID": TENANT_A,
        "KAI_MCP_HOST": HOST,
        "KAI_MCP_PORT": str(port),
        "KAI_MCP_PATH": "/mcp",
    }
    if auth:
        env.update(
            KAI_AUTH_DISABLED="false",
            CLERK_JWKS_URI="https://example.com/.well-known/jwks.json",
            CLERK_ISSUER="https://example.com",
            KAI_MCP_AUDIENCE="kai-mcp-empresa",
        )
    else:
        env["KAI_AUTH_DISABLED"] = "true"
    return subprocess.Popen(
        [sys.executable, "-m", "kai_mcp_empresa"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def wait_port(port: int, timeout: int = 40) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r, w = await asyncio.open_connection(HOST, port)
            w.close()
            return True
        except Exception:
            await asyncio.sleep(1)
    return False


async def http_checks() -> None:
    from fastmcp import Client

    proc = start_server(PORT, auth=False)
    try:
        if not await wait_port(PORT):
            report["http_server"] = "did not bind"
            return
        await asyncio.sleep(1)
        async with Client(URL) as c:
            tools = await c.list_tools()
            report["http_lists_6_tools"] = len({t.name for t in tools}) == 6
            res = await c.call_tool("kai_search", {"query": "onboarding", "limit": 5})
            report["http_invoke_ok"] = bool(res.data)
    finally:
        proc.terminate()


async def auth_http_check() -> None:
    proc = start_server(AUTH_PORT, auth=True)
    try:
        if not await wait_port(AUTH_PORT):
            report["http_401_unauth"] = "server did not bind"
            return
        await asyncio.sleep(1)
        out = subprocess.run(
            [
                "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                "-X", "POST", f"http://{HOST}:{AUTH_PORT}/mcp",
                "-H", "Content-Type: application/json",
                "-H", "Accept: application/json, text/event-stream",
                "-d", '{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
            ],
            capture_output=True, text=True, timeout=15,
        )
        report["http_status_no_token"] = out.stdout.strip()
        report["http_401_unauth"] = out.stdout.strip() in ("401", "403")
    finally:
        proc.terminate()


async def main() -> None:
    if not await wait_db():
        print("DB_UNAVAILABLE: Postgres never came up")
        sys.exit(2)
    print("DB reachable. Seeding + verifying...", flush=True)
    await seed()
    await inproc_checks()
    await http_checks()
    await auth_http_check()

    print("\n===== VERIFICATION REPORT =====")
    for k, v in report.items():
        print(f"  {k}: {v}")
    crit = [
        report.get("kai_search_returns_rows"),
        report.get("tenant_isolation (B hidden)"),
        report.get("log_interaction_persists"),
        report.get("http_lists_6_tools"),
        report.get("http_invoke_ok"),
        report.get("http_401_unauth"),
    ]
    ok = all(crit)
    print(f"\n{'ALL_PASS' if ok else 'SOME_FAILED'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
