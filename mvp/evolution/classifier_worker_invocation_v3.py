"""Closed parent-to-worker pins; no discovery, factory import, or live operation.

The two environment strings identify bytes independently pinned by the parent.
All comparison inputs originate in the runner's exact case/registry/source
snapshot. A valid manifest supplies no qualification or deployment proof itself.
"""
from __future__ import annotations

import copy
from pathlib import Path

from . import classifier_provenance_v3 as proof
from .classifier_provenance_io_v3 import StableReader, private_large_allowances
from .diagnostics import validate_catalog

SCHEMA = "evolution-classifier-worker-invocation-v1"
MANIFEST_ENV = "EVOLUTION_CLASSIFIER_INVOCATION_MANIFEST"
SHA_ENV = "EVOLUTION_CLASSIFIER_INVOCATION_MANIFEST_SHA256"
PLUGIN_MODULE = "mvp.tests.evolution.e2e.classifier_provenance_fixtures_v3"
FIELDS = {"schema_version", "authorization", "corpus_manifest", "case_id", "nodeid", "registry_sha256",
          "sources", "source_catalog", "deadline_monotonic", "public_files", "evaluator_config", "deployment_state", "native_executables"}


def environment_reference(environment):
    proof.require(type(environment) is dict and MANIFEST_ENV in environment and SHA_ENV in environment)
    value = {"path": environment[MANIFEST_ENV], "sha256": environment[SHA_ENV]}
    proof.reference(value)
    return value


def validate(value, *, case_id, nodeid, registry_sha256, sources, source_catalog_ref, source_catalog, root, deadline):
    proof.exact(value, FIELDS | ({"private_large_files"} if "private_large_files" in value else set()))
    proof.require(value["schema_version"] == SCHEMA and case_id in proof.CASE_FAMILIES
        and value["case_id"] == case_id and value["nodeid"] == nodeid
        and type(nodeid) is str and 1 <= len(nodeid) <= 2048)
    proof.sha(registry_sha256); proof.source_map(sources)
    proof.require(value["registry_sha256"] == registry_sha256 and value["sources"] == sources)
    proof.reference(source_catalog_ref)
    proof.require(value["source_catalog"] == source_catalog_ref)
    for key in ("authorization", "corpus_manifest", "evaluator_config"):
        proof.reference(value[key])
    proof.path(value["deployment_state"])
    native = proof.exact(value["native_executables"], {"docker", "kubectl"})
    for ref in native.values():
        proof.reference(ref)
    proof.require(native["docker"]["path"] == "/usr/bin/docker"
        and native["kubectl"]["path"] == value["deployment_state"] + "/bin/kubectl")
    proof.number(value["deadline_monotonic"], .001, 1e15)
    proof.number(deadline, .001, 1e15)
    proof.require(value["deadline_monotonic"] == deadline)
    proof.require(type(value["public_files"]) is dict and len(value["public_files"]) <= 8192)
    for name, hashed in value["public_files"].items():
        proof.path(name); proof.sha(hashed)
    private_large_allowances(value.get("private_large_files", ()), value["public_files"])
    validate_catalog(source_catalog, Path(root))
    proof.require(all(source_catalog.get(name) == hashed for name, hashed in sources.items() if name.endswith(".py")))
    return copy.deepcopy(value)


def load(reference, *, reader, expected):
    """Initial private read cannot obtain its own public-file whitelist."""
    proof.require(isinstance(reader, StableReader) and reader.public_files == {}
                  and reader.private_large_files == ())
    proof.reference(reference)
    arguments = copy.deepcopy(expected)
    arguments["root"] = str(arguments["root"])
    original = proof.canonical(arguments)
    value = proof.document(reader(reference))
    catalog_raw = reader(arguments["source_catalog_ref"])
    proof.require(proof.document(catalog_raw) == arguments["source_catalog"])
    result = validate(value, **arguments)
    proof.require(proof.canonical({**expected, "root": str(expected["root"])}) == original)
    reader.budget.check()
    return result
