# Tenancy Model

## Trigger Condition

Two independently owned team gateways consume one service; tenant identity must protect management data, evaluations, quotas, credentials and telemetry.

## Scope Boundary

This is service and administrator isolation. End-user identity/resource authorization stays with the consuming application. Shared local cluster administrators are trusted; namespaces are not a hostile-administrator boundary.

## Scoped Records and Storage

All Tenant-owned models, including configurations, approval events, snapshots, evidence, quotas and audit, carry tenant_id. Workload-scoped records additionally carry workload_id. Composite foreign keys prevent cross-tenant joins. Only the organization baseline registry has reserved platform scope. Tenants consume published baseline bytes through a narrow port; they cannot query platform-private records or another tenant's policy.

PostgreSQL applies ENABLE and FORCE ROW LEVEL SECURITY. Application roles do not own tables and have neither superuser nor BYPASSRLS. Every transaction sets an authenticated tenant ID with SET LOCAL; missing context rejects access. Background jobs iterate an explicit authorized tenant scope and do not reuse a connection with stale context. Migration/backup roles are separate mounted credentials unavailable to serving processes. Test direct repository SQL with both normal and intentionally omitted filters. See [PostgreSQL row security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).

## Permission Matrix

| Operation | platform_admin | tenant_admin | author | approver | operator | auditor/viewer | workload/runtime |
|---|---|---|---|---|---|---|---|
| Provision tenant / platform baseline | yes | no | no | no | no | no | no |
| Manage scoped principals/workloads | explicit tenant support | own tenant | no | no | manifest only | read if granted | no |
| Create candidate / configuration | platform scope | infrastructure config | policy/lists/tools | no | no | read if granted | no |
| Approve/reject/revoke policy | explicit security role | only with approver role | never own approval | yes, non-author | no | read | no |
| Activate/canary/rollback | only with operator role | only with operator role | no | no | own tenant | read | no |
| Audit metadata | explicit audited support grant | only with auditor role | no | review evidence only | rollout evidence only | auditor yes; viewer no | no |
| Evaluate | no implicit privilege | no implicit privilege | no | no | no | no | workload scope only |
| Pull snapshot / receipt | no implicit privilege | no | no | no | bundle creation | no | runtime scope only |

Roles may be combined for a principal, but author and approver must be different principals for each policy. Operator is a separate permission; the approved workflow does not require a third distinct person beyond author/approver. No superuser bypass of approval. Platform emergency action may suspend a tenant or revoke a policy and is audited; it cannot activate unapproved content.

## Provisioning and Onboarding

Platform registers tenant with local residency/default quota; tenant_admin creates scoped people and workload; author submits/approver approves; operator generates connector manifest and credential; team installs connector; observe traffic verifies expected applied digest; operator runs canary and promotes within mandatory floors. If an organization minimum already requires Block, onboarding explains that Observe is unavailable for those checks rather than weakening the minimum.

## Isolation Tests

For each route and background job, test A credential + B path/body/cursor/resource ID, B context ID, B approval/evidence, reused idempotency key and forged tenant metadata. Assert no B record or existence leak. Repeat evaluation concurrency with the same correlation/request IDs in A and B to detect context/counter contamination. Saturate A while B continues admitted traffic. Test key rotation, expiry, suspended tenant, revoked runtime and connection-pool reuse. Compare safe logs/metrics for accidental content or unbounded identifiers.

## Cross-References

(see data-schema.md), (see component-specs.md §IdentityService), (see component-specs.md §TenantRepository), (see test-strategy.md).
