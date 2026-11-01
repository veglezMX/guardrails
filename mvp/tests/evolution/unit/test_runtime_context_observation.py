"""Isolated evidence contracts: original calls, failed prefixes and privacy."""

import hashlib
import importlib.util
import json
import os
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from evolution import runtime_context_evidence as evidence
from evolution.diagnostics import write_catalog
from evolution.registry import EvidenceError
from guardrails.evolution.errors import EvolutionError

ROOT = Path(__file__).resolve().parents[4]
INTEGRATION = ROOT / "mvp/tests/evolution/integration"
CASE = next(name for name, variant in evidence.CASES.items() if variant == "expired")
CANARY = "private-context-token-body-must-not-be-retained"


@pytest.fixture
def observer(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(INTEGRATION))
    spec = importlib.util.spec_from_file_location(
        "runtime_context_observation_port", INTEGRATION / "runtime_context_observation.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "source"
    frozen = {}
    for name in evidence.SOURCES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def public_source():\n    return None\n")
        frozen[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    directory = tmp_path / "report" / "measurements" / CASE
    directory.mkdir(mode=0o700, parents=True)
    write_catalog(directory.parents[1] / (CASE + ".sources.json"), root, frozen)
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(directory))
    return SimpleNamespace(module=module, root=root, frozen=frozen, directory=directory,
                           path=directory / evidence.FILENAME)


def reply(status=503, code="context_invalid", *, body=None):
    correlation = str(uuid4())
    request = httpx.Request("POST", "https://localhost/v2/evaluations")
    return httpx.Response(status, request=request, headers={"X-Request-ID": correlation},
                          json=EvolutionError(code).envelope(correlation) if body is None else body)


def sample():
    return {"schema_version": evidence.SCHEMA, "rows": [
        {"stage": "input", "http_status": 200, "wire_code": None, "elapsed_ms": 3.5},
        {"stage": "output", "http_status": 503, "wire_code": "context_invalid", "elapsed_ms": 67.0},
    ]}


def validate(observer, value, *, complete=False):
    evidence.validate(value, CASE, root=observer.root, sources=observer.frozen, complete=complete)


def test_original_call_arguments_responses_cleanup_and_private_sidecar(observer, monkeypatch):
    times = iter((1.0, 1.003, 2.0, 2.067))
    monkeypatch.setattr(observer.module, "monotonic", lambda: next(times))
    payloads = [{"text": CANARY, "deadline_ms": 500}, {"context_id": CANARY}]
    headers = {"Authorization": "Bearer " + CANARY}
    replies, calls, events = [reply(200), reply()], [], []
    def post(path, **options):
        assert not observer.path.exists()
        calls.append((path, options))
        return replies[len(calls) - 1]
    @contextmanager
    def original_scope():
        try:
            yield
        finally:
            assert not observer.path.exists()
            events.append("original_cleanup")
    try:
        with observer.module.RuntimeContextObservation("expired") as observed, original_scope():
            client = SimpleNamespace(post=post)
            assert observed.post(client, "input", json=payloads[0]) is replies[0]
            assert observed.post(client, "output", json=payloads[1], headers=headers) is replies[1]
    finally:
        events.append("outer_cleanup")
    assert events == ["original_cleanup", "outer_cleanup"]
    assert calls == [("/v2/evaluations", {"json": payloads[0]}),
                     ("/v2/evaluations", {"json": payloads[1], "headers": headers})]
    assert calls[0][1]["json"] is payloads[0] and calls[1][1]["headers"] is headers
    value = json.loads(observer.path.read_bytes())
    assert [row["elapsed_ms"] for row in value["rows"]] == [3.0, 67.0]
    validate(observer, value, complete=True)
    assert os.stat(observer.path).st_mode & 0o777 == 0o600
    assert len(observer.path.read_bytes()) <= evidence.MAX_BYTES
    assert CANARY not in observer.path.read_text()
    assert all(item.headers["x-request-id"] not in observer.path.read_text() for item in replies)


@pytest.mark.parametrize("stage", ["input", "output"])
def test_transport_error_propagates_unchanged_once_and_preserves_cleanup(observer, stage):
    class PrivateFailure(RuntimeError):
        def __str__(self):
            raise AssertionError("exception string must not be read")
    original, calls, cleanup = PrivateFailure(CANARY), [], []
    def post(path, **options):
        calls.append(path)
        if len(calls) == 1 and stage == "output":
            return reply(200)
        raise original
    try:
        with pytest.raises(PrivateFailure) as caught, observer.module.RuntimeContextObservation("expired") as observed:
            client = SimpleNamespace(post=post)
            observed.post(client, "input", json={})
            observed.post(client, "output", json={})
    finally:
        cleanup.append(True)
    assert caught.value is original and cleanup == [True]
    assert len(calls) == (1 if stage == "input" else 2)
    value = json.loads(observer.path.read_bytes())
    assert value["rows"][-1]["stage"] == stage
    assert value["rows"][-1]["http_status"] == 0 and value["rows"][-1]["wire_code"] is None
    assert CANARY not in observer.path.read_text()
    validate(observer, value)
    with pytest.raises(EvidenceError):
        validate(observer, value, complete=True)


@pytest.mark.parametrize("stage,status,code", [
    ("input", 504, "evaluation_timeout"), ("output", 503, "snapshot_stale"),
    ("output", 200, "context_invalid"),
])
def test_original_failed_status_prefix_is_retained_without_becoming_success(observer, stage, status, code):
    replies = [reply(status, code)] if stage == "input" else [reply(200), reply(status, code)]
    calls = []
    def post(*args, **options):
        calls.append(args)
        return replies[len(calls) - 1]
    original = AssertionError(CANARY)
    with pytest.raises(AssertionError) as caught, observer.module.RuntimeContextObservation("expired") as observed:
        client = SimpleNamespace(post=post)
        observed.post(client, "input", json={})
        if stage == "output":
            observed.post(client, "output", json={})
        raise original
    assert caught.value is original and len(calls) == len(replies)
    value = json.loads(observer.path.read_bytes())
    assert value["rows"][-1]["http_status"] == status
    assert value["rows"][-1]["wire_code"] == (None if status == 200 else code)
    validate(observer, value)
    with pytest.raises(EvidenceError):
        validate(observer, value, complete=True)


@pytest.mark.parametrize("body", [
    {"error": {"code": CANARY}}, {"private": CANARY}, "x" * 4097,
], ids=("unknown-code", "unknown-shape", "oversized"))
def test_untrusted_denial_body_never_enters_observation(observer, body):
    response = reply(body=body)
    with pytest.raises(AssertionError), observer.module.RuntimeContextObservation("expired") as observed:
        assert observed.post(SimpleNamespace(post=lambda *args, **options: response), "input", json={}) is response
        raise AssertionError(CANARY)
    value = json.loads(observer.path.read_bytes())
    assert value["rows"][0]["http_status"] == 503 and value["rows"][0]["wire_code"] is None
    assert CANARY not in observer.path.read_text() and len(observer.path.read_bytes()) < 512


def test_malformed_buffered_body_preserves_response_without_read_or_retry(observer):
    response = reply()
    response._content = b'{"error":NaN,"private":"' + CANARY.encode() + b'"}'
    with pytest.raises(AssertionError), observer.module.RuntimeContextObservation("expired") as observed:
        assert observed.post(SimpleNamespace(post=lambda *args, **options: response), "input", json={}) is response
        raise AssertionError
    assert json.loads(observer.path.read_bytes())["rows"][0]["wire_code"] is None
    assert CANARY not in observer.path.read_text()


def test_primary_failure_survives_writer_failure_and_success_does_not(observer, monkeypatch):
    original, secondary, cleanup = AssertionError(CANARY), OSError(CANARY), []
    def failed_write(self, *, complete):
        raise secondary
    monkeypatch.setattr(observer.module.RuntimeContextObservation, "write", failed_write)
    try:
        with pytest.raises(AssertionError) as caught, observer.module.RuntimeContextObservation("expired"):
            raise original
    finally:
        cleanup.append(True)
    assert caught.value is original and cleanup == [True]
    with pytest.raises(OSError) as caught, observer.module.RuntimeContextObservation("expired"):
        pass
    assert caught.value is secondary and not observer.path.exists()


def test_pre_request_failure_records_empty_prefix_only(observer):
    with pytest.raises(RuntimeError), observer.module.RuntimeContextObservation("expired"):
        raise RuntimeError(CANARY)
    value = json.loads(observer.path.read_bytes())
    assert value == {"schema_version": evidence.SCHEMA, "rows": []}
    validate(observer, value)
    with pytest.raises(EvidenceError):
        validate(observer, value, complete=True)


@pytest.mark.parametrize("change", ["missing", "hash", "symlink"])
def test_frozen_source_is_required_before_observation_and_revalidated_before_write(observer, change):
    instance = observer.module.RuntimeContextObservation("expired")
    path = observer.root / evidence.SOURCES[0]
    if change == "missing":
        path.unlink()
    elif change == "hash":
        path.write_text("def changed_source():\n    return None\n")
    else:
        path.unlink()
        path.symlink_to(observer.root / evidence.SOURCES[1])
    with pytest.raises(EvidenceError):
        observer.module.RuntimeContextObservation("expired")
    with pytest.raises(EvidenceError):
        instance.write(complete=False)
    assert not observer.path.exists()


@pytest.mark.parametrize("change", ["public", "hardlink", "symlink"])
def test_private_source_catalog_binding_is_mandatory(observer, change):
    path = observer.directory.parents[1] / (CASE + ".sources.json")
    if change == "public":
        path.chmod(0o644)
    elif change == "hardlink":
        os.link(path, path.with_suffix(".link"))
    else:
        target = path.with_suffix(".original")
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises(EvidenceError):
        observer.module.RuntimeContextObservation("expired")
    assert not observer.path.exists()


def test_private_output_is_exclusive_and_never_replaces_prior_evidence(observer):
    with observer.module.RuntimeContextObservation("expired") as observed:
        observed.value = sample()
    before = observer.path.read_bytes()
    with pytest.raises(FileExistsError), observer.module.RuntimeContextObservation("expired") as observed:
        observed.value = sample()
    assert observer.path.read_bytes() == before


def test_symlinked_evidence_directory_ancestor_is_rejected(observer, monkeypatch):
    link = observer.directory.parents[1].with_name("redirected-report")
    link.symlink_to(observer.directory.parents[1], target_is_directory=True)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(link / "measurements" / CASE))
    with pytest.raises(EvidenceError, match="measurement_path_invalid"):
        observer.module.RuntimeContextObservation("expired")
    assert not observer.path.exists()


@pytest.mark.parametrize("scope", ["case", "ancestor"])
@pytest.mark.parametrize("timing", ["before_open", "after_open"])
def test_directory_replacement_cannot_redirect_private_evidence(observer, monkeypatch, scope, timing):
    instance = observer.module.RuntimeContextObservation("expired")
    instance.value = sample()
    original_open = os.open
    replaced = []
    def replace_directory():
        target = observer.directory if scope == "case" else observer.directory.parents[1]
        retained = target.with_name("retained-" + target.name)
        target.rename(retained)
        observer.directory.mkdir(mode=0o700, parents=True)
        outside = observer.directory / "outside-canary.txt"
        outside.write_text(CANARY)
        replaced.append(outside)
    def opening(path, flags, *args, **options):
        if path == observer.directory and not replaced:
            if timing == "before_open":
                replace_directory()
                return original_open(path, flags, *args, **options)
            descriptor = original_open(path, flags, *args, **options)
            replace_directory()
            return descriptor
        return original_open(path, flags, *args, **options)
    monkeypatch.setattr(observer.module.os, "open", opening)
    with pytest.raises(EvidenceError, match="measurement_path_invalid"):
        instance.write(complete=True)
    assert len(replaced) == 1 and replaced[0].read_text() == CANARY
    assert not observer.path.exists()


@pytest.mark.parametrize("fault", ["clock_start", "clock_end", "projection"])
def test_recording_faults_preserve_original_response_and_test_failure(observer, monkeypatch, fault):
    original, calls, ticks = AssertionError(CANARY), [], []
    secondary = RuntimeError(CANARY)
    response = reply(200)
    def clock():
        ticks.append(1)
        if len(ticks) == (1 if fault == "clock_start" else 2) and fault != "projection":
            raise secondary
        return 1.0
    def projection(value):
        raise secondary
    def post(*args, **options):
        calls.append(1)
        return response
    monkeypatch.setattr(observer.module, "monotonic", clock)
    if fault == "projection":
        monkeypatch.setattr(observer.module, "correlated_error_code", projection)
    with pytest.raises(AssertionError) as caught, observer.module.RuntimeContextObservation("expired") as observed:
        assert observed.post(SimpleNamespace(post=post), "input", json={}) is response
        raise original
    assert caught.value is original and calls == [1] and not observer.path.exists()
    with pytest.raises(EvidenceError, match="measurement_evidence_invalid"):
        observed.write(complete=False)


@pytest.mark.parametrize("fault_at", [1, 2])
def test_clock_fault_never_replaces_original_transport_exception(observer, monkeypatch, fault_at):
    original, calls, ticks = httpx.ReadTimeout(CANARY), [], []
    def clock():
        ticks.append(1)
        if len(ticks) == fault_at:
            raise RuntimeError(CANARY)
        return 1.0
    def post(*args, **options):
        calls.append(1)
        raise original
    monkeypatch.setattr(observer.module, "monotonic", clock)
    with pytest.raises(httpx.ReadTimeout) as caught, observer.module.RuntimeContextObservation("expired") as observed:
        observed.post(SimpleNamespace(post=post), "input", json={})
    assert caught.value is original and calls == [1] and not observer.path.exists()


def test_success_response_body_is_never_inspected(observer):
    class BufferedSuccess:
        status_code = 200
        @property
        def content(self):
            raise AssertionError("success content must not be read")
        @property
        def headers(self):
            raise AssertionError("success headers must not be read")
    response = BufferedSuccess()
    with pytest.raises(AssertionError), observer.module.RuntimeContextObservation("expired") as observed:
        assert observed.post(SimpleNamespace(post=lambda *a, **kw: response), "input", json={}) is response
        assert observed.recording_failed is False
        raise AssertionError
    assert json.loads(observer.path.read_bytes())["rows"][0]["http_status"] == 200


@pytest.mark.parametrize("variant", ["expired", "wrong_principal", "wrong_workload"])
def test_three_registered_variants_share_exact_bounded_schema(observer, variant, monkeypatch):
    monkeypatch.delenv("EVOLUTION_TEST_EVIDENCE_DIR")
    with observer.module.RuntimeContextObservation(variant) as observed:
        observed.value = sample()
        assert evidence.CASES[observed.case_id] == variant
    assert len(evidence.CASES) == 3 and not observer.path.exists()


@pytest.mark.parametrize("mutation", [
    lambda v: v.update(private=CANARY),
    lambda v: v["rows"].append(deepcopy(v["rows"][1])),
    lambda v: v["rows"][0].update(stage="output"),
    lambda v: v["rows"][0].update(http_status=503),
    lambda v: v["rows"][1].update(http_status=True),
    lambda v: v["rows"][1].update(http_status=600),
    lambda v: v["rows"][1].update(wire_code=CANARY),
    lambda v: v["rows"][1].update(wire_code="evaluation_timeout"),
    lambda v: v["rows"][1].update(elapsed_ms=float("nan")),
    lambda v: v["rows"][1].update(elapsed_ms=True),
    lambda v: v["rows"][1].update(elapsed_ms=-1),
    lambda v: v["rows"][1].update(elapsed_ms=120001),
    lambda v: v["rows"][1].update(request_id=CANARY),
])
def test_closed_schema_rejects_forged_private_or_impossible_rows(observer, mutation):
    value = sample()
    mutation(value)
    with pytest.raises(EvidenceError):
        validate(observer, value)


def test_sequence_refusal_prevents_extra_or_out_of_order_calls(observer):
    calls = []
    def post(*args, **options):
        calls.append(1)
        return reply(200) if len(calls) == 1 else reply()
    client = SimpleNamespace(post=post)
    with observer.module.RuntimeContextObservation("expired") as observed:
        with pytest.raises(EvidenceError):
            observed.post(client, "output", json={})
        observed.post(client, "input", json={})
        observed.post(client, "output", json={})
        with pytest.raises(EvidenceError):
            observed.post(client, "output", json={})
    assert calls == [1, 1]
