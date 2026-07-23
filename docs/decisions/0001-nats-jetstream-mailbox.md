# ADR 0001 — NATS JetStream as the agent mailbox (and a Claim-Check option for payloads)

- Status: **Superseded by [ADR 0002](0002-sqlite-backend.md)** (2026-07-23)
- Date: 2026-07-22
- Context: `agent-mail` — inter-agent messaging for local LLM agents

> **Superseded.** agent-mail shipped on NATS/JetStream, then replaced it with a single
> local SQLite file. The problem framing below still holds (durable store + a wake
> signal + a per-agent listener); the conclusion that NATS is the right store does not.
> The hosted HTTP hub already provides cross-host reach, so NATS was paying operational
> cost for a benefit we get elsewhere. See [ADR 0002](0002-sqlite-backend.md).

## Context

Multiple LLM coding agents (Claude, Codex, Gemini, …) run on one machine / LAN and
need to notify and message each other. The interim mechanism — agents dropping
message files into a shared git repo and acking by commit — is durable and
auditable but **poll-only**: no agent can get another's attention, and there is no
standard "you have mail" signal.

Prior art (AMQ, AgenticMail, Google's A2A, MCP hubs) converges on one shape:

> **a durable store + a thin "you have mail" signal + a per-agent listener.**

**NATS with JetStream** provides all three primitives natively — durable subjects,
publish, and request-reply — and is a small, mature, self-hostable dependency.

## Decision

Use **NATS JetStream** as the mailbox substrate.

- **Stream `AGENT_MAIL`** binds subject pattern `agent.mail.*`. Each agent's inbox
  is the subject `agent.mail.<recipient>`. The stream is created idempotently on
  first use.
- **One durable pull consumer per agent** (`mail-<agent>`, filtered to that agent's
  subject). Because the consumer is durable with explicit acks, a message
  **persists until that agent acks it**, and survives restarts.
- **Ack semantics carry the read model.** `inbox` peeks: it fetches pending
  messages and immediately `nak`s them, so they remain unread and are redelivered.
  `read <id>` fetches, `ack`s the matching message (consuming it), and `nak`s the
  rest. This makes redelivery idempotent at the application level — a redelivered
  message is simply shown again until explicitly read, so **nothing is
  double-processed** as long as processing is tied to the `read`/`ack`.
- **`notify` is a plain (core NATS) publish** on `agent.notify.<recipient>` — a
  lightweight, non-durable wake. It intentionally does **not** go through
  JetStream: it is a nudge to look at the durable inbox, not a message itself, so
  losing one costs nothing (the durable message is still in the inbox).

### Why not the alternatives

- **Keep the git-file channel.** Durable and auditable but no wake signal and no
  standard listener. We keep it as an interim/parallel channel; `agent-mail` adds
  the missing attention primitive.
- **Plain NATS pub/sub (no JetStream).** No persistence — an offline agent loses
  messages. Unacceptable for a mailbox.
- **A bespoke broker / database + notify.** Reinvents exactly what JetStream
  already gives us (durable subjects, per-consumer cursors, acks, redelivery).

## Pointer vs. payload — the Claim Check pattern

**Decision for v1: carry the payload inline.** A `Message` (id, from, to, thread,
intent, subject, body, created) is serialised to JSON and published as the message
body. Agent-to-agent notes are small text, JetStream comfortably holds them, and
inlining keeps the tool dependency-free (no side store to read the body from).

We explicitly **reserve the Claim Check pattern** for large payloads: rather than
inlining a big body, a producer would store the blob elsewhere (object store, file,
DMS) and send only a **pointer** (URI + checksum) through the mailbox; the consumer
dereferences it on `read`. This keeps the stream small and bounded and avoids
JetStream max-message-size limits.

The `Message` schema is forward-compatible with this: a future `intent`/field can
mark a message as a claim-check pointer without changing the transport. We are
**not** implementing dereferencing in v1 — bodies are inline — but recording the
decision so the schema and stream sizing anticipate it.

## Consequences

- Durable, restart-safe inboxes with per-agent cursors, for free.
- Peek-vs-consume is expressed purely through ack/nak; no extra bookkeeping store.
- A wake signal exists, but true mid-turn interruption does not and is out of scope
  — an agent still reads its inbox at turn boundaries (see
  `docs/inbox-check-snippet.md`).
- Very large payloads are out of scope for v1; the Claim-Check escape hatch is
  designed-for but unimplemented.
