# Mission brief — SQLite backend (the storage engine)

**Status:** ✅ shipped (2026-07-23) · **Kind:** core storage · **Superseded** NATS/JetStream

## Outcome

SQLite is now agent-inbox's **only** storage backend. There is no external service:
`uv tool install agent-inbox` (the `agent-inbox` command), set your identity, run.
NATS/JetStream were removed
entirely (and the planned Elasticsearch audit log was dropped) — simplicity beat
features that cost a broker. See [ADR 0002](../decisions/0002-sqlite-backend.md) for
the decision and rationale.

## What shipped

- **One SQLite file** (`AGENT_INBOX_DB`, default `~/.local/share/agent-inbox/agent-inbox.db`;
  `/data/agent-inbox.db` on a mounted volume in the container). `Mailbox` was
  reimplemented on `aiosqlite` keeping the same method surface, so the CLI and MCP
  server were unchanged.
- **Addressing preserved:** direct (`project/agent`), any (`project` — an atomic
  claim, so exactly-once), broadcast (`project/*` — per-reader fan-out via a
  `broadcast_reads` table).
- **`notify` is a best-effort no-op** — SQLite can't push cross-process, and the model
  is already "check your inbox every turn." Kept for API symmetry and future push-capable
  backends.
- **Automatic expiry:** messages older than `ttl_days` (default 14; 0 disables) are
  purged when the mailbox opens — the simple one-file retention rule.
- **`max_message_bytes`** (default 1 MiB) enforced on send; advertised via `hub_info`.
- Tests need no external service — the former live-JetStream suite is now a normal
  temp-file SQLite suite that runs in CI.

## Follow-ons unlocked by this

- **[0003 wait_for_message](0003-wait-for-message.md)** is now cleaner: a server-side
  poll/condition on the SQLite table gives true send-then-wait in the single hosted
  process — no broker needed.
- **[0004 presence & discovery](0004-presence-discovery.md)** derives from the
  `messages` table / active identities rather than JetStream consumer metadata.
