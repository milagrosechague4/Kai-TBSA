"""The company-brain toolset: read/write/list/search over markdown files, plus
who_am_i. Every call resolves the tenant from the token and scopes the path to
that tenant's folder — the tenant is never an argument."""

from __future__ import annotations

from fastmcp import FastMCP

from .. import fs
from ..config import Settings
from ..identity import current_context, tenant_root


def register_tools(mcp: FastMCP, settings: Settings) -> None:
    def _root():
        ctx = current_context(settings)
        return ctx, tenant_root(settings, ctx)

    @mcp.tool
    async def kai_read(path: str) -> dict:
        """Read a single file from the company brain.

        `path` is relative to the company root (e.g. "todos.md",
        "reuniones/2026-06-17.md"). Returns the file's text content. Use kai_list
        to discover paths and kai_search to find files by content.
        """
        _, root = _root()
        return fs.read_file(settings, root, path)

    @mcp.tool
    async def kai_write(path: str, content: str, mode: str = "overwrite") -> dict:
        """Create or update a file in the company brain.

        `path` is relative to the company root; parent folders are created as
        needed. `mode` is "overwrite" (replace the file) or "append" (add to the
        end — use for logs, to-do lists, meeting notes). Only text files are
        allowed (.md, .txt, .json, .csv, .yaml). Returns the written path + size.
        """
        _, root = _root()
        return fs.write_file(settings, root, path, content, mode)

    @mcp.tool
    async def kai_delete(path: str) -> dict:
        """Delete a file (or an empty folder) from the company brain.

        `path` is relative to the company root (e.g. "old-note.md",
        "drafts/stale.md"). Removes one file; an empty folder is removed too.
        Non-empty folders are refused — delete their files first. This is
        irreversible, so confirm the path with kai_list before calling.
        """
        _, root = _root()
        return fs.delete_file(settings, root, path)

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
