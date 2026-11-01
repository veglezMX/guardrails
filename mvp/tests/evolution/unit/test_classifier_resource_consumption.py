"""A retained resource observation cannot self-authorize successful collection."""
import copy
import hashlib

import pytest
from evolution import classifier_resource_measurements as evidence
from evolution.registry import EvidenceError

from mvp.tests.evolution.unit import test_classifier_resource_sampling as fixture


def sources_fixture(tmp_path):
    sources = {}
    for name in evidence.SOURCES:
        path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True)
        raw = ("# " + name + "\n").encode(); path.write_bytes(raw)
        sources[name] = hashlib.sha256(raw).hexdigest()
    return sources


@pytest.mark.parametrize("fault", (None, "missing", "failed_parent", "initial_only", "proof", "extra_proof",
    "source_before", "source_after", "map_mutation", "value_mutation", "detached"))
def test_successful_resource_consumption_requires_independent_completed_parent_proof(tmp_path, fault):
    sources = sources_fixture(tmp_path); value = fixture.payload(); saved = copy.deepcopy(value)
    def verifier(**kwargs):
        assert kwargs["phase"] == "consume" and kwargs["operation"] == value["operation"]
        assert kwargs["case_id"] == fixture.CASE and kwargs["binding_sha256"] == value["binding_sha256"]
        assert kwargs["observation"] == saved
        if fault in {"failed_parent", "initial_only"}: raise EvidenceError("synthetic authentication refused")
        if fault == "proof": return {**fixture.PROOF, "cold": "b" * 64}
        if fault == "extra_proof": return {**fixture.PROOF, "extra": "b" * 64}
        if fault == "source_after": (tmp_path / evidence.SOURCES[0]).write_text("# drift\n")
        if fault == "map_mutation": sources[evidence.SOURCES[0]] = "c" * 64
        if fault == "value_mutation": value["usage"]["classifier_cpu_usage_delta_usec"] += 1
        if fault == "detached": kwargs["observation"].clear(); kwargs["sources"].clear()
        return dict(fixture.PROOF)
    if fault == "source_before": (tmp_path / evidence.SOURCES[0]).write_text("# drift\n")
    if fault in {None, "detached"}:
        assert evidence.validate(value, fixture.CASE, root=tmp_path, sources=sources, complete=True,
            verify_provenance=verifier) == saved
    else:
        with pytest.raises(ValueError): evidence.validate(value, fixture.CASE, root=tmp_path, sources=sources,
            complete=True, verify_provenance=None if fault == "missing" else verifier)


def test_retention_is_not_a_claim_that_future_parent_completion_exists(tmp_path, monkeypatch):
    root = tmp_path / "root"; sources = sources_fixture(root)
    output = tmp_path / "run/measurements" / fixture.CASE; output.mkdir(parents=True, mode=0o700)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(output))
    value = fixture.payload()
    assert evidence.record(value, fixture.CASE, root=root, sources=sources) == value
    assert (output / evidence.FILENAME).exists()
    with pytest.raises(ValueError): evidence.validate(value, fixture.CASE, root=root, sources=sources, complete=True)
