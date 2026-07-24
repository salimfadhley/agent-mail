# Mission brief — `role` is dropped by `list_threads` and over-required by `whois`

**Status:** ✅ fixed, shipping with the CLI mission · **Kind:** bugfix
**Raised:** 2026-07-24, by analysis while building the hub API
**Severity:** latent today, **activated** by the three-part addressing convention

> Both bugs are invisible while every agent is two-part. Adopting
> `<project>/<agent>/<role>` turns them on.

## Bug A — `list_threads` ignores the role it was given

```python
async def list_threads(self, project, agent, limit=50, role=None):
    me = format_address(project, agent)          # role dropped
    params = await self._party_params(project, agent)   # role dropped again
```

The parameter is accepted and never used. For an agent holding a role, `me` is computed
as `proj/alice` while its messages carry `from_addr = "proj/alice/agent"`, and the party
clause matches nothing.

**Effect:** an agent with a role sees **zero threads** — not an error, just an empty list,
which reads as "you have no conversations".

## Bug B — `whois` requires an exact role match

```sql
SELECT * FROM agents WHERE project = ? AND agent = ? AND role = ?
```

with `role or ""`. So `whois("proj", "alice")` returns `None` for an agent registered as
`proj/alice/agent`.

**Effect:** the natural lookup fails. It is also inconsistent with addressing everywhere
else in the system, where an **omitted position is a wildcard** — `proj/alice` reaches
`proj/alice/agent` when sending, but cannot find it in the directory.

## Reproduction

```
register('p','alice', profile, 'agent')   ->  AgentInfo(address='p/alice/agent')
whois('p','alice')                        ->  None          # Bug B
list_threads('p','alice', role='agent')   ->  []            # Bug A
```

Both surfaced as failing API tests (`test_register_whois_and_list`,
`test_list_threads_only_shows_the_callers_conversations`) while building
`kitty-specs/cli-primary-client-01KYA42E`.

## Fix

- **A:** thread the `role` through — `format_address(project, agent, role)` and
  `_party_params(project, agent, role)`. The parameter already exists; it was simply
  never passed on.
- **B:** when `role` is `None`, do not filter on it. That restores consistency with the
  addressing rule (omitted = every value). When more than one role matches, return the
  most recently seen entry, since directory lookups are about finding whoever is live.

## Why it is recorded separately

Standing practice: a bug found while building a feature gets its own brief with a
reproduction, rather than disappearing into the feature's commit. These are fixed in the
same branch because they **block** the API work, but they are their own commit and their
own record.

## Definition of done

- An agent registered with a role is found by a two-part `whois`.
- An agent with a role sees its threads.
- Regression tests for both, failing before the fix.
