# Folder Structure

## Layer Dependency Rules

- Domain models/compiler rules import standard library and declared pure validation packages only; no FastAPI, SQLAlchemy, LiteLLM or network clients.
- Application services depend on domain and injected ports; adapters depend inward. Tenant state is request/transaction-local.
- Existing local-proof modules remain a compatibility surface. New v2 modules live in `mvp/guardrails/evolution/`; their explicit migration adapter may read legacy models but may not rewrite legacy semantics.
- Connector code imports the public v2 contract/client only, never repositories or policy compiler internals. Worker services use their own environment and the versioned worker contract.
- Deployment and tests can assemble layers. Product code cannot import fixtures, test runners or plan documents.

## File Tree

Paths below are repository-relative and exhaustive for planned component entry points. Small helpers belong with their component; new necessary files are logged as gaps and added to the appropriate implementation task. ADAPT means preserve the named baseline behavior while applying only the stated changes.

| Path | Action and specification |
|---|---|
| `mvp/guardrails/evolution/storage.py` | IMPLEMENT — TenantRepository (see component-specs.md §TenantRepository) |
| `mvp/guardrails/evolution/identity.py` | IMPLEMENT — IdentityService (see component-specs.md §IdentityService) |
| `mvp/guardrails/evolution/audit.py` | IMPLEMENT — AuditStore (see component-specs.md §AuditStore) |
| `mvp/guardrails/evolution/compiler.py` | IMPLEMENT — PolicyCompiler (see component-specs.md §PolicyCompiler) |
| `mvp/guardrails/evolution/governance.py` | IMPLEMENT — PolicyGovernance (see component-specs.md §PolicyGovernance) |
| `mvp/guardrails/evolution/bindings.py` | IMPLEMENT — BindingService (see component-specs.md §BindingService) |
| `mvp/guardrails/evolution/management_api.py` | IMPLEMENT — ManagementApi (see component-specs.md §ManagementApi) |
| `mvp/guardrails/evolution/cli.py` | IMPLEMENT — LifecycleCli (see component-specs.md §LifecycleCli) |
| `mvp/evolution/run.py` | IMPLEMENT — AcceptanceRunner (see component-specs.md §AcceptanceRunner) |
| `mvp/guardrails/evolution/plugins.py` | IMPLEMENT — EvaluatorRegistry (see component-specs.md §EvaluatorRegistry) |
| `mvp/guardrails/evolution/residency.py` | IMPLEMENT — ResidencyGuard (see component-specs.md §ResidencyGuard) |
| `mvp/guardrails/evolution/quotas.py` | IMPLEMENT — QuotaService (see component-specs.md §QuotaService) |
| `mvp/guardrails/evolution/snapshots.py` | IMPLEMENT — SnapshotDistributor (see component-specs.md §SnapshotDistributor) |
| `mvp/guardrails/evolution/runtime_api.py` | IMPLEMENT — EvaluationRuntime (see component-specs.md §EvaluationRuntime) |
| `mvp/connector/evolution.py` | IMPLEMENT — LiteLLMConnector (see component-specs.md §LiteLLMConnector) |
| `deploy/evolution/bootstrap.py` | IMPLEMENT — LocalDeployment (see component-specs.md §LocalDeployment) |
| `examples/evolution/client.py` | IMPLEMENT — SampleApplication (see component-specs.md §SampleApplication) |
| `mvp/guardrails/evolution/rules.py` | IMPLEMENT — RuleEvaluators (see component-specs.md §RuleEvaluators) |
| `services/classifier/app.py` | IMPLEMENT — ClassifierWorker (see component-specs.md §ClassifierWorker) |
| `mvp/guardrails/evolution/bedrock.py` | IMPLEMENT — BedrockAdapter (see component-specs.md §BedrockAdapter) |
| `mvp/guardrails/evolution/admin.py` | IMPLEMENT — AdminConsole (see component-specs.md §AdminConsole) |
| `mvp/guardrails/evolution/telemetry.py` | IMPLEMENT — OperationalTelemetry (see component-specs.md §OperationalTelemetry) |
| `deploy/evolution/recovery.py` | IMPLEMENT — RecoveryManager (see component-specs.md §RecoveryManager) |
| `mvp/guardrails/evolution/customer.py` | IMPLEMENT — CustomerRuntime (see component-specs.md §CustomerRuntime) |
| `deploy/evolution/operated_gateway.py` | IMPLEMENT — OperatedGateway (see component-specs.md §OperatedGateway) |
| `services/media/app.py` | IMPLEMENT — MediaInspector (see component-specs.md §MediaInspector) |
| `mvp/connector/boundaries.py` | IMPLEMENT — BoundarySdk (see component-specs.md §BoundarySdk) |
| `mvp/connector/streaming.py` | IMPLEMENT — StreamingGuard (see component-specs.md §StreamingGuard) |
| `mvp/guardrails/evolution/__init__.py` | IMPLEMENT — package marker |
| `mvp/guardrails/evolution/models.py` | IMPLEMENT — all v2 shapes (see data-schema.md) |
| `mvp/guardrails/evolution/ports.py` | IMPLEMENT — repository/clock/evaluator interfaces (see plugin-contract.md) |
| `mvp/guardrails/evolution/config.py` | IMPLEMENT — configuration and assembly (see tech-stack.md §DI / Wiring) |
| `mvp/guardrails/evolution/migrate.py` | IMPLEMENT — import/dry-run command (see migration-map.md) |
| `mvp/guardrails/evolution/migrations/env.py` | ADAPT — alembic init output; inject migration-only credentials and metadata (see migration-map.md) |
| `mvp/guardrails/evolution/migrations/versions/0001_shared_state.py` | IMPLEMENT — schema, row security, append-only constraints (see data-schema.md) |
| `mvp/guardrails/evolution/templates/` | IMPLEMENT — all view templates owned by AdminConsole (see admin-experience.md) |
| `mvp/guardrails/evolution/static/admin.css` | IMPLEMENT — layout, focus, state styles (see admin-experience.md) |
| `mvp/guardrails/evolution/static/admin.js` | IMPLEMENT — CSRF wiring, status polling, no client policy authority (see admin-experience.md) |
| `mvp/guardrails/evolution/static/vendor/htmx.min.js` | GENERATE — evolution-prepare copies hash-verified htmx distribution; never edit vendor bytes (see tech-stack.md) |
| `mvp/connector/gateway_app.py` | ADAPT — baseline gateway boundary factory; choose explicit v1/v2 adapter without changing v1 behavior (see migration-map.md) |
| `mvp/evolution/cases.json` | IMPLEMENT — closed required case registry (see quality-gates.md) |
| `mvp/evolution/export_openapi.py` | IMPLEMENT — deterministic v2 contract exporter (see api-endpoints.md) |
| `mvp/evolution/management-openapi.json` | GENERATE — export_openapi.py from assembled management API (see api-endpoints.md) |
| `mvp/evolution/runtime-openapi.json` | GENERATE — export_openapi.py from assembled runtime API (see api-endpoints.md) |
| `deploy/evolution/Chart.yaml` | IMPLEMENT — Helm package metadata (see deployment-topology.md) |
| `deploy/evolution/values.yaml` | IMPLEMENT — safe default remote profile (see deployment-topology.md) |
| `deploy/evolution/values-customer.yaml` | IMPLEMENT — customer profile (see variance-matrix.md) |
| `deploy/evolution/values-gateway.yaml` | IMPLEMENT — additional operated gateway profile (see variance-matrix.md) |
| `deploy/evolution/templates/` | IMPLEMENT — deployments, services, secrets references, network policies, RBAC, volumes and probes (see deployment-topology.md) |
| `deploy/evolution/templates/model-tls.yaml` | IMPLEMENT — model-pod TLS sidecar and loopback-only native listener (see deployment-topology.md §Model TLS Boundary) |
| `deploy/evolution/alerts.yaml` | IMPLEMENT — local alerts and bounded metric labels (see quality-gates.md) |
| `deploy/evolution/Dockerfile` | IMPLEMENT — control/runtime build (see tech-stack.md §Docker) |
| `deploy/evolution/Dockerfile.gateway` | ADAPT — proof gateway Dockerfile structure; package v2 connector while retaining pinned LiteLLM (see tech-stack.md §Docker) |
| `services/classifier/Dockerfile` | IMPLEMENT — isolated classifier image (see tech-stack.md §Docker) |
| `services/classifier/requirements.lock` | GENERATE — evolution-lock with hash-pinned worker dependencies (see tech-stack.md) |
| `services/media/Dockerfile` | IMPLEMENT — OCR worker and prepared language data (see tech-stack.md §Docker) |
| `services/media/requirements.lock` | GENERATE — evolution-lock for worker dependencies (see tech-stack.md) |
| `deploy/evolution/artifacts.lock.json` | GENERATE — evolution-prepare ArtifactRef manifest (see data-schema.md §ArtifactRef) |
| `evolution-requirements.lock` | GENERATE — evolution-lock resolves declared constraints and hashes (see tech-stack.md) |
| `pyproject.toml` | ADAPT — existing pyproject; add v2 script/dependency groups without renaming proof entry point (see tech-stack.md) |
| `justfile` | ADAPT — existing recipes; append evolution commands, preserve proof commands (see tech-stack.md §Task Runner) |
| `.gitignore` | ADAPT — existing ignore rules; add .evolution generated secrets/state/reports (see quality-gates.md) |
| `.github/workflows/evolution.yml` | IMPLEMENT — offline-prepared local gates on a resource-qualified runner; no cloud deployment (see test-strategy.md) |
| `mvp/tests/evolution/unit/` | IMPLEMENT — unit/port tests (see test-strategy.md) |
| `mvp/tests/evolution/contract/` | IMPLEMENT — strict wire tests (see test-strategy.md) |
| `mvp/tests/evolution/integration/` | IMPLEMENT — real store/worker tests (see test-strategy.md) |
| `mvp/tests/evolution/e2e/` | IMPLEMENT — gateway/cluster/surface cases (see test-strategy.md) |
| `mvp/tests/evolution/quality/` | IMPLEMENT — benchmark runner and corpus manifest (see test-strategy.md) |
| `mvp/tests/evolution/fixtures/` | IMPLEMENT — synthetic/provenance-labelled fixtures (see test-strategy.md) |
| `tests/evolution-browser/` | IMPLEMENT — Playwright journeys (see test-strategy.md) |
| `docs/evolution/runbook.md` | IMPLEMENT — operator startup, failure, recovery and upgrade procedures (see quality-gates.md) |
| `docs/evolution/onboarding.md` | IMPLEMENT — independent team adoption and exposure guide (see tenancy-model.md) |
| `docs/evolution/coverage.md` | GENERATE — enabled capability/detector manifest from registry and passing evidence (see guardrail-coverage.md) |
| `.evolution/` | GENERATE — ignored task-owned runtime state, secrets and evidence by deployment/runner; never commit secrets (see quality-gates.md) |

Template directories contain one template per named view plus a shared layout/error/field component. Helm directory contains separate manifests for each resource family listed above. Their contents are implementation details of the named owning components, not independent untracked capabilities.
