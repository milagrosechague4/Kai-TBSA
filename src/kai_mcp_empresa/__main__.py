"""Entrypoint: `uv run kai-mcp-empresa` or `python -m kai_mcp_empresa`."""

from __future__ import annotations

from .server import build_server


def main() -> None:
    mcp, settings = build_server()
    mcp.run(
        transport="http",
        host=settings.host,
        port=settings.port,
        path=settings.path,
    )


if __name__ == "__main__":
    main()
