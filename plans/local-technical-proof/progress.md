# Implementation Progress

## Current Status

**Active phase:** Phase 4 — Complete gateway and CLI integration (next; not started)
**Last completed task:** Phase 3 complete — all runtime and prerequisite gates passed
**Next task:** Complete runtime client configuration refresh, request-local pinning, explicit fallback and durable content-free spool/replay
**Sessions completed:** 3

This file tracks implementation only. The inspected prototype's 67 passing tests are baseline evidence, not completed tasks below. Phases 1–3 are **COMPLETE**; Phases 4–5 remain **NOT STARTED**. Record implementation sessions in `sessions/` and update this status, task checkboxes and gate evidence together. Follow AGENT.md for gaps and changes; architecture.md alone defines excluded scope.

The phases express dependency gates rather than five serial staffing assignments. Four engineers may prepare independent work in later phases against frozen interfaces, but must not claim a phase complete until its predecessor gates and its own gate pass. The estimate's overlapping waves assume this collaboration (see implementation-estimate.md).

## Phase 1: Contracts and real integration slice

> **Gate:** `just proof-bootstrap`, `just proof-unit foundation`, `just proof-contract foundation`, and `just proof-integration smoke` all exit zero.
> **Status:** COMPLETE

- [x] Create package/configuration skeleton and named proof recipe profiles; produce root package metadata, `justfile` additions and test profile registration (see folder-structure.md) (see tech-stack.md) (see quality-gates.md).
- [x] Implement immutable domain representations, shared enumerations and safe decision identity in `mvp/guardrails/contract.py` (see component-specs.md §DomainContracts) (see data-schema.md).
- [x] Implement strict JSON/request/response parsing and sanitized Problem serialization in `mvp/guardrails/transport.py`; produce an OpenAPI schema artifact from the same models (see component-specs.md §StrictContracts) (see api-endpoints.md).
- [x] Implement initial artifact resolution, TLS and local topology preparation in `mvp/proof/bootstrap.py` with the service/gateway image and Compose files; record actual resolved package locks and image digests (see component-specs.md §ProofStack) (see tech-stack.md).
- [x] Implement the deterministic HTTPS model endpoint and memory-only spy in `mvp/proof/mock_model.py` (see component-specs.md §MockModel) (see integration-protocols.md §Model and fault protocol).
- [x] Prove the native pre/post callback mechanism in `mvp/connector/litellm_hooks.py` using the pinned real LiteLLM process, one string-message request and safe response metadata; this slice is not full connector completion (see component-specs.md §LiteLLMConnector) (see integration-protocols.md §LiteLLM adapter mechanism).
- [x] Implement the minimal asynchronous verified-TLS client slice in `mvp/connector/core.py` and strict response validation used by the smoke path (see component-specs.md §RuntimeClient) (see api-endpoints.md).
- [x] Wire the temporary smoke-profile runtime in `mvp/guardrails/runtime/app.py` using valid explicit fixture policy/snapshot data and actual local evaluation; do not seed live management tables or claim a management lifecycle (see component-specs.md §EvaluationRuntime) (see migration-map.md).
- [x] Add foundation contract/immutability tests and the explicitly scoped real gateway → runtime → mock smoke case; record whether the candidate versions worked and update the resolved locks if needed (see test-strategy.md) (see quality-gates.md §Named profiles).

**Completion evidence:** strict models/schema validate; actual gateway hooks enforce input block plus input/output transformations before delivery; local certificate verification succeeds and an invalid trust case fails; the configured mock is called through real HTTP. Missing lifecycle functionality is still tracked in later phases, never skipped and counted as passing release acceptance.

**Gate evidence:** Bootstrap succeeded with recorded locks/images; foundation unit: 22 passed; foundation contract: 46 passed plus 14 subtests; real HTTPS smoke: 14 passed. Existing prototype: 67 passed. See [session 001](sessions/001-2026-10-08.md) for defects corrected, scope limits and the recorded Compose version deviation.

**Gaps closed in Phase 1:** Added isolated immutable domain types re-exported by the legacy contract module; a narrow ASGI gateway boundary for strict raw input and safe error/metadata serialization; native-hook and request-context helper modules; dependency/wheelhouse preparation; a closed gate inventory; and operator instructions. These support the named components without changing the plan's release boundary.

## Phase 2: Durable management lifecycle

> **Gate:** `just proof-unit management`, `just proof-contract management`, and `just proof-integration management` all exit zero after the Phase 1 gate.
> **Status:** COMPLETE

- [x] Implement schema-version-1 SQLite control storage, immutable policy/history constraints and transactionally coupled resource/audit/generation/operation writes in `mvp/guardrails/storage/control.py` (see component-specs.md §ControlRepository) (see data-schema.md §Persistence layout).
- [x] Implement actor/service/workload authentication, scoped authorization, tenant/workload registration and credential issuance/rotation in `mvp/guardrails/management/identity.py` (see component-specs.md §IdentityService) (see api-endpoints.md).
- [x] Implement strict capability validation, authorized retention, canonical publication/hash/ordinal and conflicting-version rejection in `mvp/guardrails/management/policies.py` (see component-specs.md §PolicyService) (see data-schema.md §Policy).
- [x] Implement Observe creation, governed state/version/timeout changes, mandatory floor checks, optimistic concurrency and audited rollback in `mvp/guardrails/management/bindings.py` (see component-specs.md §BindingService) (see variance-matrix.md §Mandatory floor demonstration).
- [x] Implement consistent full snapshots, conditional retrieval, authenticated generation acknowledgements and operation readback in `mvp/guardrails/management/snapshots.py` (see component-specs.md §SnapshotPublisher) (see integration-protocols.md §Management to runtime).
- [x] Wire management endpoints, safe errors, commit-versus-application status, and explicit incomplete audit-source handling in `mvp/guardrails/management/app.py` (see component-specs.md §ManagementAPI) (see api-endpoints.md §Management endpoints).
- [x] Add real database restart/transaction-failure, cross-tenant/role, immutable publication, history/floor and endpoint tests; record successful generations as committed until a real runtime acknowledgement exists (see test-strategy.md) (see quality-gates.md §Named profiles).

**Completion evidence:** direct API lifecycle operations and required rejections work on durable state; failed audit/storage transactions do not partly mutate state; restart preserves policies, histories and pending operations. Runtime application and complete CLI parity are still proved by their dependent phases.

**Gate evidence:** Management unit: 28 passed; management contract: 62 passed; real management HTTPS/SQLite integration: 10 passed. Bootstrap, 22 foundation unit tests, 46 foundation contract tests plus 14 subtests, 14 real gateway smoke tests, the 67-test prototype suite and `just verify` pass. See [session 002](sessions/002-2026-10-09.md). Authenticated test receipts verify acknowledgement semantics only; real runtime application remains Phase 3.

**Gaps closed in Phase 2:** Added strict management projections, shared pure governance predicates, packaged SQL schema, a generated management OpenAPI artifact, fixed case-count inventories, retained HTTPS process test state and API examples. Package-data changes reuse unchanged dependency locks. Binding recreation preserves increasing history revisions; actual SQLite contention returns a bounded 503 without overwriting a concurrent successful change.

## Phase 3: Runtime correctness and configuration consistency

> **Gate:** `just proof-unit runtime`, `just proof-contract runtime`, and `just proof-integration runtime` all exit zero after Phases 1–2 gates.
> **Status:** COMPLETE

- [x] Implement validated atomic snapshot swaps, polling/acknowledgement, age/readiness and last-known-state behavior in `mvp/guardrails/runtime/snapshots.py` (see component-specs.md §SnapshotCache) (see integration-protocols.md §Management to runtime).
- [x] Complete authenticated batch evaluation, bounded identity/correlation-scoped contexts, pinned input/output policy generations, one-time output consumption, client configuration and safe internal endpoints in `mvp/guardrails/runtime/app.py` (see component-specs.md §EvaluationRuntime) (see data-schema.md §EvaluationContext).
- [x] Implement the exact ASCII email and international phone detector with per-segment code-point spans in `mvp/guardrails/evaluators/pii.py` (see component-specs.md §LocalPIIEvaluator) (see input-output-contract.md §Detector formats).
- [x] Implement bounded queue/process admission, real stage deadlines, worker termination/replacement and clean shutdown in `mvp/guardrails/runtime/executor.py` (see component-specs.md §DeadlineExecutor) (see data-schema.md §ProofConfig).
- [x] Adapt the engine for batch findings, safe union-of-span redaction, normal action ceilings and explicit non-weakening failure precedence in `mvp/guardrails/engine.py` (see component-specs.md §DecisionEngine) (see variance-matrix.md).
- [x] Implement the separately owned runtime audit database, allowlisted records, authorized content delegation, deduplicated client events and scoped queries in `mvp/guardrails/storage/audit.py` (see component-specs.md §AuditRepository) (see data-schema.md §AuditEvent).
- [x] Complete management's federated audit query integration and explicit source-completeness results against the real runtime audit endpoint (see component-specs.md §ManagementAPI) (see api-endpoints.md).
- [x] Add actual stalled-worker/queue/audit-lock deadline tests; policy update during inference; expired/cross-workload contexts; stale/cold snapshot behavior; overlap/retention/failure regressions and SQLite restart tests (see test-strategy.md) (see quality-gates.md §Named profiles).

**Completion evidence:** runtime calls never synchronously query management; a model turn stays on its pinned generation while the next turn sees an applied update; a timed-out worker is replaced and subsequent work succeeds; no audit fallback weakens a completed block or redaction. Full real-gateway enforcement remains the Phase 4 gate.

**Gate evidence:** Runtime unit: 139 passed; runtime contract: 39 passed; real HTTPS/SQLite/process integration: 19 passed. Bootstrap and every Phase 1–2 gate pass on the rebuilt artifacts; the 67-test prototype suite and `just verify` also pass. The integration suite asserts a newer generation is applied while an actual delayed model request remains in flight. See [session 003](sessions/003-2026-10-09.md) for retained evidence, timing observations and corrected development failures.

**Gaps closed in Phase 3:** Added dedicated context/worker/runtime-projection modules, runtime SQL and OpenAPI, a fixed case-count inventory, installed-service HTTPS fixtures, the delayed mock scenario and a live management/runtime Compose overlay. Separated the smoke app to preserve its existing gate. Closed startup/shutdown and strict response-serialization defects without weakening deadlines or adding automatic mutation retries. Full connector configuration/fallback/spooling and CLI parity remain Phase 4.

## Phase 4: Complete gateway and CLI integration

> **Gate:** `just proof-unit`, `just proof-contract`, and `just proof-integration` all exit zero after Phases 1–3 gates; `just test` retains equivalent or stronger baseline assertions.
> **Status:** NOT STARTED

- [ ] Complete client-config refresh, request-local pinning, strict decision validation, stage timeouts, explicit fallback and durable content-free spool/replay in `mvp/connector/core.py` (see component-specs.md §RuntimeClient) (see integration-protocols.md §Client fallback delivery).
- [ ] Complete mandatory native hooks, all-message segment mapping, unsupported-surface rejection, single-text response enforcement and safe gateway status/metadata serialization in `mvp/connector/litellm_hooks.py` (see component-specs.md §LiteLLMConnector) (see input-output-contract.md).
- [ ] Implement the API-backed full lifecycle and evidence commands in `mvp/guardrails/cli.py`, default applied-operation wait, credential file handling and documented error exits (see component-specs.md §LifecycleCLI) (see api-endpoints.md).
- [ ] Complete independent gateway-A/B deployment identities/configuration and readiness, actual locked artifacts, isolated networking and non-destructive stack lifecycle commands (see component-specs.md §ProofStack) (see integration-protocols.md §Local topology).
- [ ] Complete credentialed proof-only scenario/fault controls and bounded volatile spy cleanup for `mvp/proof/mock_model.py` (see component-specs.md §MockModel) (see integration-protocols.md §Model and fault protocol).
- [ ] Prove API/CLI lifecycle parity, all five states through both gateways, fail-open actual model/delivery continuation, offline prepared start, TLS rejection, unsupported-route/override refusal, client-event replay and whole-stack privacy (see test-strategy.md) (see quality-gates.md).
- [ ] Reconcile prototype entry points/tests and compatibility documentation; preserve useful existing assertions while replacing the old single-text local wire fixtures explicitly (see migration-map.md).

**Completion evidence:** the full integration suite uses two real gateways, runtime, management and mock. Both public management surfaces work without internal state edits; all supported string messages are inspected. Passing this gate is a prerequisite to the single-run release evidence, not a replacement for it.

## Phase 5: Acceptance evidence and independent verification

> **Gate:** `just proof-acceptance` exits zero for all ten rows; after `just proof-down` and `just proof-reset`, a second `just proof-acceptance` exits zero with separate preserved evidence; `just verify` also exits zero for repository documentation.
> **Status:** NOT STARTED

- [ ] Implement final isolated-run orchestration, complete fixture inventory, ten-row accounting, safe manifests and results in `mvp/proof/acceptance.py`; earlier report scaffolding can begin in Phase 1 (see component-specs.md §AcceptanceRunner) (see data-schema.md §EvidenceReport).
- [ ] Execute every release row offline with actual pinned services, real boundary faults, semantic outcome comparisons and sanitized evidence; any absent, skipped or inconclusive required assertion fails (see test-strategy.md §Acceptance matrix traceability) (see quality-gates.md).
- [ ] Write setup/API/CLI instructions, detector limits, failure behavior and evidence-reading instructions using the implemented commands and contracts (see input-output-contract.md) (see migration-map.md) (see test-strategy.md).
- [ ] Have QA independently review fixture labels, execute setup/negative scenarios, inspect stored metadata/logs, and retest defects; record concrete results and remaining concerns (see implementation-estimate.md §QA effort and acceptance ownership) (see quality-gates.md).
- [ ] Produce a clean second run after the documented reset, preserve both evidence reports, and record the source commit, exact resolved dependency/image artifacts and test settings (see component-specs.md §AcceptanceRunner) (see tech-stack.md).
- [ ] Reforecast actual effort and allowance consumed, complete release-matrix review and update this status/session history only after all gates pass (see implementation-estimate.md) (see quality-gates.md).

## Overall acceptance

All five phase gates pass, all twenty components have implemented/tested behavior, and all ten release rows pass together in repeatable offline acceptance. Required contract rejections, real timing/resource boundaries, tenant/role controls, default privacy and pinning consistency have evidence. No AWS/live-provider/production-readiness conclusion is inferred. The plan checker and documentation gates remain separate from runtime acceptance.

## Implementation records

Session [001-2026-10-08.md](sessions/001-2026-10-08.md) records completed Phase 1. Every subsequent session appends a new numbered log. Each entry records completed task IDs/descriptions, exact gate commands/results, decisions, Gaps Closed, effort/remaining-risk observations and the next task. Do not mark work complete from file existence or an earlier prototype's Built label.
