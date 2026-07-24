"""The mailbox end to end — the scenarios in ``docs/messaging-rules.md``, for real.

Every test runs against **both** backends. The rules already have exhaustive unit
tests as pure functions; what is checked here is that orchestration wires them up
correctly, and that storage genuinely does not matter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_mailbox.exceptions import NameUnavailable, NoSuchMessage, UnknownActor
from agent_mailbox.mailbox import Mailbox
from agent_mailbox.records import ActorRecord
from agent_mailbox.sqlite_store import SqliteStore
from agent_mailbox.store import InMemoryStore, MessageStore
from agent_mailbox.vocabulary import ActorType

ROSEMARY = "rosemary_nasrin"
TREVOR = "trevor_mahmood"
YITZHAK = "yitzhak_levin"


@pytest.fixture(params=("in_memory", "sqlite"))
async def store(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[MessageStore]:
    if request.param == "in_memory":
        yield InMemoryStore()
    else:
        async with SqliteStore(tmp_path / "mail.db") as opened:
            yield opened


@pytest.fixture
def make_mailbox(store: MessageStore) -> Callable[..., Mailbox]:
    def build(**kwargs: object) -> Mailbox:
        return Mailbox(store, **kwargs)  # type: ignore[arg-type]

    return build


@pytest.fixture
async def mailbox(make_mailbox: Callable[..., Mailbox]) -> Mailbox:
    return make_mailbox()


async def joined(mailbox: Mailbox, *names: str) -> None:
    for name in names:
        await mailbox.join(name)


class TestScenario1Joining:
    async def test_an_agent_with_no_preference_is_issued_a_name(
        self, mailbox: Mailbox
    ) -> None:
        actor = await mailbox.join()
        assert "_" in actor.name
        assert await mailbox.whois(actor.name) is not None

    async def test_a_requested_name_is_granted_if_free(self, mailbox: Mailbox) -> None:
        assert (await mailbox.join(TREVOR)).name == TREVOR

    async def test_a_taken_name_is_refused_with_advice(self, mailbox: Mailbox) -> None:
        await mailbox.join(TREVOR)
        with pytest.raises(NameUnavailable, match="taken"):
            await mailbox.join(TREVOR)

    async def test_a_reserved_name_is_refused(self, mailbox: Mailbox) -> None:
        with pytest.raises(NameUnavailable, match="reserved"):
            await mailbox.join("local")

    async def test_issued_names_do_not_collide(self, mailbox: Mailbox) -> None:
        names = {(await mailbox.join()).name for _ in range(25)}
        assert len(names) == 25


class TestScenario2Profiles:
    async def test_facts_live_in_the_profile_and_may_change(
        self, mailbox: Mailbox
    ) -> None:
        await mailbox.join(ROSEMARY)
        await mailbox.update_profile(ROSEMARY, {"project": "billing", "engine": "opus"})
        updated = await mailbox.update_profile(ROSEMARY, {"project": "payments"})

        assert updated.name == ROSEMARY, "identity survives a change of facts"
        assert updated.profile["project"] == "payments"

    async def test_an_unknown_actor_cannot_act(self, mailbox: Mailbox) -> None:
        with pytest.raises(UnknownActor):
            await mailbox.update_profile("nobody", {})


class TestScenario3And4SendAndRead:
    async def test_send_peek_read(self, mailbox: Mailbox) -> None:
        await joined(mailbox, ROSEMARY, TREVOR)
        await mailbox.send(ROSEMARY, TREVOR, "any idea?", subject="flaky tests")

        waiting = await mailbox.peek(TREVOR)
        assert [m.summary for m in waiting] == ["flaky tests"]

        assert len(await mailbox.peek(TREVOR)) == 1, "peeking must not consume"

        got = await mailbox.read(TREVOR, waiting[0].id)
        assert got.content == "any idea?"
        assert await mailbox.peek(TREVOR) == ()

    async def test_the_sender_does_not_receive_their_own_message(
        self, mailbox: Mailbox
    ) -> None:
        await joined(mailbox, ROSEMARY, TREVOR)
        await mailbox.send(ROSEMARY, TREVOR, "hello")
        assert await mailbox.peek(ROSEMARY) == ()

    async def test_reading_someone_elses_mail_is_refused(
        self, mailbox: Mailbox
    ) -> None:
        await joined(mailbox, ROSEMARY, TREVOR, YITZHAK)
        sent = await mailbox.send(ROSEMARY, TREVOR, "private")
        with pytest.raises(NoSuchMessage):
            await mailbox.read(YITZHAK, sent.id)

    async def test_a_missing_message_and_a_forbidden_one_look_the_same(
        self, mailbox: Mailbox
    ) -> None:
        await joined(mailbox, ROSEMARY, TREVOR, YITZHAK)
        sent = await mailbox.send(ROSEMARY, TREVOR, "private")

        with pytest.raises(NoSuchMessage) as forbidden:
            await mailbox.read(YITZHAK, sent.id)
        with pytest.raises(NoSuchMessage) as absent:
            await mailbox.read(YITZHAK, "no-such-id")
        assert str(forbidden.value).replace(sent.id, "X") == str(absent.value).replace(
            "no-such-id", "X"
        )

    async def test_each_recipient_consumes_independently(
        self, mailbox: Mailbox
    ) -> None:
        await joined(mailbox, ROSEMARY, TREVOR, YITZHAK)
        sent = await mailbox.send(ROSEMARY, [TREVOR, YITZHAK], "for both of you")
        await mailbox.read(TREVOR, sent.id)

        assert await mailbox.peek(TREVOR) == ()
        assert len(await mailbox.peek(YITZHAK)) == 1

    async def test_unread_count_matches_what_is_waiting(self, mailbox: Mailbox) -> None:
        await joined(mailbox, ROSEMARY, TREVOR)
        await mailbox.send(ROSEMARY, TREVOR, "one")
        await mailbox.send(ROSEMARY, TREVOR, "two")
        assert await mailbox.unread_count(TREVOR) == 2


class TestScenario5Replying:
    async def test_a_reply_threads_and_reaches_the_sender(
        self, mailbox: Mailbox
    ) -> None:
        await joined(mailbox, ROSEMARY, TREVOR)
        original = await mailbox.send(ROSEMARY, TREVOR, "any idea?", subject="flaky")
        reply = await mailbox.reply(TREVOR, original.id, "fixture ordering")

        assert reply.to == (ROSEMARY,)
        assert reply.in_reply_to == original.id
        assert reply.summary == "Re: flaky"

    async def test_replying_does_not_require_reading_first(
        self, mailbox: Mailbox
    ) -> None:
        await joined(mailbox, ROSEMARY, TREVOR)
        original = await mailbox.send(ROSEMARY, TREVOR, "q")
        assert await mailbox.reply(TREVOR, original.id, "a")

    async def test_re_is_not_doubled(self, mailbox: Mailbox) -> None:
        await joined(mailbox, ROSEMARY, TREVOR)
        first = await mailbox.send(ROSEMARY, TREVOR, "q", subject="thing")
        second = await mailbox.reply(TREVOR, first.id, "a")
        third = await mailbox.reply(ROSEMARY, second.id, "b")
        assert third.summary == "Re: thing"

    async def test_you_cannot_reply_to_what_is_not_yours(
        self, mailbox: Mailbox
    ) -> None:
        await joined(mailbox, ROSEMARY, TREVOR, YITZHAK)
        private = await mailbox.send(ROSEMARY, TREVOR, "private")
        with pytest.raises(NoSuchMessage):
            await mailbox.reply(YITZHAK, private.id, "butting in")


class TestScenario6Groups:
    async def test_everyone_reaches_all_but_the_sender(self, mailbox: Mailbox) -> None:
        await joined(mailbox, ROSEMARY, TREVOR, YITZHAK)
        await mailbox.send(ROSEMARY, "everyone", "deploys paused", subject="pipeline")

        for who in (TREVOR, YITZHAK):
            assert len(await mailbox.peek(who)) == 1
        assert await mailbox.peek(ROSEMARY) == ()

    async def test_a_named_group_resolves_through_profiles(
        self, mailbox: Mailbox, store: MessageStore
    ) -> None:
        await joined(mailbox, ROSEMARY, TREVOR, YITZHAK)
        await store.claim_name(ActorRecord(name="ops", actor_type=ActorType.GROUP))
        await mailbox.update_profile(TREVOR, {"groups": ["ops"]})
        await mailbox.update_profile(YITZHAK, {"groups": ["legal"]})

        await mailbox.send(ROSEMARY, "ops", "ops only")
        assert len(await mailbox.peek(TREVOR)) == 1
        assert await mailbox.peek(YITZHAK) == ()


class TestScenario7Visibility:
    """The rule that leaked private mail in production."""

    async def test_a_bystander_sees_only_the_broadcast(self, mailbox: Mailbox) -> None:
        await joined(mailbox, ROSEMARY, TREVOR, YITZHAK)
        opening = await mailbox.send(ROSEMARY, "everyone", "pipeline down")
        aside = await mailbox.send(
            ROSEMARY, TREVOR, "my bad migration", in_reply_to=opening.id
        )
        await mailbox.reply(TREVOR, aside.id, "keep it quiet")

        seen = await mailbox.thread(YITZHAK, opening.id)
        assert [m.content for m in seen] == ["pipeline down"]

        assert len(await mailbox.thread(TREVOR, opening.id)) == 3
        assert len(await mailbox.thread(ROSEMARY, opening.id)) == 3

    async def test_an_unknown_thread_is_simply_empty(self, mailbox: Mailbox) -> None:
        await joined(mailbox, ROSEMARY)
        assert await mailbox.thread(ROSEMARY, "never-existed") == ()


class TestScenario8Intrusion:
    async def test_attaching_to_an_unseen_thread_starts_a_new_one_silently(
        self, mailbox: Mailbox
    ) -> None:
        await joined(mailbox, ROSEMARY, TREVOR, YITZHAK)
        private = await mailbox.send(ROSEMARY, TREVOR, "confidential")

        intruding = await mailbox.send(
            YITZHAK, TREVOR, "me too", in_reply_to=private.id
        )

        assert intruding.in_reply_to is None, "must not join the thread"
        assert [m.content for m in await mailbox.thread(TREVOR, private.id)] == [
            "confidential"
        ]

    async def test_a_participant_may_still_attach(self, mailbox: Mailbox) -> None:
        await joined(mailbox, ROSEMARY, TREVOR)
        opening = await mailbox.send(ROSEMARY, TREVOR, "hello")
        follow = await mailbox.send(
            TREVOR, ROSEMARY, "hello back", in_reply_to=opening.id
        )
        assert follow.in_reply_to == opening.id


class TestScenario9Expiry:
    async def test_a_live_conversation_survives_however_old_its_root(
        self, make_mailbox: Callable[..., Mailbox]
    ) -> None:
        clock = _Clock(datetime(2026, 6, 1, tzinfo=UTC))
        mailbox = make_mailbox(retention_days=14, clock=clock)
        await joined(mailbox, ROSEMARY, TREVOR)

        opening = await mailbox.send(ROSEMARY, TREVOR, "old root")
        clock.advance(days=40)
        await mailbox.send(TREVOR, ROSEMARY, "still talking", in_reply_to=opening.id)

        assert await mailbox.expire() == 0
        assert len(await mailbox.thread(TREVOR, opening.id)) == 2

    async def test_an_idle_conversation_is_removed_whole(
        self, make_mailbox: Callable[..., Mailbox]
    ) -> None:
        clock = _Clock(datetime(2026, 6, 1, tzinfo=UTC))
        mailbox = make_mailbox(retention_days=14, clock=clock)
        await joined(mailbox, ROSEMARY, TREVOR)

        opening = await mailbox.send(ROSEMARY, TREVOR, "old")
        await mailbox.send(TREVOR, ROSEMARY, "also old", in_reply_to=opening.id)
        clock.advance(days=40)

        assert await mailbox.expire() == 2
        assert await mailbox.thread(TREVOR, opening.id) == ()

    async def test_retention_of_zero_disables_expiry(
        self, make_mailbox: Callable[..., Mailbox]
    ) -> None:
        clock = _Clock(datetime(2026, 6, 1, tzinfo=UTC))
        mailbox = make_mailbox(retention_days=0, clock=clock)
        await joined(mailbox, ROSEMARY, TREVOR)
        await mailbox.send(ROSEMARY, TREVOR, "forever")
        clock.advance(days=4000)
        assert await mailbox.expire() == 0


class TestAuthenticationSeam:
    """ADR 0007: identity is an argument, and the engine never guesses it."""

    async def test_every_acting_method_takes_the_caller_explicitly(self) -> None:
        import inspect

        acting = (
            "send",
            "reply",
            "peek",
            "read",
            "thread",
            "unread_count",
            "update_profile",
        )
        for name in acting:
            params = list(inspect.signature(getattr(Mailbox, name)).parameters)
            assert params[:2] == ["self", "caller"], (
                f"{name} must take the caller explicitly — identity is never ambient"
            )

    async def test_acting_as_someone_else_is_possible_today(
        self, mailbox: Mailbox
    ) -> None:
        """Recorded deliberately: this deployment is unauthenticated.

        Nothing proves the caller is who it claims. That is acceptable on a trusted
        single-operator LAN and is stated plainly rather than implied by silence — when
        authentication arrives at the edge, this test changes and the engine does not.
        """
        await joined(mailbox, ROSEMARY, TREVOR)
        impersonation = await mailbox.send(TREVOR, ROSEMARY, "not really from Trevor")
        assert impersonation.attributed_to == TREVOR


class _Clock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, **delta: float) -> None:
        self._now += timedelta(**delta)


class TestRetroactiveMembership:
    """Regression: joining a group late must not open its history."""

    async def test_a_late_joiner_cannot_read_the_groups_past(
        self, mailbox: Mailbox
    ) -> None:
        from agent_mailbox.records import ActorRecord as _Actor
        from agent_mailbox.vocabulary import ActorType as _Type

        await joined(mailbox, ROSEMARY, TREVOR)
        await mailbox._store.claim_name(_Actor(name="ops", actor_type=_Type.GROUP))
        await mailbox.update_profile(TREVOR, {"groups": ["ops"]})

        root = await mailbox.send(ROSEMARY, "ops", "ops root")
        await mailbox.send(ROSEMARY, TREVOR, "private follow-up", in_reply_to=root.id)

        await joined(mailbox, YITZHAK)
        await mailbox.update_profile(YITZHAK, {"groups": ["ops"]})

        assert await mailbox.peek(YITZHAK) == ()
        assert await mailbox.thread(YITZHAK, root.id) == ()

    async def test_a_late_joiner_cannot_attach_to_the_groups_past(
        self, mailbox: Mailbox
    ) -> None:
        from agent_mailbox.records import ActorRecord as _Actor
        from agent_mailbox.vocabulary import ActorType as _Type

        await joined(mailbox, ROSEMARY, TREVOR)
        await mailbox._store.claim_name(_Actor(name="ops", actor_type=_Type.GROUP))
        await mailbox.update_profile(TREVOR, {"groups": ["ops"]})
        root = await mailbox.send(ROSEMARY, "ops", "ops root")

        await joined(mailbox, YITZHAK)
        await mailbox.update_profile(YITZHAK, {"groups": ["ops"]})

        intruding = await mailbox.send(YITZHAK, TREVOR, "me too", in_reply_to=root.id)
        assert intruding.in_reply_to is None

    async def test_a_late_joiner_does_receive_future_group_mail(
        self, mailbox: Mailbox
    ) -> None:
        """The fix must not break the point of groups."""
        from agent_mailbox.records import ActorRecord as _Actor
        from agent_mailbox.vocabulary import ActorType as _Type

        await joined(mailbox, ROSEMARY, YITZHAK)
        await mailbox._store.claim_name(_Actor(name="ops", actor_type=_Type.GROUP))
        await mailbox.update_profile(YITZHAK, {"groups": ["ops"]})

        await mailbox.send(ROSEMARY, "ops", "after joining")
        assert [m.content for m in await mailbox.peek(YITZHAK)] == ["after joining"]

    async def test_what_was_addressed_is_kept_alongside_who_it_reached(
        self, mailbox: Mailbox
    ) -> None:
        """`to` is who got it; `audience` is who it was aimed at (AS2)."""
        await joined(mailbox, ROSEMARY, TREVOR)
        sent = await mailbox.send(ROSEMARY, "everyone", "all hands")
        assert sent.to == (TREVOR,)
        assert sent.document["audience"] == ["everyone"]
