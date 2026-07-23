"""Runtime configuration and addressing for agent-mail.

Every setting has one canonical name, usable identically as a lowercase TOML key or
as an environment variable (e.g. TOML ``db`` == env ``AGENT_MAIL_DB``). Values are
resolved from four layers, later ones winning:

    field defaults  <  baked defaults.toml  <  runtime --config file  <  environment

Addresses are two-part — ``<project>/<agent>``. A bare ``<project>`` targets any one
agent on that project; ``<project>/*`` broadcasts to every agent.

Storage is a single local SQLite file (``db``) — no external services.
"""

from __future__ import annotations

import logging
import os
import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from agent_mail.config_env import RUNTIME_CONFIG_ENV, runtime_config_path
from agent_mail.exceptions import ConfigError

logger = logging.getLogger(__name__)

DEFAULT_MAX_MESSAGE_BYTES = 1_048_576  # 1 MiB
DEFAULT_TTL_DAYS = 14

_BAKED_DEFAULTS = Path(__file__).parent / "defaults.toml"
_VALID_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# No secret settings today (SQLite needs no credentials); kept so redacted() stays
# generic if secret-bearing settings are ever added.
_SECRET_FIELDS: frozenset[str] = frozenset()


def default_db_path() -> str:
    """The default SQLite file: ``$XDG_DATA_HOME/agent-mail/agent-mail.db``."""
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return str(root / "agent-mail" / "agent-mail.db")


def _validate_token(value: str, kind: str) -> str:
    if not _VALID_TOKEN.match(value):
        raise ConfigError(
            f"invalid {kind} {value!r}: use letters, digits, '-' or '_' "
            "(must start alphanumeric; no dots, spaces or wildcards), max 64 chars"
        )
    return value


def validate_agent_id(agent_id: str) -> str:
    """Return ``agent_id`` if it is a safe address token, else raise."""
    return _validate_token(agent_id, "agent id")


def validate_project(project: str) -> str:
    """Return ``project`` if it is a safe address token, else raise."""
    return _validate_token(project, "project")


def format_address(project: str, agent: str) -> str:
    """Return the canonical ``<project>/<agent>`` address string."""
    return f"{validate_project(project)}/{validate_agent_id(agent)}"


def parse_target(to: str) -> tuple[str, str, str | None]:
    """Resolve an address string to ``(kind, project, agent)``.

    * ``project/agent`` -> ``("direct", project, agent)`` — one specific agent;
    * ``project`` -> ``("any", project, None)`` — exactly one agent on the project;
    * ``project/*`` -> ``("broadcast", project, None)`` — every agent on the project.

    Validates every token, raising :class:`ConfigError` on a malformed address.
    """
    to = to.strip()
    if "/" not in to:
        return "any", validate_project(to), None
    project, name = to.split("/", 1)
    if name == "*":
        return "broadcast", validate_project(project), None
    return "direct", validate_project(project), validate_agent_id(name)


def _alias(toml_key: str, env_name: str) -> AliasChoices:
    """Accept a setting under its lowercase TOML key or its uppercase env name."""
    return AliasChoices(toml_key, env_name)


class Config(BaseSettings):
    """Resolved storage, identity, server and hub settings.

    Frozen: to vary a field build a copy with :meth:`~pydantic.BaseModel.model_copy`.
    """

    # case_sensitive is essential: the lowercase TOML aliases (e.g. ``path``, ``host``)
    # must NOT match ubiquitous uppercase env vars (``PATH``, ``HOST``, ``USER``, …).
    model_config = SettingsConfigDict(frozen=True, extra="ignore", case_sensitive=True)

    # -- storage (single local SQLite file) -------------------------------
    db: str = Field(
        default_factory=default_db_path, validation_alias=_alias("db", "AGENT_MAIL_DB")
    )
    ttl_days: int = Field(
        default=DEFAULT_TTL_DAYS,
        validation_alias=_alias("ttl_days", "AGENT_MAIL_TTL_DAYS"),
    )
    max_message_bytes: int = Field(
        default=DEFAULT_MAX_MESSAGE_BYTES,
        validation_alias=_alias("max_message_bytes", "AGENT_MAIL_MAX_MESSAGE_BYTES"),
    )

    # -- identity (two-part: project + agent) -----------------------------
    project: str | None = Field(
        default=None, validation_alias=_alias("project", "AGENT_MAIL_PROJECT")
    )
    agent_id: str | None = Field(
        default=None, validation_alias=_alias("agent_id", "AGENT_ID")
    )

    # -- MCP server -------------------------------------------------------
    transport: str = Field(
        default="stdio", validation_alias=_alias("transport", "AGENT_MAIL_TRANSPORT")
    )
    host: str = Field(
        default="127.0.0.1", validation_alias=_alias("host", "AGENT_MAIL_HOST")
    )
    port: int = Field(default=8080, validation_alias=_alias("port", "AGENT_MAIL_PORT"))
    path: str = Field(
        default="/mcp", validation_alias=_alias("path", "AGENT_MAIL_PATH")
    )
    public_url: str | None = Field(
        default=None, validation_alias=_alias("public_url", "AGENT_MAIL_PUBLIC_URL")
    )

    # -- hub identity & administration (advertised via hub_info) ----------
    hub: str = Field(
        default="agent-mail", validation_alias=_alias("hub", "AGENT_MAIL_HUB")
    )
    hub_description: str | None = Field(
        default=None,
        validation_alias=_alias("hub_description", "AGENT_MAIL_HUB_DESCRIPTION"),
    )
    admin_agent: str | None = Field(
        default=None, validation_alias=_alias("admin_agent", "AGENT_MAIL_ADMIN_AGENT")
    )
    issue_url: str | None = Field(
        default=None, validation_alias=_alias("issue_url", "AGENT_MAIL_ISSUE_URL")
    )
    contact: str | None = Field(
        default=None, validation_alias=_alias("contact", "AGENT_MAIL_CONTACT")
    )

    # -- ops --------------------------------------------------------------
    log_level: str = Field(
        default="WARNING", validation_alias=_alias("log_level", "AGENT_MAIL_LOG_LEVEL")
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Highest priority first: init kwargs, then env, then the runtime --config
        # file, then the baked defaults.toml shipped in the package.
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        runtime = runtime_config_path()
        if runtime is not None:
            if not runtime.is_file():
                raise ConfigError(
                    f"config file not found: {runtime} "
                    f"(from {RUNTIME_CONFIG_ENV} / --config)"
                )
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=runtime))
        if _BAKED_DEFAULTS.is_file():
            sources.append(
                TomlConfigSettingsSource(settings_cls, toml_file=_BAKED_DEFAULTS)
            )
        return tuple(sources)

    @classmethod
    def from_env(
        cls, agent_override: str | None = None, project_override: str | None = None
    ) -> Config:
        """Build config from all layers, letting the overrides win on identity."""
        config = cls()
        updates: dict[str, str] = {}
        if project_override:
            updates["project"] = project_override
        if agent_override:
            updates["agent_id"] = agent_override
        if updates:
            config = config.model_copy(update=updates)
        return config

    def require_address(self) -> tuple[str, str]:
        """Return the validated ``(project, agent)``, or raise if either is missing."""
        if not self.project:
            raise ConfigError(
                "no project: set AGENT_MAIL_PROJECT or pass --project <project>"
            )
        if not self.agent_id:
            raise ConfigError("no agent name: set AGENT_ID or pass --from <agent>")
        return validate_project(self.project), validate_agent_id(self.agent_id)

    def address(self) -> str | None:
        """Return the ``<project>/<agent>`` string, or None if not fully set."""
        if self.project and self.agent_id:
            return format_address(self.project, self.agent_id)
        return None

    def base_url(self) -> str:
        """Return the advertised base URL agents should connect to."""
        if self.public_url:
            return self.public_url.rstrip("/")
        host = "localhost" if self.host in ("0.0.0.0", "127.0.0.1") else self.host
        return f"http://{host}:{self.port}"

    def redacted(self) -> dict[str, object]:
        """Return the effective config with any secrets masked, for banners/logs."""
        data = self.model_dump()
        for key in _SECRET_FIELDS:
            if data.get(key):
                data[key] = "***"
        return data


def _hub_version() -> str:
    try:
        return _pkg_version("agent-mail")
    except PackageNotFoundError:  # pragma: no cover - source checkout w/o metadata
        return "0.0.0"


def hub_descriptor(
    config: Config, max_message_bytes: int | None = None
) -> dict[str, object]:
    """Return the hub's public, non-secret self-description for discovery.

    ``max_message_bytes`` is the effective per-message size limit; falls back to the
    configured cap when not supplied.
    """
    base = config.base_url()
    connect = (
        f"{base}/<project>/<agent>{config.path}"
        if config.transport == "http"
        else f"{base}{config.path.rstrip('/')}"
    )
    return {
        "hub": config.hub,
        "description": config.hub_description,
        "version": _hub_version(),
        "storage": "sqlite",
        "addressing": {
            "direct": "project/agent",
            "any": "project (bare) — one agent on the project",
            "broadcast": "project/* — every agent on the project",
        },
        "limits": {
            "max_message_bytes": (
                max_message_bytes
                if max_message_bytes is not None
                else config.max_message_bytes
            ),
        },
        "connect_url_template": connect,
        "transport": config.transport,
        "admin_agent": config.admin_agent,
        "issue_url": config.issue_url,
        "contact": config.contact,
        "tools": [
            "send_message",
            "check_inbox",
            "read_message",
            "reply_message",
            "notify_agent",
            "ping",
            "hub_info",
        ],
    }
