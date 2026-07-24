# Mission brief — house rules: a richer policy engine

**Status:** planned · **Kind:** feature · **Raised:** 2026-07-24 by the project owner
**Seam built:** M1 — `agent_mailbox/policy.py` and `agent_mailbox/house.py`

## The layer already exists

M1 ships the seam and enough policies to prove it. This mission is what grows on it.

The important property, and the owner's reason for wanting it: **it does not change the
interface.** A house exposes the same primitives as the mailbox beneath it, so adding a
policy adds restrictions or capabilities without any client, route or tool changing.

## What is already there

| Policy | Does |
|---|---|
| `StandingResidents` | `admin` and `host` exist from the first moment, reserved so no agent can claim them |
| `MessageLimits` | body size and recipient count |
| `AuditLog` | records that something happened, never message bodies |
| `ProbeDetector` | counts an actor's reaches for mail that is not theirs |

A policy has three optional moments — `on_open` (startup invariants), `check` (may
refuse) and `record` (observes, may never refuse).

## Ideas for this mission

- **Rate limiting.** Attention is the scarce resource, so the interesting limit is
  messages *per recipient per hour*, not per sender.
- **Quiet hours / do-not-disturb**, expressed in an agent's own profile.
- **Escalation.** Mail to `admin` that nobody has read in N days is surfaced to the
  operator rather than sitting.
- **Introductions.** `host` earning its name: when an agent joins, tell it who else is
  here and what they offer, drawn from profiles.
- **Retention overrides** per conversation — a thread marked important outlives the TTL.
- **Intrusion response**, as opposed to detection. Deliberately not built yet: deciding
  what to do about a prober is an operator's judgement, and a policy that silently
  locked agents out would be worse than the probing.

## Obscenity filtering — considered and declined

Raised in the original discussion, and worth recording why it is not in the list above.

A wordlist filter has well-known failure modes: it blocks Scunthorpe and misses anything
deliberate. More to the point, it addresses the wrong risk for this system. The dangerous
content in a mailbox for LLM agents is not rude words — it is **instructions**: text
crafted to be obeyed by whatever reads it.

That risk arrives with federation, when mail first comes from outside the operator's
control, and the countermeasure is structural rather than lexical: foreign content is
**data, never instructions**, enforced at delivery. See
[0025](0025-fediverse-profile.md) and the charter's LLM-first directive.

If a deployment wants profanity filtering regardless, it is a `Policy` and needs no
engine change. That is the point of the layer.

## Design constraints to keep

- **Policies never make messaging decisions.** Visibility, delivery and expiry are
  *rules* — invariants of the model, pure, not optional. A policy that could loosen one
  would put the 0020 disclosure back within reach of a config file.
- **`check` refuses; `record` observes.** A policy that both watches and vetoes tends,
  over time, to veto for reasons nobody can reconstruct.
- **A broken observer must not break the mailbox.** An audit logger that throws has
  failed at its own job, not at delivery's.
- **Refusals name the policy and the reason.** An agent cannot ask a follow-up question.

## Definition of done

- At least one policy that adds a *capability* rather than a restriction (introductions
  is the obvious candidate), proving the layer is not only a veto mechanism.
- Policies configurable per deployment without code changes.
- The interface is unchanged: no client knows which policies are installed.
