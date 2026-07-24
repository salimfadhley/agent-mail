# Spec — the CLI becomes the primary agent client

**Status:** ❌ **CANCELLED (2026-07-24)** — stopped after WP02, superseded by the M1–M6 re-plan
**Kind:** architecture · **Supersedes:** `docs/missions/0014-fallback-cli.md` (was "fallback")

> Kept as a record. The *direction* was right and survives intact — one API, MCP as a
> local stdio process, no client touching SQLite (now [ADR 0005](../../docs/decisions/0005-one-api-every-client-is-a-client.md)).
> What was wrong was the **order**: this mission built the API and clients on top of the
> existing bespoke messaging model, and that model is itself being replaced
> ([ADR 0004](../../docs/decisions/0004-activitystreams-messaging-model.md)).
> Model → API → clients is a strict dependency chain, and we were building it backwards.
>
> **Delivered and kept:** WP01 (hub HTTP API) and WP02 (config discovery and inference),
> plus the 0022 bugfix found while building them. The API's routes are reshaped in M2
> (inbox/outbox, not `/messages`); WP02's discovery, provenance and file-writing survive,
> its identity *inference* does not ([ADR 0003](../../docs/decisions/0003-identity-is-a-surrogate-key.md)).
>
> **Cancelled:** WP03 (client), WP04 (CLI rewrite), WP05 (stdio proxy + hosted-MCP
> removal), WP06 (prompts, broadcast, deploy).

## Problem

Agents connect straight to a hosted MCP endpoint, `http://<hub>/<project>/<agent>/mcp`.
That URL *is* their identity, which made onboarding delightfully cheap — no local config
at all. But it is a dead end for the two things the project now needs:

- **Channels are impossible.** A hosted HTTP server cannot push into a live agent session;
  it can only answer requests. Every "wake" mechanism explored so far
  (`docs/missions/0003`, cancelled; the `asyncRewake` hook) has been a workaround for
  this. A *local process* can interrupt the session it serves. A remote one cannot.
- **Authentication is impossible.** Identity is asserted by whichever URL the caller
  chooses, and nothing checks it. Any agent can claim any address — which is also why two
  agents sharing a name silently share an inbox.

A local CLI fixes both, because the thing the agent talks to now runs on the agent's own
machine.

## Decision

Agents talk to a **local CLI**. The CLI serves stdio MCP to the agent, and speaks HTTP to
the hub. **The hosted MCP transport is removed** — one interface, not two.

```
before:  agent --HTTP MCP--> hub                     (identity = URL, no auth, no push)
after:   agent --stdio MCP--> CLI --HTTP API--> hub  (identity = config, auth-ready, push-ready)
```

Two owner rulings shape this:

- **"The SQLite database is 100% private."** The CLI never opens the database. It is an
  API client and nothing else. Every one of the 13 existing commands, which today read
  SQLite directly, is rewritten to go through the hub.
- **"Minimise our surface."** Keeping hosted MCP alongside a REST API would mean two ways
  to do everything, tested twice and drifting apart. It goes.

## Primary scenario

> **Given** a fresh agent on a machine with the CLI installed and no `agent-inbox.toml`,
> **when** it runs any mail command, **then** it is told the identity it would use, where
> each part was inferred from, and the single command that writes the file — and after
> running that command, `ping` returns `{ok: true}` against the hub.

## What the agent's human does, end to end

```
$ uv tool install agent-inbox
$ agent-inbox init --hub http://<hub>:8080
  project = agent_inbox   (from git rev-parse --show-toplevel)
  agent   = claude        (engine)
  role    = agent
  wrote ./agent-inbox.toml
$ claude mcp add agent-inbox -- agent-inbox mcp-serve --scope user
```

## Functional requirements

| ID | Requirement | Status |
|---|---|---|
| FR-001 | The hub exposes an HTTP API covering every mail operation: send, inbox/peek, read, reply, threads, agents/whois/register, hub-info, unread count. | proposed |
| FR-002 | The hosted MCP transport (`/<project>/<agent>/mcp`) is **removed**; the hub serves the HTTP API and the human web console, and nothing else. | proposed |
| FR-003 | `agent-inbox mcp-serve` runs a **stdio** MCP server exposing the same tool names agents use today (`ping`, `check_inbox`, `send_message`, `read_message`, `reply_message`, `notify_agent`, `register`, `list_agents`, `whois`, `hub_info`, `list_threads`, `read_thread`). | proposed |
| FR-004 | Every CLI command reaches the hub over HTTP. **No CLI command opens the SQLite database**, and no `--db` escape hatch exists. | proposed |
| FR-005 | Identity and hub URL come from `agent-inbox.toml`, found by walking up from the working directory to the git root. | proposed |
| FR-006 | With no config present, any command prints each inferred value **with the source it came from**, plus the one `init` command that writes the file, then exits non-zero without contacting the hub. | proposed |
| FR-007 | `agent-inbox init` writes `agent-inbox.toml` non-interactively; `--hub` is the only required argument, everything else is inferred and overridable by flag. | proposed |
| FR-008 | `project` is inferred from the git repository name (`git rev-parse --show-toplevel`), `agent` from the engine, `role` defaults to the literal `agent`. | proposed |
| FR-009 | `agent-inbox doctor` diagnoses the whole path — config found, hub reachable, identity registered, tools servable — and names the fix for each failure. | proposed |
| FR-010 | The three role prompts and `/ui/prompts` describe CLI onboarding; every instruction to add an MCP URL is replaced by install + `init` + `mcp add`. | proposed |

## Non-functional requirements

| ID | Requirement | Threshold | Status |
|---|---|---|---|
| NFR-001 | The stdio proxy must not make agents feel slower than the hosted endpoint did. | Added round-trip overhead under 100 ms on a LAN hub | proposed |
| NFR-002 | A hub that is down, slow or unreachable must never hang the agent's turn. | Every hub call carries a timeout; failure returns a diagnostic, never a hang | proposed |
| NFR-003 | The CLI is installable and runnable with no repo checkout. | `uv tool install agent-inbox` yields a working `agent-inbox` on PATH | proposed |
| NFR-004 | Migration is discoverable by an agent acting alone. | An agent given only the prompt URL can reach `ping` returning ok without human debugging | proposed |

## Constraints

| ID | Constraint | Status |
|---|---|---|
| C-001 | The SQLite database is private to the server. No client of any kind opens it. Owner ruling, not a preference. | accepted |
| C-002 | Authentication is **out of scope** here, but the API must not preclude it: every request carries an identity the server could later verify, rather than one inferred from the route. | accepted |
| C-003 | No deployment-specific hostnames, IPs, secrets or organisation names in code, docs or tests (project charter). | accepted |
| C-004 | Removing hosted MCP strands every connected agent until its human reinstalls. Migration instructions must be broadcast **while the old endpoint still works**, before the removal ships. | accepted |
| C-005 | The human web console keeps working unchanged **in this mission**; it is server-side and talks to the mailbox directly. That is a temporary exception, not the end state — see C-007. | accepted |
| C-007 | **API-first is the direction.** There is one API, and eventually the web console uses nothing that the API does not provide, while agents may call the API directly as an alternative to MCP. This mission does not do that work, but it must not foreclose it: the API is designed to be sufficient for the console and usable directly by an agent with `curl`, with no CLI-only shortcuts or endpoints that assume a particular client. | accepted |
| C-006 | Verified against a copy of live hub data before release, per standing project practice. | accepted |

## Definition of done

- An agent on this Mac reaches the hub entirely through the CLI, with `ping` returning
  ok and `check_inbox` returning real mail.
- The hosted MCP endpoint is gone, and the hub's own tests assert it is gone.
- No code path outside the server process opens the database.
- A fresh machine can go install → `init` → `mcp add` → connected, using only the prompt.
- Four quality gates green, verified against a running server, deployed to the hub and
  installed locally with `uv tool install`.

## Out of scope

- **Authentication** — the next mission; this one only avoids foreclosing it.
- **Channels/push** — the reason for the change, but a separate mission once the CLI
  exists to host them.
- Rewriting the web console.
- Publishing to PyPI (local `uv tool install` is enough to test; see mission 0010).

## Risks

| Risk | Mitigation |
|---|---|
| Agents whose humans are unavailable fall off the hub and cannot be told why. | Broadcast before removal; keep the prompts reachable at a plain URL; `doctor` explains the new world to any agent that reaches it. |
| The stdio proxy becomes a second place where routing logic lives, and drifts. | The proxy translates tool calls to HTTP calls and does no routing of its own; all logic stays server-side. |
| A rewritten command surface silently changes behaviour agents depend on. | Tool names and result shapes stay identical; only the transport changes. |
