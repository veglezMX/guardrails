"""Validated configuration from a mounted JSON file and a closed env mapping."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .models import UUID, DomainModel, EvolutionError, Mode, strict_json_loads


class ServiceConfig(DomainModel):
    mode: Mode
    database_url_file: str
    tls_cert_file: str
    tls_key_file: str
    tls_ca_file: str
    signing_key_file: str | None = None
    backup_key_file: str | None = None
    evaluator_config_file: str | None = None
    runtime_id: UUID
    zone: Annotated[str, Field(min_length=1, max_length=128)] = "local-shared"
    snapshot_ttl_s: Annotated[int, Field(ge=1, le=60)] = 60
    sync_interval_s: Annotated[int, Field(ge=1, le=86400)] = 1
    text_budget_ms: Annotated[int, Field(ge=1, le=10000)] = 500
    classifier_budget_ms: Annotated[int, Field(ge=1, le=10000)] = 3000
    image_budget_ms: Annotated[int, Field(ge=1, le=30000)] = 10000
    audit_reserve_ms: Annotated[int, Field(ge=1, le=1000)] = 100
    queue_limit: Annotated[int, Field(ge=1, le=10000)] = 32
    worker_count: Annotated[int, Field(ge=1, le=256)] = 4
    metadata_retention_days: Annotated[int, Field(ge=1, le=3650)] = 30
    external_processing: bool = False
    telemetry: Literal["none", "aggregate", "metadata"] = "none"

    @model_validator(mode="after")
    def safe_configuration(self) -> Self:
        for name in ("database_url_file", "tls_cert_file", "tls_key_file", "tls_ca_file", "signing_key_file", "backup_key_file", "evaluator_config_file"):
            value = getattr(self, name)
            if value is not None and (not value or not Path(value).is_absolute() or "\x00" in value):
                raise ValueError("Secret and certificate references must be absolute mounted paths")
        if self.audit_reserve_ms >= min(self.text_budget_ms, self.classifier_budget_ms, self.image_budget_ms):
            raise ValueError("Audit reserve must fit inside each evaluation budget")
        if self.mode != "customer" and self.sync_interval_s != 1:
            raise ValueError("Shared runtimes must poll at one-second intervals")
        return self


ENV_FIELDS = {
    "GUARDRAILS_MODE": "mode",
    "GUARDRAILS_DATABASE_URL_FILE": "database_url_file",
    "GUARDRAILS_TLS_CERT_FILE": "tls_cert_file",
    "GUARDRAILS_TLS_KEY_FILE": "tls_key_file",
    "GUARDRAILS_TLS_CA_FILE": "tls_ca_file",
    "GUARDRAILS_SIGNING_KEY_FILE": "signing_key_file",
    "GUARDRAILS_BACKUP_KEY_FILE": "backup_key_file",
    "GUARDRAILS_EVALUATOR_CONFIG_FILE": "evaluator_config_file",
    "GUARDRAILS_RUNTIME_ID": "runtime_id",
    "GUARDRAILS_ZONE": "zone",
    "GUARDRAILS_TELEMETRY": "telemetry",
}


def load_config(path: str | Path, environ: Mapping[str, str] | None = None) -> ServiceConfig:
    environment = os.environ if environ is None else environ
    if any(name.startswith("GUARDRAILS_") and name not in ENV_FIELDS for name in environment):
        raise EvolutionError("validation_failed")
    try:
        config_path = Path(path)
        if config_path.stat().st_size > 64 * 1024:
            raise ValueError("Configuration byte limit exceeded")
        value = strict_json_loads(config_path.read_bytes(), max_bytes=64 * 1024)
        if not isinstance(value, dict):
            raise TypeError("Configuration must be an object")
        value.update({field: environment[name] for name, field in ENV_FIELDS.items() if name in environment})
        return ServiceConfig.model_validate(value)
    except (OSError, ValueError, TypeError) as exc:
        raise EvolutionError("validation_failed") from exc
