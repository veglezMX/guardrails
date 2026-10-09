"""Resolve exact owned-service dependencies in the pinned target Python image.

Only bootstrap calls this online. Repeated runs consume committed locks; an
explicit refresh is needed to choose new package versions. Acceptance must never
call this module or an installer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON_BASE = "python:3.14.8-slim@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(args, cwd=ROOT, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def pins(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9.!+_-]+", line):
            raise RuntimeError(f"Non-exact package entry in {path.name}")
        name, version = line.split("==", 1)
        canonical = re.sub(r"[-_.]+", "-", name).lower()
        if canonical in entries:
            raise RuntimeError(f"Duplicate package pin in {path.name}")
        entries[canonical] = version
    if not entries:
        raise RuntimeError(f"Empty dependency lock: {path.name}")
    return entries


def docker_python(script: str, *, mounts: list[tuple[Path, str, bool]]) -> list[str]:
    command = [
        "docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
        "--env", "PIP_DISABLE_PIP_VERSION_CHECK=1", "--env", "PIP_NO_CACHE_DIR=1",
    ]
    for source, destination, readonly in mounts:
        command.extend(("--mount", f"type=bind,source={source},target={destination}" + (",readonly" if readonly else "")))
    return command + [PYTHON_BASE, "python", "-c", script]


def verify_wheelhouse(root: Path = ROOT) -> dict[str, str]:
    proof = root / ".proof"
    wheelhouse = proof / "wheelhouse"
    manifest_path = proof / "wheelhouse.sha256.json"
    manifest = json.loads(manifest_path.read_text())
    expected = manifest["artifacts"]
    if not isinstance(expected, dict) or not expected:
        raise RuntimeError("Wheelhouse hash manifest has no artifacts")
    actual_names = {item.name for item in wheelhouse.iterdir() if item.is_file()}
    if actual_names != set(expected):
        raise RuntimeError("Wheelhouse artifact inventory changed; rerun bootstrap with explicit refresh")
    for name, digest in expected.items():
        if Path(name).name != name or not name.endswith(".whl") or sha256(wheelhouse / name) != digest:
            raise RuntimeError("Wheelhouse artifact checksum verification failed")
    for name in ("requirements.lock", "dev-requirements.lock"):
        if manifest["locks"].get(name) != sha256(root / name):
            raise RuntimeError("Dependency lock changed since wheelhouse preparation")
    return expected


def resolve(*, refresh: bool = False, root: Path = ROOT) -> dict[str, object]:
    if root != ROOT:
        raise ValueError("Resolver must use its repository root")
    proof = root / ".proof"
    proof.mkdir(exist_ok=True, mode=0o700)
    runtime_lock, dev_lock = root / "requirements.lock", root / "dev-requirements.lock"
    project = tomllib.loads((root / "pyproject.toml").read_text())
    build_requirements = project["build-system"]["requires"]
    runtime_requirements = project["project"]["dependencies"] + build_requirements
    dev_requirements = project["project"].get("optional-dependencies", {}).get("dev", [])
    if not all(isinstance(item, str) for item in runtime_requirements + dev_requirements):
        raise RuntimeError("Dependency declarations must be strings")

    if refresh or not (runtime_lock.exists() and dev_lock.exists()):
        if not refresh and runtime_lock.exists() != dev_lock.exists():
            raise RuntimeError("Only one dependency lock exists; inspect it before explicit refresh")
        with tempfile.TemporaryDirectory(prefix="dependency-resolution-", dir=proof) as staging_name:
            staging = Path(staging_name)
            script = """
import json, os, subprocess, venv
from pathlib import Path
runtime = json.loads(os.environ["PROOF_RUNTIME_DEPENDENCIES"])
development = json.loads(os.environ["PROOF_DEV_DEPENDENCIES"])
results = {}
for name, requirements in (("runtime", runtime), ("dev", runtime + development)):
    environment = Path("/tmp") / ("proof-" + name)
    venv.create(environment, with_pip=True)
    python = str(environment / "bin/python")
    subprocess.run([python, "-m", "pip", "install", "--only-binary=:all:", *requirements], check=True)
    frozen = subprocess.check_output([python, "-m", "pip", "freeze"], text=True)
    target = "requirements.lock" if name == "runtime" else "dev-requirements.lock"
    Path("/out", target).write_text("\\n".join(sorted(frozen.splitlines(), key=str.lower)) + "\\n")
    results[name] = subprocess.check_output([python, "-m", "pip", "--version"], text=True).strip()
Path("/out/pip-versions.json").write_text(json.dumps(results))
"""
            command = docker_python(script, mounts=[(staging, "/out", False)])
            image_at = command.index(PYTHON_BASE)
            command[image_at:image_at] = [
                "--env", "PROOF_RUNTIME_DEPENDENCIES=" + json.dumps(runtime_requirements),
                "--env", "PROOF_DEV_DEPENDENCIES=" + json.dumps(dev_requirements),
            ]
            run(command)
            runtime_pins = pins(staging / runtime_lock.name)
            dev_pins = pins(staging / dev_lock.name)
            if any(dev_pins.get(name) != version for name, version in runtime_pins.items()):
                raise RuntimeError("Runtime and development resolution diverged; align explicit constraints before retry")
            for source, destination in ((staging / runtime_lock.name, runtime_lock), (staging / dev_lock.name, dev_lock)):
                temporary = destination.with_suffix(".lock.new")
                temporary.write_bytes(source.read_bytes())
                temporary.replace(destination)
            pip_versions = json.loads((staging / "pip-versions.json").read_text())
        write_json(proof / "dependency-resolution.json", {
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "python_base": PYTHON_BASE, "pip_versions": pip_versions,
            "direct_runtime": runtime_requirements, "direct_dev": dev_requirements,
            "pyproject_sha256": sha256(root / "pyproject.toml"),
            "locks": {path.name: sha256(path) for path in (runtime_lock, dev_lock)},
        })
    else:
        runtime_pins, dev_pins = pins(runtime_lock), pins(dev_lock)
        if any(dev_pins.get(name) != version for name, version in runtime_pins.items()):
            raise RuntimeError("Runtime and development locks contain incompatible pins")
        recorded_path = proof / "dependency-resolution.json"
        if recorded_path.exists():
            recorded = json.loads(recorded_path.read_text())
            if recorded.get("pyproject_sha256") != sha256(root / "pyproject.toml"):
                raise RuntimeError("pyproject changed; inspect dependencies and rerun with --refresh-locks")

    manifest_path = proof / "wheelhouse.sha256.json"
    if manifest_path.exists() and not refresh:
        verify_wheelhouse(root)
    else:
        # Download into a new directory and publish only after pip succeeds.
        with tempfile.TemporaryDirectory(prefix="wheel-download-", dir=proof) as temporary_name:
            temporary = Path(temporary_name)
            script = """
import subprocess
subprocess.run(["python", "-m", "pip", "download", "--only-binary=:all:", "--dest", "/out", "-r", "/locks/dev-requirements.lock"], check=True)
"""
            run(docker_python(script, mounts=[(dev_lock, "/locks/dev-requirements.lock", True), (temporary, "/out", False)]))
            inventory = {path.name: sha256(path) for path in sorted(temporary.iterdir()) if path.is_file()}
            if not inventory or any(not name.endswith(".whl") for name in inventory):
                raise RuntimeError("Dependency preparation did not produce only binary wheels")
            wheelhouse = proof / "wheelhouse"
            if wheelhouse.exists():
                # Keep the prior wheel set as evidence when an explicit refresh
                # replaces it; never discard it silently.
                backup = proof / ("wheelhouse.previous-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
                wheelhouse.rename(backup)
            temporary.rename(wheelhouse)
        write_json(manifest_path, {
            "python_base": PYTHON_BASE, "artifacts": inventory,
            "locks": {path.name: sha256(path) for path in (runtime_lock, dev_lock)},
        })
        verify_wheelhouse(root)
    return {
        "runtime_packages": pins(runtime_lock), "development_packages": pins(dev_lock),
        "locks": {path.name: sha256(path) for path in (runtime_lock, dev_lock)},
        "wheelhouse_manifest_sha256": sha256(manifest_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-locks", action="store_true")
    arguments = parser.parse_args()
    result = resolve(refresh=arguments.refresh_locks)
    print(json.dumps({"dependency_artifacts_ready": True, "locks": result["locks"]}, sort_keys=True))


if __name__ == "__main__":
    main()
