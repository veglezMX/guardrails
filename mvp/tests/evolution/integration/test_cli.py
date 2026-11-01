"""Real TLS management server and separately executed lifecycle CLI."""

import ipaddress
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from guardrails.evolution import models
from guardrails.evolution.audit import AuditStore
from guardrails.evolution.cli import _body, _secret
from guardrails.evolution.management_api import ManagementApi, create_app
from guardrails.evolution.storage import TenantRepository

ROOT = Path(__file__).resolve().parents[4]


def certificates(path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    current = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(current - timedelta(minutes=1))
        .not_valid_after(current + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = path / "server.crt", path / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return cert_path, key_path


@pytest.fixture
def https_system(tmp_path):
    repo = TenantRepository(
        Path(os.environ["EVOLUTION_TEST_DSN_FILE"]).read_text().strip()
    )
    service = ManagementApi(repo)
    tenant = "cli-" + uuid4().hex[:20]
    quota = models.with_resource_digest(
        models.Quota(quota_id=str(uuid4()), tenant_id=tenant, version=1)
    )
    residency = models.with_resource_digest(
        models.ResidencyProfile(residency_id=str(uuid4()), tenant_id=tenant, version=1)
    )
    repo.provision_tenant(
        models.Tenant(
            tenant_id=tenant,
            display_name="CLI fixture",
            status="active",
            baseline=None,
            residency_id=residency.residency_id,
            quota_id=quota.quota_id,
        ),
        residency,
        quota,
    )
    token_files, identities = {}, {}
    with repo.transaction(tenant) as tx:
        for role, roles in {
            "author": ["author", "approver"],
            "approver": ["approver"],
            "operator": ["operator"],
            "admin": ["tenant_admin", "auditor"],
        }.items():
            actor = service.identity.provision_principal(tx, role, roles)
            credential = service.identity.issue_credential(tx, actor["principal_id"])
            path = tmp_path / (role + ".token")
            path.write_text(credential["one_time_secret"])
            path.chmod(0o600)
            token_files[role] = path
            identities[role] = actor
        tx.insert(
            "workloads",
            "chat",
            models.Workload(
                tenant_id=tenant,
                workload_id="chat",
                display_name="Chat",
                mode="remote",
                status="active",
                allowed_stages=("input", "output"),
                provider_ids=(),
                quota_id=quota.quota_id,
                required_state="observe",
            ),
        )
    cert, key = certificates(tmp_path)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(
        create_app(service=service),
        ssl_certfile=str(cert),
        ssl_keyfile=str(key),
        log_level="critical",
        access_log=False,
        lifespan="off",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    until = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < until:
        time.sleep(0.02)
    assert server.started
    url = f"https://127.0.0.1:{port}"
    payload = {
        "rules": [
            {
                "rule_id": "email",
                "kind": "pii",
                "stages": ["input", "output"],
                "action": "block",
                "mandatory": True,
                "minimum_state": "observe",
                "config": {"entities": ["email"]},
                "evaluator_revision": "pii-v2",
            }
        ],
        "residency_ref": {
            "id": residency.residency_id,
            "version": 1,
            "digest": residency.digest,
        },
        "quota_ref": {"id": quota.quota_id, "version": 1, "digest": quota.digest},
    }

    def cli(command, role="author", **options):
        argv = [
            str(Path(sys.executable).parent / "guardrails-evolution"),
            *command,
            "--url",
            url,
            "--ca-file",
            str(cert),
            "--credential-file",
            str(token_files[role]),
            "--tenant",
            tenant,
            "--json",
        ]
        for name, value in options.items():
            argv += ["--" + name.replace("_", "-"), str(value)]
        return subprocess.run(
            argv,
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "mvp")},
            capture_output=True,
            text=True,
            timeout=15,
        )

    try:
        yield repo, service, tenant, token_files, identities, payload, cli, url, cert
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        repo.close()


def test_https_cli_policy_review_role_parity_and_pending_timeout(
    https_system, tmp_path
):
    repo, service, tenant, tokens, actors, payload, cli, *_ = https_system
    source = tmp_path / "candidate.json"
    source.write_text(json.dumps(payload))
    submitted = cli(["policy", "submit"], policy="policy", file=source)
    assert submitted.returncode == 0, submitted.stderr
    candidate = json.loads(submitted.stdout)
    digest_file = tmp_path / "candidate.sha256"
    digest_file.write_text(candidate["compiled_digest"])
    own = cli(
        ["policy", "review", "approve"],
        policy="policy",
        version=1,
        digest_file=digest_file,
        reason="Self review",
    )
    assert own.returncode == 3
    approved = cli(
        ["policy", "review", "approve"],
        role="approver",
        policy="policy",
        version=1,
        digest_file=digest_file,
        reason="Independent review",
    )
    assert approved.returncode == 0, approved.stderr
    release_file = tmp_path / "release.json"
    release_file.write_text(
        json.dumps(
            {
                "policy": {
                    "tenant_id": tenant,
                    "policy_id": "policy",
                    "version": 1,
                    "digest": candidate["compiled_digest"],
                },
                "workload_ids": ["chat"],
                "state": "observe",
                "expected_revisions": {"chat": 0},
                "reason": "Canary",
            }
        )
    )
    released = cli(["release", "canary"], role="operator", file=release_file)
    assert released.returncode == 0, released.stderr
    release = json.loads(released.stdout)
    assert release["status"] == "pending"
    waited = cli(
        ["release", "wait"], role="operator", id=release["release_id"], timeout=0.1
    )
    assert waited.returncode == 5
    with repo.transaction(tenant) as tx:
        assert tx.get("applied_bindings", "chat") is None
        assert len(tx.list("approvals")) == 1


def test_https_cli_secret_output_is_exclusive_and_never_stdout(https_system, tmp_path):
    repo, service, tenant, tokens, actors, payload, cli, *_ = https_system
    source = tmp_path / "expiry.json"
    source.write_text(json.dumps({"expires_at": models.utc_now().replace("Z", "Z")}))
    # An actual future expiry within the default one-day administrator lifetime.
    source.write_text(
        json.dumps(
            {
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1))
                .isoformat()
                .replace("+00:00", "Z")
            }
        )
    )
    output = tmp_path / "issued.token"
    issued = cli(
        ["credential", "issue"],
        role="admin",
        id=actors["author"]["principal_id"],
        file=source,
        secret_output=output,
    )
    assert issued.returncode == 0, issued.stderr
    secret = output.read_text().strip()
    assert secret not in issued.stdout + issued.stderr
    assert output.stat().st_mode & 0o777 == 0o600
    assert (
        service.identity.authenticate(secret).principal_id
        == actors["author"]["principal_id"]
    )
    duplicate = cli(
        ["credential", "issue"],
        role="admin",
        id=actors["author"]["principal_id"],
        file=source,
        secret_output=output,
    )
    assert duplicate.returncode == 2
    assert output.read_text().strip() == secret


def test_https_cli_rejects_untrusted_server_and_insecure_credentials(
    https_system, tmp_path
):
    repo, service, tenant, tokens, actors, payload, cli, url, cert = https_system
    with pytest.raises(httpx.ConnectError):
        httpx.get(url + "/health/live", verify=True, trust_env=False)
    tokens["author"].chmod(0o644)
    invalid = cli(["policy", "list"])
    assert invalid.returncode == 2
    assert tokens["author"].read_text().strip() not in invalid.stdout + invalid.stderr
    link = tmp_path / "linked.token"
    link.symlink_to(tokens["author"])
    with pytest.raises(OSError):
        _secret(str(link))


def test_cli_input_rejects_duplicate_nonfinite_and_oversized_json(tmp_path):
    path = tmp_path / "body.json"
    for content in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', "[]"):
        path.write_text(content)
        with pytest.raises(ValueError):
            _body(str(path))
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError):
        _body(str(path))


def test_https_cli_configuration_versions_preserve_identity_and_retry(
    https_system, tmp_path
):
    repo, service, tenant, tokens, actors, payload, cli, *_ = https_system
    source = tmp_path / "quota.json"
    source.write_text(json.dumps({"requests_per_minute": 60}))
    created = cli(
        ["config", "create-version"], role="admin", resource="quotas", file=source
    )
    assert created.returncode == 0, created.stderr
    first = json.loads(created.stdout)
    assert first["version"] == 1
    source.write_text(json.dumps({"requests_per_minute": 120}))
    key = str(uuid4())
    options = dict(
        role="admin",
        resource="quotas",
        id=first["quota_id"],
        file=source,
        idempotency_key=key,
    )
    revised = cli(["config", "create-version"], **options)
    assert revised.returncode == 0, revised.stderr
    second = json.loads(revised.stdout)
    assert second["quota_id"] == first["quota_id"] and second["version"] == 2
    assert second["requests_per_minute"] == 120 and second["digest"] != first["digest"]
    replay = cli(["config", "create-version"], **options)
    assert replay.returncode == 0 and json.loads(replay.stdout) == second
    old = cli(
        ["config", "show"],
        role="admin",
        resource="quotas",
        id=first["quota_id"],
        version=1,
    )
    assert old.returncode == 0 and json.loads(old.stdout) == first
    source.write_text(json.dumps({"requests_per_minute": 180}))
    conflict = cli(["config", "create-version"], **options)
    assert conflict.returncode == 4 and "HTTP 409" in conflict.stderr
    with repo.transaction(tenant) as tx:
        assert tx.get("config:quotas", first["quota_id"] + ":3") is None
        assert tx.get("applied_bindings", "chat") is None
    missing_file = cli(["config", "create-version"], role="admin", resource="quotas")
    malformed = cli(
        ["config", "create-version"],
        role="admin",
        resource="quotas",
        id="not-a-uuid",
        file=source,
    )
    assert missing_file.returncode == malformed.returncode == 2


def test_https_cli_exports_every_audit_page_and_preserves_filters(
    https_system, tmp_path
):
    repo, service, tenant, tokens, actors, payload, cli, *_ = https_system
    store = AuditStore(repo)
    with repo.transaction(tenant) as tx:
        expected = {
            store.append(tx, "evaluation", workload_id="chat", action="block")[
                "event_id"
            ]
            for _ in range(7)
        }
        store.append(tx, "evaluation", workload_id="chat", action="allow")
    destination = tmp_path / "audit.json"
    exported = cli(
        ["audit", "export"],
        role="admin",
        limit=2,
        workload="chat",
        action="block",
        output=destination,
    )
    assert exported.returncode == 0, exported.stderr
    result = json.loads(destination.read_text())
    assert {item["event_id"] for item in result["items"]} == expected
    assert len(result["items"]) == 7 and result["next_cursor"] is None
    assert destination.stat().st_mode & 0o777 == 0o600
    first = cli(
        ["audit", "list"], role="admin", limit=2, workload="chat", action="block"
    )
    first_page = json.loads(first.stdout)
    assert len(first_page["items"]) == 2 and first_page["next_cursor"]
    denied = cli(["audit", "export"], role="author", limit=2)
    assert denied.returncode == 3
    store.close()


def test_https_cli_reserves_secret_file_before_issuance_and_cleans_failed_attempt(
    https_system, tmp_path
):
    repo, service, tenant, tokens, actors, payload, cli, *_ = https_system
    source = tmp_path / "expiry.json"
    source.write_text(
        json.dumps(
            {
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1))
                .isoformat()
                .replace("+00:00", "Z")
            }
        )
    )
    with repo.transaction(tenant) as tx:
        before = len(tx.list("credentials"))
    invalid = cli(
        ["credential", "issue"],
        role="admin",
        id=actors["author"]["principal_id"],
        file=source,
        secret_output=tmp_path / "missing-parent" / "token",
    )
    assert invalid.returncode == 2
    with repo.transaction(tenant) as tx:
        assert len(tx.list("credentials")) == before
    destination = tmp_path / "rejected.token"
    denied = cli(
        ["credential", "issue"],
        role="author",
        id=actors["author"]["principal_id"],
        file=source,
        secret_output=destination,
    )
    assert denied.returncode == 3
    assert not destination.exists()
    missing_id = cli(["release", "show"])
    assert missing_id.returncode == 2
