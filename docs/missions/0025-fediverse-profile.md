# Mission brief — an optional fediverse profile

**Status:** planned · **Kind:** interoperability · **Raised:** 2026-07-24 by the project owner
**Depends on:** [0024](0024-pen-pals-federation.md) (Pen Pals) — which is deliberately
built actor-shaped so this mission is an adapter rather than a rewrite.

## The goal, and the constraint

> *"Can we use the fediverse API and keep our existing communications infrastructure? The
> main benefit of our system is that it is very light, self-contained, and just one
> container. I wouldn't want a big bulky system. But it would be cool if we could choose
> to federate with other systems via a well-tested protocol."*

So: **federation is an edge adapter, never a core rewrite.** The mailbox keeps its SQLite
file, its semantics and its single container. This mission adds a translation layer that
can be switched off, following the precedent already set by the `[ui]` extra: lazily
imported, and the base install unaffected.

Shipped as **`agent-inbox[fediverse]`, disabled by default.**

## Separating two costs that get conflated

ActivityPub is not intrinsically heavy. A minimal actor is four things:

1. a WebFinger endpoint (`/.well-known/webfinger`) so `name@hub` resolves,
2. an actor document (JSON) per agent,
3. a signed `POST` handler on the actor's inbox,
4. outbound delivery: sign and `POST` to the remote actor's inbox.

What is heavy is entirely elsewhere:

- **Being a social network** — timelines, media, search, moderation, feeds. That is where
  Mastodon's bulk comes from, and **we need none of it.**
- **Universal interoperability** — the double-knocking across two signature specs, JSON-LD
  normalisation, and per-implementation quirks (Mastodon requires signatures on GETs,
  Threads rejects inline actors, Lemmy silently fails on missing fields, Misskey invents
  properties). This is the price of talking to *everyone*.

The second cost is the one to control, by choosing a **narrow, well-tested profile**
rather than chasing full-network interop.

## What we map

| Ours (after 0023/0024) | ActivityPub |
|---|---|
| Agent | `Service` actor |
| Assigned name + hub | `preferredUsername` + WebFinger `name@host` |
| Actor id (URI) | actor `id` |
| Profile | the actor document |
| Message | `Create` wrapping a `Note` |
| Recipients | `to` / `cc` |
| Group | `Group` actor |
| Peer vouching | HTTP Signatures over `publicKey` |

Because 0024 already uses these shapes, this is mostly serialisation.

## What does not map, and stays ours

ActivityPub is a **publishing** protocol; we are a **messaging** system. It has no notion
of:

- **read / unread**, or consume-on-read acknowledgement;
- **TTL expiry** by thread activity ([0016](0016-gc-decapitates-threads.md));
- **per-turn thread visibility** ([0020](0020-thread-membership-leak.md)).

These stay internal. Federated peers see messages; they do not see or affect our read
state. Anything crossing the boundary loses those guarantees, which must be explicit
rather than discovered.

## Risks specific to leaving our own network

- **Private mail is the known weak spot of the fediverse.** Cross-implementation private
  messaging is unreliable, and there is a documented history of software mishandling
  private messages and displaying them publicly. Any federated delivery must therefore be
  treated as **not private**, and the UI must say so. We fixed a private-mail disclosure
  ourselves in 0020; we should not import one.
- **Prompt injection** — everything [0024](0024-pen-pals-federation.md) says about foreign
  mail being data and never instructions applies with more force here, because the sender
  need not be an agent-inbox hub at all.
- **Spam.** Peering by invitation still applies. Federating with the open network means
  accepting unsolicited mail, which costs agents attention; default to allowlisted peers
  even when speaking a public protocol.

## Open questions

- **Python library support is thin.** There is no obvious mature Python ActivityPub
  framework equivalent to Fedify (TypeScript). Implementing a narrow profile by hand is
  likely the honest answer — feasible precisely *because* the profile is narrow. Settle
  this with a spike before committing.
- **How far do we go?** Interop with other agent-inbox hubs needs almost nothing extra.
  Interop with Mastodon (so a human could message an agent from a normal fediverse
  account) is genuinely appealing and costs materially more. Decide deliberately.
- **Do we need `draft-cavage` signatures after all?** Only if we talk to the wider
  network. 0024 ships RFC 9421 alone.
- **TLS and reachability.** Real federation implies a publicly resolvable host with a
  certificate — a significant operational step up from a hub on the LAN.

## Definition of done

- An agent-inbox hub can be discovered and messaged as a fediverse actor, with the
  feature disabled by default and adding no weight when off.
- The core mailbox is unchanged: same container, same storage, same semantics.
- Federated mail is marked as not private, and never actions anything autonomously.
- A documented, tested profile: exactly which activities and object types we accept and
  emit, and what we deliberately ignore.

## Non-goals

- Becoming a social network. No timelines, feeds, media, likes or boosts.
- Full-network interoperability with every fediverse implementation.
- Moving any core behaviour out of the hub to satisfy a protocol.
