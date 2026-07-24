"""The house: standing residents and house rules over a working mailbox."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest

from agent_mailbox.exceptions import NameUnavailable, NoSuchMessage
from agent_mailbox.house import House
from agent_mailbox.mailbox import Mailbox
from agent_mailbox.policy import (
    ADMIN,
    HOST,
    Attempt,
    AuditLog,
    BasePolicy,
    MessageLimits,
    Outcome,
    Policy,
    PolicyRefusal,
    ProbeDetector,
    StandingResidents,
)
from agent_mailbox.store import InMemoryStore

ROSEMARY = "rosemary_nasrin"
TREVOR = "trevor_mahmood"


@pytest.fixture
async def house() -> AsyncIterator[House]:
    async with House(Mailbox(InMemoryStore())) as opened:
        yield opened


class TestStandingResidents:
    async def test_admin_and_host_exist_before_anyone_joins(self, house: House) -> None:
        for resident in (ADMIN, HOST):
            actor = await house.whois(resident)
            assert actor is not None, f"{resident} must exist from the start"
            assert actor.profile["standing"] is True

    async def test_each_says_what_it_is_for(self, house: House) -> None:
        """An agent decides where to write from the profile, so it must be legible."""
        admin = await house.whois(ADMIN)
        host = await house.whois(HOST)
        assert admin is not None and host is not None
        assert "broken" in admin.profile["purpose"]
        assert "Introductions" in host.profile["purpose"]

    async def test_mail_to_an_absent_admin_waits_rather_than_failing(
        self, house: House
    ) -> None:
        """The thing most worth reporting is that something is broken.

        "The mailbox for reporting breakage does not exist yet" is a poor answer.
        """
        await house.join(ROSEMARY)
        sent = await house.send(ROSEMARY, ADMIN, "the flow graph 500s on an empty hub")

        waiting = await house.peek(ADMIN)
        assert [m.id for m in waiting] == [sent.id]

    async def test_no_agent_can_claim_a_standing_name(self, house: House) -> None:
        for resident in (ADMIN, HOST):
            with pytest.raises(NameUnavailable, match="reserved"):
                await house.join(resident)

    async def test_opening_twice_does_not_disturb_a_resident(self) -> None:
        """Reopening a mailbox must not reset profiles that were edited."""
        mailbox = Mailbox(InMemoryStore())
        async with House(mailbox) as house:
            await house.mailbox.install_resident(ADMIN)
            await house.update_profile(ADMIN, {"purpose": "edited by hand"})

        async with House(mailbox) as reopened:
            admin = await reopened.whois(ADMIN)
            assert admin is not None
            assert admin.profile["purpose"] == "edited by hand"


class TestMessageLimits:
    async def test_an_oversized_message_is_refused_with_the_limit(self) -> None:
        async with House(
            Mailbox(InMemoryStore()),
            [StandingResidents(), MessageLimits(max_body_bytes=100)],
        ) as house:
            await house.join(ROSEMARY)
            await house.join(TREVOR)
            with pytest.raises(PolicyRefusal, match="accepts up to 100"):
                await house.send(ROSEMARY, TREVOR, "x" * 200)

    async def test_too_many_recipients_is_refused(self) -> None:
        """Not about storage: every recipient spends a turn and cannot decline."""
        async with House(
            Mailbox(InMemoryStore()),
            [StandingResidents(), MessageLimits(max_recipients=2)],
        ) as house:
            await house.join(ROSEMARY)
            for n in range(3):
                await house.join(f"agent_{n}")
            with pytest.raises(PolicyRefusal, match="address a group instead"):
                await house.send(ROSEMARY, ["agent_0", "agent_1", "agent_2"], "hi")

    async def test_a_refusal_names_the_policy(self) -> None:
        """An agent that cannot ask a follow-up needs the reason in the refusal."""
        async with House(
            Mailbox(InMemoryStore()),
            [StandingResidents(), MessageLimits(max_body_bytes=10)],
        ) as house:
            await house.join(ROSEMARY)
            await house.join(TREVOR)
            with pytest.raises(PolicyRefusal) as exc:
                await house.send(ROSEMARY, TREVOR, "far too long a message")
            assert exc.value.policy == "message_limits"
            assert exc.value.code == "policy_refusal"

    async def test_nothing_is_stored_when_a_policy_refuses(self) -> None:
        async with House(
            Mailbox(InMemoryStore()),
            [StandingResidents(), MessageLimits(max_body_bytes=5)],
        ) as house:
            await house.join(ROSEMARY)
            await house.join(TREVOR)
            with pytest.raises(PolicyRefusal):
                await house.send(ROSEMARY, TREVOR, "too long")
            assert await house.peek(TREVOR) == ()


class TestObservers:
    async def test_the_audit_log_records_without_bodies(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A log that accumulates everyone's private mail is a disclosure waiting."""
        async with House(
            Mailbox(InMemoryStore()), [StandingResidents(), AuditLog()]
        ) as house:
            await house.join(ROSEMARY)
            await house.join(TREVOR)
            with caplog.at_level(logging.INFO):
                await house.send(ROSEMARY, TREVOR, "the secret is swordfish")

        logged = caplog.text
        assert "send" in logged and ROSEMARY in logged
        assert "swordfish" not in logged

    async def test_a_broken_observer_does_not_break_the_mailbox(self) -> None:
        """An observer that fails has failed at its own job, not the mailbox's."""

        class Exploding(BasePolicy):
            name = "exploding"

            async def record(self, outcome: Outcome, mailbox: Mailbox) -> None:
                raise ValueError("boom")

        async with House(
            Mailbox(InMemoryStore()), [StandingResidents(), Exploding()]
        ) as house:
            await house.join(ROSEMARY)
            await house.join(TREVOR)
            sent = await house.send(ROSEMARY, TREVOR, "still delivered")
            assert [m.id for m in await house.peek(TREVOR)] == [sent.id]

    async def test_the_probe_detector_counts_reaches_for_others_mail(self) -> None:
        detector = ProbeDetector(threshold=2)
        async with House(
            Mailbox(InMemoryStore()), [StandingResidents(), detector]
        ) as house:
            await house.join(ROSEMARY)
            await house.join(TREVOR)
            await house.join("nosy_parker")
            private = await house.send(ROSEMARY, TREVOR, "private")

            for _ in range(3):
                with pytest.raises(NoSuchMessage):
                    await house.read("nosy_parker", private.id)

        assert detector.refusals_for("nosy_parker") == 3
        assert detector.refusals_for(TREVOR) == 0

    async def test_an_observer_cannot_veto(self) -> None:
        """record() runs after the fact — by then there is nothing left to prevent."""

        class Disapproving(BasePolicy):
            name = "disapproving"
            seen = 0

            async def record(self, outcome: Outcome, mailbox: Mailbox) -> None:
                Disapproving.seen += 1
                raise PolicyRefusal(self.name, "I object")

        async with House(
            Mailbox(InMemoryStore()), [StandingResidents(), Disapproving()]
        ) as house:
            await house.join(ROSEMARY)
            await house.join(TREVOR)
            sent = await house.send(ROSEMARY, TREVOR, "delivered anyway")

        assert Disapproving.seen > 0, "the observer did run"
        assert sent.id


class TestLayering:
    async def test_a_house_makes_no_messaging_decisions_of_its_own(self) -> None:
        """With no policies it must behave exactly like the mailbox underneath."""
        mailbox = Mailbox(InMemoryStore())
        house = House(mailbox, [])
        await house.join(ROSEMARY)
        await house.join(TREVOR)

        sent = await house.send(ROSEMARY, TREVOR, "unpoliced")
        assert [m.id for m in await mailbox.peek(TREVOR)] == [sent.id]
        assert await house.whois(ADMIN) is None, "no policy, no standing residents"

    async def test_visibility_rules_still_hold_through_the_house(
        self, house: House
    ) -> None:
        """Policies are additive: they cannot loosen what the rules already enforce."""
        await house.join(ROSEMARY)
        await house.join(TREVOR)
        await house.join("bystander")

        opening = await house.send(ROSEMARY, "everyone", "pipeline down")
        await house.send(ROSEMARY, TREVOR, "between us", in_reply_to=opening.id)

        assert len(await house.thread("bystander", opening.id)) == 1
        assert len(await house.thread(TREVOR, opening.id)) == 2

    async def test_replying_through_the_house_is_policed(self) -> None:
        async with House(
            Mailbox(InMemoryStore()),
            [StandingResidents(), MessageLimits(max_body_bytes=5)],
        ) as house:
            await house.join(ROSEMARY)
            await house.join(TREVOR)
            original = await house.send(ROSEMARY, TREVOR, "hi")
            with pytest.raises(PolicyRefusal):
                await house.reply(TREVOR, original.id, "much too long a reply")

    async def test_the_built_in_policies_satisfy_the_protocol(self) -> None:
        for policy in (
            StandingResidents(),
            MessageLimits(),
            AuditLog(),
            ProbeDetector(),
        ):
            assert isinstance(policy, Policy)

    async def test_a_deployment_can_add_a_rule_without_touching_the_engine(
        self,
    ) -> None:
        """The point of the layer: house rules are additive and local."""

        class NoShouting(BasePolicy):
            name = "no_shouting"

            async def check(self, attempt: Attempt, mailbox: Mailbox) -> None:
                if attempt.action == "send" and attempt.body.isupper():
                    raise PolicyRefusal(self.name, "indoor voice, please")

        async with House(
            Mailbox(InMemoryStore()), [StandingResidents(), NoShouting()]
        ) as house:
            await house.join(ROSEMARY)
            await house.join(TREVOR)
            with pytest.raises(PolicyRefusal, match="indoor voice"):
                await house.send(ROSEMARY, TREVOR, "PIPELINE IS DOWN")
            assert await house.send(ROSEMARY, TREVOR, "pipeline is down")
