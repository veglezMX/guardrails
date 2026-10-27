"""Real private-file/socket/process recovery with a finite Docker metadata port.

The metadata fixture is not a Docker/Kubernetes or TLS acceptance claim. The
production health client remains unchanged; live repair must prove its CA check.
"""

import base64
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "forward_relocation_assembly", ROOT / "deploy/evolution/assembly.py"
)
a = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a)


class Interrupted(BaseException):
    pass


class MetadataDeployment:
    def __init__(self, path):
        self.state = path / "state"
        self.state.mkdir(mode=0o700)
        self.bin = self.state / "bin"
        self.kubeconfig = self.state / "kubeconfig"
        self.env = dict(os.environ)
        self.identity = {
            "owner_id": "synthetic-owner", "container_id": "a" * 64,
            "state": str(self.state), "uid": os.getuid(),
        }
        self.inspect = {
            "id": self.identity["container_id"], "name": "/guardrails-evolution",
            "running": True, "ports": {},
        }
        self.calls = []

    def owner(self):
        return dict(self.identity)

    def lock(self):
        return nullcontext()

    def verify_prepared(self):
        self.calls.append("prepared")

    @staticmethod
    def read_json(path):
        return a.strict_json_loads(a.seed.private_read(path))

    @staticmethod
    def write_json(path, value):
        a._write(path, value)

    def run(self, argv, *, timeout, check):
        assert argv[:3] == ["docker", "container", "inspect"]
        assert argv[-1] == self.identity["container_id"]
        assert timeout == 15 and check is False
        self.calls.append("inspect")
        return 0, json.dumps(self.inspect)


@pytest.fixture
def setup(tmp_path):
    d = MetadataDeployment(tmp_path)
    directory, owner = a._state(d)
    ports = a._port_record(d, directory)
    (directory / "seed").mkdir(mode=0o700)
    for name in a._RELOCATION_SEED_FILES:
        a._write(directory / name, {"tenants": {"team-a": {}, "team-b": {}}, "unchanged": name})
    a._write(directory / "token", b"synthetic-secret-canary")
    a._write(directory / "access.json", {
        "profile": "local_model", "management_url": f'https://localhost:{ports["management"]}',
        "ca_file": str(d.state / "credentials/ca.crt"),
        "seed_inventory_file": str(directory / "seed/inventory.json"),
        "mock_token_file": str(directory / "token"),
        "ollama_url": f'https://localhost:{ports["ollama"]}',
        "tenants": {}, "retained_route": {"digest": "unchanged"},
    })
    a._write(directory / "values.json", {"components": {"ollama": {"enabled": True}}})
    listener = socket.socket()
    listener.bind(("127.0.0.1", ports["management"]))
    listener.listen(1)
    d.inspect["ports"] = {"5000/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(ports["management"])}]}
    try:
        yield d, directory, owner, ports, listener
    finally:
        listener.close()


def preserved(directory):
    return {name: (directory / name).read_bytes() for name in (*a._RELOCATION_SEED_FILES, "token")}


def receipt(directory, owner):
    return directory / "management-relocations" / (owner["container_id"] + ".json")


def simulate_services(monkeypatch, d):
    def forward(_d, directory, name, port):
        d.calls.append(("forward", name, port))

    def health(url, ca, **kwargs):
        assert url.endswith("/health/live")
        assert ca == str(d.state / "credentials/ca.crt")
        assert kwargs == {"timeout": 30}  # Original CA; no credentials.
        d.calls.append("health")

    monkeypatch.setattr(a, "_forward", forward)
    monkeypatch.setattr(a, "_https_ready", health)


def test_completed_management_collision_changes_only_host_endpoint_and_keeps_history(setup, monkeypatch):
    d, directory, owner, old, listener = setup
    original = preserved(directory)
    before_access = d.read_json(directory / "access.json")
    pending = a._prepare_management_relocation(d, directory, owner)
    new = d.read_json(directory / "ports.json")
    assert {**new, "management": old["management"]} == old
    assert new["management"] != old["management"]
    assert {**d.read_json(directory / "access.json"), "management_url": before_access["management_url"]} == before_access
    assert pending[1]["status"] == "pending"
    assert receipt(directory, owner).stat().st_mode & 0o777 == 0o600
    assert receipt(directory, owner).parent.stat().st_mode & 0o777 == 0o700
    simulate_services(monkeypatch, d)
    a._finish_management_relocation(d, directory, owner, pending)
    frozen = receipt(directory, owner).read_bytes()
    assert d.read_json(receipt(directory, owner))["status"] == "complete"
    changed = d.read_json(directory / "access.json")
    changed["profile"] = "deterministic_mock"
    a._write(directory / "access.json", changed)
    assert a._prepare_management_relocation(d, directory, owner) is None
    assert a.access(d)["management_url"] == changed["management_url"]
    assert receipt(directory, owner).read_bytes() == frozen
    assert preserved(directory) == original
    assert a._forward_listening(a._process_identity(os.getpid()), old["management"])
    assert listener.fileno() >= 0  # The original publisher is never signalled.


@pytest.mark.parametrize("boundary", ["journal", "ports", "access", "forward", "health"])
def test_interrupted_relocation_reuses_exact_port_and_recovers_ordered_boundaries(setup, monkeypatch, boundary):
    d, directory, owner, old, _ = setup
    original = preserved(directory)
    simulate_services(monkeypatch, d)
    method = {"journal": "_publish_relocation", "ports": "_relocation_cas", "access": "_relocation_cas",
              "forward": "_forward", "health": "_https_ready"}[boundary]
    real = getattr(a, method)
    interrupted = False

    def fail_after(*args, **kwargs):
        nonlocal interrupted
        result = real(*args, **kwargs)
        relevant = boundary not in {"ports", "access"} or args[0].name == boundary + ".json"
        if relevant and not interrupted:
            interrupted = True
            raise Interrupted
        return result

    monkeypatch.setattr(a, method, fail_after)
    with pytest.raises(Interrupted):
        pending = a._prepare_management_relocation(d, directory, owner)
        a._finish_management_relocation(d, directory, owner, pending)
    planned = d.read_json(receipt(directory, owner))
    _, after = a._relocation_transition(planned)
    chosen = after["ports"]["management"]
    with pytest.raises(a.AssemblyError, match="forward_relocation_pending"):
        a.access(d)
    monkeypatch.setattr(a, method, real)
    pending = a._prepare_management_relocation(d, directory, owner)
    assert d.read_json(directory / "ports.json")["management"] == chosen != old["management"]
    a._finish_management_relocation(d, directory, owner, pending)
    assert d.read_json(receipt(directory, owner))["status"] == "complete"
    assert preserved(directory) == original


@pytest.mark.parametrize("change", ["access_field", "port", "reversed", "seed", "publication", "owner"])
def test_pending_relocation_rejects_unexpected_state_without_repairing_it(setup, monkeypatch, change):
    d, directory, owner, _, _ = setup
    pending = a._prepare_management_relocation(d, directory, owner)
    snapshots, _ = a._relocation_transition(pending[1])
    if change == "access_field":
        value = d.read_json(directory / "access.json")
        value["retained_route"] = {"digest": "foreign"}
        a._write(directory / "access.json", value)
    elif change == "port":
        value = d.read_json(directory / "ports.json")
        value["postgres"] = value["postgres"] + 1
        a._write(directory / "ports.json", value)
    elif change == "reversed":
        a._write(directory / "ports.json", snapshots["before"]["ports"])
    elif change == "seed":
        a._write(directory / "seed/bootstrap.json", {"changed": True})
    elif change == "publication":
        d.inspect["ports"]["5000/tcp"][0]["HostPort"] = "65500"
    else:
        d.identity["container_id"] = "b" * 64
        owner = d.owner()
    before = {name: (directory / name).read_bytes() for name in ("ports.json", "access.json")}
    with pytest.raises(a.AssemblyError, match="forward_relocation_(state_changed|invalid)"):
        a._prepare_management_relocation(d, directory, owner)
    assert before == {name: (directory / name).read_bytes() for name in before}


@pytest.mark.parametrize("change", ["route", "ca", "other_port", "extra_field", "wrong_owner"])
def test_journal_rederives_management_only_transition_even_with_rehashed_forgery(setup, change):
    d, directory, owner, _, _ = setup
    a._prepare_management_relocation(d, directory, owner)
    path = receipt(directory, owner)
    record = d.read_json(path)
    if change == "extra_field":
        record["extra"] = True
    elif change == "wrong_owner":
        record["owner"]["owner_id"] = "foreign"
    else:
        name = "ports" if change == "other_port" else "access"
        value = json.loads(base64.b64decode(record["after"][name]["bytes"]))
        if change == "route":
            value["retained_route"] = {"digest": "foreign"}
        elif change == "ca":
            value["ca_file"] = "/foreign/ca.crt"
        else:
            value["runtime"] += 1
        raw = a.canonical_json_bytes(value)
        record["after"][name] = {"bytes": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest()}
    a._write(path, record)
    before = path.read_bytes()
    with pytest.raises(a.AssemblyError, match="forward_relocation_invalid"):
        a._prepare_management_relocation(d, directory, owner)
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["foreign_id", "foreign_name", "stopped", "wildcard", "other_collision", "not_published"])
def test_occupied_port_needs_exact_running_owned_node_publication(setup, change):
    d, directory, owner, ports, listener = setup
    if change == "foreign_id":
        d.inspect["id"] = "b" * 64
    elif change == "foreign_name":
        d.inspect["name"] = "/unrelated"
    elif change == "stopped":
        d.inspect["running"] = False
    elif change == "wildcard":
        d.inspect["ports"]["5000/tcp"][0]["HostIp"] = "0.0.0.0"
    elif change == "other_collision":
        d.inspect["ports"]["22/tcp"] = [{"HostIp": "127.0.0.1", "HostPort": str(ports["postgres"])}]
    else:
        d.inspect["ports"]["5000/tcp"][0]["HostPort"] = "65500"
    before = {name: (directory / name).read_bytes() for name in ("ports.json", "access.json")}
    with pytest.raises(a.AssemblyError, match="forward_(relocation_owner_mismatch|port_already_used)"):
        a._prepare_management_relocation(d, directory, owner)
    assert not receipt(directory, owner).exists()
    assert before == {name: (directory / name).read_bytes() for name in before}
    assert listener.fileno() >= 0


def test_new_port_contention_keeps_pending_receipt_and_never_repicks(setup):
    d, directory, owner, _, _ = setup
    pending = a._prepare_management_relocation(d, directory, owner)
    chosen = d.read_json(directory / "ports.json")["management"]
    original = receipt(directory, owner).read_bytes()
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", chosen))
        foreign.listen(1)
        with pytest.raises(a.AssemblyError, match="forward_port_already_used"):
            a._finish_management_relocation(d, directory, owner, pending)
        assert a._prepare_management_relocation(d, directory, owner) == pending
        assert receipt(directory, owner).read_bytes() == original
        assert d.read_json(directory / "ports.json")["management"] == chosen
        assert a._forward_listening(a._process_identity(os.getpid()), chosen)


def test_atomic_new_journal_never_overwrites_receipt_or_publishes_partial_stage(setup, monkeypatch):
    _, directory, owner, _, _ = setup
    history = a._directory(directory / "management-relocations")
    path = receipt(directory, owner)
    original = a.seed.private_write

    def torn(stage, value, **kwargs):
        original(stage, b"partial")
        raise Interrupted

    monkeypatch.setattr(a.seed, "private_write", torn)
    with pytest.raises(Interrupted):
        a._publish_relocation(path, {"complete": "record"})
    assert list(history.iterdir()) == []
    monkeypatch.setattr(a.seed, "private_write", original)
    a._publish_relocation(path, {"original": True})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        a._publish_relocation(path, {"replacement": True})
    assert path.read_bytes() == before


def test_completed_old_node_receipt_allows_restored_owner_and_preserves_history(setup, monkeypatch):
    d, directory, owner, _, _ = setup
    pending = a._prepare_management_relocation(d, directory, owner)
    simulate_services(monkeypatch, d)
    a._finish_management_relocation(d, directory, owner, pending)
    original = receipt(directory, owner).read_bytes()
    d.identity["container_id"] = "b" * 64
    a._write(directory / "owner.json", d.identity)
    assert a._prepare_management_relocation(d, directory, d.owner()) is None
    assert receipt(directory, owner).read_bytes() == original


@pytest.mark.parametrize("profile", ["local_model", "deterministic_mock"])
def test_platform_repairs_pending_before_reconcile_and_completes_before_profile_write(setup, monkeypatch, profile):
    d, directory, owner, old, _ = setup
    original = preserved(directory)
    # Leave a published journal with metadata still entirely old.
    real_publish = a._publish_relocation

    def publish(*args):
        real_publish(*args)
        raise Interrupted

    monkeypatch.setattr(a, "_publish_relocation", publish)
    with pytest.raises(Interrupted):
        a._prepare_management_relocation(d, directory, owner)
    monkeypatch.setattr(a, "_publish_relocation", real_publish)
    monkeypatch.setattr(a, "_network_baseline", lambda *_: None)
    monkeypatch.setattr(a, "_desired_images", lambda *_: False)
    monkeypatch.setattr(a, "_owned_deployments", lambda *_: None)
    monkeypatch.setattr(a, "_gateway", lambda *_: {})
    monkeypatch.setattr(a, "_warm_model", lambda *_: d.calls.append("warm"))
    simulate_services(monkeypatch, d)

    def reconcile(*args, **kwargs):
        ports = d.read_json(directory / "ports.json")
        value = d.read_json(directory / "access.json")
        assert ports["management"] != old["management"]
        assert value["management_url"] == f'https://localhost:{ports["management"]}'
        assert value["profile"] == "local_model"
        assert d.read_json(receipt(directory, owner))["status"] == "pending"
        d.calls.append("reconcile")

    monkeypatch.setattr(a, "_reconcile_application_images", reconcile)
    write = a._write

    def checked_write(path, value):
        if path.name == "access.json":
            assert d.read_json(receipt(directory, owner))["status"] == "complete"
        write(path, value)

    monkeypatch.setattr(a, "_write", checked_write)
    assert a.platform(d, profile=profile)["status"] == "ready"
    assert d.calls.index("reconcile") < d.calls.index("health")
    assert d.read_json(directory / "access.json")["profile"] == profile
    assert preserved(directory) == original


@pytest.mark.parametrize("failure", ["persistence", "cancellation"])
def test_new_forward_child_is_reaped_if_identity_persistence_fails_or_is_cancelled(setup, monkeypatch, failure):
    d, directory, _, ports, listener = setup
    listener.close()
    children = []
    original_popen = subprocess.Popen
    code = "import socket,sys,time; s=socket.socket(); s.bind(('127.0.0.1',int(sys.argv[1]))); s.listen(); time.sleep(60)"
    monkeypatch.setattr(a, "_forward_argv", lambda _d, _n, port: [sys.executable, "-c", code, str(port)])

    def popen(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(a.subprocess, "Popen", popen)
    expected = OSError("synthetic-persistence") if failure == "persistence" else Interrupted()

    def fail_write(*_):
        raise expected

    monkeypatch.setattr(d, "write_json", fail_write)
    try:
        with pytest.raises(type(expected)) as caught:
            a._forward(d, directory, "management", ports["management"])
        assert caught.value is expected
        assert len(children) == 1 and children[0].poll() is not None
        assert a._process_identity(children[0].pid) is None
        assert not (directory / "forward-management.json").exists()
        with socket.socket() as check:
            check.bind(("127.0.0.1", ports["management"]))
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)


def test_large_valid_journal_completes_under_same_declared_read_and_cas_limit(setup, monkeypatch):
    d, directory, owner, _, _ = setup
    access = d.read_json(directory / "access.json")
    access["synthetic_retained_padding"] = "x" * 100_000
    a._write(directory / "access.json", access)
    pending = a._prepare_management_relocation(d, directory, owner)
    assert 128 * 1024 < receipt(directory, owner).stat().st_size <= 512 * 1024
    simulate_services(monkeypatch, d)
    a._finish_management_relocation(d, directory, owner, pending)
    assert d.read_json(receipt(directory, owner))["status"] == "complete"


def test_optional_bootstrap_plan_absence_is_preserved_and_later_creation_refused(setup, monkeypatch):
    d, directory, owner, _, _ = setup
    plan = directory / "seed/bootstrap-plan.json"
    plan.unlink()
    pending = a._prepare_management_relocation(d, directory, owner)
    assert pending[1]["seed_hashes"]["seed/bootstrap-plan.json"] is None
    a._write(plan, {"late": True})
    with pytest.raises(a.AssemblyError, match="forward_relocation_state_changed"):
        a._finish_management_relocation(d, directory, owner, pending)
    plan.unlink()
    simulate_services(monkeypatch, d)
    a._finish_management_relocation(d, directory, owner, pending)
    assert not plan.exists()


def test_oversized_journal_refuses_before_any_publication_or_metadata_change(setup):
    _, directory, owner, _, _ = setup
    a._directory(directory / "management-relocations")
    before = (directory / "ports.json").read_bytes(), (directory / "access.json").read_bytes()
    with pytest.raises(a.AssemblyError, match="forward_relocation_invalid"):
        a._publish_relocation(receipt(directory, owner), {"padding": "x" * (512 * 1024)})
    # Even a pending document exactly at the limit cannot publish if its
    # completed status would make the immutable receipt one byte too large.
    edge = {"status": "pending", "padding": ""}
    edge["padding"] = "x" * (512 * 1024 - len(a.canonical_json_bytes(edge)))
    assert len(a.canonical_json_bytes(edge)) == 512 * 1024
    with pytest.raises(a.AssemblyError, match="forward_relocation_invalid"):
        a._publish_relocation(receipt(directory, owner), edge)
    assert not receipt(directory, owner).exists()
    assert before == ((directory / "ports.json").read_bytes(), (directory / "access.json").read_bytes())


@pytest.mark.parametrize("shape", ["symlink", "permissions", "malformed"])
def test_private_journal_boundary_refuses_untrusted_files_without_operational_changes(setup, shape):
    d, directory, owner, _, _ = setup
    a._prepare_management_relocation(d, directory, owner)
    path = receipt(directory, owner)
    if shape == "symlink":
        saved = directory / "saved.json"
        path.rename(saved)
        path.symlink_to(saved)
    elif shape == "permissions":
        path.chmod(0o644)
    else:
        a._write(path, b'{"format":')
    before = (directory / "ports.json").read_bytes(), (directory / "access.json").read_bytes()
    with pytest.raises((a.AssemblyError, OSError, a.seed.EvolutionError)):
        a._prepare_management_relocation(d, directory, owner)
    assert before == ((directory / "ports.json").read_bytes(), (directory / "access.json").read_bytes())


def test_real_new_forward_survives_interruption_is_reused_then_stopped_by_exact_identity(setup, monkeypatch):
    d, directory, owner, _, _ = setup
    pending = a._prepare_management_relocation(d, directory, owner)
    chosen = d.read_json(directory / "ports.json")["management"]
    code = "import socket,sys,time; s=socket.socket(); s.bind(('127.0.0.1',int(sys.argv[1]))); s.listen(); time.sleep(60)"
    monkeypatch.setattr(a, "_forward_argv", lambda _d, _n, port: [sys.executable, "-c", code, str(port)])
    children = []
    popen = subprocess.Popen

    def tracked(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(a.subprocess, "Popen", tracked)
    real = a._forward

    def interrupted(*args):
        real(*args)
        raise Interrupted

    monkeypatch.setattr(a, "_forward", interrupted)
    try:
        with pytest.raises(Interrupted):
            a._finish_management_relocation(d, directory, owner, pending)
        saved = d.read_json(directory / "forward-management.json")
        assert a._forward_listening(saved, chosen)
        pending = a._prepare_management_relocation(d, directory, owner)
        monkeypatch.setattr(a, "_forward", real)
        monkeypatch.setattr(a, "_https_ready", lambda *_a, **_k: None)
        a._finish_management_relocation(d, directory, owner, pending)
        assert d.read_json(directory / "forward-management.json") == saved
        assert len(children) == 1
        a.stop_forwards(d, names={"management"})
        children[0].wait(timeout=3)
        assert a._process_identity(saved["pid"]) is None
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)


def test_stop_forwards_resumes_pending_metadata_before_validating_new_child(setup, monkeypatch):
    d, directory, owner, old, _ = setup
    publish = a._publish_relocation

    def interrupted(*args):
        publish(*args)
        raise Interrupted

    monkeypatch.setattr(a, "_publish_relocation", interrupted)
    with pytest.raises(Interrupted):
        a._prepare_management_relocation(d, directory, owner)
    record = d.read_json(receipt(directory, owner))
    _, after = a._relocation_transition(record)
    chosen = after["ports"]["management"]
    assert d.read_json(directory / "ports.json") == old
    code = "import socket,sys,time; s=socket.socket(); s.bind(('127.0.0.1',int(sys.argv[1]))); s.listen(); time.sleep(60)"
    monkeypatch.setattr(a, "_forward_argv", lambda _d, _n, port: [sys.executable, "-c", code, str(port)])
    popen, children = subprocess.Popen, []

    def tracked(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(a.subprocess, "Popen", tracked)
    try:
        a._forward(d, directory, "management", chosen)
        assert a._forward_listening(d.read_json(directory / "forward-management.json"), chosen)
        a.stop_forwards(d)
        assert d.read_json(directory / "ports.json")["management"] == chosen
        assert d.read_json(receipt(directory, owner))["status"] == "pending"
        children[0].wait(timeout=3)
        assert a._process_identity(children[0].pid) is None
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)


def test_incomplete_assembly_never_relocates_or_rewrites_seed_history(setup):
    d, directory, owner, old, _ = setup
    (directory / "access.json").unlink()
    before = preserved(directory)
    assert a._prepare_management_relocation(d, directory, owner) is None
    assert d.read_json(directory / "ports.json") == old
    assert d.calls == []
    assert preserved(directory) == before
    assert not receipt(directory, owner).exists()
