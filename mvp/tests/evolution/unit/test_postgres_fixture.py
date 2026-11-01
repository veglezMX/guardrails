"""Fixture recovery exercises real private files with fake Docker and SQL boundaries."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from evolution import postgres
from evolution.registry import EvidenceError
from guardrails.evolution import storage


OLD_PORT, CURRENT_PORT = 44621, 44630
IMAGE, CONTAINER = "a" * 64, "b" * 64
PASSWORD = "kept%40pass%3Aword%2Fwith%25encoding"


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    for variable in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(variable, raising=False)
    state = tmp_path / ".evolution/postgres"
    state.mkdir(parents=True, mode=0o700)
    owner = hashlib.sha256(str(tmp_path.resolve()).encode()).hexdigest()[:16]
    name = "guardrails-evolution-db-" + owner
    settings = {"container": name, "owner": owner, "image": IMAGE, "port": OLD_PORT}
    postgres.private_write(state / "instance.json", json.dumps(settings, sort_keys=True))
    for filename, role in (("owner-dsn", "evolution_owner"), ("app-dsn", "evolution_app")):
        postgres.private_write(state / filename, f"postgresql+psycopg://{role}:{PASSWORD}@127.0.0.1:{OLD_PORT}/evolution\n")
    original = {name: (state / name).read_bytes() for name in ("instance.json", "owner-dsn", "app-dsn")}
    info = {
        "Id": CONTAINER, "Name": "/" + name, "Image": "sha256:" + IMAGE,
        "Config": {"Image": "sha256:" + IMAGE, "Labels": {"guardrails.evolution.owner": owner}},
        "State": {"Running": True},
        "NetworkSettings": {"Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(CURRENT_PORT)}]}},
    }
    observed = {"commands": [], "sql": [], "migrations": [], "repositories": [], "engines": [], "disposals": 0, "replacements": []}
    runtime = {"root": tmp_path, "state": state, "original": original, "info": info, "observed": observed, "fail_connects": 0}

    def command(*args):
        observed["commands"].append(args)
        if args[0] == "inspect":
            assert args[1] in (name, CONTAINER)
            return json.dumps([runtime["info"]])
        assert args == ("start", CONTAINER)
        runtime["info"]["State"]["Running"] = True
        runtime["info"]["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"] = str(CURRENT_PORT)
        return CONTAINER

    class Result:
        def __init__(self, role):
            self.role = role

        def one(self):
            return runtime.get("row", (self.role, "evolution", False, "on"))

    class Connection:
        def __init__(self, dsn):
            self.dsn = dsn

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql):
            observed["sql"].append(str(sql))
            assert str(sql).startswith("SELECT ")
            return Result(urlsplit(self.dsn).username)

    class Engine:
        def __init__(self, dsn):
            self.dsn = dsn

        def connect(self):
            if runtime["fail_connects"]:
                runtime["fail_connects"] -= 1
                raise postgres.OperationalError("unavailable", None, Exception("private-value"))
            return Connection(self.dsn)

        def dispose(self):
            observed["disposals"] += 1

    def create_engine(dsn, **options):
        assert urlsplit(dsn).port == CURRENT_PORT, "never connect to the stale endpoint"
        assert options["hide_parameters"] is True
        assert options["connect_args"]["connect_timeout"] == 2
        assert "default_transaction_read_only=on" in options["connect_args"]["options"]
        assert "statement_timeout=2000" in options["connect_args"]["options"]
        observed["engines"].append(dsn)
        return Engine(dsn)

    def migrate(dsn):
        # This is the existing preparation migration boundary, never a real DB.
        assert urlsplit(dsn).port == CURRENT_PORT
        assert json.loads((state / "instance.json").read_bytes())["port"] == CURRENT_PORT
        observed["migrations"].append(dsn)

    class Repository:
        def __init__(self, dsn):
            assert urlsplit(dsn).port == CURRENT_PORT
            observed["repositories"].append(dsn)

        def close(self):
            observed["closed"] = True

    original_replace = postgres.private_replace

    def record_replace(path, before, after):
        observed["replacements"].append(path.name)
        original_replace(path, before, after)

    monkeypatch.setattr(postgres, "command", command)
    monkeypatch.setattr(postgres, "verify_runtime_artifacts", lambda root: [{"name": "postgres", "sha256": IMAGE}])
    monkeypatch.setattr(postgres, "create_engine", create_engine)
    monkeypatch.setattr(postgres, "private_replace", record_replace)
    monkeypatch.setattr(storage, "migrate", migrate)
    monkeypatch.setattr(storage, "TenantRepository", Repository)
    return runtime


def assert_unchanged(fixture):
    assert {name: (fixture["state"] / name).read_bytes() for name in fixture["original"]} == fixture["original"]
    assert fixture["observed"]["migrations"] == []
    assert fixture["observed"]["repositories"] == []
    assert fixture["observed"]["replacements"] == []


@pytest.mark.parametrize("running", [True, False])
def test_existing_fixture_reconciles_current_port_before_migration(fixture, running):
    fixture["info"]["State"]["Running"] = running
    if not running:
        fixture["info"]["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"] = str(OLD_PORT)
    result = postgres.prepare(fixture["root"])
    state, observed = fixture["state"], fixture["observed"]
    assert result == {"EVOLUTION_TEST_DSN_FILE": str(state / "app-dsn"), "EVOLUTION_TEST_OWNER_DSN_FILE": str(state / "owner-dsn")}
    for name in ("owner-dsn", "app-dsn"):
        assert (state / name).read_bytes() == fixture["original"][name].replace(b":44621/", b":44630/")
        assert (state / name).stat().st_mode & 0o777 == 0o600
    assert observed["replacements"] == ["owner-dsn", "app-dsn", "instance.json"]
    assert len(observed["engines"]) == len(observed["sql"]) == observed["disposals"] == 2
    assert len(observed["migrations"]) == len(observed["repositories"]) == 1
    assert observed["closed"] is True
    assert [c for c in observed["commands"] if c[0] == "start"] == ([] if running else [("start", CONTAINER)])
    assert observed["commands"][-2:] == [("inspect", CONTAINER), ("inspect", CONTAINER)]


def test_unchanged_port_preserves_private_file_bytes_and_inodes(fixture):
    postgres.prepare(fixture["root"])
    before = {name: ((fixture["state"] / name).stat().st_ino, (fixture["state"] / name).read_bytes()) for name in fixture["original"]}
    fixture["observed"]["replacements"].clear()
    postgres.prepare(fixture["root"])
    after = {name: ((fixture["state"] / name).stat().st_ino, (fixture["state"] / name).read_bytes()) for name in fixture["original"]}
    assert before == after
    assert fixture["observed"]["replacements"] == []


@pytest.mark.parametrize("binding", [None, [], [{"HostIp": "0.0.0.0", "HostPort": "44630"}], [{"HostIp": "::1", "HostPort": "44630"}], [{"HostIp": "127.0.0.1", "HostPort": "0"}], [{"HostIp": "127.0.0.1", "HostPort": "65536"}], [{"HostIp": "127.0.0.1", "HostPort": 44630}], [{"HostIp": "127.0.0.1", "HostPort": "44630"}] * 2, [None]])
def test_invalid_binding_refuses_before_any_database_connection(fixture, binding):
    fixture["info"]["NetworkSettings"]["Ports"]["5432/tcp"] = binding
    with pytest.raises(EvidenceError, match="postgres_fixture_binding_invalid"):
        postgres.prepare(fixture["root"])
    assert fixture["observed"]["engines"] == []
    assert_unchanged(fixture)


@pytest.mark.parametrize("fault", ["owner", "image_reference", "image_id", "container_name", "changed_id"])
def test_foreign_or_replaced_container_is_rejected(fixture, monkeypatch, fault):
    info = fixture["info"]
    if fault == "owner":
        info["Config"]["Labels"]["guardrails.evolution.owner"] = "foreign"
    elif fault == "image_reference":
        info["Config"]["Image"] = "sha256:" + "c" * 64
    elif fault == "image_id":
        info["Image"] = "sha256:" + "c" * 64
    elif fault == "container_name":
        info["Name"] = "/foreign"
    else:
        original = postgres.command
        def command(*args):
            output = original(*args)
            if args == ("inspect", CONTAINER):
                rows = json.loads(output)
                rows[0]["Id"] = "c" * 64
                return json.dumps(rows)
            return output
        monkeypatch.setattr(postgres, "command", command)
        info["State"]["Running"] = False
    with pytest.raises(EvidenceError, match="postgres_fixture_(ownership|image|identity)_mismatch"):
        postgres.prepare(fixture["root"])
    assert fixture["observed"]["engines"] == []
    assert_unchanged(fixture)


@pytest.mark.parametrize("before,after", [(b":44621/", b":44622/"), (b"@127.0.0.1", b"@127.0.0.2"), (b"evolution_app:", b"evolution_owner:"), (b"/evolution\n", b"/evolution?hostaddr=192.0.2.1\n"), (b"kept%40", b"kept%ZZ"), (b"kept%40", b"kept%00"), (b"kept%40", b"kept%ff")])
def test_malformed_dsn_refuses_before_either_connection(fixture, before, after):
    path = fixture["state"] / "app-dsn"
    path.write_bytes(path.read_bytes().replace(before, after))
    fixture["original"]["app-dsn"] = path.read_bytes()
    with pytest.raises(EvidenceError, match="postgres_fixture_dsn_invalid"):
        postgres.prepare(fixture["root"])
    assert fixture["observed"]["engines"] == []
    assert_unchanged(fixture)


@pytest.mark.parametrize("row", [("wrong_role", "evolution", False, "on"), ("evolution_owner", "wrong_database", False, "on"), ("evolution_owner", "evolution", True, "on"), ("evolution_owner", "evolution", False, "off")])
def test_readonly_identity_proof_failure_does_not_publish_dsns(fixture, row):
    fixture["row"] = row
    with pytest.raises(EvidenceError, match="postgres_fixture_access_mismatch"):
        postgres.prepare(fixture["root"])
    assert fixture["observed"]["disposals"] == 1
    assert_unchanged(fixture)


def test_endpoint_change_during_proof_refuses_all_writes(fixture, monkeypatch):
    original = postgres.verify_access
    def verify(dsns):
        original(dsns)
        fixture["info"]["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"] = "44631"
    monkeypatch.setattr(postgres, "verify_access", verify)
    with pytest.raises(EvidenceError, match="postgres_fixture_binding_changed"):
        postgres.prepare(fixture["root"])
    assert_unchanged(fixture)


def test_concurrent_private_edit_is_preserved_and_not_overwritten(fixture, monkeypatch):
    original = postgres.verify_access
    path = fixture["state"] / "app-dsn"
    changed = fixture["original"]["app-dsn"].replace(b"kept", b"new-kept")
    def verify(dsns):
        original(dsns)
        path.write_bytes(changed)
    monkeypatch.setattr(postgres, "verify_access", verify)
    with pytest.raises(EvidenceError, match="postgres_fixture_private_file_changed"):
        postgres.prepare(fixture["root"])
    fixture["original"]["app-dsn"] = changed
    assert_unchanged(fixture)


@pytest.mark.parametrize("fail_at", ["app-dsn", "instance.json"])
def test_interrupted_transition_recovers_using_same_credentials(fixture, monkeypatch, fail_at):
    original = postgres.private_replace
    def crash(path, before, after):
        if path.name == fail_at:
            raise OSError("simulated interruption")
        original(path, before, after)
    monkeypatch.setattr(postgres, "private_replace", crash)
    with pytest.raises(OSError, match="simulated interruption"):
        postgres.prepare(fixture["root"])
    assert json.loads((fixture["state"] / "instance.json").read_bytes())["port"] == OLD_PORT
    assert fixture["observed"]["migrations"] == []
    assert (fixture["state"] / "owner-dsn").read_bytes() == fixture["original"]["owner-dsn"].replace(b":44621/", b":44630/")
    monkeypatch.setattr(postgres, "private_replace", original)
    postgres.prepare(fixture["root"])
    assert json.loads((fixture["state"] / "instance.json").read_bytes())["port"] == CURRENT_PORT
    assert len(fixture["observed"]["migrations"]) == 1
    assert all(PASSWORD in dsn for dsn in fixture["observed"]["engines"])


def test_startup_retry_is_bounded_and_never_uses_old_endpoint(fixture, monkeypatch):
    fixture["fail_connects"] = 2
    monkeypatch.setattr(postgres.time, "sleep", lambda duration: None)
    postgres.prepare(fixture["root"])
    assert fixture["fail_connects"] == 0
    assert len(fixture["observed"]["sql"]) == 2


def test_startup_timeout_preserves_files_and_hides_driver_error(fixture, monkeypatch):
    fixture["fail_connects"] = 10
    clock = iter([100.0, 131.0])
    monkeypatch.setattr(postgres.time, "monotonic", lambda: next(clock))
    with pytest.raises(EvidenceError, match="postgres_fixture_startup_timeout") as error:
        postgres.prepare(fixture["root"])
    assert "private-value" not in str(error.value)
    assert fixture["observed"]["disposals"] == 1
    assert_unchanged(fixture)


@pytest.mark.parametrize("key", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"])
def test_ambient_routing_override_refuses_database_access(fixture, monkeypatch, key):
    monkeypatch.setenv(key, "unexpected")
    with pytest.raises(EvidenceError, match="postgres_fixture_routing_override"):
        postgres.prepare(fixture["root"])
    assert fixture["observed"]["engines"] == []
    assert_unchanged(fixture)


def test_another_preparer_cannot_enter_while_lock_held(fixture):
    path = fixture["state"] / ".prepare.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(EvidenceError, match="postgres_fixture_preparation_busy"):
            postgres.prepare(fixture["root"])
    finally:
        os.close(fd)
    assert fixture["observed"]["commands"] == []
    assert_unchanged(fixture)


@pytest.mark.parametrize("fault", ["mode", "symlink", "hardlink"])
def test_private_credential_file_guards_precede_database_access(fixture, fault):
    path = fixture["state"] / "app-dsn"
    if fault == "mode":
        path.chmod(0o644)
    elif fault == "symlink":
        moved = path.with_name("private-original")
        path.rename(moved)
        path.symlink_to(moved)
    else:
        os.link(path, path.with_name("private-hardlink"))
    with pytest.raises((EvidenceError, OSError)):
        postgres.prepare(fixture["root"])
    assert fixture["observed"]["engines"] == []
    assert_unchanged(fixture)


def test_duplicate_settings_key_is_rejected_before_container_access(fixture):
    path = fixture["state"] / "instance.json"
    path.write_bytes(path.read_bytes()[:-1] + b', "port": 44621}')
    fixture["original"]["instance.json"] = path.read_bytes()
    with pytest.raises(EvidenceError, match="postgres_fixture_settings_invalid"):
        postgres.prepare(fixture["root"])
    assert fixture["observed"]["commands"] == []
    assert_unchanged(fixture)


def test_access_timestamp_changes_do_not_invalidate_private_reads(fixture):
    path = fixture["state"] / "app-dsn"
    # Old atime ensures a real read may advance it under relatime.
    metadata = path.stat()
    os.utime(path, ns=(1, metadata.st_mtime_ns))
    assert postgres.private_read(path) == fixture["original"]["app-dsn"]


def test_fifo_credential_is_refused_without_open_blocking(fixture):
    path = fixture["state"] / "app-dsn"
    path.unlink()
    os.mkfifo(path, 0o600)
    with pytest.raises(EvidenceError, match="postgres_fixture_private_file_invalid"):
        postgres.prepare(fixture["root"])
    assert fixture["observed"]["engines"] == []


def test_symlinked_state_ancestor_is_rejected_before_io(fixture, tmp_path):
    ancestor = fixture["root"] / ".evolution"
    moved = fixture["root"] / "private-moved"
    ancestor.rename(moved)
    ancestor.symlink_to(moved, target_is_directory=True)
    with pytest.raises(EvidenceError, match="postgres_fixture_directory_invalid"):
        postgres.prepare(fixture["root"])
    assert fixture["observed"]["commands"] == []


@pytest.mark.parametrize("state", ["Paused", "Restarting", "Dead"])
def test_ambiguous_container_state_refuses_connection(fixture, state):
    fixture["info"]["State"][state] = True
    with pytest.raises(EvidenceError, match="postgres_fixture_state_invalid"):
        postgres.prepare(fixture["root"])
    assert fixture["observed"]["engines"] == []
    assert_unchanged(fixture)


@pytest.mark.parametrize("key,value", [("DOCKER_HOST", "tcp://192.0.2.1:2375"), ("DOCKER_CONTEXT", "other"), ("DOCKER_TLS", "1"), ("DOCKER_TLS_VERIFY", "1"), ("DOCKER_CERT_PATH", "/unexpected")])
def test_docker_destination_override_is_rejected_before_operations(fixture, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(EvidenceError, match="postgres_fixture_docker_override"):
        postgres.prepare(fixture["root"])
    assert fixture["observed"]["commands"] == []
    assert_unchanged(fixture)


def test_container_change_during_proof_refuses_all_writes(fixture, monkeypatch):
    original = postgres.verify_access
    def verify(dsns):
        original(dsns)
        fixture["info"]["Id"] = "c" * 64
    monkeypatch.setattr(postgres, "verify_access", verify)
    with pytest.raises(EvidenceError, match="postgres_fixture_identity_mismatch"):
        postgres.prepare(fixture["root"])
    assert_unchanged(fixture)


def test_application_auth_failure_cannot_publish_owner_dsn_alone(fixture, monkeypatch):
    original = postgres.create_engine
    def create(dsn, **kwargs):
        engine = original(dsn, **kwargs)
        if urlsplit(dsn).username == "evolution_app":
            def fail():
                raise postgres.SQLAlchemyError("private-driver-value")
            engine.connect = fail
        return engine
    monkeypatch.setattr(postgres, "create_engine", create)
    with pytest.raises(EvidenceError, match="postgres_fixture_access_failed") as error:
        postgres.prepare(fixture["root"])
    assert "private-driver-value" not in str(error.value)
    assert fixture["observed"]["disposals"] == 2
    assert_unchanged(fixture)
