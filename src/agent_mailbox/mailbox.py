"""The mailbox: the primitives everything else is built on.

This is the application layer. It holds no rules of its own — every decision is made by
a pure function in :mod:`agent_mailbox.rules` — and it holds no storage knowledge; it
talks to a :class:`~agent_mailbox.store.MessageStore`. What it does is *orchestrate*:
fetch, decide, persist.

**These method names are a public contract.** They become HTTP routes, and then MCP tool
names that agents learn from a prompt. Renaming one later is a migration, so they are
chosen to read the way the messaging rules read.

**Identity is always an argument** (ADR 0007). Every method that acts as somebody takes
``caller`` explicitly — never from configuration, a global, or ambient state. This
engine cannot ask who is really calling and does not try: proving identity is the edge's
job. Today nothing proves it at all, so **this deployment is unauthenticated** and any
caller may claim any name. Authorisation is a different matter and is already enforced,
by the pure rules, below wherever authentication will eventually sit.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta

from agent_mailbox import addressing, naming, rules
from agent_mailbox.addressing import LOCAL
from agent_mailbox.exceptions import (
    NameUnavailable,
    NoSuchMessage,
    UnknownActor,
    UnknownRecipient,
)
from agent_mailbox.records import ActorRecord, ObjectRecord, ReadRecord
from agent_mailbox.store import MessageStore
from agent_mailbox.vocabulary import ActorType

#: How many attempts to find a free generated name before giving up. The pool is around
#: 340,000 combinations, so this only matters for absurdly full mailboxes.
_NAME_ATTEMPTS = 24


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Mailbox:
    """Send, receive and read mail, over any :class:`MessageStore`.

    The clock is injected so that expiry can be tested at any date; the rules
    themselves never read it (they take a cutoff).
    """

    def __init__(
        self,
        store: MessageStore,
        *,
        hub_name: str = LOCAL,
        retention_days: int = 14,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store = store
        self._hub_name = hub_name
        self._retention_days = retention_days
        self._clock = clock

    @property
    def hub_name(self) -> str:
        """What this mailbox calls itself. It also always answers to ``local``."""
        return self._hub_name

    def address_of(self, name: str) -> str:
        """The address an actor is reachable at, from outside this module."""
        return str(addressing.Address(name, LOCAL))

    def _local(self, text: str) -> str:
        """Resolve an address to a local actor name, refusing what we cannot reach.

        Everything above this line speaks addresses; everything below speaks names.
        Keeping the translation in one place is what lets the rules stay hub-agnostic.
        """
        return addressing.local_name(text, self._hub_name)

    def _now(self) -> str:
        return self._clock().isoformat()

    async def _context(self) -> tuple[tuple[str, ...], dict[str, frozenset[str]]]:
        """Who exists and which groups they are in — the inputs every rule needs."""
        actors = tuple(await self._store.actors())
        return (
            tuple(a.name for a in actors),
            dict(rules.group_memberships(actors)),
        )

    # -- identity ----------------------------------------------------------

    async def join(self, requested_name: str | None = None) -> ActorRecord:
        """Join the mailbox, with a chosen name or an issued one.

        A name is *requested*, and the mailbox decides. Uniqueness is settled by the
        store's atomic claim, never by looking first and inserting after — that
        check-then-insert is exactly how two agents came to share one inbox.
        """
        now = self._now()
        if requested_name is not None:
            # naming.validate raises NameUnavailable directly — no translation layer,
            # because a rewrap that only changes the type is a place errors get lost.
            name = naming.validate(requested_name)
            actor = ActorRecord(
                name=name.value,
                actor_type=ActorType.SERVICE,
                created=now,
                last_seen=now,
            )
            if not await self._store.claim_name(actor):
                raise NameUnavailable(
                    f"{name.value!r} is taken — choose another, or join without a name "
                    "and one will be issued to you"
                )
            return actor

        for _attempt in range(_NAME_ATTEMPTS):
            candidate = naming.generate()
            actor = ActorRecord(
                name=candidate, actor_type=ActorType.SERVICE, created=now, last_seen=now
            )
            if await self._store.claim_name(actor):
                return actor
        raise NameUnavailable(  # pragma: no cover - needs a near-exhausted pool
            f"could not find a free name in {_NAME_ATTEMPTS} attempts"
        )

    async def whois(self, name: str) -> ActorRecord | None:
        """One actor's entry, or ``None``. Public — a directory is for lookups."""
        return await self._store.get_actor(name)

    async def directory(self) -> tuple[ActorRecord, ...]:
        """Everyone on the mailbox."""
        return tuple(await self._store.actors())

    async def update_profile(
        self, caller: str, profile: dict[str, object]
    ) -> ActorRecord:
        """Replace the caller's profile — the mutable half of identity (ADR 0003)."""
        actor = await self._require_actor(caller)
        updated = ActorRecord(
            name=actor.name,
            actor_type=actor.actor_type,
            profile=profile,
            created=actor.created,
            last_seen=self._now(),
        )
        await self._store.put_actor(updated)
        return updated

    async def _require_actor(self, name: str) -> ActorRecord:
        resolved = self._local(name)
        actor = await self._store.get_actor(resolved)
        if actor is None:
            raise UnknownActor(f"{name!r} has not joined this mailbox")
        return actor

    # -- sending -----------------------------------------------------------

    async def send(
        self,
        caller: str,
        to: str | Sequence[str],
        body: str,
        *,
        subject: str | None = None,
        cc: Sequence[str] = (),
        in_reply_to: str | None = None,
        document: dict[str, object] | None = None,
    ) -> ObjectRecord:
        """Send a message. Every actor addressed receives their own copy.

        If ``in_reply_to`` names a conversation the caller cannot see, the message
        **silently starts its own thread** instead of joining. The silence is the point:
        an error would confirm which threads exist, which is what the refusal protects.
        """
        sender = (await self._require_actor(caller)).name
        raw = (to,) if isinstance(to, str) else tuple(to)
        recipients = tuple(self._local(one) for one in raw)
        copies = tuple(self._local(one) for one in cc)
        all_actors, memberships = await self._context()
        self._reject_unknown_recipients(recipients + copies, all_actors, memberships)

        parent = in_reply_to
        if parent is not None:
            objects = tuple(await self._store.objects())
            if not rules.may_attach_to(
                objects, sender, parent, all_actors, memberships
            ):
                parent = None

        # Resolve the audience **now**, and store who it actually reached.
        #
        # ActivityStreams puts resolved recipients in `to`; storing the *unresolved*
        # audience was our deviation, and it was a disclosure. Membership is
        # self-declared, so an agent that added itself to a group later became
        # retroactively party to everything that group was ever sent — able to read the
        # history and to attach turns to threads rooted in it. Resolving at send time
        # means a message reaches who was there when it was sent, which is also what
        # every mail system does.
        # The sender comes out here too: `to` now means *who received this*, and you
        # never receive your own message. Read-time exclusion still happens and is
        # harmless — but a stored `to` that listed the sender would be a lie.
        reached = rules.resolve_audience(recipients, all_actors, memberships) - {sender}
        also = (
            rules.resolve_audience(copies, all_actors, memberships) - {sender} - reached
        )
        resolved_to = tuple(sorted(reached))
        resolved_cc = tuple(sorted(also))

        obj = ObjectRecord(
            id=uuid.uuid4().hex,
            attributed_to=sender,
            to=resolved_to,
            cc=resolved_cc,
            in_reply_to=parent,
            summary=subject,
            content=body,
            published=self._now(),
            # What was typed, kept for display and provenance. AS2 has `audience` for
            # exactly this: `to` is who it went to, `audience` is who it was aimed at.
            # `audience` is what was typed; anything else is a property we do not
            # model and are required to preserve (ADR 0006).
            document={"audience": list(recipients + copies), **(document or {})},
        )
        await self._store.add_object(obj)
        return obj

    @staticmethod
    def _reject_unknown_recipients(
        names: Sequence[str],
        all_actors: Sequence[str],
        memberships: dict[str, frozenset[str]],
    ) -> None:
        """Refuse a specific name nobody holds, before anything is stored.

        A message that reports success and reaches nobody is the worst outcome for an
        agent: it cannot notice the silence, and will wait for a reply that is never
        coming. So a mistyped name is an error.

        Groups are exempt. An empty group is legitimately empty — everyone may have
        left, or nobody may have joined yet — and that is not the sender's mistake.
        """
        known = set(all_actors) | set(memberships) | {rules.EVERYONE}
        missing = [name for name in names if name not in known]
        if missing:
            raise UnknownRecipient(
                f"nobody here is called {', '.join(repr(m) for m in missing)} — "
                "check the name, or call `directory` to see who has joined"
            )

    async def reply(
        self, caller: str, object_id: str, body: str, *, subject: str | None = None
    ) -> ObjectRecord:
        """Reply to a message, to its sender, on its thread.

        Replying does not require having read it first: reading is the natural
        precondition, so demanding it would make the obvious order the one that fails.
        """
        original = await self._visible_object(caller, object_id)
        return await self.send(
            caller,
            original.attributed_to,
            body,
            subject=subject or _reply_subject(original.summary),
            in_reply_to=original.id,
        )

    # -- receiving ---------------------------------------------------------

    async def peek(self, caller: str) -> tuple[ObjectRecord, ...]:
        """What is waiting, without consuming any of it."""
        me = (await self._require_actor(caller)).name
        all_actors, memberships = await self._context()
        objects = tuple(await self._store.objects())
        read_ids = await self._read_by(me, objects)
        return rules.unread(objects, me, read_ids, all_actors, memberships)

    async def unread_count(self, caller: str) -> int:
        """How much is waiting. Cheap enough for an agent to ask every turn."""
        return len(await self.peek(caller))

    async def read(self, caller: str, object_id: str) -> ObjectRecord:
        """Consume one message.

        The only call that acknowledges mail, and it acknowledges it **for this reader
        only** — another recipient's copy is untouched.
        """
        me = (await self._require_actor(caller)).name
        obj = await self._visible_object(caller, object_id)
        await self._store.mark_read(ReadRecord(obj.id, me, self._now()))
        return obj

    async def thread(self, caller: str, root_id: str) -> tuple[ObjectRecord, ...]:
        """The turns of a conversation **the caller is party to** — never all of it.

        Membership is per turn. A bystander who received an opening broadcast sees that
        broadcast and nothing that followed privately. An empty result means either "no
        such thread" or "none of it is yours", and the two are indistinguishable on
        purpose.
        """
        me = (await self._require_actor(caller)).name
        all_actors, memberships = await self._context()
        objects = tuple(await self._store.objects())
        return rules.visible_turns(objects, root_id, me, all_actors, memberships)

    async def view(self, caller: str, object_id: str) -> ObjectRecord:
        """One message the caller is party to, **without consuming it**.

        The single-message counterpart of :meth:`peek`. Useful when you need a
        message's details in order to act on it — replying, say — and consuming it as a
        side effect of looking would be a trap.
        """
        return await self._visible_object(caller, object_id)

    async def install_resident(
        self, name: str, *, profile: dict[str, object] | None = None
    ) -> ActorRecord:
        """Create a standing mailbox the hub itself owns, bypassing name reservation.

        ``admin`` and ``host`` are reserved precisely so no agent can claim them, which
        also means the ordinary :meth:`join` path cannot create them. This is the
        deliberate exception, used by policy at startup and nowhere else.

        Idempotent: if the name is already held, the existing actor is returned
        untouched, so reopening a mailbox never disturbs a resident's profile.
        """
        now = self._now()
        actor = ActorRecord(
            name=name,
            actor_type=ActorType.SERVICE,
            profile=profile or {},
            created=now,
            last_seen=now,
        )
        if await self._store.claim_name(actor):
            return actor
        existing = await self._store.get_actor(name)
        return existing if existing is not None else actor

    async def _visible_object(self, caller: str, object_id: str) -> ObjectRecord:
        """Fetch a message the caller is party to, or refuse indistinguishably."""
        me = (await self._require_actor(caller)).name
        obj = await self._store.get_object(object_id)
        if obj is None:
            raise NoSuchMessage(f"no message {object_id!r} available to you")
        all_actors, memberships = await self._context()
        if not rules.is_party_to(obj, me, all_actors, memberships):
            raise NoSuchMessage(f"no message {object_id!r} available to you")
        return obj

    async def _read_by(
        self, caller: str, objects: Iterable[ObjectRecord]
    ) -> frozenset[str]:
        reads = await self._store.reads_of([o.id for o in objects])
        return frozenset(
            object_id
            for object_id, entries in reads.items()
            if any(entry.reader == caller for entry in entries)
        )

    # -- housekeeping ------------------------------------------------------

    async def expire(self) -> int:
        """Remove conversations that have gone quiet. Returns messages removed.

        Judged per thread by its most recent activity, and removed whole. Expiring
        message by message once deleted the opening of a live conversation and left the
        replies — a fragment that reads as complete is worse than no fragment at all.
        """
        if self._retention_days <= 0:
            return 0
        cutoff = (self._clock() - timedelta(days=self._retention_days)).isoformat()
        objects = tuple(await self._store.objects())
        doomed = rules.expired_object_ids(objects, cutoff)
        return await self._store.remove_objects(doomed) if doomed else 0


def _reply_subject(subject: str | None) -> str | None:
    if subject is None:
        return None
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"
