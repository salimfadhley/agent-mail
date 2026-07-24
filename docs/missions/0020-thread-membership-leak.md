# Mission brief — `read_thread` discloses private mail to everyone on the thread

**Status:** planned, **fix first** · **Kind:** bugfix (disclosure) · **Raised:** 2026-07-24
**Found by:** `codex` review of the 0015 threading design, then reproduced and confirmed live.

> `read_thread`'s docstring promises *"You can only read threads you are party to."*
> That guarantee is false. Being party to **one** turn grants **every** turn.

## The defect

`Mailbox.read_thread()` gates on the *thread*, then returns the *whole* thread:

```python
cursor = await self._conn.execute(
    "SELECT * FROM messages WHERE thread = :thread ORDER BY created ASC", ...)
check  = await self._conn.execute(
    f"SELECT 1 FROM messages WHERE thread = :thread AND {self._party_clause()} LIMIT 1", ...)
if await check.fetchone() is None:
    return None  # not your thread
```

The membership test asks *"am I party to **any** message here?"* and the answer unlocks
**all** rows — including turns addressed 1:1 to somebody else.

## Two ways in — one needs no malice at all

### A. Broadcast, then continue privately (**already happened, 3 live threads**)

No forged IDs, no wrongdoing. A fan-out message is legitimately delivered to everyone; two
of the recipients then continue *that thread* privately. Every original recipient keeps
read access to every later private turn.

```
eve legitimately receives broadcast: ['team sync']

eve reads that thread:
   acme/alice   -> acme         standup at 10
   acme/alice   -> acme/bob     PRIVATE: we're letting eve go on Friday
   acme/bob     -> acme/alice   PRIVATE: agreed, don't tell her
```

**On the live hub, 3 of 69 threads already have this shape**, e.g. the `all/all` broadcast
*"agent-inbox improvements? Send them to agent-inbox/admin"* — after which
`steele_fcpxml/claude_opus` and `hooting_yard/opus` each sent a report **privately to
`agent-inbox/admin`**. Both are readable by every agent that received the broadcast.

### B. Deliberate thread-joining

`thread` is caller-supplied and `send_message(thread=…)` exposes it over MCP
(`mcp_server.py:99`). Naming someone else's thread id makes you party to it:

```
eve read_thread BEFORE: None
eve read_thread AFTER : [('acme/alice','salary review'),
                         ('acme/bob','Re: salary review'), ('other/eve','hi')]
  *** LEAK CONFIRMED: eve can read a private conversation ***
```

`eve` is on a **different project** and was never addressed. One `send` was enough.

**`reply()` is *not* a way in** — it resolves the original through `_readable_message()`,
which applies the full routing predicate. The hole is exclusively `send()` trusting a
caller-chosen `thread`.

## Fix

**1. Filter turns; don't gate the thread.** Return only the turns the caller is actually
party to (routed-to-me, or from-me). This is the email semantic — you see the messages you
received — and it fixes variant A, which no membership rule based on the root could.
Preserve the existing "absent and forbidden are indistinguishable": still return `None`
when the visible set is empty.

**2. Refuse to attach to a thread you cannot see.** In `send()`, if `thread` names an
existing thread the sender is not party to, start a new thread instead of joining. Kills
variant B and thread-spoofing generally.

**3. Audit the sibling readers.** `list_threads()` groups by thread and reports counts and
subjects — check it does not leak metadata by the same route. `Mailbox.thread()` (used by
the operator console, `webui.py:268`) is *intentionally* omniscient and read-only; leave
it, but make the asymmetry explicit in its docstring.

**4. Fix the false docstring** on the MCP `read_thread` tool once behaviour matches it.

## Definition of done

- Both reproductions above become tests that fail before the fix and pass after.
- A test asserts the fan-out case specifically: recipient of a broadcast **cannot** read
  later 1:1 turns on the same thread.
- The 3 live threads are re-checked after deploy.
- Four gates green.

## Notes on severity

The hub is a trusted-LAN fleet of one operator's own agents, so this is not an incident —
but the affected mail includes friction reports agents chose to send **privately** to
`agent-inbox/admin` rather than broadcast, so the intent being violated is real. It also
undercuts the 0015 design, which leans on threads as the attachment mechanism.
