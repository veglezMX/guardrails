"""Synthetic-only baseline bridge; no production module authority changes."""
import hashlib
import pytest
from evolution import classifier_provenance as original
from evolution import classifier_provenance_v3 as successor
from mvp.tests.evolution.unit import test_classifier_provenance as fixture

@pytest.fixture(autouse=True)
def synthetic_pins(monkeypatch, request):
    if getattr(request.module, "REAL_CHAIN", False):
        return
    for module in (original, successor):
        monkeypatch.setattr(module, "BASELINE_CORPORA", {key: hashlib.sha256(module.canonical(fixture.synthetic_corpus(key))+b"\n").hexdigest() for key in ("original", "heldout_1", "heldout_2")})
        monkeypatch.setattr(module, "SELECTION_APPROVAL_SHA256", hashlib.sha256(module.canonical(fixture.synthetic_approval())+b"\n").hexdigest())
