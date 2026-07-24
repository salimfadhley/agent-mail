"""The house: a mailbox, its standing residents, and its house rules.

:class:`~agent_mailbox.mailbox.Mailbox` knows what a mailbox *can* do. A house knows
what this one *always* does — who lives here whether or not anyone is home, what gets
refused, what gets logged.

Everything above this line should talk to a house, not a bare mailbox. The API in M2
and the clients in M3 get their policies for free by doing so, and a deployment adds a
rule of its own without the engine changing at all.

The wrapping is deliberately thin and mechanical: **check, act, record.** A house makes
no messaging decisions — those belong to the rules — and it never silently alters an
action. It permits it, refuses it, or watches it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from types import TracebackType
from typing import Self

from agent_mailbox.mailbox import Mailbox
from agent_mailbox.policy import Attempt, Outcome, Policy, default_policies
from agent_mailbox.records import ActorRecord, ObjectRecord

logger = logging.getLogger(__name__)


class House:
    """A mailbox with its house rules applied.

    Use it as an async context manager, which is when standing invariants are
    established::

        async with House(mailbox) as house:
            await house.send("rosemary_nasrin", "admin", "something is broken")
    """

    def __init__(
        self, mailbox: Mailbox, policies: Sequence[Policy] | None = None
    ) -> None:
        self._mailbox = mailbox
        self._policies = tuple(policies if policies is not None else default_policies())

    @property
    def mailbox(self) -> Mailbox:
        """The mailbox underneath, for operations that carry no policy."""
        return self._mailbox

    @property
    def policies(self) -> tuple[Policy, ...]:
        return self._policies

    async def open(self) -> Self:
        """Establish standing invariants. Idempotent — reopening changes nothing."""
        for policy in self._policies:
            await policy.on_open(self._mailbox)
        return self

    async def __aenter__(self) -> Self:
        return await self.open()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    # -- the pipeline ------------------------------------------------------

    async def _check(self, attempt: Attempt) -> None:
        for policy in self._policies:
            await policy.check(attempt, self._mailbox)

    async def _record(self, outcome: Outcome) -> None:
        """Tell every observer, and never let one break the mailbox.

        An observer that raises has failed at its own job, not at the mailbox's. The
        alternative — letting a broken audit logger fail a message that was already
        delivered — would make the mailbox less reliable than having no logging.
        """
        for policy in self._policies:
            try:
                await policy.record(outcome, self._mailbox)
            except Exception:  # noqa: BLE001 - a process boundary for observers
                logger.exception(
                    "policy %r failed while observing; the mailbox is unaffected",
                    getattr(policy, "name", policy),
                )

    # -- policed operations ------------------------------------------------

    async def send(
        self,
        caller: str,
        to: str | Sequence[str],
        body: str,
        *,
        subject: str | None = None,
        cc: Sequence[str] = (),
        in_reply_to: str | None = None,
    ) -> ObjectRecord:
        recipients = (to,) if isinstance(to, str) else tuple(to)
        attempt = Attempt(
            action="send",
            actor=caller,
            recipients=recipients + tuple(cc),
            subject=subject,
            body=body,
        )
        await self._check(attempt)
        try:
            sent = await self._mailbox.send(
                caller, to, body, subject=subject, cc=cc, in_reply_to=in_reply_to
            )
        except Exception as exc:
            await self._record(Outcome(attempt, ok=False, error=exc))
            raise
        await self._record(Outcome(attempt, ok=True, detail={"id": sent.id}))
        return sent

    async def read(self, caller: str, object_id: str) -> ObjectRecord:
        attempt = Attempt(action="read", actor=caller, detail={"id": object_id})
        await self._check(attempt)
        try:
            got = await self._mailbox.read(caller, object_id)
        except Exception as exc:
            # The signal a probe detector wants: reaching for something not yours.
            await self._record(
                Outcome(attempt, ok=False, error=exc, detail={"not_yours": True})
            )
            raise
        await self._record(Outcome(attempt, ok=True))
        return got

    async def join(self, requested_name: str | None = None) -> ActorRecord:
        attempt = Attempt(action="join", actor=requested_name or "<unnamed>")
        await self._check(attempt)
        try:
            actor = await self._mailbox.join(requested_name)
        except Exception as exc:
            await self._record(Outcome(attempt, ok=False, error=exc))
            raise
        await self._record(Outcome(attempt, ok=True, detail={"name": actor.name}))
        return actor

    async def reply(
        self, caller: str, object_id: str, body: str, *, subject: str | None = None
    ) -> ObjectRecord:
        original = await self._mailbox.view(caller, object_id)
        return await self.send(
            caller,
            original.attributed_to,
            body,
            subject=subject,
            in_reply_to=original.id,
        )

    # -- unpoliced pass-through -------------------------------------------
    #
    # Reading state changes nothing and refuses nothing, so there is no policy moment
    # to insert. Passing these through keeps the house from becoming a second, partial
    # copy of the mailbox's surface.

    async def peek(self, caller: str) -> tuple[ObjectRecord, ...]:
        return await self._mailbox.peek(caller)

    async def unread_count(self, caller: str) -> int:
        return await self._mailbox.unread_count(caller)

    async def thread(self, caller: str, root_id: str) -> tuple[ObjectRecord, ...]:
        return await self._mailbox.thread(caller, root_id)

    async def whois(self, name: str) -> ActorRecord | None:
        return await self._mailbox.whois(name)

    async def directory(self) -> tuple[ActorRecord, ...]:
        return await self._mailbox.directory()

    async def update_profile(
        self, caller: str, profile: dict[str, object]
    ) -> ActorRecord:
        return await self._mailbox.update_profile(caller, profile)

    async def expire(self) -> int:
        return await self._mailbox.expire()
