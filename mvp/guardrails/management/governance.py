"""Pure single-policy floor predicates, reusable by snapshot validation."""
from ..local_contract import DomainValidationError

STATES = ('disabled', 'observe', 'warn', 'redact', 'block')
ACTIONS = ('allow', 'log_only', 'redact', 'block')


def applicable(floors, tenant_id):
    return tuple(floor for floor in floors if floor.scope == 'organization' or floor.tenant_id == tenant_id)


def validate_combination(floors):
    ids = {floor.required_policy_id for floor in floors if floor.required_policy_id is not None}
    if len(ids) > 1:
        raise DomainValidationError('mandatory_floor_violation')


def validate_binding(binding, policy, floors):
    validate_combination(floors)
    for floor in floors:
        valid = (
            STATES.index(binding.enforcement_state) >= STATES.index(floor.minimum_state)
            and (floor.required_policy_id is None or policy.policy_id == floor.required_policy_id)
            and policy.publication_ordinal >= floor.minimum_policy_ordinals.get(policy.policy_id, 1)
            and not (floor.forbid_content_retention and policy.retain_content)
            and all(getattr(policy.failure_behavior, key) == 'block' for key in floor.require_fail_closed)
        )
        # Required stages and types must occur together in a sufficiently strong check.
        valid = valid and any(
            set(floor.required_stages) <= set(check.stages)
            and set(floor.required_pii_types) <= set(check.pii_types)
            and ACTIONS.index(check.requested_action) >= ACTIONS.index(floor.minimum_action)
            for check in policy.checks
        )
        if not valid:
            raise DomainValidationError('mandatory_floor_violation')
