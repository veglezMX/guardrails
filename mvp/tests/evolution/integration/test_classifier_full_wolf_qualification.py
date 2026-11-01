"""Required retained qualification; never invoke a model from the gate worker."""

import os
from pathlib import Path

from evolution import classifier_full_qualification_measurements as measurement
from evolution.registry import EvidenceError


def test_three_frozen_corpora_require_every_english_pass_and_resource_admission():
    root = Path(__file__).resolve().parents[4]
    path, pin = (
        os.environ.get(measurement.RECEIPT_ENV),
        os.environ.get(measurement.PIN_ENV),
    )
    if not path or not pin:
        raise EvidenceError("classifier_full_qualification_binding_required")
    sources = measurement.gate_sources(root)
    checked = measurement.verify_binding(path, pin, root=root, sources=sources)
    measurement.record(checked, root=root, sources=sources)
    result = checked["qualification"]
    assert result["cases"] == 1800
    assert result["status"] == "qualified"
    assert result["resources_pass"] is True
    for kind in ("original", "fresh", "fresh_full"):
        assert result["matrices"][kind]["en"]["quality_pass"] is True
        assert result["matrices"][kind]["en"]["recall"] >= 0.9
        assert result["matrices"][kind]["en"]["false_positive_rate"] <= 0.05
    assert result["production_enabled"] is False
    assert result["spanish_enforcement_enabled"] is False
