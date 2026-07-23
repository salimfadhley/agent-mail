# syntax=docker/dockerfile:1
#
# agent-inbox — hostable MCP server image.
#
# Runs the multi-tenant HTTP MCP server. Agents connect on their own address,
# http://<host>:<port>/<agent>/mcp — that URL is their whole configuration.
#
# Storage is a single SQLite file at /data/agent-inbox.db — mount a volume at /data
# so mail survives restarts. No external services required.
#
# Build:  docker build -t agent-inbox .
# Run:    docker run -p 8080:8080 -v agent-inbox-data:/data agent-inbox

FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# hatch-vcs derives the version from git; there is no .git in the build context,
# so pass one in (CI sets it from the tag). Defaults to a dev version.
ARG VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --frozen --no-editable


FROM python:3.12-slim AS runtime

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 agentmail
COPY --from=build --chown=agentmail:agentmail /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    AGENT_INBOX_TRANSPORT=http \
    AGENT_INBOX_HOST=0.0.0.0 \
    AGENT_INBOX_PORT=8080 \
    AGENT_INBOX_DB=/data/agent-inbox.db

# Persist the SQLite file on a volume (named volume by default; bind-mount to
# inspect it from the host — see docker-compose.yml).
RUN mkdir -p /data && chown agentmail:agentmail /data
VOLUME ["/data"]

USER agentmail
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"

ENTRYPOINT ["agent-inbox", "mcp-serve"]
