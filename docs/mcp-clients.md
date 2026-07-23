# Connecting MCP clients

`agent-inbox mcp-serve` exposes the tools `check_inbox`, `read_message`,
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
# identity is agent-inbox/claude-opus — it's in the URL, nowhere else.
claude mcp add --transport http agent-inbox \
  https://mail-host/agent-inbox/claude-opus/mcp
```

### Generic MCP client config

```json
{
  "mcpServers": {
    "agent-inbox": {
      "type": "http",
      "url": "https://mail-host/agent-inbox/claude-opus/mcp"
    }
  }
}
```

Give each agent its own URL (`/agent-inbox/claude-opus/mcp`, `/goldberg/casework/mcp`,
…). Programmatic clients that can't vary the path may instead use
`…/mcp?project=agent-inbox&agent=claude-opus` or send `X-Agent-Project` + `X-Agent-Id`
headers.

## Local stdio server (single agent per client)

The client launches `agent-inbox` as a subprocess; identity is `AGENT_INBOX_PROJECT` +
`AGENT_ID`.

### Claude Code

```bash
claude mcp add agent-inbox \
  --env AGENT_INBOX_PROJECT=agent-inbox \
  --env AGENT_ID=claude-opus \
  -- agent-inbox mcp-serve
```

### Generic MCP client config

```json
{
  "mcpServers": {
    "agent-inbox": {
      "command": "agent-inbox",
      "args": ["mcp-serve"],
      "env": {
        "AGENT_INBOX_PROJECT": "agent-inbox",
        "AGENT_ID": "claude-opus"
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
