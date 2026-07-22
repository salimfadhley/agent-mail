"""Unit tests for configuration, addressing helpers, and the config layering."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_mail.config import (
    Config,
    ConfigError,
    any_subject,
    broadcast_subject,
    direct_subject,
    format_address,
    hub_descriptor,
    notify_target_subject,
    parse_target,
    validate_agent_id,
    validate_project,
)
from agent_mail.config_env import set_runtime_config_path


@pytest.fixture(autouse=True)
def _reset_runtime_config() -> Iterator[None]:
    set_runtime_config_path(None)
    yield
    set_runtime_config_path(None)


def test_from_env_uses_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("NATS_URL", "AGENT_ID", "AGENT_MAIL_PROJECT"):
        monkeypatch.delenv(key, raising=False)
    config = Config.from_env()
    assert config.nats_url == "nats://127.0.0.1:4222"
    assert config.agent_id is None
    assert config.project is None


def test_from_env_overrides_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_ID", "env-agent")
    monkeypatch.setenv("AGENT_MAIL_PROJECT", "env-project")
    config = Config.from_env(agent_override="cli-agent", project_override="cli-project")
    assert config.agent_id == "cli-agent"
    assert config.project == "cli-project"


def test_require_address_needs_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_ID", raising=False)
    monkeypatch.setenv("AGENT_MAIL_PROJECT", "p")
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


def test_address_and_subject_helpers() -> None:
    assert format_address("proj", "alice") == "proj/alice"
    assert direct_subject("proj", "alice") == "agent.mail.proj.alice"
    assert broadcast_subject("proj") == "agent.mail.proj.__all__"
    assert any_subject("proj") == "agent.mail.proj.__any__"


def test_parse_target_modes() -> None:
    assert parse_target("proj/alice") == ("direct", "agent.mail.proj.alice")
    assert parse_target("proj") == ("any", "agent.mail.proj.__any__")
    assert parse_target("proj/*") == ("broadcast", "agent.mail.proj.__all__")


def test_notify_target_subject_modes() -> None:
    assert notify_target_subject("proj/alice") == "agent.notify.proj.alice"
    assert notify_target_subject("proj") == "agent.notify.proj.__all__"
    assert notify_target_subject("proj/*") == "agent.notify.proj.__all__"


def test_config_layering_env_beats_file_beats_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("AGENT_MAIL_HUB", "NATS_URL", "AGENT_ID"):
        monkeypatch.delenv(key, raising=False)

    assert Config().hub == "agent-mail"

    cfg = tmp_path / "agent-mail.toml"
    cfg.write_text('hub = "from-file"\nnats_url = "nats://file:4222"\n')
    set_runtime_config_path(str(cfg))
    loaded = Config()
    assert loaded.hub == "from-file"
    assert loaded.nats_url == "nats://file:4222"

    monkeypatch.setenv("AGENT_MAIL_HUB", "from-env")
    assert Config().hub == "from-env"


def test_lowercase_aliases_do_not_capture_uppercase_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOST", "somehost")
    monkeypatch.delenv("AGENT_MAIL_PATH", raising=False)
    monkeypatch.delenv("AGENT_MAIL_HOST", raising=False)
    config = Config()
    assert config.path == "/mcp"
    assert config.host == "127.0.0.1"


def test_missing_config_file_raises() -> None:
    set_runtime_config_path("/definitely/not/here.toml")
    with pytest.raises(ConfigError):
        Config()


def test_redacted_masks_secrets() -> None:
    config = Config().model_copy(
        update={"nats_password": "hunter2", "nats_token": "tok"}
    )
    redacted = config.redacted()
    assert redacted["nats_password"] == "***"
    assert redacted["nats_token"] == "***"
    assert redacted["nats_url"] == config.nats_url


def test_hub_descriptor_is_public() -> None:
    config = Config().model_copy(
        update={"hub": "h", "transport": "http", "admin_agent": "admin"}
    )
    descriptor = hub_descriptor(config)
    assert descriptor["hub"] == "h"
    assert descriptor["admin_agent"] == "admin"
    assert "<project>/<agent>" in str(descriptor["connect_url_template"])
    assert "ping" in descriptor["tools"]  # type: ignore[operator]
