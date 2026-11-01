"""Tiny public closures exercise the real writer without ML, Docker or network."""

from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.classifier import build_context as build


def put(path, raw):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.chmod(0o600)


@pytest.fixture
def closure(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network_or_process_operation_forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    root, model, wheels = (tmp_path / name for name in ("root", "model", "wheels"))
    for path in (root, model, wheels):
        path.mkdir(mode=0o700)
    actual_root = Path(build.__file__).resolve().parents[2]
    for name in build.SOURCE_KEYS:
        if name != build.PROPOSAL:
            put(root / name, (actual_root / name).read_bytes())
    proposal = {"model": {"files": {}}, "wheels": [], "python": {}, "requirements_sha256": build.digest((root / "services/classifier/requirements.lock").read_bytes())}
    for index in range(9):
        name = "public-model-file-" + str(index)
        raw = ("public model " + str(index)).encode()
        put(model / name, raw)
        proposal["model"]["files"][name] = {"sha256": build.digest(raw), "size_bytes": len(raw)}
    for index in range(29):
        name = ("long_public_name_" * 7 if index == 0 else "public_wheel_") + str(index) + ".whl"
        raw = ("public wheel " + str(index)).encode()
        put(wheels / name, raw)
        proposal["wheels"].append({"filename": name, "sha256": build.digest(raw), "size_bytes": len(raw)})
    python = tmp_path / "public-python.tar.gz"
    put(python, b"public interpreter archive")
    proposal["python"] = {"filename": python.name, "sha256": build.digest(python.read_bytes()), "size_bytes": python.stat().st_size}
    put(root / build.PROPOSAL, build.canonical(proposal))
    monkeypatch.setattr(build, "PROPOSAL_SHA", build.digest((root / build.PROPOSAL).read_bytes()))
    monkeypatch.setattr(build, "__file__", str(root / build.BUILDER))
    plan = {"schema": "classifier-image-context-plan-v1", "root": str(root), "model_directory": str(model), "wheel_directory": str(wheels), "python_archive": str(python), "output": str(tmp_path / "result"), "source_sha256": {name: build.digest((root / name).read_bytes()) for name in build.SOURCE_KEYS}}
    return plan


def run(plan):
    return build.stage(plan, build.digest(build.canonical(plan)))


def failed(plan):
    output = Path(plan["output"])
    assert not (output / "seal.json").exists()
    if output.exists():
        assert json.loads((output / "failure.json").read_bytes())["status"] == "context_not_sealed"


def test_closed_public_tar_and_seal(closure):
    root = Path(closure["root"])
    put(root / ".evolution/secret", b"private-sentinel-never-in-context")
    result = run(closure)
    output = Path(closure["output"])
    payload = (output / "context.tar").read_bytes()
    assert b"private-sentinel-never-in-context" not in payload
    assert {p.name for p in output.iterdir()} == {"plan.json", "context.tar", "manifest.json", "seal.json"}
    assert len(result["inputs"]) == 46
    assert result["context_sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["context_size_bytes"] == len(payload)
    assert result["model_loads"] == result["docker_operations"] == 0
    seal = json.loads((output / "seal.json").read_bytes())
    assert seal["manifest_sha256"] == build.digest((output / "manifest.json").read_bytes())
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        members = archive.getmembers()
        assert [item.name for item in members] == sorted(item["path"] for item in result["inputs"])
        for member, row in zip(members, result["inputs"], strict=True):
            assert member.isfile() and member.mode == 0o444 and member.mtime == 0
            assert member.uid == member.gid == 0 and member.uname == member.gname == ""
            assert build.digest(archive.extractfile(member).read()) == row["sha256"]
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir())


def test_deterministic_tar_across_distinct_output_names(closure):
    first = run(closure)
    other = {**closure, "output": closure["output"] + "-second"}
    second = run(other)
    assert first["context_sha256"] == second["context_sha256"]
    assert first["source_digest"] == second["source_digest"]
    assert first["plan_sha256"] != second["plan_sha256"]


@pytest.mark.parametrize("field", ["model_directory", "wheel_directory"])
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_artifact_directory_inventory_refuses_before_output(closure, field, change):
    folder = Path(closure[field])
    if change == "missing":
        next(folder.iterdir()).unlink()
    else:
        put(folder / "unexpected", b"public")
    with pytest.raises(build.ContextError, match="inventory_changed"):
        run(closure)
    assert not Path(closure["output"]).exists()


@pytest.mark.parametrize("change", ["bytes", "size", "symlink", "hardlink", "fifo", "writable"])
def test_unsafe_or_changed_artifact_never_seals(closure, change):
    path = min(Path(closure["model_directory"]).iterdir())
    original = path.read_bytes()
    if change == "bytes":
        path.write_bytes(b"x" * len(original))
    elif change == "size":
        path.write_bytes(original + b"x")
    elif change == "writable":
        path.chmod(0o666)
    else:
        outside = path.parent.parent / "elsewhere"
        put(outside, original)
        path.unlink()
        if change == "symlink":
            path.symlink_to(outside)
        elif change == "hardlink":
            os.link(outside, path)
        else:
            os.mkfifo(path, 0o600)
    with pytest.raises(build.ContextError):
        run(closure)
    failed(closure)


@pytest.mark.parametrize("change", ["unknown", "absent", "hash", "proposal", "base"])
def test_closed_source_authority(closure, change):
    sources = closure["source_sha256"]
    if change == "unknown":
        sources["unknown.py"] = "0" * 64
    elif change == "absent":
        sources.pop(build.BUILDER)
    elif change == "hash":
        sources[build.BUILDER] = "0" * 64
    elif change == "proposal":
        sources[build.PROPOSAL] = "0" * 64
    else:
        name = "services/classifier/Dockerfile"
        path = Path(closure["root"]) / name
        path.write_bytes(path.read_bytes().replace(build.BASE.encode(), b"unapproved-base"))
        sources[name] = build.digest(path.read_bytes())
    with pytest.raises(build.ContextError):
        run(closure)
    assert not Path(closure["output"]).exists()


def test_never_overwrites_output_or_follows_output_symlink(closure):
    output = Path(closure["output"])
    output.mkdir(mode=0o700)
    put(output / "sentinel", b"original")
    with pytest.raises(build.ContextError, match="output_exists"):
        run(closure)
    assert (output / "sentinel").read_bytes() == b"original"
    link = output.with_name("link")
    link.symlink_to(output, target_is_directory=True)
    with pytest.raises(build.ContextError, match="output_exists"):
        run({**closure, "output": str(link)})
    assert {p.name for p in output.iterdir()} == {"sentinel"}


def test_output_ancestor_must_be_private(closure):
    parent = Path(closure["output"]).parent
    parent.chmod(0o755)
    with pytest.raises(build.ContextError, match="unsafe_directory"):
        run(closure)
    assert not Path(closure["output"]).exists()


def test_source_mutation_during_read_refused(closure, monkeypatch):
    original = tarfile.TarFile.addfile
    item = Path(closure["model_directory"]) / "public-model-file-0"

    def change(archive, row, stream=None):
        result = original(archive, row, stream)
        if row.name == "model/" + item.name:
            item.write_bytes(item.read_bytes())
        return result

    monkeypatch.setattr(tarfile.TarFile, "addfile", change)
    with pytest.raises(build.ContextError, match="input_changed"):
        run(closure)
    failed(closure)


def test_already_copied_artifact_changes_before_final_seal(closure, monkeypatch):
    original = build.copy_input
    earlier = Path(closure["model_directory"]) / "public-model-file-0"

    def change(archive, item, output, deadline):
        result = original(archive, item, output, deadline)
        if item.name == "python.tar.gz":
            earlier.write_bytes(earlier.read_bytes())
        return result

    monkeypatch.setattr(build, "copy_input", change)
    with pytest.raises(build.ContextError, match="input_changed"):
        run(closure)
    failed(closure)


def test_output_replacement_receives_no_receipts_or_writes(closure, monkeypatch):
    original = build.copy_input
    visible = Path(closure["output"])
    saved = visible.with_name("original-output")
    called = False

    def replace(archive, item, output, deadline):
        nonlocal called
        result = original(archive, item, output, deadline)
        if not called:
            called = True
            visible.rename(saved)
            visible.mkdir(mode=0o700)
            put(visible / "sentinel", b"untouched")
        return result

    monkeypatch.setattr(build, "copy_input", replace)
    with pytest.raises(build.ContextError, match="output_changed"):
        run(closure)
    assert {p.name for p in visible.iterdir()} == {"sentinel"}
    assert not (saved / "seal.json").exists() and not (saved / "failure.json").exists()


def test_output_replaced_between_directory_stat_and_open(closure, monkeypatch):
    original = os.open
    visible = Path(closure["output"])
    saved = visible.with_name("original-output")
    changed = False

    def replace(path, flags, *args, **kwargs):
        nonlocal changed
        if not changed and path == visible.name and flags & os.O_DIRECTORY and kwargs.get("dir_fd") is not None:
            changed = True
            visible.rename(saved)
            visible.mkdir(mode=0o700)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace)
    with pytest.raises(build.ContextError, match="output_changed"):
        run(closure)
    assert list(visible.iterdir()) == [] and list(saved.iterdir()) == []


@pytest.mark.parametrize("limit", ["size", "time"])
def test_finite_resource_bound_never_seals(closure, monkeypatch, limit):
    monkeypatch.setattr(build, "MAX_TAR_BYTES" if limit == "size" else "MAX_SECONDS", 1 if limit == "size" else -1)
    with pytest.raises(build.ContextError, match="context_(limit_exceeded|timeout)"):
        run(closure)
    failed(closure)


def test_unexpected_exception_is_not_retained(closure, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("RAW-PRIVATE-ARBITRARY-SENTINEL")

    monkeypatch.setattr(build, "copy_input", fail)
    with pytest.raises(OSError):
        run(closure)
    failure = (Path(closure["output"]) / "failure.json").read_bytes()
    assert b"RAW-PRIVATE-ARBITRARY-SENTINEL" not in failure
    assert json.loads(failure)["code"] == "context_build_failed"


@pytest.mark.parametrize("raw", [b'[]', b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e900}', b'\xff', b'x' * (build.MAX_SMALL + 1)])
def test_plan_parser_rejects_unknown_json_shapes(raw):
    with pytest.raises(build.ContextError, match="invalid_json"):
        build.parse(raw)


def test_plan_byte_hash_is_authoritative(closure):
    with pytest.raises(build.ContextError, match="plan_changed"):
        build.stage(closure, "0" * 64)
    assert not Path(closure["output"]).exists()


@pytest.mark.parametrize("field,count", [("model_directory", 9), ("wheel_directory", 29)])
def test_inventory_reads_at_most_expected_plus_one(closure, monkeypatch, field, count):
    path = Path(closure[field])
    expected = {p.name for p in path.iterdir()}
    observed = []

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            for name in sorted(expected) + ["unexpected-extra"]:
                observed.append(name)
                yield SimpleNamespace(name=name)
            raise AssertionError("enumerated_beyond_exact_inventory_bound")

    def scan(fd):
        assert type(fd) is int and os.fstat(fd).st_ino == path.stat().st_ino
        return Entries()

    monkeypatch.setattr(os, "scandir", scan)
    with pytest.raises(build.ContextError, match="inventory_changed"):
        build.bounded_inventory(path, expected, "inventory_changed")
    assert len(observed) == count + 1


def test_recipe_has_pinned_offline_nonroot_tls_runtime():
    source = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    assert source.count("FROM " + build.BASE) == 2
    assert all(line.startswith("RUN --network=none ") for line in source.splitlines() if line.startswith("RUN "))
    assert "--no-index" in source and "--require-hashes" in source and "--only-binary=:all:" in source
    assert "USER 10001:10001" in source
    assert "COPY model/ /opt/classifier/model/" in source and "chmod -R a-w" in source
    assert '"--no-access-log"' in source and '"--no-proxy-headers"' in source
    assert '"--ssl-certfile"' in source and '"--ssl-keyfile"' in source
    assert '"--workers", "1"' in source and "CLASSIFIER_LANGUAGE_PROFILE=en" in source
    assert "ARG " not in source and "apt-get" not in source and "curl " not in source
