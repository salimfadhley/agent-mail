# ADR 0002 — SQLite as the single storage backend (superseding NATS/JetStream)

- Status: Accepted
- Date: 2026-07-23
- Supersedes: [ADR 0001](0001-nats-jetstream-mailbox.md)
- Context: `agent-mail` — inter-agent messaging for local LLM agents

## Context

agent-mail shipped on NATS/JetStream (ADR 0001). In real use, every agent connects
over HTTP to **one** central MCP server (`http://<host>:8080/<project>/<agent>/mcp`),
which owns the store. That topology changes the trade-off:

- **Cross-host reach is provided by the HTTP hub, not by NATS.** Agents on different
  machines reach the hub over HTTP regardless of the backend. NATS's headline feature —
  pub/sub across hosts without a central process — is one this deployment doesn't use.
- So NATS was paying real operational cost — running a JetStream server, a DNS-rebinding
  workaround for the hosted transport, auth/TLS config, connection-hang footguns, and
  durable-consumer identity drift — for a capability the hub already delivers another way.
- The honest limit from ADR 0001 still holds: you can't interrupt a running LLM turn, so
  the achievable win is a durable inbox read each turn plus a between-turns wake. A wake
  signal needs a listener; with the check-every-turn model, a durable store alone suffices.

The requirement is therefore just a **durable, queryable message store** with three
delivery modes (direct, any-one, broadcast) and automatic cleanup. SQLite is exactly that.

## Decision

Use a **single local SQLite file** as the one and only backend. Remove NATS/JetStream
and the planned Elasticsearch audit log entirely.

- **One file** (`AGENT_MAIL_DB`, default `~/.local/share/agent-mail/agent-mail.db`;
  `/data/agent-mail.db` on a mounted volume in the container). Zero external services:
  install, set identity, run.
- **`Mailbox` reimplemented on `aiosqlite`**, same method surface, so the CLI and MCP
  server are unchanged for callers.
- **Delivery modes on SQL:** direct = a row addressed to one agent; **any** = an atomic
  claim (`UPDATE … WHERE acked_at IS NULL`, so exactly-once); **broadcast** = a per-reader
  `broadcast_reads` table so every agent consumes its own copy.
- **`notify` becomes a best-effort no-op** — SQLite can't push cross-process; agents
  discover mail by checking their inbox each turn (which is the model anyway). The verb
  stays for API symmetry and any future push-capable backend.
- **Automatic expiry:** messages older than `ttl_days` (default 14) are purged when the
  mailbox opens — a trivial one-file retention rule that NATS/ES made complicated.
- **`max_message_bytes`** (default 1 MiB) enforced on send; advertised via `hub_info`.

## Consequences

**Gains**
- No broker to run, secure, or debug. `docker run -v agent-mail-data:/data` and you're up.
- Persistence is a volume mount; inspection is `sqlite3 agent-mail.db`.
- Tests need no external service — the former gated live-JetStream suite is now normal CI.
- Follow-ons get simpler: server-side `wait_for_message` (0003) and presence (0004) are a
  poll/query on one table in one process, not consumer choreography across a broker.

**Costs / limits**
- No multiple hub instances sharing state (SQLite is single-writer). Fine for a homelab
  with a handful of agents; if horizontal scale is ever needed, that's a future
  Postgres-or-broker decision, reopening this ADR.
- No true cross-process wake (`notify` is a no-op). Acceptable: the model is
  check-every-turn, and [0003 wait_for_message](../missions/0003-wait-for-message.md)
  gives in-process send-then-wait for the hosted hub.
- Multi-process access on one box (CLI + server) relies on WAL + `busy_timeout`, which is
  set on every connection.
