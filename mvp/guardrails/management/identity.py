"""Hash-only local credentials and server-derived authorization."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from .. import local_contract as d
from ..storage.control import document, read_document, require_revision
from ..transport import ContractProblem, parse_model
from .models import CredentialModel


def token_digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def credential(*, role, token, tenant_id=None, workload_id=None, revision=1, service_id=None):
    capabilities = {'runtime_service': ('snapshot', 'applied'),
                    'management_service': ('audit', 'schema', 'readiness')}.get(role, ())
    return d.Credential(credential_id=uuid4(), role=role, tenant_id=tenant_id, workload_id=workload_id,
                        token_sha256=token_digest(token), revision=revision,
                        created_at=datetime.now(timezone.utc), revoked_at=None,
                        service_id=service_id, capabilities=capabilities)


def insert_credential(connection, value):
    connection.execute('INSERT INTO credentials VALUES (?,?,?,?,?,?,?)',
                       (str(value.credential_id), value.token_sha256, value.role,
                        value.tenant_id, value.workload_id, None, document(value)))


class IdentityService:
    def __init__(self, repository):
        self.repository = repository

    def bootstrap(self, credentials):
        """Install trusted deployment hashes; never seed workloads, policies or bindings."""
        admins = [item for item in credentials if item.role == 'security_admin']
        if not admins or any(item.role == 'workload' for item in credentials):
            raise ContractProblem('identity_mismatch', 403)
        with self.repository.mutation(admins[0], None, 'bootstrap') as tx:
            existing = {row[0] for row in tx.connection.execute('SELECT token_sha256 FROM credentials')}
            missing = [item for item in credentials if item.token_sha256 not in existing]
            if not missing:
                tx.changed = False
                return
            tenant = d.Tenant(tenant_id='local-proof', display_name='Local proof', created_at=datetime.now(timezone.utc))
            tx.connection.execute('INSERT OR IGNORE INTO tenants VALUES (?,?)', ('local-proof', document(tenant)))
            for item in missing:
                insert_credential(tx.connection, item)

    def authenticate(self, authorization):
        if not authorization or not authorization.startswith('Bearer '):
            raise ContractProblem('authentication_failed', 401)
        token = authorization[7:]
        if not 32 <= len(token) <= 256 or any(char.isspace() for char in token):
            raise ContractProblem('authentication_failed', 401)
        try:
            digest = token_digest(token)
        except UnicodeError:
            raise ContractProblem('authentication_failed', 401) from None
        with self.repository.transaction() as connection:
            row = connection.execute('SELECT document,token_sha256 FROM credentials WHERE token_sha256=? AND revoked_at IS NULL', (digest,)).fetchone()
        if not hmac.compare_digest(digest, row['token_sha256'] if row else '0' * 64) or row is None:
            raise ContractProblem('authentication_failed', 401)
        return parse_model(row['document'].encode(), CredentialModel).to_domain()

    @staticmethod
    def authorize(actor, tenant_id=None, *, admin=False, capability=None):
        if capability is not None:
            if actor.role != 'runtime_service' or capability not in actor.capabilities:
                raise ContractProblem('identity_mismatch', 403)
            return
        if actor.role not in ('security_admin', 'tenant_operator'):
            raise ContractProblem('identity_mismatch', 403)
        if actor.role == 'tenant_operator' and tenant_id is not None and actor.tenant_id != tenant_id:
            raise ContractProblem(status=404)
        if admin and actor.role != 'security_admin':
            raise ContractProblem('identity_mismatch', 403)

    def register_tenant(self, actor, body, correlation_id=None):
        self.authorize(actor, admin=True)
        value = d.Tenant(tenant_id=body.tenant_id, display_name=body.display_name, created_at=datetime.now(timezone.utc))
        with self.repository.mutation(actor, body.tenant_id, 'tenant_registered', correlation_id=correlation_id) as tx:
            tx.connection.execute('INSERT INTO tenants VALUES (?,?)', (body.tenant_id, document(value)))
        return value.to_wire()

    def register_workload(self, actor, tenant_id, body, correlation_id=None):
        self.authorize(actor, tenant_id)
        token = secrets.token_urlsafe(32)
        value = d.Workload(tenant_id=tenant_id, workload_id=body.workload_id, display_name=body.display_name,
                           credential_revision=1, created_at=datetime.now(timezone.utc))
        issued = credential(role='workload', token=token, tenant_id=tenant_id, workload_id=body.workload_id)
        with self.repository.mutation(actor, tenant_id, 'workload_registered', workload_id=body.workload_id, correlation_id=correlation_id) as tx:
            read_document(tx.connection.execute('SELECT document FROM tenants WHERE tenant_id=?', (tenant_id,)).fetchone())
            tx.connection.execute('INSERT INTO workloads VALUES (?,?,?)', (tenant_id, body.workload_id, document(value)))
            insert_credential(tx.connection, issued)
        return dict(workload=value.to_wire(), token=token, operation=self.repository.get_operation(tx.operation_id))

    def rotate(self, actor, tenant_id, workload_id, expected, correlation_id=None):
        self.authorize(actor, tenant_id)
        token = secrets.token_urlsafe(32)
        with self.repository.mutation(actor, tenant_id, 'credential_rotated', workload_id=workload_id, correlation_id=correlation_id) as tx:
            value = read_document(tx.connection.execute('SELECT document FROM workloads WHERE tenant_id=? AND workload_id=?', (tenant_id, workload_id)).fetchone())
            require_revision(value['credential_revision'], expected)
            revision = value['credential_revision'] + 1
            rows = tx.connection.execute("SELECT document FROM credentials WHERE tenant_id=? AND workload_id=? AND role='workload' AND revoked_at IS NULL", (tenant_id, workload_id)).fetchall()
            for row in rows:
                old = parse_model(row['document'].encode(), CredentialModel).to_domain()
                revoked = replace(old, revoked_at=datetime.fromisoformat(tx.timestamp))
                tx.connection.execute('UPDATE credentials SET revoked_at=?,document=? WHERE credential_id=?', (tx.timestamp, document(revoked), str(old.credential_id)))
            issued = credential(role='workload', token=token, tenant_id=tenant_id, workload_id=workload_id, revision=revision)
            insert_credential(tx.connection, issued)
            value['credential_revision'] = revision
            tx.connection.execute('UPDATE workloads SET document=? WHERE tenant_id=? AND workload_id=?', (document(value), tenant_id, workload_id))
            tx.metadata = {'reason': 'credential_rotation'}
        return dict(credential_id=str(issued.credential_id), token=token, operation=self.repository.get_operation(tx.operation_id))
