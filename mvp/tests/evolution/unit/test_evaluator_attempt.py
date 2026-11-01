"""Durable metadata journal with actual filesystem and fake management boundary."""
import copy
import json
import os
from uuid import UUID

import pytest
from evolution import evaluator_attempt as subject
from evolution.evaluator_governance import GovernanceJourney, policy_reference
from guardrails.evolution import models
from guardrails.evolution.errors import EvolutionError
from test_evaluator_governance import STAMP, ReleasePort, uid
from test_evaluator_governance import base as base_fixture

base = base_fixture


@pytest.fixture
def arguments(tmp_path, base):
    root = tmp_path / "operations"
    root.mkdir(mode=0o700)
    source = tmp_path / "source.py"
    source.write_bytes(b"# synthetic source identity\n")
    source.chmod(0o600)
    binding = models.Binding(tenant_id="team-a", workload_id="chat", revision=7,
        policy=policy_reference(base), state="observe", release_id=uid(400), activated_by=uid(13),
        previous_revision=6, effective_after=STAMP, created_at=STAMP,
        reason="sensitive free-form reason must not be copied", status="applied").model_dump(mode="json")
    return {"parent": root, "tenant": "team-a", "original_binding": binding,
        "original_policy": base, "actors": {name: uid(index) for index, name in enumerate(
            ("tenant_admin", "author", "approver", "operator", "auditor"), 10)},
        "engine": "pii", "stage": "input", "state": "observe",
        "sources": {"mvp/synthetic.py": source}}


def make(arguments, **changes):
    return subject.GovernanceAttempt(**{**arguments, **changes})


def attached(attempt, port):
    return GovernanceJourney(tenant=attempt.tenant, operation=attempt.operation,
        actors=attempt.actors, call=port, journal=attempt, clock=lambda: 0)


def request(journey):
    return journey._request("POST", "/lists", role="author", body={"private": "raw-policy-canary"},
                            label="example-list", expected=201)


def test_explicit_operation_root_required_no_fallback_or_creation(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOLUTION_EVALUATOR_OPERATION_ROOT", raising=False)
    with pytest.raises(EvolutionError):
        subject.operation_root()
    target = tmp_path / "not-created"
    monkeypatch.setenv("EVOLUTION_EVALUATOR_OPERATION_ROOT", str(target))
    with pytest.raises(FileNotFoundError):
        subject.operation_root()
    assert not target.exists()
    target.mkdir(mode=0o700)
    assert subject.operation_root() == target


def test_intent_actor_source_original_identity_durable_before_request(arguments):
    attempt = make(arguments)
    try:
        def port(method, path, **options):
            intent = json.loads((attempt.path / "intent.json").read_bytes())
            pending = json.loads((attempt.path / "request-01-intent.json").read_bytes())
            assert intent["operation"] == attempt.operation == str(UUID(attempt.path.name))
            assert intent["actors"] == arguments["actors"]
            assert intent["original_policy"] == policy_reference(arguments["original_policy"])
            assert intent["original_binding"] == {key: value for key, value in arguments["original_binding"].items() if key != "reason"}
            assert intent["source_sha256"] == attempt.source_sha256
            assert pending["actor_id"] == arguments["actors"]["author"]
            assert pending["status"] == "request_intended_outcome_unknown"
            assert pending["idempotency_key"] == options["key"]
            assert not (attempt.path / "request-01-returned.json").exists()
            return {"private": "raw-response-canary"}
        result = request(attached(attempt, port))
        assert result == {"private": "raw-response-canary"}
        attempt.finish(passed=True, restoration="not_needed")
        raw = b"".join(path.read_bytes() for path in attempt.path.iterdir())
        assert b"raw-policy-canary" not in raw and b"raw-response-canary" not in raw
        assert b"sensitive free-form reason" not in raw
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in attempt.path.iterdir())
        assert json.loads((attempt.path / "completion.json").read_bytes())["last_request_outcome_unknown"] is False
    finally:
        attempt.close()


def test_release_handle_on_disk_before_first_poll_even_when_poll_fails(arguments, base):
    attempt = make(arguments)
    port = ReleasePort(base)
    def fail_poll(method, path, **options):
        if method == "GET" and path.startswith("/releases/"):
            saved = json.loads((attempt.path / "release-matrix-release.json").read_bytes())
            assert saved["handle"]["release_id"] == uid(500)
            assert saved["handle"]["binding_revision"] == 8
            assert saved["status"] == "acknowledged_not_yet_applied"
            raise EvolutionError("snapshot_stale")
        return port(method, path, **options)
    try:
        journey = attached(attempt, fail_poll)
        with pytest.raises(EvolutionError):
            journey.release(policy=base, workload_id="chat", state="observe", expected_revision=7,
                runtime_ids=(uid(51), uid(52)), label="matrix-release")
        assert journey.last_release["release_id"] == uid(500)
        assert sum(row[0] == "POST" for row in port.calls) == 1
        assert json.loads((attempt.path / "release-matrix-release.json").read_bytes())["handle"] == journey.last_release
    finally:
        attempt.close()


def test_unknown_mutation_keeps_intent_and_refuses_another_post(arguments):
    attempt, calls = make(arguments), []
    def broken(*args, **kwargs):
        calls.append(args)
        raise TimeoutError("private transport detail")
    try:
        journey = attached(attempt, broken)
        with pytest.raises(TimeoutError):
            request(journey)
        with pytest.raises(EvolutionError):
            request(journey)
        assert len(calls) == 1 and journey.last_release is None
        assert (attempt.path / "request-01-intent.json").is_file()
        assert not (attempt.path / "request-01-returned.json").exists()
        attempt.finish(passed=False, restoration="not_needed")
        completed = json.loads((attempt.path / "completion.json").read_bytes())
        assert completed["status"] == "case_failed" and completed["last_request_outcome_unknown"]
    finally:
        attempt.close()


@pytest.mark.parametrize("fault", ("source-change", "root-replaced", "parent-replaced", "unsafe-mode", "intent-exists"))
def test_refusal_before_network_preserves_prior_evidence(arguments, fault):
    attempt = make(arguments)
    calls = []
    old_intent = (attempt.path / "intent.json").read_bytes()
    retained = attempt.path
    try:
        if fault == "source-change":
            next(iter(arguments["sources"].values())).write_bytes(b"# drift\n")
        elif fault == "root-replaced":
            retained = attempt.path.with_name("retained-original")
            attempt.path.rename(retained)
            attempt.path.mkdir(mode=0o700)
        elif fault == "parent-replaced":
            old_parent = attempt.parent.with_name("retained-parent")
            attempt.parent.rename(old_parent)
            retained = old_parent / attempt.operation
            attempt.parent.mkdir(mode=0o700)
            attempt.path.mkdir(mode=0o700)
        elif fault == "unsafe-mode":
            attempt.path.chmod(0o755)
        else:
            (attempt.path / "request-01-intent.json").write_bytes(b"retained evidence\n")
        with pytest.raises((EvolutionError, FileExistsError)):
            request(attached(attempt, lambda *args, **kwargs: calls.append(args)))
        assert not calls and (retained / "intent.json").read_bytes() == old_intent
        if fault in {"root-replaced", "parent-replaced"}:
            assert list(attempt.path.iterdir()) == []
        if fault == "intent-exists":
            assert (attempt.path / "request-01-intent.json").read_bytes() == b"retained evidence\n"
    finally:
        attempt.close()


def test_fsync_failure_prevents_first_mutation(arguments, monkeypatch):
    attempt, calls = make(arguments), []
    try:
        def fail(*args):
            raise OSError("simulated filesystem failure")
        monkeypatch.setattr(subject.os, "fsync", fail)
        with pytest.raises(OSError):
            request(attached(attempt, lambda *args, **kwargs: calls.append(args)))
        assert calls == []
    finally:
        attempt.close()


@pytest.mark.parametrize("fault", ("public-parent", "symlink-parent", "foreign-policy", "same-actor", "source-symlink", "source-fifo"))
def test_unsafe_or_incoherent_setup_creates_no_operation(arguments, tmp_path, fault):
    kwargs = copy.copy(arguments)
    if fault == "public-parent":
        arguments["parent"].chmod(0o755)
    elif fault == "symlink-parent":
        alias = tmp_path / "alias"
        alias.symlink_to(arguments["parent"], target_is_directory=True)
        kwargs["parent"] = alias
    elif fault == "foreign-policy":
        kwargs["original_policy"] = {**arguments["original_policy"], "version": 2}
    elif fault == "same-actor":
        kwargs["actors"] = dict.fromkeys(arguments["actors"], uid(10))
    elif fault == "source-symlink":
        source = next(iter(arguments["sources"].values()))
        alias = tmp_path / "alias.py"
        alias.symlink_to(source)
        kwargs["sources"] = {"mvp/synthetic.py": alias}
    else:
        source = tmp_path / "fifo.py"
        os.mkfifo(source, 0o600)
        kwargs["sources"] = {"mvp/synthetic.py": source}
    with pytest.raises((EvolutionError, ValueError)):
        make(kwargs)
    assert list(arguments["parent"].iterdir()) == []


def test_new_attempt_never_reuses_existing_uuid_directory(arguments, monkeypatch):
    first = make(arguments)
    raw = (first.path / "intent.json").read_bytes()
    try:
        monkeypatch.setattr(subject, "uuid4", lambda: UUID(first.operation))
        with pytest.raises(FileExistsError):
            make(arguments)
        assert (first.path / "intent.json").read_bytes() == raw
    finally:
        first.close()


def test_substitution_between_child_stat_and_open_cannot_adopt_foreign_directory(arguments, monkeypatch):
    original_open = os.open
    selected = uid(765)
    monkeypatch.setattr(subject, "uuid4", lambda: UUID(selected))
    def replaced(path, flags, *args, **kwargs):
        if path == selected and flags & os.O_DIRECTORY:
            current = arguments["parent"] / selected
            current.rename(arguments["parent"] / "retained-created")
            current.mkdir(mode=0o700)
            (current / "foreign.txt").write_bytes(b"must remain unchanged")
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(subject.os, "open", replaced)
    with pytest.raises(EvolutionError):
        make(arguments)
    assert list((arguments["parent"] / "retained-created").iterdir()) == []
    foreign = arguments["parent"] / selected
    assert [path.name for path in foreign.iterdir()] == ["foreign.txt"]
    assert (foreign / "foreign.txt").read_bytes() == b"must remain unchanged"


@pytest.mark.parametrize("fault", ("actors", "tenant", "operation"))
def test_journey_must_share_journal_authorization_identity(arguments, fault):
    attempt = make(arguments)
    try:
        options = {"tenant": attempt.tenant, "operation": attempt.operation, "actors": attempt.actors,
                   "journal": attempt, "call": lambda *args, **kwargs: pytest.fail("no request allowed")}
        if fault == "actors":
            options["actors"] = {**attempt.actors, "operator": uid(900)}
        elif fault == "tenant":
            options["tenant"] = "team-b"
        else:
            options["operation"] = uid(999)
        with pytest.raises(EvolutionError):
            GovernanceJourney(**options)
    finally:
        attempt.close()


def test_arbitrary_request_text_not_retained(arguments):
    attempt = make(arguments)
    try:
        with pytest.raises(EvolutionError):
            attached(attempt, lambda *args, **kwargs: pytest.fail("no request"))._request(
                "POST", "/arbitrary-private-text", role="author", body={}, label="private", expected=201)
        assert {path.name for path in attempt.path.iterdir()} == {"intent.json"}
    finally:
        attempt.close()
