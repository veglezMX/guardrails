"""One explicitly authorized stopped-child → original timeout → recovery path.

This new capability does not extend NativeReadOnlyExecutor's public allowlist.
It reuses its authenticated executable/process transport solely for this fixed
captured program in the already-bound classifier Pod. The caller must supply
separate parent authorization, authenticated recovery readiness, final semantic
verification and durable recording. No signal is exposed as a generic argv API.
"""
from __future__ import annotations

import copy
import hashlib
import time

from mvp.evolution import classifier_admission_measurements as evidence
from mvp.tests.evolution.e2e import classifier_native_readonly as native
from mvp.tests.evolution.e2e import classifier_worker_transition as process
from mvp.tests.evolution.e2e.classifier_admission_support import check

AUTH_FIELDS = {'schema_version', 'operation', 'parent_invocation_sha256', 'preflight_sha256', 'owner_id', 'node_id',
    'pod_uid', 'container_id', 'selection_sha256', 'images_sha256', 'credentials_sha256', 'old_epoch', 'original',
    'supervisor_command_sha256', 'worker_command_sha256', 'worker_files', 'program_sha256', 'maximum_seconds'}
CLEANUP_SECONDS = 10


class _CleanupBudget(native.private_io.Budget):
    """Only restores the already-authorized original child; never an acceptance budget."""
    def __init__(self, executor, deadline, manifest, preflight, root):
        super().__init__(deadline)
        self.executor = executor
        self.manifest, self.preflight, self.root = manifest, preflight, root

    def check(self):
        check(self.deadline)
        evidence.require(self.executor.root == self.root
            and evidence.digest(self.executor.manifest) == self.manifest
            and evidence.digest(self.executor.preflight) == self.preflight,
            'admission_cleanup_authority_changed')


class _CleanupExecutor(native.NativeReadOnlyExecutor):
    """Only the captured inventory read within the separate resume allowance.

    The original manifest is retained byte-for-byte. Its public acceptance
    deadline is not extended. This private route authenticates only the already
    captured Pod before/after the fixed original-child resume program below.
    It cannot execute readiness scripts, Docker commands, or caller-selected
    Kubernetes operations through its callable interface.
    """
    def __init__(self, original, budget, transport):
        evidence.require(type(budget) is _CleanupBudget)
        super().__init__(original.manifest, original.preflight, budget, original.root)
        self.transport = copy.deepcopy(transport)

    def __call__(self, argv, *, deadline, timeout, maximum, input_text):
        prefix, config, _ = self.transport
        evidence.require(type(self.budget) is _CleanupBudget
            and native.finite(deadline) and deadline <= self.budget.deadline
            and native.finite(timeout) and 0 < timeout <= 10
            and type(maximum) is int and maximum == native.MAX_OUTPUT
            and type(argv) is tuple and argv == (*prefix, *native.GET)
            and type(input_text) is str and input_text == config,
            'admission_cleanup_inventory_scope')
        limit = min(deadline, self.budget.deadline, time.monotonic() + timeout)
        with self.budget.cap(limit):
            self._check(limit); self._sources(limit)
            executables = self.manifest['native_executables']
            evidence.require(set(executables) == {'docker', 'kubectl'}
                and executables['docker']['path'] == '/usr/bin/docker'
                and executables['kubectl']['path'] == self.manifest['deployment_state'] + '/bin/kubectl')
            state, material, owner, images = self._material(limit)
            actual, inventory = self._validate_kube(argv, input_text, state, material, owner, images, limit)
            evidence.require(inventory is True)
            result = self._invoke(actual, 'kubectl', limit, maximum, input_text)
            objects, _ = native.network.project_objects(native.contract.parse(result.encode(), limit=maximum),
                owner['owner_id'], images, self.preflight['images']['runtime'])
            evidence.require(all(objects[key] == self.preflight['binding'][key] for key in ('service', 'pods')))
            evidence.require(self._material(limit)[1] == material)
            self._sources(limit); self._check(limit)
            return result


class OwnedClassifierFault:
    def __init__(self, executor, authorization, program, *, record, readiness, authorize, verify, deadline):
        evidence.require(isinstance(executor, native.NativeReadOnlyExecutor) and type(program) is bytes
            and all(callable(x) for x in (record, readiness, authorize, verify)))
        self.executor = executor
        self.auth, self.program = copy.deepcopy(authorization), bytes(program)
        evidence.transition_authority(self.auth, self.auth.get('operation'))
        evidence.require(set(self.auth) == AUTH_FIELDS and self.auth['schema_version'] == 'evolution-classifier-worker-transition-v1')
        evidence.uuid(self.auth['operation']); evidence.uuid(self.auth['pod_uid'])
        evidence.require(all(evidence.sha(self.auth[k]) for k in ('parent_invocation_sha256', 'preflight_sha256',
            'node_id', 'container_id', 'selection_sha256', 'images_sha256', 'credentials_sha256',
            'supervisor_command_sha256', 'worker_command_sha256', 'program_sha256'))
            and self.auth['maximum_seconds'] == 180 and hashlib.sha256(self.program).hexdigest() == self.auth['program_sha256'])
        evidence.require(type(self.auth['old_epoch']) is str and len(self.auth['old_epoch']) == 32
            and all(x in '0123456789abcdef' for x in self.auth['old_epoch']))
        self.record, self.readiness, self.authorize, self.verify = record, readiness, authorize, verify
        self.deadline, self.counter = deadline, 0
        self.value = {'status': 'not_started', 'authorization': copy.deepcopy(self.auth), 'events': [], 'cleanup': 'pending'}
        self.expected = None
        self.bound_transport = None
        self.cleanup_started = False
        self.cleanup_native_attempts = []
        self.cleanup_binding = None
        self.cleanup_evidence = None

    def save(self, kind, observation):
        self.counter += 1; evidence.require(self.counter <= 64)
        event = {'kind': kind, 'observed': time.monotonic(), 'value': copy.deepcopy(observation)}
        self.value['events'].append(event)
        self.record(f'fault-{self.counter:02d}-{kind}.json', copy.deepcopy(self.value))

    def _objects(self, deadline, *, executor=None):
        e = self.executor if executor is None else executor
        e._sources(deadline)
        state, material, owner, images = e._material(deadline)
        server = e._native(owner, images, deadline)
        bound = e.preflight['binding']
        evidence.require(server == bound['api_server'] and self.auth['owner_id'] == owner['owner_id']
            and self.auth['node_id'] == owner['container_id'] == bound['node_id']
            and self.auth['pod_uid'] == bound['pods']['classifier']['pod_uid']
            and 'containerd://' + self.auth['container_id'] == bound['pods']['classifier']['container_id'])
        config, _, _ = native.network.static_kubeconfig(material['kubeconfig'], state=state, server=server, deadline=deadline)
        prefix = (str(state / 'bin/kubectl'), '--kubeconfig', '/dev/stdin', '--context', 'guardrails-evolution',
                  '--namespace', 'gr-platform', '--request-timeout=10s')
        raw = e((*prefix, *native.GET), deadline=deadline, timeout=min(10, deadline - time.monotonic()),
                maximum=native.MAX_OUTPUT, input_text=config)
        projected, _ = native.network.project_objects(native.contract.parse(raw.encode(), limit=native.MAX_OUTPUT),
                                                       owner['owner_id'], images, e.preflight['images']['runtime'])
        evidence.require(all(bound.get(k) == v for k, v in projected.items()), 'admission_pod_changed')
        return prefix, config, bound['pods']['classifier']['pod_name']

    def _call(self, operation, *, deadline, during_request=False):
        deadline = min(self.deadline, deadline); check(deadline)
        if during_request:
            # Read only the already-bound original PID/start/namespace through
            # the captured static route. Full native inventory brackets signals
            # and final recovery outside the original 3 s request. This narrow
            # path cannot send a signal or replace the admitted Pod identity.
            evidence.require(operation == 'status' and self.value['status'] == 'stopped'
                and self.bound_transport is not None)
            prefix, config, pod = self.bound_transport
            deadline = min(deadline, time.monotonic() + 1)
        else:
            prefix, config, pod = self._objects(deadline)
        settings = {'operation': operation, 'spec': {k: self.auth[k] for k in
            ('supervisor_command_sha256', 'worker_command_sha256', 'worker_files')}, 'expected': self.expected}
        args = (*prefix, '--cache-dir=/proc/self/fd', 'exec', pod, '-c', 'classifier', '--',
                '/opt/classifier/venv/bin/python', '-I', '-S', '-B', '-c', self.program.decode('utf-8'),
                native.contract.canonical(settings).decode('ascii'))
        # The program/args are constructed here; caller cannot supply a command.
        result = self.executor._invoke(args, 'kubectl', min(deadline, time.monotonic() + 10),
                                       32768, input_text=config)
        value = native.contract.parse(result.encode(), limit=32768)
        evidence.require(type(value) is dict and value.get('status') != 'refused', 'admission_fault_native_refused')
        if not during_request:
            evidence.require(self._objects(deadline) == (prefix, config, pod), 'admission_transport_changed')
            self.bound_transport = (prefix, config, pod)
        check(deadline)
        return value

    def suspend(self, *, deadline):
        check(deadline)
        evidence.require(self.value['status'] == 'not_started')
        evidence.require(self.authorize(copy.deepcopy(self.auth), copy.deepcopy(self.executor.preflight), deadline=deadline) is True,
                         'admission_transition_unauthorized')
        ready = self.readiness(deadline=deadline)
        evidence.transition_ready(ready, self.auth, old=True)
        before = self._call('capture', deadline=deadline)
        evidence.require(set(before) == {'status', 'supervisor', 'child'} and before['status'] == 'captured')
        self.expected = {k: before[k] for k in ('supervisor', 'child')}
        evidence.require(self.expected == self.auth['original'], 'admission_original_worker_changed')
        # Capture existing authority while its original parent budget is valid.
        # Later cleanup gets no new permission to observe requests or score a model.
        self.cleanup_binding = (evidence.digest(self.executor.manifest),
            evidence.digest(self.executor.preflight), self.executor.root)
        self.cleanup_evidence = (evidence.digest(self.auth), hashlib.sha256(self.program).hexdigest(),
            evidence.digest(self.expected), evidence.digest(self.bound_transport))
        self.value['status'] = 'signal_intended_outcome_unknown'
        self.save('intent', {'original': self.expected, 'ready': ready})  # fsync by required recorder BEFORE signal
        stopped = self._call('stop', deadline=deadline)
        evidence.require(stopped['status'] == 'stopped' and stopped['child']['state'] == 'T')
        process.same(stopped['child'], self.expected['child']); process.same(stopped['supervisor'], self.expected['supervisor'])
        self.value['status'] = 'stopped'; self.save('stopped', stopped)
        return copy.deepcopy(self.value)

    def stopped(self, *, deadline):
        evidence.require(self.value['status'] == 'stopped')
        value = self._call('status', deadline=deadline, during_request=True)
        evidence.require(value['status'] == 'observed' and value['old_terminated'] is False and value['child']['state'] == 'T')
        process.same(value['child'], self.expected['child']); process.same(value['supervisor'], self.expected['supervisor'])
        self.save('held', value)
        return value

    def recovered(self, *, deadline):
        evidence.require(self.value['status'] == 'stopped')
        while True:
            check(min(deadline, self.deadline))
            ready = self.readiness(deadline=min(deadline, self.deadline))
            evidence.require(type(ready) is dict and ready.get('status') in {'loading', 'ready'})
            if ready['status'] == 'ready': break
            # Readiness only: no request replay, warmup, process launch or model call.
            time.sleep(min(0.1, max(0, min(deadline, self.deadline) - time.monotonic())))
        evidence.transition_ready(ready, self.auth, old=False)
        current = self._call('status', deadline=deadline)
        evidence.require(current['status'] == 'observed' and current['old_terminated'] is True
            and current['child']['state'] in {'R', 'S'} and
            (current['child']['pid'], current['child']['start_ticks']) !=
            (self.expected['child']['pid'], self.expected['child']['start_ticks']))
        process.same(current['supervisor'], self.expected['supervisor'])
        self.save('replacement', {'process': current, 'ready': ready})
        evidence.require(self.verify(copy.deepcopy(self.value), deadline=deadline) is True,
                         'admission_transition_proof_refused')
        self.value['status'] = 'recovered'; self.save('recovered', {})
        return copy.deepcopy(self.value)

    def cleanup(self, *, deadline):
        evidence.require(not self.cleanup_started, 'admission_cleanup_replayed')
        self.cleanup_started = True
        if self.expected is None:
            self.value['cleanup'] = 'clear'; return 'clear'
        # The observation may already have expired. Restore only the exact original
        # child under a single 10-second cleanup allowance; normal native process
        # cleanup retains its separate original five-second reap limit. The failed
        # observation remains failed and its acceptance deadline is never changed.
        evidence.require(self.cleanup_binding is not None and self.bound_transport is not None)
        evidence.require(self.cleanup_evidence == (evidence.digest(self.auth), hashlib.sha256(self.program).hexdigest(),
            evidence.digest(self.expected), evidence.digest(self.bound_transport)), 'admission_cleanup_identity_changed')
        cleanup_deadline = time.monotonic() + CLEANUP_SECONDS
        original = self.executor
        budget = _CleanupBudget(original, cleanup_deadline, *self.cleanup_binding)
        budget.check()
        executor = _CleanupExecutor(original, budget, self.bound_transport)
        try:
            prefix, config, pod = self._objects(cleanup_deadline, executor=executor)
            evidence.require((prefix, config, pod) == self.bound_transport, 'admission_cleanup_route_changed')
            settings = {'operation': 'resume', 'spec': {k: self.auth[k] for k in
                ('supervisor_command_sha256', 'worker_command_sha256', 'worker_files')}, 'expected': self.expected}
            args = (*prefix, '--cache-dir=/proc/self/fd', 'exec', pod, '-c', 'classifier', '--',
                '/opt/classifier/venv/bin/python', '-I', '-S', '-B', '-c', self.program.decode('utf-8'),
                native.contract.canonical(settings).decode('ascii'))
            result = native.contract.parse(executor._invoke(args, 'kubectl', cleanup_deadline,
                32768, input_text=config).encode(), limit=32768)
            evidence.require(set(result) == {'status', 'signalled'} and type(result['signalled']) is bool
                and result['status'] in {'signal_sent', 'gone', 'pid_reused_not_signalled', 'already_running'})
            evidence.require(result['signalled'] == (result['status'] == 'signal_sent'))
            budget.check()
            evidence.require(self._objects(cleanup_deadline, executor=executor) == (prefix, config, pod),
                'admission_cleanup_route_changed')
            self.value['cleanup'] = 'clear'; self.save('cleanup', result)
            return 'clear'
        finally:
            self.cleanup_native_attempts = executor.attempts
