"""Concurrent mutations of the same file must not lose an update."""

from __future__ import annotations

import asyncio

from conftest import make_settings
from fastmcp import Client

from kai_mcp_empresa.server import build_server


async def test_each_mutation_makes_a_commit(tmp_path):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        await client.call_tool("kai_write", {"path": "t.md", "content": "uno\n"})
        await client.call_tool(
            "kai_edit", {"path": "t.md", "old_string": "uno", "new_string": "dos"}
        )
        hist = (await client.call_tool("kai_history", {"path": "t.md"})).data
        assert len(hist) == 2


async def test_concurrent_edits_no_lost_update(tmp_path):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        await client.call_tool(
            "kai_write", {"path": "board.md", "content": "- [ ] a\n- [ ] b\n"}
        )
        await asyncio.gather(
            client.call_tool(
                "kai_edit",
                {"path": "board.md", "old_string": "- [ ] a", "new_string": "- [x] a"},
            ),
            client.call_tool(
                "kai_edit",
                {"path": "board.md", "old_string": "- [ ] b", "new_string": "- [x] b"},
            ),
        )
        read = (await client.call_tool("kai_read", {"path": "board.md"})).data
        # Both edits survived — neither overwrote the other's change.
        assert read["content"] == "- [x] a\n- [x] b\n"
