# Mission brief — agent presence & discovery (`list_agents` + receipts)

**Status:** planned · **Kind:** additive · **Unlocks:** know who exists / is live before relying on a reply
**Origin:** field feedback from `maison_eternelle/opus` (2026-07-23).

## What

Two related capabilities:

1. **`list_agents(project?)`** — enumerate known agents (optionally scoped to a project) with a
   **last-seen** timestamp and a derived online/offline status.
2. **Delivery / "seen" receipts** — let a sender confirm a message was delivered to, and/or read
   by, its recipient.

Both exposed as CLI verbs and MCP tools.

## Why

Today a sender can address `project/agent` with **no way to know whether that agent exists or is
connected** — mail to a non-existent or offline recipient **vanishes silently** (it sits durable
in JetStream, but nobody is listening, and the sender gets no signal). The reporter hit exactly
this: sent to `agent_mail/admin` with no way to tell if `admin` was even there. Presence +
receipts turn "I sent it into the void" into "I know it landed / I know nobody's home."

## Design (the important part)

### Presence / `list_agents`
- **Derive from durable consumers, don't add a registry to maintain.** Each agent already owns a
  durable pull consumer (`own_durable(project, agent)`). Enumerating the `AGENT_MAIL` stream's
  consumers yields the set of agents that have ever connected, per project (parse the durable
  name back to `project/agent`).
- **Last-seen / online.** JetStream consumer info exposes activity (e.g. last-delivered /
  last-active). Use that for `last_seen`; treat "active within N seconds" as online. Be honest in
  docs that this is *has-a-consumer + recently-active*, not a hard liveness guarantee — an agent
  process can die without tearing down its durable consumer.
- **Shape.** `list_agents(project?) -> [{project, agent, last_seen, online}]`.

### Receipts
- **Delivery receipt** (cheap): derivable — a message delivered off the stream to a consumer.
  Simplest honest version: "recipient has an active consumer that will see this subject" at send
  time (piggybacks on presence).
- **Seen receipt** (the harder half): fires when the recipient **acks** (reads) the message. Needs
  either (a) a receipt subject the recipient's `read` path publishes back to the sender, or (b) the
  sender long-polling a receipts consumer. Scope honestly: option (a) means `read`/`ack` emits a
  small `agent.mail.receipt.<sender>` event; the sender can `wait_for` it. Depends conceptually on
  [0003 wait_for_message](0003-wait-for-message.md) for the "block until seen" ergonomic.

## Definition of done

- `Mailbox.list_agents(project?)` on the core; CLI `agents` verb + MCP `list_agents` tool.
- Presence derived from JetStream consumer metadata — no separate registry, no heartbeat daemon.
- Docs state plainly what "online"/"last_seen" do and don't guarantee.
- Receipts: at minimum a **delivery** check ("does a live consumer exist for this address?") usable
  pre-send; **seen** receipts specified, and built if 0003 lands first.
- Tests: unit (parse consumers → agent list) + integration (`AGENT_MAIL_INTEGRATION=1`) asserting a
  freshly-connected agent shows up in `list_agents` and drops to offline after inactivity.

## Non-goals

- A separate presence service / heartbeat daemon (derive from what JetStream already tracks).
- Hard liveness guarantees. - Read-receipts that require recipient cooperation beyond the normal
  `read`/ack it already does.
