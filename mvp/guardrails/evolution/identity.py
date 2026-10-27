"""Hash-only credentials and revocable server-side administrator sessions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from typing import Callable, Iterable
from uuid import UUID, uuid4

from .errors import EvolutionError
from .storage import TenantRepository, TenantTransaction

ROLES = frozenset({"platform_admin", "tenant_admin", "author", "approver", "operator", "auditor", "viewer", "workload", "runtime"})
ADMIN_ROLES = ROLES - {"workload", "runtime"}
SESSION_COOKIE = "__Host-guardrails-session"
COOKIE_OPTIONS = {"secure": True, "httponly": True, "samesite": "strict", "path": "/", "max_age": 28800}


def _time(value: str | datetime) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if result.tzinfo is None or result.utcoffset() != timedelta(0):
            raise ValueError()
        return result
    except (TypeError, ValueError, AttributeError):
        raise EvolutionError("validation_failed") from None


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _uuid(value: str) -> str:
    try:
        if str(UUID(value)) != value:
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        raise EvolutionError("validation_failed") from None


@dataclass(frozen=True)
class AuthenticatedIdentity:
    principal: dict
    credential: dict
    tenant_id: str | None
    workload_id: str | None
    session_id: str | None = None

    @property
    def principal_id(self) -> str:
        return self.principal["principal_id"]

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(self.principal["roles"])

    @property
    def scope(self) -> str:
        return self.tenant_id or "_platform"


class IdentityService:
    def __init__(self, repository: TenantRepository, *, clock: Callable[[], datetime] | None = None):
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def provision_principal(self, tx: TenantTransaction, display_name: str, roles: Iterable[str], *, principal_id: str | None = None) -> dict:
        role_list = tuple(roles)
        if not role_list or len(role_list) != len(set(role_list)) or not set(role_list) <= ROLES:
            raise EvolutionError("validation_failed")
        if not isinstance(display_name, str) or not 1 <= len(display_name) <= 120:
            raise EvolutionError("validation_failed")
        if ("platform_admin" in role_list) != (tx.tenant_id == "_platform"):
            raise EvolutionError("forbidden")
        if set(role_list) & {"workload", "runtime"} and len(role_list) != 1:
            raise EvolutionError("forbidden")
        principal_id = _uuid(principal_id or str(uuid4()))
        return tx.insert("principals", principal_id, {
            "principal_id": principal_id, "tenant_id": None if tx.tenant_id == "_platform" else tx.tenant_id,
            "display_name": display_name, "roles": list(role_list), "status": "active", "created_at": _stamp(self.clock()),
        })

    def issue_credential(self, tx: TenantTransaction, principal_id: str, *, expires_at: str | datetime | None = None,
                         workload_id: str | None = None) -> dict:
        principal = tx.get("principals", principal_id, required=True, for_update=True)
        if principal["status"] != "active":
            raise EvolutionError("forbidden")
        is_workload = "workload" in principal["roles"]
        if is_workload != (workload_id is not None):
            raise EvolutionError("forbidden")
        if workload_id is not None:
            workload = tx.get("workloads", workload_id, required=True)
            if workload["status"] != "active":
                raise EvolutionError("forbidden")
        now = self.clock()
        maximum = now + timedelta(days=30 if is_workload else 1)
        expiry = _time(expires_at) if expires_at is not None else maximum
        if not now < expiry <= maximum:
            raise EvolutionError("validation_failed")
        credential_id = str(uuid4())
        token = f"gr2.{tx.tenant_id}.{credential_id}.{secrets.token_urlsafe(32)}"
        tx.insert("credentials", credential_id, {
            "credential_id": credential_id, "principal_id": principal_id,
            "tenant_id": principal["tenant_id"], "workload_id": workload_id,
            "token_hash": _hash(token), "expires_at": _stamp(expiry), "revoked_at": None,
            "previous_valid_until": None, "created_at": _stamp(now),
        })
        return {"credential_id": credential_id, "one_time_secret": token, "expires_at": _stamp(expiry)}

    def rotate_credential(self, tx: TenantTransaction, credential_id: str, *, overlap_seconds: int = 0) -> dict:
        if type(overlap_seconds) is not int or not 0 <= overlap_seconds <= 60:
            raise EvolutionError("validation_failed")
        old = tx.get("credentials", credential_id, required=True, for_update=True)
        now = self.clock()
        if old["revoked_at"] is not None or _time(old["expires_at"]) <= now or old["previous_valid_until"] is not None:
            raise EvolutionError("authentication_failed")
        result = self.issue_credential(tx, old["principal_id"], expires_at=old["expires_at"], workload_id=old["workload_id"])
        old["previous_valid_until"] = _stamp(min(now + timedelta(seconds=overlap_seconds), _time(old["expires_at"])))
        tx.put("credentials", credential_id, old, expected_revision=old["revision"])
        return result

    def revoke_credential(self, tx: TenantTransaction, credential_id: str) -> dict:
        old = tx.get("credentials", credential_id, required=True, for_update=True)
        if old["revoked_at"] is None:
            old["revoked_at"] = _stamp(self.clock())
            old = tx.put("credentials", credential_id, old, expected_revision=old["revision"])
        return {"credential_id": credential_id, "revoked_at": old["revoked_at"]}

    def _parse_secret(self, token: str, prefix: str) -> tuple[str, str]:
        if not isinstance(token, str) or len(token) > 256:
            raise EvolutionError("authentication_failed")
        pieces = token.split(".")
        if len(pieces) != 4 or pieces[0] != prefix or len(pieces[3]) != 43:
            raise EvolutionError("authentication_failed")
        try:
            _uuid(pieces[2])
        except EvolutionError:
            raise EvolutionError("authentication_failed") from None
        return pieces[1], pieces[2]

    def _active(self, tx: TenantTransaction, principal_id: str, workload_id: str | None) -> dict:
        principal = tx.get("principals", principal_id)
        tenant = tx.get("tenants", tx.tenant_id)
        if principal is None or principal["status"] != "active" or tenant is None or tenant.get("status") != "active":
            raise EvolutionError("authentication_failed")
        if workload_id is not None:
            workload = tx.get("workloads", workload_id)
            if workload is None or workload["status"] != "active":
                raise EvolutionError("authentication_failed")
        return principal

    def authenticate(self, token: str) -> AuthenticatedIdentity:
        tenant_id, credential_id = self._parse_secret(token, "gr2")
        try:
            with self.repository.transaction(tenant_id) as tx:
                credential = tx.get("credentials", credential_id)
                # Always perform constant-time digest comparison, even absent IDs.
                expected = credential["token_hash"] if credential else "0" * 64
                matches = hmac.compare_digest(_hash(token), expected)
                now = self.clock()
                if (not matches or credential is None or credential["revoked_at"] is not None
                    or _time(credential["expires_at"]) <= now
                    or (credential["previous_valid_until"] is not None and _time(credential["previous_valid_until"]) <= now)):
                    raise EvolutionError("authentication_failed")
                principal = self._active(tx, credential["principal_id"], credential["workload_id"])
                return AuthenticatedIdentity(principal, credential, principal["tenant_id"], credential["workload_id"])
        except EvolutionError:
            raise EvolutionError("authentication_failed") from None

    def authorize(self, identity: AuthenticatedIdentity, roles: Iterable[str], *, tenant_id: str | None = None,
                  workload_id: str | None = None) -> None:
        requested_roles = {roles} if isinstance(roles, str) else set(roles)
        if not requested_roles.intersection(identity.roles):
            raise EvolutionError("forbidden")
        if tenant_id is not None and identity.tenant_id != tenant_id:
            # Platform privilege must be explicitly requested, never inherited by operator/auditor.
            if not ("platform_admin" in requested_roles and "platform_admin" in identity.roles and identity.tenant_id is None):
                raise EvolutionError("resource_not_found")
        if workload_id is not None and identity.workload_id != workload_id:
            raise EvolutionError("resource_not_found")

    def require_distinct_approver(self, identity: AuthenticatedIdentity, author_id: str) -> None:
        self.authorize(identity, ("approver", "platform_admin"))
        if identity.principal_id == author_id:
            raise EvolutionError("self_approval")

    def create_session(self, token: str) -> dict:
        identity = self.authenticate(token)
        if not ADMIN_ROLES.intersection(identity.roles):
            raise EvolutionError("forbidden")
        now = self.clock()
        session_id = str(uuid4())
        secret = f"gs2.{identity.scope}.{session_id}.{secrets.token_urlsafe(32)}"
        csrf = secrets.token_urlsafe(32)
        with self.repository.transaction(identity.scope) as tx:
            tx.insert("sessions", session_id, {
                "session_id": session_id, "principal_id": identity.principal_id, "secret_hash": _hash(secret), "csrf_hash": _hash(csrf),
                "expires_at": _stamp(now + timedelta(hours=8)), "idle_expires_at": _stamp(now + timedelta(minutes=30)),
                "revoked_at": None, "created_at": _stamp(now),
            })
        return {"session_id": session_id, "session_secret": secret, "csrf_token": csrf, "expires_at": _stamp(now + timedelta(hours=8)),
                "cookie_name": SESSION_COOKIE, "cookie_options": dict(COOKIE_OPTIONS)}

    def authenticate_session(self, secret: str, *, csrf_token: str | None = None, mutation: bool = False,
                             origin: str | None = None, expected_origin: str | None = None) -> AuthenticatedIdentity:
        try:
            tenant_id, session_id = self._parse_secret(secret, "gs2")
        except EvolutionError:
            raise EvolutionError("session_expired") from None
        with self.repository.transaction(tenant_id) as tx:
            session = tx.get("sessions", session_id, for_update=True)
            now = self.clock()
            if (session is None or not hmac.compare_digest(_hash(secret), session["secret_hash"])
                or session["revoked_at"] is not None or _time(session["expires_at"]) <= now or _time(session["idle_expires_at"]) <= now):
                raise EvolutionError("session_expired")
            if mutation:
                if (not isinstance(csrf_token, str) or len(csrf_token) > 256
                    or not hmac.compare_digest(_hash(csrf_token), session["csrf_hash"])
                    or not expected_origin or not expected_origin.startswith("https://") or origin != expected_origin):
                    raise EvolutionError("forbidden")
            principal = self._active(tx, session["principal_id"], None)
            session["idle_expires_at"] = _stamp(min(now + timedelta(minutes=30), _time(session["expires_at"])))
            session = tx.put("sessions", session_id, session, expected_revision=session["revision"])
            return AuthenticatedIdentity(principal, {}, principal["tenant_id"], None, session_id)

    def revoke_session(self, secret: str) -> None:
        tenant_id, session_id = self._parse_secret(secret, "gs2")
        with self.repository.transaction(tenant_id) as tx:
            session = tx.get("sessions", session_id, for_update=True)
            if session is None or not hmac.compare_digest(_hash(secret), session["secret_hash"]):
                raise EvolutionError("session_expired")
            session["revoked_at"] = _stamp(self.clock())
            tx.put("sessions", session_id, session, expected_revision=session["revision"])
