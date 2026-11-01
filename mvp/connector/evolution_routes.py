"""Private, operator-installed mapping from approved policy bytes to model routes.

The connector consumes public resource contracts and never queries mutable
configuration or the latest policy. Stage newly approved digests in this manifest
before rollout; an unlisted policy fails closed at the socket boundary.
"""
from __future__ import annotations

import os
import stat
from typing import Annotated
from urllib.parse import urlsplit

from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import (
    UUID,
    Destination,
    DomainModel,
    Identifier,
    PolicyRef,
    ProviderProfile,
    Slug,
    resource_digest,
    strict_json_loads,
)
from pydantic import Field, model_validator


class LocalService(DomainModel):
    hostname: Identifier
    addresses: Annotated[tuple[str, ...], Field(min_length=1, max_length=64)]


class ApprovedModelRoute(DomainModel):
    policy: PolicyRef
    provider: ProviderProfile
    destination: Destination
    external_processing: bool = False

    @model_validator(mode="after")
    def exact_resources(self):
        if (self.provider.kind != "model" or not self.provider.enabled
                or self.provider.destination_id != self.destination.destination_id
                or self.destination.purpose != "inference"
                or self.provider.zone != self.destination.zone):
            raise ValueError("Invalid approved model route")
        for resource in (self.provider, self.destination):
            if resource.tenant_id not in {self.policy.tenant_id, "_platform"} or resource.digest != resource_digest(resource):
                raise ValueError("Invalid exact resource bytes")
        return self


class RouteManifest(DomainModel):
    tenant_id: Slug
    workload_id: Slug
    routes: Annotated[tuple[ApprovedModelRoute, ...], Field(min_length=1, max_length=256)]
    ca_files: dict[Identifier, str]
    local_services: dict[UUID, LocalService] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_policies(self):
        keys = [route.policy.digest for route in self.routes]
        if len(keys) != len(set(keys)) or any(route.policy.tenant_id != self.tenant_id for route in self.routes):
            raise ValueError("Invalid manifest policy scope")
        if any(route.destination.tls_ca_ref not in self.ca_files for route in self.routes):
            raise ValueError("Missing destination trust root")
        return self

    @classmethod
    def from_file(cls, path):
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
                raise ValueError("Unsafe route manifest")
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("Route manifest exceeds bound")
        return cls.model_validate(strict_json_loads(raw))

    def select(self, decision, settings):
        if (settings.tenant_id, settings.workload_id) != (self.tenant_id, self.workload_id):
            raise EvolutionError("destination_denied")
        route = next((row for row in self.routes if row.policy == decision.policy), None)
        if route is None or route.provider.provider_id != settings.provider_id:
            raise EvolutionError("provider_denied")
        base = urlsplit(settings.model_base_url)
        destination = route.destination
        if (base.scheme != destination.scheme or base.hostname != destination.host
                or (base.port or 443) != destination.port
                or settings.model_route.split("/", 1)[1] != route.provider.model_id
                or settings.profile == "local_model" and route.provider.vendor != "ollama"):
            raise EvolutionError("destination_denied")
        prefix = destination.path_prefix.rstrip("/")
        path = base.path.rstrip("/") + ("/chat/completions" if settings.profile == "deterministic_mock" else "/api/chat")
        if path != prefix and not path.startswith(prefix + "/"):
            raise EvolutionError("destination_denied")
        return route
