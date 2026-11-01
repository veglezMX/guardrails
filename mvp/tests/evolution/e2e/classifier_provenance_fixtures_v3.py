"""Explicit worker plugin and fixture lifecycle, with no auto-loaded defaults.

Only the fixed worker launcher constructs this object using a validated parent
manifest, the real bounded recorder, and the reviewed read-only constructor.
The final-selected-worker verifier remains mandatory: missing production proof
must refuse before any cluster construction. No environment factory is imported.
"""
from __future__ import annotations

import copy

import pytest
from evolution import classifier_provenance_v3 as proof
from evolution.classifier_provenance_io_v3 import RecordedVerifier


class ClassifierFixtureContext:
    def __init__(self, *, manifest, recorder, read_only_constructor):
        proof.require(isinstance(recorder, RecordedVerifier) and callable(read_only_constructor))
        self.manifest = copy.deepcopy(manifest)
        self.recorder = recorder
        self.constructor = read_only_constructor
        self.cluster = None
        self.started = False
        self.authorized = False

    def authorize(self):
        proof.require(not self.authorized and not self.started)
        context = self.recorder.authorize()
        auth = context["authorization"]
        proof.require(self.recorder.verifier.authorization_ref == self.manifest["authorization"]
            and auth["corpus_manifest"] == self.manifest["corpus_manifest"]
            and auth["case"]["case_id"] == self.manifest["case_id"]
            and auth["case"]["nodeid"] == self.manifest["nodeid"]
            and auth["sources"] == self.manifest["sources"]
            and auth["registry_sha256"] == self.manifest["registry_sha256"]
            and self.recorder.directory.budget.deadline == self.manifest["deadline_monotonic"])
        self.authorized = True
        return copy.deepcopy(context)

    def cluster_factory(self, *, deadline):
        proof.require(self.authorized and not self.started)
        self.started = True  # A failed constructor cannot be retried.
        cluster = None
        try:
            with self.recorder.directory.budget.cap(deadline):
                context = self.recorder.verifier.authorize()
                before = proof.canonical(context)
                arguments = copy.deepcopy(context)
                cluster = self.constructor(context=arguments, manifest=copy.deepcopy(self.manifest),
                    deadline=self.recorder.directory.budget.deadline)
                proof.require(proof.canonical(arguments) == before and cluster is not None)
                self.recorder.directory.budget.check()
            self.cluster = cluster
            return cluster
        except BaseException:
            try:
                if cluster is not None:
                    cluster.close()
            except BaseException:  # noqa: BLE001, S110 - retain the original finite constructor refusal
                pass
            raise

    def verify(self, **kwargs):
        proof.require(self.authorized and self.cluster is not None)
        return self.recorder(**kwargs)


class ClassifierFixturePlugin:
    """Explicit pytest object. Only the exact authorized node gets the fixture."""
    def __init__(self, context):
        proof.require(isinstance(context, ClassifierFixtureContext))
        self.context = context
        self.claimed = False

    @pytest.fixture
    def classifier_provenance_context(self, request):
        proof.require(not self.claimed and request.node.nodeid == self.context.manifest["nodeid"])
        self.claimed = True
        self.context.authorize()
        return self.context
