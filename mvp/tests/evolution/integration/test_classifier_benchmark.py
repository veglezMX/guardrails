"""Required acceptance of an independently bound completed CPU benchmark."""

import os
from pathlib import Path

from evolution import classifier_benchmark_measurements as measurement
from evolution.registry import EvidenceError


def test_frozen_heldout_english_quality_and_separate_spanish_report():
    root = Path(__file__).resolve().parents[4]
    path, pin = os.environ.get(measurement.RECEIPT_ENV), os.environ.get(measurement.PIN_ENV)
    if not path or not pin:
        raise EvidenceError("classifier_benchmark_binding_required")
    sources = measurement.gate_sources(root)
    checked = measurement.verify_binding(path, pin, root=root, sources=sources)
    measurement.record(checked, root=root, sources=sources)
    result = checked["benchmark"]
    assert result["cases"] == 600
    assert result["matrices"]["en"]["quality_pass"] is True
    assert result["matrices"]["en"]["recall"] >= 0.9
    assert result["matrices"]["en"]["false_positive_rate"] <= 0.05
    assert result["spanish_enforcement_enabled"] is False
