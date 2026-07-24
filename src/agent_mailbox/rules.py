"""The messaging rules, as pure functions.

Every rule in ``docs/messaging-rules.md`` lives here, and every one of them is a
function from records to a decision. Nothing in this module touches storage, the clock,
the network or any global state — give it lists and it gives you answers.

That is not tidiness for its own sake. These rules are where the costly mistakes have
been: a thread-visibility bug leaked private mail in production, and expiry once deleted
live conversations. Rules that are pure can be tested exhaustively with literals, and
reviewed by reading, without a database in sight.

The scenario numbers refer to ``docs/messaging-rules.md``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from agent_mailbox.records import ActorRecord, ObjectRecord

#: Reserved audience meaning every actor on this mailbox (scenario 6).
EVERYONE = "everyone"


# ---------------------------------------------------------------- membership


def group_memberships(actors: Iterable[ActorRecord]) -> Mapping[str, frozenset[str]]:
    """Group name -> member names, derived from profiles.

    Membership is **computed from what actors say about themselves**, never parsed out
    of a name. That is what lets identity stay opaque (ADR 0003) while groups remain
    addressable: an actor's ``profile["groups"]`` lists the groups it belongs to.
    """
    members: dict[str, set[str]] = {}
    for actor in actors:
        if actor.is_group:
            members.setdefault(actor.name, set())
        for group in actor.profile.get("groups", ()) or ():
            members.setdefault(str(group), set()).add(actor.name)
    return {group: frozenset(names) for group, names in members.items()}


def resolve_audience(
    names: Iterable[str],
    all_actors: Iterable[str],
    memberships: Mapping[str, frozenset[str]],
) -> frozenset[str]:
    """Expand addressed names into the actors that actually receive a copy.

    Individuals resolve to themselves, groups to their members, and ``everyone`` to the
    whole mailbox. An unknown name resolves to nothing rather than raising: addressing
    is a *routing* question, and a message to nobody is simply delivered to nobody.
    """
    actors = frozenset(all_actors)
    resolved: set[str] = set()
    for name in names:
        if name == EVERYONE:
            resolved |= actors
        elif name in memberships:
            resolved |= memberships[name]
        elif name in actors:
            resolved.add(name)
    return frozenset(resolved)


# ------------------------------------------------------------------ delivery


def recipients_of(
    obj: ObjectRecord,
    all_actors: Iterable[str],
    memberships: Mapping[str, frozenset[str]],
) -> frozenset[str]:
    """Who receives a copy of ``obj`` — everyone addressed, **except its sender**.

    Self-exclusion is scenario 6: being handed back what you just said costs a turn and
    teaches nothing. It applies to fan-out and direct mail alike, so an agent that
    addresses a group it belongs to is not its own recipient.
    """
    return resolve_audience(obj.audience, all_actors, memberships) - {obj.attributed_to}


def delivers_to(
    obj: ObjectRecord,
    reader: str,
    all_actors: Iterable[str],
    memberships: Mapping[str, frozenset[str]],
) -> bool:
    """Whether ``obj`` was routed to ``reader``."""
    return reader in recipients_of(obj, all_actors, memberships)


def is_party_to(
    obj: ObjectRecord,
    actor: str,
    all_actors: Iterable[str],
    memberships: Mapping[str, frozenset[str]],
) -> bool:
    """Whether ``actor`` sent ``obj`` or received it.

    This is the unit of thread membership. It is deliberately about **one message**,
    never about a conversation — see :func:`visible_turns`.
    """
    return obj.attributed_to == actor or delivers_to(
        obj, actor, all_actors, memberships
    )


def unread(
    objects: Iterable[ObjectRecord],
    reader: str,
    read_object_ids: Iterable[str],
    all_actors: Iterable[str],
    memberships: Mapping[str, frozenset[str]],
) -> tuple[ObjectRecord, ...]:
    """What is waiting for ``reader`` (scenario 4).

    Peeking is a pure question about state; nothing here consumes anything.
    """
    already = frozenset(read_object_ids)
    return tuple(
        obj
        for obj in objects
        if obj.id not in already and delivers_to(obj, reader, all_actors, memberships)
    )


# ------------------------------------------------------------------ threading


def thread_root(objects: Iterable[ObjectRecord], object_id: str) -> str:
    """Follow ``inReplyTo`` up to the conversation's first message (scenario 5).

    Cycles cannot arise from correct use, but a corrupt store or a malicious peer could
    produce one, so the walk is bounded by what it has already seen rather than trusting
    the data to be acyclic.
    """
    by_id = {obj.id: obj for obj in objects}
    seen: set[str] = set()
    current = object_id
    while current not in seen:
        seen.add(current)
        obj = by_id.get(current)
        if obj is None or obj.in_reply_to is None or obj.in_reply_to not in by_id:
            return current
        current = obj.in_reply_to
    return current


def thread_members(
    objects: Iterable[ObjectRecord], root_id: str
) -> tuple[ObjectRecord, ...]:
    """Every message in the conversation rooted at ``root_id``, oldest first.

    The **whole** conversation, regardless of who may see it — this is the raw shape,
    used by expiry. Anything agent-facing must go through :func:`visible_turns`.
    """
    objects = tuple(objects)
    return tuple(obj for obj in objects if thread_root(objects, obj.id) == root_id)


def visible_turns(
    objects: Iterable[ObjectRecord],
    root_id: str,
    viewer: str,
    all_actors: Iterable[str],
    memberships: Mapping[str, frozenset[str]],
) -> tuple[ObjectRecord, ...]:
    """The turns of a thread that ``viewer`` is party to — never the whole thread.

    **Scenario 7, and the most important rule here.** Membership is per turn, not per
    thread: a bystander who received an opening broadcast sees that broadcast and
    nothing that followed privately.

    The previous implementation asked "am I party to *any* message in this thread?" and
    unlocked *all* of them. That leaked private replies to every recipient of the
    opening message, in production, with no malice required. Hence a filter rather than
    a gate.

    An empty result is returned for both "no such thread" and "none of it is yours",
    because distinguishing them tells an outsider which threads exist.
    """
    return tuple(
        obj
        for obj in thread_members(objects, root_id)
        if is_party_to(obj, viewer, all_actors, memberships)
    )


def may_attach_to(
    objects: Iterable[ObjectRecord],
    sender: str,
    parent_id: str | None,
    all_actors: Iterable[str],
    memberships: Mapping[str, frozenset[str]],
) -> bool:
    """Whether ``sender`` may reply to ``parent_id`` (scenario 8).

    Attaching to a conversation you cannot see is refused. It discloses nothing on its
    own — :func:`visible_turns` already filters — but it lets an outsider place a turn
    inside someone else's conversation, which reads as forgery to the participants.

    A caller that gets ``False`` should **start a new thread silently**, not raise: an
    error would confirm which thread ids exist, which is the thing being protected.

    A parent that does not exist is refused too, and that is the point. Allowing it
    made the answer an **existence oracle**: a forbidden parent came back cleared,
    while a nonexistent one was echoed, so a caller could tell "real but not yours"
    from "no such thing" by reading its own successful response. Both now clear, which
    is also plainly correct — you cannot reply to something that is not there.
    """
    if parent_id is None:
        return True
    parent = next((obj for obj in objects if obj.id == parent_id), None)
    if parent is None:
        return False
    return is_party_to(parent, sender, all_actors, memberships)


# -------------------------------------------------------------------- expiry


def expired_object_ids(objects: Iterable[ObjectRecord], cutoff: str) -> frozenset[str]:
    """Objects to delete: every message of every **fully idle** conversation.

    Scenario 9. Expiry is judged per *thread*, by its most recent activity, and removes
    the thread whole.

    Per-message expiry was a real bug (mission 0016): it deleted the opening message of
    a live conversation and left the replies, producing a fragment that reads as
    complete. A partial thread is worse than no thread, because nothing signals that
    anything is missing.

    ``cutoff`` is passed in rather than read from a clock, so this stays pure and
    testable at any date.
    """
    objects = tuple(objects)
    if not objects:
        return frozenset()
    latest: dict[str, str] = {}
    for obj in objects:
        root = thread_root(objects, obj.id)
        latest[root] = max(latest.get(root, ""), obj.published)
    dead = {root for root, last in latest.items() if last < cutoff}
    return frozenset(obj.id for obj in objects if thread_root(objects, obj.id) in dead)
