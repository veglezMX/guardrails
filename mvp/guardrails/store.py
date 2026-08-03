"""RT-2 — policy resolver with a local policy cache.

The runtime resolves the effective policy from a local cache that the control
plane refreshes asynchronously. Evaluation never blocks on a control-plane call,
in any deployment mode — which is what makes a customer-managed runtime able to
keep evaluating through a control-plane outage rather than a special case bolted
on for one mode.

A published policy version is immutable, so a cached copy is a faithful copy of
that exact version and can never have drifted from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .binding import Binding, load_binding_file
from .errors import PolicyValidationError
from .policy import Policy, load_policy_file


class PolicyUnresolved(Exception):
    """No usable cached policy exists for this workload. Routed to RT-6."""


@dataclass
class PolicyCache:
    """The runtime's read-only view of control-plane state."""

    policies: dict[tuple[str, int], Policy]
    bindings: dict[tuple[str, str], Binding]

    @classmethod
    def empty(cls) -> "PolicyCache":
        return cls(policies={}, bindings={})

    @classmethod
    def from_dirs(cls, policies_dir: Path, bindings_dir: Path) -> "PolicyCache":
        cache = cls.empty()
        for path in sorted(policies_dir.glob("*.json")):
            cache.put_policy(load_policy_file(path))
        for path in sorted(bindings_dir.glob("*.json")):
            cache.put_binding(load_binding_file(path))
        return cache

    def put_policy(self, policy: Policy) -> None:
        key = (policy.name, policy.version)
        existing = self.policies.get(key)
        if existing is not None and existing != policy:
            # Publishing an existing name and version is refused, always. This
            # is what keeps rollback meaningful and keeps "which version
            # produced this decision" answerable from an audit record.
            raise PolicyValidationError(
                f"policy '{policy.name}' version {policy.version} already exists "
                "with different content; a change is a new version"
            )
        self.policies[key] = policy

    def put_binding(self, binding: Binding) -> None:
        self.bindings[binding.key] = binding

    def resolve(self, tenant_id: str, workload_id: str) -> tuple[Policy, Binding]:
        binding = self.bindings.get((tenant_id, workload_id))
        if binding is None:
            raise PolicyUnresolved(
                f"no binding for workload '{workload_id}' of tenant '{tenant_id}'"
            )
        policy = self.policies.get((binding.policy_name, binding.policy_version))
        if policy is None:
            raise PolicyUnresolved(
                f"binding names policy '{binding.policy_name}' version "
                f"{binding.policy_version}, which is not in the local cache"
            )
        return policy, binding
