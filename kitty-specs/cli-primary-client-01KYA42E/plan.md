# Implementation Plan: CLI as the primary agent client

**Branch**: `feat/cli-primary-client` | **Date**: 2026-07-24 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `kitty-specs/cli-primary-client-01KYA42E/spec.md`

## Summary

Move agents from a hosted HTTP MCP endpoint to a **local CLI** that serves stdio MCP and
speaks HTTP to the hub. The hosted MCP transport is removed, leaving the hub with one
machine interface (a REST API) plus the human console. This unblocks channels (a local
process can push into a live session; a remote one cannot) and authentication (identity
becomes a credential the server can verify, not a URL the caller picks).

The mailbox core does not change. This is a transport and packaging mission: the same
`Mailbox` methods get a new front door, and the CLI becomes a client of it.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: FastAPI (new — the hub API), httpx (new — the CLI's client),
`mcp[cli]` (retained, now only for the CLI's **stdio** server), click, pydantic,
pydantic-settings, aiosqlite, uvicorn
**Storage**: SQLite via aiosqlite — **server-side only**, private to the hub process
**Testing**: pytest + pytest-asyncio; FastAPI `TestClient` for API routes; a live
round-trip against a running server before release
**Target Platform**: Linux container (hub, amd64+arm64) and macOS/Linux workstations (CLI)
**Project Type**: single package, two runtime roles (server, client)
**Performance Goals**: added proxy overhead under 100 ms per call on a LAN hub (NFR-001)
**Constraints**: every hub call carries a timeout and can never hang an agent turn
(NFR-002); no client opens SQLite (C-001); no deployment-specific hostnames in the repo
(C-003)
**Scale/Scope**: ~15 agents, ~70 live messages; 13 CLI commands and 12 MCP tools to move

## Key decisions

### D1 — FastAPI for the hub API, not hand-rolled ASGI

The web console is hand-rolled raw ASGI, so hand-rolling the API would match house style
and add no dependency. Choosing FastAPI anyway, because:

- **OpenAPI falls out for free.** The CLI is the first client but not the last (auth
  mission, possibly non-Python agents). A published schema is how those stay honest.
- **Validation reuses the pydantic models we already have** (`Message`, `AgentProfile`),
  so request parsing stops being hand-written `parse_qs` code.
- Starlette is already present transitively via `mcp`, so the marginal install cost is
  small.

Rejected: extending the existing raw-ASGI `WebConsole` pattern. It is fine for rendering
HTML, but every endpoint would hand-roll parsing and error mapping — more surface, not
less, which is the opposite of the mission's goal.

### D2 — The proxy translates, it does not decide

`agent-inbox mcp-serve` maps each MCP tool call onto exactly one HTTP call and returns the
response. No routing, no filtering, no caching, no fallback to local state. Any logic that
lives in the proxy is logic that can disagree with the server. This keeps the security
properties earned in mission 0020 in exactly one place.

### D3 — Identity travels in a header, not the path

Today identity is the URL path, which is why nothing can verify it. The API takes the
caller's address in a request header, so the auth mission can add a credential alongside
it without moving anything (C-002). Routes become flat: `/api/v1/<resource>`.

### D4 — Build it API-first, even though the console conversion is a later mission

The owner's direction is **one API, API-first**: eventually the web console consumes only
what the API offers, and agents may call the API directly instead of going through MCP.
This mission does not convert the console, but it decides the API's shape, so it is bound
by that future:

- **No client-specific endpoints.** Nothing exists because "the CLI needs it"; every route
  is a plain resource operation any client could use.
- **The API must cover what the console does today**, including its read-only observation
  paths — otherwise the conversion mission would have to widen the API immediately.
- **Directly usable by hand.** An agent with `curl` and the OpenAPI schema can send and
  read mail without the CLI. This is a design test to apply to each endpoint, and it is
  also the fallback if the CLI is unavailable.

The corollary worth stating: `Mailbox.thread()` is omniscient and backs the console
(mission 0020). It must **not** get an API route as-is. The console's needs and an agent's
needs differ here, and API-first means resolving that deliberately in the conversion
mission, not exposing the omniscient view by accident now.

### D5 — Removal is a flag day, announced in advance

Hosted MCP disappears in the same release that ships the API. The mitigation is
sequencing, not compatibility: broadcast migration instructions **while the old endpoint
still works**, then deploy. A compatibility window would mean maintaining both interfaces
— exactly the duplication the owner asked to remove.

## Charter Check

| Charter rule | Status |
|---|---|
| No deployment-specific hostnames, IPs, secrets, or org names in code/docs/tests | Pass — the hub URL is user config (`agent-inbox.toml`), never a default |
| Eagerly repay technical debt; be kind to our future selves | Pass — this deletes an interface rather than adding a second |
| Verify against a live server, not just tests | Planned — live round-trip in the definition of done |
| Bugs found by analysis get their own mission | N/A — no new bug found here |

## Project Structure

### Documentation (this mission)

```
kitty-specs/cli-primary-client-01KYA42E/
├── spec.md              # committed
├── plan.md              # this file
└── tasks/               # work packages
```

### Source Code (repository root)

```
src/agent_inbox/
├── api.py               # NEW — FastAPI app: routes over Mailbox
├── client.py            # NEW — httpx client shared by the CLI and the proxy
├── stdio_proxy.py       # NEW — stdio MCP server; tools call client.py
├── conf_file.py         # NEW — agent-inbox.toml discovery, inference, init
├── cli.py               # REWRITTEN — every command goes through client.py
├── mcp_server.py        # REDUCED — hosted transport removed; assembly moves to api.py
├── mailbox.py           # unchanged
├── models.py            # unchanged
├── webui.py             # unchanged (server-side, mounted alongside the API)
└── prompts/*.md         # rewritten for CLI onboarding

tests/
├── test_api.py          # NEW — route-level coverage
├── test_client.py       # NEW — timeout and error mapping
├── test_conf_file.py    # NEW — discovery and inference
└── test_cli.py          # REWRITTEN — against a stub hub, never SQLite
```

## Phasing

Four groups; the first two are independent and can run in parallel.

1. **API on the hub** (`api.py`) — routes, identity header, error mapping, mounted beside
   the console. Hosted MCP is still present at this point, so nothing breaks yet.
2. **Config file** (`conf_file.py`) — discovery by walking to the git root, inference with
   provenance, `init`. Pure local logic, testable without a hub.
3. **Client + CLI rewrite** (`client.py`, `cli.py`) — depends on 1 and 2.
4. **stdio proxy, removal, prompts** (`stdio_proxy.py`, `mcp_server.py`, `prompts/`) —
   depends on 3. The removal lands last, after the replacement is proven.

## Risks and how the plan answers them

| Risk | Answer |
|---|---|
| Rewriting 13 commands silently changes behaviour | Tool names and result shapes are frozen; only transport changes. Existing CLI tests are rewritten against a stub hub, not deleted. |
| Agents stranded by the removal | D5 — announce while the old path works, then cut. |
| The proxy drifts from the server | D2 — the proxy may not make decisions. |
| Scope creep into authentication | Explicitly out of scope; D3 only reserves the seam. |
| API shaped around the CLI, blocking the console conversion | D4 — no client-specific endpoints; every route usable by hand. |

## Open questions

None blocking. Authentication design is deliberately deferred to its own mission.
