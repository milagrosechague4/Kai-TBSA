"""Entrypoint: `uv run kai-mcp-empresa` or `python -m kai_mcp_empresa`."""

from __future__ import annotations

import uvicorn

from .server import build_server
from .url_auth import TokenInPathMiddleware


def main() -> None:
    mcp, settings = build_server()
    # Streamable HTTP app at settings.path (/mcp), wrapped so clients that can't
    # send an Authorization header (Claude.ai web) can put the token in the URL:
    # https://host/c/<token>/mcp  ->  /mcp + Authorization: Bearer <token>.
    app = TokenInPathMiddleware(mcp.http_app(path=settings.path))
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
