"""Unit tests for configuration, addressing helpers, and the config layering."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_inbox.config import (
    DEFAULT_MAX_MESSAGE_BYTES,
    DEFAULT_TTL_DAYS,
    Config,
    ConfigError,
    format_address,
    hub_descriptor,
    parse_address,
    parse_target,
    validate_agent_id,
    validate_project,
    validate_role,
)
from agent_inbox.config_env import set_runtime_config_path


@pytest.fixture(autouse=True)
def _reset_runtime_config() -> Iterator[None]:
    set_runtime_config_path(None)
    yield
    set_runtime_config_path(None)


def test_from_env_uses_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("AGENT_INBOX_DB", "AGENT_ID", "AGENT_INBOX_PROJECT"):
        monkeypatch.delenv(key, raising=False)
    config = Config.from_env()
    assert config.db.endswith("agent-inbox.db")
    assert config.ttl_days == DEFAULT_TTL_DAYS
    assert config.max_message_bytes == DEFAULT_MAX_MESSAGE_BYTES
    assert config.agent_id is None
    assert config.project is None


def test_from_env_overrides_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_ID", "env-agent")
    monkeypatch.setenv("AGENT_INBOX_PROJECT", "env-project")
    config = Config.from_env(agent_override="cli-agent", project_override="cli-project")
    assert config.agent_id == "cli-agent"
    assert config.project == "cli-project"


def test_require_address_needs_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_ID", raising=False)
    monkeypatch.setenv("AGENT_INBOX_PROJECT", "p")
    with pytest.raises(ConfigError):
        Config.from_env().require_address()
    monkeypatch.setenv("AGENT_ID", "a")
    assert Config.from_env().require_address() == ("p", "a")


@pytest.mark.parametrize("bad", ["a.b", "has space", "wild*", "", "x/y", "__all__"])
def test_invalid_ids_rejected(bad: str) -> None:
    with pytest.raises(ConfigError):
        validate_agent_id(bad)
    with pytest.raises(ConfigError):
        validate_project(bad)


def test_format_address() -> None:
    assert format_address("proj", "alice") == "proj/alice"
    with pytest.raises(ConfigError):
        format_address("proj", "bad id")


def test_parse_target_modes() -> None:
    assert parse_target("proj/alice") == ("direct", "proj", "alice")
    # broadcast to the whole project — bare, trailing slash, /all, /* all mean this
    assert parse_target("proj") == ("broadcast", "proj", None)
    assert parse_target("proj/") == ("broadcast", "proj", None)
    assert parse_target("proj/all") == ("broadcast", "proj", None)
    assert parse_target("proj/*") == ("broadcast", "proj", None)
    # one agent on the project (a shared work queue)
    assert parse_target("proj/any") == ("any", "proj", None)
    # public broadcast — every agent everywhere
    assert parse_target("all/all") == ("public", None, None)
    assert parse_target("*/*") == ("public", None, None)
    assert parse_target("all") == ("public", None, None)
    # one agent anywhere
    assert parse_target("any/any") == ("global_any", None, None)
    assert parse_target("any") == ("global_any", None, None)


def test_parse_target_validates_tokens() -> None:
    with pytest.raises(ConfigError):
        parse_target("bad project/alice")
    with pytest.raises(ConfigError):
        parse_target("proj/bad agent")


def test_reserved_words_rejected_as_names() -> None:
    for bad in ("all", "any", "ALL", "Any"):
        with pytest.raises(ConfigError):
            validate_agent_id(bad)
        with pytest.raises(ConfigError):
            validate_project(bad)


def test_specific_agent_under_global_scope_is_ambiguous() -> None:
    with pytest.raises(ConfigError):
        parse_target("all/alice")
    with pytest.raises(ConfigError):
        parse_target("any/bob")


def test_config_layering_env_beats_file_beats_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("AGENT_INBOX_HUB_NAME", "AGENT_INBOX_TTL_DAYS", "AGENT_ID"):
        monkeypatch.delenv(key, raising=False)

    assert Config().hub_name == "agent-inbox"

    cfg = tmp_path / "agent-inbox.toml"
    cfg.write_text('hub_name = "from-file"\nttl_days = 3\n')
    set_runtime_config_path(str(cfg))
    loaded = Config()
    assert loaded.hub_name == "from-file"
    assert loaded.ttl_days == 3

    monkeypatch.setenv("AGENT_INBOX_HUB_NAME", "from-env")
    assert Config().hub_name == "from-env"


def test_legacy_agent_mail_env_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("AGENT_INBOX_HUB_NAME", "AGENT_MAIL_HUB_NAME"):
        monkeypatch.delenv(key, raising=False)
    # deprecated AGENT_MAIL_* still works
    monkeypatch.setenv("AGENT_MAIL_HUB_NAME", "legacy")
    assert Config().hub_name == "legacy"
    # canonical AGENT_INBOX_* wins when both are set
    monkeypatch.setenv("AGENT_INBOX_HUB_NAME", "canonical")
    assert Config().hub_name == "canonical"


def test_lowercase_aliases_do_not_capture_uppercase_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOST", "somehost")
    monkeypatch.delenv("AGENT_INBOX_PATH", raising=False)
    monkeypatch.delenv("AGENT_INBOX_HOST", raising=False)
    config = Config()
    assert config.path == "/mcp"
    assert config.host == "127.0.0.1"


def test_missing_config_file_raises() -> None:
    set_runtime_config_path("/definitely/not/here.toml")
    with pytest.raises(ConfigError):
        Config()


def test_redacted_returns_effective_config() -> None:
    config = Config().model_copy(update={"db": "/tmp/mail.db"})
    redacted = config.redacted()
    assert redacted["db"] == "/tmp/mail.db"
    assert redacted["hub_name"] == "agent-inbox"


def test_hub_descriptor_is_public() -> None:
    config = Config().model_copy(
        update={"hub_name": "h", "transport": "http", "admin_agent": "admin"}
    )
    descriptor = hub_descriptor(config, max_message_bytes=1048576)
    assert descriptor["hub"] == "h"
    assert descriptor["storage"] == "sqlite"
    assert descriptor["admin_agent"] == "admin"
    assert "<project>/<agent>" in str(descriptor["connect_url_template"])
    assert "ping" in descriptor["tools"]  # type: ignore[operator]
    assert descriptor["limits"] == {"max_message_bytes": 1048576}
    assert descriptor["version"]


def test_hub_descriptor_defaults_limit_to_config() -> None:
    config = Config().model_copy(update={"max_message_bytes": 4096})
    descriptor = hub_descriptor(config)
    assert descriptor["limits"] == {"max_message_bytes": 4096}


# -- three-part addressing: <project>[/<agent>[/<role>]] ----------------------
#
# Each position narrows independently. `all`, `*` and an EMPTY position all mean
# "every value"; `any` also matches every value but asks for exactly one recipient.


def _t(address: str) -> tuple[str | None, str | None, str | None, bool]:
    target = parse_address(address)
    return target.project, target.agent, target.role, target.claim


@pytest.mark.parametrize(
    "forms",
    [
        # a bare project is everyone on it — and every spelling agrees
        ["a", "a/all", "a/all/all", "a/*/all", "a/*/*", "a/*", "a/", "a//"],
        # naming an agent leaves the role wide open
        ["a/b", "a/b/all", "a/b/*", "a/b/"],
        # an empty position is just another spelling of `all`, so a role can be
        # addressed on its own: //host reaches whoever holds it, anywhere
        ["//host", "*/*/host", "all/all/host"],
        ["//admin", "*/*/admin"],
        # everyone, everywhere
        ["all", "*", "*/*", "all/all/all", "//"],
    ],
)
def test_equivalent_address_spellings(forms: list[str]) -> None:
    assert len({_t(f) for f in forms}) == 1, forms


def test_positions_narrow_independently() -> None:
    assert _t("a/b/c") == ("a", "b", "c", False)
    assert _t("a/all/c") == ("a", None, "c", False)  # whoever holds role c on a
    assert _t("a/b") == ("a", "b", None, False)


def test_any_asks_for_exactly_one_recipient() -> None:
    assert parse_address("a/any").claim is True
    assert parse_address("a/any/c").claim is True  # one holder of role c
    assert parse_address("any").claim is True  # one agent anywhere
    assert parse_address("a").claim is False  # ...but a bare project is everyone
    assert parse_address("all").claim is False


@pytest.mark.parametrize("token", ["all", "ALL", "any", "Any", "*"])
def test_reserved_words_cannot_be_real_names(token: str) -> None:
    for validator in (validate_project, validate_agent_id, validate_role):
        with pytest.raises(ConfigError):
            validator(token)


def test_too_many_parts_is_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_address("a/b/c/d")
