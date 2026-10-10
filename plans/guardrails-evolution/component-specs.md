# Component Specifications

All model references below resolve in data-schema.md. Every service returns the declared `Error` on an operational failure; no component logs raw input. Locations are repository-relative. Components appear in implementation dependency order within their phase; cross-phase dependencies always point backward. The runner is introduced early and gains evidence cases with every phase.

## TenantRepository

**Location:** `mvp/guardrails/evolution/storage.py`
**Purpose:** Implements the TenantRepository responsibility in Phase 1.

**Inputs:** `Tenant` (see data-schema.md §Tenant); `Workload` (see data-schema.md §Workload); `Principal` (see data-schema.md §Principal).

**Outputs:** `Tenant` (see data-schema.md §Tenant); `Workload` (see data-schema.md §Workload); `Error` (see data-schema.md §Error).

**Logic:**
Create PostgreSQL schema, composite foreign keys, migrations and transaction ports. Establish tenant context with SET LOCAL on every transaction using authenticated scope; runtime roles are non-owner and NOBYPASSRLS. Force row security and reset pooled connections. Expose append-only policy/audit repositories plus compare-and-swap binding operations. Migrate the proof using the migration map, with a dry run and safe report.

**Dependencies:**
- None; depends only on the declared domain and external tool contracts.

**Edge Cases:**
A missing tenant context denies access. Cross-tenant IDs behave as absent. On transaction failure, rollback all state and audit/outbox writes. Reject reused global IDs that could leak a different tenant through uniqueness errors.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## IdentityService

**Location:** `mvp/guardrails/evolution/identity.py`
**Purpose:** Implements the IdentityService responsibility in Phase 1.

**Inputs:** `Principal` (see data-schema.md §Principal); `Credential` (see data-schema.md §Credential); `AdminSession` (see data-schema.md §AdminSession).

**Outputs:** `Principal` (see data-schema.md §Principal); `Credential` (see data-schema.md §Credential); `AdminSession` (see data-schema.md §AdminSession); `Error` (see data-schema.md §Error).

**Logic:**
Provision local administrator and workload principals; issue random credentials once, store hashes, rotate with bounded overlap and revoke. Resolve roles, tenant and workload before parsing content. Require a different principal for policy approval. Exchange an administrator credential for a server-side browser session; enforce CSRF, expiry, secure cookies and session revocation.

**Dependencies:**
- `TenantRepository` (see §TenantRepository)

**Edge Cases:**
Expired/revoked credentials fail before evaluation. Two credentials for one principal are still self-approval. Platform provisioning privileges do not imply arbitrary tenant audit reads. Logs never include submitted credentials.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## AuditStore

**Location:** `mvp/guardrails/evolution/audit.py`
**Purpose:** Implements the AuditStore responsibility in Phase 1.

**Inputs:** `AuditEvent` (see data-schema.md §AuditEvent); `Decision` (see data-schema.md §Decision).

**Outputs:** `AuditEvent` (see data-schema.md §AuditEvent); `Error` (see data-schema.md §Error).

**Logic:**
Persist allowlisted decision metadata and management events using transactionally ordered per-tenant sequence/hash chains. Evaluation content is stripped before constructing any persistence object. Export paginated tenant-scoped metadata. Customer-mode spools are bounded, metadata-only and replay deduplicated by event ID. Purge metadata after configured retention while retaining a signed chain checkpoint.

**Dependencies:**
- `TenantRepository` (see §TenantRepository)

**Edge Cases:**
If required audit cannot commit or spool within the reserved budget, return audit_failed and block. DB failure must not trigger content logging. Concurrent append serializes chain position; no missing-event success. Purging is an explicit retention operation with a checkpoint, not arbitrary record deletion.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## PolicyCompiler

**Location:** `mvp/guardrails/evolution/compiler.py`
**Purpose:** Implements the PolicyCompiler responsibility in Phase 1.

**Inputs:** `PolicyVersion` (see data-schema.md §PolicyVersion); `PolicyRef` (see data-schema.md §PolicyRef); `Rule` (see data-schema.md §Rule); `ListSet` (see data-schema.md §ListSet); `ResidencyProfile` (see data-schema.md §ResidencyProfile).

**Outputs:** `PolicyVersion` (see data-schema.md §PolicyVersion); `Error` (see data-schema.md §Error).

**Logic:**
Validate all schemas and immutable dependency digests. Resolve at most three parent layers, detect cycles and missing references, then compile mandatory checks, intersect allowlists/zones, union denylists, enforce floors and check transform compatibility. Canonicalize using UTF-8 JSON with sorted keys, compact separators, deterministic sorted ID collections and forbidden non-finite numbers. Hash exact compiled bytes. Produce a readable diff of changed rule IDs and dependency digests without content samples.

**Dependencies:**
- `TenantRepository` (see §TenantRepository)

**Edge Cases:**
Reject empty enabled intersections, same rule ID with incomparable overrides, external schema references, stale digests, a mandatory rule removal, unregistered engine revisions and cross-tenant references other than authorized platform baselines. Size/depth limits prevent recursive dependency expansion.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## PolicyGovernance

**Location:** `mvp/guardrails/evolution/governance.py`
**Purpose:** Implements the PolicyGovernance responsibility in Phase 1.

**Inputs:** `PolicyVersion` (see data-schema.md §PolicyVersion); `Approval` (see data-schema.md §Approval); `Principal` (see data-schema.md §Principal).

**Outputs:** `PolicyVersion` (see data-schema.md §PolicyVersion); `Approval` (see data-schema.md §Approval); `Error` (see data-schema.md §Error).

**Logic:**
Create immutable candidates with server-assigned monotonically increasing versions. Validate and compile before review. Approve exact bytes by a non-author approver; reject with a bounded reason; revoke approved digests with an append-only event and revocation outbox. Recheck current organizational requirements at approval and activation. Publish new organization minimums with an impact preview listing affected bindings; incompatible bindings are blocked until replacement approval.

**Dependencies:**
- `IdentityService` (see §IdentityService)
- `AuditStore` (see §AuditStore)
- `PolicyCompiler` (see §PolicyCompiler)

**Edge Cases:**
Use row locks to prevent approve/reject races and idempotency records for repeat commands. Editing rejected/revoked bytes is forbidden. No bootstrap admin self-approval exception. A revoked digest cannot be re-approved by undoing its status.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## BindingService

**Location:** `mvp/guardrails/evolution/bindings.py`
**Purpose:** Implements the BindingService responsibility in Phase 1.

**Inputs:** `Binding` (see data-schema.md §Binding); `Release` (see data-schema.md §Release); `Approval` (see data-schema.md §Approval).

**Outputs:** `Binding` (see data-schema.md §Binding); `Release` (see data-schema.md §Release); `Error` (see data-schema.md §Error).

**Logic:**
Require approved exact policy and authorized operator. Validate stage support, residency, floors and expected workload revisions. Onboard in observe when floors permit; otherwise return an explicit minimum-state conflict. Create desired bindings and outbox atomically. Canary rollout names specific workloads; full rollout requires passing canary evidence. Rollback appends a new revision to an approved, unrevoked, currently compatible policy; never edits history.

**Dependencies:**
- `PolicyGovernance` (see §PolicyGovernance)
- `AuditStore` (see §AuditStore)

**Edge Cases:**
Concurrent activation yields conflict instead of last-write-wins. Pending status remains visible until runtime receipts arrive. A failed rollout preserves the old applied binding but flags desired/applied divergence. Rollback cannot lower mandatory controls or restore a now-denied destination.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## ManagementApi

**Location:** `mvp/guardrails/evolution/management_api.py`
**Purpose:** Implements the ManagementApi responsibility in Phase 1.

**Inputs:** `Principal` (see data-schema.md §Principal); `PolicyVersion` (see data-schema.md §PolicyVersion); `Approval` (see data-schema.md §Approval); `Binding` (see data-schema.md §Binding); `Release` (see data-schema.md §Release).

**Outputs:** `PolicyVersion` (see data-schema.md §PolicyVersion); `Approval` (see data-schema.md §Approval); `Binding` (see data-schema.md §Binding); `Release` (see data-schema.md §Release); `Error` (see data-schema.md §Error).

**Logic:**
Expose the route inventory and strict wire validation in api-endpoints.md. Dispatch to services; enforce idempotency and If-Match, canonical response schemas and tenant-filtered cursor pagination. Separate platform registration endpoints from tenant operations. Publish generated OpenAPI and capability metadata without secrets. Implement administrator sessions through IdentityService.

**Dependencies:**
- `IdentityService` (see §IdentityService)
- `PolicyGovernance` (see §PolicyGovernance)
- `BindingService` (see §BindingService)

**Edge Cases:**
Refuse malformed JSON before persistence. Hide foreign-tenant resource existence. Stable errors omit raw validation values. Do not accept approval or identity claims supplied in arbitrary headers.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## LifecycleCli

**Location:** `mvp/guardrails/evolution/cli.py`
**Purpose:** Implements the LifecycleCli responsibility in Phase 1.

**Inputs:** `PolicyVersion` (see data-schema.md §PolicyVersion); `Approval` (see data-schema.md §Approval); `Release` (see data-schema.md §Release).

**Outputs:** `PolicyVersion` (see data-schema.md §PolicyVersion); `Approval` (see data-schema.md §Approval); `Release` (see data-schema.md §Release); `Error` (see data-schema.md §Error).

**Logic:**
Implement the command map in input-output-contract.md against HTTPS management routes only. Read credentials from a mode-0600 file; support JSON output, safe human summaries, wait-for-applied with a deadline, policy diff, evidence inspection and rollback. Validate file size locally but keep authoritative checks server-side.

**Dependencies:**
- `ManagementApi` (see §ManagementApi)

**Edge Cases:**
No direct database access or implicit role switching. Exit nonzero on pending timeout, schema rejection or failed receipt. Printing a one-time credential requires an explicit output file with restrictive permissions; standard logs contain only its ID.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## AcceptanceRunner

**Location:** `mvp/evolution/run.py`
**Purpose:** Implements the AcceptanceRunner responsibility in Phase 1.

**Inputs:** `EvidenceReport` (see data-schema.md §EvidenceReport); `CaseResult` (see data-schema.md §CaseResult); `ArtifactRef` (see data-schema.md §ArtifactRef).

**Outputs:** `EvidenceReport` (see data-schema.md §EvidenceReport); `Error` (see data-schema.md §Error).

**Logic:**
Create a closed phase-specific case registry and commands in quality-gates.md. Fail for an unknown phase, missing case, unexpected skip, zero assertions, missing artifact pin or stale source digest. Capture source/lock/image/model hashes and safe environment metadata. Phase 1 creates the runner; every later phase expands its own required registry before claiming its gate.

**Dependencies:**
- None; depends only on the declared domain and external tool contracts.

**Edge Cases:**
A test process exiting zero with no cases is failure. Store diagnostic attempts separately from passing evidence. Never overwrite a previous report. Optional external profiles cannot satisfy local mandatory case IDs.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## EvaluatorRegistry

**Location:** `mvp/guardrails/evolution/plugins.py`
**Purpose:** Implements the EvaluatorRegistry responsibility in Phase 2.

**Inputs:** `Capability` (see data-schema.md §Capability); `ArtifactRef` (see data-schema.md §ArtifactRef).

**Outputs:** `Capability` (see data-schema.md §Capability); `Error` (see data-schema.md §Error).

**Logic:**
Load an explicit, pinned built-in registry implementing plugin-contract.md. Verify declared stage/part/action support at startup and before activation. Start with the existing local email/phone engine, then register Phase 3 and 6 engines only after their gates pass. Route expensive work to isolated workers; enforce deadline and cancellation.

**Dependencies:**
- None; depends only on the declared domain and external tool contracts.

**Edge Cases:**
Unknown revision or required capability unavailable makes runtime unready for affected policy. No dynamic pip installation or plugin import from submitted policy. One worker crash must not corrupt another tenant context.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## ResidencyGuard

**Location:** `mvp/guardrails/evolution/residency.py`
**Purpose:** Implements the ResidencyGuard responsibility in Phase 2.

**Inputs:** `ResidencyProfile` (see data-schema.md §ResidencyProfile); `Destination` (see data-schema.md §Destination); `ProviderProfile` (see data-schema.md §ProviderProfile); `ListSet` (see data-schema.md §ListSet); `EvaluationRequest` (see data-schema.md §EvaluationRequest).

**Outputs:** `Error` (see data-schema.md §Error); `Capability` (see data-schema.md §Capability).

**Logic:**
Apply zone, destination and provider constraints before any content is sent to a worker or model route. Validate tool eligibility at structured boundaries. Resolve registered DNS names, check all resolved addresses against pinned CIDRs, verify TLS hostname/CA and disable redirects. Generate the egress intent consumed by deployment tests; never trust request-declared region as evidence.

**Dependencies:**
- `PolicyCompiler` (see §PolicyCompiler)

**Edge Cases:**
Deny wins over allow. DNS rebinding, wildcard host, alternate ports, redirected URL and undeclared provider alias fail closed. Metadata, backups and error reporting obey the same zone restrictions. Local provider allow does not authorize external evaluator fallback.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## QuotaService

**Location:** `mvp/guardrails/evolution/quotas.py`
**Purpose:** Implements the QuotaService responsibility in Phase 2.

**Inputs:** `Quota` (see data-schema.md §Quota); `Principal` (see data-schema.md §Principal).

**Outputs:** `Quota` (see data-schema.md §Quota); `Error` (see data-schema.md §Error).

**Logic:**
Maintain atomic per-tenant and workload UTC minute counters and UTC second burst counters with expiring inflight leases; both limits must admit. Reserve before evaluation, release inflight exactly once on completion/cancellation, reconcile abandoned leases. Operated gateway adds separate daily token/cost reservations in Phase 5; guardrail calls and model usage remain separate meters.

**Dependencies:**
- `TenantRepository` (see §TenantRepository)

**Edge Cases:**
Counter storage unavailable denies admission. One busy tenant must not consume another reserved concurrency allocation. Clock boundaries and retries cannot double-reserve; idempotency is scoped to authenticated caller and request ID. Return safe Retry-After on quota rejection.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## SnapshotDistributor

**Location:** `mvp/guardrails/evolution/snapshots.py`
**Purpose:** Implements the SnapshotDistributor responsibility in Phase 2.

**Inputs:** `Snapshot` (see data-schema.md §Snapshot); `Binding` (see data-schema.md §Binding); `RuntimeReceipt` (see data-schema.md §RuntimeReceipt).

**Outputs:** `Snapshot` (see data-schema.md §Snapshot); `RuntimeReceipt` (see data-schema.md §RuntimeReceipt); `Error` (see data-schema.md §Error).

**Logic:**
Build per-tenant canonical snapshots and Ed25519 signatures from committed outbox rows. Deliver over authenticated TLS using ETag and runtime identity. Track every target runtime receipt and mark bindings applied only after all expected digests match. Publish revocation sets; shared runtimes poll each second and stop using snapshots older than 60 seconds.

**Dependencies:**
- `BindingService` (see §BindingService)
- `IdentityService` (see §IdentityService)
- `EvaluatorRegistry` (see §EvaluatorRegistry)

**Edge Cases:**
Reject out-of-order, wrong-tenant, expired or mismatched receipts. Keep desired and applied states distinct. Signing-key rotation accepts two pinned public keys only during a bounded overlap. Control-plane outage does not fabricate fresh timestamps.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## EvaluationRuntime

**Location:** `mvp/guardrails/evolution/runtime_api.py`
**Purpose:** Implements the EvaluationRuntime responsibility in Phase 2.

**Inputs:** `EvaluationRequest` (see data-schema.md §EvaluationRequest); `EvaluationContext` (see data-schema.md §EvaluationContext); `Snapshot` (see data-schema.md §Snapshot).

**Outputs:** `Decision` (see data-schema.md §Decision); `Error` (see data-schema.md §Error).

**Logic:**
Authenticate workload, bound input, verify fresh snapshot and policy eligibility, reserve quota and pin context. Evaluate declared parts through registry under the applicable budget. Aggregate findings deterministically; apply enforcement-state mapping; validate redaction before return. Persist safe audit before allowing release. Validate every subsequent stage against context, revocations and current mandatory restrictions.

**Dependencies:**
- `EvaluatorRegistry` (see §EvaluatorRegistry)
- `ResidencyGuard` (see §ResidencyGuard)
- `QuotaService` (see §QuotaService)
- `SnapshotDistributor` (see §SnapshotDistributor)
- `AuditStore` (see §AuditStore)

**Edge Cases:**
No content cache across requests. On resource exhaustion return bounded error with no model dispatch. Mandatory failure blocks even in observe. Stale/revoked context is rejected. Overlapping redactions merge on original text offsets; JSON transforms replace only validated string leaves and revalidate the final schema.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## LiteLLMConnector

**Location:** `mvp/connector/evolution.py`
**Purpose:** Implements the LiteLLMConnector responsibility in Phase 2.

**Inputs:** `EvaluationRequest` (see data-schema.md §EvaluationRequest); `Decision` (see data-schema.md §Decision); `EvaluationContext` (see data-schema.md §EvaluationContext).

**Outputs:** `Decision` (see data-schema.md §Decision); `Error` (see data-schema.md §Error).

**Logic:**
Adapt the existing native guardrail integration for v2, retaining verified pre/post hooks and a request boundary. Normalize all supported messages with stable IDs, ignore caller attempts to disable mandatory hooks, check normalized provider route against registered profile, and apply only valid returned transforms. Keep content and context request-local. Phase 2 supports non-streaming text; Phase 6 adds the separately tested boundary and streaming components.

**Dependencies:**
- `EvaluationRuntime` (see §EvaluationRuntime)

**Edge Cases:**
Any unsupported shape fails explicitly before model dispatch. Missing/mismatched decision or failed output inspection withholds response. Retry evaluation transport only under the integration contract, never repeat inference. A compatibility manifest lists the exact tested LiteLLM version and surfaces.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## LocalDeployment

**Location:** `deploy/evolution/bootstrap.py`
**Purpose:** Implements the LocalDeployment responsibility in Phase 2.

**Inputs:** `ServiceConfig` (see data-schema.md §ServiceConfig); `ArtifactRef` (see data-schema.md §ArtifactRef); `Destination` (see data-schema.md §Destination).

**Outputs:** `ArtifactRef` (see data-schema.md §ArtifactRef); `EvidenceReport` (see data-schema.md §EvidenceReport); `Error` (see data-schema.md §Error).

**Logic:**
Prepare locked images/models/CA material, check resources and create only the named Minikube profile. Install Calico and Helm releases for shared runtime, PostgreSQL and two independent gateways. Use default-deny ingress/egress plus explicit permitted flows. Apply non-root/read-only workload settings, namespace RBAC, resource limits, readiness and secret mounts. Provide idempotent up/status/down and an explicitly separate destructive reset.

**Dependencies:**
- None; depends only on the declared domain and external tool contracts.

**Edge Cases:**
Verify a negative network probe actually fails; a NetworkPolicy YAML file alone is not evidence. Fail before starting if host headroom is insufficient. Never modify the default kube context or unrelated clusters. After prepare, deployment and acceptance must not need registry access.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## SampleApplication

**Location:** `examples/evolution/client.py`
**Purpose:** Implements the SampleApplication responsibility in Phase 2.

**Inputs:** `EvaluationRequest` (see data-schema.md §EvaluationRequest); `Decision` (see data-schema.md §Decision).

**Outputs:** `Decision` (see data-schema.md §Decision); `EvidenceReport` (see data-schema.md §EvidenceReport); `Error` (see data-schema.md §Error).

**Logic:**
Demonstrate team A and B onboarding using distinct credentials, gateway configurations and model aliases. Run deterministic mock cases for exact outputs, and real local Ollama traffic for end-to-end inference. Later extend the same sample with retrieval, authorized toy tool execution and tool-result inspection through BoundarySdk.

**Dependencies:**
- `LiteLLMConnector` (see §LiteLLMConnector)
- `LocalDeployment` (see §LocalDeployment)

**Edge Cases:**
Do not assert stochastic generated prose equals a golden string. Assert hooks, policy attribution, data path and forbidden dispatch behavior. Keep model-provider keys on gateway side; synthetic model fixtures cannot be described as real model coverage.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## RuleEvaluators

**Location:** `mvp/guardrails/evolution/rules.py`
**Purpose:** Implements the RuleEvaluators responsibility in Phase 3.

**Inputs:** `Rule` (see data-schema.md §Rule); `ContentPart` (see data-schema.md §ContentPart).

**Outputs:** `Finding` (see data-schema.md §Finding); `Error` (see data-schema.md §Error).

**Logic:**
Implement each exact detector and supported schema rule in guardrail-coverage.md. Extract string leaves of structured input with JSON pointers; preserve number/boolean types. Apply explicit term/topic allow/deny semantics and fail compilation on unsupported patterns. Provide pure span findings; the runtime owns aggregation and transformation.

**Dependencies:**
- `EvaluatorRegistry` (see §EvaluatorRegistry)

**Edge Cases:**
Check Unicode normalization mapping against original offsets; ambiguous mapping blocks transformation. Validate payment-card checksum, private-key delimiters and exact secret prefix grammars. A lexical topic detector advertises its lexical coverage and never claims semantic moderation.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## ClassifierWorker

**Location:** `services/classifier/app.py`
**Purpose:** Implements the ClassifierWorker responsibility in Phase 3.

**Inputs:** `ContentPart` (see data-schema.md §ContentPart); `Rule` (see data-schema.md §Rule); `ArtifactRef` (see data-schema.md §ArtifactRef).

**Outputs:** `Finding` (see data-schema.md §Finding); `Error` (see data-schema.md §Error).

**Logic:**
Run the pinned pretrained prompt-injection classifier in an isolated CPU service with its own Python environment. Load local artifacts only, trust_remote_code false, and eval mode with deterministic tokenization. Evaluate overlapping 512-token windows with stride 256; max 16 windows, reject over-limit input. Return maximum injection score, class mapping, model digest and threshold result. Benchmark held-out fixtures before enabling capability.

**Dependencies:**
- `EvaluatorRegistry` (see §EvaluatorRegistry)

**Edge Cases:**
Do not truncate a long tail silently. Cold model load happens before readiness. Timeout, NaN output, unknown class mapping, artifact mismatch or overload returns evaluator_unavailable. No online model downloads and no content-bearing telemetry.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## BedrockAdapter

**Location:** `mvp/guardrails/evolution/bedrock.py`
**Purpose:** Implements the BedrockAdapter responsibility in Phase 3.

**Inputs:** `Rule` (see data-schema.md §Rule); `ContentPart` (see data-schema.md §ContentPart); `ProviderProfile` (see data-schema.md §ProviderProfile); `ResidencyProfile` (see data-schema.md §ResidencyProfile).

**Outputs:** `Finding` (see data-schema.md §Finding); `Error` (see data-schema.md §Error).

**Logic:**
Implement the managed-engine port using ApplyGuardrail with pinned numeric guardrail version, explicit region and INPUT/OUTPUT mapping. Normalize intervention and sanitized findings without leaking vendor shapes into the Decision contract. Test request/response mapping and errors locally with recorded synthetic protocol fixtures. Supply a distinct live verification command disabled under local-only residency.

**Dependencies:**
- `EvaluatorRegistry` (see §EvaluatorRegistry)
- `ResidencyGuard` (see §ResidencyGuard)

**Edge Cases:**
Never send content when external processing or destination/provider approval is absent. Reject DRAFT, cross-region mismatch and unsupported stages. Disable SDK automatic retries beyond the declared budget. Masked output is accepted only when safely mappable to original parts; otherwise block.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## AdminConsole

**Location:** `mvp/guardrails/evolution/admin.py`
**Purpose:** Implements the AdminConsole responsibility in Phase 4.

**Inputs:** `AdminSession` (see data-schema.md §AdminSession); `PolicyVersion` (see data-schema.md §PolicyVersion); `Approval` (see data-schema.md §Approval); `Release` (see data-schema.md §Release); `AuditEvent` (see data-schema.md §AuditEvent).

**Outputs:** `AdminSession` (see data-schema.md §AdminSession); `PolicyVersion` (see data-schema.md §PolicyVersion); `Approval` (see data-schema.md §Approval); `Release` (see data-schema.md §Release); `Error` (see data-schema.md §Error).

**Logic:**
Implement server-rendered templates and self-hosted assets for the journeys in admin-experience.md. Reuse management service operations with the same identity and authorization. Show policy diff/digest, author and approval state, desired versus applied revision, rollout receipts, tenant scope, metadata-only audit and operational health. Protect state changes with CSRF and revision checks.

**Dependencies:**
- `ManagementApi` (see §ManagementApi)

**Edge Cases:**
A hidden button is not authorization. Stale tabs receive conflict with a refresh path. Never render submitted policy strings as HTML. No tokens in localStorage, external fonts/CDNs, raw prompt previews or cross-tenant search.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## OperationalTelemetry

**Location:** `mvp/guardrails/evolution/telemetry.py`
**Purpose:** Implements the OperationalTelemetry responsibility in Phase 4.

**Inputs:** `AuditEvent` (see data-schema.md §AuditEvent); `RuntimeReceipt` (see data-schema.md §RuntimeReceipt); `ServiceConfig` (see data-schema.md §ServiceConfig).

**Outputs:** `EvidenceReport` (see data-schema.md §EvidenceReport); `Error` (see data-schema.md §Error).

**Logic:**
Expose bounded-label metrics for latency, errors, actions, queue pressure, freshness, approval/rollout health and quota rejections. Build local Prometheus rules and dashboards. Structured logs contain IDs and codes only; validate against a canary corpus of forbidden raw values. Run capacity and fault scenarios defined by quality-gates.md and export a measured envelope.

**Dependencies:**
- `EvaluationRuntime` (see §EvaluationRuntime)

**Edge Cases:**
Tenant or workload labels require bounded registry cardinality; arbitrary content/model aliases never become labels. Metrics failure cannot cause secret-bearing fallback logs. Alert on stale policy, failed rollout, audit saturation and denied egress without paging an external service.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## RecoveryManager

**Location:** `deploy/evolution/recovery.py`
**Purpose:** Implements the RecoveryManager responsibility in Phase 4.

**Inputs:** `ArtifactRef` (see data-schema.md §ArtifactRef); `EvidenceReport` (see data-schema.md §EvidenceReport).

**Outputs:** `EvidenceReport` (see data-schema.md §EvidenceReport); `Error` (see data-schema.md §Error).

**Logic:**
Create AES-256-GCM encrypted local PostgreSQL backups with a fresh random 96-bit nonce per backup and signed metadata manifests; authenticate the backup ID and schema version as associated data. Keep the encryption key in a separate operator-mounted file, never inside the backup. Retain the last seven successful backups by default. Restore into an isolated namespace, verify counts/digests/row security, restore audit checkpoints and invalidate administrator/workload sessions as configured. Reconcile desired/applied state before reopening traffic. Exercise forward schema migration and previous-image rollback on a compatible expanded schema.

**Dependencies:**
- `TenantRepository` (see §TenantRepository)
- `SnapshotDistributor` (see §SnapshotDistributor)

**Edge Cases:**
Never prove restore by merely checking backup file existence. A schema-breaking downgrade requires the documented maintenance restore, not a blind old-image launch. Lost signing/encryption keys stop recovery. Restored revocations and generation watermarks cannot regress.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## CustomerRuntime

**Location:** `mvp/guardrails/evolution/customer.py`
**Purpose:** Implements the CustomerRuntime responsibility in Phase 5.

**Inputs:** `RuntimeInstall` (see data-schema.md §RuntimeInstall); `Bundle` (see data-schema.md §Bundle); `Snapshot` (see data-schema.md §Snapshot).

**Outputs:** `RuntimeReceipt` (see data-schema.md §RuntimeReceipt); `Decision` (see data-schema.md §Decision); `Error` (see data-schema.md §Error).

**Logic:**
Package the same runtime and engines for customer namespace installation with a tenant-only signed bundle and local SQLite cache/spool. Support online pull and manual offline import, atomic snapshot replacement, trust-root rotation and declared upgrade compatibility. Execute without synchronous control-plane access until bundle expiry. Enforce none/aggregate/metadata telemetry settings and bounded replay. Customer mode supplies the same repository/quota/context ports with a single-owner SQLite WAL adapter, using local transactional counters and no synchronous PostgreSQL dependency.

**Dependencies:**
- `EvaluationRuntime` (see §EvaluationRuntime)
- `SnapshotDistributor` (see §SnapshotDistributor)
- `RecoveryManager` (see §RecoveryManager)

**Edge Cases:**
None telemetry emits zero telemetry requests. Expired bundle blocks; last-known-good is not indefinite. Revocation cannot be instantaneous while disconnected: enforce signed expiry and expose last-sync age. Never import lower generation/revocation epoch or another tenant bundle.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## OperatedGateway

**Location:** `deploy/evolution/operated_gateway.py`
**Purpose:** Implements the OperatedGateway responsibility in Phase 5.

**Inputs:** `Workload` (see data-schema.md §Workload); `ProviderProfile` (see data-schema.md §ProviderProfile); `Quota` (see data-schema.md §Quota).

**Outputs:** `RuntimeReceipt` (see data-schema.md §RuntimeReceipt); `EvidenceReport` (see data-schema.md §EvidenceReport); `Error` (see data-schema.md §Error).

**Logic:**
Deploy an additional provider-owned LiteLLM release for consumers choosing mode gateway. Configure tenant-scoped virtual credentials, registered provider aliases, mandatory guardrail attachment, route restrictions and model token/cost reservation reconciliation. Expose inference endpoint and per-tenant usage metadata, keeping provider credentials in gateway-only secrets. Retain the two independently owned team gateways unchanged.

**Dependencies:**
- `LiteLLMConnector` (see §LiteLLMConnector)
- `QuotaService` (see §QuotaService)
- `LocalDeployment` (see §LocalDeployment)

**Edge Cases:**
Unknown aliases, guardrail disable flags and credential cross-use fail. Compare reserved and actual usage and release unused reservation once. Unknown pricing is shown as unavailable, not zero cost. Test no caller can bypass the connector through a second unguarded route.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## MediaInspector

**Location:** `services/media/app.py`
**Purpose:** Implements the MediaInspector responsibility in Phase 6.

**Inputs:** `ContentPart` (see data-schema.md §ContentPart); `Rule` (see data-schema.md §Rule); `ArtifactRef` (see data-schema.md §ArtifactRef).

**Outputs:** `ContentPart` (see data-schema.md §ContentPart); `Finding` (see data-schema.md §Finding); `Error` (see data-schema.md §Error).

**Logic:**
Decode only bounded PNG/JPEG in a sandboxed worker, normalize orientation, strip metadata and run pinned local Tesseract English/Spanish OCR. Keep extracted text and provenance ephemeral; evaluate through the same declared text checks. Scan every supplied image and reject malformed/multipage/animated unsupported content. Return findings against image part IDs.

**Dependencies:**
- `EvaluatorRegistry` (see §EvaluatorRegistry)
- `RuleEvaluators` (see §RuleEvaluators)

**Edge Cases:**
Pixel bombs, decompression limits, external URL inputs, low extraction confidence on nonblank images, timeout and corrupt data fail closed when inspection is required. Text-free supported images receive an explicit text-inspection-only result. Never claim visual harmful-content detection or redact image bytes with text offsets.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## BoundarySdk

**Location:** `mvp/connector/boundaries.py`
**Purpose:** Implements the BoundarySdk responsibility in Phase 6.

**Inputs:** `EvaluationRequest` (see data-schema.md §EvaluationRequest); `ToolCall` (see data-schema.md §ToolCall); `ContentPart` (see data-schema.md §ContentPart).

**Outputs:** `Decision` (see data-schema.md §Decision); `Error` (see data-schema.md §Error).

**Logic:**
Provide explicit application hooks for retrieval before prompt assembly, structured tool arguments before execution, and tool results before reuse. Register ToolSpec digests and enforce four-category restrictions. Update connector normalization for JSON, tool calls and inline image parts. SampleApplication demonstrates a separate business-authorization check after guardrail approval and before executing its toy tool.

**Dependencies:**
- `LiteLLMConnector` (see §LiteLLMConnector)
- `RuleEvaluators` (see §RuleEvaluators)
- `MediaInspector` (see §MediaInspector)

**Edge Cases:**
Treat retrieved text and tool results as untrusted, with distinct part roles. Block unknown tool/action names, schema failures, dangling call IDs and tool name changes during streaming assembly. One denied part blocks the containing operation; no partial tool batch executes.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).

## StreamingGuard

**Location:** `mvp/connector/streaming.py`
**Purpose:** Implements the StreamingGuard responsibility in Phase 6.

**Inputs:** `StreamSession` (see data-schema.md §StreamSession); `EvaluationContext` (see data-schema.md §EvaluationContext); `ContentPart` (see data-schema.md §ContentPart).

**Outputs:** `Decision` (see data-schema.md §Decision); `StreamSession` (see data-schema.md §StreamSession); `Error` (see data-schema.md §Error).

**Logic:**
Collect upstream SSE through LiteLLM into a bounded in-memory complete message, including fragmented tool arguments. Evaluate output once with the pinned context, then release approved/transformed content using gateway-owned SSE serialization. Before release only content-free keepalives are permitted. Propagate cancellation and enforce total duration/body bounds. Observe mode uses the same buffered default to keep the guarantee simple.

**Dependencies:**
- `BoundarySdk` (see §BoundarySdk)

**Edge Cases:**
A secret spanning any chunk boundary is inspected as one string. Truncated JSON, missing finish marker, disconnect, timeout or revoked policy before release yields terminal error and zero content bytes. A block after HTTP headers uses a documented SSE error event; it never emits DONE as a successful completion.

**Error Conditions:** Reject invalid input with a typed error before side effects; transaction, deadline and capability failures follow the logic above and the wire mappings (see api-endpoints.md §Errors). Every documented edge case requires a rejecting or recovery test (see test-strategy.md).
