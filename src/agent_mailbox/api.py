"""The hub's one machine interface.

ActivityStreams on the wire, ActivityPub's route shape, served over a
:class:`~agent_mailbox.house.House` so that house rules apply to everything reachable
from outside.

**This module adds no messaging logic.** Who receives a copy, which turns of a
thread you may see, what expires — all of that is decided below, by pure functions.
What happens here is translation: HTTP in, records out, records in, HTTP out. A
structural test enforces it, because this is exactly the layer where a convenience
shortcut would reintroduce a second door.

**Nothing here authenticates.** The caller's identity arrives in a header and is
taken at face value (ADR 0007). That is acceptable on a trusted single-operator
network, and the hub says so about itself rather than leaving it to be discovered.
Authorisation is a different matter and is already enforced underneath, so the
visibility rules hold however the caller was identified.
"""

from __future__ import annotations

from typing import Any

import msgspec
from litestar import Litestar, MediaType, Request, Response, get, post, put
from litestar.di import Provide
from litestar.exceptions import HTTPException

from agent_mailbox import __version__
from agent_mailbox.errors import mailbox_error_handler
from agent_mailbox.exceptions import MailboxError
from agent_mailbox.house import House
from agent_mailbox.wire import (
    Actor,
    Collection,
    Create,
    Note,
    Renderer,
    unknown_properties,
)

#: Who is calling. A header rather than a path segment, so authentication can later
#: verify it instead of trusting it (ADR 0007).
IDENTITY_HEADER = "X-Agent-Name"

#: ActivityStreams asks for this; plain JSON clients are not refused for lacking it.
ACTIVITY_JSON = "application/activity+json"


def caller_name(request: Request) -> str:
    """The caller's name, or a refusal that says what is missing."""
    value = request.headers.get(IDENTITY_HEADER, "").strip()
    if not value:
        raise HTTPException(
            status_code=400,
            detail=f"missing {IDENTITY_HEADER} header — send your name, for example "
            "'rosemary_nasrin'. This hub does not authenticate; it takes the header "
            "at its word.",
        )
    return value


def owns(name: str, caller: str, wire: Renderer) -> str:
    """Check that the caller is who the path says, or refuse.

    An earlier version accepted the path parameter and quietly ignored it, so
    ``GET /actors/alice/inbox`` with a header of ``bob`` returned *Bob's* inbox and a
    cheerful 200. That made the URL's owner meaningless, and would have laid a trap for
    authentication: an edge or middleware checking the path would have been checking
    nothing at all.
    """
    wanted = wire.name_from(name)
    if wanted != caller:
        raise HTTPException(
            status_code=403,
            detail=(
                f"this is {wanted}'s mailbox and you are {caller} — "
                f"use /actors/{caller}/… for your own"
            ),
        )
    return caller


class Api:
    """Routes over a house. Holds the house and the renderer; decides nothing."""

    def __init__(self, house: House, public_url: str) -> None:
        self.house = house
        self.wire = Renderer(public_url)

    # -- hub ---------------------------------------------------------------

    async def hub(self) -> dict[str, Any]:
        mailbox = self.house.mailbox
        return {
            "@context": "https://www.w3.org/ns/activitystreams",
            "type": "Service",
            "name": mailbox.hub_name,
            "version": __version__,
            "id": self.wire.base,
            # Said out loud, because a hub that quietly does not authenticate is worse
            # than one that says so.
            "authenticated": False,
            "note": (
                "This hub does not authenticate. The caller's name is taken from the "
                f"{IDENTITY_HEADER} header at face value. Suitable for a trusted "
                "network only."
            ),
            "policies": [getattr(p, "name", "?") for p in self.house.policies],
            "federates": False,
        }

    async def health(self) -> dict[str, str]:
        """Liveness only — deliberately does not touch the store.

        A wedged database should be reported by the routes that need it, not hidden
        behind a health check that also hangs.
        """
        return {"status": "ok"}

    # -- actors ------------------------------------------------------------

    async def join(self, data: dict[str, Any]) -> Actor:
        requested = data.get("preferredUsername") or data.get("name")
        actor = await self.house.join(requested)
        return self.wire.actor(actor)

    async def directory(self) -> Collection:
        actors = await self.house.directory()
        return self.wire.collection([self.wire.actor(a) for a in actors])

    async def actor(self, name: str) -> Actor:
        record = await self.house.whois(self.wire.name_from(name))
        if record is None:
            raise HTTPException(status_code=404, detail=f"no actor named {name!r}")
        return self.wire.actor(record)

    async def update_profile(
        self, name: str, data: dict[str, Any], caller: str
    ) -> Actor:
        owns(name, caller, self.wire)
        profile = data.get("profile", data)
        record = await self.house.update_profile(caller, dict(profile))
        return self.wire.actor(record)

    # -- mail --------------------------------------------------------------

    async def outbox(self, name: str, request: Request, caller: str) -> Note:
        owns(name, caller, self.wire)
        raw: dict[str, Any] = await request.json()
        activity = decode_activity(raw)
        note = activity.object

        parent = (
            self.wire.object_id_from(note.in_reply_to) if note.in_reply_to else None
        )
        if parent and not note.to and not note.cc:
            # A note with a parent and no recipients *is* a reply. Sending it as-is
            # would return 201 and reach nobody — a silent success, which is the worst
            # failure shape we have. House.reply addresses the original sender and
            # adds the `Re:` subject.
            replied = await self.house.reply(
                caller, parent, note.content, subject=note.summary
            )
            return self.wire.note(replied)

        sent = await self.house.send(
            caller,
            self.wire.recipients(note.to),
            note.content,
            subject=note.summary,
            cc=self.wire.recipients(note.cc),
            in_reply_to=parent,
            # Whatever this document carried that we do not model, kept verbatim.
            document=unknown_properties(raw) or None,
        )
        return self.wire.note(sent)

    async def inbox(self, name: str, caller: str) -> Collection:
        owns(name, caller, self.wire)
        waiting = await self.house.peek(caller)
        return self.wire.collection([self.wire.note(m) for m in waiting])

    async def read_object(self, object_id: str, caller: str) -> Note:
        got = await self.house.read(caller, self.wire.object_id_from(object_id))
        return self.wire.note(got)

    async def view_object(self, object_id: str, caller: str) -> Note:
        got = await self.house.view(caller, self.wire.object_id_from(object_id))
        return self.wire.note(got)

    async def thread(self, object_id: str, caller: str) -> Collection:
        turns = await self.house.thread(caller, self.wire.object_id_from(object_id))
        if not turns:
            # Absent and forbidden are the same answer, on purpose.
            raise HTTPException(status_code=404, detail="no such thread")
        return self.wire.collection([self.wire.note(m) for m in turns])

    async def federation_inbox(self, name: str) -> Response:
        return Response(
            status_code=501,
            content={
                "code": "not_implemented",
                "detail": (
                    "This hub does not federate. Delivery from other mailboxes is "
                    "mission 0024 (Pen Pals) and mission 0025 (fediverse profile)."
                ),
            },
        )


def decode_activity(raw: dict[str, Any]) -> Create:
    """Accept a bare Note as well as a Create wrapping one.

    A client that posts what it means — a note — should not have to know that AS2 wraps
    it in an activity. We normalise rather than refuse.
    """
    if raw.get("type") == "Create" or "object" in raw:
        return msgspec.convert(raw, Create, strict=False)
    return Create(object=msgspec.convert(raw, Note, strict=False))


def build_api(house: House, public_url: str, *, debug: bool = False) -> Litestar:
    """Assemble the app. Everything routes through the house."""
    api = Api(house, public_url)

    async def provide_caller(request: Request) -> str:
        return caller_name(request)

    @get("/", media_type=MediaType.JSON)
    async def hub() -> dict[str, Any]:
        return await api.hub()

    @get("/health")
    async def health() -> dict[str, str]:
        return await api.health()

    @post("/actors", status_code=201)
    async def join(data: dict[str, Any]) -> Actor:
        return await api.join(data)

    @get("/actors")
    async def directory() -> Collection:
        return await api.directory()

    @get("/actors/{name:str}")
    async def actor(name: str) -> Actor:
        return await api.actor(name)

    @put("/actors/{name:str}", dependencies={"caller": Provide(provide_caller)})
    async def update_profile(name: str, data: dict[str, Any], caller: str) -> Actor:
        return await api.update_profile(name, data, caller)

    @get("/actors/{name:str}/inbox", dependencies={"caller": Provide(provide_caller)})
    async def inbox(name: str, caller: str) -> Collection:
        return await api.inbox(name, caller)

    @post("/actors/{name:str}/inbox")
    async def federation_inbox(name: str) -> Response:
        return await api.federation_inbox(name)

    @post(
        "/actors/{name:str}/outbox",
        status_code=201,
        dependencies={"caller": Provide(provide_caller)},
    )
    async def outbox(name: str, request: Request, caller: str) -> Note:
        return await api.outbox(name, request, caller)

    @get("/objects/{object_id:str}", dependencies={"caller": Provide(provide_caller)})
    async def view_object(object_id: str, caller: str) -> Note:
        return await api.view_object(object_id, caller)

    @post(
        "/objects/{object_id:str}/read",
        # 200, not Litestar's default 201: consuming a message creates nothing.
        status_code=200,
        dependencies={"caller": Provide(provide_caller)},
    )
    async def read_object(object_id: str, caller: str) -> Note:
        return await api.read_object(object_id, caller)

    @get(
        "/objects/{object_id:str}/thread",
        dependencies={"caller": Provide(provide_caller)},
    )
    async def thread(object_id: str, caller: str) -> Collection:
        return await api.thread(object_id, caller)

    async def open_the_house(_: Litestar) -> None:
        """Establish standing invariants once, at startup.

        This is where `admin` and `host` come into being. Without it the hub would
        serve happily and quietly have nowhere to report a fault to.
        """
        await house.open()

    app = Litestar(
        on_startup=[open_the_house],
        route_handlers=[
            hub,
            health,
            join,
            directory,
            actor,
            update_profile,
            inbox,
            federation_inbox,
            outbox,
            view_object,
            read_object,
            thread,
        ],
        exception_handlers={MailboxError: mailbox_error_handler},
        debug=debug,
    )
    app.state.api = api
    return app
