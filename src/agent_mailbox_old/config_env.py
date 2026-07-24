"""Locate the runtime ``--config`` TOML file.

Kept separate from :mod:`agent_mailbox_old.config` so the CLI can set the path before the
settings model is built, without an import cycle or mutating ``os.environ``.
"""

from __future__ import annotations

import os
from pathlib import Path

RUNTIME_CONFIG_ENV = "AGENT_INBOX_CONFIG"
_LEGACY_CONFIG_ENV = "AGENT_MAIL_CONFIG"  # deprecated alias

_override: str | None = None


def set_runtime_config_path(path: str | None) -> None:
    """Set the runtime config path (from the ``--config`` flag)."""
    global _override
    _override = path


def runtime_config_path() -> Path | None:
    """Return the runtime config file path, or None if none was provided.

    A ``--config`` flag (via :func:`set_runtime_config_path`) wins over the
    ``AGENT_INBOX_CONFIG`` environment variable (legacy ``AGENT_MAIL_CONFIG`` still
    accepted).
    """
    raw = (
        _override
        or os.environ.get(RUNTIME_CONFIG_ENV)
        or os.environ.get(_LEGACY_CONFIG_ENV)
    )
    return Path(raw).expanduser() if raw else None
