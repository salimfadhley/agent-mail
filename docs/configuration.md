# Configuration

agent-mail resolves every setting from four layers. **Later layers win:**

```
field defaults  <  baked defaults.toml  <  runtime --config file  <  environment variables
```

- **Baked defaults** ship inside the package ([`src/agent_mail/defaults.toml`](../src/agent_mail/defaults.toml)) and document every option.
- **Runtime config file** — a TOML file you provide with `--config path.toml` (or `AGENT_MAIL_CONFIG=path.toml`). Good for developers and for `uv`-based runs.
- **Environment variables** — the last word. Ideal for containers (set them in Portainer, compose, or `-e`).

Every setting has **one canonical name**, used identically as a lowercase TOML key or its UPPERCASE environment variable — e.g. TOML `nats_url` is env `NATS_URL`.

## Two typical setups

**Container / homelab (env-first):**

```bash
docker run -p 8080:8080 \
  -e NATS_URL=nats://your-nats:4222 \
  -e AGENT_MAIL_HUB=homelab \
  -e AGENT_MAIL_ADMIN_AGENT=admin \
  ghcr.io/salimfadhley/agent-mail:latest
```

**Developer / uv (file-first):** copy `defaults.toml`, edit, and point at it:

```bash
uv run agent-mail --config ./agent-mail.toml mcp-serve
# any env var still overrides a value in the file
```

Check what actually got loaded (secrets masked):

```bash
agent-mail doctor
```

## Parameters

### NATS connection

| TOML key / env var | Default | Meaning |
|---|---|---|
| `nats_url` / `NATS_URL` | `nats://127.0.0.1:4222` | NATS server (JetStream). Use `tls://…` for TLS. |
| `nats_token` / `NATS_TOKEN` | — | Token auth (secret; masked in `doctor`). |
| `nats_user` / `NATS_USER` | — | Username auth. |
| `nats_password` / `NATS_PASSWORD` | — | Password auth (secret; masked). |
| `nats_creds_file` / `NATS_CREDS_FILE` | — | Path to a NATS `.creds` file (NGS / operator JWT). |
| `nats_ca_file` / `NATS_CA_FILE` | — | Path to a TLS CA certificate. |

Set at most one auth style. Point the `*_file` options at mounted Docker/Portainer secrets.

### Identity

| TOML key / env var | Default | Meaning |
|---|---|---|
| `agent_id` / `AGENT_ID` | — | This agent's id — for the CLI and single-agent (stdio) servers. Leave unset for a hosted multi-agent http server, where identity comes from each agent's URL. |

### MCP server

| TOML key / env var | Default | Meaning |
|---|---|---|
| `transport` / `AGENT_MAIL_TRANSPORT` | `stdio` | `stdio` (local, one agent) or `http` (hosted, multi-agent). |
| `host` / `AGENT_MAIL_HOST` | `127.0.0.1` | Bind host for `http`. Use `0.0.0.0` in a container. |
| `port` / `AGENT_MAIL_PORT` | `8080` | Bind port for `http`. |
| `path` / `AGENT_MAIL_PATH` | `/mcp` | Mount path; agents connect on `/<agent>{path}`. |
| `public_url` / `AGENT_MAIL_PUBLIC_URL` | — | Advertised base URL when behind a reverse proxy (used in `hub_info`). |

### Hub identity & administration

Advertised (non-secret) to agents via the `hub_info` MCP tool and `GET /`.

| TOML key / env var | Default | Meaning |
|---|---|---|
| `hub` / `AGENT_MAIL_HUB` | `agent-mail` | Hub name; distinguishes multiple hubs on a network. |
| `hub_description` / `AGENT_MAIL_HUB_DESCRIPTION` | — | Human-readable description. |
| `admin_agent` / `AGENT_MAIL_ADMIN_AGENT` | — | Agent id to mail for help (agents can `send_message` to it). |
| `issue_url` / `AGENT_MAIL_ISSUE_URL` | — | Where to raise a ticket. |
| `contact` / `AGENT_MAIL_CONTACT` | — | Human contact (email, name). |

### Operations

| TOML key / env var | Default | Meaning |
|---|---|---|
| `log_level` / `AGENT_MAIL_LOG_LEVEL` | `WARNING` | `DEBUG` … `ERROR`. |

### Meta

| Env var | Meaning |
|---|---|
| `AGENT_MAIL_CONFIG` | Path to the runtime TOML config file (same as `--config`). |

## Planned settings

On the [roadmap](missions/); not accepted yet (to avoid dead config):

- **[SQLite backend](missions/0002-sqlite-backend.md)** — `backend` (`nats` | `sqlite`)
  and `db` (file path). A zero-infrastructure single-box mode that needs no NATS server.
- **[Elasticsearch audit log](missions/0001-elasticsearch-audit-log.md)** — `es_url`,
  `es_api_key`, `es_index`, `es_ca_file`, `audit_bodies`. An optional NATS→ES subscriber;
  if unset, agent-mail runs exactly as today.
- **Retention** — how long unread mail persists (JetStream max age / size / count).
- **Max message size** — the Claim-Check threshold for large payloads.
