"""Anti tool-poisoning guard: the tool catalog (name/description/inputSchema) must
match the committed snapshot. Any drift fails CI with a diff for human review.
Regenerate intentionally with `uv run python scripts/dump_catalog.py`."""

from __future__ import annotations

import json
import pathlib

from conftest import make_settings
from fastmcp import Client

from kai_mcp_empresa.server import build_server

SNAPSHOT = pathlib.Path(__file__).resolve().parents[1] / "tool_catalog.snapshot.json"


async def _live_catalog() -> list[dict]:
    mcp, _ = build_server(make_settings())
    async with Client(mcp) as client:
        tools = await client.list_tools()
    return [
        {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
        for t in sorted(tools, key=lambda x: x.name)
    ]


async def test_tool_catalog_matches_snapshot():
    live = await _live_catalog()
    expected = json.loads(SNAPSHOT.read_text())
    assert live == expected, (
        "Tool catalog changed. If intentional, regenerate with "
        "`uv run python scripts/dump_catalog.py` and review the diff. "
        "This guard exists to catch silent tool-poisoning."
    )


async def test_catalog_has_six_tools_with_kai_prefix_or_special():
    live = await _live_catalog()
    names = {t["name"] for t in live}
    assert names == {
        "kai_search",
        "kai_fetch",
        "kai_list_collections",
        "kai_list_objects",
        "who_am_i",
        "log_interaction",
    }
