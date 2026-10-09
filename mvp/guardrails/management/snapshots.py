"""Stable full snapshots and authenticated, monotonic application receipts."""
import hmac
import json

from ..storage.control import audit, now
from ..transport import ContractProblem, parse_model
from .identity import IdentityService
from .models import SnapshotModel


class SnapshotPublisher:
    def __init__(self, repository):
        self.repository = repository

    def snapshot(self, actor):
        IdentityService.authorize(actor, capability='snapshot')
        with self.repository.transaction() as connection:
            row = connection.execute('SELECT snapshot FROM operations ORDER BY desired_generation DESC LIMIT 1').fetchone()
            if row is None:
                raise ContractProblem('management_unavailable', 503)
            return parse_model(row[0].encode(), SnapshotModel).model_dump(mode='json')

    def acknowledge(self, actor, body, correlation_id=None):
        IdentityService.authorize(actor, capability='applied')
        with self.repository.transaction(write=True) as connection:
            row = connection.execute('SELECT snapshot_digest FROM operations WHERE desired_generation=?', (body.generation,)).fetchone()
            old = connection.execute('SELECT generation,digest FROM runtime_ack WHERE singleton=1').fetchone()
            if row is None or not hmac.compare_digest(body.digest, row[0]) or (old and body.generation < old[0]):
                raise ContractProblem(status=409)
            if old and body.generation == old[0]:
                return {'applied_generation': old[0]}
            timestamp = now()
            audit(connection, actor_id=str(actor.credential_id), tenant_id=None, event_type='runtime_applied',
                  correlation_id=correlation_id, metadata={'applied_generation': body.generation}, occurred_at=timestamp)
            connection.execute('INSERT INTO runtime_ack VALUES (1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET generation=excluded.generation,digest=excluded.digest,acknowledged_at=excluded.acknowledged_at,service_id=excluded.service_id',
                               (body.generation, body.digest, timestamp, actor.service_id))
            connection.execute('UPDATE operations SET applied_at=? WHERE desired_generation<=? AND applied_at IS NULL', (timestamp, body.generation))
        return {'applied_generation': body.generation}

    def operation(self, actor, operation_id):
        IdentityService.authorize(actor)
        with self.repository.transaction() as connection:
            row = connection.execute('SELECT tenant_id,actor_id FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
            if row is None or (actor.role != 'security_admin' and row['tenant_id'] != actor.tenant_id):
                raise ContractProblem(status=404)
            return self.repository.operation(connection, operation_id)
