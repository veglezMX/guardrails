"""Synthetic-only immutable corpus and one-child evidence composition checks."""
from __future__ import annotations

import asyncio
import builtins
import copy
import dataclasses
import socket

import pytest
from guardrails.evolution.classifier_contract import ClassifierError, canonical

from services.classifier import bounded_qualification_core as q


@pytest.fixture(autouse=True)
def no_ml_or_network(monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split('.')[0] in {'torch', 'tokenizers', 'transformers', 'sentencepiece'}:
            pytest.fail('real ML import forbidden')
        return original(name, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail('network forbidden')
    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'bind', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


def fixture(index=2):
    names=('original','heldout_1','heldout_2',*(f'confirmation_{i}' for i in range(1,index+1)))
    documents={}
    for name in names:
        cases=[]
        for n in range(600):
            language='en' if n<400 else 'es'
            positive=n<200 or 400<=n<500
            part={'part_id':f'p{n}','role':'user','text':f'Only plain synthetic content {name} {n}'}
            if name=='original':part['kind']='text'
            cases.append({'case_id':f'metadata-label-{positive}-{n}','language':language,'expected_injection':positive,
                'category':'metadata-category','family_id':f'family{n//10}','split':'heldout','part':part})
        documents[name]={'threshold':.9,'minimum_recall':.9,'maximum_false_positive_rate':.05,'cases':cases}
    raw={name:canonical(value) for name,value in documents.items()}
    spec=q.Specification(index,'a'*64,'b'*64,canonical({'repository':'synthetic/candidate','revision':'c'*40,
        'weights_sha256':'d'*64,'bundle_sha256':'e'*64,'candidate_manifest_sha256':'a'*64,
        'selection_index':index,'qualification_only':True,'production_enabled':False}),
        canonical({'services/classifier/synthetic.py':'f'*64}),tuple(q.CorpusPin(name,q.sha(raw[name])) for name in names))
    return spec,raw,documents


def verify(raw,name):
    return {'scope':'test_only_no_authority','name':name,'sha256':q.sha(raw)}


class Clock:
    def __init__(self):self.now=100.
    def __call__(self):return self.now


class Child:
    def __init__(self,clock):
        self.clock=clock;self.loaded={'scope':'synthetic'};self.attempts=0;self.starts=0;self.closes=0;self.seen=[]
        self.fail_at=None;self.close_error=False;self.extra=None
    async def start(self):self.starts+=1;self.clock.now+=.5
    async def evaluate(self,sequence,text,deadline):
        self.attempts+=1;self.seen.append((sequence,text,deadline));self.clock.now+=.001
        if self.fail_at is not None and sequence>=self.fail_at:raise ClassifierError('evaluator_unavailable')
        n=int(text.rsplit(' ',1)[1]);score=1. if n<200 or 400<=n<500 else 0.
        value={'complete':True,'score':score,'windows':1}
        if self.extra:value.update(self.extra)
        return value
    async def close(self):
        self.closes+=1
        if self.close_error:raise RuntimeError('cleanup')


def run(spec,documents,child,clock,retain,validate_loaded=lambda value:None):
    return asyncio.run(q.score_once(documents,spec,child,retain=retain,validate_loaded=validate_loaded,clock=clock))


@pytest.mark.parametrize('index,count',[(1,2400),(2,3000),(3,3600)])
def test_all_cumulative_rows_one_child_text_only_and_exact_revalidation(index,count):
    spec,raw,documents=fixture(index);calls=[]
    assert q.corpus_documents(raw,spec,verify_corpus=lambda raw,name:calls.append((q.sha(raw),name)))==documents
    assert [name for _,name in calls]==list(documents)
    clock=Clock();child=Child(clock);retained=[]
    rows,startup,loaded,attempts=run(spec,documents,child,clock,retained.append)
    assert (child.starts,child.closes,attempts,len(rows),len(retained))==(1,1,count,count,count)
    assert startup==.5 and loaded=={'scope':'synthetic'}
    assert all(text.startswith('Only plain synthetic content ') and 'metadata' not in text for _,text,_ in child.seen)
    assert [n for n,_,_ in child.seen]==list(range(count))
    assert child.seen[0][2]==pytest.approx(103.4)
    assert q.validate_rows(b''.join(retained),documents,spec)==rows
    summary=q.summarize(rows,spec)
    assert summary['quality_pass'] is True and summary['cases']==count
    assert summary['spanish_enforcement_enabled'] is False
    assert summary['enabled_model_changed'] is False and summary['final_selected_runtime_rerun_required'] is True


@pytest.mark.parametrize('changes',[{'selection_index':0},{'selection_index':4},{'selection_index':True},
    {'selection_manifest_sha256':'bad'},{'corpus_manifest_sha256':'A'*64},{'corpora':()},
    {'identity_json':b'{}'},{'sources_json':canonical({'../escape.py':'a'*64})},
    {'sources_json':canonical({'services/../escape.py':'a'*64})},{'sources_json':b'{}'}])
def test_typed_specification_cannot_broaden_selection_or_source_scope(changes):
    spec,_,_=fixture()
    with pytest.raises((ValueError,ClassifierError)):dataclasses.replace(spec,**changes)


def test_identity_and_source_views_cannot_mutate_immutable_specification():
    spec,_,_=fixture();identity=spec.identity;sources=spec.sources
    identity['production_enabled']=True;sources.clear()
    assert spec.identity['production_enabled'] is False and len(spec.sources)==1
    with pytest.raises(dataclasses.FrozenInstanceError):spec.selection_index=3


@pytest.mark.parametrize('change',['missing','extra','changed','oversize','wrong_type'])
def test_corpus_bytes_refuse_before_authority_callback(change):
    spec,raw,_=fixture();calls=[]
    if change=='missing':raw.pop('original')
    elif change=='extra':raw['foreign']=b'{}'
    elif change=='changed':raw['original']+=b' '
    elif change=='oversize':raw['original']=b'x'*(q.MAXIMUM_REPORT_BYTES+1)
    else:raw['original']='text'
    with pytest.raises(ValueError):q.corpus_documents(raw,spec,verify_corpus=lambda *x:calls.append(x))
    assert calls==[]


@pytest.mark.parametrize('name,mutation',[('original',lambda c:c['part'].pop('kind')),
    ('original',lambda c:c['part'].update(kind='json')),('heldout_1',lambda c:c['part'].update(kind='text')),
    ('heldout_2',lambda c:c['part'].update(unexpected=True)),('confirmation_1',lambda c:c.update(language='fr')),
    ('confirmation_1',lambda c:c.update(expected_injection=1)),('confirmation_1',lambda c:c['part'].update(text=''))])
def test_realistic_original_and_fresh_part_shapes_remain_distinct(name,mutation):
    spec,raw,docs=fixture();mutation(docs[name]['cases'][0]);raw[name]=canonical(docs[name])
    spec=dataclasses.replace(spec,corpora=tuple(q.CorpusPin(pin.name,q.sha(raw[pin.name])) for pin in spec.corpora))
    with pytest.raises(ValueError):q.corpus_documents(raw,spec,verify_corpus=verify)


def test_authority_refusal_precedes_parse_and_score():
    spec,raw,_=fixture()
    def refuse(*args):raise RuntimeError('independent manifest refusal')
    with pytest.raises(RuntimeError,match='manifest refusal'):q.corpus_documents(raw,spec,verify_corpus=refuse)


def test_operational_failure_is_retained_and_never_retried_or_replaced():
    spec,_,docs=fixture(1);clock=Clock();child=Child(clock);child.fail_at=17;retained=[]
    rows,_,_,_=run(spec,docs,child,clock,retained.append)
    assert child.starts==child.closes==1 and len(child.seen)==2400
    assert all(row['error']=='evaluator_unavailable' and row['score'] is None for row in rows[17:])
    assert len(retained)==2400 and q.summarize(rows,spec)['quality_pass'] is False


def test_failed_retention_stops_before_next_case_and_preserves_primary_error():
    spec,_,docs=fixture();clock=Clock();child=Child(clock);child.close_error=True;retained=[]
    def retain(raw):
        if len(retained)==4:raise OSError('durable failure')
        retained.append(raw)
    with pytest.raises(OSError,match='durable failure'):run(spec,docs,child,clock,retain)
    assert len(child.seen)==5 and child.closes==1 and len(retained)==4


def test_cleanup_failure_after_success_is_not_a_success_result():
    spec,_,docs=fixture(1);clock=Clock();child=Child(clock);child.close_error=True
    with pytest.raises(RuntimeError,match='cleanup'):run(spec,docs,child,clock,lambda raw:None)


def test_bad_loaded_identity_refuses_before_dispatch_but_still_closes():
    spec,_,docs=fixture();clock=Clock();child=Child(clock)
    def refuse(value):raise ValueError('wrong load')
    with pytest.raises(ValueError,match='wrong load'):run(spec,docs,child,clock,lambda raw:None,refuse)
    assert child.starts==child.closes==1 and child.seen==[]


def test_caller_mutation_during_loaded_callback_does_not_rewrite_scored_input():
    spec,_,docs=fixture(1);clock=Clock();child=Child(clock)
    def mutate(value):
        value.clear();docs['original']['cases'][0]['part']['text']='MUTATED'
    rows,_,loaded,_=run(spec,docs,child,clock,lambda raw:None,mutate)
    assert child.seen[0][1]=='Only plain synthetic content original 0'
    assert len(rows)==2400 and loaded=={'scope':'synthetic'}


@pytest.fixture
def completed():
    spec,_,docs=fixture();clock=Clock();child=Child(clock);raw=[]
    rows,_,_,_=run(spec,docs,child,clock,raw.append)
    return spec,docs,rows,raw


@pytest.mark.parametrize('change',['missing','extra','reordered','label','score_bool','nan','prediction','windows_bool',
    'late','extra_field','failure_score'])
def test_retained_rows_cannot_launder_missing_or_tampered_observations(completed,change):
    spec,docs,rows,raw=completed
    if change=='missing':raw.pop()
    elif change=='extra':raw.append(raw[0])
    elif change=='reordered':raw[0],raw[1]=raw[1],raw[0]
    else:
        row=copy.deepcopy(rows[0])
        if change=='label':row['expected']=False
        elif change=='score_bool':row['score']=True
        elif change=='nan':raw[0]=raw[0].replace(b'"score":1.0',b'"score":NaN')
        elif change=='prediction':row['predicted']=False
        elif change=='windows_bool':row['windows']=True
        elif change=='late':row['elapsed_ms']=3000.01
        elif change=='extra_field':row['extra']=1
        else:row.update(error='evaluator_unavailable',score=.8);row.pop('windows')
        if change!='nan':raw[0]=canonical(row)+b'\n'
    with pytest.raises((ValueError,ClassifierError)):q.validate_rows(b''.join(raw),docs,spec)


@pytest.mark.parametrize('corpus',['original','heldout_1','heldout_2','confirmation_1','confirmation_2'])
@pytest.mark.parametrize('failure',['recall','false_positive','operational'])
def test_each_english_corpus_is_independently_required(completed,corpus,failure):
    spec,_,rows,_=completed
    selected=[row for row in rows if row['corpus']==corpus and row['language']=='en']
    if failure=='recall':
        for row in selected[:21]:row.update(score=0.,predicted=False)
    elif failure=='false_positive':
        for row in selected[200:211]:row.update(score=1.,predicted=True)
    else:selected[0].update(error='evaluator_unavailable',score=None,predicted=None)
    assert q.summarize(rows,spec)['quality_pass'] is False


def test_spanish_quality_cannot_enable_spanish_or_replace_english(completed):
    spec,_,rows,_=completed
    for row in rows:
        if row['language']=='es':row.update(score=float(not row['expected']),predicted=not row['expected'])
    result=q.summarize(rows,spec)
    assert result['quality_pass'] is True and result['spanish_enforcement_enabled'] is False
    rows[-1].update(error='evaluator_unavailable',score=None,predicted=None)
    assert q.summarize(rows,spec)['quality_pass'] is False
