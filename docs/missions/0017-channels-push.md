# Mission brief — push into a live session (Channels, and its rivals)

**Status:** planned · **Kind:** wake mechanism · **Supersedes (if it works):** the
`asyncRewake` wake hook · **Related:** 0014 (the CLI hosts the shim)

## The idea

Anthropic shipped a mechanism for exactly our problem: **Channels**.

> *"A channel is an MCP server that pushes events into your running Claude Code session…
> Unlike integrations that spawn a fresh cloud session or wait to be polled, **the event
> arrives in the session you already have open**."*

Mail would land in an agent's context without the agent asking, and without anyone
polling. Channels are two-way — a plain `reply` tool covers the outbound direction, which
is precisely a mailbox.

## Why this probably makes the wake hook redundant

We proved on 2026-07-24 that an `asyncRewake` hook exiting 2 wakes a fully idle Claude Code
session. It works. But compared with a channel it is a workaround:

| | wake hook | channel |
|---|---|---|
| mechanism | background process polls `/unread`, exits 2 | protocol-level notification |
| polling | yes (cheap, but real) | none |
| setup | hook registration + a poller per session | server declares a capability |
| delivers | "you have mail, go look" | **the message itself, in context** |
| status | works today, no gating | research preview, gated |

If channels work in our environment, the hook becomes redundant for Claude Code — it is
the same outcome reached by a less direct route.

**But that "if" is doing a lot of work**, which is why the hook is not cancelled yet:

- **Research preview**, behind an Anthropic-curated plugin allowlist (custom channels need
  `--dangerously-load-development-channels`).
- **Requires claude.ai/Console auth**; not available on Bedrock/Vertex/Foundry. Team and
  Enterprise must enable `channelsEnabled`.
- **stdio only** — a direct conflict with our hosted-HTTP identity model
  (`http://<hub>/<project>/<agent>[/<role>]/mcp`).

So: evaluate channels, keep the hook as the fallback, and only retire the hook once
channels are demonstrated working here.

## The stdio constraint is an opportunity, not just a problem

A channel must be a stdio MCP server that Claude Code spawns. Ours lives on the network.
The bridge is a **local shim**: a small stdio server that watches the hub and emits
`notifications/claude/channel` per message.

That shim is a **CLI mode** — which is why this mission depends on 0014 rather than
duplicating it. Something like `agent-inbox channel` reading the same `agent-inbox.toml`
for hub/project/agent/role. One install, one config file, one identity.

## Do not build a Claude-Code-only answer

Codex and other harnesses are likely to ship equivalents, and some already have hook-like
surfaces. The design rule that follows:

> **The hub stays harness-agnostic.** It stores mail and exposes `/unread` plus MCP.
> *Every* wake mechanism — hook, channel, or a future Codex equivalent — is a **client-side
> adapter**. Nothing harness-specific enters the server.

This is the charter's "generic, releasable infrastructure" rule applied to wake: agents on
different harnesses must be able to use the same hub, and a harness that offers no push at
all must still work by polling.

## Security — a first-order concern here, not a footnote

Anthropic's own documentation warns:

> *"**An ungated channel is a prompt injection vector.** Anyone who can reach your endpoint
> can put text in front of Claude."*
> *"Gate on the sender's identity, not the chat or room identity."*

For a mailbox **any agent can write to**, that is acute: a channel would inject other
agents' message bodies straight into a session's context. Requirements that follow:

- Gate on the **sender's address** (`project/agent[/role]`), which the hub already
  authenticates by connection URL.
- Message bodies are **untrusted input**, and the channel payload must frame them as
  quoted data — not as instructions to follow.
- Prefer delivering a **notification plus metadata** (who, subject, id) over dumping full
  bodies into context, so the agent chooses to fetch.

## Open questions to settle first

1. **Is it even available to us?** Check the allowlist, auth, and plan requirements before
   any code. If it is not reachable in this environment, the mission stops here.
2. **Does one shim serve several agents**, or is it one process per agent? Identity comes
   from the endpoint URL, so probably one per agent — a cost to weigh.
3. **What happens to mail delivered by channel?** Does it stay unread until `read_message`,
   or does delivery imply consumption? (It should stay unread — the channel is a
   notification, not an ack.)
4. **How does it degrade** when the session is closed? Events are *"dropped silently with
   no error"*, so the mailbox must remain the durable record.

## Definition of done

- A decision, evidenced: channels are usable here, or they are not.
- If usable: mail arrives in a live session without polling; sender identity is gated;
  bodies are framed as untrusted; the mailbox stays the durable record.
- The hook remains available as a fallback until channels are proven, and the hub gains
  **no** harness-specific code either way.

## Non-goals

- Retiring the wake hook before channels are demonstrated working here.
- Any harness-specific logic in the hub.
- Replacing `check_inbox` polling — it stays the portable baseline for every client.
