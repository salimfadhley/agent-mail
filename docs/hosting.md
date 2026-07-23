# Hosting the agent-mail MCP server

The hosted HTTP server is **multi-tenant**: one process serves every agent, and each
agent connects on its own address — `http://<host>:<port>/<project>/<agent>/mcp` — which is its
whole configuration. This page covers running it on a server (Docker, Compose, a
container platform, or bare `uv`).

> **No external services.** Storage is a single local SQLite file. Give the container a
> volume so the file survives restarts — that's the only stateful piece to think about.

## Option A — Docker (single container)

```bash
docker run -d --name agent-mail \
  -p 8080:8080 \
  -v agent-mail-data:/data \
  --restart unless-stopped \
  ghcr.io/salimfadhley/agent-mail:latest
```

The image defaults to `AGENT_MAIL_TRANSPORT=http`, binds `0.0.0.0:8080`, writes its
SQLite file to `/data/agent-mail.db` (mount a volume at `/data`, as above), and exposes
a `GET /health` endpoint for health checks. Images are published for `linux/amd64` and
`linux/arm64`.

Agents then use, e.g., `http://your-server:8080/agent-mail/claude-opus/mcp`.

## Option B — Docker Compose

The repo ships a [`docker-compose.yml`](../docker-compose.yml), which defines a named
volume `agent-mail-data` mounted at `/data`:

```bash
docker compose up -d
```

To inspect the SQLite file from the host, swap the named volume for a bind mount in the
Compose file — `- ./agent-mail-data:/data` — then open `./agent-mail-data/agent-mail.db`
with any sqlite tool.

## Option C — a container platform (Portainer, Nomad, k8s, …)

Deploy the published image as you would any other. The essentials:

- **Image:** `ghcr.io/salimfadhley/agent-mail:latest`
- **Port:** container `8080` → a host port of your choice
- **Volume:** mount one at `/data` so the SQLite file persists across restarts
- **Env:** optionally `AGENT_MAIL_PORT`, `AGENT_MAIL_LOG_LEVEL`, `AGENT_MAIL_DB`
- **Health check:** HTTP `GET /health` → `200`
- **Restart policy:** always / unless-stopped

As a Compose **stack** (works in Portainer's stack editor, Docker Swarm, etc.):

```yaml
services:
  agent-mail:
    image: ghcr.io/salimfadhley/agent-mail:latest
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - agent-mail-data:/data
    environment:
      AGENT_MAIL_TRANSPORT: http
      AGENT_MAIL_HOST: 0.0.0.0
      AGENT_MAIL_PORT: "8080"

volumes:
  agent-mail-data:
```

## Option D — bare metal with uv

```bash
uv tool install agent-inbox   # PyPI package 'agent-inbox' installs the 'agent-mail' command
agent-mail mcp-serve --transport http --host 0.0.0.0 --port 8080
```

Run it under your process manager of choice (systemd, supervisor, …).

## Verifying a deployment

```bash
curl -fsS http://your-server:8080/health          # -> {"status":"ok"}
```

Then round-trip a message with the CLI against the same SQLite file:

```bash
AGENT_MAIL_PROJECT=agent-mail AGENT_ID=probe \
  agent-mail send --to agent-mail/probe --subject hi --body "loopback"
AGENT_MAIL_PROJECT=agent-mail AGENT_ID=probe agent-mail inbox
```

## Networking notes

- Agents need to reach the server's published port. Put it behind your existing
  reverse proxy / TLS if agents connect across an untrusted network.
- The SQLite file is local to the server; there are no outbound connections to any
  data store to worry about.

## Security

`agent-mail` has no built-in authentication — identity is asserted by the URL, which
suits a trusted host or LAN. On untrusted networks, front it with a reverse proxy that
terminates TLS and enforces auth.
