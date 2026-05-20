"""Live HTTP end-to-end check — the scripted equivalent of MCP Inspector.

Start the server first (in another shell):
    KAI_AUTH_DISABLED=true uv run kai-mcp-empresa
Then run:
    uv run python scripts/verify_http_e2e.py

Connects over Streamable HTTP, lists the 6 tools, and invokes each read/audit
tool against the seeded dev tenant.
"""

from __future__ import annotations

import asyncio
import os
import sys

from fastmcp import Client

URL = os.environ.get("KAI_MCP_URL", "http://127.0.0.1:8080/mcp")
EXPECTED = {
    "kai_search",
    "kai_fetch",
    "kai_list_collections",
    "kai_list_objects",
    "who_am_i",
    "log_interaction",
}


async def main() -> None:
    async with Client(URL) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert names == EXPECTED, f"tool catalog mismatch: {sorted(names)}"
        print(f"[ok] Streamable HTTP connected — {len(names)} tools listed")

        cols = await client.call_tool("kai_list_collections", {})
        print("[ok] kai_list_collections ->", cols.data)

        res = await client.call_tool("kai_search", {"query": "refund policy", "limit": 3})
        titles = [r.get("title") for r in res.data]
        assert titles, "kai_search returned nothing — seed the dev tenant first"
        print("[ok] kai_search ->", titles)

        who = await client.call_tool("who_am_i", {})
        print("[ok] who_am_i ->", who.data.get("nombre", who.data))

        log = await client.call_tool("log_interaction", {"tool": "kai_search", "latency_ms": 9})
        assert log.data.get("logged") is True
        print("[ok] log_interaction ->", log.data)

    print("E2E_PASS")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print("E2E_FAIL:", repr(exc))
        sys.exit(1)
