"""Finding, inferring and writing ``agent-inbox.toml``.

The CLI is a client of a remote hub, so unlike the server it needs to be told two
things: **where the hub is** and **who it is**. This module is how it works both out
with as little ceremony as possible.

The shape of the bootstrap is deliberate. Agents drive non-interactive shells, so an
interactive wizard is the one design guaranteed to hang them. Instead everything is
**inferred and shown with its source**, and the agent confirms once by running a single
command. Seeing *where* a value came from is what lets an agent correct only the part
that is wrong instead of re-specifying everything.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "agent-inbox.toml"

# Engine markers, most specific first. Absence is not an error — an agent that cannot be
# identified simply has to name itself, which is better than guessing wrong and letting
# two agents collide on one inbox.
_ENGINE_MARKERS: tuple[tuple[str, str], ...] = (
    ("CLAUDECODE", "claude"),
    ("CLAUDE_CODE_ENTRYPOINT", "claude"),
    ("CODEX_SANDBOX", "codex"),
    ("GEMINI_CLI", "gemini"),
)

# Directory names that say nothing about the project, so the parent is more informative.
_GENERIC_NAMES = frozenset({"main", "master", "src", "app", "repo", "code", "work"})


@dataclass(frozen=True)
class Inferred:
    """One inferred value, with the evidence for it.

    ``source`` exists so the CLI can explain itself. ``value`` is ``None`` when nothing
    could be inferred, and ``source`` then says what was looked for.
    """

    value: str | None
    source: str

    @property
    def known(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class Identity:
    """A whole inferred identity: ``<project>/<agent>/<role>``."""

    project: Inferred
    agent: Inferred
    role: Inferred

    @property
    def complete(self) -> bool:
        return all(p.known for p in (self.project, self.agent, self.role))

    @property
    def address(self) -> str:
        parts = [p.value or "?" for p in (self.project, self.agent, self.role)]
        return "/".join(parts)


def normalize(token: str) -> str:
    """Reduce a name to an address token: lowercase, spaces and hyphens to ``_``."""
    out = token.strip().lower()
    for ch in (" ", "-", "."):
        out = out.replace(ch, "_")
    return "".join(c for c in out if c.isalnum() or c == "_").strip("_")


def git_root(start: Path | None = None) -> Path | None:
    """The git repository containing ``start``, or ``None`` if there isn't one."""
    cwd = Path(start or Path.cwd())
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    top = done.stdout.strip()
    return Path(top) if top else None


def find_config(start: Path | None = None) -> Path | None:
    """Locate ``agent-inbox.toml`` at or above ``start``, stopping at the git root.

    Stopping at the repository boundary is the point: walking further would let one
    project silently adopt a sibling's identity, which is exactly the confusion that
    made two agents share an inbox in the past.
    """
    here = Path(start or Path.cwd()).resolve()
    root = git_root(here)
    ceiling = root.resolve() if root else here
    for directory in (here, *here.parents):
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
        if directory == ceiling:
            break
    return None


def infer_project(start: Path | None = None) -> Inferred:
    """The project token for this working copy.

    **One repository is one project.** Sibling repositories under a shared parent are
    separate projects — the parent directory is not a project, and treating it as one
    was a documented mistake in earlier guidance.
    """
    here = Path(start or Path.cwd()).resolve()
    root = git_root(here)
    if root is not None:
        name = normalize(root.name)
        if name and name not in _GENERIC_NAMES:
            return Inferred(name, "git rev-parse --show-toplevel")
        parent = normalize(root.parent.name)
        if parent:
            return Inferred(
                parent, f"parent of the git root (its name {root.name!r} is generic)"
            )
    name = normalize(here.name)
    if name:
        return Inferred(name, "current directory name (not a git repository)")
    return Inferred(None, "no git repository and no usable directory name")


def infer_agent(env: dict[str, str] | None = None) -> Inferred:
    """Which engine is running, used as the agent token when no role is held."""
    environ = env if env is not None else dict(os.environ)
    for marker, engine in _ENGINE_MARKERS:
        if environ.get(marker):
            return Inferred(engine, f"${marker} is set")
    return Inferred(None, "no known engine marker in the environment")


def infer_role() -> Inferred:
    """Ordinary agents hold the literal role ``agent``.

    Every address is three-part. The hub's own infrastructure uses ``host`` and
    ``admin``; anything else is an ``agent`` until it says otherwise.
    """
    return Inferred("agent", "default for an ordinary agent")


def infer_identity(
    start: Path | None = None, env: dict[str, str] | None = None
) -> Identity:
    """Infer the whole address, each part carrying the evidence behind it."""
    return Identity(
        project=infer_project(start), agent=infer_agent(env), role=infer_role()
    )


def describe_missing(
    identity: Identity, start: Path | None = None, hub: str | None = None
) -> str:
    """The message shown when no config exists — an explanation, not an error.

    It has to answer three questions at once: who would I be, why do you think so, and
    what do I type. Anything less and the agent has to go and read documentation.
    """
    where = Path(start or Path.cwd()).resolve()
    lines = [
        f"No {CONFIG_NAME} found at or above {where}.",
        "",
        "Inferred identity:",
    ]
    for label, part in (
        ("project", identity.project),
        ("agent", identity.agent),
        ("role", identity.role),
    ):
        shown = part.value if part.known else "(unknown)"
        lines.append(f"  {label:8} = {shown:<20} ({part.source})")

    hub_shown = hub or "<hub url>"
    lines += ["", "Write it with:", f"  agent-inbox init --hub {hub_shown}"]
    if not identity.agent.known:
        lines += [
            "",
            "Your engine could not be detected, so name yourself explicitly:",
            f"  agent-inbox init --hub {hub_shown} --agent <name>",
        ]
    lines += [
        "",
        "The name must be unique and stable within the project — two agents sharing",
        "an address silently share an inbox. `agent-inbox agents` lists who is there.",
    ]
    return "\n".join(lines)


def render(hub: str, project: str, agent: str, role: str) -> str:
    """The file contents. Written with comments, because a human will read it next."""
    return f"""# agent-inbox — who this project is on the mailbox hub, and where the
# hub is. Written by `agent-inbox init`. Safe to edit, and safe to commit unless
# your hub URL is private to your deployment.

# The hub this project talks to.
hub = "{hub}"

# This agent's address: <project>/<agent>/<role>. Each position narrows independently,
# so `{project}` reaches every agent on the project and `{project}/{agent}` reaches
# this agent whatever its role.
project = "{project}"
agent_id = "{agent}"
role = "{role}"
"""


def write_config(
    path: Path, hub: str, project: str, agent: str, role: str, force: bool = False
) -> Path:
    """Write ``agent-inbox.toml``, refusing to clobber an existing file by default."""
    if path.exists() and not force:
        raise FileExistsError(
            f"{path} already exists — edit it, or pass --force to overwrite it."
        )
    path.write_text(render(hub, project, agent, role), encoding="utf-8")
    return path
