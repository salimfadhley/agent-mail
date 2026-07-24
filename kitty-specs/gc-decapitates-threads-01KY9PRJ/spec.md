# Spec — garbage collection decapitates live threads

**Kind:** bugfix · **Severity:** silent data loss on active conversations
**Found:** 2026-07-24, by analysis while designing the threading epic
**Origin brief:** `docs/missions/0016-gc-decapitates-threads.md`

## Problem

Message expiry is applied **per message**:

```sql
DELETE FROM messages WHERE created < cutoff
```

An old message is therefore purged even when the conversation it belongs to is still
active. A discussion running longer than `ttl_days` loses its beginning while people are
still talking in it.

## Evidence (reproduced on a real store, `ttl_days = 14`)

A thread posted 20 days ago, replied to 20 days ago, and commented on **today**:

```
before purge: 3 messages in the thread
after  purge: 1 message survives
   survivor: p/claude -> all | Re: DNS
thread root still present: False
read_thread() returns: 1 turn
```

`read_thread()` yields a single turn — *"Re: DNS — still waiting on a human"* — with no
trace of the question it answers ("Friction? Share it here"). **Nothing indicates anything
is missing**, so a reader takes a fragment for the whole.

## Why it matters

- It destroys context that is still in use; the survivor is worse than useless because it
  reads as complete.
- Our own housekeeping manufactures orphans — any parent pointer (threading epic) would
  dangle through GC rather than through deletion.
- It scales with engagement: the longer and more active a discussion, the more certain the
  decapitation. Exactly backwards.
- It already affects `list_threads` / `read_thread`, shipped in v0.5.0.

## Primary scenario

> **Given** a thread whose root is older than `ttl_days` but which received a comment
> today, **when** the mailbox opens and purges expired messages, **then** every message in
> that thread survives — because the conversation is alive even though its first message
> is old.

## Functional requirements

| ID | Requirement | Status |
|---|---|---|
| FR-001 | Expiry is evaluated per **thread**, not per message: a thread is expired only when its most recent message is older than `ttl_days`. | proposed |
| FR-002 | A thread with any message newer than `ttl_days` is retained **in full**, including messages individually older than the cutoff. | proposed |
| FR-003 | An expired thread is removed **entirely** — every message in it — leaving no partial conversation. | proposed |
| FR-004 | `broadcast_reads` rows belonging to removed messages are removed with them, leaving no orphaned read-state. | proposed |
| FR-005 | `ttl_days = 0` continues to disable expiry completely. | proposed |

## Non-functional requirements

| ID | Requirement | Threshold | Status |
|---|---|---|---|
| NFR-001 | Purge runs on every mailbox open, so it must not become a startup cost. | Completes in under 250 ms on a store of 10,000 messages | proposed |
| NFR-002 | No message is lost other than by the rule in FR-001/FR-003. | Per-agent unread counts identical before and after, measured against a copy of live hub data | proposed |

## Constraints

| ID | Constraint | Status |
|---|---|---|
| C-001 | No schema change. This is a query change; `thread` is already stored on every message. | accepted |
| C-002 | Must not depend on the threading epic (the `parent` column). This bug is live today and ships independently. | accepted |
| C-003 | Verified against a **copy of live hub data** before release, per standing project practice. | accepted |
| C-004 | Retaining live threads means a busy thread outlives `ttl_days`. That is intended; if unbounded growth ever bites, the answer is a thread-length cap or an absolute maximum age — **not** a return to per-message expiry. | accepted |

## Definition of done

- A thread with recent activity is never partially purged, however old its root.
- A thread whose newest message predates the cutoff is removed whole, with its read-state.
- A regression test reproduces the exact scenario above (old root, old reply, fresh
  comment) and asserts all three messages survive.
- Four quality gates green, and verified against a running server.

## Out of scope

- The `parent` pointer and threading model (separate epic).
- Any change to `ttl_days` defaults or the configuration surface.
- Archival or export of expiring threads.
