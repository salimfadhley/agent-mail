# agent-mail — design brief (first mission)

## What this project is

`agent-mail` is a **NATS-backed mailbox for local LLM agents**. Multiple AI coding
agents (Claude, Codex, Gemini, …) run on the same machine / LAN and need a simple,
standard way to **notify and message each other**. This project gives them that:
a thin **CLI primitive** plus an **MCP wrapper** over the same verbs, backed by
**NATS JetStream** as the durable store.

It is deliberately a **separate project** from any particular application (it was
extracted from the "goldberg" legal-corpus system, where two agents — `system` and
`casework` — needed to talk). Nothing here is legal-domain specific; it is generic
inter-agent messaging infrastructure.

## Why build it (the problem)

Two agents on one machine currently coordinate by writing message files into a
shared git repo and acknowledging by committing. That is durable and auditable, but
it is **poll-only — one agent cannot get another's attention** (no wake / no
interruption). Research into the state of the art (AMQ / AgenticMail / A2A / MCP
hubs) converged on one shape:

> **durable store + a thin "you have mail" signal + a per-agent listener.**

We already run **NATS with JetStream** (`nats://<broker-host>:4222`), which provides
exactly the missing pieces: durable subjects (the mailbox), publish (the wake
signal), and request-reply (synchronous "answer me now"). So `agent-mail` wraps NATS
in agent-friendly tooling and a standing "check your inbox" convention, so humans no
longer have to hand-relay messages between agents.

**Honest scope limit:** you cannot truly interrupt a running LLM turn. The
achievable win is (a) a **durable inbox** an agent reads at the start of each turn,
and (b) a **notify/wake signal** a per-agent listener can surface between turns.
Build (a) fully; treat a live wake-daemon as a stretch goal.

## What to create

1. **A JetStream-backed mailbox model.** A stream `AGENT_MAIL` over subject
   `agent.mail.<recipient>`, with a **durable consumer per agent** so messages
   persist until that agent acks them. Config via env: `NATS_URL`
   (default `nats://<broker-host>:4222`), agent identity via `AGENT_ID`.
   - Message schema (pydantic): `{ id, from, to, thread, intent, subject, body, created }`
     where `intent ∈ {message, reply, ack, actioned}`.

2. **A CLI primitive** (`agent-mail …`, a console entry point), verbs:
   - `send --to <agent> --subject <s> --body <b> [--thread <t>] [--intent message]`
   - `inbox` — list unread messages addressed to me (peek, do not ack)
   - `read <id>` — show a message and **ack** it (consume)
   - `reply <id> --body <b>` — reply on the same thread
   - `notify --to <agent> [--thread <t>]` — publish a lightweight "you have mail" wake signal
   - Identity from `AGENT_ID` (or `--from`). Human-readable output; `--json` for machines.

3. **An MCP wrapper** exposing the same verbs as MCP tools (`send_message`,
   `check_inbox`, `read_message`, `reply_message`, `notify_agent`) via a FastMCP
   server (`agent-mail mcp-serve`), so Claude-native agents call them as tools. The
   CLI and MCP must share one core module (no logic duplication).

4. **The "check periodically" convention.** Provide a ready-to-paste snippet for a
   global/project `CLAUDE.md`/`AGENTS.md` instructing agents to **run `agent-mail
   inbox` (or call `check_inbox`) at the start of every turn and before long tasks,
   and surface any messages before continuing** — so no human has to nudge them.
   (A Claude turn can't self-poll on a timer, so "periodic" = "every turn"; say so.)

5. **Docs + tests.** A README quickstart (how to send/read/notify, how to run the
   MCP server, the env vars); unit tests with a faked/embedded NATS where practical
   and an integration test gated behind an env flag (`AGENT_MAIL_INTEGRATION=1`)
   against the real JetStream; and an ADR recording the NATS-JetStream-as-mailbox
   decision and the pointer-vs-payload (Claim Check) choice.

## Constraints & non-goals
- **Python 3.12 + uv**; deps already added: `nats-py`, `click`, `pydantic`, `mcp[cli]`.
- **Generic** — no goldberg/legal specifics leak in. Agent names are configuration.
- **Idempotent / durable** — rely on JetStream for persistence and ack semantics;
  a redelivered message must not be double-processed.
- **Non-goal (v1):** true mid-turn interruption; a full always-on wake-daemon;
  replacing anyone's existing git-message channel (that stays as the interim).

## Definition of done
`uv run agent-mail send`/`inbox`/`read`/`reply`/`notify` work end-to-end against the
live JetStream; `agent-mail mcp-serve` exposes the same verbs; tests + docs ship; the
CLAUDE.md "check your inbox" snippet is written; committed and pushed to
`github.com/salimfadhley/agent-mail`.
