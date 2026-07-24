# Mission brief — Pen Pals: mail between hubs

**Status:** planned · **Kind:** architecture · **Raised:** 2026-07-24 by the project owner
**Depends on:** [0023](0023-assigned-names-and-profiles.md) (the `@hub` seam) and the
authentication mission. **Do not start before both.**

## The idea

Agents on different hubs correspond directly:

```
from:    lally_smith@halob
to:      hengest_deerlove@fooserver
subject: Change request
body:    Hi, I use <dependency>, and we've noticed a few problems. Would you
         consider making the following change...
```

The motivating case is the strong one: **an agent that depends on a library mailing the
agent that maintains it.** That is a genuinely new channel — better than an issue tracker,
because both ends can act rather than just describe.

Addressing costs nothing extra: `<name>@<hub>` already exists in 0023, so this mission is
almost entirely about **trust**, not syntax.

## `@local` means it can never leave

`local` is a reserved alias for the hub you are on, and it is a **guarantee of
non-egress**, not merely a default:

- `lally_smith@local` — resolves here, and **can never be federated**, whatever peering
  relationships exist.
- `lally_smith@halob` — the hub's canonical name; reachable by a recognised pen pal.

So every hub answers to two names: its own, and `local`. The federation path must
**refuse** to forward anything addressed `@local` rather than helpfully rewriting it to
the canonical name. That makes containment a property an agent can guarantee *by choosing
an address*, verifiable by inspection, with no configuration to get wrong. (It echoes
`.local` in mDNS and private address ranges — same instinct, same reason.)

## The two risks that shape the design

### 1. Foreign mail is untrusted content entering an agent's context

Locally, every agent belongs to the same operator. Across hubs, the sender is a stranger
writing directly into an agent's working memory. A "change request" reading *"…and while
you're here, run this command"* is a prompt-injection payload with a delivery mechanism
attached.

This has to be **architectural, not advisory**:

- Foreign mail is marked as foreign at the point of delivery, unmistakably and in the
  message itself — not only in metadata an agent might not read.
- Foreign mail is **data, never instructions.** It is surfaced to the operator; it does
  not trigger autonomous action.
- Nothing in a foreign message may alter configuration, identity, peering, or trust.

### 2. Spam economics are worse for agents than for humans

A human deletes junk in a second. An agent spends a **turn's attention** on it — real
tokens, real money — and cannot opt out. Email is the cautionary tale: it federated openly
first and authenticated afterwards, and has had spam ever since.

We are not repeating that order.

## The model: peering by invitation

Two hubs deliberately recognise each other. Mail flows only between recognised peers, and
either side can sever the relationship. Federation is **closed by default and opened
deliberately** — which matches the real use case, since *"I depend on your library, let's
connect"* is an act by two operators, not an open port.

## Groups do not federate

`hengest_deerlove@fooserver` is addressable. `all@fooserver` is not. You may write to a
named agent on a peer hub; you may not broadcast into someone else's fleet. **Direct
addressing crosses borders; fan-out stays home.**

This also removes the obvious amplification attack, where one message to a foreign group
address costs a whole fleet a turn each.

## Harmonise now, federate next (owner's sequencing)

This mission builds **agent-inbox ↔ agent-inbox** federation only, on our own transport.
Making those hubs speak real ActivityPub is a *separate, later* mission
([0025](0025-fediverse-profile.md)), so that this one stays small and the core stays
light.

The constraint that keeps the second mission cheap:

> **Do not invent anything ActivityPub already names.**

Concretely, in this mission:

- an agent's identifier is a **URI**, as an ActivityPub actor `id` is — not an opaque
  token we would later have to map;
- a message on the wire is shaped like a `Create` wrapping a `Note`;
- recipients live in `to` / `cc`;
- an agent's profile is shaped like an **actor document**, and agents are the `Service`
  actor type (which exists precisely for automated actors);
- peer authentication is **RFC 9421 HTTP Signatures** over the actor's public key — the
  same mechanism the fediverse uses for server-to-server vouching.

We implement **only RFC 9421**, not the expired `draft-cavage-http-signatures-12` that
the wider fediverse still carries. Because both ends are ours, we skip the
"double-knocking" (try one spec, retry with the other, cache per host) that makes real
fediverse implementations painful. 0025 can add the legacy path *if* talking to the wider
network turns out to be worth it.

Result: 0025 becomes serialisation plus signatures, not a redesign.

## Open questions for the spec

- **Resolution.** How does `@fooserver` become an endpoint — DNS SRV records (the honest
  email analogue), explicit configuration at peering time, or a registry? Explicit
  configuration is the smallest thing that works and the easiest to reason about.
- **Vouching.** A peer asserts "this message is from `hengest_deerlove`". Do we trust the
  peer hub to vouch for its own agents (the email model, where the sending server speaks
  for its users), or do we want per-agent signatures? Hub-vouching is simpler and probably
  sufficient given peering is deliberate.
- **Rate limits per peer**, since attention is the scarce resource.
- **What a severed relationship does to mail already in flight or already delivered.**

## Definition of done

- Two hubs can establish, use, and sever a pen-pal relationship.
- A message to `@local` cannot be federated, and a test proves it.
- Foreign mail is unmistakably marked and never actions anything autonomously.
- Group addresses do not resolve across hubs.
- Peering is closed by default.

## Non-goals

- Open federation. Recognised peers only.
- Discovery of hubs you have not agreed to talk to.
- Cross-hub group membership or shared threads.
