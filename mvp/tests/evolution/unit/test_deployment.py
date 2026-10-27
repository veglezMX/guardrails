"""Deployment guard tests exercise real private files and child processes."""

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "evolution_bootstrap", ROOT / "deploy/evolution/bootstrap.py"
)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def local_prepared(tmp_path):
    root = tmp_path / "root"
    state = tmp_path / "state"
    root.joinpath("deploy/evolution").mkdir(parents=True)
    state.mkdir(mode=0o700)
    deployment = bootstrap.LocalDeployment(root, state=state)
    deployment.bin.mkdir()
    deployment.downloads.mkdir()
    (state / "calico-pinned.yaml").write_text("prepared-calico")
    artifact_file = deployment.downloads / "fixture"
    artifact_file.write_bytes(b"locked real bytes")
    item = {
        "name": "fixture",
        "version": "1",
        "source_url": "https://example.invalid/fixture",
        "sha256": bootstrap.sha256(artifact_file),
        "size_bytes": artifact_file.stat().st_size,
        "license": "CC0-1.0",
        "kind": "fixture",
        "filename": "fixture",
    }
    lock = root / "deploy/evolution/preparation.lock.json"
    bootstrap.atomic_json(
        lock,
        {
            "schema_version": "guardrails-evolution-preparation-v1",
            "files": [item],
            "images": [],
        },
    )
    bootstrap.atomic_json(
        state / "prepared.json",
        {
            "preparation_lock_digest": bootstrap.sha256(lock),
            "files": deployment.file_inventory(),
            "artifacts": [
                {key: value for key, value in item.items() if key != "filename"}
            ],
        },
    )
    return deployment, artifact_file


@pytest.mark.parametrize("change", ["modified", "missing", "extra", "symlink"])
def test_offline_inventory_rejects_changed_missing_extra_and_symlink_bytes(
    tmp_path, monkeypatch, change
):
    deployment, path = local_prepared(tmp_path)

    def no_commands(*args, **kwargs):
        raise AssertionError(
            "File-only verification must not invoke acquisition or Docker"
        )

    monkeypatch.setattr(bootstrap, "command", no_commands)
    assert deployment.verify_prepared()[0]["sha256"] == bootstrap.sha256(path)
    if change == "modified":
        path.write_bytes(b"changed contents")
    elif change == "missing":
        path.unlink()
    elif change == "extra":
        (path.parent / "unregistered").write_bytes(b"extra")
    else:
        target = tmp_path / "outside"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    with pytest.raises(
        bootstrap.DeploymentError, match="prepared_file_(inventory_mismatch|symlink)"
    ):
        deployment.verify_prepared()


def test_profile_collision_and_owner_mismatch_refuse_destructive_commands(
    tmp_path, monkeypatch
):
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    deployment = bootstrap.LocalDeployment(tmp_path, state=state)
    calls = []

    def inspect_only(argv, **_):
        calls.append(argv)
        assert argv == ["docker", "container", "inspect", bootstrap.PROFILE]
        return 0, json.dumps([{"Id": "foreign-container"}])

    monkeypatch.setattr(bootstrap, "command", inspect_only)
    with pytest.raises(bootstrap.DeploymentError, match="profile_collision"):
        deployment.owner(create=True)
    bootstrap.atomic_json(
        state / "owner.json",
        {
            "profile": bootstrap.PROFILE,
            "uid": os.getuid(),
            "state": str(state),
            "owner_id": "ours",
            "container_id": "our-container",
        },
    )
    with pytest.raises(bootstrap.DeploymentError, match="profile_container_collision"):
        deployment.down()
    with pytest.raises(
        bootstrap.DeploymentError, match="explicit_reset_confirmation_required"
    ):
        deployment.reset(
            confirmation="another-profile", backup_directory=tmp_path / "backup"
        )
    assert len(calls) == 2
    assert not (tmp_path / "backup").exists()


@pytest.mark.parametrize(
    "defect",
    ["memory", "disk", "cpu", "platform", "machine", "rootless", "docker_os", "docker_failure"],
)
def test_host_preflight_refuses_before_any_cluster_mutation(
    tmp_path, monkeypatch, defect
):
    deployment = bootstrap.LocalDeployment(tmp_path, state=tmp_path / "state")
    deployment.state.mkdir(mode=0o700)
    monkeypatch.setattr(deployment, "owner", lambda **_: {"container_id": None})
    monkeypatch.setattr(deployment, "verify_prepared", list)
    original = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *a, **k: (
            (
                "MemAvailable: 10485759 kB\n"
                if defect == "memory"
                else "MemAvailable: 20971520 kB\n"
            )
            if str(path) == "/proc/meminfo"
            else original(path, *a, **k)
        ),
    )

    monkeypatch.setattr(
        bootstrap.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(free=(29 if defect == "disk" else 100) * 1024**3),
    )
    monkeypatch.setattr(bootstrap.os, "cpu_count", lambda: 2 if defect == "cpu" else 16)
    monkeypatch.setattr(
        bootstrap.platform,
        "system",
        lambda: "Darwin" if defect == "platform" else "Linux",
    )
    monkeypatch.setattr(
        bootstrap.platform, "machine", lambda: "aarch64" if defect == "machine" else "x86_64"
    )
    commands = []
    original_command = bootstrap.command
    child_starts = []

    class FailedDockerInfo:
        returncode = 1

        def __init__(self, argv, **kwargs):
            child_starts.append(argv)
            assert argv == ["docker", "info", "--format", "{{json .}}"]
            assert kwargs["start_new_session"] is True
            assert kwargs["stdin"] == subprocess.DEVNULL

        def communicate(self, input, timeout):
            assert input is None
            assert 0 < timeout <= 120
            return b"synthetic private daemon diagnostic", None

    if defect == "docker_failure":
        # Exercise the real command error mapping without starting any process.
        monkeypatch.setattr(bootstrap.subprocess, "Popen", FailedDockerInfo)

    def no_command(argv, **__):
        commands.append(argv)
        assert defect in {"rootless", "docker_os", "docker_failure"}
        assert argv == ["docker", "info", "--format", "{{json .}}"]
        if defect == "docker_failure":
            return original_command(argv)
        return 0, json.dumps(
            {
                "OSType": "windows" if defect == "docker_os" else "linux",
                "SecurityOptions": ["name=rootless"] if defect == "rootless" else [],
            }
        )

    monkeypatch.setattr(bootstrap, "command", no_command)
    reasons = {
        "memory": "host_memory_below_10_gib",
        "disk": "host_disk_below_30_gib",
        "cpu": "host_cpu_below_6",
        "platform": "unsupported_host_platform",
        "machine": "unsupported_host_platform",
        "rootless": "unsupported_docker_mode",
        "docker_os": "unsupported_docker_mode",
        "docker_failure": "command_failed_docker",
    }
    with pytest.raises(bootstrap.DeploymentError, match="^" + reasons[defect] + "$"):
        deployment.up()
    assert len(commands) == int(defect in {"rootless", "docker_os", "docker_failure"})
    assert len(child_starts) == int(defect == "docker_failure")
    assert not (deployment.state / "owner.json").exists()


def test_deployment_command_deadline_kills_descendant_processes(tmp_path):
    pid_file = tmp_path / "child.pid"
    helper = tmp_path / "helper.py"
    helper.write_text(
        'import subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\nPath(sys.argv[1]).write_text(str(p.pid))\ntime.sleep(60)\n'
    )
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        bootstrap.command([sys.executable, str(helper), str(pid_file)], timeout=1)
    assert time.monotonic() - started < 3
    pid = int(pid_file.read_text())

    def child_running():
        try:
            return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
        except FileNotFoundError, ProcessLookupError:
            return False

    for _ in range(100):
        if not child_running():
            break
        time.sleep(0.01)
    else:
        pytest.fail("Descendant remained running after deadline")
    assert not child_running()


def test_prepared_tls_has_private_ancestors_and_preserves_exact_bytes_on_replay(
    tmp_path,
):
    state = tmp_path / "deployment"
    state.mkdir(mode=0o700)
    deployment = bootstrap.LocalDeployment(ROOT, state=state)
    previous_umask = os.umask(0o022)
    try:
        deployment.prepare_certificates()
    finally:
        os.umask(previous_umask)
    tls = state / "assembly/tls"
    directories = [state / "assembly", tls, *(p for p in tls.rglob("*") if p.is_dir())]
    assert len(list(tls.rglob("tls.key"))) == 7
    assert all(p.stat().st_mode & 0o777 == 0o700 for p in directories)
    files = [p for p in state.rglob("*") if p.is_file()]
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in files)
    before = {p: p.read_bytes() for p in files}
    deployment.prepare_certificates()
    assert {p: p.read_bytes() for p in files} == before
    assert all(p.stat().st_mode & 0o777 == 0o700 for p in directories)


@pytest.mark.parametrize("defect", ["public", "symlink"])
def test_certificate_preparation_refuses_untrusted_prefix_before_target_writes(
    tmp_path, defect
):
    state = tmp_path / "deployment"
    state.mkdir(mode=0o700)
    deployment = bootstrap.LocalDeployment(ROOT, state=state)
    target = state / "assembly"
    if defect == "public":
        target.mkdir(mode=0o755)
        target.chmod(0o755)
        outside = target
    else:
        outside = tmp_path / "outside"
        outside.mkdir(mode=0o700)
        target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="^certificate_directory_not_private$"):
        deployment.prepare_certificates()
    assert list(outside.iterdir()) == []
    assert not (state / "assembly/tls").exists()
