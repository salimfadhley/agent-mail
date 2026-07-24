# syntax=docker/dockerfile:1
#
# agent-mailbox — the hub.
#
# One HTTP API in ActivityStreams, over a SQLite file. Every other surface — the CLI,
# a local MCP server, the web console — is a client of this, not part of it, so none of
# them is in this image.
#
# Storage is a single file at /data/agent-mailbox.db; mount a volume at /data so mail
# survives restarts. No external services.
#
# Build:  docker build -t agent-mailbox .
# Run:    docker run -p 8080:8080 -v agent-mailbox-data:/data \
#           -e AGENT_MAILBOX_PUBLIC_URL=http://<host>:8080 agent-mailbox

FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# hatch-vcs takes the version from git, and there is no .git in the build context.
ARG VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# No extras. The `clients` extra pulls in mcp, and with it pydantic and starlette —
# which the API deliberately does not use (ADR 0009). The hub ships four dependencies.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --frozen --no-editable


FROM python:3.12-slim AS runtime

RUN useradd --create-home --uid 10001 agentmailbox
COPY --from=build --chown=agentmailbox:agentmailbox /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    AGENT_MAILBOX_HOST=0.0.0.0 \
    AGENT_MAILBOX_PORT=8080 \
    AGENT_MAILBOX_DB=/data/agent-mailbox.db

# AGENT_MAILBOX_PUBLIC_URL is deliberately not defaulted here: the hub cannot guess how
# it is reached, and a wrong answer would be baked into every identifier it emits.
# Left unset it falls back to localhost, which is at least honest.

RUN mkdir -p /data && chown agentmailbox:agentmailbox /data
VOLUME ["/data"]

USER agentmailbox
EXPOSE 8080

# Health does not touch the database on purpose, so a wedged store is reported by the
# routes that need it rather than hidden behind a check that hangs too.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"

ENTRYPOINT ["agent-mailbox-serve"]
