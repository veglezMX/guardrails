"""Private temporary I/O and genuine pure verifier over synthetic receipts only."""
from __future__ import annotations

import copy
import hashlib
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from evolution import classifier_provenance as p
from evolution import classifier_provenance_io as io
from evolution.registry import EvidenceError

from mvp.tests.evolution.unit.test_classifier_provenance import (
    NOW,
    SHA,
    SOURCES,
    case,
    synthetic_approval,
    synthetic_corpus,
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import socket
    import subprocess
    def forbidden(*args, **kwargs):
        raise AssertionError("live operation forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(p, "BASELINE_CORPORA", {key: hashlib.sha256(p.canonical(synthetic_corpus(key)) + b"\n").hexdigest()
        for key in ("original", "heldout_1", "heldout_2")})
    monkeypatch.setattr(p, "SELECTION_APPROVAL_SHA256", hashlib.sha256(p.canonical(synthetic_approval()) + b"\n").hexdigest())


def put(path, raw=b'{"synthetic":true}\n', mode=0o600):
    path.write_bytes(raw); path.chmod(mode)
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


@pytest.fixture
def filesystem(tmp_path):
    tmp_path.chmod(0o700)
    clock = [0.]
    budget = io.Budget(20000, monotonic=lambda: clock[0])
    reader = io.StableReader(budget, public_files={})
    return SimpleNamespace(root=tmp_path, budget=budget, reader=reader, clock=clock)


def test_private_reader_returns_exact_bytes_and_never_public_by_default(filesystem):
    f = filesystem; ref = put(f.root / "proof.json")
    assert f.reader(ref) == Path(ref["path"]).read_bytes()
    Path(ref["path"]).chmod(0o644)
    with pytest.raises(EvidenceError):
        f.reader(ref)
    public = io.StableReader(f.budget, public_files={ref["path"]: ref["sha256"]})
    assert public(ref) == b'{"synthetic":true}\n'
    with pytest.raises(EvidenceError):
        public(ref | {"sha256": SHA})


@pytest.mark.parametrize('mode', [0o400, 0o444])
def test_frozen_readonly_public_evidence_requires_explicit_path_and_hash(filesystem, mode):
    f = filesystem; ref = put(f.root / 'frozen.json', mode=mode)
    with pytest.raises(EvidenceError): f.reader(ref)
    exact = io.StableReader(f.budget, public_files={ref['path']: ref['sha256']})
    assert exact(ref) == b'{"synthetic":true}\n'
    assert Path(ref['path']).stat().st_mode & 0o777 == mode
    with pytest.raises(EvidenceError): exact(ref | {'sha256': SHA})
    wrong = io.StableReader(f.budget, public_files={str(f.root / 'elsewhere.json'): ref['sha256']})
    with pytest.raises(EvidenceError): wrong(ref)


@pytest.mark.parametrize('mode', [0o440, 0o640, 0o664, 0o666, 0o744])
def test_public_pin_does_not_authorize_unknown_or_writable_permissions(filesystem, mode):
    f = filesystem; ref = put(f.root / 'unsafe.json', mode=mode)
    reader = io.StableReader(f.budget, public_files={ref['path']: ref['sha256']})
    with pytest.raises(EvidenceError): reader(ref)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory", "empty", "oversize", "permissions", "parent_mode"])
def test_reader_rejects_unsafe_private_file_identity_without_blocking(filesystem, kind):
    f = filesystem; path = f.root / "proof.json"; ref = put(path)
    if kind == "symlink":
        path.rename(f.root / "target"); path.symlink_to(f.root / "target")
    elif kind == "hardlink":
        os.link(path, f.root / "alias")
    elif kind == "fifo":
        path.unlink(); os.mkfifo(path, 0o600)
    elif kind == "directory":
        path.unlink(); path.mkdir(mode=0o700)
    elif kind == "empty":
        path.write_bytes(b"")
    elif kind == "oversize":
        with path.open("r+b") as stream:
            stream.truncate(p.MAX_BYTES + 1)
    elif kind == "permissions":
        path.chmod(0o640)
    else:
        f.root.chmod(0o755)
    with pytest.raises((EvidenceError, OSError)):
        f.reader(ref)


@pytest.mark.parametrize("mutation", ["contents", "replace_file", "replace_parent", "parent_permissions", "expired"])
def test_reader_rechecks_identity_and_original_deadline_after_actual_read(filesystem, monkeypatch, mutation):
    f = filesystem; ref = put(f.root / "proof.json"); original = os.read; once = [False]
    def read(fd, size):
        raw = original(fd, size)
        if not once[0]:
            once[0] = True
            if mutation == "contents":
                Path(ref["path"]).write_bytes(b"changed")
            elif mutation == "replace_file":
                Path(ref["path"]).unlink(); put(Path(ref["path"]))
            elif mutation == "replace_parent":
                f.root.rename(f.root.with_name(f.root.name + "-old")); f.root.mkdir(mode=0o700); put(Path(ref["path"]))
            elif mutation == "parent_permissions":
                f.root.chmod(0o755)
            else:
                f.clock[0] = f.budget.deadline
        return raw
    monkeypatch.setattr(os, "read", read)
    with pytest.raises(EvidenceError):
        f.reader(ref)


def test_budget_cap_never_refreshes_original_allowance_and_restores_scope(filesystem):
    f = filesystem
    with f.budget.cap(10):
        assert f.budget.deadline == 10
        with f.budget.cap(30):
            assert f.budget.deadline == 10
    assert f.budget.deadline == 20000
    f.clock[0] = 10
    with pytest.raises(EvidenceError), f.budget.cap(10):
        pytest.fail("expired cap must refuse entry")
    assert f.budget.deadline == 20000


def test_writer_is_exclusive_durable_and_reopenable(filesystem):
    f = filesystem; writer = io.PrivateDirectory(f.root / "journal", f.budget, create=True)
    try:
        ref = writer.document("one.json", {"synthetic": True})
        assert f.reader(ref) == b'{"synthetic":true}\n'
        with pytest.raises(FileExistsError):
            writer.write("one.json", b"changed")
        with pytest.raises(FileExistsError):
            io.PrivateDirectory(writer.path, f.budget, create=True)
        with pytest.raises(EvidenceError):
            writer.write("subdir/one.json", b"{}")
    finally:
        writer.close()
    writer.close()


def test_writer_refuses_replaced_root_and_preserves_original_bytes(filesystem):
    f = filesystem; writer = io.PrivateDirectory(f.root / "journal", f.budget, create=True)
    try:
        first = writer.document("before.json", {"saved": True})
        old = writer.path.with_name("retained"); writer.path.rename(old); writer.path.mkdir(mode=0o700)
        with pytest.raises(EvidenceError):
            writer.document("after.json", {"saved": False})
        assert (old / "before.json").read_bytes() == p.canonical({"saved": True}) + b"\n"
        assert list(writer.path.iterdir()) == [] and first["path"].endswith("before.json")
    finally:
        writer.close()


@pytest.fixture
def flow(filesystem):
    f = filesystem; c = case()
    journal = io.PrivateDirectory(f.root / "invocation", f.budget, create=True)
    checks = io.PrivateDirectory(journal.path / "checks", f.budget, create=True)
    measurement = io.PrivateDirectory(f.root / c.case_id, f.budget, create=True)
    c.authorization["output_directory"] = str(measurement.path)
    c.authorization["current_checks_directory"] = str(checks.path)
    argv = ["/synthetic/python", "-B", "-m", "synthetic.worker", c.authorization["case"]["nodeid"]]
    c.authorization["worker"]["command_sha256"] = p.digest(argv)
    c.auth_ref = c.store.put(str(journal.path / "authorization.json"), c.authorization)
    assert journal.write("authorization.json", c.store.raws[c.auth_ref["path"]]) == c.auth_ref
    class SyntheticReader(io.StableReader):
        def __call__(self, ref):
            self.budget.check()
            return super().__call__(ref) if Path(ref["path"]).is_relative_to(f.root) else c.store.load(ref)
    load = SyntheticReader(f.budget, public_files={})
    time = [NOW]
    original_current = c.options["current"]
    def current(**kwargs):
        return original_current(**kwargs) | {"observed_at": time[0].isoformat()}
    c.options.update(authorization_ref=c.auth_ref, load=load, current=current, now=lambda: time[0])
    verifier = p.ProvenanceVerifier(**c.options)
    invocation = io.InvocationJournal(verifier=verifier, directory=journal, now=lambda: time[0])
    args = {"case_id": c.case_id, "nodeid": c.authorization["case"]["nodeid"], "argv": argv,
        "executable_sha256": SHA, "launcher_sha256": SHA, "registry_sha256": SHA, "sources": SOURCES}
    bundle = SimpleNamespace(**locals())
    try:
        yield bundle
    finally:
        measurement.close(); checks.close(); journal.close()


def launch(f):
    context = f.invocation.authorize_launch(**f.args)
    assert context["authorization"] == f.c.authorization
    f.time[0] += timedelta(seconds=1)
    f.invocation.started(pid=123, process_start=456, started_at=f.time[0].isoformat())


def test_parent_actual_process_result_and_consume_close_original_worker(flow):
    f = flow; launch(f)
    runtime_verifier = p.ProvenanceVerifier(**f.c.options)
    recorder = io.RecordedVerifier(runtime_verifier, f.checks); recorder.authorize()
    for phase in p.CHECK_PHASES["tls"]:
        result = recorder(phase=phase, operation=None, binding=copy.deepcopy(f.c.binding), sources=dict(SOURCES), deadline=12345)
        assert result["parent_invocation"] == f.c.auth_ref["sha256"]
    files = {name: f.measurement.document(name, f.c.observation if name.endswith("observation.json") else {"synthetic": name})
             for name in p.FAMILY_FILES["tls"]}
    f.time[0] += timedelta(seconds=1)
    result = {"nodeid": f.c.authorization["case"]["nodeid"], "status": "pass", "duration_ms": 900, "assertions": 2, "reason_codes": []}
    ref = f.invocation.finish(exit_code=0, elapsed_ms=1000, result=result, observation_files=files, governance_files={}, current_checks=recorder.refs)
    completion = p.document(f.f.reader(ref))
    assert completion["pid"] == 123 and completion["process_start"] == 456
    assert completion["authorization"] == f.c.auth_ref and f.invocation.state == "complete"
    with pytest.raises(EvidenceError):
        f.invocation.failed(reason_code="worker_exit")
    consumer = p.ProvenanceVerifier(**(f.c.options | {"completion_ref": ref}))
    proof = consumer(phase="consume", operation=None, binding=f.c.binding, sources=SOURCES, observation=f.c.observation)
    assert proof["parent_invocation"] == f.c.auth_ref["sha256"]



@pytest.mark.parametrize("field,value", [("case_id", "foreign"), ("nodeid", "foreign"), ("argv", ["foreign"]),
    ("executable_sha256", "b" * 64), ("launcher_sha256", "b" * 64), ("registry_sha256", "b" * 64), ("sources", {"foreign": SHA})])
def test_parent_launch_binding_mismatch_never_records_spawn_intent(flow, field, value):
    f = flow
    with pytest.raises(EvidenceError):
        f.invocation.authorize_launch(**(f.args | {field: value}))
    assert not (f.journal.path / "launch-intent.json").exists() and f.invocation.state == "new"


@pytest.mark.parametrize("kind", ["exit", "failed_result", "missing_assertions", "extra_diagnostic", "no_original_spawn"])
def test_failed_original_worker_never_gets_completion(flow, kind):
    f = flow
    if kind != "no_original_spawn":
        launch(f)
    files = {name: f.measurement.document(name, {"synthetic": name}) for name in p.FAMILY_FILES["tls"]}
    result = {"nodeid": f.c.authorization["case"]["nodeid"], "status": "pass", "duration_ms": 900, "assertions": 2, "reason_codes": []}
    if kind == "failed_result":
        result["status"] = "fail"
    elif kind == "missing_assertions":
        result["assertions"] = 1
    elif kind == "extra_diagnostic":
        result["reason_codes"] = ["case_timeout"]
    f.time[0] += timedelta(seconds=2)
    with pytest.raises(EvidenceError):
        f.invocation.finish(exit_code=1 if kind == "exit" else 0, elapsed_ms=1000, result=result,
            observation_files=files, governance_files={}, current_checks=[])
    ref = f.invocation.failed(reason_code="invalid_worker_result")
    assert p.document(f.f.reader(ref))["status"] == "failed_no_completion_claim"
    assert not (f.journal.path / "completion.json").exists()


def test_failed_current_record_write_returns_no_proof_and_cannot_retry(flow, monkeypatch):
    f = flow; recorder = io.RecordedVerifier(p.ProvenanceVerifier(**f.c.options), f.checks); recorder.authorize()
    def no(*args):
        raise OSError("synthetic private details")
    monkeypatch.setattr(f.checks, "document", no)
    with pytest.raises(OSError):
        recorder(phase="before", operation=None, binding=f.c.binding, sources=SOURCES, deadline=12345)
    assert recorder.refs == [] and len(recorder.verifier.validated_checks) == 1
    with pytest.raises(EvidenceError):
        recorder(phase="before", operation=None, binding=f.c.binding, sources=SOURCES, deadline=12345)


def test_runtime_phase_cannot_precede_static_authorization_or_skip_order(flow):
    f = flow; recorder = io.RecordedVerifier(p.ProvenanceVerifier(**f.c.options), f.checks)
    with pytest.raises(EvidenceError):
        recorder(phase="before", deadline=12345)
    recorder.authorize()
    with pytest.raises(EvidenceError):
        recorder(phase="after", deadline=12345)
    assert list(f.checks.path.iterdir()) == []


@pytest.mark.parametrize("kind", ["unbounded", "different_budget"])
def test_runtime_recorder_and_parent_require_same_bounded_reader(flow, kind):
    f = flow
    loader = f.c.store.load if kind == "unbounded" else io.StableReader(io.Budget(12345), public_files={})
    verifier = p.ProvenanceVerifier(**(f.c.options | {"load": loader}))
    with pytest.raises(EvidenceError):
        io.RecordedVerifier(verifier, f.checks)
    with pytest.raises(EvidenceError):
        io.InvocationJournal(verifier=verifier, directory=f.journal)


def test_failed_case_retention_uses_only_original_outer_allowance(flow):
    f = flow
    launch(f)
    with pytest.raises(EvidenceError), f.f.budget.cap(10):
        f.f.clock[0] = 10
    ref = f.invocation.failed(reason_code="case_timeout")
    assert p.document(f.f.reader(ref))["reason_code"] == "case_timeout"
    f.f.clock[0] = f.f.budget.deadline
    with pytest.raises(EvidenceError):
        f.journal.document("too-late.json", {"attempt": True})
    assert not (f.journal.path / "too-late.json").exists()
    assert (f.journal.path / "failed.json").exists()


def test_expired_runtime_cap_prevents_reader_and_current_callback(flow):
    f = flow; recorder = io.RecordedVerifier(p.ProvenanceVerifier(**f.c.options), f.checks)
    recorder.authorize(); f.f.clock[0] = 11
    with pytest.raises(EvidenceError):
        recorder(phase="before", operation=None, binding=f.c.binding, sources=SOURCES, deadline=10)
    assert recorder.refs == [] and recorder.verifier.validated_checks == []
    assert list(f.checks.path.iterdir()) == []
