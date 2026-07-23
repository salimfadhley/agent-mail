"""agent-mail — a local SQLite mailbox for local LLM agents."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from agent_mail.config import Config
from agent_mail.exceptions import AgentMailError, ConfigError, MailboxError
from agent_mail.mailbox import Mailbox
from agent_mail.models import Intent, Message

try:
    __version__ = version("agent-inbox")
except PackageNotFoundError:  # pragma: no cover - source checkout without metadata
    __version__ = "0.0.0"


def main(argv: list[str] | None = None) -> None:
    """Console entry point (kept importable as ``agent_mail.main``)."""
    from agent_mail.cli import main as _main

    _main(argv)


__all__ = [
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
