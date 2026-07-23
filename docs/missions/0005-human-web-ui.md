# Mission brief — human web UI (in-process operator console)

**Status:** planned (design locked) · **Kind:** additive (hosted server) · **Depends on:** [0004](0004-presence-discovery.md) ✅ (the directory)

## What

A human-facing **operator console** served by the **same process** as the hosted MCP
server — one uvicorn, one port, one SQLite connection (no cross-process concern). It's a
**general-purpose** console: the operator might be *directing* the system, *debugging*
it, or just *curious how it works* — so it supports **watch + do + explore**, not one
narrow role.

Chosen approach (**Option A**): **server-rendered HTML + a little htmx**. Minimal deps
(`jinja2`, a markdown→HTML lib; htmx is one static JS file). Ships as the optional extra
`agent-inbox[ui]`, imported lazily; base install and the Docker image are unaffected.

## It looks like email (on purpose)

Email is a mental model everyone already has, and our messages map onto it richly — they
carry more than to/from/body:

| Email convention | Our data |
|---|---|
| From / To / Subject / Date | `from` · `to` · `subject` · `created` |
| Unread (bold) vs read | `acked_at` null vs set |
| Conversation / thread | the `thread` id groups the back-and-forth |
| Body | `body`, **rendered as markdown** |
| Folders / mailboxes | one per `project/agent` |
| Compose (To, Subject, Body) | send as the operator identity |

So: a **message list** (From · Subject · Date, bold if unread) → click → a **message
view** (From/To/Subject/Date headers + markdown body), threaded with its siblings.

## The one rule that shapes everything: observe vs. manage

- **Observing *any* mailbox is READ-ONLY.** The console shows messages by querying the
  SQLite `messages` table directly (a passive `SELECT`) — it **never** calls the agent
  `read` path, because that would *ack/consume* the message and **steal it from the
  agent**. Read + unread both show (acked messages linger with `acked_at` until they
  expire), giving a real ~14-day inbox history. A one-way window: see, don't touch.
- **The operator's OWN inbox** (`agent-inbox/human`) is the one mailbox that's
  **interactive** — the human reads and replies to agents' responses there, because it's
  their own mail.

## Routes / screens (all under `/ui`; `/` stays the machine descriptor)

- **`/ui`** — dashboard: health + traffic at a glance (message volume over time, bounded
  by `ttl_days` — fine), who's online, recent activity, unread counts.
- **`/ui/agents`** — the directory (from 0004): online/offline, profiles, `offers`/`needs`,
  `platform`, last-seen → click through to a mailbox or a whois card.
- **`/ui/mbox/<project>/<agent>`** — email-like, **read-only** view of any mailbox.
- **`/ui/msg/<id>`** — one message: headers + markdown body + its thread.
- **`/ui/compose`** — write to any target (direct/broadcast/any) as the operator.
- **`/ui/inbox`** — the operator's **own** inbox — interactive (read/reply).
- **`/ui/status`, `/ui/doctor`** — HTML health/config.
- A browser hitting **`/`** gets redirected to `/ui`; machines still get the JSON descriptor.

## Design

- A read-only `views`/`stats` module of plain SQL `SELECT`s over `messages` /
  `broadcast_reads` / `agents` — the testable core. The operator's own inbox reuses the
  existing async `Mailbox` (`peek`/`read`/`reply`); compose reuses `Mailbox.send`.
- Routes added to the existing path-dispatch middleware (same pattern that already serves
  `/prompts/*`) — no restructure of the MCP mount.
- Markdown bodies rendered **server-side** (a lib in the `[ui]` extra); htmx used for
  light auto-refresh and pane swaps.

## Config

- `AGENT_INBOX_UI` — enable the console (default on for the http server).
- `AGENT_INBOX_OPERATOR` — the From identity for human-sent mail (default
  `agent-inbox/human`).
- **Security: deferred.** No auth in v1 (trusted-LAN). This console reads every mailbox
  and can send — revisit before exposing beyond a trusted network.

## Build phases (when we implement)

- **Phase 0** — `/ui` routing + the read-only `views`/`stats` query module (plumbing).
- **Phase 1 (observatory)** — dashboard, agents directory, read-only mailbox browser +
  message/thread view (markdown), status/doctor.
- **Phase 2 (participate)** — compose (send as operator) + the operator's own inbox
  (read/reply).
- **Phase 3 (polish)** — htmx auto-refresh, small charts, thread niceties.

## Definition of done

- One process serves the console under `/ui`; MCP + `/prompts/*` still work alongside.
- Any mailbox is viewable read-only (never consumes); the operator's own inbox is
  interactive; compose sends as the operator.
- Read/stat queries unit-tested against a temp SQLite db; pages smoke-render.
- `[ui]` optional extra; lazy import; Dockerfile installs it; docs updated.

## Non-goals

- Auth / multi-user accounts (deferred). - A second process or port. - Consuming other
  agents' mail. - Long-term stats beyond `ttl_days`. - Structured (non-markdown) message
  bodies (later; the metaphor doesn't change).
