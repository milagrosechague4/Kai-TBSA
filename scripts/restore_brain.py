"""Restore the Koi brain from the local seed (data/koi/**) into the LIVE volume via MCP.

Used after the volume came up empty post-redeploy. Pushes every local seed file
(identities, README, todos, reuniones, transcripciones) back through kai_write.
Idempotent (overwrite). Token read from gitignored tokens.json.
"""

import asyncio
import json
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = "https://kai-mcp-empresa-production.up.railway.app/mcp"
REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "data" / "koi"


def _token(user: str = "mat") -> str:
    toks = json.loads((REPO / "tokens.json").read_text(encoding="utf-8"))
    return next(t for t, m in toks.items() if m.get("user") == user and m.get("tenant") == "koi")


async def main() -> None:
    transport = StreamableHttpTransport(URL, headers={"Authorization": f"Bearer {_token()}"})
    files = sorted(p for p in SEED.rglob("*") if p.is_file())
    async with Client(transport) as c:
        for p in files:
            rel = p.relative_to(SEED).as_posix()
            res = await c.call_tool(
                "kai_write",
                {"path": rel, "content": p.read_text(encoding="utf-8"), "mode": "overwrite"},
            )
            print("restored:", res.data.get("path"), res.data.get("bytes"))
        root = (await c.call_tool("kai_list", {"folder": "."})).data
        print("\nroot ahora:", sorted(e["name"] for e in root["entries"]))


if __name__ == "__main__":
    asyncio.run(main())
