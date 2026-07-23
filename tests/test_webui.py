"""The human web console: read-only observatory + interactive operator inbox.

These exercise the read-only ``Mailbox`` view helpers (the testable core) and drive
the ``WebConsole`` ASGI app directly with fabricated request scopes — no server, no
external services, just a temp-file SQLite db.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest_asyncio

from agent_inbox.config import Config
from agent_inbox.mailbox import Mailbox
from agent_inbox.mcp_server import AgentIdentityMiddleware
from agent_inbox.models import AgentProfile, Message
from agent_inbox.webui import WebConsole, has_ui, subject_or_snippet


@pytest_asyncio.fixture
async def env(tmp_path: Path) -> AsyncIterator[tuple[Config, Mailbox]]:
    config = Config().model_copy(
        update={"db": str(tmp_path / "console.db"), "transport": "http", "ui": True}
    )
    async with Mailbox(config) as mb:
        yield config, mb


def _project() -> str:
    return f"ui-{uuid4().hex[:8]}"


async def _asgi(
    app: Any,
    method: str,
    path: str,
    *,
    body: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, Any]:
    """Drive an ASGI app once and collect the response."""
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers or [],
    }
    events = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, Any]:
        return events.pop(0) if events else {"type": "http.request", "body": b""}

    out: dict[str, Any] = {"status": None, "headers": [], "chunks": []}

    async def send(msg: dict[str, Any]) -> None:
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = msg["headers"]
        elif msg["type"] == "http.response.body":
            out["chunks"].append(msg.get("body", b""))

    await app(scope, receive, send)
    out["body"] = b"".join(out["chunks"]).decode("utf-8")
    return out


def _location(resp: dict[str, Any]) -> str:
    for key, value in resp["headers"]:
        if key == b"location":
            return value.decode()
    return ""


def test_ui_extra_is_installed_in_tests() -> None:
    # The dev group ships jinja2 + markdown so these tests can render.
    assert has_ui() is True


async def test_browse_is_read_only_never_consumes(
    env: tuple[Config, Mailbox],
) -> None:
    config, mb = env
    project = _project()
    msg = Message(
        from_=f"{project}/alice", to=f"{project}/bob", subject="hi", body="yo"
    )
    await mb.send(msg)

    console = WebConsole(config)
    resp = await _asgi(console, "GET", f"/ui/mbox/{project}/bob")
    assert resp["status"] == 200
    assert "hi" in resp["body"]

    # Observing must NOT have consumed the message — bob still sees it unread.
    still_there = await mb.peek(project, "bob")
    assert any(m.id == msg.id for m in still_there)


async def test_missing_subject_falls_back_to_body_snippet(
    env: tuple[Config, Mailbox],
) -> None:
    config, mb = env
    project = _project()
    msg = Message(
        from_=f"{project}/alice",
        to=f"{project}/bob",
        subject=None,
        body="a body with no subject line",
    )
    await mb.send(msg)
    assert msg.subject is None
    assert subject_or_snippet(msg).startswith("(a body with no subject")

    console = WebConsole(config)
    resp = await _asgi(console, "GET", f"/ui/mbox/{project}/bob")
    assert "a body with no subject" in resp["body"]


async def test_dashboard_and_agents_render(env: tuple[Config, Mailbox]) -> None:
    config, mb = env
    project = _project()
    await mb.register(project, "alice", AgentProfile(offers=["math"], needs=["ui"]))
    await mb.send(
        Message(from_=f"{project}/alice", to=f"{project}/bob", subject="s", body="b")
    )
    console = WebConsole(config)

    dash = await _asgi(console, "GET", "/ui")
    assert dash["status"] == 200
    assert "dashboard" in dash["body"].lower()

    agents = await _asgi(console, "GET", "/ui/agents")
    assert agents["status"] == 200
    assert f"{project}/alice" in agents["body"]


async def test_message_view_shows_thread(env: tuple[Config, Mailbox]) -> None:
    config, mb = env
    project = _project()
    first = Message(
        from_=f"{project}/alice", to=f"{project}/bob", subject="start", body="hello"
    )
    await mb.send(first)
    console = WebConsole(config)
    resp = await _asgi(console, "GET", f"/ui/msg/{first.id}")
    assert resp["status"] == 200
    assert "hello" in resp["body"]


async def test_compose_sends_as_operator(env: tuple[Config, Mailbox]) -> None:
    config, mb = env
    project = _project()
    console = WebConsole(config)
    form = f"to={project}/bob&subject=hey&body=hello+there".encode()
    resp = await _asgi(
        console,
        "POST",
        "/ui/compose",
        body=form,
        headers=[(b"content-type", b"application/x-www-form-urlencoded")],
    )
    assert resp["status"] == 303
    assert _location(resp) == "/ui/mbox/agent-inbox/human"

    delivered = await mb.peek(project, "bob")
    assert len(delivered) == 1
    assert delivered[0].from_ == "agent-inbox/human"
    assert delivered[0].subject == "hey"


async def test_operator_inbox_read_consumes(env: tuple[Config, Mailbox]) -> None:
    config, mb = env
    msg = Message(
        from_="proj/alice", to="agent-inbox/human", subject="for you", body="hi human"
    )
    await mb.send(msg)
    console = WebConsole(config)

    inbox = await _asgi(console, "GET", "/ui/inbox")
    assert "for you" in inbox["body"]

    resp = await _asgi(
        console,
        "POST",
        "/ui/inbox/read",
        body=f"message_id={msg.id}".encode(),
        headers=[(b"content-type", b"application/x-www-form-urlencoded")],
    )
    assert resp["status"] == 303
    # The operator's own inbox IS interactive: reading consumes it.
    assert await mb.peek("agent-inbox", "human") == []


async def test_status_and_doctor_render(env: tuple[Config, Mailbox]) -> None:
    config, _ = env
    console = WebConsole(config)

    status = await _asgi(console, "GET", "/ui/status")
    assert status["status"] == 200
    assert "Status" in status["body"]
    assert "agent-inbox/human" in status["body"]  # operator shown

    doctor = await _asgi(console, "GET", "/ui/doctor")
    assert doctor["status"] == 200
    assert "Diagnostics" in doctor["body"]


async def test_unknown_page_is_404(env: tuple[Config, Mailbox]) -> None:
    config, _ = env
    console = WebConsole(config)
    resp = await _asgi(console, "GET", "/ui/nope")
    assert resp["status"] == 404


async def test_browser_root_redirects_to_ui(env: tuple[Config, Mailbox]) -> None:
    config, _ = env

    async def noop(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover
        return None

    mw = AgentIdentityMiddleware(
        app=noop,
        mount_path="/mcp",
        hub_json=b'{"hub":"x"}',
        config=config,
        web=WebConsole(config),
    )
    # A browser (Accept: text/html) is redirected to the console…
    browser = await _asgi(mw, "GET", "/", headers=[(b"accept", b"text/html")])
    assert browser["status"] == 303
    assert _location(browser) == "/ui"

    # …while a machine still gets the JSON hub descriptor.
    machine = await _asgi(mw, "GET", "/", headers=[(b"accept", b"application/json")])
    assert machine["status"] == 200
    assert machine["body"] == '{"hub":"x"}'

    # And /ui is delegated to the console.
    ui = await _asgi(mw, "GET", "/ui")
    assert ui["status"] == 200
    assert "dashboard" in ui["body"].lower()
