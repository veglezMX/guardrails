"""Published-only visibility, immutable floors and same-transaction locking."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from guardrails.evolution import governance
from guardrails.evolution import published_baselines as published
from guardrails.evolution.compiler import PolicyCompiler, canonical_bytes
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.governance import (
    PolicyGovernance,
    lock_admission,
    require_role,
)
from guardrails.evolution.storage import TenantTransaction
from test_compiler import approved, changed, compiler_for, policy, rule, uid


class MemoryTx:
    def __init__(self, data, tenant, *, owner=None, calls=None):
        self.data, self.tenant_id = data, tenant
        self._governance_owner = owner or tenant
        self.calls = [] if calls is None else calls

    def get(self, kind, key, *, required=False, **_):
        self.calls.append(("read", self.tenant_id, kind, key))
        value = self.data.get((self.tenant_id, kind, str(key)))
        if value is None and required:
            raise EvolutionError("resource_not_found")
        return deepcopy(value)

    def insert(self, kind, key, value, **_):
        assert (self.tenant_id, kind, str(key)) not in self.data
        self.data[self.tenant_id, kind, str(key)] = deepcopy(value)
        return value

    def put(self, kind, key, value, *, expected_revision):
        current = self.get(kind, key, required=True)
        if current.get("revision", 1) != expected_revision:
            raise EvolutionError("revision_conflict")
        self.data[self.tenant_id, kind, str(key)] = deepcopy(value)

    def append(self, kind, value):
        self.calls.append(("append", self.tenant_id, kind, deepcopy(value)))

    def page(self, kind, *, limit, after=None, **_):
        return [{"key": key, "data": deepcopy(value), "created_at": "2026-10-12T00:00:00Z"}
                for (tenant, category, key), value in sorted(self.data.items())
                if tenant == self.tenant_id and category == kind and (after is None or key > after[1])][:limit]

    def next_version(self, kind, resource_id):
        return 1 + sum(tenant == self.tenant_id and category == kind and key.startswith(resource_id + ":")
                       for tenant, category, key in self.data)

    def lock_governance(self, *, exclusive=False):
        self.calls.append(("global", self.tenant_id, exclusive, self._governance_owner))

    def lock(self, key):
        self.calls.append(("lock", self.tenant_id, key))


class Audit:
    def __init__(self):
        self.events = []

    def append(self, tx, event, **kwargs):
        self.events.append((tx.tenant_id, event, kwargs))


@pytest.fixture
def memory(monkeypatch):
    @contextmanager
    def scope(tx, tenant):
        tx.lock_governance()
        yield MemoryTx(tx.data, tenant, owner=tx._governance_owner, calls=tx.calls)
    monkeypatch.setattr(published, "_scope", scope)
    monkeypatch.setattr(governance, "_scope", scope)
    return MemoryTx({}, "_platform")


def store(tx, value, *, status="approved", visible=True):
    key = f"{value.policy_id}:{value.version}"
    tx.data[value.tenant_id, "policies", key] = value.model_dump(mode="json")
    tx.data[value.tenant_id, "policy_status", key] = {"status": status, "revision": 1}
    if visible:
        tx.data[value.tenant_id, "published_baselines", value.compiled_digest] = {
            "policy": value.reference().model_dump(mode="json"), "published_at": "2026-10-12T00:00:00Z"}
    return value.reference().model_dump(mode="json")


def current(tx, refs, revision=1):
    tx.data["_platform", "baseline", "current"] = {
        "policies": refs, "revision": revision, "created_at": "2026-10-12T00:00:00Z"}


def actor(*roles, tenant=None, number=2):
    return {"principal_id": uid(number), "tenant_id": tenant, "roles": list(roles), "status": "active"}


def test_private_platform_policy_never_read_without_exact_publication(memory):
    floor = approved(policy(owner="_platform"))
    ref = store(memory, floor, visible=False)
    view = published.PublishedBaselines(MemoryTx(memory.data, "team-a", calls=memory.calls))
    with pytest.raises(EvolutionError, check=lambda error: error.code == "resource_not_found"):
        view.get_policy(ref)
    assert not any(call[:3] == ("read", "_platform", "policies") for call in memory.calls)
    store(memory, floor)
    assert canonical_bytes(view.get_policy(ref)) == canonical_bytes(floor)
    assert not any(call[2] in {"principals", "credentials", "config:providers"}
                   for call in memory.calls if call[0] == "read")


@pytest.mark.parametrize("owner", ["team-a", "team-b"])
def test_port_rejects_nonplatform_references_before_scope(memory, owner):
    ref = approved(policy(owner=owner)).reference()
    with pytest.raises(EvolutionError, check=lambda error: error.code == "resource_not_found"):
        published.PublishedBaselines(memory).get_policy(ref)
    assert memory.calls == []


def test_ancestor_closure_requires_publication_and_propagates_revocation(memory):
    parent = approved(policy(owner="_platform"))
    child = approved(policy(2, owner="_platform", parents=[parent.reference()]), compiler_for(parent))
    store(memory, parent, visible=False)
    ref = store(memory, child)
    port = published.PublishedBaselines(memory)
    with pytest.raises(EvolutionError, check=lambda error: error.code == "resource_not_found"):
        port.get_policy(ref)
    store(memory, parent)
    assert port.get_policy(ref).status == "approved"
    memory.data["_platform", "revocations", parent.compiled_digest] = {"digest": parent.compiled_digest}
    result = port.get_policy(ref)
    assert result.status == "revoked" and canonical_bytes(result) == canonical_bytes(child)
    assert memory.data["_platform", "policies", "policy-2:1"] == child.model_dump(mode="json")


@pytest.mark.parametrize("tamper", ["marker", "bytes", "digest"])
def test_published_bytes_and_reference_integrity(memory, tamper):
    floor = approved(policy(owner="_platform"))
    ref = store(memory, floor)
    if tamper == "marker":
        memory.data["_platform", "published_baselines", floor.compiled_digest]["policy"]["version"] = 2
    elif tamper == "bytes":
        memory.data["_platform", "policies", "policy-1:1"]["rules"] = []
    else:
        memory.data["_platform", "policies", "policy-1:1"]["compiled_digest"] = "f" * 64
    with pytest.raises(EvolutionError):
        published.PublishedBaselines(memory).get_policy(ref)


def test_automatic_floor_applies_to_policy_without_platform_parent(memory):
    floor = approved(policy(owner="_platform"))
    current(memory, [store(memory, floor)])
    tenant = MemoryTx(memory.data, "team-a", calls=memory.calls)
    child = approved(policy(2, rules=[]))
    ref = store(tenant, child, visible=False)
    service = PolicyGovernance(None, Audit())
    assert child.parents == ()
    assert service.current_baselines(tenant)[0].reference() == floor.reference()
    with pytest.raises(EvolutionError, check=lambda error: error.code == "floor_conflict"):
        service.eligible(tenant, ref)


def test_publish_records_all_approved_reachable_ancestors_only_after_validation(memory):
    first = approved(policy(owner="_platform"))
    second = approved(policy(2, owner="_platform", parents=[first.reference()]), compiler_for(first))
    store(memory, first, visible=False)
    ref = store(memory, second, visible=False)
    memory.data["_platform", "policy_status", "policy-1:1"]["status"] = "revoked"
    with pytest.raises(EvolutionError, check=lambda error: error.code == "policy_revoked"):
        published.publish_closure(memory, [ref])
    assert not any(key[1] == "published_baselines" for key in memory.data)
    memory.data["_platform", "policy_status", "policy-1:1"]["status"] = "approved"
    published.publish_closure(memory, [ref])
    assert {key[2] for key in memory.data if key[1] == "published_baselines"} == {first.compiled_digest, second.compiled_digest}
    assert memory.calls[0] == ("global", "_platform", True, "_platform")


@pytest.mark.parametrize("weaker", [False, True])
def test_revoked_current_floor_can_be_replaced_without_runtime_admission_or_status_rewrite(memory, weaker):
    old = approved(policy(owner="_platform"))
    current(memory, [store(memory, old, status="revoked")])
    candidate = approved(policy(2, owner="_platform", rules=[] if weaker else [rule()]))
    ref = store(memory, candidate, visible=False)
    service = PolicyGovernance(None, Audit())
    before = deepcopy(memory.data["_platform", "policies", "policy-1:1"])
    tenant = MemoryTx(memory.data, "team-a")
    with pytest.raises(EvolutionError, check=lambda error: error.code == "policy_revoked"):
        service.current_baselines(tenant)
    if weaker:
        with pytest.raises(EvolutionError, check=lambda error: error.code == "floor_conflict"):
            service.publish_baseline(memory, actor("platform_admin"), [ref], 1)
        assert memory.data["_platform", "baseline", "current"]["revision"] == 1
    else:
        assert service.publish_baseline(memory, actor("platform_admin"), [ref], 1) == {"revision": 2, "affected_workloads": []}
        assert service.current_baselines(tenant)[0].reference() == candidate.reference()
    assert memory.data["_platform", "policies", "policy-1:1"] == before
    assert memory.data["_platform", "policy_status", "policy-1:1"]["status"] == "revoked"


def test_candidate_floor_check_supports_independent_recovery_approval_only(memory):
    floor = approved(policy(owner="_platform"))
    current(memory, [store(memory, floor, status="revoked")])
    candidate = PolicyCompiler().compile(policy(2, owner="_platform"))
    store(memory, candidate, status="candidate", visible=False)
    service = PolicyGovernance(None, Audit())
    service._candidate_check(memory, candidate)
    with pytest.raises(EvolutionError, check=lambda error: error.code == "approval_required"):
        service.publish_baseline(memory, actor("platform_admin"), [candidate.reference()], 1)
    result = service.review(memory, actor("platform_admin", "approver"), "policy-2", 1,
        outcome="approve", compiled_digest=candidate.compiled_digest, reason="Independent recovery review")
    assert result["outcome"] == "approve"
    with pytest.raises(EvolutionError, check=lambda error: error.code == "policy_revoked"):
        service.eligible(memory, candidate.reference())


def test_floor_only_validator_never_changes_status_or_grants_current_eligibility():
    floor = changed(approved(policy(owner="_platform")), status="revoked")
    candidate = approved(policy(2))
    before = canonical_bytes(floor), floor.status
    PolicyCompiler().validate_retained_floors(candidate, floor)
    with pytest.raises(EvolutionError, check=lambda error: error.code == "approval_required"):
        PolicyCompiler().validate_current(candidate, floor)
    assert (canonical_bytes(floor), floor.status) == before


@pytest.mark.parametrize("roles,allowed", [(('platform_admin',), False), (('approver',), False), (('platform_admin', 'approver'), True)])
def test_platform_review_requires_explicit_approver_and_namespace_authority(roles, allowed):
    if allowed:
        assert require_role(actor(*roles), {"approver"}, "_platform")["roles"] == list(roles)
    else:
        with pytest.raises(EvolutionError):
            require_role(actor(*roles), {"approver"}, "_platform")


def test_platform_cannot_review_own_candidate_even_with_both_roles(memory):
    candidate = PolicyCompiler().compile(policy(owner="_platform"))
    store(memory, candidate, status="candidate", visible=False)
    with pytest.raises(EvolutionError, check=lambda error: error.code == "self_approval"):
        PolicyGovernance(None, Audit()).review(memory, actor("platform_admin", "approver", number=1), "policy-1", 1,
            outcome="approve", compiled_digest=candidate.compiled_digest, reason="Invalid self review")


def test_emergency_tenant_revocation_platform_role_is_preserved():
    assert require_role(actor("platform_admin"), {"approver", "platform_admin"}, "team-a")["principal_id"] == uid(2)
    with pytest.raises(EvolutionError):
        require_role(actor("platform_admin"), {"approver"}, "team-a")


def test_impact_blocks_same_workload_in_each_incompatible_tenant_only(memory):
    floor = approved(policy(owner="_platform"))
    ref = store(memory, floor, visible=False)
    for number, tenant in enumerate(("team-a", "team-b", "team-c"), 2):
        memory.data["_platform", "tenant_directory", tenant] = {"tenant_id": "_platform", "target_tenant_id": tenant}
        value = approved(policy(number, owner=tenant, rules=[] if tenant != "team-c" else [rule()]))
        binding_ref = store(memory, value, visible=False)
        memory.data[tenant, "binding_pointers", "chat"] = {"workload_id": "chat", "binding_revision": 1}
        memory.data[tenant, "bindings", "chat:1"] = {"workload_id": "chat", "revision": 1, "policy": binding_ref}
    audit = Audit()
    result = PolicyGovernance(None, audit).publish_baseline(memory, actor("platform_admin"), [ref], 0)
    assert result == {"revision": 1, "affected_workloads": ["chat"]}
    assert {(tenant, key) for tenant, kind, key in memory.data if kind == "binding_blocks"} == {("team-a", "chat"), ("team-b", "chat")}
    assert {tenant for tenant, event, _ in audit.events if event == "baseline_published"} == {"team-a", "team-b", "_platform"}
    assert all(call[3] == "_platform" for call in memory.calls if call[0] == "global")


class Connection:
    def __init__(self):
        self.info, self.calls, self.root, self.nested = {}, [], object(), None
        self.connection = SimpleNamespace(driver_connection=SimpleNamespace(info=SimpleNamespace(transaction_status=SimpleNamespace(name="INTRANS"))))

    def get_transaction(self):
        return self.root

    def get_nested_transaction(self):
        return self.nested

    def execute(self, statement, parameters):
        self.calls.append((str(statement), parameters))


def test_global_lock_is_shared_for_both_ordinary_platform_and_tenant_admission():
    for tenant in ("_platform", "team-a"):
        connection = Connection()
        lock_admission(TenantTransaction(connection, tenant))
        assert "pg_advisory_xact_lock_shared" in connection.calls[0][0]
        assert connection.calls[1][1]["key"] == tenant + ":governance-admission"


def test_global_exclusive_cache_survives_scope_view_and_reacquires_after_savepoint():
    connection = Connection()
    original = TenantTransaction(connection, "_platform")
    original.lock_governance(exclusive=True)
    view = TenantTransaction(connection, "team-a", _governance_owner="_platform")
    view.lock_governance()
    assert len(connection.calls) == 1
    connection.nested = object()
    view.lock_governance()
    connection.nested = None
    original.lock_governance()
    assert len(connection.calls) == 3
    assert all("lock_shared" not in statement for statement, _ in connection.calls)


@pytest.mark.parametrize("nested", [False, True])
def test_shared_upgrade_refuses_without_wait_even_across_savepoint(nested):
    connection = Connection()
    tx = TenantTransaction(connection, "_platform")
    tx.lock_governance()
    if nested:
        connection.nested = object()
    with pytest.raises(EvolutionError, check=lambda error: error.code == "revision_conflict"):
        tx.lock_governance(exclusive=True)
    assert len(connection.calls) == 1
    connection.root, connection.nested = object(), None
    tx.lock_governance(exclusive=True)
    assert len(connection.calls) == 2


def test_tenant_cannot_acquire_exclusive_even_after_scope_switch():
    connection = Connection()
    view = TenantTransaction(connection, "_platform", _governance_owner="team-a")
    with pytest.raises(EvolutionError, check=lambda error: error.code == "forbidden"):
        view.lock_governance(exclusive=True)
    assert connection.calls == []


@pytest.mark.parametrize("aborted", [False, True])
def test_actual_private_scope_restores_rls_without_masking_primary_exception(aborted):
    connection = Connection()
    tx = TenantTransaction(connection, "team-a")
    with pytest.raises(RuntimeError, match="primary"), published._scope(tx, "_platform") as view:
        assert view.connection is connection and view._governance_owner == "team-a"
        if aborted:
            connection.connection.driver_connection.info.transaction_status.name = "INERROR"
        raise RuntimeError("primary")
    switches = [parameters["tenant"] for statement, parameters in connection.calls if "set_config" in statement]
    assert switches == (["_platform"] if aborted else ["_platform", "team-a"])


def test_published_cache_is_transaction_local_and_returns_independent_nested_values(memory):
    floor = approved(policy(owner="_platform"))
    ref = store(memory, floor)
    current(memory, [ref])
    memory.connection = Connection()
    port = published.PublishedBaselines(memory)
    first = port.current()[0]
    read_count = sum(call[0] == "read" for call in memory.calls)
    object.__setattr__(first.rules[0].config, "entities", ("ipv4",))
    assert port.current()[0].rules[0].config.entities == ("email",)
    assert port.get_policy(ref).rules[0].config.entities == ("email",)
    assert sum(call[0] == "read" for call in memory.calls) == read_count
    memory.connection.nested = object()
    port.current()
    assert sum(call[0] == "read" for call in memory.calls) > read_count
    read_count = sum(call[0] == "read" for call in memory.calls)
    memory.connection.root, memory.connection.nested = object(), None
    port.current()
    assert sum(call[0] == "read" for call in memory.calls) > read_count


def test_revocation_invalidates_same_transaction_published_cache(memory):
    floor = approved(policy(owner="_platform"))
    ref = store(memory, floor)
    current(memory, [ref])
    memory.connection = Connection()
    assert published.PublishedBaselines(memory).current()[0].status == "approved"
    service = PolicyGovernance(None, Audit())
    service.review(memory, actor("platform_admin"), "policy-1", 1, outcome="revoke",
                   compiled_digest=floor.compiled_digest, reason="Emergency revoke")
    assert published.PublishedBaselines(memory).current()[0].status == "revoked"
    with pytest.raises(EvolutionError, check=lambda error: error.code == "policy_revoked"):
        service.current_baselines(memory)


def test_unrelated_platform_private_revocation_does_not_block_other_tenant(memory):
    floor = approved(policy(owner="_platform"))
    current(memory, [store(memory, floor)])
    private = approved(policy(2, owner="_platform"))
    store(memory, private, visible=False)
    tenant = "team-a"
    memory.data["_platform", "tenant_directory", tenant] = {"tenant_id": "_platform", "target_tenant_id": tenant}
    own = approved(policy(3))
    ref = store(memory, own, visible=False)
    memory.data[tenant, "binding_pointers", "chat"] = {"workload_id": "chat", "binding_revision": 1}
    memory.data[tenant, "bindings", "chat:1"] = {"workload_id": "chat", "revision": 1, "policy": ref}
    PolicyGovernance(None, Audit()).review(memory, actor("platform_admin"), "policy-2", 1, outcome="revoke",
        compiled_digest=private.compiled_digest, reason="Private policy revoke")
    assert (tenant, "binding_blocks", "chat") not in memory.data


def test_platform_management_config_and_policy_read_are_scoped_not_audit_authority(memory):
    from guardrails.evolution.management_api import ManagementApi
    api = ManagementApi.__new__(ManagementApi)
    api.audit = Audit()
    api.governance = PolicyGovernance(None, api.audit)
    identity = actor("platform_admin")
    resource, status = api.dispatch(memory, identity, "POST", ["lists"],
        {"kind": "content", "allow_enabled": False, "allow": [], "deny": ["term:blocked"]}, {}, None)
    assert status == 201 and resource["tenant_id"] == "_platform"
    result, status = api.dispatch(memory, identity, "GET", ["lists", resource["list_id"], "versions", "1"], {}, {}, None)
    assert status == 200 and result == resource
    value = PolicyCompiler().compile(policy(owner="_platform"))
    store(memory, value, status="candidate", visible=False)
    result, status = api.dispatch(memory, identity, "GET", ["policies", "policy-1", "versions", "1"], {}, {}, None)
    assert status == 200 and result["policy"]["status"] == "candidate"
    with pytest.raises(EvolutionError, check=lambda error: error.code == "forbidden"):
        api.dispatch(memory, identity, "GET", ["audit"], {}, {}, None)
    tenant = MemoryTx(memory.data, "team-a")
    with pytest.raises(EvolutionError):
        api.dispatch(tenant, identity, "GET", ["lists", resource["list_id"], "versions", "1"], {}, {}, None)
    with pytest.raises(EvolutionError):
        api.dispatch(tenant, identity, "POST", ["lists"], {"kind": "content"}, {}, None)


def test_stale_publication_revision_refuses_before_markers_or_impact_writes(memory):
    floor = approved(policy(owner="_platform"))
    ref = store(memory, floor, visible=False)
    before = deepcopy(memory.data)
    with pytest.raises(EvolutionError, check=lambda error: error.code == "revision_conflict"):
        PolicyGovernance(None, Audit()).publish_baseline(memory, actor("platform_admin"), [ref], 9)
    assert memory.data == before


def test_tenant_publication_preserves_two_argument_eligible_override(memory):
    tenant = MemoryTx(memory.data, "team-a", calls=memory.calls)
    candidate = approved(policy())
    ref = store(tenant, candidate, visible=False)
    service = PolicyGovernance(None, Audit())
    original = service.eligible
    observed = []
    def eligible(tx, candidate_ref):
        observed.append((tx, candidate_ref))
        return original(tx, candidate_ref)
    service.eligible = eligible
    result = service.publish_baseline(tenant, actor("tenant_admin", tenant="team-a"), [ref], 0)
    assert result == {"revision": 1, "affected_workloads": []}
    assert observed == [(tenant, ref)]


def test_platform_empty_retained_floor_passes_explicit_empty_baselines(memory):
    candidate = approved(policy(owner="_platform"))
    ref = store(memory, candidate, visible=False)
    service = PolicyGovernance(None, Audit())
    original = service.eligible
    observed = []
    def eligible(tx, candidate_ref, *, baselines):
        observed.append((tx, candidate_ref, baselines))
        return original(tx, candidate_ref, baselines=baselines)
    service.eligible = eligible
    result = service.publish_baseline(memory, actor("platform_admin"), [ref], 0)
    assert result == {"revision": 1, "affected_workloads": []}
    assert observed == [(memory, ref, [])]
