"""Unit tests for the Message pydantic model."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from agent_mailbox_old.models import Intent, Message


def test_defaults_are_populated() -> None:
    msg = Message(from_="a", to="b", subject="hi", body="there")
    assert msg.id
    assert msg.intent is Intent.message
    assert isinstance(msg.created, datetime)
    # a brand-new message threads on its own id
    assert msg.thread == msg.id


def test_from_alias_on_construction_and_serialisation() -> None:
    by_alias = Message(**{"from": "a", "to": "b", "subject": "s", "body": "x"})
    by_name = Message(from_="a", to="b", subject="s", body="x")
    assert by_alias.from_ == by_name.from_ == "a"
    dumped = by_alias.model_dump(by_alias=True)
    assert dumped["from"] == "a"
    assert "from_" not in dumped


def test_json_roundtrip_preserves_fields() -> None:
    original = Message(
        from_="sys",
        to="casework",
        subject="corpus stale?",
        body="please reindex",
        thread="t-1",
        intent=Intent.reply,
    )
    restored = Message.from_json_bytes(original.to_json_bytes())
    assert restored == original
    assert restored.thread == "t-1"
    assert restored.intent is Intent.reply


def test_explicit_thread_is_kept() -> None:
    msg = Message(from_="a", to="b", subject="s", body="x", thread="thread-42")
    assert msg.thread == "thread-42"


def test_invalid_intent_rejected() -> None:
    with pytest.raises(ValidationError):
        Message(from_="a", to="b", subject="s", body="x", intent="bogus")  # type: ignore[arg-type]
