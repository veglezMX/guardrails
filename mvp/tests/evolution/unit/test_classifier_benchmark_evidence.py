"""Synthetic retained rows over the real frozen corpus; never perform inference."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from evolution import classifier_benchmark_evidence as evidence
from evolution.registry import EvidenceError
from guardrails.evolution.classifier_contract import canonical, identity

ROOT = Path(__file__).resolve().parents[4]


def encode(value):
    return canonical(value) + b"\n"


@pytest.fixture
def documents():
    # This corpus was frozen before any scoring; synthetic scores are not
    # acceptance evidence and never touch the real benchmark output directory.
    corpus_raw = (ROOT / evidence.CORPUS_PATH).read_bytes()
    corpus = json.loads(corpus_raw)
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in evidence.SOURCE_FILES}
    rows = [{"case_id": case["case_id"], "language": case["language"], "category": case["category"],
             "family_id": case["family_id"], "expected": case["expected_injection"],
             "predicted": case["expected_injection"], "score": 0.95 if case["expected_injection"] else 0.1,
             "error": None, "windows": 1, "elapsed_ms": 10.0} for case in corpus["cases"]]
    started = {"status": "running", "created_at": "2026-10-12T00:00:00+00:00",
               "corpus_sha256": evidence.CORPUS_SHA256, "sources": sources, "identity": identity(),
               "threshold": 0.9, "network_model_downloads": 0, "warmup_requests": 0,
               "retry_requests": 0, "scope": evidence.SCOPE}
    return corpus_raw, sources, started, rows


def package(documents):
    corpus, sources, started, rows = documents
    raw = b"".join(encode(row) for row in rows)
    # Invoke the frozen producer's report math only to make genuine-format
    # synthetic reports. The consumer independently recomputes its own matrix.
    from services.classifier.benchmark import matrices
    result = {**started, "status": "completed", "startup_seconds": 1.0,
              "cpu": {"child_user_seconds": 2.0, "child_system_seconds": 0.1,
                      "child_max_rss_kib": 1024, "torch_threads": 2, "torch_interop_threads": 1},
              "cases": len(rows), "matrices": matrices(rows), "cases_sha256": hashlib.sha256(raw).hexdigest(),
              "model_card_language": evidence.LANGUAGE_NOTE,
              "cost": {"evaluation_requests": len(rows), "completed_windows": sum(row.get("windows", 0) for row in rows),
                       "failed_requests": sum(row["error"] is not None for row in rows),
                       "total_forward_passes_including_failed_requests": "unavailable",
                       "tool_credits": "unavailable", "tokens": "unavailable"}}
    return corpus, encode(started), encode(result), raw, sources


def error_row(row, error="evaluation_timeout"):
    row.update(error=error, predicted=None, score=None)
    row.pop("windows", None)


def test_exact_all_case_coverage_and_separate_spanish_matrix(documents):
    checked = evidence.validate_benchmark(*package(documents))
    assert checked["cases"] == 600 and checked["matrices"]["en"]["true_positive"] == 200
    assert checked["matrices"]["es"]["true_positive"] == 100
    assert checked["spanish_enforcement_enabled"] is False
    assert checked["deployed_sla_verified"] is False


@pytest.mark.parametrize(("label", "errors", "passed"), ((False, 10, True), (False, 11, False), (True, 20, True), (True, 21, False)))
def test_unchanged_english_fpr_and_recall_boundaries(documents, label, errors, passed):
    selected = [row for row in documents[3] if row["language"] == "en" and row["expected"] is label]
    for row in selected[:errors]:
        row.update(predicted=not label, score=0.1 if label else 0.95)
    if passed:
        assert evidence.validate_benchmark(*package(documents))["matrices"]["en"]["quality_pass"]
    else:
        with pytest.raises(EvidenceError, match="english_quality_failed"):
            evidence.validate_benchmark(*package(documents))


def test_one_english_operational_error_is_never_counted_as_clean(documents):
    error_row(documents[3][0])
    with pytest.raises(EvidenceError, match="english_quality_failed"):
        evidence.validate_benchmark(*package(documents))


def test_observed_failed_candidate_matrix_remains_rejected(documents):
    # The real retained run had EN190TP/10FN/20FP/180TN and ES100TP/32FP.
    # Reproduce that failure shape with synthetic scores; this is a regression,
    # never a replacement for the hash-pinned original 600-row report.
    for language, label, count in (("en", True, 10), ("en", False, 20), ("es", False, 32)):
        selected = [row for row in documents[3] if row["language"] == language and row["expected"] is label]
        for row in selected[:count]:
            row.update(predicted=not label, score=0.1 if label else 0.95)
    with pytest.raises(EvidenceError, match="english_quality_failed"):
        evidence.validate_benchmark(*package(documents))


def test_spanish_operational_failure_is_retained_separately(documents):
    row = next(row for row in documents[3] if row["language"] == "es")
    error_row(row)
    result = evidence.validate_benchmark(*package(documents))
    assert result["matrices"]["en"]["quality_pass"]
    assert result["matrices"]["es"]["operational_errors"] == 1
    assert not result["matrices"]["es"]["quality_pass"] and not result["spanish_enforcement_enabled"]


@pytest.mark.parametrize("change", ("missing", "duplicate", "extra", "reordered", "relabel", "category", "family", "language"))
def test_reported_count_never_substitutes_exact_frozen_rows(documents, change):
    rows = documents[3]
    if change == "missing": rows.pop()
    elif change == "duplicate": rows[-1] = copy.deepcopy(rows[0])
    elif change == "extra": rows.append(copy.deepcopy(rows[0]))
    elif change == "reordered": rows[0], rows[1] = rows[1], rows[0]
    elif change == "relabel": rows[0]["expected"] = not rows[0]["expected"]
    else: rows[0][change if change != "family" else "family_id"] = "unregistered"
    with pytest.raises(EvidenceError): evidence.validate_benchmark(*package(documents))


@pytest.mark.parametrize("change", ("threshold", "identity", "source", "warmup", "retries", "downloads", "status", "matrix", "hash", "cpu", "language-enabled", "cost", "extra"))
def test_forged_or_incomplete_summary_refuses(documents, change):
    corpus, start_raw, result_raw, raw, sources = package(documents)
    start, result = json.loads(start_raw), json.loads(result_raw)
    if change == "threshold": start["threshold"] = result["threshold"] = 0.8
    elif change == "identity": start["identity"]["model_revision"] = result["identity"]["model_revision"] = "foreign"
    elif change == "source": start["sources"][evidence.SOURCE_FILES[0]] = result["sources"][evidence.SOURCE_FILES[0]] = "0" * 64
    elif change in {"warmup", "retries", "downloads"}:
        key = {"warmup": "warmup_requests", "retries": "retry_requests", "downloads": "network_model_downloads"}[change]
        start[key] = result[key] = 1
    elif change == "status": result["status"] = "failed"
    elif change == "matrix": result["matrices"]["en"]["recall"] = 0.5
    elif change == "hash": result["cases_sha256"] = "0" * 64
    elif change == "cpu": result["cpu"]["torch_threads"] = 4
    elif change == "language-enabled": result["matrices"]["es"]["operational_language_enabled"] = True
    elif change == "cost": result["cost"]["evaluation_requests"] = 599
    else: result["private_raw"] = "must not be accepted"
    with pytest.raises(EvidenceError): evidence.validate_benchmark(corpus, encode(start), encode(result), raw, sources)


@pytest.mark.parametrize("change", ("threshold-contradiction", "boolean-score", "score-range", "windows-zero", "windows-excess", "boolean-label", "unknown-error", "raw-field"))
def test_per_case_finite_scores_and_closed_schema(documents, change):
    row = documents[3][0]
    if change == "threshold-contradiction": row["predicted"] = not row["predicted"]
    elif change == "boolean-score": row["score"] = False
    elif change == "score-range": row["score"] = -0.1
    elif change == "windows-zero": row["windows"] = 0
    elif change == "windows-excess": row["windows"] = 17
    elif change == "boolean-label": row["expected"] = 0
    elif change == "unknown-error": error_row(row, "arbitrary-private-error")
    else: row["stdout"] = "private content must never be accepted"
    with pytest.raises(EvidenceError): evidence.validate_benchmark(*package(documents))


def test_relabeling_the_corpus_itself_cannot_make_a_report_valid(documents):
    corpus, start, result, raw, sources = package(documents)
    changed = json.loads(corpus); changed["cases"][0]["expected_injection"] = True
    with pytest.raises(EvidenceError, match="frozen_corpus_changed"):
        evidence.validate_benchmark(encode(changed), start, result, raw, sources)


@pytest.mark.parametrize("raw", (b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'{"x":1,"x":2}', b'[]', b'\xff'))
def test_nonfinite_duplicate_and_invalid_encoding_refuses(raw):
    with pytest.raises(EvidenceError): evidence._json(raw)


@pytest.fixture
def disk_package(tmp_path, documents):
    root, output = tmp_path / "source", tmp_path / "evidence"
    root.mkdir(mode=0o700); output.mkdir(mode=0o700)
    for name in (*evidence.SOURCE_FILES, evidence.CORPUS_PATH):
        target = root / name; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes()); target.chmod(0o644)
    _, started, result, rows, _ = package(documents)
    raw = {"started.json": started, "result.json": result, "cases.jsonl": rows}
    for name, value in raw.items():
        (output / name).write_bytes(value); (output / name).chmod(0o600)
    return root, output, {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}


def test_private_wrapper_requires_independent_pins_and_preserves_all_bytes(disk_package):
    root, output, pins = disk_package
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    assert evidence.verify_benchmark(output, root=root, pins=pins)["status"] == "verified"
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before
    with pytest.raises(EvidenceError): evidence.verify_benchmark(output, root=root, pins={})
    with pytest.raises(EvidenceError): evidence.verify_benchmark(output, root=root, pins={**pins, "result.json": "0" * 64})


@pytest.mark.parametrize("change", ("absent", "partial", "failure", "extra", "public", "symlink", "hardlink", "source", "append"))
def test_absent_partial_failed_or_changed_files_cannot_pass(disk_package, change):
    root, output, pins = disk_package
    if change == "absent": (output / "result.json").unlink()
    elif change == "partial": (output / "cases.jsonl").write_bytes(b'{}\n')
    elif change in {"failure", "extra"}: (output / ("failure.json" if change == "failure" else "foreign")).write_bytes(b'{}')
    elif change == "public": (output / "result.json").chmod(0o644)
    elif change == "symlink":
        (output / "result.json").rename(output.parent / "held-result")
        (output / "result.json").symlink_to(output.parent / "held-result")
    elif change == "hardlink": os.link(output / "result.json", output.parent / "linked-result")
    elif change == "source": (root / evidence.SOURCE_FILES[0]).write_bytes(b'changed source')
    else:
        with (output / "cases.jsonl").open("ab") as stream: stream.write(b'{}\n')
    before = {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()}
    with pytest.raises(EvidenceError): evidence.verify_benchmark(output, root=root, pins=pins)
    assert {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()} == before


def test_output_replacement_during_read_refuses_without_any_write(disk_package, monkeypatch):
    root, output, pins = disk_package
    original = evidence._read
    def swap(path, **kwargs):
        if path == output / "cases.jsonl":
            output.rename(output.parent / "held-output"); output.mkdir(mode=0o700)
            (output / "foreign").write_bytes(b'unchanged')
        return original(path, **kwargs)
    monkeypatch.setattr(evidence, "_read", swap)
    with pytest.raises(EvidenceError): evidence.verify_benchmark(output, root=root, pins=pins)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == {"foreign": b'unchanged'}
