"""Pure deterministic compilation of immutable policy dependencies.

Mandatory overrides use a deliberately small partial order: detector/language
sets and stages may grow; action and minimum state may rise; injection threshold
may fall; every other detector configuration and exact engine revision must
remain equal. Unproved comparisons reject. The compiler never rewrites stored
configuration bytes: the child residency/quota must already satisfy inherited
floors. Independent list resources retain their exact bytes; their effective
allows intersect and denies union.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from .errors import EvolutionError
from .models import (
    ACTION_ORDER,
    STATE_ORDER,
    ListSet,
    PolicyRef,
    PolicyVersion,
    Rule,
    VersionedResource,
    canonical_json_bytes,
    resource_digest,
)
from .ports import PolicyLookup

COMPILER_VERSION = "compiler-v2.2"
LEGACY_COMPILER_VERSION = "compiler-v2.1"
MAX_PARENT_DEPTH = 3
MAX_EXPANDED_POLICIES = 64
MAX_COMPILED_BYTES = 1024 * 1024
PolicyCompileError = EvolutionError


def _resource_payload(resource: VersionedResource) -> dict[str, Any]:
    data = resource.model_dump(mode="json")
    for name in ("allow", "deny", "cidrs", "allowed_zones", "destination_lists", "provider_lists", "tool_lists", "content_lists", "sensitive_paths"):
        if name in data:
            data[name] = sorted(data[name])
    return data


def canonical_payload(policy: PolicyVersion) -> dict[str, Any]:
    data = policy.model_dump(mode="json", exclude={"status", "compiled_digest"})
    if policy.compiler_version == LEGACY_COMPILER_VERSION:
        if getattr(policy, "resolved_destinations", ()):
            _fail("validation_failed")
        # Historical approved bytes must never gain an additive default field.
        data.pop("resolved_destinations", None)
    elif policy.compiler_version == COMPILER_VERSION:
        data["resolved_destinations"] = sorted((_resource_payload(item) for item in policy.resolved_destinations), key=lambda row: row["destination_id"])
    else:
        _fail("validation_failed")
    data["schema_version"] = "guardrails-v2"
    data["parents"] = sorted(data["parents"], key=lambda row: (row["tenant_id"], row["policy_id"], row["version"], row["digest"]))
    data["rules"] = sorted(data["rules"], key=lambda row: row["rule_id"])
    for rule in data["rules"]:
        rule["stages"] = sorted(rule["stages"])
        for name in ("entities", "detectors", "languages"):
            if name in rule["config"]:
                rule["config"][name] = sorted(rule["config"][name])
    for name, identity in (("resolved_lists", "list_id"), ("providers", "provider_id"), ("tools", "tool_id")):
        data[name] = sorted((_resource_payload(resource) for resource in getattr(policy, name)), key=lambda row: row[identity])
    data["residency"] = _resource_payload(policy.residency)
    data["quota"] = _resource_payload(policy.quota)
    return data


def canonical_bytes(policy: PolicyVersion) -> bytes:
    return canonical_json_bytes(canonical_payload(policy))


def policy_digest(policy: PolicyVersion) -> str:
    return hashlib.sha256(canonical_bytes(policy)).hexdigest()


def effective_lists(lists: tuple[ListSet, ...], kind: str) -> tuple[frozenset[str] | None, frozenset[str]]:
    """Return the effective allow constraint and deny set; None means disabled."""
    selected = [item for item in lists if item.kind == kind]
    enabled = [set(item.allow) for item in selected if item.allow_enabled]
    allow = frozenset(set.intersection(*enabled)) if enabled else None
    deny = frozenset(entry for item in selected for entry in item.deny)
    return allow, deny


def _fail(code: str = "composition_conflict") -> None:
    raise EvolutionError(code)


def _rule_strengthens(child: Rule, parent: Rule) -> bool:
    if child.kind != parent.kind or child.evaluator_revision != parent.evaluator_revision:
        return False
    if parent.mandatory and not child.mandatory:
        return False
    if ACTION_ORDER[child.action] < ACTION_ORDER[parent.action] or STATE_ORDER[child.minimum_state] < STATE_ORDER[parent.minimum_state]:
        return False
    if not set(parent.stages) <= set(child.stages):
        return False
    old, new = parent.config.model_dump(mode="json"), child.config.model_dump(mode="json")
    for key in ("entities", "detectors", "languages"):
        if key in old:
            return set(old[key]) <= set(new[key])
    if parent.kind == "injection":
        return new["threshold"] <= old["threshold"] and new["max_tokens"] == old["max_tokens"]
    return new == old


def _resource_key(resource: VersionedResource) -> str:
    for name in ("list_id", "residency_id", "provider_id", "destination_id", "tool_id", "quota_id"):
        if hasattr(resource, name):
            return getattr(resource, name)
    raise TypeError("Unsupported compiled dependency")


def _dependencies(policy: PolicyVersion):
    return (policy.residency, policy.quota, *policy.resolved_lists, *policy.providers, *policy.resolved_destinations, *policy.tools)


class PolicyCompiler:
    canonical_bytes = staticmethod(canonical_bytes)
    digest = staticmethod(policy_digest)

    def __init__(self, resolver: PolicyLookup | None = None, registered_revisions: Mapping[str, set[str] | frozenset[str] | tuple[str, ...]] | None = None, baseline_resolver: PolicyLookup | None = None):
        self.resolver = resolver
        self.baseline_resolver = baseline_resolver
        # Compilation is a pure domain boundary; revisions are metadata, never
        # imports of detector implementations or worker/transport dependencies.
        installed = {"pii": {"pii-v2"}, "secret": {"secrets-v1"},
                     "term": {"lexical-v1"}, "topic": {"lexical-v1"},
                     "json_schema": {"schema-v1"},
                     "injection": {"deberta-v2-90c9989b1a34"}, "bedrock": {"bedrock-v1"}}
        self.registered_revisions = {kind: frozenset(revisions) for kind, revisions in (registered_revisions if registered_revisions is not None else installed).items()}

    def _get(self, ref: PolicyRef, owner: str) -> PolicyVersion:
        if ref.tenant_id not in {owner, "_platform"}:
            _fail("resource_not_found")
        resolver = self.baseline_resolver if ref.tenant_id == "_platform" and owner != "_platform" else self.resolver
        if resolver is None:
            _fail("resource_not_found")
        try:
            value = resolver(ref) if callable(resolver) else resolver.get_policy(ref)
            parent = value if isinstance(value, PolicyVersion) else PolicyVersion.model_validate(value)
        except (KeyError, LookupError):
            _fail("resource_not_found")
        except (ValueError, TypeError):
            _fail("validation_failed")
        if parent.tenant_id != ref.tenant_id or parent.policy_id != ref.policy_id or parent.version != ref.version:
            _fail("resource_not_found")
        return parent

    def _parents(self, policy: PolicyVersion) -> list[PolicyVersion]:
        completed: dict[tuple[str, str, int], PolicyVersion] = {}
        active = {(policy.tenant_id, policy.policy_id, policy.version)}

        def visit(ref: PolicyRef, depth: int) -> None:
            key = (ref.tenant_id, ref.policy_id, ref.version)
            if key in active or depth > MAX_PARENT_DEPTH:
                _fail()
            if key in completed:
                if completed[key].compiled_digest != ref.digest:
                    _fail()
                return
            if len(completed) + len(active) >= MAX_EXPANDED_POLICIES:
                _fail()
            parent = self._get(ref, policy.tenant_id)
            active.add(key)
            for grandparent in parent.parents:
                visit(grandparent, depth + 1)
            active.remove(key)
            if parent.status != "approved":
                _fail("policy_revoked" if parent.status == "revoked" else "approval_required")
            if parent.compiled_digest != ref.digest or policy_digest(parent) != ref.digest:
                _fail("validation_failed")
            self._validate_resources(parent, permitted_owners={policy.tenant_id, "_platform"})
            completed[key] = parent

        for reference in policy.parents:
            visit(reference, 1)
        return list(completed.values())

    def _validate_resources(self, policy: PolicyVersion, *, permitted_owners: set[str]) -> None:
        if policy.compiler_version not in {COMPILER_VERSION, LEGACY_COMPILER_VERSION} or policy.compiler_version == LEGACY_COMPILER_VERSION and policy.resolved_destinations:
            _fail("validation_failed")
        for resource in _dependencies(policy):
            if resource.tenant_id not in permitted_owners:
                _fail("resource_not_found")
            if resource_digest(resource) != resource.digest:
                _fail("validation_failed")
        for rule in policy.rules:
            if rule.evaluator_revision not in self.registered_revisions.get(rule.kind, frozenset()):
                _fail("capability_missing")
            if rule.action == "redact" and rule.kind in {"injection", "topic", "image_text"}:
                _fail("unsafe_transformation")

    def validate_dependencies(self, policy: PolicyVersion) -> None:
        """Eligibility boundary: exact approved pins, never a latest lookup.

        Legacy policies remain readable with their original digests. A legacy
        policy with an unresolved network destination cannot authorize egress.
        New compilation may inherit such a policy only if the new approved
        result supplies the explicit pin.
        """
        self._validate_resources(policy, permitted_owners={policy.tenant_id, "_platform"})
        # Policy-wide content constraints produce findings with an exact list
        # identity. Authored rules cannot impersonate that reserved identity.
        active_content = any(
            item.list_id in policy.residency.content_lists
            and (item.allow_enabled or item.deny)
            for item in policy.resolved_lists
        )
        if active_content and any(rule.rule_id.startswith("content-list:") for rule in policy.rules):
            _fail("validation_failed")
        destinations = {item.destination_id: item for item in policy.resolved_destinations}
        for destination in destinations.values():
            if destination.zone not in policy.residency.allowed_zones:
                _fail("residency_denied")
        for provider in policy.providers:
            if provider.destination_id is None:
                continue
            destination = destinations.get(provider.destination_id)
            if destination is None:
                _fail("destination_denied")
            purpose = "inference" if provider.kind == "model" else "evaluation"
            if destination.purpose != purpose:
                _fail("destination_denied")
            if destination.zone != provider.zone or provider.zone not in policy.residency.allowed_zones:
                _fail("residency_denied")

    @staticmethod
    def _floors(child: PolicyVersion, parent: PolicyVersion) -> None:
        if not set(child.residency.allowed_zones) <= set(parent.residency.allowed_zones):
            _fail("floor_conflict")
        if child.residency.external_processing and not parent.residency.external_processing:
            _fail("floor_conflict")
        if child.residency.metadata_retention_days > parent.residency.metadata_retention_days:
            _fail("floor_conflict")
        if parent.failure_behavior == "block" and child.failure_behavior != "block":
            _fail("floor_conflict")
        for field in ("requests_per_minute", "burst", "max_inflight", "daily_tokens", "daily_cost_microunits"):
            minimum = getattr(parent.quota, field)
            current = getattr(child.quota, field)
            if minimum is not None and (current is None or current > minimum):
                _fail("floor_conflict")

    def validate_current(self, policy: PolicyVersion, baseline: PolicyVersion) -> None:
        """Recheck an approved candidate against the current organizational floor."""
        if baseline.status != "approved":
            _fail("approval_required")
        self.validate_retained_floors(policy, baseline)

    def validate_retained_floors(self, policy: PolicyVersion, baseline: PolicyVersion) -> None:
        """Compare immutable constraints, without granting lifecycle eligibility.

        Governance uses this only to prepare/publish a replacement for retained
        floors. Runtime admission still requires validate_current and approved
        current publications. Neither input's status or canonical bytes change.
        """
        self._validate_resources(policy, permitted_owners={policy.tenant_id, "_platform"})
        if policy_digest(policy) != policy.compiled_digest:
            _fail("validation_failed")
        self.validate_dependencies(policy)
        self._validate_resources(baseline, permitted_owners={policy.tenant_id, "_platform"})
        if baseline.tenant_id not in {policy.tenant_id, "_platform"}:
            _fail("resource_not_found")
        if policy_digest(baseline) != baseline.compiled_digest:
            _fail("approval_required")
        self._floors(policy, baseline)
        rules = {rule.rule_id: rule for rule in policy.rules}
        for inherited in baseline.rules:
            if inherited.mandatory and (inherited.rule_id not in rules or not _rule_strengthens(rules[inherited.rule_id], inherited)):
                _fail("floor_conflict")
        for kind in ("destination", "provider", "tool", "content"):
            current_allow, current_deny = effective_lists(policy.resolved_lists, kind)
            floor_allow, floor_deny = effective_lists(baseline.resolved_lists, kind)
            if not floor_deny <= current_deny:
                _fail("floor_conflict")
            if floor_allow is not None and (current_allow is None or not current_allow <= floor_allow):
                _fail("floor_conflict")

    def compatible(self, policy: PolicyVersion, baseline: PolicyVersion) -> bool:
        try:
            self.validate_current(policy, baseline)
        except EvolutionError:
            return False
        return True

    def compile(self, candidate: PolicyVersion | dict[str, Any]) -> PolicyVersion:
        try:
            # Revalidate even existing objects: model_copy(update=...) deliberately
            # bypasses Pydantic validation and must not bypass the compiler boundary.
            raw = candidate.model_dump(mode="json") if isinstance(candidate, PolicyVersion) else candidate
            policy = PolicyVersion.model_validate(raw)
        except (ValidationError, ValueError, TypeError):
            _fail("validation_failed")
        if policy.status != "candidate":
            _fail("revision_conflict")
        if policy.compiler_version not in {LEGACY_COMPILER_VERSION, COMPILER_VERSION}:
            _fail("validation_failed")
        policy = policy.model_copy(update={"compiler_version": COMPILER_VERSION})
        parents = self._parents(policy)
        authorized_platform_ids = {_resource_key(resource): resource.digest for parent in parents if parent.tenant_id == "_platform" for resource in _dependencies(parent)}
        self._validate_resources(policy, permitted_owners={policy.tenant_id, "_platform"} if parents else {policy.tenant_id})
        for resource in _dependencies(policy):
            if resource.tenant_id == "_platform" and policy.tenant_id != "_platform" and authorized_platform_ids.get(_resource_key(resource)) != resource.digest:
                _fail("resource_not_found")
        rules: dict[str, Rule] = {}
        resources: dict[str, dict[str, VersionedResource]] = {"resolved_lists": {}, "providers": {}, "resolved_destinations": {}, "tools": {}}
        for layer in (*parents, policy):
            if layer is not policy:
                self._floors(policy, layer)
            for rule in layer.rules:
                inherited = rules.get(rule.rule_id)
                if inherited is not None and not _rule_strengthens(rule, inherited):
                    if layer is not policy and _rule_strengthens(inherited, rule):
                        continue
                    _fail()
                rules[rule.rule_id] = rule
            for name, by_id in resources.items():
                for resource in getattr(layer, name):
                    key = _resource_key(resource)
                    old = by_id.get(key)
                    if old is not None and (old.version != resource.version or old.digest != resource.digest or old.tenant_id != resource.tenant_id):
                        _fail()
                    by_id[key] = resource
        if len(rules) > 64:
            _fail()
        merged_lists = tuple(resources["resolved_lists"].values())
        by_list_id = {item.list_id: item for item in merged_lists}
        referenced = set()
        for kind, field in (("destination", "destination_lists"), ("provider", "provider_lists"), ("tool", "tool_lists"), ("content", "content_lists")):
            ids = getattr(policy.residency, field)
            for list_id in ids:
                if list_id not in by_list_id or by_list_id[list_id].kind != kind:
                    _fail("validation_failed")
            inherited_ids = {list_id for parent in parents for list_id in getattr(parent.residency, field)}
            if not inherited_ids <= set(ids):
                _fail("floor_conflict")
            referenced.update(ids)
            allow, deny = effective_lists(tuple(by_list_id[item] for item in ids), kind)
            if allow is not None and not (allow - deny):
                _fail()
        for rule in rules.values():
            if rule.kind in {"term", "topic"}:
                list_id = rule.config.list_id
                if list_id not in by_list_id or by_list_id[list_id].kind != "content":
                    _fail("validation_failed")
                referenced.add(list_id)
            if rule.kind == "bedrock":
                provider = resources["providers"].get(rule.config.provider_id)
                if provider is None or provider.vendor != "bedrock" or provider.kind != "evaluator":
                    _fail("provider_denied")
        # No unreferenced lists can introduce an accidental second interpretation
        # between compilation and runtime enforcement.
        if set(by_list_id) != referenced:
            _fail("validation_failed")
        for provider in resources["providers"].values():
            if provider.zone not in policy.residency.allowed_zones or not provider.enabled:
                _fail("residency_denied")
            if provider.vendor == "bedrock" and not policy.residency.external_processing:
                _fail("residency_denied")
        result = PolicyVersion.model_validate({**policy.model_dump(mode="json"), "rules": [rule.model_dump(mode="json") for rule in sorted(rules.values(), key=lambda item: item.rule_id)], **{name: [item.model_dump(mode="json") for _, item in sorted(values.items())] for name, values in resources.items()}})
        self.validate_dependencies(result)
        encoded = canonical_bytes(result)
        if len(encoded) > MAX_COMPILED_BYTES:
            _fail("payload_too_large")
        return PolicyVersion.model_validate({**result.model_dump(mode="json"), "compiled_digest": hashlib.sha256(encoded).hexdigest()})

    @staticmethod
    def diff(old: PolicyVersion, new: PolicyVersion) -> dict[str, Any]:
        old_rules = {rule.rule_id: rule for rule in old.rules}
        new_rules = {rule.rule_id: rule for rule in new.rules}
        changed_rules = sorted(key for key in set(old_rules) | set(new_rules) if old_rules.get(key) != new_rules.get(key))
        old_resources = {_resource_key(resource): resource.digest for resource in _dependencies(old)}
        new_resources = {_resource_key(resource): resource.digest for resource in _dependencies(new)}
        return {"changed_rule_ids": changed_rules, "changed_dependencies": [{"resource_id": key, "old_digest": old_resources.get(key), "new_digest": new_resources.get(key)} for key in sorted(set(old_resources) | set(new_resources)) if old_resources.get(key) != new_resources.get(key)], "old_digest": old.compiled_digest, "new_digest": new.compiled_digest, "impacts": ["rules_changed"] if changed_rules else []}
