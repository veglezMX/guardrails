"""Explicit Phase3 governance commands over an authenticated management port.

The caller supplies existing scoped credentials and a durable operation UUID.
No seed/bootstrap, baseline mutation, direct SQL, endpoint dispatch or receipt
fabrication occurs here. Idempotency keys are stable within that operation.
"""
from __future__ import annotations

import copy
import ipaddress
import time
from uuid import UUID, uuid5

from guardrails.evolution import models
from guardrails.evolution.compiler import policy_digest
from guardrails.evolution.errors import EvolutionError


def require(value, code="validation_failed"):
    if not value:
        raise EvolutionError(code)


def reference(resource, field):
    return {"id": resource[field], "version": resource["version"], "digest": resource["digest"]}


def policy_reference(policy):
    return {"tenant_id": policy["tenant_id"], "policy_id": policy["policy_id"],
            "version": policy["version"], "digest": policy["compiled_digest"]}


def operator_provider(value, *, credential_ref, expected=None):
    """Restore only the caller-owned reference omitted by management safe_view."""
    require(type(value) is dict)
    if expected is not None:
        pinned = models.ProviderProfile.model_validate_json(models.canonical_json_bytes(expected))
        require(pinned.digest == models.resource_digest(pinned) and pinned.credential_ref == credential_ref)
        require(all(value[field] == getattr(pinned, field)
                    for field in ("tenant_id", "provider_id", "version", "digest")), "revision_conflict")
    require(value.get("credential_ref", credential_ref) == credential_ref)
    restored = {**value, "credential_ref": credential_ref}
    parsed = models.ProviderProfile.model_validate_json(models.canonical_json_bytes(restored))
    require(parsed.digest == models.resource_digest(parsed))
    return parsed.model_dump(mode="json")


def checked_policy(value, tenant, *, status, operator_providers=()):
    value = copy.deepcopy(value)
    pins = {}
    for provider in operator_providers:
        parsed = models.ProviderProfile.model_validate_json(models.canonical_json_bytes(provider))
        require(parsed.tenant_id == tenant and parsed.digest == models.resource_digest(parsed))
        key = (parsed.provider_id, parsed.version, parsed.digest)
        require(key not in pins)
        pins[key] = parsed.model_dump(mode="json")
    for index, provider in enumerate(value["providers"]):
        key = (provider["provider_id"], provider["version"], provider["digest"])
        if key in pins:
            value["providers"][index] = operator_provider(provider,
                credential_ref=pins[key]["credential_ref"], expected=pins[key])
    policy = models.PolicyVersion.model_validate_json(models.canonical_json_bytes(value))
    require(policy.tenant_id == tenant and policy.status == status)
    require(policy_digest(policy) == policy.compiled_digest)
    for resource in (policy.residency, policy.quota, *policy.resolved_lists,
                     *policy.providers, *policy.resolved_destinations, *policy.tools):
        require(resource.digest == models.resource_digest(resource))
    return policy.model_dump(mode="json")


def payload_from_approved(base, rule, *, replacements=(), lists=()):
    """Preserve the baseline's rules/floors, replacing only explicit resource versions.

    Do not inherit the old policy as a parent: that would merge two versions of
    the same resource identity. Server validation still enforces current floors.
    """
    base = checked_policy(base, base["tenant_id"], status="approved")
    rule = models.Rule.model_validate_json(models.canonical_json_bytes(rule)).model_dump(mode="json")
    require(rule["rule_id"] not in {row["rule_id"] for row in base["rules"]})
    resources = {"providers": copy.deepcopy(base["providers"]),
                 "resolved_destinations": copy.deepcopy(base["resolved_destinations"])}
    seen = set()
    for field, updated in replacements:
        require(field in resources)
        cls, identity = ((models.ProviderProfile, "provider_id") if field == "providers"
                         else (models.Destination, "destination_id"))
        value = cls.model_validate_json(models.canonical_json_bytes(updated))
        require(value.tenant_id == base["tenant_id"] and value.digest == models.resource_digest(value))
        key = (field, getattr(value, identity))
        require(key not in seen)
        seen.add(key)
        previous = [item for item in resources[field] if item[identity] == key[1]]
        require(len(previous) == 1 and value.version > previous[0]["version"])
        resources[field] = [value.model_dump(mode="json") if item[identity] == key[1] else item
                            for item in resources[field]]
    appended = []
    existing = {value["list_id"] for value in base["resolved_lists"]}
    for value in lists:
        item = models.ListSet.model_validate_json(models.canonical_json_bytes(value))
        require(item.tenant_id == base["tenant_id"] and item.kind == "content"
                and item.list_id not in existing and item.digest == models.resource_digest(item))
        existing.add(item.list_id)
        appended.append(item.model_dump(mode="json"))
    return {"parents": [], "rules": [*copy.deepcopy(base["rules"]), rule],
        "residency_ref": reference(base["residency"], "residency_id"),
        "quota_ref": reference(base["quota"], "quota_id"),
        "list_refs": [reference(row, "list_id") for row in (*base["resolved_lists"], *appended)],
        "provider_refs": [reference(row, "provider_id") for row in resources["providers"]],
        "destination_refs": [reference(row, "destination_id") for row in resources["resolved_destinations"]],
        "tool_refs": [reference(row, "tool_id") for row in base["tools"]],
        "failure_behavior": base["failure_behavior"]}


def candidate_matches_payload(candidate, payload):
    """Authenticate the requested materialization before independent review."""
    require(set(payload) == {"parents", "rules", "residency_ref", "quota_ref", "list_refs",
                            "provider_refs", "destination_refs", "tool_refs", "failure_behavior"})
    require(payload["parents"] == candidate["parents"] == [])
    require(candidate["failure_behavior"] == payload["failure_behavior"])
    expected_rules = [models.Rule.model_validate_json(models.canonical_json_bytes(row)).model_dump(mode="json")
                      for row in payload["rules"]]
    require(len({row["rule_id"] for row in expected_rules}) == len(expected_rules))
    require(sorted(candidate["rules"], key=lambda row: row["rule_id"]) ==
            sorted(expected_rules, key=lambda row: row["rule_id"]))
    for field, identity, requested in (("residency", "residency_id", "residency_ref"),
                                      ("quota", "quota_id", "quota_ref")):
        require(reference(candidate[field], identity) == payload[requested])
    for field, identity, requested in (("resolved_lists", "list_id", "list_refs"),
                                      ("providers", "provider_id", "provider_refs"),
                                      ("resolved_destinations", "destination_id", "destination_refs"),
                                      ("tools", "tool_id", "tool_refs")):
        actual = [reference(row, identity) for row in candidate[field]]
        expected = payload[requested]
        require(len({row["id"] for row in expected}) == len(expected))
        require(sorted(actual, key=lambda row: row["id"]) == sorted(expected, key=lambda row: row["id"]))


def owned_revision(handle, pointer, binding):
    """Refuse cleanup against a pointer or binding outside the acknowledged CAS."""
    require(pointer["binding_revision"] == handle["binding_revision"], "revision_conflict")
    for field, expected in (("tenant_id", handle["tenant_id"]), ("workload_id", handle["workload_id"]),
                            ("revision", handle["binding_revision"]), ("release_id", handle["release_id"]),
                            ("policy", handle["policy"]), ("state", handle["state"]),
                            ("previous_revision", handle["previous_revision"]), ("activated_by", handle["actor_id"])):
        require(binding[field] == expected, "revision_conflict")
    return handle["binding_revision"]


class GovernanceJourney:
    """Role-separated management calls; the injected port owns TLS and bounds.

    ``call(method, suffix, role=..., body=..., key=..., expected=...)`` must use
    an authenticated tenant-scoped management endpoint. It returns parsed JSON
    only after checking the exact response status. No automatic mutation retry.
    """
    def __init__(self, *, tenant, operation, actors, call, clock=time.monotonic, sleep=time.sleep,
                 journal=None, operator_providers=()):
        require(type(tenant) is str and tenant in {"team-a", "team-b"})
        require(str(UUID(operation)) == operation)
        require(set(actors) == {"tenant_admin", "author", "approver", "operator", "auditor"})
        require(all(str(UUID(value)) == value for value in actors.values()))
        require(len(set(actors.values())) == len(actors), "self_approval")
        self.tenant, self.operation, self.actors = tenant, UUID(operation), dict(actors)
        self.call, self.clock, self.sleep = call, clock, sleep
        self.last_release = None
        self.operator_providers = tuple(copy.deepcopy(operator_providers))
        for provider in self.operator_providers:
            parsed = models.ProviderProfile.model_validate_json(models.canonical_json_bytes(provider))
            require(parsed.tenant_id == tenant and parsed.digest == models.resource_digest(parsed))
        self.journal = journal
        require(journal is None or (journal.operation == operation and journal.tenant == tenant
                                    and journal.actors == actors))

    def _request(self, method, path, *, role, body=None, label=None, expected=200):
        require(role in self.actors and method in {"GET", "POST"})
        require(path.startswith("/") and not any(value in path for value in ("?", "#", "..")))
        require((method == "POST") == (label is not None))
        key = str(uuid5(self.operation, self.tenant + ":" + label)) if label is not None else None
        if self.journal is not None and method == "POST":
            self.journal.before_request(method=method, path=path, role=role, body=body, key=key, expected=expected)
        result = self.call(method, path, role=role, body=body, key=key, expected=expected)
        if self.journal is not None and method == "POST":
            self.journal.after_request(result)
        return result

    def classifier_versions(self, *, previous_provider, previous_destination,
                            address, model_id, artifact_digest, credential_ref):
        """Create reviewed-resource candidates on already-allowed Ollama IDs.

        Existing version-one bytes and all approvals remain untouched. Returned
        versions are not effective until a separate policy approval and release.
        """
        provider = models.ProviderProfile.model_validate_json(models.canonical_json_bytes(previous_provider))
        destination = models.Destination.model_validate_json(models.canonical_json_bytes(previous_destination))
        require(provider.tenant_id == destination.tenant_id == self.tenant)
        require(provider.version == destination.version == 1)
        require(provider.kind == "model" and provider.vendor == "ollama"
                and provider.destination_id == destination.destination_id
                and destination.purpose == "inference")
        require(provider.digest == models.resource_digest(provider)
                and destination.digest == models.resource_digest(destination))
        address = ipaddress.ip_address(address)
        require(address.is_private and not address.is_loopback and not address.is_unspecified
                and not address.is_multicast)
        from guardrails.evolution.classifier_contract import MODEL_ID, WEIGHTS_SHA256
        require(model_id == MODEL_ID and artifact_digest == WEIGHTS_SHA256)
        require(type(credential_ref) is str and bool(credential_ref))
        for resource, family, identity in ((provider, "providers", "provider_id"),
                                          (destination, "destinations", "destination_id")):
            actual = self._request("GET", f"/{family}/{getattr(resource, identity)}/versions/1", role="operator")
            if family == "providers":
                actual = operator_provider(actual, credential_ref=provider.credential_ref,
                                           expected=provider.model_dump(mode="json"))
            require(models.canonical_json_bytes(actual) == models.canonical_json_bytes(resource), "revision_conflict")
        destination_body = {"name": "classifier", "host": "classifier.gr-platform.svc.cluster.local",
            "port": 8443, "path_prefix": "/v1/classifier", "cidrs": [str(address) + ("/32" if address.version == 4 else "/128")],
            "zone": "local-shared", "purpose": "evaluation", "tls_ca_ref": destination.tls_ca_ref}
        new_destination = self._request("POST", f"/destinations/{destination.destination_id}/versions",
            role="tenant_admin", body=destination_body, label="classifier-destination-version", expected=201)
        def validate_created(value, cls, identity, previous, body):
            checked = cls.model_validate_json(models.canonical_json_bytes(value))
            require(checked.tenant_id == self.tenant and checked.version == 2
                    and getattr(checked, identity) == getattr(previous, identity)
                    and checked.digest == models.resource_digest(checked))
            require(all(value[name] == expected for name, expected in body.items()))
        validate_created(new_destination, models.Destination, "destination_id", destination, destination_body)
        provider_body = {"kind": "evaluator", "vendor": "local_classifier", "model_id": model_id,
            "artifact_digest": artifact_digest, "destination_id": destination.destination_id,
            "zone": "local-shared", "credential_ref": credential_ref}
        new_provider = self._request("POST", f"/providers/{provider.provider_id}/versions",
            role="tenant_admin", body=provider_body, label="classifier-provider-version", expected=201)
        new_provider = operator_provider(new_provider, credential_ref=credential_ref)
        validate_created(new_provider, models.ProviderProfile, "provider_id", provider, provider_body)
        return {"status": "resource_candidates_not_approved", "provider": new_provider,
                "destination": new_destination}

    def content_list(self, kind):
        require(kind in {"term", "topic"})
        body = {"kind": "content", "allow_enabled": False, "allow": [],
                "deny": ["term:forbidden-fixture" if kind == "term" else "topic:finance"]}
        value = self._request("POST", "/lists", role="author", body=body,
            label=kind + "-content-list", expected=201)
        checked = models.ListSet.model_validate_json(models.canonical_json_bytes(value))
        require(checked.tenant_id == self.tenant and checked.version == 1
                and checked.digest == models.resource_digest(checked)
                and all(value[key] == expected for key, expected in body.items()))
        return value

    def approve(self, payload):
        policy_id = "eval-" + self.operation.hex
        candidate = self._request("POST", f"/policies/{policy_id}/versions", role="author",
            body=payload, label="candidate", expected=201)
        checked = checked_policy(candidate, self.tenant, status="candidate", operator_providers=self.operator_providers)
        require(checked["author_id"] == self.actors["author"]
                and checked["policy_id"] == policy_id and checked["version"] == 1)
        candidate_matches_payload(checked, payload)
        approval = self._request("POST", f"/policies/{policy_id}/versions/1/reviews",
            role="approver", body={"outcome": "approve", "compiled_digest": checked["compiled_digest"],
            "reason": "Independent Phase3 evaluator review"}, label="review")
        review = models.Approval.model_validate_json(models.canonical_json_bytes(approval))
        require(review.actor_id == self.actors["approver"] and review.outcome == "approve"
                and review.policy.model_dump(mode="json") == policy_reference(checked))
        actual = self._request("GET", f"/policies/{policy_id}/versions/1", role="operator")
        approved = checked_policy(actual["policy"], self.tenant, status="approved", operator_providers=self.operator_providers)
        require({**approved, "status": "candidate"} == checked
                and approval in actual["approvals"])
        return approved

    def release(self, *, policy, workload_id, state, expected_revision, runtime_ids, label,
                seconds=120):
        require(type(expected_revision) is int and expected_revision >= 0)
        require(state in {"disabled", "observe", "warn", "redact", "block"})
        require(type(seconds) in (int, float) and 0 < seconds <= 120)
        require(len(runtime_ids) == len(set(runtime_ids)) == 2)
        require(all(str(UUID(value)) == value for value in runtime_ids))
        checked = checked_policy(policy, self.tenant, status="approved")
        reference = policy_reference(checked)
        current = self._request("GET", "/workloads/" + workload_id, role="operator")
        require(current["tenant_id"] == self.tenant and current["workload_id"] == workload_id)
        pointer = current["desired_binding"]
        require(pointer is not None and pointer["binding_revision"] == expected_revision, "revision_conflict")
        result = self._request("POST", "/releases", role="operator", expected=202, label=label,
            body={"policy": reference, "workload_ids": [workload_id], "stage": "canary", "state": state,
                "expected_revisions": {workload_id: expected_revision}, "reason": "Phase3 governed evaluator acceptance"})
        require(result["tenant_id"] == self.tenant and result["policy"] == reference
                and result["state"] == state and result["stage"] == "canary"
                and result["workload_ids"] == [workload_id]
                and result["expected_revisions"] == {workload_id: expected_revision})
        parsed = models.Release.model_validate_json(models.canonical_json_bytes(result))
        require(parsed.actor_id == self.actors["operator"])
        release_id = str(UUID(result["release_id"]))
        # Retain the exact acknowledged mutation before polling. A caller may
        # reconcile only this owned release after a later observation failure.
        self.last_release = {"operation": str(self.operation), "label": label,
            "release_id": release_id, "tenant_id": self.tenant, "workload_id": workload_id,
            "policy": copy.deepcopy(reference), "binding_revision": expected_revision + 1,
            "previous_revision": expected_revision, "state": state, "actor_id": parsed.actor_id}
        if self.journal is not None:
            self.journal.acknowledged_release(self.last_release)
        deadline = self.clock() + seconds
        while True:
            require(self.clock() < deadline, "snapshot_stale")
            observed = self._request("GET", "/releases/" + release_id, role="operator")
            release = models.Release.model_validate_json(models.canonical_json_bytes(observed["release"])).model_dump(mode="json")
            require(release["release_id"] == release_id and release["policy"] == reference
                    and release["expected_revisions"] == {workload_id: expected_revision}
                    and release["tenant_id"] == self.tenant and release["state"] == state
                    and release["stage"] == "canary" and release["workload_ids"] == [workload_id]
                    and release["actor_id"] == self.actors["operator"])
            require(release["status"] != "failed", "policy_unresolved")
            require(type(observed["receipts"]) is list and len(observed["receipts"]) <= 4096)
            latest = {}
            for raw in observed["receipts"]:
                receipt = models.RuntimeReceipt.model_validate_json(models.canonical_json_bytes(raw))
                require(receipt.tenant_id == self.tenant and receipt.workload_id == workload_id)
                if receipt.binding_revision != expected_revision + 1:
                    continue
                require(receipt.runtime_id in runtime_ids and receipt.digest == reference["digest"])
                previous = latest.get(receipt.runtime_id)
                if previous is None or receipt.snapshot_generation > previous.snapshot_generation:
                    latest[receipt.runtime_id] = receipt
            if (release["status"] == "applied" and set(latest) == set(runtime_ids)
                    and all(item.health == "ready" for item in latest.values())):
                current = self._request("GET", "/workloads/" + workload_id, role="operator")
                require(current["desired_binding"]["binding_revision"] == expected_revision + 1
                        and current["applied_binding"]["binding_revision"] == expected_revision + 1
                        and current["applied_binding"]["policy"] == reference
                        and current["tenant_id"] == self.tenant and current["workload_id"] == workload_id)
                return {"release": release, "receipts": [latest[key].model_dump(mode="json") for key in sorted(latest)],
                        "binding_revision": expected_revision + 1}
            self.sleep(min(1.05, max(0, deadline - self.clock())))
