"""The prompt catalog: role prompts served live-rendered from templates.

Templates live in ``prompts/*.md`` next to this module, each with a small frontmatter
(``title``, ``description``). They are rendered with *this hub's* live coordinates via
:class:`string.Template` (``$hub_url``, ``$host_agent``, …) so a human can just point an
agent at ``<hub>/prompts/<name>`` and it arrives pre-filled. Drop a new ``.md`` in and
it appears in the index — no code change.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

from agent_mailbox_old.config import Config, hub_version

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Retired prompt names kept resolving. A renamed URL that 404s silently orphans every
# reference to it — including the ones already written into agents' CLAUDE.md files.
_ALIASES = {"onboarding": "agent"}


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return ``(metadata, body)`` for a ``---``-delimited frontmatter block."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, text[end + 4 :].lstrip("\n")


def prompt_context(config: Config) -> dict[str, str]:
    """The substitution variables available to every prompt template."""
    base = config.base_url()
    return {
        "hub_name": config.hub_name,
        "hub_url": base,
        "mcp_endpoint": f"{base}/<project>/<agent>/mcp",
        "prompts_url": f"{base}/prompts",
        "admin_agent": config.admin_agent or "(unset)",
        "host_agent": config.host_agent or "(unset)",
        "version": hub_version(),
    }


def _template_path(name: str) -> Path | None:
    """Resolve a prompt name to its file, rejecting anything path-y (no traversal)."""
    name = _ALIASES.get(name, name)
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    path = _PROMPTS_DIR / f"{name}.md"
    return path if path.is_file() else None


def list_prompts() -> list[dict[str, str]]:
    """List available prompts as ``{name, title, description}``, sorted by name."""
    out: list[dict[str, str]] = []
    for path in sorted(_PROMPTS_DIR.glob("*.md")):
        meta, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
        out.append(
            {
                "name": path.stem,
                "title": meta.get("title", path.stem),
                "description": meta.get("description", ""),
            }
        )
    return out


def prompt_url(name: str, config: Config) -> str:
    """The canonical URL an agent re-reads to get the current version of a prompt."""
    return f"{config.base_url()}/prompts/{_ALIASES.get(name, name)}"


def render_short(name: str, config: Config) -> str | None:
    """A one-paste bootstrap: tell an agent its role and where its real prompt lives.

    Short enough to drop into a chat window or a config file. It deliberately carries
    no instructions of its own — the served page is the single source of truth, so a
    pasted copy can never go stale.
    """
    canonical = _ALIASES.get(name, name)
    if _template_path(canonical) is None:
        return None
    return (
        f"Your role on the {config.hub_name} agent-inbox hub is **{canonical}**.\n"
        f"Read and action {prompt_url(canonical, config)} — that page is your full, "
        f"current instructions (it is rendered live, so there is nothing to fill in). "
        f"Follow it, including the step that records your role in this project's "
        f"CLAUDE.md / AGENTS.md so it survives a restart."
    )


def _staying_current(name: str, config: Config) -> str:
    """The re-read footer, generated so the version stamp can never drift."""
    return (
        "\n## Staying current\n\n"
        f"This is the **{name}** prompt, agent-inbox **v{hub_version()}**. "
        "This prompt changes often — it is the source of truth for this role, and it "
        "is edited as the hub learns.\n\n"
        f"**Re-read {prompt_url(name, config)}** whenever you start a session, and "
        "especially whenever the hub reports a different version from the one above "
        "(`hub_info` -> `version`). Do not rely on a copy you pasted somewhere: a "
        "stale prompt propagates faster than a stale doc.\n"
    )


def render_prompt(name: str, config: Config) -> str | None:
    """Render one prompt with the hub's live coordinates, or ``None`` if unknown."""
    path = _template_path(name)
    if path is None:
        return None
    canonical = _ALIASES.get(name, name)
    _, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    context = prompt_context(config)
    context["prompt_name"] = canonical
    context["prompt_url"] = prompt_url(canonical, config)
    rendered = Template(body).safe_substitute(context)
    return rendered.rstrip() + "\n" + _staying_current(canonical, config)


def render_index(config: Config) -> str:
    """Render the catalog index (markdown) of available prompts."""
    base = config.base_url()
    lines = [
        f"# {config.hub_name} — prompt catalog",
        "",
        'Point an agent at one of these URLs ("read and action this page"):',
        "",
    ]
    for entry in list_prompts():
        url = f"{base}/prompts/{entry['name']}"
        lines.append(f"- [`{entry['name']}`]({url}) — {entry['description']}")
    return "\n".join(lines) + "\n"
