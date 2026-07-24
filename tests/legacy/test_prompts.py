"""Unit tests for the prompt catalog (discovery + live rendering)."""

from __future__ import annotations

from agent_mailbox_old import prompts
from agent_mailbox_old.config import Config


def _config() -> Config:
    return Config().model_copy(
        update={
            "hub_name": "homelab",
            "transport": "http",
            "public_url": "http://mail.example:8080",
            "host_agent": "agent-inbox/host",
            "admin_agent": "agent-inbox/admin",
        }
    )


def test_catalog_lists_the_shipped_prompts() -> None:
    names = {p["name"] for p in prompts.list_prompts()}
    assert {"agent", "host"} <= names
    for entry in prompts.list_prompts():
        assert entry["description"]  # frontmatter parsed


def test_render_fills_live_coordinates() -> None:
    body = prompts.render_prompt("agent", _config())
    assert body is not None
    assert "homelab" in body  # $hub_name
    assert "http://mail.example:8080/<project>/<agent>/mcp" in body  # $hub_url used
    assert "$hub_url" not in body and "$host_agent" not in body  # no stray templates


def test_host_prompt_mentions_its_identity_and_agent_url() -> None:
    body = prompts.render_prompt("host", _config())
    assert body is not None
    assert "agent-inbox/host" in body  # $host_agent
    assert (
        "http://mail.example:8080/prompts/agent" in body
    )  # hands newcomers the agent prompt
    assert "agent-inbox/admin" in body  # routes hub problems to the admin


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
        assert f"http://mail.example:8080/prompts/{entry['name']}" in body, (
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
    for name in ("agent", "host", "admin"):
        assert f"http://mail.example:8080/prompts/{name}" in index


# -- the three roles ---------------------------------------------------------


def test_the_three_roles_are_served() -> None:
    assert {p["name"] for p in prompts.list_prompts()} == {"agent", "host", "admin"}


def test_retired_onboarding_url_still_resolves() -> None:
    """The prompt was renamed; agents already wrote the old URL into their configs.

    A renamed URL that 404s silently orphans every reference to it — which is the
    exact failure the hub's own agents complained about.
    """
    config = _config()
    assert prompts.render_prompt("onboarding", config) == prompts.render_prompt(
        "agent", config
    )


def test_every_role_prompt_covers_the_four_setup_steps() -> None:
    """Choose a name, prove the connection, ask for a restart, persist it locally."""
    config = _config()
    for name in ("agent", "host", "admin"):
        body = prompts.render_prompt(name, config)
        assert body is not None
        lowered = body.lower()
        # (a) choose a name, and confirm it with the human
        assert "your human" in lowered, name
        # (b) a real connection test, not just configuration
        assert "ping" in lowered, name
        # (c) tools only load at session start — ask for a reboot
        assert "restart" in lowered, name
        # (d) persist name/role locally so it survives the session
        assert "claude.md" in lowered and "agents.md" in lowered, name


def test_short_prompt_is_a_one_paste_bootstrap() -> None:
    """The short form assigns a role and defers to the live page for the rest."""
    config = _config()
    for name in ("agent", "host", "admin"):
        short = prompts.render_short(name, config)
        assert short is not None
        assert name in short
        assert f"http://mail.example:8080/prompts/{name}" in short
        # short enough to paste into a chat window
        assert len(short) < 500, name
    assert prompts.render_short("nope", config) is None
    # the retired name still bootstraps, pointing at the canonical page
    assert "prompts/agent" in (prompts.render_short("onboarding", config) or "")


def test_full_prompt_carries_a_version_stamp_and_reread_pointer() -> None:
    """It says which version it is and where to get a newer one."""
    config = _config()
    version = prompts.hub_version()
    for name in ("agent", "host", "admin"):
        body = prompts.render_prompt(name, config)
        assert body is not None
        assert "Staying current" in body, name
        assert f"v{version}" in body, name
        assert f"http://mail.example:8080/prompts/{name}" in body, name
        assert "changes often" in body, name
