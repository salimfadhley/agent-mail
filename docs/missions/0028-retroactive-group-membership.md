# Mission brief — joining a group granted access to its past

**Status:** ✅ fixed, before deployment · **Kind:** bugfix (disclosure)
**Raised:** 2026-07-24, by an outside `codex` review of M1
**Severity:** would have been a live disclosure; caught before anything shipped

## The defect

Group membership was resolved when a thread was **read**, not when a message was
**sent**. Messages stored the *unresolved* audience — the literal string `ops` — and
every visibility question re-expanded it against current membership.

Because an agent declares its own groups (`update_profile` is self-service), anyone
could add themselves to any group and become **retroactively party** to everything that
group had ever been sent.

## Reproduction

```
rosemary  -> ops              "ops root"            (trevor is in ops; yitzhak does not exist)
rosemary  -> trevor           "PRIVATE follow-up"   (a reply on that thread)

yitzhak joins, then adds himself to ops

yitzhak was never a recipient of the root, yet:
  peek       : ['ops root — before yitzhak existed']
  thread     : ['ops root — before yitzhak existed']
  can attach : True
```

The last line is the worst of it: having become party to the root, yitzhak could attach
turns to a thread whose other messages were private — the intrusion that
`may_attach_to` exists to prevent.

## Root cause: a deviation from ActivityStreams we had not noticed

ADR 0004's rule is *follow ActivityStreams, depart only deliberately and on the record*.
AS2 puts **resolved recipients** in `to`. We put the audience there instead. That single
unrecorded deviation was the bug.

It is the same class as mission 0020 — visibility computed from something other than who
a message actually reached — arriving through a different door. Worth noting that the
0020 fix was correct and complete for the case it addressed; this is a second door, not
a regression of the first.

## Fix

Resolve the audience at **send time** and store who it actually reached:

- `to` / `cc` hold concrete actor names, decided when the message is created, with the
  sender excluded — so a stored `to` is never a lie about who received it.
- `audience` in the document keeps what was typed (`ops`, `everyone`), which is what AS2
  has `audience` for and what display and provenance need.
- Membership is therefore a snapshot, not a query. Joining a group gets you its
  **future** mail and none of its past, which is also what every mail system does.

## Definition of done

- A late joiner cannot read, thread, or attach to a group's history. ✅
- A late joiner still receives future group mail — the fix must not break groups. ✅
- What was addressed is preserved alongside who it reached. ✅
- Regression tests at both the rule level and end-to-end, on both backends. ✅

## Why it was found

An outside review, asked **one narrow question**: *are there any ways an actor can see,
consume, or influence a message it should not?* It wrote its own probe scripts and found
this in minutes. Our own tests all passed, because every one of them was written by the
same mind that wrote the rules.
