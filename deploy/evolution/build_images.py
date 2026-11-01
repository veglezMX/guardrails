"""Build owned images from bounded contexts containing no configuration secrets."""

from __future__ import annotations

import hashlib
import json
import os
import shutil


class BuildError(RuntimeError):
    pass


def prepare(deployment):
    d = deployment
    directory = d.state / "builds"
    directory.mkdir(mode=0o700, exist_ok=True)
    dependencies = d.read_json(d.root / ".evolution/dependencies.json")
    wheels = d.root / ".evolution" / dependencies["wheelhouse"]
    for item in dependencies["artifacts"]:
        path = wheels / item["name"]
        if (
            path.is_symlink()
            or d.sha256(path) != item["sha256"]
            or path.stat().st_size != item["size_bytes"]
        ):
            raise BuildError("build_wheel_integrity_failed")
    results = []
    for component in ("service", "gateway", "model"):
        context = directory / (component + "-context")
        if context.exists():
            shutil.rmtree(context)
        context.mkdir(mode=0o700)
        dockerfile = d.root / "deploy/evolution/images" / (component + ".Dockerfile")
        shutil.copyfile(dockerfile, context / "Dockerfile")
        if component in ("service", "gateway"):
            for package in ("guardrails", "connector", "proof"):
                shutil.copytree(
                    d.root / "mvp" / package,
                    context / "mvp" / package,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
        if component == "service":
            shutil.copytree(wheels, context / "wheels", copy_function=os.link)
            shutil.copyfile(
                d.root / "evolution-requirements.lock",
                context / "evolution-requirements.lock",
            )
            (context / "deploy/evolution").mkdir(parents=True)
            shutil.copyfile(
                d.root / "deploy/evolution/application.py",
                context / "deploy/evolution/application.py",
            )
        if component == "model":
            shutil.copytree(
                d.state / "models", context / "models", copy_function=os.link
            )
        inputs = []
        for path in sorted(context.rglob("*")):
            if path.is_symlink():
                raise BuildError("build_context_symlink")
            if path.is_file():
                inputs.append(
                    {
                        "path": str(path.relative_to(context)),
                        "sha256": d.sha256(path),
                        "size_bytes": path.stat().st_size,
                    }
                )
        source_digest = hashlib.sha256(
            json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        tag = "guardrails-evolution/" + component + ":" + source_digest[:16]
        d.run(
            [
                "docker",
                "build",
                "--pull=false",
                "--network=none",
                "--provenance=false",
                "--tag",
                tag,
                "--build-arg",
                "WHEELHOUSE=wheels",
                "--build-arg",
                "MODEL_DIRECTORY=models",
                "--file",
                context / "Dockerfile",
                context,
            ],
            timeout=900,
            output_file=directory / (component + ".log"),
        )
        _, raw = d.run(["docker", "image", "inspect", tag])
        actual = json.loads(raw)[0]
        digest = actual["Descriptor"]["digest"].removeprefix("sha256:")
        reference = "docker.io/guardrails-evolution/" + component + "@sha256:" + digest
        artifact = {
            "name": "evolution-" + component,
            "version": source_digest[:16],
            "source_url": "docker://" + reference,
            "sha256": digest,
            "size_bytes": actual["Size"],
            "license": "LicenseRef-Local-Prepared",
            "kind": "image",
        }
        d.inspect_image(artifact)
        d.write_json(
            directory / (component + ".json"),
            {"artifact": artifact, "source_digest": source_digest, "inputs": inputs},
        )
        results.append(artifact)
        shutil.rmtree(context)
    for name, key in (
        ("preparation.lock.json", "images"),
        ("artifacts.lock.json", "artifacts"),
    ):
        path = d.root / "deploy/evolution" / name
        lock = d.read_json(path)
        names = {item["name"] for item in results}
        lock[key] = [item for item in lock[key] if item["name"] not in names] + results
        d.write_json(path, lock)
    return results


def verify_inputs(deployment):
    """Bind prepared images to the exact checked-out code and installed wheel lock."""
    d = deployment
    dependencies = d.read_json(d.root / ".evolution/dependencies.json")
    wheels = d.root / ".evolution" / dependencies["wheelhouse"]
    expected = {x["name"]: x for x in d.specs()["images"]}
    for component in ("service", "gateway", "model"):
        record = d.read_json(d.state / "builds" / (component + ".json"))
        if record["artifact"] != expected.get("evolution-" + component):
            raise BuildError("build_artifact_pin_mismatch")
        source_digest = hashlib.sha256(
            json.dumps(record["inputs"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if record["source_digest"] != source_digest:
            raise BuildError("build_input_inventory_mismatch")
        for item in record["inputs"]:
            name = item["path"]
            if name == "Dockerfile":
                path = d.root / "deploy/evolution/images" / (component + ".Dockerfile")
            elif name.startswith("models/"):
                path = d.state / name
            elif name.startswith("wheels/"):
                path = wheels / name.removeprefix("wheels/")
            else:
                path = d.root / name
            if (
                path.is_symlink()
                or not path.is_file()
                or d.sha256(path) != item["sha256"]
                or path.stat().st_size != item["size_bytes"]
            ):
                raise BuildError("build_source_changed")
        # New files matter as much as changes to files present during the build.
        for package in (
            () if component == "model" else ("guardrails", "connector", "proof")
        ):
            actual = {
                str(p.relative_to(d.root))
                for p in (d.root / "mvp" / package).rglob("*")
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
            }
            saved = {
                x["path"]
                for x in record["inputs"]
                if x["path"].startswith("mvp/" + package + "/")
            }
            if actual != saved:
                raise BuildError("build_source_inventory_changed")
