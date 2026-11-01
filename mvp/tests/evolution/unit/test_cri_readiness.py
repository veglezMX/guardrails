"""Post-import CRI visibility uses one bounded read-only readiness budget."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location("cri_readiness_bootstrap", ROOT / "deploy/evolution/bootstrap.py")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)
REF = "docker.io/fixture/owned@sha256:" + "a" * 64
EXACT = json.dumps({"status": {"repoDigests": [REF]}})
CANARY = "synthetic-private-subprocess-diagnostic"


class Clock:
    def __init__(self):
        self.now = 10.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        assert 0 < duration <= .25
        assert self.now + duration <= 130.0
        self.sleeps.append(duration)
        self.now += duration


@pytest.fixture
def harness(tmp_path, monkeypatch):
    deployment = bootstrap.LocalDeployment(tmp_path, state=tmp_path / "private")
    deployment.state.mkdir(mode=0o700)
    item = {"name": "fixture", "source_url": "docker://" + REF, "sha256": "a" * 64}
    monkeypatch.setattr(deployment, "specs", lambda: {"images": [item]})
    clock = Clock()
    monkeypatch.setattr(bootstrap, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    commands, lookups = [], []
    pending = deque()
    imported = False

    def command(argv, **kwargs):
        nonlocal imported
        argv = [str(value) for value in argv]
        commands.append(argv)
        if "inspecti" in argv:
            assert argv == ["docker", "exec", bootstrap.PROFILE, "crictl", "inspecti", "--output=json", REF]
            assert kwargs["check"] is False
            if not imported:
                return 1, CANARY
            timeout = kwargs["timeout"]
            assert 0 < timeout <= min(5, 130.0 - clock.now)
            lookups.append((clock.now, timeout))
            action = pending.popleft()
            return action(timeout) if callable(action) else action
        if argv[:2] == ["docker", "cp"]:
            assert argv[2:] == [str(deployment.state / "images/fixture.tar"),
                               bootstrap.PROFILE + ":/var/lib/minikube/fixture-pinned.tar"]
            return 0, ""
        if "import" in argv:
            assert not imported, "import_repeated"
            assert "--digests" in argv and "--base-name" in argv
            imported = True
            return 0, ""
        assert argv == ["docker", "exec", bootstrap.PROFILE, "rm", "/var/lib/minikube/fixture-pinned.tar"]
        return 0, ""

    monkeypatch.setattr(bootstrap, "command", command)
    return deployment, clock, commands, lookups, pending


def assert_single_import(commands, *, removed):
    assert sum(command[:2] == ["docker", "cp"] for command in commands) == 1
    assert sum("import" in command for command in commands) == 1
    assert sum(command[3:4] == ["rm"] for command in commands) == int(removed)
    assert not any("pull" in command or "tag" in command for command in commands)


def test_transient_post_import_visibility_requires_exact_pin_without_reimport(harness):
    deployment, clock, commands, lookups, pending = harness

    def temporary_timeout(timeout):
        clock.now += timeout
        raise subprocess.TimeoutExpired(["synthetic"], timeout, output=CANARY)

    pending.extend([(1, CANARY), temporary_timeout, (0, EXACT)])
    deployment.import_images()
    assert len(lookups) == 3
    assert clock.sleeps == [.25, .25]
    assert clock.now == 15.5
    assert not pending
    assert_single_import(commands, removed=True)


@pytest.mark.parametrize("failure", ["nonzero", "subprocess-timeout", "timeout-error"])
def test_persistent_post_import_failure_exhausts_only_original_budget(harness, failure):
    deployment, clock, commands, lookups, pending = harness

    def unavailable(timeout):
        clock.now += timeout
        if failure == "subprocess-timeout":
            raise subprocess.TimeoutExpired(["synthetic"], timeout, output=CANARY)
        if failure == "timeout-error":
            raise TimeoutError(CANARY)
        return 1, CANARY

    pending.extend([unavailable] * 100)
    with pytest.raises(bootstrap.DeploymentError) as caught:
        deployment.import_images()
    assert str(caught.value) == "node_image_lookup_timeout"
    assert CANARY not in str(caught.value)
    assert clock.now == 130.0
    assert len(lookups) == 23
    assert lookups[-1][1] < 5
    assert_single_import(commands, removed=False)


@pytest.mark.parametrize("wire", [
    "", "not-json", "[]", '{"status":false}', '{"status":{}}',
    '{"status":{"repoDigests":"not-a-list"}}', '{"status":{"repoDigests":[null]}}',
    '{"status":{"repoDigests":[],"repoDigests":[]}}', '{"other":NaN,"status":{"repoDigests":[]}}',
    " " * (2 * 1024 * 1024 + 1),
], ids=["empty", "invalid-json", "nonobject", "invalid-status", "missing-digests", "nonlist", "nonstring", "duplicate", "nonfinite", "oversized"])
def test_successful_malformed_metadata_fails_immediately_without_polling(harness, wire):
    deployment, clock, commands, lookups, pending = harness
    pending.extend([(0, wire), (0, EXACT)])
    with pytest.raises(bootstrap.DeploymentError, match="^node_image_metadata_invalid$"):
        deployment.import_images()
    assert len(lookups) == 1 and not clock.sleeps
    assert list(pending) == [(0, EXACT)]
    assert_single_import(commands, removed=False)


@pytest.mark.parametrize("digests", [[], [REF.replace("fixture/owned", "foreign/owned")], [REF[:-1] + "b"]],
                         ids=["missing", "wrong-repository-same-digest", "wrong-digest"])
def test_successful_mismatching_metadata_cannot_poll_into_success(harness, digests):
    deployment, clock, commands, lookups, pending = harness
    pending.extend([(0, json.dumps({"status": {"repoDigests": digests}})), (0, EXACT)])
    with pytest.raises(bootstrap.DeploymentError, match="^node_image_digest_mismatch$"):
        deployment.import_images()
    assert len(lookups) == 1 and not clock.sleeps
    assert list(pending) == [(0, EXACT)]
    assert_single_import(commands, removed=False)


def test_exact_result_returned_after_absolute_cutoff_is_not_success(harness):
    deployment, clock, commands, lookups, pending = harness

    def returned_late(timeout):
        assert timeout == 5
        clock.now = 130.01
        return 0, EXACT

    pending.append(returned_late)
    with pytest.raises(bootstrap.DeploymentError, match="^node_image_lookup_timeout$"):
        deployment.import_images()
    assert len(lookups) == 1 and not clock.sleeps
    assert_single_import(commands, removed=False)


@pytest.mark.parametrize("exception", [KeyboardInterrupt, asyncio.CancelledError])
def test_lookup_cancellation_propagates_without_retry_or_cleanup_mutation(harness, exception):
    deployment, clock, commands, lookups, pending = harness

    def cancelled(timeout):
        raise exception()

    pending.append(cancelled)
    with pytest.raises(exception):
        deployment.import_images()
    assert len(lookups) == 1 and not clock.sleeps
    assert_single_import(commands, removed=False)


def test_non_timeout_spawn_failure_is_finite_and_not_retried(harness):
    deployment, clock, commands, lookups, pending = harness

    def unavailable_binary(timeout):
        raise FileNotFoundError(CANARY)

    pending.append(unavailable_binary)
    with pytest.raises(bootstrap.DeploymentError) as caught:
        deployment.import_images()
    assert str(caught.value) == "node_image_lookup_failed"
    assert CANARY not in str(caught.value)
    assert len(lookups) == 1 and not clock.sleeps
    assert_single_import(commands, removed=False)


def test_readiness_recovery_preserves_exact_same_node_cold_attempt(harness):
    deployment, clock, commands, lookups, pending = harness
    prepared = deployment.state / "prepared.json"
    prepared.write_text('{"synthetic":"prepared"}\n')
    boundary = {"container_id": "c" * 64, "image": "fixed-node"}
    prior = boundary | {"prepared_digest": bootstrap.sha256(prepared), "cold_start": True,
                        "created_at": "2026-10-10T00:00:00Z", "recovered_before_application_setup": False}
    attempt_path = deployment.state / "offline-attempt.json"
    bootstrap.atomic_json(attempt_path, prior)
    before = attempt_path.read_bytes(), attempt_path.stat().st_ino
    pending.extend([(1, CANARY), (0, EXACT)])
    assert deployment.record_boot_attempt(boundary, cold_start=False) == prior
    deployment.import_images()
    assert deployment.record_boot_attempt(boundary, cold_start=False) == prior
    assert (attempt_path.read_bytes(), attempt_path.stat().st_ino) == before
    assert prepared.read_text() == '{"synthetic":"prepared"}\n'
    assert len(lookups) == 2 and clock.sleeps == [.25]
    assert_single_import(commands, removed=True)
