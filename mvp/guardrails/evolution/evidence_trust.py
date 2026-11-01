"""Audited platform bootstrap for public evidence verification keys.

This is a deployment helper, not a tenant HTTP route. Operators supply a mounted
platform credential to provision a runner's public key. Private signing bytes
never cross this interface. Rotate by registering a new key ID and revoking the
old ID; a revoked ID can never be reused.
"""

from __future__ import annotations

import base64
import re

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .audit import AuditStore
from .errors import EvolutionError
from .identity import IdentityService
from .models import utc_now
from .storage import TenantTransaction


class EvidenceTrust:
    def __init__(self, repository, *, identity=None, audit=None):
        self.repository = repository
        self.identity = identity or IdentityService(repository)
        self.audit = audit or AuditStore(repository)

    @staticmethod
    def _key_id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
            raise EvolutionError("validation_failed")
        return value

    def _platform_actor(self, token):
        identity = self.identity.authenticate(token)
        if identity.tenant_id is not None or "platform_admin" not in identity.roles:
            raise EvolutionError("forbidden")
        return identity

    def provision(self, platform_credential, *, key_id, public_key):
        """Pin exactly 32 public Ed25519 bytes under a previously unused key ID."""
        actor = self._platform_actor(platform_credential)
        key_id = self._key_id(key_id)
        if type(public_key) is not bytes or len(public_key) != 32:
            raise EvolutionError("validation_failed")
        try:
            Ed25519PublicKey.from_public_bytes(public_key)
        except ValueError:
            raise EvolutionError("validation_failed") from None
        with self.repository.transaction("_platform") as tx:
            tx.lock("evidence-trust:" + key_id)
            if tx.get("evidence_trust_roots", key_id):
                raise EvolutionError("revision_conflict")
            result = tx.insert("evidence_trust_roots", key_id, {"key_id": key_id, "algorithm": "Ed25519", "public_key_base64": base64.b64encode(public_key).decode(), "status": "active", "created_at": utc_now()})
            self.audit.append(tx, "configuration_created", actor_id=actor.principal_id, metadata={"resource_type": "evidence_trust_root", "resource_id": key_id, "operation": "register"})
            return result

    def revoke(self, platform_credential, *, key_id, expected_revision):
        actor = self._platform_actor(platform_credential)
        key_id = self._key_id(key_id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise EvolutionError("validation_failed")
        with self.repository.transaction("_platform") as tx:
            tx.lock("evidence-trust:" + key_id)
            current = tx.get("evidence_trust_roots", key_id, required=True, for_update=True)
            if current["status"] != "active":
                raise EvolutionError("revision_conflict")
            result = tx.put("evidence_trust_roots", key_id, {**current, "status": "revoked", "revoked_at": utc_now()}, expected_revision=expected_revision)
            self.audit.append(tx, "support_access", actor_id=actor.principal_id, metadata={"resource_type": "evidence_trust_root", "resource_id": key_id, "operation": "revoke_evidence_key"})
            return result

    def resolve(self, key_id, *, tx=None):
        """Narrow published-key reader; retain its row lock through ingestion.

        Supplying the tenant ingestion transaction serializes key revocation with
        the evidence write. The temporary platform scope is always restored;
        no arbitrary platform records are exposed to the caller.
        """
        key_id = self._key_id(key_id)
        if tx is None:
            with self.repository.transaction("_platform") as platform:
                return self._resolve_in(platform, key_id)
        previous_scope = tx.tenant_id
        tx.execute("SELECT set_config('guardrails.tenant_id', '_platform', true)")
        try:
            return self._resolve_in(TenantTransaction(tx.connection, "_platform"), key_id)
        finally:
            tx.execute("SELECT set_config('guardrails.tenant_id', :tenant, true)", {"tenant": previous_scope})

    @staticmethod
    def _resolve_in(tx, key_id):
        record = tx.get("evidence_trust_roots", key_id, for_update=True)
        if record is None or record["status"] != "active" or record.get("algorithm") != "Ed25519":
            raise EvolutionError("forbidden")
        try:
            return Ed25519PublicKey.from_public_bytes(base64.b64decode(record["public_key_base64"], validate=True))
        except (ValueError, TypeError):
            raise EvolutionError("forbidden") from None
