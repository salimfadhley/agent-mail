"""The SQLite-backed mailbox — the single core all surfaces (CLI, MCP) share.

Storage is one local SQLite file. No external services: ``pip install agent-mail``,
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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType

import aiosqlite

from agent_mail.config import Config, format_address, parse_target
from agent_mail.exceptions import MailboxError
from agent_mail.models import AgentInfo, AgentProfile, Intent, Message

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
    subject     TEXT NOT NULL,
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
"""


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
        cursor = await self._conn.execute(
            "SELECT * FROM messages "
            "WHERE acked_at IS NULL AND ("
            "  (kind = 'direct' AND to_project = ? AND to_agent = ?)"
            "  OR (kind = 'any' AND to_project = ?)"
            "  OR (kind = 'global_any')"
            "  OR (kind = 'broadcast' AND to_project = ? AND id NOT IN ("
            "        SELECT message_id FROM broadcast_reads WHERE reader = ?))"
            "  OR (kind = 'public' AND id NOT IN ("
            "        SELECT message_id FROM broadcast_reads WHERE reader = ?))"
            ") ORDER BY created ASC",
            (project, agent, project, project, reader, reader),
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
        probe = Message(from_=me, to=me, subject="agent-mail ping", body="ping")
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

    async def update_status(self, project: str, agent: str, status: str) -> AgentInfo:
        """Update just the ``status`` of an agent's profile (creates it if absent)."""
        current = await self.whois(project, agent)
        profile = current.profile if current else AgentProfile()
        return await self.register(
            project, agent, profile.model_copy(update={"status": status})
        )

    def _row_to_agent_info(self, row: aiosqlite.Row) -> AgentInfo:
        last_seen = datetime.fromisoformat(row["last_seen"])
        online = datetime.now(tz=UTC) - last_seen <= timedelta(
            seconds=self._config.online_seconds
        )
        return AgentInfo(
            project=row["project"],
            agent=row["agent"],
            address=format_address(row["project"], row["agent"]),
            first_seen=datetime.fromisoformat(row["first_seen"]),
            last_seen=last_seen,
            online=online,
            profile=AgentProfile.model_validate_json(row["profile"] or "{}"),
        )

    async def list_agents(self, project: str | None = None) -> list[AgentInfo]:
        """List directory entries (optionally one project), newest-active first."""
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
        return [self._row_to_agent_info(row) for row in rows]

    async def whois(self, project: str, agent: str) -> AgentInfo | None:
        """Return one agent's directory entry, or ``None`` if never registered."""
        cursor = await self._conn.execute(
            "SELECT * FROM agents WHERE project = ? AND agent = ?", (project, agent)
        )
        row = await cursor.fetchone()
        return self._row_to_agent_info(row) if row else None


def _reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"
