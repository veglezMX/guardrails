"""Immutable policy candidates and digest-bound, append-only review events."""
from __future__ import annotations

from uuid import uuid4

from .compiler import PolicyCompiler, policy_digest
from .errors import EvolutionError
from .models import (
    UUID,
    Approval,
    Digest,
    DomainModel,
    PolicyRef,
    PolicyVersion,
    PositiveInt,
    canonical_json_bytes,
    utc_now,
)
from .published_baselines import (
    PublishedBaselines,
    _scope,
    invalidate_publications,
    publish_closure,
    tenant_scopes,
)


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
    platform = actor.get("tenant_id") is None and "platform_admin" in actor["roles"] and (
        tenant_id == "_platform" or "platform_admin" in roles)
    if actor.get("tenant_id") != tenant_id and not platform:
        raise EvolutionError("resource_not_found")
    return actor


def lock_admission(tx):
    """Serialize tenant governance checks with their eventual transaction commit.

    Order: request idempotency, organization requirements, tenant admission,
    policy/baseline/workload/snapshot,
    record rows, audit. Call before acquiring any of the inner locks. This does
    not authorize another tenant or replace the published platform baseline port.
    """
    tx.lock_governance()
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
        return self.compiler_factory(resolver=TransactionResolver(tx), baseline_resolver=self._published(tx))

    def _published(self, tx):
        return self.baseline_resolver if self.baseline_resolver is not None else PublishedBaselines(tx)

    def _baseline_policy(self, tx, reference):
        ref = PolicyRef.model_validate_json(canonical_json_bytes(reference))
        if ref.tenant_id == tx.tenant_id:
            value = policy_model(self.get(tx, ref.policy_id, ref.version))
        elif ref.tenant_id == "_platform":
            resolver = self._published(tx)
            value = resolver(ref) if callable(resolver) else resolver.get_policy(ref)
        else:
            raise EvolutionError("policy_unresolved")
        if value.reference() != ref or policy_digest(value) != ref.digest:
            raise EvolutionError("policy_unresolved")
        if value.status != "approved":
            raise EvolutionError("policy_revoked" if value.status == "revoked" else "approval_required")
        return value

    def current_baselines(self, tx):
        lock_admission(tx)
        publication = tx.get("baseline", "current")
        result = []
        if tx.tenant_id != "_platform":
            published = self._published(tx)
            if hasattr(published, "current"):
                result.extend(published.current())
        if publication:
            result.extend(self._baseline_policy(tx, ref) for ref in publication["policies"])
        for value in result:
            if value.status != "approved":
                raise EvolutionError("policy_revoked" if value.status == "revoked" else "approval_required")
        return list({value.compiled_digest: value for value in result}.values())

    def _current_check(self, tx, policy, *, baselines=None):
        compiler = self.compiler(tx)
        for baseline in self.current_baselines(tx) if baselines is None else baselines:
            compiler.validate_current(policy, baseline)

    def _candidate_check(self, tx, policy):
        if tx.tenant_id != "_platform":
            return self._current_check(tx, policy)
        # A revoked current organization floor continues to block admission,
        # but cannot prevent authoring/independent approval of its replacement.
        # Its exact published bytes remain a mandatory no-weakening constraint.
        for baseline in PublishedBaselines(tx).current():
            self.compiler(tx).validate_retained_floors(policy, baseline)

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
        self._candidate_check(tx, candidate)
        return candidate

    def submit(self, tx, identity, policy_id, payload):
        actor = require_role(identity, {"author", "platform_admin"} if tx.tenant_id == "_platform" else {"author"}, tx.tenant_id)
        lock_admission(tx)
        tx.lock("policy:" + policy_id)
        version = tx.next_version("policies", policy_id)
        candidate = self.materialize(tx, actor, policy_id, payload, version)
        self._candidate_check(tx, candidate)
        key = f"{policy_id}:{version}"
        tx.insert("policies", key, record(candidate), immutable=True)
        tx.insert("policy_status", key, {"status": "candidate", "revision": 1, "created_at": utc_now()})
        self.audit.append(tx, "policy_submitted", actor_id=actor["principal_id"], policy_digest=candidate.compiled_digest)
        return record(candidate)

    def status(self, tx, policy):
        lock_admission(tx)
        key = f"{policy['policy_id']}:{policy['version']}"
        state = tx.get("policy_status", key, required=True)
        if tx.get("revocations", policy["compiled_digest"]):
            return "revoked"
        for parent in policy["parents"]:
            if parent["tenant_id"] == tx.tenant_id:
                ancestor = tx.get("policies", f"{parent['policy_id']}:{parent['version']}", required=True)
                if ancestor["compiled_digest"] != parent["digest"] or self.status(tx, ancestor) == "revoked":
                    return "revoked"
            elif parent["tenant_id"] == "_platform":
                resolver = self._published(tx)
                ref = PolicyRef.model_validate(parent)
                ancestor = resolver(ref) if callable(resolver) else resolver.get_policy(ref)
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
        roles = {"approver", "platform_admin"} if outcome == "revoke" else {"approver"}
        actor = require_role(identity, roles, tx.tenant_id)
        if outcome not in ("approve", "reject", "revoke") or not isinstance(reason, str) or not 1 <= len(reason) <= 500:
            raise EvolutionError("validation_failed")
        if outcome == "revoke" and tx.tenant_id == "_platform":
            tx.lock_governance(exclusive=True)
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
            self._candidate_check(tx, policy_model(value))
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
            if tx.tenant_id == "_platform":
                invalidate_publications(tx)
                self._organization_impacts(tx, actor, reason="policy_revoked", revoked_digest=compiled_digest)
        self.audit.append(tx, "policy_" + next_state, actor_id=actor["principal_id"], policy_digest=compiled_digest)
        return approval

    def publish_baseline(self, tx, identity, refs, expected_revision):
        require_role(identity, {"tenant_admin"} if tx.tenant_id != "_platform" else {"platform_admin"}, tx.tenant_id)
        if tx.tenant_id == "_platform":
            tx.lock_governance(exclusive=True)
        lock_admission(tx)
        tx.lock("baseline")
        if type(expected_revision) is not int or expected_revision < 0 or not isinstance(refs, (list, tuple)) or not 1 <= len(refs) <= 3:
            raise EvolutionError("validation_failed")
        refs = [record(PolicyRef.model_validate(record(ref))) for ref in refs]
        current = tx.get("baseline", "current")
        if (current["revision"] if current else 0) != expected_revision:
            raise EvolutionError("revision_conflict")
        retained = PublishedBaselines(tx).current() if tx.tenant_id == "_platform" else None
        for ref in refs:
            if ref["tenant_id"] == tx.tenant_id:
                candidate = self.eligible(tx, ref) if retained is None else self.eligible(tx, ref, baselines=[])
                if retained is not None:
                    for floor in retained:
                        self.compiler(tx).validate_retained_floors(policy_model(candidate), floor)
            else:
                self._baseline_policy(tx, ref)
        baselines = [self._baseline_policy(tx, ref) for ref in refs]
        if tx.tenant_id == "_platform":
            publish_closure(tx, refs)
            impacts = self._organization_impacts(tx, principal(identity), baselines=baselines,
                                                  revision=expected_revision + 1)
        else:
            impacts = self._binding_impacts(tx, baselines, expected_revision + 1)
        data = {"policies": refs, "revision": expected_revision + 1, "created_at": current["created_at"] if current else utc_now()}
        if current:
            tx.put("baseline", "current", data, expected_revision=expected_revision)
        elif expected_revision == 0:
            tx.insert("baseline", "current", data)
        else:
            raise EvolutionError("revision_conflict")
        if tx.tenant_id == "_platform":
            invalidate_publications(tx)
        self.audit.append(tx, "baseline_published", actor_id=principal(identity)["principal_id"])
        tx.append("outbox", {"type": "baseline_published", "baseline_revision": data["revision"], "blocked_workload_ids": sorted(set(impacts)), "created_at": utc_now()})
        return {"revision": data["revision"], "affected_workloads": sorted(set(impacts))}

    def _binding_impacts(self, tx, baselines, revision, *, reason="floor_conflict"):
        lock_admission(tx)
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
            try:
                raw = self.get(tx, binding["policy"]["policy_id"], binding["policy"]["version"])
                if raw["status"] != "approved" or raw["compiled_digest"] != binding["policy"]["digest"]:
                    raise EvolutionError("policy_revoked")
                policy = policy_model(raw)
                self.compiler(tx).validate_dependencies(policy)
                for baseline in baselines:
                    if baseline.status != "approved":
                        raise EvolutionError("policy_revoked")
                    self.compiler(tx).validate_current(policy, baseline)
            except EvolutionError:
                impacts.append(binding["workload_id"])
                old_block = tx.get("binding_blocks", binding["workload_id"])
                blocked = {"workload_id": binding["workload_id"], "binding_revision": binding["revision"],
                           "baseline_revision": revision, "reason_code": reason, "created_at": utc_now()}
                if old_block:
                    tx.put("binding_blocks", binding["workload_id"], blocked, expected_revision=old_block["revision"])
                else:
                    tx.insert("binding_blocks", binding["workload_id"], blocked)
        return impacts

    def _organization_impacts(self, tx, actor, *, baselines=None, revision=None, reason="floor_conflict", revoked_digest=None):
        if tx.tenant_id != "_platform":
            raise EvolutionError("forbidden")
        impacts = []
        publication = tx.get("baseline", "current")
        revision = revision if revision is not None else publication["revision"] if publication else 0
        # During publication the new floors are supplied; during revocation the
        # published port intentionally returns revoked status for ancestry.
        global_baselines = baselines if baselines is not None else PublishedBaselines(tx).current()
        for tenant in tenant_scopes(tx):
            with _scope(tx, tenant) as target:
                affected = self._binding_impacts(target, global_baselines, revision, reason=reason)
                if affected:
                    self.audit.append(target, "baseline_published" if reason == "floor_conflict" else "policy_revoked",
                                      actor_id=actor["principal_id"], policy_digest=revoked_digest)
                    target.append("outbox", {"type": "baseline_published" if reason == "floor_conflict" else "policy_revoked",
                        **({"digest": revoked_digest} if revoked_digest is not None else {}),
                        "baseline_revision": revision, "blocked_workload_ids": sorted(set(affected)), "created_at": utc_now()})
                    impacts.extend(affected)
        return impacts
