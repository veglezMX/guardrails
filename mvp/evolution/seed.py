"""Private deployment bootstrap followed by the real HTTPS governance journey.

Bootstrap precedes management deployment; configure precedes runtime deployment;
wait requires actual running runtime receivers. No stage fabricates a receipt.
Only credential files contain bearer secrets; journals and stdout contain none.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import ssl
import stat
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from guardrails.evolution import models
from guardrails.evolution.deadlines import current_deadline
from guardrails.evolution.enrollment import RuntimeEnrollment
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.evidence_trust import EvidenceTrust
from guardrails.evolution.governance import lock_admission
from guardrails.evolution.http_client import BoundedJsonClient
from guardrails.evolution.identity import IdentityService
from guardrails.evolution.management_api import ManagementApi
from guardrails.evolution.storage import TenantRepository, migrate
from pydantic import Field, field_validator, model_validator
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


class ModelEndpoint(models.DomainModel):
    host: models.Identifier
    port: Annotated[int, Field(ge=1, le=65535)] = 8443
    addresses: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    path_prefix: str
    model_id: models.Identifier
    tls_ca_ref: models.Identifier = "cluster-model-ca"

    @model_validator(mode="after")
    def endpoint(self):
        if len(set(self.addresses)) != len(self.addresses):
            raise ValueError("Duplicate service address")
        for address in self.addresses:
            if str(ipaddress.ip_address(address)) != address:
                raise ValueError("Service address must be canonical")
        models.Destination(
            destination_id=str(uuid4()),
            tenant_id="seed-validation",
            name="model",
            host=self.host,
            port=self.port,
            path_prefix=self.path_prefix,
            cidrs=self.cidrs(),
            zone="local-shared",
            purpose="inference",
            tls_ca_ref=self.tls_ca_ref,
        )
        return self

    def cidrs(self):
        return tuple(
            str(ipaddress.ip_network(address + ("/128" if ":" in address else "/32")))
            for address in self.addresses
        )


class SeedSpec(models.DomainModel):
    database_url_file: str
    owner_database_url_file: str | None = None
    management_url: str
    ca_file: str
    evidence_public_key_file: str
    evidence_key_id: models.Identifier
    snapshot_private_key_file: str | None = None
    tenants: Annotated[tuple[models.Slug, ...], Field(min_length=2, max_length=2)] = (
        "team-a",
        "team-b",
    )
    workload_id: models.Slug = "chat"
    policy_id: models.Slug = "pii-baseline"
    mock: ModelEndpoint
    ollama: ModelEndpoint

    @field_validator(
        "database_url_file",
        "owner_database_url_file",
        "ca_file",
        "evidence_public_key_file",
        "snapshot_private_key_file",
    )
    @classmethod
    def absolute_file(cls, value):
        if value is not None and (not Path(value).is_absolute() or "\0" in value):
            raise ValueError("Mounted file references must be absolute")
        return value

    @model_validator(mode="after")
    def scope(self):
        origin = urlsplit(self.management_url)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or origin.path
            or self.management_url.endswith("/")
        ):
            raise ValueError("Management requires an exact HTTPS origin")
        if len(set(self.tenants)) != 2:
            raise ValueError("Two distinct tenants are required")
        return self


def private_read(path, *, maximum=1024 * 1024):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        details = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or details.st_mode & 0o077
            or details.st_size > maximum
        ):
            raise EvolutionError("validation_failed")
        value = source.read(maximum + 1)
    if len(value) > maximum:
        raise EvolutionError("validation_failed")
    return value


def private_write(path, value, *, replace=False):
    path = Path(path)
    if not path.is_absolute():
        raise EvolutionError("validation_failed")
    content = value if isinstance(value, bytes) else models.canonical_json_bytes(value)
    staged = (
        path.with_name(path.name + "." + uuid4().hex + ".pending") if replace else path
    )
    descriptor = os.open(
        staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    if replace:
        staged.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def output_directory(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise EvolutionError("validation_failed")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    details = path.stat()
    if details.st_uid != os.getuid() or details.st_mode & 0o077:
        raise EvolutionError("validation_failed")
    return path


def spec_digest(spec):
    return hashlib.sha256(models.canonical_json_bytes(spec)).hexdigest()


def prepare_database(spec):
    app_dsn = private_read(spec.database_url_file, maximum=4096).decode().strip()
    if spec.owner_database_url_file:
        from psycopg import sql

        owner_dsn = (
            private_read(spec.owner_database_url_file, maximum=4096).decode().strip()
        )
        owner, app = make_url(owner_dsn), make_url(app_dsn)
        if (
            (owner.host, owner.port, owner.database)
            != (app.host, app.port, app.database)
            or not app.username
            or not app.password
            or owner.username == app.username
        ):
            raise EvolutionError("validation_failed")
        migrate(owner_dsn)
        engine = create_engine(owner_dsn, hide_parameters=True)
        try:
            with engine.begin() as connection:
                driver = connection.connection.driver_connection
                row = driver.execute(
                    "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=%s",
                    (app.username,),
                ).fetchone()
                if row is None:
                    driver.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {}"
                        ).format(
                            sql.Identifier(app.username), sql.Literal(app.password)
                        )
                    )
                elif any(row):
                    raise EvolutionError("forbidden")
                for statement in (
                    "GRANT USAGE ON SCHEMA evolution TO {}",
                    "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA evolution TO {}",
                    "GRANT EXECUTE ON FUNCTION evolution.tenant_scope() TO {}",
                ):
                    driver.execute(
                        sql.SQL(statement).format(sql.Identifier(app.username))
                    )
        finally:
            engine.dispose()
    return TenantRepository(app_dsn)


@contextmanager
def _seed_lock(output):
    descriptor = os.open(
        output / ".seed.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or details.st_mode & 0o077
        ):
            raise EvolutionError("validation_failed")
        deadline = time.monotonic() + 10
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise EvolutionError("upstream_timeout") from None
                time.sleep(0.01)
        yield
    finally:
        os.close(descriptor)


class _BootstrapPlan(models.DomainModel):
    schema_version: Literal["deployment-bootstrap-v1"] = "deployment-bootstrap-v1"
    spec_digest: models.Digest
    principal_id: models.UUID
    credential_id: models.UUID
    created_at: models.Time
    expires_at: models.Time
    snapshot_public_digest: models.Digest
    evidence_public_digest: models.Digest


def _bootstrap_keys(spec, output, *, create):
    def decode(raw):
        key = (
            serialization.load_pem_private_key(raw, password=None)
            if raw.startswith(b"-----BEGIN")
            else Ed25519PrivateKey.from_private_bytes(raw)
        )
        if not isinstance(key, Ed25519PrivateKey):
            raise EvolutionError("validation_failed")
        return key

    source = (
        decode(private_read(spec.snapshot_private_key_file, maximum=16384))
        if spec.snapshot_private_key_file
        else None
    )
    key_file, public_file = (
        output / "snapshot-signing.key",
        output / "snapshot-signing.pub",
    )
    if key_file.exists() or not create:
        key = decode(private_read(key_file, maximum=16384))
    else:
        key = source or Ed25519PrivateKey.generate()
        private_write(
            key_file,
            key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            ),
        )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    if (
        source
        and source.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        != public
    ):
        raise EvolutionError("idempotency_conflict")
    if public_file.exists() or not create:
        if private_read(public_file) != public:
            raise EvolutionError("idempotency_conflict")
    else:
        private_write(public_file, public)
    return public


def _evidence_root(spec, repository, service, token, evidence_key, *, create):
    expected = base64.b64encode(evidence_key).decode()
    with repository.transaction("_platform") as tx:
        existing = tx.get("evidence_trust_roots", spec.evidence_key_id)
    if existing is None and create:
        try:
            EvidenceTrust(
                repository, identity=service.identity, audit=service.audit
            ).provision(token, key_id=spec.evidence_key_id, public_key=evidence_key)
        except EvolutionError as error:
            if error.code != "revision_conflict":
                raise
        with repository.transaction("_platform") as tx:
            existing = tx.get("evidence_trust_roots", spec.evidence_key_id)
    if (
        existing is None
        or existing.get("status") != "active"
        or existing.get("algorithm") != "Ed25519"
        or existing.get("public_key_base64") != expected
    ):
        raise EvolutionError("idempotency_conflict")


def _validate_bootstrap(spec, output, saved, repository, service, public, evidence_key):
    expected_paths = {
        "platform_token_file": str(output / "platform.token"),
        "snapshot_private_key_file": str(output / "snapshot-signing.key"),
        "snapshot_public_key_file": str(output / "snapshot-signing.pub"),
    }
    if (
        set(saved)
        != {
            *expected_paths,
            "spec_digest",
            "platform_principal_id",
            "snapshot_key_id",
            "credential_expires_at",
        }
        or saved["spec_digest"] != spec_digest(spec)
        or any(saved.get(name) != value for name, value in expected_paths.items())
        or saved["snapshot_key_id"] != hashlib.sha256(public).hexdigest()[:16]
    ):
        raise EvolutionError("idempotency_conflict")
    token = private_read(output / "platform.token", maximum=4096).decode()
    actor = service.identity.authenticate(token)
    if (
        actor.scope != "_platform"
        or actor.roles != ("platform_admin",)
        or actor.principal_id != saved["platform_principal_id"]
        or not datetime.now(UTC)
        < models.parse_time(saved["credential_expires_at"])
        <= models.parse_time(actor.credential["expires_at"])
    ):
        raise EvolutionError("idempotency_conflict")
    with repository.transaction("_platform") as tx:
        marker = tx.get("deployment_bootstrap", actor.principal_id)
    if (output / "bootstrap-plan.json").exists():
        plan = _BootstrapPlan.model_validate_json(
            private_read(output / "bootstrap-plan.json")
        )
        if (
            plan.spec_digest != saved["spec_digest"]
            or plan.principal_id != actor.principal_id
            or plan.credential_id != actor.credential["credential_id"]
            or plan.expires_at != saved["credential_expires_at"]
            or plan.snapshot_public_digest != hashlib.sha256(public).hexdigest()
            or plan.evidence_public_digest != hashlib.sha256(evidence_key).hexdigest()
        ):
            raise EvolutionError("idempotency_conflict")
        if marker is None or any(
            marker.get(key) != value
            for key, value in plan.model_dump(mode="json").items()
        ):
            raise EvolutionError("idempotency_conflict")
    elif marker is not None:
        raise EvolutionError("idempotency_conflict")
    _evidence_root(spec, repository, service, token, evidence_key, create=False)
    return saved


def bootstrap(spec: SeedSpec, output):
    """Recover the same private material and database identity after interruption."""
    try:
        output = output_directory(output)
        with _seed_lock(output):
            path, plan_path = output / "bootstrap.json", output / "bootstrap-plan.json"
            completed = path.exists()
            public = _bootstrap_keys(
                spec, output, create=not completed and not plan_path.exists()
            )
            evidence_key = private_read(spec.evidence_public_key_file, maximum=32)
            if len(evidence_key) != 32:
                raise EvolutionError("validation_failed")
            repository = (
                TenantRepository(
                    private_read(spec.database_url_file, maximum=4096).decode().strip()
                )
                if completed
                else prepare_database(spec)
            )
            service = ManagementApi(repository)
            try:
                if completed:
                    saved = models.strict_json_loads(private_read(path))
                    return _validate_bootstrap(
                        spec, output, saved, repository, service, public, evidence_key
                    )
                expected = {
                    "spec_digest": spec_digest(spec),
                    "snapshot_public_digest": hashlib.sha256(public).hexdigest(),
                    "evidence_public_digest": hashlib.sha256(evidence_key).hexdigest(),
                }
                if plan_path.exists():
                    plan = _BootstrapPlan.model_validate_json(private_read(plan_path))
                    if any(
                        getattr(plan, name) != value for name, value in expected.items()
                    ):
                        raise EvolutionError("idempotency_conflict")
                else:
                    if (output / "platform.token").exists():
                        raise EvolutionError("idempotency_conflict")
                    now = datetime.now(UTC)
                    plan = _BootstrapPlan(
                        **expected,
                        principal_id=str(uuid4()),
                        credential_id=str(uuid4()),
                        created_at=now.isoformat().replace("+00:00", "Z"),
                        expires_at=(now + timedelta(hours=23))
                        .isoformat()
                        .replace("+00:00", "Z"),
                    )
                    private_write(plan_path, plan)
                if models.parse_time(plan.expires_at) <= datetime.now(UTC):
                    raise EvolutionError("authentication_failed")
                token_file = output / "platform.token"
                marker = plan.model_dump(mode="json")
                with repository.transaction("_platform") as tx:
                    tx.lock("deployment-bootstrap:" + plan.principal_id)
                    stored = tx.get("deployment_bootstrap", plan.principal_id)
                    principal = tx.get("principals", plan.principal_id)
                    credential = tx.get("credentials", plan.credential_id)
                    if (
                        stored is not None
                        and {key: stored.get(key) for key in marker} != marker
                        or stored is None
                        and (principal is not None or credential is not None)
                    ):
                        raise EvolutionError("idempotency_conflict")
                    if token_file.exists():
                        token = private_read(token_file, maximum=4096).decode()
                    elif stored is not None:
                        raise EvolutionError("idempotency_conflict")
                    else:
                        token = f"gr2._platform.{plan.credential_id}.{secrets.token_urlsafe(32)}"
                        private_write(token_file, token.encode())
                    if not re.fullmatch(
                        r"gr2\._platform\."
                        + re.escape(plan.credential_id)
                        + r"\.[A-Za-z0-9_-]{43}",
                        token,
                    ):
                        raise EvolutionError("idempotency_conflict")
                    expected_credential = models.Credential(
                        credential_id=plan.credential_id,
                        principal_id=plan.principal_id,
                        tenant_id=None,
                        token_hash=hashlib.sha256(token.encode()).hexdigest(),
                        created_at=plan.created_at,
                        expires_at=plan.expires_at,
                    ).model_dump(mode="json")
                    if stored is not None:
                        if (
                            principal is None
                            or principal.get("status") != "active"
                            or principal.get("roles") != ["platform_admin"]
                            or principal.get("tenant_id") is not None
                            or credential is None
                            or any(
                                credential.get(name) != value
                                for name, value in expected_credential.items()
                            )
                        ):
                            raise EvolutionError("idempotency_conflict")
                    else:
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
                        service.identity.provision_principal(
                            tx,
                            "Deployment bootstrap",
                            ["platform_admin"],
                            principal_id=plan.principal_id,
                        )
                        # Deployment already has bootstrap authority. Persist the hash of
                        # the durable one-time token, so transaction rollback is replayable.
                        tx.insert(
                            "credentials", plan.credential_id, expected_credential
                        )
                        service.audit.append(
                            tx,
                            "principal_created",
                            actor_id=plan.principal_id,
                            metadata={"operation": "deployment_bootstrap"},
                        )
                        tx.insert(
                            "deployment_bootstrap",
                            plan.principal_id,
                            marker,
                            immutable=True,
                        )
                _evidence_root(
                    spec, repository, service, token, evidence_key, create=True
                )
                result = {
                    "spec_digest": spec_digest(spec),
                    "platform_principal_id": plan.principal_id,
                    "platform_token_file": str(token_file),
                    "snapshot_private_key_file": str(output / "snapshot-signing.key"),
                    "snapshot_public_key_file": str(output / "snapshot-signing.pub"),
                    "snapshot_key_id": hashlib.sha256(public).hexdigest()[:16],
                    "credential_expires_at": plan.expires_at,
                }
                _validate_bootstrap(
                    spec, output, result, repository, service, public, evidence_key
                )
                private_write(path, result)
                return result
            finally:
                service.audit.close()
                repository.close()
    except OSError, ValueError, TypeError, KeyError:
        raise EvolutionError("validation_failed") from None


class SeedClient:
    def __init__(self, spec, output, bootstrap_record):
        self.spec, self.output, self.bootstrap = spec, output, bootstrap_record
        self.path = output / "operations.json"
        self.operations = (
            models.strict_json_loads(private_read(self.path))
            if self.path.exists()
            else {}
        )
        self.client = BoundedJsonClient(
            base_url=spec.management_url,
            verify=ssl.create_default_context(cafile=spec.ca_file),
            deadline=time.monotonic(),
            max_bytes=2 * 1024 * 1024,
        )
        self.last_request = {}
        self.deadline = None

    def close(self):
        pass  # BoundedJsonClient owns a one-shot connection for each operation.

    def save(self):
        private_write(self.path, self.operations, replace=self.path.exists())

    def request(self, method, path, token_file, *, body=None, key=None):
        # Respect management's ten-per-second burst without retrying secret issuance.
        pause = 0.12 - (time.monotonic() - self.last_request.get(str(token_file), 0))
        if pause > 0:
            if self.deadline is not None and time.monotonic() + pause >= self.deadline:
                raise EvolutionError("snapshot_stale")
            time.sleep(pause)
        token = private_read(token_file, maximum=4096).decode().strip()
        headers = {"Authorization": "Bearer " + token, "Accept-Encoding": "identity"}
        if key:
            headers["Idempotency-Key"] = key
        self.last_request[str(token_file)] = time.monotonic()
        self.client.deadline = min(time.monotonic() + 10, self.deadline or float("inf"))
        response = self.client.request(method, path, json=body, headers=headers)
        if response.status_code not in {200, 201, 202}:
            code = {
                401: "authentication_failed",
                403: "forbidden",
                404: "resource_not_found",
                409: "revision_conflict",
                429: "quota_exceeded",
            }.get(response.status_code, "validation_failed")
            raise EvolutionError(code)
        return models.strict_json_loads(response.content)

    def post(self, name, path, body, token_file, *, secret_name=None):
        try:
            with _seed_lock(self.output):
                self.operations = (
                    models.strict_json_loads(private_read(self.path))
                    if self.path.exists()
                    else {}
                )
                return self._post(name, path, body, token_file, secret_name=secret_name)
        except OSError, ValueError, TypeError, KeyError:
            raise EvolutionError("validation_failed") from None

    def _recover_secret(self, previous, path, body, token_file, secret_path):
        # A one-time response cannot be requested again. A durable token can be
        # reconciled only with the exact committed public idempotency receipt.
        parts = path.split("/")
        if (
            len(parts) != 7
            or parts[:3] != ["", "v2", "tenants"]
            or parts[4] != "principals"
            or parts[6] != "credentials"
        ):
            raise EvolutionError("idempotency_conflict")
        repository = TenantRepository(
            private_read(self.spec.database_url_file, maximum=4096).decode().strip()
        )
        try:
            identity = IdentityService(repository)
            actor = identity.authenticate(
                private_read(token_file, maximum=4096).decode().strip()
            )
            identity.authorize(
                actor, {"tenant_admin", "platform_admin"}, tenant_id=parts[3]
            )
            token = private_read(secret_path, maximum=4096).decode()
            key = f"{actor.principal_id}:POST:{path}:{previous['key']}"
            if not token:
                with repository.transaction(parts[3]) as tx:
                    tx.lock("idempotency:" + key)
                    if tx.get("idempotency", key) is not None:
                        # The only secret was lost after admission. Hash-only
                        # storage cannot recover it; never issue a replacement.
                        raise EvolutionError("idempotency_conflict")
                return None
            issued = identity.authenticate(token)
            if (
                issued.tenant_id != parts[3]
                or issued.principal_id != parts[5]
                or issued.workload_id != body.get("workload_id")
            ):
                raise EvolutionError("idempotency_conflict")
            with repository.transaction(parts[3]) as tx:
                tx.lock("idempotency:" + key)
                receipt = tx.get("idempotency", key)
                credential = tx.get(
                    "credentials",
                    issued.credential["credential_id"],
                    required=True,
                    for_update=True,
                )
                if (
                    receipt is None
                    or receipt.get("fingerprint")
                    != hashlib.sha256(models.canonical_json_bytes(body)).hexdigest()
                    or receipt.get("secret_delivered") is not True
                    or receipt.get("status") != 201
                    or not hmac.compare_digest(
                        credential["token_hash"],
                        hashlib.sha256(token.encode()).hexdigest(),
                    )
                    or credential.get("revoked_at") is not None
                ):
                    raise EvolutionError("idempotency_conflict")
                result = receipt.get("response")
                expected = {
                    "credential_id": credential["credential_id"],
                    "expires_at": credential["expires_at"],
                }
                if (
                    result != expected
                    or body.get("expires_at", credential["expires_at"])
                    != credential["expires_at"]
                ):
                    raise EvolutionError("idempotency_conflict")
            return {**expected, "token_file": str(secret_path)}
        finally:
            repository.close()

    def _post(self, name, path, body, token_file, *, secret_name=None):
        if secret_name is not None and (
            not isinstance(secret_name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.token", secret_name)
        ):
            raise EvolutionError("validation_failed")
        secret_path = self.output / secret_name if secret_name else None
        fingerprint = hashlib.sha256(
            models.canonical_json_bytes({"path": path, "body": body})
        ).hexdigest()
        previous = self.operations.get(name)
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise EvolutionError("idempotency_conflict")
            if "result" in previous:
                if (
                    secret_name
                    and self._recover_secret(
                        previous, path, body, token_file, secret_path
                    )
                    != previous["result"]
                ):
                    raise EvolutionError("idempotency_conflict")
                return previous["result"]
            if secret_path and secret_path.exists():
                recovered = self._recover_secret(
                    previous, path, body, token_file, secret_path
                )
                if recovered is not None:
                    previous["result"] = recovered
                    self.save()
                    return recovered
                # A crashed process reserved an empty private file before any
                # committed request. Retry the same key, never a new issuance.
                secret_path.unlink()
        else:
            previous = self.operations[name] = {
                "key": str(uuid4()),
                "fingerprint": fingerprint,
            }
            self.save()
        descriptor = None
        if secret_path:
            descriptor = os.open(
                secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
        try:
            result = self.request(
                "POST", path, token_file, body=body, key=previous["key"]
            )
            if descriptor is not None:
                secret = result.pop("one_time_secret")
                with os.fdopen(descriptor, "wb") as stream:
                    descriptor = None
                    stream.write(secret.encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                result["token_file"] = str(secret_path)
            elif "one_time_secret" in result:
                raise EvolutionError("validation_failed")
            previous["result"] = result
            self.save()
            return result
        finally:
            if descriptor is not None:
                os.close(descriptor)
                if secret_path.stat().st_size == 0:
                    secret_path.unlink()


def reference(value, field):
    return {"id": value[field], "version": value["version"], "digest": value["digest"]}


def configure(spec: SeedSpec, output):
    output = output_directory(output)
    bootstrap_record = models.strict_json_loads(private_read(output / "bootstrap.json"))
    if bootstrap_record["spec_digest"] != spec_digest(spec):
        raise EvolutionError("idempotency_conflict")
    client = SeedClient(spec, output, bootstrap_record)
    repository = TenantRepository(
        private_read(spec.database_url_file, maximum=4096).decode().strip()
    )
    service = ManagementApi(repository)
    enrollment = RuntimeEnrollment(repository, service.identity, service.audit)
    inventory = {
        "spec_digest": spec_digest(spec),
        "snapshot_key_id": bootstrap_record["snapshot_key_id"],
        "snapshot_public_key_file": bootstrap_record["snapshot_public_key_file"],
        "tenants": {},
    }
    try:
        for tenant in spec.tenants:
            prefix, label = "/v2/tenants/" + tenant, tenant + ":"
            platform_file = bootstrap_record["platform_token_file"]
            created = client.post(
                label + "tenant",
                "/v2/tenants",
                {"tenant_id": tenant, "display_name": tenant},
                platform_file,
            )
            roles = {}
            for role in (
                "tenant_admin",
                "author",
                "approver",
                "operator",
                "auditor",
                "workload",
                "runtime-1",
                "runtime-2",
            ):
                actor_file = (
                    platform_file
                    if role == "tenant_admin"
                    else roles["tenant_admin"]["token_file"]
                )
                actual_role = "runtime" if role.startswith("runtime-") else role
                actor = client.post(
                    label + role + ":principal",
                    prefix + "/principals",
                    {"display_name": role, "roles": [actual_role]},
                    actor_file,
                )
                roles[role] = {"principal_id": actor["principal_id"]}
                if role == "workload":
                    continue
                credential = client.post(
                    label + role + ":credential",
                    prefix + "/principals/" + actor["principal_id"] + "/credentials",
                    {"expires_at": bootstrap_record["credential_expires_at"]},
                    actor_file,
                    secret_name=tenant + "-" + role + ".token",
                )
                roles[role].update(credential)
            admin, author, approver, operator = (
                roles[role]["token_file"]
                for role in ("tenant_admin", "author", "approver", "operator")
            )
            destinations, providers = {}, {}
            for profile, endpoint in (("mock", spec.mock), ("ollama", spec.ollama)):
                destinations[profile] = client.post(
                    label + profile + ":destination",
                    prefix + "/destinations",
                    {
                        "name": profile + "-model",
                        "host": endpoint.host,
                        "port": endpoint.port,
                        "path_prefix": endpoint.path_prefix,
                        "cidrs": list(endpoint.cidrs()),
                        "zone": "local-shared",
                        "purpose": "inference",
                        "tls_ca_ref": endpoint.tls_ca_ref,
                    },
                    admin,
                )
                providers[profile] = client.post(
                    label + profile + ":provider",
                    prefix + "/providers",
                    {
                        "kind": "model",
                        "vendor": "ollama",
                        "model_id": endpoint.model_id,
                        "destination_id": destinations[profile]["destination_id"],
                        "zone": "local-shared",
                    },
                    admin,
                )
            lists = {}
            for kind, resources, identity in (
                ("destination", destinations, "destination_id"),
                ("provider", providers, "provider_id"),
            ):
                lists[kind] = client.post(
                    label + kind + ":list",
                    prefix + "/lists",
                    {
                        "kind": kind,
                        "allow": [item[identity] for item in resources.values()],
                        "allow_enabled": True,
                        "deny": [],
                    },
                    author,
                )
            residency = client.post(
                label + "residency",
                prefix + "/residency/" + created["residency_id"] + "/versions",
                {
                    "allowed_zones": ["local-shared"],
                    "destination_lists": [lists["destination"]["list_id"]],
                    "provider_lists": [lists["provider"]["list_id"]],
                },
                admin,
            )
            quota = client.post(
                label + "quota",
                prefix + "/quotas/" + created["quota_id"] + "/versions",
                {"requests_per_minute": 1000, "burst": 100, "max_inflight": 4},
                admin,
            )
            workload = client.post(
                label + "workload",
                prefix + "/workloads",
                {
                    "workload_id": spec.workload_id,
                    "display_name": "Protected chat",
                    "mode": "remote",
                    "allowed_stages": ["input", "output"],
                    "provider_ids": [
                        item["provider_id"] for item in providers.values()
                    ],
                    "quota_id": quota["quota_id"],
                    "required_state": "disabled",
                },
                admin,
            )
            actor = roles["workload"]
            actor.update(
                client.post(
                    label + "workload:credential",
                    prefix + "/principals/" + actor["principal_id"] + "/credentials",
                    {
                        "expires_at": bootstrap_record["credential_expires_at"],
                        "workload_id": spec.workload_id,
                    },
                    admin,
                    secret_name=tenant + "-workload.token",
                )
            )
            runtimes = {}
            for index in (1, 2):
                role = "runtime-" + str(index)
                install = client.post(
                    label + role + ":install",
                    prefix + "/runtimes",
                    {
                        "zone": "local-shared",
                        "mode": "remote",
                        "telemetry": "none",
                        "version": "phase2-text",
                        "offline_max_age_s": 60,
                    },
                    operator,
                )
                with repository.transaction(tenant) as tx:
                    existing = tx.get("runtime_enrollments", install["runtime_id"])
                if existing is None:
                    enrollment.enroll(
                        private_read(operator, maximum=4096).decode(),
                        runtime_id=install["runtime_id"],
                        principal_id=roles[role]["principal_id"],
                        workload_ids=[spec.workload_id],
                        expected_runtime_revision=1,
                        expected_target_revisions={spec.workload_id: index - 1},
                    )
                elif existing["principal_id"] != roles[role][
                    "principal_id"
                ] or existing["workload_ids"] != [spec.workload_id]:
                    raise EvolutionError("revision_conflict")
                runtimes[role] = {"runtime_id": install["runtime_id"], **roles[role]}
            payload = {
                "rules": [
                    {
                        "rule_id": "pii",
                        "kind": "pii",
                        "stages": ["input", "output"],
                        "action": "block",
                        "mandatory": True,
                        "minimum_state": "observe",
                        "config": {"entities": ["email", "international_phone"]},
                        "evaluator_revision": "pii-v2",
                    }
                ],
                "residency_ref": reference(residency, "residency_id"),
                "quota_ref": reference(quota, "quota_id"),
                "list_refs": [reference(value, "list_id") for value in lists.values()],
                "provider_refs": [
                    reference(value, "provider_id") for value in providers.values()
                ],
                "destination_refs": [
                    reference(value, "destination_id")
                    for value in destinations.values()
                ],
            }
            policy = client.post(
                label + "policy",
                prefix + "/policies/" + spec.policy_id + "/versions",
                payload,
                author,
            )
            policy_ref = {
                "tenant_id": tenant,
                "policy_id": spec.policy_id,
                "version": policy["version"],
                "digest": policy["compiled_digest"],
            }
            client.post(
                label + "approval",
                prefix
                + "/policies/"
                + spec.policy_id
                + "/versions/"
                + str(policy["version"])
                + "/reviews",
                {
                    "outcome": "approve",
                    "compiled_digest": policy["compiled_digest"],
                    "reason": "Reviewed pinned local PII policy",
                },
                approver,
            )
            approved = client.request(
                "GET",
                prefix
                + "/policies/"
                + spec.policy_id
                + "/versions/"
                + str(policy["version"]),
                operator,
            )["policy"]
            if (
                approved["status"] != "approved"
                or approved["compiled_digest"] != policy_ref["digest"]
            ):
                raise EvolutionError("approval_required")
            identity = service.identity.authenticate(
                private_read(admin, maximum=4096).decode()
            )
            with repository.transaction(tenant) as tx:
                current = tx.get("baseline", "current")
                if current is None:
                    service.governance.publish_baseline(tx, identity, [policy_ref], 0)
                elif current["policies"] != [policy_ref]:
                    raise EvolutionError("revision_conflict")
            release = client.post(
                label + "observe",
                prefix + "/releases",
                {
                    "policy": policy_ref,
                    "workload_ids": [spec.workload_id],
                    "stage": "canary",
                    "state": "observe",
                    "expected_revisions": {spec.workload_id: 0},
                    "reason": "Initial Observe onboarding",
                },
                operator,
            )
            profiles = {}
            for profile, endpoint in (("mock", spec.mock), ("ollama", spec.ollama)):
                provider = next(
                    item
                    for item in approved["providers"]
                    if item["provider_id"] == providers[profile]["provider_id"]
                )
                destination = next(
                    item
                    for item in approved["resolved_destinations"]
                    if item["destination_id"] == provider["destination_id"]
                )
                manifest = {
                    "tenant_id": tenant,
                    "workload_id": spec.workload_id,
                    "routes": [
                        {
                            "policy": policy_ref,
                            "provider": provider,
                            "destination": destination,
                            "external_processing": False,
                        }
                    ],
                    "ca_files": {destination["tls_ca_ref"]: spec.ca_file},
                    "local_services": {
                        destination["destination_id"]: {
                            "hostname": destination["host"],
                            "addresses": list(endpoint.addresses),
                        }
                    },
                }
                manifest_file = output / (tenant + "-" + profile + "-routes.json")
                if manifest_file.exists():
                    if (
                        models.strict_json_loads(private_read(manifest_file))
                        != manifest
                    ):
                        raise EvolutionError("idempotency_conflict")
                else:
                    private_write(manifest_file, manifest)
                profiles[profile] = {
                    "provider": provider,
                    "destination": destination,
                    "route_manifest_file": str(manifest_file),
                }
            gateway_token = output / (tenant + "-gateway.token")
            if gateway_token.exists():
                private_read(gateway_token, maximum=4096)
            else:
                private_write(gateway_token, secrets.token_urlsafe(32).encode())
            inventory["tenants"][tenant] = {
                "roles": roles,
                "runtimes": runtimes,
                "gateway_token_file": str(gateway_token),
                "workload": workload,
                "policy": approved,
                "policy_ref": policy_ref,
                "policy_payload": payload,
                "profiles": profiles,
                "release_id": release["release_id"],
            }
            private_write(
                output / "inventory.json",
                inventory,
                replace=(output / "inventory.json").exists(),
            )
        return inventory
    finally:
        client.close()
        service.audit.close()
        repository.close()


def wait_applied(spec: SeedSpec, output, *, timeout=120):
    output = output_directory(output)
    inventory = models.strict_json_loads(private_read(output / "inventory.json"))
    bootstrap_record = models.strict_json_loads(private_read(output / "bootstrap.json"))
    if (
        inventory["spec_digest"] != spec_digest(spec)
        or set(inventory["tenants"]) != set(spec.tenants)
        or type(timeout) is not int
        or not 1 <= timeout <= 600
    ):
        raise EvolutionError("validation_failed")
    client = SeedClient(spec, output, bootstrap_record)
    repository = TenantRepository(
        private_read(spec.database_url_file, maximum=4096).decode().strip()
    )
    service = ManagementApi(repository)
    deadline = time.monotonic() + timeout
    client.deadline = deadline
    deadline_token = current_deadline.set(deadline)
    try:
        while True:
            if time.monotonic() >= deadline:
                raise EvolutionError("snapshot_stale")
            complete = True
            for tenant, value in inventory["tenants"].items():
                record = client.request(
                    "GET",
                    "/v2/tenants/" + tenant + "/releases/" + value["release_id"],
                    value["roles"]["operator"]["token_file"],
                )
                release = record["release"]
                if release["status"] == "failed":
                    raise EvolutionError("policy_unresolved")
                expected = {row["runtime_id"] for row in value["runtimes"].values()}
                receipts = [
                    row
                    for row in record["receipts"]
                    if row["workload_id"] == spec.workload_id
                    and row["binding_revision"] == 1
                    and row["digest"] == value["policy_ref"]["digest"]
                    and row["health"] == "ready"
                ]
                if (
                    release["status"] != "applied"
                    or {row["runtime_id"] for row in receipts} != expected
                ):
                    complete = False
                    continue
                # An applied projection is history. Prove the enrolled installs,
                # current desired revision and signed leases are still eligible.
                actor = service.identity.authenticate(
                    private_read(
                        value["roles"]["operator"]["token_file"], maximum=4096
                    ).decode()
                )
                service.identity.authorize(actor, ["operator"], tenant_id=tenant)
                with repository.transaction(tenant) as tx:
                    lock_admission(tx)
                    service.governance.eligible(tx, value["policy_ref"])
                    binding = tx.get("bindings", spec.workload_id + ":1", required=True)
                    if (
                        binding["policy"] != value["policy_ref"]
                        or binding["state"] != "observe"
                        or not service.bindings.binding_ready(tx, binding)
                    ):
                        complete = False
            if complete:
                private_write(
                    output / "applied.json",
                    {
                        "spec_digest": spec_digest(spec),
                        "status": "applied",
                        "releases": {
                            tenant: value["release_id"]
                            for tenant, value in inventory["tenants"].items()
                        },
                    },
                    replace=(output / "applied.json").exists(),
                )
                return {"status": "applied", "tenant_count": 2, "runtime_count": 4}
            if time.monotonic() >= deadline:
                raise EvolutionError("snapshot_stale")
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    except httpx.TimeoutException:
        raise EvolutionError("snapshot_stale") from None
    finally:
        current_deadline.reset(deadline_token)
        client.close()
        service.audit.close()
        repository.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("schema", "bootstrap", "configure", "wait"))
    parser.add_argument("--spec")
    parser.add_argument("--output")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    if args.stage == "schema":
        print(models.canonical_json_bytes(SeedSpec.model_json_schema()).decode())
        return 0
    try:
        if not args.spec or not args.output:
            raise EvolutionError("validation_failed")
        spec = SeedSpec.model_validate_json(private_read(args.spec, maximum=65536))
        if args.stage == "bootstrap":
            bootstrap(spec, args.output)
        elif args.stage == "configure":
            configure(spec, args.output)
        else:
            wait_applied(spec, args.output, timeout=args.timeout)
        print(
            models.canonical_json_bytes(
                {"status": "pass", "stage": args.stage}
            ).decode()
        )
        return 0
    except Exception as error:  # noqa: BLE001 - deployment never prints driver/credential details
        print(
            models.canonical_json_bytes(
                {
                    "status": "fail",
                    "stage": args.stage,
                    "code": error.code
                    if isinstance(error, EvolutionError)
                    else "validation_failed",
                }
            ).decode()
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
