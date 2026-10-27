# Implementation Progress

## Current Status

**Active phase:** Phase 3 — Rules and evaluators
**Last completed task:** P3.R2a — Revised recovery approved and source committed in logical groups. Concrete startup package validates without dispatch; TLS continuation, private metadata capture, operator backup, shared command budget and health-lineage component checks pass.
**Next task:** P3.R2b — Finish executable health composition, operator continuation bindings and final independent reviews; then perform the approved TLS-first recovery and exactly17 credential replacements. Fixed credential expiry2026-10-28T18:00Z; classifier cap nine remains.
**Sessions completed:** 4

Phase 1 passed the closed gate in run `b59de4e3-4676-4412-931b-010eacbbf1c9`: all 177 required cases passed with 1,930 measured assertions and a verified Ed25519 report. Executable source digest: `e2aeaf958ac4563d79501e1db1b537c672d39f97a86f836d601c579c6b1a2324`. The earlier failed assertion-counting attempt remains preserved separately. Phase 2 passed the sealed 1,103-case gate `1c8b455e-520f-49ee-b6be-c8bc23b7ab7d` with 21,026 assertions. The user approved exact destination pinning, preserving legacy compiled digests and requiring new approval/release for endpoint changes. Registry, quota, runtime, deployment and connector work proceeds. The predecessor proof remains preserved; phases here use their own numbering.

Current checkpoint (2026-10-27T19:35:14.743754+00:00): Phase 3 remains incomplete. The owned Docker node is stopped after a host boot change; timesyncd is active, old interop socket/forward process identities are absent. Local deployment CA expired2026-10-17 and all eight positive leaves expired2026-10-14. Old credential/journal/source pins remain unchanged; all eight predeclared recovery roots are absent, which is not database reconciliation. No live discovery, backup, credential issuance, TLS signing/renewal or startup was performed. Nix driver v3 passed23 affected checks and an independently reviewed historical package02 passed data-only validation; this grants no live admission. Revised proposal v4 explicitly requests same-key CA-certificate renewal, TLS-first local-socket read-only backup/guards, and fixed credential expiry2026-10-28T18:00Z. Implementation/review/tests/fresh binding remain prerequisites. All19 worktrees exist; no commit/push/merge. Plan log004 and durable session `/home/veglez/.agent-sessions/own/ai/guardrails/sessions/2026-10-27T1924Z_gpt-6_48d71dee_guardrails-recovery-reassessment` govern this checkpoint. Session count reconciles completed001–003 using the original continuation plus completed004; prior session logs and ROOT checkout history remain unchanged.


Active Session005 update: user approved proposal v4 and later authorized logical ongoing commits. Seven commits through `381109e9f95028d45563dc62a58cbd9833f5bc34` preserve existing source bytes; no push or merge. The inherited9-Secret draft omitted three existing namespace-local probe CA copies; source review establishes12existingSecrets and12Deployment rows within approved all-trust-copy scope. Initial synthetic CA checks failed on cryptography Extensions identity comparison; immutable successors compare ordered extension values and are being checked. No live node start, signing, TLS mutation or credential issuance has occurred. The four-file smoke run had41passes and one Hypothesis generation-speed health-check failure; unchanged compiler tests with the reported seed and explicit Hypothesis plugin subsequently passed15. These isolated results do not seal Phase3.

Session005 checkpoint at2026-10-27T20:46:59Z: fresh read-only observation confirms the same stopped container/image/volume/network and active timesyncd. The pure authority derivation authenticated62 private inputs under the existing lock and matched all four retained credential-consumer projections. Latest isolated scopes pass: worker36, Secret authority27, inventory57, binder57 and corrected startup126, with zero failures/skips. Current metadata also passes the tested startup projections. Parent result joins and finite final-health evidence remain under review; no deployment mutation or phase gate is claimed. Detailed receipts and preserved preparation failures are recorded in log005 and the active durable session.

## Phase 1: Governance foundation


> **Gate:** `just evolution-gate 1` succeeds (see quality-gates.md).
> **Status:** COMPLETE

- [x] P1.1 — Freeze baseline and resolve reproducible dependencies; introduce v2 models/ports (see tech-stack.md).
- [x] P1.2 — Implement `TenantRepository` (see component-specs.md §TenantRepository).
  - Produces: `mvp/guardrails/evolution/storage.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P1.3 — Implement `IdentityService` (see component-specs.md §IdentityService).
  - Produces: `mvp/guardrails/evolution/identity.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P1.4 — Implement `AuditStore` (see component-specs.md §AuditStore).
  - Produces: `mvp/guardrails/evolution/audit.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P1.5 — Implement `PolicyCompiler` (see component-specs.md §PolicyCompiler).
  - Produces: `mvp/guardrails/evolution/compiler.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P1.6 — Implement `PolicyGovernance` (see component-specs.md §PolicyGovernance).
  - Produces: `mvp/guardrails/evolution/governance.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P1.7 — Implement `BindingService` (see component-specs.md §BindingService).
  - Produces: `mvp/guardrails/evolution/bindings.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P1.8 — Implement `ManagementApi` (see component-specs.md §ManagementApi).
  - Produces: `mvp/guardrails/evolution/management_api.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P1.9 — Implement `LifecycleCli` (see component-specs.md §LifecycleCli).
  - Produces: `mvp/guardrails/evolution/cli.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P1.10 — Implement `AcceptanceRunner` (see component-specs.md §AcceptanceRunner).
  - Produces: `mvp/evolution/run.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P1.11 — Implement dry-run/import migration with approval-preserving conversion and rollback procedure (see migration-map.md).
- [x] P1.G1 — Supply the real signed snapshot/application receipt prerequisite required by the Phase 1 governance gate; include process-safe replay protection and authenticated enrollment.
- [x] P1.G2 — Generate and validate assembled Management API OpenAPI, including synchronization routes.
- [x] P1.G3 — Provide executable canary observation/submission and authenticated public-key trust bootstrap; verify real two-runtime full promotion and installed-dependency tamper rejection.
- [x] P1.G4 — Validate completed-session counts against sequential logs while retaining all other installed plan-checker findings; avoid falsifying live progress to satisfy a generation-only check.
- [x] P1.G5 — Execute the real local evaluator prerequisite for the governance gate's equivalent findings across two tenants, preserving separate policy and audit identity; the full Phase 2 registry/runtime remain separate tasks.
- [x] P1.G6 — Enforce configuration encoded identity in PostgreSQL and isolate version allocation for IDs containing underscores or colons.
- [x] P1.G7 — Serialize release eligibility, revocation and baseline publication through commit; prove both orderings with actual PostgreSQL transactions.
- [x] P1.G8 — Enforce management and synchronization request deadlines across body reception, database waits and transaction commit; retain finite safe errors and idempotent retry.
- [x] P1.12 — Run the closed phase gate, retain evidence and resolve every failing/missing case (see quality-gates.md).

## Phase 2: Shared service and integration

> **Gate:** `just evolution-gate 2` succeeds (see quality-gates.md).
> **Status:** COMPLETE

- [x] P2.1 — Implement `EvaluatorRegistry` (see component-specs.md §EvaluatorRegistry).
  - Produces: `mvp/guardrails/evolution/plugins.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P2.2 — Implement `ResidencyGuard` (see component-specs.md §ResidencyGuard).
  - Produces: `mvp/guardrails/evolution/residency.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P2.3 — Implement `QuotaService` (see component-specs.md §QuotaService).
  - Produces: `mvp/guardrails/evolution/quotas.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P2.4 — Implement `SnapshotDistributor` (see component-specs.md §SnapshotDistributor).
  - Produces: `mvp/guardrails/evolution/snapshots.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P2.5 — Implement `EvaluationRuntime` (see component-specs.md §EvaluationRuntime).
  - Produces: `mvp/guardrails/evolution/runtime_api.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P2.6 — Implement `LiteLLMConnector` (see component-specs.md §LiteLLMConnector).
  - Produces: `mvp/connector/evolution.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P2.7 — Implement `LocalDeployment` (see component-specs.md §LocalDeployment).
  - Produces: `deploy/evolution/bootstrap.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P2.8 — Implement `SampleApplication` (see component-specs.md §SampleApplication).
  - Produces: `examples/evolution/client.py`.
  - Tests: documented behavior/edges at the owning layer (see test-strategy.md).
- [x] P2.9 — Prove two independent team namespaces, all destination/provider restrictions, offline preparation and real local model traffic (see deployment-topology.md).
- [x] P2.G1 — Preserve optional tuple fields in wire-model parsing, including redacted Decision responses.
- [x] P2.G2 — Require genuinely applied Observe onboarding before first enforcement when mandatory floors permit Observe.
- [x] P2.G3 — Implement the user-approved exact destination pinning amendment with legacy digest and snapshot compatibility.
- [x] P2.G4 — Wire default management activation and snapshot distribution to verified declared capabilities; retain live worker health checks at runtime and actual receipts.
- [x] P2.G5 — Close acceptance gaps for import boundaries, all deployment preflight rejections, and sensitive-canary network probe privacy.
- [x] P2.G6 — Prove each advertised rule action at both text stages through the real HTTPS runtime and PostgreSQL, including exact would-be action and required audit; verify declared nonmandatory failure behavior.
- [x] P2.G7 — Keep owned database forwarding stable across TLS connection close, prove passive process-owned readiness, and retain the declared cluster resource ceiling.
- [x] P2.G8 — Recover interrupted bootstrap and secret issuance without rotating saved identities, replaying credential creation, or accepting mismatched journal/database state.
- [x] P2.G9 — Reduce measured shared-service evaluation overhead within the existing 500 ms deadline, retaining current restrictions, tenant isolation and atomic required audit.
- [x] P2.G10 — Reprepare and reconcile pinned application images without breaking owned forwards or reseeding governance; retain historical evidence and prove a fresh cold start for the final prepared artifacts.
- [x] P2.G11 — Require real password authentication on the PostgreSQL relay and direct TLS routes, reject incorrect credentials, and preserve restored data and tenant isolation.
- [x] P2.G12 — Measure two-tenant throughput under actual concurrent quota saturation; require tenant B to retain at least 90% of its measured baseline with correct decisions and durable audit. Full sealed gate now passes measured throughput and durable-audit requirements; historical failures remain retained.
- [x] P2.G13 — Start the pinned model TLS sidecar under dropped capabilities and no privilege escalation; verify identical executable bytes and preserve read-only runtime mounts.
- [x] P2.G14 — Size and verify model-sidecar resources within the existing platform budget; reject declared request/limit drift during reconciliation and final verification.
- [x] P2.G15 — Support the pinned Ollama manifest layout with a bounded, privately initialized metadata directory while retaining immutable model bytes, read-only runtime mounts and offline startup; prove actual API readiness before model traffic.
- [x] P2.G16 — Resolve measured PostgreSQL CPU contention on clean-input finalization within the existing six-CPU budget; preserve evaluation deadlines and verify actual model traffic plus affected network probes.
- [x] P2.G17 — Retain bounded, source-validated failure locations in private signed evidence without recording exception values, submitted content or secrets.
- [x] P2.G18 — Bind every native model client to the approved transport and prove pinned destination/TLS/DNS checks plus exactly one guarded dispatch for Ollama and mock paths; keep the final dispatch requirement enforced.
- [x] P2.G19 — Verify exact imported-image visibility through bounded read-only CRI readiness checks within the existing lookup deadline; preserve truthful interrupted cold-boot recovery and fail closed on mismatches.
- [x] P2.G20 — Reconcile local management forwarding when a fresh owned node publishes the preserved host port; retain immutable seed history, credential identity and recoverable operational metadata updates.
- [x] P2.G21 — Explicitly load the pinned async-test plugin in isolated acceptance workers while retaining closed plugin discovery, counted awaited assertions and private finite diagnostics.
- [x] P2.G22 — Retain bounded HTTP status, finite error code and call order for real-model failures without private payloads, extra requests or relaxed deadlines; resolve the observed failure before the phase gate.
- [x] P2.G23 — Renew bounded credentials for existing deployment identities through the authenticated API and recoverable private overlays; preserve immutable seed history, prove owned consumer reload and prevent profile replay from restoring expired tokens.
- [x] P2.G24 — Retain bounded native fixture subprocess diagnostics from the original attempt without content or credentials; diagnose the second gate’s native fixture failure without changing acceptance thresholds.
- [x] P2.G25 — Anchor quota lease expiry to the monotonic instant before its database UTC sample; prove delayed admission cannot release an in-flight charge before the original deadline, preserve rollback and the lease cap, and deploy verified rebuilt images.
- [x] P2.G26 — Retain bounded original-attempt HTTP observations for the six deployed sample-client matrix cases before fixture cleanup; diagnose gateway timeouts and non-200 replies without extra dispatches or changed budgets.
- [x] P2.G27 — Renew unchanged online snapshot leases before expiry, preserving the 60-second freshness limit, exact signed generations, revocation and in-flight turn policy; prove continuous healthy synchronization and deploy the verified fix.
- [x] P2.G28 — Retain finite original-request failure codes and stage observations for runtime action cases; resolve the observed non-200 output without weakening the 500 ms evaluation budget.
- [x] P2.G29 — Diagnose and resolve the first actual Ollama request returning HTTP 503/evaluator_unavailable in the fourth gate; use bounded original-attempt metadata and preserve all evaluation/model deadlines and dispatch counts.
- [x] P2.G30 — Reconcile the existing owned PostgreSQL test fixture after Docker reallocates its published port; preserve database/passwords/private metadata, refuse unsafe endpoints and prove restart/partial-write behavior before the complete gate.
- [x] P2.G31 — Retain bounded source-bound original HTTP observations for context-scope/expiry failures and original native transport probe failure metadata, preserving deadlines, request counts, assertions, cleanup and privacy.
- [x] P2.10 — Run the closed phase gate, retain evidence and resolve every failing/missing case (see quality-gates.md).

## Phase 3: Broader guardrail coverage

> **Gate:** `just evolution-gate 3` succeeds (see quality-gates.md).
> **Status:** IN PROGRESS

- [x] P3.R1 — Recheck stopped deployment/expired trust chain and independently review/test the Nix-store ancestry correction, preserving source and prior evidence.
- [ ] P3.R2 — Resolve revised recovery security scope; implement, review and bind same-key CA-certificate renewal and TLS-first recovery before replacing exactly17 credentials under the newly approved fixed deadline.
- [ ] P3.G1 — Renew expiring deployment TLS leaves under unchanged CA/keys with coherent backup, offline restore, prepared inventory publication and a new cold-start/access proof before long acceptance runs.
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
- [ ] P4.G1 — Add recoverable deployment credential renewal using new bounded credentials for existing principals/workloads, owned consumer reload and durable progress; preserve seed history, governance and expiration enforcement.
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
