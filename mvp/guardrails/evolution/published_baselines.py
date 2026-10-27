"""Transaction-bound access to explicitly published organization policy bytes.

There is no public arbitrary-scope repository or private-platform query port.
Every platform policy read first proves an immutable publication marker. Scope
switches stay on the caller's SQL transaction and restore its RLS context.
"""
from __future__ import annotations

from contextlib import contextmanager

from .compiler import MAX_EXPANDED_POLICIES, MAX_PARENT_DEPTH, policy_digest
from .errors import EvolutionError
from .models import PolicyRef, PolicyVersion, canonical_json_bytes, utc_now
from .storage import _SCOPE, TenantTransaction


@contextmanager
def _scope(tx, tenant):
    """Private assembly seam; callers constrain targets before using this view."""
    tx.lock_governance()
    if tenant == tx.tenant_id:
        yield tx
        return
    tx.execute("SELECT set_config('guardrails.tenant_id', :tenant, true)", {"tenant": tenant})
    try:
        yield TenantTransaction(tx.connection, tenant, _governance_owner=tx._governance_owner)
    finally:
        # SQL cannot run after a PostgreSQL statement error. The original
        # transaction must roll back; do not replace its primary exception.
        if tx.connection.connection.driver_connection.info.transaction_status.name != "INERROR":
            tx.execute("SELECT set_config('guardrails.tenant_id', :tenant, true)", {"tenant": tx.tenant_id})


def _ref(value):
    return PolicyRef.model_validate_json(canonical_json_bytes(value))


def _policy(value):
    return PolicyVersion.model_validate_json(canonical_json_bytes(value))


def invalidate_publications(tx):
    """Discard only this SQL connection's transaction-local publication reads."""
    if hasattr(tx, "connection"):
        tx.connection.info.pop("evolution_published_baselines", None)


class PublishedBaselines:
    """The production PublishedBaselinePort, bound to one admitted transaction."""
    def __init__(self, tx):
        self._tx = tx

    def _cache(self):
        self._tx.lock_governance()
        if not hasattr(self._tx, "connection"):
            return {}
        connection = self._tx.connection
        scope = (connection.get_transaction(), connection.get_nested_transaction())
        previous = connection.info.get("evolution_published_baselines")
        if previous is None or previous[0] != scope:
            previous = (scope, {})
            connection.info["evolution_published_baselines"] = previous
        return previous[1]

    @staticmethod
    def _read(platform, ref, active, completed, depth):
        key = ref.policy_id, ref.version
        if ref.tenant_id != "_platform":
            raise EvolutionError("resource_not_found")
        if key in active or depth > MAX_PARENT_DEPTH:
            raise EvolutionError("composition_conflict")
        if key in completed:
            if completed[key].compiled_digest != ref.digest:
                raise EvolutionError("composition_conflict")
            return completed[key]
        if len(active) + len(completed) >= MAX_EXPANDED_POLICIES:
            raise EvolutionError("composition_conflict")
        marker = platform.get("published_baselines", ref.digest)
        if marker is None or marker.get("policy") != ref.model_dump(mode="json"):
            raise EvolutionError("resource_not_found")
        raw = platform.get("policies", f"{ref.policy_id}:{ref.version}", required=True)
        policy = _policy(raw)
        if policy.reference() != ref or policy_digest(policy) != ref.digest:
            raise EvolutionError("policy_unresolved")
        status = platform.get("policy_status", f"{ref.policy_id}:{ref.version}", required=True)["status"]
        revoked = status != "approved" or platform.get("revocations", ref.digest) is not None
        active.add(key)
        for parent in policy.parents:
            ancestor = PublishedBaselines._read(platform, parent, active, completed, depth + 1)
            revoked |= ancestor.status != "approved"
        active.remove(key)
        policy = _policy({**raw, "status": "revoked" if revoked else "approved"})
        completed[key] = policy
        return policy

    def get_policy(self, reference):
        ref = _ref(reference)
        if ref.tenant_id != "_platform":
            raise EvolutionError("resource_not_found")
        cache = self._cache()
        key = ("policy", ref.policy_id, ref.version, ref.digest)
        if key not in cache:
            with _scope(self._tx, "_platform") as platform:
                value = self._read(platform, ref, set(), {}, 0)
                cache[key] = canonical_json_bytes(value.model_dump(mode="json"))
        # Return newly parsed values: nested rule configuration cannot mutate a
        # cached shared object. Only published immutable bytes enter this cache.
        return PolicyVersion.model_validate_json(cache[key])

    def current(self):
        cache = self._cache()
        if "current" in cache:
            return [PolicyVersion.model_validate_json(raw) for raw in cache["current"]]
        with _scope(self._tx, "_platform") as platform:
            publication = platform.get("baseline", "current")
            if publication is None:
                cache["current"] = ()
                return []
            refs = publication.get("policies")
            if not isinstance(refs, list) or not 1 <= len(refs) <= 3:
                raise EvolutionError("policy_unresolved")
            values = [self._read(platform, _ref(ref), set(), {}, 0) for ref in refs]
            cache["current"] = tuple(canonical_json_bytes(value.model_dump(mode="json")) for value in values)
            for value, raw in zip(values, cache["current"], strict=True):
                cache["policy", value.policy_id, value.version, value.compiled_digest] = raw
            return values


def publish_closure(tx, refs):
    """Platform-only write boundary; callers already checked approval/floors."""
    if tx.tenant_id != "_platform" or tx._governance_owner != "_platform":
        raise EvolutionError("forbidden")
    tx.lock_governance(exclusive=True)
    active, completed = set(), {}

    def visit(ref, depth):
        ref = _ref(ref)
        key = ref.policy_id, ref.version
        if ref.tenant_id != "_platform" or key in active or depth > MAX_PARENT_DEPTH:
            raise EvolutionError("composition_conflict")
        if key in completed:
            if completed[key].compiled_digest != ref.digest:
                raise EvolutionError("composition_conflict")
            return
        if len(active) + len(completed) >= MAX_EXPANDED_POLICIES:
            raise EvolutionError("composition_conflict")
        value = _policy(tx.get("policies", f"{ref.policy_id}:{ref.version}", required=True))
        state = tx.get("policy_status", f"{ref.policy_id}:{ref.version}", required=True)
        if value.reference() != ref or policy_digest(value) != ref.digest:
            raise EvolutionError("policy_unresolved")
        if state["status"] != "approved" or tx.get("revocations", ref.digest):
            raise EvolutionError("policy_revoked" if state["status"] == "revoked" else "approval_required")
        active.add(key)
        for parent in value.parents:
            visit(parent, depth + 1)
        active.remove(key)
        completed[key] = value

    for ref in refs:
        visit(ref, 0)
    for value in completed.values():
        marker = tx.get("published_baselines", value.compiled_digest)
        ref = value.reference().model_dump(mode="json")
        if marker is None:
            tx.insert("published_baselines", value.compiled_digest,
                      {"policy": ref, "published_at": utc_now()}, immutable=True)
        elif marker.get("policy") != ref:
            raise EvolutionError("policy_unresolved")


def tenant_scopes(tx):
    """Explicit directory enumeration for the platform-only impact writer."""
    if tx.tenant_id != "_platform" or tx._governance_owner != "_platform":
        raise EvolutionError("forbidden")
    tx.lock_governance(exclusive=True)
    after = None
    while True:
        rows = tx.page("tenant_directory", limit=100, after=after)
        for row in rows:
            data = row["data"]
            target = data["target_tenant_id"]
            if (not isinstance(target, str) or not _SCOPE.fullmatch(target)
                    or data["tenant_id"] != "_platform" or row["key"] != target or target == "_platform"):
                raise EvolutionError("policy_unresolved")
            yield target
        if len(rows) < 100:
            break
        after = rows[-1]["created_at"], rows[-1]["key"]
