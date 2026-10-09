"""Bounded memory-only, one-use turn contexts. No text is retained."""
import secrets
import time

from ..local_contract import EvaluationContext, ProofConfig
from ..transport import ContractProblem


class ContextStore:
    def __init__(self, config=None, *, clock=time.monotonic):
        self.config, self.clock = config or ProofConfig(), clock
        self.contexts = {}

    def expire(self):
        now = self.clock()
        for token in tuple(self.contexts):
            if self.contexts[token].expires_at_monotonic <= now:
                del self.contexts[token]

    def create(self, evaluation, binding, policy, client):
        self.expire()
        if len(self.contexts) >= self.config.max_contexts:
            raise ContractProblem('capacity_exceeded', 429)
        now, token = self.clock(), secrets.token_urlsafe(32)
        context = EvaluationContext(context_id=token, tenant_id=evaluation.tenant_id,
                                    workload_id=evaluation.workload_id, correlation_id=evaluation.correlation_id,
                                    binding=binding, policy=policy, snapshot_generation=client.generation,
                                    client_config=client, created_at_monotonic=now,
                                    expires_at_monotonic=now + self.config.context_ttl_s, output_consumed=False)
        self.contexts[token] = context
        return context

    def consume(self, evaluation):
        context = self.contexts.get(evaluation.context_id)
        if context is None or evaluation.stage != 'output' or (
                context.tenant_id, context.workload_id, context.correlation_id) != (
                evaluation.tenant_id, evaluation.workload_id, evaluation.correlation_id):
            raise ContractProblem('context_invalid', 422)
        del self.contexts[evaluation.context_id]  # consume before any await
        if context.expires_at_monotonic <= self.clock():
            raise ContractProblem('context_expired', 422)
        return context

    def release(self, token):
        self.contexts.pop(token, None)
