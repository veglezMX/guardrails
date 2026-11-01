"""Authenticated v2 management transport over tenant-scoped application services."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from . import models
from .audit import AuditStore
from .bindings import BindingService
from .deadlines import current_deadline
from .errors import EvolutionError
from .evidence_trust import EvidenceTrust
from .governance import PolicyGovernance, principal, record, require_role
from .identity import SESSION_COOKIE, IdentityService

READ_ROLES = {"tenant_admin", "author", "approver", "operator", "auditor", "viewer"}
RESOURCES = {"lists": ("ListSet", "list_id"), "residency": ("ResidencyProfile", "residency_id"),
             "destinations": ("Destination", "destination_id"), "providers": ("ProviderProfile", "provider_id"),
             "tools": ("ToolSpec", "tool_id"), "quotas": ("Quota", "quota_id")}
SECRET_FIELDS = {"token_hash", "secret_hash", "csrf_hash", "credential_ref", "one_time_secret"}
CANARY_CASE_IDS = frozenset({"canary-policy-attribution", "canary-runtime-health", "canary-target-coverage"})


class ManagementDeadline:
    """Bound body reception and DB work by the same absolute request deadline."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/health/live":
            await self.app(scope, receive, send)
            return
        budget = 2.0 if scope.get("path", "").startswith("/v2/runtime/") else 5.0
        token = current_deadline.set(time.monotonic() + budget)
        started = False

        async def bounded_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        error = None
        try:
            async with asyncio.timeout(budget):
                await self.app(scope, receive, bounded_send)
        except TimeoutError:
            error = EvolutionError("upstream_timeout")
        except EvolutionError as exc:
            error = exc
        except SQLAlchemyError:
            # Driver errors can contain SQL, credentials or submitted values.
            error = EvolutionError("policy_unresolved")
        finally:
            current_deadline.reset(token)
        if error is not None:
            if started:
                # Never append a second response or advertise a successful body.
                raise RuntimeError("response_deadline_exceeded") from None
            correlation = str(uuid4())
            for name, value in scope.get("headers", ()):
                if name.lower() == b"x-request-id":
                    try:
                        correlation = str(UUID(value.decode("ascii")))
                    except (ValueError, UnicodeDecodeError):
                        pass
            response = JSONResponse(error.envelope(correlation), status_code=error.status,
                                    headers={"Cache-Control": "no-store", "X-Request-ID": correlation})
            await response(scope, receive, send)


def safe_view(value):
    if isinstance(value, dict):
        return {k: safe_view(v) for k, v in value.items() if k not in SECRET_FIELDS}
    if isinstance(value, (tuple, list)):
        return [safe_view(v) for v in value]
    return value


def one_time_response(parts, method):
    return method == "POST" and ((len(parts) == 3 and parts[0] == "principals" and parts[2] == "credentials") or (len(parts) == 3 and parts[0] == "credentials" and parts[2] == "rotate"))


def response_headers(correlation, tenant, parts, method, result, status):
    headers = {"Cache-Control": "no-store", "X-Request-ID": correlation}
    if method == "POST" and status == 201 and parts and parts[0] in RESOURCES:
        _, id_name = RESOURCES[parts[0]]
        headers["Location"] = f"/v2/tenants/{tenant}/{parts[0]}/{result[id_name]}/versions/{result['version']}"
    return headers


def shape(value, required, optional=()):
    if not isinstance(value, dict) or not set(required).issubset(value) or set(value) - set(required) - set(optional):
        raise EvolutionError("validation_failed")
    return value


def now():
    return datetime.now(UTC)


def timestamp(value):
    return value.isoformat().replace("+00:00", "Z")


def parse_model(name, data):
    return record(getattr(models, name).model_validate_json(models.canonical_json_bytes(data)))


class ManagementApi:
    def __init__(self, repository, *, identity=None, audit=None, governance=None, bindings=None, cursor_key=None, evidence_keys=None, registry=None):
        self.repository = repository
        self.identity = identity or IdentityService(repository)
        self.audit = audit or AuditStore(repository)
        self.governance = governance or PolicyGovernance(repository, self.audit)
        from .plugins import EvaluatorRegistry
        self.registry = registry or EvaluatorRegistry(workers=1)
        self.bindings = bindings or BindingService(repository, self.governance, self.audit, capability_check=self.registry.activation_check)
        self.cursor_key = cursor_key or secrets.token_bytes(32)
        self.evidence_keys = evidence_keys or {}
        self.evidence_trust = EvidenceTrust(repository, identity=self.identity, audit=self.audit)

    def _cursor(self, tenant, kind, filters, last):
        payload = models.canonical_json_bytes({"tenant": tenant, "kind": kind, "filters": filters, "last": last, "expires": int(now().timestamp()) + 900})
        return base64.urlsafe_b64encode(payload + hmac.digest(self.cursor_key, payload, "sha256")).decode().rstrip("=")

    def _uncursor(self, cursor, tenant, kind, filters):
        if not cursor:
            return None
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 8192 or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
                raise ValueError
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(raw).decode().rstrip("=") != cursor:
                raise ValueError
            payload, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(signature, hmac.digest(self.cursor_key, payload, "sha256")):
                raise ValueError
            data = models.strict_json_loads(payload)
            if data["tenant"] != tenant or data["kind"] != kind or data["filters"] != filters or data["expires"] <= now().timestamp():
                raise ValueError
            return tuple(data["last"])
        except (ValueError, KeyError, TypeError):
            raise EvolutionError("invalid_cursor") from None

    def page(self, tx, kind, query, *, filters=None):
        filters = filters or {}
        if set(query) - {"cursor", "limit"}:
            raise EvolutionError("validation_failed")
        try:
            limit = int(query.get("limit", "50"))
        except ValueError:
            raise EvolutionError("validation_failed") from None
        if not 1 <= limit <= 100:
            raise EvolutionError("validation_failed")
        after = self._uncursor(query.get("cursor"), tx.tenant_id, kind, filters)
        rows = tx.page(kind, limit=limit + 1, after=after, filters=filters)
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = self._cursor(tx.tenant_id, kind, filters, [rows[-1]["created_at"], rows[-1]["key"]]) if more else None
        return {"items": [safe_view(row["data"]) for row in rows], "next_cursor": next_cursor}

    def rate_limit(self, tx, actor):
        seconds = int(now().timestamp())
        key = actor["principal_id"]
        tx.lock("management-rate:" + key)
        old = tx.get("management_rate", key)
        minute = old["minute_count"] if old and old["minute"] == seconds // 60 else 0
        burst = old["second_count"] if old and old["second"] == seconds else 0
        if minute >= 60 or burst >= 10:
            raise EvolutionError("quota_exceeded")
        value = {"minute": seconds // 60, "second": seconds, "minute_count": minute + 1, "second_count": burst + 1}
        if old:
            tx.put("management_rate", key, value, expected_revision=old["revision"])
        else:
            tx.insert("management_rate", key, value)

    def dispatch(self, tx, identity, method, parts, body, query, revision):
        tenant = tx.tenant_id
        actor = principal(identity)
        if not parts:
            raise EvolutionError("resource_not_found")
        family = parts[0]
        if family in RESOURCES:
            model_name, id_name = RESOURCES[family]
            if method == "GET":
                require_role(identity, READ_ROLES | ({"platform_admin"} if tenant == "_platform" else set()), tenant)
                if len(parts) == 1:
                    return self.page(tx, "config:" + family, query), 200
                if len(parts) == 4 and parts[2] == "versions":
                    return safe_view(tx.get("config:" + family, f"{parts[1]}:{int(parts[3])}", required=True)), 200
            if method == "POST" and (len(parts) == 1 or (len(parts) == 3 and parts[2] == "versions")):
                roles = {"platform_admin"} if tenant == "_platform" else (
                    {"author"} if family in ("lists", "tools") else {"tenant_admin"})
                require_role(identity, roles, tenant)
                if query or set(body) & {id_name, "tenant_id", "version", "digest", "created_at", "revision"}:
                    raise EvolutionError("validation_failed")
                resource_id = str(uuid4()) if len(parts) == 1 else parts[1]
                if len(parts) == 3:
                    try:
                        if str(UUID(resource_id)) != resource_id:
                            raise ValueError
                    except ValueError:
                        raise EvolutionError("resource_not_found") from None
                    # Version one establishes this tenant's resource identity.
                    # An unknown or foreign ID must not implicitly create it.
                    tx.get("config:" + family, resource_id + ":1", required=True)
                version = tx.next_version("config:" + family, resource_id)
                data = {**body, id_name: resource_id, "tenant_id": tenant, "version": version, "digest": "0" * 64, "created_at": timestamp(now())}
                model = getattr(models, model_name).model_validate_json(models.canonical_json_bytes(data))
                data = record(models.with_resource_digest(model))
                tx.insert("config:" + family, f"{resource_id}:{version}", data, immutable=True)
                self.audit.append(tx, "configuration_created", actor_id=actor["principal_id"], metadata={"resource_type": family, "resource_id": resource_id, "revision": version})
                return safe_view(data), 201
        if family == "principals" and method == "POST":
            require_role(identity, {"tenant_admin", "platform_admin"}, tenant)
            if len(parts) == 1:
                shape(body, {"display_name", "roles"})
                if "platform_admin" in body["roles"]:
                    raise EvolutionError("forbidden")
                model = models.Principal.model_validate({**body, "principal_id": str(uuid4()), "tenant_id": tenant})
                result = self.identity.provision_principal(tx, model.display_name, model.roles)
                self.audit.append(tx, "principal_created", actor_id=actor["principal_id"])
                return result, 201
            if len(parts) == 3 and parts[2] == "credentials":
                shape(body, {"expires_at"}, {"workload_id"})
                result = self.identity.issue_credential(tx, parts[1], **body)
                self.audit.append(tx, "credential_issued", actor_id=actor["principal_id"])
                return result, 201
        if family == "credentials" and method == "POST" and len(parts) == 3:
            require_role(identity, {"tenant_admin", "operator"} if parts[2] == "rotate" else {"tenant_admin"}, tenant)
            credential = tx.get("credentials", parts[1], required=True)
            if "tenant_admin" not in actor["roles"] and credential["principal_id"] != actor["principal_id"]:
                raise EvolutionError("forbidden")
            if parts[2] == "rotate":
                shape(body, {"overlap_seconds"})
                result = self.identity.rotate_credential(tx, parts[1], **body)
            elif parts[2] == "revoke":
                shape(body, {"reason"})
                if not isinstance(body["reason"], str) or not 1 <= len(body["reason"]) <= 500:
                    raise EvolutionError("validation_failed")
                result = self.identity.revoke_credential(tx, parts[1])
            else:
                raise EvolutionError("resource_not_found")
            self.audit.append(tx, "credential_" + parts[2], actor_id=actor["principal_id"])
            return result, 200
        if family == "workloads":
            if method == "POST" and len(parts) == 1:
                require_role(identity, {"tenant_admin", "platform_admin"}, tenant)
                shape(body, {"workload_id", "display_name", "mode", "allowed_stages", "provider_ids", "quota_id", "required_state"})
                value = parse_model("Workload", {**body, "tenant_id": tenant, "status": "active", "retention_enabled": False, "revision": 1, "created_at": timestamp(now())})
                quotas = tx.page("config:quotas", limit=1, filters={"quota_id": value["quota_id"]})
                if not quotas:
                    raise EvolutionError("resource_not_found")
                for provider_id in value["provider_ids"]:
                    if not tx.page("config:providers", limit=1, filters={"provider_id": provider_id}):
                        raise EvolutionError("resource_not_found")
                tx.insert("workloads", value["workload_id"], value)
                self.audit.append(tx, "workload_created", actor_id=actor["principal_id"], workload_id=value["workload_id"])
                return value, 201
            if method == "GET":
                require_role(identity, READ_ROLES, tenant)
                if len(parts) == 1:
                    return self.page(tx, "workloads", query), 200
                value = tx.get("workloads", parts[1], required=True)
                if len(parts) == 2:
                    return {**value, "desired_binding": tx.get("binding_pointers", parts[1]), "applied_binding": tx.get("applied_bindings", parts[1])}, 200
                if parts[2:] == ["bindings"]:
                    return self.page(tx, "bindings", query, filters={"workload_id": parts[1]}), 200
                if parts[2:] == ["manifest"]:
                    require_role(identity, {"operator"}, tenant)
                    manifest = tx.get("manifests", parts[1], required=True)
                    return safe_view(manifest), 200
            if method == "PATCH" and len(parts) == 2:
                require_role(identity, {"tenant_admin", "platform_admin"}, tenant)
                shape(body, (), {"display_name", "status"})
                if not body:
                    raise EvolutionError("validation_failed")
                value = tx.get("workloads", parts[1], required=True)
                value = parse_model("Workload", {**value, **body, "revision": revision + 1})
                value = tx.put("workloads", parts[1], value, expected_revision=revision)
                self.audit.append(tx, "workload_updated", actor_id=actor["principal_id"], workload_id=parts[1])
                return value, 200
            if method == "POST" and parts[2:] == ["rollback"]:
                shape(body, {"target_revision", "expected_revision", "reason"})
                return self.bindings.rollback(tx, identity, parts[1], **body), 202
        if family == "policies":
            if method == "POST" and parts[1:] == ["validate"]:
                policy_id = body.get("policy_id")
                payload = {k: v for k, v in body.items() if k != "policy_id"}
                candidate = self.governance.validate(tx, identity, policy_id, payload)
                return {"valid": True, "compiled_digest": candidate.compiled_digest, "resolved_refs": record(candidate)["parents"], "diagnostics": []}, 200
            if method == "POST" and len(parts) == 3 and parts[2] == "versions":
                return self.governance.submit(tx, identity, parts[1], body), 201
            if method == "GET":
                require_role(identity, READ_ROLES | ({"platform_admin"} if tenant == "_platform" else set()), tenant)
                if len(parts) == 1:
                    if "status" in query and query["status"] not in ("candidate", "approved", "rejected", "revoked"):
                        raise EvolutionError("validation_failed")
                    return self.policy_page(tx, query), 200
                if len(parts) == 4 and parts[2] == "versions":
                    policy = self.governance.get(tx, parts[1], int(parts[3]))
                    approvals = [row["data"] for row in tx.page("approvals", limit=100, filters={"compiled_digest": policy["compiled_digest"]})]
                    return safe_view({"policy": policy, "approvals": approvals}), 200
                if len(parts) == 3 and parts[2] == "diff":
                    require_role(identity, {"author", "approver", "operator"} | ({"platform_admin"} if tenant == "_platform" else set()), tenant)
                    old = self.governance.get(tx, parts[1], int(query["from_version"]))
                    new = self.governance.get(tx, parts[1], int(query["to_version"]))
                    return self.governance.compiler(tx).diff(models.PolicyVersion.model_validate_json(models.canonical_json_bytes(old)), models.PolicyVersion.model_validate_json(models.canonical_json_bytes(new))), 200
            if method == "POST" and len(parts) == 5 and parts[2] == "versions" and parts[4] in ("reviews", "revoke"):
                if parts[4] == "revoke":
                    shape(body, {"compiled_digest", "reason"})
                    body = {**body, "outcome": "revoke"}
                else:
                    shape(body, {"compiled_digest", "reason", "outcome"}, {"evidence_ids"})
                    if body.get("outcome") not in ("approve", "reject"):
                        raise EvolutionError("validation_failed")
                return self.governance.review(tx, identity, parts[1], int(parts[3]), **body), 200
        if family == "releases":
            if method == "POST" and len(parts) == 1:
                shape(body, {"policy", "workload_ids", "stage", "state", "expected_revisions", "reason"}, {"canary_evidence_id"})
                return self.bindings.release(tx, identity, **body), 202
            if method == "GET" and len(parts) == 2:
                require_role(identity, READ_ROLES, tenant)
                release = tx.get("releases", parts[1], required=True)
                receipts = []
                for workload_id in release["workload_ids"]:
                    after = None
                    while True:
                        batch = tx.page("receipts", limit=100, after=after, filters={"workload_id": workload_id, "binding_revision": release["expected_revisions"][workload_id] + 1})
                        receipts.extend(row["data"] for row in batch)
                        if len(batch) < 100:
                            break
                        after = (batch[-1]["created_at"], batch[-1]["key"])
                return {"release": release, "receipts": receipts}, 200
        if family == "audit" and method == "GET" and len(parts) == 1:
            require_role(identity, {"auditor"}, tenant)
            filters = {key: query[key] for key in ("workload_id", "action") if key in query}
            if set(query) - {"cursor", "limit", "workload_id", "action", "from", "until"}:
                raise EvolutionError("validation_failed")
            if "action" in filters and filters["action"] not in ("allow", "warn", "redact", "block"):
                raise EvolutionError("validation_failed")
            if ("from" in query) != ("until" in query):
                raise EvolutionError("validation_failed")
            if "from" in query:
                start, finish = models.parse_time(query["from"]), models.parse_time(query["until"])
                if start.tzinfo is None or finish.tzinfo is None or finish < start or finish - start > timedelta(days=7):
                    raise EvolutionError("validation_failed")
                filters.update({"occurred_from": query["from"], "occurred_until": query["until"]})
            return self.audit_page(tx, query, filters), 200
        if family == "capabilities" and method == "GET":
            require_role(identity, READ_ROLES, tenant)
            return {"items": [item.model_dump(mode="json") for item in self.registry.declared_capabilities()]}, 200
        if family == "runtimes" and method == "POST" and len(parts) == 1:
            require_role(identity, {"operator"}, tenant)
            shape(body, {"zone", "mode", "telemetry", "version", "offline_max_age_s"})
            runtime_id = str(uuid4())
            value = parse_model("RuntimeInstall", {**body, "runtime_id": runtime_id, "tenant_id": tenant, "public_key_id": "unprovisioned", "status": "registered", "last_seen_at": None, "revision": 1, "created_at": timestamp(now())})
            tx.insert("runtimes", runtime_id, value)
            self.audit.append(tx, "runtime_registered", actor_id=actor["principal_id"])
            return value, 201
        if family == "evidence":
            if method == "GET" and len(parts) == 2:
                require_role(identity, READ_ROLES, tenant)
                return tx.get("evidence", parts[1], required=True), 200
            if method == "POST" and len(parts) == 1:
                require_role(identity, {"operator"}, tenant)
                report = parse_model("EvidenceReport", body)
                key = self.evidence_keys.get(report["key_id"]) or self.evidence_trust.resolve(report["key_id"], tx=tx)
                try:
                    key.verify(base64.b64decode(report["signature"], validate=True), models.canonical_json_bytes({k: v for k, v in report.items() if k != "signature"}))
                except Exception:  # noqa: BLE001 - untrusted evidence must produce a safe finite error
                    raise EvolutionError("validation_failed") from None
                if report["profile"] == "canary":
                    self.validate_canary_evidence(tx, report)
                tx.insert("evidence", report["run_id"], report, immutable=True)
                if report["profile"] == "canary":
                    tx.insert("canary_evidence", report["run_id"], {"release_id": report["environment"]["release_id"], "policy_digest": report["environment"]["policy_digest"], "status": "pass", "created_at": timestamp(now())}, immutable=True)
                self.audit.append(tx, "evidence_received", actor_id=actor["principal_id"])
                return {"run_id": report["run_id"], "status": report["status"]}, 201
        raise EvolutionError("resource_not_found")

    def validate_canary_evidence(self, tx, report):
        environment = report["environment"]
        if not {"tenant_id", "release_id", "policy_digest"}.issubset(environment) or environment["tenant_id"] != tx.tenant_id:
            raise EvolutionError("resource_not_found")
        if report["status"] != "pass" or not CANARY_CASE_IDS <= {case["case_id"] for case in report["cases"] if case["status"] == "pass" and case["assertions"] > 0}:
            raise EvolutionError("approval_required")
        if models.parse_time(report["finished_at"]) > now() or now() - models.parse_time(report["finished_at"]) > timedelta(seconds=60):
            raise EvolutionError("snapshot_stale")
        release = tx.get("releases", environment["release_id"], required=True)
        if release["stage"] != "canary" or release["status"] != "applied" or release["policy"]["digest"] != environment["policy_digest"]:
            raise EvolutionError("approval_required")
        self.governance.eligible(tx, release["policy"])
        for workload_id in release["workload_ids"]:
            expected = release["expected_revisions"][workload_id] + 1
            applied = tx.get("applied_bindings", workload_id, required=True)
            desired = tx.get("binding_pointers", workload_id, required=True)
            if applied["binding_revision"] != expected or desired["binding_revision"] != expected:
                raise EvolutionError("revision_conflict")
            targets = tx.get("runtime_targets", workload_id, required=True)["runtime_ids"]
            if not targets:
                raise EvolutionError("approval_required")
            latest, after = {}, None
            while True:
                batch = tx.page("receipts", limit=100, after=after, filters={"workload_id": workload_id, "binding_revision": expected})
                for row in batch:
                    receipt = row["data"]
                    previous = latest.get(receipt["runtime_id"])
                    if previous is None or receipt["snapshot_generation"] > previous["snapshot_generation"]:
                        latest[receipt["runtime_id"]] = receipt
                if len(batch) < 100:
                    break
                after = (batch[-1]["created_at"], batch[-1]["key"])
            for runtime_id in targets:
                receipt = latest.get(runtime_id)
                if receipt is None or receipt["health"] != "ready" or receipt["digest"] != environment["policy_digest"]:
                    raise EvolutionError("approval_required")
                if not self.bindings.target_ready(tx, runtime_id, receipt):
                    raise EvolutionError("snapshot_stale")
                issued = tx.get("issued_snapshots", f"{runtime_id}:{receipt['snapshot_generation']}", required=True)
                if models.parse_time(issued["expires_at"]) <= now():
                    raise EvolutionError("snapshot_stale")

    def create_tenant(self, identity, body, *, tx=None):
        actor = principal(identity)
        if "platform_admin" not in actor["roles"] or actor["tenant_id"] is not None:
            raise EvolutionError("forbidden")
        shape(body, {"tenant_id", "display_name"})
        created = timestamp(now())
        tenant_id = body["tenant_id"]
        quota = models.Quota.model_validate_json(models.canonical_json_bytes({"quota_id": str(uuid4()), "tenant_id": tenant_id, "version": 1, "digest": "0" * 64, "workload_id": None, "created_at": created}))
        quota = record(models.with_resource_digest(quota))
        residency = models.ResidencyProfile.model_validate_json(models.canonical_json_bytes({"residency_id": str(uuid4()), "tenant_id": tenant_id, "version": 1, "allowed_zones": ["local-shared"], "destination_lists": [], "provider_lists": [], "tool_lists": [], "content_lists": [], "digest": "0" * 64, "created_at": created}))
        residency = record(models.with_resource_digest(residency))
        tenant = parse_model("Tenant", {**body, "status": "active", "baseline": None, "residency_id": residency["residency_id"], "quota_id": quota["quota_id"], "revision": 1, "created_at": created})
        self.repository.provision_tenant(tenant, residency, quota, tx=tx)
        if tx is not None:
            self.audit.append(tx, "tenant_created", actor_id=actor["principal_id"], metadata={"resource_type": "tenant", "resource_id": tenant_id})
        return tenant

    def tenant_page(self, identity, query):
        actor = principal(identity)
        if actor.get("tenant_id"):
            require_role(identity, READ_ROLES, actor["tenant_id"])
            with self.repository.transaction(actor["tenant_id"]) as tx:
                return {"items": [tx.get("tenants", actor["tenant_id"], required=True)], "next_cursor": None}
        require_role(identity, {"platform_admin"}, "_platform")
        with self.repository.transaction("_platform") as tx:
            directory = self.page(tx, "tenant_directory", query)
        records = []
        for entry in directory["items"]:
            with self.repository.transaction(entry["target_tenant_id"]) as tx:
                records.append(tx.get("tenants", entry["target_tenant_id"], required=True))
        return {"items": records, "next_cursor": directory["next_cursor"]}

    def audit_page(self, tx, query, filters):
        limit_text = query.get("limit", "50")
        if not limit_text.isdecimal() or not 1 <= int(limit_text) <= 100:
            raise EvolutionError("validation_failed")
        limit = int(limit_text)
        after = self._uncursor(query.get("cursor"), tx.tenant_id, "audit", filters)
        equality = {key: value for key, value in filters.items() if key in {"workload_id", "action"}}
        selected = []
        while len(selected) <= limit:
            batch = tx.page("audit", limit=100, after=after, filters=equality)
            for row in batch:
                occurred = row["data"]["occurred_at"]
                if "occurred_from" not in filters or models.parse_time(filters["occurred_from"]) <= models.parse_time(occurred) <= models.parse_time(filters["occurred_until"]):
                    selected.append(row)
                    if len(selected) > limit:
                        break
            if len(batch) < 100 or len(selected) > limit:
                break
            after = (batch[-1]["created_at"], batch[-1]["key"])
        more = len(selected) > limit
        rows = selected[:limit]
        cursor = self._cursor(tx.tenant_id, "audit", filters, [rows[-1]["created_at"], rows[-1]["key"]]) if more else None
        return {"items": [safe_view(row["data"]) for row in rows], "next_cursor": cursor}

    def policy_page(self, tx, query):
        if set(query) - {"cursor", "limit", "status"}:
            raise EvolutionError("validation_failed")
        limit = int(query.get("limit", "50"))
        if not 1 <= limit <= 100:
            raise EvolutionError("validation_failed")
        filters = {"status": query["status"]} if "status" in query else {}
        cursor = self._uncursor(query.get("cursor"), tx.tenant_id, "policy_summaries", filters)
        latest, after = {}, None
        while True:
            batch = tx.page("policies", limit=100, after=after)
            for row in batch:
                value = row["data"]
                previous = latest.get(value["policy_id"])
                if previous is None or previous["data"]["version"] < value["version"]:
                    latest[value["policy_id"]] = row
            if len(batch) < 100:
                break
            after = (batch[-1]["created_at"], batch[-1]["key"])
        rows = []
        for row in sorted(latest.values(), key=lambda item: (item["created_at"], item["key"])):
            if cursor is not None and (row["created_at"], row["key"]) <= cursor:
                continue
            value = row["data"]
            status = self.governance.status(tx, value)
            if filters and status != filters["status"]:
                continue
            rows.append({**row, "data": {"policy_id": value["policy_id"], "version": value["version"], "status": status}})
            if len(rows) > limit:
                break
        more, rows = len(rows) > limit, rows[:limit]
        next_cursor = self._cursor(tx.tenant_id, "policy_summaries", filters, [rows[-1]["created_at"], rows[-1]["key"]]) if more else None
        return {"items": [row["data"] for row in rows], "next_cursor": next_cursor}


def create_app(repository=None, *, service=None, distributor=None, **kwargs):
    config = None
    if service is None:
        if repository is None:
            from .config import load_config
            from .storage import TenantRepository
            config = load_config("service.json")
            from pathlib import Path
            repository = TenantRepository(Path(config.database_url_file).read_text().strip())
        if config is not None and "registry" not in kwargs:
            from .plugins import EvaluatorRegistry
            endpoints = {}
            if config.evaluator_config_file is not None:
                from .evaluator_config import load_evaluator_endpoints
                endpoints = load_evaluator_endpoints(config.evaluator_config_file,
                    runtime_zone=config.zone, external_processing=config.external_processing)
            kwargs["registry"] = EvaluatorRegistry(workers=1, zone=config.zone, **endpoints)
        service = ManagementApi(repository, **kwargs)
    app = FastAPI(title="Guardrails evolution management", version="2.0.0")
    app.add_middleware(ManagementDeadline)
    app.state.service = service

    @app.get("/health/live")
    async def live():
        return {"status": "live"}

    @app.post("/admin/session")
    async def login(request: Request):
        correlation = str(uuid4())
        rate_key = None
        try:
            expected_origin = str(request.base_url).rstrip("/")
            if request.url.scheme != "https" or request.headers.get("origin") != expected_origin:
                raise EvolutionError("forbidden")
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 4096:
                    raise EvolutionError("payload_too_large")
            content_type = request.headers.get("content-type", "").split(";", 1)[0]
            if content_type == "application/json":
                form = models.strict_json_loads(body)
                shape(form, {"credential"})
            elif content_type == "application/x-www-form-urlencoded":
                parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
                if set(parsed) != {"credential"} or len(parsed["credential"]) != 1:
                    raise EvolutionError("validation_failed")
                form = {"credential": parsed["credential"][0]}
            else:
                raise EvolutionError("invalid_json")
            token = form["credential"]
            if not isinstance(token, str) or len(token) > 256:
                raise EvolutionError("authentication_failed")
            pieces = token.split(".")
            handle = ".".join(pieces[:3]) if len(pieces) == 4 else "invalid"
            source = request.client.host if request.client else "local"
            rate_key = hmac.digest(service.cursor_key, (source + ":" + handle).encode(), "sha256").hex()
            minute = int(now().timestamp()) // 60
            with service.repository.transaction("_platform") as tx:
                tx.lock("login:" + rate_key)
                old = tx.get("login_rate", rate_key)
                attempts = old["count"] if old and old["minute"] == minute else 0
                if attempts >= 5:
                    raise EvolutionError("quota_exceeded")
                data = {"minute": minute, "count": attempts + 1}
                if old:
                    tx.put("login_rate", rate_key, data, expected_revision=old["revision"])
                else:
                    tx.insert("login_rate", rate_key, data)
            session = service.identity.create_session(token)
            identity = service.identity.authenticate(token)
            with service.repository.transaction(identity.scope) as tx:
                service.audit.append(tx, "session_created", actor_id=identity.principal_id, metadata={"resource_id": session["session_id"]})
            with service.repository.transaction("_platform") as tx:
                tx.lock("login:" + rate_key)
                attempts = tx.get("login_rate", rate_key, required=True)
                tx.put("login_rate", rate_key, {**attempts, "count": max(0, attempts["count"] - 1)}, expected_revision=attempts["revision"])
            response = JSONResponse({"session_id": session["session_id"], "csrf_token": session["csrf_token"], "expires_at": session["expires_at"]}, status_code=201, headers={"Cache-Control": "no-store", "X-Request-ID": correlation})
            response.set_cookie(session["cookie_name"], session["session_secret"], **session["cookie_options"])
            return response
        except (ValueError, TypeError, UnicodeDecodeError):
            error = EvolutionError("validation_failed")
        except EvolutionError as exc:
            error = exc
        headers = {"Cache-Control": "no-store", "X-Request-ID": correlation}
        if error.status == 429:
            headers["Retry-After"] = "60"
        return JSONResponse(error.envelope(correlation), status_code=error.status, headers=headers)

    @app.delete("/admin/session")
    async def logout(request: Request):
        correlation = str(uuid4())
        try:
            secret = request.cookies.get(SESSION_COOKIE, "")
            identity = service.identity.authenticate_session(secret, csrf_token=request.headers.get("x-csrf-token"), mutation=True,
                origin=request.headers.get("origin"), expected_origin=str(request.base_url).rstrip("/"))
            service.identity.revoke_session(secret)
            with service.repository.transaction(identity.scope) as tx:
                service.audit.append(tx, "session_revoked", actor_id=identity.principal_id, metadata={"resource_id": identity.session_id})
            response = JSONResponse({"status": "revoked"}, headers={"Cache-Control": "no-store", "X-Request-ID": correlation})
            response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="strict", path="/")
            return response
        except EvolutionError as error:
            return JSONResponse(error.envelope(correlation), status_code=error.status, headers={"Cache-Control": "no-store", "X-Request-ID": correlation})

    async def handle(request: Request):
        correlation = str(uuid4())
        try:
            if request.headers.get("x-request-id"):
                correlation = str(UUID(request.headers["x-request-id"]))
            authorization = request.headers.get("authorization", "")
            if not authorization.startswith("Bearer "):
                raise EvolutionError("authentication_failed")
            identity = service.identity.authenticate(authorization[7:])
            actor = principal(identity)
            if request.url.scheme != "https":
                raise EvolutionError("forbidden")
            segments = [part for part in request.url.path.split("/") if part]
            mutation = request.method != "GET"
            key = None
            if mutation:
                if "idempotency-key" not in request.headers:
                    raise EvolutionError("precondition_required")
                key = str(UUID(request.headers["idempotency-key"]))
                if request.headers.get("content-type", "").split(";")[0] != "application/json":
                    raise EvolutionError("invalid_json")
            revision = None
            if request.method == "PATCH":
                if "if-match" not in request.headers:
                    raise EvolutionError("precondition_required")
                revision = int(request.headers["if-match"])
                if revision < 1:
                    raise EvolutionError("validation_failed")
            tenant_id = segments[2] if len(segments) >= 3 and segments[:2] == ["v2", "tenants"] else actor.get("tenant_id") or "_platform"
            if tenant_id != (actor.get("tenant_id") or "_platform"):
                support = actor.get("tenant_id") is None and "platform_admin" in actor["roles"] and request.method in {"POST", "PATCH"} and len(segments) >= 4 and (segments[3] in {"principals", "workloads"} or (segments[3] == "policies" and segments[-1] == "revoke"))
                if not support:
                    raise EvolutionError("resource_not_found")
            # Count authenticated attempts even when later parsing or validation fails.
            with service.repository.transaction(tenant_id) as tx:
                service.rate_limit(tx, actor)
            # Identity and path scope are checked before reading policy/content bytes.
            raw = bytearray()
            if mutation:
                if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise EvolutionError("invalid_json")
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > 1024 * 1024:
                        raise EvolutionError("payload_too_large")
                try:
                    body = models.strict_json_loads(bytes(raw))
                except (ValueError, TypeError):
                    raise EvolutionError("invalid_json") from None
                if not isinstance(body, dict):
                    raise EvolutionError("validation_failed")
            else:
                body = {}
            if segments == ["v2", "tenants"]:
                if request.method == "POST":
                    require_role(identity, {"platform_admin"}, "_platform")
                    fingerprint = hashlib.sha256(models.canonical_json_bytes(body)).hexdigest()
                    idem = f"{actor['principal_id']}:POST:/v2/tenants:{key}"
                    with service.repository.transaction("_platform") as tx:
                        tx.lock("idempotency:" + idem)
                        previous = tx.get("idempotency", idem)
                        if previous and models.parse_time(previous["expires_at"]) > now():
                            if previous["fingerprint"] != fingerprint:
                                raise EvolutionError("idempotency_conflict")
                            result, status = previous["response"], previous["status"]
                        else:
                            result, status = service.create_tenant(identity, body, tx=tx), 201
                            receipt = {"fingerprint": fingerprint, "response": result, "status": status, "expires_at": timestamp(now() + timedelta(hours=24))}
                            if previous:
                                tx.put("idempotency", idem, receipt, expected_revision=previous["revision"])
                            else:
                                tx.insert("idempotency", idem, receipt)
                elif request.method == "GET":
                    result, status = service.tenant_page(identity, request.query_params), 200
                else:
                    raise EvolutionError("forbidden")
                return JSONResponse(safe_view(result), status_code=status, headers={"Cache-Control": "no-store", "X-Request-ID": correlation})
            if segments[:2] != ["v2", "tenants"] or len(segments) < 4:
                raise EvolutionError("resource_not_found")
            with service.repository.transaction(tenant_id) as tx:
                tenant = tx.get("tenants", tenant_id, required=True)
                if mutation and tenant["status"] != "active":
                    raise EvolutionError("forbidden")
                fingerprint = hashlib.sha256(models.canonical_json_bytes(body)).hexdigest()
                idem = f"{actor['principal_id']}:{request.method}:{request.url.path}:{key}"
                if mutation:
                    tx.lock("idempotency:" + idem)
                    previous = tx.get("idempotency", idem)
                    if previous and previous["expires_at"] > timestamp(now()):
                        if previous["fingerprint"] != fingerprint:
                            raise EvolutionError("idempotency_conflict")
                        if previous.get("secret_delivered"):
                            raise EvolutionError("idempotency_conflict")
                        return JSONResponse(previous["response"], status_code=previous["status"], headers=response_headers(correlation, tenant_id, segments[3:], request.method, previous["response"], previous["status"]))
                result, status = service.dispatch(tx, identity, request.method, segments[3:], body, request.query_params, revision)
                if mutation:
                    value = {"fingerprint": fingerprint, "response": safe_view(result), "status": status, "secret_delivered": one_time_response(segments[3:], request.method), "expires_at": timestamp(now() + timedelta(hours=24))}
                    if previous:
                        tx.put("idempotency", idem, value, expected_revision=previous["revision"])
                    else:
                        tx.insert("idempotency", idem, value)
            response = dict(result) if one_time_response(segments[3:], request.method) else safe_view(result)
            return JSONResponse(response, status_code=status, headers=response_headers(correlation, tenant_id, segments[3:], request.method, result, status))
        except EvolutionError as exc:
            headers = {"Cache-Control": "no-store", "X-Request-ID": correlation}
            if exc.status == 429:
                headers["Retry-After"] = "1"
            return JSONResponse(exc.envelope(correlation), status_code=exc.status, headers=headers)
        except (ValidationError, ValueError, TypeError, KeyError):
            error = EvolutionError("validation_failed")
            return JSONResponse(error.envelope(correlation), status_code=422, headers={"Cache-Control": "no-store"})

    # Register concrete routes so the exported contract never advertises an arbitrary catch-all.
    routes = {
        "/v2/tenants": ["GET", "POST"],
        "/v2/tenants/{t}/principals": ["POST"],
        "/v2/tenants/{t}/principals/{id}/credentials": ["POST"],
        "/v2/tenants/{t}/credentials/{id}/rotate": ["POST"],
        "/v2/tenants/{t}/credentials/{id}/revoke": ["POST"],
        "/v2/tenants/{t}/workloads": ["POST", "GET"],
        "/v2/tenants/{t}/workloads/{w}": ["GET", "PATCH"],
        "/v2/tenants/{t}/workloads/{w}/rollback": ["POST"],
        "/v2/tenants/{t}/workloads/{w}/bindings": ["GET"],
        "/v2/tenants/{t}/workloads/{w}/manifest": ["GET"],
        "/v2/tenants/{t}/policies": ["GET"],
        "/v2/tenants/{t}/policies/validate": ["POST"],
        "/v2/tenants/{t}/policies/{p}/versions": ["POST"],
        "/v2/tenants/{t}/policies/{p}/versions/{v}": ["GET"],
        "/v2/tenants/{t}/policies/{p}/diff": ["GET"],
        "/v2/tenants/{t}/policies/{p}/versions/{v}/reviews": ["POST"],
        "/v2/tenants/{t}/policies/{p}/versions/{v}/revoke": ["POST"],
        "/v2/tenants/{t}/releases": ["POST"],
        "/v2/tenants/{t}/releases/{id}": ["GET"],
        "/v2/tenants/{t}/audit": ["GET"],
        "/v2/tenants/{t}/capabilities": ["GET"],
        "/v2/tenants/{t}/runtimes": ["POST"],
        "/v2/tenants/{t}/evidence": ["POST"],
        "/v2/tenants/{t}/evidence/{id}": ["GET"],
    }
    for resource in RESOURCES:
        routes[f"/v2/tenants/{{t}}/{resource}"] = ["GET", "POST"]
        routes[f"/v2/tenants/{{t}}/{resource}/{{id}}/versions"] = ["POST"]
        routes[f"/v2/tenants/{{t}}/{resource}/{{id}}/versions/{{v}}"] = ["GET"]
    for path, methods in routes.items():
        for method in methods:
            app.add_api_route(path, handle, methods=[method], name=method.lower() + path.replace("/", "_"))
    if distributor is None and config is not None and config.signing_key_file:
        from pathlib import Path

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from .snapshots import SnapshotDistributor
        key_bytes = Path(config.signing_key_file).read_bytes()
        signing_key = serialization.load_pem_private_key(key_bytes, password=None) if key_bytes.startswith(b"-----BEGIN") else Ed25519PrivateKey.from_private_bytes(key_bytes)
        if not isinstance(signing_key, Ed25519PrivateKey):
            raise EvolutionError("validation_failed")
        public = signing_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        distributor = SnapshotDistributor(service.repository, service.governance, service.bindings, signing_key, hashlib.sha256(public).hexdigest()[:16], ttl_s=config.snapshot_ttl_s, capability_check=service.registry.validate_declared)
    if distributor is not None:
        from .sync_api import mount_sync_routes
        mount_sync_routes(app, service, distributor)
    _install_openapi(app)
    return app


def _install_openapi(app):
    """Describe the same closed commands enforced by the dispatcher.

    A Request-based handler is intentional so authentication precedes reading
    sensitive bodies; publish its strict schema explicitly instead of asking
    FastAPI to parse content before that authentication boundary.
    """
    from copy import deepcopy

    original = app.openapi

    def build():
        if app.openapi_schema is not None:
            return app.openapi_schema
        document = original()
        schemas = document.setdefault("components", {}).setdefault("schemas", {})
        raw_schemas = {}
        for name, model in vars(models).items():
            if isinstance(model, type) and issubclass(model, models.DomainModel):
                schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
                raw_schemas.update(schema.pop("$defs", {}))
                raw_schemas[name] = schema

        def ref(name):
            return {"$ref": "#/components/schemas/" + name}

        def obj(properties, required=(), **extra):
            return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False, **extra}

        def array(item, maximum=100):
            return {"type": "array", "items": item, "maxItems": maximum}

        def command(name, source, fields, required=None):
            props = {key: deepcopy(raw_schemas[source]["properties"][key]) for key in fields}
            schemas[name] = obj(props, required if required is not None else [key for key in raw_schemas[source].get("required", []) if key in fields])
            return name

        schemas.update(deepcopy(raw_schemas))
        schemas["Rule"]["allOf"] = [{"if": {"properties": {"kind": {"const": kind}}}, "then": {"properties": {"config": ref(config_type.__name__)}}} for kind, config_type in models.RULE_CONFIG_TYPES.items()]
        # Response schemas describe reader-safe records. Creation command schemas
        # below draw from the original domain properties and retain input refs.
        for schema in schemas.values():
            for secret in SECRET_FIELDS:
                schema.get("properties", {}).pop(secret, None)
            if "required" in schema:
                schema["required"] = [key for key in schema["required"] if key not in SECRET_FIELDS]
        uuid = {"type": "string", "format": "uuid", "pattern": "^[0-9a-f-]{36}$"}
        positive = {"type": "integer", "minimum": 1}
        digest = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
        reason = {"type": "string", "minLength": 1, "maxLength": 500}
        schemas["ResourceReference"] = obj({"id": uuid, "version": positive, "digest": digest}, ("id", "version", "digest"))
        schemas["ErrorResponse"] = obj({"error": ref("Error")}, ("error",))
        command("TenantCreate", "Tenant", ("tenant_id", "display_name"))
        command("PrincipalCreate", "Principal", ("display_name", "roles"))
        command("CredentialCreate", "Credential", ("expires_at", "workload_id"), ("expires_at",))
        schemas["CredentialRotate"] = obj({"overlap_seconds": {"type": "integer", "minimum": 0, "maximum": 60}}, ("overlap_seconds",))
        schemas["ReasonCommand"] = obj({"reason": reason}, ("reason",))
        schemas["CredentialIssued"] = obj({"credential_id": uuid, "one_time_secret": {"type": "string", "writeOnly": True}, "expires_at": {"type": "string", "format": "date-time"}}, ("credential_id", "one_time_secret", "expires_at"))
        schemas["CredentialRevoked"] = obj({"credential_id": uuid, "revoked_at": {"type": "string", "format": "date-time"}}, ("credential_id", "revoked_at"))
        command("WorkloadCreate", "Workload", ("workload_id", "display_name", "mode", "allowed_stages", "provider_ids", "quota_id", "required_state"), ("workload_id", "display_name", "mode", "allowed_stages", "provider_ids", "quota_id", "required_state"))
        command("WorkloadPatch", "Workload", ("display_name", "status"), ())
        schemas["WorkloadPatch"]["minProperties"] = 1
        policy_props = {"parents": array(ref("PolicyRef"), 3), "rules": array(ref("Rule"), 64), "residency_ref": ref("ResourceReference"), "list_refs": array(ref("ResourceReference"), 256), "destination_refs": array(ref("ResourceReference"), 256), "provider_refs": array(ref("ResourceReference"), 256), "tool_refs": array(ref("ResourceReference"), 256), "quota_ref": ref("ResourceReference"), "failure_behavior": {"enum": ["block", "observe"], "type": "string"}}
        schemas["PolicyCandidateCommand"] = obj(policy_props, ("rules", "residency_ref", "quota_ref"))
        schemas["PolicyValidateCommand"] = obj({**policy_props, "policy_id": raw_schemas["PolicyVersion"]["properties"]["policy_id"]}, ("policy_id", "rules", "residency_ref", "quota_ref"))
        schemas["PolicyValidated"] = obj({"valid": {"const": True}, "compiled_digest": digest, "resolved_refs": array(ref("PolicyRef"), 3), "diagnostics": {"type": "array", "maxItems": 0}}, ("valid", "compiled_digest", "resolved_refs", "diagnostics"))
        schemas["PolicyReview"] = obj({"compiled_digest": digest, "reason": reason, "outcome": {"type": "string", "enum": ["approve", "reject"]}, "evidence_ids": array(uuid)}, ("compiled_digest", "reason", "outcome"))
        schemas["PolicyRevoke"] = obj({"compiled_digest": digest, "reason": reason}, ("compiled_digest", "reason"))
        schemas["PolicyDetail"] = obj({"policy": ref("PolicyVersion"), "approvals": array(ref("Approval"))}, ("policy", "approvals"))
        schemas["PolicySummary"] = obj({"policy_id": {"type": "string"}, "version": positive, "status": raw_schemas["PolicyVersion"]["properties"]["status"]}, ("policy_id", "version", "status"))
        schemas["PolicyDiff"] = obj({"changed_rule_ids": array({"type": "string"}, 128), "changed_dependencies": array(obj({"resource_id": uuid, "old_digest": {"anyOf": [digest, {"type": "null"}]}, "new_digest": {"anyOf": [digest, {"type": "null"}]}}, ("resource_id", "old_digest", "new_digest")), 1024), "old_digest": digest, "new_digest": digest, "impacts": array({"type": "string"})}, ("changed_rule_ids", "changed_dependencies", "old_digest", "new_digest", "impacts"))
        command("ReleaseCommand", "Release", ("policy", "workload_ids", "stage", "state", "expected_revisions", "reason"))
        schemas["ReleaseCommand"]["properties"]["canary_evidence_id"] = uuid
        schemas["ReleaseCommand"]["allOf"] = [{"if": {"properties": {"stage": {"const": "full"}}}, "then": {"required": ["canary_evidence_id"]}}]
        schemas["ReleaseDetail"] = obj({"release": ref("Release"), "receipts": array(ref("RuntimeReceipt"), 1000)}, ("release", "receipts"))
        schemas["RollbackCommand"] = obj({"target_revision": positive, "expected_revision": {"type": "integer", "minimum": 0}, "reason": reason}, ("target_revision", "expected_revision", "reason"))
        command("RuntimeCreate", "RuntimeInstall", ("zone", "mode", "telemetry", "version", "offline_max_age_s"), ("zone", "mode", "telemetry", "version", "offline_max_age_s"))
        schemas["EvidenceReceipt"] = obj({"run_id": uuid, "status": {"enum": ["pass", "fail"]}}, ("run_id", "status"))
        schemas["SessionCreate"] = obj({"credential": {"type": "string", "maxLength": 256, "writeOnly": True}}, ("credential",))
        schemas["SessionCreated"] = obj({"session_id": uuid, "csrf_token": {"type": "string"}, "expires_at": {"type": "string", "format": "date-time"}}, ("session_id", "csrf_token", "expires_at"))
        schemas["SessionRevoked"] = obj({"status": {"const": "revoked"}}, ("status",))
        for resource, (name, id_name) in RESOURCES.items():
            fields = set(raw_schemas[name]["properties"]) - {id_name, "tenant_id", "version", "digest", "created_at", "revision"}
            command(name + "Create", name, sorted(fields))
        for name in ("Tenant", "Workload", "PolicySummary", "Binding", "AuditEvent", *(item[0] for item in RESOURCES.values())):
            schemas[name + "Page"] = obj({"items": array(ref(name)), "next_cursor": {"type": ["string", "null"]}}, ("items", "next_cursor"))
        schemas["CapabilityList"] = obj({"items": array(ref("Capability"))}, ("items",))
        schemas["WorkloadDetail"] = {**deepcopy(raw_schemas["Workload"])}
        schemas["WorkloadDetail"]["properties"].update({"desired_binding": {"type": ["object", "null"]}, "applied_binding": {"type": ["object", "null"]}})
        schemas["ConnectorManifest"] = {"type": "object", "description": "Registered runtime URL, CA fingerprint, pinned connector contract and desired/applied revisions; contains no credentials."}
        document["components"]["securitySchemes"] = {"BearerAuth": {"type": "http", "scheme": "bearer"}, "AdminSession": {"type": "apiKey", "in": "cookie", "name": SESSION_COOKIE}}

        def response(schema_name):
            return {"description": "Successful operation", "content": {"application/json": {"schema": ref(schema_name)}}}

        for path, path_item in document["paths"].items():
            for method, operation in path_item.items():
                if method not in {"get", "post", "patch", "delete"} or path == "/health/live":
                    continue
                request_name, response_name, success = None, None, "200"
                operation["security"] = [{"BearerAuth": []}]
                operation["responses"] = {str(code): {"description": "Safe application error", "content": {"application/json": {"schema": ref("ErrorResponse")}}} for code in sorted(set(models.ERROR_STATUS.values()))}
                parameters = [{"name": name, "in": "path", "required": True, "schema": positive if name == "v" else uuid if name == "id" else {"type": "string", "minLength": 1, "maxLength": 128}} for name in ("t", "w", "p", "v", "id") if "{" + name + "}" in path]
                parameters.append({"name": "X-Request-ID", "in": "header", "required": False, "schema": uuid})
                if method in {"post", "patch"} and not path.startswith("/admin/"):
                    parameters.append({"name": "Idempotency-Key", "in": "header", "required": True, "schema": uuid})
                if method == "patch":
                    parameters.append({"name": "If-Match", "in": "header", "required": True, "schema": positive})
                if path == "/admin/session":
                    operation["security"] = [] if method == "post" else [{"AdminSession": []}]
                    parameters.append({"name": "Origin", "in": "header", "required": True, "schema": {"type": "string", "format": "uri"}})
                    if method == "post":
                        request_name, response_name, success = "SessionCreate", "SessionCreated", "201"
                    else:
                        response_name = "SessionRevoked"
                        parameters.append({"name": "X-CSRF-Token", "in": "header", "required": True, "schema": {"type": "string"}})
                elif path == "/v2/runtime/snapshot":
                    response_name = "Snapshot"
                    operation["responses"]["304"] = {"description": "Unchanged and still-valid signed lease; expiry is not extended"}
                elif path == "/v2/runtime/receipts":
                    request_name = response_name = "RuntimeReceipt"
                elif path == "/v2/tenants":
                    request_name, response_name, success = ("TenantCreate", "Tenant", "201") if method == "post" else (None, "TenantPage", "200")
                elif path.endswith("/principals"):
                    request_name, response_name, success = "PrincipalCreate", "Principal", "201"
                elif path.endswith("/credentials"):
                    request_name, response_name, success = "CredentialCreate", "CredentialIssued", "201"
                elif "/credentials/" in path:
                    request_name, response_name = ("CredentialRotate", "CredentialIssued") if path.endswith("/rotate") else ("ReasonCommand", "CredentialRevoked")
                elif path.endswith("/workloads"):
                    request_name, response_name, success = ("WorkloadCreate", "Workload", "201") if method == "post" else (None, "WorkloadPage", "200")
                elif path.endswith("/workloads/{w}"):
                    request_name, response_name = ("WorkloadPatch", "Workload") if method == "patch" else (None, "WorkloadDetail")
                elif path.endswith("/rollback"):
                    request_name, response_name, success = "RollbackCommand", "Binding", "202"
                elif path.endswith("/bindings"):
                    response_name = "BindingPage"
                elif path.endswith("/manifest"):
                    response_name = "ConnectorManifest"
                elif "/policies" in path:
                    if path.endswith("/validate"):
                        request_name, response_name = "PolicyValidateCommand", "PolicyValidated"
                    elif path.endswith("/reviews"):
                        request_name, response_name = "PolicyReview", "Approval"
                    elif path.endswith("/revoke"):
                        request_name, response_name = "PolicyRevoke", "Approval"
                    elif path.endswith("/versions"):
                        request_name, response_name, success = "PolicyCandidateCommand", "PolicyVersion", "201"
                    elif path.endswith("/versions/{v}"):
                        response_name = "PolicyDetail"
                    elif path.endswith("/diff"):
                        response_name = "PolicyDiff"
                        parameters.extend({"name": name, "in": "query", "required": True, "schema": positive} for name in ("from_version", "to_version"))
                    else:
                        response_name = "PolicySummaryPage"
                        parameters.append({"name": "status", "in": "query", "required": False, "schema": raw_schemas["PolicyVersion"]["properties"]["status"]})
                elif path.endswith("/releases"):
                    request_name, response_name, success = "ReleaseCommand", "Release", "202"
                elif "/releases/" in path:
                    response_name = "ReleaseDetail"
                elif path.endswith("/audit"):
                    response_name = "AuditEventPage"
                    parameters.extend({"name": name, "in": "query", "required": False, "schema": {"type": "string"}} for name in ("workload_id", "action", "from", "until"))
                elif path.endswith("/capabilities"):
                    response_name = "CapabilityList"
                elif path.endswith("/runtimes"):
                    request_name, response_name, success = "RuntimeCreate", "RuntimeInstall", "201"
                elif path.endswith("/evidence"):
                    request_name, response_name, success = "EvidenceReport", "EvidenceReceipt", "201"
                elif "/evidence/" in path:
                    response_name = "EvidenceReport"
                else:
                    for resource, (name, _) in RESOURCES.items():
                        if "/" + resource in path:
                            request_name, response_name, success = (name + "Create", name, "201") if method == "post" else (None, name + "Page" if path.endswith("/" + resource) else name, "200")
                            break
                if response_name is None:
                    raise RuntimeError("Management route lacks an explicit wire schema")
                if response_name.endswith("Page"):
                    parameters.extend(({"name": "cursor", "in": "query", "required": False, "schema": {"type": "string"}}, {"name": "limit", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}}))
                operation["parameters"] = parameters
                operation["responses"][success] = response(response_name)
                if method == "post" and any(f"/{resource}" in path for resource in RESOURCES):
                    operation["responses"][success]["headers"] = {"Location": {"description": "Exact immutable configuration version GET path", "schema": {"type": "string", "format": "uri-reference"}}}
                    operation["description"] = "Creates an immutable configuration version; existing policy references and runtime bindings are unchanged. The server assigns the version number." if path.endswith("/versions") else "Creates a configuration identity and its immutable first version."
                if request_name:
                    content = {"application/json": {"schema": ref(request_name)}}
                    if path == "/admin/session":
                        content["application/x-www-form-urlencoded"] = {"schema": ref(request_name)}
                    operation["requestBody"] = {"required": True, "content": content}
        app.openapi_schema = document
        return document

    app.openapi = build
