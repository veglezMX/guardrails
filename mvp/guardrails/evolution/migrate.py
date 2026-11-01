"""Read-only proof inspection and atomic, approval-preserving v2 migration.

The sole adapter permitted to read local-proof models. Original immutable policy
bytes are archived; their old digest is never treated as a v2 approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from guardrails import local_contract as legacy
from guardrails.transport import GovernanceFloorModel, PolicyBindingModel, PolicyModel, TenantModel, WorkloadModel
from guardrails.management.models import CredentialModel, SnapshotModel
from . import models
from .errors import EvolutionError

TABLES = ("tenants", "workloads", "credentials", "policies", "binding_history", "bindings_current", "governance_floors", "operations", "runtime_ack", "management_audit")
TARGET_TENANT = "legacy-local-proof"
_ACTION = {"allow": "allow", "log_only": "warn", "redact": "redact", "block": "block"}
_ENTITY = {"email": "email", "phone": "international_phone"}


def _read_model(model, document):
    value = models.strict_json_loads(document)
    return model.model_validate_json(models.canonical_json_bytes(value)).to_domain()


def inspect_source(source):
    path = Path(source).resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 256 * 1024 * 1024 or any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")):
        raise EvolutionError("validation_failed")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if [row[0] for row in connection.execute("SELECT version FROM schema_migrations")] != [1]:
            raise EvolutionError("validation_failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() or connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise EvolutionError("validation_failed")
        records = {table: [dict(row) for row in connection.execute('SELECT * FROM "' + table + '"')] for table in TABLES}
    except sqlite3.Error:
        raise EvolutionError("validation_failed") from None
    finally:
        connection.close()
    if hashlib.sha256(path.read_bytes()).hexdigest() != before:
        raise EvolutionError("revision_conflict")
    conflicts = set()
    if len(records["tenants"]) != 1:
        conflicts.add("exactly_one_legacy_tenant_required")
    original_tenant = records["tenants"][0]["tenant_id"] if records["tenants"] else None
    for table, model in (("tenants", TenantModel), ("workloads", WorkloadModel), ("binding_history", PolicyBindingModel), ("bindings_current", PolicyBindingModel)):
        for row in records[table]:
            try:
                item = _read_model(model, row["document"])
                if item.tenant_id != original_tenant or row["tenant_id"] != original_tenant:
                    conflicts.add("foreign_source_tenant")
                if table in ("binding_history", "bindings_current") and (item.workload_id != row["workload_id"] or item.revision != row["revision"]):
                    conflicts.add("source_identity_mismatch")
                if table == "workloads":
                    # New workload slugs cannot silently rename old identifiers.
                    models.Workload(tenant_id=TARGET_TENANT, workload_id=item.workload_id, display_name=item.display_name,
                                    mode="remote", allowed_stages=("input", "output"), quota_id="00000000-0000-4000-8000-000000000001")
            except (ValueError, TypeError, ValidationError, EvolutionError):
                conflicts.add("invalid_source_record")
    policy_keys = set()
    for row in records["policies"]:
        try:
            policy = _read_model(PolicyModel, row["document"])
            if policy.retain_content:
                conflicts.add("raw_retention_requires_separate_authority")
            if (policy.digest != row["digest"] or policy.publication_ordinal != row["ordinal"]
                or policy.policy_id != row["policy_id"] or policy.version != row["version"]
                or policy.tenant_id != original_tenant or row["tenant_id"] != original_tenant):
                conflicts.add("source_policy_identity_or_digest_mismatch")
            if policy.policy_id.startswith("migration-floor-"):
                conflicts.add("reserved_migration_policy_name")
            policy_keys.add((row["policy_id"], row["version"]))
            for check in policy.checks:
                if check.evaluator != "local_pii" or set(check.pii_types) - _ENTITY.keys():
                    conflicts.add("unmappable_check")
        except (ValueError, TypeError, ValidationError, EvolutionError):
            conflicts.add("invalid_policy_or_digest")
    for row in records["binding_history"]:
        value = models.strict_json_loads(row["document"])
        if (value.get("policy_id"), value.get("policy_version")) not in policy_keys:
            conflicts.add("unresolved_historical_binding")
    for row in records["governance_floors"]:
        try:
            floor = _read_model(GovernanceFloorModel, row["document"])
            if floor.scope != row["scope"] or (floor.scope == "tenant" and floor.tenant_id != original_tenant):
                conflicts.add("foreign_source_floor")
            if floor.required_policy_id or floor.minimum_policy_ordinals:
                conflicts.add("legacy_named_floor_requires_explicit_conversion")
            if set(floor.required_pii_types) - _ENTITY.keys():
                conflicts.add("unmappable_floor")
            if bool(floor.required_pii_types) != bool(floor.required_stages):
                conflicts.add("unmappable_floor")
        except (ValueError, TypeError, ValidationError, EvolutionError):
            conflicts.add("invalid_source_floor")
    for row in records["management_audit"]:
        try:
            event = models.strict_json_loads(row["document"])
            if event.get("content") is not None:
                conflicts.add("raw_retention_requires_separate_authority")
            if str(UUID(row["event_id"])) != row["event_id"] or event.get("event_id") != row["event_id"]:
                conflicts.add("invalid_source_audit")
            if row["tenant_id"] not in (None, original_tenant):
                conflicts.add("foreign_source_audit")
        except (ValueError, TypeError, EvolutionError):
            conflicts.add("invalid_source_audit")
    for row in records["credentials"]:
        try:
            credential = _read_model(CredentialModel, row["document"])
            if row["tenant_id"] not in (None, original_tenant):
                conflicts.add("foreign_source_credential")
            if (str(credential.credential_id) != row["credential_id"] or credential.role != row["role"]
                or credential.tenant_id != row["tenant_id"] or credential.workload_id != row["workload_id"]
                or credential.token_sha256 != row["token_sha256"]):
                conflicts.add("source_credential_identity_mismatch")
        except (ValueError, TypeError, ValidationError, EvolutionError):
            conflicts.add("invalid_source_credential")
    generations = {}
    for row in records["operations"]:
        try:
            snapshot = _read_model(SnapshotModel, row["snapshot"])
            if snapshot.digest != row["snapshot_digest"] or snapshot.generation != row["desired_generation"]:
                conflicts.add("source_snapshot_digest_mismatch")
            if any(workload.tenant_id != original_tenant for workload in snapshot.workloads):
                conflicts.add("foreign_source_snapshot")
            generations[row["desired_generation"]] = row["snapshot_digest"]
        except (ValueError, TypeError, ValidationError, EvolutionError):
            conflicts.add("invalid_source_snapshot")
    for row in records["runtime_ack"]:
        if generations.get(row["generation"]) != row["digest"]:
            conflicts.add("unresolved_source_receipt")
    report = {"source_digest": before, "schema_version": 1, "counts": {table: len(rows) for table, rows in records.items()},
              "target_tenant": TARGET_TENANT, "conflicts": sorted(conflicts), "usable_credentials_imported": 0,
              "active_bindings_imported": 0, "candidate_approval_required": True,
              "conversion": {"log_only": "warn", "failure_behavior": "block", "workloads": "suspended", "floors": "mandatory_candidate_rules"}}
    return report, records


def dry_run(source):
    return inspect_source(source)[0]


def _floor_rules(floor):
    if not floor.required_pii_types:
        return ()
    return (models.Rule(rule_id="migration-floor-" + floor.scope, kind="pii", stages=floor.required_stages,
            action=_ACTION[floor.minimum_action], mandatory=True, minimum_state=floor.minimum_state,
            config={"entities": tuple(_ENTITY[entity] for entity in floor.required_pii_types)}, evaluator_revision="pii-v2"),)


def apply(source, repository, *, author_id):
    from .audit import AuditStore
    from .compiler import PolicyCompiler
    report, rows = inspect_source(source)
    if report["conflicts"]:
        raise EvolutionError("composition_conflict")
    digest = report["source_digest"]
    make_id = lambda name: str(uuid5(NAMESPACE_URL, "guardrails-migration:" + digest + ":" + name))
    created = models.utc_now()
    tenant_id = TARGET_TENANT
    dump = lambda item: item.model_dump(mode="json")
    author = models.Principal(principal_id=author_id, tenant_id=tenant_id, roles=("author",), status="active", display_name="Migration author", created_at=created)
    quota = models.with_resource_digest(models.Quota(quota_id=make_id("quota"), tenant_id=tenant_id, version=1, workload_id=None, created_at=created))
    residency = models.with_resource_digest(models.ResidencyProfile(residency_id=make_id("residency"), tenant_id=tenant_id, version=1, allowed_zones=("local-shared",), created_at=created))
    floors = [_read_model(GovernanceFloorModel, row["document"]) for row in rows["governance_floors"]]
    floor_rules = tuple(rule for floor in floors for rule in _floor_rules(floor))
    with repository.transaction(tenant_id) as tx:
        tx.lock("migration")
        existing = tx.get("migrations", digest)
        if existing:
            return existing
        if tx.get("tenants", tenant_id):
            raise EvolutionError("revision_conflict")
        tenant = models.Tenant(tenant_id=tenant_id, display_name="Legacy local proof", status="active", baseline=None,
                               residency_id=residency.residency_id, quota_id=quota.quota_id, created_at=created)
        tx.insert("tenants", tenant_id, dump(tenant))
        tx.insert("config:quotas", quota.quota_id + ":1", dump(quota), immutable=True)
        tx.insert("config:residency", residency.residency_id + ":1", dump(residency), immutable=True)
        tx.insert("principals", author_id, dump(author))
        for row in rows["workloads"]:
            old = _read_model(WorkloadModel, row["document"])
            workload = models.Workload(tenant_id=tenant_id, workload_id=old.workload_id, display_name=old.display_name,
                mode="remote", status="suspended", allowed_stages=("input", "output"), provider_ids=(), quota_id=quota.quota_id,
                required_state="observe", created_at=created)
            tx.insert("workloads", old.workload_id, dump(workload))
        imported, baseline_candidates = [], []
        def save_candidate(policy_id, version, rules):
            candidate = PolicyCompiler().compile(models.PolicyVersion(tenant_id=tenant_id, policy_id=policy_id, version=version,
                author_id=author_id, rules=rules, residency=residency, quota=quota, failure_behavior="block", created_at=created))
            key = f"{policy_id}:{version}"
            tx.insert("policies", key, dump(candidate), immutable=True)
            tx.insert("policy_status", key, {"status": "candidate", "revision": 1, "created_at": created})
            for kind, dep in (("config:residency", residency.residency_id), ("config:quotas", quota.quota_id)):
                tx.link("policies", key, kind, dep + ":1")
            return key
        for floor in floors:
            key = save_candidate("migration-floor-" + floor.scope, floor.revision, _floor_rules(floor))
            baseline_candidates.append(key)
            tx.insert("legacy_floors", floor.scope, {"source_scope": floor.scope, "source_revision": floor.revision,
                "original_canonical_bytes": legacy.canonical_bytes(floor).decode(), "candidate_key": key,
                "source_digest": hashlib.sha256(legacy.canonical_bytes(floor)).hexdigest(), "created_at": created}, immutable=True)
        for row in rows["policies"]:
            old = _read_model(PolicyModel, row["document"])
            tx.insert("legacy_policies", old.policy_id + ":" + old.version, {"source_digest": old.digest,
                "original_canonical_bytes": legacy.canonical_bytes(old).decode(), "source_ordinal": old.publication_ordinal,
                "created_at": created}, immutable=True)
            rules = [models.Rule(rule_id=check.check_id, kind="pii", stages=check.stages,
                action=_ACTION[check.requested_action], mandatory=True, minimum_state="observe",
                config={"entities": tuple(_ENTITY[item] for item in check.pii_types)}, evaluator_revision="pii-v2") for check in old.checks]
            if set(rule.rule_id for rule in rules) & set(rule.rule_id for rule in floor_rules):
                raise EvolutionError("composition_conflict")
            imported.append(save_candidate(old.policy_id, old.publication_ordinal, tuple(rules) + floor_rules))
        for row in rows["credentials"]:
            tx.insert("legacy_credentials", row["credential_id"], {"credential_id": row["credential_id"], "revoked": True,
                "source_role": row["role"], "source_digest": digest, "created_at": created}, immutable=True)
        for row in rows["binding_history"]:
            old = _read_model(PolicyBindingModel, row["document"])
            key = f"{old.workload_id}:{old.revision}"
            tx.insert("legacy_bindings", key, {"workload_id": old.workload_id, "source_revision": old.revision,
                "source_document": old.to_wire(), "currently_applied": False, "created_at": created}, immutable=True)
            tx.link("legacy_bindings", key, "workloads", old.workload_id)
            tx.link("legacy_bindings", key, "legacy_policies", old.policy_id + ":" + old.policy_version)
        for row in rows["runtime_ack"]:
            tx.insert("legacy_receipts", str(row["generation"]), {"source_generation": row["generation"], "source_digest": row["digest"],
                "acknowledged_at": row["acknowledged_at"], "currently_applied": False, "created_at": created}, immutable=True)
        for row in rows["operations"]:
            tx.insert("legacy_operations", row["operation_id"], {"source_operation_id": row["operation_id"],
                "source_generation": row["desired_generation"], "source_snapshot_digest": row["snapshot_digest"],
                "currently_applied": False, "created_at": created}, immutable=True)
        audit = AuditStore(repository)
        for row in rows["management_audit"]:
            event_id = make_id("audit:" + row["event_id"])
            tx.insert("legacy_audit", row["event_id"], {"source_event_id": row["event_id"], "target_event_id": event_id,
                "source_digest": hashlib.sha256(models.canonical_json_bytes(models.strict_json_loads(row["document"]))).hexdigest(),
                "occurred_at": row["occurred_at"], "created_at": created}, immutable=True)
            audit.append(tx, "migration_imported", event_id=event_id, actor_id=author_id,
                         metadata={"operation": "legacy_audit_import", "resource_id": row["event_id"]})
            tx.link("legacy_audit", row["event_id"], "audit", event_id)
        checkpoint = audit.append(tx, "migration_imported", event_id=make_id("import-checkpoint"), actor_id=author_id,
                                  metadata={"operation": "legacy_import_complete", "event_count": len(rows["management_audit"])})
        result = {**report, "migration_id": make_id("migration"), "imported_candidates": imported,
                  "baseline_candidates": baseline_candidates, "audit_checkpoint": {"event_id": checkpoint["event_id"], "event_hash": checkpoint["event_hash"]}, "created_at": created}
        tx.insert("migrations", digest, result, immutable=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("dry-run", "apply"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--database-url-file")
    parser.add_argument("--author-id")
    args = parser.parse_args(argv)
    try:
        if args.operation == "dry-run":
            result = dry_run(args.source)
        else:
            if not args.database_url_file or not args.author_id:
                raise EvolutionError("validation_failed")
            from .storage import TenantRepository
            dsn = Path(args.database_url_file)
            if not dsn.is_file() or dsn.stat().st_mode & 0o077:
                raise EvolutionError("validation_failed")
            repository = TenantRepository(dsn.read_text().strip())
            try:
                result = apply(args.source, repository, author_id=args.author_id)
            finally:
                repository.close()
        print(json.dumps(result, sort_keys=True))
        return 1 if result.get("conflicts") else 0
    except (EvolutionError, OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        print(json.dumps({"error": "migration_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
