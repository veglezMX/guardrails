# File production and layer boundaries

All implementation lives outside this plan directory. Keep the existing `mvp/` source layout to avoid a package rewrite. The plan is a build contract; `progress.md` and `sessions/` are its only implementation-time state.

## Source tree

```text
pyproject.toml, requirements.lock, dev-requirements.lock, justfile
mvp/
  guardrails/          domain plus transport adapters
    management/       lifecycle application services and HTTP app
    runtime/          snapshot/context/deadline execution and HTTP app
    storage/          SQLite adapters and schema migrations
    evaluators/       provider-authored detector adapters
  connector/          generic runtime client and LiteLLM-specific mapping
  proof/              packaging, mock, bootstrap and acceptance orchestration
  tests/              retained baseline plus unit/contract/integration/acceptance
.proof/               generated ignored keys, cache, temporary state and evidence
plans/local-technical-proof/   this build contract
```

## Component files

ADAPT means retain the named existing source as the starting point and implement the cited component changes; do not regenerate or discard proven semantics. IMPLEMENT files are new. Tests and packaging support files are listed separately.

| File | Action and source | Responsibility |
| --- | --- | --- |
| `mvp/guardrails/contract.py` | ADAPT — existing mvp/guardrails/contract.py | (see component-specs.md §DomainContracts) |
| `mvp/guardrails/transport.py` | IMPLEMENT | (see component-specs.md §StrictContracts) |
| `mvp/guardrails/storage/control.py` | IMPLEMENT | (see component-specs.md §ControlRepository) |
| `mvp/guardrails/management/identity.py` | IMPLEMENT | (see component-specs.md §IdentityService) |
| `mvp/guardrails/management/policies.py` | IMPLEMENT | (see component-specs.md §PolicyService) |
| `mvp/guardrails/management/bindings.py` | IMPLEMENT | (see component-specs.md §BindingService) |
| `mvp/guardrails/management/snapshots.py` | IMPLEMENT | (see component-specs.md §SnapshotPublisher) |
| `mvp/guardrails/management/app.py` | IMPLEMENT | (see component-specs.md §ManagementAPI) |
| `mvp/guardrails/runtime/snapshots.py` | IMPLEMENT | (see component-specs.md §SnapshotCache) |
| `mvp/guardrails/runtime/app.py` | IMPLEMENT | (see component-specs.md §EvaluationRuntime) |
| `mvp/guardrails/engine.py` | ADAPT — existing mvp/guardrails/engine.py | (see component-specs.md §DecisionEngine) |
| `mvp/guardrails/evaluators/pii.py` | ADAPT — existing mvp/guardrails/evaluators/pii.py | (see component-specs.md §LocalPIIEvaluator) |
| `mvp/guardrails/runtime/executor.py` | IMPLEMENT | (see component-specs.md §DeadlineExecutor) |
| `mvp/guardrails/storage/audit.py` | IMPLEMENT | (see component-specs.md §AuditRepository) |
| `mvp/connector/core.py` | ADAPT — existing mvp/connector/core.py | (see component-specs.md §RuntimeClient) |
| `mvp/connector/litellm_hooks.py` | ADAPT — existing mvp/connector/litellm_hooks.py | (see component-specs.md §LiteLLMConnector) |
| `mvp/guardrails/cli.py` | ADAPT — existing mvp/guardrails/cli.py | (see component-specs.md §LifecycleCLI) |
| `mvp/proof/bootstrap.py` | IMPLEMENT | (see component-specs.md §ProofStack) |
| `mvp/proof/mock_model.py` | IMPLEMENT | (see component-specs.md §MockModel) |
| `mvp/proof/acceptance.py` | IMPLEMENT | (see component-specs.md §AcceptanceRunner) |

## Supporting files

| File | Production | Purpose |
| --- | --- | --- |
| `pyproject.toml` | IMPLEMENT | Exact direct pins, explicit package discovery under mvp for guardrails/connector/proof, console script and pytest suite configuration; record/capture the build backend and dependencies. |
| `requirements.lock` | GENERATE via proof-bootstrap | Exact resolved owned runtime package snapshot (see tech-stack.md). |
| `dev-requirements.lock` | GENERATE via proof-bootstrap | Exact development/test snapshot (see tech-stack.md). |
| `justfile` | ADAPT — existing root justfile | Add proof commands/profiles, retain existing doc/test targets (see tech-stack.md). |
| `.gitignore` | ADAPT — existing root .gitignore | Exclude .proof, local secrets, volumes, caches and generated environment paths; preserve current entries. |
| `mvp/README.md` | ADAPT — existing README | Document API/CLI flow, topology, content exposure, setup/offline commands and supported formats; reference this plan. |
| `mvp/TASKS.md` | ADAPT — existing task survey | Point to this plan's estimate/progress; preserve historical completion evidence as historical. |
| `mvp/guardrails/policy.py` | ADAPT — existing policy parser | Deep immutability, namespaced categories, strict retention and canonical policy hashing (see component-specs.md §PolicyService). |
| `mvp/guardrails/binding.py` | ADAPT — existing binding model | Version/history/state/timeouts/governance fields (see component-specs.md §BindingService). |
| `mvp/guardrails/intake.py` | ADAPT — existing identity validation | Preserve server-derived identity; delegate strict transport/local credential verification (see component-specs.md §IdentityService). |
| `mvp/guardrails/errors.py` | ADAPT — existing domain errors | Typed safe error codes and runtime failures (see api-endpoints.md §Problem). |
| `mvp/guardrails/failure.py` | ADAPT — existing failure handling | Explicit failure precedence (see variance-matrix.md). |
| `mvp/guardrails/audit.py` | ADAPT — existing record builder | Metadata allowlist and category/timing fields; delegate persistence (see component-specs.md §AuditRepository). |
| `mvp/guardrails/store.py` | ADAPT — existing in-memory cache | Retain labeled legacy fixture support; active immutable lookup uses SnapshotCache and durable state uses ControlRepository (see migration-map.md). |
| `mvp/guardrails/evaluators/base.py` | ADAPT — existing interface | Provider-owned adapter/capability records (see plugin-contract.md). |
| `mvp/guardrails/evaluators/__init__.py` | ADAPT — existing registry | Advertise only proof-supported local_pii capabilities; retain legacy modules unadvertised. |
| `mvp/guardrails/__main__.py` | ADAPT — existing module entry | Route to the API-backed CLI; preserve documented legacy diagnostic entry where migration requires. |
| `mvp/guardrails/app.py` | ADAPT — existing legacy server | Retain only explicitly marked legacy test compatibility during migration; final proof uses runtime/app.py (see migration-map.md). |
| `mvp/guardrails/management/__init__.py` | IMPLEMENT | Package marker. |
| `mvp/guardrails/runtime/__init__.py` | IMPLEMENT | Package marker. |
| `mvp/guardrails/storage/__init__.py` | IMPLEMENT | Package marker. |
| `mvp/guardrails/storage/schema.sql` | IMPLEMENT | Version-1 control schema/immutable triggers matching data-schema.md. |
| `mvp/guardrails/storage/audit-schema.sql` | IMPLEMENT | Version-1 runtime audit schema matching data-schema.md. |
| `mvp/proof/__init__.py` | IMPLEMENT | Package marker. |
| `mvp/proof/Dockerfile` | IMPLEMENT | Owned CP/runtime/mock/runner image, separate commands, tested Python base and build artifacts. |
| `mvp/proof/gateway.Dockerfile` | IMPLEMENT | Derive from pinned LiteLLM, install connector without overwriting upstream dependency environment. |
| `mvp/proof/compose.yaml` | IMPLEMENT | Internal network, TLS, loopback ports, independent services/volumes, runner profiles. |
| `mvp/proof/gateway-a.yaml` | IMPLEMENT | Workload A, alias proof-a, mandatory pre/post guardrail registration. |
| `mvp/proof/gateway-b.yaml` | IMPLEMENT | Workload B, alias proof-b, separate credential/configuration. |
| `mvp/proof/config.json` | IMPLEMENT | Explicit ProofConfig defaults and no secrets. |
| `mvp/proof/faults.py` | IMPLEMENT | Credentialed proof-only failure controls and process-level orchestration (see integration-protocols.md). |
| `mvp/proof/test_profiles.py` | IMPLEMENT | Fixed required-test inventories and named profile selection; fail on missing/skipped cases (see quality-gates.md). |
| `mvp/proof/fixtures.py` | IMPLEMENT | Load/validate canonical synthetic ProofFixture variants (see data-schema.md). |
| `mvp/proof/reports.py` | IMPLEMENT | Safe EvidenceReport rendering and no-raw-content validation. |
| `mvp/proof/api-examples.md` | IMPLEMENT | Curl/CLI examples with token-file loading and sanitized output; exact endpoints from api-endpoints.md. |
| `mvp/proof/openapi.json` | GENERATE via proof-contract | Snapshot derived from strict API models; no manually maintained alternate schema. |
| `.proof/artifacts/manifest.json` | GENERATE via proof-bootstrap | Actual versions/platform/image identities and wheel hashes. |
| `.proof/pki/` | GENERATE via proof-bootstrap | Local CA/leaf materials, restrictive permissions, never committed. |
| `.proof/secrets/` | GENERATE via proof-bootstrap / API credential issuance | Restricted local credentials, never committed or printed. |
| `.proof/reports/` | GENERATE via proof-acceptance | Metadata-only evidence reports, preserved by reset. |
| `.proof/wheelhouse/` | GENERATE via proof-bootstrap | Prepared dependency artifacts verified by recorded hashes. |

## Tests

Every test file is IMPLEMENT except the existing `mvp/tests/test_*.py` and `mvp/tests/support.py`, which are ADAPT only when the migration preserves or strengthens their assertions. Test directories and purpose are authoritative in test-strategy.md; profile inventories are in quality-gates.md.

- `mvp/tests/unit/test_policy_integrity.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/unit/test_pii_formats.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/unit/test_failure_precedence.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/unit/test_snapshot_cache.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/unit/test_turn_context.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/unit/test_deadline_executor.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/contract/test_api_validation.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/contract/test_authorization.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/contract/test_cli_api_parity.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/contract/test_gateway_surface.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/integration/test_management_lifecycle.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/integration/test_snapshot_distribution.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/integration/test_sqlite_restart.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/integration/test_litellm_gateways.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/integration/test_offline_stack.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/acceptance/test_release_matrix.py` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/fixtures/pii-cases.json` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/fixtures/lifecycle-cases.json` — IMPLEMENT (see test-strategy.md).
- `mvp/tests/fixtures/failure-cases.json` — IMPLEMENT (see test-strategy.md).

## Import rules

Domain models/engine cannot import FastAPI, SQLite or LiteLLM. Transport converts to domain objects. Management/runtime application services depend on repository/client interfaces; concrete adapters are injected at startup. The LiteLLM hook imports only connector-owned code plus upstream types; provider configuration never enters a Policy. Proof controls are not imported/enabled by default service startup. No cross-process sharing of live SQLite connections.

New empty package markers are the only trivial scaffolding; no framework-generated source is required. Existing documentation tools/assets and retained legacy evaluator files remain as-is unless the migration explicitly lists an adaptation. Do not rewrite generated presentations/docx assets to create an appearance of implementation progress.
