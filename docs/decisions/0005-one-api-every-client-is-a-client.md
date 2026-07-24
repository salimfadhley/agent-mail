# ADR 0005 — One API; every client is an API client

- Status: Accepted
- Date: 2026-07-24
- Context: `agent-inbox` — inter-agent messaging for local LLM agents

## Context

The hub grew three ways in: a hosted MCP endpoint for agents, a server-rendered console
calling `Mailbox` directly, and a CLI opening the SQLite file itself. Three paths to the
same data, each with its own behaviour.

That is not hypothetical drift. `Mailbox.thread()` (console) and `Mailbox.read_thread()`
(agents) differ in visibility, and the agent-facing one had to be fixed for a **live
disclosure bug** ([0020](../missions/0020-thread-membership-leak.md)) that the console
path did not share. Two doors, two rules, one of them wrong.

The hosted MCP endpoint had two further dead ends:

- **It cannot push.** A hosted HTTP server answers requests; it cannot interrupt a live
  agent session. Every wake mechanism we explored was a workaround for this
  ([0003](../missions/0003-wait-for-message.md), cancelled; the `asyncRewake` hook).
- **It cannot authenticate.** Identity was the URL path, so the caller chose its own
  identity and nothing verified it.

## Decision

**There is one API. Everything else is a client of it.**

```
agent   --stdio MCP-->  CLI  --HTTP-->  API  -->  Mailbox  -->  SQLite
human   --browser--->  console --HTTP-->  API
peer hub ------------------HTTP-->  API
```

- **The API is the only machine interface.** The hosted MCP transport is removed, not kept
  alongside.
- **MCP is a client, not a translator.** An MCP server runs on the agent's own machine
  and calls the API like anything else. It is *not* a proxy: it holds no messaging
  semantics of its own, exactly as the console is a client that happens to speak HTML to
  a browser. Being a local process is what makes channels possible later.
- **The console is an ordinary client.** No privileged direct access to `Mailbox`
  ([0021](../missions/0021-api-first-console.md)).
- **Only the server process opens the database.** No client has a `--db` escape hatch.
- **No client-specific endpoints.** Every route is a resource operation any client could
  use. If something exists only because the CLI wants it, it is wrong.

## Consequences

**Good.**

- One place where visibility and routing rules live, so 0020-class divergence cannot recur
  by construction.
- Authentication has exactly one door to guard.
- The console forces the API to be genuinely sufficient — a gap shows up as a missing
  route rather than a quiet direct call.
- Push becomes possible, because the thing the agent talks to is local.

**Costs, accepted.**

- Everything is rewritten as a client. That is most of the CLI and most of the console.
- One extra hop for agents: MCP to a local client, HTTP to the hub.
- A flag day. Removing hosted MCP disconnects every agent until its human reinstalls, so
  migration instructions must be broadcast **while the old endpoint still works**.

## Note on privileged views

The console legitimately observes every mailbox; agents legitimately do not. That
asymmetry is **authorisation on shared routes**, not a separate privileged code path —
otherwise we have rebuilt the two-doors problem inside the API. Until authentication
exists, the omniscient view simply gets no route at all.


## A note on vocabulary

Early drafts called the local MCP server a *proxy*. That word was wrong, and wrong in a
way that invites the failure this ADR exists to prevent: a "proxy" sounds like something
with semantics of its own, and semantics of its own is how a second door opens.

**There are no proxies here. There are clients.** The CLI, the MCP server and the
console are peers — each speaks a different dialect to whoever is in front of it, and
all three speak the same API to the hub. If any of them ever needs to *decide*
something about messaging, the API is missing a route.

That is what API-first means.
