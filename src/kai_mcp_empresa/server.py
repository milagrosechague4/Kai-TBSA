"""FastMCP server assembly. Streamable HTTP transport, fail-closed auth."""

from __future__ import annotations

from fastmcp import FastMCP

from .auth import build_auth
from .config import Settings, get_settings
from .embeddings import build_embedder
from .tools.read import register_read_tools
from .tools.special import register_special_tools


def build_server(settings: Settings | None = None) -> tuple[FastMCP, Settings]:
    settings = settings or get_settings()
    settings.validate_fail_closed()

    mcp = FastMCP(name="kai-mcp-empresa", auth=build_auth(settings))
    embedder = build_embedder(settings)
    register_read_tools(mcp, settings, embedder)
    register_special_tools(mcp, settings)
    return mcp, settings
