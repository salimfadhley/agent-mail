# Implementation Plan: GC decapitates live threads

**Branch**: `fix/gc-decapitates-threads` | **Date**: 2026-07-24 | **Spec**: [spec.md](spec.md)
**Input**: [spec.md](spec.md)

**Note**: This template is filled in by the `/spec-kitty.plan` command. See `src/doctrine/missions/software-dev/command-templates/plan.md` for the execution workflow.

The planner will not begin until all planning questions have been answered—capture those answers in this document before progressing to later phases.

## Summary

Message expiry is evaluated **per message**, so a conversation still being commented on
today loses its beginning once its root passes `ttl_days`. Reproduced on a real store: a
three-message thread commented on today was reduced to one survivor reading *"Re: DNS —
still waiting on a human"*, with no trace of the question it answered and nothing
indicating anything was missing.

**Approach:** expire by **thread activity** rather than message age. A thread is stale only
when its most recent message predates the cutoff; then it is removed whole. One query
change — `thread` is already stored on every message, so no schema change is required.

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.

  If multiple developers/agents will work on this mission, add an "Implementation
  Concern Map" section below to decompose architectural intent into IC-## concerns
  before generating tasks.
-->

**Language/Version**: Python 3.12
**Primary Dependencies**: aiosqlite, pydantic / pydantic-settings, click, mcp (FastMCP), uvicorn
**Storage**: SQLite (single file, WAL); schema v4 — `messages`, `broadcast_reads`, `agents`, `hub_meta`, `forwards`
**Testing**: pytest (no unittest), temp-file SQLite, no external services; plus verification against a copy of live hub data
**Target Platform**: Linux container (multi-arch amd64/arm64) and local macOS/Linux
**Project Type**: single (library + CLI + hosted MCP server)
**Performance Goals**: purge runs on every mailbox open — under 250 ms on a 10,000-message store
**Constraints**: no schema change; must not depend on the threading epic's `parent` column; `ttl_days = 0` still disables expiry
**Scale/Scope**: ~90 messages and 11 agents on the live hub today; design for 10k messages

## Charter Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Charter directives that bear on this mission:

- **Respect risk boundaries.** Silently dropping a message is named as one of the two
  costliest failures. This mission *removes* an existing silent-drop, and its own risk is
  over-retention (benign) rather than loss.
- **Non-destructive by default.** Verified against a copy of live hub data before release;
  per-agent unread counts compared before and after.
- **Single core.** The change lives in `Mailbox._purge_expired`; CLI and MCP both inherit
  it with no duplicated logic.
- **Tests are pytest, no external services** — the reproduction runs on a temp-file store.

## Project Structure

### Documentation (this mission)

```
kitty-specs/[###-mission]/
├── plan.md              # This file (/spec-kitty.plan command output)
├── research.md          # Phase 0 output (/spec-kitty.plan command)
├── data-model.md        # Phase 1 output (/spec-kitty.plan command)
├── quickstart.md        # Phase 1 output (/spec-kitty.plan command)
├── contracts/           # Phase 1 output (/spec-kitty.plan command)
└── tasks.md             # Phase 2 output (/spec-kitty.tasks command - NOT created by /spec-kitty.plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this mission. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```
# [REMOVE IF UNUSED] Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [REMOVE IF UNUSED] Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [REMOVE IF UNUSED] Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure: feature modules, UI flows, platform tests]
```

**Structure Decision**: [Document the selected structure and reference the real
directories captured above]

## Complexity Tracking

*Fill ONLY if Charter Check has violations that must be justified*

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| *(none)* | This mission removes complexity rather than adding it: one query changes and no new abstraction, dependency or schema is introduced. | — |

## Implementation Concern Map

*Include this section when the mission has multiple distinct architectural areas that inform how tasks are decomposed.*

> **Note**: Implementation concerns are NOT work packages and are NOT executable units.
> `/spec-kitty.tasks` translates these into executable WPs — one concern may become
> multiple WPs; multiple small concerns may merge into one WP. Do not label concerns
> with WP-style IDs or sequencing language.

### IC-01 — [Name]

- **Purpose**: [One sentence: what this concern addresses and why it matters]
- **Relevant requirements**: [FR-### refs from spec.md]
- **Affected surfaces**: [File paths or module names this concern touches]
- **Sequencing/depends-on**: [IC-## IDs this concern must follow, or "none"]
- **Risks**: [Key coordination notes or implementation risks]

### IC-02 — [Name]

- **Purpose**: [One sentence]
- **Relevant requirements**: [FR-### refs]
- **Affected surfaces**: [Paths/modules]
- **Sequencing/depends-on**: [IC-## or "none"]
- **Risks**: [Notes]
