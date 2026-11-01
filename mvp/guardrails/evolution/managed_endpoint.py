"""Operator-owned, immutable routing and credentials for optional Bedrock jobs."""
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType

from .bedrock import REVISION, AwsCredentials, BedrockAdapter
from .errors import EvolutionError
from .models import (
    Capability,
    Destination,
    PolicyVersion,
    ProviderProfile,
    canonical_json_bytes,
    resource_digest,
)


@dataclass(frozen=True, init=False)
class BedrockEndpoint:
    provider_bytes: bytes
    destination_bytes: bytes
    runtime_zone: str
    region: str
    enabled: bool
    credentials: AwsCredentials = field(repr=False)
    transport: object = field(repr=False)

    def __init__(self, *, provider, destination, runtime_zone, region, credentials,
                 transport=None, enabled=False):
        if (not isinstance(provider, ProviderProfile) or not isinstance(destination, Destination)
                or provider.digest != resource_digest(provider)
                or destination.digest != resource_digest(destination)
                or provider.kind != "evaluator" or provider.vendor != "bedrock"
                or provider.destination_id != destination.destination_id
                or type(runtime_zone) is not str or not runtime_zone
                or type(region) is not str or not region
                or provider.zone != region or destination.zone != region
                or not isinstance(credentials, AwsCredentials) or type(enabled) is not bool):
            raise EvolutionError("validation_failed")
        for name, value in {"provider_bytes": canonical_json_bytes(provider),
                            "destination_bytes": canonical_json_bytes(destination),
                            "runtime_zone": runtime_zone, "region": region,
                            "credentials": credentials, "transport": transport,
                            "enabled": enabled}.items():
            object.__setattr__(self, name, value)

    def resources(self):
        return (ProviderProfile.model_validate_json(self.provider_bytes),
                Destination.model_validate_json(self.destination_bytes))

    def capabilities(self):
        status = "disabled"
        if self.enabled:
            try:
                self.credentials.validate(datetime.now(UTC))
                status = "enabled"
            except EvolutionError:
                status = "unavailable"
        return tuple(Capability(capability_id="managed-bedrock", stage=stage,
            part_kinds=("text",), actions=("allow", "warn", "redact", "block"),
            evaluator_revision=REVISION, max_input_bytes=65536, timeout_ms=500,
            zone=self.runtime_zone, status=status) for stage in ("input", "output"))

    def bind(self, policy, stage):
        from .rules import RulesBinding

        if not isinstance(policy, PolicyVersion):
            raise EvolutionError("policy_unresolved")
        RulesBinding(policy, stage)
        provider, destination = self.resources()
        selected = [row for row in policy.providers if row.provider_id == provider.provider_id]
        destinations = [row for row in policy.resolved_destinations
                        if row.destination_id == destination.destination_id]
        if (len(selected) != 1 or canonical_json_bytes(selected[0]) != self.provider_bytes
                or len(destinations) != 1 or canonical_json_bytes(destinations[0]) != self.destination_bytes):
            raise EvolutionError("provider_denied")
        adapter = BedrockAdapter(policy=policy, stage=stage, runtime_zone=self.runtime_zone,
            region=self.region, credentials_by_ref=MappingProxyType({provider.credential_ref: self.credentials}),
            transport=self.transport, enabled=self.enabled, max_inflight=1)
        for rule in policy.rules:
            if rule.kind == "bedrock":
                if rule.config.provider_id != provider.provider_id:
                    raise EvolutionError("provider_denied")
                if stage in rule.stages:
                    adapter._authorize(rule)
        return adapter
