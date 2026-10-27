"""Seal a small, explicit public-input inventory into an offline Docker TAR.

This module does not invoke Docker, import ML, download artifacts, or edit locks.
The parent operator authenticates the plan and builds the resulting context later.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

BASE = "docker.io/library/python@sha256:cfe2e24a75302a15934d37c2d86412893c0aa934dc3a97cbb439d04c01890ca9"
PROPOSAL = "services/classifier/preparation.proposal.json"
PROPOSAL_SHA = "bf76f0289eb6912a8658038f397c0f092fd30aa43691b12ef2a872911b29f68c"
BUILDER = "services/classifier/build_context.py"
SOURCE_INPUTS = {
    "services/classifier/Dockerfile": "Dockerfile",
    "services/classifier/requirements.lock": "requirements.lock",
    "mvp/guardrails/__init__.py": "app/mvp/guardrails/__init__.py",
    "mvp/guardrails/evolution/__init__.py": "app/mvp/guardrails/evolution/__init__.py",
    "mvp/guardrails/evolution/classifier_contract.py": "app/mvp/guardrails/evolution/classifier_contract.py",
    "services/classifier/app.py": "app/services/classifier/app.py",
    "services/classifier/worker.py": "app/services/classifier/worker.py",
}
SOURCE_KEYS = frozenset(SOURCE_INPUTS) | {BUILDER, PROPOSAL}
PLAN_KEYS = {"schema", "root", "model_directory", "wheel_directory", "python_archive", "output", "source_sha256"}
MAX_SMALL = 262144
MAX_TAR_BYTES = 2 * 1024**3
MAX_SECONDS = 600
CHUNK = 1024 * 1024


class ContextError(RuntimeError):
    """Finite public failure code only."""


def require(condition, code):
    if not condition:
        raise ContextError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def parse(raw):
    require(type(raw) is bytes and len(raw) <= MAX_SMALL, "invalid_json")

    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, "invalid_json")
            result[key] = value
        return result

    def reject(_):
        raise ContextError("invalid_json")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject, parse_float=reject)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ContextError("invalid_json") from error
    require(type(value) is dict, "invalid_json")
    return value


def sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def identity(row):
    return (row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_nlink, row.st_size, row.st_mtime_ns, row.st_ctime_ns)


def directory(path, *, private=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path, "unsafe_directory")
    # Check every ancestor, including /home and the session/worktree parents.
    for parent in (path, *path.parents):
        row = parent.stat(follow_symlinks=False)
        require(stat.S_ISDIR(row.st_mode) and row.st_uid in {0, os.getuid()} and not row.st_mode & 0o022, "unsafe_directory")
    row = path.stat(follow_symlinks=False)
    require(row.st_uid == os.getuid(), "unsafe_directory")
    if private:
        require(stat.S_IMODE(row.st_mode) == 0o700, "unsafe_directory")
    return row


def open_input(path):
    path = Path(path)
    directory(path.parent)
    before = path.stat(follow_symlinks=False)
    require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and before.st_nlink == 1 and not before.st_mode & 0o022, "unsafe_input")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    stream = os.fdopen(fd, "rb")
    try:
        require(identity(os.fstat(fd)) == identity(before), "input_changed")
        return stream, before
    except BaseException:
        stream.close()
        raise


def unchanged(path, stream, before):
    directory(path.parent)
    require(identity(os.fstat(stream.fileno())) == identity(before) and identity(path.stat(follow_symlinks=False)) == identity(before), "input_changed")


def small(path):
    path = Path(path)
    stream, before = open_input(path)
    with stream:
        require(before.st_size <= MAX_SMALL, "input_too_large")
        value = stream.read(MAX_SMALL + 1)
        require(len(value) == before.st_size, "input_changed")
        unchanged(path, stream, before)
    return value


def bounded_inventory(path, expected, code):
    """Do not let pathlib eagerly enumerate an unbounded artifact directory."""
    before = directory(path)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        require(identity(os.fstat(fd)) == identity(before), "input_changed")
        found = set()
        with os.scandir(fd) as entries:
            for entry in entries:
                require(len(found) < len(expected) and entry.name in expected and entry.name not in found, code)
                found.add(entry.name)
        require(found == expected, code)
        require(identity(os.fstat(fd)) == identity(before) and identity(directory(path)) == identity(before), "input_changed")
    finally:
        os.close(fd)


@dataclass(frozen=True)
class Input:
    path: Path
    name: str
    sha256: str
    size: int


def plan_checked(plan, *, creating=True):
    require(type(plan) is dict and set(plan) == PLAN_KEYS and plan["schema"] == "classifier-image-context-plan-v1", "invalid_plan")
    for name in ("root", "model_directory", "wheel_directory", "python_archive", "output"):
        require(type(plan[name]) is str and Path(plan[name]).is_absolute() and str(Path(plan[name])) == plan[name], "invalid_plan")
    sources = plan["source_sha256"]
    require(type(sources) is dict and set(sources) == SOURCE_KEYS and all(sha(v) for v in sources.values()) and sources[PROPOSAL] == PROPOSAL_SHA, "invalid_source_map")
    root = Path(plan["root"])
    require(root / BUILDER == Path(__file__).absolute(), "wrong_source_root")
    for name in ("root", "model_directory", "wheel_directory"):
        directory(Path(plan[name]))
    output = Path(plan["output"])
    directory(output.parent, private=True)
    if creating:
        require(not output.exists() and not output.is_symlink(), "output_exists")
    else:
        directory(output, private=True)
    for name in ("root", "model_directory", "wheel_directory"):
        require(not output.is_relative_to(Path(plan[name])) and not Path(plan[name]).is_relative_to(output), "output_overlaps_inputs")
    require(not Path(plan["python_archive"]).is_relative_to(output), "output_overlaps_inputs")


def resolve_inputs(plan, *, creating=True):
    plan_checked(plan, creating=creating)
    root = Path(plan["root"])
    source = {}
    for name, expected in plan["source_sha256"].items():
        raw = small(root / name)
        require(digest(raw) == expected, "source_changed")
        source[name] = raw
    proposal = parse(source[PROPOSAL])  # Its complete bytes are pinned above.
    dockerfile = source["services/classifier/Dockerfile"].decode("utf-8")
    require([line for line in dockerfile.splitlines() if line.startswith("FROM ")] == ["FROM " + BASE + " AS dependencies", "FROM " + BASE], "base_changed")
    require(digest(source["services/classifier/requirements.lock"]) == proposal["requirements_sha256"], "requirements_changed")
    inputs = [Input(root / name, target, digest(source[name]), len(source[name])) for name, target in SOURCE_INPUTS.items()]
    model = Path(plan["model_directory"])
    wheels = Path(plan["wheel_directory"])
    require(len(proposal["model"]["files"]) == 9 and len(proposal["wheels"]) == 29, "artifact_inventory_changed")
    bounded_inventory(model, set(proposal["model"]["files"]), "model_inventory_changed")
    bounded_inventory(wheels, {r["filename"] for r in proposal["wheels"]}, "wheel_inventory_changed")
    for name, row in proposal["model"]["files"].items():
        inputs.append(Input(model / name, "model/" + name, row["sha256"], row["size_bytes"]))
    for row in proposal["wheels"]:
        inputs.append(Input(wheels / row["filename"], "wheels/" + row["filename"], row["sha256"], row["size_bytes"]))
    row = proposal["python"]
    require(Path(plan["python_archive"]).name == row["filename"], "python_archive_changed")
    inputs.append(Input(Path(plan["python_archive"]), "python.tar.gz", row["sha256"], row["size_bytes"]))
    require(len(inputs) == 46 and len({r.name for r in inputs}) == 46, "artifact_inventory_changed")
    return sorted(inputs, key=lambda row: row.name)


class Output:
    """Hold output identity for the entire copy; never append to a replacement."""

    def __init__(self, path):
        self.path = Path(path)
        before = directory(self.path.parent, private=True)
        parent = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.fd = None
        try:
            require(identity(os.fstat(parent)) == identity(before), "output_parent_changed")
            require(identity(self.path.parent.stat(follow_symlinks=False)) == identity(before), "output_parent_changed")
            os.mkdir(self.path.name, mode=0o700, dir_fd=parent)
            created = os.stat(self.path.name, dir_fd=parent, follow_symlinks=False)
            self.fd = os.open(self.path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            require(identity(os.fstat(self.fd)) == identity(created) and not os.listdir(self.fd), "output_changed")
            os.fchmod(self.fd, 0o700)
            self.original = os.fstat(self.fd)
            self.check()
            os.fsync(parent)
        except BaseException:
            self.close()
            raise
        finally:
            os.close(parent)

    def check(self):
        row = directory(self.path, private=True)
        key = lambda value: (value.st_dev, value.st_ino, value.st_uid, value.st_mode)
        require(key(row) == key(self.original) == key(os.fstat(self.fd)), "output_changed")

    def create(self, name):
        self.check()
        require(name in {"plan.json", "context.tar", "manifest.json", "seal.json", "failure.json"}, "invalid_output_name")
        return os.fdopen(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd), "wb")

    def write(self, name, value):
        raw = canonical(value)
        with self.create(name) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        self.check()
        os.fsync(self.fd)
        return digest(raw)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class HashReader:
    def __init__(self, stream, deadline):
        self.stream, self.deadline = stream, deadline
        self.hash = hashlib.sha256()
        self.count = 0

    def read(self, size):
        require(time.monotonic() < self.deadline, "context_timeout")
        require(type(size) is int and 0 <= size <= CHUNK, "invalid_read_size")
        raw = self.stream.read(size)
        self.hash.update(raw)
        self.count += len(raw)
        return raw


class HashWriter:
    def __init__(self, stream, deadline):
        self.stream, self.deadline = stream, deadline
        self.hash = hashlib.sha256()
        self.count = 0

    def write(self, raw):
        require(time.monotonic() < self.deadline and self.count + len(raw) <= MAX_TAR_BYTES, "context_limit_exceeded")
        written = self.stream.write(raw)
        require(written == len(raw), "incomplete_output")
        self.hash.update(raw)
        self.count += written
        return written


def copy_input(archive, item, output, deadline):
    output.check()
    stream, before = open_input(item.path)
    with stream:
        require(before.st_size == item.size, "artifact_size_changed")
        reader = HashReader(stream, deadline)
        row = tarfile.TarInfo(item.name)
        row.size, row.mode, row.mtime = item.size, 0o444, 0
        row.uid, row.gid, row.uname, row.gname = 0, 0, "", ""
        archive.addfile(row, reader)
        require(reader.count == item.size and reader.hash.hexdigest() == item.sha256, "artifact_hash_changed")
        unchanged(item.path, stream, before)
    output.check()
    return identity(before)


def stage(plan, plan_sha256):
    require(sha(plan_sha256) and digest(canonical(plan)) == plan_sha256, "plan_changed")
    inputs = resolve_inputs(plan)
    deadline = time.monotonic() + MAX_SECONDS
    output = Output(plan["output"])
    try:
        output.write("plan.json", plan)
        with output.create("context.tar") as stream:
            hashed = HashWriter(stream, deadline)
            snapshots = {}
            with tarfile.open(fileobj=hashed, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                for item in inputs:
                    snapshots[item.name] = copy_input(archive, item, output, deadline)
            stream.flush()
            os.fsync(stream.fileno())
            saved = os.fstat(stream.fileno())
            # Repeat the exact inventories and small source pins after the copy.
            require(resolve_inputs(plan, creating=False) == inputs, "inputs_changed")
            for item in inputs:
                directory(item.path.parent)
                require(identity(item.path.stat(follow_symlinks=False)) == snapshots[item.name], "input_changed")
            output.check()
            require(identity(os.stat("context.tar", dir_fd=output.fd, follow_symlinks=False)) == identity(saved), "output_changed")
            rows = [{"path": row.name, "sha256": row.sha256, "size_bytes": row.size} for row in inputs]
            manifest = {"schema": "classifier-image-context-v1", "scope": "context_only_not_built", "plan_sha256": plan_sha256, "base_image": BASE, "inputs": rows, "source_sha256": plan["source_sha256"], "source_digest": digest(canonical(rows)), "context_sha256": hashed.hash.hexdigest(), "context_size_bytes": hashed.count, "docker_operations": 0, "model_loads": 0}
            manifest_hash = output.write("manifest.json", manifest)
            require(identity(os.fstat(stream.fileno())) == identity(saved) and identity(os.stat("context.tar", dir_fd=output.fd, follow_symlinks=False)) == identity(saved), "output_changed")
            output.write("seal.json", {"schema": "classifier-image-context-seal-v1", "scope": "context_only_not_built", "complete": True, "manifest_sha256": manifest_hash, "context_sha256": hashed.hash.hexdigest(), "context_size_bytes": hashed.count, "plan_sha256": plan_sha256})
        return manifest
    except BaseException as error:
        try:
            output.write("failure.json", {"status": "context_not_sealed", "code": str(error) if isinstance(error, ContextError) else "context_build_failed"})
        except BaseException:  # noqa: BLE001, S110 - preserve original failure without foreign-path writes
            pass  # Identity loss must not write into a replaced output directory.
        raise
    finally:
        output.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    arguments = parser.parse_args()
    try:
        raw = small(arguments.plan)
        require(sha(arguments.sha256) and digest(raw) == arguments.sha256, "plan_changed")
        plan = parse(raw)
        require(canonical(plan) == raw, "plan_not_canonical")
        stage(plan, arguments.sha256)
    except BaseException as error:  # noqa: BLE001 - CLI prints only finite error codes
        print(json.dumps({"status": "context_not_sealed", "code": str(error) if isinstance(error, ContextError) else "context_build_failed"}))
        return 1
    print(json.dumps({"status": "context_sealed_not_built"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
