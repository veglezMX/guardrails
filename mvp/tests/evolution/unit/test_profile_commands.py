"""Explicit command forwarding and bounded whole-process execution; no model/AWS."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from evolution import profiles, run
from evolution.registry import EvidenceError


def benchmark(**changes):
    return SimpleNamespace(**({"command": "benchmark", "classifier_python": Path("/prepared/python"),
        "model_directory": Path("/prepared/model"), "output": Path("/private/result"),
        "source_digest": "a" * 64} | changes))


def managed(**changes):
    return SimpleNamespace(**({"command": "bedrock-live", "allow_external": False,
        "config": Path("/private/config"), "credentials": Path("/private/credentials"),
        "ca_file": Path("/private/ca")} | changes))


@pytest.mark.parametrize("args", [managed(), benchmark(source_digest="unknown"),
    benchmark(model_directory=Path("relative")), managed(allow_external=True, credentials=Path("relative"))])
def test_profile_rejection_does_not_start_any_process(args, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(profiles.subprocess, "Popen", lambda *a, **kw: calls.append(a))
    with pytest.raises(EvidenceError):
        profiles.run_profile(args, tmp_path)
    assert calls == []


@pytest.mark.parametrize("args,timeout,module", [(benchmark(), 3600, "services.classifier.benchmark"),
    (managed(allow_external=True), 60, "guardrails.evolution.bedrock")])
def test_profile_nonzero_result_is_retained_and_ambient_python_injection_removed(args, timeout, module, monkeypatch, tmp_path):
    captured = []
    def launch(command, **options):
        captured.append((command, options))
        return SimpleNamespace(wait=lambda *, timeout: 17)
    monkeypatch.setattr(profiles.subprocess, "Popen", launch)
    for key in ("PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT", "PYTHONOPTIMIZE"):
        monkeypatch.setenv(key, "synthetic-untrusted-setting")
    assert profiles.run_profile(args, tmp_path) == 17
    command, options = captured[0]
    assert len(captured) == 1 and module in command
    assert options["start_new_session"] and options["cwd"] == tmp_path
    assert not any(key in options["env"] for key in ("PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT", "PYTHONOPTIMIZE"))
    assert options["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    if args.command == "benchmark":
        assert options["env"]["HF_HUB_OFFLINE"] == options["env"]["TRANSFORMERS_OFFLINE"] == "1"
        assert "--run-frozen-heldout" in command
        assert command[command.index("--corpus") + 1] == str(tmp_path / "mvp/tests/evolution/fixtures/injection-heldout-v1.json")
    else:
        assert "--allow-external" in command


def test_profile_timeout_kills_process_group_and_waits_before_finite_failure(monkeypatch, tmp_path):
    events = []
    def wait(timeout=None):
        events.append(("wait", timeout))
        if timeout is not None:
            raise subprocess.TimeoutExpired("synthetic-private-argv", timeout)
        return -9
    monkeypatch.setattr(profiles.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(pid=12345, wait=wait))
    monkeypatch.setattr(profiles.os, "killpg", lambda pid, sig: events.append(("kill", pid)))
    with pytest.raises(EvidenceError, match="^profile_timeout$"):
        profiles.run_profile(benchmark(), tmp_path)
    assert events == [("wait", 3600), ("kill", 12345), ("wait", None)]


@pytest.mark.parametrize("arguments", [
    ["classifier-service"],
    ["prepare", "--classifier-context", "/private/context", "--classifier-seal-sha256", "b" * 64],
    ["platform", "--profile", "classifier", "--evaluator-config-file", "/private/endpoints"],
])
def test_public_command_forwards_operator_classifier_arguments(arguments, monkeypatch):
    captured = []
    def call(argv, **kwargs):
        captured.append(argv)
        return SimpleNamespace(returncode=9)
    monkeypatch.setattr(run.subprocess, "run", call)
    assert run.main(arguments) == 9
    assert len(captured) == 1 and captured[0][2:] == arguments


@pytest.mark.parametrize("argument,value", [("--classifier-context", "/private/context"),
    ("--classifier-seal-sha256", "b" * 64)])
def test_partial_classifier_preparation_arguments_do_not_start_bootstrap(argument, value, monkeypatch):
    captured = []
    monkeypatch.setattr(run.subprocess, "run", lambda *a, **kw: captured.append(a))
    assert run.main(["prepare", argument, value]) == 1
    assert captured == []
