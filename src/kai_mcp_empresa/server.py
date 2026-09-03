"""FastMCP server assembly. Streamable HTTP transport, fail-closed auth."""

from __future__ import annotations

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import build_auth
from .config import Settings, get_settings
from .fs import sweep_stale_temps
from .obs import ToolCallLogger
from .tools.drive import register_drive_tools
from .tools.files import register_tools
from .tools.plugin import register_plugin


def build_server(settings: Settings | None = None) -> tuple[FastMCP, Settings]:
    settings = settings or get_settings()
    settings.validate_fail_closed()

    mcp = FastMCP(name="kai-mcp-empresa", auth=build_auth(settings))
    register_tools(mcp, settings)
    register_drive_tools(mcp, settings)
    register_plugin(mcp, settings)

    if settings.log_toolcalls:
        mcp.add_middleware(ToolCallLogger(settings))

    # Reap any atomic-write temp files stranded by a previous hard crash.
    sweep_stale_temps(settings.data_root)

    # Unauthenticated liveness probe for platform healthchecks (Railway, etc.).
    # The MCP endpoint itself does not 200 on a plain GET, so deploys point here.
    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return mcp, settings
