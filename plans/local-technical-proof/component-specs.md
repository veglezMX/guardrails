# Component specifications

All models below are canonical in data-schema.md; wire projections belong to api-endpoints.md. Components follow domain → application → adapter boundaries (see folder-structure.md).

## DomainContracts

**Location:** `mvp/guardrails/contract.py`

**Purpose:** Represent the canonical immutable entities and request/decision structures.

**Inputs:** Canonical model values (see data-schema.md).

**Outputs:** Validated frozen domain models or typed domain errors.

**Logic:**

1. Add TextBatch, context metadata and namespaced decision/audit fields; preserve the six core entities and reserved vocabulary.
2. Deep-freeze nested mappings/collections and defensively copy inputs. Compute allowed from action and reject inconsistent decisions.
3. Keep transport, database and LiteLLM imports outside domain modules.

**Dependencies:** (see data-schema.md).

**Edge Cases:** Mutating a source dictionary after publication cannot alter the domain object; connector fallback IDs must remain visibly distinct.

**Error Conditions:** Invalid enums, identity references or decision transformations fail validation.

## StrictContracts

**Location:** `mvp/guardrails/transport.py`

**Purpose:** Validate external JSON and convert it into canonical domain models without unsafe coercion.

**Inputs:** HTTP/CLI JSON and domain models (see data-schema.md).

**Outputs:** Strict projections, sanitized Problem errors (see api-endpoints.md §Problem).

**Logic:**

1. Enforce byte limits before parsing, reject duplicate keys and unknown fields, require strict bool/int types and schema_version.
2. Convert to/from DomainContracts; retain all required decision fields and safe null semantics.
3. Generate OpenAPI from the same request/response models and replace default validation errors with allowlisted fields/codes.

**Dependencies:** (see component-specs.md §DomainContracts).

**Edge Cases:** String false, booleans in numeric fields, oversized JSON, invalid Unicode and reserved unsupported stages are rejecting cases.

**Error Conditions:** Return the exact status and safe code; never echo the submitted value.

## ControlRepository

**Location:** `mvp/guardrails/storage/control.py`

**Purpose:** Own durable control state and atomic management mutations.

**Inputs:** Tenant, Workload, Credential, Policy, PolicyBinding, GovernanceFloor, AuditEvent (see data-schema.md).

**Outputs:** Persisted models and OperationStatus.

**Logic:**

1. Initialize schema version 1 with constraints, immutable-row triggers and migration checks.
2. Serialize writes with transactions: resource/history, audit, generation and operation all commit or all roll back.
3. Read snapshots from one consistent transaction; support revision comparisons and scoped paginated reads.

**Dependencies:** (see data-schema.md §Persistence layout).

**Edge Cases:** Restart retains publications/history/operations; identical publication readback cannot allocate a new ordinal.

**Error Conditions:** Database busy/full/read-only, failed audit insert or future schema version returns a typed storage failure.

## IdentityService

**Location:** `mvp/guardrails/management/identity.py`

**Purpose:** Authenticate and authorize local actors, services and workloads.

**Inputs:** Bearer credential plus target Tenant/Workload and operation capability (see data-schema.md).

**Outputs:** Authenticated Credential identity, issued/rotated secret or authorization failure.

**Logic:**

1. Generate 32-byte opaque secrets; hash before storage, compare hashes in constant time, and expose issuance once over TLS.
2. Enforce role, tenant and service capability boundaries before object access. Derive runtime identity from snapshot hashes.
3. Register tenants/workloads and rotate credentials transactionally; snapshot updates distribute credential revisions.

**Dependencies:** (see component-specs.md §ControlRepository).

**Edge Cases:** A lost issuance response is recoverable by explicit credential rotation; cached known identities during outage follow documented revocation lag.

**Error Conditions:** Unknown/revoked credential, role mismatch or cross-tenant target rejects without disclosing object existence.

## PolicyService

**Location:** `mvp/guardrails/management/policies.py`

**Purpose:** Validate and publish one immutable policy version.

**Inputs:** Policy writable fields, actor Credential and local evaluator capabilities (see data-schema.md §Policy).

**Outputs:** Policy validation report or published Policy with OperationStatus.

**Logic:**

1. Strictly validate checks, stage support, failure settings and content-retention authority.
2. Canonicalize content-only fields, assign transactional publication ordinal, and hash the canonical bytes.
3. Read back identical publication; reject changed content under an existing version; never update a published row.

**Dependencies:** (see component-specs.md §ControlRepository).

**Edge Cases:** Unsupported retained prototype evaluators must be rejected before publication; metadata timestamps do not affect equality.

**Error Conditions:** Schema, capability, authorization or conflicting publication errors are explicit.

## BindingService

**Location:** `mvp/guardrails/management/bindings.py`

**Purpose:** Apply governed binding creation, state changes, updates, removals and rollback.

**Inputs:** PolicyBinding mutation, expected revision, actor and applicable GovernanceFloor (see data-schema.md).

**Outputs:** New binding/history revision and OperationStatus or governance conflict.

**Logic:**

1. Create in Observe only; validate the selected immutable policy and all applicable floor predicates.
2. Apply explicit state/version/timeout changes with actor/reason; synthetic promotions require fixture evidence metadata.
3. Rollback copies a historical binding into a new revision only after current floor checks. Validate floor updates against every affected binding atomically.

**Dependencies:** (see component-specs.md §PolicyService).

**Edge Cases:** No binding removal below a required floor; incompatible organization/tenant policy IDs reject; concurrent updates cannot overwrite history.

**Error Conditions:** Missing precondition, stale revision, unknown version or weakened mandatory control rejects with no partial mutation.

## SnapshotPublisher

**Location:** `mvp/guardrails/management/snapshots.py`

**Purpose:** Expose a consistent generation and track runtime application.

**Inputs:** ControlRepository state, RuntimeSnapshot acknowledgements (see data-schema.md).

**Outputs:** RuntimeSnapshot, ETag or OperationStatus readback.

**Logic:**

1. Serialize a full snapshot from committed state, include workload hashes only, and compute its digest.
2. Serve unchanged generations as 304; authenticate runtime-specific capability.
3. Validate generation/digest acknowledgements before monotonically updating applied state and operation completion.

**Dependencies:** (see component-specs.md §ControlRepository).

**Edge Cases:** A version rollback still has an increasing generation; pending status survives a runtime outage.

**Error Conditions:** Forged/lower/nonexistent acknowledgements reject; storage failure never serves a partial snapshot.

## ManagementAPI

**Location:** `mvp/guardrails/management/app.py`

**Purpose:** Expose the complete management lifecycle and safe evidence inspection.

**Inputs:** HTTPS management requests (see api-endpoints.md §Management endpoints).

**Outputs:** Canonical projections, OperationStatus and scoped AuditEvent pages.

**Logic:**

1. Wire StrictContracts, IdentityService, PolicyService, BindingService and SnapshotPublisher; disable UI endpoints.
2. Return committed mutation state separately from runtime application status.
3. Federate control/runtime audit queries with explicit completeness/source status; content reads require admin and are audited.

**Dependencies:** (see api-endpoints.md).

**Edge Cases:** Partial audit sources are never silently omitted; validation does not mutate state; administrator credentials do not appear in snapshots.

**Error Conditions:** Use Problem for auth, validation, conflict, unavailable source and management audit failure.

## SnapshotCache

**Location:** `mvp/guardrails/runtime/snapshots.py`

**Purpose:** Keep one atomic current configuration without synchronous control-plane calls.

**Inputs:** RuntimeSnapshot and authenticated refresh results (see data-schema.md).

**Outputs:** Immutable snapshot, age/readiness metadata and acknowledgement.

**Logic:**

1. Poll with the documented deadline and ETag; validate schema/references/digest/capabilities/floors before swap.
2. Track last successful contact with a monotonic clock; rejected candidates do not refresh health.
3. Keep last good data during outage, apply maximum-age behavior, and expose generation; cold boot is not ready.

**Dependencies:** (see integration-protocols.md §Management to runtime).

**Edge Cases:** In-flight contexts retain older objects; 304 renews freshness; clock changes cannot make age negative.

**Error Conditions:** Invalid snapshots/lower generations are rejected; no snapshot yields configured unavailable behavior.

## EvaluationRuntime

**Location:** `mvp/guardrails/runtime/app.py`

**Purpose:** Authenticate evaluations and pin policy identity for each complete model turn.

**Inputs:** Evaluation, RuntimeSnapshot and EvaluationContext (see data-schema.md).

**Outputs:** Decision and runtime audit/configuration endpoints.

**Logic:**

1. Bound body/queue/context capacity, authenticate workload and check stage/identity before handling content.
2. On input resolve the current binding, create an opaque identity/correlation-scoped context and evaluate the whole batch.
3. On output validate/consume that context once, evaluate with its pinned objects, and release it; expire abandoned contexts.
4. Serve client config and deduplicated connector events using workload scope; serve audit only to a restricted management-service caller.

**Dependencies:** (see component-specs.md §SnapshotCache).

**Edge Cases:** No context text persistence; policy changes affect new inputs but not existing turns; restart invalidates contexts safely.

**Error Conditions:** Mismatch, expiry, absent binding, saturation or unavailable snapshot uses the specified error/failure contract.

## DecisionEngine

**Location:** `mvp/guardrails/engine.py`

**Purpose:** Aggregate findings and enforce state, transformation and failure precedence.

**Inputs:** Evaluation, pinned Policy and PolicyBinding; findings from DeadlineExecutor (see data-schema.md).

**Outputs:** Decision with category, allowed, evaluator metadata, timing and audit identifier.

**Logic:**

1. Skip detectors only for Disabled; otherwise evaluate required stage checks and validate all returned findings.
2. Sort/aggregate reasons and actions, compute would_be_action then normal state ceiling.
3. Validate span bounds, merge unions per segment and replace from original offsets; unsafe transformation follows explicit policy.
4. Persist an allowlisted audit record and apply non-weakening audit fallback once; measure actual stage time.

**Dependencies:** (see variance-matrix.md).

**Edge Cases:** All clean segments preserved; overlap must not leak suffixes; failure actions are not silently clamped by Observe.

**Error Conditions:** Timeout, evaluator failure, unsafe spans or audit failure produces explicit operational codes and configured behavior.

## LocalPIIEvaluator

**Location:** `mvp/guardrails/evaluators/pii.py`

**Purpose:** Detect the documented email/phone formats locally and deterministically.

**Inputs:** TextBatch and CheckSpec (see data-schema.md).

**Outputs:** Finding tuples with category-prefixed reason codes.

**Logic:**

1. Scan each segment independently using bounded parsing/patterns for the exact ASCII formats.
2. Validate token boundaries and length before accepting a match; do not detect substrings of malformed longer tokens.
3. Return code-point coordinates only, with no raw text in metadata or logs.

**Dependencies:** (see plugin-contract.md).

**Edge Cases:** Unicode surroundings, punctuation, adjacent tokens, malformed email/phone lengths and overlaps have explicit fixtures.

**Error Conditions:** Invalid evaluator configuration is rejected at publication; unexpected detector failure returns a typed evaluator error.

## DeadlineExecutor

**Location:** `mvp/guardrails/runtime/executor.py`

**Purpose:** Enforce actual execution deadlines and bounded resource usage.

**Inputs:** LocalPIIEvaluator job and ProofConfig limits (see data-schema.md §ProofConfig).

**Outputs:** Finding tuples or typed timeout/capacity error.

**Logic:**

1. Admit work into a bounded queue and execute at most four jobs in isolated worker processes.
2. Start the monotonic stage deadline before queueing; reserve the final 100 ms for audit, cancel queued/evaluator work at the 400 ms cutoff, and terminate/replace an overrunning worker asynchronously.
3. Reap workers and drain pipes after success/failure before releasing capacity; cleanup must not block the response. Never log worker input.

**Dependencies:** (see component-specs.md §LocalPIIEvaluator).

**Edge Cases:** A stalled job cannot consume unbounded workers or prevent subsequent healthy requests; shutdown cleans children.

**Error Conditions:** Actual timeout, worker crash, queue saturation or invalid worker result is observable and follows policy.

## AuditRepository

**Location:** `mvp/guardrails/storage/audit.py`

**Purpose:** Persist and query safe runtime and connector evidence.

**Inputs:** AuditEvent and scoped query filters (see data-schema.md §AuditEvent).

**Outputs:** Durable audit_id, pages or typed audit failure.

**Logic:**

1. Use the runtime-owned SQLite writer with bounded busy time; enforce metadata/content allowlists before serialization.
2. Deduplicate connector event IDs and prohibit client-submitted raw content or foreign workload identity.
3. Apply tenant/category/source filters and stable cursor pagination; omit content unless the management request carries authorized explicit delegation.

**Dependencies:** (see data-schema.md §Persistence layout).

**Edge Cases:** Audit write time is within runtime deadline; duplicate spool replay is harmless; disk full never turns a block into allow.

**Error Conditions:** Unavailable/full/locked audit destination reports failure, not a successful persistence identifier.

## RuntimeClient

**Location:** `mvp/connector/core.py`

**Purpose:** Call the runtime asynchronously and apply explicit connector fallback.

**Inputs:** TextBatch, identity, context and ClientConfig (see data-schema.md).

**Outputs:** Validated Decision or connector-origin fallback plus spool event.

**Logic:**

1. Fetch/refresh policy-free client configuration; refuse inference before first valid configuration.
2. Use verified TLS and HTTPX stage deadlines without content retries; validate response identity, action, context and transformed segment mapping.
3. On transport/protocol failure capture configured behavior and emit a distinct content-free fallback event; replay spool idempotently.

**Dependencies:** (see integration-protocols.md §Gateway to runtime).

**Edge Cases:** Fail-open must continue the real model workflow; an unpinned input failure cannot masquerade as a later successful output evaluation.

**Error Conditions:** Unknown action, malformed transformation, no config, TLS error or timeout follows the explicit boundary rules.

## LiteLLMConnector

**Location:** `mvp/connector/litellm_hooks.py`

**Purpose:** Enforce the product contract at real gateway request and response hooks.

**Inputs:** Supported gateway request/response and RuntimeClient decisions (see input-output-contract.md).

**Outputs:** Guarded model response or safe gateway error envelope.

**Logic:**

1. Install mandatory native pre/post hooks and validate supported route/fields before model execution.
2. Normalize every string message to a segment, apply input transformation/block and keep context in request-local state.
3. Validate non-streaming single-text output, evaluate with the same context and transform/block before delivery.
4. Preserve required guardrails metadata and safe error/status normalization through actual LiteLLM serialization.

**Dependencies:** (see integration-protocols.md §LiteLLM adapter mechanism).

**Edge Cases:** Caller omission/override cannot disable hooks; concurrent requests cannot exchange contexts; no direct model route bypass is advertised.

**Error Conditions:** Unsupported shape, missing registration, upstream failure or invalid runtime result fails explicitly.

## LifecycleCLI

**Location:** `mvp/guardrails/cli.py`

**Purpose:** Give internal teams the full lifecycle using the same APIs.

**Inputs:** CLI arguments, policy/request files and restricted token/CA files (see input-output-contract.md).

**Outputs:** Safe JSON output, issued credential file and documented exit code.

**Logic:**

1. Parse commands and invoke management/runtime HTTP endpoints only; never edit database or server deployment files.
2. For mutations wait for the applied-generation operation by default; preserve committed status on timeout.
3. Write issued tokens atomically to new 0600 files; render errors without content or secrets; provide offline help.

**Dependencies:** (see api-endpoints.md).

**Edge Cases:** Explicit no-wait is successful committed state, not applied state; credential-out existing path rejects rather than overwrites.

**Error Conditions:** Syntax, validation, authority, conflict, service and wait timeout use distinct documented exit codes.

## ProofStack

**Location:** `mvp/proof/bootstrap.py`

**Purpose:** Prepare pinned images, local TLS/credentials and a repeatable isolated service topology.

**Inputs:** Pinned stack configuration, host platform and ProofConfig (see data-schema.md).

**Outputs:** Prepared images/locks/certificates, sanitized deployment manifest and readiness.

**Logic:**

1. Resolve/install candidate pins in the selected service image, record transitive package snapshots and verified wheel hashes, and record actual image digests.
2. Generate local CA/leaf certificates and independent credential files with restrictive permissions; configure loopback ports and an internal network.
3. Provide bootstrap/start/stop/reset tasks without unbounded retries or downloads during acceptance; reset touches only disposable proof state.

**Dependencies:** (see tech-stack.md).

**Edge Cases:** Missing prepared images fail clearly under pull-never; certificate SAN mismatch fails; existing non-proof volumes are untouched.

**Error Conditions:** Unsupported platform, unresolved dependency, unavailable image, invalid trust or unhealthy service returns nonzero.

## MockModel

**Location:** `mvp/proof/mock_model.py`

**Purpose:** Provide deterministic local inference and volatile model-call evidence.

**Inputs:** Configured mock scenario and normalized inference request (see integration-protocols.md §Model and fault protocol).

**Outputs:** Single clean/sensitive response or selected failure; memory-only spy.

**Logic:**

1. Implement the minimal provider-compatible non-streaming chat route over verified TLS.
2. Validate service credential and capture call count/input in bounded memory only.
3. Provide harness-only scenario/spy controls; clear memory between scenarios and disable body logging.

**Dependencies:** (see input-output-contract.md).

**Edge Cases:** Sensitive output must be available even if input was redacted; blocked input yields zero calls.

**Error Conditions:** Invalid scenario/auth/request or injected timeout/error returns the prescribed sanitized response.

## AcceptanceRunner

**Location:** `mvp/proof/acceptance.py`

**Purpose:** Produce complete repeatable evidence for all ten acceptance rows.

**Inputs:** ProofFixture corpus, running ProofStack and selected artifact metadata (see data-schema.md).

**Outputs:** EvidenceReport and nonzero exit if any required assertion/evidence fails.

**Logic:**

1. Use isolated disposable run IDs, register workloads and exercise lifecycle separately through API and CLI.
2. Execute real two-gateway PII/state/version/failure cases and inspect volatile mock calls and durable audit metadata.
3. Prove offline network isolation and scan generated persistence/logs for raw fixture content/secrets.
4. Write ten individually accounted row results and version/image/lock/config metadata; rerun cleanly without broadening scope claims.

**Dependencies:** (see test-strategy.md).

**Edge Cases:** Missing/skipped row fails; unsupported shapes must fail before inference; authored corpus is distinguished from retained runtime traffic.

**Error Conditions:** Setup, assertion, evidence, privacy or egress failures return nonzero with safe diagnostics.

