"""Push the local TBSA brain (data/tbsa/**) to the live Railway server via kai_write.

Idempotent — overwrites. Run after updating any brain file locally.

Usage:
    uv run scripts/sync_tbsa_brain.py
    uv run scripts/sync_tbsa_brain.py --user sebastian  # use Sebastián's token
"""

import asyncio
import json
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "data" / "tbsa"
URL = "https://stunning-spontaneity-production-4cbd.up.railway.app/mcp"


def _token(user: str = "mila") -> str:
    toks = json.loads((REPO / "tokens.json").read_text(encoding="utf-8"))
    match = next(
        (t for t, m in toks.items() if m.get("user") == user and m.get("tenant") == "tbsa"),
        None,
    )
    if not match:
        raise SystemExit(f"No token found for user={user!r} tenant=tbsa in tokens.json")
    return match


async def main(user: str = "mila") -> None:
    token = _token(user)
    transport = StreamableHttpTransport(URL, headers={"Authorization": f"Bearer {token}"})

    files = sorted(p for p in SEED.rglob("*") if p.is_file() and not p.name.startswith("."))
    print(f"Syncing {len(files)} files to Railway as user={user!r}...\n")

    async with Client(transport) as c:
        for p in files:
            rel = p.relative_to(SEED).as_posix()
            try:
                res = await c.call_tool(
                    "kai_write",
                    {"path": rel, "content": p.read_text(encoding="utf-8"), "mode": "overwrite"},
                )
                print(f"  ✓ {rel} ({res.data.get('bytes', '?')} bytes)")
            except Exception as e:
                print(f"  ✗ {rel} — ERROR: {e}")

        # Verify
        root = (await c.call_tool("kai_list", {"folder": "."})).data
        names = sorted(e["name"] for e in root.get("entries", []))
        print(f"\nBrain root ({len(names)} entries): {names}")


if __name__ == "__main__":
    user = "mila"
    for arg in sys.argv[1:]:
        if arg.startswith("--user="):
            user = arg.split("=", 1)[1]
        elif arg == "--user" and len(sys.argv) > sys.argv.index(arg) + 1:
            user = sys.argv[sys.argv.index(arg) + 1]
    asyncio.run(main(user))
