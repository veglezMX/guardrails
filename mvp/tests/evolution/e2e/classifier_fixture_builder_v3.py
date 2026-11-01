"""Fixed, explicitly authorized worker fixture composition; no live defaults.

Calling build_worker_plugin does no I/O. Its context enter authenticates the
parent capsule and required genuine final-worker adapter before any native
executor/client can run. The exact pytest fixture then constructs one read-only
cluster. No future parent completion is needed inside the worker. The parent
must include cleanup/retention time in the original worker deadline; this module
never refreshes that deadline, even on failure.
"""
from __future__ import annotations

import base64
import copy
import os
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from evolution import classifier_provenance_io_v3 as private_io
from evolution import classifier_provenance_v3 as proof
from evolution import classifier_worker_invocation_v3 as invocation

from mvp.tests.evolution.e2e import classifier_native_readonly as native
from mvp.tests.evolution.e2e import classifier_provenance_fixtures_v3 as fixtures
from mvp.tests.evolution.e2e import classifier_provenance_support_v3 as current
from mvp.tests.evolution.e2e import classifier_readonly_cluster_v3 as readonly

SELF = 'mvp/tests/evolution/e2e/classifier_fixture_builder_v3.py'
MODULES = {
    __name__: SELF,
    'evolution.classifier_provenance': 'mvp/evolution/classifier_provenance.py',
    'evolution.classifier_corpus_extension': 'mvp/evolution/classifier_corpus_extension.py',
    'evolution.classifier_corpus_extension_v2': 'mvp/evolution/classifier_corpus_extension_v2.py',
    'evolution.classifier_corpus_extension_v3': 'mvp/evolution/classifier_corpus_extension_v3.py',
    'evolution.classifier_confirmation_v8': 'mvp/evolution/classifier_confirmation_v8.py',
    proof.__name__: 'mvp/evolution/classifier_provenance_v3.py',
    private_io.__name__: 'mvp/evolution/classifier_provenance_io_v3.py',
    invocation.__name__: 'mvp/evolution/classifier_worker_invocation_v3.py',
    native.__name__: 'mvp/tests/evolution/e2e/classifier_native_readonly.py',
    fixtures.__name__: 'mvp/tests/evolution/e2e/classifier_provenance_fixtures_v3.py',
    current.__name__: 'mvp/tests/evolution/e2e/classifier_provenance_support_v3.py',
    readonly.__name__: 'mvp/tests/evolution/e2e/classifier_readonly_cluster_v3.py',
}
REQUIRED = set(MODULES.values()) | set(native.SOURCE_MODULES.values()) | current.REQUIRED | set(readonly.SOURCE_PATHS.values())


class FixtureBuildError(RuntimeError):
    """Finite failure codes; private receipts/transport bytes never reach logs."""


def loaded_sources(manifest, root, reader):
    proof.require(root.is_absolute() and root.resolve() == root and REQUIRED <= set(manifest['sources']))
    for name, relative in MODULES.items():
        module = sys.modules.get(name)
        proof.require(Path(getattr(module, '__file__', '')).resolve() == root / relative)
        expected = manifest['sources'][relative]
        proof.require(reader.public_files.get(str(root / relative)) == expected)
        reader({'path': str(root / relative), 'sha256': expected})
    reader.budget.check()


def runtime_environment(authorization):
    """Existing wrapper destinations must match the authenticated parent scope."""
    proof.require(os.environ.get('EVOLUTION_TEST_EVIDENCE_DIR') == authorization['output_directory'])
    if authorization['governance_root'] is not None:
        proof.require(os.environ.get('EVOLUTION_EVALUATOR_OPERATION_ROOT') == authorization['governance_root'])


def save_attempts(executor, directory, *, authorization_ref, outcome):
    """Bounded private records only. Neither a read attempt nor its index is proof."""
    proof.require(outcome in {'worker_returned', 'worker_failed'})
    rows = executor.attempts
    proof.require(type(rows) is list and len(rows) <= native.MAX_ATTEMPTS)
    refs, total = [], 0
    for index, row in enumerate(rows):
        proof.exact(row, {'spawned', 'stdout', 'stderr', 'truncated', 'cleanup', 'status'})
        proof.require(type(row['spawned']) is bool and type(row['truncated']) is bool
            and row['cleanup'] in {'not_started', 'completed', 'failed'}
            and row['status'] in {'incomplete', 'completed', 'failed'})
        for name in ('stdout', 'stderr'):
            proof.require(type(row[name]) is bytes)
            total += len(row[name])
        proof.require(total <= native.MAX_RETAINED and len(row['stdout']) + len(row['stderr']) <= native.MAX_OUTPUT)
        record = {key: value for key, value in row.items() if key not in {'stdout', 'stderr'}}
        record.update(stdout_base64=base64.b64encode(row['stdout']).decode('ascii'),
            stderr_base64=base64.b64encode(row['stderr']).decode('ascii'))
        refs.append(directory.document(f'{index + 1:03d}.json', record))
    return directory.document('index.json', {'schema_version': 'evolution-classifier-private-native-attempts-v1',
        'authorization': authorization_ref, 'outcome': outcome, 'attempts': refs,
        'raw_bytes': total, 'operational_success_claim': False})


@contextmanager
def build_worker_plugin(manifest_ref, expected, source_root, *, qualify_final_worker):
    """Yield only the fixed explicit plugin; required adapter has no fallback."""
    checks = attempts = executor = context = None
    primary = None
    manifest = None
    try:
        proof.require(callable(qualify_final_worker))
        arguments = copy.deepcopy(expected)
        root = Path(source_root)
        proof.require(str(root) == str(arguments['root']))
        budget = private_io.Budget(arguments['deadline'])
        initial = private_io.StableReader(budget, public_files={})
        manifest = invocation.load(copy.deepcopy(manifest_ref), reader=initial, expected=arguments)
        reader = private_io.StableReader(budget, public_files=manifest['public_files'],
            private_large_files=manifest.get('private_large_files', ()))
        loaded_sources(manifest, root, reader)
        def observe(*, phase, operation, deadline):
            proof.require(context is not None and context.cluster is not None and phase != 'consume')
            runtime_environment(authorized['authorization'])
            checker = context.cluster._classifier_current_checker
            proof.require(isinstance(checker, current.CurrentChecker)
                and checker.authorization_sha256 == manifest['authorization']['sha256']
                and checker.executor is executor)
            with budget.cap(deadline):
                return checker.observe_locked(phase=phase, operation=operation, deadline=budget.deadline)
        verifier = private_io.ClosureProvenanceVerifier(authorization_ref=manifest['authorization'], expected_case=arguments['case_id'],
            expected_nodeid=arguments['nodeid'], expected_sources=arguments['sources'],
            expected_registry_sha256=arguments['registry_sha256'], expected_corpus_manifest=manifest['corpus_manifest'],
            load=reader, qualify_final_worker=qualify_final_worker, current=observe, now=lambda: datetime.now(UTC))
        authorized = verifier.authorize()
        runtime_environment(authorized['authorization'])
        # All filesystem authority below comes from this independently pinned,
        # fully authenticated authorization, never from a successful boolean.
        checks = private_io.PrivateDirectory(authorized['authorization']['current_checks_directory'], budget, create=True)
        attempts = private_io.PrivateDirectory(checks.path / 'native-attempts', budget, create=True)
        executor = native.NativeReadOnlyExecutor(manifest, authorized['preflight'], budget, root)
        recorder = private_io.RecordedVerifier(verifier, checks)
        actual_constructor = readonly.ReadOnlyClusterConstructor(source_root=root, reader=reader, executor=executor)
        def constructor(**kwargs):
            runtime_environment(authorized['authorization'])
            return actual_constructor(**kwargs)
        context = fixtures.ClassifierFixtureContext(manifest=manifest, recorder=recorder, read_only_constructor=constructor)
        plugin = fixtures.ClassifierFixturePlugin(context)
        # A second original-budget/source check precedes returning control. The
        # fixture reauthenticates again before its sole cluster construction.
        loaded_sources(manifest, root, reader)
        budget.check()
        yield plugin
        budget.check()
        runtime_environment(authorized['authorization'])
        loaded_sources(manifest, root, reader)
    except BaseException:  # noqa: BLE001 - sanitize after retaining all finite partial progress
        primary = FixtureBuildError('classifier_fixture_worker_failed')
        raise primary from None
    finally:
        cleanup = []
        if context is not None and context.cluster is not None:
            try:
                context.cluster.close()
            except BaseException:  # noqa: BLE001 - close/save every owned resource, no arbitrary error text
                cleanup.append('classifier_fixture_cluster_cleanup_failed')
        if executor is not None and attempts is not None:
            try:
                save_attempts(executor, attempts, authorization_ref=manifest['authorization'],
                    outcome='worker_failed' if primary is not None or cleanup else 'worker_returned')
            except BaseException:  # noqa: BLE001 - existing partial files stay retained; no deadline refresh
                cleanup.append('classifier_fixture_retention_failed')
        for directory in (attempts, checks):
            if directory is not None:
                try:
                    directory.close()
                except BaseException:  # noqa: BLE001 - try all descriptor closes
                    cleanup.append('classifier_fixture_directory_cleanup_failed')
        if cleanup:
            if primary is None:
                primary = FixtureBuildError('classifier_fixture_cleanup_failed')
                for note in cleanup: primary.add_note(note)
                raise primary from None
            for note in cleanup: primary.add_note(note)
