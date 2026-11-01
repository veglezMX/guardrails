# Data Schema

## Conventions

These are new `guardrails-v2` domain shapes. Existing `local-proof-v1` objects retain their original definitions in the compatibility profile; conversion is explicit. Python notation below specifies types, not implementation imports. `UUID` is a canonical UUID string on the wire; `Digest` is lowercase SHA-256 hex; `Time` is UTC RFC3339. `JsonValue` is recursively null, bool, finite number, string, array or object; `JsonObject` is a string-keyed object. IDs are opaque except tenant/workload slugs: lowercase ASCII letters, digits and hyphens, 1–64 characters. All stored records include `created_at: Time`; mutable records additionally have `revision: int >= 1`. Extra fields, duplicate JSON keys, NaN and non-finite numbers are rejected. Collections have explicit limits below. No model stores raw evaluation content unless named as ephemeral. Immutable configuration resources have server-assigned IDs, integer versions and digests. A compiled policy embeds at most one version of any resource ID. Tenant/Workload quota IDs are onboarding defaults; the approved PolicyVersion.quota and stricter current organizational baseline determine runtime limits. API routes own wire command envelopes; all IDs resolve to these domain records.

## Enumerations

- `Mode = Literal["remote", "gateway", "customer"]`
- `State = Literal["disabled", "observe", "warn", "redact", "block"]`, ordered as written for floors.
- `Action = Literal["allow", "warn", "redact", "block"]`, precedence block > redact > warn > allow.
- `Stage = Literal["input", "output", "retrieval", "tool_arguments", "tool_result"]`.
- `Role = Literal["platform_admin", "tenant_admin", "author", "approver", "operator", "auditor", "viewer", "workload", "runtime"]`.
- `PolicyStatus = Literal["candidate", "approved", "rejected", "revoked"]`; status is a projection of append-only events.
- `Failure = Literal["block", "observe"]`; observe failure never authorizes a structural/residency failure.
- `RuleKind = Literal["pii", "secret", "term", "topic", "json_schema", "injection", "image_text", "bedrock"]`.

## Domain Models

### Tenant

`tenant_id: str` primary key; `display_name: str` 1–120; `status: Literal["active","suspended"]`; `baseline: PolicyRef | None`; `residency_id: UUID`; `quota_id: UUID`. Suspension rejects new evaluations and administrative writes except platform recovery. Owns workloads, principals, policies, lists and audit rows.

### Workload

Composite primary key `(tenant_id: str, workload_id: str)`; `display_name: str`; `mode: Mode`; `status: Literal["active","suspended"]`; `allowed_stages: tuple[Stage,...]` unique nonempty; `provider_ids: tuple[UUID,...]`; `quota_id: UUID`; `required_state: State`; `retention_enabled: Literal[False] = False`. One current Binding, many historical bindings and credentials. Deletion is suspension and later metadata retention cleanup, never cascading removal of audit history.

### Principal

`principal_id: UUID`; `tenant_id: str | None`; `roles: tuple[Role,...]`; `status: Literal["active","revoked"]`; `display_name: str`. Null tenant is only for platform administration; workload and runtime principals have a non-null scope. Credentials, not request fields, resolve the principal. Separate credentials for the same principal do not satisfy separation of duties.

### Credential

`credential_id: UUID`; `principal_id: UUID`; `tenant_id: str | None`; `workload_id: str | None`; `token_hash: Digest`; `expires_at: Time`; `revoked_at: Time | None`; `previous_valid_until: Time | None`. Random secrets have at least 256 bits and are returned once. Hash-only storage; rotation overlap at most 60 seconds; ordinary lifetime 24 hours, workload lifetime 30 days, configurable only downward by a tenant.

### AdminSession

`session_id: UUID`; `principal_id: UUID`; `secret_hash: Digest`; `csrf_hash: Digest`; `expires_at: Time`; `idle_expires_at: Time`; `revoked_at: Time | None`. Absolute lifetime 8 hours, idle 30 minutes. No credential or raw policy content in cookies.

### PolicyRef

`tenant_id: str`; `policy_id: str`; `version: int >= 1`; `digest: Digest`. Composite foreign key identifies immutable bytes. Organization baseline uses reserved owner `_platform`; it may be referenced through an authorized publication port, never arbitrary cross-tenant reads.

### Rule

`rule_id: str` unique in compiled policy; `kind: RuleKind`; `stages: tuple[Stage,...]`; `action: Action`; `mandatory: bool`; `minimum_state: State`; `config: RuleConfig`; `evaluator_revision: str` exact registered revision. At most 64 compiled rules. A mandatory rule's settings may be strengthened only according to the compiler's documented order; unsupported comparisons reject the override.

### RuleConfig

Discriminated by Rule.kind: PII has `entities: tuple[Literal["email","international_phone","payment_card","ipv4"],...]`; secret has `detectors: tuple[Literal["private_key","aws_access_key","github_token"],...]`; term has `list_id: UUID`; topic has `list_id: UUID` and lexical term-set matching; json_schema has `schema: JsonObject` with the supported restricted dialect; injection has `threshold: float` in 0–1, default 0.90 and `max_tokens: int = 512`; image_text has `languages: tuple[Literal["eng","spa"],...]`; bedrock has `provider_id: UUID`, `guardrail_id: str`, `guardrail_version: str` numeric and non-DRAFT. Exact algorithms and supported schema vocabulary are in guardrail-coverage.md. Policy validation rejects keys for another kind.

### ListSet

`list_id: UUID`; `tenant_id: str`; `version: int`; `kind: Literal["destination","provider","tool","content"]`; `allow: tuple[str,...]`; `deny: tuple[str,...]`; `allow_enabled: bool`; `digest: Digest`. Immutable; at most 256 entries per side, each 256 characters. Destination entries are Destination IDs, provider entries are ProviderProfile IDs, tools are exact ToolSpec names; content entries use `term:value` or `topic:value`. Deny wins. Enabled empty allow means permit nothing; disabled allow means no additional allow restriction, not permission to defeat other layers. Destination/provider/tool lists are allow-enabled by default; content allow enforcement is opt-in and explicit.

### Destination

`destination_id: UUID`; `tenant_id: str`; `version: int >= 1`; `name: str`; `scheme: Literal["https"]`; `host: str` lowercase ASCII DNS name; `port: int = 443`; `path_prefix: str`; `cidrs: tuple[str,...]`; `zone: str`; `purpose: Literal["evaluation","inference","telemetry","control","backup"]`; `tls_ca_ref: str`; `digest: Digest`. No embedded credentials, wildcards, redirects or user-supplied query templates. Loopback, metadata, link-local and undeclared private ranges forbidden unless the exact local service is registered. Revalidate resolved IPs on every new connection.

### ProviderProfile

`provider_id: UUID`; `tenant_id: str`; `version: int >= 1`; `kind: Literal["model","evaluator"]`; `vendor: Literal["ollama","bedrock","local_classifier","local_rules"]`; `model_id: str`; `artifact_digest: Digest | None`; `destination_id: UUID | None`; `zone: str`; `credential_ref: str | None`; `enabled: bool`; `digest: Digest`. Local in-process engines have no destination. Shared service never receives team model-provider credentials. Bedrock region is encoded in the registered endpoint and zone and verified against the signed configuration.

### ToolSpec

`tool_id: UUID`; `name: str` exact 1–128 characters; `tenant_id: str`; `version: int`; `argument_schema: JsonObject`; `result_schema: JsonObject | None`; `sensitive_paths: tuple[str,...]` JSON pointers; `digest: Digest`. Schemas forbid external references. A permitted tool remains subject to application authorization; no identity permission claims in this model.

### ResidencyProfile

`residency_id: UUID`; `tenant_id: str`; `version: int`; `allowed_zones: tuple[str,...]`; `destination_lists: tuple[UUID,...]`; `provider_lists: tuple[UUID,...]`; `tool_lists: tuple[UUID,...]`; `content_lists: tuple[UUID,...]`; `external_processing: bool = False`; `raw_retention: Literal[False] = False`; `metadata_retention_days: int = 30`; `digest: Digest`. Default zone `local-shared`; customer mode uses its declared local zone. External zones require external_processing true and an approved policy referencing this exact digest. This flag alone does not authorize a destination. Raw retention remains disabled in all supplied acceptance profiles.

### PolicyVersion

Composite key `(tenant_id: str, policy_id: str, version: int)`; `author_id: UUID`; `parents: tuple[PolicyRef,...]` max 3; `rules: tuple[Rule,...]`; `residency: ResidencyProfile`; `resolved_lists: tuple[ListSet,...]`; `resolved_destinations: tuple[Destination,...]` max 256, at most one version per destination ID; `providers: tuple[ProviderProfile,...]`; `tools: tuple[ToolSpec,...]`; `quota: Quota`; `failure_behavior: Failure`; `compiled_digest: Digest`; `compiler_version: str`; `status: PolicyStatus`. Stored canonical bytes include all resolved dependency bytes and schema/compiler versions, excluding lifecycle projections. Never update bytes. New dependency revisions require a new version.

Destination pinning amendment approved 2026-10-10: policy commands supply exact `{id, version, digest}` destination references; compilation resolves and embeds those bytes before approval. Network providers require a matching exact destination pin, with no runtime lookup of the latest version. New compilations use `compiler-v2.2`; historical `compiler-v2.1` canonical payloads omit the new field, retaining their original bytes, digests and approvals. Historical policies without network dependencies remain eligible; unresolved network dependencies cannot authorize egress. Endpoint changes require a new policy version, approval and release.

### Approval

`approval_id: UUID`; `tenant_id: str`; `policy: PolicyRef`; `compiled_digest: Digest`; `actor_id: UUID`; `outcome: Literal["approve","reject","revoke"]`; `reason: str` 1–500, metadata only; `evidence_ids: tuple[UUID,...]`. Author cannot approve own candidate. Approval requires successful validation/compilation and baseline compatibility; rejection is final for that candidate. Revocation is final for that digest, also invalidates compiled policies referencing it as a parent, and requires approver or platform security role; use a new version to replace it.

### Binding

Composite identity `(tenant_id: str, workload_id: str, revision: int)`; `policy: PolicyRef`; `state: State`; `release_id: UUID`; `activated_by: UUID`; `previous_revision: int | None`; `effective_after: Time`; `reason: str`; `status: Literal["pending","applied","failed","superseded"]`. Immutable history; a separately stored current pointer changes by compare-and-swap. Approval status is rechecked during activation and evaluation. Pending does not mean enforced.

### Release

`release_id: UUID`; `tenant_id: str`; `policy: PolicyRef`; `workload_ids: tuple[str,...]` max 100; `stage: Literal["canary","full"]`; `state: State`; `expected_revisions: dict[str,int]`; `status: Literal["pending","applied","failed"]`; `actor_id: UUID`; `reason: str`. Canary explicitly names workloads rather than hashing individual requests. Full rollout requires the canary evidence gate; no automatic timed promotion. Requests keep their pinned context.

### RuntimeReceipt

`runtime_id: UUID`; `tenant_id: str`; `workload_id: str`; `binding_revision: int`; `snapshot_generation: int`; `digest: Digest`; `applied_at: Time`; `health: Literal["ready","stale","failed"]`. Unique by runtime/workload/generation; every configured target runtime must acknowledge before applied status. Missing runtime never counts as success.

### Snapshot

`schema_version: Literal["guardrails-v2"]`; `runtime_id: UUID`; `tenant_id: str`; `generation: int`; `issued_at: Time`; `expires_at: Time`; `bindings: tuple[Binding,...]`; `policies: tuple[PolicyVersion,...]`; `revoked_digests: tuple[Digest,...]`; `digest: Digest`; `key_id: str`; `signature: str` base64 Ed25519. Per-tenant; verify signature, digest, expiry and monotonic generation before atomic swap. Online TTL 60 seconds; customer offline validity at most 24 hours by default and explicit in bundle.

### ContentPart

Ephemeral `part_id: str`; `kind: Literal["text","json","image"]`; `role: Literal["system","user","assistant","retrieval","tool"]`; `text: str | None`; `value: JsonValue | None`; `media_type: Literal["image/png","image/jpeg"] | None`; `data_base64: str | None`; `tool: ToolCall | None`; `source_id: str | None`. Exactly one payload field matches kind. Image bytes bounded to 4 MiB decoded and 16 megapixels; up to 4 images, 64 parts, 64 KiB total UTF-8 text/JSON, 24 MiB HTTP body. No URL or filesystem reference accepted. JSON nesting ≤32; no more than 10,000 nodes. No raw content stored in audit.

### ToolCall

Ephemeral `call_id: str`; `name: str`; `arguments: JsonObject`; `tool_digest: Digest`; `result_for_call_id: str | None`. Arguments and results require a matching signed ToolSpec. Tool arguments are not emitted to the application until the entire structured call passes. A tool result references an existing turn's evaluated call; absence is context_invalid.

### EvaluationRequest

Ephemeral `request_id: UUID`; `correlation_id: UUID`; `tenant_id: str`; `workload_id: str`; `stage: Stage`; `parts: tuple[ContentPart,...]`; `context_id: UUID | None`; `provider_id: UUID | None`; `deadline_ms: int`; `schema_version: Literal["guardrails-v2"]`. Input begins a turn; all subsequent stages require context. Workload identity must match the credential. Provider is required for model-bound input/output and must agree with connector's registered route. Stage/part combinations must be in the advertised capability contract.

### EvaluationContext

`context_id: UUID`; `tenant_id: str`; `workload_id: str`; `policy: PolicyRef`; `binding_revision: int`; `snapshot_generation: int`; `provider_id: UUID | None`; `expires_at: Time`; `evaluated_call_ids: tuple[str,...]`. TTL 120 seconds for text, 180 seconds for image profile, never beyond customer bundle expiry; online stages additionally require a fresh snapshot/revocation lease. Replicas store metadata only; request body remains transient. Context is scoped to the credential's workload and checked for revocation at every stage.

### Finding

`rule_id: str`; `category: str` catalog ID; `part_id: str`; `reason_code: str`; `start: int | None`; `end: int | None`; `json_pointer: str | None`; `score: float | None`; `evaluator_revision: str`. Offsets are Unicode codepoint half-open intervals on original text; no matched text. Image findings identify part and extracted-text provenance, not a promise of image redaction. Scores are not probabilities unless the engine documents calibration.

### Decision

`decision_id: UUID`; `request_id: UUID`; `context_id: UUID | None`; `tenant_id: str`; `workload_id: str`; `stage: Stage`; `policy: PolicyRef | None`; `binding_revision: int | None`; `snapshot_generation: int | None`; `action: Action`; `would_be_action: Action`; `reason_codes: tuple[str,...]`; `findings: tuple[Finding,...]`; `transformed_parts: tuple[ContentPart,...] | None`; `evaluation_ms: float`; `audit_status: Literal["persisted","spooled","failed"]`; `evaluator_revisions: tuple[str,...]`. Transformed parts exist iff action is redact; preserve all part IDs and roles. Image redaction is unsupported and becomes block in redaction/block states. Unknown policy prevents allow. Transformed content is response-only, excluded from metadata persistence.

### AuditEvent

`event_id: UUID`; `tenant_id: str`; `workload_id: str | None`; `actor_id: UUID | None`; `event_type: str`; `correlation_id: UUID`; `decision_id: UUID | None`; `policy_digest: Digest | None`; `binding_revision: int | None`; `reason_codes: tuple[str,...]`; `action: Action | None`; `occurred_at: Time`; `previous_hash: Digest | None`; `event_hash: Digest`; `raw_content_retained: Literal[False] = False`; `metadata: dict[str,str|int|bool]` from an explicit key allowlist. No prompts, tokens, tool arguments, terms matched, images or unsalted raw-content hashes. Event hash covers canonical safe fields. Hash chains detect accidental tampering; trusted DB/host administrators remain outside that guarantee.

### Quota

`quota_id: UUID`; `tenant_id: str`; `version: int >= 1`; `digest: Digest`; `workload_id: str | None`; `requests_per_minute: int = 60`; `burst: int = 10`; `max_inflight: int = 4`; `daily_tokens: int | None`; `daily_cost_microunits: int | None`. Evaluation tokens/requests and gateway model tokens are separate meters. Atomic counters for the UTC minute and UTC second (burst is the per-second ceiling), plus inflight leases; stricter tenant/workload bound wins. Unknown model price makes cost accounting unavailable and forbids a claim of cost-cap enforcement.

### RuntimeInstall

`runtime_id: UUID`; `tenant_id: str`; `zone: str`; `mode: Mode`; `public_key_id: str`; `version: str`; `telemetry: Literal["none","aggregate","metadata"]`; `sync_interval_s: int = 30`; `offline_max_age_s: int = 86400`; `status: Literal["registered","ready","stale","revoked"]`; `last_seen_at: Time | None`. Remote/shared runtimes poll at one second and use online TTL; the customer defaults apply only to customer mode. None telemetry disables all telemetry transmission but not requested policy synchronization.

### Bundle

`bundle_id: UUID`; `runtime_id: UUID`; `snapshot: Snapshot`; `artifacts: tuple[ArtifactRef,...]`; `minimum_runtime_version: str`; `revocation_epoch: int`; `expires_at: Time`; `digest: Digest`; `signature: str`. One tenant only; max 10 MiB manifest excluding separately hashed artifacts. Trust roots provisioned out of band. Offline import must not downgrade generation or revocation epoch.

### ArtifactRef

`name: str`; `version: str`; `source_url: str`; `sha256: Digest`; `license: str`; `size_bytes: int`; `kind: Literal["image","wheel","model","binary","fixture"]`. Downloads occur only in prepare mode; acceptance verifies exact bytes without contacting registries. No mutable tag alone is acceptable evidence.

### Capability

`capability_id: str`; `stage: Stage`; `part_kinds: tuple[str,...]`; `actions: tuple[Action,...]`; `evaluator_revision: str`; `max_input_bytes: int`; `timeout_ms: int`; `zone: str`; `status: Literal["enabled","disabled","unavailable"]`. Compiled policy requirements must be satisfiable by the runtime's enabled capabilities; no silent omission.

### StreamSession

Ephemeral `stream_id: UUID`; `context_id: UUID`; `state: Literal["collecting","evaluating","releasing","blocked","closed"]`; `received_bytes: int`; `deadline_at: Time`; `finish_reason: str | None`. Entire output is buffered in memory, maximum 256 KiB or the tighter content cap. No uninspected text/tool bytes are released. Terminal state reached once; cancellation discards buffers and cancels upstream.

### EvidenceReport

`run_id: UUID`; `source_commit: str`; `source_digest: Digest`; `lock_digest: Digest`; `profile: str`; `phase: int`; `started_at: Time`; `finished_at: Time`; `cases: tuple[CaseResult,...]`; `artifact_digests: tuple[ArtifactRef,...]`; `status: Literal["pass","fail"]`; `environment: dict[str,str]` safe allowlist; `key_id: str`; `signature: str` base64 Ed25519 over canonical report bytes excluding signature. Reports never contain raw traffic or credentials.

### CaseResult

`case_id: str`; `requirement_id: str`; `status: Literal["pass","fail","skip"]`; `duration_ms: float`; `assertions: int`; `reason_codes: tuple[str,...]`. Required skipped/absent cases fail the phase gate. External Bedrock live tests have a separate optional profile and cannot contribute to a local pass claim.

### Error

`code: str`; `message: str` static safe text; `correlation_id: UUID`; `retryable: bool`; `details: dict[str,str|int]` safe field names/limits only. Enumerated wire mappings are in api-endpoints.md. No validation echo of submitted values.

## Configuration Schema

### ServiceConfig

`mode: Mode`; `database_url_file: str`; `tls_cert_file: str`; `tls_key_file: str`; `tls_ca_file: str`; `signing_key_file: str | None`; `backup_key_file: str | None`; `runtime_id: UUID`; `zone: str = "local-shared"`; `snapshot_ttl_s: int = 60`; `sync_interval_s: int = 1`; `text_budget_ms: int = 500`; `classifier_budget_ms: int = 3000`; `image_budget_ms: int = 10000`; `audit_reserve_ms: int = 100`; `queue_limit: int = 32`; `worker_count: int = 4`; `metadata_retention_days: int = 30`; `external_processing: bool = False`; `telemetry: Literal["none","aggregate","metadata"] = "none"`. Worker pools for classifier/OCR are separate single-worker services; their budgets never silently replace the text budget. Secrets are mounted files, never command-line arguments.

## State Machines and Transactions

Policy transitions: candidate → approved or rejected; approved → revoked. No reverse transition. Approval and rejection append lifecycle records under row lock. Same idempotency key and body replays the stored response; a different body returns conflict.

Binding transaction locks workload, checks revision, approval, floors and residency, appends history/audit/outbox and updates desired pointer atomically. Receipts update applied projection only after all targets acknowledge. Concurrent activations return one success and one conflict. New restrictive baseline publication marks incompatible bindings blocked before new requests; it never silently edits approved bytes. Migration history is not evidence of new approval.

Storage primary and foreign keys always include tenant scope. Audit, policy bytes, approval events and binding history are append-only; lifecycle projections are mutable indexes. Administrative reason text must not contain content samples. Shared DB uses forced row security and least-privilege non-owner roles. See tenancy-model.md for access boundaries.
