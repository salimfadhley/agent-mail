"""Runtime configuration and addressing for agent-inbox.

Every setting has one canonical name, usable identically as a lowercase TOML key or
as an environment variable (e.g. TOML ``db`` == env ``AGENT_INBOX_DB``). The legacy
``AGENT_MAIL_*`` names are still accepted as deprecated aliases. Values are resolved
from four layers, later ones winning:

    field defaults  <  baked defaults.toml  <  runtime --config file  <  environment

Addresses are two-part — ``<project>/<agent>``. A bare ``<project>`` targets any one
agent on that project; ``<project>/*`` broadcasts to every agent.

Storage is a single local SQLite file (``db``) — no external services.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
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

from agent_inbox.config_env import RUNTIME_CONFIG_ENV, runtime_config_path
from agent_inbox.exceptions import ConfigError

logger = logging.getLogger(__name__)

DEFAULT_MAX_MESSAGE_BYTES = 1_048_576  # 1 MiB
DEFAULT_TTL_DAYS = 14
DEFAULT_ONLINE_SECONDS = 300  # an agent is "online" if seen within this window
DEFAULT_STALE_DAYS = 7  # a directory entry unseen this long is hidden by default

_BAKED_DEFAULTS = Path(__file__).parent / "defaults.toml"
_VALID_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Address wildcards, usable in either position (project or agent). ``*`` is a synonym
# for ``all``. They are reserved: a real project or agent may not be named these.
RESERVED_TOKENS = frozenset({"all", "any"})

# No secret settings today (SQLite needs no credentials); kept so redacted() stays
# generic if secret-bearing settings are ever added.
_SECRET_FIELDS: frozenset[str] = frozenset()


def default_db_path() -> str:
    """The default SQLite file: ``$XDG_DATA_HOME/agent-inbox/agent-inbox.db``."""
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return str(root / "agent-inbox" / "agent-inbox.db")


def _validate_token(value: str, kind: str) -> str:
    if not _VALID_TOKEN.match(value):
        raise ConfigError(
            f"invalid {kind} {value!r}: use letters, digits, '-' or '_' "
            "(must start alphanumeric; no dots, spaces or wildcards), max 64 chars"
        )
    if value.lower() in RESERVED_TOKENS:
        raise ConfigError(
            f"invalid {kind} {value!r}: 'all' and 'any' are reserved address words"
        )
    return value


def validate_agent_id(agent_id: str) -> str:
    """Return ``agent_id`` if it is a safe address token, else raise."""
    return _validate_token(agent_id, "agent id")


def validate_project(project: str) -> str:
    """Return ``project`` if it is a safe address token, else raise."""
    return _validate_token(project, "project")


def validate_role(role: str) -> str:
    """Return ``role`` if it is a safe address token, else raise."""
    return _validate_token(role, "role")


def format_address(project: str, agent: str, role: str | None = None) -> str:
    """Return the canonical ``<project>/<agent>[/<role>]`` address string."""
    base = f"{validate_project(project)}/{validate_agent_id(agent)}"
    return f"{base}/{validate_role(role)}" if role else base


@dataclass(frozen=True)
class Target:
    """A parsed destination address.

    Addresses are ``<project>/<agent>/<role>``, and **each position narrows
    independently**. A position holding a real token matches only that value; a
    position holding ``all`` (or ``*``, or simply omitted) matches every value; a
    position holding ``any`` also matches every value but asks for *one* recipient.

    So ``None`` on a field means "any value matches" — the field is a wildcard — and
    :attr:`claim` says how many of the matching agents actually receive it:

    * ``claim=True``  -> exactly one matching agent gets it (first read wins).
    * ``claim=False`` -> every matching agent gets its own copy.
    """

    project: str | None  # None = wildcard
    agent: str | None  # None = wildcard
    role: str | None  # None = wildcard
    claim: bool  # `any` appeared somewhere: deliver to exactly one

    @property
    def kind(self) -> str:
        """Storage kind: ``claim`` (exactly once) or ``fanout`` (per-reader copy)."""
        return "claim" if self.claim else "fanout"

    @property
    def is_specific(self) -> bool:
        """Whether this names exactly one agent (no wildcards anywhere)."""
        return self.project is not None and self.agent is not None


def _parse_part(part: str) -> tuple[str | None, bool]:
    """Resolve one address position to ``(literal_or_None, is_any)``."""
    token = part.strip().replace("*", "all") or "all"
    lowered = token.lower()
    if lowered == "all":
        return None, False
    if lowered == "any":
        return None, True
    return token, False


def parse_address(to: str) -> Target:
    """Parse ``<project>[/<agent>[/<role>]]`` into a :class:`Target`.

    Omitted trailing positions default to ``all``, so ``goldberg`` means every agent on
    goldberg and ``goldberg/claude`` means every claude on it, whatever their role.
    A bare ``any`` means one agent anywhere; a bare ``all`` (or ``*``) means everyone.

    Raises :class:`ConfigError` on a malformed address or one with too many parts.
    """
    raw = to.strip()
    parts = [p for p in raw.split("/")]
    if len(parts) > 3:
        raise ConfigError(
            f"too many parts in address {to!r}: expected <project>[/<agent>[/<role>]]"
        )
    # A bare `any` means any/any/any; everything else defaults omitted parts to `all`.
    if len(parts) == 1 and parts[0].strip().lower() == "any":
        parts = ["any", "any", "any"]
    parts += ["all"] * (3 - len(parts))

    project, p_any = _parse_part(parts[0])
    agent, a_any = _parse_part(parts[1])
    role, r_any = _parse_part(parts[2])
    if project is not None:
        project = validate_project(project)
    if agent is not None:
        agent = validate_agent_id(agent)
    if role is not None:
        role = validate_role(role)
    # A role can only be addressed under a concrete agent-or-wildcard; there is no
    # ordering constraint beyond that, since each position narrows independently.
    return Target(
        project=project, agent=agent, role=role, claim=p_any or a_any or r_any
    )


def parse_target(to: str) -> tuple[str, str | None, str | None]:
    """Resolve an address string to ``(kind, project, agent)``.

    The **project part** is the scope and the **agent part** is the fan-out; an absent
    agent part defaults to ``all``, and ``*`` is a synonym for ``all``. So:

    * ``project/agent`` -> ``("direct", project, agent)`` — one specific agent;
    * ``project`` (bare), ``project/``, ``project/all``, ``project/*``
      -> ``("broadcast", project, None)`` — every agent on the project (common case);
    * ``project/any`` -> ``("any", project, None)`` — one agent on the project, chosen
      when the message is read (a shared work queue; rarer);
    * ``all/all`` (or ``*/*``, bare ``all``) -> ``("public", None, None)`` — every
      agent everywhere (public broadcast);
    * ``any/any`` (or bare ``any``) -> ``("global_any", None, None)`` — one agent
      anywhere (very rare).

    Validates real tokens, raising :class:`ConfigError` on a malformed or ambiguous
    address (e.g. a specific agent under a global scope, like ``all/alice``).
    """
    proj_part, _sep, agent_part = to.strip().partition("/")
    if not _sep:
        # bare token: a bare project means "everyone on it"; bare ``any`` -> any/any;
        # bare ``all``/``*`` -> all/all.
        agent_part = "any" if proj_part.strip().lower() == "any" else "all"
    proj_part = (proj_part.strip() or "all").replace("*", "all")
    agent_part = (agent_part.strip() or "all").replace("*", "all")
    proj_l, agent_l = proj_part.lower(), agent_part.lower()

    if proj_l in ("all", "any"):  # global scope (any project)
        if agent_l == "all":
            return "public", None, None
        if agent_l == "any":
            return "global_any", None, None
        raise ConfigError(
            f"cannot address a specific agent across all projects: {to!r} "
            "(use 'all/all' for everyone, or 'any/any' for one)"
        )
    project = validate_project(proj_part)
    if agent_l == "all":
        return "broadcast", project, None
    if agent_l == "any":
        return "any", project, None
    return "direct", project, validate_agent_id(agent_part)


def _alias(toml_key: str, env_name: str) -> AliasChoices:
    """Accept a setting under its TOML key or env name.

    For brand-prefixed vars, ``AGENT_INBOX_<X>`` is canonical and the legacy
    ``AGENT_MAIL_<X>`` is kept as a deprecated back-compat alias (canonical wins).
    """
    if env_name.startswith("AGENT_MAIL_"):
        canonical = "AGENT_INBOX_" + env_name.removeprefix("AGENT_MAIL_")
        return AliasChoices(toml_key, canonical, env_name)
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
    online_seconds: int = Field(
        default=DEFAULT_ONLINE_SECONDS,
        validation_alias=_alias("online_seconds", "AGENT_MAIL_ONLINE_SECONDS"),
    )
    # A directory entry unseen for this long is "stale" and hidden from list_agents
    # by default (dead identities have the emptiest cards and clutter the room).
    # 0 disables staleness entirely.
    stale_days: int = Field(
        default=DEFAULT_STALE_DAYS,
        validation_alias=_alias("stale_days", "AGENT_MAIL_STALE_DAYS"),
    )

    # -- identity (two-part: project + agent) -----------------------------
    project: str | None = Field(
        default=None, validation_alias=_alias("project", "AGENT_MAIL_PROJECT")
    )
    agent_id: str | None = Field(
        default=None, validation_alias=_alias("agent_id", "AGENT_ID")
    )
    # Optional third address position. Most agents hold no distinct role; the hub's
    # own infrastructure nodes (host, admin) do.
    role: str | None = Field(
        default=None, validation_alias=_alias("role", "AGENT_ROLE")
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

    # -- human web console (/ui) ------------------------------------------
    # Serve the operator console at /ui on the http server. Read-only over any
    # mailbox; interactive only for the operator's own inbox.
    ui: bool = Field(default=True, validation_alias=_alias("ui", "AGENT_MAIL_UI"))
    # The identity human-sent mail (compose/reply from the console) is sent as, and
    # the one inbox the console treats as interactive.
    operator: str = Field(
        default="agent-inbox/human",
        validation_alias=_alias("operator", "AGENT_MAIL_OPERATOR"),
    )

    # -- hub identity & administration (advertised via hub_info) ----------
    # The name of this mailbox collection (a "hub"). Set a distinct name per collection
    # if you run more than one on the same storage.
    hub_name: str = Field(
        default="agent-inbox",
        validation_alias=_alias("hub_name", "AGENT_MAIL_HUB_NAME"),
    )
    hub_description: str | None = Field(
        default=None,
        validation_alias=_alias("hub_description", "AGENT_MAIL_HUB_DESCRIPTION"),
    )
    admin_agent: str | None = Field(
        default=None, validation_alias=_alias("admin_agent", "AGENT_MAIL_ADMIN_AGENT")
    )
    # The coordinator agents introduce themselves to on sign-on (keeps the who's-who
    # roster). Distinct from admin_agent (hub operator contact); may be the same id.
    host_agent: str | None = Field(
        default=None, validation_alias=_alias("host_agent", "AGENT_MAIL_HOST_AGENT")
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


def hub_version() -> str:
    """The running mailbox version (from the installed ``agent-inbox`` distribution)."""
    try:
        return _pkg_version("agent-inbox")
    except PackageNotFoundError:  # pragma: no cover - source checkout w/o metadata
        return "0.0.0"


# Back-compat alias for internal callers.
_hub_version = hub_version


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
        "hub": config.hub_name,
        "description": config.hub_description,
        "version": _hub_version(),
        "storage": "sqlite",
        "addressing": {
            "direct": "project/agent — one specific agent",
            "broadcast": "project (or project/all, project/*) — all agents on it",
            "any": "project/any — one agent on the project (a shared work queue)",
            "public": "all/all (or */*) — every agent everywhere",
            "global_any": "any/any — one agent anywhere",
        },
        "limits": {
            "max_message_bytes": (
                max_message_bytes
                if max_message_bytes is not None
                else config.max_message_bytes
            ),
        },
        "connect_url_template": connect,
        "prompts_url": f"{base}/prompts" if config.transport == "http" else None,
        "ui_url": (f"{base}/ui" if config.transport == "http" and config.ui else None),
        "transport": config.transport,
        "admin_agent": config.admin_agent,
        "host_agent": config.host_agent,
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
            "register",
            "update_status",
            "list_agents",
            "whois",
            "list_threads",
            "read_thread",
        ],
    }
