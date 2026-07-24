# Mission brief — re-clone the working copy under the correct name

**Status:** planned · **Kind:** tech debt · **Raised:** 2026-07-24

## The debt

The project was renamed `agent-mail` → `agent-inbox` on 2026-07-23 (PyPI blocked
`agent-mail` as too close to an existing `agentmail`). Everything followed — package,
command, env prefix, MCP server name, Docker image, GitHub repo, docs, prompts — **except
the working copy on disk**, which is still:

```
/Volumes/Home/work/agent-mail          ← retired name
```

`git remote` correctly reports `agent-inbox`, so the repository itself is right. Only the
containing directory carries the old name.

## Why it is worth fixing

Small, but it is exactly the "inconsistent naming causes confusion" the charter's
tech-debt directive exists for, and it has already produced concrete confusion:

- **`agent-inbox/host` mis-analysed it.** It listed the disk path as evidence that the
  project had "three names" and briefly proposed reverting the hub to `agent-mail` — a
  change that would have reintroduced the collision the rename was performed to fix. It
  withdrew the proposal once told the history, but the stale path is what made the wrong
  reading plausible.
- It is the fallback source for project derivation. `.git` is authoritative and gives the
  right answer, but any tool falling back to the directory name gets `agent-mail`.
- Every absolute path in notes, memory and scratch files carries the retired name.

## Why it needs its own mission rather than a quick `mv`

Renaming the directory **while working inside it** breaks things mid-flight:

- the shell's working directory and any running processes;
- absolute paths in agent memory, session scratchpads and saved artefacts;
- `.kittify/` and `kitty-specs/` absolute paths recorded by spec-kitty
  (`feature_dir`, `output_path` in `agent_profiles_manifest.json`);
- the local MCP config if it references a path;
- IDE project settings under `.idea/`.

Hence a deliberate mission at a clean moment, not an in-flight `mv`.

## Approach

Prefer a **fresh clone** over a rename — it proves the repository is self-sufficient and
leaves the old tree intact until the new one is confirmed good:

1. Clone `github.com/salimfadhley/agent-inbox` to `/Volumes/Home/work/agent-inbox`.
2. `uv sync`, run the four gates, confirm green from scratch.
3. Re-point anything holding an absolute path: spec-kitty (`spec-kitty materialize` or
   re-init), IDE settings, and any agent memory referencing the old path.
4. Confirm the hub still reachable and the CLI still works from the new tree.
5. Only then retire the old directory (rename to `agent-mail.retired` first; delete later).

## Definition of done

- Work happens in a path named for the project, with the four gates green from a clean
  clone.
- No tooling still points at the retired path.
- The old tree is archived, not deleted, until the new one has been used successfully.

## Non-goals

- Renaming the GitHub repo, package, image or hub project — all already correct.
- Changing anything the retired name legitimately still appears in: the deprecated
  `agent-mail` CLI alias, `AGENT_MAIL_*` env back-compat, and historical ADRs.
