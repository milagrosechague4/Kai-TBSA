"""Provision a tenant: create its brain folder, add whitelisted users to the
tokens file, and seed an optional _identity profile per user. Idempotent.

Usage:
    uv run python scripts/install_tenant.py installs/koi.json

Tenant spec (JSON):
    {
      "tenant": "koi",
      "name": "Koi Group",
      "users": [
        {"user": "mat",  "role": "founder"},
        {"user": "mili", "role": "cfo", "profile": "Co-CEO. Pidió el ERP de finanzas."}
      ]
    }

Each user gets a freshly generated bearer token printed once. Hand each person
their line; whoever holds a token is in the whitelist. Revoke = delete the entry
from tokens.json. The token is NEVER stored anywhere but the tokens file.
"""

from __future__ import annotations

import json
import pathlib
import secrets
import sys

from kai_mcp_empresa.config import get_settings


def install(spec_path: str) -> None:
    spec = json.loads(pathlib.Path(spec_path).read_text(encoding="utf-8"))
    tenant = spec["tenant"]
    settings = get_settings()

    # 1. Brain folder.
    root = (settings.data_root / tenant).resolve()
    root.mkdir(parents=True, exist_ok=True)

    # 2. Tokens file (the whitelist). Preserve existing entries; only add missing users.
    tokens_path = settings.tokens_file
    tokens: dict[str, dict] = {}
    if tokens_path.exists():
        tokens = json.loads(tokens_path.read_text(encoding="utf-8"))

    existing = {(m["tenant"], m["user"]) for m in tokens.values()}
    issued: list[tuple[str, str]] = []
    for u in spec["users"]:
        key = (tenant, u["user"])
        if key in existing:
            continue
        token = f"kai_{tenant}_{secrets.token_urlsafe(24)}"
        tokens[token] = {
            "tenant": tenant,
            "user": u["user"],
            "role": u.get("role"),
        }
        issued.append((u["user"], token))
        # Seed an identity profile if provided.
        if u.get("profile"):
            ident_dir = root / "_identity"
            ident_dir.mkdir(exist_ok=True)
            (ident_dir / f"{u['user']}.md").write_text(
                u["profile"] + "\n", encoding="utf-8"
            )

    tokens_path.write_text(json.dumps(tokens, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")

    print(f"Tenant {spec.get('name', tenant)!r} (slug={tenant})")
    print(f"  brain folder: {root}")
    print(f"  tokens file:  {tokens_path}  ({len(tokens)} total users)")
    if issued:
        print("  NEW tokens (hand each person their line — printed once):")
        for user, token in issued:
            print(f"    {user}: {token}")
    else:
        print("  no new users (all already in the whitelist)")


if __name__ == "__main__":
    spec = sys.argv[1] if len(sys.argv) > 1 else "installs/koi.json"
    install(spec)
