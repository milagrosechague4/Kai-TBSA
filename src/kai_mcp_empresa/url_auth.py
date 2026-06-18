"""URL-embedded bearer auth.

Claude.ai / ChatGPT web custom connectors can't send a custom `Authorization`
header — they only speak OAuth. Our server uses static per-person bearer tokens.
This pure-ASGI middleware lets those clients authenticate by putting the token in
the URL path instead of a header:

    https://host/c/<token>/mcp   ->   /mcp   with   Authorization: Bearer <token>

The token is moved from the path into the header, so the normal
`StaticTokenVerifier` handles it unchanged and never 401s (so Claude never falls
into the OAuth flow). Plain `/mcp` with a real Authorization header keeps working
for header-capable clients (Claude Desktop config, our own scripts). No OAuth, no
DB — same per-person whitelist.

Tokens are `kai_<tenant>_<urlsafe>` (no `/`), so the first path segment after the
prefix is always the whole token.
"""

from __future__ import annotations

_PREFIX = "/c/"


class TokenInPathMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http":
            path: str = scope.get("path", "")
            if path.startswith(_PREFIX):
                token, _, tail = path[len(_PREFIX):].partition("/")
                if token:
                    new_path = "/" + tail  # e.g. "/c/<token>/mcp" -> "/mcp"
                    scope = dict(scope)
                    scope["path"] = new_path
                    scope["raw_path"] = new_path.encode("latin-1")
                    headers = [
                        (k, v)
                        for (k, v) in scope.get("headers", [])
                        if k.lower() != b"authorization"
                    ]
                    headers.append((b"authorization", f"Bearer {token}".encode("latin-1")))
                    scope["headers"] = headers
        await self.app(scope, receive, send)
