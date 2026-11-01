"""Native pinned LiteLLM lifecycle over real TLS; runtime/model are explicit fixtures."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from evolution.diagnostics import SourceCatalog, read_catalog
from evolution.measurements import NATIVE_CASE, validate_native_fixture_observation
from evolution.registry import EvidenceError, unique_object

IMAGE = "ghcr.io/berriai/litellm@sha256:3d88bd757134a00e8c00237b85a021ab1f6d54abb9c1df750f411dbe7256a500"
ROOT = Path(__file__).resolve().parents[4]


PROBE = "mvp/tests/evolution/e2e/native_gateway_probe.py"
OBSERVATION_MARKER = "native_fixture_observation"
MAX_CAPTURE_BYTES = 2 * 1024 * 1024


def frozen_observation_sources():
    """Bind exact source before the original subprocess begins."""
    directory = os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR")
    if directory is not None:
        path = Path(directory)
        info = path.lstat()
        if (not path.is_absolute() or path.is_symlink() or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise EvidenceError("measurement_path_invalid")
        sources = read_catalog(path.parents[1] / (NATIVE_CASE + ".sources.json"), ROOT)
    else:
        # Standalone pytest still freezes the named source before execution;
        # this is an explicit binding, never validation against an unbound AST.
        sources = {PROBE: hashlib.sha256((ROOT / PROBE).read_bytes()).hexdigest()}
    SourceCatalog(ROOT, sources).load(PROBE)
    return sources


def unavailable_observation(outcome, returncode, elapsed_ms, failure_bin):
    return {
        "schema_version": "evolution-native-fixture-observation-v1",
        "command_outcome": outcome, "returncode": returncode, "elapsed_ms": elapsed_ms,
        "probe_status": "unavailable", "stage": "unavailable", "gateway_index": None,
        "failure_bin": failure_bin, "error_category": None, "checks_completed": None,
        "model_requests": None, "runtime_requests": None, "readiness_status": None,
        "readiness_code": None, "frames": [], "cleanup_failed": None,
    }


def project_observation(result, error, elapsed_ms, sources):
    """Observe the child only; outer Docker cleanup remains a wrapper failure."""
    if error is not None:
        timeout = isinstance(error, subprocess.TimeoutExpired)
        return unavailable_observation("timeout" if timeout else "command_error", None, elapsed_ms,
                                       "command_timeout" if timeout else "command_error")
    value = unavailable_observation("returned", result.returncode, elapsed_ms, "observation_missing")
    try:
        # Complete capture only: a prefix or overlong/duplicate/malformed marker
        # is never accepted as a complete terminal observation.
        if type(result.stdout) is not str or len(result.stdout.encode()) > MAX_CAPTURE_BYTES:
            raise ValueError()
        marker_lines = [line for line in result.stdout.splitlines() if OBSERVATION_MARKER in line]
        if not marker_lines:
            validate_native_fixture_observation(value, root=ROOT, sources=sources)
            return value
        if len(marker_lines) != 1 or len(marker_lines[0].encode()) > 16384:
            raise ValueError()
        report = json.loads(marker_lines[0], object_pairs_hook=unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(report) is not dict or set(report) != {OBSERVATION_MARKER}:
            raise ValueError()
        observed = report[OBSERVATION_MARKER]
        allowed = set(value) - {"schema_version", "command_outcome", "returncode", "elapsed_ms"}
        if type(observed) is not dict or set(observed) != allowed:
            raise ValueError()
        value.update(observed)
        validate_native_fixture_observation(value, root=ROOT, sources=sources)
        return value
    except (EvidenceError, ValueError, TypeError, UnicodeError, RecursionError):
        return unavailable_observation("returned", result.returncode, elapsed_ms, "observation_invalid")


def record_observation(result, error, started, sources):
    value = project_observation(result, error, round((time.monotonic() - started) * 1000, 3), sources)
    validate_native_fixture_observation(value, root=ROOT, sources=sources)
    if directory := os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR"):
        path = Path(directory)
        info = path.lstat()
        if (not path.is_absolute() or path.is_symlink() or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise EvidenceError("measurement_path_invalid")
        raw = json.dumps(value, sort_keys=True, allow_nan=False).encode() + b"\n"
        if len(raw) > 16384:
            raise EvidenceError("measurement_schema_invalid")
        fd = os.open(path / "native-fixture-observation.json",
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())


def test_two_native_gateways_inspect_transform_block_and_reject_bypass_offline():
    sources = frozen_observation_sources()
    result, command_error = None, None
    started = time.monotonic()
    name = "evolution-native-fixture-" + uuid4().hex
    command = ["docker", "run", "--rm", "--name", name, "--pull=never", "--network=none", "--read-only",
        "--user", f"{os.getuid()}:{os.getgid()}", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--log-driver=none",
        "--tmpfs", "/fixture:rw,nosuid,nodev,noexec,size=64m,mode=1777",
        "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
        "--env", "PYTHONPATH=/evolution", "--env", "LITELLM_LOCAL_MODEL_COST_MAP=True",
        "--volume", f"{ROOT / 'mvp'}:/evolution:ro", "--entrypoint", "python", IMAGE,
        "/evolution/tests/evolution/e2e/native_gateway_probe.py"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    except BaseException as error:
        command_error = error
        raise
    finally:
        # The randomly named container belongs only to this test; never touch the
        # task cluster or a user's pre-existing container on timeout.
        try:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=15, check=False)
        finally:
            record_observation(result, command_error, started, sources)
    records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    reports = [row for row in records if row.get("status") in {"pass", "fail"}]
    assert result.returncode == 0, records
    assert len(reports) == 1
    report = reports[0]
    assert report["status"] == "pass" and report["checks"] == 57
    assert report["fixture"] == "synthetic-runtime-and-model"
    assert report["gateway_count"] == 2 and report["litellm_version"] == "1.104.2"
    assert report["model_requests"] == 14 and report["runtime_requests"] == 26
