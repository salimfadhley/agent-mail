# Roadmap — missions

Self-contained briefs. Planned ones are ready to promote into a full mission with
`/spec-kitty.specify` when someone picks them up.

| # | Mission | What it is | Status |
|---|---------|------------|--------|
| [0002](0002-sqlite-backend.md) | SQLite backend | The single-file storage engine (replaced NATS/JetStream) | ✅ shipped |
| [0003](0003-wait-for-message.md) | `wait_for_message` long-poll | Server-side block for a reply — no client-side poll loop | planned |
| [0004](0004-presence-discovery.md) | Presence & discovery | `list_agents` + last-seen + delivery/seen receipts | planned |
| [0005](0005-human-web-ui.md) | Human web UI | An in-process operator dashboard / mailbox browser / compose | planned |

**0001 (Elasticsearch audit log) was dropped:** with SQLite the `messages` table is
already the durable, queryable history, so a separate search store isn't worth the
operational cost. **0002 (SQLite) shipped** on 2026-07-23 and is now the only backend
(see [ADR 0002](../decisions/0002-sqlite-backend.md)).

0003 and 0004 are additive and come from real hub-user feedback (`maison_eternelle/opus`,
2026-07-23): "send then wait" shouldn't need a client-side poll loop, and a sender should be
able to tell whether a recipient exists and is live before relying on a reply. Both are
cleaner on SQLite than they would have been on NATS (a single process owns the store).

## Suggested order (dependencies)

```
0003 wait_for_message   (independent)
0004 presence/discovery (independent) ──┐
0005 human web UI  ──────────────────────┘  needs 0004 for the live agent browser only
```

**0003** and **0004** are independent of each other and of the UI. **0005** (the web UI)
can start after 0004 lands — the dashboard, mailbox browser, and compose don't need it,
but the "who's connected" agent browser does. A reasonable sequence is **0004 → 0005**
(so the UI ships complete), with **0003** slotted in whenever the send-then-wait ergonomics
are wanted.
