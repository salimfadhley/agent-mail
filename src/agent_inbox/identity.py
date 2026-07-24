"""Per-request agent identity for the hosted MCP server.

When the MCP server runs over HTTP it is multi-tenant: many agents share one URL
*base* but each connects on its own address — ``/<project>/<agent>[/<role>]/mcp``
— so the request path (or ``?project=&agent=`` / ``X-Agent-*`` headers) says who is
calling.
The ASGI middleware stashes that in a :class:`~contextvars.ContextVar`; the MCP tools
read it back here. Over stdio (single-tenant) the contextvar is unset and identity
falls back to ``AGENT_INBOX_PROJECT`` / ``AGENT_ID`` from the config.
"""

from __future__ import annotations

from contextvars import ContextVar

from agent_inbox.config import (
    Config,
    validate_agent_id,
    validate_project,
    validate_role,
)
from agent_inbox.exceptions import ConfigError

# (project, agent, role) for the current request, or None over stdio. The role is
# optional — most agents are just agents — so it may be None.
_current: ContextVar[tuple[str, str, str | None] | None] = ContextVar(
    "agent_inbox_current_address", default=None
)


def set_current_agent(address: tuple[str, str, str | None] | None) -> object:
    """Bind the calling ``(project, agent, role)`` for this context.

    Returns a reset token. ``role`` may be ``None``: an agent without a distinct
    named role is addressed with two parts, which is the common case.
    """
    return _current.set(address)


def reset_current_agent(token: object) -> None:
    """Undo a previous :func:`set_current_agent` using its token."""
    _current.reset(token)  # type: ignore[arg-type]


def resolve_identity(config: Config) -> tuple[str, str, str | None]:
    """Return the validated ``(project, agent, role)`` of the calling agent.

    Prefers the per-request address (HTTP, multi-tenant); falls back to the server's
    configured identity (stdio). ``role`` is ``None`` when the agent holds no distinct
    role. Raises :class:`ConfigError` if no identity is available at all.
    """
    per_request = _current.get()
    if per_request is not None:
        project, agent, role = per_request
        return (
            validate_project(project),
            validate_agent_id(agent),
            validate_role(role) if role else None,
        )
    if config.project and config.agent_id:
        return (
            validate_project(config.project),
            validate_agent_id(config.agent_id),
            validate_role(config.role) if config.role else None,
        )
    raise ConfigError(
        "no identity for this request: connect on /<project>/<agent>[/<role>]/mcp "
        "(or set AGENT_INBOX_PROJECT + AGENT_ID for a single-agent server)"
    )
