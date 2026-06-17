"""Regenerate tool_catalog.snapshot.json — the anti tool-poisoning baseline.

Run intentionally after a deliberate tool change, then review the diff:
    uv run python scripts/dump_catalog.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import tempfile

from fastmcp import Client

from kai_mcp_empresa.config import Settings
from kai_mcp_empresa.server import build_server

SNAPSHOT = pathlib.Path(__file__).resolve().parents[1] / "tool_catalog.snapshot.json"


async def main() -> None:
    settings = Settings(
        _env_file=None,
        auth_disabled=True,
        dev_tenant="testco",
        dev_user="tester",
        data_root=pathlib.Path(tempfile.mkdtemp(prefix="kai-dump-")),
    )
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        tools = await client.list_tools()
    catalog = [
        {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
        for t in sorted(tools, key=lambda x: x.name)
    ]
    SNAPSHOT.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(catalog)} tools to {SNAPSHOT}")


if __name__ == "__main__":
    asyncio.run(main())
