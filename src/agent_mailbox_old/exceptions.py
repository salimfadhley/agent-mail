"""Project-wide exception hierarchy.

Every failure agent-inbox raises on purpose derives from :class:`AgentInboxError`, so
callers (and the CLI / MCP process boundaries) can catch the whole family with one
``except`` while still being able to catch narrowly where they can recover.
"""

from __future__ import annotations


class AgentInboxError(RuntimeError):
    """Base class for all deliberate agent-inbox failures."""


class ConfigError(AgentInboxError):
    """Configuration is missing or invalid (e.g. no agent identity, bad agent id)."""


class MailboxError(AgentInboxError):
    """A mailbox operation failed (e.g. reading a message that is not there)."""


# Deprecated alias for the pre-rename name; kept so external callers don't break.
AgentMailError = AgentInboxError
