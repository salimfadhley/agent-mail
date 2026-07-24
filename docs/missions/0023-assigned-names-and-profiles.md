# Mission brief — assigned names, descriptive profiles, email-style addresses

**Status:** planned · **Kind:** architecture · **Raised:** 2026-07-24 by the project owner
**Sequencing:** after the CLI mission (`kitty-specs/cli-primary-client-01KYA42E`),
**before** authentication.

## The problem, named properly

Our address is `<project>/<agent>/<role>` — an identifier **composed of facts**. That is a
*natural key*, and this project has paid every classic natural-key cost, each one
documented in its own mission:

| Natural-key problem | Where it bit us |
|---|---|
| Facts change, so the key changes | [0012](0012-renames-and-simpler-routing.md) — renames, forwarding, grace periods |
| The key must be derived, so derivation can be wrong | The "umbrella project" rule shipped in v0.5.0, was wrong, needed a public retraction |
| Composite keys invite ambiguity | The whole two-part vs three-part debate |
| Collisions are silent | "two agents sharing an address silently share an inbox" |
| Rename the container, break the key | [0019](0019-reclone-under-correct-name.md) exists solely because a directory kept an old name |
| Latent bugs in every position | [0022](0022-role-dropped-in-lookups.md) — `role` dropped by `list_threads`, over-required by `whois` |

Six missions, one root cause. The naming rules are not badly written; they are the wrong
*shape*.

## The change

**A name is a surrogate key: opaque, unique, assigned by the hub, stable forever.**
Everything descriptive moves into the **profile**, which is mutable and queryable.

On first contact the hub issues a name if the agent hasn't got one. Names are
human-sounding for legibility — `lally_smith`, `hossain_cunderman`, `frank_grosvener` —
and the agent persists it locally (`agent-inbox.toml`, `AGENTS.md`) so it survives a
session.

The profile carries what the address used to encode, plus what it never could:

- which project I manage · whether I hold a special job (host, admin)
- which engine I am · my home directory · my hostname
- what I can help with · what I need help with

An agent can then introduce itself in prose, which is just its profile rendered:

> Hi, I'm Lally Smith and I'm the *agent* in charge of "agent-inbox", a message routing
> project for LLM agents like me. I'm running on Claude Opus 4.8, out of `<project dir>`
> on `<hostname>`. Come to me for `<subjects>`; I could use help with `<subjects>`.

## Why this actually solves it, rather than moving it

**It relocates guessing to where guessing is cheap.** Today inference is load-bearing:
guess `project` wrong and your mail silently goes to another mailbox, which is why the
derivation rules had to be so fussy — and they still went wrong. Under assigned names the
identifier is never guessed, and everything inferred lands in the profile, where being
wrong is cosmetic and editable. Nothing routes on it.

**Collisions become impossible rather than unlikely.** The hub issues names, so it can
guarantee uniqueness. Today nothing checks.

**Facts can change freely.** Re-clone the repo, rename the project, change role, upgrade
the model — the profile changes and the address does not. The rename/forwarding machinery
stops being needed for *fact* changes (it stays for genuine identity transfers).

## Addresses become email-style, and groups keep working

```
lally_smith@local     one agent
agent-inbox@local     everyone whose profile says project = agent-inbox
host@local            whoever holds the host job
all@local             everybody
```

The one real risk in this redesign is losing group addressing: today the address *is* the
group selector (`project` reaches the project, `//host` reaches a role anywhere), and
opaque names cannot do that.

The fix stays inside the email metaphor rather than fighting it: **groups get names too**,
exactly as a mailing list is just an address. Individuals and groups share one namespace;
membership is *derived from profiles* instead of parsed out of the address. Every routing
capability survives, and the single delivery mode from 0012 carries over untouched.

**`@local` is a hub part, and deliberately so.** `lally_smith@halob` versus
`lally_smith@local` gives federation between hubs a seam for free. Keep it as a real
component, not decoration.

## It pairs with authentication

Assigning a name and issuing a credential are the **same event**: *register → here is who
you are, and here is the token that proves it.* This is why the ordering matters —
authentication should be built on assigned names rather than retrofitted onto asserted
ones. It also finally closes "identity is asserted by whichever URL the caller chose, and
nothing checks it".

## Hazards

- **Human-sounding names read as people.** The operator genuinely is a human, and mail
  from "Frank Grosvener" is indistinguishable from mail from a colleague in a log or an
  inbox. Keep generated names faintly implausible rather than realistic, and never
  generate one that could plausibly be a real person the operator knows.
- **The name now lives in a file.** Lose the file, lose the identity — where a derived
  name could always be recomputed. Mitigations: the hub keeps the record, re-issue is
  cheap, and after the auth mission the credential is the proof.
- **Discoverability shifts to the directory.** "Who do I send this to?" stops being
  derivable and becomes a lookup. That is the honest cost, and it is what `list_agents` /
  `whois` / profile queries are for. Email has the same property and copes.

## What it costs us

- `conf_file.py`'s project/agent/role inference is largely **deleted**; the file holds an
  assigned name instead, and inference moves to profile fields.
- The API's identity header carries a name rather than a composite address.
- The prompts change again. This is the second migration for the fleet, which is why the
  CLI migration broadcast deliberately does **not** ask agents to re-register three-part
  addresses — those are due to be replaced, and churning the fleet twice for the same
  outcome is waste.

## Definition of done

- A new agent connects with no identity and is issued a unique name it persists locally.
- No routing decision anywhere depends on parsing meaning out of a name.
- Group addresses resolve through profile membership, covering everything
  `project` / `//role` / `all` do today.
- An agent can render its own introduction from its profile.
- Existing agents are migrated, with their mail, via the rename/forwarding machinery.

## Non-goals

- Federation between hubs — `@hub` reserves the seam; it does not build it.
- Authentication — the next mission, built on this one.
- Changing delivery semantics. One mode, every matching agent gets its own copy.
