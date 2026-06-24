"""Structured tool-call logging: one JSON line per call, no secrets."""

from __future__ import annotations

import json

from conftest import make_settings
from fastmcp import Client

from kai_mcp_empresa.server import build_server


async def test_tool_call_emits_one_json_line(tmp_path, caplog):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    with caplog.at_level("INFO", logger="kai.toolcall"):
        async with Client(mcp) as client:
            await client.call_tool("kai_write", {"path": "a.md", "content": "hi\n"})

    lines = [r.getMessage() for r in caplog.records if r.name == "kai.toolcall"]
    assert lines, "expected at least one tool-call log line"
    payloads = [json.loads(m) for m in lines]
    write = next(p for p in payloads if p["tool"] == "kai_write")
    assert write["event"] == "tool_call"
    assert write["tenant"] == "testco"
    assert write["user"] == "tester"
    assert write["ok"] is True
    assert isinstance(write["ms"], int)
    # No content, no token ever in the log payload.
    assert "hi" not in write.get("arg", "")
    assert "content" not in json.dumps(write)


async def test_failed_tool_call_logs_error(tmp_path, caplog):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    with caplog.at_level("INFO", logger="kai.toolcall"):
        async with Client(mcp) as client:
            await client.call_tool(
                "kai_read", {"path": "missing.md"}, raise_on_error=False
            )
    payloads = [json.loads(r.getMessage()) for r in caplog.records if r.name == "kai.toolcall"]
    read = next(p for p in payloads if p["tool"] == "kai_read")
    assert read["ok"] is False
    assert read["err"]
