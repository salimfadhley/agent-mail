# Hosting the agent-mail MCP server

The hosted HTTP server is **multi-tenant**: one process serves every agent, and each
agent connects on its own address — `http://<host>:<port>/<project>/<agent>/mcp` — which is its
whole configuration. This page covers running it on a server (Docker, Compose, a
container platform, or bare `uv`).

> **Prerequisite: NATS with JetStream.** `agent-mail` does not run NATS for you. Point
> `NATS_URL` at an existing server. For a quick all-in-one trial, the Compose file
> below can start a throwaway NATS for you.

## Option A — Docker (single container)

```bash
docker run -d --name agent-mail \
  -p 8080:8080 \
  -e NATS_URL=nats://your-nats-host:4222 \
  --restart unless-stopped \
  ghcr.io/salimfadhley/agent-mail:latest
```

The image defaults to `AGENT_MAIL_TRANSPORT=http`, binds `0.0.0.0:8080`, and exposes a
`GET /health` endpoint for health checks. Images are published for `linux/amd64` and
`linux/arm64`.

Agents then use, e.g., `http://your-server:8080/agent-mail/claude-opus/mcp`.

## Option B — Docker Compose

The repo ships a [`docker-compose.yml`](../docker-compose.yml):

```bash
# Against an existing NATS:
NATS_URL=nats://your-nats-host:4222 docker compose up -d

# Or start a throwaway NATS alongside for a trial:
docker compose --profile with-nats up -d
```

## Option C — a container platform (Portainer, Nomad, k8s, …)

Deploy the published image as you would any other. The essentials:

- **Image:** `ghcr.io/salimfadhley/agent-mail:latest`
- **Port:** container `8080` → a host port of your choice
- **Env:** `NATS_URL` (required), optionally `AGENT_MAIL_PORT`, `AGENT_MAIL_LOG_LEVEL`
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
    environment:
      NATS_URL: nats://your-nats-host:4222
      AGENT_MAIL_TRANSPORT: http
      AGENT_MAIL_HOST: 0.0.0.0
      AGENT_MAIL_PORT: "8080"
```

## Option D — bare metal with uv

```bash
uv tool install agent-mail
NATS_URL=nats://your-nats-host:4222 \
  agent-mail mcp-serve --transport http --host 0.0.0.0 --port 8080
```

Run it under your process manager of choice (systemd, supervisor, …).

## Verifying a deployment

```bash
curl -fsS http://your-server:8080/health          # -> {"status":"ok"}
```

Then round-trip a message with the CLI pointed at the same NATS:

```bash
NATS_URL=nats://your-nats-host:4222 AGENT_ID=probe \
  agent-mail send --to probe --subject hi --body "loopback"
NATS_URL=nats://your-nats-host:4222 AGENT_ID=probe agent-mail inbox
```

## Networking notes

- The server needs to reach NATS at `NATS_URL`. From inside a container, a NATS
  running on the Docker host is typically `nats://host.docker.internal:4222` (or the
  host's LAN IP); NATS in the same Compose project is reachable by service name.
- Agents need to reach the server's published port. Put it behind your existing
  reverse proxy / TLS if agents connect across an untrusted network.

## Security

`agent-mail` has no built-in authentication — identity is asserted by the URL, which
suits a trusted host or LAN. On untrusted networks, front it with a reverse proxy that
terminates TLS and enforces auth, and keep NATS unexposed to the public internet.
