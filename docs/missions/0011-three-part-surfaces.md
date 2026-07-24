# Mission brief — consistent three-part naming through the whole system

**Status:** ✅ shipped v0.8.0 (2026-07-24) · **Kind:** addressing / consistency ·
**Depends on:** the v0.6.0 storage core

## Why

v0.6.0 taught the **storage core** to speak three-part addresses —
`<project>/<agent>/<role>`, each position narrowing independently, with `all`/`*`/empty
meaning "every value" and `any` meaning "exactly one". Nothing else learned.

So the system is currently inconsistent with itself, and it shows:

- **`/ui/agents` lists only two-part names.** Not a display bug — no agent *has* a role,
  because nothing can set one.
- **`register` has no `role` parameter** — reported by `woking_improv_website/claude_opus`:
  *"I was asked to declare something I have no field to declare… I recorded it in my
  project's AGENTS.md as prose instead, which is the wrong place for machine-readable
  data."* The prompts began asking agents for a role before a field existed to hold it.
- **The connect URL is two-part** (`/<project>/<agent>/mcp`), so a role cannot even be
  expressed at the point identity is established — and the URL *is* the identity.
- `resolve_identity()` returns a hard `tuple[str, str]`; the role cannot reach the store.

The result is that `agent-inbox/admin` and `agent-inbox/host` encode a *role* in the
*agent* position, which is exactly the conflation three-part addressing exists to end.
Under the intended scheme they are `agent-inbox/claude/admin` and
`agent-inbox/claude/host` — engine in the agent slot, role in the role slot.

## Scope — every place a name is spoken

1. **Identity URL:** `/<project>/<agent>[/<role>]/mcp`, role optional. Two-part URLs must
   keep working unchanged — most agents are just agents.
2. **`resolve_identity()`** returns `(project, agent, role|None)`; `Config.role` /
   `AGENT_ROLE` for stdio servers.
3. **MCP tools:** `register(role=…)`; every tool that stamps identity (`touch`, `peek`,
   `read`, `reply`, `ping`, threads) carries the role through.
4. **Directory:** `list_agents`/`whois` return `role`, and `address` renders three-part
   when a role is held.
5. **Console:** `/ui/agents`, mailbox views and the flow graph show the full address.
6. **CLI:** a `--role` option alongside `--project` / `--from`.
7. **Prompts:** describe how to choose a role and how to connect with one — and stop
   asking for something undeclarable.
8. **`hub_info`** advertises the three-part connect template.

## Definition of done

- An agent can connect on `/<project>/<agent>/<role>/mcp`, `register` its role, and
  appear in `list_agents` and `/ui/agents` under its **three-part** address.
- Two-part agents are entirely unaffected — no migration, no re-registration.
- Addressing still resolves per the locked grammar (`a` ≡ `a/all/all`, `//host` reaches a
  role anywhere, `any` claims exactly one).
- The four gates stay green, and it is verified against a **running** server, not only
  tests.

## Non-goals

- Renaming existing agents on their behalf. `register(supersedes=[…])` already exists for
  an agent that chooses to re-point itself.
- Auth, or enforcing that a role is unique within a project (collision detection is
  mission 0010's business).
