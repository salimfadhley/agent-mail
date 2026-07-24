"""The storage contract, and the architecture that depends on it.

Two things are checked here, and both are structural rather than behavioural.

**The conformance suite** is parametrised over store implementations. Adding a backend
means adding it to ``STORES`` and making these pass — nothing else. That is the test of
whether storage is genuinely replaceable, and it is the reason the port has no messaging
verbs on it.

**The purity check** asserts the rules never learn about storage. An
architecture that is only written down erodes; this one fails a test instead.
"""

from __future__ import annotations

import ast
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from agent_mailbox import rules
from agent_mailbox.records import ActorRecord, ObjectRecord, ReadRecord
from agent_mailbox.sqlite_store import SqliteStore
from agent_mailbox.store import InMemoryStore, MessageStore


@asynccontextmanager
async def in_memory(tmp_path: Path) -> AsyncIterator[MessageStore]:
    yield InMemoryStore()


@asynccontextmanager
async def on_sqlite(tmp_path: Path) -> AsyncIterator[MessageStore]:
    async with SqliteStore(tmp_path / "mail.db") as store:
        yield store


#: Every backend passes the same suite. Adding one means adding it here — nothing else.
#: If a backend ever needed its own tests, the port would have stopped being an
#: abstraction and started being two.
STORES: tuple[Callable[..., object], ...] = (in_memory, on_sqlite)


@pytest.fixture(params=STORES, ids=lambda f: f.__name__)
async def store(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[MessageStore]:
    async with request.param(tmp_path) as opened:
        yield opened


def an_actor(name: str, **profile: object) -> ActorRecord:
    return ActorRecord(name=name, profile=profile, created="...", last_seen="...")


def an_object(ident: str, sender: str = "a", to: tuple[str, ...] = ()) -> ObjectRecord:
    return ObjectRecord(
        id=ident, attributed_to=sender, to=to, content="hi", published="2026-07-24"
    )


class TestNameClaiming:
    """Uniqueness is enforced here or nowhere."""

    async def test_a_free_name_is_claimed(self, store: MessageStore) -> None:
        assert await store.claim_name(an_actor("rosemary_nasrin")) is True

    async def test_a_taken_name_is_refused(self, store: MessageStore) -> None:
        await store.claim_name(an_actor("trevor_mahmood"))
        assert await store.claim_name(an_actor("trevor_mahmood")) is False

    async def test_refusal_does_not_overwrite_the_incumbent(
        self, store: MessageStore
    ) -> None:
        """The loser of a race must not clobber the winner's profile."""
        await store.claim_name(an_actor("trevor_mahmood", project="billing"))
        await store.claim_name(an_actor("trevor_mahmood", project="impostor"))
        held = await store.get_actor("trevor_mahmood")
        assert held is not None and held.profile["project"] == "billing"

    async def test_unknown_actor_is_none(self, store: MessageStore) -> None:
        assert await store.get_actor("nobody") is None

    async def test_put_actor_updates_an_existing_entry(
        self, store: MessageStore
    ) -> None:
        await store.claim_name(an_actor("rosemary_nasrin", project="old"))
        await store.put_actor(an_actor("rosemary_nasrin", project="new"))
        held = await store.get_actor("rosemary_nasrin")
        assert held is not None and held.profile["project"] == "new"
        assert len(tuple(await store.actors())) == 1


class TestObjects:
    async def test_round_trips(self, store: MessageStore) -> None:
        await store.add_object(an_object("m1"))
        got = await store.get_object("m1")
        assert got is not None and got.content == "hi"

    async def test_objects_come_back_oldest_first(self, store: MessageStore) -> None:
        for ident, when in (("m2", "...02"), ("m1", "...01"), ("m3", "...03")):
            await store.add_object(
                ObjectRecord(id=ident, attributed_to="a", published=when)
            )
        assert [o.id for o in await store.objects()] == ["m1", "m2", "m3"]

    async def test_unknown_object_is_none(self, store: MessageStore) -> None:
        assert await store.get_object("nope") is None

    async def test_removal_reports_what_actually_went(
        self, store: MessageStore
    ) -> None:
        await store.add_object(an_object("m1"))
        assert await store.remove_objects(["m1", "never-existed"]) == 1
        assert await store.get_object("m1") is None

    async def test_unknown_document_properties_survive(
        self, store: MessageStore
    ) -> None:
        """ActivityStreams requires preserving what you do not understand (ADR 0006).

        A peer may send extensions we have never seen; dropping them corrupts the
        document on the way back out.
        """
        obj = ObjectRecord(
            id="m1",
            attributed_to="a",
            document={
                "@context": "…",
                "x:mood": "cheerful",
                "nested": {"deep": [1, 2]},
            },
        )
        await store.add_object(obj)
        got = await store.get_object("m1")
        assert got is not None
        assert got.document["x:mood"] == "cheerful"
        assert got.document["nested"] == {"deep": [1, 2]}


class TestReadState:
    async def test_first_read_is_recorded(self, store: MessageStore) -> None:
        assert await store.mark_read(ReadRecord("m1", "trevor_mahmood", "...")) is True

    async def test_second_read_by_the_same_reader_is_refused(
        self, store: MessageStore
    ) -> None:
        """Atomic, so a message cannot be consumed twice."""
        await store.mark_read(ReadRecord("m1", "trevor_mahmood", "..."))
        assert await store.mark_read(ReadRecord("m1", "trevor_mahmood", "…")) is False

    async def test_readers_are_independent(self, store: MessageStore) -> None:
        await store.mark_read(ReadRecord("m1", "trevor_mahmood", "..."))
        assert await store.mark_read(ReadRecord("m1", "yitzhak_levin", "...")) is True
        reads = await store.reads_of(["m1"])
        assert {r.reader for r in reads["m1"]} == {"trevor_mahmood", "yitzhak_levin"}

    async def test_removing_an_object_removes_its_read_state(
        self, store: MessageStore
    ) -> None:
        """Otherwise expiry leaves orphaned rows behind for ever."""
        await store.add_object(an_object("m1"))
        await store.mark_read(ReadRecord("m1", "trevor_mahmood", "..."))
        await store.remove_objects(["m1"])
        assert await store.reads_of(["m1"]) == {"m1": ()}


class TestArchitecture:
    """The abstraction is only worth having if something keeps it honest."""

    def test_the_rules_never_import_storage(self) -> None:
        """Pure functions in, pure functions out — no store, no clock, no network.

        If a rule ever needs the store, the port is too narrow and should grow a
        primitive; it must not be fixed by importing storage into the rules.
        """
        source = Path(rules.__file__).read_text()
        imported = {
            node.module.split(".")[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name.split(".")[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        forbidden = {
            "sqlite3",
            "aiosqlite",
            "asyncio",
            "datetime",
            "time",
            "random",
            "os",
            "httpx",
            "fastapi",
        }
        assert not imported & forbidden, f"rules must stay pure: {imported & forbidden}"
        assert "store" not in imported, "rules must not know how anything is stored"

    def test_no_rule_is_a_coroutine(self) -> None:
        """Purity implies synchronous: a rule that awaits is doing I/O somewhere."""
        import inspect

        offenders = [
            name
            for name, obj in vars(rules).items()
            if inspect.iscoroutinefunction(obj)
        ]
        assert not offenders, f"rules must be synchronous: {offenders}"

    def test_the_port_exposes_no_messaging_verbs(self) -> None:
        """Storage verbs only. A domain verb here means logic has leaked downwards."""
        domain = {"send", "reply", "inbox", "thread", "peek", "broadcast", "deliver"}
        methods = {m for m in dir(MessageStore) if not m.startswith("_")}
        assert not {m for m in methods if any(d in m for d in domain)}
