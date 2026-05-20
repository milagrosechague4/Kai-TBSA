"""Unit tests for the tenant-from-claims mapping (401/403 logic) — no DB needed."""

from __future__ import annotations

import pytest
from conftest import TENANT_A, TENANT_B, make_settings

from kai_mcp_empresa.identity import ForbiddenError, context_from_claims


def test_valid_token_same_tenant_maps_context():
    s = make_settings(tenant_id=TENANT_A)
    ctx = context_from_claims(
        s,
        {"sub": "user_abc", "org_id": TENANT_A, "role": "analyst", "acl_tags": ["finance"]},
    )
    assert ctx.tenant_id == TENANT_A
    assert ctx.user_id == "user_abc"
    assert ctx.role == "analyst"
    assert ctx.acl_tags == ("finance",)


def test_token_for_other_tenant_is_forbidden():
    s = make_settings(tenant_id=TENANT_A)
    with pytest.raises(ForbiddenError):
        context_from_claims(s, {"sub": "user_abc", "org_id": TENANT_B})


def test_token_missing_tenant_claim_is_forbidden():
    s = make_settings(tenant_id=TENANT_A)
    with pytest.raises(ForbiddenError):
        context_from_claims(s, {"sub": "user_abc"})


def test_single_string_acl_is_normalized_to_tuple():
    s = make_settings(tenant_id=TENANT_A)
    ctx = context_from_claims(s, {"sub": "u", "org_id": TENANT_A, "acl_tags": "finance"})
    assert ctx.acl_tags == ("finance",)
