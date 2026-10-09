"""Capability-checked, immutable policy publication."""
from datetime import datetime

from .. import local_contract as d
from ..storage.control import document, read_document
from ..transport import ContractProblem
from .identity import IdentityService


class PolicyService:
    def __init__(self, repository):
        self.repository = repository

    def validate(self, actor, tenant_id, body):
        IdentityService.authorize(actor, tenant_id)
        if body.tenant_id != tenant_id:
            raise ContractProblem('identity_mismatch', 403)
        draft = body.to_domain()  # Revalidates supported evaluator/stages/types at publication.
        if draft.retain_content and actor.role != 'security_admin':
            raise ContractProblem('identity_mismatch', 403)
        with self.repository.transaction() as connection:
            read_document(connection.execute('SELECT document FROM tenants WHERE tenant_id=?', (tenant_id,)).fetchone())
        return dict(valid=True, canonical_digest=draft.content_digest(), capabilities_version=d.DETECTOR_FORMAT_VERSION)

    def publish(self, actor, tenant_id, body, correlation_id=None):
        digest = self.validate(actor, tenant_id, body)['canonical_digest']
        created = True
        with self.repository.mutation(actor, tenant_id, 'policy_published', correlation_id=correlation_id) as tx:
            row = tx.connection.execute('SELECT document,digest,operation_id FROM policies WHERE tenant_id=? AND policy_id=? AND version=?', (tenant_id, body.policy_id, body.version)).fetchone()
            if row:
                if row['digest'] != digest:
                    raise ContractProblem(status=409)
                policy = read_document(row)
                operation_id = row['operation_id']
                tx.changed = False
                created = False
            else:
                ordinal = tx.connection.execute('SELECT COALESCE(MAX(ordinal),0)+1 FROM policies WHERE tenant_id=? AND policy_id=?', (tenant_id, body.policy_id)).fetchone()[0]
                policy = d.Policy(**{name: getattr(body.to_domain(), name) for name in d.PolicyDraft.__dataclass_fields__},
                                  publication_ordinal=ordinal, digest=digest,
                                  published_at=datetime.fromisoformat(tx.timestamp), published_by=actor.credential_id).to_wire()
                operation_id = tx.operation_id
                tx.connection.execute('INSERT INTO policies VALUES (?,?,?,?,?,?,?)',
                                      (tenant_id, body.policy_id, body.version, ordinal, digest, document(policy), operation_id))
                tx.metadata = {'policy_id': body.policy_id, 'policy_version': body.version, 'policy_digest': digest}
        return dict(policy=policy, operation=self.repository.get_operation(operation_id)), created
