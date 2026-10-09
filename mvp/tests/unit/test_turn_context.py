from dataclasses import replace

import pytest

from guardrails.local_contract import ProofConfig
from guardrails.runtime.contexts import ContextStore
from guardrails.transport import ContractProblem
from proof.runtime_test_support import components, evaluation


def test_context_pins_immutable_policy_and_is_consumed_once():
    store = ContextStore()
    request = evaluation('secret text')
    binding, policy, client = components()
    ctx = store.create(request, binding, policy, client)
    assert ctx.policy is policy and ctx.binding is binding and ctx.snapshot_generation == 5
    assert 'secret text' not in repr(ctx) and not hasattr(ctx, 'content')
    output = evaluation(stage='output', context=ctx.context_id, correlation=request.correlation_id)
    assert store.consume(output) is ctx
    with pytest.raises(ContractProblem) as error:
        store.consume(output)
    assert error.value.code == 'context_invalid'


@pytest.mark.parametrize('mismatch', ['workload', 'correlation', 'tenant', 'unknown'])
def test_context_scope_cannot_be_exchanged_or_consumed_by_another_turn(mismatch):
    store = ContextStore()
    request = evaluation()
    ctx = store.create(request, *components())
    output = evaluation(stage='output', context=ctx.context_id, correlation=request.correlation_id)
    if mismatch == 'workload':
        output = replace(output, workload_id='team-b')
    elif mismatch == 'tenant':
        output = replace(output, tenant_id='other')
    elif mismatch == 'correlation':
        output = replace(output, correlation_id=evaluation().correlation_id)
    else:
        output = replace(output, context_id='unknown')
    with pytest.raises(ContractProblem):
        store.consume(output)
    assert ctx.context_id in store.contexts


def test_capacity_expiry_and_release_are_bounded():
    clock = [1.0]
    store = ContextStore(ProofConfig(context_ttl_s=120, max_contexts=1), clock=lambda: clock[0])
    request = evaluation()
    ctx = store.create(request, *components())
    with pytest.raises(ContractProblem) as error:
        store.create(request, *components())
    assert error.value.status == 429
    clock[0] = 121.0
    with pytest.raises(ContractProblem) as error:
        store.consume(evaluation(stage='output', context=ctx.context_id, correlation=request.correlation_id))
    assert error.value.code == 'context_expired' and not store.contexts
    token = store.create(request, *components()).context_id
    store.release(token)
    assert not store.contexts
