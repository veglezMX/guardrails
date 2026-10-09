"""Strict management projections shared by HTTP, persistence and schema export."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import Field, model_validator

from .. import local_contract as d
from ..transport import (
    StrictModel, Identifier, PositiveInt, TimeoutMs, TenantModel, WorkloadModel,
    PolicyModel, PolicyBindingModel, GovernanceFloorModel, ProblemModel,
)


class Envelope(StrictModel):
    schema_version: Literal['local-proof-v1']


class TenantWrite(Envelope):
    tenant_id: Identifier
    display_name: str


class WorkloadWrite(Envelope):
    workload_id: Identifier
    display_name: str


class RotationWrite(Envelope):
    reason: Literal['credential_rotation']


class BindingWrite(Envelope):
    policy_id: Identifier
    policy_version: Identifier
    input_timeout_ms: TimeoutMs = 1500
    output_timeout_ms: TimeoutMs = 1500
    reason: Literal['initial_binding', 'policy_update']


class StateWrite(Envelope):
    enforcement_state: d.EnforcementState
    reason: Literal['synthetic_promotion', 'synthetic_demotion']
    evidence_kind: Literal['synthetic_local_proof']
    fixture_run_id: UUID


class RollbackWrite(Envelope):
    target_revision: PositiveInt
    reason: Literal['rollback']


class RemovalWrite(Envelope):
    reason: Literal['binding_removal']


class FloorWrite(Envelope):
    require_binding: bool
    required_stages: tuple[d.Stage, ...]
    required_pii_types: tuple[d.PiiType, ...]
    minimum_action: d.DecisionAction
    minimum_state: Literal['disabled', 'observe']
    required_policy_id: Identifier | None
    minimum_policy_ordinals: dict[Identifier, PositiveInt]
    require_fail_closed: tuple[str, ...]
    forbid_content_retention: bool

    def to_floor(self, scope, scope_id, revision):
        return d.GovernanceFloor(scope=scope, tenant_id=scope_id if scope == 'tenant' else None,
                                 revision=revision, **self.model_dump(exclude={'schema_version'}))

    @model_validator(mode='after')
    def validate_rules(self):
        self.to_floor('organization', 'org', 1)
        for values in (self.required_stages, self.required_pii_types, self.require_fail_closed):
            if len(values) != len(set(values)):
                raise ValueError('invalid_field')
        return self


class CredentialModel(StrictModel):
    domain_class = d.Credential
    credential_id: UUID
    role: d.Role
    tenant_id: Identifier | None
    workload_id: Identifier | None
    token_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    revision: PositiveInt
    created_at: datetime
    revoked_at: datetime | None
    service_id: Identifier | None
    capabilities: tuple[str, ...]


class SnapshotModel(StrictModel):
    domain_class = d.RuntimeSnapshot
    generation: PositiveInt
    generated_at: datetime
    workloads: tuple[WorkloadModel, ...]
    credential_hashes: tuple[CredentialModel, ...]
    policies: tuple[PolicyModel, ...]
    bindings: tuple[PolicyBindingModel, ...]
    floors: tuple[GovernanceFloorModel, ...]
    digest: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class AppliedWrite(StrictModel):
    generation: PositiveInt
    digest: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class OperationModel(StrictModel):
    domain_class = d.OperationStatus
    operation_id: UUID
    desired_generation: PositiveInt
    applied_generation: Annotated[int, Field(ge=0)]
    status: Literal['pending', 'applied']
    committed_at: datetime
    applied_at: datetime | None


T = TypeVar('T')


class Mutation(StrictModel, Generic[T]):
    resource: T
    operation: OperationModel


class Page(StrictModel, Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None


class WorkloadIssued(StrictModel):
    workload: WorkloadModel
    token: str
    operation: OperationModel


class CredentialIssued(StrictModel):
    credential_id: UUID
    token: str
    operation: OperationModel


class PolicyPublished(StrictModel):
    policy: PolicyModel
    operation: OperationModel


class PolicySummary(StrictModel):
    tenant_id: Identifier
    policy_id: Identifier
    version: Identifier
    publication_ordinal: PositiveInt
    digest: str
    published_at: datetime
    published_by: UUID
    schema_version: Literal['local-proof-v1']
    detector_format_version: Literal['pii-ascii-v1']
    retain_content: bool


class BindingRead(StrictModel):
    desired: PolicyBindingModel
    applied_revision: PositiveInt | None
    applied_generation: Annotated[int, Field(ge=0)]
    cache_age_s: Annotated[float, Field(ge=0)] | None


class AuditModel(StrictModel):
    event_id: UUID
    source: Literal['management', 'runtime', 'connector']
    occurred_at: datetime
    tenant_id: Identifier | None
    workload_id: Identifier | None
    actor_id: UUID | None
    event_type: str
    correlation_id: UUID | None
    metadata: dict
    content: None


class AuditPage(Page[AuditModel]):
    source_status: dict[str, Literal['available', 'unavailable']]
    complete: bool


class AuditUnavailable(ProblemModel):
    source_status: dict[str, Literal['available', 'unavailable']]
    complete: Literal[False]


class AppliedResponse(StrictModel):
    applied_generation: PositiveInt


class ReadyModel(StrictModel):
    ready: bool
    degraded: bool
