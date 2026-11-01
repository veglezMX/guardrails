"""Dedicated admission current observer; original common checker stays strict.

The full original read-only ownership/source/private/credentials/forward/node
checks are preserved. Exactly one separately authorized replacement epoch may be
observed after the admission authority validates its original stopped-worker and
peer-request timeline. No record or frozen preflight is rewritten.
"""
from __future__ import annotations

import copy
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from evolution import classifier_admission_authority as admission
from evolution import classifier_provenance_v2 as provenance
from guardrails.evolution.classifier_contract import identity, parse

from mvp.tests.evolution.e2e.classifier_provenance_support_v2 import (
    SCHEMA,
    BoundedObserver,
    CurrentChecker,
    ProvenanceCheckError,
    actual_subjects,
    digest,
    held_lock,
    network,
    remaining,
    require,
)

ADMISSION_MODULES = {
    __name__: 'mvp/tests/evolution/e2e/classifier_admission_current.py',
    admission.__name__: 'mvp/evolution/classifier_admission_authority.py',
    admission.evidence.__name__: 'mvp/evolution/classifier_admission_measurements.py',
}


class AdmissionCurrentChecker(CurrentChecker):
    def _sources(self, sources, deadline):
        require(set(ADMISSION_MODULES.values()) <= set(sources), 'admission_source_scope')
        return super()._sources(sources, deadline)

    def _loaded_sources(self):
        super()._loaded_sources()
        for name, relative in ADMISSION_MODULES.items():
            module = sys.modules.get(name)
            path = Path(getattr(module, '__file__', ''))
            require(path.is_absolute() and path.resolve() == self.root / relative,
                    'admission_loaded_source_mismatch')

    def observe_admission_locked(self, *, phase, operation, deadline, context, transition):
        started = time.monotonic()
        remaining(deadline)
        stop = min(deadline, started + 60)
        try:
            require(callable(self.executor) and self.cluster.profile == 'classifier'
                    and phase in {'before', 'after', 'restored', 'consume'}, 'current_configuration')
            require(digest(self.preflight_raw) == self.preflight_sha256
                    and digest(self.authorization_raw) == self.authorization_sha256, 'parent_pin_changed')
            auth = parse(self.authorization_raw, limit=network.MAX_METADATA)
            preflight = parse(self.preflight_raw, limit=network.MAX_METADATA)
            require(auth['preflight']['sha256'] == self.preflight_sha256, 'preflight_authorization')
            require(provenance.timestamp(auth['created_at']) <= datetime.now(UTC) < provenance.timestamp(auth['expires_at']),
                    'authorization_expired')
            provenance.validate_preflight(preflight, auth, now=datetime.now(UTC))
            require(context['shared']['authorization'] == auth and context['shared']['preflight'] == preflight
                    and context['authorization']['prerequisite']['sha256'] == self.authorization_sha256,
                    'admission_prerequisite_changed')
            require(context['authorization']['operation'] == operation
                    and deadline <= context['authorization']['deadline_monotonic'], 'admission_operation_changed')
            actual_expected = copy.deepcopy(preflight['expected'])
            if transition is not None:
                require(phase in {'after', 'restored', 'consume'}
                        and context['authorization']['case_id'] == admission.evidence.CASES[2]
                        and transition['authorization'] == context['transition'], 'admission_transition_required')
                # AdmissionAuthority already checked full request/peer timing. This
                # adapter independently checks the original identity and replacement
                # prefix before comparing the live worker against that exact epoch.
                require(transition['status'] == 'recovered' and transition['cleanup'] == 'clear'
                        and len(transition['events']) == 6, 'admission_transition_incomplete')
                prefix = copy.deepcopy(transition)
                prefix.update(status='stopped', cleanup='pending', events=prefix['events'][:4])
                actual_expected['worker_epoch'] = admission.replacement_prefix(prefix, context['transition'])
            else:
                require(phase == 'before' or context['transition'] is None, 'admission_transition_missing')
            require(preflight['identity'] == identity(), 'selected_runtime_identity')
            details = preflight['images']
            require(digest(self.node_boundary_raw) == preflight['node_boundary']['sha256']
                    == preflight['expected']['node_boundary_sha256']
                    and parse(self.node_boundary_raw, limit=network.MAX_METADATA) == details['node_boundary_snapshot'],
                    'node_boundary_receipt')
            require(set(details) == {'runtime', 'native_images', 'node_boundary_snapshot'}
                    and details['runtime'] in {'runtime-1', 'runtime-2'} and type(details['native_images']) is dict
                    and set(details['native_images']) == {'runtime', 'classifier'}, 'native_preflight')
            held_lock(self.cluster.deployment.state, deadline=stop)
            source = self._sources(preflight['sources'], stop)
            self._loaded_sources()
            material, owner = self._private(preflight, stop)
            entries = self._journal(material, owner, source, preflight, stop)
            self._forwards(material, preflight, source, stop)
            observer = BoundedObserver(self.cluster, runtime=details['runtime'], deadline=stop, executor=self.executor)
            observer.bound = copy.deepcopy(preflight['binding'])
            observer.images = copy.deepcopy(details['native_images'])
            observer._routing()
            self._boundary(preflight, material, source, stop)
            sample = observer.sample_locked()
            require(sample['binding_sha256'] == preflight['expected']['binding_sha256']
                    and sample['counter']['epoch'] == actual_expected['worker_epoch'], 'worker_epoch_changed')
            subjects = actual_subjects(material, entries, state=self.cluster.deployment.state,
                port=parse(material['assembly/ports.json'])['postgres'], deadline=stop,
                minimum_validity_seconds=preflight['expected']['minimum_validity_seconds'])
            self._forwards(material, preflight, source, stop)
            after_sample = observer.sample_locked()
            require(after_sample['binding_sha256'] == sample['binding_sha256']
                    and after_sample['counter']['epoch'] == sample['counter']['epoch'], 'worker_epoch_changed')
            self._boundary(preflight, material, source, stop)
            require(self._private(preflight, stop)[0] == material, 'private_file_changed')
            self._journal(material, owner, source, preflight, stop)
            require(self._sources(preflight['sources'], stop) == source, 'source_changed')
            self._loaded_sources()
            held_lock(self.cluster.deployment.state, deadline=stop)
            remaining(stop)
            value = {key: copy.deepcopy(actual_expected[key]) for key in provenance.CURRENT_FIELDS}
            value.update(schema_version=SCHEMA, preflight_sha256=self.preflight_sha256, phase=phase, operation=operation,
                         subjects=subjects, observed_at=datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
                         elapsed_ms=round((time.monotonic() - started) * 1000, 6))
            provenance.verify_current(actual_expected, value, preflight_sha256=self.preflight_sha256,
                                      phase=phase, operation=operation, now=datetime.now(UTC))
            return value
        except Exception:  # noqa: BLE001 - sanitize infrastructure failures and preserve primary error
            raise ProvenanceCheckError('classifier_current_check_failed') from None

