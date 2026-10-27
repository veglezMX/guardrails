"""Actual pinned native client paths must traverse the owned model transport."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from evolution import native_transport_measurements as observation

ROOT = Path(__file__).resolve().parents[4]
IMAGE = "ghcr.io/berriai/litellm@sha256:3d88bd757134a00e8c00237b85a021ab1f6d54abb9c1df750f411dbe7256a500"


def run_probe(*, production_entrypoint=False):
    case_id = ("GW-NATIVE-PRODUCTION-MODULE-IDENTITY" if production_entrypoint
               else "GW-NATIVE-TRANSPORT-PINNED-OPENAI-OLLAMA-OWNED-TRANSPORT")
    sources = observation.frozen_sources(ROOT, case_id)
    name = "evolution-native-transport-" + uuid4().hex
    result, command_error = None, None
    command = ["docker", "run", "--name", name, "--pull=never", "--network=none", "--read-only",
               "--user", f"{os.getuid()}:{os.getgid()}", "--cap-drop=ALL", "--security-opt=no-new-privileges",
               "--log-driver=none", "--cpus=2", "--memory=1536m", "--pids-limit=128",
               "--tmpfs", "/fixture:rw,nosuid,nodev,noexec,size=64m,mode=1777",
               "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
               "--env", "PYTHONPATH=/evolution", "--env", "LITELLM_LOCAL_MODEL_COST_MAP=True",
               "--volume", f"{ROOT / 'mvp'}:/evolution:ro", "--entrypoint", "python", IMAGE,
               "-B", "/evolution/tests/evolution/e2e/native_transport_probe.py"]
    if production_entrypoint:
        command.append("--production-entrypoint")
    started = time.monotonic()
    try:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=210, check=False)
        except BaseException as error:
            command_error = error
            raise
        finally:
            removed = subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=10, check=False)
            absent = subprocess.run(["docker", "ps", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.Names}}"],
                                    capture_output=True, timeout=5, check=False)
            assert removed.returncode == 0 and absent.returncode == 0 and not absent.stdout.strip(), "owned_fixture_cleanup"
        assert result is not None
        records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        print(json.dumps({"native_observations": records}, sort_keys=True))
        reports = [row for row in records if row.get("status") in {"pass", "fail"}]
        assert result.returncode == 0, [row for row in records if row.get("status") == "fail" or row.get("event") == "shutdown"]
        return records, reports
    finally:
        original_failure = sys.exc_info()[0] is not None
        try:
            observation.record(result, command_error, round((time.monotonic() - started) * 1000, 3),
                               case_id=case_id, root=ROOT, sources=sources)
        except BaseException:
            if not original_failure:
                raise


def test_native_openai_and_ollama_share_guarded_transport_without_retry_or_fallback():
    records, reports = run_probe()
    assert reports == [{"status": "pass", "checks": 195, "fixture": "synthetic-native-http-providers",
                        "profiles": 2, "model_requests": 14, "sink_requests": 0, "runtime_requests": 34}]
    cases = [row for row in records if row.get("event") == "case"]
    assert len(cases) == 26
    assert {row["profile"] for row in cases} == {"deterministic_mock", "local_model"}
    assert all(row["dispatches"] == 0 for row in cases if row["kind"] in {
        "wrong-path", "wrong-model", "wrong-message", "forbidden-dns", "wrong-image", "connect-failure"})
    assert all(row["dispatches"] == 1 and row["status"] != 200 for row in cases if row["kind"] == "protocol-failure")
    assert all(row["status"] == 200 for row in cases if row["kind"] == "clean")


def test_production_module_entrypoint_preserves_guarded_native_provider_identity():
    records, reports = run_probe(production_entrypoint=True)
    assert reports == [{"status": "pass", "checks": 65, "fixture": "production-module-entrypoint",
                        "profiles": 2, "model_requests": 8, "sink_requests": 0, "runtime_requests": 12}]
    cases = [row for row in records if row.get("event") == "case"]
    assert len(cases) == 14
    assert {row["profile"] for row in cases} == {"deterministic_mock", "local_model"}
    assert all(row["status"] == 200 and row["dispatches"] == 1 for row in cases if row["kind"] == "clean")
    assert all(row["status"] != 200 and row["dispatches"] == 1 for row in cases if row["kind"] in {"redirect", "unsupported-response"})
    assert all(row["status"] != 200 and row["dispatches"] == 0 for row in cases if row["kind"].startswith("caller-"))
    shutdowns = [row for row in records if row.get("event") == "shutdown_duration"]
    assert len(shutdowns) == 2 and all(0 < row["elapsed_ms"] < 15_000 for row in shutdowns)
