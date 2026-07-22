"""Live round-trip against real JetStream. Gated behind AGENT_MAIL_INTEGRATION=1.

Run with::

    AGENT_MAIL_INTEGRATION=1 NATS_URL=nats://your-nats:4222 \
        uv run pytest tests/test_integration.py
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from agent_mail.config import Config
from agent_mail.mailbox import Mailbox
from agent_mail.models import Intent, Message

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_MAIL_INTEGRATION") != "1",
    reason="set AGENT_MAIL_INTEGRATION=1 to run live JetStream tests",
)


def _project() -> str:
    return f"itest-{uuid4().hex[:8]}"


async def test_direct_send_peek_read_reply() -> None:
    project = _project()
    async with Mailbox(Config.from_env()) as mb:
        original = Message(
            from_=f"{project}/alice", to=f"{project}/bob", subject="ping", body="here?"
        )
        await mb.send(original)

        # peek does not consume
        assert any(m.id == original.id for m in await mb.peek(project, "bob"))
        assert any(m.id == original.id for m in await mb.peek(project, "bob"))

        # read consumes
        got = await mb.read(project, "bob", original.id)
        assert got.body == "here?"
        assert await mb.peek(project, "bob") == []

        # reply lands directly in alice's inbox, threaded
        second = Message(
            from_=f"{project}/alice", to=f"{project}/bob", subject="q", body="q"
        )
        await mb.send(second)
        reply = await mb.reply(project, "bob", second.id, "yes")
        assert reply.intent is Intent.reply
        assert reply.to == f"{project}/alice"
        assert any(m.id == reply.id for m in await mb.peek(project, "alice"))
        await mb.read(project, "alice", reply.id)


async def test_broadcast_reaches_every_agent() -> None:
    project = _project()
    async with Mailbox(Config.from_env()) as mb:
        # create both agents' consumers first, so the broadcast fans out to them
        await mb.peek(project, "alice")
        await mb.peek(project, "bob")

        await mb.send(
            Message(from_=f"{project}/sys", to=f"{project}/*", subject="all", body="hi")
        )
        assert any(m.subject == "all" for m in await mb.peek(project, "alice"))
        assert any(m.subject == "all" for m in await mb.peek(project, "bob"))


async def test_any_delivers_to_exactly_one_agent() -> None:
    project = _project()
    async with Mailbox(Config.from_env()) as mb:
        await mb.peek(project, "alice")
        await mb.peek(project, "bob")

        await mb.send(
            Message(from_=f"{project}/sys", to=project, subject="task", body="do it")
        )

        # alice grabs it from the shared queue
        pending = [m for m in await mb.peek(project, "alice") if m.subject == "task"]
        assert pending
        await mb.read(project, "alice", pending[0].id)

        # bob no longer sees it — it was consumed once
        assert not any(m.subject == "task" for m in await mb.peek(project, "bob"))


async def test_ping_roundtrip() -> None:
    project = _project()
    async with Mailbox(Config.from_env()) as mb:
        received = await mb.ping(project, "alice")
        assert received.subject == "agent-mail ping"
        assert await mb.peek(project, "alice") == []


async def test_notify_publishes() -> None:
    async with Mailbox(Config.from_env()) as mb:
        await mb.notify(f"{_project()}/x")  # fire-and-forget; success = no raise
