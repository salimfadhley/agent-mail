# ADR 0009 — Litestar and msgspec for the API

- Status: Accepted
- Date: 2026-07-24
- Context: `agent-mailbox` — choosing the HTTP layer for M2
- Related: [ADR 0004](0004-activitystreams-messaging-model.md) (AS2 on the wire),
  [ADR 0006](0006-sqlite-hybrid-storage.md) (unknown properties must survive)

## Context

FastAPI was assumed, on the strength of free OpenAPI documentation. The owner removed
that from the scales — *"I don't care about swagger"* — and asked for a real comparison,
noting that **better performance at low cost is still a win**.

Two facts made the question more open than it looked:

- **The new engine uses no pydantic at all.** Records are frozen dataclasses. There is
  no incumbency to preserve.
- **Free API docs are worth less to us than usual.** The vocabulary is a W3C standard
  that W3C already documents; what needs writing is *our profile*, which no framework
  generates. And our readers are agents, for whom the machine-readable schema matters
  and the rendered UI does not.

## Measurement

A spike implemented the same two routes — `POST /actors/{name}/outbox` and
`GET /actors/{name}/inbox` — in both frameworks, over a realistic AS2 `Create`/`Note`
including the awkward parts: `@context`, camelCase properties, and a `to` field that may
be a string or an array.

| | FastAPI + pydantic | Litestar + msgspec |
|---|---|---|
| Installed size | **7.5 MB** (pydantic-core alone is 4.5) | **2.5 MB** |
| Import time | 0.90 s | 0.77 s |
| Round trip, send | baseline | **1.2×** |
| Round trip, inbox of 20 | baseline | **1.5×** |
| AS2 encode+decode | 3.2 µs/op | **0.8 µs/op (4.2×)** |
| OpenAPI 3.1 | ✅ | ✅ |
| camelCase | per-field `Field(alias=…)` | struct-level `rename={…}` |
| Polymorphic `str \| list[str]` | ✅ `anyOf` | ✅ |
| Unknown properties preserved | native (`extra="allow"`) | needs the raw dict alongside |
| Ecosystem | large | smaller |

## Decision

**Litestar with msgspec for the hub's API.**

The deciding factors are **size and fit**, not speed. Speed is a bonus we are not short
of: the charter calls this *"a low-throughput coordination tool"*, and 1.5× on requests
buys us nothing real.

- **Three times smaller** matters, because "one lightweight container" is a property this
  project sells. 5 MB of a 7.5 MB stack is pydantic-core, serving models we do not have.
- **msgspec fits what we already are.** Our records are dataclasses; msgspec handles them
  natively, where pydantic would want a parallel hierarchy or per-call adapters.
- **`rename={…}` at struct level is tidier** than an alias on every field, and we have a
  lot of camelCase to declare.

## The one point where FastAPI is genuinely better

pydantic's `extra="allow"` preserves unknown properties for free, which
[ADR 0006](0006-sqlite-hybrid-storage.md) requires — a peer may send AS2 extensions we
have never seen, and dropping them corrupts the document on the way back out. msgspec
structs drop them.

This is close to a wash in our design, because **we already keep the raw document
alongside the typed fields**. Decoding twice — once into a struct for routing, once into
a plain dict for storage — is what ADR 0006 describes anyway. It is one extra line at
the boundary, not a workaround.

It is, however, the thing most likely to bite, so it belongs in the tests: a foreign
document with unknown properties must round-trip through the API unchanged.

## Risks accepted

- **Smaller ecosystem.** Fewer examples, fewer answers, and agents are likelier to know
  FastAPI. Mitigated by the API being small and standard-shaped — we are not doing
  anything exotic, and we control both ends.
- **Two serialisation libraries in the repo.** `mcp` depends on pydantic, so the *client*
  side pulls it in regardless. That is acceptable because the split is clean: the **hub**
  ships litestar+msgspec, the **clients** ship whatever `mcp` needs, and we write no
  pydantic models of our own. If we ever find ourselves maintaining the same model twice,
  this decision was wrong and should be revisited.

## Revisit if

- We end up writing pydantic models by hand, defeating the point.
- The ecosystem gap costs more than the 5 MB saves.
- A federation implementation needs something only the FastAPI ecosystem has.
