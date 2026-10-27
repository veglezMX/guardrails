# API Endpoints

## Trigger Condition

Management, evaluation, synchronization and administration expose nontrivial HTTPS interfaces. This document owns wire shapes; data-schema.md owns domain records.

## Common Contract

Base paths are `/v2` on the management or runtime service as specified below. JSON UTF-8 only; reject unknown fields, duplicate keys, non-finite values and unsupported schema versions. Tenant slug `t`, workload slug `w`, policy slug `p`, numeric version `v`, and UUID resource IDs use domain constraints. Authenticate before processing content. Request tenant/workload must equal credential scope. Foreign resources return 404 to authenticated callers; unauthenticated requests return 401.

Bearer credentials for API/CLI; Secure HttpOnly SameSite=Strict sessions for UI. Browser mutations additionally require a CSRF token and same-origin checks. TLS certificate/hostname validation is mandatory. Request IDs are UUIDs in `X-Request-ID`, generated if missing. Mutations require `Idempotency-Key` UUID and changed mutable resources require `If-Match` integer revision. Missing precondition returns 428, stale revision 409. Idempotency is scoped by principal, tenant, method and route; retain management responses for 24h. Same key/different canonical body returns 409. Never store raw evaluation bodies for deduplication.

Successful records are serialized domain shapes; responses omit token_hash, secret_hash, csrf_hash, private keys and credential references from reader views. Mutations return 201 for creation, 200 for synchronous transition and 202 for a rollout awaiting receipts. No endpoint silently creates a policy or weakens a control.

## Management Routes

| Method and path | Request body / query | Response | Role |
|---|---|---|---|
| POST /v2/tenants | display_name, tenant_id; server atomically creates safe local residency/quota defaults | Tenant, 201 | platform_admin |
| GET /v2/tenants | cursor, limit | Tenant page | platform_admin; tenant actors see own only |
| POST /v2/tenants/{t}/principals | display_name, roles | Principal, 201 | tenant_admin; cannot grant platform_admin |
| POST /v2/tenants/{t}/principals/{id}/credentials | expires_at, optional workload_id | credential_id, one_time_secret, expires_at | tenant_admin; platform bootstrap separately scoped |
| POST /v2/tenants/{t}/credentials/{id}/rotate | overlap_seconds 0–60 | credential_id, one_time_secret, expires_at | tenant_admin or credential-owning operator |
| POST /v2/tenants/{t}/credentials/{id}/revoke | reason | credential_id, revoked_at | tenant_admin |
| POST /v2/tenants/{t}/workloads | workload_id, display_name, mode, allowed_stages, provider_ids, quota_id, required_state | Workload, 201 | tenant_admin |
| GET /v2/tenants/{t}/workloads | cursor, limit | Workload page | viewer and higher |
| GET /v2/tenants/{t}/workloads/{w} | none | Workload plus desired/applied binding IDs | viewer and higher |
| PATCH /v2/tenants/{t}/workloads/{w} | display_name or status only | Workload | tenant_admin, If-Match |
| POST /v2/tenants/{t}/policies/validate | policy_id, parents, rules, residency_ref, list_refs, destination_refs, provider_refs, tool_refs, quota_ref | valid, compiled_digest, resolved_refs, diagnostics | author |
| POST /v2/tenants/{t}/policies/{p}/versions | same candidate payload as validate, excluding policy_id | PolicyVersion, 201 | author |
| GET /v2/tenants/{t}/policies | cursor, limit, status | policy_id/latest-version/status summary page | viewer and higher |
| GET /v2/tenants/{t}/policies/{p}/versions/{v} | none | PolicyVersion and Approval history | viewer and higher |
| GET /v2/tenants/{t}/policies/{p}/diff | from_version, to_version | changed_rule_ids, changed_dependencies, old_digest, new_digest, impacts | author, approver, operator |
| POST /v2/tenants/{t}/policies/{p}/versions/{v}/reviews | outcome approve/reject, compiled_digest, reason, evidence_ids | Approval | approver; different principal from author |
| POST /v2/tenants/{t}/policies/{p}/versions/{v}/revoke | compiled_digest, reason | Approval, revocation operation ID | approver or platform_admin with explicit tenant scope |
| POST /v2/tenants/{t}/releases | policy, workload_ids, stage, state, expected_revisions, reason, canary_evidence_id for full | Release, 202 | operator |
| GET /v2/tenants/{t}/releases/{id} | none | Release and RuntimeReceipt list | viewer and higher |
| POST /v2/tenants/{t}/workloads/{w}/rollback | target_revision, expected_revision, reason | Binding, 202 | operator |
| GET /v2/tenants/{t}/workloads/{w}/bindings | cursor, limit | Binding history page | viewer and higher |
| GET /v2/tenants/{t}/workloads/{w}/manifest | none | tenant/workload IDs, runtime URL, CA fingerprint, contract version, tested connector version, required surfaces, desired/applied revisions | operator; no secret |
| GET /v2/tenants/{t}/audit | cursor, limit, workload_id, action, from, until | safe AuditEvent page | auditor, tenant_admin with audit role |
| GET /v2/tenants/{t}/capabilities | runtime_id optional | Capability list | viewer and higher |
| POST /v2/tenants/{t}/runtimes | zone, mode, telemetry, version, offline_max_age_s | RuntimeInstall, 201 | operator |
| POST /v2/tenants/{t}/runtimes/{id}/bundles | generation, expires_at | Bundle, 201 | operator |
| POST /v2/tenants/{t}/evidence | signed report metadata and artifact digests | EvidenceReport receipt | operator; server verifies trusted runner key |
| GET /v2/tenants/{t}/evidence/{id} | none | EvidenceReport | viewer and higher |

Immutable configuration resources use this exact route family: `POST /v2/tenants/{t}/{resource}` creates a new UUID resource at version 1, `POST /v2/tenants/{t}/{resource}/{id}/versions` creates the next immutable version of an existing resource, `GET /v2/tenants/{t}/{resource}` lists summaries, and `GET /v2/tenants/{t}/{resource}/{id}/versions/{v}` returns exact bytes. Allowed resources and bodies are `lists` → ListSet, `residency` → ResidencyProfile, `destinations` → Destination, `providers` → ProviderProfile, `tools` → ToolSpec, and `quotas` → Quota. Both creation routes exclude server-owned resource identity, tenant_id, version, digest, created_at and revision fields from request bodies. Author can submit lists/tools; tenant_admin submits destinations/providers/residency/quotas. None becomes effective until referenced by an approved policy/release; live quota changes are versioned and approved through that policy. Therefore runtime budgets cannot be relaxed by editing an unapproved configuration record. Policy validate and submit commands accept `destination_refs` (up to 256 exact `{id, version, digest}` references). Compiled policies embed `resolved_destinations`; every network provider must resolve to an exact approved destination version. Endpoint changes require a new policy version, approval and release. Historical compiler-v2.1 bytes and digests remain unchanged. Organization baseline publication uses the same governance service under reserved `_platform` scope and explicit platform roles.

Version allocation is atomic and monotonically increasing within each tenant/resource/UUID, with database-enforced uniqueness. Creation returns 201 with a Location header identifying the exact GET version route. A missing or foreign UUID returns 404. Existing scoped idempotency rules apply: retrying the same key and body returns the same version and Location, while changing the body under that key returns 409. Concurrent distinct creations receive distinct versions. Creating a version preserves all prior bytes, digests, approvals and bindings; using changed dependencies requires a newly compiled and independently approved policy before release. Lifecycle CLI `config create-version` creates a new resource without `--id` and a new version of that resource with `--id`.

Versioned configuration records all have a server-assigned UUID identity and integer version even where a domain display name is the lookup key. Returns include ID, version and digest. References sent by clients always include all three; policy materialization embeds exact resolved bytes. No configuration DELETE or in-place policy PUT exists.

## Evaluation Routes

| Method and path | Input | Output | Identity |
|---|---|---|---|
| POST /v2/evaluations | EvaluationRequest | Decision, 200 including content-block decisions | workload credential |
| GET /v2/capabilities | no body | Capability list filtered by workload | workload credential |
| GET /health/live | no body | status only | local probe |
| GET /health/ready | no body | ready flag and safe dependency codes | authenticated internal probe |

Request example:

```json
{"schema_version":"guardrails-v2","request_id":"00000000-0000-4000-8000-000000000001","correlation_id":"00000000-0000-4000-8000-000000000002","tenant_id":"team-a","workload_id":"chat","stage":"input","parts":[{"part_id":"m0","kind":"text","role":"user","text":"Hello"}],"provider_id":"00000000-0000-4000-8000-000000000003","context_id":null,"deadline_ms":500}
```

HTTP 200 means evaluation completed, not that content is allowed. Caller must inspect Decision.action. For a valid policy with an operational detector failure, a persisted block Decision may be returned with an operational reason. Failure before a valid decision can be constructed returns the Error envelope and must be treated as unavailable by the connector. The gateway maps content block to 403, upstream model failure to 502/504, and guardrail unavailability to 503; it never converts an Error to allow.

## Runtime Synchronization Routes

`GET /v2/runtime/snapshot`: runtime bearer scope selects tenant/runtime; If-None-Match supports 304 only if the current signed lease is still valid, otherwise return a newly issued Snapshot with a new generation/expiry. A 304 does not extend a cached expiry. `POST /v2/runtime/receipts`: RuntimeReceipt. `POST /v2/runtime/telemetry`: mode-specific safe batch, maximum 100 events or 64 KiB. Server rejects fields not allowed by RuntimeInstall.telemetry. None mode rejects transmission; aggregate contains counters/buckets only. Bundle import is a local CLI operation that performs all signature/tenant/generation checks before cache swap.

## Browser Routes

`POST /admin/session` exchanges a credential entered in an HTTPS form for a secure cookie and CSRF token; credential is discarded. `DELETE /admin/session` revokes it. `/admin` and its tenant-scoped pages render server-side using identical permission checks. No browser identity uses an unverified reverse-proxy header. No application end-user authentication is introduced.

## Pagination and Rate Limits

List routes default to 50, maximum 100 rows. Opaque HMAC-signed cursor contains tenant, filters, last `(created_at,id)` and expiry 15 minutes; changing filters or tenant invalidates it. Stable keyset order avoids duplicate/omitted rows from offsets. Max audit query interval 7 days, expandable through successive requests; export streams metadata in bounded pages with the same scope checks.

Management: 60 requests/minute per principal, burst 10. Evaluation uses approved tenant/workload Quota. Credential/session login: 5 failures/minute per source+principal handle; do not disclose whether the principal exists. Sync has one request/second shared runtime, configured customer interval. Return 429 and integer Retry-After; retry never grants higher quota.

## Errors

Wire envelope is `{"error":{"code":"approval_required","message":"An approved policy version is required","correlation_id":"00000000-0000-4000-8000-000000000002","retryable":false,"details":{}}}`.

| HTTP | Codes | Retry |
|---|---|---|
| 400 | invalid_json, invalid_cursor, unsupported_surface | after correction |
| 401 | authentication_failed, session_expired | new valid credential |
| 403 | forbidden, self_approval, residency_denied, destination_denied, provider_denied, tool_denied | no automatic retry |
| 404 | resource_not_found | no |
| 409 | revision_conflict, idempotency_conflict, approval_required, policy_revoked, floor_conflict, capability_missing | reconcile first |
| 413 | payload_too_large | smaller input |
| 422 | validation_failed, composition_conflict, inspection_incomplete | corrected policy/input |
| 428 | precondition_required | supply revision |
| 429 | quota_exceeded, queue_full | Retry-After |
| 503 | policy_unresolved, snapshot_stale, evaluator_unavailable, audit_failed, context_invalid, unsafe_transformation | only according to protocol |
| 504 | evaluation_timeout, upstream_timeout | no automatic inference replay |

Decision reasons additionally include pii_detected, secret_detected, content_denied, content_not_allowed, schema_invalid, injection_detected, image_text_detected, disabled and inspection_scope_image_text. Operational reasons use the error codes above. All codes are finite schema enums; adding a code requires contract compatibility tests.

## Streaming Contract

The guardrails HTTP API returns completed decisions, not inference token streams. LiteLLM retains SSE serialization. Connector holds all content/tool deltas until a successful output decision. Before release only content-free keepalives are allowed. On pre-header failure use normal error HTTP status. After stream headers, send `event: error` with the safe Error envelope then close; never send uninspected partial content or a successful terminal marker. Approved output is serialized in order and ends with the gateway's normal completion marker. Client cancellation cancels upstream work and clears buffers.

## Cross-References

(see data-schema.md), (see component-specs.md §ManagementApi), (see integration-protocols.md), (see admin-experience.md).
