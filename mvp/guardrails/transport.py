"""Strict local-proof JSON projections and content-free API failures.

All wire entry points decode through parse_model, not FastAPI's permissive JSON
body decoder. Pydantic produces the schema from these same projections.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Annotated, Any, ClassVar, Literal, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from . import local_contract as domain

# JSON escaping can expand a valid 1 MiB text to 6 MiB. Content limits are checked
# separately after parsing. The envelope limit still bounds parser allocation.
MAX_JSON_BYTES = 8_388_608
Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$", max_length=64)]
NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
PositiveInt = Annotated[int, Field(ge=1)]
TimeoutMs = Annotated[int, Field(ge=750, le=5000)]


class ContractProblem(Exception):
    """Public exception contains safe constants and schema-owned field names."""

    def __init__(self, code: str = "invalid_request", status: int = 422, errors: tuple[dict[str, str], ...] = ()):
        self.code = code if code in domain.OPERATIONAL_CODES else "invalid_request"
        self.status = status if status in (400, 401, 403, 404, 409, 413, 422, 428, 429, 503) else 422
        self.errors = errors
        super().__init__(self.code)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False, hide_input_in_errors=True)
    domain_class: ClassVar[type[domain.DomainModel] | None] = None

    def to_domain(self) -> domain.DomainModel:
        if self.domain_class is None:
            raise TypeError("no_domain_projection")
        return self.domain_class(**{name: _to_domain(getattr(self, name)) for name in type(self).model_fields})

    @model_validator(mode="after")
    def check_domain(self):
        if self.domain_class is not None:
            self.to_domain()
        return self


def _to_domain(value: Any) -> Any:
    if isinstance(value, StrictModel):
        return value.to_domain()
    if isinstance(value, (tuple, list)):
        return tuple(_to_domain(item) for item in value)
    if isinstance(value, dict):
        return {key: _to_domain(item) for key, item in value.items()}
    return value


class TextSegmentModel(StrictModel):
    domain_class = domain.TextSegment
    segment_id: str
    text: str


class TextBatchModel(StrictModel):
    domain_class = domain.TextBatch
    segments: Annotated[tuple[TextSegmentModel, ...], Field(min_length=1, max_length=32)]


class FailureBehaviorModel(StrictModel):
    domain_class = domain.FailureBehavior
    evaluation_timeout: domain.FailureAction
    evaluation_unavailable: domain.FailureAction
    policy_unresolved: domain.FailureAction
    management_unavailable: domain.FailureAction
    audit_failure: domain.FailureAction
    transformation_failure: domain.FailureAction


class CheckSpecModel(StrictModel):
    domain_class = domain.CheckSpec
    check_id: Identifier
    evaluator: Literal["local_pii"]
    stages: tuple[domain.Stage, ...]
    pii_types: tuple[domain.PiiType, ...]
    requested_action: Literal["log_only", "redact", "block"]


class PolicyWriteModel(StrictModel):
    domain_class = domain.PolicyDraft
    schema_version: Literal["local-proof-v1"]
    tenant_id: Identifier
    policy_id: Identifier
    version: Identifier
    checks: tuple[CheckSpecModel, ...]
    failure_behavior: FailureBehaviorModel
    retain_content: bool
    detector_format_version: Literal["pii-ascii-v1"]


class PolicyValidationModel(StrictModel):
    valid: Literal[True]
    canonical_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    capabilities_version: str

    @field_validator("valid", mode="before")
    @classmethod
    def require_boolean(cls, value):
        # Literal[True] accepts numeric 1/1.0 even in Pydantic strict mode.
        if type(value) is not bool:
            raise ValueError("invalid_field")
        return value


class PolicyModel(PolicyWriteModel):
    domain_class = domain.Policy
    publication_ordinal: PositiveInt
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    published_at: datetime
    published_by: UUID


class TenantModel(StrictModel):
    domain_class = domain.Tenant
    tenant_id: Identifier
    display_name: str
    created_at: datetime


class WorkloadModel(StrictModel):
    domain_class = domain.Workload
    tenant_id: Identifier
    workload_id: Identifier
    display_name: str
    created_at: datetime
    credential_revision: PositiveInt
    enforcement_posture: Literal["cooperative"]


class PolicyBindingModel(StrictModel):
    domain_class = domain.PolicyBinding
    tenant_id: Identifier
    workload_id: Identifier
    policy_id: Identifier
    policy_version: Identifier
    enforcement_state: domain.EnforcementState
    revision: PositiveInt
    generation: PositiveInt
    input_timeout_ms: TimeoutMs
    output_timeout_ms: TimeoutMs
    updated_at: datetime
    updated_by: UUID
    reason: domain.ChangeReason
    evidence_kind: Literal["synthetic_local_proof"] | None
    fixture_run_id: UUID | None


class GovernanceFloorModel(StrictModel):
    domain_class = domain.GovernanceFloor
    scope: Literal["organization", "tenant"]
    tenant_id: Identifier | None
    revision: PositiveInt
    require_binding: bool
    required_stages: tuple[domain.Stage, ...]
    required_pii_types: tuple[domain.PiiType, ...]
    minimum_action: domain.DecisionAction
    minimum_state: Literal["disabled", "observe"]
    required_policy_id: Identifier | None
    minimum_policy_ordinals: dict[str, PositiveInt]
    require_fail_closed: tuple[str, ...]
    forbid_content_retention: bool


class EvaluationRequestModel(StrictModel):
    schema_version: Literal["local-proof-v1"]
    tenant_id: Identifier
    workload_id: Identifier
    stage: domain.Stage
    content: TextBatchModel
    correlation_id: UUID
    context_id: Annotated[str, Field(min_length=1, max_length=128)] | None

    def to_domain(self) -> domain.Evaluation:
        return domain.Evaluation(evaluation_id=uuid4(), tenant_id=self.tenant_id, workload_id=self.workload_id, stage=self.stage, content=self.content.to_domain(), correlation_id=self.correlation_id, context_id=self.context_id, received_at=datetime.now(timezone.utc))

    @model_validator(mode="after")
    def check_evaluation(self):
        self.to_domain()
        return self


class FindingModel(StrictModel):
    domain_class = domain.Finding
    check_id: Identifier
    segment_id: str
    reason_code: str
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=1)]


class EvaluatorMetadataModel(StrictModel):
    domain_class = domain.EvaluatorMetadata
    evaluator_id: str
    detector_format_version: str
    elapsed_ms: NonNegative


class ClientConfigModel(StrictModel):
    domain_class = domain.ClientConfig
    schema_version: Literal["local-proof-v1"]
    tenant_id: Identifier
    workload_id: Identifier
    generation: PositiveInt
    input_timeout_ms: TimeoutMs
    output_timeout_ms: TimeoutMs
    failure_behavior: FailureBehaviorModel
    retrieved_at: datetime


class DecisionModel(StrictModel):
    domain_class = domain.Decision
    decision_id: UUID
    evaluation_id: UUID | None
    correlation_id: UUID
    context_id: Annotated[str, Field(min_length=1, max_length=128)] | None
    tenant_id: Identifier
    workload_id: Identifier
    stage: domain.Stage
    policy_id: Identifier | None
    policy_version: Identifier | None
    policy_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None
    binding_revision: PositiveInt | None
    snapshot_generation: PositiveInt | None
    enforcement_state: domain.EnforcementState | None
    action: domain.DecisionAction
    allowed: bool
    would_be_action: domain.DecisionAction
    reason_codes: tuple[str, ...]
    operational_codes: tuple[str, ...]
    guardrail_category: Literal["GR-003"] | None
    evaluator_metadata: tuple[EvaluatorMetadataModel, ...]
    audit_id: UUID | None
    findings: tuple[FindingModel, ...]
    transformed_content: TextBatchModel | None
    evaluation_ms: NonNegative
    source: Literal["runtime", "connector"]
    audit_status: Literal["persisted", "spooled", "failed"]
    client_config: ClientConfigModel | None

    def to_domain(self) -> domain.Decision:
        if self.allowed != (self.action != "block"):
            raise domain.DomainValidationError(field="allowed")
        return domain.Decision(**{name: _to_domain(getattr(self, name)) for name in type(self).model_fields if name != "allowed"})


class FieldErrorModel(StrictModel):
    field: str
    code: str


class ProblemModel(StrictModel):
    type: str
    title: str
    status: int
    code: str
    correlation_id: UUID
    errors: tuple[FieldErrorModel, ...]


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractProblem(status=400)
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ContractProblem(status=400)


def _json_values(value: Any) -> None:
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ContractProblem(status=400)
    elif isinstance(value, dict):
        for key, item in value.items():
            _json_values(key)
            _json_values(item)
    elif isinstance(value, list):
        for item in value:
            _json_values(item)


def strict_json_loads(raw: bytes, *, max_bytes: int = MAX_JSON_BYTES) -> Any:
    """Reject malformed, duplicate, non-finite, ill-formed Unicode and huge JSON."""
    if not isinstance(raw, bytes):
        raise ContractProblem(status=400)
    if len(raw) > max_bytes:
        raise ContractProblem(status=413)
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_pairs, parse_constant=_reject_constant)
        _json_values(value)
        return value
    except ContractProblem:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise ContractProblem(status=400) from None


def _validation_problem(error: ValidationError, model: type[StrictModel]) -> ContractProblem:
    safe: list[dict[str, str]] = []
    code = "invalid_request"
    for item in error.errors(include_url=False, include_input=False, include_context=True):
        domain_error = item.get("ctx", {}).get("error")
        if isinstance(domain_error, domain.DomainValidationError):
            code = domain_error.code
        loc = item.get("loc", ())
        field = str(loc[0]) if loc and loc[0] in model.model_fields else "body"
        safe.append({"field": field, "code": "invalid_field"})
    return ContractProblem(code, 422, tuple(safe[:20]))


M = TypeVar("M", bound=StrictModel)


def parse_model(raw: bytes, model: type[M]) -> M:
    value = strict_json_loads(raw)
    try:
        # JSON mode accepts UUID/timestamp strings and array->tuple projections
        # while strict=True still rejects scalar coercion (including bool->int).
        return model.model_validate_json(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except ValidationError as error:
        raise _validation_problem(error, model) from None
    except domain.DomainValidationError as error:
        raise ContractProblem(error.code, 422, ({"field": "body", "code": "invalid_field"},)) from None


def parse_evaluation(raw: bytes) -> EvaluationRequestModel:
    return parse_model(raw, EvaluationRequestModel)


def parse_decision(raw: bytes) -> DecisionModel:
    return parse_model(raw, DecisionModel)


def parse_policy(raw: bytes) -> PolicyWriteModel:
    return parse_model(raw, PolicyWriteModel)


def encode_model(value: domain.DomainModel | StrictModel) -> dict[str, Any]:
    return value.model_dump(mode="json") if isinstance(value, StrictModel) else domain.domain_to_wire(value)


_TITLES = {400: "Malformed JSON", 401: "Authentication required", 403: "Operation forbidden", 404: "Resource not found", 409: "Resource conflict", 413: "Request too large", 422: "Invalid request", 428: "Precondition required", 429: "Capacity exceeded", 503: "Service unavailable"}


def problem_document(error: ContractProblem, correlation_id: UUID) -> dict[str, Any]:
    return {"type": f"urn:guardrails:problem:{error.code}", "title": _TITLES[error.status], "status": error.status, "code": error.code, "correlation_id": str(correlation_id), "errors": list(error.errors)}


def problem_response(error: ContractProblem, correlation_id: UUID):
    from fastapi.responses import JSONResponse
    return JSONResponse(problem_document(error, correlation_id), status_code=error.status, media_type="application/problem+json")


def install_exception_handlers(app) -> None:
    from fastapi.exceptions import RequestValidationError, ResponseValidationError
    from starlette.exceptions import HTTPException

    @app.exception_handler(ContractProblem)
    async def contract_error(_request, error):
        return problem_response(error, uuid4())

    @app.exception_handler(RequestValidationError)
    async def request_error(_request, _error):
        return problem_response(ContractProblem(errors=({"field": "body", "code": "invalid_field"},)), uuid4())

    @app.exception_handler(ResponseValidationError)
    async def response_error(_request, _error):
        return problem_response(ContractProblem("evaluation_unavailable", 503), uuid4())

    @app.exception_handler(HTTPException)
    async def http_error(_request, error):
        code = "authentication_failed" if error.status_code == 401 else "invalid_request"
        return problem_response(ContractProblem(code, error.status_code), uuid4())


def contract_openapi() -> dict[str, Any]:
    """Generate the foundation contract artifact; this is not a running API."""
    from fastapi import FastAPI
    app = FastAPI(title="Guardrails local proof foundation", version=domain.SCHEMA_VERSION, docs_url=None, redoc_url=None)

    @app.post("/v1/evaluate", response_model=DecisionModel, responses={422: {"model": ProblemModel}})
    def schema_evaluation(body: EvaluationRequestModel):
        raise RuntimeError("schema_only")

    @app.post("/v1/tenants/{tenant_id}/policies/validate", response_model=PolicyValidationModel)
    def schema_policy(tenant_id: str, body: PolicyWriteModel):
        raise RuntimeError("schema_only")

    @app.get("/v1/client-config", response_model=ClientConfigModel)
    def schema_client_config():
        raise RuntimeError("schema_only")

    return app.openapi()
