"""What the store persists.

Plain frozen data. No behaviour, no storage knowledge, no network — these cross the
boundary between the pure messaging rules (:mod:`agent_mailbox.rules`) and whatever is
keeping the bytes (:mod:`agent_mailbox.store`).

Each record follows ADR 0006: **typed fields for everything we route on, plus
the whole document.** That is not redundancy: ActivityStreams requires an
implementation to
preserve properties it does not understand, so a peer may send extensions we have never
seen and they must survive a round trip; typed fields alone cannot do that.

The typed fields are **derived** from the document, never edited beside it. Two
representations that can drift are the classic failure of this pattern.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from agent_mailbox.vocabulary import ActorType, ObjectType


def _frozen(mapping: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(mapping or {}))


@dataclass(frozen=True, slots=True)
class ActorRecord:
    """An agent, a group, or the human operator.

    ``name`` is the identity: unique, opaque, permanent. Everything that can change
    lives in ``profile`` — which is the whole point of ADR 0003.
    """

    name: str
    actor_type: ActorType = ActorType.SERVICE
    #: Free-form and mutable: project, engine, host, working directory, offers, needs.
    profile: Mapping[str, Any] = field(default_factory=dict)
    created: str = ""
    last_seen: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile", _frozen(self.profile))

    @property
    def is_group(self) -> bool:
        return self.actor_type is ActorType.GROUP


@dataclass(frozen=True, slots=True)
class ObjectRecord:
    """One message: a `Note`, as stored.

    ``to`` and ``cc`` hold names — of individual actors or of groups. Resolving a group
    to its members is a **rule**, not a storage concern, so the store never needs to
    understand addressing.
    """

    id: str
    attributed_to: str
    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    in_reply_to: str | None = None
    summary: str | None = None
    content: str = ""
    published: str = ""
    object_type: ObjectType = ObjectType.NOTE
    #: The document as received, including properties we do not model (ADR 0006).
    document: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "document", _frozen(self.document))
        object.__setattr__(self, "to", tuple(self.to))
        object.__setattr__(self, "cc", tuple(self.cc))

    @property
    def audience(self) -> frozenset[str]:
        """Everyone this message names, however it names them."""
        return frozenset(self.to) | frozenset(self.cc)


@dataclass(frozen=True, slots=True)
class ReadRecord:
    """That ``reader`` consumed ``object_id`` at ``at``.

    Per reader, because a fan-out message is consumed independently by each recipient:
    "has anyone read this?" and "have *you* read this?" are different questions.
    """

    object_id: str
    reader: str
    at: str
