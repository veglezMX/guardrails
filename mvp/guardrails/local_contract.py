"""Immutable local-proof domain. No web, database, or gateway dependencies.

The original single-text contract remains available during the staged migration.
This module owns the local-proof-v1 batch contract described by the release plan.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import types
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints
from uuid import UUID

SCHEMA_VERSION = "local-proof-v1"
DETECTOR_FORMAT_VERSION = "pii-ascii-v1"
MAX_CONTENT_BYTES = 1_048_576
MAX_SEGMENTS = 32
Stage = Literal["input", "output", "retrieval", "tool_call", "tool_result", "memory_write", "final_response"]
EnforcementState = Literal["disabled", "observe", "warn", "redact", "block"]
DecisionAction = Literal["allow", "log_only", "redact", "block"]
FailureAction = Literal["block", "allow_and_log"]
Role = Literal["security_admin", "tenant_operator", "workload", "runtime_service", "management_service"]
ChangeReason = Literal["initial_binding", "policy_update", "synthetic_promotion", "synthetic_demotion", "rollback", "credential_rotation", "floor_update", "binding_removal"]
PiiType = Literal["email", "phone"]
OPERATIONAL_CODES = frozenset(("disabled", "evaluation_timeout", "evaluation_unavailable", "policy_unresolved", "management_unavailable", "audit_write_failed", "unsafe_transformation", "unsupported_surface", "invalid_request", "identity_mismatch", "authentication_failed", "mandatory_floor_violation", "context_invalid", "context_expired", "capacity_exceeded", "client_unconfigured", "upstream_failed"))
REASON_CODES = frozenset(("GR-003:local_pii:email", "GR-003:local_pii:phone"))


class DomainValidationError(ValueError):
    """Safe machine-readable failure; never includes submitted values."""

    def __init__(self, code: str = "invalid_request", field: str = "body"):
        self.code = code
        self.field = field
        super().__init__(code)


def _require(condition: bool, field: str, code: str = "invalid_request") -> None:
    if not condition:
        raise DomainValidationError(code, field)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _valid_type(value: Any, annotation: Any) -> bool:
    origin, args = get_origin(annotation), get_args(annotation)
    if annotation is Any:
        return True
    if origin in (Union, types.UnionType):
        return any(_valid_type(value, item) for item in args)
    if origin is Literal:
        return any(type(value) is type(item) and value == item for item in args)
    if origin is tuple:
        return isinstance(value, tuple) and ((len(args) == 2 and args[1] is Ellipsis and all(_valid_type(item, args[0]) for item in value)) or (len(value) == len(args) and all(_valid_type(item, expected) for item, expected in zip(value, args))))
    if origin is Mapping:
        return isinstance(value, Mapping) and all(_valid_type(key, args[0]) and _valid_type(item, args[1]) for key, item in value.items())
    if annotation is float:
        return type(value) in (float, int) and math.isfinite(value)
    if annotation in (str, int, bool):
        return type(value) is annotation
    if annotation is type(None):
        return value is None
    return isinstance(value, annotation)


def _identifier(value: str, field: str) -> None:
    _require(bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value, flags=re.ASCII)), field)


def domain_to_wire(value: Any) -> Any:
    if is_dataclass(value):
        result = {item.name: domain_to_wire(getattr(value, item.name)) for item in fields(value)}
        if isinstance(value, Decision):
            result["allowed"] = value.allowed
        return result
    if isinstance(value, Mapping):
        return {key: domain_to_wire(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [domain_to_wire(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(domain_to_wire(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass(frozen=True, kw_only=True)
class DomainModel:
    def __post_init__(self) -> None:
        hints = get_type_hints(type(self))
        for item in fields(self):
            value = _freeze(getattr(self, item.name))
            object.__setattr__(self, item.name, value)
            _require(_valid_type(value, hints[item.name]), item.name)
            if isinstance(value, str):
                try:
                    value.encode("utf-8", errors="strict")
                except UnicodeError:
                    raise DomainValidationError(field=item.name) from None
            if isinstance(value, datetime):
                _require(value.tzinfo is not None and value.utcoffset() is not None and value.utcoffset().total_seconds() == 0, item.name)
            if item.name in ("tenant_id", "workload_id", "policy_id", "check_id", "version", "policy_version") and value is not None:
                _identifier(value, item.name)

    def to_wire(self) -> dict[str, Any]:
        return domain_to_wire(self)


@dataclass(frozen=True, kw_only=True)
class Tenant(DomainModel):
    tenant_id: str
    display_name: str
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class Workload(DomainModel):
    tenant_id: str
    workload_id: str
    display_name: str
    created_at: datetime
    credential_revision: int
    enforcement_posture: Literal["cooperative"] = "cooperative"

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(self.credential_revision >= 1, "credential_revision")


@dataclass(frozen=True, kw_only=True)
class Credential(DomainModel):
    credential_id: UUID
    role: Role
    tenant_id: str | None
    workload_id: str | None
    token_sha256: str
    revision: int
    created_at: datetime
    revoked_at: datetime | None
    service_id: str | None
    capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(bool(re.fullmatch(r"[0-9a-f]{64}", self.token_sha256)), "token_sha256")
        _require(self.revision >= 1, "revision")
        if self.role == "workload":
            _require(self.tenant_id is not None and self.workload_id is not None and self.service_id is None, "role")
        elif self.role == "tenant_operator":
            _require(self.tenant_id is not None and self.workload_id is None and self.service_id is None, "role")
        elif self.role == "security_admin":
            _require(self.tenant_id is None and self.workload_id is None and self.service_id is None, "role")
        else:
            _require(self.service_id is not None and self.tenant_id is None and self.workload_id is None, "role")


@dataclass(frozen=True, kw_only=True)
class CheckSpec(DomainModel):
    check_id: str
    evaluator: Literal["local_pii"]
    stages: tuple[Stage, ...]
    pii_types: tuple[PiiType, ...]
    requested_action: Literal["log_only", "redact", "block"]

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(bool(self.stages) and len(self.stages) == len(set(self.stages)), "stages")
        _require(all(stage in ("input", "output") for stage in self.stages), "stages", "unsupported_surface")
        _require(bool(self.pii_types) and len(self.pii_types) == len(set(self.pii_types)), "pii_types")


@dataclass(frozen=True, kw_only=True)
class EvaluatorCapabilities(DomainModel):
    evaluator_id: Literal["local_pii"] = "local_pii"
    detector_format_version: Literal["pii-ascii-v1"] = "pii-ascii-v1"
    supported_stages: tuple[Stage, ...] = ("input", "output")
    guardrail_category: Literal["GR-003"] = "GR-003"
    pii_types: tuple[PiiType, ...] = ("email", "phone")


@dataclass(frozen=True, kw_only=True)
class FailureBehavior(DomainModel):
    evaluation_timeout: FailureAction
    evaluation_unavailable: FailureAction
    policy_unresolved: FailureAction
    management_unavailable: FailureAction
    audit_failure: FailureAction
    transformation_failure: FailureAction


@dataclass(frozen=True, kw_only=True)
class PolicyDraft(DomainModel):
    tenant_id: str
    policy_id: str
    version: str
    checks: tuple[CheckSpec, ...]
    failure_behavior: FailureBehavior
    retain_content: bool
    schema_version: Literal["local-proof-v1"] = "local-proof-v1"
    detector_format_version: Literal["pii-ascii-v1"] = "pii-ascii-v1"

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(bool(self.checks) and len({check.check_id for check in self.checks}) == len(self.checks), "checks")

    def content_digest(self) -> str:
        names = ("schema_version", "tenant_id", "policy_id", "version", "checks", "failure_behavior", "retain_content", "detector_format_version")
        return hashlib.sha256(canonical_bytes({name: getattr(self, name) for name in names})).hexdigest()


@dataclass(frozen=True, kw_only=True)
class Policy(PolicyDraft):
    publication_ordinal: int
    published_at: datetime
    published_by: UUID
    digest: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(self.publication_ordinal >= 1, "publication_ordinal")
        _require(self.digest == self.content_digest(), "digest")


@dataclass(frozen=True, kw_only=True)
class PolicyBinding(DomainModel):
    tenant_id: str
    workload_id: str
    policy_id: str
    policy_version: str
    enforcement_state: EnforcementState
    revision: int
    generation: int
    input_timeout_ms: int
    output_timeout_ms: int
    updated_at: datetime
    updated_by: UUID
    reason: ChangeReason
    evidence_kind: Literal["synthetic_local_proof"] | None
    fixture_run_id: UUID | None

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(self.revision >= 1 and self.generation >= 1, "revision")
        for name in ("input_timeout_ms", "output_timeout_ms"):
            _require(750 <= getattr(self, name) <= 5000, name)
        if self.reason == "initial_binding":
            _require(self.enforcement_state == "observe", "enforcement_state")
        if self.reason in ("synthetic_promotion", "synthetic_demotion"):
            _require(self.evidence_kind == "synthetic_local_proof" and self.fixture_run_id is not None, "evidence_kind")


@dataclass(frozen=True, kw_only=True)
class GovernanceFloor(DomainModel):
    scope: Literal["organization", "tenant"]
    tenant_id: str | None
    revision: int
    require_binding: bool
    required_stages: tuple[Stage, ...]
    required_pii_types: tuple[PiiType, ...]
    minimum_action: DecisionAction
    minimum_state: Literal["disabled", "observe"]
    required_policy_id: str | None
    minimum_policy_ordinals: Mapping[str, int]
    require_fail_closed: tuple[str, ...]
    forbid_content_retention: bool

    def __post_init__(self) -> None:
        super().__post_init__()
        _require((self.scope == "organization") == (self.tenant_id is None), "tenant_id")
        _require(self.revision >= 1, "revision")
        _require(all(stage in ("input", "output") for stage in self.required_stages), "required_stages", "unsupported_surface")
        for policy_id, ordinal in self.minimum_policy_ordinals.items():
            _identifier(policy_id, "minimum_policy_ordinals")
            _require(ordinal >= 1, "minimum_policy_ordinals")
        _require(set(self.require_fail_closed) <= {item.name for item in fields(FailureBehavior)}, "require_fail_closed")


@dataclass(frozen=True, kw_only=True)
class TextSegment(DomainModel):
    segment_id: str
    text: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(bool(re.fullmatch(r"[0-9]{1,2}", self.segment_id)) and int(self.segment_id) < MAX_SEGMENTS, "segment_id")


@dataclass(frozen=True, kw_only=True)
class TextBatch(DomainModel):
    segments: tuple[TextSegment, ...]

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(1 <= len(self.segments) <= MAX_SEGMENTS, "segments")
        _require(len({segment.segment_id for segment in self.segments}) == len(self.segments), "segment_id")
        _require(sum(len(segment.text.encode("utf-8")) for segment in self.segments) <= MAX_CONTENT_BYTES, "content")


@dataclass(frozen=True, kw_only=True)
class Evaluation(DomainModel):
    evaluation_id: UUID
    tenant_id: str
    workload_id: str
    stage: Stage
    content: TextBatch
    correlation_id: UUID
    context_id: str | None
    received_at: datetime

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(self.stage in ("input", "output"), "stage", "unsupported_surface")
        _require((self.stage == "input" and self.context_id is None) or (self.stage == "output" and bool(self.context_id)), "context_id")


@dataclass(frozen=True, kw_only=True)
class Finding(DomainModel):
    check_id: str
    segment_id: str
    reason_code: str
    start: int
    end: int

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(self.reason_code in REASON_CODES, "reason_code")
        _require(0 <= self.start < self.end, "start")

    def validate_content(self, content: TextBatch) -> None:
        segments = {segment.segment_id: segment for segment in content.segments}
        _require(self.segment_id in segments, "segment_id", "unsafe_transformation")
        _require(self.end <= len(segments[self.segment_id].text), "end", "unsafe_transformation")


@dataclass(frozen=True, kw_only=True)
class EvaluatorMetadata(DomainModel):
    evaluator_id: str
    detector_format_version: str
    elapsed_ms: float

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(self.elapsed_ms >= 0, "elapsed_ms")


@dataclass(frozen=True, kw_only=True)
class ClientConfig(DomainModel):
    schema_version: Literal["local-proof-v1"]
    tenant_id: str
    workload_id: str
    generation: int
    input_timeout_ms: int
    output_timeout_ms: int
    failure_behavior: FailureBehavior
    retrieved_at: datetime

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(self.generation >= 1, "generation")
        for name in ("input_timeout_ms", "output_timeout_ms"):
            _require(750 <= getattr(self, name) <= 5000, name)


@dataclass(frozen=True, kw_only=True)
class Decision(DomainModel):
    decision_id: UUID
    evaluation_id: UUID | None
    correlation_id: UUID
    context_id: str | None
    tenant_id: str
    workload_id: str
    stage: Stage
    policy_id: str | None
    policy_version: str | None
    policy_digest: str | None
    binding_revision: int | None
    snapshot_generation: int | None
    enforcement_state: EnforcementState | None
    action: DecisionAction
    would_be_action: DecisionAction
    reason_codes: tuple[str, ...]
    operational_codes: tuple[str, ...]
    guardrail_category: Literal["GR-003"] | None
    evaluator_metadata: tuple[EvaluatorMetadata, ...]
    audit_id: UUID | None
    findings: tuple[Finding, ...]
    transformed_content: TextBatch | None
    evaluation_ms: float
    source: Literal["runtime", "connector"]
    audit_status: Literal["persisted", "spooled", "failed"]
    client_config: ClientConfig | None

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(self.stage in ("input", "output"), "stage", "unsupported_surface")
        _require(self.evaluation_ms >= 0, "evaluation_ms")
        _require((self.action == "redact") == (self.transformed_content is not None), "transformed_content")
        _require((self.audit_status == "persisted") == (self.audit_id is not None), "audit_id")
        _require((self.source == "runtime") == (self.evaluation_id is not None), "evaluation_id")
        _require(set(self.reason_codes) <= REASON_CODES, "reason_codes")
        _require(set(self.operational_codes) <= OPERATIONAL_CODES, "operational_codes")
        _require((bool(self.reason_codes)) == (self.guardrail_category == "GR-003"), "guardrail_category")
        _require({finding.reason_code for finding in self.findings} <= set(self.reason_codes), "findings")
        policy_fields = (self.policy_id, self.policy_version, self.policy_digest, self.binding_revision, self.snapshot_generation, self.enforcement_state)
        _require(all(value is None for value in policy_fields) or all(value is not None for value in policy_fields), "policy_id")
        if self.policy_digest is not None:
            _require(bool(re.fullmatch(r"[a-f0-9]{64}", self.policy_digest)), "policy_digest")
            _require(self.binding_revision >= 1 and self.snapshot_generation >= 1, "binding_revision")
        if self.client_config is not None:
            _require(self.client_config.tenant_id == self.tenant_id and self.client_config.workload_id == self.workload_id, "client_config")
        object.__setattr__(self, "reason_codes", tuple(sorted(set(self.reason_codes))))
        object.__setattr__(self, "operational_codes", tuple(sorted(set(self.operational_codes))))
        object.__setattr__(self, "findings", tuple(sorted(self.findings, key=lambda item: (item.segment_id, item.check_id, item.start, item.end))))

    @property
    def allowed(self) -> bool:
        return self.action != "block"

    def validate_content(self, original: TextBatch) -> None:
        for finding in self.findings:
            finding.validate_content(original)
        if self.transformed_content is not None:
            _require(tuple(segment.segment_id for segment in self.transformed_content.segments) == tuple(segment.segment_id for segment in original.segments), "transformed_content", "unsafe_transformation")


@dataclass(frozen=True, kw_only=True)
class EvaluationContext(DomainModel):
    context_id: str
    tenant_id: str
    workload_id: str
    correlation_id: UUID
    binding: PolicyBinding
    policy: Policy
    snapshot_generation: int
    client_config: ClientConfig
    created_at_monotonic: float
    expires_at_monotonic: float
    output_consumed: bool

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(bool(self.context_id) and self.snapshot_generation >= 1, "context_id")
        _require(0 <= self.created_at_monotonic < self.expires_at_monotonic, "expires_at_monotonic")
        _require(self.binding.tenant_id == self.tenant_id == self.policy.tenant_id == self.client_config.tenant_id, "tenant_id")
        _require(self.binding.workload_id == self.workload_id == self.client_config.workload_id, "workload_id")
        _require(self.binding.policy_id == self.policy.policy_id and self.binding.policy_version == self.policy.version, "policy_id")


@dataclass(frozen=True, kw_only=True)
class RuntimeSnapshot(DomainModel):
    generation: int
    generated_at: datetime
    workloads: tuple[Workload, ...]
    credential_hashes: tuple[Credential, ...]
    policies: tuple[Policy, ...]
    bindings: tuple[PolicyBinding, ...]
    floors: tuple[GovernanceFloor, ...]
    digest: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(self.generation >= 1, "generation")
        _require(all(credential.role == "workload" for credential in self.credential_hashes), "credential_hashes")
        content = {item.name: getattr(self, item.name) for item in fields(self) if item.name != "digest"}
        _require(self.digest == hashlib.sha256(canonical_bytes(content)).hexdigest(), "digest")
        # Cross-object references/capabilities/floor checks belong to SnapshotCache.


@dataclass(frozen=True, kw_only=True)
class OperationStatus(DomainModel):
    operation_id: UUID
    desired_generation: int
    applied_generation: int
    status: Literal["pending", "applied"]
    committed_at: datetime
    applied_at: datetime | None

    def __post_init__(self) -> None:
        super().__post_init__()
        _require(self.desired_generation >= 1 and self.applied_generation >= 0, "desired_generation")
        _require((self.status == "applied") == (self.applied_at is not None), "applied_at")
        if self.status == "applied":
            _require(self.applied_generation >= self.desired_generation, "applied_generation")


@dataclass(frozen=True, kw_only=True)
class ProofConfig(DomainModel):
    runtime_deadline_ms: int = 500
    audit_reserve_ms: int = 100
    poll_interval_ms: int = 1000
    max_snapshot_age_s: int = 60
    context_ttl_s: int = 120
    max_contexts: int = 1024
    max_segments: int = 32
    max_content_bytes: int = 1_048_576
    max_http_bytes: int = 8_388_608
    workers: int = 4
    max_queued_jobs: int = 32
    sqlite_busy_timeout_ms: int = 100
    unresolved_policy_behavior: FailureAction = "block"
    management_unavailable_without_snapshot: FailureAction = "block"
    model_timeout_s: int = 10

    def __post_init__(self) -> None:
        super().__post_init__()
        for item in fields(self):
            value = getattr(self, item.name)
            if type(value) is int:
                _require(value > 0, item.name)
        _require(self.audit_reserve_ms < self.runtime_deadline_ms, "audit_reserve_ms")
        _require(self.max_segments <= MAX_SEGMENTS and self.max_content_bytes <= MAX_CONTENT_BYTES, "max_segments")
