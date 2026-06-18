"""Filesystem operations scoped to a single tenant root.

Every path a tool receives is untrusted. `resolve_within` is the one gate: it
rejects absolute paths, parent traversal, and symlink escapes, guaranteeing the
returned path lives inside the tenant root. No tool touches the filesystem
without going through here.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp.exceptions import ToolError

from .config import Settings


class PathError(ToolError):
    """The requested path is outside the tenant root or otherwise not allowed."""


def resolve_within(root: Path, relpath: str) -> Path:
    """Resolve `relpath` against `root`, guaranteeing the result stays inside it.

    A leading "/" is treated as sugar for "from the company root" (agents often
    pass "/todos.md"). `..` traversal is rejected, and the fully resolved real
    path (symlinks included) is confirmed to be the root or below — so even an
    absolute-looking input can never escape the tenant folder.
    """
    root = root.resolve()
    rel = (relpath or "").strip().lstrip("/")
    if not rel or rel == ".":
        return root
    candidate = Path(rel)
    if candidate.is_absolute():
        raise PathError(f"absolute paths are not allowed: {relpath!r}")
    if ".." in candidate.parts:
        raise PathError(f"path traversal is not allowed: {relpath!r}")

    resolved = (root / candidate).resolve()
    # The resolved path (after following any symlinks) must be the root or below.
    if resolved != root and root not in resolved.parents:
        raise PathError(f"path escapes the tenant root: {relpath!r}")
    return resolved


def _check_suffix(settings: Settings, path: Path) -> None:
    if path.suffix.lower() not in settings.allowed_suffixes:
        raise PathError(
            f"file type {path.suffix or '(none)'!r} not allowed; "
            f"allowed: {', '.join(settings.allowed_suffixes)}"
        )


def read_file(settings: Settings, root: Path, relpath: str) -> dict:
    path = resolve_within(root, relpath)
    if not path.exists():
        raise PathError(f"file not found: {relpath!r}")
    if path.is_dir():
        raise PathError(f"{relpath!r} is a directory; use kai_list")
    _check_suffix(settings, path)
    text = path.read_text(encoding="utf-8")
    return {
        "path": str(path.relative_to(root)),
        "bytes": len(text.encode("utf-8")),
        "content": text,
    }


def write_file(
    settings: Settings, root: Path, relpath: str, content: str, mode: str = "overwrite"
) -> dict:
    if mode not in ("overwrite", "append"):
        raise PathError(f"mode must be 'overwrite' or 'append', got {mode!r}")
    path = resolve_within(root, relpath)
    if path == root or path.is_dir():
        raise PathError(f"{relpath!r} is not a writable file path")
    _check_suffix(settings, path)

    new_bytes = len(content.encode("utf-8"))
    existing = 0
    if mode == "append" and path.exists():
        existing = path.stat().st_size
    if existing + new_bytes > settings.max_file_bytes:
        raise PathError(
            f"file would exceed the {settings.max_file_bytes}-byte limit "
            f"({existing + new_bytes} bytes)"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    if mode == "append":
        with path.open("a", encoding="utf-8") as fh:
            fh.write(content)
    else:
        path.write_text(content, encoding="utf-8")

    return {
        "path": str(path.relative_to(root)),
        "mode": mode,
        "created": created,
        "bytes": path.stat().st_size,
    }


def delete_file(settings: Settings, root: Path, relpath: str) -> dict:
    """Delete a single file, or an empty folder. Goes through the same path gate.

    Non-empty folders are refused (no recursive delete — delete the files first).
    Irreversible.
    """
    path = resolve_within(root, relpath)
    if path == root:
        raise PathError("refusing to delete the company root")
    if not path.exists():
        raise PathError(f"path not found: {relpath!r}")
    if path.is_dir():
        try:
            path.rmdir()  # succeeds only if empty
        except OSError as exc:
            raise PathError(
                f"{relpath!r} is a non-empty folder; delete its files first"
            ) from exc
        return {"path": str(path.relative_to(root)), "deleted": True, "type": "dir"}
    path.unlink()
    return {"path": str(path.relative_to(root)), "deleted": True, "type": "file"}


def list_tree(settings: Settings, root: Path, folder: str = ".") -> dict:
    base = resolve_within(root, folder)
    if not base.exists():
        raise PathError(f"folder not found: {folder!r}")
    if base.is_file():
        raise PathError(f"{folder!r} is a file; use kai_read")

    entries: list[dict] = []
    for child in sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if child.name.startswith("."):
            continue
        is_dir = child.is_dir()
        entries.append(
            {
                "name": child.name,
                "path": str(child.relative_to(root)),
                "type": "dir" if is_dir else "file",
                "bytes": None if is_dir else child.stat().st_size,
            }
        )
    return {"folder": str(base.relative_to(root)) or ".", "entries": entries}


def search(settings: Settings, root: Path, query: str, limit: int = 20) -> list[dict]:
    """Case-insensitive substring search across the tenant's text files.

    Returns one hit per matching file with the matched line numbers + snippets.
    Plain grep, no embeddings — the brain is small and the win is transparency.
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    hits: list[dict] = []
    for path in sorted(root.rglob("*")):
        if len(hits) >= limit:
            break
        if not path.is_file() or path.suffix.lower() not in settings.allowed_suffixes:
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        matches = []
        for n, line in enumerate(text.splitlines(), start=1):
            if q in line.lower():
                matches.append({"line": n, "text": line.strip()[:200]})
                if len(matches) >= 5:
                    break
        if matches:
            hits.append(
                {
                    "path": str(path.relative_to(root)),
                    "matches": matches,
                }
            )
    return hits
