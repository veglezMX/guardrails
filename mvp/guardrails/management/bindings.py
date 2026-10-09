"""Observe onboarding, append-only history and conjunctive governance."""
from dataclasses import replace
from datetime import datetime
import json

from .. import local_contract as d
from ..storage.control import document, read_document, require_revision
from ..transport import ContractProblem, GovernanceFloorModel, PolicyBindingModel, PolicyModel, parse_model
from .governance import STATES, applicable, validate_binding, validate_combination
from .identity import IdentityService


def load_model(row, model):
    return parse_model(document(read_document(row)).encode(), model).to_domain()


def floors(connection):
    return tuple(load_model(row, GovernanceFloorModel) for row in connection.execute('SELECT document FROM governance_floors ORDER BY scope,scope_id'))


def policy_for(connection, tenant_id, policy_id, version):
    return load_model(connection.execute('SELECT document FROM policies WHERE tenant_id=? AND policy_id=? AND version=?', (tenant_id, policy_id, version)).fetchone(), PolicyModel)


def current(connection, tenant_id, workload_id):
    row = connection.execute('SELECT document FROM bindings_current WHERE tenant_id=? AND workload_id=?', (tenant_id, workload_id)).fetchone()
    return load_model(row, PolicyBindingModel) if row else None


def reference(binding):
    if binding is None:
        return None
    return dict(policy_id=binding.policy_id, policy_version=binding.policy_version,
                binding_revision=binding.revision, state=binding.enforcement_state,
                input_timeout_ms=binding.input_timeout_ms, output_timeout_ms=binding.output_timeout_ms)


class BindingService:
    def __init__(self, repository):
        self.repository = repository

    def change(self, actor, tenant_id, workload_id, body, expected, kind, correlation_id=None):
        IdentityService.authorize(actor, tenant_id)
        with self.repository.mutation(actor, tenant_id, 'binding_' + kind, workload_id=workload_id, correlation_id=correlation_id) as tx:
            connection = tx.connection
            read_document(connection.execute('SELECT document FROM workloads WHERE tenant_id=? AND workload_id=?', (tenant_id, workload_id)).fetchone())
            old = current(connection, tenant_id, workload_id)
            require_revision(old.revision if old else None, expected, create=old is None and kind == 'set')
            if old is None and kind != 'set':
                raise ContractProblem(status=404)
            active_floors = applicable(floors(connection), tenant_id)
            if kind == 'remove':
                if any(floor.require_binding for floor in active_floors):
                    raise ContractProblem('mandatory_floor_violation', 409)
                connection.execute('DELETE FROM bindings_current WHERE tenant_id=? AND workload_id=?', (tenant_id, workload_id))
                tx.metadata = {'reason': 'binding_removal', 'previous': reference(old), 'new': None}
                value = None
            else:
                revision = connection.execute('SELECT COALESCE(MAX(revision),0)+1 FROM binding_history WHERE tenant_id=? AND workload_id=?', (tenant_id, workload_id)).fetchone()[0]
                common = dict(revision=revision, generation=tx.generation, updated_at=datetime.fromisoformat(tx.timestamp),
                              updated_by=actor.credential_id, reason=body.reason, evidence_kind=None, fixture_run_id=None)
                if kind == 'set':
                    if body.reason != ('initial_binding' if old is None else 'policy_update'):
                        raise ContractProblem(status=422)
                    value = d.PolicyBinding(tenant_id=tenant_id, workload_id=workload_id,
                                            policy_id=body.policy_id, policy_version=body.policy_version,
                                            input_timeout_ms=body.input_timeout_ms, output_timeout_ms=body.output_timeout_ms,
                                            enforcement_state=old.enforcement_state if old else 'observe', **common)
                elif kind == 'state':
                    promotion = STATES.index(body.enforcement_state) > STATES.index(old.enforcement_state)
                    if body.reason != ('synthetic_promotion' if promotion else 'synthetic_demotion') or body.enforcement_state == old.enforcement_state:
                        raise ContractProblem(status=422)
                    common.update(evidence_kind=body.evidence_kind, fixture_run_id=body.fixture_run_id)
                    value = replace(old, enforcement_state=body.enforcement_state, **common)
                elif kind == 'rollback':
                    historical = load_model(connection.execute('SELECT document FROM binding_history WHERE tenant_id=? AND workload_id=? AND revision=?',
                                                               (tenant_id, workload_id, body.target_revision)).fetchone(), PolicyBindingModel)
                    # A rollback carries any synthetic provenance of its target state.
                    common.update(evidence_kind=historical.evidence_kind, fixture_run_id=historical.fixture_run_id)
                    value = replace(historical, **common)
                else:
                    raise ValueError('unknown_binding_operation')
                policy = policy_for(connection, tenant_id, value.policy_id, value.policy_version)
                try:
                    validate_binding(value, policy, active_floors)
                except d.DomainValidationError:
                    raise ContractProblem('mandatory_floor_violation', 409) from None
                connection.execute('INSERT INTO binding_history VALUES (?,?,?,?)', (tenant_id, workload_id, revision, document(value)))
                connection.execute('INSERT INTO bindings_current VALUES (?,?,?,?,?,?) ON CONFLICT(tenant_id,workload_id) DO UPDATE SET revision=excluded.revision,policy_id=excluded.policy_id,policy_version=excluded.policy_version,document=excluded.document',
                                   (tenant_id, workload_id, revision, value.policy_id, value.policy_version, document(value)))
                tx.metadata = dict(reason=body.reason, previous=reference(old), new=reference(value),
                                   binding_revision=revision, state=value.enforcement_state,
                                   evidence_kind=value.evidence_kind,
                                   fixture_run_id=str(value.fixture_run_id) if value.fixture_run_id else None)
        operation = self.repository.get_operation(tx.operation_id)
        return operation if value is None else dict(resource=value.to_wire(), operation=operation)

    def set_floor(self, actor, scope, scope_id, body, expected, correlation_id=None):
        IdentityService.authorize(actor, admin=True)
        if scope not in ('organization', 'tenant') or (scope == 'organization' and scope_id != 'org'):
            raise ContractProblem(status=404)
        tenant_id = scope_id if scope == 'tenant' else None
        with self.repository.mutation(actor, tenant_id, 'floor_updated', correlation_id=correlation_id) as tx:
            connection = tx.connection
            if tenant_id:
                read_document(connection.execute('SELECT document FROM tenants WHERE tenant_id=?', (tenant_id,)).fetchone())
            row = connection.execute('SELECT document FROM governance_floors WHERE scope=? AND scope_id=?', (scope, scope_id)).fetchone()
            old = load_model(row, GovernanceFloorModel) if row else None
            require_revision(old.revision if old else None, expected, create=old is None)
            value = body.to_floor(scope, scope_id, old.revision + 1 if old else 1)
            candidate = tuple(item for item in floors(connection) if (item.scope, item.tenant_id) != (value.scope, value.tenant_id)) + (value,)
            try:
                # Detect incompatible IDs even in tenants with no current bindings.
                for row in connection.execute('SELECT tenant_id FROM tenants'):
                    validate_combination(applicable(candidate, row[0]))
                for row in connection.execute('SELECT document FROM bindings_current'):
                    binding = load_model(row, PolicyBindingModel)
                    policy = policy_for(connection, binding.tenant_id, binding.policy_id, binding.policy_version)
                    validate_binding(binding, policy, applicable(candidate, binding.tenant_id))
            except d.DomainValidationError:
                raise ContractProblem('mandatory_floor_violation', 409) from None
            connection.execute('INSERT INTO governance_floors VALUES (?,?,?) ON CONFLICT(scope,scope_id) DO UPDATE SET document=excluded.document', (scope, scope_id, document(value)))
            tx.metadata = {'reason': 'floor_update', 'previous': old.to_wire() if old else None, 'new': value.to_wire()}
        return dict(resource=value.to_wire(), operation=self.repository.get_operation(tx.operation_id))

    def read(self, actor, tenant_id, workload_id):
        IdentityService.authorize(actor, tenant_id)
        with self.repository.transaction() as connection:
            binding = current(connection, tenant_id, workload_id)
            if binding is None:
                raise ContractProblem(status=404)
            ack = connection.execute('SELECT generation FROM runtime_ack WHERE singleton=1').fetchone()
            revision = None
            if ack:
                row = connection.execute('SELECT snapshot FROM operations WHERE desired_generation=?', (ack[0],)).fetchone()
                applied = json.loads(row[0])
                revision = next((item['revision'] for item in applied['bindings'] if (item['tenant_id'], item['workload_id']) == (tenant_id, workload_id)), None)
            # An acknowledgement does not report runtime's last successful refresh time.
            return dict(desired=binding.to_wire(), applied_revision=revision,
                        applied_generation=ack[0] if ack else 0, cache_age_s=None)
