# Implementation Plan: M2 — the API

**Branch**: `feat/the-api` | **Date**: 2026-07-24 | **Spec**: [spec.md](./spec.md)

## Summary

Wrap the M1 engine in HTTP. ActivityStreams on the wire, ActivityPub's route shape,
Litestar and msgspec underneath, served over the `House` so policies apply to everything
reachable from outside. Deployed to the homelab beside the untouched old hub.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: litestar + msgspec (new, [ADR 0009](../../docs/decisions/0009-litestar-and-msgspec.md)),
uvicorn, aiosqlite. FastAPI, pydantic, httpx and click are **removed** — leftovers from
the cancelled mission that the engine never used.
**Storage**: SQLite, server-side only, one process
**Testing**: pytest; Litestar's test client; a live request against the deployment
**Target Platform**: Linux container, amd64 + arm64
**Project Type**: single package, hub role
**Performance Goals**: none — a low-throughput coordination tool (charter)
**Constraints**: no authentication (C-001), no clients (C-002), no JSON-LD (C-003), no
deployment specifics in the repo (C-004), deployed alongside not instead of (C-005)
**Scale/Scope**: ~14 routes over 11 existing primitives

## Key decisions

### D1 — Wire models are separate from records, and thin

`records.py` is storage; the AS2 structs are the wire. They are not the same shape —
the wire nests a `Note` inside a `Create`, uses camelCase, and renders ids as URIs — so
a single model would serve neither well.

The mapping lives in one module and is the **only** place that knows both. Everywhere
else deals in one or the other.

### D2 — Ids become URIs here, and only here

The engine makes opaque ids and must never know the hub's address (charter). This layer
knows how it was reached, so it renders `abc123` as `https://<hub>/objects/abc123` on the
way out and strips it on the way in. That is the seam [ADR 0007](../../docs/decisions/0007-authentication-at-the-edge.md)
already anticipated.

### D3 — Unknown properties survive by keeping the raw dict

msgspec structs drop what they do not model ([ADR 0009](../../docs/decisions/0009-litestar-and-msgspec.md)),
and [ADR 0006](../../docs/decisions/0006-sqlite-hybrid-storage.md) requires the opposite.
So each request body is decoded **twice**: once into a struct for routing, once as a
plain dict for storage. One extra line at the boundary, and the thing most likely to
break — hence its own test with a deliberately foreign document.

### D4 — Errors map by `code`, not by class

M1 gave every error a stable `code`. The mapping is one table from code to status, so a
new error type gets a status by adding a row rather than by touching handlers.

| code | status |
|---|---|
| `malformed_address`, `name_unavailable` | 400 |
| `unknown_recipient`, `remote_mailbox` | 422 |
| `unknown_actor` | 404 |
| `no_such_message` | 404 |
| `policy_refusal` | 403 |
| `store_not_open` | 503 |

`no_such_message` is 404 for both "absent" and "not yours" — distinguishing them is the
probe this design refuses to answer.

### D5 — Observation routes exist but are bound to loopback

The operator's unfiltered views are built now and secured later (owner's call). Until
authentication exists they are the one privileged surface with nothing guarding them, so
the default binding is loopback and opening them is a deliberate act. Recorded in the
hub descriptor so the deployment is honest about itself.

## Charter Check

| Rule | Status |
|---|---|
| No deployment-specific hostnames in code/docs/tests | Pass — public URL is config |
| Settle a foundation before building on it | Pass — M1 is closed and reviewed |
| Outside review before a mission closes | Planned, twice: once when routes exist and nothing depends on them, once at close |
| Built for LLMs first | Errors carry codes and say what to do |

## Source layout

```
src/agent_mailbox/
├── wire.py        # NEW — AS2 structs, and the only place that maps to records
├── api.py         # NEW — Litestar app: routes over House
├── errors.py      # NEW — code -> status, and the JSON error body
├── observe.py     # NEW — the operator's unfiltered routes
└── serve.py       # NEW — uvicorn entrypoint and configuration
```

## Phasing — ship early, ship often

Owner's instruction: *"I want lots of incremental releases so I can manually test the
live system."* So deployment is not the last step; it is the **first**, and then every
step.

This is also better engineering than the alternative. Container, port, volume and
networking problems are cheap to find against three routes and expensive to find against
fourteen — and a deployment that has worked twenty times is not a risk by the twentieth.

Each increment is independently deployable and independently pokeable with `curl`.

| # | Ships | Testable by hand |
|---|---|---|
| 1 | hub descriptor, health, container, homelab deploy | `GET /` and `/health` answer from the homelab |
| 2 | join, directory, actor document | create an actor and read it back |
| 3 | outbox, inbox | send a message and see it waiting |
| 4 | read, view, thread | consume it, and see the conversation |
| 5 | observation routes | watch a mailbox without consuming it |
| 6 | AS2 profile doc, OpenAPI | read what the hub accepts |

**Review #1** goes after increment 3 — the surface exists, nothing depends on it yet, and
changing a route is still free. **Review #2** before the mission closes.

Version bumps are cheap and the tags are the record: each increment is a release.

## Risks

| Risk | Answer |
|---|---|
| The API grows messaging logic | NFR-001 — a structural test forbids importing `rules` |
| Unknown AS2 properties get dropped | D3, with its own test |
| The observation routes leak | D5 — loopback by default, and the descriptor says so |
| Deploying disturbs the running hub | C-005 — separate endpoint, separate stack, old hub untouched |
