"""Complete synthetic cumulative reports; never read a real scored corpus."""
from __future__ import annotations

import copy

import pytest
from guardrails.evolution.classifier_contract import canonical

from services.classifier import bounded_qualification_evidence as e
from services.classifier.tests.test_bounded_qualification_core import (
    Child,
    Clock,
    fixture,
    no_ml_or_network,
    run,
    verify,
)

__all__=['no_ml_or_network']


def network():
    return {'schema_version':'classifier-offline-namespace-v1','namespace':'net:[2]','host_namespace':'net:[1]',
        'interfaces':['lo'],'links':[{'name':'lo','index':1,'hardware_type':772,'flags':8,'kind':None,
        'operstate':2,'carrier':1,'link_index':0,'master_index':0,'foreign_namespace_link':False,'tunnel':None,
        'address_count':0,'route_count':0}],'external_routes':0,'ipv4_connect_errno':101,'ipv6_connect_errno':99}


def report(index=2):
    spec,raws,documents=fixture(index);clock=Clock();child=Child(clock);retained=[]
    loaded={'qualification_only':True,'quality_verified':False,'scoring_requests':0,
        **{k:spec.identity[k] for k in ('repository','revision','weights_sha256')}}
    child.loaded=loaded
    rows,startup,_,attempts=run(spec,documents,child,clock,retained.append)
    prior={name:{'path':'/private/'+name,'sha256':'7'*64} for name in ('old1','old2','old3')}
    expected=e.Expected(spec,'8'*64,'9'*64,canonical(prior),canonical(loaded))
    started=e.started(expected,network(),'2026-10-12T16:00:00+00:00');cases=b''.join(retained)
    cpu={'child_user_seconds':2.,'child_system_seconds':.1,'child_max_rss_kib':1024,
        'torch_threads':2,'torch_interop_threads':1,'affinity':[0,1]}
    result={**started,**e.core.summarize(rows,spec),'status':'completed','completed_at':'2026-10-12T16:01:00+00:00',
        'cases_sha256':e.core.sha(cases),'startup_seconds':startup,'loaded':loaded,'cpu':cpu,
        'resources_pass':True,'network_after':network(),'cost':e.cost(rows,attempts)}
    return expected,raws,documents,started,result,cases


def check(value,require_quality=True):
    expected,raws,_,start,result,cases=value
    return e.validate_report(raws,canonical(start),canonical(result),cases,expected,verify_corpus=verify,require_quality=require_quality)


@pytest.mark.parametrize('index,count',[(1,2400),(2,3000),(3,3600)])
def test_complete_rows_are_recomputed_and_initial_scope_never_enables(index,count):
    value=check(report(index))
    assert value['status']=='qualified_initial_only' and value['cases']==count
    assert not value['production_enabled'] and not value['spanish_enforcement_enabled']
    assert value['final_selected_runtime_rerun_required'] is True


@pytest.mark.parametrize('corpus',['original','heldout_1','heldout_2','confirmation_1','confirmation_2'])
def test_each_english_corpus_must_pass_independently(corpus):
    value=list(report());expected,_,documents,_,result,cases=value
    rows=e.core.validate_rows(cases,documents,expected.specification)
    changed=0
    for row in rows:
        if row['corpus']==corpus and row['language']=='en' and not row['expected'] and changed<11:
            row.update(score=.9,predicted=True);changed+=1
    raw=b''.join(canonical(row)+b'\n' for row in rows);value[-1]=raw
    result.update(e.core.summarize(rows,expected.specification),cases_sha256=e.core.sha(raw))
    assert check(value,False)['status']=='failed_initial'
    with pytest.raises(ValueError,match='english_quality_failed'):check(value)


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(extra=True),lambda r:r.pop('cost'),lambda r:r.update(cases=600),
    lambda r:r.update(threshold=.89),lambda r:r.update(retry_requests=1),lambda r:r.update(warmup_requests=1),
    lambda r:r.update(model_reloads=1),lambda r:r.update(enabled_model_changed=True),
    lambda r:r.update(spanish_enforcement_enabled=True),lambda r:r.update(final_selected_runtime_rerun_required=False),
    lambda r:r.update(cases_sha256='0'*64),lambda r:r['loaded'].update(quality_verified=True),
    lambda r:r['sources'].clear(),lambda r:r['corpora'].pop('confirmation_2'),
    lambda r:r.update(completed_at='2020-01-01T00:00:00+00:00'),lambda r:r['cost'].update(attempted_dispatches=2999),
    lambda r:r['cost'].update(forward_passes_including_failed_cases=3000),lambda r:r['cpu'].update(torch_threads=True),
    lambda r:r['cpu'].update(affinity=[0,0]),lambda r:r['cpu'].update(child_user_seconds=float('inf')),
    lambda r:r['network_after'].update(ipv4_connect_errno=99),lambda r:r['network_after'].update(namespace='net:[1]'),
])
def test_forged_summary_scope_lineage_resources_or_namespace_refuses(mutation):
    value=list(report());value[4]=copy.deepcopy(value[4]);mutation(value[4])
    with pytest.raises((ValueError,KeyError)):check(value)


@pytest.mark.parametrize('kind',['missing','order','score','label','suffix','extra'])
def test_report_hash_cannot_substitute_for_all_original_rows(kind):
    value=list(report());lines=value[-1].splitlines()
    if kind=='missing':lines.pop()
    elif kind=='order':lines[0],lines[1]=lines[1],lines[0]
    elif kind=='suffix':lines[-1]=b'{}'
    elif kind=='extra':lines.append(lines[-1])
    else:
        row=e.core.parse(lines[0]);row['score' if kind=='score' else 'expected']=False;lines[0]=canonical(row)
    value[-1]=b'\n'.join(lines)+b'\n';value[4]['cases_sha256']=e.core.sha(value[-1])
    with pytest.raises(ValueError):check(value)


def test_resource_failure_is_retained_but_never_accepted():
    value=report();value[4]['cpu']['child_max_rss_kib']=3072*1024+1;value[4]['resources_pass']=False
    assert check(value,False)['status']=='failed_initial'
    with pytest.raises(ValueError,match='resource_limit_exceeded'):check(value)


def test_missing_independent_corpus_authority_is_not_a_validation_bypass():
    expected,raws,_,start,result,cases=report()
    with pytest.raises(ValueError):e.validate_report(raws,canonical(start),canonical(result),cases,expected,verify_corpus=None)
