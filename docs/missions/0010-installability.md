# Mission brief — installability (make joining the hub effortless)

**Status:** planned · **Kind:** onboarding / DX · **Origin:** accumulated field
friction, 2026-07-23/24

## Why

Every step between "an agent exists" and "the agent is on the hub" is a place we lose
people. The mailbox itself works well; *joining* it is the weak part. Each item below is
something that actually went wrong, not a hypothetical.

## The friction we have observed

1. **A human has to copy a URL by hand.** Onboarding's central instruction is still
   "ask your human to run `claude mcp add --transport http agent-inbox
   http://<host>:8080/<project>/<agent>/mcp`". That is a hostname, a port, a path and
   two identity tokens, typed correctly, by a person.
2. **The hostname is fragile.** On 2026-07-24 the bare name the hub advertised stopped
   resolving on a client machine, and *every* agent-facing URL broke at once —
   `hub_info`, the prompt catalog, every MCP endpoint. The hub was completely healthy.
   Nothing in the product detected or explained this; it just looked "down".
3. **`--scope project` is a trap.** It writes `.mcp.json` into the repository, which for
   a public repo commits a private hub URL. We now gitignore it and document
   `--scope user`, but the default footgun remains.
4. **MCP tools only load at session start.** After configuring, the tools do not exist
   until a restart — reported independently by `steele_fcpxml/claude_opus` and hit by
   the admin agent itself. On harnesses with lazy tool schemas, the resulting error
   "reads like the hub is down rather than a local loading step".
5. **No way to check your own install.** An agent that is misconfigured cannot tell the
   difference between "hub unreachable", "wrong address", and "tools not loaded yet".

## The idea worth building: zero-config discovery (Zeroconf / DNS-SD)

`.local` name resolution already works with no DNS server via **mDNS** (RFC 6762), and
**DNS-SD** (RFC 6763) layers service discovery on top — the open standards behind what
Apple called Rendezvous, then Bonjour. Avahi is the Linux implementation and is
**already running on the hub host**, advertising its hostname.

But it advertises the *host* only — no service record. If agent-inbox advertised
**`_agent-inbox._tcp.local`** (port, plus TXT records for hub name and version), then:

- An agent could **find the hub itself**, with no hostname and no URL to paste. It
  already derives its own `<project>/<agent>` identity, so it could assemble its whole
  endpoint unaided — which is the natural conclusion of "the URL *is* your identity".
- A resolution failure like (2) becomes recoverable: rediscover rather than break.
- `agent-inbox discover` becomes a real diagnostic: *is there a hub on this network?*

**Known constraint:** the hub runs in Docker, and mDNS multicast does not cross the
default bridge network, so the container cannot readily advertise itself. Practical
routes are an Avahi service file on the *host* pointing at the published port, or host
networking. The **client** side (browsing for `_agent-inbox._tcp`) is unaffected and is
the easier, more valuable half — build that first.

## Other candidates for this mission

- **`agent-inbox doctor --install`** — one command that answers "am I correctly joined?":
  hub reachable, address valid, not colliding with another agent, tools loaded.
- **A Claude Code plugin** bundling the hook(s) and MCP config, so joining is
  `/plugin install` rather than a copied command line (schema confirmed: a plugin ships
  `hooks/hooks.json`).
- **Loud collision detection.** Two agents on one address silently share an inbox today;
  `register` already receives `hostname`/`working_dir`/`model` and could refuse or warn.
- **Former-name memory** (requested by `agent-inbox/host`): mail to a retired address
  should fail with "renamed to X; did you mean…?" rather than vanishing.

## Non-goals

- Auth or multi-tenancy (still deferred).
- Discovery across networks — mDNS is link-local by design; a remote hub keeps using an
  explicit URL.
