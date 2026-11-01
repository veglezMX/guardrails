"""Strict, immutable domain records for the evolution service.

JSON arrays become tuple fields, but strings, booleans and numbers are never
coerced. JSON object payloads are recursively frozen after validation. Transport
code must return ``EvolutionError.envelope()`` rather than Pydantic diagnostics,
which may contain the submitted value.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import math
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Mapping, Self, get_args, get_origin
from uuid import UUID as ParsedUUID

from .errors import EvolutionError

from pydantic import (
    AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field,
    JsonValue as PydanticJsonValue, model_validator,
)

Mode = Literal["remote", "gateway", "customer"]
State = Literal["disabled", "observe", "warn", "redact", "block"]
Action = Literal["allow", "warn", "redact", "block"]
Stage = Literal["input", "output", "retrieval", "tool_arguments", "tool_result"]
Role = Literal["platform_admin", "tenant_admin", "author", "approver", "operator", "auditor", "viewer", "workload", "runtime"]
PolicyStatus = Literal["candidate", "approved", "rejected", "revoked"]
Failure = Literal["block", "observe"]
RuleKind = Literal["pii", "secret", "term", "topic", "json_schema", "injection", "image_text", "bedrock"]
STATE_ORDER = {value: index for index, value in enumerate(get_args(State))}
ACTION_ORDER = {value: index for index, value in enumerate(get_args(Action))}
ZERO_DIGEST = "0" * 64


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _uuid(value: str) -> str:
    if str(ParsedUUID(value)) != value:
        raise ValueError("UUID must use canonical lowercase form")
    return value


def _time(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value):
        raise ValueError("Time must be UTC RFC3339 with a Z suffix")
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


UUID = Annotated[str, AfterValidator(_uuid)]
Time = Annotated[str, AfterValidator(_time)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9-]{1,64}$")]
Owner = Annotated[str, Field(pattern=r"^(?:[a-z0-9-]{1,64}|_platform)$")]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]
PositiveInt = Annotated[int, Field(ge=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class FrozenDict(dict):
    """A JSON-compatible mapping whose contents cannot change after validation."""

    def _deny(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("Domain JSON is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _deny

    def __deepcopy__(self, memo: dict) -> FrozenDict:
        return self


class FrozenList(list):
    def _deny(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("Domain JSON is immutable")

    __setitem__ = __delitem__ = append = extend = insert = pop = remove = clear = reverse = sort = __iadd__ = __imul__ = _deny

    def __deepcopy__(self, memo: dict) -> FrozenList:
        return self


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return FrozenList(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def validate_json_value(value: Any, *, max_depth: int = 32, max_nodes: int = 10_000) -> Any:
    nodes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if depth > max_depth or nodes > max_nodes:
            raise ValueError("JSON complexity limit exceeded")
        if type(item) is str:
            item.encode("utf-8", errors="strict")
            return
        if item is None or type(item) in (bool, int):
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("Non-finite numbers are forbidden")
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                visit(child, depth + 1)
            return
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                raise ValueError("JSON object keys must be strings")
            for key in item:
                key.encode("utf-8", errors="strict")
            for child in item.values():
                visit(child, depth + 1)
            return
        raise ValueError("Unsupported JSON value")

    visit(value, 0)
    return value


JsonValue = Annotated[PydanticJsonValue, BeforeValidator(validate_json_value)]
JsonObject = Annotated[dict[str, PydanticJsonValue], BeforeValidator(validate_json_value)]


def strict_json_loads(data: str | bytes | bytearray, *, max_bytes: int = 24 * 1024 * 1024) -> Any:
    if len(data.encode("utf-8") if isinstance(data, str) else data) > max_bytes:
        raise ValueError("JSON body exceeds byte limit")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON keys are forbidden")
            result[key] = value
        return result

    def reject_constant(_: str) -> None:
        raise ValueError("Non-finite numbers are forbidden")

    try:
        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8", errors="strict")
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=reject_constant)
    except (RecursionError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid JSON encoding or depth") from exc
    # The raw request may contain model envelopes around a 32-level payload.
    validate_json_value(value, max_depth=64, max_nodes=100_000)
    return value


def canonical_json_bytes(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, validate_default=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def wire_arrays(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        result = dict(value)
        for name, field in cls.model_fields.items():
            annotation = field.annotation
            if get_origin(annotation) is Literal and name in result:
                options = get_args(annotation)
                if not any(type(result[name]) is type(option) and result[name] == option for option in options):
                    raise ValueError("Literal value has an invalid type or value")
            is_tuple = get_origin(annotation) is tuple or any(get_origin(arg) is tuple for arg in get_args(annotation))
            # Optional Annotated tuples retain the inner Annotated wrapper in
            # Pydantic's field annotation (e.g. Decision.transformed_parts).
            is_tuple = is_tuple or any(
                get_origin(arg) is Annotated and get_origin(get_args(arg)[0]) is tuple
                for arg in get_args(annotation)
            )
            if is_tuple and isinstance(result.get(name), list):
                result[name] = tuple(result[name])
        return result

    @model_validator(mode="after")
    def freeze_payloads(self) -> Self:
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, (dict, list, tuple)):
                object.__setattr__(self, name, _freeze(value))
        return self

    @classmethod
    def model_validate_json(cls, json_data: str | bytes | bytearray, *, strict: bool | None = None, context: Any = None, **kwargs: Any) -> Self:
        return cls.model_validate(strict_json_loads(json_data), strict=strict, context=context, **kwargs)


class StoredRecord(DomainModel):
    created_at: Time = Field(default_factory=utc_now)


class MutableRecord(StoredRecord):
    revision: PositiveInt = 1


class PolicyRef(DomainModel):
    tenant_id: Owner
    policy_id: Identifier
    version: PositiveInt
    digest: Digest


class Tenant(MutableRecord):
    tenant_id: Slug
    display_name: Annotated[str, Field(min_length=1, max_length=120)]
    status: Literal["active", "suspended"] = "active"
    baseline: PolicyRef | None = None
    residency_id: UUID
    quota_id: UUID


class Workload(MutableRecord):
    tenant_id: Slug
    workload_id: Slug
    display_name: Annotated[str, Field(min_length=1, max_length=120)]
    mode: Mode
    status: Literal["active", "suspended"] = "active"
    allowed_stages: Annotated[tuple[Stage, ...], Field(min_length=1, max_length=5)]
    provider_ids: Annotated[tuple[UUID, ...], Field(max_length=256)] = ()
    quota_id: UUID
    required_state: State = "disabled"
    retention_enabled: Literal[False] = False

    @model_validator(mode="after")
    def unique_stages(self) -> Self:
        if len(set(self.allowed_stages)) != len(self.allowed_stages) or len(set(self.provider_ids)) != len(self.provider_ids):
            raise ValueError("Workload collections must be unique")
        return self


class Principal(MutableRecord):
    principal_id: UUID
    tenant_id: Slug | None
    roles: Annotated[tuple[Role, ...], Field(min_length=1, max_length=9)]
    status: Literal["active", "revoked"] = "active"
    display_name: Annotated[str, Field(min_length=1, max_length=120)]

    @model_validator(mode="after")
    def scope_roles(self) -> Self:
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("Roles must be unique")
        if self.tenant_id is None and set(self.roles) != {"platform_admin"}:
            raise ValueError("An unscoped principal may only administer the platform")
        if self.tenant_id is not None and "platform_admin" in self.roles:
            raise ValueError("Platform roles cannot be tenant-scoped")
        return self


class Credential(MutableRecord):
    credential_id: UUID
    principal_id: UUID
    tenant_id: Slug | None
    workload_id: Slug | None = None
    token_hash: Digest
    expires_at: Time
    revoked_at: Time | None = None
    previous_valid_until: Time | None = None

    @model_validator(mode="after")
    def lifetime(self) -> Self:
        if self.workload_id is not None and self.tenant_id is None:
            raise ValueError("A workload credential requires tenant scope")
        created, expiry = parse_time(self.created_at), parse_time(self.expires_at)
        maximum = 30 * 86400 if self.workload_id else 86400
        if not 0 < (expiry - created).total_seconds() <= maximum:
            raise ValueError("Credential lifetime exceeds the permitted bound")
        if self.previous_valid_until and parse_time(self.previous_valid_until) > expiry:
            raise ValueError("Credential overlap exceeds expiration")
        return self


class AdminSession(MutableRecord):
    session_id: UUID
    principal_id: UUID
    secret_hash: Digest
    csrf_hash: Digest
    expires_at: Time
    idle_expires_at: Time
    revoked_at: Time | None = None

    @model_validator(mode="after")
    def lifetime(self) -> Self:
        created, expiry, idle = map(parse_time, (self.created_at, self.expires_at, self.idle_expires_at))
        if not 0 < (expiry - created).total_seconds() <= 8 * 3600 or idle > expiry or idle <= created:
            raise ValueError("Invalid session lifetime")
        # Sliding idle expiry is checked relative to the service clock by IdentityService.
        return self


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class PiiConfig(DomainModel):
    entities: Annotated[tuple[Literal["email", "international_phone", "payment_card", "ipv4"], ...], Field(min_length=1, max_length=4)]


class SecretConfig(DomainModel):
    detectors: Annotated[tuple[Literal["private_key", "aws_access_key", "github_token"], ...], Field(min_length=1, max_length=3)]


class TermConfig(DomainModel):
    list_id: UUID


class TopicConfig(DomainModel):
    list_id: UUID


SCHEMA_KEYWORDS = frozenset({"type", "properties", "required", "additionalProperties", "items", "enum", "const", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "anyOf"})
SCHEMA_TYPES = frozenset({"null", "boolean", "object", "array", "number", "integer", "string"})


def validate_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Validate the bounded schema dialect before any evaluation library sees it."""
    validate_json_value(schema, max_depth=64, max_nodes=10_000)
    properties = 0

    def visit(node: Any, depth: int) -> None:
        nonlocal properties
        if not isinstance(node, dict) or depth > 32:
            raise ValueError("Invalid schema or schema depth exceeded")
        if set(node) - SCHEMA_KEYWORDS:
            raise ValueError("Unsupported schema keyword")
        for key, value in node.items():
            if key == "type" and (not isinstance(value, str) or value not in SCHEMA_TYPES):
                raise ValueError("Invalid schema type")
            if key == "properties":
                if not isinstance(value, dict):
                    raise ValueError("Schema properties must be an object")
                properties += len(value)
                if properties > 256:
                    raise ValueError("Schema property limit exceeded")
                for child in value.values():
                    visit(child, depth + 1)
            elif key == "required":
                if not isinstance(value, (list, tuple)) or any(type(item) is not str for item in value) or len(value) != len(set(value)):
                    raise ValueError("Invalid required property list")
            elif key == "additionalProperties":
                if type(value) is not bool:
                    raise ValueError("additionalProperties must be boolean")
            elif key == "items":
                visit(value, depth + 1)
            elif key == "anyOf":
                if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 64:
                    raise ValueError("Invalid schema alternative count")
                for child in value:
                    visit(child, depth + 1)
            elif key in {"minLength", "maxLength", "minItems", "maxItems"}:
                if type(value) is not int or value < 0:
                    raise ValueError("Invalid schema bound")
            elif key in {"minimum", "maximum"}:
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("Invalid numeric schema bound")
            elif key == "enum":
                if not isinstance(value, (list, tuple)) or not value:
                    raise ValueError("Invalid schema enum")
                encoded = [canonical_json_bytes(item) for item in value]
                if len(encoded) != len(set(encoded)):
                    raise ValueError("Schema enum values must be unique")
        for lower, upper in (("minimum", "maximum"), ("minLength", "maxLength"), ("minItems", "maxItems")):
            if lower in node and upper in node and node[lower] > node[upper]:
                raise ValueError("Schema bounds are contradictory")

    visit(schema, 1)
    return schema


class JsonSchemaConfig(DomainModel):
    model_config = ConfigDict(serialize_by_alias=True)
    schema_value: Annotated[JsonObject, AfterValidator(validate_schema)] = Field(alias="schema")

    @property
    def schema(self) -> JsonObject:
        return self.schema_value


class InjectionConfig(DomainModel):
    threshold: Annotated[float, Field(ge=0, le=1)] = 0.90
    max_tokens: Literal[512] = 512


class ImageTextConfig(DomainModel):
    languages: Annotated[tuple[Literal["eng", "spa"], ...], Field(min_length=1, max_length=2)]


class BedrockConfig(DomainModel):
    provider_id: UUID
    guardrail_id: Identifier
    guardrail_version: Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]


RuleConfig = PiiConfig | SecretConfig | TermConfig | TopicConfig | JsonSchemaConfig | InjectionConfig | ImageTextConfig | BedrockConfig
RULE_CONFIG_TYPES = {"pii": PiiConfig, "secret": SecretConfig, "term": TermConfig, "topic": TopicConfig, "json_schema": JsonSchemaConfig, "injection": InjectionConfig, "image_text": ImageTextConfig, "bedrock": BedrockConfig}


class Rule(DomainModel):
    rule_id: Identifier
    kind: RuleKind
    stages: Annotated[tuple[Stage, ...], Field(min_length=1, max_length=5)]
    action: Action
    mandatory: bool = False
    minimum_state: State = "disabled"
    config: RuleConfig
    evaluator_revision: Identifier

    @model_validator(mode="before")
    @classmethod
    def parse_config(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("kind") in RULE_CONFIG_TYPES:
            value = dict(value)
            config_type = RULE_CONFIG_TYPES[value["kind"]]
            value["config"] = config_type.model_validate(value.get("config"))
        return value

    @model_validator(mode="after")
    def config_matches_kind(self) -> Self:
        if type(self.config) is not RULE_CONFIG_TYPES[self.kind]:
            raise ValueError("Rule kind and configuration disagree")
        if len(self.stages) != len(set(self.stages)):
            raise ValueError("Rule stages must be unique")
        for name in ("entities", "detectors", "languages"):
            values = getattr(self.config, name, ())
            if len(values) != len(set(values)):
                raise ValueError("Rule configuration entries must be unique")
        if self.mandatory and self.action == "allow":
            raise ValueError("A mandatory rule cannot have an allow action")
        if self.kind == "bedrock" and set(self.stages) - {"input", "output"}:
            raise ValueError("Managed engine supports input and output only")
        return self


class VersionedResource(StoredRecord):
    tenant_id: Owner
    version: PositiveInt = 1
    digest: Digest = ZERO_DIGEST


class ListSet(VersionedResource):
    list_id: UUID
    kind: Literal["destination", "provider", "tool", "content"]
    allow: Annotated[tuple[Annotated[str, Field(min_length=1, max_length=256)], ...], Field(max_length=256)] = ()
    deny: Annotated[tuple[Annotated[str, Field(min_length=1, max_length=256)], ...], Field(max_length=256)] = ()
    allow_enabled: bool

    @model_validator(mode="before")
    @classmethod
    def default_allow(cls, value: Any) -> Any:
        if isinstance(value, dict) and "allow_enabled" not in value:
            value = {**value, "allow_enabled": value.get("kind") != "content"}
        return value

    @model_validator(mode="after")
    def entries(self) -> Self:
        for values in (self.allow, self.deny):
            if len(values) != len(set(values)):
                raise ValueError("List entries must be unique")
            for item in values:
                if self.kind in {"destination", "provider"}:
                    _uuid(item)
                if self.kind == "tool" and len(item) > 128:
                    raise ValueError("Tool names exceed the supported bound")
                if self.kind == "content":
                    if not item.startswith("term:") and item not in {"topic:finance", "topic:medical", "topic:software"}:
                        raise ValueError("Unsupported content list entry")
                    if item == "term:":
                        raise ValueError("Content literals cannot be empty")
        return self


class Destination(VersionedResource):
    destination_id: UUID
    name: Identifier
    scheme: Literal["https"] = "https"
    host: Annotated[str, Field(min_length=1, max_length=253)]
    port: Annotated[int, Field(ge=1, le=65535)] = 443
    path_prefix: Annotated[str, Field(min_length=1, max_length=2048)]
    cidrs: Annotated[tuple[str, ...], Field(min_length=1, max_length=64)]
    zone: Identifier
    purpose: Literal["evaluation", "inference", "telemetry", "control", "backup"]
    tls_ca_ref: Identifier

    @model_validator(mode="after")
    def endpoint(self) -> Self:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", self.host) or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in self.host.split(".")):
            raise ValueError("Destination must use an exact lowercase DNS name")
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            pass
        else:
            raise ValueError("Destination host must be a DNS name")
        if not self.path_prefix.startswith("/") or any(char in self.path_prefix for char in "?#@\\%") or any(piece in {".", ".."} for piece in self.path_prefix.split("/")):
            raise ValueError("Destination path must be a literal absolute prefix")
        for cidr in self.cidrs:
            ipaddress.ip_network(cidr, strict=True)
        return self


class ProviderProfile(VersionedResource):
    provider_id: UUID
    kind: Literal["model", "evaluator"]
    vendor: Literal["ollama", "bedrock", "local_classifier", "local_rules"]
    model_id: Identifier
    artifact_digest: Digest | None = None
    destination_id: UUID | None = None
    zone: Identifier
    credential_ref: Identifier | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def engine_destination(self) -> Self:
        if self.vendor == "local_rules" and (self.destination_id is not None or self.credential_ref is not None):
            raise ValueError("In-process rules do not use network destinations or credentials")
        if self.vendor in {"ollama", "bedrock"} and self.destination_id is None:
            raise ValueError("Network providers require a registered destination")
        return self


class ToolSpec(VersionedResource):
    tool_id: UUID
    name: Identifier
    argument_schema: Annotated[JsonObject, AfterValidator(validate_schema)]
    result_schema: Annotated[JsonObject, AfterValidator(validate_schema)] | None = None
    sensitive_paths: Annotated[tuple[str, ...], Field(max_length=256)] = ()

    @model_validator(mode="after")
    def pointers(self) -> Self:
        for pointer in self.sensitive_paths:
            if pointer and (not pointer.startswith("/") or re.search(r"~(?![01])", pointer)):
                raise ValueError("Invalid JSON pointer")
        return self


class ResidencyProfile(VersionedResource):
    residency_id: UUID
    allowed_zones: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=64)] = ("local-shared",)
    destination_lists: Annotated[tuple[UUID, ...], Field(max_length=64)] = ()
    provider_lists: Annotated[tuple[UUID, ...], Field(max_length=64)] = ()
    tool_lists: Annotated[tuple[UUID, ...], Field(max_length=64)] = ()
    content_lists: Annotated[tuple[UUID, ...], Field(max_length=64)] = ()
    external_processing: bool = False
    raw_retention: Literal[False] = False
    metadata_retention_days: Annotated[int, Field(ge=1, le=3650)] = 30

    @model_validator(mode="after")
    def unique_values(self) -> Self:
        for name in ("allowed_zones", "destination_lists", "provider_lists", "tool_lists", "content_lists"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError("Residency collections must be unique")
        return self


class Quota(VersionedResource):
    quota_id: UUID
    workload_id: Slug | None = None
    requests_per_minute: Annotated[int, Field(ge=1, le=1_000_000)] = 60
    burst: Annotated[int, Field(ge=1, le=1_000_000)] = 10
    max_inflight: Annotated[int, Field(ge=1, le=10_000)] = 4
    daily_tokens: PositiveInt | None = None
    daily_cost_microunits: PositiveInt | None = None


class PolicyVersion(StoredRecord):
    tenant_id: Owner
    policy_id: Identifier
    version: PositiveInt
    author_id: UUID
    parents: Annotated[tuple[PolicyRef, ...], Field(max_length=3)] = ()
    rules: Annotated[tuple[Rule, ...], Field(max_length=64)]
    residency: ResidencyProfile
    resolved_lists: Annotated[tuple[ListSet, ...], Field(max_length=256)] = ()
    resolved_destinations: Annotated[tuple[Destination, ...], Field(max_length=256)] = ()
    providers: Annotated[tuple[ProviderProfile, ...], Field(max_length=256)] = ()
    tools: Annotated[tuple[ToolSpec, ...], Field(max_length=256)] = ()
    quota: Quota
    failure_behavior: Failure = "block"
    compiled_digest: Digest = ZERO_DIGEST
    compiler_version: Identifier = "compiler-v2.1"
    status: PolicyStatus = "candidate"

    @model_validator(mode="after")
    def unique_resources(self) -> Self:
        for collection, attr in ((self.rules, "rule_id"), (self.resolved_lists, "list_id"), (self.resolved_destinations, "destination_id"), (self.providers, "provider_id"), (self.tools, "tool_id")):
            ids = [getattr(item, attr) for item in collection]
            if len(ids) != len(set(ids)):
                raise ValueError("A policy may embed only one version of each resource ID")
        return self

    def reference(self) -> PolicyRef:
        return PolicyRef(tenant_id=self.tenant_id, policy_id=self.policy_id, version=self.version, digest=self.compiled_digest)


class Approval(StoredRecord):
    approval_id: UUID
    tenant_id: Owner
    policy: PolicyRef
    compiled_digest: Digest
    actor_id: UUID
    outcome: Literal["approve", "reject", "revoke"]
    reason: Reason
    evidence_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def policy_scope(self) -> Self:
        if self.tenant_id != self.policy.tenant_id or self.compiled_digest != self.policy.digest:
            raise ValueError("Approval must identify exact same-tenant policy bytes")
        return self


class Binding(StoredRecord):
    tenant_id: Slug
    workload_id: Slug
    revision: PositiveInt
    policy: PolicyRef
    state: State
    release_id: UUID
    activated_by: UUID
    previous_revision: PositiveInt | None = None
    effective_after: Time
    reason: Reason
    status: Literal["pending", "applied", "failed", "superseded"] = "pending"

    @model_validator(mode="after")
    def binding_scope(self) -> Self:
        if self.policy.tenant_id != self.tenant_id:
            raise ValueError("Bindings must use same-tenant policies")
        if self.previous_revision is not None and self.previous_revision >= self.revision:
            raise ValueError("Binding revisions must advance")
        return self


class Release(MutableRecord):
    release_id: UUID
    tenant_id: Slug
    policy: PolicyRef
    workload_ids: Annotated[tuple[Slug, ...], Field(min_length=1, max_length=100)]
    stage: Literal["canary", "full"]
    state: State
    expected_revisions: dict[Slug, NonNegativeInt]
    status: Literal["pending", "applied", "failed"] = "pending"
    actor_id: UUID
    reason: Reason

    @model_validator(mode="after")
    def release_scope(self) -> Self:
        if self.tenant_id != self.policy.tenant_id or set(self.workload_ids) != set(self.expected_revisions) or len(self.workload_ids) != len(set(self.workload_ids)):
            raise ValueError("Release scope and expected revisions must agree")
        return self


class RuntimeReceipt(StoredRecord):
    runtime_id: UUID
    tenant_id: Slug
    workload_id: Slug
    binding_revision: PositiveInt
    snapshot_generation: PositiveInt
    digest: Digest
    applied_at: Time
    health: Literal["ready", "stale", "failed"]


class Snapshot(StoredRecord):
    schema_version: Literal["guardrails-v2"] = "guardrails-v2"
    runtime_id: UUID
    tenant_id: Slug
    generation: PositiveInt
    issued_at: Time
    expires_at: Time
    bindings: Annotated[tuple[Binding, ...], Field(max_length=1000)]
    policies: Annotated[tuple[PolicyVersion, ...], Field(max_length=1000)]
    revoked_digests: Annotated[tuple[Digest, ...], Field(max_length=10000)] = ()
    digest: Digest
    key_id: Identifier
    signature: str

    @model_validator(mode="after")
    def snapshot_scope(self) -> Self:
        if any(item.tenant_id != self.tenant_id for item in (*self.bindings, *self.policies)):
            raise ValueError("Snapshot cannot cross tenant boundaries")
        if parse_time(self.expires_at) <= parse_time(self.issued_at):
            raise ValueError("Snapshot must have a positive lifetime")
        validate_signature(self.signature)
        return self


def validate_signature(value: str) -> str:
    try:
        if len(base64.b64decode(value, validate=True)) != 64:
            raise ValueError("Signature must be Ed25519")
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid Ed25519 signature encoding") from exc
    return value


class ToolCall(DomainModel):
    call_id: Identifier
    name: Identifier
    arguments: JsonObject
    tool_digest: Digest
    result_for_call_id: Identifier | None = None


class ContentPart(DomainModel):
    part_id: Identifier
    kind: Literal["text", "json", "image"]
    role: Literal["system", "user", "assistant", "retrieval", "tool"]
    text: str | None = None
    value: JsonValue | None = None
    media_type: Literal["image/png", "image/jpeg"] | None = None
    data_base64: str | None = None
    tool: ToolCall | None = None
    source_id: Identifier | None = None

    @model_validator(mode="after")
    def payload(self) -> Self:
        if self.kind == "text":
            if self.text is None or self.value is not None or self.media_type is not None or self.data_base64 is not None:
                raise ValueError("Text parts require exactly one text payload")
            if len(self.text.encode("utf-8")) > 65536:
                raise ValueError("Text byte limit exceeded")
        elif self.kind == "json":
            if "value" not in self.model_fields_set or self.text is not None or self.media_type is not None or self.data_base64 is not None:
                raise ValueError("JSON parts require exactly one JSON payload")
            if len(canonical_json_bytes(self.value)) > 65536:
                raise ValueError("JSON byte limit exceeded")
        else:
            if self.media_type is None or self.data_base64 is None or self.text is not None or self.value is not None:
                raise ValueError("Image parts require inline bytes and media type")
            if len(self.data_base64) > 4 * ((4 * 1024 * 1024 + 2) // 3):
                raise ValueError("Image byte limit exceeded")
            try:
                decoded = base64.b64decode(self.data_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("Invalid image byte encoding") from exc
            if not decoded or len(decoded) > 4 * 1024 * 1024:
                raise ValueError("Image byte limit exceeded")
            # Pixel, format and decompression checks belong to the isolated decoder.
        return self


class EvaluationRequest(DomainModel):
    request_id: UUID
    correlation_id: UUID
    tenant_id: Slug
    workload_id: Slug
    stage: Stage
    parts: Annotated[tuple[ContentPart, ...], Field(min_length=1, max_length=64)]
    context_id: UUID | None = None
    provider_id: UUID | None = None
    deadline_ms: Annotated[int, Field(ge=1, le=180000)]
    schema_version: Literal["guardrails-v2"] = "guardrails-v2"

    @model_validator(mode="after")
    def request_bounds(self) -> Self:
        if len({part.part_id for part in self.parts}) != len(self.parts):
            raise ValueError("Part IDs must be unique")
        if sum(part.kind == "image" for part in self.parts) > 4:
            raise ValueError("Image count exceeds the request limit")
        total = sum(len(part.text.encode("utf-8")) if part.kind == "text" else len(canonical_json_bytes(part.value)) if part.kind == "json" else 0 for part in self.parts)
        total += sum(len(canonical_json_bytes(part.tool.arguments)) for part in self.parts if part.tool)
        if total > 65536:
            raise ValueError("Combined text and JSON byte limit exceeded")
        if self.stage != "input" and self.context_id is None:
            raise ValueError("Subsequent stages require a pinned evaluation context")
        if self.stage in {"input", "output"} and self.provider_id is None:
            raise ValueError("Model-bound stages require a registered provider")
        if self.stage == "input" and self.context_id is not None:
            raise ValueError("Input must begin a new evaluation context")
        return self


class EvaluationContext(StoredRecord):
    context_id: UUID
    tenant_id: Slug
    workload_id: Slug
    policy: PolicyRef
    binding_revision: PositiveInt
    snapshot_generation: PositiveInt
    provider_id: UUID | None = None
    expires_at: Time
    evaluated_call_ids: Annotated[tuple[Identifier, ...], Field(max_length=256)] = ()

    @model_validator(mode="after")
    def context_scope(self) -> Self:
        if self.policy.tenant_id != self.tenant_id:
            raise ValueError("Evaluation context must use a same-tenant policy")
        return self


class Finding(DomainModel):
    rule_id: Identifier
    category: Identifier
    part_id: Identifier
    reason_code: Identifier
    start: NonNegativeInt | None = None
    end: NonNegativeInt | None = None
    json_pointer: str | None = None
    score: Annotated[float, Field(ge=0, le=1)] | None = None
    evaluator_revision: Identifier

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.reason_code not in DECISION_REASON_CODES:
            raise ValueError("Unknown finding reason code")
        if (self.start is None) != (self.end is None) or (self.start is not None and self.end <= self.start):
            raise ValueError("Finding offsets must describe a positive half-open interval")
        if self.json_pointer and (not self.json_pointer.startswith("/") or re.search(r"~(?![01])", self.json_pointer)):
            raise ValueError("Invalid finding JSON pointer")
        return self


class Decision(DomainModel):
    decision_id: UUID
    request_id: UUID
    context_id: UUID | None = None
    tenant_id: Slug
    workload_id: Slug
    stage: Stage
    policy: PolicyRef | None = None
    binding_revision: PositiveInt | None = None
    snapshot_generation: PositiveInt | None = None
    action: Action
    would_be_action: Action
    reason_codes: Annotated[tuple[str, ...], Field(max_length=128)] = ()
    findings: Annotated[tuple[Finding, ...], Field(max_length=10000)] = ()
    transformed_parts: Annotated[tuple[ContentPart, ...], Field(max_length=64)] | None = None
    evaluation_ms: Annotated[float, Field(ge=0)]
    audit_status: Literal["persisted", "spooled", "failed"]
    evaluator_revisions: Annotated[tuple[str, ...], Field(max_length=64)] = ()

    @model_validator(mode="after")
    def safe_release(self) -> Self:
        if set(self.reason_codes) - DECISION_REASON_CODES:
            raise ValueError("Unknown decision reason code")
        if (self.action == "redact") != (self.transformed_parts is not None):
            raise ValueError("Only redaction decisions include transformed content")
        if self.policy is None and self.action != "block":
            raise ValueError("Unknown policy cannot authorize content release")
        if self.policy is not None and self.policy.tenant_id != self.tenant_id:
            raise ValueError("Decision must use a same-tenant policy")
        if self.audit_status == "failed" and self.action != "block":
            raise ValueError("Failed mandatory audit cannot authorize release")
        return self


AUDIT_METADATA_KEYS = frozenset({"resource_type", "resource_id", "operation", "revision", "previous_revision", "release_id", "runtime_id", "snapshot_generation", "outcome", "status", "event_count", "retention_days", "elapsed_ms", "policy_id", "policy_version", "credential_id", "principal_id", "stage", "evaluator_revision", "failure_code", "deployment_mode", "schema_version"})


class AuditEvent(StoredRecord):
    event_id: UUID
    tenant_id: Owner
    workload_id: Slug | None = None
    actor_id: UUID | None = None
    event_type: Identifier
    correlation_id: UUID
    decision_id: UUID | None = None
    policy_digest: Digest | None = None
    binding_revision: PositiveInt | None = None
    reason_codes: Annotated[tuple[str, ...], Field(max_length=128)] = ()
    action: Action | None = None
    occurred_at: Time
    previous_hash: Digest | None = None
    event_hash: Digest
    raw_content_retained: Literal[False] = False
    metadata: dict[str, str | int | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def safe_metadata(self) -> Self:
        if set(self.metadata) - AUDIT_METADATA_KEYS:
            raise ValueError("Audit metadata keys must belong to the safe allowlist")
        if any(isinstance(value, str) and len(value) > 128 for value in self.metadata.values()):
            raise ValueError("Audit metadata value exceeds the safe bound")
        return self


class RuntimeInstall(MutableRecord):
    runtime_id: UUID
    tenant_id: Slug
    zone: Identifier
    mode: Mode
    public_key_id: Identifier
    version: Identifier
    telemetry: Literal["none", "aggregate", "metadata"]
    sync_interval_s: Annotated[int, Field(ge=1, le=86400)] = 30
    offline_max_age_s: Annotated[int, Field(ge=1, le=86400)] = 86400
    status: Literal["registered", "ready", "stale", "revoked"] = "registered"
    last_seen_at: Time | None = None

    @model_validator(mode="before")
    @classmethod
    def shared_defaults(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("mode") in {"remote", "gateway"}:
            value = {"sync_interval_s": 1, "offline_max_age_s": 60, **value}
        return value

    @model_validator(mode="after")
    def online_bounds(self) -> Self:
        if self.mode != "customer" and (self.sync_interval_s != 1 or self.offline_max_age_s > 60):
            raise ValueError("Shared runtimes require one-second polling and online leases")
        return self


class ArtifactRef(DomainModel):
    name: Identifier
    version: Identifier
    source_url: Annotated[str, Field(min_length=1, max_length=2048)]
    sha256: Digest
    license: Identifier
    size_bytes: PositiveInt
    kind: Literal["image", "wheel", "model", "binary", "fixture"]


class Bundle(StoredRecord):
    bundle_id: UUID
    runtime_id: UUID
    snapshot: Snapshot
    artifacts: Annotated[tuple[ArtifactRef, ...], Field(max_length=1000)]
    minimum_runtime_version: Identifier
    revocation_epoch: NonNegativeInt
    expires_at: Time
    digest: Digest
    signature: Annotated[str, AfterValidator(validate_signature)]

    @model_validator(mode="after")
    def bundle_scope(self) -> Self:
        if self.runtime_id != self.snapshot.runtime_id or parse_time(self.expires_at) > parse_time(self.snapshot.expires_at):
            raise ValueError("Bundle runtime and expiry must agree with its snapshot")
        if len(canonical_json_bytes(self)) > 10 * 1024 * 1024:
            raise ValueError("Bundle manifest exceeds the byte limit")
        return self


class Capability(DomainModel):
    capability_id: Identifier
    stage: Stage
    part_kinds: Annotated[tuple[Literal["text", "json", "image"], ...], Field(min_length=1, max_length=3)]
    actions: Annotated[tuple[Action, ...], Field(min_length=1, max_length=4)]
    evaluator_revision: Identifier
    max_input_bytes: PositiveInt
    timeout_ms: PositiveInt
    zone: Identifier
    status: Literal["enabled", "disabled", "unavailable"]


class StreamSession(DomainModel):
    stream_id: UUID
    context_id: UUID
    state: Literal["collecting", "evaluating", "releasing", "blocked", "closed"]
    received_bytes: Annotated[int, Field(ge=0, le=256 * 1024)]
    deadline_at: Time
    finish_reason: str | None = None


class CaseResult(DomainModel):
    case_id: Identifier
    requirement_id: Identifier
    status: Literal["pass", "fail", "skip"]
    duration_ms: Annotated[float, Field(ge=0)]
    assertions: NonNegativeInt
    reason_codes: Annotated[tuple[str, ...], Field(max_length=128)] = ()


class EvidenceReport(StoredRecord):
    run_id: UUID
    source_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40,64}$")]
    source_digest: Digest
    lock_digest: Digest
    profile: Identifier
    phase: Annotated[int, Field(ge=1, le=6)]
    started_at: Time
    finished_at: Time
    cases: Annotated[tuple[CaseResult, ...], Field(min_length=1, max_length=10000)]
    artifact_digests: Annotated[tuple[ArtifactRef, ...], Field(max_length=1000)]
    status: Literal["pass", "fail"]
    environment: dict[str, str]
    key_id: Identifier
    signature: Annotated[str, AfterValidator(validate_signature)]

    @model_validator(mode="after")
    def trustworthy_summary(self) -> Self:
        if self.status == "pass" and any(case.status != "pass" or case.assertions == 0 for case in self.cases):
            raise ValueError("Passing evidence requires passing asserted cases")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("Evidence case IDs must be unique")
        if parse_time(self.finished_at) < parse_time(self.started_at):
            raise ValueError("Evidence finish must follow start")
        if set(self.environment) - {"python", "platform", "architecture", "profile", "runner_version", "postgresql", "kubernetes", "minikube", "cpu_count", "memory_bytes", "manifest_sha256", "tenant_id", "release_id", "policy_digest"}:
            raise ValueError("Evidence environment keys must be allowlisted")
        if "manifest_sha256" in self.environment and not re.fullmatch(r"[0-9a-f]{64}", self.environment["manifest_sha256"]):
            raise ValueError("Evidence manifest digest must be lowercase SHA-256")
        if "tenant_id" in self.environment and not re.fullmatch(r"[a-z0-9-]{1,64}", self.environment["tenant_id"]):
            raise ValueError("Evidence tenant scope must be a canonical slug")
        if "release_id" in self.environment:
            _uuid(self.environment["release_id"])
        if "policy_digest" in self.environment and not re.fullmatch(r"[0-9a-f]{64}", self.environment["policy_digest"]):
            raise ValueError("Evidence policy digest must be lowercase SHA-256")
        return self


ERROR_STATUS = {
    **dict.fromkeys(("invalid_json", "invalid_cursor", "unsupported_surface"), 400),
    **dict.fromkeys(("authentication_failed", "session_expired"), 401),
    **dict.fromkeys(("forbidden", "self_approval", "residency_denied", "destination_denied", "provider_denied", "tool_denied"), 403),
    "resource_not_found": 404,
    **dict.fromkeys(("revision_conflict", "idempotency_conflict", "approval_required", "policy_revoked", "floor_conflict", "capability_missing"), 409),
    "payload_too_large": 413,
    **dict.fromkeys(("validation_failed", "composition_conflict", "inspection_incomplete"), 422),
    "precondition_required": 428,
    **dict.fromkeys(("quota_exceeded", "queue_full"), 429),
    **dict.fromkeys(("policy_unresolved", "snapshot_stale", "evaluator_unavailable", "audit_failed", "context_invalid", "unsafe_transformation"), 503),
    **dict.fromkeys(("evaluation_timeout", "upstream_timeout"), 504),
}
DECISION_REASON_CODES = frozenset(ERROR_STATUS) | {"pii_detected", "secret_detected", "content_denied", "content_not_allowed", "schema_invalid", "injection_detected", "image_text_detected", "disabled", "inspection_scope_image_text"}
ErrorCode = Literal["invalid_json", "invalid_cursor", "unsupported_surface", "authentication_failed", "session_expired", "forbidden", "self_approval", "residency_denied", "destination_denied", "provider_denied", "tool_denied", "resource_not_found", "revision_conflict", "idempotency_conflict", "approval_required", "policy_revoked", "floor_conflict", "capability_missing", "payload_too_large", "validation_failed", "composition_conflict", "inspection_incomplete", "precondition_required", "quota_exceeded", "queue_full", "policy_unresolved", "snapshot_stale", "evaluator_unavailable", "audit_failed", "context_invalid", "unsafe_transformation", "evaluation_timeout", "upstream_timeout"]


class Error(DomainModel):
    code: ErrorCode
    message: Annotated[str, Field(min_length=1, max_length=500)]
    correlation_id: UUID
    retryable: bool = False
    details: dict[str, str | int] = Field(default_factory=dict)



def resource_digest(resource: VersionedResource) -> str:
    data = resource.model_dump(mode="json", exclude={"digest"})
    for name in ("allow", "deny", "cidrs", "allowed_zones", "destination_lists", "provider_lists", "tool_lists", "content_lists", "sensitive_paths"):
        if name in data:
            data[name] = sorted(data[name])
    return hashlib.sha256(canonical_json_bytes(data)).hexdigest()


def with_resource_digest(resource: VersionedResource) -> Any:
    return type(resource).model_validate({**resource.model_dump(mode="python"), "digest": resource_digest(resource)})
