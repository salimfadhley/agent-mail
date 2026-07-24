"""SQLite-specific behaviour: the things the port cannot express.

The conformance suite in ``test_store_contract.py`` already proves this backend obeys
the contract. What is left is what only a real file can be asked: does it persist, and
are the two atomic operations genuinely atomic when connections actually race?

That last question is the important one. ``claim_name`` and ``mark_read`` are the only
reason the port is a protocol rather than a bag of functions, and their atomicity is
asserted in a docstring everywhere else. Here it is demonstrated.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_mailbox.exceptions import MailboxError, StoreNotOpen
from agent_mailbox.records import ActorRecord, ObjectRecord, ReadRecord
from agent_mailbox.sqlite_store import SCHEMA_VERSION, SqliteStore


class TestPersistence:
    async def test_data_survives_reopening(self, tmp_path: Path) -> None:
        db = tmp_path / "mail.db"
        async with SqliteStore(db) as store:
            await store.claim_name(ActorRecord(name="rosemary_nasrin"))
            await store.add_object(
                ObjectRecord(
                    id="m1",
                    attributed_to="rosemary_nasrin",
                    to=("trevor_mahmood",),
                    content="still here?",
                    published="2026-07-24T12:00:00Z",
                )
            )
            await store.mark_read(ReadRecord("m1", "trevor_mahmood", "..."))

        async with SqliteStore(db) as store:
            assert await store.get_actor("rosemary_nasrin") is not None
            obj = await store.get_object("m1")
            assert obj is not None and obj.content == "still here?"
            assert obj.to == ("trevor_mahmood",)
            assert len((await store.reads_of(["m1"]))["m1"]) == 1

    async def test_a_fresh_file_records_its_schema_version(
        self, tmp_path: Path
    ) -> None:
        async with SqliteStore(tmp_path / "mail.db") as store:
            assert await store.schema_version() == SCHEMA_VERSION

    async def test_in_memory_is_supported(self) -> None:
        async with SqliteStore(":memory:") as store:
            assert await store.claim_name(ActorRecord(name="ephemeral")) is True

    async def test_using_it_unopened_says_so(self) -> None:
        """A named error, not a bare RuntimeError (coding standards §2).

        A caller that catches this can open the store and retry; one that catches
        RuntimeError catches every other interpreter failure with it.
        """
        store = SqliteStore(":memory:")
        with pytest.raises(StoreNotOpen, match="async with") as exc:
            await store.get_actor("anyone")
        assert exc.value.code == "store_not_open"
        assert isinstance(exc.value, MailboxError)


class TestAtomicity:
    """The two operations that carry the system's correctness, raced for real."""

    async def test_only_one_of_many_claimants_gets_the_name(
        self, tmp_path: Path
    ) -> None:
        """Twelve connections, one name. Exactly one must win.

        This is the failure the previous system actually had: nothing enforced
        uniqueness, so two agents could hold one address and silently share an inbox.
        A check-then-insert in Python would pass a single-threaded test and lose here.
        """
        db = tmp_path / "mail.db"
        async with SqliteStore(db):
            pass  # create the schema first

        async def claim(n: int) -> bool:
            async with SqliteStore(db) as store:
                return await store.claim_name(
                    ActorRecord(name="trevor_mahmood", profile={"claimant": n})
                )

        results = await asyncio.gather(*(claim(n) for n in range(12)))
        assert sum(results) == 1, f"{sum(results)} claimants won; exactly one may"

        async with SqliteStore(db) as store:
            held = await store.get_actor("trevor_mahmood")
            assert held is not None
            winner = results.index(True)
            assert held.profile["claimant"] == winner, "the winner's profile must stand"

    async def test_a_message_is_consumed_once_however_hard_it_is_raced(
        self, tmp_path: Path
    ) -> None:
        """Ten concurrent reads of one message by one reader: one wins."""
        db = tmp_path / "mail.db"
        async with SqliteStore(db):
            pass

        async def consume() -> bool:
            async with SqliteStore(db) as store:
                return await store.mark_read(ReadRecord("m1", "trevor_mahmood", "..."))

        results = await asyncio.gather(*(consume() for _ in range(10)))
        assert sum(results) == 1

    async def test_different_readers_each_consume_their_own_copy(
        self, tmp_path: Path
    ) -> None:
        """Fan-out: everyone addressed gets a copy, so everyone's read succeeds."""
        db = tmp_path / "mail.db"
        async with SqliteStore(db):
            pass

        async def consume(reader: str) -> bool:
            async with SqliteStore(db) as store:
                return await store.mark_read(ReadRecord("m1", reader, "..."))

        readers = ("rosemary_nasrin", "trevor_mahmood", "yitzhak_levin")
        assert all(await asyncio.gather(*(consume(r) for r in readers)))


class TestRobustness:
    async def test_a_corrupt_json_column_costs_one_field_not_the_mailbox(
        self, tmp_path: Path
    ) -> None:
        """A damaged row should not make the whole store unopenable."""
        db = tmp_path / "mail.db"
        async with SqliteStore(db) as store:
            await store.add_object(
                ObjectRecord(id="m1", attributed_to="a", published="...")
            )
            await store._db.execute(  # type: ignore[attr-defined]
                "UPDATE objects SET document = ?, to_names = ? WHERE id = ?",
                ("{not json", "[[[", "m1"),
            )
            await store._db.commit()  # type: ignore[attr-defined]

        async with SqliteStore(db) as store:
            obj = await store.get_object("m1")
            assert obj is not None
            assert obj.document == {} and obj.to == ()
            assert obj.attributed_to == "a", "undamaged fields must still be readable"

    async def test_documents_round_trip_through_json(self, tmp_path: Path) -> None:
        """Unknown ActivityStreams properties must survive storage (ADR 0006)."""
        document = {
            "@context": "https://www.w3.org/ns/activitystreams",
            "x:mood": "cheerful",
            "tags": ["a", "b"],
            "nested": {"deep": {"deeper": 1}},
        }
        async with SqliteStore(tmp_path / "mail.db") as store:
            await store.add_object(
                ObjectRecord(id="m1", attributed_to="a", document=document)
            )
            got = await store.get_object("m1")
            assert got is not None
            assert json.loads(json.dumps(dict(got.document))) == document

    async def test_removing_nothing_is_not_an_error(self, tmp_path: Path) -> None:
        async with SqliteStore(tmp_path / "mail.db") as store:
            assert await store.remove_objects([]) == 0
            assert await store.reads_of([]) == {}
