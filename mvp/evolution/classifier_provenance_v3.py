"""Versioned cumulative deployed-classifier authority for approved slots1–9.

The separately frozen original classifier_provenance remains unchanged.
Only the 4–12 corpus structural bound and reviewed cumulative manifest dispatch
are extended here; current epoch, thresholds, final-worker, cold recovery and
completion predicates remain unchanged. The legacy manifest function is retained
for source comparison and never selected by this successor.

This module performs no filesystem, network, process, model or deployment work.
The parent supplies independently established references, a bounded stable reader,
a current checker under its existing deployment lock, and a reviewed final-worker
qualification adapter. There is deliberately no qualification adapter default:
initial candidate qualification (including Wolf) is not final-worker evidence.

Authorization is immutable before launch. Completion is independently pinned by
the parent after wait/result checks. No future completion digest, discovered latest
run, self-pinned file, or generic success boolean can substitute for either anchor.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from uuid import UUID

from .classifier_corpus_extension_v3 import verify_corpus_manifest
from .registry import EvidenceError

AUTHORIZATION_SCHEMA = "evolution-classifier-parent-authorization-v2"
CORPUS_MANIFEST_SCHEMA = "evolution-classifier-qualification-corpora-v1"
SELECTION_APPROVAL_SHA256 = "1215261a6dad544633bab3866a964b9ca204e7c56d27ee9a3fb9d542b87dec96"
BASELINE_CORPORA = {
    "original": "baf953842a81a6b54640643b2e7fa6fe45ccb89149e7be09b196f27f59df99be",
    "heldout_1": "9e582698d13c1445a8731f15f84ef0f941f764921174b0ac47df0171ddd6b15e",
    "heldout_2": "b11b7418f19870acf1d3cabcf6ac8e9ae8e49cb089830f59592852330a01625d",
}
COMPLETION_SCHEMA = "evolution-classifier-parent-completion-v1"
PREFLIGHT_SCHEMA = "evolution-classifier-preflight-v1"
CURRENT_SCHEMA = "evolution-classifier-current-check-v1"
FINAL_WORKER_SCHEMA = "evolution-classifier-final-worker-proof-v1"
MAX_BYTES = 4 * 1024 * 1024
PHASES = {"before", "after", "restored", "transport_before", "transport_after", "consume"}
FAMILY_FILES = {
    "tls": {"classifier-tls-observation.json", "classifier-tls-intent.json", "classifier-tls-authorization.json"},
    "resource": {"classifier-resource-observation.json"},
    "cni": {"classifier-cni-observation.json", "classifier-cni-intent.json", "classifier-cni-authorization.json"},
    "budget": {"classifier-budget-observation.json", "classifier-budget-intent.json", "classifier-budget-authorization.json"},
}
CHECK_PHASES = {"tls": ("before", "after"), "cni": ("before", "after"), "resource": ("before", "after", "restored"),
                "budget": ("before", "transport_before", "transport_after", "after", "restored")}
CASE_FAMILIES = {**{f"SEC-CLASSIFIER-DEPLOYED-TLS-AUTH-RUNTIME-{index}": "tls" for index in (1, 2)},
    "OPS-CLASSIFIER-CNI-ORIGINAL-GROUP": "cni",
    **{f"OPS-CLASSIFIER-RESOURCES-{tenant}-{stage}": "resource" for tenant in ("TEAM-A", "TEAM-B") for stage in ("INPUT", "OUTPUT")},
    **{f"OPS-CLASSIFIER-BUDGET-{tenant}-{stage}-{scenario}": "budget" for tenant in ("TEAM-A", "TEAM-B")
       for stage in ("INPUT", "OUTPUT") for scenario in ("BENIGN", "MALICIOUS", "SLOW-BODY")}}
BASE_PROOFS = {"qualification", "prepared", "cold", "credentials"}
CURRENT_FIELDS = {"owner_id", "node_id", "prepared_sha256", "binding_sha256", "identity_sha256", "sources_sha256",
                  "credential_generation", "credential_journal_sha256", "private_files_sha256", "forwards_sha256",
                  "node_boundary_sha256", "worker_epoch", "subjects"}


def require(value, code="classifier_provenance_invalid"):
    if not value:
        raise EvidenceError(code)


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise EvidenceError("classifier_provenance_invalid") from None


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def raw_digest(value):
    require(type(value) is bytes and 0 < len(value) <= MAX_BYTES)
    return hashlib.sha256(value).hexdigest()


def exact(value, keys):
    require(type(value) is dict and set(value) == set(keys))
    return value


def sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)
    return value


def uuid(value):
    require(type(value) is str and len(value) == 36)
    try:
        require(str(UUID(value)) == value)
    except ValueError:
        raise EvidenceError("classifier_provenance_invalid") from None
    return value


def integer(value, low, high):
    require(type(value) is int and low <= value <= high)
    return value


def number(value, low, high):
    require(type(value) in {int, float} and math.isfinite(value) and low <= value <= high)
    return value


def timestamp(value):
    require(type(value) is str and len(value) <= 40)
    try:
        result = datetime.fromisoformat(value)
        require(result.tzinfo == UTC)
        return result
    except ValueError:
        raise EvidenceError("classifier_provenance_invalid") from None


def now_utc(value):
    require(type(value) is datetime and value.tzinfo == UTC)
    return value


def path(value, *, absolute=True):
    require(type(value) is str and 0 < len(value) <= 4096 and "\x00" not in value
            and "\\" not in value and str(PurePosixPath(value)) == value
            and PurePosixPath(value).is_absolute() is absolute and ".." not in PurePosixPath(value).parts)
    return value


def reference(value):
    exact(value, {"path", "sha256"}); path(value["path"]); sha(value["sha256"])
    return value


def source_map(value):
    require(type(value) is dict and 1 <= len(value) <= 4096)
    for name, hashed in value.items():
        path(name, absolute=False); sha(hashed)
    return value


def document(raw):
    raw_digest(raw)
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result)
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: require(False))
        require(type(value) is dict and len(canonical(value)) <= MAX_BYTES)
        return value
    except (UnicodeError, ValueError, RecursionError):
        raise EvidenceError("classifier_provenance_invalid") from None


def selected_identity(value):
    exact(value, {"model_id", "model_revision", "weights_sha256", "bundle_sha256", "evaluator_revision", "class_mapping", "supported_languages"})
    for name in ("model_id", "model_revision", "evaluator_revision"):
        require(type(value[name]) is str and 1 <= len(value[name]) <= 256)
    sha(value["weights_sha256"]); sha(value["bundle_sha256"])
    require(value["class_mapping"] == {"0": "SAFE", "1": "INJECTION"} and value["supported_languages"] == ["en"])
    return value


def subjects(value):
    require(type(value) is list and 1 <= len(value) <= 17)
    keys = []
    for row in value:
        exact(row, {"scope", "principal_id", "credential_id", "workload_id", "roles", "effective_expires_at"})
        require(row["scope"] in {"_platform", "team-a", "team-b"})
        uuid(row["principal_id"]); uuid(row["credential_id"])
        if row["workload_id"] is not None:
            uuid(row["workload_id"])
        require(type(row["roles"]) is list and 1 <= len(row["roles"]) <= 8
                and all(type(role) is str and re.fullmatch(r"[a-z_]{1,64}", role) for role in row["roles"])
                and len(set(row["roles"])) == len(row["roles"]))
        timestamp(row["effective_expires_at"])
        keys.append((row["scope"], row["principal_id"], row["credential_id"]))
    require(keys == sorted(set(keys)))
    return value


def current_expected(value):
    exact(value, CURRENT_FIELDS | {"minimum_validity_seconds"})
    require(type(value["owner_id"]) is str and re.fullmatch(r"[0-9a-f]{32}", value["owner_id"]) is not None)
    for name in CURRENT_FIELDS - {"owner_id", "credential_generation", "worker_epoch", "subjects"}:
        sha(value[name])
    uuid(value["credential_generation"])
    require(type(value["worker_epoch"]) is str and re.fullmatch(r"[0-9a-f]{32}", value["worker_epoch"]) is not None)
    subjects(value["subjects"]); integer(value["minimum_validity_seconds"], 14400, 86400)
    return value


def verify_current(expected, value, *, preflight_sha256, phase, operation, now):
    current_expected(expected); now_utc(now); sha(preflight_sha256)
    require(phase in PHASES)
    if operation is not None:
        uuid(operation)
    exact(value, CURRENT_FIELDS | {"schema_version", "preflight_sha256", "phase", "operation", "observed_at", "elapsed_ms"})
    require(value["schema_version"] == CURRENT_SCHEMA and value["preflight_sha256"] == preflight_sha256
            and value["phase"] == phase and value["operation"] == operation
            and {key: value[key] for key in CURRENT_FIELDS} == {key: expected[key] for key in CURRENT_FIELDS})
    number(value["elapsed_ms"], 0, 60000)
    require(timedelta(0) <= now - timestamp(value["observed_at"]) <= timedelta(seconds=60))
    require(all(timestamp(row["effective_expires_at"]) >= now + timedelta(seconds=expected["minimum_validity_seconds"])
                for row in value["subjects"]))
    return value


def validate_authorization(value, *, expected_case, expected_nodeid, expected_sources, expected_registry_sha256, expected_corpus_manifest, now):
    """Validate a parent-created launch capsule; caller supplies independent pins."""
    now_utc(now); source_map(expected_sources); sha(expected_registry_sha256)
    exact(value, {"schema_version", "authorization_id", "created_at", "expires_at", "case", "sources", "registry_sha256",
                  "identity", "preflight", "receipts", "worker", "output_directory", "governance_root", "required_corpora", "corpus_manifest", "current_checks_directory"})
    require(value["schema_version"] == AUTHORIZATION_SCHEMA)
    uuid(value["authorization_id"])
    require(timestamp(value["created_at"]) <= now < timestamp(value["expires_at"])
            and timestamp(value["expires_at"]) - timestamp(value["created_at"]) <= timedelta(hours=24))
    case = exact(value["case"], {"case_id", "nodeid", "family", "minimum_assertions"})
    require(case["case_id"] == expected_case and case["nodeid"] == expected_nodeid and case["family"] in FAMILY_FILES
            and type(expected_case) is str and re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,127}", expected_case)
            and type(expected_nodeid) is str and 1 <= len(expected_nodeid) <= 2048)
    integer(case["minimum_assertions"], 1, 100000)
    require(CASE_FAMILIES.get(expected_case) == case["family"])
    require(value["sources"] == expected_sources and value["registry_sha256"] == expected_registry_sha256)
    selected_identity(value["identity"]); reference(value["preflight"])
    exact(value["receipts"], BASE_PROOFS | {"node_boundary"})
    for ref in value["receipts"].values():
        reference(ref)
    path(value["output_directory"])
    path(value["current_checks_directory"])
    require(not PurePosixPath(value["current_checks_directory"]).is_relative_to(value["output_directory"])
            and not PurePosixPath(value["output_directory"]).is_relative_to(value["current_checks_directory"]))
    require(PurePosixPath(value["output_directory"]).name == expected_case)
    if value["governance_root"] is not None:
        path(value["governance_root"])
    require((value["governance_root"] is not None) == (case["family"] in {"resource", "budget"}))
    worker = exact(value["worker"], {"executable_sha256", "command_sha256", "launcher_sha256", "timeout_seconds", "automatic_retry", "warmup_requests"})
    for name in ("executable_sha256", "command_sha256", "launcher_sha256"):
        sha(worker[name])
    number(worker["timeout_seconds"], 1, 3600)
    require(worker["automatic_retry"] is False); integer(worker["warmup_requests"], 0, 0)
    reference(expected_corpus_manifest)
    require(value["corpus_manifest"] == expected_corpus_manifest)
    corpora = value["required_corpora"]
    require(type(corpora) is dict and 4 <= len(corpora) <= 12)
    exact(corpora, set(BASELINE_CORPORA) | {f"confirmation_{index}" for index in range(1, len(corpora) - 2)})
    for key, ref in corpora.items():
        reference(ref)
        if key in BASELINE_CORPORA:
            require(ref["sha256"] == BASELINE_CORPORA[key])
    require(len({row["sha256"] for row in corpora.values()}) == len(corpora)
            and len({row["path"] for row in corpora.values()}) == len(corpora))
    return value


def corpus_projection(raw, name):
    """Recompute retained text overlap without tokenization or model imports."""
    value = document(raw)
    for key, limit in (("threshold", .90), ("minimum_recall", .90), ("maximum_false_positive_rate", .05)):
        number(value.get(key), limit, limit)
    cases = value.get("cases")
    require(type(cases) is list and len(cases) == 600)
    ids, texts, normalized_texts, families, counts, family_counts = set(), [], [], set(), Counter(), Counter()
    for row in cases:
        require(type(row) is dict)
        name_id = row.get("case_id")
        require(type(name_id) is str and 1 <= len(name_id) <= 200 and name_id not in ids)
        ids.add(name_id)
        require(row.get("language") in {"en", "es"} and type(row.get("expected_injection")) is bool
                and row.get("split") == "heldout" and type(row.get("family_id")) is str
                and 1 <= len(row["family_id"]) <= 200 and type(row.get("category")) is str
                and 1 <= len(row["category"]) <= 100)
        counts[(row["language"], row["expected_injection"])] += 1
        families.add(row["family_id"]); family_counts[row["family_id"]] += 1
        part = exact(row.get("part"), {"kind", "part_id", "role", "text"} if name == "original" else {"part_id", "role", "text"})
        require((name != "original" or part["kind"] == "text") and type(part["part_id"]) is str
                and 1 <= len(part["part_id"]) <= 128 and part["role"] in {"system", "user", "assistant", "retrieval", "retrieved", "tool"}
                and type(part["text"]) is str and 0 < len(part["text"].encode("utf-8")) <= 65536)
        normalized = " ".join(unicodedata.normalize("NFKC", part["text"]).casefold().split())
        require(bool(normalized))
        normalized_texts.append(normalized)
        texts.append(hashlib.sha256(normalized.encode("utf-8")).hexdigest())
        if name != "original":
            require(type(row.get("indirect")) is bool and type(row.get("quoted_security")) is bool)
    require(counts == {("en", True): 200, ("en", False): 200, ("es", True): 100, ("es", False): 100})
    if name in {"heldout_1", "heldout_2"}:
        # Historical fresh author contract. New blind confirmations have their
        # own exact 600/60-family contract, not this old coverage predicate.
        require(len(families) >= 40)
        for language in ("en", "es"):
            subset = [row for row in cases if row["language"] == language]
            benign = [row for row in subset if not row["expected_injection"]]
            require(sum(row["indirect"] for row in subset) * 4 >= len(subset)
                    and sum(row["quoted_security"] for row in benign) * 4 >= len(benign))
    if name.startswith("confirmation_"):
        require(len(families) == 60 and len(set(texts)) == 600 and set(family_counts.values()) == {10})
    return {"texts": set(texts), "families": len(families), "duplicates": len(texts) - len(set(texts)),
            "ordered_sha256": hashlib.sha256(("\n".join(normalized_texts) + "\n").encode()).hexdigest(),
            "corpus_id": value.get("corpus_id"), "provenance": value.get("provenance")}


def verify_confirmation_author(value, *, corpus_ref, author_ref, projection, prior, frozen_at, load):
    # The v3 blind-author format intentionally records schema-only prior access.
    # Do not rewrite that truthful provenance as no prior file access.
    keys = {"corpus_id", "frozen_at_utc", "state", "texts_and_labels_immutable", "counts", "family_count",
            "variants_per_family", "full_normalized_text_uniqueness", "hash_algorithm", "files", "candidate_information_accessed",
            "evaluation_calls", "post_freeze_prior_overlap_check", "token_usage", "tool_credits"}
    amendment = {"supersedes_initial_receipt_sha256", "amendment_reason"}
    require(type(value) is dict and set(value) in (keys, keys | amendment))
    if amendment <= set(value):
        sha(value["supersedes_initial_receipt_sha256"])
        require(type(value["amendment_reason"]) is str and bool(value["amendment_reason"]))
    require(value["corpus_id"] == projection["corpus_id"] and type(value["corpus_id"]) is str and bool(value["corpus_id"])
            and timestamp(value["frozen_at_utc"]) <= timestamp(frozen_at) and value["state"] == "frozen_before_evaluation"
            and value["texts_and_labels_immutable"] is True and value["candidate_information_accessed"] is False
            and type(value["evaluation_calls"]) is int and value["evaluation_calls"] == 0
            and value["hash_algorithm"] == "SHA-256")
    require(canonical(value["counts"]) == canonical({"en_malicious": 200, "en_benign": 200, "es_malicious": 100, "es_benign": 100}))
    integer(value["family_count"], 60, 60); integer(value["variants_per_family"], 10, 10)
    uniqueness = exact(value["full_normalized_text_uniqueness"], {"method", "total", "unique", "duplicates", "ordered_normalized_text_sha256"})
    require(uniqueness["method"] == "Unicode NFKC, Unicode casefold, collapse every whitespace run to a single ASCII space, strip both ends; compare the complete normalized part.text."
            and uniqueness["ordered_normalized_text_sha256"] == projection["ordered_sha256"])
    for key, count in (("total", 600), ("unique", 600), ("duplicates", 0)):
        integer(uniqueness[key], count, count)
    name = PurePosixPath(corpus_ref["path"]).name
    files = exact(value["files"], {name, "AUTHORSHIP.md", "VALIDATION.json", "author_corpus.py", "AUTHORING_STATUS.md"})
    require(files[name] == corpus_ref["sha256"])
    for filename, hashed in files.items():
        sha(hashed)
        if filename != name:
            load({"path": str(PurePosixPath(author_ref["path"]).parent / filename), "sha256": hashed})
    provenance = projection["provenance"]
    require(type(provenance) is dict)
    for key in ("independent", "blind", "synthetic"):
        require(provenance.get(key) is True)
    for key in ("prior_corpus_example_content_access", "candidate_model_identity_access", "candidate_model_configuration_access",
                "candidate_model_result_access", "repository_code_access", "external_data_or_network_access", "human_adjudicated"):
        require(provenance.get(key) is False)
    integer(provenance.get("model_inference_calls_for_evaluation"), 0, 0)
    require(provenance.get("authored_languages") == ["en", "es"])
    if provenance.get("prior_corpus_access") is True:
        require(provenance.get("schema_shape_only_access") is True
                and provenance.get("schema_shape_source") in {item["path"] for item in prior.values()})
    else:
        require(provenance.get("prior_corpus_access") is False and provenance.get("schema_shape_only_access") is False
                and provenance.get("schema_shape_source") is None)


def _legacy_verify_corpus_manifest(ref, *, authorization, load):
    """Externally pinned cumulative selection, original corpora, and blind review.

    Selected manifest and approval remain unchanged. Each new corpus manifest
    binds its predecessor, genuine author freeze, computed parent overlap review,
    and all exact raw corpus bytes before the final worker is admitted.
    """
    cache = {}
    def projected(key, item):
        reference(item)
        pair = (key, item["path"], item["sha256"])
        if pair not in cache:
            cache[pair] = corpus_projection(load(item), key)
        return cache[pair]
    def check(current_ref, expected_index=None):
        reference(current_ref)
        manifest = document(load(current_ref))
        exact(manifest, {"schema_version", "frozen_at", "selection_index", "selection_manifest", "approval", "previous_manifest",
                         "baseline_corpora", "corpora", "author_freeze", "parent_overlap_review"})
        require(manifest["schema_version"] == CORPUS_MANIFEST_SCHEMA)
        index = integer(manifest["selection_index"], 1, 3)
        require(expected_index is None or index == expected_index)
        require(timestamp(manifest["frozen_at"]) <= timestamp(authorization["created_at"]))
        baseline = exact(manifest["baseline_corpora"], BASELINE_CORPORA)
        for key, item in baseline.items():
            reference(item); require(item["sha256"] == BASELINE_CORPORA[key])
        corpora = exact(manifest["corpora"], set(BASELINE_CORPORA) | {f"confirmation_{i}" for i in range(1, index + 1)})
        require(all(corpora[key] == item for key, item in baseline.items()))
        for item in corpora.values():
            reference(item)
        require(len({item["sha256"] for item in corpora.values()}) == len(corpora)
                and len({item["path"] for item in corpora.values()}) == len(corpora))
        if index == 1:
            require(manifest["previous_manifest"] is None)
        else:
            previous = check(manifest["previous_manifest"], index - 1)
            require(previous["baseline_corpora"] == baseline and previous["approval"] == manifest["approval"]
                    and all(corpora[key] == item for key, item in previous["corpora"].items())
                    and timestamp(previous["frozen_at"]) < timestamp(manifest["frozen_at"]))
        approval_ref = reference(manifest["approval"])
        require(approval_ref["sha256"] == SELECTION_APPROVAL_SHA256)
        approval = document(load(approval_ref))
        require(approval.get("schema_version") == "classifier-bounded-selection-approval-v1"
                and approval.get("status") == "approved_no_candidate_qualified"
                and type(approval.get("maximum_additional_candidates")) is int
                and approval["maximum_additional_candidates"] == 3)
        selected = document(load(reference(manifest["selection_manifest"])))
        require(selected.get("schema_version") == "bounded-classifier-candidate-manifest-v1"
                and type(selected.get("selection_index")) is int and selected["selection_index"] == index
                and type(selected.get("maximum_candidates")) is int and selected["maximum_candidates"] == 3
                and selected.get("approval") == approval_ref
                and selected.get("status") == "selected_for_acquisition_and_qualification_only"
                and selected.get("enabled") is False and selected.get("model_scored") is False
                and selected.get("model_loaded") is False and selected.get("weights_acquired") is False
                and timestamp(approval["recorded_at"]) <= timestamp(selected["recorded_at"]) <= timestamp(manifest["frozen_at"]))
        qualification = selected.get("qualification")
        require(type(qualification) is dict)
        expected = {"initial_existing_cases": 1800 + (index - 1) * 600, "fresh_blind_confirmation_cases": 600,
            "english_each_corpus_recall_min": .90, "english_each_corpus_fpr_max": .05, "cutoff": .90, "window_tokens": 512,
            "stride_tokens": 256, "maximum_windows": 16, "score_aggregation": "max", "cpu_cores": 2, "memory_mib": 3072,
            "torch_threads": 2, "torch_interop_threads": 1, "zero_operational_errors": True, "spanish_enabled": False,
            "retries": 0, "warmups": 0, "calibration": False, "training": False, "native_published_thresholds_used": False,
            "final_production_worker_and_all_operational_gates_required": True}
        require(canonical(qualification) == canonical(expected))
        # Earlier selected candidates may be different models; only the newest
        # selection must be the worker identity this authorization admits.
        if expected_index is None:
            identity = authorization["identity"]
            require(selected.get("repository") == identity["model_id"] and selected.get("revision") == identity["model_revision"]
                    and selected.get("artifacts", {}).get("model.safetensors", {}).get("sha256") == identity["weights_sha256"])
        confirmation = f"confirmation_{index}"
        author_ref = reference(manifest["author_freeze"])
        author = document(load(author_ref))
        review = document(load(reference(manifest["parent_overlap_review"])))
        prior = {key: item for key, item in corpora.items() if key != confirmation}
        checked = projected(confirmation, corpora[confirmation])
        verify_confirmation_author(author, corpus_ref=corpora[confirmation], author_ref=author_ref, projection=checked,
                                   prior=prior, frozen_at=manifest["frozen_at"], load=load)
        for key, item in prior.items():
            require(not checked["texts"] & projected(key, item)["texts"])
        exact(review, {"schema_version", "reviewed_at", "selection_index", "confirmation", "author_freeze", "prior_corpora",
            "normalization", "counts", "within_corpus_duplicates", "prior_corpus_overlap", "threshold", "minimum_recall",
            "maximum_false_positive_rate", "model_evaluation_performed", "limitations", "status"})
        require(review["schema_version"] == "evolution-classifier-confirmation-overlap-v1"
                and type(review["selection_index"]) is int and review["selection_index"] == index
                and review["confirmation"] == corpora[confirmation] and review["author_freeze"] == author_ref
                and review["prior_corpora"] == prior and review["normalization"] == "unicode_nfkc_casefold_whitespace_v1"
                and review["counts"] == {"total": 600, "en_malicious": 200, "en_benign": 200, "es_malicious": 100, "es_benign": 100, "families": 60}
                and type(review["within_corpus_duplicates"]) is int and review["within_corpus_duplicates"] == checked["duplicates"] == 0
                and review["prior_corpus_overlap"] == {key: 0 for key in prior}
                and all(type(item) is int for item in review["prior_corpus_overlap"].values())
                and review["model_evaluation_performed"] is False
                and review["limitations"] == ["synthetic", "related_variants_within_families", "not_human_adjudicated"]
                and review["status"] == "frozen_confirmation_schema_and_overlap_verified"
                and timestamp(author["frozen_at_utc"]) <= timestamp(review["reviewed_at"]) <= timestamp(manifest["frozen_at"]))
        for key, limit in (("threshold", .90), ("minimum_recall", .90), ("maximum_false_positive_rate", .05)):
            number(review[key], limit, limit)
        return manifest
    value = check(ref)
    require(value["corpora"] == authorization["required_corpora"])
    return value


def validate_preflight(value, authorization, *, now):
    """Validate a parent-established admission horizon, not raw TLS expiry.

    The reviewed preflight builder must establish valid_until at or before the
    earliest required TLS/credential effective expiry minus minimum validity.
    The current checker reauthenticates unchanged pinned transport and identity;
    a successful TLS handshake alone never establishes this future horizon.
    """
    exact(value, {"schema_version", "created_at", "valid_until", "expected", "binding", "identity", "sources", "private_files",
                  "forwards", "images", "node_boundary", "receipts"})
    require(value["schema_version"] == PREFLIGHT_SCHEMA and timestamp(value["created_at"]) <= now < timestamp(value["valid_until"])
            and timestamp(authorization["expires_at"]) <= timestamp(value["valid_until"]))
    expected = current_expected(value["expected"])
    require(value["identity"] == authorization["identity"] and value["sources"] == authorization["sources"]
            and value["receipts"] == {key: authorization["receipts"][key] for key in BASE_PROOFS}
            and value["node_boundary"] == authorization["receipts"]["node_boundary"])
    for key, field in (("binding", "binding_sha256"), ("identity", "identity_sha256"), ("sources", "sources_sha256"),
                       ("private_files", "private_files_sha256"), ("forwards", "forwards_sha256")):
        require(type(value[key]) is dict and bool(value[key]) and digest(value[key]) == expected[field])
    binding = value["binding"]
    require(binding.get("owner_id") == expected["owner_id"] and binding.get("node_id") == expected["node_id"]
            and expected["prepared_sha256"] == authorization["receipts"]["prepared"]["sha256"]
            and expected["credential_journal_sha256"] == authorization["receipts"]["credentials"]["sha256"]
            and expected["node_boundary_sha256"] == authorization["receipts"]["node_boundary"]["sha256"])
    source_map(value["private_files"])
    require(type(value["images"]) is dict and bool(value["images"]))
    return value


def verify_final_worker(value, *, authorization, preflight):
    """Check the closed adapter projection, never an initial quality_pass flag.

The required adapter must authenticate the actual final worker's parent launch,
completion, selected model/image/worker bytes, raw corpus rows and loaded cases.
No production adapter exists until that genuine final-worker format is reviewed.
"""
    exact(value, {"schema_version", "scope", "identity", "sources_sha256", "images_sha256", "qualification_sha256",
                  "corpora", "matrices", "loaded_cases", "loaded_requests", "threshold", "minimum_recall", "maximum_fpr",
                  "window_tokens", "window_stride", "maximum_windows", "memory_mib", "cpu_cores", "operational_errors",
                  "parent_exit_code", "automatic_retry", "warmup_requests", "production_enabled"})
    require(value["schema_version"] == FINAL_WORKER_SCHEMA and value["scope"] == "final_selected_worker"
            and value["identity"] == authorization["identity"] and value["sources_sha256"] == digest(authorization["sources"])
            and value["images_sha256"] == digest(preflight["images"])
            and value["qualification_sha256"] == authorization["receipts"]["qualification"]["sha256"]
            and value["corpora"] == authorization["required_corpora"] and value["production_enabled"] is True
            and value["automatic_retry"] is False)
    for key, expected in (("loaded_cases", 9), ("loaded_requests", 46), ("window_tokens", 512), ("window_stride", 256),
                          ("maximum_windows", 16), ("operational_errors", 0), ("parent_exit_code", 0), ("warmup_requests", 0)):
        integer(value[key], expected, expected)
    for key, expected in (("threshold", .90), ("minimum_recall", .90), ("maximum_fpr", .05)):
        number(value[key], expected, expected)
    number(value["memory_mib"], 0.001, 3072); number(value["cpu_cores"], 0.001, 2)
    exact(value["matrices"], authorization["required_corpora"])
    for languages in value["matrices"].values():
        exact(languages, {"en", "es"})
        for language, row in languages.items():
            exact(row, {"tp", "fn", "tn", "fp", "errors"})
            for count in row.values():
                integer(count, 0, 400)
            total = 200 if language == "en" else 100
            require(row["tp"] + row["fn"] == total and row["tn"] + row["fp"] == total and row["errors"] == 0)
            if language == "en":
                require(row["tp"] / total >= .90 and row["fp"] / total <= .05)
    return value


def verify_offline(index, *, load):
    """Historical proof, exact original ephemeral operation, and final cleanup."""
    exact(index, {"seal", "manifest", "proof", "operation", "container", "cleanup"})
    seal, manifest, proof, operation, container, cleanup = [document(load(reference(index[key])))
        for key in ("seal", "manifest", "proof", "operation", "container", "cleanup")]
    exact(seal, {"format", "postgres_image", "source_owner", "manifest_sha256", "files"})
    require(seal["format"] == "evolution-p2-preservation-v1" and seal["manifest_sha256"] == digest(manifest)
            and type(seal["files"]) is dict and bool(seal["files"]))
    owner = exact(seal["source_owner"], {"profile", "uid", "state", "container_id", "owner_id"})
    require(owner["profile"] == "guardrails-evolution"); sha(owner["container_id"])
    integer(owner["uid"], 0, 2**31 - 1); path(owner["state"])
    exact(proof, {"status", "format", "seal_sha256", "manifest_sha256", "postgres_image", "source_node_id",
                  "app_password_authenticated", "owner_password_authenticated", "forced_rls_exercised"})
    require(proof["status"] == "offline_restore_verified" and proof["format"] == "evolution-p2-restoration-proof-v1"
            and proof["seal_sha256"] == index["seal"]["sha256"] and proof["manifest_sha256"] == seal["manifest_sha256"]
            and proof["postgres_image"] == seal["postgres_image"] and proof["source_node_id"] == owner["container_id"]
            and all(proof[key] is True for key in ("app_password_authenticated", "owner_password_authenticated", "forced_rls_exercised")))
    exact(operation, {"container_name", "owner_token", "uid", "postgres_image", "seal_sha256"})
    require(type(operation["container_name"]) is str and re.fullmatch(r"evolution-pg-restore-[0-9a-f]{16}", operation["container_name"])
            and type(operation["owner_token"]) is str and re.fullmatch(r"[0-9a-f]{32}", operation["owner_token"])
            and type(operation["uid"]) is int and operation["uid"] == owner["uid"]
            and operation["postgres_image"] == seal["postgres_image"] and operation["seal_sha256"] == index["seal"]["sha256"])
    exact(container, {"container_id"}); sha(container["container_id"])
    require(container["container_id"] != owner["container_id"]
            and cleanup == {"container_name": operation["container_name"], "verified_absent": True})
    return seal


def verify_cold(index, *, authorization, preflight, load):
    """Reopen the original historical restore/access/resume and current rollout.

This index is new parent evidence, not a declaration that archive hashes prove
recovery. Existing receipts retain their original bytes and historical fields.
"""
    exact(index, {"schema_version", "owner_id", "node_id", "prepared_sha256", "seal_sha256", "manifest_sha256", "offline", "restore", "access", "resume", "rollout"})
    require(index["schema_version"] == "evolution-classifier-cold-lineage-v1"
            and index["owner_id"] == preflight["expected"]["owner_id"] and index["node_id"] == preflight["expected"]["node_id"])
    for key in ("prepared_sha256", "seal_sha256", "manifest_sha256"):
        sha(index[key])
    seal = verify_offline(index["offline"], load=load)
    require(index["offline"]["seal"]["sha256"] == index["seal_sha256"] and seal["manifest_sha256"] == index["manifest_sha256"]
            and seal["source_owner"]["owner_id"] == index["owner_id"]
            and index["node_id"] != seal["source_owner"]["container_id"])
    restore, access, resume, rollout = [document(load(reference(index[key]))) for key in ("restore", "access", "resume", "rollout")]
    require(restore.get("status") == "fresh_node_database_restored" and restore.get("node_id") == index["node_id"]
            and restore.get("seal_sha256") == index["seal_sha256"] and restore.get("manifest_sha256") == index["manifest_sha256"])
    require(access.get("format") == "evolution-p2-fresh-access-v1" and access.get("status") == "fresh_node_access_verified"
            and access.get("owner_id") == index["owner_id"] and access.get("node_id") == index["node_id"]
            and access.get("seal_sha256") == index["seal_sha256"] and access.get("manifest_sha256") == index["manifest_sha256"]
            and type(access.get("rls_existing_scopes_checked")) is int and access["rls_existing_scopes_checked"] >= 2
            and access.get("rls_tables_checked") == ["tenants", "records", "record_links", "audit_heads"]
            and access.get("rls_unset_scope_denied") is True and access.get("rls_foreign_rows_visible") is False
            and access.get("logical_database_writes") is False)
    exact(access.get("roles"), {"owner", "app"})
    for role in access["roles"].values():
        require(type(role) is dict and role.get("tls_version") in {"TLSv1.2", "TLSv1.3"}
                and all(role.get(key) is True for key in ("authenticated", "tls_verified_full", "read_only", "wrong_password_rejected", "plaintext_rejected")))
    require(resume.get("result") == {"status": "private_assembly_restored", "governance_seed_reexecuted": False,
                                     "old_forward_records_restored": False, "historical_token_journals_unchanged": True}
            and resume.get("operation") == "resume" and resume.get("node_id") == index["node_id"]
            and resume.get("prepared_sha256") == index["prepared_sha256"] and resume.get("backup_seal_sha256") == index["seal_sha256"])
    exact(rollout, {"schema_version", "status", "owner_id", "node_id", "from_prepared_sha256", "to_prepared_sha256",
                    "identity_sha256", "sources_sha256", "images_sha256", "binding_sha256", "cold_index_inputs_sha256", "profile", "completed_at"})
    require(rollout["schema_version"] == "evolution-classifier-rollout-v1" and rollout["status"] == "selected_worker_readiness_verified"
            and rollout["profile"] == "classifier" and rollout["owner_id"] == index["owner_id"] and rollout["node_id"] == index["node_id"]
            and rollout["from_prepared_sha256"] == index["prepared_sha256"]
            and rollout["to_prepared_sha256"] == preflight["expected"]["prepared_sha256"]
            and rollout["identity_sha256"] == digest(authorization["identity"])
            and rollout["sources_sha256"] == digest(authorization["sources"])
            and rollout["images_sha256"] == digest(preflight["images"])
            and rollout["binding_sha256"] == digest(preflight["binding"])
            and rollout["cold_index_inputs_sha256"] == digest({key: value for key, value in index.items() if key != "rollout"})
            and timestamp(rollout["completed_at"]) <= timestamp(preflight["created_at"]))


def verify_credentials(value, preflight, *, now):
    """Cross-bind retained completed generation to actual authenticated subjects.

The current checker additionally runs the existing journal/private transition
validator and authenticates tokens. Historical journal node_id is not rewritten.
"""
    require(type(value) is dict and value.get("schema_version") == "task-credential-renewal-v1"
            and value.get("status") == "complete" and value.get("generation") == preflight["expected"]["credential_generation"]
            and value.get("owner_id") == preflight["expected"]["owner_id"])
    expiry = timestamp(value.get("expires_at"))
    require(expiry >= now + timedelta(seconds=preflight["expected"]["minimum_validity_seconds"]))
    require(type(value.get("entries")) is list and len(value["entries"]) == 17
            and type(value.get("rollouts")) is dict and len(value["rollouts"]) == 4
            and all(type(row) is dict and row.get("complete") is True for row in value["rollouts"].values()))
    entries = {}
    for row in value["entries"]:
        require(type(row) is dict and row.get("scope") in {"_platform", "team-a", "team-b"}
                and (row.get("retired") is True if row["scope"] != "_platform" else row.get("retired") is False))
        receipt = exact(row.get("receipt"), {"credential_id", "expires_at"})
        uuid(receipt["credential_id"]); uuid(row.get("principal_id"))
        require(timestamp(receipt["expires_at"]) == expiry)
        key = (row["scope"], row["principal_id"], receipt["credential_id"])
        require(key not in entries); entries[key] = row
    for subject in preflight["expected"]["subjects"]:
        row = entries.get((subject["scope"], subject["principal_id"], subject["credential_id"]))
        require(row is not None and row.get("roles") == subject["roles"] and row.get("workload_id") == subject["workload_id"]
                and timestamp(subject["effective_expires_at"]) <= expiry)


def validate_completion(value, *, authorization_ref, authorization, observation_files, now):
    reference(authorization_ref); now_utc(now)
    exact(value, {"schema_version", "authorization", "authorization_id", "case_id", "nodeid", "started_at", "completed_at",
                  "worker", "pid", "process_start", "exit_code", "elapsed_ms", "result", "observation_files", "governance_files", "current_checks"})
    require(value["schema_version"] == COMPLETION_SCHEMA and value["authorization"] == authorization_ref
            and value["authorization_id"] == authorization["authorization_id"] and value["case_id"] == authorization["case"]["case_id"]
            and value["nodeid"] == authorization["case"]["nodeid"] and value["worker"] == authorization["worker"]
            and timestamp(authorization["created_at"]) <= timestamp(value["started_at"]) < timestamp(value["completed_at"]) <= now
            and timestamp(value["completed_at"]) < timestamp(authorization["expires_at"]))
    integer(value["pid"], 1, 2**31 - 1); integer(value["process_start"], 1, 2**63 - 1); integer(value["exit_code"], 0, 0)
    number(value["elapsed_ms"], .001, authorization["worker"]["timeout_seconds"] * 1000)
    result = exact(value["result"], {"nodeid", "status", "duration_ms", "assertions", "reason_codes"})
    require(result["nodeid"] == value["nodeid"] and result["status"] == "pass" and result["reason_codes"] == [])
    integer(result["assertions"], authorization["case"]["minimum_assertions"], 100000)
    number(result["duration_ms"], 0, value["elapsed_ms"])
    require(value["observation_files"] == observation_files)
    exact(observation_files, FAMILY_FILES[authorization["case"]["family"]])
    for name, ref in observation_files.items():
        reference(ref); require(ref["path"] == str(PurePosixPath(authorization["output_directory"]) / name))
    require(type(value["governance_files"]) is dict)
    if authorization["governance_root"] is None:
        require(value["governance_files"] == {})
    checks = value["current_checks"]
    require(type(checks) is list and len(checks) == len(CHECK_PHASES[authorization["case"]["family"]]))
    for index, (phase, ref) in enumerate(zip(CHECK_PHASES[authorization["case"]["family"]], checks, strict=True)):
        reference(ref)
        require(ref["path"] == str(PurePosixPath(authorization["current_checks_directory"]) / f"{index + 1:02d}-{phase}.json"))
    return value


def verify_governance(files, *, authorization, observation, operation, load):
    uuid(operation)
    require(authorization["governance_root"] is not None and type(files) is dict)
    fixed = {"intent.json", "completion.json", "release-matrix-release.json", "release-restore-original-binding.json"}
    require(fixed <= set(files))
    extra = set(files) - fixed
    require(len(extra) % 2 == 0 and 2 <= len(extra) <= 32 and extra == {
        f"request-{index:02d}-{suffix}.json" for index in range(1, len(extra) // 2 + 1) for suffix in ("intent", "returned")})
    rows = {}
    for name, ref in files.items():
        reference(ref); require(ref["path"] == str(PurePosixPath(authorization["governance_root"]) / operation / name))
        rows[name] = document(load(ref))
    intent, done = rows["intent.json"], rows["completion.json"]
    require(intent.get("schema_version") == "evolution-governance-attempt-v1" and intent.get("operation") == operation
            and intent.get("status") == "intent_only_no_mutation_claim" and intent.get("engine") == "injection"
            and intent.get("state") == "observe" and intent.get("stage") in {"input", "output"}
            and intent.get("tenant_id") in {"team-a", "team-b"})
    require(done == {"operation": operation, "status": "case_passed", "restoration": "applied", "last_request_outcome_unknown": False})
    require(type(intent.get("source_sha256")) is dict and bool(intent["source_sha256"])
            and all(authorization["sources"].get(name) == hashed for name, hashed in intent["source_sha256"].items()))
    handles = []
    for label in ("matrix-release", "restore-original-binding"):
        row = exact(rows["release-" + label + ".json"], {"schema_version", "status", "handle"})
        require(row["schema_version"] == "evolution-governance-release-handle-v1" and row["status"] == "acknowledged_not_yet_applied")
        handle = exact(row["handle"], {"operation", "label", "release_id", "tenant_id", "workload_id", "policy", "binding_revision", "previous_revision", "state", "actor_id"})
        uuid(handle["release_id"])
        require(handle["operation"] == operation and handle["label"] == label and handle["tenant_id"] == intent["tenant_id"]
                and handle["actor_id"] == intent["actors"]["operator"] and handle["workload_id"] == intent["original_binding"]["workload_id"])
        integer(handle["previous_revision"], 1, 2**63 - 2)
        require(type(handle["binding_revision"]) is int and handle["binding_revision"] == handle["previous_revision"] + 1)
        handles.append(handle)
    require(handles[0]["previous_revision"] == intent["original_binding"]["revision"]
            and handles[1]["previous_revision"] == handles[0]["binding_revision"]
            and handles[1]["policy"] == intent["original_policy"] and handles[1]["state"] == intent["original_binding"]["state"])
    policy = exact(handles[0]["policy"], {"tenant_id", "policy_id", "version", "digest"})
    require(policy["tenant_id"] == intent["tenant_id"] and policy["policy_id"] == "eval-" + UUID(operation).hex
            and type(policy["version"]) is int and policy["version"] == 1 and handles[0]["state"] == "observe")
    sha(policy["digest"])
    for index in range(1, len(extra) // 2 + 1):
        before, after = (rows[f"request-{index:02d}-{suffix}.json"] for suffix in ("intent", "returned"))
        require(before.get("operation") == after.get("operation") == operation
                and before.get("status") == "request_intended_outcome_unknown"
                and after.get("status") == "port_returned_expected_status_not_application_proof")
        sha(before.get("body_sha256")); sha(after.get("response_sha256"))
    require(observation.get("governance") == {
        "intent_sha256": files["intent.json"]["sha256"], "release_sha256": files["release-matrix-release.json"]["sha256"],
        "restoration_sha256": files["release-restore-original-binding.json"]["sha256"], "completion_sha256": files["completion.json"]["sha256"]})
    expected = observation.get("expected") or observation.get("decision")
    require(type(expected) is dict and expected.get("binding_revision") == handles[0]["binding_revision"])
    if "policy" in expected:
        require(expected["policy"] == handles[0]["policy"] and expected.get("tenant") == intent["tenant_id"]
                and expected.get("stage") == intent["stage"] and expected.get("workload_id") == handles[0]["workload_id"])
    if "policy_digest" in expected:
        require(expected["policy_digest"] == handles[0]["policy"]["digest"])
    require(observation.get("tenant", intent["tenant_id"]) == intent["tenant_id"] and observation.get("stage", intent["stage"]) == intent["stage"])


class ProvenanceVerifier:
    """Required adapters are explicit objects supplied by reviewed parent code.

    ``load(ref)`` returns stable private bytes within the caller's deadline.
    ``qualify_final_worker(reference, identity, sources, images, required_corpora, corpus_manifest, load)``
    authenticates genuine final-worker evidence and returns FINAL_WORKER_SCHEMA.
    ``current(phase, operation, deadline)`` checks without repair under caller lock.
    It returns CURRENT_SCHEMA; no truthy/boolean success response is accepted.
    """
    def __init__(self, *, authorization_ref, expected_case, expected_nodeid, expected_sources, expected_registry_sha256,
                 load, qualify_final_worker, current, now, expected_corpus_manifest, completion_ref=None):
        require(all(callable(port) for port in (load, qualify_final_worker, current, now)))
        reference(authorization_ref)
        if completion_ref is not None:
            reference(completion_ref)
        self.authorization_ref, self.completion_ref = copy.deepcopy(authorization_ref), copy.deepcopy(completion_ref)
        self.expected = copy.deepcopy({"expected_case": expected_case, "expected_nodeid": expected_nodeid,
            "expected_sources": expected_sources, "expected_registry_sha256": expected_registry_sha256,
            "expected_corpus_manifest": expected_corpus_manifest})
        self._load, self.qualify, self.current, self.now = load, qualify_final_worker, current, now
        self.raws = {}
        # The reviewed fixture durably records each returned record outside the
        # measurement directory; the parent later pins their exact inventory.
        self.validated_checks = []

    def load(self, ref):
        reference(ref)
        original = canonical(ref)
        argument = copy.deepcopy(ref)
        raw = self._load(argument)
        require(canonical(argument) == original and raw_digest(raw) == ref["sha256"])
        key = (ref["path"], ref["sha256"])
        require(not any(pathname == ref["path"] and hashed != ref["sha256"] for pathname, hashed in self.raws))
        if key in self.raws:
            require(raw == self.raws[key])
        self.raws[key] = raw
        return raw

    def stable(self):
        for (pathname, hashed), raw in list(self.raws.items()):
            require(self.load({"path": pathname, "sha256": hashed}) == raw)

    def _authorization(self, now):
        self.stable()
        authorization = document(self.load(self.authorization_ref))
        validate_authorization(authorization, **self.expected, now=now)
        preflight = document(self.load(authorization["preflight"]))
        validate_preflight(preflight, authorization, now=now)
        prepared = document(self.load(authorization["receipts"]["prepared"]))
        exact(prepared, {"schema_version", "artifacts", "preparation_lock_digest", "files", "environment"})
        require(type(prepared["schema_version"]) is int and prepared["schema_version"] == 1
                and type(prepared["artifacts"]) is list and bool(prepared["artifacts"])
                and type(prepared["files"]) is list and bool(prepared["files"]) and type(prepared["environment"]) is dict)
        sha(prepared["preparation_lock_digest"])
        verify_cold(document(self.load(authorization["receipts"]["cold"])), authorization=authorization, preflight=preflight, load=self.load)
        verify_credentials(document(self.load(authorization["receipts"]["credentials"])), preflight, now=now)
        require(document(self.load(authorization["receipts"]["node_boundary"])) == preflight["images"].get("node_boundary_snapshot"))
        initial = document(self.load(authorization["receipts"]["qualification"]))
        require(initial.get("schema_version") not in {"wolf-full-qualification-result-v1", "wolf-qualification-result-v1",
            "evolution-classifier-full-qualification-binding-v1", "evolution-classifier-wolf-qualification-binding-v1"}
            and initial.get("production_enabled", True) is not False)
        corpus_manifest = verify_corpus_manifest(authorization["corpus_manifest"], authorization=authorization, load=self.load)
        arguments = copy.deepcopy({"reference": authorization["receipts"]["qualification"], "identity": authorization["identity"],
                                  "sources": authorization["sources"], "images": preflight["images"], "required_corpora": authorization["required_corpora"],
                                  "corpus_manifest": corpus_manifest})
        original = canonical(arguments)
        qualified = self.qualify(**arguments, load=self.load)
        require(canonical(arguments) == original)
        verify_final_worker(qualified, authorization=authorization, preflight=preflight)
        return authorization, preflight

    def _proof(self, authorization):
        result = {key: authorization["receipts"][key]["sha256"] for key in BASE_PROOFS}
        result["parent_invocation"] = self.authorization_ref["sha256"]
        family = authorization["case"]["family"]
        if family in {"tls", "budget"}:
            result["transport"] = authorization["preflight"]["sha256"]
        if family == "cni":
            result["node_boundary"] = authorization["receipts"]["node_boundary"]["sha256"]
        return result

    def authorize(self):
        """Authenticate static launch inputs before Popen or client construction.

        This performs no current check, creates no invocation/operational proof,
        and records no runtime phase. Required bounded reader/final-worker ports
        remain mandatory. The caller provides the independent launch authority.
        """
        authorization, preflight = self._authorization(now_utc(self.now()))
        self.stable()
        final_now = now_utc(self.now())
        validate_authorization(authorization, **self.expected, now=final_now)
        validate_preflight(preflight, authorization, now=final_now)
        verify_credentials(document(self.raws[(authorization["receipts"]["credentials"]["path"], authorization["receipts"]["credentials"]["sha256"])]), preflight, now=final_now)
        require(all(timestamp(row["effective_expires_at"]) >= final_now + timedelta(seconds=preflight["expected"]["minimum_validity_seconds"])
                    for row in preflight["expected"]["subjects"]))
        return copy.deepcopy({"authorization": authorization, "preflight": preflight, "proof": self._proof(authorization)})

    def __call__(self, *, phase, operation, sources, binding=None, binding_sha256=None, observation=None,
                 group=None, case_id=None, invocation=None, deadline=None):
        require(phase in PHASES)
        if phase != "consume":
            number(deadline, .001, 1e15)  # Remaining time is enforced by the bounded I/O adapters.
        input_snapshot = canonical({"sources": sources, "binding": binding, "observation": observation, "group": group})
        if operation is not None:
            uuid(operation)
        if invocation is not None:
            uuid(invocation)
        now = now_utc(self.now())
        authorization, preflight = self._authorization(now)
        require(phase == "consume" or phase in CHECK_PHASES[authorization["case"]["family"]])
        require(sources == authorization["sources"] and case_id in {None, authorization["case"]["case_id"]})
        if binding is not None:
            require(binding == preflight["binding"])
        else:
            require(binding_sha256 == preflight["expected"]["binding_sha256"])
        if authorization["case"]["family"] in {"resource", "budget", "cni"}:
            require(operation is not None)
        else:
            require(operation is None)
        current = self.current(phase=phase, operation=operation, deadline=deadline)
        verify_current(preflight["expected"], current, preflight_sha256=authorization["preflight"]["sha256"],
                       phase=phase, operation=operation, now=now_utc(self.now()))
        if phase == "consume":
            require(self.completion_ref is not None)
            completion = document(self.load(self.completion_ref))
            files = completion.get("observation_files")
            validate_completion(completion, authorization_ref=self.authorization_ref, authorization=authorization,
                                observation_files=files, now=now_utc(self.now()))
            raw_observations = {name: self.load(ref) for name, ref in files.items()}
            name = "classifier-" + authorization["case"]["family"] + "-observation.json"
            retained = document(raw_observations[name])
            require(retained.get("operation") == operation)
            if authorization["case"]["family"] == "cni":
                require(type(group) is dict and retained.get("group") == group)
            else:
                require(type(observation) is dict and retained == observation)
            if invocation is not None:
                require(retained.get("invocation") == invocation)
            previous = timestamp(completion["started_at"])
            for check_phase, ref in zip(CHECK_PHASES[authorization["case"]["family"]], completion["current_checks"], strict=True):
                checked = document(self.load(ref))
                observed = timestamp(checked.get("observed_at"))
                require(previous <= observed <= timestamp(completion["completed_at"]))
                verify_current(preflight["expected"], checked, preflight_sha256=authorization["preflight"]["sha256"],
                               phase=check_phase, operation=operation, now=observed)
                previous = observed
            if authorization["governance_root"] is not None:
                verify_governance(completion["governance_files"], authorization=authorization, observation=retained,
                                  operation=operation, load=self.load)
        self.stable()
        final_now = now_utc(self.now())
        validate_authorization(authorization, **self.expected, now=final_now)
        validate_preflight(preflight, authorization, now=final_now)
        verify_current(preflight["expected"], current, preflight_sha256=authorization["preflight"]["sha256"],
                       phase=phase, operation=operation, now=final_now)
        require(input_snapshot == canonical({"sources": sources, "binding": binding, "observation": observation, "group": group}))
        self.validated_checks.append(copy.deepcopy(current))
        return self._proof(authorization)
