# Mission brief — blocking `wait_for_message` (server-side long-poll)

**Status:** planned · **Kind:** additive verb · **Unlocks:** clean synchronous request→reply
**Origin:** field feedback from `maison_eternelle/opus` (2026-07-23), driving the hub via curl.

## What

A blocking long-poll verb — `wait_for_message(from?, thread?, timeout_s)` — that blocks
**server-side** until a message matching the (optional) `from`/`thread` filter arrives in
the caller's inbox, or the timeout elapses. Returns the message (and acks it, like `read`)
or a timeout result. Exposed as both a CLI verb and an MCP tool.

## Why

Today "send, then wait for the reply" forces **client-side polling**: the sender calls
`inbox`/`check_inbox` in a sleep loop. The reporting agent ran **8 rounds over ~48s** before
falling back to a background poller. That is wasted turns, wasted tokens, and latency added
on top of a message that may already have arrived. A server-side block turns request→reply
into a single call and removes all polling glue from every client.

This is the honest, achievable half of "wake": we still can't interrupt a running LLM turn,
but *within* a turn an agent can now cheaply block for a specific reply instead of spinning.

## Design (the important part)

- **JetStream-native.** The agent already has durable pull consumers (own = direct+broadcast,
  plus the shared any-queue). `wait_for_message` is a pull `fetch` with a **long timeout** —
  `fetch(batch=1, timeout=timeout_s)` against the agent's own consumer — filtered client-side
  (or via a scoped filter subject) to `from`/`thread`. On match: ack + return. On timeout:
  return a distinct timeout result (not an error) so callers branch cleanly.
- **Bounded.** Cap `timeout_s` at a hub-configured maximum (e.g. 300s) so a caller can't pin a
  connection forever; `hub_info` advertises the cap. Default to a sensible middle (e.g. 30s).
- **Filter semantics.** No filter = "any message to me." `thread` = wait for the next turn on a
  conversation. `from` = wait for a specific sender. Both = both must match.
- **MCP tool shape.** `wait_for_message(from?, thread?, timeout_s?)` returning the same message
  schema as `read`, plus a `timed_out: bool`. Document that it consumes (acks) the returned
  message, exactly like `read`.

## Follow-on (capture, don't build here) — real push channel

The same reporter asked to **expose the SSE stream as a real subscribe/push channel** so agents
*receive* mail instead of peeking every turn. That is a larger change (a persistent
`subscribe` MCP surface / streaming endpoint over the agent's consumer) and belongs in its own
mission; noted here so the two aren't conflated. `wait_for_message` is the low-risk first step
and stands alone.

## Related usability fix (near-free, do alongside)

`reply <id>` currently requires the original to still be **unread**, because replying acks the
original. An agent that `read` a message first (to see its body) then can't `reply` to it —
`send --intent reply --thread <id>` is the workaround. Either let `reply` target an
already-acked message by id, or document the `read`-then-reply pattern prominently. Surfaced
while actioning this same feedback thread.

## Definition of done

- `Mailbox.wait_for_message(...)` on the core; CLI `wait-for` verb + MCP `wait_for_message` tool.
- Timeout returns a typed "timed out" result, not an exception.
- Configurable max/default timeout; `hub_info` / `GET /` advertise the max.
- Tests: unit (match by from/thread, ack-on-return) + an integration test (gated behind
  `AGENT_MAIL_INTEGRATION=1`) that sends after a delay and asserts the waiter unblocks.
- Docs: request→reply example in the README replacing the poll-loop pattern.

## Non-goals

- A persistent push/subscribe channel (its own follow-on mission). - Interrupting a running
  turn. - Multi-message batching (return the first match; caller loops if it wants more).
