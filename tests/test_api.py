"""The API end to end, over HTTP.

The engine is already tested exhaustively; what is checked here is translation — that
AS2 goes in and out intact, that errors become honest statuses, and that no messaging
decision has quietly migrated up into this layer.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
from litestar.testing import TestClient

from agent_mailbox import api as api_module
from agent_mailbox.api import IDENTITY_HEADER, build_api
from agent_mailbox.house import House
from agent_mailbox.mailbox import Mailbox
from agent_mailbox.store import InMemoryStore

HUB = "http://hub.invalid"
ROSEMARY = "rosemary_nasrin"
TREVOR = "trevor_mahmood"
YITZHAK = "yitzhak_levin"


@pytest.fixture
def client() -> Iterator[TestClient]:
    house = House(Mailbox(InMemoryStore(), hub_name="testhub"))
    with TestClient(app=build_api(house, HUB)) as c:
        yield c


def as_(name: str) -> dict[str, str]:
    return {IDENTITY_HEADER: name}


def join(client: TestClient, name: str) -> dict:
    r = client.post("/actors", json={"preferredUsername": name})
    assert r.status_code == 201, r.text
    return r.json()


def note(to: list[str], content: str, **kw: object) -> dict:
    return {
        "@context": "https://www.w3.org/ns/activitystreams",
        "type": "Create",
        "object": {"type": "Note", "to": to, "content": content, **kw},
    }


class TestHub:
    def test_the_hub_describes_itself(self, client: TestClient) -> None:
        body = client.get("/").json()
        assert body["type"] == "Service"
        assert body["name"] == "testhub"
        assert body["federates"] is False

    def test_it_says_out_loud_that_it_does_not_authenticate(
        self, client: TestClient
    ) -> None:
        """A hub that quietly does not authenticate is worse than one that says so."""
        body = client.get("/").json()
        assert body["authenticated"] is False
        assert "does not authenticate" in body["note"]

    def test_health_answers_without_the_store(self, client: TestClient) -> None:
        assert client.get("/health").json() == {"status": "ok"}


class TestIdentity:
    def test_a_missing_name_is_refused_with_advice(self, client: TestClient) -> None:
        r = client.get(f"/actors/{ROSEMARY}/inbox")
        assert r.status_code == 400
        assert IDENTITY_HEADER in r.json()["detail"]

    def test_an_unknown_caller_is_a_404(self, client: TestClient) -> None:
        r = client.get("/actors/ghost/inbox", headers=as_("ghost"))
        assert r.status_code == 404
        assert r.json()["code"] == "unknown_actor"


class TestActors:
    def test_joining_returns_an_actor_document(self, client: TestClient) -> None:
        actor = join(client, ROSEMARY)
        assert actor["preferredUsername"] == ROSEMARY
        assert actor["id"] == f"{HUB}/actors/{ROSEMARY}"
        assert actor["inbox"] == f"{HUB}/actors/{ROSEMARY}/inbox"
        assert actor["type"] == "Service"

    def test_joining_without_a_name_is_issued_one(self, client: TestClient) -> None:
        actor = client.post("/actors", json={}).json()
        assert "_" in actor["preferredUsername"]

    def test_a_taken_name_is_a_conflict(self, client: TestClient) -> None:
        join(client, ROSEMARY)
        r = client.post("/actors", json={"preferredUsername": ROSEMARY})
        assert r.status_code == 409
        assert r.json()["code"] == "name_unavailable"

    def test_the_directory_lists_everyone(self, client: TestClient) -> None:
        join(client, ROSEMARY)
        join(client, TREVOR)
        body = client.get("/actors").json()
        names = {a["preferredUsername"] for a in body["items"]}
        assert {ROSEMARY, TREVOR} <= names
        assert body["totalItems"] == len(body["items"])

    def test_standing_residents_are_present(self, client: TestClient) -> None:
        """admin and host exist before anyone joins."""
        names = {a["preferredUsername"] for a in client.get("/actors").json()["items"]}
        assert {"admin", "host"} <= names

    def test_an_unknown_actor_is_a_404(self, client: TestClient) -> None:
        assert client.get("/actors/nobody").status_code == 404


class TestMail:
    def test_the_whole_cycle(self, client: TestClient) -> None:
        join(client, ROSEMARY)
        join(client, TREVOR)

        sent = client.post(
            f"/actors/{ROSEMARY}/outbox",
            json=note([TREVOR], "one run in five", summary="flaky tests"),
            headers=as_(ROSEMARY),
        )
        assert sent.status_code == 201, sent.text
        posted = sent.json()
        assert posted["attributedTo"] == f"{HUB}/actors/{ROSEMARY}"
        assert posted["to"] == [f"{HUB}/actors/{TREVOR}"]
        assert posted["summary"] == "flaky tests"

        waiting = client.get(f"/actors/{TREVOR}/inbox", headers=as_(TREVOR)).json()[
            "items"
        ]
        assert [n["summary"] for n in waiting] == ["flaky tests"]

        again = client.get(f"/actors/{TREVOR}/inbox", headers=as_(TREVOR)).json()
        assert again["totalItems"] == 1, "peeking must not consume"

        object_id = posted["id"]
        read = client.post(
            f"/objects/{object_id.rsplit('/', 1)[-1]}/read", headers=as_(TREVOR)
        )
        assert read.status_code == 200
        assert (
            client.get(f"/actors/{TREVOR}/inbox", headers=as_(TREVOR)).json()[
                "totalItems"
            ]
            == 0
        )

    def test_a_bare_note_is_accepted_as_well_as_a_create(
        self, client: TestClient
    ) -> None:
        """A client posting what it means should not need to know AS2 wraps it."""
        join(client, ROSEMARY)
        join(client, TREVOR)
        r = client.post(
            f"/actors/{ROSEMARY}/outbox",
            json={"type": "Note", "to": [TREVOR], "content": "unwrapped"},
            headers=as_(ROSEMARY),
        )
        assert r.status_code == 201, r.text

    def test_actor_uris_are_accepted_as_recipients(self, client: TestClient) -> None:
        """An agent that read an actor document will send the URI back."""
        join(client, ROSEMARY)
        join(client, TREVOR)
        r = client.post(
            f"/actors/{ROSEMARY}/outbox",
            json=note([f"{HUB}/actors/{TREVOR}"], "by uri"),
            headers=as_(ROSEMARY),
        )
        assert r.status_code == 201, r.text
        assert (
            client.get(f"/actors/{TREVOR}/inbox", headers=as_(TREVOR)).json()[
                "totalItems"
            ]
            == 1
        )

    def test_an_unknown_recipient_is_refused(self, client: TestClient) -> None:
        join(client, ROSEMARY)
        r = client.post(
            f"/actors/{ROSEMARY}/outbox",
            json=note(["nobody_here"], "typo"),
            headers=as_(ROSEMARY),
        )
        assert r.status_code == 422
        assert r.json()["code"] == "unknown_recipient"

    def test_another_mailbox_is_refused_differently(self, client: TestClient) -> None:
        join(client, ROSEMARY)
        r = client.post(
            f"/actors/{ROSEMARY}/outbox",
            json=note(["someone@another_hub"], "abroad"),
            headers=as_(ROSEMARY),
        )
        assert r.status_code == 422
        assert r.json()["code"] == "remote_mailbox"

    def test_reading_someone_elses_mail_is_a_404(self, client: TestClient) -> None:
        join(client, ROSEMARY)
        join(client, TREVOR)
        join(client, YITZHAK)
        sent = client.post(
            f"/actors/{ROSEMARY}/outbox",
            json=note([TREVOR], "private"),
            headers=as_(ROSEMARY),
        ).json()
        ident = sent["id"].rsplit("/", 1)[-1]
        r = client.post(f"/objects/{ident}/read", headers=as_(YITZHAK))
        assert r.status_code == 404
        assert r.json()["code"] == "no_such_message"


class TestThreads:
    def test_a_thread_shows_only_your_turns(self, client: TestClient) -> None:
        """Mission 0020, over HTTP."""
        for who in (ROSEMARY, TREVOR, YITZHAK):
            join(client, who)

        opening = client.post(
            f"/actors/{ROSEMARY}/outbox",
            json=note(["everyone"], "pipeline down"),
            headers=as_(ROSEMARY),
        ).json()
        root = opening["id"].rsplit("/", 1)[-1]

        client.post(
            f"/actors/{ROSEMARY}/outbox",
            json=note([TREVOR], "between us", inReplyTo=opening["id"]),
            headers=as_(ROSEMARY),
        )

        bystander = client.get(f"/objects/{root}/thread", headers=as_(YITZHAK)).json()
        assert [n["content"] for n in bystander["items"]] == ["pipeline down"]

        participant = client.get(f"/objects/{root}/thread", headers=as_(TREVOR)).json()
        assert participant["totalItems"] == 2

    def test_an_unknown_thread_is_a_404(self, client: TestClient) -> None:
        join(client, ROSEMARY)
        r = client.get("/objects/nope/thread", headers=as_(ROSEMARY))
        assert r.status_code == 404


class TestForeignProperties:
    def test_unknown_as2_properties_survive(self, client: TestClient) -> None:
        """ADR 0006: preserve what we do not understand.

        msgspec structs drop unmodelled fields, so the body is decoded twice. This is
        the test that catches it if that second decode is ever dropped.
        """
        join(client, ROSEMARY)
        join(client, TREVOR)
        body = note([TREVOR], "hello")
        body["object"]["sensitive"] = True
        body["object"]["x:mood"] = "cheerful"
        body["object"]["tag"] = [{"type": "Hashtag", "name": "#ops"}]

        sent = client.post(
            f"/actors/{ROSEMARY}/outbox", json=body, headers=as_(ROSEMARY)
        )
        assert sent.status_code == 201, sent.text

        ident = sent.json()["id"].rsplit("/", 1)[-1]
        assert client.get(f"/objects/{ident}", headers=as_(TREVOR)).status_code == 200

        # and the extras really are in the store, not merely accepted and dropped
        import asyncio

        mailbox = client.app.state.api.house.mailbox  # type: ignore[attr-defined]
        record = asyncio.run(mailbox.view(TREVOR, ident))
        assert record.document["x:mood"] == "cheerful"
        assert record.document["sensitive"] is True
        assert record.document["tag"] == [{"type": "Hashtag", "name": "#ops"}]


class TestFederationIsAbsent:
    def test_the_federation_inbox_says_not_yet(self, client: TestClient) -> None:
        r = client.post(f"/actors/{ROSEMARY}/inbox", json={})
        assert r.status_code == 501
        assert "does not federate" in r.json()["detail"]


class TestNoLogicHere:
    """NFR-001: the API translates and does not decide."""

    def test_the_api_never_imports_the_rules(self) -> None:
        """A convenience shortcut here is how a second door opens (mission 0028)."""
        source = Path(api_module.__file__).read_text()
        imported = {
            node.module.split(".")[-1]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "rules" not in imported, "messaging decisions belong below this layer"

    def test_the_api_never_builds_a_record(self) -> None:
        """Constructing an ObjectRecord here would bypass send() and its policies."""
        source = Path(api_module.__file__).read_text()
        assert "ObjectRecord(" not in source
        assert "ActorRecord(" not in source
