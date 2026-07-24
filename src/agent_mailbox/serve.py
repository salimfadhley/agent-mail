"""Running the hub.

Configuration is environment only — no config file, no flags. The hub is a container;
a container's contract is its environment, and anything else would need mounting.

Nothing here has a default that names a machine. `AGENT_MAILBOX_PUBLIC_URL` is how the
hub learns its own address, and it must be told (charter: no deployment-specific
hostnames in the repo). Everything else has a sensible default so a bare `docker run`
works.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from litestar import Litestar

from agent_mailbox.api import build_api
from agent_mailbox.house import House
from agent_mailbox.mailbox import Mailbox
from agent_mailbox.sqlite_store import SqliteStore

logger = logging.getLogger(__name__)

ENV_PREFIX = "AGENT_MAILBOX_"


def _env(name: str, default: str) -> str:
    return os.environ.get(f"{ENV_PREFIX}{name}", default).strip()


@dataclass(frozen=True, slots=True)
class Settings:
    """What the hub needs to know about where it is running."""

    db: str = "/data/agent-mailbox.db"
    host: str = "0.0.0.0"  # noqa: S104 - a container binds its own interface
    port: int = 8080
    #: How the hub refers to itself in the URIs it emits. If unset, callers still work
    #: — they just receive relative-looking identifiers, which is worse than being told.
    public_url: str = ""
    hub_name: str = "local"
    retention_days: int = 14
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        port = int(_env("PORT", "8080"))
        host = _env("HOST", "0.0.0.0")  # noqa: S104
        return cls(
            db=_env("DB", "/data/agent-mailbox.db"),
            host=host,
            port=port,
            public_url=_env("PUBLIC_URL", "") or f"http://localhost:{port}",
            hub_name=_env("HUB_NAME", "local"),
            retention_days=int(_env("RETENTION_DAYS", "14")),
            log_level=_env("LOG_LEVEL", "INFO").upper(),
        )


def build_app(settings: Settings | None = None) -> Litestar:
    """Build the hub, opening its store for the life of the application.

    The store is opened in a Litestar startup hook rather than here, so that building
    the app is cheap and testable and nothing touches the disk at import time.
    """
    config = settings or Settings.from_env()
    logging.basicConfig(level=config.log_level)

    store = SqliteStore(config.db)
    mailbox = Mailbox(
        store, hub_name=config.hub_name, retention_days=config.retention_days
    )
    house = House(mailbox)
    app = build_api(house, config.public_url)

    async def open_store(_: Litestar) -> None:
        await store.__aenter__()
        logger.info(
            "agent-mailbox serving %s as %s, storing at %s",
            config.public_url,
            config.hub_name,
            config.db,
        )

    async def close_store(_: Litestar) -> None:
        await store.__aexit__(None, None, None)

    # Opening the store must come before the house opens, or the standing residents
    # would be created against a store that is not there yet.
    app.on_startup.insert(0, open_store)
    app.on_shutdown.append(close_store)
    return app


def main() -> None:
    """Entry point for `agent-mailbox-serve` and for the container."""
    import uvicorn

    config = Settings.from_env()
    uvicorn.run(
        build_app(config),
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
