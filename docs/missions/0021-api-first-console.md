# Mission brief — API-first: the console consumes only the API

**Status:** planned · **Kind:** architecture · **Raised:** 2026-07-24 by the project owner
**Depends on:** the CLI mission (`kitty-specs/cli-primary-client-01KYA42E`), which builds
the API this one adopts.

## The direction

> *"In a future mission, we can align the /ui features to the API. The user interface will
> only use features provided by the API. There is only one API. We are 'api first'. That
> means, eventually, agents can use the API as well as an MCP server."*

Three consequences follow, and they are the whole mission:

1. **One API, no privileged clients.** The web console stops calling `Mailbox` directly
   and becomes an ordinary consumer of the same HTTP API the CLI uses.
2. **The API is a first-class agent interface.** MCP becomes one way in, not the only way.
   An agent with `curl` and the OpenAPI schema can do everything an MCP agent can — which
   also makes the API the fallback when the CLI is unavailable.
3. **Anything the console can do, the API can do.** Where it cannot today, the API grows
   — deliberately, not by bolting on console-shaped endpoints.

## Why it is worth doing

- **Drift stops.** Two paths to the same data diverge; we have already been bitten by this
  in the small (`Mailbox.thread` vs `read_thread` differ in visibility, mission 0020).
- **It proves the API is complete.** A console built on the API cannot quietly depend on
  server internals, so gaps surface as missing endpoints instead of hidden shortcuts.
- **It makes the API testable as a contract**, which the authentication mission will need.

## The hard part: the console is meant to see everything

The console is an *observatory* — it deliberately shows the operator every mailbox — while
agents must see only their own mail. Mission 0020 removed exactly this conflation from
`read_thread`, and `Mailbox.thread()` remains omniscient precisely because only the
console uses it.

So "the console uses only the API" cannot mean "expose the omniscient view to everyone".
This mission must decide how an operator's authority is represented in the API — most
likely an operator credential that unlocks observation routes, which lands naturally on
top of the authentication mission rather than before it.

**This is the reason the CLI mission does not give `Mailbox.thread()` a route.** Doing so
casually would re-open 0020 hub-wide.

## Sequencing

Best after authentication, because the console's extra authority needs somewhere to live.
Doing it before would mean either an unauthenticated omniscient endpoint (unacceptable) or
a temporary operator-only side door (throwaway work).

## Definition of done

- The console makes no direct `Mailbox` calls; every screen is backed by an API route.
- Operator-only observation is expressed as authorisation on shared routes, not as a
  separate privileged code path.
- The OpenAPI schema covers every operation both clients use.
- An agent can complete a full send/read/reply cycle with `curl` alone, using only the
  published schema.

## Non-goals

- Rewriting the console's HTML/UX — this is about where its data comes from.
- Removing MCP. It stays as the ergonomic path for agents; the API joins it.
