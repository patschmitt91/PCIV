# ADR-0003: Use pydantic for structured agent I/O

- **Status:** Accepted
- **Date:** 2026-04-24
- **Deciders:** @patschmitt91
- **Supersedes:** —

## Context

Three of the four phases (Plan, Critique, Verify) exchange a single
JSON object between the model and the orchestrator. The orchestrator
branches on the content of that object (e.g. critique verdict,
subtask dependency graph, verify per-subtask decisions). If the model
returns malformed JSON, the orchestrator must either repair it or
abort with a clear error — silently accepting partially-parsed
structure leads to run failures hours downstream.

Candidates for the structure layer:

1. `pydantic` models with a bounded repair loop.
2. Plain `dict[str, Any]` plus ad-hoc `isinstance` checks.
3. `dataclasses` plus `json.loads` without validation.
4. `jsonschema` against hand-written schemas.

## Decision

Every structured agent emits a subclass of `pydantic.BaseModel`
(`Plan`, `Critique`, `VerdictReport`). The base class `JsonAgent`
owns a bounded repair loop: on `ValidationError` or `json.JSONDecodeError`
it re-prompts the model with the prior raw output and the error
message, up to the model's configured `retries`. After the final
attempt the agent raises and the run fails preflight of its own
next phase.

Pydantic is used only for model I/O boundaries, not as the
application data model everywhere.

## Consequences

### Positive

- **One validator per schema.** The Pydantic model is the schema, the
  validator, and the static type. There is no drift between the
  JSON contract and the Python type.
- **Actionable error messages on failure.** `ValidationError` already
  points at the offending field. The repair loop hands that message
  back to the model, which usually fixes the issue in one retry.
- **Consistent control flow across phases.** All three JSON phases
  reuse `JsonAgent`, so adding a new structured phase is a new
  `BaseModel` subclass plus a prompt template, not a new orchestrator
  fork.
- **Tool-call-friendly.** Implement agents bind pydantic models to
  their tool signatures through the agent-framework `Agent` API;
  the same schema is used for tool arguments and for the final
  structured result.

### Negative

- **Pydantic v2 is a real dependency.** v1 / v2 migrations are a
  known pain point in the Python ecosystem. We pin to `pydantic>=2.9`
  and take the compatibility hit up front.
- **Schema changes are breaking.** Adding a required field to `Plan`
  means older ledger rows can't be re-parsed directly. Mitigated by
  marking new fields `Optional` + documenting the minor bump.

### Neutral

- Pydantic's strictness is configurable per model. We use `extra =
  "ignore"` on the top-level schemas so the model is free to emit
  additional fields without the run failing.

## Alternatives considered

### `dict[str, Any]` with manual checks

**Rejected.** Scatters validation throughout the orchestrator; every
branch that reads a field has to guard against missing keys. The
error messages degrade from "`verdict: Input should be one of
{'ship', 'iterate', 'reject'}`" to `KeyError: 'verdict'` deep in the
pipeline.

### `dataclasses` without validation

**Rejected.** Gives us types for the IDE but no runtime enforcement.
The orchestrator would still need its own validator, so we'd pay
for two layers (dataclass + ad-hoc validator) to get the same
coverage Pydantic gives in one.

### `jsonschema`

**Rejected.** Validates well but produces no typed object on the
Python side, so we'd need a second mapping step into dataclasses or
TypedDicts. Pydantic collapses those two layers.

## Validation

- `tests/test_types_validation.py` covers the success, repair, and
  exhaustion paths for each structured model.
- The `JsonAgent` repair loop is covered end-to-end in
  `tests/test_plan_agent.py`, `test_critique_agent.py`, and
  `test_verify_agent.py` with a mocked Azure client that returns
  malformed output on the first call.
