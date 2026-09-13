"""The company-brain toolset: read/write/list/search over markdown files, plus
who_am_i. Every call resolves the tenant from the token and scopes the path to
that tenant's folder — the tenant is never an argument."""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from .. import fs
from ..config import Settings
from ..git import CommitContext
from ..identity import current_context, tenant_root
from ..locks import tenant_lock


_WRITE_ALLOWED = {"directora_operativa", "gerente_proyectos", "dev"}

def _check_write(ctx) -> dict | None:
    """Return an error dict if the caller can't write, None if they can."""
    if (ctx.role or "") not in _WRITE_ALLOWED:
        return {
            "error": "Sin permiso de escritura.",
            "detalle": f"Tu rol ({ctx.role}) es de solo lectura. Para actualizar el brain, contactá a Mila o Gustavo.",
        }
    return None


def register_tools(mcp: FastMCP, settings: Settings) -> None:
    def _root():
        ctx = current_context(settings)
        return ctx, tenant_root(settings, ctx)

    @mcp.tool
    async def kai_read(path: str, offset: int = 0, limit: int = 0) -> dict:
        """Read a file from the company brain — whole, or a window of it.

        `path` is relative to the company root (e.g. "todos.md",
        "reuniones/2026-06-17.md"). For long files (transcripts, ledgers) pass
        `offset` (1-based start line) and `limit` (max lines) to page through
        without loading everything; the result includes `total_lines` and the
        `start_line`/`end_line` returned. Use kai_list to discover paths and
        kai_search to find files by content.
        """
        _, root = _root()
        return fs.read_file(settings, root, path, offset, limit)

    @mcp.tool
    async def kai_write(path: str, content: str, mode: str = "overwrite") -> dict:
        """Create or update a file in the company brain.

        `path` is relative to the company root; parent folders are created as
        needed. `mode` is "overwrite" (replace the file) or "append" (add to the
        end — use for logs, to-do lists, meeting notes). Only text files are
        allowed (.md, .txt, .json, .csv, .yaml). Returns the written path + size.
        """
        ctx, root = _root()
        if err := _check_write(ctx):
            return err
        cc = CommitContext(user=ctx.user, role=ctx.role, tool="kai_write")
        async with tenant_lock(root):
            return await asyncio.to_thread(
                fs.write_file, settings, root, path, content, mode, cc
            )

    @mcp.tool
    async def kai_edit(
        path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> dict:
        """Make a targeted edit to an existing file — replace exact text in place.

        Prefer this over kai_write for changing part of a file: only the text
        matching `old_string` changes, so editing one row of a shared file (a line
        in todos.md or loops-abiertos.md) never rewrites or clobbers the rest.
        `old_string` must match exactly (whitespace included) and be unique — add
        surrounding context if it isn't, or set `replace_all` to replace every
        occurrence. Returns the path and number of replacements.
        """
        ctx, root = _root()
        if err := _check_write(ctx):
            return err
        cc = CommitContext(user=ctx.user, role=ctx.role, tool="kai_edit")
        async with tenant_lock(root):
            return await asyncio.to_thread(
                fs.edit_file, settings, root, path, old_string, new_string, replace_all, cc
            )

    @mcp.tool
    async def kai_delete(path: str) -> dict:
        """Delete a file (or an empty folder) from the company brain.

        `path` is relative to the company root (e.g. "old-note.md",
        "drafts/stale.md"). Removes one file; an empty folder is removed too.
        Non-empty folders are refused — delete their files first. This is
        irreversible, so confirm the path with kai_list before calling.
        """
        ctx, root = _root()
        if err := _check_write(ctx):
            return err
        cc = CommitContext(user=ctx.user, role=ctx.role, tool="kai_delete")
        async with tenant_lock(root):
            return await asyncio.to_thread(fs.delete_file, settings, root, path, cc)

    @mcp.tool
    async def kai_list(folder: str = ".") -> dict:
        """List files and subfolders inside a folder of the company brain.

        `folder` is relative to the company root (default = the root itself).
        Returns each entry's name, path, type (file/dir) and size. Walk into
        subfolders by passing their path back in.
        """
        _, root = _root()
        return fs.list_tree(settings, root, folder)

    @mcp.tool
    async def kai_search(query: str, limit: int = 20) -> list[dict]:
        """Search the company brain for files containing a phrase.

        Case-insensitive substring match across every text file. Returns one
        result per matching file with the matched line numbers and snippets, so
        you can then kai_read the relevant file.
        """
        _, root = _root()
        limit = max(1, min(limit, 100))
        return fs.search(settings, root, query, limit)

    @mcp.tool
    async def kai_history(path: str, limit: int = 20) -> list[dict]:
        """Show the change history of a file in the company brain.

        Returns the commits that touched `path`, newest first — each with a short
        `sha`, the `author` (the teammate who made the change), a relative and ISO
        date, and the message. Use the `sha` with kai_revert to restore a previous
        version. Empty if the file has no recorded history yet.
        """
        _, root = _root()
        limit = max(1, min(limit, 100))
        return fs.file_history(settings, root, path, limit)

    @mcp.tool
    async def kai_revert(path: str, commit: str) -> dict:
        """Restore a file to how it was at a previous commit.

        `commit` is a `sha` from kai_history. The file's content is rolled back to
        that version and the rollback is saved as a NEW commit on top — history is
        never rewritten, so every change (including this one) stays auditable.
        Returns the path, the commit reverted to, and the new commit sha.
        """
        ctx, root = _root()
        cc = CommitContext(user=ctx.user, role=ctx.role, tool="kai_revert")
        async with tenant_lock(root):
            return await asyncio.to_thread(fs.revert_file, settings, root, path, commit, cc)

    @mcp.tool
    async def kai_sources(route_for: str = "", limit: int = 10) -> dict:
        """Return the Source Directory entries the caller is authorized to access.

        Reads `source-directory.md` from the tenant brain, filters by the
        caller's role, and returns metadata to guide which files to kai_read.
        Pass `route_for` with a topic or question to rank entries by relevance.
        `limit` caps results (max 30). Blocked entries are listed separately so
        the agent can cite the steward rather than inventing inaccessible content.
        """
        ctx, root = _root()
        limit = max(1, min(limit, 30))

        # Maps token role → Source Directory role name
        _TOKEN_TO_SD: dict[str, str] = {
            "ceo": "fundador",
            "consultora": "directora_operativa",
            "office_manager": "coordinadora",
            "gerente_proyectos": "gerente_proyectos",
            "dev": "*",
        }
        sd_role = _TOKEN_TO_SD.get(ctx.role or "", "unknown")

        try:
            raw = fs.read_file(settings, root, "source-directory.md")["content"]
        except fs.PathError:
            return {"error": "source-directory.md not found in brain", "sources": []}

        import re as _re

        import unicodedata as _ud

        def _norm(s: str) -> str:
            return _ud.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()

        _q_norm = _norm(route_for) if route_for else ""
        _q_words = _q_norm.split()

        sources = []
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if any(c.startswith("-") for c in cells):
                continue
            if cells and cells[0].upper() == "ID":
                continue
            if len(cells) < 7:
                continue

            src_id = cells[0].strip("`")
            name = cells[1]
            domain = cells[2]
            owner = cells[3]
            steward = cells[4]
            access_raw = cells[5]
            status = cells[6]
            link_cell = cells[7] if len(cells) >= 8 else None

            # Extract URL from markdown link syntax [text](url)
            link: str | None = None
            if link_cell and link_cell not in ("—", "-", ""):
                m = _re.search(r"\(([^)]+)\)", link_cell)
                link = m.group(1) if m else link_cell

            # Access check
            if sd_role == "*":
                has_access = True
            elif access_raw.strip() == "todos":
                has_access = True
            else:
                allowed = {r.strip() for r in access_raw.split(",")}
                has_access = sd_role in allowed

            # Relevance score: accent-normalized, field-weighted, phrase bonus
            score = 0
            if route_for:
                id_n, name_n, domain_n = _norm(src_id), _norm(name), _norm(domain)
                score += sum(3 for w in _q_words if w in id_n)
                score += sum(3 for w in _q_words if w in name_n)
                score += sum(2 for w in _q_words if w in domain_n)
                if _q_norm in f"{id_n} {name_n} {domain_n}":
                    score += 10

            if not has_access:
                continue

            entry: dict = {
                "id": src_id,
                "name": name,
                "domain": domain,
                "owner": owner,
                "steward": steward,
                "status": status,
                "_score": score,
            }
            if link:
                entry["link"] = link

            sources.append(entry)

        if route_for:
            sources.sort(key=lambda s: -s["_score"])
        else:
            sources.sort(key=lambda s: s["name"])

        for s in sources:
            del s["_score"]

        sources = sources[:limit]

        return {
            "caller": {"user": ctx.user, "role": ctx.role, "sd_role": sd_role},
            "total_returned": len(sources),
            "accessible": sources,
        }

    @mcp.tool
    async def who_am_i() -> dict:
        """Return the caller's identity: which company (tenant), user, and role.

        Lets the agent ground "my company", "my role" without the user repeating
        it. If the brain has a `_identity/<user>.md` profile, its content is
        included so the agent can resolve peers, tone, and standard references.
        """
        ctx, root = _root()
        profile = None
        try:
            profile = fs.read_file(settings, root, f"_identity/{ctx.user}.md")["content"]
        except fs.PathError:
            pass
        return {
            "tenant": ctx.tenant,
            "user": ctx.user,
            "role": ctx.role,
            "profile": profile,
        }

    @mcp.tool
    async def kai_runtime_config() -> dict:
        """Return server version, available tools, and caller identity.

        Use to verify connectivity and detect server updates. The returned
        `tools` list is the authoritative catalog for this deployment — compare
        it against the plugin's local skill catalog to know when a ZIP update
        is available.
        """
        from datetime import datetime, timezone

        ctx, _ = _root()
        return {
            "server": {
                "name": "kai-mcp-empresa",
                "version": "0.1.0",
                "mcp_path": settings.path,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            "caller": {"tenant": ctx.tenant, "user": ctx.user, "role": ctx.role},
            "tools": sorted([
                "kai_read", "kai_write", "kai_edit", "kai_delete", "kai_list",
                "kai_search", "kai_history", "kai_revert", "kai_sources",
                "kai_list_drive", "kai_read_sheet", "who_am_i", "kai_runtime_config",
            ]),
        }
