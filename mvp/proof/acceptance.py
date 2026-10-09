"""One-command, offline ten-row acceptance with preserved safe evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time

from .reports import EvidencePlugin, digest, row_results, source_files, write_safe

ROOT=Path(__file__).resolve().parents[2]
PREPARED=ROOT/'.proof'
INVENTORY = ROOT / 'mvp/proof/acceptance-cases.json'


def required_inventory():
    value = json.loads(INVENTORY.read_text())
    if not value or {row for item in value.values() for row in item['rows']} != set(range(1, 11)):
        raise ValueError('incomplete_acceptance_inventory')
    from .fixtures import load_fixtures
    for fixture in load_fixtures(ROOT/'mvp/tests/fixtures','00000000-0000-0000-0000-000000000001'):
        function={'pii':'test_pii_corpus_both_gateways','lifecycle':'test_authored_lifecycle','failure':'test_authored_failure_boundaries'}[fixture.kind]
        node=f'mvp/tests/acceptance/test_release_matrix.py::{function}[{fixture.fixture_id}]'
        if node not in value or sorted(value[node]['rows'])!=sorted(fixture.acceptance_rows):
            raise ValueError('fixture_inventory_mismatch')
    return value


def worker():
    import pytest
    # Recompile only this pytest process's test/helper imports. Propagating a
    # cache-prefix environment variable makes every real service/CLI child
    # recompile its installed dependencies instead of using prepared bytecode.
    sys.pycache_prefix='/tmp/acceptance-pycache'
    report = Path(os.environ['PROOF_ACCEPTANCE_REPORT'])
    inventory = required_inventory()
    plugin = EvidencePlugin(inventory, report / 'cases.json')
    # Explicit node selection is closed and independent of ambient pytest filters.
    # No terminal reporter, traceback, captured bodies, or JUnit raw exception text.
    os.environ.pop('PYTEST_ADDOPTS', None)
    result = pytest.main(['-p', 'no:terminal', '-p', 'no:cacheprovider', '-o', 'addopts=',
        '-o', 'enable_assertion_pass_hook=true', '--basetemp=' + str(Path(os.environ['PROOF_ACCEPTANCE_STATE'])/'pytest-tmp'),
        *inventory], plugins=[plugin])
    return int(result)


def command(*args, check=True):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=check)


def validate_artifacts():
    manifest = json.loads((PREPARED / 'artifacts/manifest.json').read_text())
    sources = source_files()
    if manifest.get('source_files') != sources or manifest.get('source_digest') != digest(sources):
        raise ValueError('prepared_source_mismatch')
    for key in ('owned', 'gateway'):
        identity = manifest['images'][key]
        actual = json.loads(command('docker','image','inspect',identity['reference']).stdout)[0]
        if actual['Id'] != identity['image_id']:
            raise ValueError('prepared_image_mismatch')
    for name, expected in manifest['dependencies']['locks'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != expected:
            raise ValueError('prepared_lock_mismatch')
    wheel_manifest = PREPARED/'wheelhouse.sha256.json'
    if hashlib.sha256(wheel_manifest.read_bytes()).hexdigest() != manifest['dependencies']['wheelhouse_manifest_sha256']:
        raise ValueError('prepared_wheel_manifest_mismatch')
    return manifest


def main():
    from .gateway_test_support import GatewayStack, port
    from .acceptance_stack import Supervisor
    from guardrails.local_contract import ProofConfig, domain_to_wire
    from .fixtures import load_fixtures
    stack = GatewayStack(acceptance=True)
    report = PREPARED/'reports'/stack.run_id
    report.mkdir(parents=True, mode=0o700)
    started = datetime.now(timezone.utc).isoformat()
    inventory = required_inventory()
    errors, manifest, supervisor = [], {}, None
    runner_exit = None
    external_network, sentinel = stack.project+'-outside', stack.project+'-sentinel'
    sentinel_reachable = False
    sentinel_created = network_created = False
    source = source_files()
    commit = command('git','rev-parse','HEAD').stdout.strip()
    dirty = bool(command('git','status','--porcelain').stdout.strip())
    try:
        artifacts = validate_artifacts()
        fixtures = load_fixtures(ROOT/'mvp/tests/fixtures', stack.run_id)
        stack.prepare()
        config = json.loads(stack.config.read_text())
        for name,service in config['services'].items():
            service.pop('ports', None)
            service['image']=artifacts['images']['gateway' if name.startswith('gateway-') else 'owned']['image_id']
        config['services']['mock-model']['environment']['GUARDRAILS_MOCK_CORPUS'] = '/app/mvp/tests/fixtures/pii-cases.json'
        stack.config.write_text(json.dumps(config, indent=2))
        external_port = port()
        command('docker','network','create',external_network)
        network_created=True
        command('docker','run','-d','--name',sentinel,'--network',external_network,'--pull','never',
                '-p',f'127.0.0.1:{external_port}:8787',artifacts['images']['owned']['image_id'],'python','-m','http.server','8787')
        sentinel_created=True
        deadline = time.monotonic()+10
        import httpx
        with httpx.Client(trust_env=False) as client:
            while True:
                try:
                    sentinel_reachable = client.get(f'http://127.0.0.1:{external_port}').status_code == 200
                    if sentinel_reachable:
                        break
                except httpx.TransportError:
                    pass
                if time.monotonic()>deadline:
                    raise ValueError('sentinel_not_reachable')
                time.sleep(.1)
        address = json.loads(command('docker','inspect',sentinel).stdout)[0]['NetworkSettings']['Networks'][external_network]['IPAddress']
        supervisor = Supervisor(stack, address)
        runner = dict(image=artifacts['images']['owned']['image_id'], pull_policy='never',
            user=f'{os.getuid()}:{os.getgid()}', working_dir=str(ROOT), networks=['proof'],
            environment=dict(PYTHONPATH=str(ROOT/'mvp'), PYTHONDONTWRITEBYTECODE='1',
                PROOF_ACCEPTANCE_STATE=str(stack.root), PROOF_ACCEPTANCE_REPORT=str(report),
                PROOF_PKI=str(PREPARED/'pki'), PROOF_CONTROL_SOCKET=supervisor.path),
            volumes=[dict(type='bind',source=str(ROOT),target=str(ROOT),read_only=True),
                dict(type='bind',source=str(stack.root),target=str(stack.root)),
                dict(type='bind',source=str(report),target=str(report)),
                dict(type='bind',source=str(ROOT/supervisor.path),target=str(ROOT/supervisor.path))])
        config['services']['acceptance-runner'] = runner
        stack.config.write_text(json.dumps(config, indent=2))
        fixture_hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'mvp/tests/fixtures').glob('*.json'))}
        manifest = dict(run_id=stack.run_id, started_at=started, commit=commit, source_dirty=dirty,
            source_digest=digest(source), source_files=source, platform=artifacts['platform'],
            artifacts=artifacts, fixture_checksums=fixture_hashes,
            fixtures=[dict(fixture_id=f.fixture_id,kind=f.kind,acceptance_rows=list(f.acceptance_rows)) for f in fixtures],
            config_digest=digest(config), proof_config=domain_to_wire(ProofConfig()),
            test_overrides=dict(context_ttl_s=1, max_snapshot_age_s=1, scheduling_tolerance_ms=100, mock_hold_watchdog_s=8,
                queue_test_workers=1, queue_test_max_queued_jobs=2),
            topology=config, sentinel_reachable_from_supervisor=sentinel_reachable,
            assertion_count_semantics='instrumented successful Python assert statements; zero-assert scenarios fail',
            evidence_state_path=str(stack.root.relative_to(ROOT)))
        write_safe(report/'manifest.json',manifest)
        stack.compose('up','-d','--pull','never','--no-build','management','runtime','mock-model')
        print(f'Acceptance run {stack.run_id}; safe progress: {report.relative_to(ROOT)}/progress.json', flush=True)
        result = stack.compose('run','--rm','--no-deps','--pull','never','--name',stack.project+'-runner',
                               'acceptance-runner','python','-m','proof.acceptance','--worker',check=False)
        runner_exit = result.returncode
        # stdout/stderr may contain pytest-captured fixture values: discard them.
        if result.returncode and not (report/'cases.json').exists():
            errors.append('runner_failed')
        validate_artifacts()
        if source_files() != source:
            errors.append('source_changed_during_run')
    except Exception as error:
        known = {'prepared_source_mismatch','prepared_image_mismatch','prepared_lock_mismatch',
                 'prepared_wheel_manifest_mismatch','sentinel_not_reachable'}
        code = str(error) if isinstance(error, ValueError) and str(error) in known else 'acceptance_setup_or_execution_failed'
        errors.append(code)
    finally:
        if supervisor:
            supervisor.close()
        if stack.config.exists():
            for service in ('management','runtime','mock-model','gateway-a','gateway-b'):
                path=stack.root/(service+'.log')
                # Successful runs retain exactly the snapshots scanned by the
                # final privacy case. Do not append unscanned shutdown output.
                # A run that failed before that case still gets diagnostic logs.
                if not path.exists():
                    log=stack.compose('logs','--no-color',service,check=False)
                    path.write_text(log.stdout+log.stderr)
                    path.chmod(0o600)
            stopped = stack.compose('down','--remove-orphans',check=False)
            if stopped.returncode:
                errors.append('cleanup_failed')
        if sentinel_created and command('docker','rm','-f',sentinel,check=False).returncode:
            errors.append('sentinel_cleanup_failed')
        if network_created and command('docker','network','rm',external_network,check=False).returncode:
            errors.append('network_cleanup_failed')
        stack.client.close()
        if hasattr(stack,'api'):
            stack.api.client.close()
    cases = json.loads((report/'cases.json').read_text()) if (report/'cases.json').exists() else {'cases':{},'errors':['evidence_missing']}
    errors += cases['errors']
    if not cases.get('execution_complete'):
        errors.append('runner_incomplete')
    if runner_exit is not None and runner_exit != cases.get('exit_code'):
        errors.append('runner_exit_mismatch')
    if cases.get('exit_code') and all(v.get('passed') for v in cases['cases'].values()):
        errors.append('runner_failed')
    for artifact in ('manifest.json','observations.json','privacy.json'):
        if not (report/artifact).is_file():
            errors.append('required_artifact_missing')
    rows = row_results(inventory,cases['cases'],global_errors=errors)
    offline = next((v['passed'] for n,v in cases['cases'].items() if n.endswith('::test_offline_prepared_services_and_runner')),False)
    evidence = dict(run_id=stack.run_id, started_at=started, finished_at=datetime.now(timezone.utc).isoformat(),
        commit=commit, source_dirty=dirty, source_digest=digest(source), platform=platform.platform(),
        versions={name:str(manifest.get('artifacts',{}).get(name,'unknown')) for name in ('docker_engine','docker_compose','host_supervisor_python')},
        image_digests={k:v['image_id'] for k,v in manifest.get('artifacts',{}).get('images',{}).items()},
        lock_checksums=manifest.get('artifacts',{}).get('dependencies',{}).get('locks',{}),
        config_digest=manifest.get('config_digest',''), acceptance_rows=rows,
        offline_probe_passed=offline and sentinel_reachable, passed=all(r['passed'] for r in rows),
        limitations=['Synthetic local fixture coverage; no population-level PII accuracy claim.',
            'Linux x86_64, cooperative non-streaming text only; no AWS/live model/production readiness.',
            'Independent QA and a second matching fresh run are separate final release gates.',
            'Authored input/credential files and the separately authorized retention store are excluded from default privacy scans.'])
    write_safe(report/'results.json',evidence)
    # Worker scans every generated report against the complete in-memory traffic
    # inventory. The supervisor additionally checks its final serialization.
    from .privacy import scan_reports
    from .mock_model import SCENARIOS
    from .fixtures import PiiFixture
    prohibited=set(SCENARIOS.values())
    prohibited.update(p.read_text().strip() for p in stack.root.rglob('*.token'))
    prohibited.update(segment.text for f in load_fixtures(ROOT/'mvp/tests/fixtures',stack.run_id)
                      if isinstance(f,PiiFixture) for segment in f.input_segments.segments)
    try:
        scan_reports(report,prohibited)
    except ValueError:
        evidence['passed']=False
        for row in evidence['acceptance_rows']:
            row['passed']=False
            row['sanitized_error_codes'].append('report_privacy_failed')
        write_safe(report/'results.json',evidence)
    for row in rows:
        print(f"Row {row['row']}: {'PASS' if row['passed'] else 'FAIL'} ({row['assertion_count']} assertions)")
    print('Evidence retained: '+str(report.relative_to(ROOT)))
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker',action='store_true')
    args = parser.parse_args()
    raise SystemExit(worker() if args.worker else main())
