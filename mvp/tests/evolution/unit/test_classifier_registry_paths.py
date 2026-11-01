"""Classifier test registration preserves the closed gate and earlier inventories."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from evolution.registry import EvidenceError, load_registry


def document(nodeid):
    return {
        "schema_version": "guardrails-evolution-cases-v1",
        "phases": {
            str(number): {
                "status": "ready" if number == 3 else "unavailable",
                "required_families": ["detectors"],
                "cases": [
                    {
                        "case_id": "DET-CLASSIFIER-IMAGE-INPUTS",
                        "requirement_id": "P3.CLASSIFIER.PREPARATION",
                        "profile": "local",
                        "mode": "remote",
                        "family": "detectors",
                        "nodeid": nodeid,
                        "assertions": ["The exact offline input closure is enforced."],
                        "timeout_s": 60,
                    }
                ]
                if number == 3
                else [],
            }
            for number in range(1, 7)
        },
    }


def write_document(tmp_path, value):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(value))
    return path


@pytest.mark.parametrize(
    "nodeid",
    (
        "services/classifier/tests/test_image_context.py::test_sealed_context",
        "services/classifier/tests/test_preparation.py::test_inputs[missing-model]",
        "services/classifier/tests/unit/test_future.py::TestInputs::test_safe",
        "mvp/tests/evolution/unit/test_fixture.py::test_behavior",
        "tests/evolution-browser/test_smoke.py::test_console",
    ),
)
def test_classifier_path_and_existing_roots_load_exact_registered_node(tmp_path, nodeid):
    path = write_document(tmp_path, document(nodeid))
    result = load_registry(path, 3)
    assert len(result) == 1
    assert result[0].nodeid == nodeid
    assert result[0].assertions == ("The exact offline input closure is enforced.",)


@pytest.mark.parametrize(
    "nodeid",
    (
        "/services/classifier/tests/test_case.py::test_case",
        "../services/classifier/tests/test_case.py::test_case",
        "services/classifier/tests/../app.py::test_case",
        "services/classifier/tests/nested/../../app.py::test_case",
        "services/classifier/tests/../../../foreign/test_case.py::test_case",
        "services/other/tests/test_case.py::test_case",
        "services/classifier/tests-extra/test_case.py::test_case",
        "services/classifier/testing/test_case.py::test_case",
        "services/classifier/test/test_case.py::test_case",
        "services/classifier/app.py::test_case",
        "services/classifier/tests.py::test_case",
        "foreign/services/classifier/tests/test_case.py::test_case",
        "--services/classifier/tests/test_case.py::test_case",
        "services\\classifier\\tests\\test_case.py::test_case",
        "services/classifier/tests/test_case.txt::test_case",
        "services/classifier/tests/test_case.py",
        "services/classifier/tests/test_case.py::",
        "services/classifier/tests/::test_case",
        "services/classifier/tests/test_case.py/::test_case",
    ),
)
def test_classifier_prefix_does_not_admit_traversal_or_foreign_roots(tmp_path, nodeid):
    with pytest.raises(EvidenceError, match="^invalid_test_nodeid$"):
        load_registry(write_document(tmp_path, document(nodeid)), 3)


@pytest.mark.parametrize("duplicate", ("nodeid", "case_id"))
def test_classifier_cases_keep_cross_phase_uniqueness(tmp_path, duplicate):
    value = document("services/classifier/tests/test_preparation.py::test_inputs")
    original = value["phases"]["3"]["cases"][0]
    other = dict(original)
    if duplicate == "nodeid":
        other["case_id"] = "DET-DIFFERENT-ID"
    else:
        other["nodeid"] = "mvp/tests/evolution/unit/test_fixture.py::test_different"
    value["phases"]["2"]["cases"] = [other]
    value["phases"]["2"]["status"] = "ready"
    with pytest.raises(EvidenceError, match="^duplicate_case$"):
        load_registry(write_document(tmp_path, value), 3)


@pytest.mark.parametrize(
    ("field", "changed", "reason"),
    (
        ("assertions", [], "missing_expected_assertions"),
        ("assertions", ["  "], "missing_expected_assertions"),
        ("family", "unregistered-family", "invalid_case_requirement"),
        ("profile", "bedrock-live", "invalid_case_profile"),
        ("timeout_s", 1201, "invalid_case_timeout"),
    ),
)
def test_classifier_paths_cannot_relax_case_evidence(tmp_path, field, changed, reason):
    value = document("services/classifier/tests/test_preparation.py::test_inputs")
    value["phases"]["3"]["cases"][0][field] = changed
    with pytest.raises(EvidenceError, match=f"^{reason}$"):
        load_registry(write_document(tmp_path, value), 3)


def test_classifier_path_does_not_make_an_unavailable_phase_pass(tmp_path):
    value = document("services/classifier/tests/test_preparation.py::test_inputs")
    value["phases"]["3"]["status"] = "unavailable"
    with pytest.raises(EvidenceError, match="^phase_not_implemented$"):
        load_registry(write_document(tmp_path, value), 3)


def test_registering_classifier_case_preserves_prior_phase_cases_and_source(tmp_path):
    source = Path(__file__).resolve().parents[3] / "evolution" / "cases.json"
    original_bytes = source.read_bytes()
    original_document = json.loads(original_bytes)
    original_cases = {phase: load_registry(source, phase) for phase in (1, 2)}
    value = copy.deepcopy(original_document)
    selected = document("services/classifier/tests/test_preparation.py::test_inputs")
    value["phases"]["3"] = selected["phases"]["3"]
    path = write_document(tmp_path, value)
    assert len(load_registry(path, 3)) == 1
    for phase in (1, 2):
        assert load_registry(path, phase) == original_cases[phase]
        assert value["phases"][str(phase)] == original_document["phases"][str(phase)]
        assert [asdict(case) for case in load_registry(path, phase)] == [
            asdict(case) for case in original_cases[phase]
        ]
    for phase in (4, 5, 6):
        assert value["phases"][str(phase)] == original_document["phases"][str(phase)]
    assert source.read_bytes() == original_bytes
