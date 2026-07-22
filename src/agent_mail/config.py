"""Runtime configuration for agent-mail.

Every setting has one canonical name, usable identically as a lowercase TOML key or
as an environment variable (e.g. TOML ``nats_url`` == env ``NATS_URL``). Values are
resolved from four layers, later ones winning:

    field defaults  <  baked defaults.toml  <  runtime --config file  <  environment

The runtime file is named by ``AGENT_MAIL_CONFIG`` (the ``--config`` flag sets it).
Environment variables always win, which is the least-surprising choice for containers.
"""

from __future__ import annotations

import logging
import re
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

DEFAULT_NATS_URL = "nats://127.0.0.1:4222"
STREAM_NAME = "AGENT_MAIL"
MAIL_SUBJECT_PREFIX = "agent.mail"
NOTIFY_SUBJECT_PREFIX = "agent.notify"

_BAKED_DEFAULTS = Path(__file__).parent / "defaults.toml"
_VALID_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Fields that must never be echoed in logs, banners, or discovery responses.
_SECRET_FIELDS = frozenset({"nats_token", "nats_password"})


def validate_agent_id(agent_id: str) -> str:
    """Return ``agent_id`` unchanged if it is a safe NATS subject token, else raise.

    Agent ids become part of a NATS subject and a JetStream durable name, so they
    must not contain dots, spaces or wildcards.
    """
    if not _VALID_AGENT_ID.match(agent_id):
        raise ConfigError(
            f"invalid agent id {agent_id!r}: use letters, digits, '-' or '_' "
            "(no dots, spaces or wildcards), max 64 chars"
        )
    return agent_id


def mail_subject(recipient: str) -> str:
    """Return the durable mailbox subject for ``recipient``."""
    return f"{MAIL_SUBJECT_PREFIX}.{validate_agent_id(recipient)}"


def notify_subject(recipient: str) -> str:
    """Return the ephemeral wake-signal subject for ``recipient``."""
    return f"{NOTIFY_SUBJECT_PREFIX}.{validate_agent_id(recipient)}"


def durable_name(agent_id: str) -> str:
    """Return the per-agent JetStream durable consumer name."""
    return f"mail-{validate_agent_id(agent_id)}"


def _alias(toml_key: str, env_name: str) -> AliasChoices:
    """Accept a setting under its lowercase TOML key or its uppercase env name."""
    return AliasChoices(toml_key, env_name)


class Config(BaseSettings):
    """Resolved connection, identity, server and hub settings.

    Frozen: to vary a field build a copy with :meth:`~pydantic.BaseModel.model_copy`.
    """

    # case_sensitive is essential: the lowercase TOML aliases (e.g. ``path``, ``host``)
    # must NOT match ubiquitous uppercase env vars (``PATH``, ``HOST``, ``USER``, …).
    # Env vars use the documented UPPERCASE alias; TOML keys use the lowercase one.
    model_config = SettingsConfigDict(frozen=True, extra="ignore", case_sensitive=True)

    # -- NATS connection --------------------------------------------------
    nats_url: str = Field(
        DEFAULT_NATS_URL, validation_alias=_alias("nats_url", "NATS_URL")
    )
    nats_token: str | None = Field(
        None, validation_alias=_alias("nats_token", "NATS_TOKEN")
    )
    nats_user: str | None = Field(
        None, validation_alias=_alias("nats_user", "NATS_USER")
    )
    nats_password: str | None = Field(
        None, validation_alias=_alias("nats_password", "NATS_PASSWORD")
    )
    nats_creds_file: str | None = Field(
        None, validation_alias=_alias("nats_creds_file", "NATS_CREDS_FILE")
    )
    nats_ca_file: str | None = Field(
        None, validation_alias=_alias("nats_ca_file", "NATS_CA_FILE")
    )

    # -- identity ---------------------------------------------------------
    agent_id: str | None = Field(None, validation_alias=_alias("agent_id", "AGENT_ID"))

    # -- MCP server -------------------------------------------------------
    transport: str = Field(
        "stdio", validation_alias=_alias("transport", "AGENT_MAIL_TRANSPORT")
    )
    host: str = Field("127.0.0.1", validation_alias=_alias("host", "AGENT_MAIL_HOST"))
    port: int = Field(8080, validation_alias=_alias("port", "AGENT_MAIL_PORT"))
    path: str = Field("/mcp", validation_alias=_alias("path", "AGENT_MAIL_PATH"))
    public_url: str | None = Field(
        None, validation_alias=_alias("public_url", "AGENT_MAIL_PUBLIC_URL")
    )

    # -- hub identity & administration (advertised via hub_info) ----------
    hub: str = Field("agent-mail", validation_alias=_alias("hub", "AGENT_MAIL_HUB"))
    hub_description: str | None = Field(
        None, validation_alias=_alias("hub_description", "AGENT_MAIL_HUB_DESCRIPTION")
    )
    admin_agent: str | None = Field(
        None, validation_alias=_alias("admin_agent", "AGENT_MAIL_ADMIN_AGENT")
    )
    issue_url: str | None = Field(
        None, validation_alias=_alias("issue_url", "AGENT_MAIL_ISSUE_URL")
    )
    contact: str | None = Field(
        None, validation_alias=_alias("contact", "AGENT_MAIL_CONTACT")
    )

    # -- ops --------------------------------------------------------------
    log_level: str = Field(
        "WARNING", validation_alias=_alias("log_level", "AGENT_MAIL_LOG_LEVEL")
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
    def from_env(cls, agent_override: str | None = None) -> Config:
        """Build config from all layers, letting ``agent_override`` win on identity."""
        config = cls()
        if agent_override:
            config = config.model_copy(update={"agent_id": agent_override})
        return config

    def require_identity(self) -> str:
        """Return the validated agent id, or raise if none was configured."""
        if not self.agent_id:
            raise ConfigError("no agent identity: set AGENT_ID or pass --from <agent>")
        return validate_agent_id(self.agent_id)

    def base_url(self) -> str:
        """Return the advertised base URL agents should connect to."""
        if self.public_url:
            return self.public_url.rstrip("/")
        host = "localhost" if self.host in ("0.0.0.0", "127.0.0.1") else self.host
        return f"http://{host}:{self.port}"

    def redacted(self) -> dict[str, object]:
        """Return the effective config with secrets masked, for banners/logs."""
        data = self.model_dump()
        for key in _SECRET_FIELDS:
            if data.get(key):
                data[key] = "***"
        return data


def hub_descriptor(config: Config) -> dict[str, object]:
    """Return the hub's public, non-secret self-description for discovery.

    Served by ``GET /`` and the ``hub_info`` MCP tool so an agent can learn, on
    sign-on, which hub it reached, how to connect, and how to get help.
    """
    base = config.base_url()
    return {
        "hub": config.hub,
        "description": config.hub_description,
        "connect_url_template": f"{base}{config.path.rstrip('/')}"
        if config.transport != "http"
        else f"{base}/<agent>{config.path}",
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
