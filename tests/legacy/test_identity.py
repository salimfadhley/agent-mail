"""Unit tests for per-request identity resolution and the ASGI middleware."""

from __future__ import annotations

import pytest

from agent_mailbox_old.config import Config
from agent_mailbox_old.exceptions import ConfigError
from agent_mailbox_old.identity import (
    reset_current_agent,
    resolve_identity,
    set_current_agent,
)
from agent_mailbox_old.mcp_server import AgentIdentityMiddleware


def _config(project: str | None, agent: str | None) -> Config:
    return Config().model_copy(update={"project": project, "agent_id": agent})


def test_per_request_identity_wins_over_env() -> None:
    token = set_current_agent(("proj", "alice", None))
    try:
        assert resolve_identity(_config("server", "default")) == ("proj", "alice", None)
    finally:
        reset_current_agent(token)


def test_falls_back_to_env_identity() -> None:
    assert resolve_identity(_config("proj", "bob")) == ("proj", "bob", None)


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
    address, rewritten = _extract("/agent-inbox/casework/mcp")
    assert address == ("agent-inbox", "casework", None)
    assert rewritten == "/mcp"


def test_path_identity_preserves_subpath() -> None:
    address, rewritten = _extract("/agent-inbox/casework/mcp/messages")
    assert address == ("agent-inbox", "casework", None)
    assert rewritten == "/mcp/messages"


def test_query_identity_is_extracted() -> None:
    address, rewritten = _extract("/mcp", query=b"project=proj&agent=alice")
    assert address == ("proj", "alice", None)
    assert rewritten == "/mcp"


def test_header_identity_is_extracted() -> None:
    address, _ = _extract(
        "/mcp", headers=[(b"x-agent-project", b"proj"), (b"x-agent-id", b"gemini")]
    )
    assert address == ("proj", "gemini", None)


def test_plain_mount_has_no_identity() -> None:
    address, _ = _extract("/mcp")
    assert address is None


# -- three-part identity (mission 0011) ---------------------------------------


def test_role_is_extracted_from_the_url() -> None:
    """/<project>/<agent>/<role>/mcp — the role is the third position."""
    address, rewritten = _extract("/agent-inbox/claude/admin/mcp")
    assert address == ("agent-inbox", "claude", "admin")
    assert rewritten == "/mcp"


def test_role_url_preserves_subpath() -> None:
    address, rewritten = _extract("/agent-inbox/claude/admin/mcp/messages")
    assert address == ("agent-inbox", "claude", "admin")
    assert rewritten == "/mcp/messages"


def test_two_part_urls_are_unaffected() -> None:
    """Most agents hold no role; their existing URLs must keep working untouched."""
    address, rewritten = _extract("/proj/claude/mcp")
    assert address == ("proj", "claude", None)
    assert rewritten == "/mcp"


def test_role_from_query_and_header() -> None:
    address, _ = _extract("/mcp", query=b"project=p&agent=a&role=host")
    assert address == ("p", "a", "host")
    address, _ = _extract(
        "/mcp",
        headers=[
            (b"x-agent-project", b"p"),
            (b"x-agent-id", b"a"),
            (b"x-agent-role", b"host"),
        ],
    )
    assert address == ("p", "a", "host")


def test_role_falls_back_to_config_for_stdio() -> None:
    config = _config("proj", "claude").model_copy(update={"role": "admin"})
    assert resolve_identity(config) == ("proj", "claude", "admin")
