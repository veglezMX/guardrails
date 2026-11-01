"""Immutable policy candidates and digest-bound, append-only review events."""
from __future__ import annotations

from uuid import uuid4

from .errors import EvolutionError
from .models import Approval, PolicyVersion, PolicyRef, canonical_json_bytes, utc_now, UUID, Digest, PositiveInt, DomainModel
from .compiler import PolicyCompiler, policy_digest


class ResourceReference(DomainModel):
    id: UUID
    version: PositiveInt
    digest: Digest


def record(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def policy_model(value):
    return PolicyVersion.model_validate_json(canonical_json_bytes(value))


def principal(identity):
    return identity.principal if hasattr(identity, "principal") else identity


def require_role(identity, roles, tenant_id):
    actor = principal(identity)
    if actor.get("status") != "active" or not set(actor["roles"]).intersection(roles):
        raise EvolutionError("forbidden")
    platform = actor.get("tenant_id") is None and "platform_admin" in actor["roles"] and "platform_admin" in roles
    if actor.get("tenant_id") != tenant_id and not platform:
        raise EvolutionError("resource_not_found")
    return actor


def lock_admission(tx):
    """Serialize tenant governance checks with their eventual transaction commit.

    Order: request idempotency, admission, policy/baseline/workload/snapshot,
    record rows, audit. Call before acquiring any of the inner locks. This does
    not authorize another tenant or replace the published platform baseline port.
    """
    tx.lock("governance-admission")


class TransactionResolver:
    def __init__(self, tx):
        self.tx = tx

    def get_policy(self, ref):
        if ref.tenant_id != self.tx.tenant_id:
            raise EvolutionError("resource_not_found")
        raw = self.tx.get("policies", f"{ref.policy_id}:{ref.version}", required=True)
        if raw["compiled_digest"] != ref.digest:
            raise EvolutionError("composition_conflict")
        state = self.tx.get("policy_status", f"{ref.policy_id}:{ref.version}", required=True)
        return policy_model({**raw, "status": state["status"]})


class PolicyGovernance:
    def __init__(self, repository, audit, *, compiler_factory=PolicyCompiler, baseline_resolver=None):
        self.repository = repository
        self.audit = audit
        self.compiler_factory = compiler_factory
        self.baseline_resolver = baseline_resolver

    def compiler(self, tx):
        return self.compiler_factory(resolver=TransactionResolver(tx), baseline_resolver=self.baseline_resolver)

    def current_baselines(self, tx):
        publication = tx.get("baseline", "current")
        if not publication:
            return []
        result = []
        for ref in publication["policies"]:
            ref = PolicyRef.model_validate_json(canonical_json_bytes(ref))
            if ref.tenant_id == tx.tenant_id:
                result.append(TransactionResolver(tx).get_policy(ref))
            elif ref.tenant_id == "_platform" and self.baseline_resolver:
                baseline = self.baseline_resolver.get_policy(ref)
                if baseline.reference() != ref:
                    raise EvolutionError("policy_unresolved")
                result.append(baseline)
            else:
                raise EvolutionError("policy_unresolved")
        return result

    def _current_check(self, tx, policy, *, baselines=None):
        compiler = self.compiler(tx)
        for baseline in self.current_baselines(tx) if baselines is None else baselines:
            compiler.validate_current(policy, baseline)

    def _resolve_config(self, tx, resource, ref):
        ref = ResourceReference.model_validate(ref).model_dump(mode="json")
        value = tx.get("config:" + resource, f"{ref['id']}:{ref['version']}", required=True)
        if value["digest"] != ref["digest"] or value["tenant_id"] != tx.tenant_id:
            raise EvolutionError("composition_conflict")
        return value

    def materialize(self, tx, actor, policy_id, payload, version):
        allowed = {"parents", "rules", "residency_ref", "list_refs", "destination_refs", "provider_refs", "tool_refs", "quota_ref", "failure_behavior"}
        if set(payload) - allowed or not {"rules", "residency_ref", "quota_ref"}.issubset(payload):
            raise EvolutionError("validation_failed")
        raw = {
            "tenant_id": tx.tenant_id, "policy_id": policy_id, "version": version,
            "author_id": actor["principal_id"], "parents": payload.get("parents", []),
            "rules": payload["rules"], "residency": self._resolve_config(tx, "residency", payload["residency_ref"]),
            "resolved_lists": [self._resolve_config(tx, "lists", ref) for ref in payload.get("list_refs", [])],
            "resolved_destinations": [self._resolve_config(tx, "destinations", ref) for ref in payload.get("destination_refs", [])],
            "providers": [self._resolve_config(tx, "providers", ref) for ref in payload.get("provider_refs", [])],
            "tools": [self._resolve_config(tx, "tools", ref) for ref in payload.get("tool_refs", [])],
            "quota": self._resolve_config(tx, "quotas", payload["quota_ref"]),
            "failure_behavior": payload.get("failure_behavior", "block"),
            "compiled_digest": "0" * 64, "compiler_version": "compiler-v2.2",
            "status": "candidate", "created_at": utc_now(),
        }
        return self.compiler(tx).compile(policy_model(raw))

    def validate(self, tx, identity, policy_id, payload):
        actor = require_role(identity, {"author", "platform_admin"} if tx.tenant_id == "_platform" else {"author"}, tx.tenant_id)
        lock_admission(tx)
        candidate = self.materialize(tx, actor, policy_id, payload, 1)
        self._current_check(tx, candidate)
        return candidate

    def submit(self, tx, identity, policy_id, payload):
        actor = require_role(identity, {"author", "platform_admin"} if tx.tenant_id == "_platform" else {"author"}, tx.tenant_id)
        lock_admission(tx)
        tx.lock("policy:" + policy_id)
        version = tx.next_version("policies", policy_id)
        candidate = self.materialize(tx, actor, policy_id, payload, version)
        self._current_check(tx, candidate)
        key = f"{policy_id}:{version}"
        tx.insert("policies", key, record(candidate), immutable=True)
        tx.insert("policy_status", key, {"status": "candidate", "revision": 1, "created_at": utc_now()})
        self.audit.append(tx, "policy_submitted", actor_id=actor["principal_id"], policy_digest=candidate.compiled_digest)
        return record(candidate)

    def status(self, tx, policy):
        key = f"{policy['policy_id']}:{policy['version']}"
        state = tx.get("policy_status", key, required=True)
        if tx.get("revocations", policy["compiled_digest"]):
            return "revoked"
        for parent in policy["parents"]:
            if parent["tenant_id"] == tx.tenant_id:
                ancestor = tx.get("policies", f"{parent['policy_id']}:{parent['version']}", required=True)
                if ancestor["compiled_digest"] != parent["digest"] or self.status(tx, ancestor) == "revoked":
                    return "revoked"
            elif parent["tenant_id"] == "_platform" and self.baseline_resolver is not None:
                ancestor = self.baseline_resolver.get_policy(PolicyRef.model_validate(parent))
                if ancestor.status != "approved" or ancestor.compiled_digest != parent["digest"] or policy_digest(ancestor) != parent["digest"]:
                    return "revoked"
            else:
                return "revoked"
        return state["status"]

    def get(self, tx, policy_id, version):
        value = tx.get("policies", f"{policy_id}:{version}", required=True)
        return {**value, "status": self.status(tx, value)}

    def eligible(self, tx, ref, *, baselines=None):
        """Validate current governance; supplied baselines belong to this locked transaction."""
        lock_admission(tx)
        ref = record(PolicyRef.model_validate(record(ref)))
        if ref["tenant_id"] != tx.tenant_id:
            raise EvolutionError("resource_not_found")
        value = self.get(tx, ref["policy_id"], ref["version"])
        if value["compiled_digest"] != ref["digest"]:
            raise EvolutionError("approval_required")
        if value["status"] == "revoked":
            raise EvolutionError("policy_revoked")
        if value["status"] != "approved":
            raise EvolutionError("approval_required")
        policy = policy_model(value)
        if policy_digest(policy) != ref["digest"]:
            raise EvolutionError("validation_failed")
        self.compiler(tx).validate_dependencies(policy)
        self._current_check(tx, policy, baselines=baselines)
        return value

    def review(self, tx, identity, policy_id, version, *, outcome, compiled_digest, reason, evidence_ids=()):
        roles = {"approver", "platform_admin"} if outcome == "revoke" or tx.tenant_id == "_platform" else {"approver"}
        actor = require_role(identity, roles, tx.tenant_id)
        if outcome not in ("approve", "reject", "revoke") or not isinstance(reason, str) or not 1 <= len(reason) <= 500:
            raise EvolutionError("validation_failed")
        lock_admission(tx)
        key = f"{policy_id}:{version}"
        tx.lock("policy:" + policy_id)
        value = self.get(tx, policy_id, version)
        state = tx.get("policy_status", key, required=True, for_update=True)
        if value["compiled_digest"] != compiled_digest:
            raise EvolutionError("revision_conflict")
        if actor["principal_id"] == value["author_id"] and outcome in ("approve", "reject"):
            raise EvolutionError("self_approval")
        if value["status"] == "revoked":
            raise EvolutionError("policy_revoked")
        if (outcome in ("approve", "reject") and value["status"] != "candidate") or (outcome == "revoke" and value["status"] != "approved"):
            raise EvolutionError("revision_conflict")
        if outcome == "approve":
            self._current_check(tx, policy_model(value))
        for evidence_id in evidence_ids:
            tx.get("evidence", str(evidence_id), required=True)
        approval = {"approval_id": str(uuid4()), "tenant_id": tx.tenant_id,
                    "policy": {"tenant_id": tx.tenant_id, "policy_id": policy_id, "version": version, "digest": compiled_digest},
                    "compiled_digest": compiled_digest, "actor_id": actor["principal_id"],
                    "outcome": outcome, "reason": reason, "evidence_ids": list(evidence_ids), "created_at": utc_now()}
        approval = record(Approval.model_validate(approval))
        tx.insert("approvals", approval["approval_id"], approval, immutable=True)
        next_state = {"approve": "approved", "reject": "rejected", "revoke": "revoked"}[outcome]
        tx.put("policy_status", key, {**state, "status": next_state, "revision": state["revision"] + 1}, expected_revision=state["revision"])
        if outcome == "revoke":
            tx.insert("revocations", compiled_digest, {"digest": compiled_digest, "created_at": utc_now()}, immutable=True)
            tx.append("outbox", {"type": "policy_revoked", "digest": compiled_digest, "created_at": utc_now()})
        self.audit.append(tx, "policy_" + next_state, actor_id=actor["principal_id"], policy_digest=compiled_digest)
        return approval

    def publish_baseline(self, tx, identity, refs, expected_revision):
        require_role(identity, {"tenant_admin"} if tx.tenant_id != "_platform" else {"platform_admin"}, tx.tenant_id)
        lock_admission(tx)
        tx.lock("baseline")
        if type(expected_revision) is not int or expected_revision < 0 or not isinstance(refs, (list, tuple)) or not 1 <= len(refs) <= 3:
            raise EvolutionError("validation_failed")
        refs = [record(PolicyRef.model_validate(record(ref))) for ref in refs]
        for ref in refs:
            self.eligible(tx, ref)
        impacts = []
        after = None
        pointers = []
        while True:
            batch = tx.page("binding_pointers", limit=100, after=after)
            pointers.extend(row["data"] for row in batch)
            if len(batch) < 100:
                break
            after = (batch[-1]["created_at"], batch[-1]["key"])
        for pointer in pointers:
            binding = tx.get("bindings", f"{pointer['workload_id']}:{pointer['binding_revision']}", required=True)
            policy = policy_model(self.get(tx, binding["policy"]["policy_id"], binding["policy"]["version"]))
            for ref in refs:
                baseline = policy_model(self.get(tx, ref["policy_id"], ref["version"]))
                try:
                    self.compiler(tx).validate_current(policy, baseline)
                except EvolutionError:
                    impacts.append(binding["workload_id"])
                    old_block = tx.get("binding_blocks", binding["workload_id"])
                    blocked = {"workload_id": binding["workload_id"], "binding_revision": binding["revision"], "baseline_revision": expected_revision + 1, "reason_code": "floor_conflict", "created_at": utc_now()}
                    if old_block:
                        tx.put("binding_blocks", binding["workload_id"], blocked, expected_revision=old_block["revision"])
                    else:
                        tx.insert("binding_blocks", binding["workload_id"], blocked)
                    break
        current = tx.get("baseline", "current")
        data = {"policies": refs, "revision": expected_revision + 1, "created_at": current["created_at"] if current else utc_now()}
        if current:
            tx.put("baseline", "current", data, expected_revision=expected_revision)
        elif expected_revision == 0:
            tx.insert("baseline", "current", data)
        else:
            raise EvolutionError("revision_conflict")
        self.audit.append(tx, "baseline_published", actor_id=principal(identity)["principal_id"])
        tx.append("outbox", {"type": "baseline_published", "baseline_revision": data["revision"], "blocked_workload_ids": sorted(set(impacts)), "created_at": utc_now()})
        return {"revision": data["revision"], "affected_workloads": sorted(set(impacts))}
