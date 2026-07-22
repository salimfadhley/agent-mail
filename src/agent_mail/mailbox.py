"""The JetStream-backed mailbox — the single core all surfaces (CLI, MCP) share.

Addressing is two-part, ``<project>/<agent>``:

* **direct** ``project/agent`` -> that specific agent's inbox;
* **any** ``project`` (bare) -> exactly one agent on the project (a shared work queue);
* **broadcast** ``project/*`` -> a copy to every agent on the project.

Each agent keeps its **own** durable consumer (direct + broadcast, so broadcasts fan
out because every agent's consumer is independent) plus membership in a **shared**
per-project consumer for the anycast queue.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from types import TracebackType
from typing import Any

import nats
from nats.aio.client import Client as NatsClient
from nats.aio.msg import Msg
from nats.errors import NoServersError
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, StreamConfig
from nats.js.errors import NotFoundError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agent_mail.config import (
    STREAM_NAME,
    Config,
    any_durable,
    any_subject,
    broadcast_subject,
    direct_subject,
    format_address,
    notify_target_subject,
    own_durable,
    parse_target,
)
from agent_mail.exceptions import MailboxError
from agent_mail.models import Intent, Message

logger = logging.getLogger(__name__)

_STREAM_SUBJECTS = ["agent.mail.>"]

# How many messages a single fetch round asks for, and the overall ceiling when
# draining a mailbox. Comfortably above any realistic local agent backlog.
_FETCH_BATCH = 64
_FETCH_MAX = 1024
_FETCH_TIMEOUT = 1.0


def _connect_options(config: Config) -> dict[str, Any]:
    """Build nats.connect() kwargs for auth/TLS from config (all optional)."""
    opts: dict[str, Any] = {}
    if config.nats_creds_file:
        opts["user_credentials"] = config.nats_creds_file
    if config.nats_token:
        opts["token"] = config.nats_token
    if config.nats_user:
        opts["user"] = config.nats_user
    if config.nats_password:
        opts["password"] = config.nats_password
    if config.nats_ca_file:
        opts["tls"] = ssl.create_default_context(cafile=config.nats_ca_file)
    return opts


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
    retry=retry_if_exception_type((NoServersError, OSError)),
    reraise=True,
)
async def _connect(nats_url: str, **options: Any) -> NatsClient:
    """Open a NATS connection, retrying transient failures (server still booting)."""
    return await nats.connect(nats_url, **options)


class Mailbox:
    """Durable inbox + wake signals over NATS JetStream.

    Use as an async context manager::

        async with Mailbox(config) as mb:
            await mb.send(msg)

    Connection and stream/consumer creation are idempotent.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._nc: NatsClient | None = None
        self._js: JetStreamContext | None = None

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Open the NATS connection and ensure the mailbox stream exists."""
        if self._nc is not None:
            return
        logger.debug("connecting to NATS at %s", self._config.nats_url)
        self._nc = await _connect(
            self._config.nats_url, **_connect_options(self._config)
        )
        self._js = self._nc.jetstream()
        await self._ensure_stream()

    async def close(self) -> None:
        """Close the NATS connection (graceful drain, hard-close fallback)."""
        nc = self._nc
        if nc is not None:
            try:
                await asyncio.wait_for(nc.drain(), timeout=2.0)
            except Exception:
                if not nc.is_closed:
                    await nc.close()
            self._nc = None
            self._js = None

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
    def _stream(self) -> JetStreamContext:
        if self._js is None:
            raise MailboxError("mailbox is not connected; call connect() first")
        return self._js

    @property
    def _conn(self) -> NatsClient:
        if self._nc is None:
            raise MailboxError("mailbox is not connected; call connect() first")
        return self._nc

    async def _ensure_stream(self) -> None:
        """Create/widen the ``AGENT_MAIL`` stream to bind ``agent.mail.>``."""
        js = self._stream
        try:
            info = await js.stream_info(STREAM_NAME)
            if set(info.config.subjects or []) != set(_STREAM_SUBJECTS):
                info.config.subjects = list(_STREAM_SUBJECTS)
                await js.update_stream(info.config)
        except NotFoundError:
            logger.info("creating JetStream stream %s", STREAM_NAME)
            await js.add_stream(
                StreamConfig(name=STREAM_NAME, subjects=list(_STREAM_SUBJECTS))
            )

    async def _bind(
        self, durable: str, filters: list[str]
    ) -> JetStreamContext.PullSubscription:
        """Ensure a durable pull consumer with these filter subjects, and bind to it."""
        js = self._stream
        await js.add_consumer(
            STREAM_NAME,
            ConsumerConfig(
                durable_name=durable,
                filter_subjects=filters,
                ack_policy=AckPolicy.EXPLICIT,
            ),
        )
        return await js.pull_subscribe_bind(durable=durable, stream=STREAM_NAME)

    async def _inbox_subscriptions(
        self, project: str, agent: str
    ) -> list[JetStreamContext.PullSubscription]:
        """The agent's own consumer (direct + broadcast) and the shared any-queue."""
        own = await self._bind(
            own_durable(project, agent),
            [direct_subject(project, agent), broadcast_subject(project)],
        )
        shared = await self._bind(any_durable(project), [any_subject(project)])
        return [own, shared]

    @staticmethod
    async def _drain_pending(
        sub: JetStreamContext.PullSubscription,
    ) -> list[Msg]:
        """Fetch every currently-pending message without acking it."""
        collected: list[Msg] = []
        while len(collected) < _FETCH_MAX:
            try:
                batch = await sub.fetch(batch=_FETCH_BATCH, timeout=_FETCH_TIMEOUT)
            except NatsTimeoutError:
                break
            if not batch:
                break
            collected.extend(batch)
            if len(batch) < _FETCH_BATCH:
                break
        return collected

    # -- verbs -------------------------------------------------------------

    async def send(self, message: Message) -> Message:
        """Publish ``message`` to the subject resolved from its ``to`` address."""
        _, subject = parse_target(message.to)
        await self._stream.publish(subject, message.to_json_bytes())
        logger.debug("sent %s -> %s (%s)", message.from_, message.to, message.id)
        return message

    async def peek(self, project: str, agent: str) -> list[Message]:
        """Return unread messages for ``project/agent`` without consuming them."""
        messages: list[Message] = []
        for sub in await self._inbox_subscriptions(project, agent):
            try:
                for msg in await self._drain_pending(sub):
                    messages.append(Message.from_json_bytes(msg.data))
                    await msg.nak()
            finally:
                await sub.unsubscribe()
        messages.sort(key=lambda m: m.created)
        return messages

    async def read(self, project: str, agent: str, message_id: str) -> Message:
        """Return the message with ``message_id`` and ack (consume) it."""
        found: Message | None = None
        for sub in await self._inbox_subscriptions(project, agent):
            try:
                for msg in await self._drain_pending(sub):
                    parsed = Message.from_json_bytes(msg.data)
                    if found is None and parsed.id == message_id:
                        found = parsed
                        await msg.ack()
                    else:
                        await msg.nak()
            finally:
                await sub.unsubscribe()
        if found is None:
            raise MailboxError(
                f"no unread message with id {message_id!r} in {project}/{agent}'s inbox"
            )
        logger.debug("read %s from %s/%s", message_id, project, agent)
        return found

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
        """Publish a lightweight, non-durable 'you have mail' wake for ``to``."""
        payload = json.dumps({} if thread is None else {"thread": thread}).encode()
        await self._conn.publish(notify_target_subject(to), payload)
        await self._conn.flush()
        logger.debug("notified %s", to)

    async def ping(self, project: str, agent: str) -> Message:
        """Round-trip a probe to ``project/agent`` itself and consume it."""
        me = format_address(project, agent)
        probe = Message(from_=me, to=me, subject="agent-mail ping", body="ping")
        await self.send(probe)
        received = await self.read(project, agent, probe.id)
        logger.debug("ping round-trip ok for %s (%s)", me, probe.id)
        return received


def _reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"
