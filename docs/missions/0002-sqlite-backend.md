# Mission brief — SQLite backend (zero-infrastructure mode)

**Status:** planned · **Kind:** alternative backend · **Unlocks:** run with no external services

## What

A second mailbox backend backed by a local **SQLite** file, selectable via config, so
agent-mail runs with **no NATS server** — `pip install agent-mail`, run, done. NATS
stays the choice for LAN / multi-host; SQLite is the super-lightweight single-box option.

## Why

The current hard dependency on a running NATS+JetStream server is the biggest barrier
to "just try it." For agents sharing one machine (the common case), a local SQLite file
gives a durable inbox with zero infrastructure. Lowering the floor to nothing widens
adoption enormously.

## Design (the important part)

- **Extract a `MailStore` protocol** from today's NATS-specific `Mailbox`: the five
  verbs (`send`, `peek`, `read`, `reply`, `notify`) plus `ping`. The CLI and MCP server
  already delegate to one core, so they need no changes.
- **Two implementations:** `NatsStore` (today's behavior) and `SqliteStore`. Select with
  `AGENT_MAIL_BACKEND=nats|sqlite`; SQLite path via `AGENT_MAIL_DB`.
- **SQLite specifics:** a `messages` table (id, from, to, thread, intent, subject, body,
  created, acked_at); peek = unread rows, read = set `acked_at`. Use **WAL mode +
  busy_timeout** so multiple processes (CLI, server, several agents) on the box coexist.
- **Honest capability differences:** no cross-process *wake* (SQLite can't push), so
  `notify` is a local best-effort no-op — which is fine, because the model is already
  "check your inbox every turn." Single machine (or shared filesystem) only; no LAN.
- **`doctor`/`hub_info`** report the active backend so operators/agents can see it.

## Config (add to the config system)

`backend` (`nats` | `sqlite`, default `nats`), `db` (SQLite file path). Document under
[`../configuration.md`](../configuration.md).

## Definition of done

- `MailStore` protocol + `NatsStore` + `SqliteStore`; core wiring selects by config.
- The existing integration suite is **parametrized over both backends** (the sqlite
  parametrization needs no external service, so it runs in normal CI).
- `agent-mail doctor` validates the DB path / backend.
- Docs: a "no-infrastructure quickstart" using the SQLite backend.

## Non-goals

- Multi-host or high-concurrency (that's what the NATS backend is for). - Replacing
  NATS. - A network wake signal in SQLite mode.
