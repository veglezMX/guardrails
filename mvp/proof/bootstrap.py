"""Prepare pinned local-proof artifacts; service startup and acceptance are separate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import secrets
import subprocess
import sys
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path

try:
    from proof.resolve_dependencies import PYTHON_BASE, resolve, run, sha256, write_json
except ModuleNotFoundError:
    from resolve_dependencies import PYTHON_BASE, resolve, run, sha256, write_json

ROOT = Path(__file__).resolve().parents[2]
PROOF = ROOT / ".proof"
LITELLM_BASE = "ghcr.io/berriai/litellm:v1.104.2@sha256:3d88bd757134a00e8c00237b85a021ab1f6d54abb9c1df750f411dbe7256a500"
OWNED_IMAGE = "local/guardrails-proof:phase1"
GATEWAY_IMAGE = "local/guardrails-gateway:phase1"


def ensure_image(reference: str) -> dict:
    found = subprocess.run(
        ["docker", "image", "inspect", reference], cwd=ROOT,
        text=True, capture_output=True,
    )
    if found.returncode:
        run(["docker", "pull", reference])
    return json.loads(run(["docker", "image", "inspect", reference], capture=True))[0]


def image_identity(reference: str) -> dict:
    inspected = json.loads(run(["docker", "image", "inspect", reference], capture=True))[0]
    return {
        "reference": reference, "image_id": inspected["Id"],
        "repository_digests": inspected.get("RepoDigests", []),
        "os": inspected["Os"], "architecture": inspected["Architecture"],
    }


def secret(name: str) -> str:
    path = PROOF / "secrets" / f"{name}.token"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if len(value) < 32 or any(character.isspace() for character in value):
            raise RuntimeError(f"Invalid existing proof credential file: {path.name}")
    else:
        value = ("sk-" if name.endswith(".client") else "") + secrets.token_urlsafe(32)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    path.chmod(0o600)
    return value


def prepare_credentials() -> None:
    credentials = {
        name: secret(name) for name in (
            "gateway-a.workload", "gateway-b.workload",
            "gateway-a.client", "gateway-b.client", "control", "mock",
            "management-admin", "runtime-service", "management-service", "tenant-operator",
        )
    }
    if len(set(credentials.values())) != len(credentials):
        raise RuntimeError("Proof credentials must be distinct")
    token_hash = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    failures = dict.fromkeys((
        "evaluation_timeout", "evaluation_unavailable", "policy_unresolved",
        "management_unavailable", "audit_failure", "transformation_failure",
    ), "block")
    # This is an explicit fixture seed, not management publication or lifecycle.
    fixture = {
        "schema_version": "local-proof-v1", "profile": "smoke", "snapshot_generation": 1,
        "workloads": [
            {
                "tenant_id": "local-proof", "workload_id": workload,
                "token_sha256": token_hash(credentials[f"{workload}.workload"]),
            }
            for workload in ("gateway-a", "gateway-b")
        ],
        "policy": {
            "schema_version": "local-proof-v1", "tenant_id": "local-proof",
            "policy_id": "smoke-pii", "version": "v1",
            "checks": [{
                "check_id": "pii", "evaluator": "local_pii", "stages": ["input", "output"],
                "pii_types": ["email", "phone"], "requested_action": "block",
            }],
            "failure_behavior": failures, "retain_content": False,
            "detector_format_version": "pii-ascii-v1",
        },
        "enforcement_state": "block", "control_token_sha256": token_hash(credentials["control"]),
    }
    write_json(PROOF / "smoke-runtime.json", fixture)
    (PROOF / "smoke-runtime.json").chmod(0o600)
    config_path = PROOF / 'management.json'
    prior = json.loads(config_path.read_text()) if config_path.exists() else {'credentials': []}
    existing = {item['token_sha256']: item for item in prior['credentials']}
    actors = []
    for name, role, tenant, service, capabilities in (
        ('management-admin', 'security_admin', None, None, []),
        ('tenant-operator', 'tenant_operator', 'local-proof', None, []),
        ('runtime-service', 'runtime_service', None, 'runtime', ['snapshot', 'applied']),
        ('management-service', 'management_service', None, 'management', ['audit', 'schema', 'readiness']),
    ):
        digest = token_hash(credentials[name])
        actors.append(existing.get(digest) or dict(
            credential_id=str(uuid4()), role=role, tenant_id=tenant, workload_id=None,
            token_sha256=digest, revision=1, created_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            revoked_at=None, service_id=service, capabilities=capabilities,
        ))
    write_json(config_path, {'credentials': actors})
    config_path.chmod(0o600)
    write_json(PROOF / 'live-management.json', {'credentials': actors, 'runtime': {
        'url': 'https://runtime:8444', 'ca_file': '/pki/ca.crt', 'token_file': '/secrets/management-service.token'}})
    write_json(PROOF / 'live-runtime.json', {
        'credentials': [item for item in actors if item['role'] == 'management_service'],
        'management_url': 'https://management:8443', 'ca_file': '/pki/ca.crt',
        'token_file': '/secrets/runtime-service.token', 'proof_config': {},
    })
    for name in ('live-management.json', 'live-runtime.json'):
        (PROOF / name).chmod(0o600)


def prepare_certificates(*, refresh: bool) -> None:
    script = r"""
import os, subprocess
from pathlib import Path
root = Path("/pki")
os.umask(0o077)
refresh = os.environ["PROOF_REFRESH_CERTIFICATES"] == "1"
services = ("management", "runtime", "mock-model", "gateway-a", "gateway-b")
def run(*args):
    subprocess.run(["openssl", *args], check=True, capture_output=True, text=True)
ca_crt, ca_key = root / "ca.crt", root / "ca.key"
if ca_crt.exists() != ca_key.exists() and not refresh:
    raise RuntimeError("Incomplete CA; inspect proof state before --refresh-certificates")
if refresh or not ca_crt.exists():
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(ca_key),
        "-out", str(ca_crt), "-days", "30", "-subj", "/CN=Local Guardrails Proof CA",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign")
else:
    run("x509", "-checkend", "86400", "-noout", "-in", str(ca_crt))
for service in services:
    crt, key = root / f"{service}.crt", root / f"{service}.key"
    if crt.exists() != key.exists() and not refresh:
        raise RuntimeError("Incomplete leaf credentials; inspect before --refresh-certificates")
    if refresh or not crt.exists():
        csr, extensions = root / f"{service}.csr", root / f"{service}.ext"
        extensions.write_text(
            f"subjectAltName=DNS:{service},DNS:localhost,IP:127.0.0.1\n"
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
        )
        run("req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
            "-out", str(csr), "-subj", f"/CN={service}")
        run("x509", "-req", "-in", str(csr), "-CA", str(ca_crt), "-CAkey", str(ca_key),
            "-CAcreateserial", "-out", str(crt), "-days", "14", "-sha256",
            "-extfile", str(extensions))
        csr.unlink()
        extensions.unlink()
    run("x509", "-checkend", "86400", "-noout", "-in", str(crt))
    run("verify", "-CAfile", str(ca_crt), str(crt))
for path in root.iterdir():
    if path.is_file():
        path.chmod(0o600)
"""
    run([
        "docker", "run", "--rm", "--network", "none",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--env", f"PROOF_REFRESH_CERTIFICATES={int(refresh)}",
        "--mount", f"type=bind,source={PROOF / 'pki'},target=/pki",
        "--entrypoint", "python", OWNED_IMAGE, "-c", script,
    ])


def installed_versions(reference: str, *, owned: bool) -> dict:
    script = r"""
import importlib.metadata as metadata, json, platform, sqlite3, ssl, subprocess
packages = {item.metadata["Name"]: item.version for item in metadata.distributions() if item.metadata["Name"]}
try:
    openssl_binary = subprocess.check_output(["openssl", "version"], text=True).strip()
except (OSError, subprocess.CalledProcessError):
    openssl_binary = None
print(json.dumps({
    "python": platform.python_version(), "platform": platform.platform(),
    "sqlite": sqlite3.sqlite_version, "python_openssl": ssl.OPENSSL_VERSION,
    "openssl_binary": openssl_binary, "packages": dict(sorted(packages.items(), key=lambda item:item[0].lower())),
}, sort_keys=True))
"""
    result = json.loads(run([
        "docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
        reference, "-c", script,
    ], capture=True))
    if owned and result["python"] != "3.14.8":
        raise RuntimeError("Owned image does not run the selected CPython")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-locks", action="store_true", help="Explicitly resolve dependency versions again")
    parser.add_argument("--refresh-certificates", action="store_true", help="Replace this proof's local CA and leaf certificates")
    arguments = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise RuntimeError("This proof currently requires Linux x86_64")
    PROOF.mkdir(exist_ok=True, mode=0o700)
    PROOF.chmod(0o700)
    for name in ("pki", "secrets", "state", "state/runtime", "state/live-runtime", "state/management", "state/management-tests", "state/runtime-tests", "state/gateway-a", "state/gateway-b", "artifacts", "evidence"):
        target = PROOF / name
        target.mkdir(exist_ok=True, mode=0o700)
        target.chmod(0o700)
    engine = run(["docker", "version", "--format", "{{.Server.Version}}"], capture=True)
    compose = run(["docker", "compose", "version", "--short"], capture=True).lstrip("v")
    python_identity, gateway_identity = ensure_image(PYTHON_BASE), ensure_image(LITELLM_BASE)
    if any(image["Architecture"] != "amd64" or image["Os"] != "linux" for image in (python_identity, gateway_identity)):
        raise RuntimeError("Pinned base images must resolve for Linux amd64")
    dependencies = resolve(refresh=arguments.refresh_locks)
    # Host supervision/CLI only; every service still uses its pinned image.
    host_python = PROOF / 'host-venv/bin/python'
    if not host_python.exists():
        run([sys.executable, '-m', 'venv', str(PROOF / 'host-venv')])
    run([str(host_python), '-m', 'pip', 'install', '--no-index', '--find-links=' + str(PROOF / 'wheelhouse'), '-r', str(ROOT / 'dev-requirements.lock')], capture=True)
    run(["docker", "build", "--pull=false", "-f", "mvp/proof/Dockerfile", "-t", OWNED_IMAGE, "."])
    run(["docker", "build", "--pull=false", "-f", "mvp/proof/gateway.Dockerfile", "-t", GATEWAY_IMAGE, "."])
    prepare_certificates(refresh=arguments.refresh_certificates)
    prepare_credentials()
    owned_versions = installed_versions(OWNED_IMAGE, owned=True)
    gateway_versions = installed_versions(GATEWAY_IMAGE, owned=False)
    if not owned_versions["openssl_binary"]:
        raise RuntimeError("The owned image must include OpenSSL for local certificate preparation")
    if gateway_versions["packages"].get("litellm") != "1.104.2":
        raise RuntimeError("Derived gateway image does not contain the pinned LiteLLM version")
    source_commit = run(["git", "rev-parse", "HEAD"], capture=True)
    source_dirty = bool(run(["git", "status", "--porcelain"], capture=True))
    manifest = {
        "schema_version": "phase1-bootstrap-v1",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit, "source_dirty": source_dirty,
        "platform": {"host": platform.platform(), "machine": platform.machine()},
        "docker_engine": engine, "docker_compose": compose,
        "deviations": ([] if compose == "5.6.0" else [{
            "component": "docker_compose", "planned": "5.6.0", "actual": compose,
            "disposition": "Record actual host version; smoke gate must prove required Compose behavior",
        }]),
        "images": {
            "python_base": image_identity(PYTHON_BASE),
            "litellm_base": image_identity(LITELLM_BASE),
            "owned": image_identity(OWNED_IMAGE), "gateway": image_identity(GATEWAY_IMAGE),
        },
        "owned_environment": owned_versions, "gateway_environment": gateway_versions,
        "dependencies": dependencies,
        "config_sha256": sha256(PROOF / "smoke-runtime.json"),
        "certificates": {
            path.name: sha256(path) for path in sorted((PROOF / "pki").glob("*.crt"))
        },
        "host_supervisor_python": platform.python_version(),
        "profile": "local-proof",
        "limitations": [
            "Phase-one fixture configuration is not management publication or lifecycle acceptance",
            "Dependency installation and image builds are online preparation only",
            "Actual offline execution and TLS verification require the separate smoke gate",
        ],
    }
    write_json(PROOF / "artifacts" / "manifest.json", manifest)
    print("Prepared pinned proof images, wheelhouse, local TLS and explicit smoke fixtures.")
    print("Artifact evidence: .proof/artifacts/manifest.json")


if __name__ == "__main__":
    main()
