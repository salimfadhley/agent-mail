"""The messaging rules, tested as pure functions.

One class per scenario in ``docs/messaging-rules.md``. No store, no clock, no I/O —
literals in, decisions out. That is the point of the split: the rules that have cost us
most in production are the ones that can now be checked by reading.
"""

from __future__ import annotations

from agent_mailbox.records import ActorRecord, ObjectRecord
from agent_mailbox.rules import (
    EVERYONE,
    expired_object_ids,
    group_memberships,
    is_party_to,
    may_attach_to,
    recipients_of,
    resolve_audience,
    thread_members,
    thread_root,
    unread,
    visible_turns,
)
from agent_mailbox.vocabulary import ActorType

ROSEMARY = "rosemary_nasrin"
TREVOR = "trevor_mahmood"
YITZHAK = "yitzhak_levin"
SAL = "sal"

ACTORS = (ROSEMARY, TREVOR, YITZHAK, SAL)
NO_GROUPS: dict[str, frozenset[str]] = {}


def note(
    ident: str,
    sender: str,
    to: tuple[str, ...] = (),
    *,
    cc: tuple[str, ...] = (),
    parent: str | None = None,
    body: str = "",
    when: str = "2026-07-24T12:00:00Z",
) -> ObjectRecord:
    return ObjectRecord(
        id=ident,
        attributed_to=sender,
        to=to,
        cc=cc,
        in_reply_to=parent,
        content=body,
        published=when,
    )


class TestScenario3Delivery:
    """Every actor addressed gets its own copy."""

    def test_direct_message_reaches_only_its_recipient(self) -> None:
        msg = note("m1", ROSEMARY, (TREVOR,))
        assert recipients_of(msg, ACTORS, NO_GROUPS) == {TREVOR}

    def test_cc_recipients_are_addressed_too(self) -> None:
        msg = note("m1", ROSEMARY, (TREVOR,), cc=(YITZHAK,))
        assert recipients_of(msg, ACTORS, NO_GROUPS) == {TREVOR, YITZHAK}

    def test_unknown_names_deliver_to_nobody_rather_than_raising(self) -> None:
        """Addressing is routing: a message to nobody is delivered to nobody."""
        msg = note("m1", ROSEMARY, ("nobody_here",))
        assert recipients_of(msg, ACTORS, NO_GROUPS) == frozenset()


class TestScenario6Groups:
    """A group is just an address, and you never get your own broadcast."""

    def test_everyone_reaches_the_whole_mailbox_except_the_sender(self) -> None:
        msg = note("m1", ROSEMARY, (EVERYONE,))
        assert recipients_of(msg, ACTORS, NO_GROUPS) == {TREVOR, YITZHAK, SAL}
        assert ROSEMARY not in recipients_of(msg, ACTORS, NO_GROUPS)

    def test_membership_comes_from_profiles_not_from_the_name(self) -> None:
        actors = (
            ActorRecord(name=ROSEMARY, profile={"groups": ["ops"]}),
            ActorRecord(name=TREVOR, profile={"groups": ["ops"]}),
            ActorRecord(name=YITZHAK, profile={"groups": ["legal"]}),
            ActorRecord(name="ops", actor_type=ActorType.GROUP),
        )
        memberships = group_memberships(actors)
        assert memberships["ops"] == {ROSEMARY, TREVOR}
        assert memberships["legal"] == {YITZHAK}

    def test_sender_excluded_from_a_group_it_belongs_to(self) -> None:
        memberships = {"ops": frozenset({ROSEMARY, TREVOR})}
        msg = note("m1", ROSEMARY, ("ops",))
        assert recipients_of(msg, ACTORS, memberships) == {TREVOR}

    def test_a_group_with_no_members_reaches_nobody(self) -> None:
        assert resolve_audience(("ops",), ACTORS, {"ops": frozenset()}) == frozenset()


class TestScenario4Peeking:
    """Peek never consumes; unread is a question about state."""

    def test_unread_lists_only_what_was_routed_to_you(self) -> None:
        objects = (
            note("m1", ROSEMARY, (TREVOR,)),
            note("m2", ROSEMARY, (YITZHAK,)),
        )
        assert [o.id for o in unread(objects, TREVOR, (), ACTORS, NO_GROUPS)] == ["m1"]

    def test_already_read_messages_drop_out(self) -> None:
        objects = (note("m1", ROSEMARY, (TREVOR,)),)
        assert unread(objects, TREVOR, ("m1",), ACTORS, NO_GROUPS) == ()

    def test_read_state_is_per_reader(self) -> None:
        """Trevor consuming his copy must not consume Yitzhak's."""
        objects = (note("m1", ROSEMARY, (TREVOR, YITZHAK)),)
        assert unread(objects, TREVOR, ("m1",), ACTORS, NO_GROUPS) == ()
        assert len(unread(objects, YITZHAK, (), ACTORS, NO_GROUPS)) == 1


class TestScenario5Threading:
    """Parent pointers, not thread labels."""

    def test_root_of_an_opening_message_is_itself(self) -> None:
        objects = (note("m1", ROSEMARY, (TREVOR,)),)
        assert thread_root(objects, "m1") == "m1"

    def test_root_is_found_through_a_chain_of_replies(self) -> None:
        objects = (
            note("m1", ROSEMARY, (TREVOR,)),
            note("m2", TREVOR, (ROSEMARY,), parent="m1"),
            note("m3", ROSEMARY, (TREVOR,), parent="m2"),
        )
        assert thread_root(objects, "m3") == "m1"
        assert [o.id for o in thread_members(objects, "m1")] == ["m1", "m2", "m3"]

    def test_a_missing_parent_starts_a_new_thread(self) -> None:
        """A reply whose parent has expired is a root, not an orphan."""
        objects = (note("m2", TREVOR, (ROSEMARY,), parent="gone"),)
        assert thread_root(objects, "m2") == "m2"

    def test_a_cycle_terminates(self) -> None:
        """Correct use cannot produce one; a corrupt store or a peer could."""
        objects = (
            note("m1", ROSEMARY, (TREVOR,), parent="m2"),
            note("m2", TREVOR, (ROSEMARY,), parent="m1"),
        )
        assert thread_root(objects, "m1") in {"m1", "m2"}


class TestScenario7Visibility:
    """You see the turns you are party to — never the whole thread."""

    OBJECTS = (
        note("m1", ROSEMARY, (EVERYONE,), body="pipeline down", when="...01"),
        note(
            "m2",
            ROSEMARY,
            (TREVOR,),
            parent="m1",
            body="my bad migration",
            when="...02",
        ),
        note(
            "m3", TREVOR, (ROSEMARY,), parent="m2", body="keep it quiet", when="...03"
        ),
    )

    def test_bystander_sees_only_the_broadcast(self) -> None:
        """The exact shape of a disclosure bug that reached production (0020)."""
        seen = visible_turns(self.OBJECTS, "m1", YITZHAK, ACTORS, NO_GROUPS)
        assert [o.content for o in seen] == ["pipeline down"]

    def test_participants_see_the_whole_conversation(self) -> None:
        for who in (ROSEMARY, TREVOR):
            seen = visible_turns(self.OBJECTS, "m1", who, ACTORS, NO_GROUPS)
            assert len(seen) == 3, f"{who} should see every turn"

    def test_a_stranger_sees_nothing(self) -> None:
        assert visible_turns(self.OBJECTS, "m1", "outsider", ACTORS, NO_GROUPS) == ()

    def test_absent_and_forbidden_are_indistinguishable(self) -> None:
        """Both empty, so nobody can probe which threads exist."""
        forbidden = visible_turns(self.OBJECTS, "m1", "outsider", ACTORS, NO_GROUPS)
        absent = visible_turns(
            self.OBJECTS, "no-such-thread", ROSEMARY, ACTORS, NO_GROUPS
        )
        assert forbidden == absent == ()

    def test_being_party_to_one_turn_grants_nothing_about_the_others(self) -> None:
        assert is_party_to(self.OBJECTS[0], YITZHAK, ACTORS, NO_GROUPS)
        assert not is_party_to(self.OBJECTS[1], YITZHAK, ACTORS, NO_GROUPS)


class TestScenario8Intrusion:
    """You cannot attach a turn to a conversation you cannot see."""

    OBJECTS = (
        note("m1", ROSEMARY, (TREVOR,), body="private"),
        note("m2", TREVOR, (ROSEMARY,), parent="m1"),
    )

    def test_a_participant_may_reply(self) -> None:
        assert may_attach_to(self.OBJECTS, TREVOR, "m1", ACTORS, NO_GROUPS)

    def test_an_outsider_may_not(self) -> None:
        assert not may_attach_to(self.OBJECTS, YITZHAK, "m1", ACTORS, NO_GROUPS)

    def test_a_new_thread_is_always_allowed(self) -> None:
        assert may_attach_to(self.OBJECTS, YITZHAK, None, ACTORS, NO_GROUPS)

    def test_an_unknown_parent_is_refused_like_a_forbidden_one(self) -> None:
        """Both clear the parent, so the answer is not an existence oracle.

        Allowing an unknown parent let a caller distinguish "real but not yours" from
        "no such thing" by reading its own successful response — the probe the
        visibility rules refuse to answer everywhere else. Found by outside review.
        """
        assert not may_attach_to(
            self.OBJECTS, YITZHAK, "never-existed", ACTORS, NO_GROUPS
        )
        assert not may_attach_to(self.OBJECTS, YITZHAK, "m1", ACTORS, NO_GROUPS)


class TestScenario9Expiry:
    """Mail expires by conversation, not by message."""

    def test_a_live_thread_survives_however_old_its_root(self) -> None:
        """Mission 0016: per-message expiry decapitated live conversations."""
        objects = (
            note("m1", ROSEMARY, (TREVOR,), when="2026-07-01T00:00:00Z"),
            note("m2", TREVOR, (ROSEMARY,), parent="m1", when="2026-07-24T00:00:00Z"),
        )
        assert expired_object_ids(objects, "2026-07-10T00:00:00Z") == frozenset()

    def test_an_idle_thread_is_removed_whole(self) -> None:
        objects = (
            note("m1", ROSEMARY, (TREVOR,), when="2026-06-01T00:00:00Z"),
            note("m2", TREVOR, (ROSEMARY,), parent="m1", when="2026-06-02T00:00:00Z"),
        )
        assert expired_object_ids(objects, "2026-07-10T00:00:00Z") == {"m1", "m2"}

    def test_threads_expire_independently(self) -> None:
        objects = (
            note("old1", ROSEMARY, (TREVOR,), when="2026-06-01T00:00:00Z"),
            note("new1", ROSEMARY, (TREVOR,), when="2026-07-24T00:00:00Z"),
        )
        assert expired_object_ids(objects, "2026-07-10T00:00:00Z") == {"old1"}

    def test_nothing_stored_expires_nothing(self) -> None:
        assert expired_object_ids((), "2026-07-10T00:00:00Z") == frozenset()


class TestPurity:
    """The rules must stay free of hidden inputs — that is what makes them checkable."""

    def test_rules_take_no_clock(self) -> None:
        """Expiry is given a cutoff, so it can be tested at any date."""
        import inspect

        assert "cutoff" in inspect.signature(expired_object_ids).parameters

    def test_repeated_calls_agree(self) -> None:
        objects = TestScenario7Visibility.OBJECTS
        first = visible_turns(objects, "m1", YITZHAK, ACTORS, NO_GROUPS)
        second = visible_turns(objects, "m1", YITZHAK, ACTORS, NO_GROUPS)
        assert first == second


class TestRetroactiveMembership:
    """Joining a group must not grant access to what it was sent before you arrived.

    Found by an outside review of M1. Group membership was resolved when a thread was
    *read* rather than when a message was *sent*, and since an agent declares its own
    groups, anyone could add themselves to a group and retroactively read its history —
    then attach turns to private threads rooted in it. The 0020 disclosure, through a
    different door.

    The fix restores ActivityStreams: `to` holds the resolved recipients, decided at
    send time. Storing the unresolved audience there was our deviation, and the
    deviation was the bug.
    """

    def test_resolution_is_a_snapshot_not_a_query(self) -> None:
        """The rule itself is unchanged — what matters is *when* it is applied."""
        present = ("rosemary_nasrin", "trevor_mahmood")
        at_send = resolve_audience(("ops",), present, {"ops": frozenset(present)})
        assert at_send == {"rosemary_nasrin", "trevor_mahmood"}

        # a later arrival changes the membership map, but not a message already sent
        later = (*present, "yitzhak_levin")
        at_read = resolve_audience(("ops",), later, {"ops": frozenset(later)})
        assert "yitzhak_levin" in at_read
        assert "yitzhak_levin" not in at_send

    def test_a_stored_message_names_actors_not_groups(self) -> None:
        """Once `to` holds actors, membership cannot change who a message reached."""
        already_resolved = note("m1", ROSEMARY, (TREVOR,))
        for memberships in ({}, {"ops": frozenset({YITZHAK})}):
            assert recipients_of(already_resolved, ACTORS, memberships) == {TREVOR}
