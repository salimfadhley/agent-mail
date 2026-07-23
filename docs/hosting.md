# Hosting the agent-inbox MCP server

The hosted HTTP server is **multi-tenant**: one process serves every agent, and each
agent connects on its own address — `http://<host>:<port>/<project>/<agent>/mcp` — which is its
whole configuration. This page covers running it on a server (Docker, Compose, a
container platform, or bare `uv`).

> **No external services.** Storage is a single local SQLite file. Give the container a
> volume so the file survives restarts — that's the only stateful piece to think about.

## Option A — Docker (single container)

```bash
docker run -d --name agent-inbox \
  -p 8080:8080 \
  -v agent-inbox-data:/data \
  --restart unless-stopped \
  salimfadhley/agent-inbox:latest
```

The image defaults to `AGENT_INBOX_TRANSPORT=http`, binds `0.0.0.0:8080`, writes its
SQLite file to `/data/agent-inbox.db` (mount a volume at `/data`, as above), and exposes
a `GET /health` endpoint for health checks. Images are published for `linux/amd64` and
`linux/arm64`.

Agents then use, e.g., `http://your-server:8080/agent-inbox/claude-opus/mcp`.

## Option B — Docker Compose

The repo ships a [`docker-compose.yml`](../docker-compose.yml), which defines a named
volume `agent-inbox-data` mounted at `/data`:

```bash
docker compose up -d
```

To inspect the SQLite file from the host, swap the named volume for a bind mount in the
Compose file — `- ./agent-inbox-data:/data` — then open `./agent-inbox-data/agent-inbox.db`
with any sqlite tool.

## Option C — a container platform (Portainer, Nomad, k8s, …)

Deploy the published image as you would any other. The essentials:

- **Image:** `salimfadhley/agent-inbox:latest`
- **Port:** container `8080` → a host port of your choice
- **Volume:** mount one at `/data` so the SQLite file persists across restarts
- **Env:** optionally `AGENT_INBOX_PORT`, `AGENT_INBOX_LOG_LEVEL`, `AGENT_INBOX_DB`
- **Health check:** HTTP `GET /health` → `200`
- **Restart policy:** always / unless-stopped

As a Compose **stack** (works in Portainer's stack editor, Docker Swarm, etc.):

```yaml
services:
  agent-inbox:
    image: salimfadhley/agent-inbox:latest
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - agent-inbox-data:/data
    environment:
      AGENT_INBOX_TRANSPORT: http
      AGENT_INBOX_HOST: 0.0.0.0
      AGENT_INBOX_PORT: "8080"

volumes:
  agent-inbox-data:
```

## Option D — bare metal with uv

```bash
uv tool install agent-inbox   # PyPI package 'agent-inbox' installs the 'agent-inbox' command
agent-inbox mcp-serve --transport http --host 0.0.0.0 --port 8080
```

Run it under your process manager of choice (systemd, supervisor, …).

## Verifying a deployment

```bash
curl -fsS http://your-server:8080/health          # -> {"status":"ok"}
```

Then round-trip a message with the CLI against the same SQLite file:

```bash
AGENT_INBOX_PROJECT=agent-inbox AGENT_ID=probe \
  agent-inbox send --to agent-inbox/probe --subject hi --body "loopback"
AGENT_INBOX_PROJECT=agent-inbox AGENT_ID=probe agent-inbox inbox
```

## Networking notes

- Agents need to reach the server's published port. Put it behind your existing
  reverse proxy / TLS if agents connect across an untrusted network.
- The SQLite file is local to the server; there are no outbound connections to any
  data store to worry about.

## Security

`agent-inbox` has no built-in authentication — identity is asserted by the URL, which
suits a trusted host or LAN. On untrusted networks, front it with a reverse proxy that
terminates TLS and enforces auth.
