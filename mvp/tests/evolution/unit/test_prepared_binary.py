"""Preparation must preserve an executing owned tool's inode and process."""

import errno
import importlib.util
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "binary_bootstrap_test", ROOT / "deploy/evolution/bootstrap.py"
)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def test_preparation_reuses_running_exact_binary_and_atomically_replaces_changed_bytes(
    tmp_path,
):
    source, target = tmp_path / "source", tmp_path / "sleep"
    shutil.copyfile(shutil.which("sleep"), source)
    expected = bootstrap.sha256(source)
    bootstrap.install_binary(source, target, expected)
    process = subprocess.Popen(
        [str(target), "60"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 3
        while Path(f"/proc/{process.pid}/exe").stat().st_ino != target.stat().st_ino:
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.005)
        original = target.stat().st_ino
        with pytest.raises(OSError) as error:
            os.open(target, os.O_WRONLY | os.O_TRUNC)
        assert error.value.errno == errno.ETXTBSY
        bootstrap.install_binary(source, target, expected)
        assert target.stat().st_ino == original
        assert target.stat().st_mode & 0o777 == 0o700
        assert bootstrap.sha256(target) == expected
        assert process.poll() is None
        target.chmod(0o600)
        bootstrap.install_binary(source, target, expected)
        assert target.stat().st_ino != original
        assert target.stat().st_mode & 0o777 == 0o700
        assert Path(f"/proc/{process.pid}/exe").stat().st_ino == original
        assert process.poll() is None
        source.write_bytes(source.read_bytes() + b"synthetic-new-artifact-trailer")
        replacement = bootstrap.sha256(source)
        bootstrap.install_binary(source, target, replacement)
        assert target.stat().st_ino != original
        assert Path(f"/proc/{process.pid}/exe").stat().st_ino == original
        assert process.poll() is None
        assert bootstrap.sha256(target) == replacement
        assert (
            subprocess.run([str(target), "0.01"], timeout=3, check=False).returncode
            == 0
        )
        assert not list(tmp_path.glob(".sleep-*"))
    finally:
        process.terminate()
        process.wait(timeout=3)


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_prepared_binary_refuses_unsafe_target_without_touching_source(tmp_path, kind):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"synthetic-artifact")
    if kind == "symlink":
        target.symlink_to(source)
    else:
        os.mkfifo(target)
    before = source.read_bytes()
    with pytest.raises(
        bootstrap.DeploymentError, match="prepared_binary_target_invalid"
    ):
        bootstrap.install_binary(source, target, bootstrap.sha256(source))
    assert source.read_bytes() == before
    assert (
        target.is_symlink()
        if kind == "symlink"
        else target.lstat().st_mode & 0o170000 == 0o010000
    )


@pytest.mark.parametrize("kind", ["bad-digest", "source-fifo"])
def test_prepared_binary_failure_preserves_old_tool_and_removes_temporary_files(
    tmp_path, kind
):
    source, target = tmp_path / "source", tmp_path / "target"
    target.write_bytes(b"old-prepared-tool")
    if kind == "source-fifo":
        os.mkfifo(source)
    else:
        source.write_bytes(b"changed-source")
    with pytest.raises(
        bootstrap.DeploymentError,
        match="prepared_binary_(integrity_failed|source_invalid)",
    ):
        bootstrap.install_binary(source, target, "0" * 64)
    assert target.read_bytes() == b"old-prepared-tool"
    assert not list(tmp_path.glob(".target-*"))
