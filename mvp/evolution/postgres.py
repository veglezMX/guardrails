"""Task-owned PostgreSQL fixture with pinned image and separate serving role.

No registry access occurs here. Dependency preparation must acquire the exact
image first. Existing containers are reused only when ownership labels match.
"""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, unquote

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from .dependencies import ROOT, verify_runtime_artifacts
from .registry import EvidenceError


def command(*args):
    result = subprocess.run(
        ["docker", "--host", "unix:///var/run/docker.sock", *args], capture_output=True, text=True, timeout=60
    )
    if result.returncode:
        raise EvidenceError("postgres_container_operation_failed")
    return result.stdout


def private_write(path, value):
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as output:
        output.write(value)
        output.flush()
        os.fsync(output.fileno())


def private_read(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or not 0 < before.st_size <= 16384
        ):
            raise EvidenceError("postgres_fixture_private_file_invalid")
        raw = source.read(16385)
        def stable(metadata):
            return (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid,
                    metadata.st_nlink, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)
        if stable(os.fstat(source.fileno())) != stable(before) or stable(path.lstat()) != stable(before) or len(raw) != before.st_size:
            raise EvidenceError("postgres_fixture_private_file_changed")
        return raw


def private_replace(path, before, after):
    """Replace one private file durably; callers hold the preparation lock."""
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(after)
            output.flush()
            os.fsync(output.fileno())
        if private_read(path) != before:
            raise EvidenceError("postgres_fixture_private_file_changed")
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def owned_container(name, identity, image, container_id=None, *, running=False):
    rows = json.loads(command("inspect", container_id or name))
    if not isinstance(rows, list) or len(rows) != 1:
        raise EvidenceError("postgres_fixture_identity_mismatch")
    info = rows[0]
    if (
        not re.fullmatch(r"[0-9a-f]{64}", info.get("Id", ""))
        or info.get("Name") != "/" + name
        or (container_id is not None and info["Id"] != container_id)
    ):
        raise EvidenceError("postgres_fixture_identity_mismatch")
    if (info.get("Config", {}).get("Labels") or {}).get("guardrails.evolution.owner") != identity:
        raise EvidenceError("postgres_fixture_ownership_mismatch")
    if info["Config"].get("Image") != "sha256:" + image or info.get("Image") != "sha256:" + image:
        raise EvidenceError("postgres_fixture_image_mismatch")
    if (
        type(info.get("State", {}).get("Running")) is not bool
        or any(info["State"].get(key, False) is not False for key in ("Paused", "Restarting", "Dead"))
    ):
        raise EvidenceError("postgres_fixture_state_invalid")
    if running and info.get("State", {}).get("Running") is not True:
        raise EvidenceError("postgres_fixture_not_running")
    return info


def published_port(info):
    bindings = info.get("NetworkSettings", {}).get("Ports", {}).get("5432/tcp")
    if (
        not isinstance(bindings, list)
        or len(bindings) != 1
        or not isinstance(bindings[0], dict)
        or bindings[0].get("HostIp") != "127.0.0.1"
        or not isinstance(bindings[0].get("HostPort"), str)
        or not re.fullmatch(r"[1-9][0-9]{0,4}", bindings[0]["HostPort"])
        or int(bindings[0]["HostPort"]) > 65535
    ):
        raise EvidenceError("postgres_fixture_binding_invalid")
    return int(bindings[0]["HostPort"])


def current_dsn(raw, role, old_port, port):
    # These are producer-owned URLs, not arbitrary libpq routing instructions.
    match = re.fullmatch(
        rb"postgresql\+psycopg://" + role.encode() +
        rb":(?P<password>[A-Za-z0-9._~%\-]+)@127\.0\.0\.1:(?P<port>[1-9][0-9]{0,4})/evolution(?P<newline>\n?)",
        raw,
    )
    if match is None or int(match["port"]) not in {old_port, port}:
        raise EvidenceError("postgres_fixture_dsn_invalid")
    encoded = match["password"].decode("ascii")
    try:
        password = unquote(encoded, errors="strict")
    except UnicodeError:
        raise EvidenceError("postgres_fixture_dsn_invalid") from None
    if quote(password, safe="") != encoded or any(ord(char) < 32 or ord(char) == 127 for char in password):
        raise EvidenceError("postgres_fixture_dsn_invalid")
    start, end = match.span("port")
    return raw[:start] + str(port).encode("ascii") + raw[end:]


def verify_access(dsns):
    # No ambient hostaddr/service may redirect the explicitly inspected endpoint.
    if any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")):
        raise EvidenceError("postgres_fixture_routing_override")
    deadline = time.monotonic() + 30
    for role, raw in dsns:
        engine = create_engine(
            raw.decode("ascii").strip(), hide_parameters=True,
            connect_args={"connect_timeout": 2, "options": "-c default_transaction_read_only=on -c statement_timeout=2000 -c lock_timeout=2000"},
        )
        try:
            while True:
                try:
                    with engine.connect() as connection:
                        observed = connection.execute(text(
                            "SELECT current_user, current_database(), pg_is_in_recovery(), current_setting('transaction_read_only')"
                        )).one()
                    if tuple(observed) != (role, "evolution", False, "on"):
                        raise EvidenceError("postgres_fixture_access_mismatch")
                    break
                except OperationalError:
                    if time.monotonic() >= deadline:
                        raise EvidenceError("postgres_fixture_startup_timeout") from None
                    time.sleep(0.1)
        except SQLAlchemyError:
            raise EvidenceError("postgres_fixture_access_failed") from None
        finally:
            engine.dispose()


def unique_settings(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise EvidenceError("postgres_fixture_settings_invalid")
    return result


def reconcile_existing(state, name, identity, image):
    settings_path = state / "instance.json"
    original = private_read(settings_path)
    settings = json.loads(original, object_pairs_hook=unique_settings)
    if (
        not isinstance(settings, dict)
        or set(settings) != {"container", "owner", "image", "port"}
        or settings["container"] != name
        or settings["owner"] != identity
        or settings["image"] != image
        or type(settings["port"]) is not int
        or not 1 <= settings["port"] <= 65535
    ):
        raise EvidenceError("postgres_fixture_identity_mismatch")
    info = owned_container(name, identity, image)
    container_id = info["Id"]
    if info.get("State", {}).get("Running") is not True:
        command("start", container_id)
    info = owned_container(name, identity, image, container_id, running=True)
    port = published_port(info)
    changes, dsns = [], []
    for filename, role in (("owner-dsn", "evolution_owner"), ("app-dsn", "evolution_app")):
        path = state / filename
        before = private_read(path)
        after = current_dsn(before, role, settings["port"], port)
        changes.append((path, before, after))
        dsns.append((role, after))
    verify_access(dsns)
    if published_port(owned_container(name, identity, image, container_id, running=True)) != port:
        raise EvidenceError("postgres_fixture_binding_changed")
    # Recheck the whole snapshot before the first write. DSNs first/settings last
    # makes a crash recoverable: URLs may contain only the saved or current port.
    for path, before, _ in changes + [(settings_path, original, original)]:
        if private_read(path) != before:
            raise EvidenceError("postgres_fixture_private_file_changed")
    for path, before, after in changes:
        if before != after:
            private_replace(path, before, after)
    if settings["port"] != port:
        settings["port"] = port
        private_replace(settings_path, original, json.dumps(settings, sort_keys=True).encode())
    return state / "app-dsn", state / "owner-dsn"


def prepare(root: Path = ROOT):
    if (
        os.environ.get("DOCKER_HOST", "") not in ("", "unix:///var/run/docker.sock")
        or any(os.environ.get(key) for key in ("DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"))
    ):
        raise EvidenceError("postgres_fixture_docker_override")
    state = root / ".evolution/postgres"
    if state.resolve() != root.resolve() / ".evolution/postgres":
        raise EvidenceError("postgres_fixture_directory_invalid")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = state.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700 or metadata.st_uid != os.getuid():
        raise EvidenceError("postgres_fixture_directory_invalid")
    descriptor = os.open(state / ".prepare.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
            raise EvidenceError("postgres_fixture_lock_invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EvidenceError("postgres_fixture_preparation_busy") from None
        return _prepare(root)
    finally:
        os.close(descriptor)


def _prepare(root: Path):
    artifacts = verify_runtime_artifacts(root)
    image = next((item for item in artifacts if item["name"] == "postgres"), None)
    if image is None:
        raise EvidenceError("postgres_image_pin_missing")
    identity = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    name = "guardrails-evolution-db-" + identity
    state = root / ".evolution/postgres"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings_path = state / "instance.json"
    if settings_path.exists():
        app_file, owner_file = reconcile_existing(state, name, identity, image["sha256"])
    else:
        owner_password, app_password = (
            secrets.token_urlsafe(32),
            secrets.token_urlsafe(32),
        )
        env_path = state / "postgres.env"
        private_write(
            env_path,
            "POSTGRES_USER=evolution_owner\nPOSTGRES_DB=evolution\nPOSTGRES_PASSWORD="
            + owner_password
            + "\n",
        )
        command(
            "run",
            "--detach",
            "--pull=never",
            "--name",
            name,
            "--label",
            "guardrails.evolution.owner=" + identity,
            "--env-file",
            str(env_path),
            "--publish",
            "127.0.0.1::5432",
            "sha256:" + image["sha256"],
        )
        info = json.loads(command("inspect", name))[0]
        port = int(info["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"])
        owner_dsn = f"postgresql+psycopg://evolution_owner:{quote(owner_password, safe='')}@127.0.0.1:{port}/evolution"
        app_dsn = f"postgresql+psycopg://evolution_app:{quote(app_password, safe='')}@127.0.0.1:{port}/evolution"
        owner_file, app_file = state / "owner-dsn", state / "app-dsn"
        private_write(owner_file, owner_dsn)
        private_write(app_file, app_dsn)
        engine = create_engine(owner_dsn, hide_parameters=True)
        deadline = time.monotonic() + 30
        while True:
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                break
            except OperationalError:
                if time.monotonic() >= deadline:
                    raise EvidenceError("postgres_fixture_startup_timeout") from None
                time.sleep(0.1)
        from guardrails.evolution.storage import migrate

        migrate(owner_dsn)
        with engine.begin() as connection:
            # Password comes from our ASCII URL-safe generator, never user SQL.
            connection.exec_driver_sql(
                "CREATE ROLE evolution_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD '"
                + app_password
                + "'"
            )
            connection.execute(text("GRANT USAGE ON SCHEMA evolution TO evolution_app"))
            connection.execute(
                text(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA evolution TO evolution_app"
                )
            )
            connection.execute(
                text(
                    "GRANT EXECUTE ON FUNCTION evolution.tenant_scope() TO evolution_app"
                )
            )
        engine.dispose()
        settings = {
            "container": name,
            "owner": identity,
            "image": image["sha256"],
            "port": port,
        }
        private_write(settings_path, json.dumps(settings, sort_keys=True))
    from guardrails.evolution.storage import TenantRepository, migrate

    # Explicit fixture preparation also upgrades an existing task database.
    # Serving connections never receive this separately stored owner credential.
    # The migration is idempotent and preserves existing records and checkpoints.
    migrate(owner_file.read_text().strip())

    repository = TenantRepository(app_file.read_text().strip())
    repository.close()
    return {
        "EVOLUTION_TEST_DSN_FILE": str(app_file),
        "EVOLUTION_TEST_OWNER_DSN_FILE": str(owner_file),
    }


if __name__ == "__main__":
    try:
        result = prepare()
        print(
            json.dumps(
                {
                    "database_ready": True,
                    "application_dsn_file": result["EVOLUTION_TEST_DSN_FILE"],
                }
            )
        )
    except EvidenceError, OSError, ValueError, KeyError, SQLAlchemyError:
        print(
            json.dumps(
                {"database_ready": False, "reason": "postgres_preparation_failed"}
            )
        )
        raise SystemExit(1)
