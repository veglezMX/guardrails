# HTTP and CLI contracts

## Common transport

Owned APIs use JSON over TLS. Requests carry `Authorization: Bearer TOKEN`; mutation `X-Correlation-ID` is an optional UUID. `schema_version=local-proof-v1` is required in versioned request envelopes. Body identity fields, when present, must match credentials. Strict unknown-field/type/duplicate-key rules are in data-schema.md. A tenant operator is scoped to exactly one tenant; cross-tenant objects return 404 to avoid enumeration. Workload credentials can only evaluate/read their own runtime configuration and submit their own client events. Security admin can manage tenants/floors/content-retention operations; runtime_service and management_service have only their named internal endpoint capabilities.

Canonical wire projections serialize model field names as snake_case, tuples as JSON arrays, UTC timestamps as strings. Read-only computed fields cannot be supplied on writes. JSON request examples below are model references to avoid conflicting field definitions. Query limits default to 50, range 1–200, with opaque cursor pagination and deterministic timestamp/event-ID order.

## Problem

Owned-service errors use `application/problem+json`: `type: "urn:guardrails:problem:<code>"; title: str; status: int; code: str; correlation_id: UUID; errors: list[{field: str, code: str}]`. Safe fixed titles only; no input value, text excerpt, credential, stack trace or arbitrary exception detail. 400 malformed JSON; 401 invalid credentials; 403 role/identity rejection; 404 missing scoped object; 409 duplicate version/revision/governance conflict; 413 size; 422 invalid schema/capability; 429 capacity; 503 unavailable/not-ready/audit failure. API error handlers replace FastAPI's default validation serialization. A valid evaluation resulting in block is HTTP 200 Decision; enforcement happens at the gateway boundary.

## Management endpoints

Paths below have prefix `/v1`. All updates, state/rollback/rotation POSTs and DELETE require `If-Match` of the current resource revision, except first binding/floor creation, which requires `If-None-Match: *`. Missing precondition is 428; stale precondition 409. A mutation reason is the ChangeReason enum from data-schema.md, not a free-form note; unknown reasons reject. State mutation responses are 202 with `{resource, operation: OperationStatus}`; registration/publication readback is immediately committed but runtime distribution still uses the returned operation.

| Method and path | Input projection / authorization | Output | CLI command |
| --- | --- | --- | --- |
| POST `/tenants` | Tenant tenant_id/display_name; security_admin | 201 Tenant | `guardrails tenants register --id ID --name NAME` |
| GET `/tenants/{t}/workloads` | Tenant operator or security_admin | paginated Workload | `guardrails workloads list --tenant T` |
| POST `/tenants/{t}/workloads` | Workload workload_id/display_name; scoped operator/admin | 201 `{workload, token, operation}`; one-time token | `guardrails workloads register --tenant T --id W --name NAME --credential-out PATH` |
| GET `/tenants/{t}/workloads/{w}` | Scoped operator/admin | Workload | `guardrails workloads get --tenant T --id W` |
| POST `/tenants/{t}/workloads/{w}/credentials/rotate` | reason=credential_rotation; If-Match is current credential_revision; scoped operator/admin | 201 `{credential_id, token, operation}` | `guardrails workloads rotate-credential --tenant T --id W --revision N --reason CODE --credential-out PATH` |
| POST `/tenants/{t}/policies/validate` | Policy writable fields; scoped operator/admin | 200 `{valid:true, canonical_digest, capabilities_version}` or Problem | `guardrails policies validate --tenant T --file FILE` |
| POST `/tenants/{t}/policies` | Same writable Policy; scoped operator/admin, true retention admin-only | 201 `{policy,operation}`; 200 identical replay; 409 changed bytes | `guardrails policies publish --tenant T --file FILE` |
| GET `/tenants/{t}/policies` | Scoped operator/admin | paginated Policy summaries (omit check bodies) | `guardrails policies list --tenant T` |
| GET `/tenants/{t}/policies/{p}/versions/{v}` | Scoped operator/admin | Full Policy including digest | `guardrails policies get --tenant T --id P --version V` |
| PUT `/tenants/{t}/workloads/{w}/binding` | policy_id, policy_version, input/output timeouts, reason; no state allowed on creation | 202 binding+operation; initial Observe; later version change retains state | `guardrails bindings set --tenant T --workload W --policy P --version V --revision N --reason CODE` (omit revision to create) |
| GET `/tenants/{t}/workloads/{w}/binding` | Scoped operator/admin | `{desired:PolicyBinding, applied_revision:int|null, applied_generation:int, cache_age_s:float|null}` | `guardrails bindings get --tenant T --workload W` |
| GET `/tenants/{t}/workloads/{w}/binding/history` | Scoped operator/admin | paginated binding revisions | `guardrails bindings history --tenant T --workload W` |
| POST `/tenants/{t}/workloads/{w}/binding/state` | enforcement_state, reason, evidence_kind=synthetic_local_proof, fixture_run_id; revision precondition | 202 binding+operation after floor validation | `guardrails bindings state --tenant T --workload W --state STATE --revision N --reason CODE --fixture-run UUID` |
| POST `/tenants/{t}/workloads/{w}/binding/rollback` | target_revision, reason; current revision precondition | 202 new binding revision+operation after current floor validation | `guardrails bindings rollback --tenant T --workload W --to-revision N --revision CURRENT --reason CODE` |
| DELETE `/tenants/{t}/workloads/{w}/binding` | reason; scoped operator/admin; reject mandatory binding removal | 202 operation | `guardrails bindings remove --tenant T --workload W --revision N --reason CODE` |
| PUT `/governance/floors/{scope}/{scope_id}` | GovernanceFloor writable fields; security_admin; organization scope_id is org; tenant floor scope_id is that tenant_id | 202 floor+operation, reject incompatible bindings/combined floors | `guardrails floors set --scope S --scope-id ID --file FILE --revision N` |
| GET `/governance/floors/{scope}/{scope_id}` | Scoped operator/admin | GovernanceFloor | `guardrails floors get --scope S --scope-id ID` |
| GET `/operations/{id}` | Actor's tenant or admin | OperationStatus | `guardrails operations get --id UUID` |
| GET `/audit` | tenant_id required for operator; optional workload/correlation/source/category filters | paginated AuditEvent, source_status, complete flag | `guardrails audit list --tenant T --workload W --correlation UUID --source SOURCE` (filters optional) |

Audit queries default to both management and runtime sources, including replayed connector events. If one is unavailable return 503 with source_status unless caller requests `allow_partial=true`; then return 200 with complete=false and explicit unavailable sources. Never pretend an empty partial result is complete. Content is omitted unless security_admin uses include_content=true; that read itself is audited. All queries enforce tenant scope independently of filters.

## Evaluation and internal endpoints

| Service / endpoint | Contract | Authorization and behavior |
| --- | --- | --- |
| Runtime POST `/v1/evaluate` | Evaluation writable fields wrapped with schema_version; returns Decision | workload only, identity matched; input creates context/output consumes it; block is 200 |
| Runtime GET `/v1/client-config` | ClientConfig for authenticated workload | workload only, no caller workload selector |
| Runtime GET `/v1/capabilities` | `{schema_version, supported_stages:[input,output], supported_actions:[allow,log_only,redact,block], evaluators:[{id:local_pii,format:pii-ascii-v1,category:GR-003}]}` | workload or management_service credential verified locally; does not advertise retained legacy evaluators |
| Runtime POST `/v1/client-events` | array of up to 100 AuditEvent, source=connector | workload only; reject claimed other identity/content; deduplicate IDs |
| Runtime GET `/internal/v1/audit` | Same paginated audit projection scoped by validated delegation | management service token; explicit tenant filter mandatory |
| Management GET `/internal/v1/snapshot` | RuntimeSnapshot / 304 | runtime_service token; no public policy distribution endpoint |
| Management POST `/internal/v1/applied` | `{generation:int, digest:str}` | runtime_service; reject nonexistent/mismatched generation |
| Both GET `/health/live` | `{live:true}` | no content/identity detail |
| Management GET `/health/ready` | `{ready:bool, degraded:bool}` | operator/admin; 503 when not ready |
| Runtime GET `/health/ready` | `{ready:bool, degraded:bool, applied_generation:int|null, cache_age_s:float|null}` | management_service credential installed in local runtime configuration; 503 when not ready |
| Management GET `/openapi.json` | Generated management OpenAPI specification | operator/admin; UI disabled |
| Management GET `/runtime-openapi.json` | Proxied runtime schema | operator/admin; unavailable runtime returns 503 |
| Runtime GET `/openapi.json` | Generated runtime OpenAPI specification | management_service credential verified locally; UI disabled |

CLI `guardrails evaluate --runtime-url URL --token-file FILE --file REQUEST` is a direct Evaluation API diagnostic; it does not invoke a model. CLI `guardrails capabilities` calls the capability endpoint. Legacy file-only `validate` is replaced by the management validation command; no file edit path is a lifecycle shortcut.

## Request revision and retry rules

GET can retry bounded transport failures; writes and evaluations cannot be automatically retried. Repeating publication of identical immutable content is safe. Binding preconditions prevent double application; on ambiguous timeout read current resource and operation status before choosing the next write. Credential issuance failure may leave a registered workload without a delivered secret: read it back and explicitly rotate using its revision, invalidating the old credential. This resolves onboarding without storing plaintext issuance responses or inventing broad idempotency infrastructure.
