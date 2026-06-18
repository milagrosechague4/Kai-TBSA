"""Seed the `kai-platform` tenant (the product creation library) into the LIVE
brain via the MCP.

`kai-platform` is the cross-tenant product knowledge of Kai: REFERENCES the agent
reads (conocimiento/) + SKILLS it runs (skills-library/). It is the moat, so it
lives in its OWN tenant — never in a client brain. This re-pushes the local seed
(data/kai-platform/**) to the live volume, the same role restore_brain.py plays
for koi (Railway volumes don't survive a wipe; this re-seeds idempotently).

Prereqs: the kai-platform tokens must be in the LIVE KAI_TOKENS_JSON (provision
with install_tenant.py installs/kai-platform.json, then merge the tokens into the
Railway env var and redeploy). Token read from gitignored tokens.json.

Usage:
    uv run python scripts/seed_kai_platform.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

REPO = pathlib.Path(__file__).resolve().parent.parent
STAGE = REPO / "data" / "kai-platform"
URL = "https://kai-mcp-empresa-production.up.railway.app/mcp"
ALLOWED = {".md", ".txt", ".json", ".csv", ".yaml", ".yml"}


def _token(user: str = "mat") -> str:
    tokens = json.loads((REPO / "tokens.json").read_text(encoding="utf-8"))
    for tok, meta in tokens.items():
        if meta.get("tenant") == "kai-platform" and meta.get("user") == user:
            return tok
    raise SystemExit(f"no kai-platform token for user={user}")


def _files() -> list[pathlib.Path]:
    return [
        p
        for p in sorted(STAGE.rglob("*"))
        if p.is_file()
        and p.suffix.lower() in ALLOWED
        and not any(part.startswith(".") for part in p.relative_to(STAGE).parts)
    ]


async def main() -> None:
    transport = StreamableHttpTransport(URL, headers={"Authorization": f"Bearer {_token()}"})
    async with Client(transport) as client:
        me = (await client.call_tool("who_am_i", {})).data
        if me["tenant"] != "kai-platform":
            raise SystemExit(f"token resolves to {me['tenant']!r}, not kai-platform")
        n = 0
        for p in _files():
            rel = str(p.relative_to(STAGE))
            await client.call_tool(
                "kai_write",
                {"path": rel, "content": p.read_text(encoding="utf-8"), "mode": "overwrite"},
            )
            n += 1
        listing = (await client.call_tool("kai_list", {"folder": "."})).data
        print(f"seeded {n} files into kai-platform")
        print("root:", [e["name"] for e in listing["entries"]])


if __name__ == "__main__":
    asyncio.run(main())
