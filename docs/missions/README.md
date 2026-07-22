# Roadmap — future missions

Self-contained briefs for planned work. Each is ready to promote into a full mission
with `/spec-kitty.specify` when someone picks it up.

| # | Mission | What it adds | Kind |
|---|---------|--------------|------|
| [0001](0001-elasticsearch-audit-log.md) | Elasticsearch audit log | Optional NATS→ES subscriber: searchable history + dashboards | Additive |
| [0002](0002-sqlite-backend.md) | SQLite backend | A zero-infrastructure single-box mode (no NATS server) | Alternative backend |

Neither changes today's behavior: Elasticsearch is opt-in and NATS stays authoritative;
the SQLite backend is selectable and off by default.
