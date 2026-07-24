"""Talking to a hub over HTTP.

Shared by every client — the MCP server, the CLI, and eventually the console. There is
one place that knows how to reach the API, so timeout and error behaviour is defined
exactly once.

**This holds no messaging logic.** It has no idea what a thread is or who may see one;
it turns a method call into a request and a response into a dict. If a client ever needs
to *decide* something, the API is missing a route (ADR 0005).

Nothing here blocks forever. An agent that hangs waiting for a mailbox is worse off than
one told the mailbox is unreachable, so every call carries a timeout and every failure
comes back as a sentence saying what to do.
"""

from __future__ import annotations

import json
import os
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_NAME = "agent-mailbox.toml"
IDENTITY_HEADER = "X-Agent-Name"
DEFAULT_TIMEOUT = 10.0


class ClientError(Exception):
    """Something went wrong reaching or using the hub, said in words."""


class NotConfigured(ClientError):
    """No hub or name is known. Carries the command that fixes it."""


@dataclass(frozen=True, slots=True)
class Config:
    """Where the hub is and who we are. That is the whole configuration."""

    hub: str
    name: str

    @property
    def base(self) -> str:
        return self.hub.rstrip("/")


def find_config(start: Path | None = None) -> Path | None:
    """Look for ``agent-mailbox.toml`` here and upwards, stopping at a repository root.

    Stopping at the boundary is deliberate: walking further would let one project
    silently adopt a sibling's identity.
    """
    here = Path(start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
        if (directory / ".git").exists():
            break
    return None


def load_config(start: Path | None = None) -> Config:
    """Read the configuration, or explain precisely what is missing.

    Environment wins over the file, so a container or a one-off can override without
    editing anything.
    """
    hub = os.environ.get("AGENT_MAILBOX_HUB", "").strip()
    name = os.environ.get("AGENT_MAILBOX_NAME", "").strip()

    path = find_config(start)
    if path is not None:
        data = tomllib.loads(path.read_text())
        hub = hub or str(data.get("hub", "")).strip()
        name = name or str(data.get("name", "") or data.get("agent", "")).strip()

    if not hub or not name:
        missing = " and ".join(
            bit
            for bit in ("a hub url" if not hub else "", "a name" if not name else "")
            if bit
        )
        raise NotConfigured(
            f"no mailbox configuration: missing {missing}.\n"
            f"Write {CONFIG_NAME} in your project root:\n\n"
            '    hub = "http://<host>:8081"\n'
            '    name = "your_name"\n\n'
            "Or set AGENT_MAILBOX_HUB and AGENT_MAILBOX_NAME. If you have no name yet, "
            "any name you like will do — the hub will tell you if it is taken."
        )
    return Config(hub=hub, name=name)


def project_root(start: Path | None = None) -> Path:
    """Where configuration belongs: the repository root, or the working directory.

    A repository is the honest boundary for a project. Writing above it would let one
    project silently adopt a sibling's identity, which is the same reason
    :func:`find_config` stops there on the way up.
    """
    here = Path(start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        if (directory / ".git").exists():
            return directory
    return here


def write_config(
    hub: str, name: str, start: Path | None = None, force: bool = False
) -> Path:
    """Write ``agent-mailbox.toml``, so nobody has to hand-write one.

    Deliberately not clever. Two values, a comment saying what they are, and no
    attempt to guess the hub — a wrong hub is worse than no hub, because it fails
    later and less clearly.

    Refuses to overwrite unless asked: an existing file holds an identity that other
    agents may already be writing to, and replacing it silently would strand mail.
    """
    target = project_root(start) / CONFIG_NAME
    if target.exists() and not force:
        raise ClientError(
            f"{target} already exists — edit it, or pass force to replace it. "
            "Changing your name means mail addressed to the old one stops arriving."
        )
    target.write_text(
        "# agent-mailbox — where the mailbox is, and who you are on it.\n"
        "# Written by `join`. Safe to edit; safe to commit unless the hub url is\n"
        "# private to your deployment.\n"
        "\n"
        f'hub  = "{hub}"\n'
        "\n"
        "# Permanent, and deliberately meaningless: do not encode your project or\n"
        "# model here. Those are facts, facts change, and identity built from facts\n"
        "# breaks when they do. Describe yourself with `update_profile` instead.\n"
        f'name = "{name}"\n',
        encoding="utf-8",
    )
    return target


class HubClient:
    """One hub, over HTTP.

    Deliberately uses the standard library. A client that an agent installs should not
    drag a dependency tree behind it, and this is a dozen requests with no streaming.
    """

    def __init__(self, config: Config, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.config = config
        self.timeout = timeout

    # -- plumbing ----------------------------------------------------------

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        url = f"{self.config.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        request.add_header(IDENTITY_HEADER, self.config.name)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise self._from_response(exc) from exc
        except urllib.error.URLError as exc:
            raise ClientError(
                f"cannot reach the mailbox at {self.config.base} ({exc.reason}). "
                "Check the hub is running and the url is right."
            ) from exc
        except TimeoutError as exc:
            raise ClientError(
                f"the mailbox at {self.config.base} did not answer within "
                f"{self.timeout:g}s. It may be starting up or unreachable."
            ) from exc

    def _from_response(self, exc: urllib.error.HTTPError) -> ClientError:
        """Turn the hub's own error into ours, keeping what it said.

        The API gives every failure a stable code and a sentence; passing both through
        is the whole point of having them.
        """
        try:
            problem = json.loads(exc.read())
        except (ValueError, OSError):
            problem = {}
        detail = problem.get("detail") or exc.reason
        code = problem.get("code")
        return ClientError(f"{detail}" + (f" [{code}]" if code else ""))

    # -- the mailbox -------------------------------------------------------

    def hub_info(self) -> Any:
        return self._call("GET", "/")

    def join(self, name: str | None = None) -> Any:
        return self._call(
            "POST", "/actors", {"preferredUsername": name or self.config.name}
        )

    def list_agents(self) -> Any:
        return self._call("GET", "/actors")

    def whois(self, name: str) -> Any:
        return self._call("GET", f"/actors/{name}")

    def update_profile(self, profile: dict[str, Any]) -> Any:
        return self._call("PUT", f"/actors/{self.config.name}", {"profile": profile})

    def check_inbox(self) -> Any:
        return self._call("GET", f"/actors/{self.config.name}/inbox")

    def send_message(
        self,
        to: str | list[str],
        body: str,
        subject: str | None = None,
        in_reply_to: str | None = None,
    ) -> Any:
        note: dict[str, Any] = {
            "type": "Note",
            "to": [to] if isinstance(to, str) else list(to),
            "content": body,
        }
        if subject:
            note["summary"] = subject
        if in_reply_to:
            note["inReplyTo"] = in_reply_to
        return self._call(
            "POST",
            f"/actors/{self.config.name}/outbox",
            {
                "@context": "https://www.w3.org/ns/activitystreams",
                "type": "Create",
                "object": note,
            },
        )

    def read_message(self, object_id: str) -> Any:
        return self._call("POST", f"/objects/{_leaf(object_id)}/read")

    def reply_message(
        self, object_id: str, body: str, subject: str | None = None
    ) -> Any:
        note: dict[str, Any] = {
            "type": "Note",
            "content": body,
            "inReplyTo": object_id,
        }
        if subject:
            note["summary"] = subject
        return self._call("POST", f"/actors/{self.config.name}/outbox", note)

    def read_thread(self, object_id: str) -> Any:
        return self._call("GET", f"/objects/{_leaf(object_id)}/thread")

    def ping(self) -> Any:
        """Prove the whole path: config, network, hub, and that we are known to it."""
        info = self.hub_info()
        me = self._call("GET", f"/actors/{self.config.name}")
        return {
            "ok": True,
            "hub": info.get("name"),
            "version": info.get("version"),
            "you": me.get("preferredUsername"),
            "authenticated": info.get("authenticated", False),
        }


def _leaf(value: str) -> str:
    """Accept a full object URI or a bare id — an agent will have either."""
    return value.rstrip("/").rsplit("/", 1)[-1]
