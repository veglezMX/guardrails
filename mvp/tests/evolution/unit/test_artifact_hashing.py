"""Real-file integrity and concurrency tests for prepared artifact hashing."""

import errno
import hashlib
import importlib.util
import json
import os
import queue
import selectors
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "artifact_bootstrap", ROOT / "deploy/evolution/bootstrap.py"
)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_hash_refuses_actual_symlink_and_fifo_without_blocking(tmp_path, kind):
    target = tmp_path / "synthetic-artifact"
    if kind == "symlink":
        real = tmp_path / "real"
        real.write_bytes(b"synthetic artifact bytes")
        target.symlink_to(real)
    else:
        os.mkfifo(target, mode=0o600)
    script = """import importlib.util,json,sys
s=importlib.util.spec_from_file_location('b',sys.argv[1]);b=importlib.util.module_from_spec(s);s.loader.exec_module(b)
print('ready',flush=True)
if sys.stdin.readline() != 'run\\n': raise SystemExit(5)
try: b._hash_file(b.Path(sys.argv[2]))
except (OSError,b.DeploymentError) as e: print(json.dumps({'class':type(e).__name__,'errno':getattr(e,'errno',None),'code':str(e) if isinstance(e,b.DeploymentError) else None}))
else: raise SystemExit(4)
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-c",
            script,
            str(ROOT / "deploy/evolution/bootstrap.py"),
            str(target),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        # Import and interpreter startup have a separate bounded budget. Only
        # after this handshake does the strict nonblocking hash deadline begin.
        expected = b"ready\n"
        received = bytearray()
        deadline = time.monotonic() + 15
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while len(received) < len(expected):
                assert selector.select(max(0, deadline - time.monotonic())), (
                    "hash_child_startup_timeout"
                )
                chunk = os.read(child.stdout.fileno(), len(expected) - len(received))
                assert chunk, "hash_child_exited_before_ready"
                received.extend(chunk)
                assert expected.startswith(received), "hash_child_invalid_handshake"
        output, errors = child.communicate(input=b"run\n", timeout=3)
        assert child.returncode == 0
        assert errors == b""
        metadata = json.loads(output)
    finally:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=3)
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()
    if kind == "symlink":
        assert metadata == {"class": "OSError", "errno": errno.ELOOP, "code": None}
        assert real.read_bytes() == b"synthetic artifact bytes"
    else:
        assert metadata == {
            "class": "DeploymentError",
            "errno": None,
            "code": "hash_requires_regular_file",
        }
    assert target.is_symlink() if kind == "symlink" else target.is_fifo()


def on_first_read(monkeypatch, change):
    actual = hashlib.sha256

    class HashWithReadBoundary:
        def __init__(self):
            self.hasher = actual()
            self.changed = False

        def update(self, data):
            self.hasher.update(data)
            if not self.changed:
                self.changed = True
                change()

        def hexdigest(self):
            return self.hasher.hexdigest()

    monkeypatch.setattr(bootstrap.hashlib, "sha256", HashWithReadBoundary)


def test_hash_rejects_same_byte_inode_substitution_during_actual_read(
    tmp_path, monkeypatch
):
    path = tmp_path / "artifact"
    replacement = tmp_path / "replacement"
    payload = bytes(range(256)) * 8192 + b"tail"
    path.write_bytes(payload)
    replacement.write_bytes(payload)
    before = path.stat()
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert replacement.stat().st_ino != before.st_ino
    on_first_read(monkeypatch, lambda: os.replace(replacement, path))
    with pytest.raises(bootstrap.DeploymentError, match="^file_changed_during_hash$"):
        bootstrap._hash_file(path)
    assert path.stat().st_ino != before.st_ino
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert path.read_bytes() == payload


def test_hash_rejects_in_place_mutation_even_when_size_and_mtime_are_restored(
    tmp_path, monkeypatch
):
    path = tmp_path / "artifact"
    payload = b"A" * (2 * 1024 * 1024 + 17)
    path.write_bytes(payload)
    before = path.stat()

    def change():
        with path.open("r+b") as handle:
            handle.write(b"B")
            handle.flush()
            os.fsync(handle.fileno())
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    on_first_read(monkeypatch, change)
    with pytest.raises(bootstrap.DeploymentError, match="^file_changed_during_hash$"):
        bootstrap._hash_file(path)
    after = path.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    assert after.st_ctime_ns != before.st_ctime_ns
    assert path.read_bytes() == b"B" + payload[1:]


def prepared_fixture(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    deployment = bootstrap.LocalDeployment(tmp_path, state=state)
    names = [
        "calico-pinned.yaml",
        "bin/z-tool",
        "bin/a-tool",
        "downloads/z-large",
        "downloads/a-empty",
        ".minikube/cache/nested/z-image",
        ".minikube/cache/a-file",
        "images/z-image.tar",
        "images/a-image.tar",
        "models/z-model/blob",
        "credentials/ca.crt",
        "credentials/wrong-ca.crt",
        "assembly/tls/gr-platform/server/tls.crt",
        "builds/z-build.json",
        "builds/a-build.json",
    ]
    expected = []
    for index, name in enumerate(names):
        path = state / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = bytes([index]) * (0 if name.endswith("a-empty") else 1048583 + index)
        path.write_bytes(data)
        expected.append(
            {
                "path": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        )
    linked = state / "models/a-hardlink"
    linked.hardlink_to(state / "downloads/z-large")
    expected.append(
        {
            "path": "models/a-hardlink",
            "sha256": expected[3]["sha256"],
            "size_bytes": expected[3]["size_bytes"],
        }
    )
    for excluded in (
        "credentials/ca.key",
        "assembly/tls/gr-platform/server/tls.key",
        "untracked.txt",
    ):
        (state / excluded).write_bytes(b"excluded synthetic bytes")
    return deployment, sorted(expected, key=lambda item: item["path"])


def test_parallel_inventory_matches_sorted_sequential_full_bytes_with_three_workers(
    tmp_path, monkeypatch
):
    deployment, expected = prepared_fixture(tmp_path)
    actual_hash = bootstrap._hash_file
    gate = threading.Barrier(3)
    lock = threading.Lock()
    active = maximum = 0
    started = []
    completed = []

    def tracked(path):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            started.append(path)
            position = len(started)
        try:
            if position <= 3:
                gate.wait(timeout=3)
            return actual_hash(path)
        finally:
            with lock:
                active -= 1
                completed.append(path)

    monkeypatch.setattr(bootstrap, "_hash_file", tracked)
    inventory = deployment.file_inventory()
    assert inventory == expected
    assert [item["path"] for item in inventory] == sorted(
        item["path"] for item in inventory
    )
    assert maximum == 3
    assert active == 0
    assert len(started) == len(expected)
    assert sorted(started) == sorted(completed)
    assert len(set(started)) == len(started)  # Hardlinks still get separate full reads.
    assert bootstrap.sha256(deployment.state / "bin/a-tool") == next(
        x["sha256"] for x in expected if x["path"] == "bin/a-tool"
    )


def test_parallel_inventory_propagates_real_file_failure_and_joins_other_reads(
    tmp_path, monkeypatch
):
    deployment, _ = prepared_fixture(tmp_path)
    original = bootstrap._hash_file
    victim = deployment.state / ".minikube/cache/a-file"
    failed = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    outcomes = queue.Queue()

    def changed(path):
        nonlocal active
        with lock:
            active += 1
        try:
            if path == victim:
                path.unlink()
                os.mkfifo(path, mode=0o600)
                try:
                    return original(path)
                finally:
                    failed.set()
            assert release.wait(3)
            return original(path)
        finally:
            with lock:
                active -= 1

    def run():
        try:
            outcomes.put(deployment.file_inventory())
        except bootstrap.DeploymentError as error:
            outcomes.put(error)

    monkeypatch.setattr(bootstrap, "_hash_file", changed)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert failed.wait(3)
        assert thread.is_alive()
        assert outcomes.empty()
        release.set()
        thread.join(3)
        assert not thread.is_alive()
        outcome = outcomes.get_nowait()
        assert isinstance(outcome, bootstrap.DeploymentError)
        assert str(outcome) == "hash_requires_regular_file"
        assert active == 0
        assert victim.is_fifo()
    finally:
        release.set()
        thread.join(4)
