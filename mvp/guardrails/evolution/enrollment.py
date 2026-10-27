"""Operator-authenticated initial deployment enrollment, outside the public API.

Register RuntimeInstall and provision its runtime-only principal through the
existing management services first. Enroll before the workload's first release.
Identity and target associations are immutable: fleet changes require an explicit
rollout workflow, never a silent retarget or privilege inherited from platform.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import Field, ValidationError

from .errors import EvolutionError
from .models import DomainModel, RuntimeInstall, Slug, UUID, canonical_json_bytes, parse_time


class _EnrollmentRequest(DomainModel):
    runtime_id: UUID
    principal_id: UUID
    workload_ids: Annotated[tuple[Slug, ...], Field(min_length=1, max_length=100)]
    expected_runtime_revision: Annotated[int, Field(ge=1)]
    expected_target_revisions: dict[Slug, Annotated[int, Field(ge=0)]]


class RuntimeEnrollment:
    """One transaction commits enrollment, target CAS, audit and outbox."""

    def __init__(self, repository, identity_service, audit):
        self.repository, self.identity, self.audit = repository, identity_service, audit

    def enroll(self, operator_token: str, *, runtime_id: str, principal_id: str,
               workload_ids, expected_runtime_revision: int,
               expected_target_revisions: dict[str, int]) -> dict:
        actor = self.identity.authenticate(operator_token)
        self.identity.authorize(actor, {"operator"})
        if actor.tenant_id is None:
            raise EvolutionError("forbidden")
        try:
            request = _EnrollmentRequest.model_validate_json(canonical_json_bytes({
                "runtime_id": runtime_id, "principal_id": principal_id,
                "workload_ids": workload_ids, "expected_runtime_revision": expected_runtime_revision,
                "expected_target_revisions": expected_target_revisions,
            }))
        except (ValidationError, ValueError, TypeError):
            raise EvolutionError("validation_failed") from None
        targets = sorted(request.workload_ids)
        if len(targets) != len(set(targets)) or set(request.expected_target_revisions) != set(targets):
            raise EvolutionError("validation_failed")

        with self.repository.transaction(actor.tenant_id) as tx:
            # Hold current authorization rows while committing: previously issued
            # in-process identity objects never bypass revocation or role changes.
            tenant = tx.get("tenants", tx.tenant_id, required=True, for_update=True)
            operator = tx.get("principals", actor.principal_id, required=True, for_update=True)
            credential = tx.get("credentials", actor.credential["credential_id"], required=True, for_update=True)
            now = self.identity.clock()
            if (tenant["status"] != "active" or operator["status"] != "active"
                or "operator" not in operator["roles"] or credential["revoked_at"] is not None
                or parse_time(credential["expires_at"]) <= now
                or (credential["previous_valid_until"] is not None
                    and parse_time(credential["previous_valid_until"]) <= now)):
                raise EvolutionError("authentication_failed")
            tx.lock("runtime-enrollment:" + request.runtime_id)
            tx.lock("runtime-principal:" + request.principal_id)
            install = tx.get("runtimes", request.runtime_id, required=True, for_update=True)
            runtime_principal = tx.get("principals", request.principal_id, required=True, for_update=True)
            try:
                RuntimeInstall.model_validate_json(canonical_json_bytes(install))
            except (ValidationError, ValueError, TypeError):
                raise EvolutionError("validation_failed") from None
            if install["status"] != "registered" or runtime_principal["status"] != "active" or runtime_principal["roles"] != ["runtime"]:
                raise EvolutionError("forbidden")
            if install["revision"] != request.expected_runtime_revision:
                raise EvolutionError("revision_conflict")
            if tx.get("runtime_enrollments", request.runtime_id) or tx.get("runtime_principals", request.principal_id):
                raise EvolutionError("revision_conflict")
            # Tenant defaults are immutable version 1. Approved policy residency
            # remains a separate release/snapshot admission constraint.
            residency = tx.get("config:residency", f"{tenant['residency_id']}:1", required=True)
            if install["zone"] not in residency["allowed_zones"]:
                raise EvolutionError("residency_denied")
            target_rows = {}
            for workload_id in targets:
                tx.lock("workload:" + workload_id)
                workload = tx.get("workloads", workload_id, required=True, for_update=True)
                if workload["status"] != "active":
                    raise EvolutionError("forbidden")
                if workload["mode"] != install["mode"]:
                    raise EvolutionError("validation_failed")
                # Adding an expected receiver after activation would invalidate
                # existing applied state without a new release. Refuse it here.
                if tx.get("binding_pointers", workload_id):
                    raise EvolutionError("revision_conflict")
                previous = tx.get("runtime_targets", workload_id, for_update=True)
                revision = previous["revision"] if previous else 0
                if revision != request.expected_target_revisions[workload_id]:
                    raise EvolutionError("revision_conflict")
                if previous and request.runtime_id in previous["runtime_ids"]:
                    raise EvolutionError("revision_conflict")
                if previous and len(previous["runtime_ids"]) >= 100:
                    raise EvolutionError("quota_exceeded")
                target_rows[workload_id] = previous

            enrollment = tx.insert("runtime_enrollments", request.runtime_id, {
                "tenant_id": tx.tenant_id, "runtime_id": request.runtime_id,
                "principal_id": request.principal_id, "workload_ids": targets,
            }, immutable=True)
            tx.insert("runtime_principals", request.principal_id, {
                "tenant_id": tx.tenant_id, "principal_id": request.principal_id,
                "runtime_id": request.runtime_id,
            }, immutable=True)
            tx.link("runtime_enrollments", request.runtime_id, "runtimes", request.runtime_id)
            tx.link("runtime_enrollments", request.runtime_id, "principals", request.principal_id)
            tx.link("runtime_principals", request.principal_id, "runtime_enrollments", request.runtime_id)
            revisions = {}
            for workload_id, previous in target_rows.items():
                value = {"tenant_id": tx.tenant_id, "workload_id": workload_id,
                         "runtime_ids": sorted([*(previous["runtime_ids"] if previous else []), request.runtime_id])}
                updated = (tx.put("runtime_targets", workload_id, value, expected_revision=previous["revision"])
                           if previous else tx.insert("runtime_targets", workload_id, value))
                revisions[workload_id] = updated["revision"]
                tx.link("runtime_enrollments", request.runtime_id, "workloads", workload_id)
                tx.link("runtime_targets", workload_id, "runtime_enrollments", request.runtime_id)
            updated_install = tx.put("runtimes", request.runtime_id, install, expected_revision=request.expected_runtime_revision)
            self.audit.append(tx, "runtime_registered", actor_id=actor.principal_id, metadata={
                "operation": "enrollment", "runtime_id": request.runtime_id,
                "principal_id": request.principal_id, "revision": updated_install["revision"],
            })
            tx.append("outbox", {"tenant_id": tx.tenant_id, "type": "runtime_enrolled",
                                "runtime_id": request.runtime_id, "workload_ids": targets})
            return {"runtime": updated_install, "enrollment": enrollment, "target_revisions": revisions}
