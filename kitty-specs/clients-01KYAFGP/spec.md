# Spec — M3, the clients

**Kind:** foundation · **Date:** 2026-07-24
**Binding:** [ADR 0005](../../docs/decisions/0005-one-api-every-client-is-a-client.md)

## What this is

Three clients of the one API. **None of them is a proxy.** Each speaks a different
dialect to whoever is in front of it and the same API to the hub.

| Client | Front | Back |
|---|---|---|
| **MCP server** | stdio MCP, to an agent | the API |
| **CLI** | argv, to a human | the API |
| **console** | HTML, to a browser | the API |

The rule that keeps them honest: **if a client ever needs to *decide* something about
messaging, the API is missing a route.** No client holds visibility rules, addressing
logic, or state of its own.

## Priority

The **MCP server first**, because it is the only one that lets an agent use the hub at
all. The CLI and console are for humans and can follow.

## Functional requirements

| ID | Requirement | Status |
|---|---|---|
| FR-001 | `agent-mailbox mcp` runs a stdio MCP server exposing the mailbox as tools. | proposed |
| FR-002 | Tools: `ping`, `check_inbox`, `send_message`, `read_message`, `reply_message`, `read_thread`, `whois`, `list_agents`, `join`, `hub_info`. | proposed |
| FR-003 | Every tool is one HTTP call to the API. No client-side routing, filtering or caching. | proposed |
| FR-004 | Configuration is `agent-mailbox.toml` beside the project, or environment; the hub URL and this agent's name are all it needs. | proposed |
| FR-005 | With no configuration, the client says exactly what is missing and the one command that fixes it — and does not contact the hub. | proposed |
| FR-006 | A hub that is down, slow or unreachable never hangs the agent's turn: every call has a timeout and fails with a diagnostic. | proposed |
| FR-007 | The `agent-mailbox` CLI covers the same operations for a human, with readable output. | proposed |
| FR-008 | A web console shows mailboxes read-only, and the operator's own inbox interactively. | proposed |
| FR-009 | A paste-able onboarding prompt tells an agent how to connect and what the etiquette is. | proposed |

## Non-functional requirements

| ID | Requirement | Threshold | Status |
|---|---|---|---|
| NFR-001 | No client contains messaging logic. | A structural test: no client module imports `rules`, `mailbox`, `house` or `store` | proposed |
| NFR-002 | An agent's turn is never blocked by the hub. | Every request carries a timeout; failure is a message, not a hang | proposed |
| NFR-003 | Installing the client does not install the hub. | The `clients` extra pulls no server dependency | proposed |

## Constraints

| ID | Constraint | Status |
|---|---|---|
| C-001 | Clients talk to the API over HTTP only. None imports the engine. | accepted |
| C-002 | No deployment-specific hostnames in the repo; the hub URL is configuration. | accepted |
| C-003 | Authentication is still absent (ADR 0007). Clients send a name and say so. | accepted |

## Definition of done

- A real agent, configured from the prompt alone, sends and receives mail on the live
  homelab hub.
- The CLI does the same from a terminal.
- The console shows what is happening.
- An outside review before the mission closes.
- Four gates green.

## Out of scope

Authentication (M4) · channels (M5) · federation (M6/M7).
