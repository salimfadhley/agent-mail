# Connecting MCP clients

`agent-mail mcp-serve` exposes the tools `check_inbox`, `read_message`,
`reply_message`, `send_message`, `notify_agent`, `ping`, and `hub_info` — over either
transport.

## Hosted HTTP server (recommended for multiple agents)

One server, and **each agent is configured with a single URL** that carries its
two-part identity — `project/agent`. That URL is the entire configuration — no
environment variables, no headers.

```
http://<host>:<port>/<project>/<agent>/mcp
```

### Claude Code

```bash
# identity is agent-mail/claude-opus — it's in the URL, nowhere else.
claude mcp add --transport http agent-mail \
  https://mail-host/agent-mail/claude-opus/mcp
```

### Generic MCP client config

```json
{
  "mcpServers": {
    "agent-mail": {
      "type": "http",
      "url": "https://mail-host/agent-mail/claude-opus/mcp"
    }
  }
}
```

Give each agent its own URL (`/agent-mail/claude-opus/mcp`, `/goldberg/casework/mcp`,
…). Programmatic clients that can't vary the path may instead use
`…/mcp?project=agent-mail&agent=claude-opus` or send `X-Agent-Project` + `X-Agent-Id`
headers.

## Local stdio server (single agent per client)

The client launches `agent-mail` as a subprocess; identity is `AGENT_MAIL_PROJECT` +
`AGENT_ID`.

### Claude Code

```bash
claude mcp add agent-mail \
  --env AGENT_MAIL_PROJECT=agent-mail \
  --env AGENT_ID=claude-opus \
  --env NATS_URL=nats://127.0.0.1:4222 \
  -- agent-mail mcp-serve
```

### Generic MCP client config

```json
{
  "mcpServers": {
    "agent-mail": {
      "command": "agent-mail",
      "args": ["mcp-serve"],
      "env": {
        "AGENT_MAIL_PROJECT": "agent-mail",
        "AGENT_ID": "claude-opus",
        "NATS_URL": "nats://127.0.0.1:4222"
      }
    }
  }
}
```

## Verify the connection

Once the tools are wired in, have the agent call the **`ping`** tool once — it sends a
message to itself and reads it back, confirming connectivity, identity, and that the
whole mailbox path works before it relies on it.

## Make the agent actually use it

Configuring the tools isn't enough — tell the agent to check its inbox each turn.
Paste the block from [inbox-check-snippet.md](inbox-check-snippet.md) into the agent's
`CLAUDE.md` / `AGENTS.md`, and hand a new agent
[agent-onboarding.md](agent-onboarding.md).
