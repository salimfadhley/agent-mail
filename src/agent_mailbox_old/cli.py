"""The ``agent-inbox`` command-line primitive."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
from collections.abc import Awaitable, Callable, Sequence
from typing import NoReturn

import click

from agent_mailbox_old.config import Config, format_address, hub_descriptor, parse_target
from agent_mailbox_old.config_env import set_runtime_config_path
from agent_mailbox_old.exceptions import ConfigError, MailboxError
from agent_mailbox_old.mailbox import Mailbox
from agent_mailbox_old.models import AgentInfo, AgentProfile, Intent, Message

logger = logging.getLogger(__name__)


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _fail(message: str) -> NoReturn:
    click.echo(f"error: {message}", err=True)
    raise SystemExit(1)


async def _with_mailbox[T](
    config: Config, action: Callable[[Mailbox], Awaitable[T]]
) -> T:
    async with Mailbox(config) as mailbox:
        return await action(mailbox)


async def _reachable(_mailbox: Mailbox) -> bool:
    """No-op action; reaching it means the db opened and the schema is ready."""
    return True


def _message_dict(message: Message) -> dict[str, object]:
    return message.model_dump(by_alias=True, mode="json")


def _print_message_human(message: Message, *, full: bool) -> None:
    click.echo(f"[{message.id}] {message.intent.value}")
    click.echo(f"  from:    {message.from_}")
    click.echo(f"  to:      {message.to}")
    click.echo(f"  thread:  {message.thread}")
    click.echo(f"  subject: {message.subject or '(none)'}")
    click.echo(f"  created: {message.created.isoformat()}")
    if full:
        click.echo("  ---")
        for line in message.body.splitlines() or [""]:
            click.echo(f"  {line}")


def _emit(payload: object, *, as_json: bool, human: Callable[[], None]) -> None:
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        human()


@click.group()
@click.option(
    "--project",
    default=None,
    help="Your project (overrides AGENT_INBOX_PROJECT).",
)
@click.option(
    "--from",
    "from_",
    default=None,
    help="Your agent name (overrides AGENT_ID).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of human text.",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    envvar="AGENT_INBOX_CONFIG",
    help="Path to a TOML config file (env vars still override it).",
)
@click.pass_context
def cli(
    ctx: click.Context,
    project: str | None,
    from_: str | None,
    as_json: bool,
    config_path: str | None,
) -> None:
    """A mailbox for LLM agents — send, receive and reply between agents.

    Addresses are `<project>/<agent>/<role>`, and each position narrows
    independently. A literal matches only itself; `all`, `*` and an empty position
    all mean "every value". Every matching agent gets its own copy.

    \b
      proj/claude          that one agent
      proj/claude/system   that agent in that role
      proj                 every agent on the project
      //host               whoever holds the `host` role, anywhere
      all                  everyone, everywhere

    The `any` keyword was retired — address an agent or a whole project instead.
    """
    ctx.ensure_object(dict)
    set_runtime_config_path(config_path)
    config = Config.from_env(agent_override=from_, project_override=project)
    logging.getLogger("agent_mailbox_old").setLevel(config.log_level.upper())
    ctx.obj["config"] = config
    ctx.obj["as_json"] = as_json


@cli.command()
@click.option(
    "--to",
    required=True,
    help="project/agent · project or project/all · project/any · all/all · any/any",
)
@click.option(
    "--subject",
    default=None,
    help="Message subject (optional but encouraged — makes the message readable).",
)
@click.option("--body", required=True, help="Message body.")
@click.option("--thread", default=None, help="Thread id to attach to (optional).")
@click.option(
    "--intent",
    type=click.Choice([i.value for i in Intent]),
    default=Intent.message.value,
    help="Message intent.",
)
@click.pass_context
def send(
    ctx: click.Context,
    to: str,
    subject: str | None,
    body: str,
    thread: str | None,
    intent: str,
) -> None:
    """Send a message to an agent, any agent on a project, or all of them."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["as_json"]
    try:
        project, agent = config.require_address()
        message = Message(
            from_=format_address(project, agent),
            to=to,
            subject=subject,
            body=body,
            thread=thread,
            intent=Intent(intent),
        )
        _run(_with_mailbox(config, lambda mb: mb.send(message)))
    except (ConfigError, MailboxError) as exc:
        _fail(str(exc))
    logger.info("sent %s to %s", message.id, message.to)
    _emit(
        _message_dict(message),
        as_json=as_json,
        human=lambda: click.echo(f"sent [{message.id}] to {message.to}"),
    )


@cli.command()
@click.pass_context
def inbox(ctx: click.Context) -> None:
    """List unread messages addressed to me (peek — does not ack)."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["as_json"]
    try:
        project, agent = config.require_address()
        messages = _run(_with_mailbox(config, lambda mb: mb.peek(project, agent)))
    except (ConfigError, MailboxError) as exc:
        _fail(str(exc))
        return

    logger.info("inbox for %s/%s: %d unread", project, agent, len(messages))

    def human() -> None:
        if not messages:
            click.echo("inbox empty")
            return
        click.echo(f"{len(messages)} unread message(s):")
        for message in messages:
            _print_message_human(message, full=False)

    _emit([_message_dict(m) for m in messages], as_json=as_json, human=human)


@cli.command()
@click.argument("message_id")
@click.pass_context
def read(ctx: click.Context, message_id: str) -> None:
    """Show a message and ack it (consume)."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["as_json"]
    try:
        project, agent = config.require_address()
        message = _run(
            _with_mailbox(config, lambda mb: mb.read(project, agent, message_id))
        )
    except (ConfigError, MailboxError) as exc:
        _fail(str(exc))
        return
    logger.info("read %s", message_id)
    _emit(
        _message_dict(message),
        as_json=as_json,
        human=lambda: _print_message_human(message, full=True),
    )


@cli.command()
@click.argument("message_id")
@click.option("--body", required=True, help="Reply body.")
@click.option("--subject", default=None, help="Override the reply subject.")
@click.pass_context
def reply(ctx: click.Context, message_id: str, body: str, subject: str | None) -> None:
    """Reply on the same thread and ack the original."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["as_json"]
    try:
        project, agent = config.require_address()
        message = _run(
            _with_mailbox(
                config, lambda mb: mb.reply(project, agent, message_id, body, subject)
            )
        )
    except (ConfigError, MailboxError) as exc:
        _fail(str(exc))
        return
    logger.info("replied %s on thread %s", message.id, message.thread)
    _emit(
        _message_dict(message),
        as_json=as_json,
        human=lambda: click.echo(
            f"replied [{message.id}] to {message.to} on thread {message.thread}"
        ),
    )


@cli.command()
@click.option("--to", required=True, help="Recipient agent id.")
@click.option("--thread", default=None, help="Thread the wake refers to (optional).")
@click.pass_context
def notify(ctx: click.Context, to: str, thread: str | None) -> None:
    """Publish a lightweight 'you have mail' wake signal (non-durable)."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["as_json"]
    try:
        _run(_with_mailbox(config, lambda mb: mb.notify(to, thread)))
    except (ConfigError, MailboxError) as exc:
        _fail(str(exc))
    logger.info("notified %s", to)
    _emit(
        {"notified": to, "thread": thread},
        as_json=as_json,
        human=lambda: click.echo(f"notified {to}"),
    )


@cli.command()
@click.pass_context
def ping(ctx: click.Context) -> None:
    """Round-trip a message to yourself to check agent-inbox is operational."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["as_json"]
    try:
        project, agent = config.require_address()
        me = format_address(project, agent)
        start = time.perf_counter()
        received = _run(_with_mailbox(config, lambda mb: mb.ping(project, agent)))
        roundtrip_ms = round((time.perf_counter() - start) * 1000, 1)
    except (ConfigError, MailboxError) as exc:
        logger.warning("ping failed: %s", exc)
        if as_json:
            click.echo(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            click.echo(f"ping FAILED: {exc}", err=True)
        raise SystemExit(1) from exc
    logger.info("ping ok for %s in %sms", me, roundtrip_ms)
    _emit(
        {
            "ok": True,
            "agent": me,
            "message_id": received.id,
            "roundtrip_ms": roundtrip_ms,
        },
        as_json=as_json,
        human=lambda: click.echo(f"ok — round-trip for {me} in {roundtrip_ms}ms"),
    )


@cli.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Validate configuration and storage; print effective config."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["as_json"]

    db_ok = True
    db_error: str | None = None
    try:
        _run(_with_mailbox(config, _reachable))
    except Exception as exc:  # process boundary: report, don't crash
        db_ok = False
        db_error = str(exc)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "ok": db_ok,
                    "config": config.redacted(),
                    "storage": {"backend": "sqlite", "ready": db_ok, "error": db_error},
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
        )
    else:
        click.echo(f"hub:       {config.hub_name}")
        click.echo(f"db:        {config.db}")
        click.echo(f"transport: {config.transport}")
        click.echo(f"agent_id:  {config.agent_id or '(unset — hosted / multi-agent)'}")
        status = "✅ ready" if db_ok else f"❌ {db_error or 'unavailable'}"
        click.echo(f"storage:   {status}")
    if not db_ok:
        raise SystemExit(1)


@cli.command(name="hub-info")
@click.pass_context
def hub_info_cmd(ctx: click.Context) -> None:
    """Show this hub's public self-description (name, limits, connect URL, admin)."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["as_json"]
    try:
        max_size = _run(_with_mailbox(config, lambda mb: mb.max_message_size()))
    except Exception:  # process boundary: still show the static descriptor
        max_size = None
    descriptor = hub_descriptor(config, max_message_bytes=max_size)
    if as_json:
        click.echo(json.dumps(descriptor, indent=2, sort_keys=True))
    else:
        for key, value in descriptor.items():
            click.echo(f"{key}: {value}")


def _print_agent_human(info: AgentInfo) -> None:
    dot = "online" if info.online else "offline"
    p = info.profile
    click.echo(f"{info.address}  [{p.status}, {dot}]")
    if p.model:
        click.echo(f"  model:   {p.model}")
    if p.charter_summary:
        click.echo(f"  role:    {p.charter_summary}")
    if p.offers:
        click.echo(f"  offers:  {', '.join(p.offers)}")
    if p.needs:
        click.echo(f"  needs:   {', '.join(p.needs)}")
    if p.objective:
        click.echo(f"  doing:   {p.objective}")
    click.echo(f"  last:    {info.last_seen.isoformat()}")


@cli.command()
@click.option("--project", "project_filter", default=None, help="Only this project.")
@click.pass_context
def agents(ctx: click.Context, project_filter: str | None) -> None:
    """List agents in the directory: who's here, online, and what they do."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["as_json"]
    try:
        entries = _run(_with_mailbox(config, lambda mb: mb.list_agents(project_filter)))
    except (ConfigError, MailboxError) as exc:
        _fail(str(exc))

    def human() -> None:
        if not entries:
            click.echo("no agents registered")
            return
        click.echo(f"{len(entries)} agent(s):")
        for a in entries:
            dot = "online " if a.online else "offline"
            role = a.profile.charter_summary or ", ".join(a.profile.offers)
            click.echo(f"  [{dot}] {a.address}  ({a.profile.status})  {role}")

    _emit([a.model_dump(mode="json") for a in entries], as_json=as_json, human=human)


@cli.command()
@click.argument("address")
@click.pass_context
def whois(ctx: click.Context, address: str) -> None:
    """Show one agent's directory card. ADDRESS is project/agent."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["as_json"]
    try:
        kind, project, agent = parse_target(address)
        if kind != "direct" or project is None or agent is None:
            _fail(f"whois needs a specific project/agent, got {address!r}")
        info = _run(_with_mailbox(config, lambda mb: mb.whois(project, agent)))
    except (ConfigError, MailboxError) as exc:
        _fail(str(exc))
    if info is None:
        _emit(
            {"found": False, "address": address},
            as_json=as_json,
            human=lambda: click.echo(f"{address}: not in the directory"),
        )
        return
    _emit(
        info.model_dump(mode="json"),
        as_json=as_json,
        human=lambda: _print_agent_human(info),
    )


@cli.command()
@click.option("--model", default=None, help="Engine, e.g. claude-opus, openai-codex.")
@click.option("--status", default="available", help="available | busy | away.")
@click.option("--offers", default=None, help="Comma-separated capabilities.")
@click.option("--needs", default=None, help="Comma-separated help wanted.")
@click.option("--working-dir", "working_dir", default=None)
@click.option("--hostname", default=None)
@click.option("--platform", default=None, help='CPU/arch, e.g. "Apple M3 (arm64)".')
@click.option("--ide", default=None, help="jetbrains | vscode | none.")
@click.option("--objective", default=None, help="Current objective.")
@click.option("--charter", "charter", default=None, help="One-line role/purpose.")
@click.option("--human", default=None, help="The human you work for.")
@click.pass_context
def register(
    ctx: click.Context,
    model: str | None,
    status: str,
    offers: str | None,
    needs: str | None,
    working_dir: str | None,
    hostname: str | None,
    platform: str | None,
    ide: str | None,
    objective: str | None,
    charter: str | None,
    human: str | None,
) -> None:
    """Register/refresh my profile in the directory."""
    config: Config = ctx.obj["config"]
    as_json: bool = ctx.obj["as_json"]
    profile = AgentProfile(
        model=model,
        status=status,
        offers=[s.strip() for s in offers.split(",") if s.strip()] if offers else [],
        needs=[s.strip() for s in needs.split(",") if s.strip()] if needs else [],
        working_dir=working_dir,
        hostname=hostname,
        platform=platform,
        ide=ide,
        objective=objective,
        charter_summary=charter,
        human=human,
    )
    try:
        project, agent = config.require_address()
        info = _run(
            _with_mailbox(config, lambda mb: mb.register(project, agent, profile))
        )
    except (ConfigError, MailboxError) as exc:
        _fail(str(exc))
    _emit(
        info.model_dump(mode="json"),
        as_json=as_json,
        human=lambda: click.echo(f"registered {info.address}"),
    )


@cli.command(name="mcp-serve")
@click.option(
    "--transport",
    type=click.Choice(["stdio", "http"]),
    default=None,
    envvar="AGENT_INBOX_TRANSPORT",
    help="MCP transport: stdio (local, single agent) or http (hosted, multi-agent).",
)
@click.option(
    "--host",
    default=None,
    envvar="AGENT_INBOX_HOST",
    help="Bind host for http transport (default 127.0.0.1).",
)
@click.option(
    "--port",
    type=int,
    default=None,
    envvar="AGENT_INBOX_PORT",
    help="Bind port for http transport (default 8080).",
)
@click.option(
    "--path",
    "path_",
    default=None,
    envvar="AGENT_INBOX_PATH",
    help="Mount path for http transport (default /mcp).",
)
@click.pass_context
def mcp_serve(
    ctx: click.Context,
    transport: str | None,
    host: str | None,
    port: int | None,
    path_: str | None,
) -> None:
    """Run the MCP server exposing the same verbs as tools.

    Over http the server is multi-agent: each agent connects on its own address,
    ``http://<host>:<port>/<agent>/mcp``, which is its whole configuration.
    """
    from agent_mailbox_old.mcp_server import serve

    base: Config = ctx.obj["config"]
    updates: dict[str, object] = {}
    if transport:
        updates["transport"] = transport
    if host:
        updates["host"] = host
    if port is not None:
        updates["port"] = port
    if path_:
        updates["path"] = path_
    config = base.model_copy(update=updates) if updates else base
    serve(config)


@cli.command("hook-check")
@click.option(
    "--url",
    default=None,
    envvar="AGENT_INBOX_HOOK_URL",
    help="Hub URL for this agent. Accepts the same .../mcp URL you gave your MCP "
    "client; the probe endpoint is derived from it. Omit to read a local database.",
)
@click.option(
    "--event",
    default="PostToolBatch",
    help="Hook event name to echo back in the JSON (must match the event you "
    "registered the hook on).",
)
@click.option("--timeout", default=2.0, help="Give up after this many seconds.")
@click.pass_context
def hook_check(ctx: click.Context, url: str | None, event: str, timeout: float) -> None:
    """Emit a Claude Code hook payload if this agent has unread mail (else nothing).

    Designed to run on every beat of an agent's loop, so it is deliberately dull:
    one cheap count, no bodies, and it prints **only** well-formed hook JSON —
    stray stdout would break Claude Code's parsing. It exits 0 whatever happens,
    because a mail check must never be able to stall the agent it serves.
    """
    message: str | None = None
    try:
        count, senders = _unread_snapshot(ctx, url, timeout)
        if count:
            who = ", ".join(sorted(set(senders))[:3]) or "another agent"
            plural = "s" if count != 1 else ""
            message = (
                f"You have {count} unread agent-inbox message{plural} from {who}. "
                "Call `check_inbox` to read them before continuing."
            )
    except Exception:  # never let a mail probe break the loop it runs inside
        logger.debug("hook-check failed", exc_info=True)
        message = None

    if message:
        click.echo(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": event,
                        "additionalContext": message,
                    }
                }
            )
        )
    ctx.exit(0)


def _unread_snapshot(
    ctx: click.Context, url: str | None, timeout: float
) -> tuple[int, list[str]]:
    """Return ``(unread_count, senders)`` from the hub over HTTP, or a local db.

    Remote is the common case: a hosted agent's mail lives on the hub, and reading
    some unrelated local SQLite file would silently report zero forever.
    """
    if url:
        probe = url.rstrip("/")
        if probe.endswith("/mcp"):
            probe = probe[: -len("/mcp")]
        probe = f"{probe}/unread"
        with urllib.request.urlopen(probe, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return int(payload.get("unread", 0)), list(payload.get("from", []))

    config: Config = ctx.obj["config"]
    project, agent = config.require_address()

    async def _count() -> tuple[int, list[str]]:
        async with Mailbox(config) as mailbox:
            return await mailbox.unread_count(project, agent)

    return _run(_count())


def _setup_logging() -> None:
    level = os.environ.get("AGENT_INBOX_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Console entry point."""
    _setup_logging()
    cli.main(args=list(argv) if argv is not None else None, standalone_mode=True)


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:])
