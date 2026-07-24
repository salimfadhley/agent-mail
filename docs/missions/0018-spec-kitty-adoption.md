# Mission brief — adopt spec-kitty properly (process, panels, and cross-engine review)

**Status:** planned · **Kind:** process / tooling · **Raised:** 2026-07-24

## Why

The project runs on hand-written briefs in `docs/missions/`. That was a deliberate early
choice ("re-plan for our simpler world") and it worked — 17 briefs, eleven releases, all
deployed and verified. It has now been superseded: **spec-kitty's machinery is the
preferred process**, and if something is asked for the old way, the right response is to do
it *and* say what spec-kitty recommends.

Adoption so far has been ad hoc and taught us where the sharp edges are. This mission
finishes the job deliberately rather than discovering the rest by trial.

## What we already learned the hard way

- `spec-kitty specify <name>` only **scaffolds**; the real entry point is the
  `/spec-kitty.specify` skill workflow. `spec-kitty next` will say `Next step: discovery`.
- **Planning artifacts cannot be committed to `main`.** The branch must be created *before*
  mission files are written: `spec-kitty agent mission create … --start-branch <branch>`.
  Getting that ordering wrong wastes two attempts (it did).
- `--topology single_branch` bakes `target_branch: main` and **no** coordination branch,
  which deadlocks against the protected-branch rule. The default `coord` topology exists
  for a reason.
- `spec-kitty plan` is **interactive** and will hang a non-interactive shell. The
  non-interactive equivalent is `spec-kitty agent mission setup-plan`, which reports
  `phase_complete`.
- Gates check **substance**, not structure: `plan` refuses until Technical Context carries
  real values, and `spec-commit` refuses a spec without genuine `FR-###` rows.
- Hand-editing `.kittify/charter/charter.md` desynchronises it; `spec-kitty charter sync`
  regenerates the derived YAML.
- Mission slugs carry a ULID and `mission_number` is assigned at merge, so the tidy
  `0016-` numbering does not survive the port. Cross-reference by name.

## Scope

### 1. Finish the port
The 9 shipped briefs stay as history — spec-kitty's value is its *forward* gates and they
cannot apply retroactively. The live ones move across: **0016** (part-ported: spec + plan
committed), **0015**, **0014**, **0010**, **0017**.

### 2. Multi-agent review panels — different lenses, not more of the same
spec-kitty projects **17 agent profiles** into `.claude/agents/`. Their value is that each
is primed to notice a *different* failure class, which is what makes several opinions worth
more than one:

| Lens | Profiles |
|---|---|
| design | `architect-alphonso`, `planner-priti`, `designer-dagmar` |
| critique | `reviewer-renata`, `paula-patterns` (boundary leaks, whack-a-field fixes), `debugger-debbie` |
| build | `implementer-ivan`, `python-pedro`, `node-norris`, `java-jenny`, `frontend-freddy` |
| knowledge | `researcher-robbie`, `curator-carla`, `doctrine-daphne` |
| other | `randy-reducer`, `retrospective-facilitator` |

Define **when** a panel runs — the trigger being a mission large or consequential enough
to warrant more scrutiny than one reviewer — and **which** lenses suit which mission kind. Use
`spec-kitty dispatch "<request>" --profile <id>` so opinions are governed and recorded,
which the project config already requires and which has not been happening.

### 3. Cross-engine review — the outsider check
An author reviewing their own spec defends its assumptions. **Codex** is genuinely
independent — different training, no shared blind spots — and is authenticated on this
host. Establish it as the standing outside opinion for large missions.

Two ways to reach it, both now available:

- **`codex exec "<prompt>"`** — one-shot, works today. (**Note:** `-p` is `--profile`, a
  sandbox policy, *not* a prompt. Reviews of a large spec can take well over 7 minutes, so
  run them in the background.)
- **Codex on the hub** — configured 2026-07-24 as **`agent-inbox/codex`**
  (`codex mcp add agent-inbox --url http://<hub>/agent-inbox/codex/mcp`), verified with a
  `ping`. This makes Codex a **peer** rather than something we shell out to: it can
  `check_inbox`, reply, and — once 0015 lands — `comment` on a shared thread where every
  reviewer sees the others' reasoning.

That last point is the project's own thesis, untested until now: we built a mailbox so
agents on different harnesses could collaborate, and have not yet used it that way.

**Gemini is not viable headless on this host** — it demands an interactive browser login.
Antigravity is not installed. `cursor-agent` is present but unevaluated.

### 4. Merge discipline
Decide what `accept` / `review` / `merge` mean here, given the project has been committing
straight to `main` with releases cut from tags. Spec-kitty wants feature branches and merge
gates; reconcile the two rather than fighting them per-mission.

## Definition of done

- Every live mission exists in `kitty-specs/` with a substantive spec.
- A written, followed rule for when a review panel runs and which lenses it uses.
- Codex established as the outsider check for large missions, reachable both ways.
- A documented branch/merge flow that matches how we actually release.
- `docs/missions/` clearly marked as historical, with no ambiguity about which is canonical.

## Non-goals

- Retro-fitting spec-kitty artefacts onto the 9 shipped missions.
- Adopting every spec-kitty feature for its own sake — take what earns its keep.
- Blocking ordinary work on this mission; it runs alongside.
