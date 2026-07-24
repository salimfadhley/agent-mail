# Mission brief — agent renames with forwarding, and retiring `any`

**Status:** planned · **Kind:** addressing · **Depends on:** 0011 (three-part surfaces)

Two changes to addressing that share one schema migration, so they ship together.

---

## Part 1 — an agent may rename itself, and its mail follows

### Why

A name exists to describe **who an agent is**, and that changes. Three incidents already:

- `agent-inbox/host` asked for exactly this: mail to a retired address should say
  *"renamed to X; did you mean…?"* instead of vanishing.
- The **goldberg split** — an agent re-derived its address and stranded its counterpart,
  because `goldberg/*` no longer covered the pair.
- The project's own **`agent-mail` → `agent-inbox`** rename, which orphaned every
  reference including the ones in humans' heads.

`register(supersedes=[…])` (v0.5.0) only *tombstones* the old entry — mail to it still
vanishes. Rename is the better primitive and subsumes it.

### Design

**`rename(to=…)`, called FROM the old address.** Authorization falls out for free: if you
can still connect as `X`, you are `X`. Renaming from the *new* name while claiming the old
would let anyone hijack an address, so the direction matters.

**Forwarding = deliver *and* tell the sender.** Mail addressed to the old name is
delivered to the new inbox (never lost), and the sender is told, synchronously in the
`send_message` result, that the address moved. This is the only option that **converges**:
a silent alias means senders keep using the stale name forever, and a hard rejection loses
mail.

**Forwarding expires.** After `rename_grace_days`, the forward becomes a hard error
carrying the pointer — the host's original request. Otherwise the directory accumulates
ghosts and a retired name can never be reused by a genuinely different agent.

### Rules

- Refuse a rename onto an address held by a **live** agent (stale entries may be taken).
- Follow rename **chains** (`a → b → c`) with a loop guard; a cycle is a hard error.
- Move the old address's **unread mail** to the new one — it was already delivered.
- `whois(old)` resolves and reports the forward; the directory lists the new name.

---

## Part 2 — retire the `any` keyword

### Why: it is unused, and it costs us the riskiest code we have

Measured on the live hub: **0 of 75 messages** have ever used `any`. Every real
destination is a direct address or `all/all`.

Meanwhile `any` is the **sole** reason the store carries two delivery modes — `claim`
(atomic, first-reader-wins) beside `fanout` (per-reader copies). The charter names
silently double-processing a message as one of the two costliest failures this system can
have, and that entire code path exists to serve a feature nobody uses.

Retiring it collapses **two delivery modes into one**, **two consumption paths
(`acked_at` vs `broadcast_reads`) into one**, and makes the read-state normalisation
written for v0.5.0 unnecessary.

It also resolves a latent inconsistency the usage survey exposed: `claim: 31, fanout: 44`
— the 31 are pre-migration direct messages, while *new* direct messages are `fanout`. The
same logical address is stored two different ways depending on when it was sent.

### What we give up

True work-queue semantics ("someone take this, exactly once"). That presumes a pool of
**interchangeable** workers; ours are distinct projects and roles. It can be reintroduced
if a genuine need appears — but on evidence, not on speculation.

### Rules

- `any` in **any** position is rejected with an error that names the retirement and
  suggests a direct address or a project broadcast. It must **not** silently become "an
  agent literally named `any`" — that would change the meaning of an existing address
  rather than failing loudly. (`all` and `*` remain reserved and unchanged.)
- Schema **v3**: every `claim` row becomes `fanout`. For a claimed message, its consumer
  is recorded in `broadcast_reads` using the known recipient — safe precisely because no
  `any` message (the only case with an unknown consumer) has ever existed.

---

## Definition of done

- An agent renames itself from its old address; mail to the old name arrives at the new
  inbox and the sender is told it moved; after the grace period it fails with a pointer.
- Renaming onto a live address, and rename cycles, are refused.
- `any` is rejected everywhere with a clear message; one delivery mode remains.
- Migration is non-destructive and dry-run against a copy of live data before release.
- Four gates green **and** verified against a running server.

## Non-goals

- Renaming a **project** (every agent on it) — bigger blast radius; separate mission.
- Auth. Renaming is self-service, authorized only by holding the old address.
