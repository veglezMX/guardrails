"""Metadata-only release accounting. Raw pytest failures are never serialized."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_files():
    paths = [p for p in (ROOT / 'mvp').rglob('*') if p.is_file() and (p.suffix in ('.py', '.sql', '.json', '.yaml') or p.name.endswith('Dockerfile'))]
    paths += [ROOT / name for name in ('pyproject.toml', 'requirements.lock', 'dev-requirements.lock', 'justfile')]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def write_safe(path, value, prohibited=()):
    raw = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()
    # Match plain AND JSON-escaped forms, including tokens and Unicode corpus values.
    for item in prohibited:
        if not item:
            continue
        candidates = (item.encode(), json.dumps(item, ensure_ascii=True)[1:-1].encode())
        if any(candidate in raw for candidate in candidates):
            raise ValueError('unsafe_evidence')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.pending')
    temporary.write_bytes(raw)
    temporary.chmod(0o600)
    temporary.replace(path)


class EvidencePlugin:
    def __init__(self, inventory, destination):
        self.inventory, self.destination = inventory, destination
        self.counts, self.outcomes, self.collected = Counter(), {}, []
        self.error_codes = set()

    def pytest_configure(self, config):
        # pytest.main installs the rewrite hook during configuration. Registering
        # before that call reaches a dummy hook and loses the helper registrations.
        import pytest
        pytest.register_assert_rewrite('proof.management_test_support', 'proof.runtime_test_support',
                                      'proof.gateway_test_support', 'proof.acceptance_stack')

    def pytest_collection_modifyitems(self, session, config, items):
        self.collected = [item.nodeid for item in items]
        if set(self.collected) != set(self.inventory) or len(self.collected) != len(self.inventory):
            import pytest
            self.error_codes.add('case_inventory_mismatch')
            raise pytest.UsageError('case_inventory_mismatch')

    def pytest_assertion_pass(self, item, lineno, orig, expl):
        # Do not retain the expression or explanation: they can contain traffic.
        self.counts[item.nodeid] += 1

    def pytest_runtest_setup(self, item):
        if os.environ.get('PROOF_ACCEPTANCE_STATE'):
            label = 'authorized-retention' if 'test_retention_delegation_' in item.nodeid else hashlib.sha256(item.nodeid.encode()).hexdigest()[:16]
            directory = Path(os.environ['PROOF_ACCEPTANCE_STATE']) / 'boundary-tests' / label
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.environ['PROOF_MANAGEMENT_TEST_ROOT'] = str(directory)

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            self.error_codes.add('collection_failed')

    def pytest_runtest_logreport(self, report):
        item = self.outcomes.setdefault(report.nodeid, dict(passed=False, duration_s=0.0, phases=[], errors=[]))
        item['duration_s'] += report.duration
        item['phases'].append(report.when)
        if report.failed or report.skipped or hasattr(report, 'wasxfail'):
            item['errors'].append('case_' + report.when + '_failed')
            # Safe source locations aid diagnosis without exception/fixture values.
            trace = getattr(getattr(report, 'longrepr', None), 'reprtraceback', None)
            if trace:
                item['locations'] = [dict(file=str(entry.reprfileloc.path), line=entry.reprfileloc.lineno)
                                     for entry in trace.reprentries if getattr(entry, 'reprfileloc', None)]
        item['passed'] = set(item['phases']) == {'setup', 'call', 'teardown'} and not item['errors']
        if report.when == 'teardown':
            write_safe(self.destination.with_name('progress.json'), dict(completed=len(self.outcomes),
                total=len(self.inventory), last_case=report.nodeid, passed=item['passed'],
                failed_cases=sum(not v['passed'] for v in self.outcomes.values())))
            snapshot={node:dict(value,assertion_count=self.counts[node]) for node,value in self.outcomes.items()}
            write_safe(self.destination,dict(cases=snapshot,errors=sorted(self.error_codes),execution_complete=False,exit_code=None))

    def pytest_sessionfinish(self, session, exitstatus):
        for node in self.inventory:
            value = self.outcomes.setdefault(node, dict(passed=False, duration_s=0.0, phases=[], errors=['case_missing']))
            value['assertion_count'] = self.counts[node]
            if not value['assertion_count']:
                value['passed'] = False
                value['errors'].append('assertions_missing')
        if self.error_codes or any(not value['passed'] for value in self.outcomes.values()):
            session.exitstatus = 1
        write_safe(self.destination, dict(cases=self.outcomes, errors=sorted(self.error_codes), execution_complete=True, exit_code=int(session.exitstatus)))
        if os.environ.get('PROOF_ACCEPTANCE_STATE'):
            from .privacy import scan_reports, TRAFFIC, SECRETS
            try:
                scan_reports(self.destination.parent, TRAFFIC | SECRETS)
            except ValueError:
                self.error_codes.add('report_privacy_failed')
                session.exitstatus=1
                write_safe(self.destination,dict(cases=self.outcomes,errors=sorted(self.error_codes),execution_complete=True,exit_code=1))


def row_results(inventory, cases, *, global_errors=()):
    rows = []
    for row in range(1, 11):
        required = [name for name, values in inventory.items() if row in values['rows']]
        errors = set(global_errors)
        if not required:
            errors.add('row_inventory_empty')
        assertions = 0
        for name in required:
            value = cases.get(name, {})
            assertions += value.get('assertion_count', 0)
            if not value.get('passed'):
                errors.update(value.get('errors', ['case_missing']))
        rows.append(dict(row=row, passed=not errors and assertions > 0, scenario_ids=required,
                         assertion_count=assertions, artifact_paths=['cases.json', 'manifest.json', 'observations.json'],
                         sanitized_error_codes=sorted(errors)))
    return rows
