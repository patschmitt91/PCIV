# Configuration

All runtime behavior is driven by [plan.yaml](../plan.yaml). This page
documents every top-level key. Environment-variable overrides are listed
where the adapter consumes them.

## `version`

Schema version. Integer. Must be `1` for this release.

## `models`

One block per logical role: `planner`, `critic`, `implementer`,
`verifier`. Each block:

| Key              | Meaning                                                      |
|------------------|--------------------------------------------------------------|
| `provider`       | Only `azure_openai` is supported today.                      |
| `deployment`     | Azure OpenAI deployment name (overridable by env var).       |
| `api_version`    | Azure OpenAI API version (see the Azure docs for valid ids). |
| `max_tokens`     | Max output tokens per call.                                  |
| `timeout_s`      | Per-call timeout in seconds.                                 |
| `retries`        | Number of retries on transient failure.                      |
| `max_turns`      | Implementer only: tool-loop turn cap.                        |
| `max_concurrency`| Implementer only: concurrent fan-out cap (asyncio semaphore).|

Environment-variable overrides for `deployment`:

| Variable                              | Replaces placeholder      |
|---------------------------------------|---------------------------|
| `AZURE_OPENAI_PLAN_DEPLOYMENT`        | planner `azure-reasoning` |
| `AZURE_OPENAI_CRITIC_DEPLOYMENT`      | critic  `azure-reasoning` |
| `AZURE_OPENAI_IMPLEMENT_DEPLOYMENT`   | implementer `azure-codegen` |
| `AZURE_OPENAI_VERIFY_DEPLOYMENT`      | verifier `azure-reasoning` |

Always required: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`.

## `pricing`

USD per 1M tokens, per placeholder model id. Map of `placeholder_id ->
{input_per_mtok, output_per_mtok}`. Used by the budget governor's
projection and per-call cost accounting. Update to match your Azure
negotiated rates; no default is safe for billing.

## `budget`

| Key                                            | Meaning                             |
|------------------------------------------------|-------------------------------------|
| `default_ceiling_usd`                          | CLI `--budget` default.             |
| `monthly_cap_usd`                              | Optional. Cross-run rolling-window USD cap (ADR 0007). When set, every `pciv run` consults a SQLite-backed `agentcore.budget.PersistentBudgetLedger` mounted on `runtime.sqlite_path` and refuses to start a run whose projected cost wouldn't fit in the remaining window. `null` / omitted disables the check; per-run `--budget` still applies. |
| `window`                                       | `monthly` (UTC `YYYY-MM`) or `daily` (UTC `YYYY-MM-DD`). Default `monthly`. Ignored when `monthly_cap_usd` is unset. |
| `projection.plan_input_tokens`                 | Preflight input-token estimate.     |
| `projection.plan_output_tokens`                | Preflight output-token estimate.    |
| `projection.critique_input_tokens`             | …                                   |
| `projection.critique_output_tokens`            | …                                   |
| `projection.implement_input_tokens_per_subtask`| Per-subtask projection.             |
| `projection.implement_output_tokens_per_subtask`| …                                  |
| `projection.verify_input_tokens`               | …                                   |
| `projection.verify_output_tokens`              | …                                   |
| `projection.expected_subtasks`                 | Preflight fan-out estimate.         |

Preflight sums the projection block against `pricing` and aborts before
any network call if the result exceeds the per-run ceiling. When the
cross-run cap is active, the same projection is also compared against the
rolling window's remaining allowance; either check can reject a run.
Use `pciv run --ignore-cross-run-cap` to bypass the cross-run check (the
spend is still recorded with `forced=1` for audit).

## `iteration`

| Key                   | Meaning                                        |
|-----------------------|------------------------------------------------|
| `max_rounds`          | Maximum verify-iterate rounds. CLI override: `--max-iter`. |
| `max_plan_revisions`  | Maximum plan re-attempts on a `reject` critique. |

## `gates`

HITL gate behaviour. Each gate has `enabled` and `default`.

| Gate             | Purpose                                                |
|------------------|--------------------------------------------------------|
| `approve_plan`   | Operator approves the plan after critique passes.      |
| `approve_merge`  | Operator approves squash-merge to the integration branch. |

Valid gate decisions: `approve`, `revise`, `reject`, `abort`. `--yes`
auto-approves both.

## `telemetry`

| Key                                      | Meaning                                  |
|------------------------------------------|------------------------------------------|
| `service_name`                           | OTel service name.                       |
| `app_insights_connection_string_env`     | Env var that, when set, enables Azure Monitor export. Falls back to a console exporter if unset. |

## `runtime`

| Key            | Meaning                                        |
|----------------|------------------------------------------------|
| `state_dir`    | Directory for ledger + worktrees (default `.pciv`). |
| `sqlite_path`  | Ledger path (default `.pciv/ledger.db`).       |
