"""Exact policy restrictions before a worker or pinned HTTPS dispatch."""

from __future__ import annotations

from typing import get_args

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from .compiler import effective_lists, policy_digest
from .egress import AuthorizedRoute
from .errors import EvolutionError
from .models import (
    EvaluationRequest,
    Mode,
    PolicyVersion,
    canonical_json_bytes,
    resource_digest,
)


class ResidencyGuard:
    def check_request(
        self, policy, request, *, runtime_zone: str, mode: str, workload
    ) -> None:
        """Check trusted installed zone and approved exact resource membership.

        Runtime supplies authenticated workload/current eligibility. Connector
        separately matches its actual configured provider route. Tool result call
        history belongs to the runtime context port, not this stateless guard.
        """
        try:
            policy = PolicyVersion.model_validate_json(canonical_json_bytes(policy))
            request = EvaluationRequest.model_validate_json(
                canonical_json_bytes(request)
            )
        except ValidationError, ValueError, TypeError:
            raise EvolutionError("validation_failed") from None
        workload = (
            workload.model_dump(mode="json")
            if hasattr(workload, "model_dump")
            else workload
        )
        if not isinstance(workload, dict) or (
            policy.tenant_id != request.tenant_id
            or workload.get("tenant_id") != request.tenant_id
            or workload.get("workload_id") != request.workload_id
            or workload.get("status") != "active"
        ):
            raise EvolutionError("forbidden")
        if mode not in get_args(Mode) or workload.get("mode") != mode:
            raise EvolutionError("forbidden")
        if request.stage not in workload.get("allowed_stages", ()):
            raise EvolutionError("unsupported_surface")
        if not runtime_zone or runtime_zone not in policy.residency.allowed_zones:
            raise EvolutionError("residency_denied")
        if policy_digest(policy) != policy.compiled_digest:
            raise EvolutionError("policy_unresolved")
        resources = (
            policy.residency,
            policy.quota,
            *policy.providers,
            *policy.resolved_lists,
            *policy.tools,
            *policy.resolved_destinations,
        )
        for resource in resources:
            # Platform bytes are authorized only by upstream compiled-policy
            # eligibility; this guard never reads platform or latest resources.
            if resource.tenant_id not in (policy.tenant_id, "_platform"):
                raise EvolutionError("forbidden")
            if resource.digest != resource_digest(resource):
                raise EvolutionError("policy_unresolved")
        by_list = {item.list_id: item for item in policy.resolved_lists}
        groups = {}
        for kind, field in (
            ("provider", "provider_lists"),
            ("destination", "destination_lists"),
            ("tool", "tool_lists"),
        ):
            group = []
            for list_id in getattr(policy.residency, field):
                if list_id not in by_list or by_list[list_id].kind != kind:
                    raise EvolutionError("policy_unresolved")
                group.append(by_list[list_id])
            groups[kind] = tuple(group)
        providers = {item.provider_id: item for item in policy.providers}
        if request.provider_id is not None:
            if request.provider_id not in workload.get("provider_ids", ()):
                raise EvolutionError("provider_denied")
            self._provider(
                providers.get(request.provider_id),
                "model",
                policy,
                runtime_zone,
                groups,
            )
        for rule in policy.rules:
            if request.stage in rule.stages and rule.kind == "bedrock":
                self._provider(
                    providers.get(rule.config.provider_id),
                    "evaluator",
                    policy,
                    runtime_zone,
                    groups,
                )
        tools = {item.name: item for item in policy.tools}
        if len(tools) != len(policy.tools):
            raise EvolutionError("policy_unresolved")
        for part in request.parts:
            call = part.tool
            if call is None:
                if request.stage in {"tool_arguments", "tool_result"}:
                    raise EvolutionError("tool_denied")
                continue
            tool = tools.get(call.name)
            if tool is None or tool.digest != call.tool_digest:
                raise EvolutionError("tool_denied")
            self._listed(groups["tool"], "tool", call.name, "tool_denied")
            if not Draft202012Validator(tool.argument_schema).is_valid(call.arguments):
                raise EvolutionError("tool_denied")
            if request.stage == "tool_arguments" and (
                part.kind != "json"
                or part.value != call.arguments
                or call.result_for_call_id is not None
            ):
                raise EvolutionError("tool_denied")
            if request.stage == "tool_result":
                if call.result_for_call_id is None:
                    raise EvolutionError("context_invalid")
                value = part.value if part.kind == "json" else part.text
                if part.kind == "image" or (
                    tool.result_schema is not None
                    and not Draft202012Validator(tool.result_schema).is_valid(value)
                ):
                    raise EvolutionError("tool_denied")

    @staticmethod
    def _listed(lists, kind, identity, code):
        if not lists:
            raise EvolutionError(code)
        allow, deny = effective_lists(lists, kind)
        if identity in deny or (allow is not None and identity not in allow):
            raise EvolutionError(code)

    def _provider(self, provider, kind, policy, runtime_zone, groups):
        if provider is None or not provider.enabled or provider.kind != kind:
            raise EvolutionError("provider_denied")
        self._listed(
            groups["provider"], "provider", provider.provider_id, "provider_denied"
        )
        if provider.zone not in policy.residency.allowed_zones:
            raise EvolutionError("residency_denied")
        if provider.vendor == "bedrock" and not policy.residency.external_processing:
            raise EvolutionError("residency_denied")
        if provider.destination_id is not None:
            self._listed(
                groups["destination"],
                "destination",
                provider.destination_id,
                "destination_denied",
            )
            destination = next(
                (
                    item
                    for item in policy.resolved_destinations
                    if item.destination_id == provider.destination_id
                ),
                None,
            )
            if destination is None:
                raise EvolutionError("destination_denied")
            if (
                destination.zone != provider.zone
                or destination.zone not in policy.residency.allowed_zones
            ):
                raise EvolutionError("residency_denied")
            route = AuthorizedRoute(
                provider, destination, policy.residency.external_processing
            )
            route.validate()
            return route
        if provider.vendor != "local_rules":
            raise EvolutionError("capability_missing")
        if provider.zone != runtime_zone:
            raise EvolutionError("residency_denied")

    def authorize_provider(
        self, policy, provider_id: str, *, runtime_zone: str, purpose: str
    ) -> AuthorizedRoute:
        """Resolve approved bytes without a latest-resource or DNS lookup.

        Callers supply a currently eligible policy. The returned route still
        requires PinnedHttpsTransport to check every DNS answer and TLS peer.
        """
        try:
            policy = PolicyVersion.model_validate_json(canonical_json_bytes(policy))
        except ValidationError, ValueError, TypeError:
            raise EvolutionError("validation_failed") from None
        if policy_digest(policy) != policy.compiled_digest:
            raise EvolutionError("policy_unresolved")
        if runtime_zone not in policy.residency.allowed_zones:
            raise EvolutionError("residency_denied")
        if purpose not in {"inference", "evaluation"}:
            raise EvolutionError("destination_denied")
        for resource in (
            policy.residency,
            *policy.resolved_lists,
            *policy.providers,
            *policy.resolved_destinations,
        ):
            if resource.tenant_id not in (policy.tenant_id, "_platform"):
                raise EvolutionError("forbidden")
            if resource.digest != resource_digest(resource):
                raise EvolutionError("policy_unresolved")
        by_list = {item.list_id: item for item in policy.resolved_lists}
        groups = {}
        for kind, field in (
            ("provider", "provider_lists"),
            ("destination", "destination_lists"),
        ):
            group = []
            for list_id in getattr(policy.residency, field):
                if list_id not in by_list or by_list[list_id].kind != kind:
                    raise EvolutionError("policy_unresolved")
                group.append(by_list[list_id])
            groups[kind] = tuple(group)
        provider = next(
            (item for item in policy.providers if item.provider_id == provider_id), None
        )
        route = self._provider(
            provider,
            "model" if purpose == "inference" else "evaluator",
            policy,
            runtime_zone,
            groups,
        )
        if route is None:
            raise EvolutionError("capability_missing")
        return route
