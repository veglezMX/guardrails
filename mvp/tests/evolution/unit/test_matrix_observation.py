"""Original matrix-call evidence is bounded, source-bound and content-free."""

import asyncio
import hashlib
import importlib.util
import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from evolution import run
from evolution.diagnostics import write_catalog
from evolution.measurements import (
    FILES,
    MATRIX_CASES,
    MATRIX_READINESS_ROLES,
    MATRIX_SCHEMA,
    MATRIX_SCHEMA_V2,
    MATRIX_SOURCES,
    collect_measurements,
    prepare_directory,
    validate_matrix_observation,
)
from evolution.registry import EvidenceError
from guardrails.evolution.errors import EvolutionError
from test_runner import case, make_test_root

ROOT = Path(__file__).resolve().parents[4]
CASE = next(iter(MATRIX_CASES))
CANARY = "private-matrix-token-prompt-never-retained"


def sources(root):
    result = {}
    for name in MATRIX_SOURCES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def public_source():\n    return None\n")
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def sample(*, completed=False):
    return {
        "schema_version": MATRIX_SCHEMA, "tenant_side": "A", "state": "observe",
        "actual_attempts": 1, "completed": int(completed), "rows": [{
            "call_ordinal": 0, "tenant_side": "A", "http_status": 200 if completed else 0,
            "wire_code": None, "elapsed_ms": 15002.5, "sample_completed": completed,
            "outcome": "returned" if completed else "other_error", "sample_error": None,
        }],
    }


@pytest.fixture
def wrapper(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "mvp/tests/evolution/e2e"))
    path = ROOT / MATRIX_SOURCES[0]
    spec = importlib.util.spec_from_file_location("matrix_fixture_observation_port", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evidence(wrapper, tmp_path, monkeypatch):
    frozen = sources(tmp_path / "source")
    monkeypatch.setattr(wrapper, "ROOT", tmp_path / "source")
    directory = prepare_directory(tmp_path / "report", CASE)
    write_catalog(directory.parents[1] / (CASE + ".sources.json"), wrapper.ROOT, frozen)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(directory))
    return directory, frozen



def ready(wrapper):
    observation = wrapper.ReadinessObservation()
    for index, _ in enumerate(MATRIX_READINESS_ROLES):
        observation.begin(index)
        observation.returned(index, SimpleNamespace(status_code=200))
    observation.finished()
    return observation


def test_matrix_schema_distinguishes_timeout_headers_validation_and_cleanup(tmp_path):
    frozen = sources(tmp_path)
    values = [sample(), sample(completed=True)]
    header_error = sample()
    header_error["rows"][0].update(http_status=503, wire_code="evaluator_unavailable",
                                   outcome="evolution_error", sample_error="evaluator_unavailable")
    values.append(header_error)
    boundary_mapping = sample()
    boundary_mapping["rows"][0].update(http_status=503, wire_code="unsupported_surface",
                                       outcome="evolution_error", sample_error="evaluator_unavailable")
    values.append(boundary_mapping)
    closed_after_validation = sample(completed=True)
    closed_after_validation["rows"][0]["outcome"] = "other_error"
    values.append(closed_after_validation)
    two = sample(completed=True)
    two["rows"].append({**sample()["rows"][0], "call_ordinal": 1})
    two["actual_attempts"] = 2
    values.append(two)
    for value in values:
        validate_matrix_observation(value, CASE, root=tmp_path, sources=frozen)
        assert len(json.dumps(value).encode()) < 16384
    assert closed_after_validation["completed"] == 1
    assert header_error["completed"] == 0


def test_matrix_schema_rejects_private_fields_nonfinite_values_and_false_partitions(tmp_path):
    frozen = sources(tmp_path)
    mutations = (
        lambda v: v.update(prompt=CANARY),
        lambda v: v.update(tenant_side="B"),
        lambda v: v.update(state="block"),
        lambda v: v.update(actual_attempts=True),
        lambda v: v.update(completed=1),
        lambda v: v["rows"][0].update(request_id=CANARY),
        lambda v: v["rows"][0].update(call_ordinal=True),
        lambda v: v["rows"][0].update(call_ordinal=1),
        lambda v: v["rows"][0].update(http_status=True),
        lambda v: v["rows"][0].update(http_status=600),
        lambda v: v["rows"][0].update(wire_code=CANARY),
        lambda v: v["rows"][0].update(wire_code="forbidden", http_status=200),
        lambda v: v["rows"][0].update(elapsed_ms=float("nan")),
        lambda v: v["rows"][0].update(elapsed_ms=float("inf")),
        lambda v: v["rows"][0].update(elapsed_ms=600001),
        lambda v: v["rows"][0].update(outcome=CANARY),
        lambda v: v["rows"][0].update(outcome="returned"),
        lambda v: v["rows"][0].update(sample_completed=1),
        lambda v: v["rows"][0].update(sample_error=CANARY),
        lambda v: v["rows"][0].update(sample_error="forbidden"),
    )
    for mutate in mutations:
        value = sample()
        mutate(value)
        with pytest.raises(EvidenceError, match="^measurement_schema_invalid$"):
            validate_matrix_observation(value, CASE, root=tmp_path, sources=frozen)
    value = sample()
    value["rows"].append({**value["rows"][0], "call_ordinal": 1})
    value["actual_attempts"] = 2
    with pytest.raises(EvidenceError, match="^measurement_schema_invalid$"):
        validate_matrix_observation(value, CASE, root=tmp_path, sources=frozen)
    value["rows"].append({**value["rows"][0], "call_ordinal": 2})
    value["actual_attempts"] = 3
    with pytest.raises(EvidenceError, match="^measurement_schema_invalid$"):
        validate_matrix_observation(value, CASE, root=tmp_path, sources=frozen)


def test_matrix_case_allowlist_is_exactly_six_and_rejects_cross_case_substitution(tmp_path):
    frozen = sources(tmp_path)
    assert set(MATRIX_CASES.values()) == {
        (side, state) for side in ("A", "B") for state in ("observe", "warn", "redact")
    }
    for case_id, (side, state) in MATRIX_CASES.items():
        value = sample()
        value.update(tenant_side=side, state=state)
        value["rows"][0]["tenant_side"] = side
        validate_matrix_observation(value, case_id, root=tmp_path, sources=frozen)
        assert FILES[case_id] == "cluster-matrix-observation.json"
    for case_id in (CASE.replace("OBSERVE", "BLOCK"), [], "unknown-case"):
        with pytest.raises(EvidenceError, match="^measurement_schema_invalid$"):
            validate_matrix_observation(sample(), case_id, root=tmp_path, sources=frozen)
    with pytest.raises(EvidenceError, match="^measurement_schema_invalid$"):
        validate_matrix_observation(sample(), CASE.replace("TEAM-A", "TEAM-B"), root=tmp_path, sources=frozen)


@pytest.mark.parametrize("failure", ("timeout", "evolution", "cancelled", "unknown-code"))
def test_matrix_sample_preserves_one_original_call_exception_and_finite_metadata(wrapper, failure):
    class PrivateTimeout(TimeoutError):
        def __str__(self):
            raise AssertionError("exception text must not be read")

    error = {"timeout": PrivateTimeout(CANARY), "evolution": EvolutionError("forbidden"),
             "cancelled": asyncio.CancelledError(CANARY), "unknown-code": EvolutionError("forbidden")}[failure]
    if failure == "unknown-code":
        error.code = CANARY
    calls = []

    def complete(tenant, text, *, observation):
        calls.append((tenant, text))
        observation["elapsed_ms"] = 15001.0
        if failure == "evolution":
            observation.update(http_status=403, wire_code="forbidden")
        raise error

    rows = []
    with pytest.raises(type(error)) as caught:
        wrapper._matrix_sample(SimpleNamespace(sample_complete=complete), "private-tenant", CANARY, rows, "A")
    assert caught.value is error and calls == [("private-tenant", CANARY)]
    assert len(rows) == 1 and rows[0]["elapsed_ms"] == 15001.0
    assert rows[0]["outcome"] == {"timeout": "other_error", "evolution": "evolution_error",
                                 "unknown-code": "evolution_error", "cancelled": "cancelled"}[failure]
    assert rows[0]["sample_error"] == ("forbidden" if failure == "evolution" else None)
    assert CANARY not in json.dumps(rows) and "private-tenant" not in json.dumps(rows)


def test_matrix_sample_success_identity_and_status_mismatch_are_preserved_safely(wrapper):
    result = {"content": CANARY}
    calls = []

    def complete(tenant, text, *, observation):
        calls.append((tenant, text))
        observation.update(http_status=200, sample_completed=True, wire_code="forbidden", elapsed_ms=2.0)
        return result

    rows = []
    actual = wrapper._matrix_sample(SimpleNamespace(sample_complete=complete), "private", CANARY, rows, "A")
    assert actual is result and len(calls) == 1
    assert rows[0]["sample_completed"] is True and rows[0]["outcome"] == "returned"
    assert rows[0]["wire_code"] is None and CANARY not in json.dumps(rows)


def test_matrix_sample_retains_known_correlated_wire_code_separately_from_status(wrapper, tmp_path):
    error = EvolutionError("evaluator_unavailable")

    def complete(tenant, text, *, observation):
        observation.update(http_status=503, wire_code="unsupported_surface", elapsed_ms=3.0)
        raise error

    rows = []
    with pytest.raises(EvolutionError) as caught:
        wrapper._matrix_sample(SimpleNamespace(sample_complete=complete), "private", CANARY, rows, "A")
    assert caught.value is error
    assert rows[0]["http_status"] == 503 and rows[0]["wire_code"] == "unsupported_surface"
    assert rows[0]["sample_error"] == "evaluator_unavailable"
    value = {**sample(), "rows": rows}
    validate_matrix_observation(value, CASE, root=tmp_path, sources=sources(tmp_path))


def test_matrix_freezes_catalogue_before_calls_and_refuses_missing_worker_catalogue(wrapper, tmp_path, monkeypatch):
    directory, frozen = evidence(wrapper, tmp_path, monkeypatch)
    assert wrapper._matrix_sources(CASE) == frozen
    (directory.parents[1] / (CASE + ".sources.json")).unlink()
    with pytest.raises(EvidenceError):
        wrapper._matrix_sources(CASE)
    assert list(directory.iterdir()) == []


def test_matrix_changed_or_missing_sources_refuse_retention_even_for_empty_rows(wrapper, tmp_path, monkeypatch):
    directory, frozen = evidence(wrapper, tmp_path, monkeypatch)
    path = wrapper.ROOT / MATRIX_SOURCES[1]
    path.write_text("def private_modified_source():\n    return '" + CANARY + "'\n")
    with pytest.raises(EvidenceError, match="^measurement_schema_invalid$"):
        wrapper._record_matrix(CASE, [], frozen, ready(wrapper))
    assert list(directory.iterdir()) == []
    path.unlink()
    with pytest.raises(EvidenceError):
        wrapper._record_matrix(CASE, [], frozen, ready(wrapper))
    assert list(directory.iterdir()) == []


def test_matrix_record_is_private_exclusive_and_source_bound(wrapper, tmp_path, monkeypatch):
    directory, frozen = evidence(wrapper, tmp_path, monkeypatch)
    observed = ready(wrapper)
    wrapper._record_matrix(CASE, sample()["rows"], frozen, observed)
    path = directory / FILES[CASE]
    raw = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(raw) == {**sample(), "schema_version": MATRIX_SCHEMA_V2, "readiness": observed.snapshot()}
    with pytest.raises(FileExistsError):
        wrapper._record_matrix(CASE, sample()["rows"], frozen, observed)
    assert path.read_bytes() == raw
    result = collect_measurements(directory.parents[1], [CASE], [], root=wrapper.ROOT, sources=frozen)
    assert result[str(path.relative_to(directory.parents[1]))] == {
        "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw), "source_files": frozen,
    }


@pytest.mark.parametrize("variant", ("duplicate", "extra-field", "oversized", "symlink", "fifo"))
def test_matrix_collector_refuses_untrusted_attachment_bytes(wrapper, tmp_path, monkeypatch, variant):
    directory, frozen = evidence(wrapper, tmp_path, monkeypatch)
    path = directory / FILES[CASE]
    value = sample()
    if variant == "duplicate":
        path.write_text(json.dumps(value)[:-1] + ',"rows":[]}')
    elif variant == "extra-field":
        path.write_text(json.dumps({**value, "body": CANARY}))
    elif variant == "oversized":
        path.write_bytes(b" " * 16385)
    elif variant == "symlink":
        target = tmp_path / "private-target"
        target.write_text(CANARY)
        path.symlink_to(target)
    else:
        os.mkfifo(path, 0o600)
    if variant not in {"symlink", "fifo"}:
        path.chmod(0o600)
    with pytest.raises(EvidenceError):
        collect_measurements(directory.parents[1], [CASE], [CASE], root=wrapper.ROOT, sources=frozen)
    assert not (directory.parents[1] / "manifest.json").exists()


@pytest.mark.parametrize("record_failure", (False, True), ids=("retained", "record-error"))
def test_matrix_original_failure_is_recorded_before_cleanup_without_replacing_it(wrapper, tmp_path, monkeypatch, record_failure):
    directory, frozen = evidence(wrapper, tmp_path, monkeypatch)
    events = []
    original = TimeoutError(CANARY)

    class Port:
        tenants = ("tenant-a", "tenant-b")

        def __init__(self, profile, *, readiness_observation):
            assert profile == "deterministic_mock"
            seeded = ready(wrapper)
            readiness_observation.rows = seeded.rows
            readiness_observation.complete = True

        def release(self, tenant, state):
            return 1

        def scenario(self, name):
            events.append("scenario")

        def sample_complete(self, tenant, text, *, observation):
            observation["elapsed_ms"] = 15000.0
            events.append("attempt")
            raise original

        def close(self):
            events.append("close")

    saved_record = wrapper._record_matrix

    def record(*args):
        events.append("record")
        if record_failure:
            raise ValueError("secondary-private-error")
        return saved_record(*args)

    monkeypatch.setattr(wrapper, "Cluster", Port)
    monkeypatch.setattr(wrapper, "_record_matrix", record)
    with pytest.raises(TimeoutError) as caught:
        wrapper.test_deployed_input_output_state_matrix_exact_dispatch_and_attribution("observe", 0)
    assert caught.value is original
    assert events == ["scenario", "attempt", "record", "scenario", "close"]
    if record_failure:
        assert list(directory.iterdir()) == []
    else:
        value = json.loads((directory / FILES[CASE]).read_text())
        assert value["actual_attempts"] == 1 and value["rows"][0]["http_status"] == 0
        assert CANARY not in json.dumps(value)
        validate_matrix_observation(value, CASE, root=wrapper.ROOT, sources=frozen)


def test_matrix_cleanup_attempts_both_operations_and_preserves_first_failure(wrapper, monkeypatch):
    for original in (None, TimeoutError(CANARY)):
        for faults in (("record",), ("scenario",), ("close",), ("scenario", "close"),
                       ("record", "scenario", "close")):
            errors = {name: RuntimeError(name + CANARY) for name in faults}
            calls = []

            def invoke(name, calls=calls, errors=errors):
                calls.append(name)
                if name in errors:
                    raise errors[name]

            def record(*_args):
                invoke("record")

            def scenario(name):
                assert name == "clean"
                invoke("scenario")

            def close():
                invoke("close")

            monkeypatch.setattr(wrapper, "_record_matrix", record)
            port = SimpleNamespace(scenario=scenario, close=close)
            if original is None:
                with pytest.raises(RuntimeError) as caught:
                    wrapper._matrix_cleanup(port, CASE, [], {}, original, ready(wrapper))
                assert caught.value is errors[faults[0]]
            else:
                with pytest.raises(TimeoutError) as caught:
                    try:
                        raise original
                    finally:
                        wrapper._matrix_cleanup(port, CASE, [], {}, original, ready(wrapper))
                assert caught.value is original
            assert calls == ["record", "scenario", "close"]


def test_matrix_signed_worker_preserves_failed_original_observation_without_passing_case(tmp_path, monkeypatch):
    value = sample()
    root = make_test_root(tmp_path / "root", f'''import json, os
from pathlib import Path
def test_behavior():
    path = Path(os.environ["EVOLUTION_TEST_EVIDENCE_DIR"]) / "cluster-matrix-observation.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({value!r}, stream)
    assert path.is_file()
    raise TimeoutError({CANARY!r})
''')
    frozen = sources(root)
    selected = replace(case(), case_id=CASE)
    fixture = root / selected.nodeid.split("::")[0]
    frozen[str(fixture.relative_to(root))] = run.digest(fixture)
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    from evolution import postgres

    monkeypatch.setattr(run, "load_registry", lambda *_: (selected,))
    monkeypatch.setattr(run, "verify_environment", lambda *_: None)
    monkeypatch.setattr(run, "verify_installed_wheels", lambda *_: None)
    monkeypatch.setattr(run, "phase_artifacts", lambda *_: [])
    monkeypatch.setattr(postgres, "prepare", lambda *_: {})
    monkeypatch.setattr(run, "source_files", lambda *_: frozen)
    monkeypatch.setattr(run.subprocess, "check_output", lambda *_a, **_k: "2" * 40)
    assert run._run_gate(2, root, str(uuid4()), destination) == 1
    report = json.loads((destination / "report.json").read_text())
    run.verify_report(report, (root / ".evolution/secrets/acceptance-ed25519.pub").read_bytes())
    assert report["cases"][0]["status"] == "fail"
    manifest = json.loads((destination / "manifest.json").read_text())
    relative = f"measurements/{CASE}/{FILES[CASE]}"
    assert manifest["measurements"][relative]["sha256"] == run.digest(destination / relative)
    assert manifest["measurements"][relative]["source_files"] == {name: frozen[name] for name in MATRIX_SOURCES}
    assert CANARY not in (destination / relative).read_text()
    assert CANARY not in json.dumps(report) and CANARY not in json.dumps(manifest)
