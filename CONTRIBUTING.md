# Contributing to agent-inbox

Thanks for helping. `agent-inbox` is small, generic infrastructure — contributions
should keep it that way.

## Setup

```bash
uv sync --dev
uv run pre-commit install    # optional but recommended
```

You need **Python 3.12+** and nothing else — storage is a single local SQLite file, so
the test suite requires no external services.

## Quality gates

These must pass before a change is complete (CI enforces them):

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

The whole suite (including the mailbox round-trip tests) runs against SQLite with no
services to stand up and nothing gated behind an environment flag.

## Coding standards

Follow [`docs/coding-standards.md`](docs/coding-standards.md) and the project rules in
[`AGENTS.md`](AGENTS.md). In short: full type annotations, absolute imports, specific
exceptions from `agent_inbox.exceptions`, logging over `print`, config through
`agent_inbox.config.Config`, and ruff-clean + pyright-clean.

Keep it **generic** — no deployment-specific hostnames, IPs, secrets, or org names in
code, docs, or tests.

## Commits & PRs

- Small, focused commits with imperative messages (`feat:`, `fix:`, `docs:`, `test:`).
- A PR should keep the gates green and update docs when behaviour changes.

## Releases

Versions come from git tags via `hatch-vcs`.

- **Docker image** is built and pushed to GHCR on every push to `main` (`:latest`) and
  on `v*` tags (`:<version>`).
- **PyPI** publish happens on a `v*` tag via Trusted Publishing:

  ```bash
  git tag v0.1.0 && git push origin v0.1.0
  ```

## License

By contributing you agree your contributions are licensed under
[GPL-3.0-or-later](LICENSE).
