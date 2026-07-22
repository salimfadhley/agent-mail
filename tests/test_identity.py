"""Unit tests for per-request identity resolution and the ASGI middleware."""

from __future__ import annotations

import pytest

from agent_mail.config import Config
from agent_mail.exceptions import ConfigError
from agent_mail.identity import (
    reset_current_agent,
    resolve_identity,
    set_current_agent,
)
from agent_mail.mcp_server import AgentIdentityMiddleware


def _config(project: str | None, agent: str | None) -> Config:
    return Config().model_copy(update={"project": project, "agent_id": agent})


def test_per_request_identity_wins_over_env() -> None:
    token = set_current_agent(("proj", "alice"))
    try:
        assert resolve_identity(_config("server", "default")) == ("proj", "alice")
    finally:
        reset_current_agent(token)


def test_falls_back_to_env_identity() -> None:
    assert resolve_identity(_config("proj", "bob")) == ("proj", "bob")


def test_raises_without_any_identity() -> None:
    with pytest.raises(ConfigError):
        resolve_identity(_config(None, None))


async def _noop(scope, receive, send):  # pragma: no cover - not invoked
    return None


def _extract(
    path: str, query: bytes = b"", headers: list | None = None
) -> tuple[tuple[str, str] | None, str]:
    mw = AgentIdentityMiddleware(app=_noop, mount_path="/mcp", hub_json=b"{}")
    scope = {
        "type": "http",
        "path": path,
        "query_string": query,
        "headers": headers or [],
    }
    address = mw._extract(scope)
    return address, scope["path"]


def test_path_identity_is_extracted_and_rewritten() -> None:
    address, rewritten = _extract("/agent-mail/casework/mcp")
    assert address == ("agent-mail", "casework")
    assert rewritten == "/mcp"


def test_path_identity_preserves_subpath() -> None:
    address, rewritten = _extract("/agent-mail/casework/mcp/messages")
    assert address == ("agent-mail", "casework")
    assert rewritten == "/mcp/messages"


def test_query_identity_is_extracted() -> None:
    address, rewritten = _extract("/mcp", query=b"project=proj&agent=alice")
    assert address == ("proj", "alice")
    assert rewritten == "/mcp"


def test_header_identity_is_extracted() -> None:
    address, _ = _extract(
        "/mcp", headers=[(b"x-agent-project", b"proj"), (b"x-agent-id", b"gemini")]
    )
    assert address == ("proj", "gemini")


def test_plain_mount_has_no_identity() -> None:
    address, _ = _extract("/mcp")
    assert address is None
