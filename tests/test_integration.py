"""End-to-end mailbox round-trips against a real (temp-file) SQLite store.

No external services and no gating: the SQLite backend needs nothing but a file,
so these run in normal CI.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from agent_mail.config import Config
from agent_mail.exceptions import ConfigError, MailboxError
from agent_mail.mailbox import Mailbox
from agent_mail.models import AgentProfile, Intent, Message


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


async def test_any_delivers_to_exactly_one_agent(mailbox: Mailbox) -> None:
    project = _project()
    await mailbox.send(
        Message(from_=f"{project}/sys", to=f"{project}/any", subject="task", body="x")
    )

    # both see the unclaimed task
    pending = [m for m in await mailbox.peek(project, "alice") if m.subject == "task"]
    assert pending
    assert any(m.subject == "task" for m in await mailbox.peek(project, "bob"))

    # alice claims it
    await mailbox.read(project, "alice", pending[0].id)

    # bob no longer sees it, and can't claim it either — consumed exactly once
    assert not any(m.subject == "task" for m in await mailbox.peek(project, "bob"))
    with pytest.raises(MailboxError):
        await mailbox.read(project, "bob", pending[0].id)


async def test_public_broadcast_reaches_agents_across_projects(
    mailbox: Mailbox,
) -> None:
    await mailbox.send(
        Message(from_="ops/sys", to="all/all", subject="townhall", body="hi all")
    )
    # agents on unrelated projects all see it, and each consumes its own copy
    a = await mailbox.peek("proj-a", "alice")
    b = await mailbox.peek("proj-b", "bob")
    assert any(m.subject == "townhall" for m in a)
    assert any(m.subject == "townhall" for m in b)
    await mailbox.read("proj-a", "alice", a[0].id)
    a2 = await mailbox.peek("proj-a", "alice")
    assert not any(m.subject == "townhall" for m in a2)
    assert any(m.subject == "townhall" for m in await mailbox.peek("proj-b", "bob"))


async def test_global_any_delivers_to_one_agent_anywhere(mailbox: Mailbox) -> None:
    await mailbox.send(
        Message(from_="ops/sys", to="any/any", subject="whoever", body="grab me")
    )
    a = [m for m in await mailbox.peek("proj-a", "alice") if m.subject == "whoever"]
    assert a
    await mailbox.read("proj-a", "alice", a[0].id)
    # nobody else, on any project, can still claim it
    assert not any(m.subject == "whoever" for m in await mailbox.peek("proj-b", "bob"))
    with pytest.raises(MailboxError):
        await mailbox.read("proj-b", "bob", a[0].id)


async def test_ping_roundtrip(mailbox: Mailbox) -> None:
    project = _project()
    received = await mailbox.ping(project, "alice")
    assert received.subject == "agent-mail ping"
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
