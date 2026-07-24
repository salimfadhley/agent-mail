"""An MCP server that is a client.

Runs on the agent's own machine, speaks MCP over stdio to whatever is in front of it,
and HTTP to the hub. **It is not a proxy** (ADR 0005): it holds no messaging semantics,
makes no routing decisions, and keeps no state. Each tool is one API call.

The test to apply if this file ever grows: *does this tool decide anything?* If it does,
the API is missing a route and the decision belongs there, where every client gets it.

Being local is also what makes push possible later — a hosted server can only answer,
whereas a process on the agent's machine can interrupt the session it serves.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from agent_mailbox.client import (
    CONFIG_NAME,
    ClientError,
    Config,
    HubClient,
    NotConfigured,
    load_config,
    project_root,
    write_config,
)

mcp = FastMCP("agent-mailbox")


def _client() -> HubClient:
    return HubClient(load_config())


def _guard(call: Any) -> Any:
    """Run a call and turn any failure into words the agent can act on.

    An exception escaping into a tool result is a stack trace in an agent's context: it
    burns attention and says nothing useful. Every failure here is a sentence.
    """
    try:
        return call()
    except NotConfigured as exc:
        return {"ok": False, "problem": "not configured", "what_to_do": str(exc)}
    except ClientError as exc:
        return {"ok": False, "problem": str(exc)}


def _summarise(note: dict[str, Any]) -> dict[str, Any]:
    """A message in the shape an agent actually wants to read."""
    return {
        "id": note.get("id"),
        "from": _leaf(note.get("attributedTo")),
        "to": [_leaf(t) for t in note.get("to") or []],
        "subject": note.get("summary"),
        "body": note.get("content"),
        "sent": note.get("published"),
        "in_reply_to": note.get("inReplyTo"),
    }


def _leaf(value: str | None) -> str | None:
    return value.rstrip("/").rsplit("/", 1)[-1] if value else value


@mcp.tool()
def ping() -> dict[str, Any]:
    """Prove you are really connected to the mailbox. Call this first.

    Returns the hub's name and your own, so a wrong hub or a wrong name shows up
    immediately rather than as confusing silence later.
    """
    return _guard(lambda: _client().ping())


@mcp.tool()
def join(
    name: str | None = None, hub: str | None = None, replace_config: bool = False
) -> dict[str, Any]:
    """Claim your name on the mailbox, and write your configuration for you.

    Call this once, on your first contact. If there is no `agent-mailbox.toml` yet,
    pass the `hub` url you were given and this writes the file into your project root
    — you do not have to create it by hand.

    A name is requested, not assumed: if it is taken you will be told, and you should
    pick another. Leave it empty and one will be issued to you. Your name is permanent
    and deliberately meaningless — do not encode your project or model into it.
    """

    def go() -> dict[str, Any]:
        try:
            config = load_config()
        except NotConfigured:
            if not hub:
                return {
                    "ok": False,
                    "problem": "no configuration yet, and no hub url given",
                    "what_to_do": (
                        "Call join again with the hub url you were given, for example "
                        'join(hub="http://<host>:8081", name="your_name"). '
                        f"I will write {CONFIG_NAME} for you."
                    ),
                }
            config = Config(hub=hub, name=name or "unnamed")

        client = HubClient(config)
        # Claim first, write second. Writing a config for a name the hub refuses would
        # leave a file claiming an identity that is not ours.
        claimed = client.join(
            name or (None if config.name == "unnamed" else config.name)
        )
        granted = claimed.get("preferredUsername", config.name)

        written = None
        target = project_root() / CONFIG_NAME
        if not target.exists() or replace_config:
            written = str(write_config(config.hub, granted, force=replace_config))
        return {
            "ok": True,
            "name": granted,
            "hub": config.hub,
            "config_written": written,
            "next": (
                "Restart your session if you have no mailbox tools yet, then call ping."
                if written
                else "Call ping to confirm, then update_profile to say who you are."
            ),
        }

    return _guard(go)


@mcp.tool()
def check_inbox() -> dict[str, Any]:
    """What is waiting for you. Does **not** consume anything.

    Call this at the start of a turn. Reading the list is free; `read_message` is what
    marks something as handled.
    """

    def go() -> dict[str, Any]:
        page = _client().check_inbox()
        return {
            "waiting": page.get("totalItems", 0),
            "messages": [_summarise(n) for n in page.get("items", [])],
        }

    return _guard(go)


@mcp.tool()
def send_message(to: str, body: str, subject: str | None = None) -> dict[str, Any]:
    """Send a message.

    `to` is another agent's name, a group, or `everyone`. A subject is optional but
    strongly encouraged — a recipient decides whether to spend a turn on your message
    from the subject alone.

    Be sparing with `everyone`: every recipient pays a full turn's attention and none
    of them can decline. A question you would like *someone* to answer belongs in a
    direct message.
    """
    return _guard(lambda: _summarise(_client().send_message(to, body, subject)))


@mcp.tool()
def read_message(message_id: str) -> dict[str, Any]:
    """Read a message and mark it handled. This is the only call that consumes."""
    return _guard(lambda: _summarise(_client().read_message(message_id)))


@mcp.tool()
def reply_message(
    message_id: str, body: str, subject: str | None = None
) -> dict[str, Any]:
    """Reply to a message. Goes to its sender, on its thread, with `Re:` added."""
    return _guard(
        lambda: _summarise(_client().reply_message(message_id, body, subject))
    )


@mcp.tool()
def read_thread(message_id: str) -> dict[str, Any]:
    """The conversation a message belongs to — the turns **you** are part of.

    You see what you sent and what was sent to you. Side conversations between others
    on the same thread are not shown, so a thread you joined through a broadcast shows
    the broadcast and not what followed privately.
    """

    def go() -> dict[str, Any]:
        page = _client().read_thread(message_id)
        return {
            "turns": page.get("totalItems", 0),
            "messages": [_summarise(n) for n in page.get("items", [])],
        }

    return _guard(go)


@mcp.tool()
def list_agents() -> dict[str, Any]:
    """Who is on this mailbox, and what each of them is for."""

    def go() -> dict[str, Any]:
        page = _client().list_agents()
        return {
            "agents": [
                {
                    "name": a.get("preferredUsername"),
                    "about": a.get("summary"),
                    "profile": a.get("profile"),
                }
                for a in page.get("items", [])
            ]
        }

    return _guard(go)


@mcp.tool()
def whois(name: str) -> dict[str, Any]:
    """One agent's profile — what they work on and what they can help with."""
    return _guard(lambda: _client().whois(name))


@mcp.tool()
def update_profile(profile: str) -> dict[str, Any]:
    """Describe yourself, as a JSON object.

    Everything descriptive lives here rather than in your name: your project, engine,
    machine, what you can help with, what you need. Facts change; your name does not.
    """

    def go() -> dict[str, Any]:
        try:
            parsed = json.loads(profile)
        except json.JSONDecodeError as exc:
            return {"ok": False, "problem": f"profile must be a JSON object: {exc}"}
        return _client().update_profile(parsed)

    return _guard(go)


@mcp.tool()
def hub_info() -> dict[str, Any]:
    """What this mailbox is, what it enforces, and whether it authenticates."""
    return _guard(lambda: _client().hub_info())


def main() -> None:
    """Entry point for `agent-mailbox-mcp`, run over stdio by an MCP client."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
