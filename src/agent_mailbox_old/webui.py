"""The human operator console — a small server-rendered web UI at ``/ui``.

Runs in the **same process** as the hosted MCP server (one uvicorn, one port, one
SQLite file). It presents the hub as email: a message list, a message view, threads,
a directory of agents, and a compose box.

The one rule that shapes everything — **observe vs. manage**:

* Observing *any* mailbox is **read-only**. Screens query the store with the
  ``Mailbox.browse``/``thread``/``stats`` SELECT helpers, which never ack or consume —
  so watching an agent's inbox can't steal its mail.
* The **operator's own** inbox (:attr:`Config.operator`, default ``agent-inbox/human``)
  is the one mailbox that is interactive: the operator reads and replies there, and
  compose sends *as* the operator.

Rendering (jinja2 + a markdown lib) lives behind the optional ``agent-inbox[ui]``
extra and is imported lazily, so the base install and the MCP tools are unaffected.
"""

from __future__ import annotations

import html
import json
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from agent_mailbox_old.config import Config, format_address, hub_version, parse_target
from agent_mailbox_old.exceptions import AgentInboxError
from agent_mailbox_old.mailbox import Mailbox
from agent_mailbox_old.models import Message

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

_CONTENT_TYPES = {
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}

# Time windows offered on the flow graph (query key -> label, hours; None = all).
_WINDOWS: list[tuple[str, str]] = [
    ("1h", "1h"),
    ("24h", "24h"),
    ("7d", "7d"),
    ("all", "all"),
]
_WINDOW_HOURS: dict[str, int | None] = {"1h": 1, "24h": 24, "7d": 168, "all": None}

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


class UIExtraMissing(AgentInboxError):
    """Raised when the console is reached but the ``[ui]`` extra isn't installed."""


@lru_cache(maxsize=1)
def _jinja_env() -> Any:
    """Build the Jinja environment lazily (needs the ``[ui]`` extra)."""
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised via has_ui()
        raise UIExtraMissing(
            "the web console needs the optional dependencies: "
            "pip install 'agent-inbox[ui]'"
        ) from exc
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["markdown"] = _render_markdown
    env.filters["shortdate"] = _shortdate
    env.filters["snippet"] = _snippet
    env.globals["title_of"] = subject_or_snippet
    return env


def has_ui() -> bool:
    """Whether the optional ``[ui]`` extra (jinja2 + markdown) is importable."""
    try:
        import jinja2  # noqa: F401
        import markdown  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _render_markdown(text: str) -> Any:
    from markdown import markdown as to_html

    try:
        from markupsafe import Markup
    except ModuleNotFoundError:  # pragma: no cover - markupsafe ships with jinja2
        Markup = str  # type: ignore[assignment]
    return Markup(to_html(text or "", extensions=["fenced_code", "tables"]))


def _shortdate(value: datetime | str) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    return value.strftime("%Y-%m-%d %H:%M")


def _snippet(text: str, limit: int = 80) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def subject_or_snippet(message: Message) -> str:
    """A display title: the subject, or a body snippet when none was given."""
    if message.subject:
        return message.subject
    snippet = _snippet(message.body, 60)
    return f"({snippet})" if snippet else "(no subject)"


# -- the console ----------------------------------------------------------------


class WebConsole:
    """ASGI handler for everything under ``/ui``.

    Instantiated once per server with the resolved :class:`Config`; each request opens
    its own short-lived :class:`Mailbox` connection.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        project, agent = self._split(config.operator)
        self._op_project = project
        self._op_agent = agent

    @staticmethod
    def _split(address: str) -> tuple[str, str]:
        kind, project, agent = parse_target(address)
        if kind != "direct" or project is None or agent is None:
            # Fall back to a sane operator identity rather than crash the server.
            return "agent-inbox", "human"
        return project, agent

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "/ui")
        method = scope.get("method", "GET")
        try:
            if not has_ui():
                await self._error(
                    send,
                    503,
                    "Web console unavailable",
                    "This build was installed without the console dependencies. "
                    "Install them with <code>pip install 'agent-inbox[ui]'</code>.",
                )
                return
            await self._route(scope, receive, send, path, method)
        except Exception:  # process-boundary backstop; log and show a 500
            logger.exception("console error handling %s %s", method, path)
            await self._error(
                send, 500, "Console error", "Something went wrong rendering this page."
            )

    async def _route(
        self, scope: Scope, receive: Receive, send: Send, path: str, method: str
    ) -> None:
        rel = path[len("/ui") :].strip("/")
        parts = rel.split("/") if rel else []

        if not parts:
            await self._dashboard(send)
        elif parts == ["agents"]:
            await self._agents(send)
        elif parts == ["mbox"] and len(parts) == 1:
            await self._redirect(send, "/ui/agents")
        elif parts[0] == "mbox" and len(parts) in (3, 4):
            await self._mailbox(
                send, parts[1], parts[2], parts[3] if len(parts) == 4 else None
            )
        elif parts[0] == "msg" and len(parts) == 2:
            await self._message(send, parts[1])
        elif parts == ["compose"]:
            if method == "POST":
                await self._do_compose(scope, receive, send)
            else:
                await self._compose_form(send)
        elif parts == ["inbox"]:
            await self._inbox(send)
        elif parts == ["inbox", "read"] and method == "POST":
            await self._do_read(scope, receive, send)
        elif parts == ["inbox", "reply"] and method == "POST":
            await self._do_reply(scope, receive, send)
        elif parts == ["prompts"]:
            await self._prompts(send)
        elif parts == ["flow"]:
            await self._flow(scope, send)
        elif parts == ["flow", "edge"]:
            await self._flow_edge(scope, send)
        elif parts[0] == "static" and len(parts) == 2:
            await self._static(send, parts[1])
        elif parts == ["status"]:
            await self._status(send)
        elif parts == ["doctor"]:
            await self._doctor(send)
        else:
            await self._error(send, 404, "Not found", f"No console page at {path!r}.")

    # -- screens (read-only observatory) ----------------------------------

    async def _dashboard(self, send: Send) -> None:
        async with Mailbox(self._config) as mb:
            stats = await mb.stats()
            agents = await mb.list_agents()
        await self._render(
            send,
            "dashboard.html",
            stats=stats,
            agents=agents,
            peak=max((n for _, n in stats.per_day), default=0),
        )

    async def _agents(self, send: Send) -> None:
        async with Mailbox(self._config) as mb:
            agents = await mb.list_agents()
        await self._render(send, "agents.html", agents=agents)

    async def _mailbox(
        self, send: Send, project: str, agent: str, role: str | None = None
    ) -> None:
        try:
            format_address(project, agent, role)
        except AgentInboxError:
            await self._error(send, 400, "Bad address", "That is not a valid mailbox.")
            return
        async with Mailbox(self._config) as mb:
            items = await mb.browse(project, agent, role)
            info = await mb.whois(project, agent, role)
        interactive = (project, agent) == (self._op_project, self._op_agent)
        await self._render(
            send,
            "mailbox.html",
            project=project,
            agent=agent,
            role=role,
            address=format_address(project, agent, role),
            items=items,
            info=info,
            interactive=interactive,
        )

    async def _message(self, send: Send, message_id: str) -> None:
        async with Mailbox(self._config) as mb:
            message = await mb.message_by_id(message_id)
            if message is None:
                await self._error(
                    send, 404, "No such message", "That message id was not found."
                )
                return
            thread = await mb.thread(message.thread or message.id)
        await self._render(send, "message.html", message=message, thread=thread)

    async def _flow(self, scope: Scope, send: Send) -> None:
        since_key = _query(scope).get("since", "24h")
        cutoff, label = _since_window(since_key)
        async with Mailbox(self._config) as mb:
            graph = await mb.flow_graph(cutoff)
        online = set(graph.online)
        vis_nodes = [
            {
                "id": addr,
                "label": addr.replace("/", "/\n", 1),
                "online": addr in online,
            }
            for addr in graph.nodes
        ]
        vis_edges = [{"from": e.frm, "to": e.to, "count": e.count} for e in graph.edges]
        await self._render(
            send,
            "flow.html",
            nodes_json=json.dumps(vis_nodes),
            edges_json=json.dumps(vis_edges),
            edge_count=len(graph.edges),
            node_count=len(graph.nodes),
            broadcast_count=graph.broadcast_count,
            since_key=since_key,
            since_label=label,
            windows=_WINDOWS,
        )

    async def _flow_edge(self, scope: Scope, send: Send) -> None:
        q = _query(scope)
        frm, to = (q.get("from") or "").strip(), (q.get("to") or "").strip()
        cutoff, label = _since_window(q.get("since", "24h"))
        if not frm or not to:
            await self._error(send, 400, "Bad request", "Need both from and to.")
            return
        async with Mailbox(self._config) as mb:
            messages = await mb.messages_between(frm, to, cutoff)
        await self._render(
            send,
            "flow_edge.html",
            frm=frm,
            to=to,
            since_label=label,
            messages=messages,
        )

    async def _prompts(self, send: Send) -> None:
        """The prompt catalog, for humans: read it, and copy it out in one click."""
        from agent_mailbox_old.prompts import list_prompts, render_prompt, render_short

        entries = []
        for meta in list_prompts():
            body = render_prompt(meta["name"], self._config)
            if body is None:  # pragma: no cover - listed implies renderable
                continue
            entries.append(
                {
                    **meta,
                    "body": body,
                    "short": render_short(meta["name"], self._config),
                }
            )
        await self._render(
            send,
            "prompts.html",
            entries=entries,
            base_url=self._config.base_url(),
            version=hub_version(),
        )

    async def _static(self, send: Send, name: str) -> None:
        # Serve a vendored asset. Reject anything path-y (no traversal).
        if not name or "/" in name or "\\" in name or name.startswith("."):
            await self._error(send, 404, "Not found", "No such asset.")
            return
        path = _STATIC_DIR / name
        if not path.is_file():
            await self._error(send, 404, "Not found", f"No asset {name!r}.")
            return
        ctype = _CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        await _raw(send, path.read_bytes(), ctype)

    async def _inbox(self, send: Send) -> None:
        async with Mailbox(self._config) as mb:
            items = await mb.browse(self._op_project, self._op_agent)
        await self._render(
            send,
            "inbox.html",
            address=format_address(self._op_project, self._op_agent),
            items=items,
        )

    # -- interactive (operator only) --------------------------------------

    async def _compose_form(
        self, send: Send, error: str | None = None, to: str = ""
    ) -> None:
        async with Mailbox(self._config) as mb:
            agents = await mb.list_agents()
        # Suggest every known agent, plus the broadcast/anycast form per project, so
        # the To field auto-completes to a valid address.
        suggestions: list[str] = []
        seen: set[str] = set()
        for a in agents:
            for cand in (a.address, f"{a.project}/all", f"{a.project}/any"):
                if cand not in seen:
                    seen.add(cand)
                    suggestions.append(cand)
        await self._render(
            send,
            "compose.html",
            operator=format_address(self._op_project, self._op_agent),
            error=error,
            to=to,
            suggestions=suggestions,
        )

    async def _do_compose(self, scope: Scope, receive: Receive, send: Send) -> None:
        form = await _read_form(receive)
        to = (form.get("to") or "").strip()
        body = form.get("body") or ""
        subject = (form.get("subject") or "").strip() or None
        if not to or not body.strip():
            await self._compose_form(
                send, error="Both a recipient and a body are required.", to=to
            )
            return
        message = Message(
            from_=format_address(self._op_project, self._op_agent),
            to=to,
            subject=subject,
            body=body,
        )
        try:
            async with Mailbox(self._config) as mb:
                await mb.touch(self._op_project, self._op_agent)
                await mb.send(message)
        except AgentInboxError as exc:
            await self._compose_form(send, error=str(exc), to=to)
            return
        await self._redirect(send, f"/ui/mbox/{self._op_project}/{self._op_agent}")

    async def _do_read(self, scope: Scope, receive: Receive, send: Send) -> None:
        form = await _read_form(receive)
        message_id = (form.get("message_id") or "").strip()
        try:
            async with Mailbox(self._config) as mb:
                await mb.read(self._op_project, self._op_agent, message_id)
        except AgentInboxError:
            pass  # already consumed / gone — fall through to the inbox
        await self._redirect(send, "/ui/inbox")

    async def _do_reply(self, scope: Scope, receive: Receive, send: Send) -> None:
        form = await _read_form(receive)
        message_id = (form.get("message_id") or "").strip()
        body = form.get("body") or ""
        subject = (form.get("subject") or "").strip() or None
        if body.strip():
            try:
                async with Mailbox(self._config) as mb:
                    await mb.reply(
                        self._op_project, self._op_agent, message_id, body, subject
                    )
            except AgentInboxError:
                pass
        await self._redirect(send, "/ui/inbox")

    # -- diagnostics ------------------------------------------------------

    async def _status(self, send: Send) -> None:
        async with Mailbox(self._config) as mb:
            stats = await mb.stats()
        await self._render(
            send,
            "status.html",
            stats=stats,
            version=hub_version(),
            base_url=self._config.base_url(),
            db=self._config.db,
            ttl_days=self._config.ttl_days,
            operator=format_address(self._op_project, self._op_agent),
            now=datetime.now(tz=UTC),
        )

    async def _doctor(self, send: Send) -> None:
        checks: list[tuple[str, bool, str]] = []
        ok_ui = has_ui()
        checks.append(("console dependencies ([ui] extra)", ok_ui, "jinja2 + markdown"))
        try:
            async with Mailbox(self._config) as mb:
                await mb.stats()
            checks.append(("database reachable", True, self._config.db))
        except Exception as exc:  # noqa: BLE001 - report, don't crash the page
            checks.append(("database reachable", False, str(exc)))
        checks.append(
            (
                "public_url set (needed behind a proxy)",
                self._config.public_url is not None,
                self._config.public_url or "(unset — using host:port)",
            )
        )
        await self._render(send, "doctor.html", checks=checks)

    # -- rendering helpers ------------------------------------------------

    async def _render(self, send: Send, template: str, **context: Any) -> None:
        env = _jinja_env()
        tmpl = env.get_template(template)
        body = tmpl.render(nav=_NAV, hub_name=self._config.hub_name, **context)
        await _html(send, body)

    async def _redirect(self, send: Send, location: str) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 303,
                "headers": [(b"location", location.encode())],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    async def _error(self, send: Send, status: int, title: str, detail: str) -> None:
        page = (
            f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"<body style='font-family:system-ui;max-width:40rem;margin:3rem auto'>"
            f"<h1>{html.escape(title)}</h1><p>{detail}</p>"
            f"<p><a href='/ui'>← console</a></p>"
        )
        await _html(send, page, status=status)


_NAV = [
    ("/ui", "Dashboard"),
    ("/ui/agents", "Agents"),
    ("/ui/flow", "Flow"),
    ("/ui/inbox", "My inbox"),
    ("/ui/compose", "Compose"),
    ("/ui/prompts", "Prompts"),
    ("/ui/status", "Status"),
]


def _query(scope: Scope) -> dict[str, str]:
    """Parse the request query string into a flat ``{key: first-value}`` dict."""
    parsed = parse_qs(scope.get("query_string", b"").decode(), keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items()}


def _since_window(key: str) -> tuple[str | None, str]:
    """Map a window key (1h/24h/7d/all) to ``(iso_cutoff | None, label)``."""
    hours = _WINDOW_HOURS.get(key, 24)
    if hours is None:
        return None, "all time"
    cutoff = (datetime.now(tz=UTC) - timedelta(hours=hours)).isoformat()
    return cutoff, f"last {key}"


async def _read_form(receive: Receive) -> dict[str, str]:
    """Read and urldecode an ``application/x-www-form-urlencoded`` request body."""
    body = b""
    while True:
        event = await receive()
        body += event.get("body", b"")
        if not event.get("more_body"):
            break
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items()}


async def _html(send: Send, body: str, status: int = 200) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/html; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body.encode("utf-8")})


async def _raw(send: Send, body: bytes, content_type: str) -> None:
    """Send raw bytes with a content type + long cache (vendored, versioned assets)."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", content_type.encode()),
                (b"cache-control", b"public, max-age=31536000, immutable"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
