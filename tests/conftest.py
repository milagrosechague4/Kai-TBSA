"""Shared test helpers."""

from __future__ import annotations

import tempfile
from pathlib import Path

from kai_mcp_empresa.config import Settings

DEV_TENANT = "testco"
DEV_USER = "tester"


def make_settings(**over) -> Settings:
    """Auth-disabled settings on a throwaway data root (one per call unless given)."""
    base = dict(
        auth_disabled=True,
        dev_tenant=DEV_TENANT,
        dev_user=DEV_USER,
        data_root=Path(tempfile.mkdtemp(prefix="kai-test-")),
    )
    base.update(over)
    return Settings(_env_file=None, **base)
