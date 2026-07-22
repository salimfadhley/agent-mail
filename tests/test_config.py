"""Unit tests for configuration and subject helpers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_mail.config import (
    Config,
    ConfigError,
    durable_name,
    hub_descriptor,
    mail_subject,
    notify_subject,
    validate_agent_id,
)
from agent_mail.config_env import set_runtime_config_path


@pytest.fixture(autouse=True)
def _reset_runtime_config() -> Iterator[None]:
    set_runtime_config_path(None)
    yield
    set_runtime_config_path(None)


def test_from_env_uses_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NATS_URL", raising=False)
    monkeypatch.delenv("AGENT_ID", raising=False)
    config = Config.from_env()
    assert config.nats_url == "nats://127.0.0.1:4222"
    assert config.agent_id is None


def test_from_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_ID", "env-agent")
    config = Config.from_env(agent_override="cli-agent")
    assert config.agent_id == "cli-agent"


def test_require_identity_raises_without_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_ID", raising=False)
    with pytest.raises(ConfigError):
        Config.from_env().require_identity()


@pytest.mark.parametrize("bad", ["a.b", "has space", "wild*", "", "x/y"])
def test_invalid_agent_ids_rejected(bad: str) -> None:
    with pytest.raises(ConfigError):
        validate_agent_id(bad)


def test_subject_helpers() -> None:
    assert mail_subject("casework") == "agent.mail.casework"
    assert notify_subject("casework") == "agent.notify.casework"
    assert durable_name("casework") == "mail-casework"


def test_config_layering_env_beats_file_beats_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("AGENT_MAIL_HUB", "NATS_URL", "AGENT_ID"):
        monkeypatch.delenv(key, raising=False)

    # 1. baked default
    assert Config().hub == "agent-mail"

    # 2. runtime --config file overrides the baked default
    cfg = tmp_path / "agent-mail.toml"
    cfg.write_text('hub = "from-file"\nnats_url = "nats://file:4222"\n')
    set_runtime_config_path(str(cfg))
    loaded = Config()
    assert loaded.hub == "from-file"
    assert loaded.nats_url == "nats://file:4222"

    # 3. environment wins over the file
    monkeypatch.setenv("AGENT_MAIL_HUB", "from-env")
    assert Config().hub == "from-env"


def test_lowercase_aliases_do_not_capture_uppercase_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PATH/HOST/USER are always in the environment; they must not leak into the
    # matching lowercase-aliased fields (regression for case-insensitive matching).
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
    assert "<agent>" in str(descriptor["connect_url_template"])
    assert "ping" in descriptor["tools"]  # type: ignore[operator]
