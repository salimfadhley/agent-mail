# Coding Standards

Canonical coding standards for the EUNRG family of projects, consolidated from 9 existing repo copies. These rules apply to all Python source code, tests, and infrastructure helpers in the codebase.

This document does **not** specify a Python version target — each repo declares its own floor in `pyproject.toml`.

---

## 1. Type annotations

- All function signatures must have parameter and return type annotations.
- Use modern syntax: `str | None` instead of `Optional[str]`, `list[str]` instead of `List[str]`.
- Add `from __future__ import annotations` at the top of files that need forward references.
- Annotate non-obvious local variables, especially when the inferred type would be ambiguous:
  ```python
  results: list[Trade] = []
  cache: dict[str, datetime] = {}
  ```

---

## 2. Exceptions

> **Raise as specifically as possible; catch only as generally as you need.**

### Where they live

- Each package may define its own `exceptions.py` with classes specific to that package's failures.
- Project-wide `src/<project>/exceptions.py` holds genuinely generic errors (e.g. `ConfigurationError`, `<Project>Error` base class).

### How to raise

- **Throw specifically.** Raise the most specific exception that describes the failure. Prefer a custom subclass over a built-in `ValueError` / `RuntimeError` when the failure has domain meaning.
- A function that raises `InvalidTradeId` tells the reader, the IDE, the caller, and the type checker exactly what failure mode to expect. A function that raises `ValueError("bad trade id")` requires every caller to read the message string.

### How to catch

- **Catch narrowly.** Catch the specific exception types you can actually recover from. Let everything else propagate.
- `except Exception:` is **only** allowed at process boundaries — CLI `main()`, HTTP handler, message consumer — as a backstop for graceful logging. Anywhere else it is a code smell.
- Specific catches surface bugs early. `except Exception:` swallows typos, refactor mistakes, and missing imports — you only learn about them when output is wrong.

### Never catch a programming error

Narrow is not sufficient — **what** you catch matters as much as how much.

- `TypeError`, `AttributeError`, `NameError`, `IndexError`, and usually `KeyError` are **bugs in your code**, not conditions to recover from. Catching one converts a crash with a stack trace into wrong behaviour with no explanation, which is strictly worse.
- If you find yourself catching `TypeError`, the fix is almost always a guard clause or a type annotation, not a handler.
- The exceptions worth catching describe things the *world* did: a file was missing, a payload was malformed, a name was taken, a peer was unreachable.

```python
# no — hides a bug in our own code
try:
    recipients = message.to
except (TypeError, AttributeError):
    recipients = []

# yes — the failure is in the input, and it is named
if not isinstance(message.to, Sequence):
    raise MalformedAddress(...)
```

### Give errors a machine-readable code

Where an exception crosses a layer boundary — engine to API, API to client — carry a stable `code` on the exception class. Prose is for the human or agent reading it and may be reworded freely; the code is what the layer above switches on. Callers should never pattern-match on English.

### Subclassing for retry

When using `tenacity` (see §6), specific exception types let you `retry_if_exception_type(TransientError)` without retrying on validation errors. Generic exception types break this.

---

## 3. Logging, not print

- Use the `logging` module with module-level loggers: `logger = logging.getLogger(__name__)`.
- **Never use `print()` in library or service code.** Reasons:
  - `print` writes to stdout unconditionally; logging respects level filters.
  - `print` has no timestamp, no context, no severity — useless in production triage.
  - `print` cannot be redirected to syslog, CloudWatch, or a file by configuration.
  - `print` output is invisible in many production contexts (Jenkins jobs, ECS tasks).
- **CLI tool exception:** intentional CLI user-facing output may use `click.echo(...)` (or `click.echo(..., err=True)` for diagnostics on stderr). Always also log the same information so that automated runs leave a trace.

---

## 4. F-strings

Use f-strings for all string interpolation. Do not use `%`-formatting or `str.format()` except where required by an external API (e.g. `logging.info("user=%s", user)` — logging uses lazy `%`-style for performance).

---

## 5. Config separation

- No module-level constants for environment-specific config (URLs, paths, credentials, hostnames).
- Configuration goes through a single config module / object (e.g. `pydantic-settings`), loaded from environment variables or a config file.
- Page-scoped constants in Streamlit pages are an explicit override (see §16).

---

## 6. Retries

Use `tenacity` for any retryable operation (HTTP calls, database connects, file IO that may be racing).

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(TransientError),
)
def fetch_from_api(...):
    ...
```

Pair with specific exception types (§2) so retries only happen for transient failures.

---

## 7. Testing

- **`pytest`, never `unittest`.** Use plain `def test_*()` functions, not `unittest.TestCase` classes.
- **Tests live at `/tests` in the repo root.**
- **The `tests/` tree mirrors the source package layout.** A module at `src/<package>/<subpackage>/module.py` has its tests at `tests/<subpackage>/test_module.py`.
- Prefer integration tests (real artefacts, real DB connections in regtests) over unit tests for POC and v1 work.
- Use `pytest` fixtures (`@pytest.fixture`) for shared setup, not `setUp` / `setUpClass`.

---

## 8. Imports

- Ordering and grouping managed by **ruff** (isort rules).
- Do not maintain manual import order; let `ruff check --fix` do it.
- One import per line (ruff default).
- Use absolute imports (`from eunrg_utils.foo import bar`), not relative (`from .foo import bar`), except for re-exports inside a package's `__init__.py`.

---

## 9. Linting and formatting

- **`ruff` for linting and formatting** (one tool replaces black + flake8 + isort).
- **`pyright`** for type checking.
- Enforce in CI; do not merge code that fails lint or type check.
- Auto-fix over fail: when ruff offers a fix (`ruff check --fix`), apply it rather than disabling the rule.

---

## 10. Secrets

- **Never commit secrets.** Not in code, not in config files, not in tests, not in plans.
- **Preferred storage:** AWS Secrets Manager, following the path convention in the global CLAUDE.md:
  - `<env>/apps/<app-name>/credentials/<credential-type>/<credential-name>` for app-specific
  - `<env>/shared/credentials/<credential-type>/<credential-name>` for shared
- **Fallback for non-AWS projects:** local `.env` file, gitignored. Document the required keys in a committed `.env.example`.
- **No fake-looking placeholder secrets** in plans or PRs (e.g. `password=changeme123`). It looks like a real secret to scanners and to reviewers.
- When a secret is added, moved, or rotated, note it in the post-implementation report.

---

## 11. Immutable data

- Prefer `frozen` `dataclass` or `pydantic.BaseModel(frozen=True)` for data records.
- Mutable shared state is a smell — pass new objects instead of mutating in place.
- Use `tuple` over `list` and `frozenset` over `set` when the collection is not intended to change.

---

## 12. Mature dependencies

- Prefer libraries that are widely adopted, actively maintained, and stable on the version you depend on.
- Pin dependency versions in `pyproject.toml` / `uv.lock`. Do not float to "latest".
- Avoid pre-1.0 libraries unless there is no alternative; document why.
- Avoid abandonware (no commit in 12+ months, open security issues).

---

## 13. Docstrings

- Docstrings are **optional**. A well-named function with type hints is often self-documenting.
- Add a docstring when the **why** is non-obvious — a hidden constraint, a subtle invariant, a workaround for a specific bug, behaviour that would surprise a reader.
- If you write a docstring, use Google or NumPy style consistently within the project. Do not mix styles.

---

## 14. Pre-commit hooks

Every repo should have a `.pre-commit-config.yaml` running at minimum:

- `ruff check --fix`
- `ruff format`
- `pyright` (or skip if too slow; CI catches it anyway)
- `trailing-whitespace`
- `end-of-file-fixer`
- `check-yaml`

Hooks should auto-fix where possible (ruff's `--fix` flag) rather than only failing.

---

## 15. Quality gates

Before declaring work complete, all of these must pass:

```bash
uv run pytest
uv run ruff check
uv run ruff format --check
uv run pyright
```

CI must enforce all four. A merge that lands with any of these failing is a process violation.

---

## 16. Streamlit conventions

For Streamlit apps (`eunrg-pgops-streamlit`, `eunrg-sumo-streamlit`, etc.):

- Use `st.navigation` for multi-page apps; do not rely on the legacy `pages/` directory layout.
- Apply the caching matrix:
  - `@st.cache_data` for serialisable data results
  - `@st.cache_resource` for connections / clients / models
  - Neither for per-user state — use `st.session_state`
- Page-scoped constants (URLs, query strings, feature flags) at the top of a page module are an explicit override of §5.
- Health check endpoint: `/_stcore/health`.
- In production, set `showErrorDetails="type"` (show error type, not full traceback, to end users).
- Resolve DNS hostnames early at app startup, fail fast if a required service is unreachable.

---

## 17. uv

- Use `uv` for all Python workflows: `uv sync`, `uv run`, `uv add`, `uv remove`.
- **Do not invoke** `.venv/Scripts/python.exe` (or the POSIX equivalent) directly. Always go through `uv run`.
- This ensures consistent dependency resolution and avoids stale-venv bugs.

---

## Project-specific overrides

This document is the canonical baseline. Each project's `AGENTS.md` may add project-specific rules but should not contradict this baseline. If a project needs to override a rule (e.g. a legacy library that requires `print()`), document the override and the reason in that project's `AGENTS.md`.

---

## Open items

- [x] Python version target — per-project, not in this document
- [x] Linting/formatting stack — ruff + pyright
- [x] Secrets handling — AWS Secrets Manager preferred, .env fallback, never commit
- [x] `print()` rule — logging preferred, CLI exception via `click.echo`
- [x] Exception design — per-package + project-level, throw specific, catch narrow
- [x] Tests directory — `/tests` at repo root, mirroring source layout
- [x] Import ordering — ruff
