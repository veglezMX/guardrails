# Prototype Migration Map

## Purpose and compatibility boundary

This plan evolves the inspected reference runtime into the accepted local proof. The baseline at `eb68dd9` is reusable prototype code, not a supported deployed service. Changes described here are future implementation; this planning task changes no runtime behavior.

The new proof wire schema is explicitly `local-proof-v1` (see input-output-contract.md §Compatibility). The prior single-text request/decision examples are not silently accepted as that schema. New requests use the complete `TextBatch` representation and pin output with an `EvaluationContext`; strict validation rejects unsupported versions/shapes rather than projecting partial data and claiming coverage.

No automatic migration of real user data is authorized or required. Bootstrap prepares fresh disposable proof stores and generated local credentials. It creates application/workload/policy fixtures through the public API, not by loading hand-authored deployment JSON into live tables. Local operator/service trust material is the narrowly defined bootstrap input; subsequent workload issuance and lifecycle use APIs.

## Source migration

| Existing source | Target handling | Evidence required |
| --- | --- | --- |
| `mvp/guardrails/contract.py` | Evolve immutable domain values for batch text, local-proof schema, generation/context and visible runtime/connector origins (see component-specs.md §DomainContracts). | Deep mutation rejection, canonical serialization and strict wire projections. |
| `mvp/guardrails/policy.py` | Reuse parser/validation concepts, adapt to canonical policy/check/failure schema and authorized strict retention; preserve no unsafe boolean coercion (see component-specs.md §PolicyService). | Changed-version conflict, type errors and capability/minimum checks through API and CLI. |
| `mvp/guardrails/binding.py` | Reuse identity/binding concepts; move mutation authority/history into management services and durable repository (see component-specs.md §BindingService). | Observe creation, roles, floors, revision preconditions and rollback events. |
| `mvp/guardrails/store.py` | Treat file-loaded cache as legacy fixture support while new runtime uses validated immutable snapshot/cache component (see component-specs.md §SnapshotCache). | New service entry point cannot depend on manual deployment edits; snapshot fault/update tests pass. |
| `mvp/guardrails/app.py` | Retain the legacy standalone app only as a clearly labeled reference/test fixture if still needed; production proof entry points are `guardrails.management.app:create_app` and `guardrails.runtime.app:create_app`. Adapt imports explicitly (see component-specs.md §ManagementAPI) (see component-specs.md §EvaluationRuntime). | Proof recipes and CLI launch only the new entry points; legacy examples cannot be mistaken for accepted service evidence. |
| `mvp/guardrails/intake.py` | Reuse authenticated workload/body identity comparison, strengthen strict batch and stage/version validation through the shared transport layer (see component-specs.md §StrictContracts) (see component-specs.md §IdentityService). | Cross-workload/tenant rejection and safe validation-error fixtures. |
| `mvp/guardrails/engine.py` | Preserve normalized decision ideas; implement batch spans, union redaction, explicit failure precedence and audit non-weakening (see component-specs.md §DecisionEngine). | Verified overlap regression, all states, deadline/failure precedence and unchanged clean segments. |
| `mvp/guardrails/evaluators/pii.py` | Replace broad undocumented matching with the exact email/phone baseline and local adapter contract (see component-specs.md §LocalPIIEvaluator). | Positive/negative boundary corpus; no arbitrary long-number or malformed-token substring acceptance. |
| `mvp/guardrails/evaluators/bedrock.py`, deterministic/schema evaluators | Keep as clearly identified prototype code if useful; exclude from the proof runtime's advertised capability registry and publication support. Do not count fake-provider parity as live integration. | Unsupported capability publication fails; manifest lists only proof-supported capabilities. |
| `mvp/guardrails/audit.py` | Reuse allowlisted metadata ideas; route proof runtime persistence/query to its own audit SQLite repository (see component-specs.md §AuditRepository). | Correct default privacy, durable metadata, explicit failed persistence and scoped queries. |
| `mvp/connector/core.py` | Adapt client to asynchronous verified HTTPS, complete response validation and real fail-open continuation; retain `GuardedInvocation` only if it uses the same tested enforcement logic (see component-specs.md §RuntimeClient). | Model-call and returned-response assertions under input/output fail-open, safe spooled fallback and no blocking HTTP in async callbacks. |
| `mvp/connector/litellm_hooks.py` | Replace unverified custom-logger assumptions with the pinned native integration, full supported message mapping and request-local context (see component-specs.md §LiteLLMConnector). | Two actual gateways, unsupported route/override refusal and before-delivery output enforcement. |
| `mvp/guardrails/cli.py` | Add/replace public proof commands with the complete API-backed lifecycle. Keep a legacy standalone command only if explicitly labeled and excluded from proof setup. Never let lifecycle commands edit internal server files (see component-specs.md §LifecycleCLI). | Direct API and CLI workflows reject the same invalid changes; wait/credential/exit behavior is documented. |
| `mvp/deployment/` and `mvp/demo.py` | Retain historical reference fixtures if useful and mark their limited purpose; acceptance fixtures/bootstrap generate fresh API-owned state. | Release instructions invoke proof setup/acceptance, not the old hand-authored two-binding demo. |
| `mvp/README.md`, `mvp/TASKS.md`, root recipes | Link to the accepted release and implemented proof commands; replace applicable stale Built/remaining-effort claims with acceptance evidence and this estimate. Preserve historical circulated proposal/review records. | Documentation validation and clean independent setup. |

New modules in storage, management, runtime and proof directories follow folder-structure.md; the table does not authorize a parallel undocumented implementation path. Remove obsolete compatibility wrappers only once their callers/tests have migrated and the change preserves useful behavior evidence. Do not keep two independent enforcement implementations that disagree about fallback or transformation safety.

## Test migration

Keep the existing `just test` entry point and preserve every useful baseline assertion. Where new schema shapes require fixture changes, update test inputs/expected outputs deliberately and document the applicable local-proof contract. New proof tests add the actual gateway, lifecycle, timeout and offline evidence missing from the prototype (see test-strategy.md).

| Existing assumption or weak assertion | Replacement evidence |
| --- | --- |
| A failed evaluation with `allowed=True` proves fail-open | Spy proves input inference really happens and output fallback returns the permitted model response; origin/evidence is explicit. |
| Frozen dataclass or changed republish rejection proves immutable policy | Source dictionary mutation and nested field mutation cannot change a publication; SQLite rejects update/delete and survives restart. |
| Pre-raised `EvaluatorTimeout` proves deadline control | Real stalled worker is terminated/replaced within bounded execution; queued work expires and later healthy work succeeds. |
| Failure decisions follow the normal finding ceiling | Adopt variance-matrix.md explicitly: configured failure behavior is separate; test each state/failure interaction and never weaken existing content restrictions after audit failure. |
| Last-user-message/first-choice helpers imply guarded chat | Exercise every accepted string message and reject unsupported input/output shape before unsafe progress. |
| Fake Bedrock versus local evaluator proves portable live behavior | Preserve only as a historical unit test where useful; required local proof compares two actual LiteLLM gateways on the same supported detector/configuration. |
| One HTTP runtime/core-client class proves the end-to-end release | Run full management/runtime/gateway/mock stack, public API/CLI lifecycle and all ten acceptance rows offline. |

Do not preserve known unsafe behavior merely to keep old expected values green. A changed assertion requires a cited contract, a stronger externally meaningful replacement and an implementation session entry. A test removed as irrelevant to the accepted local surface must remain distinguishable from a missed required case; no accepted row can be removed through test migration.

## Persistent data and reset

Initialize fresh **control SQLite schema version 1** and **runtime audit SQLite schema version 1** separately (see data-schema.md §Persistence layout). A process writes only its own database. New services reject an unknown newer database schema. Future schema upgrades are explicit transactional migrations; a future destructive migration requires a backup. This plan does not invent a conversion from old hand-authored files or historical logs into accepted production data.

`proof-reset` targets only the named disposable proof project's stores/containers according to its contract and preserves exported evidence. It never cleans a primary checkout, another worktree, unrelated Docker resources or user databases. A clean proof run generates new workload credentials and new corpus-run identifiers; static fixture inputs remain versioned sources, never mistaken for previously retained traffic.

## First-slice and final cutover

Phase 1 may supply a known valid immutable fixture snapshot to the smoke-only runtime to prove native LiteLLM callbacks and TLS before the management plane exists. This adapter is test-only, visibly selected by the smoke profile, and has no authority to populate the full acceptance run. Phase 2 onward tests durable management; Phase 3 integrates actual snapshot distribution; Phase 4 uses real public lifecycle and both gateways; Phase 5 accepts the release only through the complete run.

The proof entry points, dependency locks, schema artifacts, CLI help and setup documentation switch together. A mixed setup using the new connector with the old single-text wire service must reject explicitly. After the new full gate passes, update status and documentation to the actual implemented behavior; do not claim compatibility or completed delivery because the migration map exists.
