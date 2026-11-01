"""The noisy-neighbor stage sidecar is paired, source-bound and never optional on pass."""

import json
import os
from copy import deepcopy

import pytest
from evolution import run, stage_measurements
from evolution.measurements import (
    FILES,
    NOISY_CASE,
    REAL_MODEL_CASE,
    collect_measurements,
    prepare_directory,
)
from evolution.registry import EvidenceError
from test_measurement_evidence import sample, sample_v3, stage_sample, stage_sources


def setup_pair(tmp_path):
    root = tmp_path / "source"
    frozen = stage_sources(root)
    destination = tmp_path / "report"
    directory = prepare_directory(destination, NOISY_CASE)
    measurement = sample_v3()
    primary = directory / FILES[NOISY_CASE]
    secondary = directory / stage_measurements.FILENAME
    run.write_once(primary, measurement)
    run.write_once(secondary, stage_sample(measurement))
    return root, frozen, destination, primary, secondary


def test_stage_pair_binds_both_files_to_frozen_sources_and_exact_failure_rows(tmp_path):
    root, frozen, destination, primary, secondary = setup_pair(tmp_path)
    assert isinstance(FILES[NOISY_CASE], str)
    result = collect_measurements(destination, [NOISY_CASE], [NOISY_CASE], root=root, sources=frozen)
    assert set(result) == {str(p.relative_to(destination)) for p in (primary, secondary)}
    for path in (primary, secondary):
        info = result[str(path.relative_to(destination))]
        assert info == {"sha256": run.digest(path), "size_bytes": path.stat().st_size,
                        "source_files": frozen}
    original = json.loads(secondary.read_bytes())
    for change in (lambda v: v["rows"][0].update(ordinal=31),
                   lambda v: v["rows"][0].update(replica=1),
                   lambda v: v["rows"][0].update(status=504),
                   lambda v: v["rows"][0].update(response_ms=10 ** 1000),
                   lambda v: v["omitted"].update(contention_a=0),
                   lambda v: v["rows"][0].update(request_id="private-stage-canary")):
        value = deepcopy(original)
        change(value)
        secondary.write_text(json.dumps(value))
        with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
            collect_measurements(destination, [NOISY_CASE], root=root, sources=frozen)


def test_stage_pair_requires_primary_v3_and_second_file_for_pass_only(tmp_path):
    root, frozen, destination, primary, secondary = setup_pair(tmp_path)
    primary_bytes, secondary_bytes = primary.read_bytes(), secondary.read_bytes()
    primary.unlink()
    with pytest.raises(EvidenceError, match="measurement_evidence_missing"):
        collect_measurements(destination, [NOISY_CASE], root=root, sources=frozen)
    primary.write_bytes(primary_bytes)
    primary.chmod(0o600)
    secondary.unlink()
    # Historical base-only diagnostics remain readable, never a passed pair.
    assert len(collect_measurements(destination, [NOISY_CASE])) == 1
    with pytest.raises(EvidenceError, match="measurement_evidence_missing"):
        collect_measurements(destination, [NOISY_CASE], [NOISY_CASE], root=root, sources=frozen)
    primary.write_text(json.dumps(sample()))
    assert len(collect_measurements(destination, [NOISY_CASE])) == 1
    secondary.write_bytes(secondary_bytes)
    secondary.chmod(0o600)
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
        collect_measurements(destination, [NOISY_CASE], root=root, sources=frozen)
    primary.write_bytes(primary_bytes)
    other = prepare_directory(destination, REAL_MODEL_CASE)
    run.write_once(other / stage_measurements.FILENAME, {})
    with pytest.raises(EvidenceError, match="measurement_unexpected_files"):
        collect_measurements(destination, [NOISY_CASE, REAL_MODEL_CASE], root=root, sources=frozen)


def test_stage_collection_validates_primary_before_opening_secondary(tmp_path, monkeypatch):
    from evolution import measurements
    root, frozen, destination, primary, secondary = setup_pair(tmp_path)
    primary.write_text('{"schema_version":"evolution-noisy-neighbor-v3"}')
    calls = []
    original = measurements._read_value
    def read(path, limit):
        calls.append(path)
        if path == secondary:
            raise AssertionError("invalid primary must stop before reading secondary")
        return original(path, limit)
    monkeypatch.setattr(measurements, "_read_value", read)
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
        collect_measurements(destination, [NOISY_CASE], root=root, sources=frozen)
    assert calls == [primary]


def test_stage_reader_rejects_private_file_source_and_closed_schema_tampering(tmp_path):
    root, frozen, destination, _, secondary = setup_pair(tmp_path)
    valid = secondary.read_bytes()
    for raw in (b'{"rows":[],"rows":[]}', b'{"rows":NaN}', b'x' * 16385,
                b'{"private":"private-stage-canary"}'):
        secondary.write_bytes(raw)
        with pytest.raises(EvidenceError):
            collect_measurements(destination, [NOISY_CASE], root=root, sources=frozen)
    secondary.write_bytes(valid)
    secondary.chmod(0o644)
    with pytest.raises(EvidenceError, match="measurement_path_invalid"):
        collect_measurements(destination, [NOISY_CASE], root=root, sources=frozen)
    secondary.chmod(0o600)
    saved = tmp_path / "saved-stage"
    secondary.rename(saved)
    secondary.symlink_to(saved)
    with pytest.raises(EvidenceError):
        collect_measurements(destination, [NOISY_CASE], root=root, sources=frozen)
    secondary.unlink()
    os.mkfifo(secondary, 0o600)
    with pytest.raises(EvidenceError, match="measurement_path_invalid"):
        collect_measurements(destination, [NOISY_CASE], root=root, sources=frozen)
    secondary.unlink()
    saved.rename(secondary)
    changed = root / stage_measurements.SOURCES[0]
    changed.write_text("# changed-stage-source\n")
    with pytest.raises(EvidenceError, match="diagnostic_source_changed"):
        collect_measurements(destination, [NOISY_CASE], root=root, sources=frozen)
