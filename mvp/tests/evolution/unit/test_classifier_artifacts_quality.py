"""Small synthetic artifact checks and frozen quality math, without ML loads."""
import hashlib
import json
import sys
from pathlib import Path

import pytest
from guardrails.evolution import classifier_contract as contract

from services.classifier import benchmark, worker


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / "model"
    root.mkdir()
    raw = b"small synthetic model fixture; never deserialized"
    (root / "model.safetensors").write_bytes(raw)
    monkeypatch.setattr(worker, "FILES", {"model.safetensors": (hashlib.sha256(raw).hexdigest(), len(raw))})
    return root


def test_small_artifact_verification_does_not_import_machine_learning(bundle):
    before = {name for name in sys.modules if name == "torch" or name.startswith("transformers")}
    worker.verify_bundle(bundle)
    assert {name for name in sys.modules if name == "torch" or name.startswith("transformers")} == before


@pytest.mark.parametrize("change", ["missing", "extra", "different", "symlink", "hardlink"])
def test_local_artifact_closure_refuses_unsafe_or_changed_bytes(bundle, change):
    path = bundle / "model.safetensors"
    if change == "missing":
        path.unlink()
    elif change == "extra":
        (bundle / "config.py").write_text("untrusted code")
    elif change == "different":
        path.write_bytes(b"different bytes")
    elif change == "symlink":
        outside = bundle.parent / "outside"
        path.rename(outside)
        path.symlink_to(outside)
    else:
        (bundle.parent / "hardlink").hardlink_to(path)
    with pytest.raises((contract.ClassifierError, OSError)):
        worker.verify_bundle(bundle)


def test_artifact_replacement_while_hashing_is_rejected(bundle, monkeypatch):
    original = worker.hashlib.file_digest
    def replace(stream, algorithm):
        result = original(stream, algorithm)
        path = bundle / "model.safetensors"
        raw = path.read_bytes()
        path.unlink()
        path.write_bytes(raw)
        return result
    monkeypatch.setattr(worker.hashlib, "file_digest", replace)
    with pytest.raises(contract.ClassifierError):
        worker.verify_bundle(bundle)


def test_wrong_python_fails_before_model_import(monkeypatch):
    monkeypatch.setattr(worker.sys, "version_info", (3, 13, 15))
    monkeypatch.setattr(worker, "verify_bundle", lambda _: pytest.fail("wrong interpreter must precede artifact scan"))
    with pytest.raises(contract.ClassifierError):
        worker.load_engine("/never-read")


def test_frozen_heldout_counts_roles_labels_and_predeclared_thresholds():
    path = Path(__file__).parents[1] / "fixtures/injection-heldout-v1.json"
    corpus = benchmark.read_corpus(path)
    assert len(corpus["cases"]) == 600
    english = [row for row in corpus["cases"] if row["language"] == "en"]
    assert sum(row["quoted_security"] for row in english) == 100
    assert sum(row["indirect_retrieval"] for row in english) == 150
    assert {row["part"]["role"] for row in english} == {"system", "user", "assistant", "retrieval", "tool"}
    assert corpus["threshold"] == 0.9 and corpus["minimum_recall"] == 0.9 and corpus["maximum_false_positive_rate"] == 0.05


def test_relabeling_even_one_frozen_case_is_refused(tmp_path):
    path = Path(__file__).parents[1] / "fixtures/injection-heldout-v1.json"
    corpus = json.loads(path.read_bytes())
    corpus["cases"][0]["expected_injection"] = True
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(corpus))
    with pytest.raises(ValueError, match="frozen_corpus_changed"):
        benchmark.read_corpus(changed)


def synthetic_scores():
    return [{"language": language, "expected": label, "predicted": label, "elapsed_ms": 10.0} for language, count in (("en", 200), ("es", 100)) for label in (False, True) for _ in range(count)]


def test_quality_metrics_separate_spanish_and_do_not_enable_language():
    result = benchmark.matrices(synthetic_scores())
    assert result["en"]["quality_pass"] and result["es"]["quality_pass"]
    assert result["en"]["true_positive"] == 200 and result["es"]["true_positive"] == 100
    assert result["es"]["operational_language_enabled"] is False


@pytest.mark.parametrize("failures,expected", [(10, True), (11, False)])
def test_false_positive_gate_uses_exact_benign_denominator(failures, expected):
    rows = synthetic_scores()
    for row in rows[:failures]:
        row["predicted"] = True
    result = benchmark.matrices(rows)["en"]
    assert result["quality_pass"] is expected and result["false_positive_rate"] == failures / 200


@pytest.mark.parametrize("misses,expected", [(20, True), (21, False)])
def test_recall_gate_does_not_lower_threshold_or_drop_misses(misses, expected):
    rows = synthetic_scores()
    for row in rows[200:200 + misses]:
        row["predicted"] = False
    result = benchmark.matrices(rows)["en"]
    assert result["quality_pass"] is expected and result["recall"] == (200 - misses) / 200


def test_any_operational_error_fails_quality_instead_of_becoming_clean():
    rows = synthetic_scores()
    rows[0]["predicted"] = None
    result = benchmark.matrices(rows)["en"]
    assert result["operational_errors"] == 1 and not result["quality_pass"]
    assert result["true_negative"] == 199 and result["false_positive"] == 0
