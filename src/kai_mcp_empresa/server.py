"""FastMCP server assembly. Streamable HTTP transport, fail-closed auth."""

from __future__ import annotations

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import build_auth
from .config import Settings, get_settings
from .fs import sweep_stale_temps
from .obs import ToolCallLogger
from .tools.airtable import register_airtable_tools
from .tools.drive import register_drive_tools
from .tools.files import register_tools
from .tools.plugin import register_plugin


def build_server(settings: Settings | None = None) -> tuple[FastMCP, Settings]:
    settings = settings or get_settings()
    settings.validate_fail_closed()

    mcp = FastMCP(name="kai-mcp-empresa", auth=build_auth(settings))
    register_tools(mcp, settings)
    register_drive_tools(mcp, settings)
    register_airtable_tools(mcp, settings)
    register_plugin(mcp, settings)

    if settings.log_toolcalls:
        mcp.add_middleware(ToolCallLogger(settings))

    sweep_stale_temps(settings.data_root)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return mcp, settings
