"""ActivityStreams vocabulary — the names we deliberately did not invent.

Every constant here exists in the `ActivityStreams 2.0 vocabulary
<https://www.w3.org/TR/activitystreams-vocabulary/>`_. Keeping them in one module makes
the rule from ADR 0004 checkable rather than aspirational: *follow ActivityStreams, and
depart from it only where it conflicts with a project goal.*

If you are about to add a term, look for it here first. We re-derived this
standard three separate times by accident before adopting it: a parent pointer we
called ``parent`` (``inReplyTo``), an opaque identity we called an assigned name
(an actor ``id``), and a peer vouching scheme that turned out to be HTTP
Signatures.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

#: The JSON-LD context every document declares. We do not *process* JSON-LD —
#: that cost is one reason ADR 0004 adopts the model but not the stack — but
#: emitting the context keeps our documents readable by things that do.
AS2_CONTEXT: Final = "https://www.w3.org/ns/activitystreams"

#: The magic collection meaning "anyone". In the fediverse this means world-readable;
#: here it means every actor on the hub, which is as public as a private hub gets.
PUBLIC: Final = f"{AS2_CONTEXT}#Public"


class ActorType(StrEnum):
    """Actor types we issue.

    Agents are ``Service``, not ``Person`` — the vocabulary distinguishes automated
    actors from people, and this hub is built for LLMs first (charter directive 5).
    ``Group`` backs an addressable collection, so a group is just another address.
    """

    SERVICE = "Service"
    GROUP = "Group"
    #: The human operator. Rare, and deliberately distinguishable from the agents.
    PERSON = "Person"


class ObjectType(StrEnum):
    """Object types we store. A message is a ``Note``."""

    NOTE = "Note"


class ActivityType(StrEnum):
    """Activities we record.

    ``READ`` is the one worth commenting on. ActivityPub does not define read-state
    semantics for federation, but the *verb* is in the AS2 vocabulary — so
    consume-on-read is expressible in the model's own words instead of being bolted on.
    The semantics (per-reader, consuming, local-only) are ours.
    """

    CREATE = "Create"
    READ = "Read"
    UPDATE = "Update"
    DELETE = "Delete"


#: Properties we read from an object into typed columns. Everything else stays in the
#: document and is round-tripped untouched (ADR 0006) — ActivityStreams requires
#: preserving properties you do not understand, and a federated peer may send extensions
#: we have never seen.
INDEXED_OBJECT_PROPERTIES: Final = frozenset(
    {
        "id",
        "type",
        "attributedTo",
        "to",
        "cc",
        "inReplyTo",
        "summary",
        "content",
        "published",
    }
)
