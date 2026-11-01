"""A changed installed dependency cannot produce a signed canary report."""

import importlib.metadata
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from https_support import serve_https

ROOT = Path(__file__).resolve().parents[4]


def test_canary_rejects_tampered_installed_code_before_http_or_report(tmp_path):
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    package = importlib.metadata.distribution("httpx")
    shutil.copytree(Path(package.locate_file("httpx")), shadow / "httpx")
    metadata_path = next(
        Path(package.locate_file(name)).parent
        for name in package.files
        if str(name).endswith(".dist-info/METADATA")
    )
    shutil.copytree(metadata_path, shadow / metadata_path.name)
    code = shadow / "httpx" / "_config.py"
    code.write_bytes(
        code.read_bytes()
        + b"\n# Deliberately changed installed bytes for integrity rejection.\n"
    )
    runtime_id, release_id = str(uuid4()), str(uuid4())
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps({"chat": [runtime_id]}))
    credential = tmp_path / "operator.token"
    credential.write_text("synthetic-credential-must-never-reach-http")
    credential.chmod(0o600)
    key = tmp_path / "snapshot.pub"
    key.write_bytes(
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    requests = multiprocessing.Value("i", 0)

    def application():
        app = FastAPI()

        @app.get("/health/live")
        async def live():
            return {"status": "live"}

        @app.get("/v2/tenants/{tenant}/releases/{release}")
        async def forbidden_dispatch(tenant, release):
            with requests.get_lock():
                requests.value += 1
            return {}

        return app

    before = set((ROOT / ".evolution/reports").glob("*/report.json"))
    with serve_https(application, tmp_path / "tls") as server:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "evolution.canary",
                "--url",
                server["url"],
                "--ca-file",
                server["ca_file"],
                "--credential-file",
                str(credential),
                "--tenant",
                "canary-check",
                "--release-id",
                release_id,
                "--targets-file",
                str(targets),
                "--snapshot-key-file",
                str(key),
                "--snapshot-key-id",
                "test-key",
                "--runtime-cache",
                runtime_id + "=" + str(tmp_path / "unused-cache"),
                "--submit",
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "PYTHONPATH": str(shadow) + os.pathsep + str(ROOT / "mvp"),
            },
            capture_output=True,
            text=True,
            timeout=20,
        )
    assert result.returncode == 6
    assert json.loads(result.stdout) == {
        "status": "fail",
        "reason": "canary_observation_failed",
    }
    assert requests.value == 0
    assert set((ROOT / ".evolution/reports").glob("*/report.json")) == before
    assert credential.read_text() not in result.stdout + result.stderr
