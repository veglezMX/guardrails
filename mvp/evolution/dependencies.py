"""Online dependency preparation; gates consume verified local bytes only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import runpy
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from email.parser import BytesParser
from importlib import metadata
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
from zipfile import ZipFile

from .registry import EvidenceError, read_json

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def declarations(root: Path):
    project = tomllib.loads((root / "pyproject.toml").read_text())
    return {
        "python": project["project"]["requires-python"],
        "dependencies": project["project"]["dependencies"],
        "extras": project["project"]["optional-dependencies"],
        "build": project["build-system"]["requires"],
    }


def pins(path: Path) -> dict[str, str]:
    result = {}
    current = None
    hashes = set()
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("--hash=sha256:"):
            if current is None or not re.fullmatch(
                r"--hash=sha256:[0-9a-f]{64}(?: \\)?", stripped
            ):
                raise EvidenceError("invalid_lock_hash")
            hashes.add(current)
            continue
        match = re.fullmatch(r"([A-Za-z0-9._-]+)==([^ ;\\]+)(?: \\)?", stripped)
        if not match:
            raise EvidenceError("dependency_not_hash_locked")
        current = re.sub(r"[-_.]+", "-", match[1]).lower()
        if current in result:
            raise EvidenceError("duplicate_dependency_pin")
        result[current] = match[2]
    if not result or set(result) != hashes:
        raise EvidenceError("dependency_hash_missing")
    return result


def verify_environment(lock: Path):
    if sys.version_info[:2] != (3, 14):
        raise EvidenceError("python_version_mismatch")
    expected = pins(lock)
    for package, version in expected.items():
        try:
            actual = metadata.version(package)
        except metadata.PackageNotFoundError as exc:
            raise EvidenceError("dependency_missing") from exc
        if actual != version:
            raise EvidenceError("dependency_version_mismatch")
    return expected


def verify_artifacts(root: Path):
    state = root / ".evolution"
    manifest = read_json(state / "dependencies.json")
    if manifest.get("lock_digest") != digest(root / "evolution-requirements.lock"):
        raise EvidenceError("prepared_lock_stale")
    if (
        manifest.get("declarations_digest")
        != hashlib.sha256(canonical(declarations(root))).hexdigest()
    ):
        raise EvidenceError("prepared_declarations_stale")
    artifacts = manifest.get("artifacts", [])
    if not artifacts:
        raise EvidenceError("artifact_pins_missing")
    wheelhouse = state / manifest["wheelhouse"]
    if wheelhouse.parent != state or wheelhouse.is_symlink():
        raise EvidenceError("invalid_artifact_path")
    names = set()
    packages = {}
    for artifact in artifacts:
        path = wheelhouse / artifact["name"]
        if path.parent != wheelhouse or path.is_symlink() or not path.is_file():
            raise EvidenceError("artifact_missing")
        if (
            artifact["name"] in names
            or artifact["sha256"] != digest(path)
            or artifact["size_bytes"] != path.stat().st_size
        ):
            raise EvidenceError("artifact_integrity_failed")
        names.add(artifact["name"])
        package, version = wheel_identity(path)
        packages[package] = version
    if {path.name for path in wheelhouse.iterdir()} != names or packages != pins(
        root / "evolution-requirements.lock"
    ):
        raise EvidenceError("artifact_inventory_mismatch")
    return artifacts


def verify_runtime_artifacts(root: Path):
    """Inspect prepared local images without any pull or registry traffic."""
    manifest = read_json(root / "deploy/evolution/artifacts.lock.json")
    if manifest.get("schema_version") != "guardrails-evolution-artifacts-v1":
        raise EvidenceError("runtime_artifact_schema_invalid")
    artifacts = manifest.get("artifacts", [])
    if not artifacts or len({item["name"] for item in artifacts}) != len(artifacts):
        raise EvidenceError("runtime_artifact_inventory_invalid")
    for artifact in artifacts:
        if artifact["kind"] != "image" or not re.fullmatch(
            r"[0-9a-f]{64}", artifact["sha256"]
        ):
            raise EvidenceError("runtime_artifact_pin_invalid")
        pin = "sha256:" + artifact["sha256"]
        if not artifact["source_url"].startswith("docker://") or not artifact["source_url"].endswith("@" + pin):
            raise EvidenceError("runtime_artifact_pin_invalid")
        reference = artifact["source_url"].removeprefix("docker://")
        try:
            process = subprocess.run(
                ["docker", "image", "inspect", reference],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if process.returncode:
                raise EvidenceError("runtime_image_missing")
            image = json.loads(process.stdout)[0]
            observed = image["Id"] == pin or any(
                item.endswith("@" + pin) for item in image.get("RepoDigests", [])
            )
            if not observed or image["Size"] != artifact["size_bytes"]:
                raise EvidenceError("runtime_image_mismatch")
        except OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired:
            raise EvidenceError("runtime_image_unavailable") from None
    return artifacts


def verify_deployment_artifacts(root: Path):
    """Phase 2+ preparation is verified offline, including exact derived bytes."""
    path = root / "deploy/evolution/bootstrap.py"
    if path.is_symlink() or not path.is_file():
        raise EvidenceError("deployment_preparation_missing")
    module = runpy.run_path(str(path))
    try:
        artifacts = module["verify_prepared"](root)
    except module["DeploymentError"] as exc:
        reason = str(exc)
        raise EvidenceError(reason if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", reason) else "deployment_preparation_invalid") from None
    from guardrails.evolution.models import ArtifactRef

    for artifact in artifacts:
        ArtifactRef.model_validate_json(canonical(artifact))
    return artifacts


def phase_artifacts(root: Path, phase: int):
    """Close image, dependency and prepared-data inventories without duplicates."""
    artifacts = verify_artifacts(root) + verify_runtime_artifacts(root)
    if phase >= 2:
        artifacts += verify_deployment_artifacts(root)
    result = {}
    for artifact in artifacts:
        name = artifact["name"]
        if name in result and result[name] != artifact:
            raise EvidenceError("artifact_name_conflict")
        result[name] = artifact
    return list(result.values())


def wheel_identity(path):
    with ZipFile(path) as archive:
        records = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA") and name.count("/") == 1
        ]
        if len(records) != 1:
            raise EvidenceError("invalid_wheel_metadata")
        message = BytesParser().parsebytes(archive.read(records[0]))
    return re.sub(r"[-_.]+", "-", message["Name"]).lower(), message["Version"]


def smoke():
    from importlib import import_module

    import jsonschema
    import pydantic

    for module in (
        "alembic",
        "cryptography",
        "fastapi",
        "httpx",
        "jinja2",
        "prometheus_client",
        "psycopg",
        "sqlalchemy",
        "uvicorn",
    ):
        import_module(module)

    class Smoke(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(extra="forbid", strict=True)
        value: int

    if Smoke(value=1).model_dump() != {"value": 1}:
        raise EvidenceError("schema_smoke_failed")
    jsonschema.Draft202012Validator.check_schema(
        {"type": "object", "additionalProperties": False}
    )
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    key.public_key().verify(key.sign(b"evolution-smoke"), b"evolution-smoke")


def prepare(root=ROOT, *, refresh=False):
    if sys.version_info[:2] != (3, 14):
        raise EvidenceError("python_version_mismatch")
    state = root / ".evolution"
    state.mkdir(exist_ok=True, mode=0o700)
    lock = root / "evolution-requirements.lock"
    if refresh or not lock.exists():
        command = [
            "uv",
            "pip",
            "compile",
            "pyproject.toml",
            "--all-extras",
            "--python-version",
            "3.14",
            "--generate-hashes",
            "--output-file",
            str(lock),
            "--quiet",
        ]
        if refresh:
            command.append("--upgrade")
        subprocess.run(command, check=True, cwd=root)
    pins(lock)
    venv = state / "venv314"
    if not venv.exists():
        subprocess.run(
            ["uv", "venv", str(venv), "--python", sys.executable], check=True, cwd=root
        )
    python = venv / "bin/python"
    subprocess.run(
        ["uv", "pip", "sync", "--python", str(python), "--require-hashes", str(lock)],
        check=True,
        cwd=root,
    )
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), "--no-deps", "--no-build-isolation", "--editable", str(root)],
        check=True,
        cwd=root,
    )
    wheelhouse = state / ("wheels-" + str(uuid4()))
    wheelhouse.mkdir(mode=0o700)
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "--require-hashes",
            "--dest",
            str(wheelhouse),
            "-r",
            str(lock),
            "--quiet",
        ],
        check=True,
        cwd=root,
    )
    artifacts = []
    for wheel in sorted(wheelhouse.iterdir()):
        package, version = wheel_identity(wheel)
        with ZipFile(wheel) as archive:
            name = next(
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA") and name.count("/") == 1
            )
            metadata_record = BytesParser().parsebytes(archive.read(name))
        license_name = (
            metadata_record.get("License-Expression")
            or metadata_record.get("License")
            or "See wheel license files"
        )
        # Preserve provenance without embedding an entire license text in reports.
        if len(license_name) > 200:
            license_name = "See wheel license files"
        artifacts.append(
            {
                "name": wheel.name,
                "version": version,
                "source_url": f"https://pypi.org/project/{quote(package)}/{quote(version)}/",
                "sha256": digest(wheel),
                "license": license_name,
                "size_bytes": wheel.stat().st_size,
                "kind": "wheel",
            }
        )
    manifest = {
        "schema_version": "guardrails-evolution-dependencies-v1",
        "prepared_at": datetime.now(UTC).isoformat(),
        "lock_digest": digest(lock),
        "declarations_digest": hashlib.sha256(
            canonical(declarations(root))
        ).hexdigest(),
        "wheelhouse": wheelhouse.name,
        "artifacts": artifacts,
    }
    path = state / "dependencies.json"
    if path.exists():
        path.rename(state / ("dependencies.previous-" + str(uuid4()) + ".json"))
    with path.open("x") as output:
        json.dump(manifest, output, indent=2, sort_keys=True)
    env = dict(os.environ, PYTHONPATH=str(root / "mvp"))
    subprocess.run(
        [str(python), "-m", "evolution.dependencies", "--smoke"],
        check=True,
        cwd=root,
        env=env,
    )
    print(
        json.dumps(
            {
                "dependencies_ready": True,
                "lock_digest": digest(lock),
                "artifacts": len(artifacts),
            }
        )
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-locks", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.smoke:
            verify_environment(ROOT / "evolution-requirements.lock")
            verify_artifacts(ROOT)
            smoke()
        else:
            prepare(refresh=args.refresh_locks)
    except (EvidenceError, subprocess.CalledProcessError) as exc:
        print(
            str(exc)
            if isinstance(exc, EvidenceError)
            else "dependency_preparation_failed",
            file=sys.stderr,
        )
        return 1
    return 0


def verify_installed_wheels(root: Path):
    """Compare imported-installation candidates against the pinned wheel bytes.

    RECORD and generated entry-point scripts are installer metadata; package code,
    extension modules, data and dist-info metadata must remain byte-identical.
    """
    import sysconfig

    manifest = read_json(root / ".evolution/dependencies.json")
    wheelhouse = root / ".evolution" / manifest["wheelhouse"]
    schemes = sysconfig.get_paths()
    for artifact in manifest["artifacts"]:
        wheel = wheelhouse / artifact["name"]
        package, _ = wheel_identity(wheel)
        distribution = metadata.distribution(package)
        with ZipFile(wheel) as archive:
            for member in archive.infolist():
                name = member.filename
                if member.is_dir() or name.endswith(".dist-info/RECORD"):
                    continue
                parts = Path(name).parts
                if ".." in parts or Path(name).is_absolute():
                    raise EvidenceError("invalid_wheel_path")
                if parts[0].endswith(".data"):
                    if len(parts) < 3 or parts[1] not in {
                        "purelib",
                        "platlib",
                        "data",
                        "headers",
                        "scripts",
                    }:
                        raise EvidenceError("invalid_wheel_path")
                    if parts[1] in {"headers", "scripts"}:
                        continue
                    path = Path(schemes[parts[1]]).joinpath(*parts[2:])
                else:
                    path = Path(distribution.locate_file(name))
                if (
                    not path.is_file()
                    or digest(path) != hashlib.sha256(archive.read(member)).hexdigest()
                ):
                    raise EvidenceError("installed_dependency_integrity_failed")


if __name__ == "__main__":
    sys.exit(main())
