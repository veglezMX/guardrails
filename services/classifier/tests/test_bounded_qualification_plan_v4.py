"""Real bounded file reads and captured helper execution with synthetic authority."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest
from guardrails.evolution.classifier_contract import canonical

from services.classifier import bounded_qualification_plan_v4 as p
from services.classifier.tests.test_bounded_qualification_core import (
    no_ml_or_network,
)
from services.classifier.tests.test_bounded_qualification_evidence import network
from services.classifier.tests.test_bounded_qualification_extension import fixture

__all__=['no_ml_or_network']


def put(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    raw=value if type(value) is bytes else canonical(value)
    path.write_bytes(raw);path.chmod(0o600)
    return {'path':str(path),'sha256':p.sha(raw)}


@pytest.fixture
def bound(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir(mode=0o700)
    for name in p.SOURCE_FILES:put(root/name,b'# synthetic authenticated source\n')
    monkeypatch.setattr(p,'PINNED_DEPENDENCIES',{})
    actual=p.source_snapshot(root)
    model=tmp_path/'model';model.mkdir(mode=0o700)
    helper=tmp_path/'helper.py';load=tmp_path/'load';load.mkdir(mode=0o700)
    files={name:(p.sha(name.encode()),len(name)) for name in p.adapter.FILES}
    monkeypatch.setattr(p.adapter,'FILES',files)
    pins={}
    for name in ('compat','shared','network','review','environment'):
        row=put(tmp_path/(name+'.py'),b'# synthetic '+name.encode());pins[row['path']]=row['sha256']
    acquisition=put(tmp_path/'acquisition.json',{'manifest_sha256':p.adapter.MANIFEST_SHA256,
        'model_directory':str(model),'status':'artifacts_verified_not_qualified','model_loaded':False,'model_scored':False,
        'files':{name:{'sha256':pin,'size_bytes':size} for name,(pin,size) in files.items()}})
    source=f'''from pathlib import Path
PINS={{Path(name):pin for name,pin in {pins!r}.items()}}
MODEL=Path({str(model)!r})
ACQUISITION=Path({acquisition['path']!r})
ACQUISITION_SHA={acquisition['sha256']!r}
COMPAT=Path({str(tmp_path/'compat.py')!r})
SHARED=Path({str(tmp_path/'shared.py')!r})
NETWORK=Path({str(tmp_path/'network.py')!r})
PREDECESSOR_SHA={'2'*64!r}
def validate_admission(value,cpus):
    if value!={{'affinity_cpus':cpus,'admitted':True}}:raise ValueError('synthetic_admission')
'''.encode()
    helper_ref=put(helper,source);monkeypatch.setattr(p,'LOAD_HELPER_SHA256',helper_ref['sha256'])
    lineage={'scope':'load_only_not_qualified','helper_sha256':helper_ref['sha256'],
        'candidate_manifest_sha256':p.adapter.MANIFEST_SHA256,'network_observer_sha256':pins[str(tmp_path/'network.py')],
        'predecessor_helper_sha256':'2'*64,'acquisition_receipt_sha256':acquisition['sha256']}
    start={'schema_version':'candidate-04-load-only-start-v1',**lineage,'pins':pins,'host_namespace':'net:[1]',
        'maximum_seconds':300,'affinity_cpus':[0,1]}
    metadata={'repository':p.adapter.REPOSITORY,'revision':p.adapter.REVISION,'weights_sha256':p.adapter.WEIGHTS_SHA256,
        'qualification_only':True,'quality_verified':False,'scoring_requests':0,'native_empty_validation_calls':1}
    child={'schema_version':'candidate-04-load-only-child-v1',**lineage,'status':'loaded',
        'compat_sha256':pins[str(tmp_path/'compat.py')],'shared_compat_sha256':pins[str(tmp_path/'shared.py')],
        'predictor_calls':0,'tokenizer_encode_calls':1,'native_empty_validation_calls':1,'elapsed_ms':100,
        'metadata':metadata,'resource_admission':{'affinity_cpus':[0,1],'admitted':True},
        'network_before':network(),'network_after':network()}
    cleanup={'schema_version':'candidate-04-load-cleanup-v1','group_signal_requested':True,'group_already_absent':False,
        'leader_reaped':True,'leader_exit_observed':True,'errors':[]}
    refs={'helper':helper_ref,'started':put(load/'started.json',start),'child':put(load/'child.json',child),
        'cleanup':put(load/'cleanup.json',cleanup),'closure':put(load/'closure.json',{'synthetic':{'sha256':'a'*64}})}
    result={**child,'schema_version':'candidate-04-load-only-result-v1','quality_verified':False,'enabled_model_changed':False,
        'child_sha256':refs['child']['sha256'],'cleanup_sha256':refs['cleanup']['sha256'],
        'installed_closure_sha256':refs['closure']['sha256'],'installed_files':1}
    refs['result']=put(load/'result.json',result);monkeypatch.setattr(p,'LOAD_RESULT_SHA256',refs['result']['sha256'])
    _,raws,_=fixture(4);corpora={name:put(tmp_path/(name+'.json'),raw) for name,raw in raws.items()}
    manifest={'corpora':corpora,'selection_manifest':{'sha256':p.adapter.MANIFEST_SHA256}}
    manifest_ref=put(tmp_path/'manifest.json',manifest);monkeypatch.setattr(p,'CORPUS_MANIFEST_SHA256',manifest_ref['sha256'])
    authority_calls=[]
    def verify_manifest(ref,*,authorization,load):
        authority_calls.append(copy.deepcopy(authorization))
        assert load(ref)==canonical(manifest)
        return copy.deepcopy(manifest)
    monkeypatch.setattr(p,'verify_corpus_manifest',verify_manifest)
    monkeypatch.setattr(p,'corpus_projection',lambda raw,name:{'synthetic_only':name})
    prior={name:put(tmp_path/(name+'.json'),{'immutable':'synthetic','name':name}) for name in p.PRIOR_EVIDENCE}
    for name in ('wolf_full_failure_retention','candidate_01_incompatible','candidate_02_failed','candidate_03_failed'):
        Path(prior[name]['path']).chmod(0o644)
    monkeypatch.setattr(p,'PRIOR_EVIDENCE',{name:ref['sha256'] for name,ref in prior.items()})
    plan={'schema_version':'bounded-extended-initial-qualification-plan-v1','created_at':'2026-10-12T16:00:00+00:00',
        'selection_index':4,'sources':actual,'corpus_manifest':manifest_ref,'load_only':refs,'prior_evidence':prior,
        'model_directory':str(model),'output':str(tmp_path/'quality'),'host_namespace':'net:[1]'}
    return root,plan,authority_calls


def test_real_file_closure_captured_helper_and_required_corpus_authority(bound):
    root,plan,calls=bound
    expected,raws,documents,closure=p.validate_plan(plan,root,'9'*64)
    assert expected.specification.case_count==4200 and len(raws)==len(documents)==7 and len(closure)==1
    assert expected.loaded_json and len(calls)==1 and calls[0]['identity']['model_id']==p.adapter.REPOSITORY
    assert calls[0]['required_corpora'].keys()==raws.keys()
    assert expected.specification.approval_chain == {'original':p.core.ORIGINAL_APPROVAL_SHA256,
        'extension':p.core.EXTENSION_APPROVAL_SHA256}
    assert tuple(raws)==('original','heldout_1','heldout_2','confirmation_1','confirmation_2','confirmation_3','confirmation_4')


@pytest.mark.parametrize('change',['source','missing_source','namespace','helper','result','child','cleanup','closure',
    'failed_load','acquisition','prior','corpus','manifest','model','host','selection','extra'])
def test_drift_or_failed_lineage_refuses_before_any_model_boundary(bound,change):
    root,plan,_=bound
    if change=='source':put(root/p.SOURCE_FILES[0],b'changed')
    elif change=='missing_source':(root/p.SOURCE_FILES[0]).unlink()
    elif change=='namespace':put(root/'services/__init__.py',b'foreign')
    elif change in {'helper','result','child','cleanup','closure'}:put(Path(plan['load_only'][change]['path']),b'{}')
    elif change=='failed_load':put(Path(plan['load_only']['result']['path']).parent/'failure.json',b'{}')
    elif change=='acquisition':put(root.parent/'acquisition.json',b'{}')
    elif change=='prior':put(Path(next(iter(plan['prior_evidence'].values()))['path']),b'{}')
    elif change=='corpus':put(root.parent/'confirmation_2.json',b'{}')
    elif change=='manifest':plan['corpus_manifest']['sha256']='0'*64
    elif change=='model':plan['model_directory']=str(root)
    elif change=='host':plan['host_namespace']='net:[999]'
    elif change=='selection':plan['selection_index']=1
    else:plan['extra']=True
    with pytest.raises((ValueError,OSError)):p.validate_plan(plan,root,'9'*64)


@pytest.mark.parametrize('kind',['fifo','symlink','mode','hardlink'])
def test_authenticated_helper_reader_refuses_unsafe_replacements(bound,kind):
    root,plan,_=bound;path=Path(plan['load_only']['helper']['path'])
    if kind=='fifo':path.unlink();os.mkfifo(path,0o600)
    elif kind=='symlink':path.rename(path.with_suffix('.old'));path.symlink_to(path.with_suffix('.old'))
    elif kind=='mode':path.chmod(0o666)
    else:os.link(path,path.with_suffix('.link'))
    with pytest.raises((ValueError,OSError)):p.validate_plan(plan,root,'9'*64)


def test_independent_manifest_refusal_propagates_without_loading_helper(bound,monkeypatch):
    root,plan,_=bound
    def refuse(*args,**kwargs):raise ValueError('corpus_authority')
    def forbidden(*args,**kwargs):pytest.fail('load evidence checked after manifest refusal')
    monkeypatch.setattr(p,'verify_corpus_manifest',refuse);monkeypatch.setattr(p,'load_proof',forbidden)
    with pytest.raises(ValueError,match='corpus_authority'):p.validate_plan(plan,root,'9'*64)


@pytest.mark.parametrize('mode',[0o400,0o444,0o600,0o644])
def test_exact_hash_public_evidence_allows_original_readonly_mode(tmp_path,mode):
    path=tmp_path/'frozen.json';ref=put(path,b'{"immutable":true}\n');path.chmod(mode)
    assert p.public_reference(ref)==b'{"immutable":true}\n'
    assert path.stat().st_mode&0o777==mode


@pytest.mark.parametrize('mode',[0o666,0o660,0o664,0o640,0o755,0o777])
def test_public_evidence_permission_allowlist_stays_closed(tmp_path,mode):
    path=tmp_path/'frozen.json';ref=put(path,b'{}');path.chmod(mode)
    with pytest.raises(ValueError):p.public_reference(ref)


@pytest.mark.parametrize('change',['pin','absent_pin','extra','fifo','symlink','hardlink'])
def test_readonly_does_not_excuse_missing_authority_or_unsafe_identity(tmp_path,change):
    path=tmp_path/'frozen.json';ref=put(path,b'{}');path.chmod(0o444)
    if change=='pin':ref['sha256']='0'*64
    elif change=='absent_pin':ref.pop('sha256')
    elif change=='extra':ref['trusted']=True
    elif change=='fifo':path.unlink();os.mkfifo(path,0o600)
    elif change=='symlink':path.rename(tmp_path/'old');path.symlink_to(tmp_path/'old')
    else:os.link(path,tmp_path/'other')
    with pytest.raises((ValueError,OSError)):p.public_reference(ref)


def test_actual_private_results_and_public_parent_summaries_keep_distinct_modes(bound):
    root,plan,_=bound
    before={name:Path(ref['path']).stat().st_mode&0o777 for name,ref in plan['prior_evidence'].items()}
    assert before=={'protectai_failed':0o600,'wolf_small_failed':0o600,
        'wolf_full_failure_retention':0o644,'candidate_01_incompatible':0o644,'candidate_02_failed':0o644,'candidate_03_failed':0o644}
    p.validate_plan(plan,root,'9'*64)
    assert {name:Path(ref['path']).stat().st_mode&0o777 for name,ref in plan['prior_evidence'].items()}==before


@pytest.mark.parametrize('name',['wolf_full_failure_retention','candidate_01_incompatible','candidate_02_failed','candidate_03_failed'])
@pytest.mark.parametrize('change',['wrong_pin','unsafe_mode'])
def test_public_parent_summary_exception_requires_exact_authority_and_safe_mode(bound,name,change):
    root,plan,_=bound
    if change=='wrong_pin':plan['prior_evidence'][name]['sha256']='0'*64
    else:Path(plan['prior_evidence'][name]['path']).chmod(0o666)
    with pytest.raises(ValueError):p.validate_plan(plan,root,'9'*64)


@pytest.mark.parametrize('name',['protectai_failed','wolf_small_failed'])
def test_original_private_results_do_not_inherit_public_summary_permission(bound,name):
    root,plan,_=bound;Path(plan['prior_evidence'][name]['path']).chmod(0o644)
    with pytest.raises(ValueError,match='unsafe_file'):p.validate_plan(plan,root,'9'*64)


def test_executable_closure_requires_versioned_authority_and_omits_other_candidate_code():
    assert 'mvp/evolution/classifier_corpus_extension.py' in p.SOURCE_FILES
    assert 'mvp/evolution/classifier_provenance.py' in p.SOURCE_FILES
    assert 'services/classifier/horizon_candidate_04_compat.py' in p.SOURCE_FILES
    assert not any('shieldlm_compat' in name or 'piguard_candidate' in name for name in p.SOURCE_FILES)
    assert p.PINNED_DEPENDENCIES['mvp/evolution/classifier_provenance.py']=='a6f216232bf0b3e1dc6b86fec4d0ae5af067bc9114913887c7dc31ddaf63febc'
