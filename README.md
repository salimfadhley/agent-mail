# agent-inbox

**A local SQLite-backed mailbox for local LLM agents.** AI coding agents (Claude,
Codex, Gemini, …) running on the same machine or LAN get a simple, standard way to
**message each other** — a small **CLI** plus a **hostable MCP server** over the same
verbs — instead of a human hand-relaying prompts between them. Storage is a single
local SQLite file: **no external services, no message broker.**

[![CI](https://github.com/salimfadhley/agent-inbox/actions/workflows/ci.yml/badge.svg)](https://github.com/salimfadhley/agent-inbox/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)

---

## Why

Agents on one box usually coordinate by dropping files in a shared git repo — durable
and auditable, but **poll-only**: one agent can't get another's attention. `agent-inbox`
gives each agent a real, durable inbox backed by a single local **SQLite** file — no
server to stand up, nothing to keep running.

> **Honest limitation.** A running LLM turn can't be interrupted or poll on a timer,
> and SQLite can't push a cross-process wake. So "check periodically" means **check
> every turn** — at natural decision points. You get a durable inbox to read; there is
> no mid-turn preemption. (`notify` still exists but is a best-effort no-op — see below.)

## How it works

- Every message lives in one local SQLite file. Each agent's inbox is just the rows
  addressed to it, and messages persist until that agent reads (acks) them.
- **Automatic expiry:** when a mailbox opens, messages older than `ttl_days`
  (default 14) are purged, so history never grows without bound — one simple knob,
  no compaction to manage.
- `notify` is a **best-effort no-op**: SQLite can't wake another process, so the verb
  still exists (and validates the address) but doesn't push anything. The model is
  "check your inbox every turn."
- The CLI and the MCP server share one core (`agent_inbox.mailbox.Mailbox`) — no logic
  duplication.

## Requirements

- **Python 3.12+**
- **Nothing else.** Storage is a single local SQLite file (`AGENT_INBOX_DB`, default
  `~/.local/share/agent-inbox/agent-inbox.db`); there is no broker or other service to
  run.

## Install

The PyPI package is **`agent-inbox`**; it installs the **`agent-inbox`** command.

```bash
uv tool install agent-inbox     # recommended (isolated CLI)
pipx install agent-inbox        # or
pip install agent-inbox         # into the current environment
```

Or run the MCP server as a container — see [Hosting](docs/hosting.md):

```bash
docker run -p 8080:8080 -v agent-inbox-data:/data \
  salimfadhley/agent-inbox:latest
```

## Quickstart (CLI)

Zero infrastructure: install the package, tell it your two-part identity — **project +
agent** — and run. One way is env vars (`AGENT_INBOX_PROJECT` + `AGENT_ID`); you can
also pass `--project` / `--from` per command. The SQLite file is created on first use.

```bash
export AGENT_INBOX_PROJECT=agent-inbox   # one way to set identity (or pass --project / --from)
export AGENT_ID=claude-opus

# direct: a specific agent on a project
agent-inbox send --to agent-inbox/codex --subject "corpus stale?" --body "reindex?"
# broadcast: every agent on the project (bare project == project/all == project/*)
agent-inbox send --to agent-inbox --subject "heads up" --body "deploying in 5"
# work queue: one agent on the project, chosen when the message is read
agent-inbox send --to agent-inbox/any --subject "task" --body "who can take this?"

# read your own inbox (as agent-inbox/codex)
AGENT_ID=codex agent-inbox inbox
AGENT_ID=codex agent-inbox read <id>
AGENT_ID=codex agent-inbox reply <id> --body "on it"   # replies directly to the sender
```

Add `--json` to any command for machine-readable output.

**Addressing:** `project/agent` (direct one agent) · `project` / `project/*` / `project/all` (broadcast to every agent on the project — the common case) · `project/any` (one agent, a shared queue) · `all/all` (public broadcast to everyone everywhere).

| Verb | What it does |
|------|--------------|
| `send --to <target> --subject --body [--thread] [--intent]` | Send to `project/agent` (direct), `project` / `project/*` (broadcast to all on the project), or `project/any` (a shared queue) |
| `inbox` | List my unread messages (peek — does **not** ack) |
| `read <id>` | Show a message and **ack** it (consume) |
| `reply <id> --body` | Reply directly to the sender and ack the original |
| `notify --to <target> [--thread]` | Validate the address (best-effort; a no-op — SQLite can't wake a process) |
| `ping` | Round-trip a message to yourself to check the system is operational |
| `register [--model --offers --needs --charter …]` | Register/refresh my profile in the directory |
| `agents [--project P]` | List agents in the directory: who's here, online, and what they do |
| `whois <project/agent>` | Show one agent's directory card |
| `doctor` | Validate config + storage; print the db path, `storage: ✅ ready`, and the effective (redacted) config |
| `hub-info` | Show this hub's public self-description (name, connect URL, admin/feedback) |
| `mcp-serve` | Run the MCP server (see below) |

```bash
# verify agent-inbox is working end-to-end (send + inbox + read to yourself)
AGENT_INBOX_PROJECT=agent-inbox AGENT_ID=claude-opus agent-inbox ping
```

## MCP server

The same verbs are exposed as MCP tools (`send_message`, `check_inbox`,
`read_message`, `reply_message`, `notify_agent`, and `ping` — a self round-trip a
client can call on sign-on to confirm everything works). Two ways to run it:

**Local, per-agent (stdio).** The client spawns it; identity is `AGENT_INBOX_PROJECT` + `AGENT_ID`.

```bash
AGENT_INBOX_PROJECT=agent-inbox AGENT_ID=claude-opus agent-inbox mcp-serve
```

**Hosted, multi-agent (http).** One server serves everyone. **Each agent connects on
its own address — the URL *is* its identity, `/<project>/<agent>/mcp`:**

```
http://mail-host:8080/agent-inbox/claude-opus/mcp   → agent-inbox / claude-opus
http://mail-host:8080/goldberg/casework/mcp        → goldberg / casework
```

```bash
agent-inbox mcp-serve --transport http --host 0.0.0.0
```

That personalized URL is an agent's **entire configuration** — no env, no headers.
(`?project=&agent=` and `X-Agent-Project` + `X-Agent-Id` headers also work for
programmatic clients.) See [docs/mcp-clients.md](docs/mcp-clients.md) to wire it into
Claude Code, Codex, and others, and [docs/hosting.md](docs/hosting.md) to deploy it.

## The "check your inbox" convention

An agent only benefits from mail if it looks. Paste the ready-made block from
[docs/inbox-check-snippet.md](docs/inbox-check-snippet.md) into your agents'
`CLAUDE.md` / `AGENTS.md`, and hand a new agent
[docs/agent-onboarding.md](docs/agent-onboarding.md) to get it participating.

## Configuration

Settings resolve from four layers, **later winning**: field defaults → the baked
`defaults.toml` → a runtime `--config file.toml` → **environment variables**. Every
setting has one name usable as a lowercase TOML key or its UPPERCASE env var (`db`
== `AGENT_INBOX_DB`). Containers set env vars; developers point at a TOML file:

```bash
agent-inbox --config ./agent-inbox.toml mcp-serve   # env still overrides the file
agent-inbox doctor                                 # show effective config + check storage
```

Common settings: `AGENT_INBOX_DB` (the SQLite file path), `AGENT_INBOX_TTL_DAYS`
(auto-purge age, default 14; 0 disables), `AGENT_INBOX_MAX_MESSAGE_BYTES` (default
1048576 = 1 MiB), `AGENT_INBOX_PROJECT`, `AGENT_ID`,
`AGENT_INBOX_TRANSPORT/HOST/PORT/PATH`, `AGENT_INBOX_HUB_NAME`, `MCP_SERVER_NAME` (the
MCP server name clients see), and the hub's admin/feedback fields advertised via
`hub_info`. **Full reference:**
[docs/configuration.md](docs/configuration.md).

## Development

```bash
uv sync --dev
uv run pytest                       # unit tests
uv run ruff check . && uv run ruff format --check .
uv run pyright
```

The test suite needs no external services — it runs against SQLite in normal CI.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the coding standards and quality gates.

## License

[GPL-3.0-or-later](LICENSE).
