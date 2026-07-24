"""agent-mailbox — a SQLite mailbox for local LLM agents.

One HTTP API is the hub's only machine interface; the CLI, a local stdio MCP server
and the human console are all clients of it. The messaging model follows
ActivityStreams, and identity is issued by the hub rather than derived from facts.

The binding decisions live in ``docs/decisions/``:

* ADR 0003 — identity is a surrogate key, not a natural key
* ADR 0004 — the messaging model follows ActivityStreams
* ADR 0005 — one API; every client is a client
* ADR 0006 — SQLite, with typed columns plus a document column

This package is written from scratch. The superseded implementation is kept at
``agent_mailbox_old`` for historical reference only and is deleted once this one is
green; nothing here may import from it.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agent-mailbox")
except PackageNotFoundError:  # pragma: no cover - only when running from a bare tree
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
