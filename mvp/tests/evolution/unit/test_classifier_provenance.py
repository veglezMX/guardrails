"""Synthetic receipt algebra only; these fixtures are never operational proof."""
from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from evolution import classifier_provenance as p
from evolution.registry import EvidenceError

NOW = datetime(2026, 10, 12, 16, tzinfo=UTC)
SHA = "a" * 64
OWNER = "b" * 32
NODE = "c" * 64
OP = str(UUID(int=90))
INVOCATION = str(UUID(int=91))
GENERATION = str(UUID(int=92))
SOURCES = {"mvp/synthetic.py": SHA}


def uid(index):
    return str(UUID(int=index))


def stamp(delta=0):
    return (NOW + timedelta(seconds=delta)).isoformat()


class Store:
    def __init__(self):
        self.raws = {}
        self.calls = []

    def put(self, name, value):
        pathname = name if name.startswith("/") else "/synthetic/" + name
        raw = p.canonical(value) + b"\n"
        self.raws[pathname] = raw
        return {"path": pathname, "sha256": hashlib.sha256(raw).hexdigest()}

    def load(self, ref):
        self.calls.append(copy.deepcopy(ref))
        return self.raws[ref["path"]]


def synthetic_corpus(name):
    rows = []
    for language, expected, total in (("en", True, 200), ("en", False, 200), ("es", True, 100), ("es", False, 100)):
        for index in range(total):
            ordinal = len(rows)
            row = {"case_id": f"{name}-{ordinal}", "language": language, "expected_injection": expected,
                "split": "heldout", "category": "synthetic", "family_id": f"family-{ordinal // 10}",
                "part": {"part_id": str(ordinal), "role": "user", "text": f"Synthetic {name} {language} {expected} example {index}."},
                "indirect": True, "quoted_security": not expected}
            if name == "original":
                row["part"]["kind"] = "text"
            rows.append(row)
    return {"corpus_id": "synthetic-" + name, "threshold": .9, "minimum_recall": .9, "maximum_false_positive_rate": .05,
        "provenance": {"independent": True, "blind": True, "synthetic": True, "prior_corpus_access": True,
            "prior_corpus_example_content_access": False, "schema_shape_only_access": True,
            "schema_shape_source": "/synthetic/corpus-heldout_2.json", "candidate_model_identity_access": False,
            "candidate_model_configuration_access": False, "candidate_model_result_access": False,
            "repository_code_access": False, "external_data_or_network_access": False, "human_adjudicated": False,
            "model_inference_calls_for_evaluation": 0, "authored_languages": ["en", "es"]}, "cases": rows}


def synthetic_approval():
    return {"schema_version": "classifier-bounded-selection-approval-v1", "recorded_at": stamp(-1000),
            "status": "approved_no_candidate_qualified", "maximum_additional_candidates": 3}


def selection_chain(store, identity, index=1):
    approval = store.put("selection-approval.json", synthetic_approval())
    baseline = {key: store.put("corpus-" + key + ".json", synthetic_corpus(key)) for key in ("original", "heldout_1", "heldout_2")}
    corpora = dict(baseline); previous = None
    for selection_index in range(1, index + 1):
        key = f"confirmation_{selection_index}"
        prior = dict(corpora)
        corpora[key] = store.put("corpus-" + key + ".json", synthetic_corpus(key))
        selected = {"schema_version": "bounded-classifier-candidate-manifest-v1", "recorded_at": stamp(-900 + selection_index),
            "selection_index": selection_index, "maximum_candidates": 3, "approval": approval, "repository": identity["model_id"],
            "revision": identity["model_revision"], "artifacts": {"model.safetensors": {"sha256": identity["weights_sha256"]}},
            "status": "selected_for_acquisition_and_qualification_only", "enabled": False, "model_scored": False,
            "model_loaded": False, "weights_acquired": False,
            "qualification": {"initial_existing_cases": 1800 + (selection_index - 1) * 600, "fresh_blind_confirmation_cases": 600,
                "english_each_corpus_recall_min": .90, "english_each_corpus_fpr_max": .05, "cutoff": .90, "window_tokens": 512,
                "stride_tokens": 256, "maximum_windows": 16, "score_aggregation": "max", "cpu_cores": 2, "memory_mib": 3072,
                "torch_threads": 2, "torch_interop_threads": 1, "zero_operational_errors": True, "spanish_enabled": False,
                "retries": 0, "warmups": 0, "calibration": False, "training": False, "native_published_thresholds_used": False,
                "final_production_worker_and_all_operational_gates_required": True}}
        projection = p.corpus_projection(store.load(corpora[key]), key)
        author_files = {"corpus-" + key + ".json": corpora[key]["sha256"]}
        for filename in ("AUTHORSHIP.md", "VALIDATION.json", "author_corpus.py", "AUTHORING_STATUS.md"):
            author_files[filename] = store.put(f"author-{selection_index}/" + filename, {"synthetic": filename})["sha256"]
        author = {"corpus_id": "synthetic-" + key, "frozen_at_utc": stamp(-500 + selection_index), "state": "frozen_before_evaluation",
            "texts_and_labels_immutable": True, "counts": {"en_malicious": 200, "en_benign": 200, "es_malicious": 100, "es_benign": 100},
            "family_count": 60, "variants_per_family": 10, "full_normalized_text_uniqueness": {
                "method": "Unicode NFKC, Unicode casefold, collapse every whitespace run to a single ASCII space, strip both ends; compare the complete normalized part.text.",
                "total": 600, "unique": 600, "duplicates": 0, "ordered_normalized_text_sha256": projection["ordered_sha256"]},
            "hash_algorithm": "SHA-256", "files": author_files, "candidate_information_accessed": False, "evaluation_calls": 0,
            "post_freeze_prior_overlap_check": "synthetic custodian", "token_usage": "synthetic", "tool_credits": "synthetic"}
        author_ref = store.put(f"author-{selection_index}/freeze.json", author)
        review = {"schema_version": "evolution-classifier-confirmation-overlap-v1", "reviewed_at": stamp(-400 + selection_index),
            "selection_index": selection_index, "confirmation": corpora[key], "author_freeze": author_ref, "prior_corpora": prior,
            "normalization": "unicode_nfkc_casefold_whitespace_v1",
            "counts": {"total": 600, "en_malicious": 200, "en_benign": 200, "es_malicious": 100, "es_benign": 100, "families": 60},
            "within_corpus_duplicates": 0, "prior_corpus_overlap": {k: 0 for k in prior}, "threshold": .9, "minimum_recall": .9,
            "maximum_false_positive_rate": .05, "model_evaluation_performed": False,
            "limitations": ["synthetic", "related_variants_within_families", "not_human_adjudicated"],
            "status": "frozen_confirmation_schema_and_overlap_verified"}
        manifest = {"schema_version": p.CORPUS_MANIFEST_SCHEMA, "frozen_at": stamp(-300 + selection_index), "selection_index": selection_index,
            "selection_manifest": store.put(f"selected-{selection_index}.json", selected), "approval": approval,
            "previous_manifest": previous, "baseline_corpora": baseline, "corpora": dict(corpora), "author_freeze": author_ref,
            "parent_overlap_review": store.put(f"overlap-{selection_index}.json", review)}
        previous = store.put(f"corpus-manifest-{selection_index}.json", manifest)
    return corpora, previous, manifest


def case(family="tls", *, source_node="d" * 64, selection_index=1):
    store = Store()
    case_id = {"tls": "SEC-CLASSIFIER-DEPLOYED-TLS-AUTH-RUNTIME-1", "resource": "OPS-CLASSIFIER-RESOURCES-TEAM-A-INPUT",
               "cni": "OPS-CLASSIFIER-CNI-ORIGINAL-GROUP", "budget": "OPS-CLASSIFIER-BUDGET-TEAM-A-INPUT-BENIGN"}[family]
    identity = {"model_id": "synthetic/final", "model_revision": "synthetic-revision", "weights_sha256": SHA,
                "bundle_sha256": SHA, "evaluator_revision": "synthetic-evaluator", "class_mapping": {"0": "SAFE", "1": "INJECTION"},
                "supported_languages": ["en"]}
    corpora, corpus_manifest_ref, corpus_manifest = selection_chain(store, identity, selection_index)
    qualification = store.put("final-worker.json", {"schema_version": "synthetic-final-binding-never-operational", "production_enabled": True})
    prepared = store.put("prepared.json", {"schema_version": 1, "artifacts": ["synthetic"], "files": ["synthetic"],
        "preparation_lock_digest": SHA, "environment": {}})
    boundary = store.put("node-boundary.json", {"synthetic": "current native checker must authenticate actual boundary"})
    binding = {"owner_id": OWNER, "node_id": NODE, "pods": {"classifier": {"synthetic": True}}}
    private = {"owner.json": SHA}
    forwards = {"runtime": {"pid": 123, "start": 456, "command_sha256": SHA}}
    images = {"classifier": {"oci": SHA, "config": SHA}, "node_boundary_snapshot": p.document(store.load(boundary))}
    subject = {"scope": "team-a", "principal_id": uid(10), "credential_id": uid(20), "workload_id": None,
               "roles": ["runtime"], "effective_expires_at": stamp(36000)}
    entries = [{"scope": "team-a", "principal_id": uid(10 + index), "workload_id": None, "roles": ["runtime"],
                "retired": True, "receipt": {"credential_id": uid(20 + index), "expires_at": stamp(36000)}} for index in range(17)]
    journal = {"schema_version": "task-credential-renewal-v1", "status": "complete", "generation": GENERATION,
        "owner_id": OWNER, "node_id": "d" * 64, "expires_at": stamp(36000), "entries": entries,
        "rollouts": {str(index): {"complete": True} for index in range(4)}}
    credentials = store.put("credentials.json", journal)
    owner = {"profile": "guardrails-evolution", "uid": 1000, "state": "/synthetic/state", "container_id": source_node, "owner_id": OWNER}
    manifest = {"synthetic": "logical dump manifest"}
    seal = {"format": "evolution-p2-preservation-v1", "source_owner": owner, "postgres_image": "postgres@sha256:" + SHA,
            "manifest_sha256": p.digest(manifest), "files": {"private.tar": {"sha256": SHA}}}
    seal_ref = store.put("seal.json", seal)
    ephemeral = "evolution-pg-restore-" + "e" * 16
    offline = {"seal": seal_ref, "manifest": store.put("manifest.json", manifest),
        "proof": store.put("offline-proof.json", {"status": "offline_restore_verified", "format": "evolution-p2-restoration-proof-v1",
            "seal_sha256": seal_ref["sha256"], "manifest_sha256": p.digest(manifest), "postgres_image": seal["postgres_image"],
            "source_node_id": owner["container_id"], "app_password_authenticated": True, "owner_password_authenticated": True, "forced_rls_exercised": True}),
        "operation": store.put("offline-operation.json", {"container_name": ephemeral, "owner_token": "f" * 32, "uid": 1000,
            "postgres_image": seal["postgres_image"], "seal_sha256": seal_ref["sha256"]}),
        "container": store.put("offline-container.json", {"container_id": "f" * 64}),
        "cleanup": store.put("offline-cleanup.json", {"container_name": ephemeral, "verified_absent": True})}
    role = {"tls_version": "TLSv1.3", **{key: True for key in ("authenticated", "tls_verified_full", "read_only", "wrong_password_rejected", "plaintext_rejected")}}
    restore = store.put("restore.json", {"status": "fresh_node_database_restored", "node_id": NODE,
        "seal_sha256": seal_ref["sha256"], "manifest_sha256": p.digest(manifest)})
    access = store.put("access.json", {"status": "fresh_node_access_verified", "format": "evolution-p2-fresh-access-v1", "owner_id": OWNER,
        "node_id": NODE, "seal_sha256": seal_ref["sha256"], "manifest_sha256": p.digest(manifest), "rls_existing_scopes_checked": 2,
        "rls_tables_checked": ["tenants", "records", "record_links", "audit_heads"], "rls_unset_scope_denied": True,
        "rls_foreign_rows_visible": False, "logical_database_writes": False, "roles": {"owner": role, "app": role}})
    resume = store.put("resume.json", {"operation": "resume", "node_id": NODE, "prepared_sha256": SHA, "backup_seal_sha256": seal_ref["sha256"],
        "result": {"status": "private_assembly_restored", "governance_seed_reexecuted": False, "old_forward_records_restored": False,
                   "historical_token_journals_unchanged": True}})
    cold = {"schema_version": "evolution-classifier-cold-lineage-v1", "owner_id": OWNER, "node_id": NODE, "prepared_sha256": SHA,
            "seal_sha256": seal_ref["sha256"], "manifest_sha256": p.digest(manifest), "offline": offline, "restore": restore, "access": access, "resume": resume}
    rollout = {"schema_version": "evolution-classifier-rollout-v1", "status": "selected_worker_readiness_verified", "profile": "classifier",
        "owner_id": OWNER, "node_id": NODE, "from_prepared_sha256": SHA, "to_prepared_sha256": prepared["sha256"],
        "identity_sha256": p.digest(identity), "sources_sha256": p.digest(SOURCES), "images_sha256": p.digest(images),
        "binding_sha256": p.digest(binding), "cold_index_inputs_sha256": p.digest(cold), "completed_at": stamp(-40)}
    cold["rollout"] = store.put("rollout.json", rollout)
    cold_ref = store.put("cold.json", cold)
    receipts = {"qualification": qualification, "prepared": prepared, "cold": cold_ref, "credentials": credentials, "node_boundary": boundary}
    expected = {"owner_id": OWNER, "node_id": NODE, "prepared_sha256": prepared["sha256"], "binding_sha256": p.digest(binding),
        "identity_sha256": p.digest(identity), "sources_sha256": p.digest(SOURCES), "credential_generation": GENERATION,
        "credential_journal_sha256": credentials["sha256"], "private_files_sha256": p.digest(private), "forwards_sha256": p.digest(forwards),
        "node_boundary_sha256": boundary["sha256"], "worker_epoch": "f" * 32, "subjects": [subject], "minimum_validity_seconds": 14400}
    preflight = {"schema_version": p.PREFLIGHT_SCHEMA, "created_at": stamp(-30), "valid_until": stamp(20000), "expected": expected,
        "binding": binding, "identity": identity, "sources": SOURCES, "private_files": private, "forwards": forwards, "images": images,
        "node_boundary": boundary, "receipts": {key: receipts[key] for key in p.BASE_PROOFS}}
    preflight_ref = store.put("preflight.json", preflight)
    authorization = {"schema_version": p.AUTHORIZATION_SCHEMA, "authorization_id": uid(100), "created_at": stamp(-20), "expires_at": stamp(10000),
        "case": {"case_id": case_id, "nodeid": "synthetic.py::test_original", "family": family, "minimum_assertions": 2},
        "sources": SOURCES, "registry_sha256": SHA, "identity": identity, "preflight": preflight_ref, "receipts": receipts,
        "worker": {"executable_sha256": SHA, "command_sha256": SHA, "launcher_sha256": SHA, "timeout_seconds": 900,
                   "automatic_retry": False, "warmup_requests": 0}, "output_directory": "/synthetic/measurements/" + case_id,
        "governance_root": "/synthetic/governance" if family in {"resource", "budget"} else None, "required_corpora": corpora,
        "current_checks_directory": "/synthetic/current-checks", "corpus_manifest": corpus_manifest_ref}
    auth_ref = store.put("authorization.json", authorization)
    matrices = {key: {language: {"tp": total, "fn": 0, "tn": total, "fp": 0, "errors": 0}
        for language, total in (("en", 200), ("es", 100))} for key in corpora}
    final = {"schema_version": p.FINAL_WORKER_SCHEMA, "scope": "final_selected_worker", "identity": identity,
        "sources_sha256": p.digest(SOURCES), "images_sha256": p.digest(images), "qualification_sha256": qualification["sha256"],
        "corpora": corpora, "matrices": matrices, "loaded_cases": 9, "loaded_requests": 46, "threshold": .9, "minimum_recall": .9,
        "maximum_fpr": .05, "window_tokens": 512, "window_stride": 256, "maximum_windows": 16, "memory_mib": 2048,
        "cpu_cores": 2, "operational_errors": 0, "parent_exit_code": 0, "automatic_retry": False, "warmup_requests": 0, "production_enabled": True}
    operation = OP if family in {"resource", "budget", "cni"} else None
    observation = {"operation": operation, "invocation": INVOCATION, "synthetic": True}
    if family == "cni":
        observation["group"] = {"readiness_binding": binding, "synthetic": True}
    governance_files = {}
    if family in {"resource", "budget"}:
        original_policy = {"tenant_id": "team-a", "policy_id": "original", "version": 1, "compiled_digest": SHA}
        matrix_policy = {**original_policy, "policy_id": "eval-" + UUID(operation).hex}
        actors = {role: uid(index + 200) for index, role in enumerate(("tenant_admin", "author", "approver", "operator", "auditor"))}
        original_binding = {"tenant_id": "team-a", "workload_id": uid(300), "revision": 1, "policy": original_policy, "state": "block"}
        intent = {"schema_version": "evolution-governance-attempt-v1", "operation": operation, "tenant_id": "team-a", "engine": "injection",
                  "stage": "input", "state": "observe", "actors": actors, "source_sha256": SOURCES, "original_binding": original_binding,
                  "original_policy": original_policy, "status": "intent_only_no_mutation_claim"}
        gov_rows = {"intent.json": intent, "completion.json": {"operation": operation, "status": "case_passed", "restoration": "applied", "last_request_outcome_unknown": False}}
        for index, label in enumerate(("matrix-release", "restore-original-binding")):
            gov_rows["release-" + label + ".json"] = {"schema_version": "evolution-governance-release-handle-v1", "status": "acknowledged_not_yet_applied",
                "handle": {"operation": operation, "label": label, "release_id": uid(400 + index), "tenant_id": "team-a", "workload_id": uid(300),
                           "policy": matrix_policy if index == 0 else original_policy, "binding_revision": index + 2, "previous_revision": index + 1,
                           "state": "observe" if index == 0 else "block", "actor_id": actors["operator"]}}
        gov_rows["request-01-intent.json"] = {"operation": operation, "status": "request_intended_outcome_unknown", "body_sha256": SHA}
        gov_rows["request-01-returned.json"] = {"operation": operation, "status": "port_returned_expected_status_not_application_proof", "response_sha256": SHA}
        governance_files = {name: store.put(authorization["governance_root"] + "/" + operation + "/" + name, row) for name, row in gov_rows.items()}
        observation["governance"] = {"intent_sha256": governance_files["intent.json"]["sha256"], "completion_sha256": governance_files["completion.json"]["sha256"],
            "release_sha256": governance_files["release-matrix-release.json"]["sha256"], "restoration_sha256": governance_files["release-restore-original-binding.json"]["sha256"]}
        observation["decision"] = {"binding_revision": 2, "policy_digest": SHA}
    files = {name: store.put(authorization["output_directory"] + "/" + name,
        observation if name.endswith("observation.json") else {"synthetic": name}) for name in p.FAMILY_FILES[family]}
    completion = {"schema_version": p.COMPLETION_SCHEMA, "authorization": auth_ref, "authorization_id": authorization["authorization_id"],
        "case_id": case_id, "nodeid": authorization["case"]["nodeid"], "started_at": stamp(-10), "completed_at": stamp(-1),
        "worker": authorization["worker"], "pid": 123, "process_start": 456, "exit_code": 0, "elapsed_ms": 9000,
        "result": {"nodeid": authorization["case"]["nodeid"], "status": "pass", "duration_ms": 8900, "assertions": 2, "reason_codes": []},
        "observation_files": files, "governance_files": governance_files, "current_checks": []}
    for index, phase in enumerate(p.CHECK_PHASES[family]):
        row = {key: copy.deepcopy(value) for key, value in expected.items() if key != "minimum_validity_seconds"} | {
            "schema_version": p.CURRENT_SCHEMA, "preflight_sha256": preflight_ref["sha256"], "phase": phase, "operation": operation,
            "observed_at": stamp(-8 + index), "elapsed_ms": 15}
        completion["current_checks"].append(store.put(authorization["current_checks_directory"] + f"/{index + 1:02d}-{phase}.json", row))
    completion_ref = store.put("completion.json", completion)
    def current(*, phase, operation, deadline):
        assert deadline is None or deadline == 12345
        return {key: copy.deepcopy(value) for key, value in expected.items() if key != "minimum_validity_seconds"} | {
            "schema_version": p.CURRENT_SCHEMA, "preflight_sha256": preflight_ref["sha256"], "phase": phase, "operation": operation,
            "observed_at": stamp(), "elapsed_ms": 15}
    def qualify(**kwargs):
        assert kwargs["identity"] == identity and kwargs["sources"] == SOURCES and kwargs["required_corpora"] == corpora
        assert kwargs["images"] == images and kwargs["corpus_manifest"] == corpus_manifest
        kwargs["load"](kwargs["reference"])
        return copy.deepcopy(final)
    options = {"authorization_ref": auth_ref, "expected_case": case_id, "expected_nodeid": authorization["case"]["nodeid"],
        "expected_sources": SOURCES, "expected_registry_sha256": SHA, "load": store.load, "qualify_final_worker": qualify,
        "current": current, "now": lambda: NOW, "expected_corpus_manifest": corpus_manifest_ref}
    store.calls.clear()  # Fixture construction is outside the observed adapter boundary.
    return SimpleNamespace(**locals())


def invoke(c, phase="before", **overrides):
    options = dict(c.options)
    if phase == "consume":
        options["completion_ref"] = c.completion_ref
    options.update(overrides)
    verifier = p.ProvenanceVerifier(**options)
    result = verifier(phase=phase, case_id=c.case_id, invocation=INVOCATION, operation=c.operation,
        binding=copy.deepcopy(c.binding), sources=dict(SOURCES), observation=copy.deepcopy(c.observation),
        group=copy.deepcopy(c.observation.get("group")), deadline=12345)
    return verifier, result


@pytest.fixture(autouse=True)
def no_operations(monkeypatch):
    import socket
    import subprocess
    def forbidden(*args, **kwargs):
        raise AssertionError("synthetic unit attempted an operation")
    # Synthetic fixture anchors are explicit test-only substitutions. Production
    # retains literal immutable original corpus and real user-approval SHA pins.
    monkeypatch.setattr(p, "BASELINE_CORPORA", {name: hashlib.sha256(p.canonical(synthetic_corpus(name)) + b"\n").hexdigest()
        for name in ("original", "heldout_1", "heldout_2")})
    monkeypatch.setattr(p, "SELECTION_APPROVAL_SHA256", hashlib.sha256(p.canonical(synthetic_approval()) + b"\n").hexdigest())
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.mark.parametrize("family", ["tls", "resource", "cni", "budget"])
@pytest.mark.parametrize("phase", ["before", "after", "consume"])
def test_original_family_lifecycle_has_independent_completion(family, phase):
    c = case(family)
    _, proof = invoke(c, phase)
    assert proof["parent_invocation"] == c.auth_ref["sha256"]
    assert set(proof) == p.BASE_PROOFS | {"parent_invocation"} | ({"transport"} if family in {"tls", "budget"} else {"node_boundary"} if family == "cni" else set())
    assert any(row == c.auth_ref for row in c.store.calls)
    assert (any(row == c.completion_ref for row in c.store.calls)) is (phase == "consume")


@pytest.mark.parametrize("name", ["load", "qualify_final_worker", "current", "now"])
def test_all_operational_adapters_required_before_reads(name):
    c = case(); c.options[name] = None
    with pytest.raises(EvidenceError):
        p.ProvenanceVerifier(**c.options)
    assert c.store.calls == []


def test_missing_parent_completion_never_consumes_success():
    c = case(); verifier = p.ProvenanceVerifier(**c.options)
    with pytest.raises(EvidenceError):
        verifier(phase="consume", operation=None, binding=c.binding, sources=SOURCES, observation=c.observation)


@pytest.mark.parametrize("field,value", [("exit_code", 1), ("exit_code", True), ("elapsed_ms", 900001), ("elapsed_ms", float("nan")),
    ("completed_at", stamp(1)), ("started_at", stamp(-21)), ("pid", 0), ("process_start", 0), ("authorization_id", uid(999))])
def test_parent_completion_failures(field, value):
    c = case(); changed = copy.deepcopy(c.completion); changed[field] = value
    with pytest.raises(EvidenceError):
        p.validate_completion(changed, authorization_ref=c.auth_ref, authorization=c.authorization, observation_files=c.files, now=NOW)


@pytest.mark.parametrize("field,value", [("status", "fail"), ("assertions", 1), ("assertions", True), ("reason_codes", ["case_timeout"]),
                                       ("nodeid", "foreign"), ("duration_ms", 9001)])
def test_original_worker_result_cannot_be_relabelled(field, value):
    c = case(); changed = copy.deepcopy(c.completion); changed["result"][field] = value
    with pytest.raises(EvidenceError):
        p.validate_completion(changed, authorization_ref=c.auth_ref, authorization=c.authorization, observation_files=c.files, now=NOW)


@pytest.mark.parametrize("field", sorted(p.CURRENT_FIELDS))
def test_current_identity_changes_refuse(field):
    c = case(); row = c.current(phase="before", operation=None, deadline=None); row[field] = "foreign"
    with pytest.raises(EvidenceError):
        p.verify_current(c.expected, row, preflight_sha256=c.preflight_ref["sha256"], phase="before", operation=None, now=NOW)


@pytest.mark.parametrize("field,value", [("observed_at", stamp(-60.001)), ("observed_at", stamp(.001)), ("elapsed_ms", 60000.001),
    ("elapsed_ms", -1), ("elapsed_ms", True), ("elapsed_ms", float("inf")), ("phase", "after"), ("operation", OP), ("preflight_sha256", SHA)])
def test_current_clock_and_bracket_are_exact(field, value):
    c = case(); row = c.current(phase="before", operation=None, deadline=None); row[field] = value
    with pytest.raises(EvidenceError):
        p.verify_current(c.expected, row, preflight_sha256=c.preflight_ref["sha256"], phase="before", operation=None, now=NOW)


def test_current_horizon_does_not_accept_once_valid_credential():
    c = case(); row = c.current(phase="before", operation=None, deadline=None)
    with pytest.raises(EvidenceError):
        p.verify_current(c.expected, row, preflight_sha256=c.preflight_ref["sha256"], phase="before", operation=None, now=NOW + timedelta(seconds=22000))


@pytest.mark.parametrize("field,value", [("scope", "initial_candidate"), ("production_enabled", False), ("operational_errors", 570),
    ("parent_exit_code", 1), ("automatic_retry", True), ("warmup_requests", 1), ("loaded_requests", 45), ("loaded_cases", 8),
    ("threshold", .89), ("minimum_recall", .89), ("maximum_fpr", .10), ("window_tokens", 511), ("maximum_windows", 15),
    ("window_stride", 257), ("memory_mib", 3073), ("cpu_cores", 3), ("images_sha256", SHA), ("qualification_sha256", SHA)])
def test_final_worker_admission_never_accepts_candidate_or_relaxed_limits(field, value):
    c = case(); changed = copy.deepcopy(c.final); changed[field] = value
    with pytest.raises(EvidenceError):
        p.verify_final_worker(changed, authorization=c.authorization, preflight=c.preflight)


@pytest.mark.parametrize("corpus", ["original", "heldout_1", "heldout_2"])
@pytest.mark.parametrize("counts", [{"tp": 179, "fn": 21, "tn": 200, "fp": 0, "errors": 0},
    {"tp": 200, "fn": 0, "tn": 189, "fp": 11, "errors": 0}, {"tp": 200, "fn": 0, "tn": 200, "fp": 0, "errors": 1},
    {"tp": 199, "fn": 0, "tn": 200, "fp": 0, "errors": 0}])
def test_every_original_and_heldout_english_matrix_required(corpus, counts):
    c = case(); changed = copy.deepcopy(c.final); changed["matrices"][corpus]["en"] = counts
    with pytest.raises(EvidenceError):
        p.verify_final_worker(changed, authorization=c.authorization, preflight=c.preflight)


def test_spanish_is_reported_without_new_enforcement_threshold():
    c = case(); changed = copy.deepcopy(c.final)
    for languages in changed["matrices"].values():
        languages["es"] = {"tp": 0, "fn": 100, "tn": 0, "fp": 100, "errors": 0}
    assert p.verify_final_worker(changed, authorization=c.authorization, preflight=c.preflight) == changed


@pytest.mark.parametrize("field,value", [("status", "issuing"), ("generation", uid(999)), ("owner_id", "f" * 32), ("expires_at", stamp(14399))])
def test_pending_foreign_or_expired_generation_refused(field, value):
    c = case(); changed = copy.deepcopy(c.journal); changed[field] = value
    with pytest.raises(EvidenceError):
        p.verify_credentials(changed, c.preflight, now=NOW)


@pytest.mark.parametrize("location", ["retired", "receipt", "roles", "rollout"])
def test_credential_retirement_receipts_and_actual_subjects_join(location):
    c = case(); changed = copy.deepcopy(c.journal)
    if location == "retired":
        changed["entries"][0]["retired"] = False
    elif location == "receipt":
        changed["entries"][0]["receipt"]["credential_id"] = uid(999)
    elif location == "roles":
        changed["entries"][0]["roles"] = ["tenant_admin"]
    else:
        changed["rollouts"]["0"]["complete"] = False
    with pytest.raises(EvidenceError):
        p.verify_credentials(changed, c.preflight, now=NOW)


@pytest.mark.parametrize("part,field,value", [("proof", "status", "archive_hash_verified"), ("proof", "forced_rls_exercised", False),
    ("proof", "app_password_authenticated", False), ("proof", "source_node_id", NODE), ("cleanup", "verified_absent", False),
    ("cleanup", "container_name", "foreign"), ("operation", "uid", 999), ("container", "container_id", "d" * 64)])
def test_offline_restore_proof_is_required_beyond_archive_hash(part, field, value):
    c = case(); index = copy.deepcopy(c.offline); row = p.document(c.store.load(index[part])); row[field] = value
    index[part] = c.store.put("mutated-" + part, row)
    with pytest.raises(EvidenceError):
        p.verify_offline(index, load=c.store.load)


@pytest.mark.parametrize("part,field,value", [("restore", "status", "backup_captured"), ("restore", "node_id", SHA),
    ("access", "rls_foreign_rows_visible", True), ("access", "rls_unset_scope_denied", False), ("access", "logical_database_writes", True),
    ("access", "rls_existing_scopes_checked", 1), ("access", "manifest_sha256", SHA), ("rollout", "profile", "deterministic_mock"),
    ("rollout", "images_sha256", SHA), ("rollout", "from_prepared_sha256", NODE)])
def test_original_cold_and_selected_rollout_must_share_identity(part, field, value):
    c = case(); index = copy.deepcopy(c.cold); row = p.document(c.store.load(index[part])); row[field] = value
    index[part] = c.store.put("mutated-" + part, row)
    with pytest.raises(EvidenceError):
        p.verify_cold(index, authorization=c.authorization, preflight=c.preflight, load=c.store.load)


@pytest.mark.parametrize("part", ["intent.json", "completion.json", "release-matrix-release.json", "release-restore-original-binding.json", "request-01-returned.json"])
def test_governance_cannot_omit_original_or_unknown_ack(part):
    c = case("resource"); files = copy.deepcopy(c.governance_files); del files[part]
    with pytest.raises(EvidenceError):
        p.verify_governance(files, authorization=c.authorization, observation=c.observation, operation=OP, load=c.store.load)


@pytest.mark.parametrize("field,value", [("status", "case_failed"), ("restoration", "failed"), ("last_request_outcome_unknown", True), ("operation", uid(999))])
def test_failed_governance_never_attaches_success(field, value):
    c = case("budget"); files = copy.deepcopy(c.governance_files); done = p.document(c.store.load(files["completion.json"])); done[field] = value
    files["completion.json"] = c.store.put(files["completion.json"]["path"], done)
    with pytest.raises(EvidenceError):
        p.verify_governance(files, authorization=c.authorization, observation=c.observation, operation=OP, load=c.store.load)


def test_parent_current_record_cannot_be_boolean():
    with pytest.raises(EvidenceError):
        invoke(case(), current=lambda **_: True)


def test_qualification_callback_cannot_be_boolean():
    with pytest.raises(EvidenceError):
        invoke(case(), qualify_final_worker=lambda **_: True)


def test_adapter_argument_mutation_rejected():
    c = case()
    def mutate(**args):
        args["identity"]["model_id"] = "changed"
        return c.final
    with pytest.raises(EvidenceError):
        invoke(c, qualify_final_worker=mutate)


def test_source_bytes_changed_inside_callback_refused_on_reopen():
    c = case()
    def mutate(**args):
        c.store.raws[c.auth_ref["path"]] += b" "
        return c.final
    with pytest.raises(EvidenceError):
        invoke(c, qualify_final_worker=mutate)


def test_reader_cannot_repin_reference():
    c = case()
    def mutate(ref):
        raw = c.store.load(ref); ref["sha256"] = SHA
        return raw
    with pytest.raises(EvidenceError):
        invoke(c, load=mutate)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'[]', b'{} trailing', b'\xff', b''])
def test_json_ambiguity_and_nonfinite_rejected(raw):
    with pytest.raises(EvidenceError):
        p.document(raw)


@pytest.mark.parametrize("pathname", ["relative.json", "/x/../y", "/x//y", "/x/./y", "/x/", "/x\\y", "/x\x00y"])
def test_noncanonical_references_rejected(pathname):
    with pytest.raises(EvidenceError):
        p.reference({"path": pathname, "sha256": SHA})


@pytest.mark.parametrize("field", ["identity", "sources", "private_files", "forwards", "binding"])
def test_preflight_projection_must_match_actual_retained_values(field):
    c = case(); value = copy.deepcopy(c.preflight); value[field] = {"foreign": SHA}
    with pytest.raises(EvidenceError):
        p.validate_preflight(value, c.authorization, now=NOW)


def test_resource_consume_accepts_hash_seam_and_reopens_raw_governance():
    c = case("resource"); verifier = p.ProvenanceVerifier(**c.options, completion_ref=c.completion_ref)
    result = verifier(phase="consume", operation=OP, binding_sha256=p.digest(c.binding), sources=SOURCES, observation=c.observation)
    assert result["credentials"] == c.credentials["sha256"]
    assert {ref["path"] for ref in c.governance_files.values()} <= {ref["path"] for ref in c.store.calls}


def test_cni_consume_uses_original_group_only_seam():
    c = case("cni"); verifier = p.ProvenanceVerifier(**c.options, completion_ref=c.completion_ref)
    result = verifier(phase="consume", operation=OP, binding=c.binding, sources=SOURCES, group=c.observation["group"])
    assert result["node_boundary"] == c.boundary["sha256"]
    assert verifier.validated_checks[-1]["phase"] == "consume"


@pytest.mark.parametrize("mutation", ["missing", "reorder", "outside", "foreign_operation", "stale_epoch"])
def test_consume_requires_original_retained_checks(mutation):
    c = case("budget"); complete = copy.deepcopy(c.completion)
    if mutation == "missing":
        complete["current_checks"].pop()
    elif mutation == "reorder":
        complete["current_checks"].reverse()
    elif mutation == "outside":
        complete["current_checks"][0]["path"] = "/foreign/before.json"
    else:
        ref = complete["current_checks"][0]; row = p.document(c.store.load(ref))
        row["operation" if mutation == "foreign_operation" else "worker_epoch"] = uid(999) if mutation == "foreign_operation" else "a" * 32
        complete["current_checks"][0] = c.store.put(ref["path"], row)
    c.completion_ref = c.store.put("completion-mutated.json", complete)
    with pytest.raises(EvidenceError):
        invoke(c, "consume")


def test_expiry_during_readonly_adapter_is_not_hidden():
    c = case(); times = iter([NOW, NOW, NOW + timedelta(seconds=10001)])
    with pytest.raises(EvidenceError):
        invoke(c, now=lambda: next(times))


@pytest.mark.parametrize("phase", p.CHECK_PHASES["budget"])
def test_runtime_keeps_exact_current_projection_for_required_durable_recorder(phase):
    c = case("budget"); verifier, _ = invoke(c, phase)
    assert verifier.validated_checks == [c.current(phase=phase, operation=OP, deadline=None)]


@pytest.mark.parametrize("field,value", [("schema_version", "unknown"), ("expires_at", stamp()), ("created_at", stamp(1)),
    ("registry_sha256", NODE), ("sources", {"mvp/synthetic.py": NODE}), ("output_directory", "/foreign/wrong-case"),
    ("governance_root", "/unexpected"), ("current_checks_directory", "/synthetic/measurements/SEC-CLASSIFIER-DEPLOYED-TLS-AUTH-RUNTIME-1/checks")])
def test_launch_capsule_requires_independent_exact_original_case_binding(field, value):
    c = case(); changed = copy.deepcopy(c.authorization); changed[field] = value
    with pytest.raises(EvidenceError):
        p.validate_authorization(changed, expected_case=c.case_id, expected_nodeid=c.authorization["case"]["nodeid"],
            expected_sources=SOURCES, expected_registry_sha256=SHA, expected_corpus_manifest=c.corpus_manifest_ref, now=NOW)


@pytest.mark.parametrize("changed", [{"minimum_validity_seconds": 14399}, {"worker_epoch": "arbitrary"}])
def test_current_projection_cannot_relax_horizon_or_epoch_schema(changed):
    c = case()
    with pytest.raises(EvidenceError):
        p.current_expected(c.expected | changed)


@pytest.mark.parametrize("report", [
    {"schema_version": "wolf-full-qualification-result-v1", "quality_pass": False, "production_enabled": False},
    {"schema_version": "evolution-classifier-full-qualification-binding-v1"},
    {"schema_version": "evolution-classifier-wolf-qualification-binding-v1"},
    {"schema_version": "synthetic-final-binding-never-operational", "production_enabled": False},
])
def test_initial_wolf_never_reaches_final_worker_adapter(report):
    c = case()
    # Rebuild the external anchor deliberately as a synthetic parent would. The
    # semantic refusal must remain even though every affected byte pin is valid.
    c.authorization["receipts"]["qualification"] = c.store.put("initial.json", report)
    c.preflight["receipts"]["qualification"] = c.authorization["receipts"]["qualification"]
    c.authorization["preflight"] = c.store.put("preflight-new.json", c.preflight)
    c.options["authorization_ref"] = c.store.put("auth-new.json", c.authorization)
    def forbidden(**kwargs):
        pytest.fail("initial evidence must be refused before final adapter")
    with pytest.raises(EvidenceError):
        invoke(c, qualify_final_worker=forbidden)


@pytest.mark.parametrize("label,field,value", [("matrix-release", "state", "disabled"),
    ("matrix-release", "binding_revision", 3), ("restore-original-binding", "previous_revision", 1),
    ("restore-original-binding", "state", "observe"), ("matrix-release", "actor_id", uid(999))])
def test_owned_original_governance_chain_cannot_be_substituted(label, field, value):
    c = case("resource"); files = copy.deepcopy(c.governance_files); name = "release-" + label + ".json"
    row = p.document(c.store.load(files[name])); row["handle"][field] = value
    files[name] = c.store.put(files[name]["path"], row)
    with pytest.raises(EvidenceError):
        p.verify_governance(files, authorization=c.authorization, observation=c.observation, operation=OP, load=c.store.load)


@pytest.mark.parametrize("deadline", [None, True, -1, float("nan"), float("inf")])
def test_runtime_cannot_create_a_new_allowance_from_missing_caller_deadline(deadline):
    c = case(); verifier = p.ProvenanceVerifier(**c.options)
    with pytest.raises(EvidenceError):
        verifier(phase="before", operation=None, binding=c.binding, sources=SOURCES, deadline=deadline)
    assert c.store.calls == []


def test_internally_consistent_same_node_restore_is_not_cold_recovery():
    c = case(source_node=NODE)
    # All seal/proof/restore/access/resume/rollout pins were constructed together.
    # This is a semantic new-node refusal, not a byte or manifest mismatch.
    assert p.verify_offline(c.offline, load=c.store.load)["source_owner"]["container_id"] == c.cold["node_id"]
    with pytest.raises(EvidenceError):
        p.verify_cold(c.cold, authorization=c.authorization, preflight=c.preflight, load=c.store.load)


def checked_corpus_manifest(c, ref=None):
    verifier = p.ProvenanceVerifier(**c.options)
    return p.verify_corpus_manifest(ref or c.corpus_manifest_ref, authorization=c.authorization, load=verifier.load)


def changed_manifest(c, field, value):
    changed = copy.deepcopy(c.corpus_manifest); changed[field] = value
    return c.store.put("mutated-corpus-manifest.json", changed)


@pytest.mark.parametrize("selection_index", [1, 2, 3])
def test_exact_cumulative_corpora_preserve_every_previous_frozen_confirmation(selection_index):
    c = case(selection_index=selection_index)
    assert len(checked_corpus_manifest(c)["corpora"]) == 3 + selection_index
    _, proof = invoke(c)
    assert proof["parent_invocation"] == c.auth_ref["sha256"]
    assert len(c.store.calls) > 3 + selection_index


@pytest.mark.parametrize("field,value", [("selection_index", 0), ("selection_index", 4), ("selection_index", True),
    ("schema_version", "unknown"), ("frozen_at", stamp(1)), ("previous_manifest", {"path": "/foreign", "sha256": SHA}),
    ("baseline_corpora", {}), ("corpora", {}), ("corpora", {"original": {"path": "/foreign", "sha256": SHA}})])
def test_independent_corpus_manifest_rejects_scope_and_history_changes(field, value):
    c = case()
    with pytest.raises(EvidenceError):
        checked_corpus_manifest(c, changed_manifest(c, field, value))


@pytest.mark.parametrize("field", ["baseline_corpora", "corpora"])
def test_original_corpus_pin_cannot_be_replaced_even_with_parent_reanchor(field):
    c = case(); mapping = copy.deepcopy(c.corpus_manifest[field]); mapping["original"]["sha256"] = SHA
    with pytest.raises(EvidenceError):
        checked_corpus_manifest(c, changed_manifest(c, field, mapping))


@pytest.mark.parametrize("index", [2, 3])
@pytest.mark.parametrize("mutation", ["missing", "skip_index", "replaced_prior", "backward_freeze"])
def test_cumulative_history_cannot_skip_or_rewrite_previous_corpus(index, mutation):
    c = case(selection_index=index); value = copy.deepcopy(c.corpus_manifest)
    if mutation == "missing":
        value["previous_manifest"] = None
    elif mutation == "skip_index":
        value["previous_manifest"] = c.store.put("skipped-history.json", value)
    elif mutation == "replaced_prior":
        value["corpora"]["confirmation_1"] = c.store.put("replaced.json", synthetic_corpus("foreign"))
    else:
        value["frozen_at"] = stamp(-500)
    with pytest.raises(EvidenceError):
        checked_corpus_manifest(c, c.store.put("changed-history.json", value))


@pytest.mark.parametrize("field,value", [("status", "qualified"), ("selection_index", True), ("maximum_candidates", 4),
    ("enabled", True), ("model_scored", True), ("model_loaded", True), ("weights_acquired", True),
    ("repository", "foreign/model"), ("revision", "foreign"), ("recorded_at", stamp(1))])
def test_candidate_selection_is_exact_pre_acquisition_authority(field, value):
    c = case(); selected = p.document(c.store.load(c.corpus_manifest["selection_manifest"])); selected[field] = value
    ref = c.store.put("selection-changed.json", selected)
    with pytest.raises(EvidenceError):
        checked_corpus_manifest(c, changed_manifest(c, "selection_manifest", ref))


@pytest.mark.parametrize("key,value", [("cutoff", .8), ("maximum_windows", 17), ("english_each_corpus_fpr_max", .1),
    ("initial_existing_cases", 1200), ("fresh_blind_confirmation_cases", 500), ("zero_operational_errors", False),
    ("retries", 1), ("warmups", True), ("calibration", True)])
def test_selected_candidate_cannot_weaken_frozen_acceptance_limits(key, value):
    c = case(); selected = p.document(c.store.load(c.corpus_manifest["selection_manifest"])); selected["qualification"][key] = value
    ref = c.store.put("selection-limits-changed.json", selected)
    with pytest.raises(EvidenceError):
        checked_corpus_manifest(c, changed_manifest(c, "selection_manifest", ref))


@pytest.mark.parametrize("field,value", [("normalization", "identity"), ("within_corpus_duplicates", True),
    ("counts", {"total": 599}), ("prior_corpus_overlap", {}), ("model_evaluation_performed", True),
    ("status", "passed"), ("limitations", []), ("threshold", .8), ("reviewed_at", stamp(-1000)),
    ("author_freeze", {"path": "/foreign", "sha256": SHA})])
def test_parent_review_projection_cannot_hide_changed_lineage_or_normalization(field, value):
    c = case(); review = p.document(c.store.load(c.corpus_manifest["parent_overlap_review"])); review[field] = value
    ref = c.store.put("changed-overlap.json", review)
    with pytest.raises(EvidenceError):
        checked_corpus_manifest(c, changed_manifest(c, "parent_overlap_review", ref))


@pytest.mark.parametrize("field,value", [("candidate_information_accessed", True), ("evaluation_calls", True),
    ("state", "evaluated"), ("texts_and_labels_immutable", False), ("family_count", 59), ("variants_per_family", 9),
    ("corpus_id", "foreign"), ("hash_algorithm", "MD5"), ("frozen_at_utc", stamp(1))])
def test_genuine_blind_author_shape_cannot_be_claimed_after_evaluation(field, value):
    c = case(); author = p.document(c.store.load(c.corpus_manifest["author_freeze"])); author[field] = value
    ref = c.store.put("author-1/changed-freeze.json", author)
    with pytest.raises(EvidenceError):
        checked_corpus_manifest(c, changed_manifest(c, "author_freeze", ref))


def test_normalized_overlap_is_computed_from_text_not_parent_zero_flag():
    c = case(); row = synthetic_corpus("confirmation_1")
    row["cases"][0]["part"]["text"] = "  SYNTHETIC\u00a0HELDOUT_1 EN TRUE EXAMPLE 0.\n"
    raw = p.canonical(row)
    fresh = p.corpus_projection(raw, "confirmation_1")
    prior = p.corpus_projection(c.store.load(c.corpora["heldout_1"]), "heldout_1")
    assert len(fresh["texts"] & prior["texts"]) == 1
    # Re-anchor all parent/author claims consistently to this changed corpus;
    # only recomputing the raw overlap can reject their false zero declaration.
    key = "confirmation_1"; ref = c.store.put("corpus-" + key + ".json", row)
    manifest = copy.deepcopy(c.corpus_manifest); manifest["corpora"][key] = ref
    author = p.document(c.store.load(manifest["author_freeze"])); author["files"]["corpus-" + key + ".json"] = ref["sha256"]
    author["full_normalized_text_uniqueness"]["ordered_normalized_text_sha256"] = fresh["ordered_sha256"]
    manifest["author_freeze"] = c.store.put("author-1/changed-author.json", author)
    review = p.document(c.store.load(manifest["parent_overlap_review"])); review["confirmation"] = ref; review["author_freeze"] = manifest["author_freeze"]
    manifest["parent_overlap_review"] = c.store.put("changed-review.json", review)
    c.authorization["required_corpora"] = manifest["corpora"]
    with pytest.raises(EvidenceError):
        checked_corpus_manifest(c, c.store.put("overlap-manifest.json", manifest))


@pytest.mark.parametrize("kind", ["confirmation_1", "heldout_1", "heldout_2"])
def test_old_indirect_coverage_is_not_added_to_new_confirmation_contract(kind):
    value = synthetic_corpus(kind)
    for row in value["cases"]:
        row["indirect"] = False
    if kind == "confirmation_1":
        assert p.corpus_projection(p.canonical(value), kind)["families"] == 60
    else:
        with pytest.raises(EvidenceError):
            p.corpus_projection(p.canonical(value), kind)


@pytest.mark.parametrize("mutation", ["duplicate", "class_count", "family_count", "part_kind", "empty_text"])
def test_confirmation_schema_counts_and_normalized_uniqueness_are_recomputed(mutation):
    row = synthetic_corpus("confirmation_1")
    if mutation == "duplicate":
        row["cases"][1]["part"]["text"] = row["cases"][0]["part"]["text"].upper()
    elif mutation == "class_count":
        row["cases"][0]["expected_injection"] = False
    elif mutation == "family_count":
        row["cases"][0]["family_id"] = "extra"
    elif mutation == "part_kind":
        row["cases"][0]["part"]["kind"] = "text"
    else:
        row["cases"][0]["part"]["text"] = "  \n"
    with pytest.raises(EvidenceError):
        p.corpus_projection(p.canonical(row), "confirmation_1")


def test_static_authorization_before_launch_does_not_call_current_or_record_operational_phase():
    c = case()
    def forbidden(**kwargs):
        pytest.fail("static authorization cannot touch a cluster")
    verifier = p.ProvenanceVerifier(**(c.options | {"current": forbidden}))
    result = verifier.authorize()
    assert result["authorization"] == c.authorization and result["preflight"] == c.preflight
    assert verifier.validated_checks == [] and result["proof"]["parent_invocation"] == c.auth_ref["sha256"]
    result["authorization"]["identity"]["model_id"] = "detached"
    assert verifier.authorize()["authorization"]["identity"]["model_id"] == c.identity["model_id"]


def test_static_authorization_rechecks_expiry_after_required_adapter():
    c = case(); moments = iter([NOW, NOW + timedelta(seconds=10001)])
    verifier = p.ProvenanceVerifier(**(c.options | {"now": lambda: next(moments)}))
    with pytest.raises(EvidenceError):
        verifier.authorize()
    assert verifier.validated_checks == []


def test_final_worker_adapter_cannot_mutate_actual_images():
    c = case()
    def mutate(**kwargs):
        kwargs["images"]["classifier"]["oci"] = "foreign"
        return c.final
    with pytest.raises(EvidenceError):
        invoke(c, qualify_final_worker=mutate)


def test_external_corpus_manifest_anchor_cannot_be_replaced_by_auth_bytes():
    c = case(); c.options["expected_corpus_manifest"] = {"path": "/foreign/manifest", "sha256": SHA}
    with pytest.raises(EvidenceError):
        invoke(c)


def test_node_boundary_receipt_must_be_the_exact_current_native_snapshot():
    c = case(); c.preflight["images"]["node_boundary_snapshot"] = {"synthetic": "foreign"}
    c.authorization["preflight"] = c.store.put("preflight-changed.json", c.preflight)
    c.options["authorization_ref"] = c.store.put("auth-changed.json", c.authorization)
    # This changed projection invalidates rollout too; directly preserve its
    # authorized image digest to isolate the native receipt equality refusal.
    cold = p.document(c.store.load(c.authorization["receipts"]["cold"]))
    rollout = p.document(c.store.load(cold["rollout"])); rollout["images_sha256"] = p.digest(c.preflight["images"])
    cold["rollout"] = c.store.put("rollout-changed.json", rollout)
    cold_ref = c.store.put("cold-changed.json", cold)
    c.authorization["receipts"]["cold"] = c.preflight["receipts"]["cold"] = cold_ref
    c.authorization["preflight"] = c.store.put("preflight-changed-again.json", c.preflight)
    c.options["authorization_ref"] = c.store.put("auth-changed-again.json", c.authorization)
    with pytest.raises(EvidenceError):
        invoke(c)


@pytest.mark.parametrize("index", [1, 2, 3])
@pytest.mark.parametrize("counts", [{"tp": 179, "fn": 21, "tn": 200, "fp": 0, "errors": 0},
    {"tp": 200, "fn": 0, "tn": 189, "fp": 11, "errors": 0},
    {"tp": 200, "fn": 0, "tn": 200, "fp": 0, "errors": 1}])
def test_each_new_confirmation_matrix_keeps_original_quality_and_error_limits(index, counts):
    c = case(selection_index=3); changed = copy.deepcopy(c.final)
    changed["matrices"][f"confirmation_{index}"]["en"] = counts
    with pytest.raises(EvidenceError):
        p.verify_final_worker(changed, authorization=c.authorization, preflight=c.preflight)


@pytest.mark.parametrize("field,value", [("candidate_model_identity_access", True), ("candidate_model_configuration_access", True),
    ("candidate_model_result_access", True), ("prior_corpus_example_content_access", True), ("model_inference_calls_for_evaluation", 1),
    ("schema_shape_only_access", False), ("schema_shape_source", "/foreign/corpus"), ("prior_corpus_access", False)])
def test_truthful_schema_only_author_access_is_required_without_model_or_example_access(field, value):
    c = case(); key = "confirmation_1"; projection = p.corpus_projection(c.store.load(c.corpora[key]), key)
    projection["provenance"][field] = value
    with pytest.raises(EvidenceError):
        p.verify_confirmation_author(p.document(c.store.load(c.corpus_manifest["author_freeze"])), corpus_ref=c.corpora[key],
            author_ref=c.corpus_manifest["author_freeze"], projection=projection, prior=c.corpus_manifest["baseline_corpora"],
            frozen_at=c.corpus_manifest["frozen_at"], load=p.ProvenanceVerifier(**c.options).load)


def test_author_source_changed_after_freeze_is_not_ignored():
    c = case(); author_root = c.corpus_manifest["author_freeze"]["path"].rsplit("/", 1)[0]
    c.store.raws[author_root + "/AUTHORSHIP.md"] += b" changed"
    with pytest.raises(EvidenceError):
        checked_corpus_manifest(c)


def test_no_final_worker_adapter_means_no_static_launch_authorization():
    c = case()
    def unavailable(**kwargs):
        raise EvidenceError("genuine_final_worker_adapter_unavailable")
    verifier = p.ProvenanceVerifier(**(c.options | {"qualify_final_worker": unavailable}))
    with pytest.raises(EvidenceError, match="genuine_final_worker_adapter_unavailable"):
        verifier.authorize()
    assert verifier.validated_checks == []
