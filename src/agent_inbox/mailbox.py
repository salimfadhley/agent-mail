"""The SQLite-backed mailbox — the single core all surfaces (CLI, MCP) share.

Storage is one local SQLite file. No external services: ``pip install agent-inbox``,
run, done. For a hosted hub, point the file at a mounted volume.

Addressing is two-part, ``<project>/<agent>``:

* **direct** ``project/agent`` -> that specific agent's inbox;
* **any** ``project`` (bare) -> exactly one agent on the project (claimed on read, so
  the first reader wins — a shared work queue);
* **broadcast** ``project/*`` -> a copy for every agent on the project (each agent
  consumes its own copy, tracked per-reader).

A message is *unread* for an agent until that agent ``read``\\s it. Old messages are
purged automatically after ``ttl_days`` (see :meth:`Mailbox._purge_expired`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType

import aiosqlite

from agent_inbox.config import Config, format_address, parse_address, parse_target
from agent_inbox.exceptions import MailboxError
from agent_inbox.models import AgentInfo, AgentProfile, Intent, Message

logger = logging.getLogger(__name__)

# Bumped whenever the on-disk shape changes; stamped into SQLite's `user_version`
# so an opening server knows exactly which upgrades it still owes. v1 = the
# original two-part store; v2 = three-part addressing (a `role` position).
SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    from_addr   TEXT NOT NULL,
    to_addr     TEXT NOT NULL,
    -- Delivery: 'claim' = exactly one matching agent gets it (first read wins);
    -- 'fanout' = every matching agent consumes its own copy.
    kind        TEXT NOT NULL,
    -- Each routing column is the literal it must match, or NULL for "any value"
    -- (i.e. the address had `all`/`any`/nothing in that position).
    to_project  TEXT,
    to_agent    TEXT,
    to_role     TEXT,
    thread      TEXT,
    intent      TEXT NOT NULL,
    subject     TEXT,                   -- optional; NULL when the sender omitted one
    body        TEXT NOT NULL,
    created     TEXT NOT NULL,          -- ISO-8601 UTC
    acked_at    TEXT                    -- direct/any: set when consumed; NULL = unread
);
CREATE INDEX IF NOT EXISTS idx_messages_route
    ON messages (to_project, to_agent, kind, acked_at);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages (created);

CREATE TABLE IF NOT EXISTS broadcast_reads (
    message_id  TEXT NOT NULL,
    reader      TEXT NOT NULL,          -- 'project/agent' that consumed the broadcast
    acked_at    TEXT NOT NULL,
    PRIMARY KEY (message_id, reader)
);

CREATE TABLE IF NOT EXISTS agents (
    project     TEXT NOT NULL,
    agent       TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT '',     -- '' = this agent holds no role
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    profile     TEXT NOT NULL DEFAULT '{}',   -- JSON AgentProfile
    PRIMARY KEY (project, agent, role)
);
CREATE INDEX IF NOT EXISTS idx_agents_last_seen ON agents (last_seen);

-- Hub-level facts. `initialized_at` is stamped once, when this storage is first
-- created, and never rewritten: it lets a rejoining agent tell a *reset* directory
-- from a genuinely new one (they are otherwise indistinguishable, and the failure
-- is silent — agents re-derive addresses against an empty room).
CREATE TABLE IF NOT EXISTS hub_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class MailboxStats:
    """A read-only snapshot of hub traffic for the console dashboard."""

    total_messages: int
    unread_messages: int
    agents_total: int
    agents_online: int
    per_day: list[tuple[str, int]] = field(default_factory=list)
    recent: list[Message] = field(default_factory=list)


@dataclass(frozen=True)
class ThreadTurn:
    """One message in a thread, with the read-state its *sender* is entitled to see."""

    message: Message
    read_at: str | None  # when the recipient consumed it; None = still unread
    mine: bool  # did the calling agent send this turn?


@dataclass(frozen=True)
class ThreadSummary:
    """A thread the calling agent is party to."""

    thread: str
    subject: str | None
    counterparts: list[str]  # the other addresses on the thread
    turns: int
    last_at: str
    last_from: str
    awaiting_them: bool  # my latest turn is still unread by the other side


@dataclass(frozen=True)
class FlowEdge:
    """A directed edge in the message-flow graph: ``frm`` sent ``count`` to ``to``."""

    frm: str
    to: str
    count: int
    last: str  # ISO-8601 of the most recent message on this edge


@dataclass(frozen=True)
class FlowGraph:
    """The message-flow graph over a time window (directed, direct messages only)."""

    edges: list[FlowEdge]
    nodes: list[str]  # agent addresses that appear as an endpoint
    online: list[str]  # of those, the ones currently online
    broadcast_count: int  # non-direct messages in the window (not drawn; a footnote)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _row_to_message(row: aiosqlite.Row) -> Message:
    return Message(
        id=row["id"],
        from_=row["from_addr"],
        to=row["to_addr"],
        thread=row["thread"],
        intent=Intent(row["intent"]),
        subject=row["subject"],
        body=row["body"],
        created=row["created"],
    )


class Mailbox:
    """Durable inbox over a local SQLite file.

    Use as an async context manager::

        async with Mailbox(config) as mb:
            await mb.send(msg)

    Connecting opens the file, ensures the schema, and purges expired messages.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._db: aiosqlite.Connection | None = None

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Open the SQLite file, ensure the schema, and purge expired messages."""
        if self._db is not None:
            return
        target = self._config.db
        if target != ":memory:":
            path = Path(target).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            target = str(path)
        logger.debug("opening mailbox db at %s", target)
        self._db = await aiosqlite.connect(target)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._migrate()
        await self._db.executescript(_SCHEMA)
        # Stamp when this storage was created. INSERT OR IGNORE means an existing
        # store keeps its original timestamp — only a fresh file gets a new one.
        await self._db.execute(
            "INSERT OR IGNORE INTO hub_meta (key, value) VALUES ('initialized_at', ?)",
            (_now_iso(),),
        )
        await self._db.commit()
        await self._purge_expired()

    async def _columns(self, table: str) -> set[str]:
        cursor = await self._conn.execute(f"PRAGMA table_info({table})")
        return {row["name"] for row in await cursor.fetchall()}

    async def _table_exists(self, table: str) -> bool:
        cursor = await self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        return await cursor.fetchone() is not None

    async def _schema_version(self) -> int:
        """The store's schema version, from SQLite's built-in ``user_version``."""
        cursor = await self._conn.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def _migrate(self) -> None:
        """Bring the store up to :data:`SCHEMA_VERSION`, in place and non-destructively.

        The version is stamped in SQLite's own ``user_version`` header, so the server
        knows on open exactly which upgrades to run — no sniffing of columns, and no
        re-running work that is already done.

        Nothing here drops a message. Losing the store once already cost us: agents
        re-derived their addresses against an emptied directory and one project ended
        up split across two names that could no longer reach each other.
        """
        fresh = not await self._table_exists("messages")
        if fresh:
            # Brand-new file: _SCHEMA is about to create the current shape.
            await self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            await self._conn.commit()
            return

        version = await self._schema_version()
        if version == 0:
            version = 1  # a store predating versioning is, by definition, v1
        if version > SCHEMA_VERSION:
            raise MailboxError(
                f"database schema is v{version} but this agent-inbox only understands "
                f"v{SCHEMA_VERSION} — it was written by a newer version. Upgrade "
                "agent-inbox rather than letting an old build write to it."
            )
        if version < 2:
            await self._migrate_v1_to_v2()
            version = 2

        await self._conn.execute(f"PRAGMA user_version = {version}")
        await self._conn.commit()

    async def _migrate_v1_to_v2(self) -> None:
        """v1 -> v2: three-part addressing (a ``role`` position) .

        Each step re-checks the shape it is about to change, so a store that was
        half-upgraded by an older build (before versioning existed) still converges.
        """
        logger.info("migrating store v1 -> v2 (three-part addressing)")

        if "to_role" not in await self._columns("messages"):
            # The table must be REBUILT, not merely widened. The v1 table declared
            # `to_project NOT NULL` (and `subject NOT NULL`), and ALTER TABLE cannot
            # relax a constraint — so a merely-widened table would reject every
            # wildcard address and every subject-less message from then on. Copy the
            # rows into the current shape instead, translating as we go.
            await self._conn.execute(
                "CREATE TABLE IF NOT EXISTS messages_v2 ("
                " id TEXT PRIMARY KEY, from_addr TEXT NOT NULL, to_addr TEXT NOT NULL,"
                " kind TEXT NOT NULL, to_project TEXT, to_agent TEXT, to_role TEXT,"
                " thread TEXT, intent TEXT NOT NULL, subject TEXT, body TEXT NOT NULL,"
                " created TEXT NOT NULL, acked_at TEXT)"
            )
            await self._conn.execute(
                "INSERT OR IGNORE INTO messages_v2 "
                "(id, from_addr, to_addr, kind, to_project, to_agent, to_role,"
                " thread, intent, subject, body, created, acked_at) "
                "SELECT id, from_addr, to_addr,"
                # the five old kinds collapse onto two delivery modes
                "  CASE WHEN kind IN ('broadcast','public') THEN 'fanout'"
                "       ELSE 'claim' END,"
                # '' meant "no project scope"; NULL is now the wildcard
                "  NULLIF(to_project, ''), to_agent, NULL,"
                "  thread, intent, subject, body, created, acked_at "
                "FROM messages"
            )
            await self._conn.execute("DROP TABLE messages")
            await self._conn.execute("ALTER TABLE messages_v2 RENAME TO messages")
            await self._conn.commit()

        if await self._table_exists("agents") and "role" not in await self._columns(
            "agents"
        ):
            # SQLite cannot alter a primary key, so rebuild and copy every row.
            # Discrete statements (not executescript, which forces its own COMMIT
            # mid-transaction and can deadlock against the open connection).
            await self._conn.execute(
                "CREATE TABLE IF NOT EXISTS agents_v2 ("
                " project TEXT NOT NULL, agent TEXT NOT NULL,"
                " role TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL,"
                " last_seen TEXT NOT NULL, profile TEXT NOT NULL DEFAULT '{}',"
                " PRIMARY KEY (project, agent, role))"
            )
            await self._conn.execute(
                "INSERT OR IGNORE INTO agents_v2 "
                "(project, agent, role, first_seen, last_seen, profile) "
                "SELECT project, agent, '', first_seen, last_seen, profile FROM agents"
            )
            await self._conn.execute("DROP TABLE agents")
            await self._conn.execute("ALTER TABLE agents_v2 RENAME TO agents")
            await self._conn.commit()

    async def close(self) -> None:
        """Close the SQLite connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> Mailbox:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # -- internals ---------------------------------------------------------

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise MailboxError("mailbox is not connected; call connect() first")
        return self._db

    async def _purge_expired(self) -> None:
        """Delete messages older than ``ttl_days`` (and orphaned broadcast reads)."""
        ttl = self._config.ttl_days
        if ttl <= 0:
            return
        cutoff = (datetime.now(tz=UTC) - timedelta(days=ttl)).isoformat()
        cur = await self._conn.execute(
            "DELETE FROM messages WHERE created < ?", (cutoff,)
        )
        deleted = cur.rowcount
        await self._conn.execute(
            "DELETE FROM broadcast_reads "
            "WHERE message_id NOT IN (SELECT id FROM messages)"
        )
        await self._conn.commit()
        if deleted:
            logger.info("purged %d message(s) older than %d day(s)", deleted, ttl)

    def _not_found(self, project: str, agent: str, message_id: str) -> MailboxError:
        return MailboxError(
            f"no unread message with id {message_id!r} in {project}/{agent}'s inbox"
        )

    # -- verbs -------------------------------------------------------------

    async def max_message_size(self) -> int:
        """The effective max bytes for one message (the configured cap)."""
        return self._config.max_message_bytes

    async def send(self, message: Message) -> Message:
        """Store ``message`` for the recipient(s) named by its ``to`` address."""
        payload = message.to_json_bytes()
        cap = self._config.max_message_bytes
        if cap and len(payload) > cap:
            raise MailboxError(
                f"message too large: {len(payload)} bytes exceeds the hub's max of "
                f"{cap} bytes (see hub_info -> limits)"
            )
        target = parse_address(message.to)
        # Each routing column is the literal to match, or NULL for "any value".
        await self._conn.execute(
            "INSERT INTO messages (id, from_addr, to_addr, kind, to_project, "
            "to_agent, to_role, thread, intent, subject, body, created, acked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                message.id,
                message.from_,
                message.to,
                target.kind,
                target.project,
                target.agent,
                target.role,
                message.thread,
                message.intent.value,
                message.subject,
                message.body,
                message.created.isoformat(),
            ),
        )
        await self._conn.commit()
        logger.debug("sent %s -> %s (%s)", message.from_, message.to, message.id)
        return message

    # A message is routed to a reader when every position either matches the reader's
    # value or is NULL ("any value"). Defined once so peek and read can never drift.
    _ROUTES_TO = (
        "(to_project IS NULL OR to_project = :proj)"
        " AND (to_agent IS NULL OR to_agent = :agent)"
        " AND (to_role IS NULL OR to_role = :role)"
    )
    # You never receive your own fan-out. A `claim` addressed at exactly one agent is
    # exempt so that a deliberate self-send still works — `ping` relies on it.
    _NOT_MY_OWN_FANOUT = "NOT (kind = 'fanout' AND from_addr = :me)"

    def _reader(self, project: str, agent: str, role: str | None) -> dict[str, str]:
        return {
            "proj": project,
            "agent": agent,
            "role": role or "",
            "me": format_address(project, agent, role),
        }

    async def peek(
        self, project: str, agent: str, role: str | None = None
    ) -> list[Message]:
        """Return unread messages routed to this agent, without consuming them."""
        params = self._reader(project, agent, role)
        cursor = await self._conn.execute(
            "SELECT * FROM messages WHERE acked_at IS NULL"
            f" AND {self._ROUTES_TO}"
            f" AND {self._NOT_MY_OWN_FANOUT}"
            " AND (kind = 'claim' OR id NOT IN ("
            "     SELECT message_id FROM broadcast_reads WHERE reader = :me))"
            " ORDER BY created ASC",
            params,
        )
        rows = await cursor.fetchall()
        return [_row_to_message(row) for row in rows]

    async def unread_count(
        self, project: str, agent: str, role: str | None = None
    ) -> tuple[int, list[str]]:
        """How many messages are waiting, and who they're from. Cheap and read-only.

        Intended for a "you have mail" poller that runs on every beat of an agent's
        loop, so it must stay a single indexed COUNT — never load the bodies.
        """
        params = self._reader(project, agent, role)
        cursor = await self._conn.execute(
            "SELECT from_addr, COUNT(*) AS n FROM messages WHERE acked_at IS NULL"
            f" AND {self._ROUTES_TO}"
            f" AND {self._NOT_MY_OWN_FANOUT}"
            " AND (kind = 'claim' OR id NOT IN ("
            "     SELECT message_id FROM broadcast_reads WHERE reader = :me))"
            " GROUP BY from_addr ORDER BY n DESC",
            params,
        )
        rows = await cursor.fetchall()
        return sum(r["n"] for r in rows), [r["from_addr"] for r in rows]

    async def read(
        self, project: str, agent: str, message_id: str, role: str | None = None
    ) -> Message:
        """Return the message with ``message_id`` and consume it for this agent.

        A **claim** message is taken atomically (first reader wins, so it is delivered
        exactly once). A **fanout** message records that *this* agent consumed its own
        copy; every other matching agent still sees theirs.

        The caller must be routed the message — the same predicate ``peek`` uses — so
        you cannot read mail that wasn't addressed to you.
        """
        params = self._reader(project, agent, role)
        params["id"] = message_id
        cursor = await self._conn.execute(
            f"SELECT * FROM messages WHERE id = :id AND {self._ROUTES_TO}",
            params,
        )
        row = await cursor.fetchone()
        if row is None:  # no such message, or not routed to this agent
            raise self._not_found(project, agent, message_id)

        reader = params["me"]
        if row["kind"] == "fanout":
            seen = await (
                await self._conn.execute(
                    "SELECT 1 FROM broadcast_reads WHERE message_id = ? AND reader = ?",
                    (message_id, reader),
                )
            ).fetchone()
            if seen is not None:
                raise self._not_found(project, agent, message_id)
            await self._conn.execute(
                "INSERT INTO broadcast_reads (message_id, reader, acked_at) "
                "VALUES (?, ?, ?)",
                (message_id, reader, _now_iso()),
            )
            await self._conn.commit()
        else:  # claim: exactly one reader may take it
            claim = await self._conn.execute(
                "UPDATE messages SET acked_at = ? WHERE id = ? AND acked_at IS NULL",
                (_now_iso(), message_id),
            )
            await self._conn.commit()
            if claim.rowcount != 1:  # already consumed (another agent won the claim)
                raise self._not_found(project, agent, message_id)

        logger.debug("read %s from %s/%s", message_id, project, agent)
        return _row_to_message(row)

    async def reply(
        self,
        project: str,
        agent: str,
        message_id: str,
        body: str,
        subject: str | None = None,
        role: str | None = None,
    ) -> Message:
        """Consume message ``message_id`` and reply directly to its sender."""
        original = await self.read(project, agent, message_id, role)
        reply = Message(
            from_=format_address(project, agent, role),
            to=original.from_,
            thread=original.thread or original.id,
            intent=Intent.reply,
            subject=subject or _reply_subject(original.subject),
            body=body,
        )
        return await self.send(reply)

    async def notify(self, to: str, thread: str | None = None) -> None:
        """Best-effort 'you have mail' wake.

        With the SQLite backend there is no cross-process push, so this is a no-op
        beyond validating the address: agents discover mail by checking their inbox
        each turn. Kept for API symmetry (and future backends that can push).
        """
        parse_target(to)  # validate the address; raise on a malformed one
        logger.debug("notify %s (no-op with the sqlite backend)", to)

    async def ping(self, project: str, agent: str, role: str | None = None) -> Message:
        """Round-trip a probe to this agent's own address and consume it."""
        me = format_address(project, agent, role)
        probe = Message(from_=me, to=me, subject="agent-inbox ping", body="ping")
        await self.send(probe)
        received = await self.read(project, agent, probe.id, role)
        logger.debug("ping round-trip ok for %s (%s)", me, probe.id)
        return received

    # -- directory / presence ---------------------------------------------

    async def touch(self, project: str, agent: str, role: str | None = None) -> None:
        """Record that this agent was just active (upsert ``last_seen``)."""
        now = _now_iso()
        await self._conn.execute(
            "INSERT INTO agents (project, agent, role, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(project, agent, role) DO UPDATE SET "
            "last_seen = excluded.last_seen",
            (project, agent, role or "", now, now),
        )
        await self._conn.commit()

    async def register(
        self,
        project: str,
        agent: str,
        profile: AgentProfile,
        role: str | None = None,
    ) -> AgentInfo:
        """Set this agent's profile (and mark it active). Returns the entry."""
        now = _now_iso()
        await self._conn.execute(
            "INSERT INTO agents (project, agent, role, first_seen, last_seen, profile) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project, agent, role) DO UPDATE SET "
            "last_seen = excluded.last_seen, profile = excluded.profile",
            (project, agent, role or "", now, now, profile.model_dump_json()),
        )
        await self._conn.commit()
        info = await self.whois(project, agent, role)
        assert info is not None  # just inserted
        return info

    async def retire(self, project: str, agent: str, role: str | None = None) -> bool:
        """Remove a directory entry. Returns whether one was actually removed.

        Used to tombstone a **superseded identity** — e.g. after re-deriving your
        address you retire the old one so the room isn't full of your ghosts.
        """
        cursor = await self._conn.execute(
            "DELETE FROM agents WHERE project = ? AND agent = ? AND role = ?",
            (project, agent, role or ""),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def supersede(self, caller: str, addresses: list[str]) -> list[str]:
        """Retire former identities on the caller's behalf; returns those removed.

        Guard rail: since there is no authentication, an entry may only be retired if
        it is **stale** (long inactive). That lets an agent clean up its own dead
        identities while making it impossible to evict a live agent from the directory.
        """
        removed: list[str] = []
        for address in addresses:
            kind, project, agent = parse_target(address)
            if kind != "direct" or project is None or agent is None:
                continue
            if address == caller:  # never retire the identity you are using
                continue
            info = await self.whois(project, agent)
            if info is None or not info.stale:
                logger.info(
                    "refusing to supersede %s (absent or still active)", address
                )
                continue
            if await self.retire(project, agent):
                removed.append(address)
        return removed

    async def update_status(
        self, project: str, agent: str, status: str, role: str | None = None
    ) -> AgentInfo:
        """Update just the ``status`` of an agent's profile (creates it if absent)."""
        current = await self.whois(project, agent, role)
        profile = current.profile if current else AgentProfile()
        return await self.register(
            project, agent, profile.model_copy(update={"status": status}), role
        )

    def _row_to_agent_info(self, row: aiosqlite.Row) -> AgentInfo:
        last_seen = datetime.fromisoformat(row["last_seen"])
        idle = datetime.now(tz=UTC) - last_seen
        online = idle <= timedelta(seconds=self._config.online_seconds)
        stale = self._config.stale_days > 0 and idle > timedelta(
            days=self._config.stale_days
        )
        role = row["role"] or None
        return AgentInfo(
            project=row["project"],
            agent=row["agent"],
            role=role,
            address=format_address(row["project"], row["agent"], role),
            first_seen=datetime.fromisoformat(row["first_seen"]),
            last_seen=last_seen,
            online=online,
            stale=stale,
            profile=AgentProfile.model_validate_json(row["profile"] or "{}"),
        )

    async def storage_initialized_at(self) -> str | None:
        """When this hub's storage was created (ISO-8601), or ``None`` if unknown.

        A rejoining agent compares this against when it last registered: if the store
        is newer, the directory was **reset** and remembered addresses may be stale.
        """
        cursor = await self._conn.execute(
            "SELECT value FROM hub_meta WHERE key = 'initialized_at'"
        )
        row = await cursor.fetchone()
        return row["value"] if row else None

    async def list_agents(
        self, project: str | None = None, include_stale: bool = False
    ) -> list[AgentInfo]:
        """List directory entries (optionally one project), newest-active first.

        By default entries unseen for ``stale_days`` are hidden: dead identities are
        exactly the ones with the emptiest profiles, so they make the room look worse
        than it is to a newcomer. Pass ``include_stale=True`` to see everything.
        """
        if project is not None:
            cursor = await self._conn.execute(
                "SELECT * FROM agents WHERE project = ? ORDER BY last_seen DESC",
                (project,),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM agents ORDER BY last_seen DESC"
            )
        rows = await cursor.fetchall()
        agents = [self._row_to_agent_info(row) for row in rows]
        if include_stale:
            return agents
        return [a for a in agents if not a.stale]

    async def whois(
        self, project: str, agent: str, role: str | None = None
    ) -> AgentInfo | None:
        """Return one agent's directory entry, or ``None`` if never registered."""
        cursor = await self._conn.execute(
            "SELECT * FROM agents WHERE project = ? AND agent = ? AND role = ?",
            (project, agent, role or ""),
        )
        row = await cursor.fetchone()
        return self._row_to_agent_info(row) if row else None

    # -- read-only console views (SELECT only — never consume) -------------
    #
    # These power the human web console. Unlike ``read``/``peek`` they NEVER ack or
    # claim a message, so observing another agent's mailbox can't steal its mail.

    async def browse(
        self, project: str, agent: str, role: str | None = None
    ) -> list[tuple[Message, bool]]:
        """All messages routed to ``project/agent`` (read *and* unread), newest first.

        Returns ``(message, unread)`` pairs. Purely observational — it never consumes,
        so it is safe to point at *any* agent's mailbox.
        """
        reader = format_address(project, agent, role)
        cursor = await self._conn.execute(
            f"SELECT * FROM messages WHERE {self._ROUTES_TO} ORDER BY created DESC",
            self._reader(project, agent, role),
        )
        rows = await cursor.fetchall()
        read_ids = await self._reader_broadcast_ids(reader)
        items: list[tuple[Message, bool]] = []
        for row in rows:
            if row["kind"] == "fanout":
                unread = row["id"] not in read_ids
            else:
                unread = row["acked_at"] is None
            items.append((_row_to_message(row), unread))
        return items

    async def _reader_broadcast_ids(self, reader: str) -> set[str]:
        cursor = await self._conn.execute(
            "SELECT message_id FROM broadcast_reads WHERE reader = ?", (reader,)
        )
        return {row["message_id"] for row in await cursor.fetchall()}

    async def message_by_id(self, message_id: str) -> Message | None:
        """Return one message by id without consuming it (read-only), or ``None``."""
        cursor = await self._conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        )
        row = await cursor.fetchone()
        return _row_to_message(row) if row else None

    async def thread(self, thread_id: str) -> list[Message]:
        """Return every message on ``thread_id`` (oldest first), read-only."""
        cursor = await self._conn.execute(
            "SELECT * FROM messages WHERE thread = ? ORDER BY created ASC",
            (thread_id,),
        )
        return [_row_to_message(row) for row in await cursor.fetchall()]

    async def stats(self) -> MailboxStats:
        """A read-only snapshot of hub traffic for the console dashboard."""
        total = await self._scalar("SELECT COUNT(*) FROM messages")
        # Unread = direct/any/global_any not yet acked. (Broadcast/public unread is
        # per-reader; we count the simple claim kinds for an at-a-glance figure.)
        unread = await self._scalar(
            "SELECT COUNT(*) FROM messages WHERE kind = 'claim' AND acked_at IS NULL"
        )
        agents = await self.list_agents()
        online = sum(1 for a in agents if a.online)
        cutoff = (datetime.now(tz=UTC) - timedelta(days=7)).isoformat()
        cursor = await self._conn.execute(
            "SELECT substr(created, 1, 10) AS day, COUNT(*) AS n FROM messages "
            "WHERE created >= ? GROUP BY day ORDER BY day ASC",
            (cutoff,),
        )
        per_day = [(row["day"], row["n"]) for row in await cursor.fetchall()]
        recent_cursor = await self._conn.execute(
            "SELECT * FROM messages ORDER BY created DESC LIMIT 10"
        )
        recent = [_row_to_message(row) for row in await recent_cursor.fetchall()]
        return MailboxStats(
            total_messages=total,
            unread_messages=unread,
            agents_total=len(agents),
            agents_online=online,
            per_day=per_day,
            recent=recent,
        )

    async def _scalar(self, sql: str, params: tuple[object, ...] = ()) -> int:
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    # -- threads (what have I sent? did they read it?) ---------------------

    def _party_clause(self) -> str:
        """SQL matching every message the caller is party to (sent or addressed to)."""
        return f"(from_addr = :me OR ({self._ROUTES_TO}))"

    async def _party_params(
        self, project: str, agent: str, role: str | None = None
    ) -> dict[str, str]:
        return self._reader(project, agent, role)

    async def _read_state(self, message_ids: list[str]) -> dict[str, str | None]:
        """When each message was consumed, whichever delivery mode it used.

        A `claim` records consumption on the row (`acked_at`); a `fanout` records it
        per-reader in `broadcast_reads`. Callers asking "have they read it yet?" must
        not care which — so this normalises both into one map.
        """
        if not message_ids:
            return {}
        marks = ",".join("?" * len(message_ids))
        state: dict[str, str | None] = {}
        cursor = await self._conn.execute(
            f"SELECT id, acked_at FROM messages WHERE id IN ({marks})",
            tuple(message_ids),
        )
        for row in await cursor.fetchall():
            state[row["id"]] = row["acked_at"]
        cursor = await self._conn.execute(
            f"SELECT message_id, MIN(acked_at) AS first_read FROM broadcast_reads "
            f"WHERE message_id IN ({marks}) GROUP BY message_id",
            tuple(message_ids),
        )
        for row in await cursor.fetchall():
            if not state.get(row["message_id"]):
                state[row["message_id"]] = row["first_read"]
        return state

    async def list_threads(
        self,
        project: str,
        agent: str,
        limit: int = 50,
        role: str | None = None,
    ) -> list[ThreadSummary]:
        """Threads this agent is party to — **including ones it started**.

        ``check_inbox`` only shows unread mail *to* you; this shows what you have sent
        and whether it went anywhere, so a coordinator need not keep its working memory
        in a local file.
        """
        me = format_address(project, agent)
        params = await self._party_params(project, agent)
        cursor = await self._conn.execute(
            f"SELECT * FROM messages WHERE {self._party_clause()} ORDER BY created ASC",
            params,
        )
        rows = await cursor.fetchall()
        by_thread: dict[str, list[aiosqlite.Row]] = {}
        for row in rows:
            by_thread.setdefault(row["thread"] or row["id"], []).append(row)

        read_state = await self._read_state([r["id"] for r in rows])
        summaries: list[ThreadSummary] = []
        for thread_id, turns in by_thread.items():
            last = turns[-1]
            others: list[str] = []
            for r in turns:
                for addr in (r["from_addr"], r["to_addr"]):
                    if addr != me and addr not in others:
                        others.append(addr)
            # "awaiting them" = the last word is mine and they haven't consumed it
            awaiting = last["from_addr"] == me and not read_state.get(last["id"])
            subject = next((r["subject"] for r in turns if r["subject"]), None)
            summaries.append(
                ThreadSummary(
                    thread=thread_id,
                    subject=subject,
                    counterparts=others,
                    turns=len(turns),
                    last_at=last["created"],
                    last_from=last["from_addr"],
                    awaiting_them=awaiting,
                )
            )
        summaries.sort(key=lambda s: s.last_at, reverse=True)
        return summaries[:limit]

    async def read_thread(
        self,
        project: str,
        agent: str,
        thread_id: str,
        role: str | None = None,
    ) -> list[ThreadTurn] | None:
        """Every turn on a thread, in order, with read-state. Read-only — never acks.

        Returns ``None`` if the thread doesn't exist **or the caller isn't party to
        it** (the two are deliberately indistinguishable from outside).
        """
        me = format_address(project, agent, role)
        params = await self._party_params(project, agent, role)
        params["thread"] = thread_id
        cursor = await self._conn.execute(
            "SELECT * FROM messages WHERE thread = :thread ORDER BY created ASC",
            {"thread": thread_id},
        )
        rows = await cursor.fetchall()
        if not rows:
            return None
        check = await self._conn.execute(
            f"SELECT 1 FROM messages WHERE thread = :thread AND {self._party_clause()} "
            f"LIMIT 1",
            params,
        )
        if await check.fetchone() is None:
            return None  # not your thread
        read_state = await self._read_state([r["id"] for r in rows])
        return [
            ThreadTurn(
                message=_row_to_message(row),
                read_at=read_state.get(row["id"]),
                mine=row["from_addr"] == me,
            )
            for row in rows
        ]

    async def flow_graph(self, since: str | None = None) -> FlowGraph:
        """Directed agent→agent message flow over a window (read-only).

        ``since`` is an ISO-8601 cutoff (messages at/after it); ``None`` = all history.
        Only ``direct`` messages produce edges — broadcast/anycast have no single
        recipient node — so those are returned as a ``broadcast_count`` footnote.
        A→B and B→A are distinct edges, so per-direction counts fall out naturally.
        """
        where = "to_project IS NOT NULL AND to_agent IS NOT NULL"
        params: tuple[object, ...] = ()
        if since is not None:
            where += " AND created >= ?"
            params = (since,)
        cursor = await self._conn.execute(
            f"SELECT from_addr, to_addr, COUNT(*) AS n, MAX(created) AS last "
            f"FROM messages WHERE {where} "
            f"GROUP BY from_addr, to_addr ORDER BY n DESC",
            params,
        )
        edges = [
            FlowEdge(frm=r["from_addr"], to=r["to_addr"], count=r["n"], last=r["last"])
            for r in await cursor.fetchall()
        ]
        broadcast_count = await self._scalar(
            "SELECT COUNT(*) FROM messages "
            "WHERE (to_project IS NULL OR to_agent IS NULL)"
            + (" AND created >= ?" if since is not None else ""),
            params,
        )
        node_set: list[str] = []
        seen: set[str] = set()
        for edge in edges:
            for addr in (edge.frm, edge.to):
                if addr not in seen:
                    seen.add(addr)
                    node_set.append(addr)
        agents = {a.address: a.online for a in await self.list_agents()}
        online = [addr for addr in node_set if agents.get(addr)]
        return FlowGraph(
            edges=edges,
            nodes=node_set,
            online=online,
            broadcast_count=broadcast_count,
        )

    async def messages_between(
        self, frm: str, to: str, since: str | None = None
    ) -> list[Message]:
        """Direct messages ``frm`` -> ``to`` in the window, newest first (read-only)."""
        sql = (
            "SELECT * FROM messages "
            "WHERE to_agent IS NOT NULL AND from_addr = ? AND to_addr = ?"
        )
        params: list[object] = [frm, to]
        if since is not None:
            sql += " AND created >= ?"
            params.append(since)
        sql += " ORDER BY created DESC"
        cursor = await self._conn.execute(sql, tuple(params))
        return [_row_to_message(row) for row in await cursor.fetchall()]


def _reply_subject(subject: str | None) -> str | None:
    if subject is None:
        return None
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"
