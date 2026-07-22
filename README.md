# agent-mail

**A NATS-backed mailbox for local LLM agents.** AI coding agents (Claude, Codex,
Gemini, …) running on the same machine or LAN get a simple, standard way to
**message and notify each other** — a small **CLI** plus a **hostable MCP server**
over the same verbs — instead of a human hand-relaying prompts between them.

[![CI](https://github.com/salimfadhley/agent-mail/actions/workflows/ci.yml/badge.svg)](https://github.com/salimfadhley/agent-mail/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)

---

## Why

Agents on one box usually coordinate by dropping files in a shared git repo — durable
and auditable, but **poll-only**: one agent can't get another's attention. `agent-mail`
uses **NATS JetStream** (a durable store + a wake signal + request/reply) to add the
missing piece: a real inbox each agent reads, and a "you have mail" nudge.

> **Honest limitation.** A running LLM turn can't be interrupted or poll on a timer.
> So "check periodically" means **check every turn** — at natural decision points. You
> get a durable inbox to read and a `notify` wake to leave; not mid-turn preemption.

## How it works

- A JetStream stream `AGENT_MAIL` binds `agent.mail.*`. Each agent's inbox is the
  subject `agent.mail.<agent>`, drained by a **durable per-agent consumer**, so
  messages persist until that agent acks them.
- `notify` is a lightweight non-durable publish on `agent.notify.<agent>` — a nudge
  to go read the durable inbox.
- The CLI and the MCP server share one core (`agent_mail.mailbox.Mailbox`) — no logic
  duplication.

See [docs/decisions/0001-nats-jetstream-mailbox.md](docs/decisions/0001-nats-jetstream-mailbox.md)
for the design rationale (and the Claim-Check option for large payloads).

## Requirements

- **Python 3.12+**
- **A NATS server with JetStream enabled.** This is a prerequisite — `agent-mail`
  does not bundle one. Point `NATS_URL` at it. (For a throwaway local one:
  `docker run -p 4222:4222 nats -js`.)

## Install

```bash
uv tool install agent-mail      # recommended (isolated CLI)
pipx install agent-mail         # or
pip install agent-mail          # into the current environment
```

Or run the MCP server as a container — see [Hosting](docs/hosting.md):

```bash
docker run -p 8080:8080 -e NATS_URL=nats://your-nats:4222 \
  ghcr.io/salimfadhley/agent-mail:latest
```

## Quickstart (CLI)

Identity is two-part — **project + agent** (`--project` / `AGENT_MAIL_PROJECT` and
`--from` / `AGENT_ID`); the server from `NATS_URL`.

```bash
export NATS_URL=nats://127.0.0.1:4222
export AGENT_MAIL_PROJECT=agent-mail
export AGENT_ID=claude-opus

# direct: a specific agent on a project
agent-mail send --to agent-mail/codex --subject "corpus stale?" --body "reindex?"
# any one agent on a project (a shared work queue)
agent-mail send --to agent-mail --subject "task" --body "who can take this?"
# broadcast: every agent on a project
agent-mail send --to agent-mail/* --subject "heads up" --body "deploying in 5"

# read your own inbox (as agent-mail/codex)
AGENT_ID=codex agent-mail inbox
AGENT_ID=codex agent-mail read <id>
AGENT_ID=codex agent-mail reply <id> --body "on it"   # replies directly to the sender
```

Add `--json` to any command for machine-readable output.

**Addressing:** `project/agent` (direct) · `project` (any one agent) · `project/*` (broadcast).

| Verb | What it does |
|------|--------------|
| `send --to <target> --subject --body [--thread] [--intent]` | Send to `project/agent`, `project` (any), or `project/*` (all) |
| `inbox` | List my unread messages (peek — does **not** ack) |
| `read <id>` | Show a message and **ack** it (consume) |
| `reply <id> --body` | Reply directly to the sender and ack the original |
| `notify --to <target> [--thread]` | Publish a non-durable "you have mail" wake |
| `ping` | Round-trip a message to yourself to check the system is operational |
| `doctor` | Validate config + NATS connectivity; print the effective (redacted) config |
| `hub-info` | Show this hub's public self-description (name, connect URL, admin/feedback) |
| `mcp-serve` | Run the MCP server (see below) |

```bash
# verify agent-mail is working end-to-end (send + inbox + read to yourself)
AGENT_MAIL_PROJECT=agent-mail AGENT_ID=claude-opus agent-mail ping
```

## MCP server

The same verbs are exposed as MCP tools (`send_message`, `check_inbox`,
`read_message`, `reply_message`, `notify_agent`, and `ping` — a self round-trip a
client can call on sign-on to confirm everything works). Two ways to run it:

**Local, per-agent (stdio).** The client spawns it; identity is `AGENT_MAIL_PROJECT` + `AGENT_ID`.

```bash
AGENT_MAIL_PROJECT=agent-mail AGENT_ID=claude-opus \
  NATS_URL=nats://127.0.0.1:4222 agent-mail mcp-serve
```

**Hosted, multi-agent (http).** One server serves everyone. **Each agent connects on
its own address — the URL *is* its identity, `/<project>/<agent>/mcp`:**

```
http://mail-host:8080/agent-mail/claude-opus/mcp   → agent-mail / claude-opus
http://mail-host:8080/goldberg/casework/mcp        → goldberg / casework
```

```bash
NATS_URL=nats://your-nats:4222 agent-mail mcp-serve --transport http --host 0.0.0.0
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
setting has one name usable as a lowercase TOML key or its UPPERCASE env var (`nats_url`
== `NATS_URL`). Containers set env vars; developers point at a TOML file:

```bash
agent-mail --config ./agent-mail.toml mcp-serve   # env still overrides the file
agent-mail doctor                                 # show effective config + check NATS
```

Common settings: `NATS_URL`, `AGENT_MAIL_PROJECT`, `AGENT_ID`, `AGENT_MAIL_TRANSPORT/HOST/PORT/PATH`,
`AGENT_MAIL_HUB`, NATS auth (`NATS_TOKEN` / `NATS_CREDS_FILE` / …), and the hub's
admin/feedback fields advertised via `hub_info`. **Full reference:**
[docs/configuration.md](docs/configuration.md).

## Development

```bash
uv sync --dev
uv run pytest                       # unit tests
uv run ruff check . && uv run ruff format --check .
uv run pyright
```

Live round-trip against a real JetStream (opt-in):

```bash
AGENT_MAIL_INTEGRATION=1 NATS_URL=nats://your-nats:4222 uv run pytest tests/test_integration.py
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the coding standards and quality gates.

## Roadmap

Planned missions (see [docs/missions](docs/missions/)):

- **[Elasticsearch audit log](docs/missions/0001-elasticsearch-audit-log.md)** — an
  optional NATS→ES subscriber for searchable history and dashboards.
- **[SQLite backend](docs/missions/0002-sqlite-backend.md)** — a zero-infrastructure
  single-box mode so you can run agent-mail with no NATS server at all.

## License

[GPL-3.0-or-later](LICENSE).
