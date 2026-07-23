"""Unit tests for the prompt catalog (discovery + live rendering)."""

from __future__ import annotations

from agent_inbox import prompts
from agent_inbox.config import Config


def _config() -> Config:
    return Config().model_copy(
        update={
            "hub_name": "homelab",
            "transport": "http",
            "public_url": "http://halob:8080",
            "host_agent": "agent-inbox/host",
            "admin_agent": "agent-inbox/admin",
        }
    )


def test_catalog_lists_the_shipped_prompts() -> None:
    names = {p["name"] for p in prompts.list_prompts()}
    assert {"onboarding", "host"} <= names
    for entry in prompts.list_prompts():
        assert entry["description"]  # frontmatter parsed


def test_render_fills_live_coordinates() -> None:
    body = prompts.render_prompt("onboarding", _config())
    assert body is not None
    assert "homelab" in body  # $hub_name
    assert "http://halob:8080/<project>/<agent>/mcp" in body  # $hub_url used
    assert "$hub_url" not in body and "$host_agent" not in body  # no stray templates


def test_host_prompt_mentions_its_identity_and_onboarding_url() -> None:
    body = prompts.render_prompt("host", _config())
    assert body is not None
    assert "agent-inbox/host" in body  # $host_agent
    assert "http://halob:8080/prompts/onboarding" in body  # $prompts_url


def test_every_prompt_tells_the_agent_to_persist_a_pointer() -> None:
    """A prompt read once is forgotten; each must plant a pointer in the agent's config.

    Every served role prompt has to tell the agent to record, in its own startup config
    (CLAUDE.md / AGENTS.md), both its address and an instruction to re-read the prompt
    on start — otherwise the bootstrap is one-shot and drifts.
    """
    cfg = _config()
    for entry in prompts.list_prompts():
        body = prompts.render_prompt(entry["name"], cfg)
        assert body is not None, entry["name"]
        lowered = body.lower()
        assert "claude.md" in lowered and "agents.md" in lowered, (
            f"{entry['name']}: must name the config file to update"
        )
        assert "on start" in lowered, (
            f"{entry['name']}: must tell the agent to re-read the prompt on start"
        )
        # the pointer it plants must be this prompt's own live URL
        assert f"http://halob:8080/prompts/{entry['name']}" in body, (
            f"{entry['name']}: must point back at its own URL"
        )


def test_unknown_prompt_is_none_and_traversal_is_blocked() -> None:
    cfg = _config()
    assert prompts.render_prompt("does-not-exist", cfg) is None
    assert prompts.render_prompt("../config", cfg) is None
    assert prompts.render_prompt("", cfg) is None


def test_index_links_each_prompt() -> None:
    index = prompts.render_index(_config())
    assert "# homelab — prompt catalog" in index
    assert "http://halob:8080/prompts/onboarding" in index
    assert "http://halob:8080/prompts/host" in index
