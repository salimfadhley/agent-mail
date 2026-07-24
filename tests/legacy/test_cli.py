"""CLI tests with NATS faked — exercises arg parsing and output, no live server."""

from __future__ import annotations

import json
from types import TracebackType

import pytest
from click.testing import CliRunner

from agent_mailbox_old import cli as cli_module
from agent_mailbox_old.models import Intent, Message


class FakeMailbox:
    """Records calls and returns canned data in place of the real Mailbox."""

    calls: list[tuple[str, tuple[object, ...]]] = []
    peek_result: list[Message] = []
    read_result: Message | None = None

    def __init__(self, config: object) -> None:
        self.config = config

    async def __aenter__(self) -> FakeMailbox:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def send(self, message: Message) -> Message:
        FakeMailbox.calls.append(("send", (message,)))
        return message

    async def peek(self, project: str, agent: str) -> list[Message]:
        FakeMailbox.calls.append(("peek", (project, agent)))
        return FakeMailbox.peek_result

    async def read(self, project: str, agent: str, message_id: str) -> Message:
        FakeMailbox.calls.append(("read", (project, agent, message_id)))
        assert FakeMailbox.read_result is not None
        return FakeMailbox.read_result

    async def reply(
        self,
        project: str,
        agent: str,
        message_id: str,
        body: str,
        subject: str | None = None,
    ) -> Message:
        FakeMailbox.calls.append(("reply", (project, agent, message_id, body, subject)))
        return Message(
            from_=f"{project}/{agent}",
            to="peer/x",
            subject=subject or "Re: x",
            body=body,
            intent=Intent.reply,
        )

    async def notify(self, to: str, thread: str | None = None) -> None:
        FakeMailbox.calls.append(("notify", (to, thread)))

    async def ping(self, project: str, agent: str) -> Message:
        FakeMailbox.calls.append(("ping", (project, agent)))
        me = f"{project}/{agent}"
        return Message(from_=me, to=me, subject="agent-inbox ping", body="ping")

    async def max_message_size(self) -> int:
        FakeMailbox.calls.append(("max_message_size", ()))
        return 1048576


@pytest.fixture(autouse=True)
def patch_mailbox(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeMailbox.calls = []
    FakeMailbox.peek_result = []
    FakeMailbox.read_result = None
    monkeypatch.setattr(cli_module, "Mailbox", FakeMailbox)
    monkeypatch.setenv("AGENT_INBOX_PROJECT", "proj")
    monkeypatch.setenv("AGENT_ID", "tester")


def run(*args: str) -> object:
    return CliRunner().invoke(cli_module.cli, list(args))


def test_send_happy_path() -> None:
    result = run("send", "--to", "peer/bob", "--subject", "hi", "--body", "there")
    assert result.exit_code == 0, result.output
    assert "sent" in result.output
    kind, payload = FakeMailbox.calls[-1]
    assert kind == "send"
    sent = payload[0]
    assert isinstance(sent, Message)
    assert sent.to == "peer/bob" and sent.from_ == "proj/tester"


def test_send_to_project_any() -> None:
    result = run("send", "--to", "peer", "--subject", "hi", "--body", "b")
    assert result.exit_code == 0, result.output
    sent = FakeMailbox.calls[-1][1][0]
    assert isinstance(sent, Message)
    assert sent.to == "peer"


def test_send_json_output() -> None:
    result = run("--json", "send", "--to", "peer/bob", "--subject", "hi", "--body", "b")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["from"] == "proj/tester"
    assert data["to"] == "peer/bob"


def test_send_rejects_bad_intent() -> None:
    result = run(
        "send", "--to", "p", "--subject", "s", "--body", "b", "--intent", "nope"
    )
    assert result.exit_code != 0
    assert "nope" in result.output or "Invalid value" in result.output


def test_send_requires_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_ID", raising=False)
    result = run("send", "--to", "p", "--subject", "s", "--body", "b")
    assert result.exit_code == 1
    assert "agent name" in result.output


def test_send_requires_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_INBOX_PROJECT", raising=False)
    result = run("send", "--to", "p", "--subject", "s", "--body", "b")
    assert result.exit_code == 1
    assert "project" in result.output


def test_inbox_empty() -> None:
    result = run("inbox")
    assert result.exit_code == 0, result.output
    assert "inbox empty" in result.output
    assert FakeMailbox.calls[-1] == ("peek", ("proj", "tester"))


def test_inbox_lists_messages() -> None:
    FakeMailbox.peek_result = [
        Message(from_="peer/a", to="proj/tester", subject="s1", body="b1", id="id-1"),
    ]
    result = run("inbox")
    assert result.exit_code == 0, result.output
    assert "id-1" in result.output
    assert "1 unread" in result.output


def test_read_shows_and_acks() -> None:
    FakeMailbox.read_result = Message(
        from_="peer/a", to="proj/tester", subject="s", body="hello world", id="abc"
    )
    result = run("read", "abc")
    assert result.exit_code == 0, result.output
    assert "hello world" in result.output
    assert FakeMailbox.calls[-1] == ("read", ("proj", "tester", "abc"))


def test_reply_threads() -> None:
    result = run("reply", "abc", "--body", "roger")
    assert result.exit_code == 0, result.output
    assert "replied" in result.output
    kind, payload = FakeMailbox.calls[-1]
    assert kind == "reply"
    assert payload[:4] == ("proj", "tester", "abc", "roger")


def test_notify() -> None:
    result = run("notify", "--to", "peer/bob")
    assert result.exit_code == 0, result.output
    assert "notified peer/bob" in result.output
    assert FakeMailbox.calls[-1] == ("notify", ("peer/bob", None))


def test_doctor_reports_ready() -> None:
    result = run("doctor")
    assert result.exit_code == 0, result.output
    assert "ready" in result.output


def test_hub_info_shows_hub_name_and_limit() -> None:
    result = run("hub-info")
    assert result.exit_code == 0, result.output
    assert "agent-inbox" in result.output
    assert "1048576" in result.output  # max_message_bytes from the fake mailbox


def test_ping_ok() -> None:
    result = run("ping")
    assert result.exit_code == 0, result.output
    assert "ok" in result.output
    assert FakeMailbox.calls[-1] == ("ping", ("proj", "tester"))


def test_ping_json_reports_ok() -> None:
    result = run("--json", "ping")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["agent"] == "proj/tester"


def test_flags_override_identity() -> None:
    result = run(
        "--project",
        "otherproj",
        "--from",
        "other",
        "send",
        "--to",
        "p/x",
        "--subject",
        "s",
        "--body",
        "b",
    )
    assert result.exit_code == 0, result.output
    sent = FakeMailbox.calls[-1][1][0]
    assert isinstance(sent, Message)
    assert sent.from_ == "otherproj/other"
