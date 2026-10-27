"""One fresh, retained platform-test database on the existing owned fixture.

Never starts/recreates a container, changes the ordinary fixture database, drops
history, or retries a CREATE whose outcome is unknown. The caller supplies an
explicit durable operation root and the exact acceptance Python source closure.
"""
from __future__ import annotations

import fcntl
import hashlib
import importlib
import json
import os
import re
import signal
import stat
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url

from . import postgres
from .diagnostics import SourceCatalog, validate_catalog
from .registry import EvidenceError, unique_object

NODE = "mvp/tests/evolution/integration/test_published_platform_lifecycle.py::test_published_platform_governance_signed_https_and_commit_order"
ENV = "EVOLUTION_PLATFORM_TEST_DSN_FILE"
PREFIX = "evolution_platform_"
STORAGE_MODULES = ("guardrails.evolution.storage", *[
    "guardrails.evolution.migrations.versions." + name for name in
    ("0001_shared_state", "0002_config_identity", "0003_quota_state")])
REQUIRED_SOURCES = frozenset({
    "mvp/evolution/platform_database.py", "mvp/evolution/postgres.py",
    "mvp/evolution/run.py", "mvp/guardrails/evolution/storage.py", NODE.split("::")[0],
    *[f"mvp/guardrails/evolution/migrations/versions/{name}.py" for name in
      ("0001_shared_state", "0002_config_identity", "0003_quota_state")],
})


def require(condition, code="platform_database_invalid"):
    if not condition:
        raise EvidenceError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"


def directory(path):
    info = path.lstat()
    require(path.is_absolute() and path.resolve() == path and stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
            "platform_database_directory_invalid")
    return info.st_dev, info.st_ino


def public_read(path):
    require(path.is_absolute() and path.resolve() == path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and before.st_nlink == 1 and not before.st_mode & 0o022
                and 0 < before.st_size <= 1024 * 1024)
        raw = os.read(fd, 1024 * 1024 + 1)
        def stable(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        require(len(raw) == before.st_size and stable(before) == stable(os.fstat(fd))
                == stable(path.lstat()) and path.resolve() == path)
        return raw
    finally:
        os.close(fd)


class Attempt:
    """Held-directory, exclusive receipts; failed/unknown attempts remain intact."""
    def __init__(self, root):
        self.root = root
        self.root_identity = directory(root)
        require(root.is_relative_to(Path.home() / ".agent-sessions"), "platform_database_durable_root_required")
        self.operation = str(uuid4())
        self.database = PREFIX + self.operation.replace("-", "")
        self.path = root / self.operation
        self.fd = None
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(parent)
            require((opened.st_dev, opened.st_ino) == self.root_identity and directory(root) == self.root_identity)
            os.mkdir(self.operation, mode=0o700, dir_fd=parent)
            created = os.stat(self.operation, dir_fd=parent, follow_symlinks=False)
            require(stat.S_ISDIR(created.st_mode) and created.st_uid == os.getuid()
                    and stat.S_IMODE(created.st_mode) == 0o700)
            self.fd = os.open(self.operation, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            opened = os.fstat(self.fd)
            self.identity = created.st_dev, created.st_ino
            require((opened.st_dev, opened.st_ino) == self.identity)
            self.check()
            os.fsync(parent)
        except BaseException:
            self.close()
            raise
        finally:
            os.close(parent)

    def check(self):
        require(self.fd is not None and directory(self.root) == self.root_identity
                and directory(self.path) == self.identity)
        info = os.fstat(self.fd)
        require((info.st_dev, info.st_ino) == self.identity and stat.S_IMODE(info.st_mode) == 0o700
                and info.st_uid == os.getuid())

    def write(self, name, raw):
        require(name in {"intent.json", "created.json", "ready.json", "failed.json", "app-dsn", "owner-dsn"}
                and type(raw) is bytes and 0 < len(raw) <= 1024 * 1024)
        self.check()
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with os.fdopen(fd, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.fsync(self.fd)
        self.check()

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


@contextmanager
def deadline():
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "platform_database_existing_deadline")
    previous = signal.getsignal(signal.SIGALRM)
    def expired(_signal, _frame):
        raise EvidenceError("platform_database_timeout")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 120)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class Fixture:
    def __init__(self, root):
        self.root = root
        require(root.is_absolute() and root.resolve() == root)
        self.state = root / ".evolution/postgres"
        self.identity = directory(self.state)
        self.fd = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.lock = None
        try:
            opened = os.fstat(self.fd)
            require((opened.st_dev, opened.st_ino) == self.identity)
            self.lock = os.open(".prepare.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
            metadata = os.fstat(self.lock)
            require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid()
                    and metadata.st_nlink == 1 and stat.S_IMODE(metadata.st_mode) == 0o600)
            self.lock_identity = metadata.st_dev, metadata.st_ino
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.raw = {name: self.read(name) for name in ("instance.json", "owner-dsn", "app-dsn")}
            self.settings = json.loads(self.raw["instance.json"], object_pairs_hook=unique_object)
            settings = self.settings
            identity = hashlib.sha256(str(root).encode()).hexdigest()[:16]
            require(type(settings) is dict and set(settings) == {"container", "owner", "image", "port"}
                    and settings["owner"] == identity and settings["container"] == "guardrails-evolution-db-" + identity
                    and type(settings["port"]) is int and 0 < settings["port"] <= 65535)
            self.lock_path = root / "deploy/evolution/artifacts.lock.json"
            self.lock_raw = public_read(self.lock_path)
            artifacts = json.loads(self.lock_raw, object_pairs_hook=unique_object)
            require(artifacts["schema_version"] == "guardrails-evolution-artifacts-v1")
            pins = [row for row in artifacts["artifacts"] if row["name"] == "postgres"]
            require(len(pins) == 1 and pins[0]["kind"] == "image"
                    and re.fullmatch(r"[0-9a-f]{64}", pins[0]["sha256"]) is not None
                    and settings["image"] == pins[0]["sha256"]
                    and pins[0]["source_url"].startswith("docker://")
                    and pins[0]["source_url"].endswith("@sha256:" + settings["image"]))
            info = postgres.owned_container(settings["container"], identity, settings["image"], running=True)
            self.container_id = info["Id"]
            require(postgres.published_port(info) == settings["port"], "platform_database_port_drift")
            for filename, role in (("owner-dsn", "evolution_owner"), ("app-dsn", "evolution_app")):
                require(postgres.current_dsn(self.raw[filename], role, settings["port"], settings["port"]) == self.raw[filename])
            postgres.verify_access([(role, self.raw[name]) for name, role in
                                    (("owner-dsn", "evolution_owner"), ("app-dsn", "evolution_app"))])
            self.check()
        except BaseException:
            self.close()
            raise

    def read(self, name):
        return postgres.private_read(Path(f"/proc/{os.getpid()}/fd/{self.fd}") / name)

    def check(self):
        require(directory(self.state) == self.identity and public_read(self.lock_path) == self.lock_raw)
        metadata = os.stat(".prepare.lock", dir_fd=self.fd, follow_symlinks=False)
        require((metadata.st_dev, metadata.st_ino) == self.lock_identity
                and stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid()
                and stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_nlink == 1)
        require(all(self.read(name) == raw for name, raw in self.raw.items()), "platform_database_fixture_changed")
        settings = self.settings
        info = postgres.owned_container(settings["container"], settings["owner"], settings["image"], self.container_id, running=True)
        require(postgres.published_port(info) == settings["port"], "platform_database_port_drift")

    def close(self):
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def connect(raw, *, read_only):
    value = make_url(raw.decode("ascii").strip())
    options = "-c statement_timeout=15000 -c lock_timeout=2000 -c idle_in_transaction_session_timeout=15000"
    if read_only:
        options += " -c default_transaction_read_only=on"
    connection = make_conninfo(host=value.host, port=value.port, user=value.username,
                              password=value.password, dbname=value.database,
                              connect_timeout=2, options=options)
    return psycopg.connect(connection, autocommit=True)


def verify_roles(connection, database, user):
    observed = connection.execute("SELECT current_user,current_database(),pg_is_in_recovery()").fetchone()
    require(observed == (user, database, False), "platform_database_access_invalid")
    app = connection.execute("SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolcanlogin FROM pg_roles WHERE rolname='evolution_app'").fetchone()
    require(app == (False, False, False, False, True), "platform_database_app_role_invalid")


def new_dsns(fixture, database):
    require(re.fullmatch(PREFIX + r"[0-9a-f]{32}", database) is not None)
    # Source URLs have already passed the exact original producer URL validator.
    return {name: raw.rstrip(b"\n").removesuffix(b"/evolution") + b"/" + database.encode() + b"\n"
            for name, raw in fixture.raw.items() if name in {"owner-dsn", "app-dsn"}}


def migrate_database(raw):
    from guardrails.evolution.storage import migrate
    # Call the current production revision loop, with bounded driver settings.
    url = make_url(raw.decode("ascii").strip()).update_query_dict({"connect_timeout": "2",
        "options": "-c statement_timeout=15000 -c lock_timeout=2000 -c idle_in_transaction_session_timeout=15000"})
    migrate(url.render_as_string(hide_password=False))


def grant_and_verify(dsns, database):
    with connect(dsns["owner-dsn"], read_only=False) as owner:
        verify_roles(owner, database, "evolution_owner")
        owner.execute("GRANT USAGE ON SCHEMA evolution TO evolution_app")
        owner.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA evolution TO evolution_app")
        owner.execute("GRANT EXECUTE ON FUNCTION evolution.tenant_scope() TO evolution_app")
        tables = owner.execute("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='evolution' AND c.relkind='r' ORDER BY c.relname").fetchall()
        require(1 <= len(tables) <= 64 and {row[0] for row in tables} >= {"tenants", "records", "record_links", "audit_heads"})
        for name, enabled, forced, role in tables:
            require(type(name) is str and re.fullmatch(r"[a-z_]{1,63}", name) and enabled is True
                    and forced is True and role == "evolution_owner", "platform_database_rls_invalid")
            require(owner.execute(sql.SQL("SELECT count(*) FROM evolution.{}").format(sql.Identifier(name))).fetchone() == (0,),
                    "platform_database_not_empty")
    with connect(dsns["app-dsn"], read_only=True) as app:
        verify_roles(app, database, "evolution_app")
        app.execute("SELECT set_config('guardrails.tenant_id','_platform',false)")
        require(app.execute("SELECT count(*) FROM evolution.records").fetchone() == (0,))
        require(app.execute("SELECT count(*) FROM evolution.tenants").fetchone() == (0,))


def verify_sources(root, sources):
    validate_catalog(sources, root)
    require(REQUIRED_SOURCES <= sources.keys(), "platform_database_source_closure_missing")
    loaded = [("mvp/evolution/platform_database.py", Path(__file__).resolve()),
              ("mvp/evolution/postgres.py", Path(postgres.__file__).resolve())]
    for name in STORAGE_MODULES:
        module = importlib.import_module(name)
        loaded.append(("mvp/" + name.replace(".", "/") + ".py", Path(module.__file__).resolve()))
    for name, path in loaded:
        require(hashlib.sha256(public_read(path)).hexdigest() == sources[name],
                "platform_database_loaded_source_mismatch")
    catalog = SourceCatalog(root, sources)
    for name in sources:
        catalog.path(name)
        catalog.load(name)


def allocate(*, root, fixture_root, operation_root, sources):
    """Return only a new private app DSN path after genuine verification succeeds."""
    require(all(not value for key, value in os.environ.items() if key.startswith("PG"))
            and os.environ.get("DOCKER_HOST", "") in ("", "unix:///var/run/docker.sock")
            and all(not os.environ.get(key) for key in ("DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_TLS",
                "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "BUILDX_CONFIG")), "platform_database_routing_override")
    attempt = fixture = None
    stage, outcome, oid = "validate", "not_requested", None
    try:
        with deadline():
            verify_sources(root, sources)
            attempt = Attempt(operation_root)
            fixture = Fixture(fixture_root)
            dsns = new_dsns(fixture, attempt.database)
            with connect(fixture.raw["owner-dsn"], read_only=True) as owner:
                verify_roles(owner, "evolution", "evolution_owner")
                require(owner.execute("SELECT rolsuper OR rolcreatedb FROM pg_roles WHERE rolname=current_user").fetchone() == (True,))
                require(owner.execute("SELECT oid FROM pg_database WHERE datname=%s", (attempt.database,)).fetchone() is None,
                        "platform_database_already_exists")
            intent = {"schema_version": "evolution-platform-database-attempt-v1", "operation": attempt.operation,
                "database": attempt.database, "nodeid": NODE, "source_sha256": sources,
                "fixture": {"root": str(fixture_root), "container_id": fixture.container_id,
                    **fixture.settings, "metadata_sha256": hashlib.sha256(fixture.raw["instance.json"]).hexdigest(),
                    "artifact_lock_sha256": hashlib.sha256(fixture.lock_raw).hexdigest()},
                "status": "intent_before_create", "allocation_budget_seconds": 120}
            attempt.write("intent.json", canonical(intent))
            for name, raw in dsns.items():
                attempt.write(name, raw)
            fixture.check()
            verify_sources(root, sources)
            attempt.check()
            stage, outcome = "create", "unknown"
            with connect(fixture.raw["owner-dsn"], read_only=False) as owner:
                verify_roles(owner, "evolution", "evolution_owner")
                owner.execute(sql.SQL("CREATE DATABASE {} OWNER evolution_owner TEMPLATE template0").format(sql.Identifier(attempt.database)))
                outcome = "created"
                row = owner.execute("SELECT oid,pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (attempt.database,)).fetchone()
                require(row is not None and type(row[0]) is int and row[0] > 0 and row[1] == "evolution_owner")
                oid = row[0]
            attempt.write("created.json", canonical({"operation": attempt.operation, "database": attempt.database,
                "database_oid": oid, "status": "created_retained"}))
            stage = "migrate"
            migrate_database(dsns["owner-dsn"])
            stage = "verify"
            grant_and_verify(dsns, attempt.database)
            fixture.check()
            verify_sources(root, sources)
            attempt.write("ready.json", canonical({"operation": attempt.operation, "database": attempt.database,
                "database_oid": oid, "status": "ready_empty_retained", "source_sha256": sources,
                "fixture_container_id": fixture.container_id, "case_executed": False}))
            return {ENV: str(attempt.path / "app-dsn")}
    except (Exception, KeyboardInterrupt) as error:
        # Never expose a driver exception containing private connection details.
        if attempt is not None:
            try:
                attempt.write("failed.json", canonical({"operation": attempt.operation,
                    "database": attempt.database, "stage": stage, "create_outcome": outcome,
                    "database_oid": oid, "status": "failed_retained_no_retry", "case_executed": False}))
            except (OSError, EvidenceError):
                pass  # Preserve the original intent; never write into a displaced path.
        if isinstance(error, KeyboardInterrupt):
            raise
        raise EvidenceError("platform_database_allocation_failed") from None
    finally:
        if fixture is not None:
            fixture.close()
        if attempt is not None:
            attempt.close()


def environment_for_case(nodeid, *, root, sources, environ):
    if nodeid != NODE:
        return {}
    require(not environ.get(ENV), "platform_database_external_dsn_refused")
    fixture = environ.get("EVOLUTION_PLATFORM_FIXTURE_ROOT")
    operations = environ.get("EVOLUTION_PLATFORM_OPERATION_ROOT")
    require(type(fixture) is str and fixture and type(operations) is str and operations,
            "platform_database_explicit_roots_required")
    for name, leaf in (("EVOLUTION_TEST_DSN_FILE", "app-dsn"), ("EVOLUTION_TEST_OWNER_DSN_FILE", "owner-dsn")):
        require(not environ.get(name) or environ[name] == str(Path(fixture) / ".evolution/postgres" / leaf),
                "platform_database_fixture_environment_mismatch")
    return allocate(root=Path(root), fixture_root=Path(fixture), operation_root=Path(operations), sources=sources)
