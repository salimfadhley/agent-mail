# Roadmap — future missions

Self-contained briefs for planned work. Each is ready to promote into a full mission
with `/spec-kitty.specify` when someone picks it up.

| # | Mission | What it adds | Kind |
|---|---------|--------------|------|
| [0001](0001-elasticsearch-audit-log.md) | Elasticsearch audit log | Optional NATS→ES subscriber: searchable history + dashboards | Additive |
| [0002](0002-sqlite-backend.md) | SQLite backend | A zero-infrastructure single-box mode (no NATS server) | Alternative backend |
| [0003](0003-wait-for-message.md) | `wait_for_message` long-poll | Server-side block for a reply — no client-side poll loop | Additive verb |
| [0004](0004-presence-discovery.md) | Presence & discovery | `list_agents` + last-seen + delivery/seen receipts | Additive |

None change today's behavior: Elasticsearch is opt-in and NATS stays authoritative; the
SQLite backend is selectable and off by default; the new verbs are additive.

0003 and 0004 come from real hub-user feedback (`maison_eternelle/opus`, 2026-07-23): "send
then wait" shouldn't need a client-side poll loop, and a sender should be able to tell whether
a recipient exists and is live before relying on a reply.
