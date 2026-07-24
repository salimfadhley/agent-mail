"""agent-inbox — a local SQLite mailbox for local LLM agents."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from agent_mailbox_old.config import Config
from agent_mailbox_old.exceptions import (
    AgentInboxError,
    AgentMailError,  # deprecated alias
    ConfigError,
    MailboxError,
)
from agent_mailbox_old.mailbox import Mailbox
from agent_mailbox_old.models import Intent, Message

try:
    __version__ = version("agent-inbox")
except PackageNotFoundError:  # pragma: no cover - source checkout without metadata
    __version__ = "0.0.0"


def main(argv: list[str] | None = None) -> None:
    """Console entry point (kept importable as ``agent_mailbox_old.main``)."""
    from agent_mailbox_old.cli import main as _main

    _main(argv)


__all__ = [
    "AgentInboxError",
    "AgentMailError",
    "Config",
    "ConfigError",
    "Intent",
    "Mailbox",
    "MailboxError",
    "Message",
    "__version__",
    "main",
]
