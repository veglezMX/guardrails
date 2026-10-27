"""Assert a separately executed, independently pinned real loaded-worker run."""
import os
from pathlib import Path

import pytest
from evolution import classifier_loaded_measurements as measurement
from evolution.classifier_loaded_evidence import CASES, require


@pytest.mark.parametrize("case", CASES)
def test_loaded_classifier_lifecycle(case):
    # Both the private binding path and its digest must come from the reviewed
    # parent launch configuration. Never discover or self-pin a latest run.
    path = os.environ.get(measurement.RECEIPT_ENV, "")
    expected = os.environ.get(measurement.PIN_ENV, "")
    require(bool(path) and bool(expected), "loaded_binding_required")
    root = Path(__file__).resolve().parents[4]
    case_id = measurement.CASE_IDS[case]
    sources = measurement.gate_sources(root, case_id)
    checked = measurement.verify_binding(path, expected, root=root, sources=sources)
    measurement.record(checked, case_id=case_id, root=root, sources=sources)
    result = checked["loaded"]
    assert result["status"] == "passed"
    assert next(row for row in result["cases"] if row["case"] == case)["status"] == "passed"
    assert result["scope"] == "loaded_worker_and_asgi_no_external_network"
