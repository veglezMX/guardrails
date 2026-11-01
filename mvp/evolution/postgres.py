"""Task-owned PostgreSQL fixture with pinned image and separate serving role.

No registry access occurs here. Dependency preparation must acquire the exact
image first. Existing containers are reused only when ownership labels match.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from .dependencies import ROOT, verify_runtime_artifacts
from .registry import EvidenceError


def command(*args):
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=60
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


def prepare(root: Path = ROOT):
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
        settings = json.loads(settings_path.read_text())
        if (
            settings["container"] != name
            or settings["owner"] != identity
            or settings["image"] != image["sha256"]
        ):
            raise EvidenceError("postgres_fixture_identity_mismatch")
        info = json.loads(command("inspect", name))[0]
        if info["Config"]["Labels"].get("guardrails.evolution.owner") != identity:
            raise EvidenceError("postgres_fixture_ownership_mismatch")
        if info["Config"]["Image"] != "sha256:" + image["sha256"]:
            raise EvidenceError("postgres_fixture_image_mismatch")
        if not info["State"]["Running"]:
            command("start", name)
        app_file, owner_file = state / "app-dsn", state / "owner-dsn"
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
