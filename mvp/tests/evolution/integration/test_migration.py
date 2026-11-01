"""Actual read-only SQLite to PostgreSQL migration, including rollback."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from guardrails import local_contract as old
from guardrails.evolution import migrate
from guardrails.evolution.audit import AuditStore
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.storage import TenantRepository, migrate as migrate_schema
from guardrails.storage.control import ControlRepository


@pytest.fixture
def migration_repository():
    app_file, owner_file = os.environ.get("EVOLUTION_TEST_DSN_FILE"), os.environ.get("EVOLUTION_TEST_OWNER_DSN_FILE")
    if not app_file or not owner_file:
        pytest.fail("Real PostgreSQL18 owner and nonowner DSN files are required")
    owner = create_engine(Path(owner_file).read_text().strip(), isolation_level="AUTOCOMMIT", hide_parameters=True)
    name = "evolution_import_" + uuid4().hex[:12]
    with owner.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    owner_url = owner.url.set(database=name).render_as_string(hide_password=False)
    migrate_schema(owner_url)
    admin = create_engine(owner_url, hide_parameters=True)
    app_url = make_url(Path(app_file).read_text().strip()).set(database=name)
    with admin.begin() as conn:
        role = app_url.username
        from psycopg import sql
        conn.connection.driver_connection.execute(sql.SQL("GRANT USAGE ON SCHEMA evolution TO {}").format(sql.Identifier(role)))
        conn.connection.driver_connection.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA evolution TO {}").format(sql.Identifier(role)))
        conn.connection.driver_connection.execute(sql.SQL("GRANT EXECUTE ON FUNCTION evolution.tenant_scope() TO {}").format(sql.Identifier(role)))
    admin.dispose()
    repo = TenantRepository(app_url.render_as_string(hide_password=False))
    yield repo
    repo.close()
    with owner.connect() as conn:
        conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
    owner.dispose()


def source_fixture(path, *, bad_digest=False, raw=False, named_floor=False):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    actor = uuid4()
    tenant = old.Tenant(tenant_id="local-proof", display_name="Legacy", created_at=now)
    workload = old.Workload(tenant_id="local-proof", workload_id="gateway-a", display_name="Gateway A", created_at=now, credential_revision=1, enforcement_posture="cooperative")
    behavior = old.FailureBehavior(**{key: "block" for key in ("evaluation_timeout", "evaluation_unavailable", "policy_unresolved", "management_unavailable", "audit_failure", "transformation_failure")})
    draft = old.PolicyDraft(tenant_id="local-proof", policy_id="pii", version="v1",
        checks=(old.CheckSpec(check_id="pii", evaluator="local_pii", stages=("input", "output"), pii_types=("email", "phone"), requested_action="redact"),),
        failure_behavior=behavior, retain_content=raw)
    policy = old.Policy(**{key: getattr(draft, key) for key in draft.__dataclass_fields__}, publication_ordinal=1,
                        published_at=now, published_by=actor, digest=draft.content_digest())
    binding = old.PolicyBinding(tenant_id="local-proof", workload_id="gateway-a", policy_id="pii", policy_version="v1", enforcement_state="observe",
        revision=1, generation=1, input_timeout_ms=750, output_timeout_ms=750, updated_at=now, updated_by=actor,
        reason="initial_binding", evidence_kind=None, fixture_run_id=None)
    floor = old.GovernanceFloor(scope="organization", tenant_id=None, revision=1, require_binding=True,
        required_stages=("input", "output"), required_pii_types=("email",), minimum_action="block", minimum_state="observe",
        required_policy_id="pii" if named_floor else None, minimum_policy_ordinals={}, require_fail_closed=("audit_failure",), forbid_content_retention=True)
    credential = old.Credential(credential_id=uuid4(), role="workload", tenant_id="local-proof", workload_id="gateway-a",
        token_sha256=hashlib.sha256(b"SYNTHETIC-LEGACY-TOKEN").hexdigest(), revision=1, created_at=now, revoked_at=None, service_id=None, capabilities=())
    event = {"event_id": str(uuid4()), "source": "management", "occurred_at": "2026-09-01T00:00:00Z", "tenant_id": "local-proof",
             "workload_id": "gateway-a", "actor_id": str(actor), "event_type": "policy_published", "correlation_id": None,
             "metadata": {"policy_id": "pii", "policy_digest": policy.digest}, "content": None}
    encode = lambda value: old.canonical_bytes(value).decode()
    with sqlite3.connect(path) as conn:
        conn.executescript(Path("mvp/guardrails/storage/schema.sql").read_text())
        conn.execute("INSERT INTO tenants VALUES (?,?)", (tenant.tenant_id, encode(tenant)))
        conn.execute("INSERT INTO workloads VALUES (?,?,?)", (workload.tenant_id, workload.workload_id, encode(workload)))
        conn.execute("INSERT INTO credentials VALUES (?,?,?,?,?,?,?)", (str(credential.credential_id), credential.token_sha256, credential.role, credential.tenant_id, credential.workload_id, None, encode(credential)))
        conn.execute("INSERT INTO policies VALUES (?,?,?,?,?,?,?)", (policy.tenant_id, policy.policy_id, policy.version, policy.publication_ordinal,
            "0" * 64 if bad_digest else policy.digest, encode(policy), str(uuid4())))
        conn.execute("INSERT INTO binding_history VALUES (?,?,?,?)", (binding.tenant_id, binding.workload_id, binding.revision, encode(binding)))
        conn.execute("INSERT INTO bindings_current VALUES (?,?,?,?,?,?)", (binding.tenant_id, binding.workload_id, binding.revision, binding.policy_id, binding.policy_version, encode(binding)))
        conn.execute("INSERT INTO governance_floors VALUES (?,?,?)", (floor.scope, "organization", encode(floor)))
        conn.execute("INSERT INTO management_audit VALUES (?,?,?,?,?,?)", (event["event_id"], event["occurred_at"], event["tenant_id"], event["workload_id"], None, encode(event)))
        snapshot = ControlRepository.build_snapshot(conn, 1, "2026-09-01T00:00:00Z")
        conn.execute("INSERT INTO operations VALUES (?,?,?,?,?,?,?,?)", (str(uuid4()), 1, str(actor), tenant.tenant_id, "2026-09-01T00:00:00Z", "2026-09-01T00:00:01Z", snapshot["digest"], encode(snapshot)))
        conn.execute("INSERT INTO runtime_ack VALUES (?,?,?,?,?)", (1, 1, snapshot["digest"], "2026-09-01T00:00:01Z", "proof-runtime"))
    path.chmod(0o400)
    return policy, credential, event


def test_dry_run_is_read_only_and_cli_report_contains_no_credentials(tmp_path):
    path = tmp_path / "source.sqlite3"
    policy, credential, event = source_fixture(path)
    before, stat = path.read_bytes(), path.stat()
    report = migrate.dry_run(path)
    assert report["conflicts"] == []
    assert report["counts"]["policies"] == 1 and report["counts"]["governance_floors"] == 1
    assert report["usable_credentials_imported"] == 0 and report["active_bindings_imported"] == 0
    assert path.read_bytes() == before and path.stat().st_mtime_ns == stat.st_mtime_ns
    result = subprocess.run([sys.executable, "-m", "guardrails.evolution.migrate", "dry-run", "--source", str(path)], capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)["source_digest"] == hashlib.sha256(before).hexdigest()
    assert credential.token_sha256 not in result.stdout and policy.digest not in result.stdout
    assert path.read_bytes() == before


@pytest.mark.parametrize("change,conflict", [("bad_digest", "source_policy_identity_or_digest_mismatch"), ("raw", "raw_retention_requires_separate_authority"), ("named_floor", "legacy_named_floor_requires_explicit_conversion")])
def test_unmappable_or_corrupt_source_rejected_before_any_import(tmp_path, migration_repository, change, conflict):
    path = tmp_path / "source.sqlite3"
    source_fixture(path, **{change: True})
    report = migrate.dry_run(path)
    assert conflict in report["conflicts"]
    with pytest.raises(EvolutionError, match="Composition conflict"):
        migrate.apply(path, migration_repository, author_id=str(uuid4()))
    with migration_repository.transaction(migrate.TARGET_TENANT) as tx:
        assert tx.get("tenants", migrate.TARGET_TENANT) is None
        assert tx.list("policies") == []
        assert tx.list("audit") == []


def test_import_preserves_bytes_floors_history_and_never_approves(tmp_path, migration_repository):
    path = tmp_path / "source.sqlite3"
    policy, credential, event = source_fixture(path)
    source_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    author_id = str(uuid4())
    result = migrate.apply(path, migration_repository, author_id=author_id)
    again = migrate.apply(path, migration_repository, author_id=author_id)
    assert result == again
    assert result["baseline_candidates"] == ["migration-floor-organization:1"]
    with migration_repository.transaction(migrate.TARGET_TENANT) as tx:
        imported = tx.get("policies", "pii:1")
        assert imported["status"] == "candidate"
        assert imported["compiled_digest"] != policy.digest
        assert len(imported["rules"]) == 2
        floor = next(rule for rule in imported["rules"] if rule["rule_id"] == "migration-floor-organization")
        assert floor["mandatory"] is True and floor["action"] == "block" and floor["minimum_state"] == "observe"
        assert tx.get("legacy_policies", "pii:v1")["original_canonical_bytes"] == old.canonical_bytes(policy).decode()
        assert len(tx.list("legacy_bindings")) == 1 and tx.list("legacy_bindings")[0]["currently_applied"] is False
        assert len(tx.list("legacy_receipts")) == 1 and tx.list("legacy_receipts")[0]["currently_applied"] is False
        assert len(tx.list("legacy_operations")) == 1
        assert tx.get("workloads", "gateway-a")["status"] == "suspended"
        assert tx.list("bindings") == [] and tx.list("binding_pointers") == [] and tx.list("approvals") == []
        assert tx.list("credentials") == []
        serialized = json.dumps([row.data for row in tx.execute("SELECT data FROM evolution.records")])
        assert credential.token_sha256 not in serialized and "SYNTHETIC-LEGACY-TOKEN" not in serialized
        assert tx.get("legacy_credentials", str(credential.credential_id))["revoked"] is True
        archive = tx.get("legacy_audit", event["event_id"])
        assert archive["source_digest"] == hashlib.sha256(old.canonical_bytes(event)).hexdigest()
        assert len(tx.list("audit")) == 2
        assert tx.get("audit", result["audit_checkpoint"]["event_id"])["event_hash"] == result["audit_checkpoint"]["event_hash"]
    assert AuditStore(migration_repository).verify_chain(migrate.TARGET_TENANT)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source_digest


def test_import_fault_rolls_back_all_tenant_state(tmp_path, migration_repository):
    path = tmp_path / "source.sqlite3"
    source_fixture(path)
    # Inject a genuine persistence fault after tenant/defaults/principal writes.
    from guardrails.evolution.storage import TenantTransaction
    insert = TenantTransaction.insert
    def fail_on_policy(self, kind, *args, **kwargs):
        if kind == "policies":
            raise EvolutionError("audit_failed")
        return insert(self, kind, *args, **kwargs)
    from unittest.mock import patch
    with patch.object(TenantTransaction, "insert", fail_on_policy):
        with pytest.raises(EvolutionError, match="Audit failed"):
            migrate.apply(path, migration_repository, author_id=str(uuid4()))
    with migration_repository.transaction(migrate.TARGET_TENANT) as tx:
        assert tx.get("tenants", migrate.TARGET_TENANT) is None
        assert tx.list("principals") == []
        assert tx.list("config:quotas") == []
        assert tx.list("migrations") == []
        assert tx.list("audit") == []


def test_snapshot_digest_and_credential_metadata_are_validated(tmp_path):
    source = tmp_path / "source.sqlite3"
    source_fixture(source)
    source.chmod(0o600)
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE operations SET snapshot_digest=?", ("f" * 64,))
        conn.execute("UPDATE credentials SET role='unknown-role'")
    conflicts = migrate.dry_run(source)["conflicts"]
    assert "source_snapshot_digest_mismatch" in conflicts
    assert "source_credential_identity_mismatch" in conflicts
    assert "unresolved_source_receipt" in conflicts
