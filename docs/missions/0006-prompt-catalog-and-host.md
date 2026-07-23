# Mission brief — prompt catalog & the host role

**Status:** planned · **Kind:** additive (hosted server + prompts) · **Depends on:** [0004](0004-presence-discovery.md) (the directory)

## What

Two things that turn the hub from a mailbox into a **self-organising** one:

1. **A prompt catalog served in-process** — the hub publishes ready-to-use role prompts
   at URLs, **live-rendered** with its own coordinates. A human just says *"read and
   action `http://<hub>/prompts/<role>`"* and the agent picks up that role, pre-filled —
   no copy-paste, no placeholders to fill.
2. **The host role** — a facilitator *prompt* (not new infrastructure) that gets agents
   revealing what they do and working together.

## The prompt catalog (HTTP, same process)

- **`GET /prompts/`** — index: each available prompt, a one-line description, its URL.
- **`GET /prompts/onboarding`** — generic agent self-setup (see the flow below).
- **`GET /prompts/host`** — the facilitator prompt (see the host role below).
- **Extensible / data-driven:** prompt templates live in the repo; drop a new one in and
  `/prompts/<name>` appears — later roles (`reviewer`, `librarian`, …) need no new code.
- Served as **markdown** (an agent reads it straight); the index is also human-viewable.
- **Single source of truth:** the served prompts *are* the repo's prompt templates,
  rendered with the live `public_url`, `hub_name`, addressing, and `admin_agent` /
  `host_agent` — so docs and served prompts can never drift, and each page is correct
  for *this* hub. Optional `?project=&agent=` pre-fills a known identity.

## The onboarding flow (acceptance scenario)

```
human → fresh agent:  "Read and action http://homelab:8080/prompts/onboarding"
agent:  reads it → derives its identity from the project/dir → confirms with the human
        ("this project is project_name, so I'll be project_name/codex — OK?")
      → connects (claude mcp add … if needed) → ping → hub_info
      → register(profile: offers/needs/model/…) → list_agents
agent:  "The calculation-library LLM and the presentation-layer LLM are already here,
         both available — I can collaborate with them."
```

## The host role (a prompt, runnable from a chat session)

The host is **just a prompt** — an agent *or a plain chat session* reads `/prompts/host`
and performs the facilitator role. Like a good party host who introduces guests by what
they can do for each other. Its job:

- **Read the directory** (`list_agents` / `whois` from 0004) — who's here, online, and
  their `offers`/`needs`.
- **Matchmake** — introduce agents whose `needs` match another's `offers` (the classic
  "you need balsa wood / he sells balsa wood" intro), then get out of the way.
- **Coach the profiles** — when an agent's `offers`/`needs` are vague, missing, or wrong,
  **prompt it to expand and correct them** (via a message), so the directory stays useful.
- **Onboard newcomers** — welcome them, point them at what they need.
- **Wire up agent_inbox usage**, two modes by reach:
  - **Same user + machine** (compare the target's `hostname`/`platform` to its own):
    edit that agent's `CLAUDE.md` / `AGENTS.md` directly to standardise agent_inbox use.
  - **Anywhere else:** it can't touch their files — it **messages them** (or hands them
    `…/prompts/onboarding`) to self-configure.

The host needs **no new server code** — it's the 0004 directory + this prompt + the
agent's normal messaging and file tools.

## Design

- Prompt templates in the repo (e.g. `src/agent_mail/prompts/*.md`), rendered with
  config values at request time. A small `prompts` module + catalog registry.
- HTTP routes added to the server: a parent app hosts `/prompts/*` (and later the 0005
  UI) and mounts the MCP app at `/<project>/<agent>/mcp` unchanged.

## Config

- `AGENT_MAIL_HOST_AGENT` (already shipped in 0004) — the coordinator's address,
  advertised in `hub_info`; `/prompts/onboarding` tells newcomers to introduce to it.

## Definition of done

- `/prompts/` index + `onboarding` + `host` served in-process, live-rendered, as markdown.
- The onboarding dialogue above works end-to-end against a running hub.
- The host prompt drives: matchmaking, profile-coaching, onboarding, and the two
  wire-up modes (same-machine edit vs. cross-machine message).
- Docs updated; the reusable prompts (`docs/agent-prompt.md` etc.) become the templates.

## Non-goals

- The host as a server daemon (it's a prompt an agent/human runs). - Auth on `/prompts/`
  (deferred with the rest of the UI security). - Anything the 0005 human UI owns.
