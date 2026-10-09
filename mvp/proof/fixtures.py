"""Strict, authored acceptance fixtures; expectations never come from the detector."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from guardrails import local_contract as d
from guardrails.management import models as m
from guardrails.transport import (
    StrictModel, TextBatchModel, FindingModel, PolicyWriteModel, strict_json_loads,
)

FixtureId = Annotated[str, Field(pattern=r'^[a-z][a-z0-9-]{0,99}$')]
Row = Annotated[int, Field(ge=1, le=10)]


class Fixture(StrictModel):
    fixture_id: FixtureId
    acceptance_rows: tuple[Row, ...]

    @model_validator(mode='after')
    def unique_rows(self):
        if not self.acceptance_rows or len(set(self.acceptance_rows)) != len(self.acceptance_rows):
            raise ValueError('invalid_fixture_rows')
        return self


class PiiFixture(Fixture):
    kind: Literal['pii']
    label: Literal['clean', 'email', 'phone', 'overlap', 'invalid_span', 'unsupported_format']
    stage: d.Stage
    input_segments: TextBatchModel
    mock_scenario: FixtureId
    policy_alias: FixtureId
    enforcement_state: d.EnforcementState
    expected_findings: tuple[FindingModel, ...]
    expected_reason_codes: tuple[str, ...]
    expected_operational_codes: tuple[str, ...]
    expected_action: d.DecisionAction
    expected_transformed: TextBatchModel | None
    expect_model_call: bool

    @model_validator(mode='after')
    def valid_expectations(self):
        self.input_segments.to_domain()
        segments = {s.segment_id: s.text for s in self.input_segments.segments}
        for finding in self.expected_findings:
            if finding.segment_id not in segments or not 0 <= finding.start < finding.end <= len(segments[finding.segment_id]):
                raise ValueError('invalid_expected_span')
        if (self.expected_action == 'redact') != (self.expected_transformed is not None):
            raise ValueError('invalid_expected_transformation')
        if self.expected_transformed:
            self.expected_transformed.to_domain()
            if [s.segment_id for s in self.expected_transformed.segments] != list(segments):
                raise ValueError('invalid_expected_segments')
        if self.stage == 'output' and len(segments) != 1:
            raise ValueError('invalid_output_segments')
        return self


class LifecycleStep(StrictModel):
    operation: Literal['register', 'publish', 'bind', 'state', 'update', 'rollback', 'floor', 'inspect', 'rotate', 'unbind']
    request_template: dict
    expected_status: Annotated[int, Field(ge=200, le=599)]
    expected_problem_code: str | None

    def validate_request(self, run_id: str):
        model = {'register': m.WorkloadWrite, 'publish': PolicyWriteModel,
                 'bind': m.BindingWrite, 'update': m.BindingWrite, 'state': m.StateWrite,
                 'rollback': m.RollbackWrite, 'floor': m.FloorWrite,
                 'rotate': m.RotationWrite, 'unbind': m.RemovalWrite}.get(self.operation)
        value = dict(self.request_template)
        if value.get('fixture_run_id') == '$run':
            value['fixture_run_id'] = run_id
        if model:
            # JSON mode preserves strict scalar checking and JSON array -> tuple conversion.
            model.model_validate_json(d.canonical_bytes(value))
        elif value:
            raise ValueError('invalid_inspect_template')
        return value


class LifecycleFixture(Fixture):
    kind: Literal['lifecycle']
    actor_alias: Literal['admin', 'operator']
    interface: Literal['api', 'cli']
    steps: Annotated[tuple[LifecycleStep, ...], Field(min_length=1)]


class FailureFixture(Fixture):
    kind: Literal['failure']
    failure_location: Literal['worker_timeout', 'runtime_down', 'management_down', 'audit_write', 'unsafe_span', 'malformed_response', 'context_expired', 'client_unconfigured']
    policy_alias: FixtureId
    stage: d.Stage
    enforcement_state: d.EnforcementState
    expected_action: d.DecisionAction
    expected_source: Literal['runtime', 'connector']
    expected_operational_code: str
    expect_model_call: bool
    expect_delivery: bool


ProofFixture = Annotated[PiiFixture | LifecycleFixture | FailureFixture, Field(discriminator='kind')]
ADAPTER = TypeAdapter(tuple[ProofFixture, ...])


def load_fixtures(directory: Path, run_id: str) -> tuple[ProofFixture, ...]:
    result = []
    for name in ('pii-cases.json', 'lifecycle-cases.json', 'failure-cases.json'):
        raw = (directory / name).read_bytes()
        strict_json_loads(raw)
        result.extend(ADAPTER.validate_json(raw, strict=True))
    ids = [fixture.fixture_id for fixture in result]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('duplicate_or_empty_fixture_inventory')
    for fixture in result:
        if isinstance(fixture, LifecycleFixture):
            for step in fixture.steps:
                step.validate_request(run_id)
    return tuple(result)
