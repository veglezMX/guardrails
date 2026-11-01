"""Pure callback boundaries: no deployment, model, process or socket activity."""
from __future__ import annotations

import socket
import subprocess
from types import SimpleNamespace

import pytest
from evolution.registry import EvidenceError

from mvp.tests.evolution.e2e import (
    classifier_cni_acceptance_support as cni,
)
from mvp.tests.evolution.e2e import (
    classifier_resource_sampling_support as resource,
)

FAMILIES = (resource, cni)
PHASES = ((resource, "before"), (resource, "after"), (resource, "restored"),
          (cni, "before"), (cni, "after"))


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Callback test forbids external operations")

    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def proof(family):
    names = {"parent_invocation", "qualification", "prepared", "cold", "credentials"}
    if family is cni:
        names.add("node_boundary")
    return dict.fromkeys(names, "a" * 64)


def invoke(family, verifier, *, phase="before", deadline=103.5, binding=None, sources=None):
    kwargs = {"phase": phase, "observer": SimpleNamespace(bound=binding or {"owned": [1]}),
              "sources": sources or {"source.py": "a" * 64}, "operation": "synthetic",
              "deadline": deadline}
    if family is cni:
        kwargs["group"] = {"retained": [1]}
    return family.verify(verifier, **kwargs)


@pytest.mark.parametrize("family,phase", PHASES,
                         ids=("resource-before", "resource-after", "resource-restored", "cni-before", "cni-after"))
def test_original_absolute_deadline_reaches_each_required_verifier(monkeypatch, family, phase):
    monkeypatch.setattr(family.time, "monotonic", lambda: 100.0)
    binding, sources, calls = {"owned": [1]}, {"source.py": "a" * 64}, []

    def verifier(**kwargs):
        calls.append(kwargs["deadline"])
        assert kwargs["deadline"] == 103.5 and kwargs["phase"] == phase
        kwargs["binding"]["owned"].append(2)
        kwargs["sources"]["source.py"] = "b" * 64
        if family is cni:
            kwargs["group"]["retained"].append(2)
        return proof(family)

    assert invoke(family, verifier, phase=phase, binding=binding, sources=sources) == proof(family)
    assert calls == [103.5] and binding == {"owned": [1]} and sources == {"source.py": "a" * 64}


@pytest.mark.parametrize("family", FAMILIES, ids=("resource", "cni"))
@pytest.mark.parametrize("outcome", ("already_expired", "late_return", "primary_failure"))
def test_callback_cannot_refresh_deadline_or_hide_its_original_failure(monkeypatch, family, outcome):
    clock, calls = [104.0 if outcome == "already_expired" else 100.0], []
    monkeypatch.setattr(family.time, "monotonic", lambda: clock[0])
    primary = RuntimeError("synthetic original callback failure")

    def verifier(**kwargs):
        calls.append(kwargs["deadline"])
        assert kwargs["deadline"] == 103.5
        clock[0] = 104.0
        if outcome == "primary_failure":
            raise primary
        return proof(family)

    with pytest.raises(RuntimeError if outcome == "primary_failure" else EvidenceError) as caught:
        invoke(family, verifier)
    assert calls == ([] if outcome == "already_expired" else [103.5])
    if outcome == "primary_failure":
        assert caught.value is primary
