# Mission brief — human web UI (in-process, same server)

**Status:** planned · **Kind:** additive (hosted server) · **Depends on:** [0004](0004-presence-discovery.md) for the live agent browser only

## What

A human-facing web UI served by the **same process** as the hosted MCP server — so an
operator can watch the traffic and join in. No second process, no second port: the UI
shares the one uvicorn and the one SQLite connection (which sidesteps any cross-process
SQLite concern).

Chosen approach (**Option A**): **server-rendered HTML + a little htmx**, added as
Starlette routes to the existing ASGI app. Minimal dependency footprint (`jinja2`; htmx
is one static JS file). No NiceGUI/Streamlit, no websockets. Ships as an optional extra
`agent-inbox[ui]`, imported lazily; the base CLI/library stays light and the Docker
image installs `.[ui]`.

## Why

Agents already message each other; a human wants to observe usage, read what's in each
mailbox, and occasionally send a message in — without hand-relaying through an agent.

## Routes / screens

- **`/`** — dashboard: general usage stats (volume over time, totals; **bounded by
  `ttl_days`**, which is fine — we don't care about weeks-old history) and per-agent
  sent/received counts. The machine hub descriptor moves to **`/hub`** (already an
  alias); optionally content-negotiate `/` so `Accept: application/json` still returns it.
- **`/ui/mbox/<project>/<agent>`** — an email-like view of one mailbox: list messages
  with unread/read status (from `acked_at` for direct/any, `broadcast_reads` for
  broadcast/public), click a message to read its full content.
- **`/ui/compose`** — write a message to any agent/target and send it. Sent **from a
  configurable operator identity**, `AGENT_MAIL_OPERATOR` (default `agent_inbox/human`)
  — the human addresses *to* agents, no impersonation.
- **`/status`, `/doctor`** — HTML versions of the existing health/config checks.
- **Agent browser** — a list of connected agents with activity. **Deferred until 0004**
  (presence): until then we can only list agents seen in message history, so the real
  "who's here" view comes with the `presence` table.

## Design

- A read-only `views`/`stats` module of plain SQL SELECTs over the existing schema
  (`messages`, `broadcast_reads`) — the testable core. Writes go through the existing
  async `Mailbox.send`.
- Compose the ASGI app: a parent app hosts the UI routes and mounts the MCP app (with
  its identity middleware) at `/<project>/<agent>/mcp` unchanged.
- MCP paths and behavior are untouched; the UI is purely additive.

## Config

- `AGENT_MAIL_UI` — enable the UI (default on for the http server).
- `AGENT_MAIL_OPERATOR` — the From identity for human-sent messages (default
  `agent_inbox/human`).
- **Security: deferred.** No auth in this mission (trusted-network assumption). Revisit
  before exposing beyond a trusted LAN — this UI reads every mailbox and can send.

## Definition of done

- The http server serves the dashboard, a mailbox view, compose, and status/doctor from
  one process; MCP still works alongside.
- Read/stat queries are unit-tested against a temp SQLite db; pages smoke-render.
- `[ui]` optional extra; lazy import; Dockerfile installs it; docs updated.

## Non-goals

- Auth / multi-user accounts (deferred). - A second process or port. - Long-term stats
  beyond `ttl_days`. - The live agent browser (that lands with 0004).
