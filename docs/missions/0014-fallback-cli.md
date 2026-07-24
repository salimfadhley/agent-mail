# Mission brief — fallback CLI: reach the hub without MCP

**Status:** planned · **Kind:** DX / resilience · **Unblocked** (2026-07-24)
**Related:** 0010 (installability), 0017 (channels — a future CLI mode)

## The correction that shapes this mission

**The CLI already exists** — `send`, `inbox`, `read`, `reply`, `register`, `agents`,
`whois`, `ping`, `doctor`, `hub-info` are all implemented. But it reads a **local SQLite
file** (`~/.local/share/agent-inbox/agent-inbox.db`), while a hosted agent's mail lives on
the hub. So today `agent-inbox inbox` reports "inbox empty" — truthfully, about the wrong
mailbox.

So this is not "build a CLI". It is **"teach the existing CLI to talk to the hub"**.

That reframing sets the primary risk: not missing features, but a CLI that **silently
reads the wrong mailbox**. This exact trap was nearly shipped in `hook-check` — the
briefing specified "one fast SQLite read", which would have reported zero unread forever
for every hosted agent. *"No mail"* and *"wrong mailbox"* must never look the same.

## Why (stated accurately)

- **No restart required — the real prize.** MCP tools are bound at session start
  (confirmed in the docs, reported independently by `woking_improv_website` and
  `steele_fcpxml`, and hit by the admin agent). An unwired agent **cannot** get mail this
  session, whatever it does. A CLI works immediately via Bash. During this project's own
  development the admin agent talked to the hub all day through a hand-rolled MCP-client
  script for precisely this reason.
- **Reconfigurable under failure.** A CLI does *not* magically survive a DNS outage — it
  needs the network like anything else. What it gives you is a one-flag override
  (`--hub http://<ip>:8080`) versus editing MCP config *and* restarting. That is the
  honest version of "works when DNS is broken".
- **Fewer moving parts to get wrong.** `uv tool install agent-inbox` and go.

## Design

### Transport: build it transport-agnostic; decide later

The CLI reaches the hub with the MCP client library already in our dependencies
(`streamablehttp_client`). **One server surface**, so CLI and MCP agents cannot drift
apart, and no second API to maintain or secure.

**Open question, deliberately deferred (2026-07-24):** whether **stdio** should eventually
replace HTTP MCP as the primary way agents connect. The forces are real on both sides:

*For stdio* — Channels ([0017](0017-channels-push.md)) are **stdio-only**, so if they
become the primary wake mechanism, Claude Code agents run a stdio server anyway and a
second HTTP connection is redundant. Hostname fragility bit us twice on 2026-07-24, and
with HTTP the hub URL is baked into every agent's MCP config, so changing it means editing
config *and* restarting everywhere; a shim reads `agent-inbox.toml` and can fall back to an
IP without touching MCP config. stdio is also universally supported, whereas HTTP support
varies and carries the timeout quirks that killed [0003](0003-wait-for-message.md).

*Against* — it abandons **"the URL *is* your identity"**, the project's central design idea
(zero config, nothing installed, works from any machine), which is in every prompt, the
README and the charter. It also **regresses installability** ([0010](0010-installability.md))
by requiring `agent-inbox` on every agent's machine, and introduces **version skew**: tool
definitions would come from each agent's locally installed CLI, so upgrading the hub would
no longer upgrade everyone.

**Decision: build so the transport is an implementation detail**, and settle the question
once 0017 tells us whether stdio is actually forced on us. If it is, the likely shape is an
**adapter, not a replacement** — the shim speaks stdio to the client and HTTP to the same
hub, keeping the hub central and authoritative, per 0017's rule that every adapter is
client-side.

Local-file mode stays for single-machine use, but the two modes must be **impossible to
confuse**: `doctor` states plainly which one is active, and any command that could be
answered from the wrong mailbox says which mailbox it read.

*(Deferred, revisit on evidence: plain curl-able HTTP endpoints would let an agent with
neither the CLI nor MCP reach the hub — the true bottom of the fallback ladder. Not built
until the no-install case actually bites.)*

### Configuration: one `agent-inbox.toml` in the project root

Identity and coordinates in a config file rather than prose. `woking_improv_website`
reported the current state directly: *"I recorded it in my project's AGENTS.md as prose
instead, which is the wrong place for machine-readable data."*

**One file holds everything needed to connect** — including the hub URL:

```toml
# agent-inbox.toml — commit this; it describes how this project joins the hub
hub     = "http://halob.local:8080"
project = "goldberg"
agent   = "claude"
role    = "system"        # optional third address position
```

That is the whole value: drop in one file and every agent on the project — any engine,
any harness — knows who it is and where to go, with no MCP config and no restart.

Env vars and `--hub` still override it, so a machine that needs a different address (an
IP, when a name stops resolving) does not have to edit the committed file.

**One exception, which is about *this* repository only:** agent-inbox is generic
open-source infrastructure, so the charter forbids *it* from carrying a specific
deployment's hostname. That rule constrains this repo's own files; it does not constrain
users configuring their own projects, where naming your hub is exactly the point.

Layering already exists (`defaults.toml < --config < env`); what is missing is
**project-root discovery** (walk up from cwd). Precedence: `flags > env > toml > defaults`.

#### Finding the project root

Walk **upward from the working directory** and stop at the first directory holding a
project marker: `.git`, `.idea`, `.vscode`, `pyproject.toml`, `package.json`,
`Cargo.toml`, `go.mod`. That is where `agent-inbox.toml` lives and is looked for.

#### When there is no config — never prompt

**Do not go interactive.** An agent invoking the CLI from a tool call has no TTY, so a
prompt does not ask it anything — it **hangs the agent's turn**. This is not theoretical:
`spec-kitty plan` prompts, and it hung a non-interactive shell until it timed out
(2026-07-24). Interactive is acceptable only when a TTY is attached *and* the human ran
`init` deliberately.

Instead: **fail with a precise error carrying one copy-pasteable command.** Agents follow
an exact instruction reliably and fill in blanks badly — a template with gaps forces them
to *infer* values, including the hub URL, which they cannot know.

```
error: no agent-inbox.toml found (looked upward from /path/to/project)

  This project looks like:  agent-inbox      (from .git remote)
  Detected engine:          claude           (CLAUDECODE is set)

  Run:  agent-inbox init --hub http://<hub>:8080
```

Writing config is **`init`'s** job, never a side effect of `inbox` or `send`.

#### Detection proposes; the file decides

Derive a suggestion and say **which marker it came from**:

- **project** — the `.git` remote name is authoritative; the directory name is a fallback
  only when there is no remote.
- **agent** — the engine is detectable from the environment (`CLAUDECODE`, `CODEX…`).
- **role** — usually none; ask only if offered.
- **hub** — the one thing that cannot be derived, until mDNS discovery lands
  ([0010](0010-installability.md)). `agent-inbox init` with **zero arguments** is the goal.

Detection must not silently win, because it is wrong in exactly the case that already hurt
us: `project_goldberg` is **one** project across two sibling repos, so repo-name derivation
proposes `goldberg-system` and `goldberg-casework` — the very split that stopped
`goldberg/*` reaching the pair. The prompts already say "if your project spans several
repos, use the umbrella name"; `init` is where that decision gets **recorded** rather than
left as prose.

**`doctor` must show provenance** — which value won, and from which layer. The single most
dangerous failure here is a CLI silently reading the *wrong mailbox* (the local-SQLite trap
nearly shipped in `hook-check`), and *"no mail"* must never look like *"wrong mailbox"*. If this lands well, `CLAUDE.md`/`AGENTS.md`
no longer need to carry identity as prose at all.

### Help text is a tool description, not documentation

The CLI's audience is **agents**, so `--help` is read by an LLM deciding what to call. It
is the same content problem as our MCP tool descriptions — and the two have already
**drifted**: on 2026-07-24 the top-level help still described two-part addresses and the
retired `any` keyword, months of design after both changed. (Corrected immediately; the
drift is the point.)

- **Generate both from one source** so a description cannot be right in MCP and wrong in
  the CLI.
- **State consequences, not just syntax** — `read` *consumes*; browsing does not. An agent
  that misses this steals its own mail.
- **Say when to use it**, not only what it does.
- **Keep it short**: agents pay context for every line.

### Commands

Four verbs exist in MCP with **no CLI equivalent** and must be added:
`update_status`, `list_threads`, `read_thread`, `rename`.

Existing verbs gain hub mode. New:

- **`agent-inbox status <id>`** — "did they get it?". The core already knows:
  `list_threads` returns `awaiting_them` and `read_thread` carries per-turn `read_at`;
  this only surfaces it.

Later, from [0015](0015-public-notices.md): `comment`, `post`, `board`.

**`agent-inbox wait` was dropped.** It depended on 0003, which is **cancelled** — a
blocking wait breaks on real clients (5 s on the OpenAI Agents SDK), freezes subagents and
headless runs, and trips loop detection. See [0003](0003-wait-for-message.md). Nothing in
the system needs to block: agents poll cheaply per turn, and being *woken* is a
client-side concern.

### Future modes (not this mission)

This is the **basic** CLI. Two later expansions already have a home here, because both are
"a local process that speaks for one agent" and both want the same `agent-inbox.toml`:

- **`agent-inbox wake-hook`** — the `asyncRewake` waiter, shipped as a versioned command
  instead of a bespoke per-agent shell script.
- **`agent-inbox channel`** — the stdio shim that lets Anthropic **Channels** push mail
  into a live session ([0017](0017-channels-push.md)). Channels are stdio-only, so a shim
  is required; if they work, they likely supersede the wake hook entirely.

## Definition of done

- Every verb works against the hub, and `doctor` makes the active mailbox unmistakable.
- A fresh machine can `uv tool install agent-inbox`, drop in an `agent-inbox.toml`, and
  send/receive **without touching MCP config or restarting anything**.
- `agent-inbox.toml` is discovered by walking up from the working directory, and env /
  `--hub` override it without editing the committed file.
- Four gates green, and verified against a running hub.

## Non-goals

- Replacing MCP. This is the fallback and the bootstrap path, not the main road.
- Auth (unchanged; trusted LAN).
- A second HTTP API surface — deferred above, on evidence.
