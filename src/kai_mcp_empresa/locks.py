"""Per-tenant write serialization.

A mutating tool (write/edit/delete/revert) holds its tenant's lock across the
read-modify-write + git commit, so two callers editing the same brain can't lose
each other's change. One lock per tenant root, created on first use. In-process
only — the server runs as a single process (Railway: one container). A second
worker would need filesystem locking; out of scope.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

# Keyed by the resolved tenant root. Mutated only from the event loop thread
# (no await between get and set), so a plain dict is safe.
_locks: dict[Path, asyncio.Lock] = {}


def tenant_lock(root: Path) -> asyncio.Lock:
    key = root.resolve()
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock
