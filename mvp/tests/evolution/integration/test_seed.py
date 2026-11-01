"""Deployment stages use HTTPS governance and four genuine signed receivers."""

import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from evolution.seed import (
    SeedSpec,
    bootstrap,
    configure,
    private_read,
    private_write,
    wait_applied,
)
from guardrails.evolution import models
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.management_api import ManagementApi, create_app
from guardrails.evolution.runtime_apply import RuntimeApplyClient, RuntimeSnapshotStore
from guardrails.evolution.snapshots import SnapshotDistributor
from guardrails.evolution.storage import TenantRepository
from https_support import serve_https


def test_seed_real_https_governance_replay_private_credentials_and_four_receivers(
    tmp_path,
):
    dsn_file = Path(os.environ["EVOLUTION_TEST_DSN_FILE"])
    dsn = dsn_file.read_text().strip()
    signing = Ed25519PrivateKey.generate()
    public = signing.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    key_id = hashlib.sha256(public).hexdigest()[:16]
    private_write(
        tmp_path / "signer.key",
        signing.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        ),
    )
    private_write(
        tmp_path / "evidence.pub",
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw),
    )
    tenant_ids = ("seed-a-" + uuid4().hex[:12], "seed-b-" + uuid4().hex[:12])

    def factory():
        repo = TenantRepository(dsn)
        service = ManagementApi(repo)
        distributor = SnapshotDistributor(
            repo, service.governance, service.bindings, signing, key_id
        )
        return create_app(service=service, distributor=distributor)

    with serve_https(factory, tmp_path / "tls") as server:
        spec = SeedSpec.model_validate(
            {
                "database_url_file": str(dsn_file),
                "management_url": server["url"],
                "ca_file": server["ca_file"],
                "snapshot_private_key_file": str(tmp_path / "signer.key"),
                "evidence_public_key_file": str(tmp_path / "evidence.pub"),
                "evidence_key_id": "seed-" + uuid4().hex,
                "tenants": list(tenant_ids),
                "mock": {
                    "host": "mock.example.test",
                    "addresses": ["10.12.0.10"],
                    "path_prefix": "/v1",
                    "model_id": "gpt-4o-evolution-mock",
                },
                "ollama": {
                    "host": "ollama.example.test",
                    "addresses": ["10.12.0.11"],
                    "path_prefix": "/api",
                    "model_id": "llama3.2:1b",
                },
            }
        )
        output = tmp_path / "seed"
        initial = bootstrap(spec, output)
        assert initial["snapshot_key_id"] == key_id
        assert bootstrap(spec, output) == initial
        inventory = configure(spec, output)
        assert set(inventory["tenants"]) == set(tenant_ids)
        assert (
            len(
                {
                    row["runtime_id"]
                    for value in inventory["tenants"].values()
                    for row in value["runtimes"].values()
                }
            )
            == 4
        )
        repo = TenantRepository(dsn)
        try:
            before = {}
            for tenant, value in inventory["tenants"].items():
                assert value["policy"]["status"] == "approved"
                assert (
                    value["policy"]["author_id"]
                    == value["roles"]["author"]["principal_id"]
                )
                assert (
                    value["roles"]["author"]["principal_id"]
                    != value["roles"]["approver"]["principal_id"]
                )
                for profile, route in value["profiles"].items():
                    manifest = models.strict_json_loads(
                        private_read(route["route_manifest_file"])
                    )
                    assert manifest["routes"][0]["policy"] == value["policy_ref"]
                    assert manifest["routes"][0]["provider"] == route["provider"]
                    assert manifest["routes"][0]["destination"] == route["destination"]
                    assert (
                        route["destination"] in value["policy"]["resolved_destinations"]
                    )
                    assert route["provider"] in value["policy"]["providers"]
                    assert route["destination"]["cidrs"] == list(
                        getattr(spec, profile).cidrs()
                    )
                with repo.transaction(tenant) as tx:
                    assert (
                        tx.get("releases", value["release_id"])["status"] == "pending"
                    )
                    assert tx.get("applied_bindings", "chat") is None
                    assert tx.get("baseline", "current")["policies"] == [
                        value["policy_ref"]
                    ]
                    assert tx.get("binding_pointers", "chat")["binding_revision"] == 1
                    credentials = tx.list("credentials")
                    assert len(credentials) == 8
                    assert all(
                        "token_hash" in row and "one_time_secret" not in row
                        for row in credentials
                    )
                    before[tenant] = {
                        kind: len(tx.list(kind))
                        for kind in (
                            "principals",
                            "credentials",
                            "policies",
                            "releases",
                            "audit",
                        )
                    }
            assert configure(spec, output) == inventory
            for tenant in tenant_ids:
                with repo.transaction(tenant) as tx:
                    assert {
                        kind: len(tx.list(kind)) for kind in before[tenant]
                    } == before[tenant]
            with pytest.raises(EvolutionError) as pending:
                wait_applied(spec, output, timeout=1)
            assert pending.value.code == "snapshot_stale"
            assert not (output / "applied.json").exists()
            for tenant, value in inventory["tenants"].items():
                for replica, runtime in value["runtimes"].items():
                    store = RuntimeSnapshotStore(
                        tmp_path / (tenant + replica + ".cache"),
                        tenant,
                        runtime["runtime_id"],
                        {key_id: signing.public_key()},
                    )
                    receiver = RuntimeApplyClient(
                        server["url"], server["ca_file"], runtime["token_file"], store
                    )
                    try:
                        result = receiver.sync_once()
                        assert result["status"] == "ready"
                        assert result["receipts"] == 1
                        assert (
                            store.current("chat")[2].compiled_digest
                            == value["policy_ref"]["digest"]
                        )
                    finally:
                        receiver.close()
            assert wait_applied(spec, output, timeout=5) == {
                "status": "applied",
                "tenant_count": 2,
                "runtime_count": 4,
            }
            for tenant, value in inventory["tenants"].items():
                with repo.transaction(tenant) as tx:
                    assert (
                        tx.get("releases", value["release_id"])["status"] == "applied"
                    )
                    assert tx.get("applied_bindings", "chat")["binding_revision"] == 1
                    assert len(tx.list("receipts")) == 2
            journals = b"".join(path.read_bytes() for path in output.glob("*.json"))
            secret_field_retained = (
                b"one_time_secret" in journals or b"token_hash" in journals
            )
            assert not secret_field_retained
            secrets = [private_read(path) for path in output.glob("*.token")]
            secret_count, unique_count = len(secrets), len(set(secrets))
            assert secret_count == 19
            assert unique_count == secret_count
            secret_retained = any(secret in journals for secret in secrets)
            assert not secret_retained
            assert all(
                path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir()
            )
            tenant = tenant_ids[0]
            target = inventory["tenants"][tenant]["runtimes"]["runtime-1"]["runtime_id"]
            with repo.transaction(tenant) as tx:
                install = tx.get("runtimes", target, required=True)
                tx.put(
                    "runtimes",
                    target,
                    {**install, "status": "revoked"},
                    expected_revision=install["revision"],
                )
            with pytest.raises(EvolutionError) as stale:
                wait_applied(spec, output, timeout=1)
            assert stale.value.code == "snapshot_stale"
            with repo.transaction(tenant) as tx:
                assert (
                    tx.get("releases", inventory["tenants"][tenant]["release_id"])[
                        "status"
                    ]
                    == "applied"
                )
        finally:
            repo.close()


def test_seed_private_file_boundary_refuses_symlink_insecure_permissions_and_overwrite(
    tmp_path,
):
    target = tmp_path / "private.token"
    private_write(target, b"test-secret")
    assert private_read(target) == b"test-secret"
    with pytest.raises(FileExistsError):
        private_write(target, b"replacement")
    assert private_read(target) == b"test-secret"
    linked = tmp_path / "linked.token"
    linked.symlink_to(target)
    with pytest.raises(OSError):
        private_read(linked)
    target.chmod(0o644)
    with pytest.raises(EvolutionError) as insecure:
        private_read(target)
    assert insecure.value.code == "validation_failed"


def test_seed_invalid_spec_cli_does_not_echo_private_content(tmp_path, capsys):
    from evolution.seed import main

    source = tmp_path / "invalid-spec.json"
    private_write(source, {"unexpected-secret": "never-print-this-value"})
    assert (
        main(["configure", "--spec", str(source), "--output", str(tmp_path / "output")])
        == 1
    )
    captured = capsys.readouterr()
    assert models.strict_json_loads(captured.out) == {
        "status": "fail",
        "stage": "configure",
        "code": "validation_failed",
    }
    assert "never-print-this-value" not in captured.out + captured.err
    assert not (tmp_path / "output").exists()
