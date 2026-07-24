"""SQLite behind the storage port.

An **adapter**, and nothing else. Every messaging decision — who receives a copy, which
turns of a thread you may see, which conversations have gone quiet — is made above this
file by pure functions. What happens here is rows in and rows out.

That division is why this module is dull, and dullness is the goal: if this file
ever needs to know what a broadcast is, the port is wrong.

Shape follows ADR 0006 — typed columns for everything routed on, plus a ``document``
column holding the object as received. ActivityStreams requires preserving
properties you do not understand, and a peer may send extensions we have never seen.

Two statements carry the correctness of the whole system, and both are ``INSERT OR
IGNORE`` against a primary key:

* claiming a name — otherwise two agents race and silently share an inbox;
* marking a read — otherwise one message is consumed twice.

SQLite decides both, atomically, rather than a read-then-write in Python that a second
connection could interleave with.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import aiosqlite

from agent_mailbox.exceptions import StoreNotOpen
from agent_mailbox.records import ActorRecord, ObjectRecord, ReadRecord
from agent_mailbox.store import MessageStore
from agent_mailbox.vocabulary import ActorType, ObjectType

#: Bumped when the schema changes shape. There is nothing to migrate *from* yet: this
#: package is a fresh start, and the superseded implementation's data is not carried
#: over (its messages expire in a fortnight anyway).
SCHEMA_VERSION = 1

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS actors (
        name       TEXT PRIMARY KEY,
        actor_type TEXT NOT NULL,
        profile    TEXT NOT NULL DEFAULT '{}',
        created    TEXT NOT NULL DEFAULT '',
        last_seen  TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS objects (
        id            TEXT PRIMARY KEY,
        object_type   TEXT NOT NULL,
        attributed_to TEXT NOT NULL,
        to_names      TEXT NOT NULL DEFAULT '[]',
        cc_names      TEXT NOT NULL DEFAULT '[]',
        in_reply_to   TEXT,
        summary       TEXT,
        content       TEXT NOT NULL DEFAULT '',
        published     TEXT NOT NULL DEFAULT '',
        document      TEXT NOT NULL DEFAULT '{}'
    )
    """,
    # Read-state is per (object, reader): the composite key is what makes a second
    # consumption by the same reader a no-op rather than a duplicate row.
    """
    CREATE TABLE IF NOT EXISTS reads (
        object_id TEXT NOT NULL,
        reader    TEXT NOT NULL,
        at        TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (object_id, reader)
    )
    """,
    "CREATE INDEX IF NOT EXISTS objects_published ON objects (published, id)",
    "CREATE INDEX IF NOT EXISTS objects_in_reply_to ON objects (in_reply_to)",
    "CREATE INDEX IF NOT EXISTS reads_object ON reads (object_id)",
)


def _loads(raw: str | None, fallback: Any) -> Any:
    """Tolerate a malformed JSON column rather than making the mailbox unopenable.

    A corrupt row should cost one message, not the whole store.
    """
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _to_actor(row: aiosqlite.Row) -> ActorRecord:
    return ActorRecord(
        name=row["name"],
        actor_type=ActorType(row["actor_type"]),
        profile=_loads(row["profile"], {}),
        created=row["created"],
        last_seen=row["last_seen"],
    )


def _to_object(row: aiosqlite.Row) -> ObjectRecord:
    return ObjectRecord(
        id=row["id"],
        object_type=ObjectType(row["object_type"]),
        attributed_to=row["attributed_to"],
        to=tuple(_loads(row["to_names"], [])),
        cc=tuple(_loads(row["cc_names"], [])),
        in_reply_to=row["in_reply_to"],
        summary=row["summary"],
        content=row["content"],
        published=row["published"],
        document=_loads(row["document"], {}),
    )


class SqliteStore:
    """The storage port, backed by one SQLite file.

    Used as an async context manager, which is where the connection and schema live::

        async with SqliteStore("mail.db") as store:
            await store.claim_name(actor)

    ``:memory:`` is accepted and gives a store that vanishes with the connection.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> Self:
        conn = await aiosqlite.connect(self._path)
        conn.row_factory = aiosqlite.Row
        # WAL lets readers run while a write is in flight. Only one process opens this
        # file (ADR 0005), so this is about the server's own concurrency, not sharing.
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        for statement in _SCHEMA:
            await conn.execute(statement)
        await conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        await conn.commit()
        self._conn = conn
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise StoreNotOpen(
                "SqliteStore must be used as an async context manager: "
                "`async with SqliteStore(path) as store:`"
            )
        return self._conn

    async def schema_version(self) -> int:
        cursor = await self._db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    # -- actors ------------------------------------------------------------

    async def claim_name(self, actor: ActorRecord) -> bool:
        """Insert only if the name is free — SQLite decides, not us.

        ``INSERT OR IGNORE`` against the primary key means the loser of a race changes
        nothing, so a second claimant can never overwrite the incumbent's profile.
        """
        cursor = await self._db.execute(
            "INSERT OR IGNORE INTO actors "
            "(name, actor_type, profile, created, last_seen) VALUES (?, ?, ?, ?, ?)",
            (
                actor.name,
                actor.actor_type.value,
                json.dumps(dict(actor.profile)),
                actor.created,
                actor.last_seen,
            ),
        )
        await self._db.commit()
        return cursor.rowcount == 1

    async def get_actor(self, name: str) -> ActorRecord | None:
        cursor = await self._db.execute("SELECT * FROM actors WHERE name = ?", (name,))
        row = await cursor.fetchone()
        return _to_actor(row) if row else None

    async def put_actor(self, actor: ActorRecord) -> None:
        await self._db.execute(
            "INSERT INTO actors (name, actor_type, profile, created, last_seen) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "actor_type=excluded.actor_type, profile=excluded.profile, "
            "last_seen=excluded.last_seen",
            (
                actor.name,
                actor.actor_type.value,
                json.dumps(dict(actor.profile)),
                actor.created,
                actor.last_seen,
            ),
        )
        await self._db.commit()

    async def actors(self) -> Iterable[ActorRecord]:
        cursor = await self._db.execute("SELECT * FROM actors ORDER BY name")
        return tuple(_to_actor(row) for row in await cursor.fetchall())

    # -- objects -----------------------------------------------------------

    async def add_object(self, obj: ObjectRecord) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO objects (id, object_type, attributed_to, to_names, "
            "cc_names, in_reply_to, summary, content, published, document) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                obj.id,
                obj.object_type.value,
                obj.attributed_to,
                json.dumps(list(obj.to)),
                json.dumps(list(obj.cc)),
                obj.in_reply_to,
                obj.summary,
                obj.content,
                obj.published,
                json.dumps(dict(obj.document)),
            ),
        )
        await self._db.commit()

    async def get_object(self, object_id: str) -> ObjectRecord | None:
        cursor = await self._db.execute(
            "SELECT * FROM objects WHERE id = ?", (object_id,)
        )
        row = await cursor.fetchone()
        return _to_object(row) if row else None

    async def objects(self) -> Iterable[ObjectRecord]:
        cursor = await self._db.execute(
            "SELECT * FROM objects ORDER BY published ASC, id ASC"
        )
        return tuple(_to_object(row) for row in await cursor.fetchall())

    async def remove_objects(self, object_ids: Iterable[str]) -> int:
        ids = tuple(object_ids)
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        # Read-state goes with the objects. Leaving it behind would accumulate rows
        # referring to messages that no longer exist, for ever.
        await self._db.execute(f"DELETE FROM reads WHERE object_id IN ({marks})", ids)
        cursor = await self._db.execute(
            f"DELETE FROM objects WHERE id IN ({marks})", ids
        )
        await self._db.commit()
        return cursor.rowcount

    # -- read state --------------------------------------------------------

    async def mark_read(self, read: ReadRecord) -> bool:
        """Record a consumption, once. ``False`` if this reader already consumed it."""
        cursor = await self._db.execute(
            "INSERT OR IGNORE INTO reads (object_id, reader, at) VALUES (?, ?, ?)",
            (read.object_id, read.reader, read.at),
        )
        await self._db.commit()
        return cursor.rowcount == 1

    async def reads_of(
        self, object_ids: Iterable[str]
    ) -> Mapping[str, tuple[ReadRecord, ...]]:
        ids = tuple(object_ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        cursor = await self._db.execute(
            f"SELECT * FROM reads WHERE object_id IN ({marks})", ids
        )
        found: dict[str, list[ReadRecord]] = {object_id: [] for object_id in ids}
        for row in await cursor.fetchall():
            found[row["object_id"]].append(
                ReadRecord(row["object_id"], row["reader"], row["at"])
            )
        return {object_id: tuple(reads) for object_id, reads in found.items()}


# A static conformance check. `runtime_checkable` only verifies that method *names*
# exist, so this assignment is what actually holds the adapter to the port's
# signatures — pyright rejects the module if they drift.
_conforms: MessageStore = SqliteStore(":memory:")
