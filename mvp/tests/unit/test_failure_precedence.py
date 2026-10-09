from dataclasses import replace

import pytest

from guardrails.engine import BatchDecisionEngine
from guardrails.evaluators.pii import LocalPIIEvaluator
from proof.runtime_test_support import components, evaluation


def decide(state, *, requested='block', failures=None, result=None, failure=None):
    binding, policy, client = components(state, requested=requested, failures=failures)
    request = evaluation('a@b.co', 'unchanged')
    if result is None:
        result = dict(findings=[f.to_wire() for f in LocalPIIEvaluator().evaluate(request.content, policy.checks[0])], elapsed_ms=1.0)
    return BatchDecisionEngine().decide(request, binding, policy, client, result=result, failure=failure), client.failure_behavior


@pytest.mark.parametrize('state,action', [('disabled', 'allow'), ('observe', 'log_only'), ('warn', 'log_only'), ('redact', 'redact'), ('block', 'block')])
def test_normal_ceilings_preserve_findings_and_never_invent_actions(state, action):
    result, _ = decide(state)
    assert result.action == action and result.allowed == (action != 'block')
    assert result.would_be_action == ('allow' if state == 'disabled' else 'block')
    assert bool(result.findings) == (state != 'disabled')
    assert result.operational_codes == (('disabled',) if state == 'disabled' else ())
    if action == 'redact':
        assert [s.text for s in result.transformed_content.segments] == ['[REDACTED]', 'unchanged']


@pytest.mark.parametrize('state', ['disabled', 'observe', 'warn', 'redact', 'block'])
@pytest.mark.parametrize('failure', ['evaluation_timeout', 'evaluation_unavailable', 'management_unavailable', 'policy_unresolved'])
@pytest.mark.parametrize('behavior,action', [('block', 'block'), ('allow_and_log', 'log_only')])
def test_failure_actions_are_separate_from_state_ceilings(state, failure, behavior, action):
    result, _ = decide(state, failures={failure: behavior}, failure=failure)
    assert result.action == action and result.operational_codes == (failure,)
    assert not result.reason_codes and not result.findings and result.guardrail_category is None


@pytest.mark.parametrize('state,expected', [('observe', 'log_only'), ('redact', 'redact'), ('block', 'block')])
def test_permissive_audit_failure_cannot_weaken_computed_action(state, expected):
    result, behavior = decide(state, failures={'audit_failure': 'allow_and_log'})
    failed = BatchDecisionEngine().audit_failed(result, behavior)
    assert failed.action == expected and failed.transformed_content == result.transformed_content
    assert failed.audit_id is None and failed.audit_status == 'failed'
    assert failed.reason_codes == result.reason_codes and 'audit_write_failed' in failed.operational_codes


@pytest.mark.parametrize('state', ['disabled', 'observe', 'redact'])
def test_fail_closed_audit_escalates_even_disabled(state):
    result, behavior = decide(state)
    failed = BatchDecisionEngine().audit_failed(result, behavior)
    assert failed.action == 'block' and failed.transformed_content is None


@pytest.mark.parametrize('behavior,expected', [('block', 'block'), ('allow_and_log', 'log_only')])
@pytest.mark.parametrize('invalid', ['end', 'segment', 'check', 'reason', 'type'])
def test_invalid_worker_findings_follow_transformation_failure(behavior, expected, invalid):
    finding = dict(check_id='pii', segment_id='0', reason_code='GR-003:local_pii:email', start=0, end=6)
    field, value = {'end': ('end', 999), 'segment': ('segment_id', '9'), 'check': ('check_id', 'foreign'),
                    'reason': ('reason_code', 'raw secret'), 'type': ('start', True)}[invalid]
    finding[field] = value
    result, _ = decide('redact', failures={'transformation_failure': behavior}, result=dict(findings=[finding], elapsed_ms=1.0))
    assert result.action == expected and result.operational_codes == ('unsafe_transformation',)
    assert result.transformed_content is None and not result.findings


def test_block_state_preserves_weaker_policy_action_and_clean_content():
    result, _ = decide('block', requested='redact')
    assert result.action == 'redact'
    clean, _ = decide('block', result=dict(findings=[], elapsed_ms=0.0))
    assert clean.action == 'allow' and not clean.reason_codes


def test_runtime_audit_rejects_unknown_schema_without_rewriting(tmp_path):
    import sqlite3
    from guardrails.storage.audit import AuditRepository
    from guardrails.transport import ContractProblem
    path = tmp_path / 'unknown.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.executescript('CREATE TABLE schema_migrations(version INTEGER); INSERT INTO schema_migrations VALUES(2);')
    before = path.read_bytes()
    with pytest.raises(ContractProblem):
        AuditRepository(path)
    assert path.read_bytes() == before


def test_runtime_audit_failure_does_not_fabricate_identifier_or_persist_partial_row(tmp_path):
    import sqlite3
    import time
    from guardrails.storage.audit import AuditRepository
    from guardrails.transport import ContractProblem
    repository = AuditRepository(tmp_path / 'audit.sqlite3')
    decision, _ = decide('block')
    with sqlite3.connect(repository.path) as connection:
        connection.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON decision_audit BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(ContractProblem) as error:
        repository.write_decision(decision, evaluation('a@b.co'), components()[1], deadline=time.monotonic() + .5, cache_age=0.0)
    assert error.value.code == 'audit_write_failed'
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM decision_audit').fetchone()[0] == 0
