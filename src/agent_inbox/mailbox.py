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

from agent_inbox.config import Config, format_address, parse_target
from agent_inbox.exceptions import MailboxError
from agent_inbox.models import AgentInfo, AgentProfile, Intent, Message

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    from_addr   TEXT NOT NULL,
    to_addr     TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- 'direct' | 'any' | 'broadcast'
    to_project  TEXT NOT NULL,
    to_agent    TEXT,                   -- direct only; NULL for any/broadcast
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
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    profile     TEXT NOT NULL DEFAULT '{}',   -- JSON AgentProfile
    PRIMARY KEY (project, agent)
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
        await self._db.executescript(_SCHEMA)
        # Stamp when this storage was created. INSERT OR IGNORE means an existing
        # store keeps its original timestamp — only a fresh file gets a new one.
        await self._db.execute(
            "INSERT OR IGNORE INTO hub_meta (key, value) VALUES ('initialized_at', ?)",
            (_now_iso(),),
        )
        await self._db.commit()
        await self._purge_expired()

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
        kind, project, agent = parse_target(message.to)
        # Global kinds (public / global_any) have no project scope; store "" (the
        # column is NOT NULL and real projects are never empty).
        await self._conn.execute(
            "INSERT INTO messages (id, from_addr, to_addr, kind, to_project, "
            "to_agent, thread, intent, subject, body, created, acked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                message.id,
                message.from_,
                message.to,
                kind,
                project or "",
                agent,
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

    async def peek(self, project: str, agent: str) -> list[Message]:
        """Return unread messages for ``project/agent`` without consuming them."""
        reader = format_address(project, agent)
        # Fan-out and claim-anywhere kinds skip their own sender — you should never
        # receive your own broadcast. Direct is exempt: `ping` self-sends deliberately.
        cursor = await self._conn.execute(
            "SELECT * FROM messages "
            "WHERE acked_at IS NULL AND ("
            "  (kind = 'direct' AND to_project = ? AND to_agent = ?)"
            "  OR (kind = 'any' AND to_project = ? AND from_addr != ?)"
            "  OR (kind = 'global_any' AND from_addr != ?)"
            "  OR (kind = 'broadcast' AND to_project = ? AND from_addr != ?"
            "      AND id NOT IN ("
            "        SELECT message_id FROM broadcast_reads WHERE reader = ?))"
            "  OR (kind = 'public' AND from_addr != ? AND id NOT IN ("
            "        SELECT message_id FROM broadcast_reads WHERE reader = ?))"
            ") ORDER BY created ASC",
            (
                project,
                agent,
                project,
                reader,
                reader,
                project,
                reader,
                reader,
                reader,
                reader,
            ),
        )
        rows = await cursor.fetchall()
        return [_row_to_message(row) for row in rows]

    async def read(self, project: str, agent: str, message_id: str) -> Message:
        """Return the message with ``message_id`` and consume it for this agent.

        For **direct**/**any** messages this claims the row atomically (first reader
        wins — so an ``any`` message is delivered exactly once). For **broadcast** it
        records that *this* agent has consumed its copy; other agents still see theirs.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise self._not_found(project, agent, message_id)

        kind = row["kind"]
        if kind in ("broadcast", "public"):
            # fan-out kinds: each agent consumes its own copy. 'public' spans all
            # projects, so it has no project scope to check.
            if kind == "broadcast" and row["to_project"] != project:
                raise self._not_found(project, agent, message_id)
            reader = format_address(project, agent)
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
        else:  # claim kinds: direct, any (project), global_any (anywhere)
            if kind == "direct" and (
                row["to_project"] != project or row["to_agent"] != agent
            ):
                raise self._not_found(project, agent, message_id)
            if kind == "any" and row["to_project"] != project:
                raise self._not_found(project, agent, message_id)
            claim = await self._conn.execute(
                "UPDATE messages SET acked_at = ? WHERE id = ? AND acked_at IS NULL",
                (_now_iso(), message_id),
            )
            await self._conn.commit()
            if claim.rowcount != 1:  # already consumed (e.g. another agent won the any)
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
    ) -> Message:
        """Consume message ``message_id`` and reply directly to its sender."""
        original = await self.read(project, agent, message_id)
        reply = Message(
            from_=format_address(project, agent),
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

    async def ping(self, project: str, agent: str) -> Message:
        """Round-trip a probe to ``project/agent`` itself and consume it."""
        me = format_address(project, agent)
        probe = Message(from_=me, to=me, subject="agent-inbox ping", body="ping")
        await self.send(probe)
        received = await self.read(project, agent, probe.id)
        logger.debug("ping round-trip ok for %s (%s)", me, probe.id)
        return received

    # -- directory / presence ---------------------------------------------

    async def touch(self, project: str, agent: str) -> None:
        """Record that ``project/agent`` was just active (upsert ``last_seen``)."""
        now = _now_iso()
        await self._conn.execute(
            "INSERT INTO agents (project, agent, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(project, agent) DO UPDATE SET last_seen = excluded.last_seen",
            (project, agent, now, now),
        )
        await self._conn.commit()

    async def register(
        self, project: str, agent: str, profile: AgentProfile
    ) -> AgentInfo:
        """Set ``project/agent``'s profile (and mark it active). Returns the entry."""
        now = _now_iso()
        await self._conn.execute(
            "INSERT INTO agents (project, agent, first_seen, last_seen, profile) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(project, agent) DO UPDATE SET "
            "last_seen = excluded.last_seen, profile = excluded.profile",
            (project, agent, now, now, profile.model_dump_json()),
        )
        await self._conn.commit()
        info = await self.whois(project, agent)
        assert info is not None  # just inserted
        return info

    async def retire(self, project: str, agent: str) -> bool:
        """Remove a directory entry. Returns whether one was actually removed.

        Used to tombstone a **superseded identity** — e.g. after re-deriving your
        address you retire the old one so the room isn't full of your ghosts.
        """
        cursor = await self._conn.execute(
            "DELETE FROM agents WHERE project = ? AND agent = ?", (project, agent)
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

    async def update_status(self, project: str, agent: str, status: str) -> AgentInfo:
        """Update just the ``status`` of an agent's profile (creates it if absent)."""
        current = await self.whois(project, agent)
        profile = current.profile if current else AgentProfile()
        return await self.register(
            project, agent, profile.model_copy(update={"status": status})
        )

    def _row_to_agent_info(self, row: aiosqlite.Row) -> AgentInfo:
        last_seen = datetime.fromisoformat(row["last_seen"])
        idle = datetime.now(tz=UTC) - last_seen
        online = idle <= timedelta(seconds=self._config.online_seconds)
        stale = self._config.stale_days > 0 and idle > timedelta(
            days=self._config.stale_days
        )
        return AgentInfo(
            project=row["project"],
            agent=row["agent"],
            address=format_address(row["project"], row["agent"]),
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

    async def whois(self, project: str, agent: str) -> AgentInfo | None:
        """Return one agent's directory entry, or ``None`` if never registered."""
        cursor = await self._conn.execute(
            "SELECT * FROM agents WHERE project = ? AND agent = ?", (project, agent)
        )
        row = await cursor.fetchone()
        return self._row_to_agent_info(row) if row else None

    # -- read-only console views (SELECT only — never consume) -------------
    #
    # These power the human web console. Unlike ``read``/``peek`` they NEVER ack or
    # claim a message, so observing another agent's mailbox can't steal its mail.

    async def browse(self, project: str, agent: str) -> list[tuple[Message, bool]]:
        """All messages routed to ``project/agent`` (read *and* unread), newest first.

        Returns ``(message, unread)`` pairs. Purely observational — it never consumes,
        so it is safe to point at *any* agent's mailbox.
        """
        reader = format_address(project, agent)
        cursor = await self._conn.execute(
            "SELECT * FROM messages WHERE "
            "  (kind = 'direct' AND to_project = ? AND to_agent = ?)"
            "  OR (kind = 'any' AND to_project = ?)"
            "  OR (kind = 'global_any')"
            "  OR (kind = 'broadcast' AND to_project = ?)"
            "  OR (kind = 'public')"
            " ORDER BY created DESC",
            (project, agent, project, project),
        )
        rows = await cursor.fetchall()
        read_ids = await self._reader_broadcast_ids(reader)
        items: list[tuple[Message, bool]] = []
        for row in rows:
            if row["kind"] in ("broadcast", "public"):
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
            "SELECT COUNT(*) FROM messages "
            "WHERE kind IN ('direct','any','global_any') AND acked_at IS NULL"
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
        return (
            "(from_addr = :me"
            " OR (kind = 'direct' AND to_project = :proj AND to_agent = :agent)"
            " OR (kind IN ('any','broadcast') AND to_project = :proj)"
            " OR kind IN ('public','global_any'))"
        )

    async def _party_params(self, project: str, agent: str) -> dict[str, str]:
        return {
            "me": format_address(project, agent),
            "proj": project,
            "agent": agent,
        }

    async def list_threads(
        self, project: str, agent: str, limit: int = 50
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

        summaries: list[ThreadSummary] = []
        for thread_id, turns in by_thread.items():
            last = turns[-1]
            others: list[str] = []
            for r in turns:
                for addr in (r["from_addr"], r["to_addr"]):
                    if addr != me and addr not in others:
                        others.append(addr)
            # "awaiting them" = the last word is mine and they haven't consumed it
            awaiting = last["from_addr"] == me and last["acked_at"] is None
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
        self, project: str, agent: str, thread_id: str
    ) -> list[ThreadTurn] | None:
        """Every turn on a thread, in order, with read-state. Read-only — never acks.

        Returns ``None`` if the thread doesn't exist **or the caller isn't party to
        it** (the two are deliberately indistinguishable from outside).
        """
        me = format_address(project, agent)
        params = await self._party_params(project, agent)
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
        return [
            ThreadTurn(
                message=_row_to_message(row),
                read_at=row["acked_at"],
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
        where = "kind = 'direct'"
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
            "SELECT COUNT(*) FROM messages WHERE kind != 'direct'"
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
            "WHERE kind = 'direct' AND from_addr = ? AND to_addr = ?"
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
