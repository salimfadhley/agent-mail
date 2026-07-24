"""FastMCP wrapper exposing the mailbox verbs as MCP tools.

Every tool delegates to :class:`agent_inbox.mailbox.Mailbox` — the same core the CLI
uses — so there is no logic duplication.

Two ways to run it:

* **stdio** (local, single agent): identity comes from ``AGENT_INBOX_PROJECT`` +
  ``AGENT_ID``. This is how a Claude/Codex client spawns the server as a subprocess.
* **http** (hosted, multi-agent): one server serves many agents. Each agent connects
  on its own address — ``http://<host>:<port>/<project>/<agent>/mcp`` — and the path
  names the caller. That URL is the agent's entire configuration.
  ``?project=&agent=`` and ``X-Agent-Project`` + ``X-Agent-Id`` headers also work.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from agent_inbox.config import (
    Config,
    format_address,
    hub_descriptor,
    hub_version,
    parse_target,
)
from agent_inbox.identity import (
    reset_current_agent,
    resolve_identity,
    set_current_agent,
)
from agent_inbox.mailbox import Mailbox
from agent_inbox.models import AgentProfile, Intent, Message
from agent_inbox.prompts import render_index, render_prompt

logger = logging.getLogger(__name__)

# agent-inbox is trusted-network software with no built-in auth (front it with a
# reverse proxy on untrusted networks). MCP's DNS-rebinding protection only allows
# localhost Host headers by default, which makes a hosted server unreachable by
# remote agents — so we disable it and serve any Host.
# The server name clients see. Defaults to "agent-inbox" and is overridable via
# MCP_SERVER_NAME — so renaming the project need not force every agent to
# re-register and reconnect.
mcp = FastMCP(
    os.environ.get("MCP_SERVER_NAME", "agent-inbox"),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


_STARTED_MONO = time.monotonic()


def _config() -> Config:
    return Config.from_env()


def _dump(message: Message) -> dict[str, Any]:
    return message.model_dump(by_alias=True, mode="json")


def _envelope(
    config: Config, project: str, agent: str, role: str | None = None
) -> dict[str, Any]:
    """Mailbox context returned alongside inbox responses (an 'envelope').

    Per-message metadata (the sender ``from`` and the sent time ``created``) already
    lives on each message; this adds the mailbox's own context.
    """
    now = datetime.now(tz=UTC)
    return {
        "hub": config.hub_name,
        "version": hub_version(),
        "now": now.isoformat(),
        "timezone": "UTC",
        "uptime_seconds": round(time.monotonic() - _STARTED_MONO, 1),
        "your_address": format_address(project, agent, role),
    }


@mcp.tool()
async def send_message(
    to: str,
    body: str,
    subject: str | None = None,
    thread: str | None = None,
    intent: str = Intent.message.value,
) -> dict[str, Any]:
    """Send a message. ``to`` is ``project/agent`` (direct), ``project`` (any one
    agent), or ``project/*`` (broadcast to all). Returns the sent message.

    ``subject`` is optional but encouraged: a one-line subject makes your message
    readable at a glance in the web console and email-style views.
    """
    config = _config()
    project, agent, role = resolve_identity(config)
    message = Message(
        from_=format_address(project, agent, role),
        to=to,
        subject=subject,
        body=body,
        thread=thread,
        intent=Intent(intent),
    )
    async with Mailbox(config) as mailbox:
        await mailbox.touch(project, agent, role)
        await mailbox.send(message)
    return _dump(message)


@mcp.tool()
async def check_inbox() -> dict[str, Any]:
    """List my unread messages without consuming them (peek). Call each turn.

    Returns an envelope: ``{"mailbox": {hub, version, now, timezone, uptime_seconds,
    your_address}, "messages": [...]}``. Each message carries its sender (``from``)
    and sent time (``created``).
    """
    config = _config()
    project, agent, role = resolve_identity(config)
    async with Mailbox(config) as mailbox:
        await mailbox.touch(project, agent, role)
        messages = await mailbox.peek(project, agent, role)
    return {
        "mailbox": _envelope(config, project, agent, role),
        "messages": [_dump(m) for m in messages],
    }


@mcp.tool()
async def read_message(message_id: str) -> dict[str, Any]:
    """Read one message by id and ack (consume) it.

    Returns ``{"mailbox": {...context...}, "message": {...}}``.
    """
    config = _config()
    project, agent, role = resolve_identity(config)
    async with Mailbox(config) as mailbox:
        await mailbox.touch(project, agent, role)
        message = await mailbox.read(project, agent, message_id, role)
    return {
        "mailbox": _envelope(config, project, agent, role),
        "message": _dump(message),
    }


@mcp.tool()
async def reply_message(
    message_id: str, body: str, subject: str | None = None
) -> dict[str, Any]:
    """Reply directly to the sender and ack the original. Returns the reply message."""
    config = _config()
    project, agent, role = resolve_identity(config)
    async with Mailbox(config) as mailbox:
        await mailbox.touch(project, agent, role)
        reply = await mailbox.reply(project, agent, message_id, body, subject, role)
    return _dump(reply)


@mcp.tool()
async def notify_agent(to: str, thread: str | None = None) -> dict[str, Any]:
    """Wake a target (non-durable). ``to`` is ``project/agent``, ``project``, or
    ``project/*``."""
    config = _config()
    async with Mailbox(config) as mailbox:
        await mailbox.notify(to, thread)
    return {"notified": to, "thread": thread}


@mcp.tool()
async def ping() -> dict[str, Any]:
    """Round-trip a message to yourself to confirm agent-inbox is operational.

    Good to call once on sign-on: it verifies connectivity, your identity, and that
    send + inbox + read all work. Consumes only its own probe.
    """
    config = _config()
    project, agent, role = resolve_identity(config)
    async with Mailbox(config) as mailbox:
        await mailbox.touch(project, agent, role)
        received = await mailbox.ping(project, agent, role)
    return {
        "ok": True,
        "agent": format_address(project, agent, role),
        "message_id": received.id,
    }


@mcp.tool()
async def hub_info() -> dict[str, Any]:
    """Describe this agent-inbox hub: name, version, addressing, limits, and contacts.

    Non-secret. Call on sign-on to learn which hub you reached, the max message size
    (`limits.max_message_bytes`), and who administers it.

    `storage_initialized_at` says when this hub's storage was created. If that is
    *later* than when you last registered, the directory was **reset** — re-verify your
    counterparts' addresses instead of trusting remembered ones.
    """
    config = _config()
    descriptor = hub_descriptor(config, max_message_bytes=config.max_message_bytes)
    async with Mailbox(config) as mailbox:
        descriptor["storage_initialized_at"] = await mailbox.storage_initialized_at()
    return descriptor


@mcp.tool()
async def register(
    model: str | None = None,
    status: str = "available",
    offers: list[str] | None = None,
    needs: list[str] | None = None,
    working_dir: str | None = None,
    hostname: str | None = None,
    platform: str | None = None,
    ide: str | None = None,
    open_to_help: bool | None = None,
    objective: str | None = None,
    charter_summary: str | None = None,
    human: str | None = None,
    supersedes: list[str] | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """Register/refresh my profile so other agents can find me and what I do.

    Call on sign-on. `offers` = what you can do for others; `needs` = help you want —
    those two are how the host matches agents up. Everything is optional; you can only
    set your own profile (identity comes from your connection).

    `role` is your third address position — use it when you hold a distinct named job
    (`host`, `admin`, `casework`). Most agents are just agents and can omit it. If you
    connected on a `/<project>/<agent>/<role>/mcp` URL your role is already known and
    this argument is unnecessary.

    `supersedes` lists former addresses of yours to retire, so the directory isn't left
    full of your ghosts after you re-derive an address. Only long-inactive entries can
    be retired — you can never evict a live agent.
    """
    config = _config()
    project, agent, url_role = resolve_identity(config)
    # the URL is the stronger claim; the argument is for stdio/explicit registration
    role = url_role or role
    profile = AgentProfile(
        model=model,
        status=status,
        offers=offers or [],
        needs=needs or [],
        working_dir=working_dir,
        hostname=hostname,
        platform=platform,
        ide=ide,
        open_to_help=open_to_help,
        objective=objective,
        charter_summary=charter_summary,
        human=human,
    )
    async with Mailbox(config) as mailbox:
        info = await mailbox.register(project, agent, profile, role)
        retired = (
            await mailbox.supersede(format_address(project, agent, role), supersedes)
            if supersedes
            else []
        )
    return {**info.model_dump(mode="json"), "retired": retired}


@mcp.tool()
async def update_status(status: str) -> dict[str, Any]:
    """Update just my status: `available`, `busy`, or `away`."""
    config = _config()
    project, agent, role = resolve_identity(config)
    async with Mailbox(config) as mailbox:
        info = await mailbox.update_status(project, agent, status, role)
    return info.model_dump(mode="json")


@mcp.tool()
async def list_agents(
    project: str | None = None, include_stale: bool = False
) -> dict[str, Any]:
    """List agents in the directory (optionally one project): who's here, whether
    they're online (seen recently), and their profiles (offers/needs/role).

    Long-abandoned entries are hidden by default (they're usually superseded
    identities with empty profiles); pass `include_stale=true` to see them.
    """
    config = _config()
    caller_project, caller_agent, caller_role = resolve_identity(config)
    async with Mailbox(config) as mailbox:
        await mailbox.touch(caller_project, caller_agent, caller_role)
        agents = await mailbox.list_agents(project, include_stale=include_stale)
    return {
        "mailbox": _envelope(config, caller_project, caller_agent, caller_role),
        "agents": [a.model_dump(mode="json") for a in agents],
    }


@mcp.tool()
async def list_threads(limit: int = 50) -> dict[str, Any]:
    """List conversations I'm part of — **including ones I started**, newest first.

    `check_inbox` only shows unread mail *to* me; this shows what I have *sent* and
    whether it went anywhere. Use it to track who you're waiting on instead of keeping
    notes outside the hub. `awaiting_them` means my last turn is still unread by them —
    that, not silence, is the signal to nudge.
    """
    config = _config()
    project, agent, role = resolve_identity(config)
    async with Mailbox(config) as mailbox:
        await mailbox.touch(project, agent, role)
        threads = await mailbox.list_threads(project, agent, limit, role)
    return {
        "mailbox": _envelope(config, project, agent, role),
        "threads": [
            {
                "thread": t.thread,
                "subject": t.subject,
                "counterparts": t.counterparts,
                "turns": t.turns,
                "last_at": t.last_at,
                "last_from": t.last_from,
                "awaiting_them": t.awaiting_them,
            }
            for t in threads
        ],
    }


@mcp.tool()
async def read_thread(thread_id: str) -> dict[str, Any]:
    """Read a whole conversation in order, both directions. Does NOT consume anything.

    Each turn carries `read_at` (when the recipient consumed it; null = still unread)
    and `mine`. Use this to catch up on a thread you were handed mid-way. You can only
    read threads you are party to.
    """
    config = _config()
    project, agent, role = resolve_identity(config)
    async with Mailbox(config) as mailbox:
        await mailbox.touch(project, agent, role)
        turns = await mailbox.read_thread(project, agent, thread_id, role)
    if turns is None:
        return {"found": False, "thread": thread_id}
    return {
        "mailbox": _envelope(config, project, agent, role),
        "thread": thread_id,
        "turns": [
            {**_dump(t.message), "read_at": t.read_at, "mine": t.mine} for t in turns
        ],
    }


@mcp.tool()
async def whois(address: str) -> dict[str, Any]:
    """Look up one agent's directory card. `address` is `project/agent`."""
    config = _config()
    kind, project, agent = parse_target(address)
    if kind != "direct" or project is None or agent is None:
        raise ValueError(
            f"whois needs a specific project/agent address, got {address!r}"
        )
    async with Mailbox(config) as mailbox:
        info = await mailbox.whois(project, agent)
    return (
        info.model_dump(mode="json") if info else {"found": False, "address": address}
    )


# -- HTTP multi-tenant identity middleware --------------------------------------

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class AgentIdentityMiddleware:
    """Resolve the calling agent from the request URL and bind it for the handler.

    Rewrites ``/<project>/<agent>/mcp`` to the plain mount path before delegating, so
    the same underlying MCP app serves every agent. Also answers ``GET /health`` and
    serves the hub descriptor at ``GET /`` (and ``/hub``) directly.
    """

    def __init__(
        self,
        app: ASGIApp,
        mount_path: str,
        hub_json: bytes,
        config: Config | None = None,
        web: ASGIApp | None = None,
    ) -> None:
        self._app = app
        self._mount = mount_path.rstrip("/") or "/mcp"
        self._hub_json = hub_json
        self._config = config
        self._web = web
        # /<project>/<agent>[/<role>]/mcp — the role segment is optional, so
        # two-part URLs (the common case) keep working untouched.
        self._pattern = re.compile(
            rf"^/(?P<project>[^/]+)/(?P<agent>[^/]+)(?:/(?P<role>[^/]+))?"
            rf"{re.escape(self._mount)}(?P<rest>/.*)?$"
        )
        # GET /<project>/<agent>[/<role>]/unread
        self._unread_pattern = re.compile(
            r"^/(?P<project>[^/]+)/(?P<agent>[^/]+)(?:/(?P<role>[^/]+))?/unread/?$"
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        path = scope.get("path")
        if path == "/health":
            await self._json(send, b'{"status":"ok"}')
            return
        # A deliberately tiny "do I have mail?" probe: one indexed COUNT, no MCP
        # session handshake. It runs on every beat of a recipient's loop, so it has
        # to be far cheaper than opening a streamable-http session would be.
        if path is not None and self._unread_pattern.match(path):
            await self._unread(send, path)
            return
        # The human console owns /ui; a browser hitting / is sent there, while a
        # machine (no text/html Accept) still gets the JSON hub descriptor.
        if (
            self._web is not None
            and path is not None
            and (path == "/ui" or path.startswith("/ui/"))
        ):
            await self._web(scope, receive, send)
            return
        if path in ("/", "/hub"):
            if self._web is not None and path == "/" and self._wants_html(scope):
                await self._redirect(send, "/ui")
                return
            await self._json(send, self._hub_json)
            return
        if self._config is not None and (path == "/prompts" or path == "/prompts/"):
            # A human wants the console page (readable, copyable); a machine wants
            # the plain markdown index it can parse.
            if self._web is not None and self._wants_html(scope):
                await self._redirect(send, "/ui/prompts")
                return
            await self._text(send, render_index(self._config))
            return
        if (
            self._config is not None
            and path is not None
            and path.startswith("/prompts/")
        ):
            name = path[len("/prompts/") :].strip("/")
            body = render_prompt(name, self._config)
            if body is None:
                await self._text(
                    send,
                    f"# not found\n\nNo prompt named {name!r}. "
                    f"See {self._config.base_url()}/prompts\n",
                    status=404,
                )
            else:
                await self._text(send, body)
            return
        address = self._extract(scope)
        token = set_current_agent(address)
        try:
            await self._app(scope, receive, send)
        finally:
            reset_current_agent(token)

    def _extract(self, scope: Scope) -> tuple[str, str, str | None] | None:
        match = self._pattern.match(scope.get("path", ""))
        if match:
            rest = match.group("rest") or ""
            new_path = self._mount + rest
            scope["path"] = new_path
            scope["raw_path"] = new_path.encode()
            return match.group("project"), match.group("agent"), match.group("role")
        query = parse_qs(scope.get("query_string", b"").decode())
        if query.get("project") and query.get("agent"):
            role = query.get("role", [None])[0]
            return query["project"][0], query["agent"][0], role
        headers = {k: v for k, v in (scope.get("headers") or [])}
        project = headers.get(b"x-agent-project")
        agent = headers.get(b"x-agent-id")
        if project and agent:
            role = headers.get(b"x-agent-role")
            return project.decode(), agent.decode(), role.decode() if role else None
        return None

    async def _unread(self, send: Send, path: str) -> None:
        """Answer the cheap unread probe: ``{"unread": n, "from": [...]}``."""
        match = self._unread_pattern.match(path)
        if match is None or self._config is None:  # pragma: no cover - guarded above
            await self._json(send, b'{"unread":0}')
            return
        try:
            count, senders = 0, []
            async with Mailbox(self._config) as mailbox:
                count, senders = await mailbox.unread_count(
                    match.group("project"), match.group("agent"), match.group("role")
                )
            body = json.dumps({"unread": count, "from": senders}).encode()
        except Exception:  # a probe must never take the server down
            logger.exception("unread probe failed for %s", path)
            body = b'{"unread":0,"error":"probe failed"}'
        await self._json(send, body)

    @staticmethod
    def _wants_html(scope: Scope) -> bool:
        headers = {k: v for k, v in (scope.get("headers") or [])}
        accept = headers.get(b"accept", b"").decode().lower()
        return "text/html" in accept

    @staticmethod
    async def _redirect(send: Send, location: str) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 303,
                "headers": [(b"location", location.encode())],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    @staticmethod
    async def _json(send: Send, body: bytes) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _text(send: Send, body: str, status: int = 200) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"text/markdown; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": body.encode("utf-8")})


def build_http_app(config: Config, max_message_bytes: int | None = None) -> ASGIApp:
    """Build the multi-tenant ASGI app for the hosted MCP server."""
    mcp.settings.streamable_http_path = config.path
    hub_json = json.dumps(hub_descriptor(config, max_message_bytes)).encode()
    web: ASGIApp | None = None
    if config.ui:
        from agent_inbox.webui import WebConsole

        web = WebConsole(config)
    return AgentIdentityMiddleware(
        mcp.streamable_http_app(), config.path, hub_json, config, web
    )


def serve(config: Config | None = None) -> None:
    """Run the MCP server over the configured transport."""
    config = config or _config()
    if config.transport == "http":
        import uvicorn

        # The hub advertises its configured max message size on GET /.
        max_size = config.max_message_bytes
        logger.info(
            "serving MCP over http on %s:%s (agents connect on /<project>/<agent>%s)",
            config.host,
            config.port,
            config.path,
        )
        uvicorn.run(
            build_http_app(config, max_size), host=config.host, port=config.port
        )
    else:
        logger.info("serving MCP over stdio as %s/%s", config.project, config.agent_id)
        mcp.run()


if __name__ == "__main__":  # pragma: no cover
    serve()
