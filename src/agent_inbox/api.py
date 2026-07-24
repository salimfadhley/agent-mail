"""The hub's HTTP API — the one machine interface.

This replaces the hosted MCP transport, in which identity was the URL path
(``/<project>/<agent>/mcp``). That was cheap to onboard but a dead end: a caller
choosing its own identity can never be authenticated, and a server that only answers
requests can never push into a live agent session.

Here identity arrives in a **header**, so a credential can be added beside it later
without moving anything, and the routes are plain resource operations rather than
anything shaped around a particular client. That second property is load-bearing: the
human console is expected to become an ordinary consumer of this same API, and agents
may call it directly instead of going through MCP.

One deliberate omission: :meth:`Mailbox.thread` is **not** exposed. It returns every
turn on a thread regardless of who is asking, because it backs the operator console.
Publishing it would undo the per-turn visibility rule that mission 0020 established —
see ``read_thread`` below, which is the agent-safe view.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agent_inbox.config import Config, format_address, hub_descriptor, parse_address
from agent_inbox.exceptions import ConfigError, MailboxError
from agent_inbox.mailbox import Mailbox, ThreadSummary, ThreadTurn
from agent_inbox.models import AgentInfo, AgentProfile, Intent, Message

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"

#: The caller's address. A header rather than a path segment so that authentication can
#: later verify it instead of trusting it (see the module docstring).
IDENTITY_HEADER = "X-Agent-Address"


class Caller:
    """Who is making the request, parsed from the identity header."""

    __slots__ = ("project", "agent", "role")

    def __init__(self, project: str, agent: str, role: str | None) -> None:
        self.project = project
        self.agent = agent
        self.role = role

    @property
    def address(self) -> str:
        return format_address(self.project, self.agent, self.role)


async def _caller(
    x_agent_address: Annotated[str | None, Header(alias=IDENTITY_HEADER)] = None,
) -> Caller:
    """Resolve the identity header, or refuse the request.

    A wildcard is rejected: you may *address* a whole project, but you cannot *be* one.
    """
    if not x_agent_address:
        raise HTTPException(
            status_code=400,
            detail=f"missing {IDENTITY_HEADER} header — send your address as "
            "<project>/<agent>/<role>",
        )
    try:
        target = parse_address(x_agent_address)
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if target.project is None or target.agent is None:
        raise HTTPException(
            status_code=400,
            detail=f"{x_agent_address!r} is not a specific agent — {IDENTITY_HEADER} "
            "must name a project and an agent",
        )
    return Caller(target.project, target.agent, target.role)


async def _mailbox(request: Request) -> AsyncIterator[Mailbox]:
    """Open a mailbox for the life of one request.

    Per-request rather than per-app: the server process is the only thing allowed to
    touch SQLite, and short connections keep expiry and migration behaviour identical
    to every other entry point.
    """
    config: Config = request.app.state.config
    async with Mailbox(config) as mb:
        yield mb


CallerDep = Annotated[Caller, Depends(_caller)]
MailboxDep = Annotated[Mailbox, Depends(_mailbox)]


# --------------------------------------------------------------------------- payloads


class SendRequest(BaseModel):
    to: str
    body: str
    subject: str | None = None
    thread: str | None = None
    intent: Intent = Intent.message


class ReplyRequest(BaseModel):
    body: str
    subject: str | None = None


class RegisterRequest(BaseModel):
    profile: AgentProfile = Field(default_factory=AgentProfile)


class StatusRequest(BaseModel):
    status: str


class UnreadResponse(BaseModel):
    count: int
    senders: list[str]


class NotifyResponse(BaseModel):
    notified: str
    thread: str | None
    delivered: bool


class PingResponse(BaseModel):
    ok: bool
    address: str
    hub: str


# ------------------------------------------------------------------------------- app


async def _mailbox_error(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


async def _config_error(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def build_api(config: Config) -> FastAPI:
    """Build the API application."""
    api = FastAPI(
        title="agent-inbox",
        summary="Mail between local LLM agents.",
        version=str(hub_descriptor(config).get("version", "0")),
    )
    api.state.config = config
    api.add_exception_handler(MailboxError, _mailbox_error)
    api.add_exception_handler(ConfigError, _config_error)

    # -- hub ---------------------------------------------------------------

    @api.get(f"{API_PREFIX}/hub", tags=["hub"])
    async def get_hub() -> dict[str, Any]:
        """What this hub is, what it allows, and who administers it."""
        return hub_descriptor(config, config.max_message_bytes)

    @api.get(f"{API_PREFIX}/ping", tags=["hub"])
    async def ping(who: CallerDep, mb: MailboxDep) -> PingResponse:
        """Prove the whole path works: config, network, hub, storage, identity."""
        await mb.touch(who.project, who.agent, who.role)
        return PingResponse(ok=True, address=who.address, hub=config.hub_name)

    # -- mail --------------------------------------------------------------

    @api.post(f"{API_PREFIX}/messages", tags=["mail"], status_code=201)
    async def send_message(
        payload: SendRequest, who: CallerDep, mb: MailboxDep
    ) -> Message:
        """Send a message. Every matching agent receives its own copy."""
        await mb.touch(who.project, who.agent, who.role)
        return await mb.send(
            Message(
                from_=who.address,
                to=payload.to,
                subject=payload.subject,
                body=payload.body,
                thread=payload.thread,
                intent=payload.intent,
            )
        )

    @api.get(f"{API_PREFIX}/messages", tags=["mail"])
    async def inbox(who: CallerDep, mb: MailboxDep) -> list[Message]:
        """Unread mail routed to the caller. Peeking never consumes."""
        await mb.touch(who.project, who.agent, who.role)
        return await mb.peek(who.project, who.agent, who.role)

    @api.get(f"{API_PREFIX}/messages/unread", tags=["mail"])
    async def unread(who: CallerDep, mb: MailboxDep) -> UnreadResponse:
        """How much mail is waiting, and from whom — cheap enough to poll."""
        count, senders = await mb.unread_count(who.project, who.agent, who.role)
        return UnreadResponse(count=count, senders=senders)

    @api.post(f"{API_PREFIX}/messages/{{message_id}}/read", tags=["mail"])
    async def read_message(message_id: str, who: CallerDep, mb: MailboxDep) -> Message:
        """Consume one message. This is the only call that acknowledges mail."""
        return await mb.read(who.project, who.agent, message_id, who.role)

    @api.post(f"{API_PREFIX}/messages/{{message_id}}/reply", tags=["mail"])
    async def reply_message(
        message_id: str, payload: ReplyRequest, who: CallerDep, mb: MailboxDep
    ) -> Message:
        """Reply to a message, on its thread, consuming it if still unread."""
        return await mb.reply(
            who.project, who.agent, message_id, payload.body, payload.subject, who.role
        )

    @api.post(f"{API_PREFIX}/notify", tags=["mail"], status_code=202)
    async def notify(
        to: str, mb: MailboxDep, thread: str | None = None
    ) -> NotifyResponse:
        """Best-effort nudge. Storage cannot push, so this validates and no-ops."""
        await mb.notify(to, thread)
        return NotifyResponse(notified=to, thread=thread, delivered=False)

    # -- threads -----------------------------------------------------------

    @api.get(f"{API_PREFIX}/threads", tags=["threads"])
    async def list_threads(
        who: CallerDep, mb: MailboxDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
    ) -> list[ThreadSummary]:
        """Conversations the caller is part of, most recent first."""
        return await mb.list_threads(who.project, who.agent, limit, who.role)

    @api.get(f"{API_PREFIX}/threads/{{thread_id}}", tags=["threads"])
    async def read_thread(
        thread_id: str, who: CallerDep, mb: MailboxDep
    ) -> list[ThreadTurn]:
        """The turns on a thread **the caller is party to** — never the whole thread.

        Membership is per turn (mission 0020): side conversations between others on the
        same thread are not returned. Absent and forbidden are indistinguishable.
        """
        turns = await mb.read_thread(who.project, who.agent, thread_id, who.role)
        if turns is None:
            raise HTTPException(status_code=404, detail="no such thread")
        return turns

    # -- directory ---------------------------------------------------------

    @api.get(f"{API_PREFIX}/agents", tags=["directory"])
    async def list_agents(
        mb: MailboxDep, project: str | None = None
    ) -> list[AgentInfo]:
        """Who is on the hub, optionally narrowed to one project."""
        return await mb.list_agents(project)

    @api.get(f"{API_PREFIX}/agents/{{project}}/{{agent}}", tags=["directory"])
    async def whois(project: str, agent: str, mb: MailboxDep) -> AgentInfo:
        """One agent's directory entry."""
        info = await mb.whois(project, agent)
        if info is None:
            raise HTTPException(
                status_code=404, detail=f"no such agent: {project}/{agent}"
            )
        return info

    @api.put(f"{API_PREFIX}/agents/me", tags=["directory"])
    async def register(
        payload: RegisterRequest, who: CallerDep, mb: MailboxDep
    ) -> AgentInfo:
        """Announce or update the caller's own directory entry."""
        return await mb.register(who.project, who.agent, payload.profile, who.role)

    @api.post(f"{API_PREFIX}/agents/me/status", tags=["directory"])
    async def update_status(
        payload: StatusRequest, who: CallerDep, mb: MailboxDep
    ) -> AgentInfo:
        """Update just the caller's status, leaving the rest of the profile alone."""
        return await mb.update_status(who.project, who.agent, payload.status, who.role)

    return api
