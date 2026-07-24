"""The hub's HTTP API (mission cli-primary-client).

The API is the hub's only machine interface once the hosted MCP transport is removed,
so these cover both the happy paths and the two properties that must not regress:
identity is never inferred from the path, and no route hands out the omniscient
whole-thread view that mission 0020 removed from agents.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_inbox.api import API_PREFIX, IDENTITY_HEADER, build_api
from agent_inbox.config import Config


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    config = Config().model_copy(update={"db": str(tmp_path / "api.db")})
    with TestClient(build_api(config)) as c:
        yield c


def _as(address: str) -> dict[str, str]:
    return {IDENTITY_HEADER: address}


def _send(client: TestClient, frm: str, to: str, body: str, **kw: object) -> dict:
    r = client.post(
        f"{API_PREFIX}/messages", json={"to": to, "body": body, **kw}, headers=_as(frm)
    )
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------ identity


def test_identity_header_is_required(client: TestClient) -> None:
    r = client.get(f"{API_PREFIX}/messages")
    assert r.status_code == 400
    assert IDENTITY_HEADER in r.json()["detail"]


def test_identity_must_name_a_specific_agent(client: TestClient) -> None:
    """You may address a whole project, but you cannot *be* one."""
    r = client.get(f"{API_PREFIX}/messages", headers=_as("someproject"))
    assert r.status_code == 400
    assert "specific agent" in r.json()["detail"]


def test_retired_any_keyword_is_rejected(client: TestClient) -> None:
    r = client.get(f"{API_PREFIX}/messages", headers=_as("proj/any"))
    assert r.status_code == 400
    assert "retired" in r.json()["detail"]


def test_ping_confirms_the_whole_path(client: TestClient) -> None:
    r = client.get(f"{API_PREFIX}/ping", headers=_as("proj/claude/agent"))
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["address"] == "proj/claude/agent"


# ---------------------------------------------------------------------- mail


def test_send_peek_read_cycle(client: TestClient) -> None:
    sent = _send(client, "proj/alice/agent", "proj/bob/agent", "here?", subject="ping")
    assert sent["from"] == "proj/alice/agent"

    listed = client.get(f"{API_PREFIX}/messages", headers=_as("proj/bob/agent")).json()
    assert [m["subject"] for m in listed] == ["ping"]

    # peeking did not consume
    again = client.get(f"{API_PREFIX}/messages", headers=_as("proj/bob/agent")).json()
    assert len(again) == 1

    got = client.post(
        f"{API_PREFIX}/messages/{sent['id']}/read", headers=_as("proj/bob/agent")
    )
    assert got.status_code == 200 and got.json()["body"] == "here?"
    assert (
        client.get(f"{API_PREFIX}/messages", headers=_as("proj/bob/agent")).json() == []
    )


def test_reading_someone_elses_mail_is_refused(client: TestClient) -> None:
    sent = _send(client, "proj/alice/agent", "proj/bob/agent", "private")
    r = client.post(
        f"{API_PREFIX}/messages/{sent['id']}/read", headers=_as("proj/eve/agent")
    )
    assert r.status_code == 404


def test_reply_threads_and_reaches_the_sender(client: TestClient) -> None:
    sent = _send(client, "proj/alice/agent", "proj/bob/agent", "q?", subject="question")
    r = client.post(
        f"{API_PREFIX}/messages/{sent['id']}/reply",
        json={"body": "yes"},
        headers=_as("proj/bob/agent"),
    )
    assert r.status_code == 200
    reply = r.json()
    assert reply["to"] == "proj/alice/agent"
    assert reply["thread"] == sent["thread"]
    assert reply["subject"].lower().startswith("re:")


def test_unread_count_reports_senders(client: TestClient) -> None:
    _send(client, "proj/alice/agent", "proj/bob/agent", "one")
    _send(client, "proj/carol/agent", "proj/bob/agent", "two")
    r = client.get(
        f"{API_PREFIX}/messages/unread", headers=_as("proj/bob/agent")
    ).json()
    assert r["count"] == 2
    assert set(r["senders"]) == {"proj/alice/agent", "proj/carol/agent"}


def test_broadcast_reaches_every_agent_on_the_project(client: TestClient) -> None:
    _send(client, "proj/sys/agent", "proj", "all hands", subject="notice")
    for who in ("proj/alice/agent", "proj/bob/agent"):
        listed = client.get(f"{API_PREFIX}/messages", headers=_as(who)).json()
        assert [m["subject"] for m in listed] == ["notice"]


def test_message_too_large_is_rejected_with_a_usable_error(tmp_path: Path) -> None:
    config = Config().model_copy(
        update={"db": str(tmp_path / "small.db"), "max_message_bytes": 200}
    )
    with TestClient(build_api(config)) as c:
        r = c.post(
            f"{API_PREFIX}/messages",
            json={"to": "p/b/agent", "body": "x" * 500},
            headers=_as("p/a/agent"),
        )
        assert r.status_code == 404  # MailboxError
        assert "too large" in r.json()["detail"]


# ------------------------------------------------------------------- threads


def test_read_thread_returns_only_the_callers_turns(client: TestClient) -> None:
    """Mission 0020: membership is per turn, not per thread."""
    root = _send(client, "proj/alice/agent", "proj", "standup at 10", subject="sync")
    thread = root["thread"]
    _send(
        client,
        "proj/alice/agent",
        "proj/bob/agent",
        "private one",
        subject="Re: sync",
        thread=thread,
    )

    eve = client.get(f"{API_PREFIX}/threads/{thread}", headers=_as("proj/eve/agent"))
    assert eve.status_code == 200
    assert [t["message"]["body"] for t in eve.json()] == ["standup at 10"]

    alice = client.get(
        f"{API_PREFIX}/threads/{thread}", headers=_as("proj/alice/agent")
    )
    assert [t["message"]["body"] for t in alice.json()] == [
        "standup at 10",
        "private one",
    ]


def test_unknown_thread_is_a_404(client: TestClient) -> None:
    r = client.get(f"{API_PREFIX}/threads/nope", headers=_as("proj/alice/agent"))
    assert r.status_code == 404


def test_no_route_exposes_the_omniscient_thread_view(client: TestClient) -> None:
    """`Mailbox.thread()` ignores who is asking — it backs the operator console only.

    Publishing it would re-open mission 0020 for every client at once, so this pins the
    omission. If a console-facing route is ever added it must carry its own
    authorisation, not ride in on the agent API.
    """
    paths = set(client.app.openapi()["paths"])  # type: ignore[attr-defined]
    assert f"{API_PREFIX}/threads/{{thread_id}}" in paths
    for path in paths:
        assert "raw" not in path and "all-turns" not in path


def test_list_threads_only_shows_the_callers_conversations(client: TestClient) -> None:
    _send(client, "proj/alice/agent", "proj/bob/agent", "hello", subject="a")
    mine = client.get(f"{API_PREFIX}/threads", headers=_as("proj/alice/agent")).json()
    assert len(mine) == 1
    assert (
        client.get(f"{API_PREFIX}/threads", headers=_as("proj/eve/agent")).json() == []
    )


# ----------------------------------------------------------------- directory


def test_register_whois_and_list(client: TestClient) -> None:
    r = client.put(
        f"{API_PREFIX}/agents/me",
        json={"profile": {"model": "opus", "offers": ["deploys"]}},
        headers=_as("proj/alice/agent"),
    )
    assert r.status_code == 200 and r.json()["address"] == "proj/alice/agent"

    who = client.get(f"{API_PREFIX}/agents/proj/alice").json()
    assert who["profile"]["offers"] == ["deploys"]

    assert client.get(f"{API_PREFIX}/agents/proj/nobody").status_code == 404
    assert any(
        a["address"] == "proj/alice/agent"
        for a in client.get(f"{API_PREFIX}/agents").json()
    )


def test_update_status_preserves_the_rest_of_the_profile(client: TestClient) -> None:
    client.put(
        f"{API_PREFIX}/agents/me",
        json={"profile": {"offers": ["x"]}},
        headers=_as("proj/alice/agent"),
    )
    r = client.post(
        f"{API_PREFIX}/agents/me/status",
        json={"status": "away"},
        headers=_as("proj/alice/agent"),
    )
    assert r.json()["profile"]["status"] == "away"
    assert r.json()["profile"]["offers"] == ["x"]


# ----------------------------------------------------------------------- hub


def test_hub_descriptor_is_public(client: TestClient) -> None:
    """No identity needed: this is how a client discovers limits before configuring."""
    r = client.get(f"{API_PREFIX}/hub")
    assert r.status_code == 200
    assert "version" in r.json()


def test_openapi_schema_is_published(client: TestClient) -> None:
    """The schema is the contract for clients we have not written yet."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert f"{API_PREFIX}/messages" in r.json()["paths"]
