# Canonical domain and configuration models

## Conventions

Python notation below specifies internal models; api-endpoints.md owns their HTTP projections. Strict validation rejects unknown fields, string-to-boolean/number coercion, non-finite numbers and duplicate JSON keys. IDs use ASCII `[a-z0-9][a-z0-9_-]{0,63}`; UUIDs are server-generated unless noted. Timestamps are UTC RFC3339. Collections in domain objects are tuples/frozen mappings, recursively; a frozen dataclass around a mutable dict is insufficient. JSON serialization sorts object keys without changing list order and hashes UTF-8 canonical bytes using SHA-256.

## Enumerations

- EnforcementState = disabled | observe | warn | redact | block, ordered in that sequence.
- DecisionAction = allow | log_only | redact | block, ordered in that sequence.
- Stage reserves input | output | retrieval | tool_call | tool_result | memory_write | final_response. Only input/output are active; the other five are recognized but rejected as unsupported_surface without adding any new stage (DEC-014).
- FailureAction = block | allow_and_log; block is stronger. Transformation failure is constrained as defined in variance-matrix.md.
- Role = security_admin | tenant_operator | workload | runtime_service | management_service.
- EventSource = management | runtime | connector.
- ReasonCode = GR-003:local_pii:email | GR-003:local_pii:phone; every detector reason uses the DEC-013 category namespace.
- ProblemCode / OperationalCode includes disabled, evaluation_timeout, evaluation_unavailable, policy_unresolved, management_unavailable, audit_write_failed, unsafe_transformation, unsupported_surface, invalid_request, identity_mismatch, authentication_failed, mandatory_floor_violation, context_invalid, context_expired, capacity_exceeded, client_unconfigured, upstream_failed. These are transport/operational status codes, not detector reason_codes.
- ChangeReason = initial_binding | policy_update | synthetic_promotion | synthetic_demotion | rollback | credential_rotation | floor_update | binding_removal. No arbitrary note text is persisted in this release.

## Tenant

`tenant_id: str; display_name: str; created_at: datetime`.
Tenant creation is a security-admin operation. The bootstrap creates `local-proof`; additional tenants are used for isolation tests. Tenant IDs are immutable.

## Workload

`tenant_id: str; workload_id: str; display_name: str; created_at: datetime; credential_revision: int >= 1; enforcement_posture: Literal["cooperative"]`.
Unique `(tenant_id, workload_id)`. Registration does not imply a binding. Credentials are separate records; workload identity is always derived from the authenticated credential, never trusted from a body alone.

## Credential

`credential_id: UUID; role: Role; tenant_id: str | None; workload_id: str | None; token_sha256: str; revision: int; created_at: datetime; revoked_at: datetime | None; service_id: str | None; capabilities: tuple[str, ...]`.
Generate 32 random bytes as a URL-safe opaque bearer token. Return its secret only on creation/rotation, never store plaintext in SQLite, snapshots, audit or logs. Constant-time hash comparison. Tenant operators have one tenant; workload credentials have exactly one tenant/workload; security admin is the only global role. Runtime-service credentials can retrieve snapshots and acknowledge generations only. Management-service credentials can retrieve runtime audit, schema and detailed readiness only. Both identify a named service; neither can evaluate or manage workloads. Gateway event submission uses its workload credential. Bootstrap stores service-token hashes in each receiving service's restricted configuration, without publishing them to workload snapshots.

## CheckSpec

`check_id: str; evaluator: Literal["local_pii"]; stages: tuple[Stage, ...]; pii_types: tuple[Literal["email", "phone"], ...]; requested_action: Literal["log_only", "redact", "block"]`.
Nonempty unique stages/types; check IDs unique per policy. Only this evaluator is supported by the proof publication capability set. Detector format version is `pii-ascii-v1`, supplied by the registered evaluator and included in the policy digest. No arbitrary regular expressions or per-connector thresholds.

## EvaluatorCapabilities

`evaluator_id: Literal["local_pii"]; detector_format_version: Literal["pii-ascii-v1"]; supported_stages: tuple[Stage,...]; guardrail_category: Literal["GR-003"]; pii_types: tuple[Literal["email","phone"],...]`. The release capability record advertises both types and input/output only.

## FailureBehavior

`evaluation_timeout: FailureAction; evaluation_unavailable: FailureAction; policy_unresolved: FailureAction; management_unavailable: FailureAction; audit_failure: FailureAction; transformation_failure: FailureAction`.
All six fields are mandatory on publication. Explicit proof seed uses block for all six; acceptance publishes a separate allow-and-log configuration to prove it. Unbound workloads use the explicit deployment-level unresolved-policy action because no policy can be resolved. Never infer an allow default.

## Policy

`tenant_id: str; policy_id: str; version: str; publication_ordinal: int >= 1; checks: tuple[CheckSpec, ...]; failure_behavior: FailureBehavior; retain_content: bool; schema_version: Literal["local-proof-v1"]; detector_format_version: Literal["pii-ascii-v1"]; digest: str; published_at: datetime; published_by: UUID`.
Version is an opaque ID, not a sort key. Ordinal increases per tenant/policy ID transactionally. Nonempty checks. Runtime capability validation occurs at validation AND publication AND snapshot loading. Only security_admin may publish retain_content=true. Identical republish is a readback success; same version with different canonical content is a conflict. Database rows cannot be updated/deleted after publication. Policy digest covers only schema_version, tenant_id, policy_id, version, checks, failure_behavior, retain_content and detector_format_version; exclude digest, ordinal and publication timestamp/actor so identical replay is stable.

## PolicyBinding

`tenant_id: str; workload_id: str; policy_id: str; policy_version: str; enforcement_state: EnforcementState; revision: int >= 1; generation: int >= 1; input_timeout_ms: int; output_timeout_ms: int; updated_at: datetime; updated_by: UUID; reason: ChangeReason; evidence_kind: Literal["synthetic_local_proof"] | None; fixture_run_id: UUID | None`.
One current binding per workload, plus append-only revision history. Initial state MUST be observe; clients cannot create directly in another state. After creation any explicit state change is permitted if floors allow it, including demotion; call it a state change and audit old/new values. Local promotion requires `evidence_kind=synthetic_local_proof` and fixture_run_id, never claims live approval. Timeouts default to 1,500 ms, must be 750–5,000 ms and exceed the 500 ms runtime budget. Binding updates use expected revision. A version change retains the current state; rollback restores a named historical version/state/timeouts through a new revision and current floor checks.

## GovernanceFloor

`scope: Literal["organization", "tenant"]; tenant_id: str | None; revision: int; require_binding: bool; required_stages: tuple[Stage, ...]; required_pii_types: tuple[str, ...]; minimum_action: DecisionAction; minimum_state: Literal["disabled", "observe"]; required_policy_id: str | None; minimum_policy_ordinals: frozenmap[str, int]; require_fail_closed: tuple[str, ...]; forbid_content_retention: bool`.
Floors apply conjunctively as validation predicates, never policy composition. This local proof supports only Disabled/Observe floor minimum states, preserving mandatory Observe onboarding; higher floor states reject as unsupported rather than trapping a new workload. Workload enforcement states still support all five values. Staged inactive onboarding for higher mandatory states is a later extension. Ordinal constraints apply when the policy_id matches their key. required_policy_id, when set, requires that exact policy ID; conflicting non-null organization/tenant IDs make a floor update invalid. A different policy must still satisfy every generic check, and cannot bypass an explicit required ID. Required stages and PII types must appear together in a check at or above minimum_action, so split weaker checks cannot pass. An unbound registered workload remains inactive under a required-binding floor: evaluation always blocks regardless of deployment fail-open settings; initial binding still starts Observe and must satisfy the floor. This allows registration before publication without bypassing mandatory controls. A security admin sets organization floors and tenant floors; tenant operators cannot change either. Reject a floor update that invalidates any existing applicable binding, and reject removal of a binding when require_binding=true. Default seed floors are empty/no mandatory binding, permitting five-state demonstrations; later acceptance installs a floor and proves weakening rejection.

## TextSegment

`segment_id: str; text: str`.
IDs are unique within a batch, maximum 32. Segment IDs contain only generated indices, never user text. Offsets into text use Python/Unicode code points, not UTF-8 bytes. Do not normalize Unicode or concatenate message boundaries before matching.

## TextBatch

`segments: tuple[TextSegment, ...]`.
One to 32 segments, aggregate UTF-8 length <=1 MiB. Empty segment text is valid. Gateway input maps every string message to an index segment; output has one segment. This vendor-neutral contract does not contain LiteLLM role or model fields.

## Evaluation

`evaluation_id: UUID; tenant_id: str; workload_id: str; stage: Stage; content: TextBatch; correlation_id: UUID; context_id: str | None; received_at: datetime`.
Input forbids context_id; output requires it. Caller correlation is accepted only as a valid UUID or generated at gateway ingress. Claimed identities must match authentication. Policy selection comes only from the binding. Evaluation bodies exist in memory unless explicit retention applies.

## Finding

`check_id: str; segment_id: str; reason_code: str; start: int; end: int`.
Require `0 <= start < end <= len(segment.text)` and a known segment ID. Never store matched substrings in findings. Merge overlapping/touching spans by union per segment before replacing; sort deterministically. Invalid spans fail transformation, not partial success.

## Decision

`decision_id: UUID; evaluation_id: UUID | None; correlation_id: UUID; context_id: str | None; tenant_id: str; workload_id: str; stage: Stage; policy_id: str | None; policy_version: str | None; policy_digest: str | None; binding_revision: int | None; snapshot_generation: int | None; enforcement_state: EnforcementState | None; action: DecisionAction; allowed: bool; would_be_action: DecisionAction; reason_codes: tuple[str, ...]; operational_codes: tuple[str, ...]; guardrail_category: Literal["GR-003"] | None; evaluator_metadata: tuple[EvaluatorMetadata, ...]; audit_id: UUID | None; findings: tuple[Finding, ...]; transformed_content: TextBatch | None; evaluation_ms: float >= 0; source: Literal["runtime", "connector"]; audit_status: Literal["persisted", "spooled", "failed"]; client_config: ClientConfig | None`.
EvaluatorMetadata is `evaluator_id: str; detector_format_version: str; elapsed_ms: float >= 0`; this is evaluator identity/timing metadata, not a production metrics subsystem. allowed is derived as action != block. guardrail_category is GR-003 when detector reasons exist, otherwise null; operational codes never pretend to be PII findings. audit_id is present only for a persisted event. Reasons sorted/deduplicated; finding order by segment/check/start/end. transformed_content is required only for redact and must cover every segment. Never send original text back for allow/block. Unknown action or invalid transformation received by the connector is a protocol failure. Connector fallback has no fabricated runtime decision/evaluation identifiers: its own decision_id is distinguished by source, evaluation_id is nullable in that projection. Input responses include the pinned timeout/fallback projection for the turn.

## EvaluationContext

`context_id: str; tenant_id: str; workload_id: str; correlation_id: UUID; binding: PolicyBinding; policy: Policy; snapshot_generation: int; client_config: ClientConfig; created_at_monotonic: float; expires_at_monotonic: float; output_consumed: bool`.
Random 32-byte token; memory-only. No text. Bind token to authenticated identity and correlation. Allow at most one output evaluation; duplicate/incompatible/expired context is rejected through explicit failure handling. Release after output/block or expiry; cap active contexts. Runtime restart invalidates contexts and requires safe connector fallback.

## RuntimeSnapshot

`generation: int; generated_at: datetime; workloads: tuple[Workload, ...]; credential_hashes: tuple[Credential, ...]; policies: tuple[Policy, ...]; bindings: tuple[PolicyBinding, ...]; floors: tuple[GovernanceFloor, ...]; digest: str`.
Internal TLS retrieval only; omit admin/operator credentials and any secret. Include workload credential hashes only. Snapshot digest covers generation, generated_at and every payload collection, excluding digest itself. Verify references, digest, capabilities and governance before an atomic swap. On equal generation use ETag/304 heartbeat. Reject lower generations; rollback is a newer generation containing an older policy reference.

## ClientConfig

`schema_version: Literal["local-proof-v1"]; tenant_id: str; workload_id: str; generation: int; input_timeout_ms: int; output_timeout_ms: int; failure_behavior: FailureBehavior; retrieved_at: datetime`.
Only timeouts, fallback actions and identity/version metadata; no detection rules, thresholds, checks or policy bodies. Gateway retrieves it using its workload credential and retains the last valid configuration during outages. A never-configured client refuses inference. Refresh every second; input Decision can replace the per-turn configuration without altering the shared cache. No on-disk client-config retention is needed: a restarted gateway waits for runtime readiness.

## AuditEvent

`event_id: UUID; source: EventSource; occurred_at: datetime; tenant_id: str | None; workload_id: str | None; actor_id: UUID | None; event_type: str; correlation_id: UUID | None; metadata: frozenmap[str, scalar | tuple]; content: TextBatch | None`.
Metadata allowlist: decision/evaluation IDs, policy ID/version/digest, binding revision, desired/applied generation, state, stage, action/would_be_action, namespaced reason codes, operational codes, guardrail_category, allowed, evaluator metadata, audit ID, validated ChangeReason, finding coordinates/check IDs, evaluation_ms, cache age, operation ID, previous/new references, synthetic fixture-run reference, audit status. Arbitrary caller strings/exception text are forbidden. Retain content only under authorized true policy; management records never contain it. Client fallback event IDs are stable across spool retries and deduplicated on ingestion.

## OperationStatus

`operation_id: UUID; desired_generation: int; applied_generation: int; status: Literal["pending", "applied"]; committed_at: datetime; applied_at: datetime | None`.
Application status requires authenticated runtime acknowledgement and matching generation. It does not assert every connector has refreshed client configuration; expose their individual cached generations in proof health/evidence.

## ProofConfig

`runtime_deadline_ms=500; audit_reserve_ms=100; poll_interval_ms=1000; max_snapshot_age_s=60; context_ttl_s=120; max_contexts=1024; max_segments=32; max_content_bytes=1048576; max_http_bytes=8388608; workers=4; max_queued_jobs=32; sqlite_busy_timeout_ms=100; unresolved_policy_behavior="block"; management_unavailable_without_snapshot="block"; model_timeout_s=10`.
All fields are strict positive integers except literal failure values. Runtime safety settings are deployment config, visible in evidence. Fault tests may lower staleness/deadlines through explicit proof-profile configuration, never change production defaults implicitly.

## ProofFixture

Tagged union of PiiFixture, LifecycleFixture and FailureFixture, all with unique fixture_id and `acceptance_rows: tuple[int, ...]` drawn from 1–10. Only authored synthetic content. Loader rejects unknown fixture fields and validates request templates against the API models before execution.

## PiiFixture

`fixture_id: str; kind: Literal["pii"]; acceptance_rows: tuple[int,...]; label: Literal["clean","email","phone","overlap","invalid_span","unsupported_format"]; stage: Stage; input_segments: TextBatch; mock_scenario: str; policy_alias: str; enforcement_state: EnforcementState; expected_findings: tuple[Finding,...]; expected_reason_codes: tuple[str,...]; expected_operational_codes: tuple[str,...]; expected_action: DecisionAction; expected_transformed: TextBatch | None; expect_model_call: bool`.
Invalid-span cases use a proof-only evaluator injection, never imply the real PII detector emits those spans. Unsupported-format cases document coverage limits without claiming comprehensive safety.

## LifecycleFixture

`fixture_id: str; kind: Literal["lifecycle"]; acceptance_rows: tuple[int,...]; actor_alias: str; interface: Literal["api","cli"]; steps: tuple[LifecycleStep,...]`.
LifecycleStep is `operation: Literal["register","publish","bind","state","update","rollback","floor","inspect","rotate","unbind"]; request_template: frozenmap; expected_status: int; expected_problem_code: str | None`. Templates resolve named aliases to run-local IDs and are validated against the corresponding endpoint schema (see api-endpoints.md). All scenario policy documents are canonical writable Policy projections; templates never mutate a live database directly.

## FailureFixture

`fixture_id: str; kind: Literal["failure"]; acceptance_rows: tuple[int,...]; failure_location: Literal["worker_timeout","runtime_down","management_down","audit_write","unsafe_span","malformed_response","context_expired","client_unconfigured"]; policy_alias: str; stage: Stage; enforcement_state: EnforcementState; expected_action: DecisionAction; expected_source: Literal["runtime","connector"]; expected_operational_code: str; expect_model_call: bool; expect_delivery: bool`.
Fault controls are selected through the dedicated proof-only credentialed endpoint or service orchestration. No prompt-derived switches, arbitrary executable fixture code or persistent raw spy payloads.

## EvidenceReport

`run_id: UUID; started_at: datetime; commit: str; platform: str; versions: frozenmap[str,str]; image_digests: frozenmap[str,str]; lock_checksums: frozenmap[str,str]; config_digest: str; acceptance_rows: tuple[RowResult,...]; offline_probe_passed: bool; limitations: tuple[str,...]`.
RowResult is `row: int[1..10]; passed: bool; scenario_ids: tuple[str,...]; assertion_count: int; artifact_paths: tuple[str,...]; sanitized_error_codes: tuple[str,...]`. Metadata only; never serialize raw request captures, tokens, full exception bodies or sensitive fixture values into persistent reports.

## Persistence layout

Control SQLite tables: tenants, workloads, credentials, policies, bindings_current, binding_history, governance_floors, operations, runtime_ack, management_audit, schema_migrations. The operations table stores actor_id and tenant_id for authorization as internal persistence fields in addition to OperationStatus. Foreign keys on; WAL mode; transactional writes; unique policy keys and ordinal constraints; immutable policy/history triggers. All state-changing transactions include a management audit row or roll back.

Runtime SQLite tables: decision_audit, client_events, schema_migrations; deduplicate client_events by event_id. A runtime-only writer owns this file. In-memory snapshots and contexts are rebuilt/refetched after restart; no raw body cache is persisted. Files/directories use 0600/0700 permissions. Initial schema version is 1; migration script rejects newer unknown versions, upgrades transactionally and keeps a backup before a future destructive migration. The proof reset command explicitly targets disposable proof volumes only.
