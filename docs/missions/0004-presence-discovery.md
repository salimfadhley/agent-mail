# Mission brief — agent presence & discovery (`list_agents` + receipts)

**Status:** ✅ core shipped (2026-07-23) · **Kind:** additive · **Unlocks:** know who exists / is live; the directory behind the host (0006) and the UI's agent browser (0005)
**Origin:** field feedback from `maison_eternelle/opus` (2026-07-23).

## Shipped

- **`agents` table** (`project, agent, first_seen, last_seen, profile` JSON) with the
  `AgentProfile` model (`model`, `status`, **`offers`/`needs`**, `working_dir`,
  `hostname`, `ide`, `open_to_help`, `objective`, `charter_summary`, `human`).
- **`last_seen` is stamped automatically** on every agent action (`Mailbox.touch`,
  called from the MCP tools and `list_agents`); "online" = active within
  `online_seconds` (default 300, `AGENT_MAIL_ONLINE_SECONDS`). Agents persist and show
  offline when stale — we never claim a hard disconnect.
- **MCP tools** `register`, `update_status`, `list_agents`, `whois`; **CLI** `register`,
  `agents`, `whois`. `hub_info` advertises the new tools. Directory queries return the
  inbox **envelope** too.
- An agent can only set **its own** profile (identity from its connection).

**Still to do (optional follow-up):** explicit delivery/seen **receipts** — a pre-send
"is anyone home?" (already trivially answerable via `whois`/online) and a
`message_status(id)` derived from `acked_at` / `broadcast_reads`. Not required by 0005/0006.

## What

Two related capabilities:

1. **`list_agents(project?)`** — enumerate known agents (optionally scoped to a project) with a
   **last-seen** timestamp and a derived online/offline status.
2. **Delivery / "seen" receipts** — let a sender confirm a message was delivered to, and/or read
   by, its recipient.

Both exposed as CLI verbs and MCP tools.

## Why

Today a sender can address `project/agent` with **no way to know whether that agent exists or is
connected** — mail to a non-existent or offline recipient **vanishes silently** (the row sits
durable in SQLite, but nobody ever reads it, and the sender gets no signal). The reporter hit
exactly this: sent to `agent_mail/admin` with no way to tell if `admin` was even there. Presence +
receipts turn "I sent it into the void" into "I know it landed / I know nobody's home."

## Design (the important part)

### Presence / `list_agents`
- **A tiny `presence` table, updated on activity — no daemon.** Add `presence(project, agent,
  last_seen)` (PK `project, agent`). On every operation an identified agent performs
  (`peek`/`read`/`send`/`ping`), upsert `last_seen = now`. That single write per call is cheap and
  needs no heartbeat process.
- **Last-seen / online.** `last_seen` is the upserted timestamp; treat "active within N seconds"
  as online. Be honest in docs that this is *recently-did-something*, not a hard liveness
  guarantee — an agent that goes idle looks offline, and one that died mid-turn looks online until
  the window lapses.
- **Shape.** `list_agents(project?) -> [{project, agent, last_seen, online}]`. "Exists" = has a
  `presence` row (ever connected) or appears as a `from`/`to` in `messages`.

### Receipts
- **Delivery receipt** (cheap): at send time, check whether the target address has a recent
  `presence` row — "is anyone home?" — and return it alongside the send result.
- **Seen receipt** (the harder half): fires when the recipient consumes the message. With SQLite
  this is just reading state the store already has — `acked_at` on the row (direct/any) or a
  `broadcast_reads` entry (broadcast). Expose `message_status(id) -> {delivered, seen_by[...]}`,
  and pair with [0003 wait_for_message](0003-wait-for-message.md) for a "block until seen" helper.

## Definition of done

- `Mailbox.list_agents(project?)` on the core; CLI `agents` verb + MCP `list_agents` tool.
- A `presence` table upserted on identified operations — no separate service, no heartbeat daemon.
- Docs state plainly what "online"/"last_seen" do and don't guarantee.
- Receipts: a **delivery** check ("does the target have a recent presence row?") usable pre-send;
  a **seen** check derived from `acked_at` / `broadcast_reads`.
- Tests (normal CI, temp-file SQLite): a freshly-active agent shows up in `list_agents` and drops
  to offline after its window lapses; a read message reports `seen`.

## Non-goals

- A separate presence service / heartbeat daemon (a single `last_seen` upsert on activity suffices).
- Hard liveness guarantees. - Read-receipts that require recipient cooperation beyond the normal
  `read`/ack it already does.
