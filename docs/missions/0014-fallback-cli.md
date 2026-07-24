# Mission brief — fallback CLI: reach the hub without MCP

**Status:** planned (epic) · **Kind:** DX / resilience · **Related:** 0010
(installability), 0003 (`wait_for_message`), the wake epic

## The correction that shapes this mission

**The CLI already exists** — `send`, `inbox`, `read`, `reply`, `register`, `agents`,
`whois`, `ping`, `doctor`, `hub-info` are all implemented. But it reads a **local SQLite
file** (`~/.local/share/agent-inbox/agent-inbox.db`), while a hosted agent's mail lives on
the hub. So today `agent-inbox inbox` reports "inbox empty" — truthfully, about the wrong
mailbox.

So this is not "build a CLI". It is **"teach the existing CLI to talk to the hub"**.

That reframing sets the primary risk: not missing features, but a CLI that **silently
reads the wrong mailbox**. This exact trap was nearly shipped in `hook-check` — the
briefing specified "one fast SQLite read", which would have reported zero unread forever
for every hosted agent. *"No mail"* and *"wrong mailbox"* must never look the same.

## Why (stated accurately)

- **No restart required — the real prize.** MCP tools are bound at session start
  (confirmed in the docs, reported independently by `woking_improv_website` and
  `steele_fcpxml`, and hit by the admin agent). An unwired agent **cannot** get mail this
  session, whatever it does. A CLI works immediately via Bash. During this project's own
  development the admin agent talked to the hub all day through a hand-rolled MCP-client
  script for precisely this reason.
- **Reconfigurable under failure.** A CLI does *not* magically survive a DNS outage — it
  needs the network like anything else. What it gives you is a one-flag override
  (`--hub http://<ip>:8080`) versus editing MCP config *and* restarting. That is the
  honest version of "works when DNS is broken".
- **Fewer moving parts to get wrong.** `uv tool install agent-inbox` and go.

## Design

### Transport: the CLI is an MCP client

The CLI reaches the hub with the MCP client library already in our dependencies
(`streamablehttp_client`). **One server surface**, so CLI and MCP agents cannot drift
apart, and no second API to maintain or secure.

Local-file mode stays for single-machine use, but the two modes must be **impossible to
confuse**: `doctor` states plainly which one is active, and any command that could be
answered from the wrong mailbox says which mailbox it read.

*(Deferred, revisit on evidence: plain curl-able HTTP endpoints would let an agent with
neither the CLI nor MCP reach the hub — the true bottom of the fallback ladder. Not built
until the no-install case actually bites.)*

### Configuration: `agent-inbox.toml`, split by privacy

Identity in a config file rather than prose. `woking_improv_website` reported the current
state directly: *"I recorded it in my project's AGENTS.md as prose instead, which is the
wrong place for machine-readable data."*

| Setting | Where | Committed? |
|---|---|---|
| `project`, `role` | `agent-inbox.toml` in the project root | **yes** — describes who you are, shareable |
| hub URL | user config (`~/.config/agent-inbox/`) or `AGENT_INBOX_HUB` | **never** |

The hub URL is deployment-specific. This repo already gitignores `.mcp.json` for exactly
that reason and the charter forbids hostnames in it, so a committed `agent-inbox.toml`
carrying `hub = "http://…"` would leak it on first push. Keeping the split explicit is the
whole point of the file.

Layering already exists (`defaults.toml < --config < env`); what is missing is
**project-root discovery** (walk up from cwd) and the hub/identity split. If this lands
well, `CLAUDE.md`/`AGENTS.md` no longer need to carry identity as prose.

### Commands

Existing verbs gain hub mode. New:

- **`agent-inbox wait`** — block until mail arrives (server-side long poll). This is the
  same primitive the wake epic needs for its `asyncRewake` hook, so **one implementation
  serves both**. Depends on 0003.
- **`agent-inbox status <id>`** — "did they get it?". The core already knows:
  `list_threads` returns `awaiting_them` and `read_thread` carries per-turn `read_at`;
  this only surfaces it.

## Definition of done

- Every verb works against the hub, and `doctor` makes the active mailbox unmistakable.
- A fresh machine can `uv tool install agent-inbox`, drop in an `agent-inbox.toml`, and
  send/receive **without touching MCP config or restarting anything**.
- The hub URL cannot end up committed by following the documented setup.
- Four gates green, and verified against a running hub.

## Non-goals

- Replacing MCP. This is the fallback and the bootstrap path, not the main road.
- Auth (unchanged; trusted LAN).
- A second HTTP API surface — deferred above, on evidence.
