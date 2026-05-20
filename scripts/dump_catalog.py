"""Regenerate the committed tool catalog snapshot. Run intentionally after a
deliberate change to a tool's name/description/schema."""

from __future__ import annotations

import asyncio
import json
import pathlib

from fastmcp import Client

from kai_mcp_empresa.config import Settings
from kai_mcp_empresa.server import build_server

OUT = pathlib.Path(__file__).resolve().parents[1] / "tool_catalog.snapshot.json"


async def main() -> None:
    mcp, _ = build_server(Settings(_env_file=None, auth_disabled=True))
    async with Client(mcp) as client:
        tools = await client.list_tools()
    catalog = [
        {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
        for t in sorted(tools, key=lambda x: x.name)
    ]
    OUT.write_text(json.dumps(catalog, indent=2) + "\n")
    print(f"wrote {OUT.name} ({len(catalog)} tools)")


if __name__ == "__main__":
    asyncio.run(main())
