"""Per-request agent identity for the hosted MCP server.

When the MCP server runs over HTTP it is multi-tenant: many agents share one URL
*base* but each connects on its own address — ``/<project>/<agent>/mcp`` — so the
request path (or ``?project=&agent=`` / ``X-Agent-*`` headers) says who is calling.
The ASGI middleware stashes that in a :class:`~contextvars.ContextVar`; the MCP tools
read it back here. Over stdio (single-tenant) the contextvar is unset and identity
falls back to ``AGENT_MAIL_PROJECT`` / ``AGENT_ID`` from the config.
"""

from __future__ import annotations

from contextvars import ContextVar

from agent_mail.config import Config, validate_agent_id, validate_project
from agent_mail.exceptions import ConfigError

# (project, agent) for the current request, or None over stdio.
_current: ContextVar[tuple[str, str] | None] = ContextVar(
    "agent_mail_current_address", default=None
)


def set_current_agent(address: tuple[str, str] | None) -> object:
    """Bind the calling ``(project, agent)`` for this context; returns a reset token."""
    return _current.set(address)


def reset_current_agent(token: object) -> None:
    """Undo a previous :func:`set_current_agent` using its token."""
    _current.reset(token)  # type: ignore[arg-type]


def resolve_identity(config: Config) -> tuple[str, str]:
    """Return the validated ``(project, agent)`` of the calling agent.

    Prefers the per-request address (HTTP, multi-tenant); falls back to the server's
    configured project/agent (stdio). Raises :class:`ConfigError` if neither is set.
    """
    per_request = _current.get()
    if per_request is not None:
        project, agent = per_request
        return validate_project(project), validate_agent_id(agent)
    if config.project and config.agent_id:
        return validate_project(config.project), validate_agent_id(config.agent_id)
    raise ConfigError(
        "no identity for this request: connect on /<project>/<agent>/mcp "
        "(or set AGENT_MAIL_PROJECT + AGENT_ID for a single-agent server)"
    )
