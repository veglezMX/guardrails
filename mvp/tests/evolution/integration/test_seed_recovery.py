"""Interrupt actual PostgreSQL bootstrap and HTTPS one-time credential delivery."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from evolution import seed
from guardrails.evolution import models
from guardrails.evolution.audit import AuditStore
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.evidence_trust import EvidenceTrust
from guardrails.evolution.identity import IdentityService
from guardrails.evolution.management_api import ManagementApi, create_app
from guardrails.evolution.storage import TenantRepository
from https_support import serve_https


class Interrupted(BaseException):
    pass


@pytest.fixture
def setup_seed(tmp_path):
    dsn = Path(os.environ["EVOLUTION_TEST_DSN_FILE"]).read_bytes()
    seed.private_write(tmp_path / "database.url", dsn)
    evidence = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    seed.private_write(tmp_path / "evidence.pub", evidence)
    spec = seed.SeedSpec.model_validate(
        {
            "database_url_file": str(tmp_path / "database.url"),
            "management_url": "https://localhost:8443",
            "ca_file": str(tmp_path / "ca.crt"),
            "evidence_public_key_file": str(tmp_path / "evidence.pub"),
            "evidence_key_id": "recover-" + uuid4().hex,
            "mock": {
                "host": "mock.example.test",
                "addresses": ["10.10.0.1"],
                "path_prefix": "/v1",
                "model_id": "mock",
            },
            "ollama": {
                "host": "ollama.example.test",
                "addresses": ["10.10.0.2"],
                "path_prefix": "/api",
                "model_id": "local",
            },
        }
    )
    repository = TenantRepository(dsn.decode().strip())
    try:
        yield spec, tmp_path / "seed", repository
    finally:
        repository.close()


def snapshot_rows(repository, principal):
    with repository.transaction("_platform") as tx:
        credentials = [
            row["data"]
            for row in tx.page("credentials", filters={"principal_id": principal})
        ]
        audit = [
            row["data"] for row in tx.page("audit", filters={"actor_id": principal})
        ]
        return credentials, audit, tx.get("deployment_bootstrap", principal)


@pytest.mark.parametrize(
    "point",
    [
        "private_key",
        "public_key",
        "plan",
        "token",
        "transaction",
        "database_commit",
        "evidence_commit",
        "completed_journal",
    ],
)
def test_bootstrap_recovers_exact_keys_identity_and_audit_after_interruption(
    setup_seed, monkeypatch, point
):
    spec, output, repository = setup_seed
    real_write, real_append, real_provision = (
        seed.private_write,
        AuditStore.append,
        EvidenceTrust.provision,
    )
    seen = {}
    names = {
        "private_key": "snapshot-signing.key",
        "public_key": "snapshot-signing.pub",
        "plan": "bootstrap-plan.json",
        "token": "platform.token",
        "completed_journal": "bootstrap.json",
    }

    def write(path, value, **kwargs):
        real_write(path, value, **kwargs)
        if Path(path).name == names.get(point):
            seen[Path(path).name] = Path(path).read_bytes()
            raise Interrupted()

    def append(self, tx, *args, **kwargs):
        result = real_append(self, tx, *args, **kwargs)
        if (
            point == "transaction"
            and kwargs.get("metadata", {}).get("operation") == "deployment_bootstrap"
        ):
            raise Interrupted()
        return result

    def provision(self, *args, **kwargs):
        if point == "database_commit":
            raise Interrupted()
        result = real_provision(self, *args, **kwargs)
        if point == "evidence_commit":
            raise Interrupted()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(seed, "private_write", write)
        patch.setattr(AuditStore, "append", append)
        patch.setattr(EvidenceTrust, "provision", provision)
        with pytest.raises(Interrupted):
            seed.bootstrap(spec, output)
    durable = {
        p.name: p.read_bytes()
        for p in output.iterdir()
        if p.name
        in {
            "snapshot-signing.key",
            "snapshot-signing.pub",
            "platform.token",
            "bootstrap-plan.json",
        }
    }
    result = seed.bootstrap(spec, output)
    assert seed.bootstrap(spec, output) == result
    assert all((output / name).read_bytes() == value for name, value in durable.items())
    assert all((output / name).read_bytes() == value for name, value in seen.items())
    actor = IdentityService(repository).authenticate(
        seed.private_read(result["platform_token_file"]).decode()
    )
    assert actor.principal_id == result["platform_principal_id"]
    assert actor.roles == ("platform_admin",)
    credentials, audit, marker = snapshot_rows(repository, actor.principal_id)
    assert len(credentials) == 1
    assert len(audit) == 2
    assert {row["event_type"] for row in audit} == {
        "principal_created",
        "configuration_created",
    }
    assert marker["credential_id"] == credentials[0]["credential_id"]
    assert (
        credentials[0]["token_hash"]
        == hashlib.sha256(seed.private_read(output / "platform.token")).hexdigest()
    )
    journals = b"".join(p.read_bytes() for p in output.glob("*.json"))
    assert seed.private_read(output / "platform.token") not in journals
    assert b"one_time_secret" not in journals and b"token_hash" not in journals
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir())


def test_bootstrap_concurrent_replay_creates_one_database_identity(setup_seed):
    spec, output, repository = setup_seed
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: seed.bootstrap(spec, output), range(2)))
    assert results[0] == results[1]
    credentials, audit, marker = snapshot_rows(
        repository, results[0]["platform_principal_id"]
    )
    assert len(credentials) == 1 and len(audit) == 2
    assert marker["credential_id"] == credentials[0]["credential_id"]


@pytest.mark.parametrize(
    "tamper",
    [
        "private_key",
        "public_key",
        "token",
        "plan",
        "completed_path",
        "revoked_principal",
        "revoked_credential",
        "revoked_evidence",
        "missing_token",
        "missing_plan",
        "insecure_token",
        "symlink_token",
    ],
)
def test_bootstrap_rejects_inconsistent_private_files_and_database_without_rotation(
    setup_seed, tamper
):
    spec, output, repository = setup_seed
    result = seed.bootstrap(spec, output)
    principal = result["platform_principal_id"]
    credentials, audit, _ = snapshot_rows(repository, principal)
    if tamper in {"private_key", "public_key"}:
        path = output / (
            "snapshot-signing.key"
            if tamper == "private_key"
            else "snapshot-signing.pub"
        )
        path.write_bytes(b"x" * 32)
    elif tamper == "token":
        path = output / "platform.token"
        value = path.read_text()
        path.write_text(value[:-1] + ("A" if value[-1] != "A" else "B"))
    elif tamper in {"plan", "completed_path"}:
        path = output / (
            "bootstrap-plan.json" if tamper == "plan" else "bootstrap.json"
        )
        value = models.strict_json_loads(seed.private_read(path))
        value["principal_id" if tamper == "plan" else "platform_token_file"] = (
            str(uuid4()) if tamper == "plan" else str(output / "other.token")
        )
        seed.private_write(path, value, replace=True)
    elif tamper == "missing_token":
        (output / "platform.token").unlink()
    elif tamper == "missing_plan":
        (output / "bootstrap-plan.json").unlink()
    elif tamper == "insecure_token":
        (output / "platform.token").chmod(0o644)
    elif tamper == "symlink_token":
        path = output / "platform.token"
        target = output / "saved.token"
        path.rename(target)
        path.symlink_to(target)
    else:
        kind, key, field = {
            "revoked_principal": ("principals", principal, "status"),
            "revoked_credential": (
                "credentials",
                credentials[0]["credential_id"],
                "revoked_at",
            ),
            "revoked_evidence": (
                "evidence_trust_roots",
                spec.evidence_key_id,
                "status",
            ),
        }[tamper]
        with repository.transaction("_platform") as tx:
            record = tx.get(kind, key, required=True)
            tx.put(
                kind,
                key,
                {
                    **record,
                    field: models.utc_now() if field == "revoked_at" else "revoked",
                },
                expected_revision=record["revision"],
            )
    before = {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()}
    with pytest.raises(EvolutionError) as failure:
        seed.bootstrap(spec, output)
    assert failure.value.code in {
        "validation_failed",
        "idempotency_conflict",
        "authentication_failed",
    }
    assert {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()} == before
    after_credentials, after_audit, _ = snapshot_rows(repository, principal)
    assert len(after_credentials) == len(credentials) == 1
    assert after_audit == audit


@pytest.fixture
def issued_system(setup_seed, tmp_path):
    spec, output, repository = setup_seed

    def factory():
        return create_app(
            service=ManagementApi(
                TenantRepository(Path(spec.database_url_file).read_text().strip())
            )
        )

    with serve_https(factory, tmp_path / "tls") as server:
        spec = spec.model_copy(
            update={"management_url": server["url"], "ca_file": server["ca_file"]}
        )
        initial = seed.bootstrap(spec, output)
        client = seed.SeedClient(spec, output, initial)
        tenant = "recovery-" + uuid4().hex[:16]
        client.post(
            "tenant",
            "/v2/tenants",
            {"tenant_id": tenant, "display_name": tenant},
            initial["platform_token_file"],
        )
        principal = client.post(
            "principal",
            f"/v2/tenants/{tenant}/principals",
            {"display_name": "Admin", "roles": ["tenant_admin"]},
            initial["platform_token_file"],
        )
        yield (
            spec,
            output,
            repository,
            client,
            initial,
            tenant,
            principal["principal_id"],
        )
        client.close()


def issue(client, initial, tenant, principal):
    return client.post(
        "credential",
        f"/v2/tenants/{tenant}/principals/{principal}/credentials",
        {"expires_at": initial["credential_expires_at"]},
        initial["platform_token_file"],
        secret_name="admin.token",
    )


def test_secret_delivery_journal_failure_recovers_from_exact_https_receipt(
    issued_system, monkeypatch
):
    spec, output, repository, client, initial, tenant, principal = issued_system
    original = client.save

    def save():
        if "result" in client.operations.get("credential", {}):
            raise Interrupted()
        original()

    with monkeypatch.context() as patch:
        patch.setattr(client, "save", save)
        with pytest.raises(Interrupted):
            issue(client, initial, tenant, principal)
    token = seed.private_read(output / "admin.token")
    assert (
        "result"
        not in models.strict_json_loads(seed.private_read(output / "operations.json"))[
            "credential"
        ]
    )
    with repository.transaction(tenant) as tx:
        before = {
            kind: tx.list(kind) for kind in ("credentials", "audit", "idempotency")
        }
    resumed = seed.SeedClient(spec, output, initial)

    def forbidden_request(*args, **kwargs):
        raise AssertionError("Recovery must not redeliver the HTTP credential request")

    monkeypatch.setattr(resumed, "request", forbidden_request)
    result = issue(resumed, initial, tenant, principal)
    assert issue(resumed, initial, tenant, principal) == result
    assert seed.private_read(output / "admin.token") == token
    assert (
        IdentityService(repository).authenticate(token.decode()).principal_id
        == principal
    )
    with repository.transaction(tenant) as tx:
        assert {kind: tx.list(kind) for kind in before} == before
    assert token not in seed.private_read(output / "operations.json")
    assert b"one_time_secret" not in seed.private_read(output / "operations.json")


@pytest.mark.parametrize(
    "mismatch", ["token", "receipt", "principal", "symlink", "permissions"]
)
def test_secret_recovery_refuses_unrelated_or_insecure_material(
    issued_system, monkeypatch, mismatch
):
    spec, output, repository, client, initial, tenant, principal = issued_system
    result = issue(client, initial, tenant, principal)
    journal = models.strict_json_loads(seed.private_read(output / "operations.json"))
    del journal["credential"]["result"]
    seed.private_write(output / "operations.json", journal, replace=True)
    if mismatch == "token":
        seed.private_write(
            output / "admin.token",
            seed.private_read(output / "platform.token"),
            replace=True,
        )
    elif mismatch == "receipt":
        with repository.transaction(tenant) as tx:
            row = next(
                row
                for row in tx.list("idempotency")
                if row.get("response", {}).get("credential_id")
                == result["credential_id"]
            )
            # The persisted request receipt is the required link to this operation.
            tx.execute(
                "UPDATE evolution.records SET data=jsonb_set(data,'{fingerprint}',to_jsonb(CAST(:value AS text))) WHERE tenant_id=:tenant AND kind='idempotency' AND data->'response'->>'credential_id'=:credential",
                {
                    "value": "0" * 64,
                    "tenant": tenant,
                    "credential": result["credential_id"],
                },
            )
            assert row["secret_delivered"] is True
    elif mismatch == "principal":
        with repository.transaction(tenant) as tx:
            row = tx.get("principals", principal, required=True)
            tx.put(
                "principals",
                principal,
                {**row, "status": "revoked"},
                expected_revision=row["revision"],
            )
    elif mismatch == "symlink":
        path = output / "admin.token"
        path.rename(output / "saved.token")
        path.symlink_to(output / "saved.token")
    else:
        (output / "admin.token").chmod(0o644)
    with repository.transaction(tenant) as tx:
        credentials = tx.list("credentials")
    resumed = seed.SeedClient(spec, output, initial)
    monkeypatch.setattr(
        resumed,
        "request",
        lambda *args, **kwargs: pytest.fail("Must not request replacement credential"),
    )
    with pytest.raises(EvolutionError) as error:
        issue(resumed, initial, tenant, principal)
    assert error.value.code in {
        "validation_failed",
        "idempotency_conflict",
        "authentication_failed",
    }
    with repository.transaction(tenant) as tx:
        assert tx.list("credentials") == credentials
    assert (
        "result"
        not in models.strict_json_loads(seed.private_read(output / "operations.json"))[
            "credential"
        ]
    )


def test_empty_reservation_after_process_death_resumes_same_request(issued_system):
    import subprocess
    import sys

    spec, output, repository, _client, initial, tenant, principal = issued_system
    spec_path = output / "child-spec.json"
    seed.private_write(spec_path, spec)
    child = """
import os,sys
from pathlib import Path
from evolution.seed import SeedSpec,SeedClient,private_read
from guardrails.evolution.models import strict_json_loads
output=Path(sys.argv[1]); spec=SeedSpec.model_validate_json(private_read(output/'child-spec.json'))
initial=strict_json_loads(private_read(output/'bootstrap.json'))
client=SeedClient(spec,output,initial)
client.request=lambda *args,**kwargs: os._exit(17)
client.post('credential',f'/v2/tenants/{sys.argv[2]}/principals/{sys.argv[3]}/credentials',{'expires_at':initial['credential_expires_at']},initial['platform_token_file'],secret_name='admin.token')
"""
    process = subprocess.run(
        [sys.executable, "-c", child, str(output), tenant, principal],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 17
    assert process.stdout == process.stderr == b""
    assert seed.private_read(output / "admin.token") == b""
    operation = models.strict_json_loads(seed.private_read(output / "operations.json"))[
        "credential"
    ]
    with repository.transaction(tenant) as tx:
        assert tx.list("credentials") == []
    resumed = seed.SeedClient(spec, output, initial)
    result = issue(resumed, initial, tenant, principal)
    saved = models.strict_json_loads(seed.private_read(output / "operations.json"))[
        "credential"
    ]
    assert saved["key"] == operation["key"]
    assert saved["result"] == result
    with repository.transaction(tenant) as tx:
        credentials = tx.list("credentials")
        assert len(credentials) == 1
        assert credentials[0]["credential_id"] == result["credential_id"]


def test_lost_one_time_response_fails_without_rotating_or_reissuing(
    issued_system, monkeypatch
):
    spec, output, repository, client, initial, tenant, principal = issued_system
    original = client.request

    def request(*args, **kwargs):
        original(*args, **kwargs)
        raise Interrupted()

    with monkeypatch.context() as patch:
        patch.setattr(client, "request", request)
        with pytest.raises(Interrupted):
            issue(client, initial, tenant, principal)
    assert not (output / "admin.token").exists()
    with repository.transaction(tenant) as tx:
        before = {
            kind: tx.list(kind) for kind in ("credentials", "audit", "idempotency")
        }
        assert len(before["credentials"]) == 1
    resumed = seed.SeedClient(spec, output, initial)
    with pytest.raises(EvolutionError) as error:
        issue(resumed, initial, tenant, principal)
    assert error.value.code == "revision_conflict"
    assert not (output / "admin.token").exists()
    with repository.transaction(tenant) as tx:
        assert {kind: tx.list(kind) for kind in before} == before
    assert (
        "result"
        not in models.strict_json_loads(seed.private_read(output / "operations.json"))[
            "credential"
        ]
    )


def test_legacy_completed_bootstrap_without_plan_or_marker_replays_unchanged(
    setup_seed,
):
    from datetime import UTC, datetime, timedelta

    spec, output, repository = setup_seed
    seed.output_directory(output)
    service = ManagementApi(repository)
    signing = Ed25519PrivateKey.generate()
    public = signing.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    seed.private_write(
        output / "snapshot-signing.key",
        signing.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        ),
    )
    seed.private_write(output / "snapshot-signing.pub", public)
    try:
        with repository.transaction("_platform") as tx:
            if tx.get("tenants", "_platform") is None:
                tx.insert(
                    "tenants",
                    "_platform",
                    {
                        "tenant_id": "_platform",
                        "status": "active",
                        "display_name": "Platform",
                    },
                )
            principal = service.identity.provision_principal(
                tx, "Deployment bootstrap", ["platform_admin"]
            )
            credential = service.identity.issue_credential(
                tx, principal["principal_id"]
            )
            seed.private_write(
                output / "platform.token", credential["one_time_secret"].encode()
            )
            service.audit.append(
                tx,
                "principal_created",
                actor_id=principal["principal_id"],
                metadata={"operation": "deployment_bootstrap"},
            )
        EvidenceTrust(
            repository, identity=service.identity, audit=service.audit
        ).provision(
            credential["one_time_secret"],
            key_id=spec.evidence_key_id,
            public_key=seed.private_read(spec.evidence_public_key_file),
        )
        legacy = {
            "spec_digest": seed.spec_digest(spec),
            "platform_principal_id": principal["principal_id"],
            "platform_token_file": str(output / "platform.token"),
            "snapshot_private_key_file": str(output / "snapshot-signing.key"),
            "snapshot_public_key_file": str(output / "snapshot-signing.pub"),
            "snapshot_key_id": hashlib.sha256(public).hexdigest()[:16],
            "credential_expires_at": (datetime.now(UTC) + timedelta(hours=23))
            .isoformat()
            .replace("+00:00", "Z"),
        }
        seed.private_write(output / "bootstrap.json", legacy)
        before = {p.name: p.read_bytes() for p in output.iterdir()}
        records = snapshot_rows(repository, principal["principal_id"])
        assert records[2] is None
        assert seed.bootstrap(spec, output) == legacy
        assert not (output / "bootstrap-plan.json").exists()
        assert all(
            (output / name).read_bytes() == value for name, value in before.items()
        )
        assert snapshot_rows(repository, principal["principal_id"]) == records
    finally:
        service.audit.close()
