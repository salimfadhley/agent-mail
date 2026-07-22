# Mission brief — Elasticsearch audit log (optional)

**Status:** planned · **Kind:** additive observability · **Depends on:** the NATS backend

## What

An **optional** audit/search layer: a subscriber that tails agent-mail traffic and
indexes an append-only event log into **Elasticsearch**, so operators can search mail,
build Kibana dashboards, and keep history — without changing how the mailbox works.

## Why

NATS+ES is a common, well-understood pairing. Today the only record of what was sent
is the JetStream stream itself; there is no searchable history or dashboarding. ES adds
that. Keeping it a *subscriber* (not an inline write) means it's optional, doesn't slow
sends, and only the hub needs ES credentials.

## Design (the important part)

- **ES is a sink, never the source of truth.** NATS JetStream stays authoritative for
  the mailbox. If ES is down or unset, mail flows exactly as today; the indexer catches
  up when ES returns.
- **An indexer, not an inline write.** A durable NATS consumer tails `agent.mail.*` plus
  a new lightweight events subject `agent.mail.events.*` (send / read / reply / notify /
  ping emitted by the core as small events), and bulk-indexes into `es_index`.
- **Body privacy toggle.** `audit_bodies` chooses metadata-only vs full-body indexing.
- **Runs where the hub runs** — as a background task in `mcp-serve`, or a standalone
  `agent-mail index` process.

## Config (add to the config system; all optional — unset = no indexing)

`es_url`, `es_api_key`, `es_index`, `es_ca_file`, `audit_bodies`. Reserved in
[`../configuration.md`](../configuration.md) under "Planned settings".

## Definition of done

- Core emits events on `agent.mail.events.*` for every verb.
- An indexer (background task + `agent-mail index`) bulk-indexes events into ES.
- Integration test gated behind an env flag against a real ES; unit tests fake ES.
- Docs: hosting an ES-backed hub, the index mapping, the body toggle.

## Non-goals

- ES as the mailbox store of record. - Querying ES from the CLI (v1). - Retention
  lives with the JetStream retention mission, not here.
