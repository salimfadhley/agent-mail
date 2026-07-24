# ADR 0007 — Authentication belongs at the edge; the engine trusts its caller

- Status: Accepted
- Date: 2026-07-24
- Context: `agent-mailbox` — deciding where identity is *proved*, before it is built
- Related: [ADR 0005](0005-one-api-every-client-is-a-client.md)

## Context

There is no authentication yet, and there will not be until its own mission. But every
layer being built now has to decide whether it verifies identity, and getting that wrong
is expensive to unpick — the previous system inferred identity from a URL path, which is
precisely why nothing could ever check it.

## Decision

**Authentication happens at the edge. The engine trusts the caller it is given.**

Concretely:

- **Every `Mailbox` method that acts as somebody takes that identity as an explicit
  argument.** Never from configuration, a thread-local, an environment variable, or any
  other ambient source. There is exactly one way identity enters the engine, and it is
  visible in the signature.
- **The edge — the HTTP API — is responsible for proving it.** It turns a credential into
  a name and calls the engine with that name. Everything before the call is
  authentication; everything after is not.
- **The engine has no way to ask "who is really calling?"**, and that is deliberate. A
  layer that cannot be tricked into acting as someone else is one that never guesses.

## Authentication is not authorisation, and only one of them moves

This distinction is what makes the seam cheap:

| | Question | Where it lives | Status |
|---|---|---|---|
| **Authentication** | Is this really `rosemary_nasrin`? | the edge | not built |
| **Authorisation** | May `rosemary_nasrin` see this turn? | `rules.py`, pure | **built** |

Authorisation is already done. Per-turn thread visibility, the refusal to attach to an
unseen conversation, and self-exclusion from your own broadcast are all authorisation
decisions, and they are pure functions with no notion of credentials. Adding
authentication does not touch them.

That is the payoff: the rule that leaked private mail in production is enforced *below*
the authentication layer, so it holds however the caller was identified — including for
an unauthenticated deployment, which is what we have today.

## How authentication gets added later

1. The edge gains a credential check, and resolves the credential to a name.
2. Registration gains a step: claiming a name also issues a secret. Name assignment and
   credential issuance are the same event, which is why identity had to be settled first.
3. **The engine does not change.** Its methods already take a name.

The only anticipated change to this file's decision is that `caller` may become a small
object carrying a name plus capabilities, rather than a bare name — for the operator's
observe-everything authority, which ADR 0005 requires be expressed as authorisation on
shared paths rather than a privileged code path. That is an additive change to a
parameter that already exists.

## Consequences

- Today's deployment is **unauthenticated and should be treated as such**: any caller can
  claim to be any actor. That is acceptable on a trusted single-operator LAN, and must be
  stated plainly rather than implied by silence.
- The engine is trivially testable: acting as somebody is passing a string.
- No layer needs a "current user" global, which is the usual home of this class of bug.
- Object identifiers are generated as **opaque strings, not absolute URIs**. The engine
  does not know the hub's address and must not (charter: no deployment-specific
  hostnames); rendering an id as a URI is the edge's job, in the same layer that knows
  how it was reached.
