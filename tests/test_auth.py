"""Auth: the tokens file is the whitelist, and claims map to a CallerContext."""

from __future__ import annotations

import json

import pytest
from conftest import make_settings

from kai_mcp_empresa.auth import load_tokens
from kai_mcp_empresa.config import Settings
from kai_mcp_empresa.identity import ForbiddenError, context_from_claims


def test_load_tokens_shapes_claims(tmp_path):
    f = tmp_path / "tokens.json"
    f.write_text(
        json.dumps(
            {
                "tok-mili": {"tenant": "koi", "user": "mili", "role": "cfo"},
                "tok-mat": {"tenant": "koi", "user": "mat"},
            }
        )
    )
    tokens = load_tokens(f)
    assert tokens["tok-mili"]["tenant"] == "koi"
    assert tokens["tok-mili"]["client_id"] == "koi:mili"
    assert tokens["tok-mili"]["role"] == "cfo"
    # token strings are keys, never echoed into client_id
    assert "tok-mili" not in tokens["tok-mili"]["client_id"]


def test_load_tokens_requires_tenant(tmp_path):
    f = tmp_path / "tokens.json"
    f.write_text(json.dumps({"tok-x": {"user": "nobody"}}))
    with pytest.raises(SystemExit):
        load_tokens(f)


def test_context_from_claims_happy_path():
    ctx = context_from_claims({"tenant": "koi", "user": "mili", "role": "cfo"})
    assert (ctx.tenant, ctx.user, ctx.role) == ("koi", "mili", "cfo")
    assert ctx.client_id == "koi:mili"


def test_context_rejects_missing_tenant():
    with pytest.raises(ForbiddenError):
        context_from_claims({"user": "mili"})


@pytest.mark.parametrize("bad", ["../koi", "koi/secrets", "KOI", "a b", ""])
def test_context_rejects_unsafe_tenant_slug(bad):
    with pytest.raises(ForbiddenError):
        context_from_claims({"tenant": bad, "user": "x"})


def test_tokens_json_env_satisfies_fail_closed(tmp_path):
    s = Settings(
        _env_file=None,
        auth_disabled=False,
        tokens_json='{"tok-a": {"tenant": "koi", "user": "mili"}}',
        tokens_file=tmp_path / "nope.json",  # absent on purpose
        data_root=tmp_path / "data",
    )
    s.validate_fail_closed()  # must not raise — env var supplies the whitelist


def test_build_auth_reads_tokens_json_env(tmp_path):
    from kai_mcp_empresa.auth import _tokens_for

    s = Settings(
        _env_file=None,
        auth_disabled=False,
        tokens_json='{"tok-a": {"tenant": "koi", "user": "mili", "role": "cfo"}}',
        data_root=tmp_path / "data",
    )
    tokens = _tokens_for(s)
    assert tokens["tok-a"]["tenant"] == "koi"
    assert tokens["tok-a"]["client_id"] == "koi:mili"


def test_fail_closed_without_tokens_file(tmp_path):
    s = Settings(
        _env_file=None,
        auth_disabled=False,
        tokens_file=tmp_path / "nope.json",
        data_root=tmp_path / "data",
    )
    with pytest.raises(SystemExit):
        s.validate_fail_closed()


def test_dev_mode_binds_loopback_only():
    s = make_settings(host="0.0.0.0")
    with pytest.raises(SystemExit):
        s.validate_fail_closed()


def test_path_field_does_not_capture_shell_PATH(monkeypatch):
    # Regression: pydantic-settings is case-insensitive by default, so the
    # `path` field would otherwise absorb the shell's $PATH and mount the server
    # at a garbage URL. case_sensitive=True must keep them separate.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    s = Settings(_env_file=None, auth_disabled=True)
    assert s.path == "/mcp"
