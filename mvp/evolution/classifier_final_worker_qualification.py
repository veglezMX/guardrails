"""Fixed, fail-closed selected-worker adapter for the parent provenance port.

No producer version is admitted by default. Registering a version requires the
reviewed actual quality, lifecycle and image producer source hashes. The parent
then supplies an independently pinned capsule after their original completions.
Nothing in this module loads a model, executes source, opens a file or creates a
client. The supplied stable reader retains its caller's original deadline.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType

from . import classifier_final_worker_evidence as e
from . import classifier_loaded_evidence as loaded
from . import classifier_provenance as p

CAPSULE_SCHEMA = "evolution-classifier-selected-worker-capsule-v1"
REQUIRED_RUNTIME_SOURCES = {"mvp/guardrails/evolution/classifier_contract.py", "services/classifier/worker.py",
                            "services/classifier/app.py", "services/classifier/network_isolation.py"}


@dataclass(frozen=True)
class ProducerVersion:
    selection_index: int
    quality_launcher: str
    quality_producer: str
    loaded_launcher: str
    loaded_producer: str
    image_producer: str
    initial_launcher: str
    initial_consumer: str
    initial_support: str
    environment: str
    model_artifacts: str
    runtime_paths: tuple[str, ...]
    installed_base: str
    installed_site: str
    installed_python: str


# Intentionally empty: no future launcher hash, wildcard, environment-selected
# factory or initial-qualification producer can authorize a selected-worker pass.
REVIEWED_VERSIONS = MappingProxyType({})


class Reader:
    def __init__(self, load):
        p.require(callable(load)); self.load = load; self.seen = {}; self.size = 0; self.large_private = set()

    def raw(self, ref):
        p.reference(ref)
        stable = copy.deepcopy(ref)
        argument = copy.deepcopy(stable)
        value = self.load(argument)
        key = (stable["path"], stable["sha256"])
        limit = 16 * 1024 * 1024 if key in self.large_private else p.MAX_BYTES
        p.require(type(value) is bytes and 0 < len(value) <= limit
                  and hashlib.sha256(value).hexdigest() == stable["sha256"] and ref == stable and argument == stable)
        p.require(not any(path == stable["path"] and hashed != stable["sha256"] for path, hashed in self.seen))
        if key in self.seen:
            p.require(value == self.seen[key])
        else:
            self.size += len(value)
            p.require(len(self.seen) < 8192 and self.size <= 128 * 1024 * 1024)
            self.seen[key] = value
        return value

    def document(self, ref):
        return p.document(self.raw(ref))

    def finish(self):
        for (path, pin), raw in tuple(self.seen.items()):
            p.require(self.raw({"path": path, "sha256": pin}) == raw)


def read_sources(reader, root, sources):
    p.path(root); p.source_map(sources)
    for name, pin in sources.items():
        reader.raw({"path": str(PurePosixPath(root) / name), "sha256": pin})


def execution(reader, group, *, role, authority):
    p.exact(group, {"plan", "launcher", "producer", "started", "result", "cleanup", "reports"})
    expected_launcher = authority.quality_launcher if role == "quality" else authority.loaded_launcher
    expected_producer = authority.quality_producer if role == "quality" else authority.loaded_producer
    p.require(group["launcher"]["sha256"] == expected_launcher and group["producer"]["sha256"] == expected_producer)
    reader.raw(group["launcher"]); reader.raw(group["producer"])
    plan = reader.document(group["plan"])
    start, result, cleanup = (reader.document(group[name]) for name in ("started", "result", "cleanup"))
    p.exact(group["reports"], e.REPORT_FILES)
    output = p.path(plan["output_directory"])
    for name, ref in group["reports"].items():
        p.require(ref["path"] == str(PurePosixPath(output) / name))
    invocation_root = PurePosixPath(group["started"]["path"]).parent
    for key, name in (("started", "started.json"), ("result", "result.json"), ("cleanup", "cleanup.json")):
        p.require(group[key]["path"] == str(invocation_root / name))
    p.require(result["cleanup_sha256"] == group["cleanup"]["sha256"])
    e.same(plan["launcher"], group["launcher"]); e.same(plan["producer"], group["producer"])
    e.same(plan["argv"], start["argv"])
    e.invocation(start, result, cleanup, role=role, plan_ref=group["plan"], report_refs=group["reports"],
                 launcher_ref=group["launcher"], producer_ref=group["producer"], maximum_seconds=3600 if role == "quality" else 900)
    p.require(start["environment_sha256"] == plan["environment"]["sha256"])
    p.require(plan["environment"]["sha256"] == authority.environment)
    reader.raw(plan["environment"])
    reports = {name: reader.raw(ref) for name, ref in group["reports"].items()}
    return plan, reports, start, result


def historical(reader, value, *, authority, identity, corpora, corpus_manifest):
    """Preserve exact historical source bytes; never evaluate them as current."""
    index = p.integer(authority.selection_index, 1, 6)
    p.require(len(e.corpus_order(corpora)) == index + 3)
    if index <= 3:
        from services.classifier import bounded_qualification_core as core
        from services.classifier import bounded_qualification_evidence as initial
        family = "bounded-initial"
    else:
        from services.classifier import bounded_qualification_core_extended as core
        from services.classifier import (
            bounded_qualification_evidence_extended as initial,
        )
        family = "bounded-extended-initial"

    p.exact(value, {"capsule", "snapshot", "consumer"})
    p.require(value["consumer"]["sha256"] == authority.initial_consumer)
    reader.raw(value["consumer"])
    capsule = reader.document(value["capsule"])
    p.exact(capsule, {"schema_version", "scope", "plan", "launcher", "support", "invocation", "quality"})
    p.require(capsule["schema_version"] == family + "-parent-completion-v1"
              and capsule["scope"] == initial.SCOPE and capsule["launcher"]["sha256"] == authority.initial_launcher
              and capsule["support"]["sha256"] == authority.initial_support)
    reader.raw(capsule["launcher"]); reader.raw(capsule["support"])
    plan = reader.document(capsule["plan"])
    p.require(plan["schema_version"] == family + "-qualification-plan-v1"
              and type(plan["selection_index"]) is int and plan["selection_index"] == index)
    e.same(plan["corpus_manifest"], corpus_manifest)
    snapshot = reader.document(value["snapshot"])
    p.exact(snapshot, {"status", "source_root", "snapshot_root", "created_at", "files"})
    p.require(snapshot["status"] == "initial_source_snapshot_before_scoring")
    p.path(snapshot["source_root"]); p.path(snapshot["snapshot_root"])
    p.require(snapshot["source_root"] != snapshot["snapshot_root"])
    p.require(set(plan["sources"]) < set(snapshot["files"])
              and len(snapshot["files"]) == len(plan["sources"]) + 1
              and all(snapshot["files"][name] == pin for name, pin in plan["sources"].items())
              and authority.initial_consumer in snapshot["files"].values())
    read_sources(reader, snapshot["snapshot_root"], snapshot["files"])
    p.exact(capsule["invocation"], {"started.json", "result.json", "cleanup.json"})
    p.exact(capsule["quality"], e.REPORT_FILES)
    invocation = {key: reader.document(ref) for key, ref in capsule["invocation"].items()}
    inv_start, inv_end = invocation["started.json"], invocation["result.json"]
    fields = {"schema_version", "status", "started_at", "plan_sha256", "launcher_sha256", "support_sha256", "output",
              "maximum_seconds", "automatic_retry", "warmup_requests", "sources"}
    p.exact(inv_start, fields)
    p.exact(inv_end, fields | {"completed_at", "exit_code", "elapsed_seconds", "cleanup_sha256", "quality_report_pins",
                              "enabled_model_changed", "final_selected_runtime_rerun_required"})
    e.same({key: inv_start[key] for key in fields - {"status"}}, {key: inv_end[key] for key in fields - {"status"}})
    p.require(inv_start["status"] == "running" and inv_end["status"] == "completed"
              and inv_end["schema_version"] == family + "-invocation-v1"
              and inv_end["plan_sha256"] == capsule["plan"]["sha256"]
              and inv_end["launcher_sha256"] == authority.initial_launcher
              and inv_end["support_sha256"] == capsule["support"]["sha256"]
              and inv_end["output"] == plan["output"] and inv_end["sources"] == plan["sources"]
              and inv_end["automatic_retry"] is False and inv_end["enabled_model_changed"] is False
              and inv_end["final_selected_runtime_rerun_required"] is True)
    p.integer(inv_end["exit_code"], 0, 0); p.integer(inv_end["warmup_requests"], 0, 0)
    p.integer(inv_end["maximum_seconds"], 3600, 3600); p.number(inv_end["elapsed_seconds"], .000001, 3600)
    p.require(p.timestamp(snapshot["created_at"]) <= p.timestamp(inv_start["started_at"])
              <= p.timestamp(inv_end["completed_at"]) and p.timestamp(plan["created_at"]) <= p.timestamp(inv_start["started_at"]))
    p.require(inv_end["cleanup_sha256"] == capsule["invocation"]["cleanup.json"]["sha256"])
    cleanup = invocation["cleanup.json"]
    p.exact(cleanup, {"schema_version", "group_signal_requested", "group_already_absent", "leader_reaped", "leader_exit_observed", "errors"})
    p.require(cleanup["schema_version"] == "wolf-full-initial-cleanup-v1" and cleanup["leader_reaped"] is True
              and cleanup["leader_exit_observed"] is True and cleanup["errors"] == []
              and type(cleanup["group_signal_requested"]) is bool and type(cleanup["group_already_absent"]) is bool
              and cleanup["group_signal_requested"] is not cleanup["group_already_absent"])
    raw = {key: reader.raw(ref) for key, ref in capsule["quality"].items()}
    e.same(inv_end["quality_report_pins"], {key: e.digest(value) for key, value in raw.items()})
    start = p.document(raw["started.json"])
    old_identity = start["identity"]
    p.require(old_identity["repository"] == identity["model_id"] and old_identity["revision"] == identity["model_revision"]
              and old_identity["weights_sha256"] == identity["weights_sha256"] and old_identity["bundle_sha256"] == identity["bundle_sha256"])
    e.same(start["sources"], plan["sources"])
    spec_args = (index, start["selection_manifest_sha256"], corpus_manifest["sha256"],
                 core.canonical(old_identity), core.canonical(plan["sources"]),
                 tuple(core.CorpusPin(name, corpora[name]["sha256"]) for name in e.corpus_order(corpora)))
    if index > 3:
        spec_args += (core.canonical({"original": core.ORIGINAL_APPROVAL_SHA256,
                                     "extension": core.EXTENSION_APPROVAL_SHA256}),)
    spec = core.Specification(*spec_args)
    for ref in plan["prior_evidence"].values():
        reader.raw(ref)
    load_result = reader.document(plan["load_only"]["result"])
    p.exact(plan["load_only"], {"helper", "started", "closure", "child", "cleanup", "result"})
    closure_ref = p.reference(plan["load_only"]["closure"])
    # The outer StableReader separately requires its independently authenticated
    # exact private-large-file allowance. This local limit cannot grant that IO authority.
    reader.large_private.add((closure_ref["path"], closure_ref["sha256"]))
    for ref in plan["load_only"].values():
        reader.raw(ref)
    p.require(load_result["installed_closure_sha256"] == closure_ref["sha256"])
    e.installed_closure(reader.raw(closure_ref), expected_files=load_result["installed_files"],
        base=authority.installed_base, site=authority.installed_site, executable=authority.installed_python)
    expected = initial.Expected(spec, capsule["plan"]["sha256"], plan["load_only"]["result"]["sha256"],
                                core.canonical(plan["prior_evidence"]), core.canonical(load_result["metadata"]))
    initial.validate_report({name: reader.raw(ref) for name, ref in corpora.items()}, raw["started.json"], raw["result.json"],
                            raw["cases.jsonl"], expected, verify_corpus=p.corpus_projection, require_quality=True)
    p.require(p.timestamp(inv_start["started_at"]) <= p.timestamp(start["created_at"])
              <= p.timestamp(p.document(raw["result.json"])["completed_at"]) <= p.timestamp(inv_end["completed_at"]))
    return inv_end["completed_at"]


def lifecycle(reader, group, *, authority, identity, sources, historical_completed):
    plan, raw, start, result = execution(reader, group, role="loaded", authority=authority)
    p.exact(plan, {"schema_version", "scope", "created_at", "identity", "sources", "scoring_sources", "producer",
                   "launcher", "environment", "output_directory", "host_namespace", "entrypoints", "argv"})
    p.require(plan["schema_version"] == "selected-worker-loaded-plan-v1" and plan["scope"] == e.SCOPE)
    e.same(plan["identity"], identity); e.same(plan["entrypoints"], e.ENTRYPOINTS)
    e.same(plan["scoring_sources"], sources)
    for name, pin in plan["sources"].items():
        p.require(name in sources and sources[name] == pin)
    p.require(p.timestamp(historical_completed) <= p.timestamp(plan["created_at"]) <= p.timestamp(start["created_at"]))
    loaded.verify(raw, pins={name: ref["sha256"] for name, ref in group["reports"].items()},
                  expected_sources=plan["sources"], expected_identity=identity, host_namespace=plan["host_namespace"])
    return result["completed_at"]


def image_closure(reader, ref, *, authority, identity, sources, images, bundle):
    """Bind actual original build/OCI/prepared/native evidence, never manifests alone."""
    value = reader.document(ref)
    p.exact(value, {"schema_version", "producer", "identity", "sources", "build", "context_manifest", "context_seal",
                    "prepared", "preparation_lock", "artifacts_lock", "oci_manifest", "oci_config", "native", "started", "result"})
    p.require(value["schema_version"] == "selected-worker-image-binding-v1"
              and value["producer"]["sha256"] == authority.image_producer)
    reader.raw(value["producer"]); e.same(value["identity"], identity); e.same(value["sources"], sources)
    build, context, seal, prepared, prep_lock, artifacts, manifest, config, native = (
        reader.document(value[key]) for key in ("build", "context_manifest", "context_seal", "prepared", "preparation_lock",
                                               "artifacts_lock", "oci_manifest", "oci_config", "native"))
    p.exact(build, {"schema", "artifact", "source_digest", "inputs", "source_inventory", "context_directory", "seal_sha256",
                    "manifest_sha256", "context_sha256", "context_size_bytes", "base_image", "image_id", "image_manifest_digest"})
    p.exact(context, {"schema", "scope", "plan_sha256", "base_image", "inputs", "source_sha256", "source_digest", "context_sha256",
                      "context_size_bytes", "docker_operations", "model_loads"})
    p.exact(seal, {"schema", "scope", "complete", "manifest_sha256", "context_sha256", "context_size_bytes", "plan_sha256"})
    p.require(build["schema"] == "classifier-image-build-v1" and context["schema"] == "classifier-image-context-v1"
              and seal["schema"] == "classifier-image-context-seal-v1" and seal["complete"] is True
              and seal["scope"] == context["scope"] == "context_only_not_built"
              and seal["plan_sha256"] == context["plan_sha256"])
    p.integer(context["docker_operations"], 0, 0); p.integer(context["model_loads"], 0, 0)
    p.require(build["manifest_sha256"] == value["context_manifest"]["sha256"] == seal["manifest_sha256"]
              and build["seal_sha256"] == value["context_seal"]["sha256"]
              and build["context_sha256"] == context["context_sha256"] == seal["context_sha256"]
              and build["context_size_bytes"] == context["context_size_bytes"] == seal["context_size_bytes"])
    e.same(build["inputs"], context["inputs"])
    p.require(type(context["inputs"]) is list and 1 <= len(context["inputs"]) <= 1024)
    inputs = {}
    for row in context["inputs"]:
        p.exact(row, {"path", "sha256", "size_bytes"})
        p.path(row["path"], absolute=False); p.sha(row["sha256"]); p.integer(row["size_bytes"], 0, 4 * 1024 ** 3)
        p.require(row["path"] not in inputs)
        inputs[row["path"]] = row
    # Match the original build_context canonical format, including its newline.
    input_digest = hashlib.sha256((json.dumps(context["inputs"], sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()).hexdigest()
    p.require(build["source_digest"] == context["source_digest"] == input_digest)
    inventory = p.source_map(build["source_inventory"])
    p.require(type(authority.runtime_paths) is tuple and REQUIRED_RUNTIME_SOURCES <= set(authority.runtime_paths)
              and len(set(authority.runtime_paths)) == len(authority.runtime_paths)
              and all(name in sources and inventory.get(name) == sources[name] for name in authority.runtime_paths))
    for name in authority.runtime_paths:
        p.require(context["source_sha256"].get(name) == sources[name]
                  and inputs.get("app/" + name, {}).get("sha256") == sources[name])
    p.require({name[6:] for name in inputs if name.startswith("model/")} == set(bundle))
    for name, (pin, size) in bundle.items():
        p.require(inputs["model/" + name] == {"path": "model/" + name, "sha256": pin, "size_bytes": size})
    artifact = build["artifact"]
    p.require(artifact["name"] == "evolution-classifier" and artifact["kind"] == "image"
              and artifact["sha256"] == value["oci_manifest"]["sha256"]
              and build["image_manifest_digest"] == "sha256:" + artifact["sha256"]
              and build["image_id"] == manifest["config"]["digest"] == "sha256:" + value["oci_config"]["sha256"])
    p.require(prepared["schema_version"] == 1 and prepared["preparation_lock_digest"] == value["preparation_lock"]["sha256"])
    p.require([row["sha256"] for row in prepared["files"] if row["path"] == "builds/classifier.json"] == [value["build"]["sha256"]])
    for rows in (prepared["artifacts"], prep_lock["images"], artifacts["artifacts"]):
        p.require([row for row in rows if row["name"] == "evolution-classifier"] == [artifact])
    p.exact(native, {"schema_version", "prepared_sha256", "identity", "observations"})
    p.require(native["schema_version"] == "selected-worker-native-image-observations-v1"
              and native["prepared_sha256"] == value["prepared"]["sha256"])
    e.same(native["identity"], identity)
    p.exact(images, {"runtime", "native_images", "node_boundary_snapshot"})
    p.require(images["runtime"] in {"runtime-1", "runtime-2"})
    p.exact(native["observations"], {"runtime-1", "runtime-2"})
    e.same(native["observations"][images["runtime"]], images)
    runtime_ids = set()
    for runtime_name, observation in native["observations"].items():
        p.exact(observation, {"runtime", "native_images", "node_boundary_snapshot"})
        p.require(observation["runtime"] == runtime_name)
        e.same(observation["node_boundary_snapshot"], images["node_boundary_snapshot"])
        p.exact(observation["native_images"], {"runtime", "classifier"})
        for row in observation["native_images"].values():
            p.exact(row, {"container_id", "expected_reference", "manifest_digest", "config_digest", "rootfs_chain_id", "ready"})
            p.sha(row["container_id"]); p.require(row["ready"] is True)
            for key in ("manifest_digest", "config_digest", "rootfs_chain_id"):
                p.require(type(row[key]) is str and row[key].startswith("sha256:")); p.sha(row[key][7:])
            p.require(row["expected_reference"].endswith("@" + row["manifest_digest"]))
        runtime_ids.add(observation["native_images"]["runtime"]["container_id"])
        e.same(observation["native_images"]["classifier"], images["native_images"]["classifier"])
    p.require(len(runtime_ids) == 2)
    node = p.exact(images["node_boundary_snapshot"], {"container_id", "image", "rules_sha256", "network_namespace",
                  "cpu_limit", "cpu_quota_us", "cpu_period_us", "memory_limit_bytes"})
    p.sha(node["container_id"]); p.sha(node["rules_sha256"])
    p.require(type(node["image"]) is str and "@sha256:" in node["image"])
    p.sha(node["image"].rsplit("@sha256:", 1)[1])
    p.require(type(node["network_namespace"]) is str and node["network_namespace"].startswith("net:[")
              and node["network_namespace"].endswith("]") and node["network_namespace"][5:-1].isdigit())
    p.integer(node["cpu_limit"], 6, 6); p.integer(node["memory_limit_bytes"], 8 * 1024 ** 3, 8 * 1024 ** 3)
    p.integer(node["cpu_period_us"], 1, 1000000)
    p.integer(node["cpu_quota_us"], node["cpu_period_us"] * 6, node["cpu_period_us"] * 6)
    classifier = images["native_images"]["classifier"]
    p.require(classifier["expected_reference"] == artifact["source_url"].removeprefix("docker://")
              and classifier["manifest_digest"] == build["image_manifest_digest"] and classifier["config_digest"] == build["image_id"])
    layers = config["rootfs"]["diff_ids"]
    p.require(type(layers) is list and 1 <= len(layers) <= 128)
    chain = None
    for layer in layers:
        p.require(type(layer) is str and layer.startswith("sha256:")); p.sha(layer[7:])
        chain = layer if chain is None else "sha256:" + hashlib.sha256((chain + " " + layer).encode()).hexdigest()
    p.require(classifier["rootfs_chain_id"] == chain)
    start, result = (reader.document(value[key]) for key in ("started", "result"))
    fields = {"schema_version", "status", "producer", "created_at", "identity", "sources"}
    p.exact(start, fields)
    p.exact(result, fields | {"completed_at", "exit_code", "artifacts"})
    p.require(start["schema_version"] == "selected-worker-image-observation-invocation-v1"
              and start["status"] == "running" and result["status"] == "completed")
    e.same({key: start[key] for key in fields - {"status"}}, {key: result[key] for key in fields - {"status"}})
    e.same(start["producer"], value["producer"]); e.same(start["identity"], identity); e.same(start["sources"], sources)
    p.integer(result["exit_code"], 0, 0)
    e.same(result["artifacts"], {key: value[key] for key in ("build", "context_manifest", "context_seal", "prepared",
                                                          "preparation_lock", "artifacts_lock", "oci_manifest", "oci_config", "native")})
    p.require(p.timestamp(result["completed_at"]) >= p.timestamp(start["created_at"]))
    return result["completed_at"]


def model_bundle(reader, ref, *, authority, identity, selected):
    """Authenticate all public model bytes through their original acquired rows."""
    p.require(ref["sha256"] == authority.model_artifacts)
    value = reader.document(ref)
    p.require(value["schema"] == "bounded-candidate-acquisition-v1"
              and value["status"] == "artifacts_verified_not_qualified"
              and value["repository"] == identity["model_id"] and value["revision"] == identity["model_revision"]
              and value["manifest_sha256"] == selected["sha256"]
              and value["model_loaded"] is False and value["model_scored"] is False
              and value["threshold_changed"] is False and value["automatic_retry"] is False)
    manifest = reader.document(selected)
    p.require(type(value["files"]) is dict and 1 <= len(value["files"]) <= 32
              and set(value["files"]) == set(manifest["artifacts"]))
    bundle = {}
    for name, row in value["files"].items():
        p.path(name, absolute=False); p.require(PurePosixPath(name).name == name)
        p.exact(row, {"sha256", "size_bytes", "source_git_oid"})
        p.sha(row["sha256"]); p.integer(row["size_bytes"], 1, 4 * 1024 ** 3)
        expected = manifest["artifacts"][name]
        p.require(row["sha256"] == expected["sha256"] and row["size_bytes"] == expected["size_bytes"]
                  and row["source_git_oid"] == expected["git_oid"])
        bundle[name] = [row["sha256"], row["size_bytes"]]
    p.require(bundle["model.safetensors"][0] == identity["weights_sha256"])
    # Runtime classifier_contract.BUNDLE_SHA256 hashes canonical JSON without
    # a trailing newline; archive JSON framing is a separate representation.
    p.require(p.digest(bundle) == identity["bundle_sha256"])
    return bundle


def verify(*, reference, identity, sources, images, required_corpora, corpus_manifest, load):
    """Fixed seven-keyword port; external capsule authority is mandatory."""
    arguments = copy.deepcopy((reference, identity, sources, images, required_corpora, corpus_manifest))
    reference, identity, sources, images, required_corpora, corpus_manifest = arguments
    p.selected_identity(identity); p.source_map(sources); e.corpus_order(required_corpora)
    reader = Reader(load); capsule = reader.document(reference)
    p.exact(capsule, {"schema_version", "scope", "created_at", "producer_version", "identity", "root", "sources",
                      "corpus_manifest", "selection_manifest", "historical_initial", "final_quality", "loaded", "image_binding"})
    p.require(capsule["schema_version"] == CAPSULE_SCHEMA and capsule["scope"] == e.SCOPE)
    version = capsule["producer_version"]
    p.require(type(version) is str and version in REVIEWED_VERSIONS, "final_worker_producer_unreleased")
    authority = REVIEWED_VERSIONS[version]
    p.require(type(authority) is ProducerVersion)
    e.same(capsule["identity"], identity)
    p.require(REQUIRED_RUNTIME_SOURCES <= set(capsule["sources"]) <= set(sources))
    p.require(all(sources[name] == pin for name, pin in capsule["sources"].items()))
    read_sources(reader, capsule["root"], sources)
    authorization = {"created_at": capsule["created_at"], "identity": identity, "required_corpora": required_corpora}
    index = p.integer(authority.selection_index, 1, 6)
    p.require(len(required_corpora) == index + 3)
    if index <= 3:
        manifest = p.verify_corpus_manifest(capsule["corpus_manifest"], authorization=authorization, load=reader.raw)
    else:
        from .classifier_corpus_extension import verify_corpus_manifest
        manifest = verify_corpus_manifest(capsule["corpus_manifest"], authorization=authorization, load=reader.raw)
    p.require(manifest["selection_index"] == index)
    e.same(manifest, corpus_manifest)
    e.same(manifest["selection_manifest"], capsule["selection_manifest"])
    documents = {name: reader.document(ref) for name, ref in required_corpora.items()}
    initial_completed = historical(reader, capsule["historical_initial"], authority=authority,
                                   identity=identity, corpora=required_corpora, corpus_manifest=capsule["corpus_manifest"])
    plan, raw, start, completion = execution(reader, capsule["final_quality"], role="quality", authority=authority)
    e.same(plan["corpus_manifest"], capsule["corpus_manifest"]); e.same(plan["selection_manifest"], capsule["selection_manifest"])
    bundle = model_bundle(reader, plan["model_artifacts"], authority=authority, identity=identity,
                          selected=capsule["selection_manifest"])
    checked = e.quality(raw, plan=plan, plan_ref=capsule["final_quality"]["plan"], identity=identity,
                        sources=capsule["sources"], corpora=required_corpora, documents=documents, host_namespace=plan["host_namespace"])
    p.require(p.timestamp(initial_completed) <= p.timestamp(plan["created_at"]) <= p.timestamp(start["created_at"])
              <= p.timestamp(checked["started_at"]) <= p.timestamp(checked["completed_at"]) <= p.timestamp(completion["completed_at"]))
    completed = lifecycle(reader, capsule["loaded"], authority=authority, identity=identity,
                          sources=capsule["sources"], historical_completed=initial_completed)
    p.require(max(p.timestamp(completed), p.timestamp(completion["completed_at"])) <= p.timestamp(capsule["created_at"]))
    image_completed = image_closure(reader, capsule["image_binding"], authority=authority, identity=identity,
                                    sources=capsule["sources"], images=images, bundle=bundle)
    p.require(p.timestamp(image_completed) <= p.timestamp(capsule["created_at"]))
    reader.finish()
    return {"schema_version": p.FINAL_WORKER_SCHEMA, "scope": e.SCOPE, "identity": identity,
            "sources_sha256": p.digest(sources), "images_sha256": p.digest(images), "qualification_sha256": reference["sha256"],
            "corpora": required_corpora, "matrices": checked["matrices"], "loaded_cases": 9, "loaded_requests": 46,
            "threshold": .90, "minimum_recall": .90, "maximum_fpr": .05, "window_tokens": 512, "window_stride": 256,
            "maximum_windows": 16, "memory_mib": checked["memory_mib"], "cpu_cores": checked["cpu_cores"],
            "operational_errors": 0, "parent_exit_code": 0, "automatic_retry": False, "warmup_requests": 0,
            "production_enabled": True}
