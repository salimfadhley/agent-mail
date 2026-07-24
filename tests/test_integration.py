"""End-to-end mailbox round-trips against a real (temp-file) SQLite store.

No external services and no gating: the SQLite backend needs nothing but a file,
so these run in normal CI.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from agent_inbox.config import Config
from agent_inbox.exceptions import ConfigError, MailboxError
from agent_inbox.mailbox import Mailbox
from agent_inbox.models import AgentProfile, Intent, Message


@pytest_asyncio.fixture
async def mailbox(tmp_path: Path) -> AsyncIterator[Mailbox]:
    config = Config().model_copy(update={"db": str(tmp_path / "mail.db")})
    async with Mailbox(config) as mb:
        yield mb


def _project() -> str:
    return f"itest-{uuid4().hex[:8]}"


async def test_direct_send_peek_read_reply(mailbox: Mailbox) -> None:
    project = _project()
    original = Message(
        from_=f"{project}/alice", to=f"{project}/bob", subject="ping", body="here?"
    )
    await mailbox.send(original)

    # peek does not consume
    assert any(m.id == original.id for m in await mailbox.peek(project, "bob"))
    assert any(m.id == original.id for m in await mailbox.peek(project, "bob"))

    # read consumes
    got = await mailbox.read(project, "bob", original.id)
    assert got.body == "here?"
    assert await mailbox.peek(project, "bob") == []

    # a second read of the same message is not found (already consumed)
    with pytest.raises(MailboxError):
        await mailbox.read(project, "bob", original.id)

    # reply lands directly in alice's inbox, threaded
    second = Message(
        from_=f"{project}/alice", to=f"{project}/bob", subject="q", body="q"
    )
    await mailbox.send(second)
    reply = await mailbox.reply(project, "bob", second.id, "yes")
    assert reply.intent is Intent.reply
    assert reply.to == f"{project}/alice"
    assert reply.thread == second.id
    assert any(m.id == reply.id for m in await mailbox.peek(project, "alice"))
    await mailbox.read(project, "alice", reply.id)


async def test_direct_message_is_invisible_to_other_agents(mailbox: Mailbox) -> None:
    project = _project()
    await mailbox.send(
        Message(from_=f"{project}/a", to=f"{project}/bob", subject="s", body="b")
    )
    assert await mailbox.peek(project, "carol") == []
    with pytest.raises(MailboxError):
        # carol cannot read a message addressed to bob
        msg_id = (await mailbox.peek(project, "bob"))[0].id
        await mailbox.read(project, "carol", msg_id)


async def test_broadcast_reaches_every_agent(mailbox: Mailbox) -> None:
    project = _project()
    await mailbox.send(
        Message(from_=f"{project}/sys", to=f"{project}/*", subject="all", body="hi")
    )
    alice = await mailbox.peek(project, "alice")
    bob = await mailbox.peek(project, "bob")
    assert any(m.subject == "all" for m in alice)
    assert any(m.subject == "all" for m in bob)

    # each agent consumes its own copy independently
    await mailbox.read(project, "alice", alice[0].id)
    assert not any(m.subject == "all" for m in await mailbox.peek(project, "alice"))
    assert any(m.subject == "all" for m in await mailbox.peek(project, "bob"))


async def test_any_addresses_are_rejected(mailbox: Mailbox) -> None:
    """The retired keyword fails at send time, with an explanation."""
    project = _project()
    with pytest.raises(ConfigError, match="retired"):
        await mailbox.send(
            Message(from_=f"{project}/sys", to=f"{project}/any", subject="t", body="x")
        )


async def test_every_recipient_gets_its_own_copy(mailbox: Mailbox) -> None:
    """One delivery mode: a project broadcast reaches everyone, independently."""
    project = _project()
    await mailbox.send(
        Message(from_=f"{project}/sys", to=project, subject="task", body="x")
    )
    alice = [m for m in await mailbox.peek(project, "alice") if m.subject == "task"]
    assert alice and any(
        m.subject == "task" for m in await mailbox.peek(project, "bob")
    )

    # alice consuming her copy leaves bob's untouched
    await mailbox.read(project, "alice", alice[0].id)
    assert not any(m.subject == "task" for m in await mailbox.peek(project, "alice"))
    assert any(m.subject == "task" for m in await mailbox.peek(project, "bob"))

    # and a second read by the same agent is still refused
    with pytest.raises(MailboxError):
        await mailbox.read(project, "alice", alice[0].id)


async def test_ping_roundtrip(mailbox: Mailbox) -> None:
    project = _project()
    received = await mailbox.ping(project, "alice")
    assert received.subject == "agent-inbox ping"
    assert await mailbox.peek(project, "alice") == []


async def test_notify_is_a_noop_but_validates(mailbox: Mailbox) -> None:
    await mailbox.notify(f"{_project()}/x")  # success = no raise
    with pytest.raises(ConfigError):
        await mailbox.notify("bad project/x")


async def test_register_and_whois(mailbox: Mailbox) -> None:
    profile = AgentProfile(
        model="claude-opus",
        offers=["deploys", "the legal corpus"],
        needs=["balsa wood"],
        charter_summary="runs the pipeline",
    )
    info = await mailbox.register("goldberg", "opus", profile)
    assert info.address == "goldberg/opus"
    assert info.online is True
    assert info.profile.offers == ["deploys", "the legal corpus"]

    got = await mailbox.whois("goldberg", "opus")
    assert got is not None
    assert got.profile.needs == ["balsa wood"]
    assert got.profile.charter_summary == "runs the pipeline"

    assert await mailbox.whois("goldberg", "nobody") is None


async def test_list_agents_and_project_filter(mailbox: Mailbox) -> None:
    await mailbox.register("proj-a", "alice", AgentProfile(status="busy"))
    await mailbox.register("proj-a", "bob", AgentProfile())
    await mailbox.register("proj-b", "carol", AgentProfile())

    everyone = await mailbox.list_agents()
    assert {a.address for a in everyone} == {
        "proj-a/alice",
        "proj-a/bob",
        "proj-b/carol",
    }
    only_a = await mailbox.list_agents("proj-a")
    assert {a.address for a in only_a} == {"proj-a/alice", "proj-a/bob"}


async def test_touch_creates_and_updates_last_seen(mailbox: Mailbox) -> None:
    await mailbox.touch("p", "a")
    first = await mailbox.whois("p", "a")
    assert first is not None and first.online is True
    # touching again keeps first_seen but advances last_seen
    await mailbox.touch("p", "a")
    second = await mailbox.whois("p", "a")
    assert second is not None
    assert second.first_seen == first.first_seen
    assert second.last_seen >= first.last_seen


async def test_update_status(mailbox: Mailbox) -> None:
    await mailbox.register("p", "a", AgentProfile(offers=["x"]))
    info = await mailbox.update_status("p", "a", "away")
    assert info.profile.status == "away"
    assert info.profile.offers == ["x"]  # other fields preserved


async def test_online_threshold(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    config = Config().model_copy(
        update={"db": str(tmp_path / "mail.db"), "online_seconds": 300}
    )
    async with Mailbox(config) as mb:
        await mb.register("p", "a", AgentProfile())
        assert (await mb.whois("p", "a")).online is True  # type: ignore[union-attr]
        old = (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat()
        await mb._conn.execute(
            "UPDATE agents SET last_seen = ? WHERE project = 'p' AND agent = 'a'",
            (old,),
        )
        await mb._conn.commit()
        assert (await mb.whois("p", "a")).online is False  # type: ignore[union-attr]


async def test_max_message_size_enforced(tmp_path: Path) -> None:
    config = Config().model_copy(
        update={"db": str(tmp_path / "mail.db"), "max_message_bytes": 200}
    )
    async with Mailbox(config) as mb:
        big = Message(from_="p/a", to="p/b", subject="s", body="x" * 500)
        with pytest.raises(MailboxError):
            await mb.send(big)


async def test_expiry_purges_old_messages(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    db = str(tmp_path / "mail.db")
    config = Config().model_copy(update={"db": db, "ttl_days": 7})
    async with Mailbox(config) as mb:
        old = Message(
            from_="p/a",
            to="p/bob",
            subject="old",
            body="b",
            created=datetime.now(tz=UTC) - timedelta(days=30),
        )
        fresh = Message(from_="p/a", to="p/bob", subject="fresh", body="b")
        await mb.send(old)
        await mb.send(fresh)

    # purge runs on connect
    async with Mailbox(config) as mb:
        subjects = {m.subject for m in await mb.peek("p", "bob")}
    assert subjects == {"fresh"}


# -- mission 0009: hub feedback from the host --------------------------------


async def test_sender_does_not_receive_own_broadcast(mailbox: Mailbox) -> None:
    """An all/all broadcast must not land back in the sender's own inbox."""
    project = _project()
    await mailbox.register(project, "alice", AgentProfile())
    shout = Message(from_=f"{project}/alice", to="all/all", subject="hi", body="all")
    await mailbox.send(shout)

    assert all(m.id != shout.id for m in await mailbox.peek(project, "alice"))
    # ...but everyone else still gets it
    assert any(m.id == shout.id for m in await mailbox.peek(project, "bob"))


async def test_direct_self_send_still_works(mailbox: Mailbox) -> None:
    """ping self-sends a direct message — that must keep working."""
    project = _project()
    received = await mailbox.ping(project, "alice")
    assert received.from_ == f"{project}/alice"


async def test_storage_initialized_at_is_stamped_and_stable(mailbox: Mailbox) -> None:
    first = await mailbox.storage_initialized_at()
    assert first is not None
    # reconnecting must not rewrite it — that is the whole point
    await mailbox.close()
    await mailbox.connect()
    assert await mailbox.storage_initialized_at() == first


async def test_list_threads_shows_what_i_sent(mailbox: Mailbox) -> None:
    project = _project()
    sent = Message(
        from_=f"{project}/alice", to=f"{project}/bob", subject="q", body="question?"
    )
    await mailbox.send(sent)

    # the SENDER can see it — check_inbox never showed them this
    threads = await mailbox.list_threads(project, "alice")
    assert len(threads) == 1
    t = threads[0]
    assert t.counterparts == [f"{project}/bob"]
    assert t.turns == 1
    assert t.awaiting_them is True  # unread by bob

    # once bob reads it, the sender can tell
    await mailbox.read(project, "bob", sent.id)
    assert (await mailbox.list_threads(project, "alice"))[0].awaiting_them is False


async def test_read_thread_shows_read_state_and_is_party_restricted(
    mailbox: Mailbox,
) -> None:
    project = _project()
    first = Message(
        from_=f"{project}/alice", to=f"{project}/bob", subject="s", body="one"
    )
    await mailbox.send(first)

    turns = await mailbox.read_thread(project, "alice", first.thread or first.id)
    assert turns is not None and len(turns) == 1
    assert turns[0].mine is True
    assert turns[0].read_at is None  # not yet consumed

    # reading a thread must not consume it
    assert any(m.id == first.id for m in await mailbox.peek(project, "bob"))

    # a stranger on another project cannot read it
    assert (
        await mailbox.read_thread("other-proj", "eve", first.thread or first.id) is None
    )
    assert await mailbox.read_thread(project, "alice", "no-such-thread") is None


async def test_read_thread_hides_private_turns_from_broadcast_recipients(
    mailbox: Mailbox,
) -> None:
    """A fan-out recipient must not read 1:1 turns that continue the same thread.

    Regression for mission 0020. The membership test used to ask "am I party to *any*
    message on this thread?" and unlock *every* row, so an ordinary broadcast that two
    recipients then continued privately leaked those private turns to everyone who got
    the original. No malice needed — three live threads already had this shape.
    """
    project = _project()
    announcement = Message(
        from_=f"{project}/alice", to=project, subject="sync", body="10am"
    )
    await mailbox.send(announcement)
    thread = announcement.thread or announcement.id

    # eve is a legitimate recipient of the broadcast
    assert any(m.id == announcement.id for m in await mailbox.peek(project, "eve"))

    # alice and bob continue *that thread* one-to-one
    for frm, to, body in (
        ("alice", "bob", "private one"),
        ("bob", "alice", "private two"),
    ):
        await mailbox.send(
            Message(
                from_=f"{project}/{frm}",
                to=f"{project}/{to}",
                thread=thread,
                subject="Re: sync",
                body=body,
            )
        )

    # eve sees only the turn she was actually party to
    eve_turns = await mailbox.read_thread(project, "eve", thread)
    assert eve_turns is not None
    assert [t.message.body for t in eve_turns] == ["10am"]

    # the actual participants still see the whole conversation
    alice_turns = await mailbox.read_thread(project, "alice", thread)
    assert alice_turns is not None
    assert [t.message.body for t in alice_turns] == [
        "10am",
        "private one",
        "private two",
    ]


async def test_send_cannot_inject_a_turn_into_someone_elses_thread(
    mailbox: Mailbox,
) -> None:
    """A send naming a thread the sender can't see starts its own thread instead.

    Mission 0020. Not a disclosure — read_thread filters per turn — but injecting into
    a stranger's conversation reads as forgery to the participants. Refusal is quiet so
    a sender cannot probe which thread ids exist.
    """
    project = _project()
    first = Message(
        from_=f"{project}/alice", to=f"{project}/bob", subject="salary", body="private"
    )
    await mailbox.send(first)
    thread = first.thread or first.id

    intruder = Message(
        from_="other-proj/eve",
        to=f"{project}/bob",
        thread=thread,
        subject="me too",
        body="x",
    )
    sent = await mailbox.send(intruder)

    # it sent, but on its own thread — bob's conversation with alice is untouched
    assert sent.thread == intruder.id
    assert [m.body for m in await mailbox.thread(thread)] == ["private"]

    # a genuine participant may still continue the thread
    cont = await mailbox.send(
        Message(
            from_=f"{project}/bob",
            to=f"{project}/alice",
            thread=thread,
            subject="Re: salary",
            body="ok",
        )
    )
    assert cont.thread == thread


async def test_read_thread_cannot_be_joined_by_naming_the_thread(
    mailbox: Mailbox,
) -> None:
    """Naming someone else's thread on a send must not grant read access to it.

    Regression for mission 0020. ``thread`` is caller-supplied and exposed over MCP, so
    a single ``send`` naming a private thread used to make the sender party to it all.
    """
    project = _project()
    first = Message(
        from_=f"{project}/alice",
        to=f"{project}/bob",
        subject="salary",
        body="confidential",
    )
    await mailbox.send(first)
    thread = first.thread or first.id

    assert await mailbox.read_thread("other-proj", "eve", thread) is None

    # eve names that thread on a message to herself
    await mailbox.send(
        Message(
            from_="other-proj/eve",
            to="other-proj/eve",
            thread=thread,
            subject="hi",
            body="me too",
        )
    )

    turns = await mailbox.read_thread("other-proj", "eve", thread)
    bodies = [t.message.body for t in turns or []]
    assert "confidential" not in bodies


async def test_stale_entries_hidden_and_supersede_protects_the_living(
    mailbox: Mailbox, tmp_path: Path
) -> None:
    project = _project()
    await mailbox.register(project, "live", AgentProfile())

    # a live agent is listed, and cannot be superseded by someone else
    assert any(a.agent == "live" for a in await mailbox.list_agents(project))
    assert await mailbox.supersede(f"{project}/other", [f"{project}/live"]) == []
    assert any(a.agent == "live" for a in await mailbox.list_agents(project))

    # force an entry to look long-abandoned
    old = (datetime.now(tz=UTC) - timedelta(days=99)).isoformat()
    await mailbox._conn.execute(
        "UPDATE agents SET last_seen = ? WHERE project = ? AND agent = ?",
        (old, project, "live"),
    )
    await mailbox._conn.commit()

    assert not any(a.agent == "live" for a in await mailbox.list_agents(project))
    assert any(
        a.agent == "live"
        for a in await mailbox.list_agents(project, include_stale=True)
    )
    # now it can be tombstoned
    assert await mailbox.supersede(f"{project}/other", [f"{project}/live"]) == [
        f"{project}/live"
    ]
    assert await mailbox.whois(project, "live") is None


# -- migration from the pre-three-part schema ---------------------------------


async def test_migration_preserves_a_pre_three_part_store(tmp_path: Path) -> None:
    """Opening an old store must upgrade it in place without losing anything.

    Losing the store once already cost us: agents re-derived addresses against an
    empty directory and one project ended up split across two names. So this builds
    a genuine v0.5-era database and asserts every row survives and still routes.
    """
    import sqlite3

    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE messages (
            id TEXT PRIMARY KEY, from_addr TEXT NOT NULL, to_addr TEXT NOT NULL,
            kind TEXT NOT NULL, to_project TEXT NOT NULL, to_agent TEXT,
            thread TEXT, intent TEXT NOT NULL, subject TEXT, body TEXT NOT NULL,
            created TEXT NOT NULL, acked_at TEXT
        );
        CREATE TABLE broadcast_reads (
            message_id TEXT NOT NULL, reader TEXT NOT NULL, acked_at TEXT NOT NULL,
            PRIMARY KEY (message_id, reader)
        );
        CREATE TABLE agents (
            project TEXT NOT NULL, agent TEXT NOT NULL, first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL, profile TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (project, agent)
        );
        """
    )
    now = datetime.now(tz=UTC).isoformat()
    con.executemany(
        "INSERT INTO messages (id, from_addr, to_addr, kind, to_project, to_agent, "
        "thread, intent, subject, body, created, acked_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # a direct message still waiting to be read
            (
                "m1",
                "p/alice",
                "p/bob",
                "direct",
                "p",
                "bob",
                "m1",
                "message",
                "hello",
                "body one",
                now,
                None,
            ),
            # a project broadcast
            (
                "m2",
                "p/alice",
                "p/all",
                "broadcast",
                "p",
                None,
                "m2",
                "message",
                "shout",
                "body two",
                now,
                None,
            ),
            # a public broadcast — stored with '' for "no project scope"
            (
                "m3",
                "q/carol",
                "all/all",
                "public",
                "",
                None,
                "m3",
                "message",
                "townhall",
                "body three",
                now,
                None,
            ),
        ],
    )
    con.execute(
        "INSERT INTO agents (project, agent, first_seen, last_seen, profile) "
        "VALUES (?,?,?,?,?)",
        ("p", "bob", now, now, '{"offers": ["deploys"]}'),
    )
    con.commit()
    con.close()

    config = Config().model_copy(update={"db": str(db)})
    async with Mailbox(config) as mb:
        # nothing was dropped
        assert await mb._scalar("SELECT COUNT(*) FROM messages") == 3

        # the directory entry survived, profile intact, now carrying an empty role
        bob = await mb.whois("p", "bob")
        assert bob is not None
        assert bob.profile.offers == ["deploys"]
        assert bob.role is None

        # and everything still routes: bob sees his direct message, the project
        # broadcast, and the public one
        subjects = {m.subject for m in await mb.peek("p", "bob")}
        assert subjects == {"hello", "shout", "townhall"}

        # an agent on another project sees only the public broadcast
        assert {m.subject for m in await mb.peek("q", "dave")} == {"townhall"}

        # reading still consumes exactly once
        first = next(m for m in await mb.peek("p", "bob") if m.subject == "hello")
        await mb.read("p", "bob", first.id)
        assert "hello" not in {m.subject for m in await mb.peek("p", "bob")}


async def test_migration_is_idempotent(tmp_path: Path) -> None:
    """Re-opening an already-migrated store must not re-run or corrupt anything."""
    config = Config().model_copy(update={"db": str(tmp_path / "twice.db")})
    async with Mailbox(config) as mb:
        await mb.send(Message(from_="p/a", to="p/b", subject="s", body="b"))
        stamp = await mb.storage_initialized_at()
    async with Mailbox(config) as mb:
        assert await mb._scalar("SELECT COUNT(*) FROM messages") == 1
        assert await mb.storage_initialized_at() == stamp


# -- mission 0013: friction fixes ---------------------------------------------


async def test_reply_works_after_reading(mailbox: Mailbox) -> None:
    """read-then-reply is the NATURAL sequence; it must not be the broken one.

    Reported by goldberg/system: reading acks, after which reply_message could not
    find the message at all.
    """
    project = _project()
    original = Message(
        from_=f"{project}/alice", to=f"{project}/bob", subject="q", body="question?"
    )
    await mailbox.send(original)

    await mailbox.read(project, "bob", original.id)  # consumed
    reply = await mailbox.reply(
        project, "bob", original.id, "answer"
    )  # must still work

    assert reply.intent is Intent.reply
    assert reply.to == f"{project}/alice"
    assert reply.thread == original.thread
    assert reply.subject == "Re: q"

    # ...but you still cannot reply to mail that was never addressed to you
    with pytest.raises(MailboxError):
        await mailbox.reply("other", "eve", original.id, "nope")


async def test_storage_stamp_never_postdates_its_own_data(
    mailbox: Mailbox,
) -> None:
    """A live hub claimed it was created 38 minutes AFTER its oldest message.

    Reported by woking_improv_website: the stamp made rejoining agents distrust a
    directory that had never been reset.
    """
    project = _project()
    await mailbox.send(
        Message(from_=f"{project}/a", to=f"{project}/b", subject="s", body="b")
    )
    # simulate the v0.5.0 upgrade case: hub_meta written long after the real data
    await mailbox._conn.execute(
        "UPDATE hub_meta SET value = ? WHERE key = 'initialized_at'",
        ((datetime.now(tz=UTC) + timedelta(days=1)).isoformat(),),
    )
    await mailbox._conn.commit()

    stamp = await mailbox.storage_initialized_at()
    oldest = await mailbox._oldest_record()
    assert stamp is not None and oldest is not None
    assert stamp <= oldest, "the stamp must never post-date the data it holds"


async def test_reach_lets_an_agent_triage_at_a_glance(mailbox: Mailbox) -> None:
    """Was this aimed at me, or at everyone? Previously only derivable by parsing `to`.

    Reported by steele_fcpxml, whose sharpest point was that the broadcast asking
    people NOT to reply is exactly the one they cannot tell from a direct request.
    """
    project = _project()
    await mailbox.send(
        Message(from_=f"{project}/a", to=f"{project}/bob", subject="d", body="x")
    )
    await mailbox.send(
        Message(from_=f"{project}/a", to=f"{project}/all", subject="p", body="x")
    )
    await mailbox.send(Message(from_="other/a", to="all/all", subject="b", body="x"))

    reach = {m.subject: m.reach for m in await mailbox.peek(project, "bob")}
    assert reach == {"d": "direct", "p": "project", "b": "broadcast"}


# -- mission 0012 part 2: renames with forwarding ------------------------------


async def test_rename_moves_mail_profile_and_forwards(mailbox: Mailbox) -> None:
    project = _project()
    await mailbox.register(project, "claude", AgentProfile(offers=["deploys"]))
    waiting = Message(
        from_=f"{project}/peer", to=f"{project}/claude", subject="waiting", body="x"
    )
    await mailbox.send(waiting)

    old, new, moved = await mailbox.rename(
        project, "claude", f"{project}/claude/system"
    )
    assert (old, new) == (f"{project}/claude", f"{project}/claude/system")
    assert moved == 1

    # mail already waiting came with them, under the new name
    assert [m.subject for m in await mailbox.peek(project, "claude", "system")] == [
        "waiting"
    ]
    # the profile moved too
    moved_info = await mailbox.whois(project, "claude", "system")
    assert moved_info is not None and moved_info.profile.offers == ["deploys"]
    assert await mailbox.whois(project, "claude") is None

    # later mail to the OLD name is delivered onward, and the sender is told
    sent = await mailbox.send(
        Message(
            from_=f"{project}/peer", to=f"{project}/claude", subject="later", body="y"
        )
    )
    assert sent.forwarded_to == new
    assert sent.to == new
    assert "later" in {
        m.subject for m in await mailbox.peek(project, "claude", "system")
    }


async def test_rename_refuses_to_displace_a_live_agent(mailbox: Mailbox) -> None:
    project = _project()
    await mailbox.register(project, "alice", AgentProfile())
    await mailbox.register(project, "bob", AgentProfile())
    with pytest.raises(MailboxError, match="already held"):
        await mailbox.rename(project, "alice", f"{project}/bob")


async def test_rename_refuses_wildcards_and_cycles(mailbox: Mailbox) -> None:
    project = _project()
    await mailbox.register(project, "a", AgentProfile())
    with pytest.raises(MailboxError, match="wildcard"):
        await mailbox.rename(project, "a", project)

    # a -> b, then b -> a would make delivery loop
    await mailbox.rename(project, "a", f"{project}/b")
    with pytest.raises(MailboxError, match="forwards back"):
        await mailbox.rename(project, "b", f"{project}/a")


async def test_forwarding_expires_into_a_pointer(mailbox: Mailbox) -> None:
    """After the grace period mail stops being delivered, but still says where to go."""
    project = _project()
    await mailbox.register(project, "old", AgentProfile())
    await mailbox.rename(project, "old", f"{project}/new")

    past = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    await mailbox._conn.execute("UPDATE forwards SET expires_at = ?", (past,))
    await mailbox._conn.commit()

    with pytest.raises(MailboxError, match="renamed"):
        await mailbox.send(
            Message(from_=f"{project}/p", to=f"{project}/old", subject="s", body="b")
        )


async def test_a_rename_chain_stays_one_hop(mailbox: Mailbox) -> None:
    """a -> b -> c: mail to the ORIGINAL name must reach the final one."""
    project = _project()
    await mailbox.register(project, "a", AgentProfile())
    await mailbox.rename(project, "a", f"{project}/b")
    await mailbox.rename(project, "b", f"{project}/c")

    sent = await mailbox.send(
        Message(from_=f"{project}/p", to=f"{project}/a", subject="chain", body="x")
    )
    assert sent.forwarded_to == f"{project}/c"
    assert "chain" in {m.subject for m in await mailbox.peek(project, "c")}


async def test_rename_does_not_resurrect_your_own_broadcasts(
    mailbox: Mailbox,
) -> None:
    """Renaming must not make an agent start receiving its own old fan-outs.

    The self-exclusion guard matches on from_addr, so leaving sent mail under the
    old name reintroduces the self-broadcast bug fixed in v0.5.0 — caught by running
    a rename against a copy of real hub data.
    """
    project = _project()
    await mailbox.register(project, "alice", AgentProfile())
    await mailbox.send(
        Message(from_=f"{project}/alice", to="all/all", subject="mine", body="x")
    )
    before = len(await mailbox.peek(project, "alice"))

    await mailbox.rename(project, "alice", f"{project}/alice/lead")

    after = [m.subject for m in await mailbox.peek(project, "alice", "lead")]
    assert "mine" not in after, "an agent must never receive its own broadcast"
    assert len(after) == before

    # and the sent message is still discoverable as theirs
    threads = await mailbox.list_threads(project, "alice", role="lead")
    assert any(t.last_from == f"{project}/alice/lead" for t in threads)


# -- mission 0016: expiry follows thread activity, not message age -------------


async def _age(mailbox: Mailbox, message_id: str, days: int) -> None:
    """Backdate a message so TTL behaviour can be exercised without waiting."""
    when = (datetime.now(tz=UTC) - timedelta(days=days)).isoformat()
    await mailbox._conn.execute(
        "UPDATE messages SET created = ? WHERE id = ?", (when, message_id)
    )
    await mailbox._conn.commit()


async def test_a_live_thread_is_never_decapitated(mailbox: Mailbox) -> None:
    """A conversation commented on today keeps its root, however old that root is.

    The reported failure: a 3-message thread rooted 20 days ago but commented on
    today was reduced to one survivor — "Re: DNS — still waiting on a human" — with
    no trace of the question it answered, and nothing signalling the loss.
    """
    project = _project()
    root = Message(
        from_=f"{project}/host", to="all", subject="Friction? Share it here", body="?"
    )
    await mailbox.send(root)
    old_reply = Message(
        from_=f"{project}/codex", to="all", thread=root.thread, subject="DNS", body="x"
    )
    await mailbox.send(old_reply)
    await _age(mailbox, root.id, 20)
    await _age(mailbox, old_reply.id, 20)

    # ...and someone comments TODAY
    fresh = Message(
        from_=f"{project}/claude",
        to="all",
        thread=root.thread,
        subject="Re: DNS",
        body="y",
    )
    await mailbox.send(fresh)

    await mailbox._purge_expired()

    surviving = await mailbox.thread(root.thread or root.id)
    assert {m.subject for m in surviving} == {
        "Friction? Share it here",
        "DNS",
        "Re: DNS",
    }, "a live conversation must keep its root and every turn"


async def test_a_quiet_thread_expires_whole(mailbox: Mailbox) -> None:
    """When nobody has spoken for ttl_days, the conversation goes entirely."""
    project = _project()
    root = Message(from_=f"{project}/a", to=f"{project}/b", subject="old", body="x")
    await mailbox.send(root)
    reply = Message(
        from_=f"{project}/b",
        to=f"{project}/a",
        thread=root.thread,
        subject="re",
        body="y",
    )
    await mailbox.send(reply)
    await mailbox.read(project, "b", root.id)  # leaves a broadcast_reads row
    await _age(mailbox, root.id, 30)
    await _age(mailbox, reply.id, 30)

    await mailbox._purge_expired()

    assert await mailbox.thread(root.thread or root.id) == []
    # read-state for the removed messages goes with them
    orphaned = await mailbox._scalar(
        "SELECT COUNT(*) FROM broadcast_reads WHERE message_id NOT IN "
        "(SELECT id FROM messages)"
    )
    assert orphaned == 0


async def test_ttl_zero_still_disables_expiry(mailbox: Mailbox) -> None:
    project = _project()
    config = mailbox._config.model_copy(update={"ttl_days": 0})
    msg = Message(from_=f"{project}/a", to=f"{project}/b", subject="ancient", body="x")
    await mailbox.send(msg)
    await _age(mailbox, msg.id, 3650)

    async with Mailbox(config) as never_expires:
        await never_expires._purge_expired()
        assert await never_expires.message_by_id(msg.id) is not None
