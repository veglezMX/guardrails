"""Native pinned LiteLLM lifecycle over real TLS; runtime/model are explicit fixtures."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

IMAGE = "ghcr.io/berriai/litellm@sha256:3d88bd757134a00e8c00237b85a021ab1f6d54abb9c1df750f411dbe7256a500"
ROOT = Path(__file__).resolve().parents[4]


def test_two_native_gateways_inspect_transform_block_and_reject_bypass_offline():
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
    finally:
        # The randomly named container belongs only to this test; never touch the
        # task cluster or a user's pre-existing container on timeout.
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=15, check=False)
    records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    reports = [row for row in records if row.get("status") in {"pass", "fail"}]
    assert result.returncode == 0, records
    assert len(reports) == 1
    report = reports[0]
    assert report["status"] == "pass" and report["checks"] == 57
    assert report["fixture"] == "synthetic-runtime-and-model"
    assert report["gateway_count"] == 2 and report["litellm_version"] == "1.104.2"
    assert report["model_requests"] == 14 and report["runtime_requests"] == 26
