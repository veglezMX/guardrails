"""Atomic evaluation quotas; trusted admission supplies approved limit bytes.

Call reserve inside the short eligibility transaction with current_deadline set
through commit. Never dispatch until that transaction commits. No content or
content fingerprints are retained; duplicate IDs cannot purchase free work.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import get_args
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .deadlines import current_deadline, remaining_ms
from .errors import EvolutionError
from .models import Quota, Stage, canonical_json_bytes

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def _uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
        return value
    except ValueError, TypeError, AttributeError:
        raise EvolutionError("validation_failed") from None


def _hash(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _storage_failure(error):
    code = (
        "upstream_timeout"
        if getattr(getattr(error, "orig", None), "sqlstate", None) in {"57014", "55P03"}
        else "evaluator_unavailable"
    )
    raise EvolutionError(code) from None


@dataclass(frozen=True)
class Reservation:
    lease_id: str
    owner_token: str = field(repr=False)
    tenant_id: str
    principal_id: str
    workload_id: str
    request_id: str
    expires_at: datetime


class QuotaExceeded(EvolutionError):
    def __init__(self, retry_after_s: int):
        super().__init__("quota_exceeded")
        self.retry_after_s = max(1, min(60, math.ceil(retry_after_s)))


class QuotaService:
    """PostgreSQL implementation of evaluation request and inflight meters.

    Clock injection is a test port; production uses database UTC, shared by all
    replicas. Daily model token/cost accounting is intentionally a separate meter.
    """

    def __init__(
        self,
        repository,
        *,
        clock=None,
        lease_max_seconds=10,
        cleanup_budget_seconds=0.5,
    ):
        if (
            type(lease_max_seconds) not in (int, float)
            or not math.isfinite(lease_max_seconds)
            or not 0 < lease_max_seconds <= 10
            or type(cleanup_budget_seconds) not in (int, float)
            or not math.isfinite(cleanup_budget_seconds)
            or not 0 < cleanup_budget_seconds <= 1
        ):
            raise ValueError("Invalid quota service budget")
        self.repository = repository
        self.clock = clock
        self.lease_max_seconds = lease_max_seconds
        self.cleanup_budget_seconds = cleanup_budget_seconds

    def _now(self, tx):
        now = (
            self.clock()
            if self.clock
            else tx.execute("SELECT clock_timestamp()").scalar_one()
        )
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() != timedelta(0)
        ):
            raise EvolutionError("evaluator_unavailable")
        return now.astimezone(UTC)

    @staticmethod
    def _quota(value, tenant_id, workload_id, *, tenant_scope):
        try:
            quota = (
                value
                if isinstance(value, Quota)
                else Quota.model_validate_json(canonical_json_bytes(value))
            )
        except ValidationError, ValueError, TypeError:
            raise EvolutionError("validation_failed") from None
        if quota.tenant_id not in (tenant_id, "_platform"):
            raise EvolutionError("forbidden")
        if quota.workload_id not in ((None,) if tenant_scope else (None, workload_id)):
            raise EvolutionError("forbidden")
        return quota

    def reserve(
        self,
        tx,
        *,
        principal,
        workload_id,
        request_id,
        policy_digest,
        stage,
        tenant_quota,
        workload_quota,
        deadline_monotonic,
    ):
        """Return the sole owner handle or fail without any admitted mutations.

        Caller must already hold governance-admission and validate eligibility,
        baseline authority, credential workload and revocation. Rechecking stored
        active scope here prevents a stale Principal object from admitting work.
        """
        outer = current_deadline.get()
        if (
            outer is None
            or type(deadline_monotonic) not in (int, float)
            or not math.isfinite(deadline_monotonic)
            or outer > deadline_monotonic
        ):
            raise EvolutionError("validation_failed")
        remaining_ms()
        if (
            tx.tenant_id == "_platform"
            or not isinstance(workload_id, str)
            or not _SLUG.fullmatch(workload_id)
        ):
            raise EvolutionError("forbidden")
        _uuid(request_id)
        if (
            not isinstance(policy_digest, str)
            or not _DIGEST.fullmatch(policy_digest)
            or stage not in get_args(Stage)
        ):
            raise EvolutionError("validation_failed")
        actor = (
            principal.model_dump(mode="json")
            if hasattr(principal, "model_dump")
            else principal
        )
        if (
            not isinstance(actor, dict)
            or actor.get("tenant_id") != tx.tenant_id
            or actor.get("status") != "active"
        ):
            raise EvolutionError("forbidden")
        principal_id = _uuid(actor.get("principal_id"))
        tq = self._quota(tenant_quota, tx.tenant_id, workload_id, tenant_scope=True)
        wq = self._quota(workload_quota, tx.tenant_id, workload_id, tenant_scope=False)
        try:
            tx.lock("quota")
            scope_rows = tx.execute(
                """SELECT kind,data FROM evolution.records WHERE tenant_id=:tenant AND
                ((kind='tenants' AND key=:tenant) OR (kind='principals' AND key=:principal)
                 OR (kind='workloads' AND key=:workload))
                ORDER BY CASE kind WHEN 'tenants' THEN 0 WHEN 'principals' THEN 1 ELSE 2 END
                FOR UPDATE""",
                {
                    "tenant": tx.tenant_id,
                    "principal": principal_id,
                    "workload": workload_id,
                },
            ).all()
            scopes = {row.kind: row.data for row in scope_rows}
            for kind in ("tenants", "principals", "workloads"):
                record = scopes.get(kind)
                if record is None or record.get("status") != "active":
                    raise EvolutionError("forbidden")
                if kind == "principals" and (
                    record.get("tenant_id") != tx.tenant_id
                    or "workload" not in record.get("roles", ())
                ):
                    raise EvolutionError("forbidden")
            key = f"{principal_id}:{workload_id}:{request_id}"
            if tx.get("quota_leases", key) is not None:
                raise EvolutionError("idempotency_conflict")
            # Pair database UTC with the monotonic instant before its query.
            # Later meter work must not shorten the first request's lease;
            # query latency can only conservatively retain its charge longer.
            clock_started = time.monotonic()
            now = self._now(tx)
            epoch = now.timestamp()
            usage = tx.execute(
                """SELECT count(*) AS tenant_count,
                count(*) FILTER (WHERE data->>'workload_id'=:workload) AS workload_count,
                min((data->>'expires_epoch')::numeric) AS tenant_expiry,
                min((data->>'expires_epoch')::numeric) FILTER (WHERE data->>'workload_id'=:workload) AS workload_expiry
                FROM evolution.records WHERE tenant_id=:tenant AND kind='quota_leases'
                AND data->>'state'='active' AND (data->>'expires_epoch')::numeric>:now""",
                {"tenant": tx.tenant_id, "workload": workload_id, "now": epoch},
            ).one()
            retry = []
            if usage.tenant_count >= tq.max_inflight:
                retry.append(float(usage.tenant_expiry) - epoch)
            if usage.workload_count >= wq.max_inflight:
                retry.append(float(usage.workload_expiry) - epoch)
            window_keys = [
                f"{scope}:{seconds}:{math.floor(epoch / seconds) * seconds}"
                for scope in ("tenant", "workload:" + workload_id)
                for seconds in (60, 1)
            ]
            existing_windows = {
                row.key: row.data
                for row in tx.execute(
                    """SELECT key,data FROM evolution.records WHERE tenant_id=:tenant
                    AND kind='quota_windows' AND key=ANY(CAST(:keys AS text[]))""",
                    {"tenant": tx.tenant_id, "keys": window_keys},
                )
            }
            windows = []
            for scope, quota in (("tenant", tq), ("workload:" + workload_id, wq)):
                for seconds, limit in (
                    (60, quota.requests_per_minute),
                    (1, quota.burst),
                ):
                    start = math.floor(epoch / seconds) * seconds
                    window_key = f"{scope}:{seconds}:{start}"
                    current = existing_windows.get(window_key)
                    count = current["count"] if current else 0
                    if count >= limit:
                        retry.append(start + seconds - epoch)
                    windows.append(
                        (
                            window_key,
                            current,
                            {
                                "tenant_id": tx.tenant_id,
                                "scope": scope,
                                "seconds": seconds,
                                "window_start": start,
                                "count": count + 1,
                            },
                        )
                    )
            if retry:
                raise QuotaExceeded(math.ceil(max(retry)))
            # No writes precede admission checks. All writes share the caller's
            # transaction and its deadline check immediately before commit.
            for window_key, old, data in windows:
                if old:
                    tx.put(
                        "quota_windows",
                        window_key,
                        data,
                        expected_revision=old["revision"],
                    )
                else:
                    tx.insert("quota_windows", window_key, data)
            # The admission transaction may have a shorter deadline than the
            # authenticated detector profile. Its lease must retain the charge
            # for the whole admitted operation, including audit finalization.
            remaining = min(deadline_monotonic - time.monotonic(), self.lease_max_seconds)
            if remaining <= 0:
                raise EvolutionError("upstream_timeout")
            expires = now + timedelta(
                seconds=min(deadline_monotonic - clock_started, self.lease_max_seconds)
            )
            lease_id, owner = str(uuid4()), secrets.token_urlsafe(32)
            tx.insert(
                "quota_leases",
                key,
                {
                    "tenant_id": tx.tenant_id,
                    "principal_id": principal_id,
                    "workload_id": workload_id,
                    "request_id": request_id,
                    "policy_digest": policy_digest,
                    "stage": stage,
                    "lease_id": lease_id,
                    "owner_hash": _hash(owner),
                    "state": "active",
                    "expires_epoch": expires.timestamp(),
                },
            )
            tx.link("quota_leases", key, "principals", principal_id)
            tx.link("quota_leases", key, "workloads", workload_id)
            remaining_ms()
            return Reservation(
                lease_id,
                owner,
                tx.tenant_id,
                principal_id,
                workload_id,
                request_id,
                expires,
            )
        except SQLAlchemyError as error:
            _storage_failure(error)

    def release(self, reservation: Reservation, *, outcome="completed") -> bool:
        """Bounded cleanup independent of an expired evaluation deadline.

        Error propagates safely; the unexpired lease remains charged and expires
        naturally, rather than allowing dispatch after a failed cleanup.
        """
        if not isinstance(reservation, Reservation) or outcome not in {
            "completed",
            "cancelled",
            "failed",
        }:
            raise EvolutionError("validation_failed")
        token = current_deadline.set(time.monotonic() + self.cleanup_budget_seconds)
        try:
            with self.repository.transaction(reservation.tenant_id) as tx:
                tx.lock("quota")
                key = f"{reservation.principal_id}:{reservation.workload_id}:{reservation.request_id}"
                lease = tx.get("quota_leases", key)
                try:
                    valid = (
                        lease is not None
                        and lease["lease_id"] == reservation.lease_id
                        and hmac.compare_digest(
                            lease["owner_hash"], _hash(reservation.owner_token)
                        )
                    )
                except KeyError, AttributeError, UnicodeError:
                    valid = False
                if not valid:
                    raise EvolutionError("forbidden")
                if lease["state"] != "active":
                    return False
                state = (
                    "expired"
                    if lease["expires_epoch"] <= self._now(tx).timestamp()
                    else "released"
                )
                tx.put(
                    "quota_leases",
                    key,
                    {**lease, "state": state},
                    expected_revision=lease["revision"],
                )
                return True
        except SQLAlchemyError as error:
            _storage_failure(error)
        finally:
            current_deadline.reset(token)

    def reconcile(self, tenant_id, *, bounded_batch=256) -> int:
        if type(bounded_batch) is not int or not 1 <= bounded_batch <= 1000:
            raise EvolutionError("validation_failed")
        token = current_deadline.set(time.monotonic() + self.cleanup_budget_seconds)
        try:
            with self.repository.transaction(tenant_id) as tx:
                tx.lock("quota")
                now = self._now(tx).timestamp()
                rows = tx.execute(
                    """SELECT key,data FROM evolution.records
                    WHERE tenant_id=:tenant AND kind='quota_leases' AND data->>'state'='active'
                    AND (data->>'expires_epoch')::numeric<=:now
                    ORDER BY (data->>'expires_epoch')::numeric,key LIMIT :limit""",
                    {"tenant": tenant_id, "now": now, "limit": bounded_batch},
                ).all()
                for key, lease in rows:
                    tx.put(
                        "quota_leases",
                        key,
                        {**lease, "state": "expired"},
                        expected_revision=lease["revision"],
                    )
                return len(rows)
        except SQLAlchemyError as error:
            _storage_failure(error)
        finally:
            current_deadline.reset(token)
