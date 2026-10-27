"""Build owned images from bounded contexts containing no configuration secrets."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
import time
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType


class BuildError(RuntimeError):
    pass


def prepare(deployment, *, classifier_context=None, classifier_seal_sha256=None):
    d = deployment
    classifier_arguments(d, classifier_context, classifier_seal_sha256)
    if classifier_context is not None:
        with classifier_bundle(d, classifier_context, classifier_seal_sha256):
            pass  # Refuse invalid public inputs before any image build.
        native_image(d, classifier_builder(d).BASE)
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
    if classifier_context is not None:
        results.append(
            prepare_classifier(d, classifier_context, classifier_seal_sha256)
        )
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
    classifier = expected.get("evolution-classifier")
    locked = classifier_lock_rows(d)
    need(
        locked == ([] if classifier is None else [classifier]),
        "classifier_lock_mismatch",
    )
    record_path = d.state / "builds/classifier.json"
    if classifier is not None:
        verify_classifier(d, d.read_json(record_path), classifier)
    elif record_path.exists() or record_path.is_symlink():
        raise BuildError("classifier_record_without_lock")


CLASSIFIER_BUILDER_SHA = (
    "58c269fc983a8f970b9061e7ad8ea899d602aab1b568bbef74a98a2b1266c7b6"
)
NATIVE_DOCKER = ("/usr/bin/docker", "--host", "unix:///var/run/docker.sock")
CLASSIFIER_RECORD_KEYS = {
    "schema",
    "artifact",
    "source_digest",
    "inputs",
    "source_inventory",
    "context_directory",
    "seal_sha256",
    "manifest_sha256",
    "context_sha256",
    "context_size_bytes",
    "base_image",
    "image_id",
    "image_manifest_digest",
}


def need(condition, code):
    if not condition:
        raise BuildError(code)


def classifier_arguments(d, directory, seal_sha256):
    need(
        (directory is None) == (seal_sha256 is None),
        "classifier_context_arguments_required",
    )
    present = bool(classifier_lock_rows(d))
    record = d.state / "builds/classifier.json"
    need(
        directory is not None
        or not (present or record.exists() or record.is_symlink()),
        "classifier_context_required",
    )


def classifier_lock_rows(d):
    first = [r for r in d.specs()["images"] if r["name"] == "evolution-classifier"]
    second = [
        r
        for r in d.read_json(d.root / "deploy/evolution/artifacts.lock.json")[
            "artifacts"
        ]
        if r["name"] == "evolution-classifier"
    ]
    need(len(first) <= 1 and second == first, "classifier_lock_mismatch")
    return first


def classifier_builder(d):
    """Load only the reviewed stdlib context validator, never a model module."""
    path = d.root / "services/classifier/build_context.py"
    need(path.resolve(strict=True) == path, "classifier_builder_changed")
    before = path.stat(follow_symlinks=False)
    need(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == os.getuid()
        and before.st_nlink == 1
        and not before.st_mode & 0o022
        and 0 < before.st_size <= 262144,
        "classifier_builder_changed",
    )
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_uid",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    identity = lambda row: tuple(getattr(row, name) for name in fields)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        need(identity(os.fstat(fd)) == identity(before), "classifier_builder_changed")
        raw = stream.read(262145)
        need(
            len(raw) == before.st_size
            and hashlib.sha256(raw).hexdigest() == CLASSIFIER_BUILDER_SHA
            and identity(os.fstat(fd)) == identity(before)
            and identity(path.stat(follow_symlinks=False)) == identity(before),
            "classifier_builder_changed",
        )
        key = "evolution_classifier_context_builder"
        module = ModuleType(key)
        module.__file__ = str(path)
        sys.modules[key] = module
        # Execute the bytes authenticated above, never a second pathname read.
        exec(compile(raw, str(path), "exec"), module.__dict__)  # noqa: S102 - authenticated immutable source
        need(
            path.resolve(strict=True) == path
            and identity(os.fstat(fd)) == identity(before)
            and identity(path.stat(follow_symlinks=False)) == identity(before),
            "classifier_builder_changed",
        )
        return module


def classifier_sources(d, builder):
    """Bound the entire classifier source tree, including additions and deletions."""
    found = {}
    pending = [d.root / "services/classifier"]
    visited = 0
    while pending:
        directory = pending.pop()
        builder.directory(directory)
        with os.scandir(directory) as entries:
            for item in entries:
                visited += 1
                need(visited <= 256, "classifier_source_inventory_limit")
                if item.name == "__pycache__" or item.name.endswith(".pyc"):
                    continue
                need(not item.is_symlink(), "classifier_source_symlink")
                path = Path(item.path)
                if item.is_dir(follow_symlinks=False):
                    pending.append(path)
                else:
                    found[str(path.relative_to(d.root))] = builder.digest(
                        builder.small(path)
                    )
    for name in builder.SOURCE_KEYS:
        found[name] = builder.digest(builder.small(d.root / name))
    return dict(sorted(found.items()))


def hash_stream(builder, stream, deadline):
    digest, count = hashlib.sha256(), 0
    while True:
        need(time.monotonic() < deadline, "classifier_verification_timeout")
        raw = stream.read(builder.CHUNK)
        if not raw:
            return digest.hexdigest(), count
        count += len(raw)
        need(count <= builder.MAX_TAR_BYTES, "classifier_input_size_limit")
        digest.update(raw)


def check_tar(builder, stream, rows, deadline):
    """No extraction: only 46 regular files from the authenticated public closure."""
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for expected in rows:
            need(time.monotonic() < deadline, "classifier_verification_timeout")
            item = archive.next()
            need(
                item is not None
                and item.isfile()
                and item.name == expected["path"]
                and item.size == expected["size_bytes"]
                and item.mode == 0o444
                and item.uid == item.gid == item.mtime == 0
                and item.uname == item.gname == ""
                and set(item.pax_headers) <= {"path"},
                "classifier_tar_inventory_changed",
            )
            payload = archive.extractfile(item)
            actual_hash, actual_size = hash_stream(builder, payload, deadline)
            need(
                actual_hash == expected["sha256"]
                and actual_size == expected["size_bytes"],
                "classifier_tar_payload_changed",
            )
        need(archive.next() is None, "classifier_tar_inventory_changed")


@contextmanager
def classifier_bundle(d, directory, seal_sha256):
    builder = classifier_builder(d)
    try:
        need(builder.sha(seal_sha256), "classifier_seal_pin_invalid")
        directory = Path(directory)
        builder.directory(directory, private=True)
        builder.bounded_inventory(
            directory,
            {"plan.json", "manifest.json", "seal.json", "context.tar"},
            "classifier_package_changed",
        )
        raw = {
            name: builder.small(directory / name)
            for name in ("plan.json", "manifest.json", "seal.json")
        }
        docs = {name: builder.parse(value) for name, value in raw.items()}
        need(
            all(builder.canonical(docs[n]) == value for n, value in raw.items()),
            "classifier_metadata_not_canonical",
        )
        seal, manifest, plan = (
            docs["seal.json"],
            docs["manifest.json"],
            docs["plan.json"],
        )
        need(builder.digest(raw["seal.json"]) == seal_sha256, "classifier_seal_changed")
        need(
            set(seal)
            == {
                "schema",
                "scope",
                "complete",
                "manifest_sha256",
                "context_sha256",
                "context_size_bytes",
                "plan_sha256",
            }
            and seal["schema"] == "classifier-image-context-seal-v1"
            and seal["scope"] == "context_only_not_built"
            and seal["complete"] is True,
            "classifier_seal_invalid",
        )
        need(
            set(manifest)
            == {
                "schema",
                "scope",
                "plan_sha256",
                "base_image",
                "inputs",
                "source_sha256",
                "source_digest",
                "context_sha256",
                "context_size_bytes",
                "docker_operations",
                "model_loads",
            }
            and manifest["schema"] == "classifier-image-context-v1"
            and manifest["scope"] == "context_only_not_built"
            and type(manifest["docker_operations"]) is int
            and manifest["docker_operations"] == 0
            and type(manifest["model_loads"]) is int
            and manifest["model_loads"] == 0,
            "classifier_manifest_invalid",
        )
        need(
            plan["root"] == str(d.root) and plan["output"] == str(directory),
            "classifier_context_location_changed",
        )
        need(
            seal["manifest_sha256"] == builder.digest(raw["manifest.json"])
            and seal["plan_sha256"]
            == manifest["plan_sha256"]
            == builder.digest(raw["plan.json"])
            and seal["context_sha256"] == manifest["context_sha256"]
            and seal["context_size_bytes"] == manifest["context_size_bytes"]
            and manifest["base_image"] == builder.BASE
            and manifest["source_sha256"] == plan["source_sha256"],
            "classifier_metadata_mismatch",
        )
        inputs = builder.resolve_inputs(plan, creating=False)
        rows = [
            {"path": x.name, "sha256": x.sha256, "size_bytes": x.size} for x in inputs
        ]
        need(
            manifest["inputs"] == rows
            and manifest["source_digest"] == builder.digest(builder.canonical(rows)),
            "classifier_input_inventory_changed",
        )
        need(
            type(seal["context_size_bytes"]) is int
            and 0 < seal["context_size_bytes"] <= builder.MAX_TAR_BYTES,
            "classifier_context_size_invalid",
        )
        deadline = time.monotonic() + builder.MAX_SECONDS
        snapshots = {}
        # Current public caches must still be the complete reviewed byte closure.
        for item in inputs:
            source, original = builder.open_input(item.path)
            with source:
                need(original.st_size == item.size, "classifier_cached_input_changed")
                digest, size = hash_stream(builder, source, deadline)
                need(
                    digest == item.sha256 and size == item.size,
                    "classifier_cached_input_changed",
                )
                builder.unchanged(item.path, source, original)
                snapshots[item.name] = builder.identity(original)
        inventory = classifier_sources(d, builder)
        stream, before = builder.open_input(directory / "context.tar")
        with stream:
            digest, size = hash_stream(builder, stream, deadline)
            need(
                digest == seal["context_sha256"] and size == seal["context_size_bytes"],
                "classifier_context_changed",
            )
            stream.seek(0)
            check_tar(builder, stream, rows, deadline)
            builder.unchanged(directory / "context.tar", stream, before)
            stream.seek(0)
            yield builder, stream, manifest, inventory
            builder.unchanged(directory / "context.tar", stream, before)
            need(
                classifier_sources(d, builder) == inventory, "classifier_source_changed"
            )
            need(
                builder.resolve_inputs(plan, creating=False) == inputs,
                "classifier_input_inventory_changed",
            )
            need(
                all(
                    builder.identity(x.path.stat(follow_symlinks=False))
                    == snapshots[x.name]
                    for x in inputs
                ),
                "classifier_cached_input_changed",
            )
            need(
                all(
                    builder.small(directory / name) == value
                    for name, value in raw.items()
                ),
                "classifier_metadata_changed",
            )
    except builder.ContextError as error:
        raise BuildError(str(error)) from None


def native_image(d, reference):
    """Explicit native daemon; never let a missing immutable base trigger a pull."""
    need(
        d.env.get("DOCKER_HOST", "unix:///var/run/docker.sock")
        == "unix:///var/run/docker.sock"
        and all(
            not d.env.get(name)
            for name in (
                "DOCKER_CONTEXT",
                "DOCKER_TLS_VERIFY",
                "DOCKER_CERT_PATH",
                "BUILDX_BUILDER",
                "BUILDKIT_HOST",
                "BUILDX_CONFIG",
                "DOCKER_CONFIG",
            )
        ),
        "classifier_docker_routing_invalid",
    )
    code, raw = d.run(
        [*NATIVE_DOCKER, "image", "inspect", reference], timeout=30, check=False
    )
    need(code == 0 and len(raw) <= 262144, "classifier_image_missing")
    try:
        values = json.loads(raw)
        need(
            type(values) is list and len(values) == 1 and type(values[0]) is dict,
            "classifier_image_invalid",
        )
        image = values[0]
        need(
            image["Os"] == "linux"
            and image["Architecture"] == "amd64"
            and type(image["Size"]) is int
            and image["Size"] > 0
            and type(image["Id"]) is str
            and image["Id"].startswith("sha256:")
            and len(image["Id"]) == 71
            and all(c in "0123456789abcdef" for c in image["Id"][7:]),
            "classifier_image_invalid",
        )
        if "@sha256:" in reference:
            aliases = {reference, reference.removeprefix("docker.io/")}
            if reference.startswith("docker.io/library/"):
                aliases.add(reference.removeprefix("docker.io/library/"))
            need(
                bool(aliases.intersection(image.get("RepoDigests", [])))
                or image["Id"] == "sha256:" + reference.rsplit(":", 1)[1],
                "classifier_image_pin_mismatch",
            )
        return image
    except ValueError, KeyError, TypeError:
        raise BuildError("classifier_image_invalid") from None


def prepare_classifier(d, directory, seal_sha256):
    with classifier_bundle(d, directory, seal_sha256) as (
        builder,
        stream,
        manifest,
        inventory,
    ):
        base = native_image(d, builder.BASE)
        tag = "guardrails-evolution/classifier:" + manifest["source_digest"][:16]
        d.run(
            [
                *NATIVE_DOCKER,
                "build",
                "--builder",
                "default",
                "--pull=false",
                "--network=none",
                "--provenance=false",
                "--tag",
                tag,
                "-",
            ],
            timeout=900,
            input_stream=stream,
            output_file=d.state / "builds/classifier.log",
        )
        image = native_image(d, tag)
        digest = image.get("Descriptor", {}).get("digest", "").removeprefix("sha256:")
        need(builder.sha(digest), "classifier_image_manifest_invalid")
        reference = "docker.io/guardrails-evolution/classifier@sha256:" + digest
        need(native_image(d, reference) == image, "classifier_built_image_changed")
        need(native_image(d, builder.BASE) == base, "classifier_base_changed")
        artifact = {
            "name": "evolution-classifier",
            "version": manifest["source_digest"][:16],
            "source_url": "docker://" + reference,
            "sha256": digest,
            "size_bytes": image["Size"],
            "license": "LicenseRef-Local-Prepared",
            "kind": "image",
        }
        record = {
            "schema": "classifier-image-build-v1",
            "artifact": artifact,
            "source_digest": manifest["source_digest"],
            "inputs": manifest["inputs"],
            "source_inventory": inventory,
            "context_directory": str(directory),
            "seal_sha256": seal_sha256,
            "manifest_sha256": builder.digest(
                builder.small(Path(directory) / "manifest.json")
            ),
            "context_sha256": manifest["context_sha256"],
            "context_size_bytes": manifest["context_size_bytes"],
            "base_image": builder.BASE,
            "image_id": image["Id"],
            "image_manifest_digest": "sha256:" + digest,
        }
    # The context's post-build checks run before publishing any success record.
    d.write_json(d.state / "builds/classifier.json", record)
    return artifact


def verify_classifier(d, record, artifact):
    need(
        type(record) is dict
        and set(record) == CLASSIFIER_RECORD_KEYS
        and record["schema"] == "classifier-image-build-v1"
        and record["artifact"] == artifact,
        "classifier_build_record_invalid",
    )
    for name, key in (
        ("preparation.lock.json", "images"),
        ("artifacts.lock.json", "artifacts"),
    ):
        rows = [
            r
            for r in d.read_json(d.root / "deploy/evolution" / name)[key]
            if r["name"] == "evolution-classifier"
        ]
        need(rows == [artifact], "classifier_lock_mismatch")
    with classifier_bundle(d, record["context_directory"], record["seal_sha256"]) as (
        builder,
        _stream,
        manifest,
        inventory,
    ):
        need(
            record["source_inventory"] == inventory
            and all(
                record[name] == manifest[name]
                for name in (
                    "source_digest",
                    "inputs",
                    "context_sha256",
                    "context_size_bytes",
                    "base_image",
                )
            )
            and record["manifest_sha256"]
            == builder.digest(
                builder.small(Path(record["context_directory"]) / "manifest.json")
            ),
            "classifier_build_inputs_changed",
        )
        need(
            artifact
            == {
                "name": "evolution-classifier",
                "version": manifest["source_digest"][:16],
                "source_url": "docker://docker.io/guardrails-evolution/classifier@sha256:"
                + artifact["sha256"],
                "sha256": artifact["sha256"],
                "size_bytes": artifact["size_bytes"],
                "license": "LicenseRef-Local-Prepared",
                "kind": "image",
            }
            and builder.sha(artifact["sha256"])
            and record["image_manifest_digest"] == "sha256:" + artifact["sha256"],
            "classifier_artifact_invalid",
        )
        image = native_image(d, artifact["source_url"].removeprefix("docker://"))
        need(
            image["Id"] == record["image_id"]
            and image["Size"] == artifact["size_bytes"]
            and image.get("Descriptor", {}).get("digest")
            == record["image_manifest_digest"],
            "classifier_prepared_image_changed",
        )
