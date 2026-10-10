# Implementation Progress

## Current Status

**Active phase:** Phase 1 — Governance foundation
**Last completed task:** —
**Next task:** P1.1 — Freeze baseline and resolve reproducible dependencies
**Sessions completed:** 0

This is a new implementation plan. No task below is implemented by creating these documents. The completed predecessor proof remains complete; phases here use their own numbering. Follow dependency order within and between phases. All planned commands must be implemented before claiming their gates.

## Phase 1: Governance foundation

> **Gate:** `just evolution-gate 1` succeeds (see quality-gates.md).
> **Status:** NOT STARTED

- [ ] P1.1 — Freeze baseline and resolve reproducible dependencies; introduce v2 models/ports (see tech-stack.md).
- [ ] P1.2 — Implement `TenantRepository` (see component-specs.md §TenantRepository).
  - Produces: `mvp/guardrails/evolution/storage.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P1.3 — Implement `IdentityService` (see component-specs.md §IdentityService).
  - Produces: `mvp/guardrails/evolution/identity.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P1.4 — Implement `AuditStore` (see component-specs.md §AuditStore).
  - Produces: `mvp/guardrails/evolution/audit.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P1.5 — Implement `PolicyCompiler` (see component-specs.md §PolicyCompiler).
  - Produces: `mvp/guardrails/evolution/compiler.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P1.6 — Implement `PolicyGovernance` (see component-specs.md §PolicyGovernance).
  - Produces: `mvp/guardrails/evolution/governance.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P1.7 — Implement `BindingService` (see component-specs.md §BindingService).
  - Produces: `mvp/guardrails/evolution/bindings.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P1.8 — Implement `ManagementApi` (see component-specs.md §ManagementApi).
  - Produces: `mvp/guardrails/evolution/management_api.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P1.9 — Implement `LifecycleCli` (see component-specs.md §LifecycleCli).
  - Produces: `mvp/guardrails/evolution/cli.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P1.10 — Implement `AcceptanceRunner` (see component-specs.md §AcceptanceRunner).
  - Produces: `mvp/evolution/run.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P1.11 — Implement dry-run/import migration with approval-preserving conversion and rollback procedure (see migration-map.md).
- [ ] P1.12 — Run the closed phase gate, retain evidence and resolve every failing/missing case (see quality-gates.md).

## Phase 2: Shared service and integration

> **Gate:** `just evolution-gate 2` succeeds (see quality-gates.md).
> **Status:** NOT STARTED

- [ ] P2.1 — Implement `EvaluatorRegistry` (see component-specs.md §EvaluatorRegistry).
  - Produces: `mvp/guardrails/evolution/plugins.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P2.2 — Implement `ResidencyGuard` (see component-specs.md §ResidencyGuard).
  - Produces: `mvp/guardrails/evolution/residency.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P2.3 — Implement `QuotaService` (see component-specs.md §QuotaService).
  - Produces: `mvp/guardrails/evolution/quotas.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P2.4 — Implement `SnapshotDistributor` (see component-specs.md §SnapshotDistributor).
  - Produces: `mvp/guardrails/evolution/snapshots.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P2.5 — Implement `EvaluationRuntime` (see component-specs.md §EvaluationRuntime).
  - Produces: `mvp/guardrails/evolution/runtime_api.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P2.6 — Implement `LiteLLMConnector` (see component-specs.md §LiteLLMConnector).
  - Produces: `mvp/connector/evolution.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P2.7 — Implement `LocalDeployment` (see component-specs.md §LocalDeployment).
  - Produces: `deploy/evolution/bootstrap.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P2.8 — Implement `SampleApplication` (see component-specs.md §SampleApplication).
  - Produces: `examples/evolution/client.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P2.9 — Prove two independent team namespaces, all destination/provider restrictions, offline preparation and real local model traffic (see deployment-topology.md).
- [ ] P2.10 — Run the closed phase gate, retain evidence and resolve every failing/missing case (see quality-gates.md).

## Phase 3: Broader guardrail coverage

> **Gate:** `just evolution-gate 3` succeeds (see quality-gates.md).
> **Status:** NOT STARTED

- [ ] P3.1 — Implement `RuleEvaluators` (see component-specs.md §RuleEvaluators).
  - Produces: `mvp/guardrails/evolution/rules.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P3.2 — Implement `ClassifierWorker` (see component-specs.md §ClassifierWorker).
  - Produces: `services/classifier/app.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P3.3 — Implement `BedrockAdapter` (see component-specs.md §BedrockAdapter).
  - Produces: `mvp/guardrails/evolution/bedrock.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P3.4 — Freeze licensed detector corpus, exact secret grammars and held-out benchmark; publish enabled capabilities only after passing (see guardrail-coverage.md).
- [ ] P3.5 — Run the closed phase gate, retain evidence and resolve every failing/missing case (see quality-gates.md).

## Phase 4: Administration and operations

> **Gate:** `just evolution-gate 4` succeeds (see quality-gates.md).
> **Status:** NOT STARTED

- [ ] P4.1 — Implement `AdminConsole` (see component-specs.md §AdminConsole).
  - Produces: `mvp/guardrails/evolution/admin.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P4.2 — Implement `OperationalTelemetry` (see component-specs.md §OperationalTelemetry).
  - Produces: `mvp/guardrails/evolution/telemetry.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P4.3 — Implement `RecoveryManager` (see component-specs.md §RecoveryManager).
  - Produces: `deploy/evolution/recovery.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P4.4 — Complete all browser role/error states and keyboard journeys (see admin-experience.md).
- [ ] P4.5 — Run capacity, alert, privacy, backup/restore and schema/image rollback drills (see quality-gates.md).
- [ ] P4.6 — Run the closed phase gate, retain evidence and resolve every failing/missing case (see quality-gates.md).

## Phase 5: Portable delivery modes

> **Gate:** `just evolution-gate 5` succeeds (see quality-gates.md).
> **Status:** NOT STARTED

- [ ] P5.1 — Implement `CustomerRuntime` (see component-specs.md §CustomerRuntime).
  - Produces: `mvp/guardrails/evolution/customer.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P5.2 — Implement `OperatedGateway` (see component-specs.md §OperatedGateway).
  - Produces: `deploy/evolution/operated_gateway.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P5.3 — Prove all mode/telemetry combinations and document offline revocation limits (see variance-matrix.md).
- [ ] P5.4 — Run the closed phase gate, retain evidence and resolve every failing/missing case (see quality-gates.md).

## Phase 6: Additional boundaries and release

> **Gate:** `just evolution-gate 6` succeeds (see quality-gates.md).
> **Status:** NOT STARTED

- [ ] P6.1 — Implement `MediaInspector` (see component-specs.md §MediaInspector).
  - Produces: `services/media/app.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P6.2 — Implement `BoundarySdk` (see component-specs.md §BoundarySdk).
  - Produces: `mvp/connector/boundaries.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P6.3 — Implement `StreamingGuard` (see component-specs.md §StreamingGuard).
  - Produces: `mvp/connector/streaming.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [ ] P6.4 — Extend sample application with retrieval and separately authorized toy tools; prove zero forbidden execution (see input-output-contract.md).
- [ ] P6.5 — Run preserved predecessor regression and all local evolution cases twice with matching source/artifact digests (see quality-gates.md).
- [ ] P6.6 — Publish onboarding, coverage, operations runbooks and machine-readable evidence; validate generated contracts/docs (see folder-structure.md).
- [ ] P6.7 — Run the closed phase gate, retain evidence and resolve every failing/missing case (see quality-gates.md).

## Acceptance Criteria

- [ ] All nine selected workstreams have their stated evidence and no incomplete capability is advertised (see architecture.md §Scope Boundaries).
- [ ] Two independently owned LiteLLM configurations consume one shared local service with tenant isolation (see tenancy-model.md).
- [ ] Immutable compilation, distinct approval, rollout, revocation and eligible rollback pass every negative case (see quality-gates.md).
- [ ] Four-category allow/deny rules and residency restrictions prevent forbidden dispatch and raw retention (see guardrail-coverage.md).
- [ ] Declared detectors meet exact/held-out gates with artifact and corpus provenance (see guardrail-coverage.md).
- [ ] Administration, operational and recovery journeys pass through real interfaces (see admin-experience.md).
- [ ] Customer-runtime and operated-gateway variants pass without changing A/B ownership (see variance-matrix.md).
- [ ] Supported stream, tool, retrieval and image boundaries withhold uninspected content (see api-endpoints.md §Streaming Contract).
- [ ] Two complete matching local acceptance runs, compatibility regression and documentation gates pass (see quality-gates.md).
