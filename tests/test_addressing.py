"""Addressing: ``name@hub``, and the promise ``@local`` makes.

The parsing tests are pure. The round-trip tests run against both backends, and answer
the plainest question that can be asked of a mailbox: can ``a@local`` write to
``b@local``, and can ``b@local`` reply?
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from agent_mailbox.addressing import LOCAL, Address, local_name, parse
from agent_mailbox.exceptions import (
    AddressError,
    MailboxError,
    MalformedAddress,
    NameUnavailable,
    NoSuchMessage,
    RemoteMailbox,
    UnknownActor,
    UnknownRecipient,
)
from agent_mailbox.mailbox import Mailbox
from agent_mailbox.sqlite_store import SqliteStore
from agent_mailbox.store import InMemoryStore


@pytest.fixture(params=("in_memory", "sqlite"))
async def mailbox(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[Mailbox]:
    if request.param == "in_memory":
        yield Mailbox(InMemoryStore())
    else:
        async with SqliteStore(tmp_path / "mail.db") as store:
            yield Mailbox(store)


class TestParsing:
    def test_a_full_address_splits_into_name_and_hub(self) -> None:
        assert parse("trevor_mahmood@local") == Address("trevor_mahmood", "local")

    def test_a_bare_name_means_this_mailbox(self) -> None:
        """Bare names are the common case; meaning anything else would be a trap."""
        assert parse("trevor_mahmood") == Address("trevor_mahmood", LOCAL)

    def test_addresses_render_back(self) -> None:
        assert str(parse("trevor_mahmood")) == "trevor_mahmood@local"

    def test_case_is_normalised(self) -> None:
        assert parse("Trevor_Mahmood@LOCAL") == Address("trevor_mahmood", "local")

    @pytest.mark.parametrize(
        ("bad", "because"),
        [
            ("", "empty"),
            ("@local", "no name"),
            ("trevor@", "no hub"),
            ("a@b@c", "more than one"),
        ],
    )
    def test_malformed_addresses_say_what_is_wrong(
        self, bad: str, because: str
    ) -> None:
        with pytest.raises(AddressError, match=because):
            parse(bad)


class TestNonEgress:
    """``@local`` is a guarantee, not a default."""

    def test_local_can_never_leave(self) -> None:
        assert parse("trevor_mahmood@local").guarantees_non_egress

    def test_naming_the_hub_directly_carries_no_such_promise(self) -> None:
        """Equivalent for delivery today, but a hub's own name means something abroad.

        Only the literal `@local` is a promise, which is what makes containment
        checkable by reading the address.
        """
        assert not parse("trevor_mahmood@workshop").guarantees_non_egress

    def test_a_hub_answers_to_both_its_own_name_and_local(self) -> None:
        assert parse("t@local").is_local_to("workshop")
        assert parse("t@workshop").is_local_to("workshop")
        assert not parse("t@elsewhere").is_local_to("workshop")

    def test_another_mailbox_is_refused_loudly(self) -> None:
        """Not silently dropped: an agent must learn immediately.

        Federation later turns this error into a delivery, rather than changing what
        silence meant.
        """
        with pytest.raises(AddressError, match="does not federate yet"):
            local_name("trevor_mahmood@elsewhere")


class TestRoundTrip:
    """Can a@local message b@local, and can b@local reply?"""

    async def test_a_at_local_messages_b_at_local_and_b_replies(
        self, mailbox: Mailbox
    ) -> None:
        await mailbox.join("rosemary_nasrin")
        await mailbox.join("trevor_mahmood")

        sent = await mailbox.send(
            "rosemary_nasrin@local",
            "trevor_mahmood@local",
            "the payment suite fails one run in five. Any idea?",
            subject="flaky tests",
        )

        waiting = await mailbox.peek("trevor_mahmood@local")
        assert [m.summary for m in waiting] == ["flaky tests"]
        assert waiting[0].id == sent.id

        got = await mailbox.read("trevor_mahmood@local", sent.id)
        assert got.content.startswith("the payment suite")

        reply = await mailbox.reply(
            "trevor_mahmood@local", sent.id, "fixture ordering — I'll push a fix"
        )
        assert reply.to == ("rosemary_nasrin",)
        assert reply.in_reply_to == sent.id
        assert reply.summary == "Re: flaky tests"

        back = await mailbox.peek("rosemary_nasrin@local")
        assert [m.content for m in back] == ["fixture ordering — I'll push a fix"]

        thread = await mailbox.thread("rosemary_nasrin@local", sent.id)
        assert [m.summary for m in thread] == ["flaky tests", "Re: flaky tests"]

    async def test_addressed_and_bare_forms_are_the_same_actor(
        self, mailbox: Mailbox
    ) -> None:
        """`b` and `b@local` must never become two mailboxes."""
        await mailbox.join("rosemary_nasrin")
        await mailbox.join("trevor_mahmood")

        await mailbox.send("rosemary_nasrin", "trevor_mahmood@local", "one")
        await mailbox.send("rosemary_nasrin@local", "trevor_mahmood", "two")

        assert len(await mailbox.peek("trevor_mahmood")) == 2
        assert len(await mailbox.peek("trevor_mahmood@local")) == 2

    async def test_a_group_can_be_addressed_with_a_hub(self, mailbox: Mailbox) -> None:
        await mailbox.join("rosemary_nasrin")
        await mailbox.join("trevor_mahmood")
        await mailbox.send("rosemary_nasrin", "everyone@local", "all hands")
        assert len(await mailbox.peek("trevor_mahmood")) == 1

    async def test_sending_off_this_mailbox_is_refused(self, mailbox: Mailbox) -> None:
        await mailbox.join("rosemary_nasrin")
        with pytest.raises(AddressError, match="does not federate yet"):
            await mailbox.send("rosemary_nasrin", "someone@another_hub", "hello")

    async def test_a_hub_with_its_own_name_still_answers_to_local(
        self, tmp_path: Path
    ) -> None:
        mailbox = Mailbox(InMemoryStore(), hub_name="workshop")
        await mailbox.join("rosemary_nasrin")
        await mailbox.join("trevor_mahmood")

        await mailbox.send(
            "rosemary_nasrin@workshop", "trevor_mahmood@local", "either form works"
        )
        assert len(await mailbox.peek("trevor_mahmood@workshop")) == 1
        assert mailbox.address_of("trevor_mahmood") == "trevor_mahmood@local"


class TestDistinctFailures:
    """Different failures, different types — the remedies are different too."""

    async def test_a_mistyped_local_name_is_refused_not_silently_dropped(
        self, mailbox: Mailbox
    ) -> None:
        """The worst outcome for an agent is a send that succeeds and reaches nobody.

        It cannot notice the silence, and waits for a reply that is never coming.
        """
        await mailbox.join("rosemary_nasrin")
        with pytest.raises(UnknownRecipient, match="nobody here is called"):
            await mailbox.send("rosemary_nasrin", "trevor_mahmoood", "typo")

    async def test_an_unknown_local_name_and_a_remote_one_are_different_errors(
        self, mailbox: Mailbox
    ) -> None:
        """One says "fix the name"; the other says "this needs federation"."""
        await mailbox.join("rosemary_nasrin")

        with pytest.raises(UnknownRecipient) as local_miss:
            await mailbox.send("rosemary_nasrin", "nobody_here", "x")
        with pytest.raises(RemoteMailbox) as remote:
            await mailbox.send("rosemary_nasrin", "somebody@another_hub", "x")

        assert local_miss.value.code == "unknown_recipient"
        assert remote.value.code == "remote_mailbox"
        assert not isinstance(local_miss.value, RemoteMailbox)
        assert not isinstance(remote.value, UnknownRecipient)

    async def test_a_malformed_address_is_neither(self, mailbox: Mailbox) -> None:
        await mailbox.join("rosemary_nasrin")
        with pytest.raises(MalformedAddress) as exc:
            await mailbox.send("rosemary_nasrin", "a@b@c", "x")
        assert exc.value.code == "malformed_address"

    async def test_all_three_are_catchable_as_one(self, mailbox: Mailbox) -> None:
        """Catch AddressError when the difference does not matter."""
        await mailbox.join("rosemary_nasrin")
        for bad in ("nobody_here", "somebody@another_hub", "a@b@c"):
            with pytest.raises(AddressError):
                await mailbox.send("rosemary_nasrin", bad, "x")

    async def test_an_empty_group_is_not_an_error(self, mailbox: Mailbox) -> None:
        """A group with nobody in it is legitimately empty, not a typo."""
        await mailbox.join("rosemary_nasrin")
        await mailbox.update_profile("rosemary_nasrin", {"groups": ["ops"]})
        sent = await mailbox.send("rosemary_nasrin", "ops", "anyone there?")
        assert sent.to == ("ops",)

    async def test_everyone_works_on_an_otherwise_empty_mailbox(
        self, mailbox: Mailbox
    ) -> None:
        await mailbox.join("rosemary_nasrin")
        assert await mailbox.send("rosemary_nasrin", "everyone", "hello?")

    async def test_a_caller_who_never_joined_is_a_different_error_again(
        self, mailbox: Mailbox
    ) -> None:
        """About who is acting, not who is addressed — the fix is to join."""
        await mailbox.join("rosemary_nasrin")
        with pytest.raises(UnknownActor) as exc:
            await mailbox.send("ghost", "rosemary_nasrin", "boo")
        assert exc.value.code == "unknown_actor"
        assert not isinstance(exc.value, AddressError)

    async def test_every_error_carries_a_stable_code(self) -> None:
        """The code is what the API layer maps; the prose is for the agent."""
        codes = {
            MalformedAddress: "malformed_address",
            UnknownRecipient: "unknown_recipient",
            RemoteMailbox: "remote_mailbox",
            UnknownActor: "unknown_actor",
            NameUnavailable: "name_unavailable",
            NoSuchMessage: "no_such_message",
        }
        for error, code in codes.items():
            assert error.code == code
            assert issubclass(error, MailboxError)
        assert len(set(codes.values())) == len(codes), "codes must be distinct"
