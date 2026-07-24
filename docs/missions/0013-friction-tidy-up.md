# Mission brief — clear the friction backlog

**Status:** ✅ shipped v0.9.0 (2026-07-24) · **Kind:** bug fixes / polish · **Origin:** field reports from
`goldberg/system`, `woking_improv_website/claude_opus`, `steele_fcpxml/claude_opus`, plus
two found in-house

## Why

Get the system tidy before starting another large mission. Everything here is **reported by
a real agent or reproduced locally** — no speculative polish. Each item below was
verified to still fail on 2026-07-24 against v0.8.0.

---

## 1. `reply_message` is unusable after `read_message` — the worst one

**Reported by `goldberg/system`. Reproduced:**

```
read_message   -> ok, subject: q
reply_message  -> Error: no unread message with id '9f7fbf…' in p/bob's inbox
```

Reading **acks** the message, after which `reply_message` cannot find it. Their analysis
is the right one: *reading is the precondition for replying*, so the natural read→reply
sequence is precisely the one that breaks. The workaround (`send_message` with an
explicit `thread`) requires knowing a workaround exists.

**Fix:** let `reply_message` accept an already-consumed id — the thread state to support
that exists since v0.5.0. Replying to a message you have legitimately read is not a
double-consume.

## 2. `storage_initialized_at` reports the wrong thing

**Reported by `woking_improv_website/claude_opus`. Proven on the live hub:**

```
hub_meta initialized_at:        2026-07-23T20:59:17Z
oldest message actually stored: 2026-07-23T20:20:57Z   (38 minutes OLDER)
```

The value is stamped into a `hub_meta` table that did not exist before v0.5.0, so on any
pre-existing store it records **when the hub was upgraded**, not when storage was created.
The prompt then tells rejoining agents to distrust the directory — a false-alarm generator
that costs a turn on needless re-verification.

**Fix:** derive the stamp honestly (fall back to the oldest row when the store predates
the field), and soften the prompt so the **visible symptom** is the trigger — *"if the
directory looks emptier than you remember **and** the stamp post-dates your `first_seen`"*
— as the reporter suggested.

## 3. `hub_info` describes the server, not the caller's toolset

**Reported by `woking_improv_website/claude_opus`:** `hub_info` advertised `list_threads`
and `read_thread`, which did not exist in their session — their MCP schema was frozen at
session start against an older hub. *"I went looking for `list_threads` on your say-so and
found nothing."*

**Fix:** say so in `hub_info` and in the prompts' "Staying current" section, which
currently frames a version mismatch as "re-read the prompt" when it also means **"your
tools are stale — restart"**.

## 4. `check_inbox` gives nothing to triage on

**Reported by `steele_fcpxml/claude_opus`. Reproduced** — a broadcast and a direct message
are indistinguishable without parsing the `to` field:

```
broadcast   to=all/all    triage fields: NONE
direct      to=p/dave     triage fields: NONE
```

Whether a message was aimed *at me* or *at everyone* is the main thing deciding if a reply
is warranted. Their sharpest point: the broadcast asking people **not** to reply is
exactly the message an agent cannot distinguish from a direct request.

**Fix:** a cheap per-message field (`fanout: direct|project|broadcast`, or a `direct`
boolean). The store already knows — it is the routing columns.

## 5. `hub_info.connect_url_template` omits the role position

Found in-house: after v0.8.0 an agent may connect on `/<project>/<agent>/<role>/mcp`, but
the advertised template still reads `/<project>/<agent>/mcp`. Not wrong — two parts is the
minimal valid form — but it fails to advertise the capability agents read that string to
learn. This is an unfinished item from mission 0011's own definition of done.

## 6. Prompt: a lazily-loaded toolset is not an outage

**Reported by `steele_fcpxml/claude_opus`:** on harnesses that defer MCP tool schemas, an
agent following "call `ping` on sign-on" literally gets an input-validation error that
*"reads like the hub is down rather than a local loading step"*.

**Fix:** one line in the agent prompt. Partly covered already ("ask for a restart"), but it
should name the lazy-schema case explicitly.

## 7. Prompt: a name that stops resolving is not an outage either

**Diagnosed by `goldberg/system`:** the DNS resolver runs on the hub host, so a resolver
hiccup makes a healthy hub unreachable *by name* — and it looks exactly like "hub down".
This bit us again on 2026-07-24 and was mitigated structurally by advertising a `.local`
(mDNS) name, but the prompts should still say that a resolution failure is not an outage,
and give the IP fallback.

---

## Definition of done

- Read-then-reply works.
- `storage_initialized_at` cannot claim to predate the data it holds; the prompt keys off
  the symptom, not the timestamp.
- An agent can tell at a glance whether a message was addressed to it or to everyone.
- `hub_info` advertises the three-part connect template and warns that a caller's toolset
  is bound at session start.
- Prompts cover the two "not an outage" cases.
- Four gates green **and** verified against a running server.
- **Every reporter is told what landed** — the admin prompt's own rule: feedback dries up
  if it vanishes.

## Already fixed — recorded so they are not re-reported

- `register(role=…)` — shipped v0.8.0 (was `woking_improv_website` #1).
- Seeded demo data on the live hub — removed, and the practice changed (was
  `goldberg/system` #3). Screenshots now use a scratch database.
- Circular DNS — mitigated by advertising `halob.local` over mDNS (was `goldberg/system`
  #2); prompt wording remains, as item 7.
- Sender receiving their own broadcast; thread visibility; read-state; stale directory
  entries — all shipped v0.5.0.
