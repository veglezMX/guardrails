# Migration Map

## Trigger Condition

Shared PostgreSQL state and a richer v2 contract evolve the existing SQLite/text-only proof.

## Scope Boundary

Keep the completed proof and its source compatibility profile runnable. Do not alter historical specs, progress or passing evidence. Migration does not imply that old policies were approved under the new workflow.

## Correspondence

| Existing object | New object | Rule |
|---|---|---|
| local-proof Tenant | Tenant legacy-local-proof | Preserve provenance; do not automatically split an old tenant into two |
| gateway-a / gateway-b Workload | Legacy workloads | Preserve identity/history; create new team-a/chat and team-b/chat through onboarding for shared-service acceptance |
| Immutable PolicyDraft/Policy | PolicyVersion candidate plus legacy source record | Preserve original canonical bytes/digest in migration archive; compile a new v2 digest; require separate approver before activation |
| Binding history | Historical Binding import | Preserve source revisions as provenance; no active v2 binding until approval and an explicit new release |
| Governance floors | Organization/tenant baseline candidates | Translate supported minimums without weakening; reject unmappable controls |
| Credentials | Revoked legacy Credential metadata | Do not migrate bearer secrets/hashes as usable v2 credentials; issue new scoped credentials |
| Management audit | Namespaced metadata-only AuditEvent import | Preserve original event ID in safe provenance and original digest; new audit chain checkpoint identifies import |
| Runtime receipts | Historical evidence only | Require fresh v2 runtime receipts; never count old snapshots as currently applied |
| Raw-content opt-in proof records | No automatic import | Leave source protected; export only under explicit separate retention authority |
| Proof text Decision | v2 text Decision at adapter boundary | Preserve meaning but add versioned provenance; unsupported v2 stages cannot be flattened into v1 text |

## Procedure

1. Run `just evolution-migrate dry-run` against a read-only SQLite backup; verify source schema version, table counts and foreign keys. Print safe mapping/conflict report.
2. Stop source management writes for the selected migration profile, take a consistent SQLite backup and compute digests. Existing proof acceptance profile remains separate.
3. Create PostgreSQL schema with migration-owner credentials, then switch all application processes to non-owner runtime roles. Test row security before import.
4. Import in one transaction per tenant, record deterministic source IDs, archive source metadata and reject unsupported values. Repeat run is idempotent; a changed source digest requires a new migration ID.
5. Validate counts, immutable bytes, history links, audit provenance and explicit absence of usable old credentials. Compile candidates; obtain normal approval and release through API/CLI.
6. Switch only the migration profile's endpoints after all new receipts and negative tenant probes pass. No dual writes.
7. Retain read-only source backup and mapping report through the next accepted phase; delete only through separately authorized retention housekeeping.

## Rollback and Coexistence

Before new v2 writes, abort and return to the untouched proof profile. After v2 writes, do not copy v2 data backward into SQLite; restore PostgreSQL's last verified backup or keep the old proof available as a separate non-authoritative profile. Versioned routes coexist on separate deployment profiles, not a silent downgrade endpoint. Gateway manifests explicitly select v1 or v2 and refuse unknown schema versions.

Future schema migrations use expand → backfill → compatible rollout → contract in a later release; never remove a column required by the previous runtime image during the same rollback window. Test backup restore and image downgrade separately.

## Cross-References

(see component-specs.md §TenantRepository), (see component-specs.md §RecoveryManager), (see data-schema.md), (see quality-gates.md).
