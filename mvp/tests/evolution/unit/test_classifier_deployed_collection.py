"""Collector closure around synthetic families; no deployment or model calls."""
from __future__ import annotations

import hashlib
import socket
import subprocess
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from evolution import classifier_deployed_collection as collection
from evolution import measurements
from evolution.registry import EvidenceError
from guardrails.evolution.classifier_contract import canonical

from mvp.tests.evolution.unit import (
    test_classifier_budget_measurements as budget_fixture,
)
from mvp.tests.evolution.unit import test_classifier_cni_measurements as cni_fixture
from mvp.tests.evolution.unit import (
    test_classifier_resource_sampling as resource_fixture,
)
from mvp.tests.evolution.unit import test_classifier_tls_measurements as tls_fixture

FAMILIES = (collection.budget, collection.cni, collection.resource, collection.tls)


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Synthetic collector suite forbids external operations")
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def fixtures(tmp_path, family):
    root, sources = tls_fixture.source_fixture(tmp_path, module=family)
    if family is collection.budget:
        value = budget_fixture.payload(sources=sources)
        case = value["case_id"]
        extra = {family.INTENT: family.intent(value["invocation"], case, sources),
            family.AUTHORIZATION: family.authorization(value["invocation"], value["operation"], case,
                value["intent_sha256"], value["provenance"], value["binding"])}
    elif family is collection.cni:
        value = cni_fixture.payload(sources)
        case = family.CASE
        extra = {family.INTENT: family.intent(value["operation"], sources),
            family.AUTHORIZATION: family.authorization(value["operation"], value["intent_sha256"],
                value["provenance"], value["group"]["readiness_binding"], value["group"]["image_binding"])}
    elif family is collection.resource:
        value = resource_fixture.payload()
        case = resource_fixture.CASE
        extra = {}
    else:
        value = tls_fixture.payload(sources)
        case = value["case_id"]
        extra = {family.INTENT: family.intent(value["invocation"], case, sources),
            family.AUTHORIZATION: family.authorization(value)}
    destination = tmp_path / "report"
    directory = destination / "measurements" / case
    directory.mkdir(mode=0o700, parents=True)
    for name, data in {family.FILENAME: value, **extra}.items():
        path = directory / name
        path.write_bytes(canonical(data) + b"\n")
        path.chmod(0o600)
    return root, sources, destination, directory, case, value


@pytest.mark.parametrize("family", FAMILIES, ids=("budget", "cni", "resource", "tls"))
def test_all_original_files_are_bound_to_the_explicit_consume_callback(tmp_path, family):
    root, sources, destination, directory, case, value = fixtures(tmp_path, family)
    calls = []
    def verifier(**kwargs):
        calls.append(kwargs)
        assert kwargs["phase"] == "consume"
        return dict(value["provenance"])
    records = measurements.collect_measurements(destination, [case], [case], root=root,
        sources=sources, provenance={case: verifier}, classifier_deadline=time.monotonic()+60)
    assert len(calls) == 1 and len(records) == len(collection.names_for(family))
    for name in collection.names_for(family):
        row = records[str((directory / name).relative_to(destination))]
        assert row["sha256"] == hashlib.sha256((directory / name).read_bytes()).hexdigest()
        assert row["source_files"] == sources


@pytest.mark.parametrize("family", FAMILIES, ids=("budget", "cni", "resource", "tls"))
@pytest.mark.parametrize("fault", (
    "no_verifier", "foreign_case_verifier", "refused_completion", "different_proof",
    "extra_file", "missing_primary", "symlink", "hardlink", "wide_mode",
    "changed_after_verifier", "source_changed", "bad_parent_mode",
))
def test_required_deployed_evidence_rejects_unverified_or_changed_bytes(tmp_path, family, fault):
    root, sources, destination, directory, case, value = fixtures(tmp_path, family)
    def verifier(**kwargs):
        if fault == "refused_completion":
            raise RuntimeError("Synthetic failed parent invocation")
        if fault == "different_proof":
            return {**value["provenance"], "qualification": "b" * 64}
        if fault == "changed_after_verifier":
            (directory / family.FILENAME).write_bytes(canonical(value) + b" \n")
        return dict(value["provenance"])
    proof = {case: verifier}
    path = directory / family.FILENAME
    if fault == "no_verifier": proof = None
    elif fault == "foreign_case_verifier": proof = {"foreign": verifier}
    elif fault == "extra_file": (directory / "unexpected.json").write_text("{}")
    elif fault == "missing_primary": path.unlink()
    elif fault == "symlink":
        target = tmp_path / "target.json"; path.rename(target); path.symlink_to(target)
    elif fault == "hardlink": (tmp_path / "duplicate.json").hardlink_to(path)
    elif fault == "wide_mode": path.chmod(0o644)
    elif fault == "source_changed": (root / family.SOURCES[0]).write_text("# altered\n")
    elif fault == "bad_parent_mode": directory.chmod(0o755)
    with pytest.raises(EvidenceError):
        measurements.collect_measurements(destination, [case], [case], root=root,
            sources=sources, provenance=proof, classifier_deadline=time.monotonic()+60)


@pytest.mark.parametrize("family", (collection.budget, collection.cni, collection.tls), ids=("budget", "cni", "tls"))
@pytest.mark.parametrize("fault", ("missing_intent", "missing_authorization", "changed_intent", "changed_authorization", "orphan_intent"))
def test_auxiliary_journals_are_exact_original_files_not_directory_allowances(tmp_path, family, fault):
    root, sources, destination, directory, case, value = fixtures(tmp_path, family)
    if fault.startswith("missing_"):
        name = family.INTENT if fault == "missing_intent" else family.AUTHORIZATION
        (directory / name).unlink()
    elif fault.startswith("changed_"):
        name = family.INTENT if fault == "changed_intent" else family.AUTHORIZATION
        (directory / name).write_bytes((directory / name).read_bytes() + b" ")
    else:
        (directory / family.FILENAME).unlink()
        (directory / family.AUTHORIZATION).unlink()
    with pytest.raises(EvidenceError):
        measurements.collect_measurements(destination, [case], [case], root=root,
            sources=sources, provenance={case: lambda **kwargs: dict(value["provenance"])}, classifier_deadline=time.monotonic()+60)


@pytest.mark.parametrize("family", FAMILIES, ids=("budget", "cni", "resource", "tls"))
def test_nonpassing_case_retains_component_bytes_without_claiming_parent_success(tmp_path, family):
    root, sources, destination, directory, case, _value = fixtures(tmp_path, family)
    records = measurements.collect_measurements(destination, [case], [], root=root, sources=sources, classifier_deadline=time.monotonic()+60)
    assert len(records) == len(collection.names_for(family))
    assert str((directory / family.FILENAME).relative_to(destination)) in records


@pytest.mark.parametrize("family", FAMILIES, ids=("budget", "cni", "resource", "tls"))
def test_directory_enumeration_stops_at_first_entry_beyond_the_closed_set(tmp_path, monkeypatch, family):
    _, _, _, directory, _, _ = fixtures(tmp_path, family)
    expected = sorted(collection.names_for(family))
    info = (directory / family.FILENAME).stat()
    visited = []
    def rows():
        for name in [*expected, "unexpected.json"]:
            visited.append(name)
            yield SimpleNamespace(name=name, stat=lambda **kwargs: info)
        raise AssertionError("Collector enumerated past its exact closed file limit")
    @contextmanager
    def bounded_scandir(path):
        assert path == directory
        yield rows()
    monkeypatch.setattr(collection.os, "scandir", bounded_scandir)
    with pytest.raises(EvidenceError):
        collection.attachment_names(directory, family)
    assert visited == [*expected, "unexpected.json"]
