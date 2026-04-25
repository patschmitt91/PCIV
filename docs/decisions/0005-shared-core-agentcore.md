# 0005 — Shared core via `agentcore`

## Status

Accepted (2025).

## Context

`src/pciv/redaction.py` carried a redaction module that was effectively a copy of AgentBudgeteer's. Bug fixes had to be ported by hand. `src/pciv/agents/_azure.py` wired the Azure OpenAI client for API-key auth only, with no path to managed identity.

Phase 4 of `HARDENING_PROMPT.md` mandates extracting these into a shared library so the secret-pattern set has one owner and AAD-first auth is opt-out instead of opt-in.

## Decision

Depend on the new sibling repo **`agentcore`** (located alongside `PCIV/`). `pciv/redaction.py` becomes a re-export shim from `agentcore.redaction`, preserving existing call sites. The `agentcore.azure_client.build_client` factory is the recommended replacement for `_azure.build_azure_client`; migration of the agents to the new factory follows in a subsequent change.

## Consequences

- Single source of truth for redaction patterns and the env-snapshot cache.
- `pyproject.toml` pins `agentcore>=0.1.0,<0.2`. Development uses an editable install of the local sibling.
- A breaking change in `agentcore` requires coordinated bumps in both PCIV and AgentBudgeteer.

See `agentcore/docs/decisions/0001-extracted-from-pciv-and-agentbudgeteer.md`.
