"""Portable source and genuine family/collector joins; no external operations."""
from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import time

import pytest
from evolution import classifier_deployed_collection as collection
from evolution import classifier_source_projection as projection
from evolution import diagnostics, measurements
from evolution.registry import EvidenceError

from mvp.tests.evolution.unit import test_classifier_deployed_collection as fixtures


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Synthetic source projection forbids external operations")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)


def source_tree(tmp_path):
    root = tmp_path / "source"
    rows = {"mvp/example.py": b"value = 1\n", projection.NATIVE_ASSET: b"# synthetic node policy\n"}
    for name, raw in rows.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    pins = {name: hashlib.sha256(raw).hexdigest() for name, raw in rows.items()}
    return root, pins


def test_python_catalog_and_complete_asset_inventory_produce_same_closed_map(tmp_path):
    root, pins = source_tree(tmp_path)
    python = {name: sha for name, sha in pins.items() if name.endswith(".py")}
    all_sources = {**pins, "README.md": "f" * 64, "mvp/fixture.json": "a" * 64}
    assert projection.project_sources(root, python, deadline=time.monotonic()+60) == pins
    assert projection.project_sources(root, all_sources, deadline=time.monotonic()+60) == pins
    diagnostics.validate_catalog(python, root)
    with pytest.raises(EvidenceError):
        diagnostics.validate_catalog(pins, root)


@pytest.mark.parametrize("fault", ("missing", "symlink", "parent_symlink", "hardlink", "fifo", "directory", "empty", "oversize", "wide", "executable", "wrong_owner", "wrong_pin", "changed_after_read", "expired"))
def test_policy_read_refuses_unsafe_identity_or_changed_bytes(tmp_path, monkeypatch, fault):
    root, pins = source_tree(tmp_path)
    path = root / projection.NATIVE_ASSET
    if fault == "missing": path.unlink()
    elif fault == "symlink":
        target = tmp_path / "policy"; path.rename(target); path.symlink_to(target)
    elif fault == "parent_symlink":
        target = tmp_path / "node"; path.parent.rename(target); path.parent.symlink_to(target, target_is_directory=True)
    elif fault == "hardlink": (tmp_path / "link").hardlink_to(path)
    elif fault == "fifo": path.unlink(); os.mkfifo(path)
    elif fault == "directory": path.unlink(); path.mkdir()
    elif fault == "empty": path.write_bytes(b"")
    elif fault == "oversize": path.write_bytes(b"x" * (projection.MAX_ASSET_BYTES + 1))
    elif fault == "wide": path.chmod(0o666)
    elif fault == "executable": path.chmod(0o755)
    elif fault == "wrong_owner": monkeypatch.setattr(projection.os, "getuid", lambda: path.stat().st_uid + 1)
    elif fault == "wrong_pin": pins[projection.NATIVE_ASSET] = "e" * 64
    elif fault == "changed_after_read":
        original = projection.os.fstat; calls = []
        def fstat(fd):
            calls.append(fd)
            if len(calls) == 2: path.write_bytes(b"replacement\n")
            return original(fd)
        monkeypatch.setattr(projection.os, "fstat", fstat)
    else:
        values = iter((1, 1000)); monkeypatch.setattr(projection.time, "monotonic", lambda: next(values))
    with pytest.raises(EvidenceError): projection.project_sources(root, pins, deadline=time.monotonic()+60)


@pytest.mark.parametrize("mode", (0o400, 0o444, 0o600, 0o644))
def test_explicit_regular_read_only_policy_modes_are_accepted(tmp_path, mode):
    root, pins = source_tree(tmp_path); (root / projection.NATIVE_ASSET).chmod(mode)
    assert projection.project_sources(root, pins, deadline=time.monotonic()+60) == pins


@pytest.mark.parametrize("fault", ("empty", "nonmapping", "bad_sha", "absolute", "traversal", "double_separator", "extra_dot", "foreign_python", "oversized_map"))
def test_catalog_shape_is_not_weakened_for_native_assets(tmp_path, fault):
    root, pins = source_tree(tmp_path)
    if fault == "empty": pins = {}
    elif fault == "nonmapping": pins = []
    elif fault == "bad_sha": pins["README.md"] = "bad"
    elif fault == "absolute": pins["/tmp/file.txt"] = "a" * 64
    elif fault == "traversal": pins["mvp/../file.txt"] = "a" * 64
    elif fault == "double_separator": pins["mvp//file.txt"] = "a" * 64
    elif fault == "extra_dot": pins["./file.txt"] = "a" * 64
    elif fault == "foreign_python": pins["untrusted/file.py"] = "a" * 64
    else: pins.update({f"file{i}.txt": "a" * 64 for i in range(projection.MAX_INPUT_FILES)})
    with pytest.raises(EvidenceError): projection.project_sources(root, pins, deadline=time.monotonic()+60)


@pytest.mark.parametrize("family", fixtures.FAMILIES, ids=("budget", "cni", "resource", "tls"))
def test_real_gate_sources_and_both_collectors_bind_identical_projection(tmp_path, monkeypatch, family):
    root, sources, destination, directory, case, value = fixtures.fixtures(tmp_path, family)
    python = {name: sha for name, sha in sources.items() if name.endswith(".py")}
    diagnostics.write_catalog(directory.parents[1] / (case + ".sources.json"), root, python)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(directory))
    projected = family.gate_sources(root, deadline=time.monotonic()+60) if family is collection.cni else family.gate_sources(root, case, deadline=time.monotonic()+60)
    assert projected == sources
    calls = []
    def verifier(**kwargs):
        assert kwargs["sources"] == projected
        calls.append(kwargs)
        return dict(value["provenance"])
    first = measurements.collect_measurements(destination, [case], [case], root=root,
        sources=python, provenance={case: verifier}, classifier_deadline=time.monotonic()+60)
    second = measurements.collect_measurements(destination, [case], [case], root=root,
        sources={**sources, "README.md": "b" * 64}, provenance={case: verifier}, classifier_deadline=time.monotonic()+60)
    assert first == second and len(calls) == 2
    assert all(row["source_files"] == sources for row in first.values())


@pytest.mark.parametrize("family", (collection.budget, collection.cni, collection.tls), ids=("budget", "cni", "tls"))
@pytest.mark.parametrize("fault", ("added_python", "missing_python", "native_changed_in_callback"))
def test_actual_observation_cannot_drop_extra_or_missing_source_entries(tmp_path, family, fault):
    root, sources, destination, _directory, case, value = fixtures.fixtures(tmp_path, family)
    if fault == "added_python":
        path = root / "mvp/extra.py"; path.write_bytes(b"# extra\n")
        sources[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif fault == "missing_python": sources.pop(family.SOURCES[0])
    def verifier(**kwargs):
        if fault == "native_changed_in_callback": (root / projection.NATIVE_ASSET).write_bytes(b"# changed\n")
        return dict(value["provenance"])
    with pytest.raises(EvidenceError):
        measurements.collect_measurements(destination, [case], [case], root=root,
            sources=sources, provenance={case: verifier}, classifier_deadline=time.monotonic()+60)


@pytest.mark.parametrize('deadline',(None,True,float('nan'),float('inf'),0,1))
def test_invalid_or_expired_original_deadline_refuses_before_policy_read(tmp_path,monkeypatch,deadline):
    root,pins=source_tree(tmp_path)
    monkeypatch.setattr(projection.os,'open',lambda *a,**kw:pytest.fail('opened after deadline'))
    with pytest.raises(EvidenceError):projection.project_sources(root,pins,deadline=deadline)


def test_projection_requires_explicit_deadline_and_checks_after_asset_read(tmp_path,monkeypatch):
    root,pins=source_tree(tmp_path);clock=[1.]
    monkeypatch.setattr(projection.time,'monotonic',lambda:clock[0])
    original=projection.asset_digest
    def read(*args,**kwargs):
        assert kwargs['deadline']==3
        value=original(*args,**kwargs);clock[0]=3;return value
    monkeypatch.setattr(projection,'asset_digest',read)
    with pytest.raises(TypeError):projection.project_sources(root,pins)
    with pytest.raises(EvidenceError):projection.project_sources(root,pins,deadline=3)


@pytest.mark.parametrize('family',fixtures.FAMILIES,ids=('budget','cni','resource','tls'))
def test_consumer_callback_cannot_hide_exhaustion_before_final_source_check(tmp_path,monkeypatch,family):
    root,sources,destination,_directory,case,value=fixtures.fixtures(tmp_path,family)
    clock=[1.];calls=[]
    monkeypatch.setattr(projection.time,'monotonic',lambda:clock[0])
    def verifier(**kwargs):
        calls.append(kwargs);clock[0]=3;return dict(value['provenance'])
    with pytest.raises(EvidenceError):
        measurements.collect_measurements(destination,[case],[case],root=root,sources=sources,
            provenance={case:verifier},classifier_deadline=3)
    assert len(calls)==1
