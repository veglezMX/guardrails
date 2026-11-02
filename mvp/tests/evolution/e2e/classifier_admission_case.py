"""Caller-held-lock orchestration for the three bounded admission observations.

This unregistered family requires an independently authenticated parent callback
and an original exclusive durable recorder. The parent constructs the read-only
cluster and owns its lifetime/lock. A failed prefix never becomes a success
receipt. No final-worker adapter, operation directory or fault authority defaults
are supplied here; the existing common epoch invariant is unchanged.
"""
from __future__ import annotations

import asyncio
import copy
import time
from pathlib import Path

from evolution.diagnostics import SourceCatalog

from mvp.evolution import classifier_admission_measurements as evidence
from mvp.tests.evolution.e2e.classifier_admission_fault import OwnedClassifierFault
from mvp.tests.evolution.e2e.classifier_admission_governance import GovernedAdmission
from mvp.tests.evolution.e2e.classifier_admission_support import (
    AdmissionObservation,
    RuntimePort,
    ScopeReader,
    check,
)
from mvp.tests.evolution.e2e.classifier_provenance_support import held_lock

SCHEMA = 'evolution-classifier-admission-case-v1'
SOURCES = frozenset({'mvp/evolution/evaluator_attempt.py', 'mvp/evolution/evaluator_governance.py',
    'mvp/evolution/evaluator_cases.py', 'mvp/evolution/classifier_admission_measurements.py',
    'mvp/tests/evolution/e2e/classifier_admission_case.py',
    'mvp/tests/evolution/e2e/classifier_admission_support.py',
    'mvp/tests/evolution/e2e/classifier_admission_governance.py',
    'mvp/tests/evolution/e2e/classifier_admission_fault.py',
    'mvp/tests/evolution/e2e/classifier_worker_transition.py'})
FIELDS = {'schema_version','case_id','operation','authorization_sha256','sources','status','phase',
    'measurement','governance','elapsed_ms','failure_type'}


def closed(value):
    evidence.require(type(value) is dict and set(value)==FIELDS and value['schema_version']==SCHEMA)
    evidence.require(value['case_id'] in evidence.CASES and value['status']=='observed'
        and value['phase']=='complete' and value['failure_type'] is None and evidence.sha(value['authorization_sha256']))
    evidence.uuid(value['operation']);evidence.number(value['elapsed_ms'],0,900000)
    evidence.require(type(value['sources']) is dict and SOURCES<=set(value['sources']))
    measurement=value['measurement']
    evidence.require(measurement['case_id']==value['case_id'] and measurement['operation']==value['operation']
        and measurement['authorization_sha256']==value['authorization_sha256'] and measurement['sources']==value['sources'])
    governance=value['governance'];evidence.require(type(governance) is dict and set(governance)==set(evidence.TENANTS))
    for tenant,item in governance.items():
        evidence.require(type(item) is dict and set(item)=={'operation','references','source_sha256','restoration','quota_audit'}
            and item['restoration']=='applied' and type(item['source_sha256']) is dict and item['source_sha256'])
        evidence.uuid(item['operation'])
        evidence.require(set(item['references'])=={'intent.json','release-matrix-release.json','release-restore-original-binding.json','completion.json'})
        for name,ref in item['references'].items():
            evidence.require(type(ref) is dict and set(ref)=={'path','sha256'} and evidence.sha(ref['sha256'])
                and type(ref['path']) is str and Path(ref['path']).is_absolute()
                and Path(ref['path']).name==name and Path(ref['path']).parent.name==item['operation'])
        evidence.require(all(value['sources'].get(k)==v for k,v in item['source_sha256'].items()))
        audit=item['quota_audit']
        if tenant=='team-a' and value['case_id']!=evidence.CASES[0]:
            evidence.require(type(audit) is dict and set(audit)=={'event_id','tenant_id','actor_id','event_type','event_hash','metadata'}
                and audit['tenant_id']==tenant and audit['event_type']=='configuration_created' and evidence.sha(audit['event_hash']))
            evidence.uuid(audit['event_id']);evidence.uuid(audit['actor_id'])
            metadata=audit['metadata'];evidence.require(type(metadata) is dict and set(metadata)=={'resource_type','resource_id','revision'}
                and metadata['resource_type']=='quotas' and type(metadata['revision']) is int and metadata['revision']>=2)
            evidence.uuid(metadata['resource_id'])
        else:evidence.require(audit is None)


def validate(value, *, verify_provenance, deadline):
    """Final consumer callback authenticates parent/current/transition/journal refs."""
    evidence.require(callable(verify_provenance));check(deadline);closed(value)
    original=evidence.digest(value)
    # Inner semantic checks and genuine final authorization share one callback;
    # no local permissive verifier is substituted at either boundary.
    evidence.validate(value['measurement'],verify_provenance=lambda measurement: verify_provenance(
        phase='consume',case_id=value['case_id'],operation=value['operation'],
        measurement=measurement,governance=copy.deepcopy(value['governance']),
        sources=copy.deepcopy(value['sources']),authorization_sha256=value['authorization_sha256'],deadline=deadline))
    evidence.require(evidence.digest(value)==original,'admission_consumer_changed');check(deadline)
    return copy.deepcopy(value)


def run_case_locked(*, case_id, operation, cluster, source_root, sources, operation_root,
                    authorization_sha256, record, verify_provenance, deadline, fault=None):
    """Genuine production ports; the caller owns original lock and durable output.

    ``record(name,value)`` must exclusively persist canonical private evidence
    under the parent-authorized operation. ``verify_provenance`` is mandatory at
    before/authorized/after/restored/consume boundaries and must authenticate the
    exact callback deadline, sources, invocation, journal identities, current
    private state and (for fault only) separately authorized epoch transition.
    The 30s/180s observation cap and original HTTP budgets sit within this 900s
    governance/retention allowance. No timeout grants a fresh deadline.
    """
    evidence.require(case_id in evidence.CASES and callable(record) and callable(verify_provenance))
    evidence.uuid(operation);evidence.require(evidence.sha(authorization_sha256))
    evidence.require((isinstance(fault,OwnedClassifierFault) and fault.auth['operation']==operation)
        if case_id==evidence.CASES[2] else fault is None)
    started=time.monotonic();stop=min(deadline,started+900);check(stop)
    source_root=Path(source_root);evidence.require(source_root.is_absolute() and source_root.resolve()==source_root)
    sources=copy.deepcopy(sources);evidence.require(type(sources) is dict and SOURCES<=set(sources))
    catalog=SourceCatalog(source_root,expected=sources)
    def guards():
        check(stop);held_lock(cluster.state,deadline=stop)
        for name in sorted(SOURCES):
            evidence.require(evidence.sha(sources[name]));catalog.path(name);catalog.load(name);check(stop)
    guards()
    value={'schema_version':SCHEMA,'case_id':case_id,'operation':operation,'authorization_sha256':authorization_sha256,
        'sources':sources,'status':'failed','phase':'intent','measurement':None,'governance':{},'elapsed_ms':0,'failure_type':None}
    governed=None;observed=None;primary=None;sequence=0
    def save():
        nonlocal sequence
        sequence+=1;evidence.require(sequence<=16)
        value['elapsed_ms']=round((time.monotonic()-started)*1000,3)
        record(f'admission-case-{sequence:02d}.json',copy.deepcopy(value))
    def verify(phase, measurement=None):
        guards()
        before=evidence.digest(value)
        evidence.require(verify_provenance(phase=phase,case_id=case_id,operation=operation,
            measurement=copy.deepcopy(measurement),governance=copy.deepcopy(value['governance']),
            sources=copy.deepcopy(sources),authorization_sha256=authorization_sha256,deadline=stop) is True,
            'admission_parent_proof_refused')
        evidence.require(evidence.digest(value)==before,'admission_callback_changed');guards()
        return True
    save()  # exact original intent before governance/client construction
    try:
        verify('before')
        value['phase']='governance';save()
        governed=GovernedAdmission(cluster,operation_root=operation_root,source_root=source_root,sources=sources,case_id=case_id)
        subjects=governed.prepare(deadline=stop)
        verify('authorized')
        value['phase']='observe';save()
        observed=AdmissionObservation(case_id=case_id,operation=operation,sources=sources,subjects=subjects,
            authorization_sha256=authorization_sha256,http=RuntimePort(cluster,deadline=stop),reader=ScopeReader(cluster),
            record=record,verify=lambda measurement:verify('restored',measurement),fault=fault)
        asyncio.run(observed.run(deadline=stop));value['measurement']=copy.deepcopy(observed.value)
        verify('after',value['measurement'])
    except BaseException as error:  # noqa: BLE001 - restore owned bindings even after cancellation or interruption
        primary=error
    finally:
        if governed is not None and governed.started:
            value['phase']='restore'
            try:governed.restore(passed=primary is None,deadline=stop)
            except BaseException as error:  # noqa: BLE001 - retain the primary error while recording cleanup failure
                if primary is None:primary=error
                else:primary.add_note('admission_governance_cleanup_failed')
            value['governance']=copy.deepcopy(governed.receipts)
        if observed is not None:value['measurement']=copy.deepcopy(observed.value)
    if primary is None:
        try:
            evidence.require(observed is not None and governed is not None)
            value['measurement']=observed.complete(restoration='restored')
            value['phase']='complete';value['status']='observed';guards();closed(value);save()
            return copy.deepcopy(value)
        except BaseException as error:primary=error  # noqa: BLE001 - retain failed completion before re-raising
    value['status']='failed'
    name=type(primary).__name__
    value['failure_type']=name if name in {'AdmissionError','EvolutionError','EvidenceError','TimeoutError','OSError'} else 'OtherError'
    try:save()
    except BaseException:primary.add_note('admission_case_retention_failed')  # noqa: BLE001 - preserve primary failure
    raise primary.with_traceback(primary.__traceback__)
