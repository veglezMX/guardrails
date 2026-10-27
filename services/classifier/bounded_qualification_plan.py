"""Authenticated file-only binding for the approved cumulative initial run.

The reviewed parent launcher pins every executable source before importing this
module. Candidate authority is explicit; an arbitrary consistent plan cannot
select another model, corpus set, load receipt or predecessor disposition.
"""
from __future__ import annotations

import os
import re
import stat
import types
from pathlib import Path

from evolution.classifier_provenance import corpus_projection, verify_corpus_manifest
from guardrails.evolution.classifier_contract import canonical, parse

from services.classifier import bounded_qualification_core as core
from services.classifier import bounded_qualification_evidence as evidence
from services.classifier import bounded_qualification_process as process
from services.classifier import modernbert_candidate_02_compat as adapter
from services.classifier import network_isolation
from services.classifier.wolf_full_qualification import (
    FIXED_SOURCES,
    read,
    reference,
    sha,
)

LOAD_HELPER_SHA256='7be68a2ef2946967215d6b4f0d9957cded82b3688d6f301b8cdd311e0c0f7d06'
LOAD_RESULT_SHA256='b4bcce139c531ea8938fce67199d685e6c0f41e4d01ca5b7b556f0b4cfb3402f'
CORPUS_MANIFEST_SHA256='c47d3799b25f72b88fc6273cf437a2a0b090a1bda0e2a6a4a0dbcc5ef3fe98ee'
PRIOR_EVIDENCE={
    'protectai_failed':'86fae5fda7f9315e1e9d6d6c95e76aefda88581d0aa1c48d3133b4c718636425',
    'wolf_small_failed':'798bd6abf3e3e65353136d1ca71374f3e6a1ec6610bd2ea157ad39f272efa2d9',
    'wolf_full_failure_retention':'bafd4183c2f00d1c819ac77a660b5e5725f8a5270ac4511c9989fbb22a301f14',
    'candidate_01_incompatible':'d585a96a0012ba635896124016f50ac101cb73a85a6ca1b2aeb50c26bb5b2f80',
}
MODULE_ORDER=(*process.module_order(2),
    ('evolution','mvp/evolution/__init__.py',True),
    ('evolution.registry','mvp/evolution/registry.py',False),
    ('evolution.classifier_provenance','mvp/evolution/classifier_provenance.py',False),
    ('services.classifier.bounded_qualification_evidence','services/classifier/bounded_qualification_evidence.py',False),
    ('services.classifier.bounded_qualification_plan','services/classifier/bounded_qualification_plan.py',False),
    ('services.classifier.bounded_qualification','services/classifier/bounded_qualification.py',False))
SOURCE_FILES=tuple(path for _,path,_ in MODULE_ORDER if path is not None)
PINNED_DEPENDENCIES={**FIXED_SOURCES,
    'services/classifier/wolf_full_qualification.py':'3526922613946fa7a43ae96e90798716dd3d77488c441e18ef271097938465c0',
    'services/classifier/modernbert_candidate_02_compat.py':'d3dba0b980db99bccbe5cb7d15685e4ae99d31436ebf0b0ed385d197dd704f91',
    'mvp/evolution/__init__.py':'eab7b2cb17c6bad855212498e1576c0e376b9060b9c78af00c8d47ae8d9b55d6',
    'mvp/evolution/registry.py':'10ff1db4417d0afd15a3a2bdcf9d24888219e54d5eeb59082bd85ed7aa5a0621',
    'mvp/evolution/classifier_provenance.py':'a6f216232bf0b3e1dc6b86fec4d0ae5af067bc9114913887c7dc31ddaf63febc'}
PLAN_FIELDS={'schema_version','created_at','selection_index','sources','corpus_manifest','load_only',
    'prior_evidence','model_directory','output','host_namespace'}


def require(value,code):core.require(value,code)


def source_snapshot(root):
    root=Path(root)
    require(root.is_absolute() and root.resolve()==root,'root')
    require(not any(os.path.lexists(root/name) for name in ('services/__init__.py','services/classifier/__init__.py')),'namespace_changed')
    actual={name:sha(read(root/name,maximum=1024*1024)) for name in SOURCE_FILES}
    require(all(actual[name]==pin for name,pin in PINNED_DEPENDENCIES.items()),'fixed_source')
    return actual


def public_reference(ref):
    """Pinned immutable public evidence may be read-only; private inputs stay0600."""
    require(type(ref) is dict and set(ref)=={'path','sha256'} and type(ref['path']) is str,'public_reference')
    core.pinned_hash(ref['sha256']);path=Path(ref['path'])
    require(path.is_absolute() and path.resolve()==path,'public_path')
    def directory_key(row):return row.st_dev,row.st_ino,row.st_mode,row.st_uid
    parents={parent:directory_key(parent.stat(follow_symlinks=False)) for parent in path.parents}
    require(all(stat.S_ISDIR(value[2]) and not value[2]&0o022 for value in parents.values()),'public_parent')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid==os.getuid() and before.st_nlink==1
            and stat.S_IMODE(before.st_mode) in {0o400,0o444,0o600,0o644} and 0<before.st_size<=4*1024*1024,'public_file')
        raw=stream.read(4*1024*1024+1)
        def key(row):return tuple(getattr(row,n) for n in ('st_dev','st_ino','st_mode','st_uid','st_nlink','st_size','st_mtime_ns','st_ctime_ns'))
        require(len(raw)==before.st_size and key(before)==key(os.fstat(stream.fileno()))==key(path.lstat())
            and path.resolve()==path and all(directory_key(parent.stat(follow_symlinks=False))==saved for parent,saved in parents.items())
            and sha(raw)==ref['sha256'],'public_changed')
    return raw


def load_proof(refs, *, host_namespace, model_directory):
    require(type(refs) is dict and set(refs)=={'helper','started','closure','child','cleanup','result'},'load_refs')
    require(refs['helper']['sha256']==LOAD_HELPER_SHA256 and refs['result']['sha256']==LOAD_RESULT_SHA256,'load_authority')
    raw=reference(refs['helper'])
    # Only captured bytes of the independently reviewed exact helper execute.
    helper=types.ModuleType('_bounded_authenticated_load_proof')
    helper.__file__=refs['helper']['path']
    exec(compile(raw,helper.__file__,'exec'),helper.__dict__)  # noqa: S102 - captured independently pinned helper bytes
    captured={str(path):public_reference({'path':str(path),'sha256':pin}) for path,pin in helper.PINS.items()}
    acquired=public_reference({'path':str(helper.ACQUISITION),'sha256':helper.ACQUISITION_SHA})
    require(model_directory==str(helper.MODEL),'model_directory')
    acquisition=parse(acquired,limit=4*1024*1024)
    require(acquisition['manifest_sha256']==adapter.MANIFEST_SHA256 and acquisition['model_directory']==model_directory
            and acquisition['status']=='artifacts_verified_not_qualified' and acquisition['model_loaded'] is False
            and acquisition['model_scored'] is False and not os.path.lexists(helper.ACQUISITION.parent/'failed.json'),'acquisition')
    for name,row in acquisition['files'].items():
        require(name in adapter.FILES and (row['sha256'],row['size_bytes'])==adapter.FILES[name],'artifact_pin')
    require(set(acquisition['files'])==set(adapter.FILES),'artifact_inventory')
    raws={name:reference(refs[name],maximum=16*1024*1024 if name=='closure' else 4*1024*1024)
          for name in ('started','closure','child','cleanup','result')}
    directory=Path(refs['result']['path']).parent
    require(all(Path(refs[name]['path'])==directory/(name+'.json') for name in raws)
            and not os.path.lexists(directory/'failure.json'),'load_directory')
    start,child,result,cleanup=(parse(raws[name],limit=4*1024*1024) for name in ('started','child','result','cleanup'))
    require(set(start)=={'schema_version','helper_sha256','pins','host_namespace','network_observer_sha256',
        'predecessor_helper_sha256','maximum_seconds','scope','affinity_cpus','acquisition_receipt_sha256','candidate_manifest_sha256'}
        and start['schema_version']=='candidate-02-load-only-start-v1' and start['helper_sha256']==LOAD_HELPER_SHA256
        and canonical(start['pins'])==canonical({str(path):pin for path,pin in helper.PINS.items()})
        and start['host_namespace']==host_namespace and start['maximum_seconds']==300,'load_start')
    require(result['schema_version']=='candidate-02-load-only-result-v1' and child['schema_version']=='candidate-02-load-only-child-v1'
        and result['child_sha256']==sha(raws['child']) and result['cleanup_sha256']==sha(raws['cleanup'])
        and result['installed_closure_sha256']==sha(raws['closure']),'load_digest')
    for row in (start,child,result):
        require(row['scope']=='load_only_not_qualified' and row['helper_sha256']==LOAD_HELPER_SHA256
            and row['candidate_manifest_sha256']==adapter.MANIFEST_SHA256
            and row['network_observer_sha256']==helper.PINS[helper.NETWORK]
            and row['predecessor_helper_sha256']==helper.PREDECESSOR_SHA
            and row['acquisition_receipt_sha256']==helper.ACQUISITION_SHA,'load_lineage')
    for row in (child,result):
        require(row['status']=='loaded' and row['compat_sha256']==helper.PINS[helper.COMPAT]
            and row['shared_compat_sha256']==helper.PINS[helper.SHARED]
            and type(row['predictor_calls']) is int and row['predictor_calls']==0
            and all(type(row[name]) is int and row[name]==1 for name in ('tokenizer_encode_calls','native_empty_validation_calls'))
            and evidence.numeric(row['elapsed_ms']) and row['elapsed_ms']<=300000,'load_calls')
    require(result['quality_verified'] is False and result['enabled_model_changed'] is False
        and canonical(child['metadata'])==canonical(result['metadata'])
        and child['resource_admission']==result['resource_admission'],'load_scope')
    helper.validate_admission(result['resource_admission'],start['affinity_cpus'])
    require(set(cleanup)=={'schema_version','group_signal_requested','group_already_absent','leader_reaped','leader_exit_observed','errors'}
        and cleanup['schema_version']=='candidate-02-load-cleanup-v1' and cleanup['leader_reaped'] is True
        and cleanup['leader_exit_observed'] is True and cleanup['errors']==[]
        and type(cleanup['group_signal_requested']) is bool and type(cleanup['group_already_absent']) is bool
        and cleanup['group_signal_requested'] is not cleanup['group_already_absent'],'load_cleanup')
    require(child['network_before']==child['network_after']==result['network_before']==result['network_after']
        and result['network_before']['host_namespace']==host_namespace,'load_network')
    network_isolation.validate(result['network_before'])
    require('ipv4_connect_errno' in result['network_before'] and 'ipv6_connect_errno' in result['network_before'],'load_network_probes')
    closure=parse(raws['closure'],limit=16*1024*1024)
    require(type(result['installed_files']) is int and 0<len(closure)==result['installed_files']<=50000,'load_closure')
    require(all(sha(public_reference({'path':name,'sha256':sha(value)}))==sha(value) for name,value in captured.items()),'load_inputs_changed')
    return result['metadata'],closure


def validate_plan(plan,root,plan_sha256):
    require(type(plan) is dict and set(plan)==PLAN_FIELDS and plan['schema_version']=='bounded-initial-qualification-plan-v1'
        and type(plan['selection_index']) is int and plan['selection_index']==2,'plan_schema')
    core.pinned_hash(plan_sha256);evidence.timestamp(plan['created_at'])
    require(plan['sources']==source_snapshot(root),'source_changed')
    require(type(plan['host_namespace']) is str and re.fullmatch(r'net:\[[0-9]+\]',plan['host_namespace']),'host_namespace')
    for name in ('model_directory','output'):
        path=Path(plan[name]);require(path.is_absolute() and path.resolve()==path,'plan_path')
    require(plan['corpus_manifest']['sha256']==CORPUS_MANIFEST_SHA256,'corpus_manifest_authority')
    manifest=parse(public_reference(plan['corpus_manifest']),limit=4*1024*1024)
    authorization={'created_at':plan['created_at'],'identity':{'model_id':adapter.REPOSITORY,'model_revision':adapter.REVISION,
        'weights_sha256':adapter.WEIGHTS_SHA256},'required_corpora':manifest['corpora']}
    verified=verify_corpus_manifest(plan['corpus_manifest'],authorization=authorization,load=public_reference)
    require(verified['selection_manifest']['sha256']==adapter.MANIFEST_SHA256,'selection_manifest')
    identity={'repository':adapter.REPOSITORY,'revision':adapter.REVISION,'weights_sha256':adapter.WEIGHTS_SHA256,
        'bundle_sha256':sha(canonical(dict(adapter.FILES))),'candidate_manifest_sha256':adapter.MANIFEST_SHA256,
        'selection_index':2,'qualification_only':True,'production_enabled':False}
    names=('original','heldout_1','heldout_2','confirmation_1','confirmation_2')
    spec=core.Specification(2,adapter.MANIFEST_SHA256,CORPUS_MANIFEST_SHA256,canonical(identity),canonical(plan['sources']),
        tuple(core.CorpusPin(name,verified['corpora'][name]['sha256']) for name in names))
    corpus_raws={name:public_reference(verified['corpora'][name]) for name in names}
    documents=core.corpus_documents(corpus_raws,spec,verify_corpus=corpus_projection)
    require(type(plan['prior_evidence']) is dict and set(plan['prior_evidence'])==set(PRIOR_EVIDENCE),'prior_evidence')
    for name,pin in PRIOR_EVIDENCE.items():
        require(plan['prior_evidence'][name]['sha256']==pin,'prior_pin')
        if name in {'wolf_full_failure_retention','candidate_01_incompatible'}:
            public_reference(plan['prior_evidence'][name])
        else:
            reference(plan['prior_evidence'][name])
    loaded,closure=load_proof(plan['load_only'],host_namespace=plan['host_namespace'],model_directory=plan['model_directory'])
    expected=evidence.Expected(spec,plan_sha256,LOAD_RESULT_SHA256,canonical(plan['prior_evidence']),canonical(loaded))
    return expected,corpus_raws,documents,closure
