"""ActivityStreams on the wire, and the only place that maps it to records.

Two shapes, deliberately not one. :mod:`agent_mailbox.records` is storage; these structs
are what goes over HTTP. They differ in ways that matter — the wire nests a ``Note``
inside a ``Create``, uses camelCase, and renders identifiers as absolute URIs — so a
single model would serve neither well.

**Identifiers become URIs here, and only here.** The engine mints opaque ids and must
never know the hub's address (charter: no deployment-specific hostnames in code). This
layer knows how it was reached, so it expands ``abc123`` into
``https://<hub>/objects/abc123`` on the way out and strips it on the way in.

**Unknown properties survive.** ActivityStreams requires an implementation to
preserve what it does not understand, and msgspec structs drop it — so a request
body is decoded twice: once into a struct for routing, and once as a plain dict
kept for storage. One extra line at the boundary, and the thing most likely to
break, hence its own test (ADR 0006, ADR 0009).
"""

from __future__ import annotations

from typing import Any

import msgspec

from agent_mailbox.records import ActorRecord, ObjectRecord
from agent_mailbox.vocabulary import AS2_CONTEXT

#: Properties we model. Anything else in an inbound document is unknown to us and is
#: kept verbatim rather than dropped.
MODELLED = frozenset(
    {
        "@context",
        "id",
        "type",
        "attributedTo",
        "actor",
        "object",
        "to",
        "cc",
        "summary",
        "content",
        "inReplyTo",
        "published",
    }
)


class Note(
    msgspec.Struct,
    rename={"attributed_to": "attributedTo", "in_reply_to": "inReplyTo"},
    omit_defaults=False,
):
    """A message, as ActivityStreams describes one."""

    id: str | None = None
    type: str = "Note"
    attributed_to: str | None = None
    to: list[str] = []
    cc: list[str] = []
    summary: str | None = None
    content: str = ""
    in_reply_to: str | None = None
    published: str | None = None


class Create(msgspec.Struct, rename={"context": "@context"}):
    """The activity that wraps a new ``Note``."""

    object: Note
    context: str = AS2_CONTEXT
    type: str = "Create"
    actor: str | None = None


class Actor(
    msgspec.Struct,
    rename={"context": "@context", "preferred_username": "preferredUsername"},
):
    """An actor document — the profile, in AS2's words."""

    id: str
    preferred_username: str
    context: str = AS2_CONTEXT
    type: str = "Service"
    summary: str | None = None
    #: Everything descriptive. AS2 has no vocabulary for "which project do you work on",
    #: so it lives here rather than being forced into a property that means something
    #: else (ADR 0004: depart deliberately, and say so).
    profile: dict[str, Any] = {}
    inbox: str = ""
    outbox: str = ""


class Collection(
    msgspec.Struct, rename={"context": "@context", "total_items": "totalItems"}
):
    """A page of items — an inbox, a thread, the directory."""

    items: list[Any]
    context: str = AS2_CONTEXT
    type: str = "OrderedCollection"
    total_items: int = 0


class Renderer:
    """Maps between records and the wire for one hub.

    Holds the public URL, which is the single thing this layer knows and the engine
    must not.
    """

    def __init__(self, public_url: str) -> None:
        self.base = public_url.rstrip("/")

    # -- ids ---------------------------------------------------------------

    def actor_uri(self, name: str) -> str:
        return f"{self.base}/actors/{name}"

    def object_uri(self, object_id: str) -> str:
        return f"{self.base}/objects/{object_id}"

    def name_from(self, value: str) -> str:
        """Accept either a bare name or one of our actor URIs.

        Tolerant on the way in because clients are written by other people — and
        because an agent that has read an actor document will naturally send the URI
        back. Rejecting that would be pedantry.
        """
        text = value.strip()
        prefix = f"{self.base}/actors/"
        if text.startswith(prefix):
            return text[len(prefix) :].strip("/")
        if "://" in text:
            # someone else's URI: keep the last segment and let addressing refuse it
            return text.rstrip("/").rsplit("/", 1)[-1]
        return text

    def object_id_from(self, value: str) -> str:
        text = value.strip()
        prefix = f"{self.base}/objects/"
        return text[len(prefix) :].strip("/") if text.startswith(prefix) else text

    # -- records to the wire -----------------------------------------------

    def note(self, record: ObjectRecord) -> Note:
        return Note(
            id=self.object_uri(record.id),
            attributed_to=self.actor_uri(record.attributed_to),
            to=[self.actor_uri(n) for n in record.to],
            cc=[self.actor_uri(n) for n in record.cc],
            summary=record.summary,
            content=record.content,
            in_reply_to=(
                self.object_uri(record.in_reply_to) if record.in_reply_to else None
            ),
            published=record.published or None,
        )

    def actor(self, record: ActorRecord) -> Actor:
        uri = self.actor_uri(record.name)
        return Actor(
            id=uri,
            preferred_username=record.name,
            type=record.actor_type.value,
            summary=str(record.profile.get("purpose") or "") or None,
            profile=dict(record.profile),
            inbox=f"{uri}/inbox",
            outbox=f"{uri}/outbox",
        )

    def collection(self, items: list[Any]) -> Collection:
        return Collection(items=items, total_items=len(items))

    # -- the wire to arguments ---------------------------------------------

    def recipients(self, values: list[str]) -> list[str]:
        return [self.name_from(v) for v in values]


def unknown_properties(document: dict[str, Any]) -> dict[str, Any]:
    """Everything in an inbound document that we do not model.

    Kept so a peer's extensions survive a round trip. ActivityStreams requires this and
    typed structs cannot do it, which is why the body is decoded twice.
    """
    inner = document.get("object")
    source = inner if isinstance(inner, dict) else document
    return {k: v for k, v in source.items() if k not in MODELLED}
