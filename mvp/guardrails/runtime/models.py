"""Closed runtime audit, diagnostic and proof-control projections."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .. import local_contract as d
from ..transport import (StrictModel, Identifier, NonNegative, PositiveInt,
                         FindingModel, TextBatchModel)


class SafeEvaluator(StrictModel):
    evaluator_id: Literal['local_pii']
    detector_format_version: Literal['pii-ascii-v1']
    elapsed_ms: NonNegative


class AuditMetadata(StrictModel):
    decision_id: UUID
    evaluation_id: UUID | None
    policy_id: Identifier | None
    policy_version: Identifier | None
    policy_digest: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')] | None
    binding_revision: PositiveInt | None
    snapshot_generation: PositiveInt | None
    enforcement_state: d.EnforcementState | None
    stage: Literal['input', 'output']
    action: d.DecisionAction
    allowed: bool
    would_be_action: d.DecisionAction
    reason_codes: tuple[Literal['GR-003:local_pii:email', 'GR-003:local_pii:phone'], ...]
    operational_codes: tuple[str, ...]
    guardrail_category: Literal['GR-003'] | None
    evaluator_metadata: tuple[SafeEvaluator, ...]
    audit_id: UUID | None
    findings: tuple[FindingModel, ...]
    evaluation_ms: NonNegative
    cache_age_s: NonNegative | None = None
    audit_status: Literal['persisted', 'spooled', 'failed']

    @model_validator(mode='after')
    def valid_metadata(self):
        if (not set(self.operational_codes) <= d.OPERATIONAL_CODES
                or self.allowed != (self.action != 'block')
                or bool(self.reason_codes) != (self.guardrail_category == 'GR-003')
                or (self.audit_status == 'persisted') != (self.audit_id is not None)):
            raise ValueError('invalid_field')
        return self


class RuntimeAuditEvent(StrictModel):
    event_id: UUID
    source: Literal['runtime', 'connector']
    occurred_at: datetime
    tenant_id: Identifier
    workload_id: Identifier
    actor_id: None
    event_type: Literal['evaluation', 'client_fallback']
    correlation_id: UUID
    metadata: AuditMetadata
    content: TextBatchModel | None

    @model_validator(mode='after')
    def valid_event(self):
        if self.occurred_at.utcoffset() is None or self.occurred_at.utcoffset().total_seconds() != 0:
            raise ValueError('invalid_field')
        if self.source == 'connector':
            if (self.event_type != 'client_fallback' or self.content is not None
                    or self.metadata.evaluation_id is not None or self.metadata.audit_id is not None
                    or self.metadata.audit_status == 'persisted' or self.metadata.findings
                    or self.metadata.reason_codes or self.metadata.evaluator_metadata):
                raise ValueError('invalid_field')
        elif self.event_type != 'evaluation' or self.metadata.evaluation_id is None:
            raise ValueError('invalid_field')
        return self


class ClientEventsResponse(StrictModel):
    accepted_event_ids: tuple[UUID, ...]


class RuntimeReady(StrictModel):
    ready: bool
    degraded: bool
    applied_generation: PositiveInt | None
    cache_age_s: NonNegative | None


class CapabilityEvaluator(StrictModel):
    id: Literal['local_pii']
    format: Literal['pii-ascii-v1']
    category: Literal['GR-003']


class Capabilities(StrictModel):
    schema_version: Literal['local-proof-v1']
    supported_stages: tuple[Literal['input', 'output'], ...]
    supported_actions: tuple[d.DecisionAction, ...]
    evaluators: tuple[CapabilityEvaluator, ...]


class FaultControl(StrictModel):
    worker: Literal['stall', 'crash', 'invalid_span'] | None
    response: Literal['malformed', 'invalid_transformation', 'delay'] | None = None
    stage: Literal['input', 'output'] = 'input'
