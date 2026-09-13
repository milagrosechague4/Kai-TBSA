"""Push brain-staging updates al Kai TBSA en Railway.

Sesión 03/09/2026: Drive 00-09 + Cobertura Mediática sheet + punteros brain.
Empuja: _index.md (actualizado) + drive-mapa.md (nuevo) + comunicacion/comunicacion-estado.md (nuevo).
"""

import asyncio
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = "https://stunning-spontaneity-production-4cbd.up.railway.app/mcp"
TOKEN = "kai_tbsa_gLVDyb8HcF5IVslThXDMwHcN4ZsPXB5z"

BRAIN_STAGING = Path.home() / "Documents/Mila-life/proyectos/tbsa/brain-staging"

FILES_TO_PUSH = [
    ("_index.md", BRAIN_STAGING / "_index.md"),
    ("drive-mapa.md", BRAIN_STAGING / "drive-mapa.md"),
    ("comunicacion/comunicacion-estado.md", BRAIN_STAGING / "comunicacion/comunicacion-estado.md"),
]


async def main() -> None:
    transport = StreamableHttpTransport(URL, headers={"Authorization": f"Bearer {TOKEN}"})
    async with Client(transport) as c:
        for brain_path, local_path in FILES_TO_PUSH:
            content = local_path.read_text(encoding="utf-8")
            res = await c.call_tool(
                "kai_write",
                {"path": brain_path, "content": content, "mode": "overwrite"},
            )
            print(f"✓ {brain_path} — {res.content[0].text if res.content else res}")

        # Verificar root
        root = await c.call_tool("kai_list", {"folder": "."})
        entries = root.content[0].text if root.content else str(root)
        print(f"\nRoot del brain:\n{entries}")


if __name__ == "__main__":
    asyncio.run(main())
