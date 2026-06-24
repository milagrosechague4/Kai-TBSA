"""End-to-end through the FastMCP Client: the tools read/write the tenant brain."""

from __future__ import annotations

from conftest import make_settings
from fastmcp import Client

from kai_mcp_empresa.server import build_server


async def test_write_list_read_search_who_am_i(tmp_path):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)

    async with Client(mcp) as client:
        await client.call_tool(
            "kai_write",
            {"path": "todos.md", "content": "- [ ] cargar la llamada — @mat\n"},
        )
        await client.call_tool(
            "kai_write",
            {
                "path": "reuniones/2026-06-17.md",
                "content": "# Arranque MCP\nMili pidió el ERP.\n",
            },
        )

        listing = (await client.call_tool("kai_list", {"folder": "."})).data
        names = {e["name"] for e in listing["entries"]}
        assert {"todos.md", "reuniones"} <= names

        read = (await client.call_tool("kai_read", {"path": "todos.md"})).data
        assert "cargar la llamada" in read["content"]

        hits = (await client.call_tool("kai_search", {"query": "ERP"})).data
        assert any(h["path"] == "reuniones/2026-06-17.md" for h in hits)

        me = (await client.call_tool("who_am_i", {})).data
        assert me["tenant"] == "testco"
        assert me["user"] == "tester"

    # The files really landed under the tenant folder.
    assert (tmp_path / "testco" / "todos.md").exists()


async def test_append_mode_via_client(tmp_path):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        await client.call_tool("kai_write", {"path": "log.md", "content": "uno\n"})
        await client.call_tool(
            "kai_write", {"path": "log.md", "content": "dos\n", "mode": "append"}
        )
        read = (await client.call_tool("kai_read", {"path": "log.md"})).data
        assert read["content"] == "uno\ndos\n"


async def test_who_am_i_includes_identity_profile(tmp_path):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        await client.call_tool(
            "kai_write",
            {"path": "_identity/tester.md", "content": "Rol: QA. Reporta a nadie.\n"},
        )
        me = (await client.call_tool("who_am_i", {})).data
        assert me["profile"] is not None
        assert "QA" in me["profile"]


async def test_edit_targets_one_row_via_client(tmp_path):
    # The squad case: closing one loop must not rewrite the rest of the board.
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        await client.call_tool(
            "kai_write",
            {
                "path": "loops-abiertos.md",
                "content": "- [ ] ERP devengado\n- [ ] conectar squad\n- [ ] reu Manu\n",
            },
        )
        res = (
            await client.call_tool(
                "kai_edit",
                {
                    "path": "loops-abiertos.md",
                    "old_string": "- [ ] conectar squad",
                    "new_string": "- [x] conectar squad",
                },
            )
        ).data
        assert res["replacements"] == 1
        read = (await client.call_tool("kai_read", {"path": "loops-abiertos.md"})).data
        assert read["content"] == (
            "- [ ] ERP devengado\n- [x] conectar squad\n- [ ] reu Manu\n"
        )


async def test_read_window_via_client(tmp_path):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        await client.call_tool(
            "kai_write", {"path": "t.md", "content": "a\nb\nc\nd\n"}
        )
        out = (
            await client.call_tool("kai_read", {"path": "t.md", "offset": 2, "limit": 2})
        ).data
        assert out["content"] == "b\nc\n"
        assert out["total_lines"] == 4 and out["truncated"] is True


async def test_read_missing_file_is_error(tmp_path):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "kai_read", {"path": "nope.md"}, raise_on_error=False
        )
        assert result.is_error


def test_healthz_route_is_registered(tmp_path):
    # The platform healthcheck (Railway) hits /healthz; /mcp does not 200 on GET.
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    paths = {getattr(r, "path", None) for r in mcp.http_app().routes}
    assert "/healthz" in paths
    assert "/mcp" in paths


async def test_traversal_blocked_via_client(tmp_path):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "kai_read", {"path": "../../etc/passwd"}, raise_on_error=False
        )
        assert result.is_error


async def test_history_and_revert_roundtrip(tmp_path):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        await client.call_tool("kai_write", {"path": "plan.md", "content": "v1\n"})
        await client.call_tool(
            "kai_edit", {"path": "plan.md", "old_string": "v1", "new_string": "v2"}
        )
        hist = (await client.call_tool("kai_history", {"path": "plan.md"})).data
        assert len(hist) == 2
        assert hist[0]["author"] == "tester"  # newest first (the edit)

        first_sha = hist[-1]["sha"]  # the original write
        res = (
            await client.call_tool(
                "kai_revert", {"path": "plan.md", "commit": first_sha}
            )
        ).data
        assert res["reverted_to"] == first_sha
        assert res["sha"]  # the rollback is recorded as a real new commit
        read = (await client.call_tool("kai_read", {"path": "plan.md"})).data
        assert read["content"] == "v1\n"
        # Revert is a NEW commit on top — history grew, nothing was rewritten.
        hist2 = (await client.call_tool("kai_history", {"path": "plan.md"})).data
        assert len(hist2) == 3


async def test_revert_unknown_commit_is_error(tmp_path):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        await client.call_tool("kai_write", {"path": "x.md", "content": "hi\n"})
        result = await client.call_tool(
            "kai_revert", {"path": "x.md", "commit": "deadbeef"}, raise_on_error=False
        )
        assert result.is_error
