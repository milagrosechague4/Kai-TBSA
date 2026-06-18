"""Sync the Koi ERP engagement docs into the LIVE brain via the MCP.

Per the Kai methodology, each engagement lives under `clientes/<cliente>/`. The
Koi ERP context goes to `clientes/koi/`. The ERP build itself stays in the
separate `ledger` repo — this only pushes the team-facing CONTEXT + blueprint.

Token read from gitignored tokens.json (never hardcode secrets).
"""

import asyncio
import json
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = "https://kai-mcp-empresa-production.up.railway.app/mcp"
REPO = Path(__file__).resolve().parent.parent
LEDGER = Path.home() / "Documents/GitHub/ledger"

FILES = [
    ("clientes/koi/contexto.md", LEDGER / "docs/contexto-koi-erp.md"),
    ("clientes/koi/README.md", LEDGER / "README.md"),
    ("clientes/koi/esqueleto.md", LEDGER / "docs/ESQUELETO.md"),
    ("clientes/koi/preguntas-manu.md", LEDGER / "docs/preguntas-manu.md"),
    ("clientes/koi/mensaje-manu.md", LEDGER / "docs/mensaje-manu.md"),
]

# The old root-level erp/ files predate the clientes/ reorg. No kai_delete tool
# yet, so overwrite them with a tombstone that redirects.
TOMBSTONE_PATHS = [
    "erp/README.md",
    "erp/esqueleto.md",
    "erp/preguntas-manu.md",
    "erp/mensaje-manu.md",
]
TOMBSTONE = (
    "# Movido → `clientes/koi/`\n\n"
    "Este contenido ahora vive en **`clientes/koi/`**. El cerebro sigue la "
    "metodología Kai: el contexto de cada engagement va bajo `clientes/`.\n"
)

TODO_NOTE = """

## ERP Koi — esqueleto LISTO (2026-06-17)
- [x] Revisar las pantallas de los 2 sheets (Rochi 47 tabs + Manu 61) con fórmulas reales — @mat
- [x] Esqueleto completo en repo `ledger`; contexto del engagement en `clientes/koi/` — @mat
- [ ] Pre-build #0: service account + compartir sheets + respuestas de Manu (ver `clientes/koi/preguntas-manu.md`) — @mat
> Hallazgo clave: 2 workbooks. El devengado se calcula agregando las pestañas `EERR {entidad}` de Manu (alimentadas por `Proyección`), NO directo del ledger.
"""


def _load_token(user: str = "mat") -> str:
    tokens = json.loads((REPO / "tokens.json").read_text(encoding="utf-8"))
    for tok, meta in tokens.items():
        if meta.get("user") == user and meta.get("tenant") == "koi":
            return tok
    raise SystemExit(f"no token for user={user}")


async def main() -> None:
    transport = StreamableHttpTransport(URL, headers={"Authorization": f"Bearer {_load_token()}"})
    async with Client(transport) as client:
        for dest, src in FILES:
            res = await client.call_tool(
                "kai_write",
                {"path": dest, "content": src.read_text(encoding="utf-8"), "mode": "overwrite"},
            )
            print("wrote:", res.data.get("path"), res.data.get("bytes"))

        for dead in TOMBSTONE_PATHS:
            res = await client.call_tool("kai_write", {"path": dead, "content": TOMBSTONE, "mode": "overwrite"})
            print("tombstone:", res.data.get("path"))

        if "--todo" in sys.argv:
            await client.call_tool("kai_write", {"path": "todos.md", "content": TODO_NOTE, "mode": "append"})
            print("appended todos")

        listing = await client.call_tool("kai_list", {"folder": "clientes/koi"})
        print("clientes/koi:", [e["name"] for e in listing.data["entries"]])


if __name__ == "__main__":
    asyncio.run(main())
