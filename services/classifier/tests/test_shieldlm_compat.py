"""Candidate01 source/fake checks; real ML imports and network are forbidden."""
from __future__ import annotations

import builtins
import hashlib
import json
import os
import socket
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from services.classifier import shieldlm_compat as compat

CONFIG = Path(__file__).parents[3] / 'mvp/tests/evolution/fixtures/shieldlm-candidate-01-config.json'


@pytest.fixture(autouse=True)
def no_real_ml_or_network(monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split('.')[0] in {'torch', 'transformers', 'tokenizers', 'sentencepiece'}:
            pytest.fail('real ML import forbidden')
        return original(name, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail('network forbidden')
    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'bind', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


def config_class(**kw):
    return SimpleNamespace(**kw, num_labels=2, id2label={0:'LABEL_0',1:'LABEL_1'},label2id={'LABEL_0':0,'LABEL_1':1})


def test_config_only_adapts_dtype_spelling_and_preserves_unknown_export_metadata():
    raw=CONFIG.read_bytes(); original=json.loads(raw); calls=[]
    def factory(**kw):
        calls.append(kw); return config_class(**kw)
    config=compat.make_config(raw,factory)
    assert len(calls)==1 and config.num_labels==2
    assert calls[0]=={**{k:v for k,v in original.items() if k!='dtype'},'torch_dtype':'float32'}
    assert config.id2label=={0:'LABEL_0',1:'LABEL_1'} and CONFIG.read_bytes()==raw
    assert compat.FILES['README.md'][0]=='f7bc6d85e7b7b83bcc6e9e06c6bf4126ed098cd7baaa918f1893ae59b93355bf'


@pytest.mark.parametrize('field,value', [('pooler_hidden_act','tanh'),('dtype','float16'),('hidden_size',384),
    ('num_hidden_layers',24),('max_position_embeddings',1024),('pos_att_type',['c2p']),('position_buckets',128),
    ('position_biased_input',True),('relative_attention',False),('share_att_key',False),
    ('id2label',{'0':'attack','1':'benign'}),('auto_map',{'AutoModel':'foreign'})])
def test_changed_model_metadata_refuses_before_factory(field,value):
    raw=json.loads(CONFIG.read_bytes());raw[field]=value
    with pytest.raises(compat.ShieldCompatibilityError):
        compat.make_config(json.dumps(raw).encode(),lambda **kw:pytest.fail('constructed unpinned config'))


@pytest.mark.parametrize('field,value', [('position_biased_input',True),('share_att_key',False),('position_buckets',128),
    ('num_labels',1),('id2label',{0:'attack',1:'benign'}),('label2id',None),('conv_kernel_size',3)])
def test_actual_config_must_consume_all_semantic_fields(field,value):
    def factory(**kw):
        config=config_class(**kw);setattr(config,field,value);return config
    with pytest.raises(compat.ShieldCompatibilityError):compat.make_config(CONFIG.read_bytes(),factory)


class Component:
    def __init__(self,value):self.value=json.loads(json.dumps(value))
    def __getstate__(self):return json.dumps(self.value).encode()


def added_tokens():
    return [{'id':number,'content':token,'single_word':False,'lstrip':False,'rstrip':False,'normalized':False,'special':True}
            for token,number in compat.SPECIAL_IDS.items()]


class Backend:
    def __init__(self,tokens=None):
        self.model=type('Unigram',(),{})()
        for name,value in compat.TOKENIZER_COMPONENTS.items():setattr(self,name,Component(value))
        self.truncation={'direction':'right','max_length':512,'strategy':'longest_first','stride':0}
        self.padding={'length':512,'direction':'right','pad_to_multiple_of':None,'pad_id':0,'pad_type_id':0,'pad_token':'[PAD]'}
        self.added={row['id']:SimpleNamespace(**{k:v for k,v in row.items() if k!='id'}) for row in added_tokens()}
        self.ids=dict(compat.SPECIAL_IDS);self.tokens=[11,12] if tokens is None else tokens;self.calls=[]
    def get_vocab_size(self,*,with_added_tokens):return 128001 if with_added_tokens else 128000
    def get_added_tokens_decoder(self):return self.added
    def token_to_id(self,token):return self.ids[token]
    def num_special_tokens_to_add(self,pair):assert pair is False;return 0
    def no_truncation(self):self.calls.append('no_truncation');self.truncation=None
    def no_padding(self):self.calls.append('no_padding');self.padding=None
    def encode(self,text,*,add_special_tokens):
        self.calls.append((text,add_special_tokens));return SimpleNamespace(ids=self.tokens.copy())


def test_native_zero_special_template_preserves_original_text_and_all_flags():
    backend=Backend();before={k:vars(v).copy() for k,v in backend.added.items()}
    tokenizer=compat.ShieldTokenizer(backend,added_tokens())
    assert backend.calls==['no_truncation','no_padding']
    assert tokenizer.encode('  Original\tText  ')==[11,12]
    assert backend.calls[-1]==('  Original\tText  ',False)
    assert tokenizer.num_special_tokens_to_add(pair=False)==0
    assert tokenizer.build_inputs_with_special_tokens([11,12])==[11,12]
    assert before=={k:vars(v) for k,v in backend.added.items()}


@pytest.mark.parametrize('component',list(compat.TOKENIZER_COMPONENTS))
def test_native_component_change_refuses(component):
    backend=Backend();getattr(backend,component).value['unexpected']=True
    with pytest.raises(compat.ShieldCompatibilityError):compat.ShieldTokenizer(backend,added_tokens())


@pytest.mark.parametrize('change',['wrong_model','missing_token','extra_token','mask_lstrip','mask_normalized','wrong_cls',
    'wrong_vocab','wrong_special_count','missing_truncation','missing_padding','left_padding','wrong_padding_id'])
def test_native_backend_state_cannot_silently_fall_back(change):
    b=Backend()
    if change=='wrong_model':b.model=object()
    elif change=='missing_token':del b.added[128000]
    elif change=='extra_token':b.added[999]=b.added[0]
    elif change=='mask_lstrip':b.added[128000].lstrip=True
    elif change=='mask_normalized':b.added[128000].normalized=True
    elif change=='wrong_cls':b.ids['[CLS]']=2
    elif change=='wrong_vocab':b.get_vocab_size=lambda **kw:128100
    elif change=='wrong_special_count':b.num_special_tokens_to_add=lambda pair:2
    elif change=='missing_truncation':b.truncation=None
    elif change=='missing_padding':b.padding=None
    elif change=='left_padding':b.padding['direction']='left'
    else:b.padding['pad_id']=1
    with pytest.raises(compat.ShieldCompatibilityError):compat.ShieldTokenizer(b,added_tokens())
    assert b.calls==[]


@pytest.mark.parametrize('field,value',[('direction','Right'),('direction','left'),('strategy','LongestFirst'),
    ('strategy','only_first'),('max_length',256),('stride',1),('unexpected',True)])
def test_native_api_lowercase_truncation_exact_before_disable(field,value):
    b=Backend();b.truncation[field]=value
    with pytest.raises(compat.ShieldCompatibilityError):compat.ShieldTokenizer(b,added_tokens())


@pytest.mark.parametrize('method',['encode','num_special_tokens_to_add','build_inputs_with_special_tokens'])
@pytest.mark.parametrize('field',['truncation','padding'])
def test_reenabled_truncation_or_padding_refuses_every_operation(method,field):
    b=Backend();t=compat.ShieldTokenizer(b,added_tokens());setattr(b,field,{'enabled':True})
    args={'encode':('text',),'num_special_tokens_to_add':(),'build_inputs_with_special_tokens':([11],)}
    with pytest.raises(compat.ShieldCompatibilityError):getattr(t,method)(*args[method])
    assert b.calls==['no_truncation','no_padding']


@pytest.mark.parametrize('tokens',[[],[True],[-1],[128001],[1.5],(1,),[1]*513])
def test_bad_window_and_empty_encoding_are_not_scored(tokens):
    t=compat.ShieldTokenizer(Backend(),added_tokens())
    with pytest.raises(compat.ShieldCompatibilityError):t.build_inputs_with_special_tokens(tokens)


@pytest.mark.parametrize('length,expected',[(1,1),(512,1),(513,2),(768,2),(769,3),(3000,11),(4352,16)])
def test_complete_native_window_coverage_no_synthetic_tokens(length,expected):
    b=Backend(list(range(length)));t=compat.ShieldTokenizer(b,added_tokens())
    jobs=compat.window_jobs([{'text':'synthetic full document'}],t,time.monotonic()+5)
    assert len(jobs)==expected
    assert [tokens[0] for _,tokens in jobs]==list(range(0,expected*256,256))
    assert jobs[-1][1][-1]==length-1
    assert set().union(*(set(tokens) for _,tokens in jobs))==set(range(length))
    assert b.calls==['no_truncation','no_padding',('synthetic full document',False)]


@pytest.mark.parametrize('length',[0,4353,5000,65537])
def test_incomplete_or_empty_native_coverage_refuses_before_any_forward(length):
    from guardrails.evolution.classifier_contract import ClassifierError
    t=compat.ShieldTokenizer(Backend(list(range(length))),added_tokens())
    with pytest.raises((ClassifierError,compat.ShieldCompatibilityError)):
        compat.window_jobs([{'text':'synthetic'}],t,time.monotonic()+5)


def test_aggregate_maximum_applies_across_all_parts():
    from guardrails.evolution.classifier_contract import ClassifierError
    t=compat.ShieldTokenizer(Backend([10]),added_tokens())
    with pytest.raises(ClassifierError,match='inspection_incomplete'):
        compat.window_jobs([{'text':'synthetic'}]*17,t,time.monotonic()+5)


def test_window_deadline_includes_tokenization_before_first_forward(monkeypatch):
    from guardrails.evolution.classifier_contract import ClassifierError
    t=compat.ShieldTokenizer(Backend([10]),added_tokens());clock=iter([1.,3.])
    monkeypatch.setattr(compat.time,'monotonic',lambda:next(clock))
    with pytest.raises(ClassifierError,match='evaluation_timeout'):compat.window_jobs([{'text':'synthetic'}],t,2.)


@pytest.mark.parametrize('raw',[b'{}',b'',b'x','not-bytes'])
def test_unpinned_tokenizer_never_constructed(raw):
    class Never:
        @staticmethod
        def from_str(_):pytest.fail('native construction forbidden')
    with pytest.raises(compat.ShieldCompatibilityError):compat.make_tokenizer(raw,Never)


def test_wrong_python_refuses_before_model_or_files(monkeypatch):
    monkeypatch.setattr(compat.sys,'version_info',(3,14,0))
    monkeypatch.setattr(compat,'verify_bundle',lambda _:pytest.fail('no bundle read'))
    with pytest.raises(compat.ShieldCompatibilityError):compat.load_for_qualification('/not-used')


@pytest.mark.parametrize('name',['torch','transformers','tokenizers'])
def test_preimported_ml_refuses_before_model(monkeypatch,name):
    monkeypatch.setattr(compat.sys,'version_info',(3,13,16));monkeypatch.setitem(compat.sys.modules,name,ModuleType(name))
    monkeypatch.setattr(compat,'verify_bundle',lambda _:pytest.fail('no bundle read'))
    with pytest.raises(compat.ShieldCompatibilityError):compat.load_for_qualification('/not-used')


def modules(monkeypatch,tmp_path):
    package=tmp_path/'transformers';rows=[];checks=[]
    monkeypatch.setattr(compat,'_read_pinned',lambda p,e:checks.append((p,e)))
    for stem in ('configuration_deberta_v2','modeling_deberta_v2'):
        name='transformers.models.deberta_v2.'+stem;m=ModuleType(name)
        m.__file__=str(package/'models/deberta_v2'/(stem+'.py'));rows.append(m);monkeypatch.setitem(compat.sys.modules,name,m)
    cfg=type('DebertaV2Config',(),{});model=type('DebertaV2ForSequenceClassification',(),{})
    cfg.__module__,model.__module__=[m.__name__ for m in rows]
    rows[0].DebertaV2Config=rows[1].DebertaV2Config=cfg;rows[1].DebertaV2ForSequenceClassification=model
    return package,rows,cfg,model,checks


def test_loaded_modules_bind_actual_authenticated_paths_and_class_objects(monkeypatch,tmp_path):
    package,_rows,cfg,model,checks=modules(monkeypatch,tmp_path)
    compat.verify_imported_modules(package,cfg,model)
    assert len(checks)==2


@pytest.mark.parametrize('fault',['config_path','model_path','no_file','config_class','model_class','model_config','class_module'])
def test_shadowed_modules_cannot_reach_pretrained_load(monkeypatch,tmp_path,fault):
    package,rows,cfg,model,_=modules(monkeypatch,tmp_path)
    if fault=='config_path':rows[0].__file__+='.foreign'
    elif fault=='model_path':rows[1].__file__+='.foreign'
    elif fault=='no_file':del rows[0].__file__
    elif fault=='config_class':rows[0].DebertaV2Config=object()
    elif fault=='model_class':rows[1].DebertaV2ForSequenceClassification=object()
    elif fault=='model_config':rows[1].DebertaV2Config=object()
    else:model.__module__='foreign.module'
    with pytest.raises(compat.ShieldCompatibilityError):compat.verify_imported_modules(package,cfg,model)


def tiny_bundle(monkeypatch,tmp_path):
    root=tmp_path/'model';root.mkdir(mode=0o700);pins={}
    for name in compat.FILES:
        raw=('synthetic:'+name).encode();p=root/name;p.write_bytes(raw);p.chmod(0o600)
        pins[name]=(hashlib.sha256(raw).hexdigest(),len(raw))
    monkeypatch.setattr(compat,'FILES',pins);return root


def test_exact_five_file_bundle_retains_only_config_and_tokenizer(monkeypatch,tmp_path):
    root=tiny_bundle(monkeypatch,tmp_path)
    assert compat.verify_bundle(root)=={n:('synthetic:'+n).encode() for n in ('config.json','tokenizer.json')}


@pytest.mark.parametrize('change',['extra','missing','changed_weights','symlink','hardlink','world_writable','fifo'])
def test_public_bundle_refuses_unsafe_or_incomplete_closure(monkeypatch,tmp_path,change):
    root=tiny_bundle(monkeypatch,tmp_path);target=root/'model.safetensors'
    if change=='extra':(root/'training_args.bin').write_bytes(b'foreign')
    elif change=='missing':target.unlink()
    elif change=='changed_weights':target.write_bytes(b'foreign')
    elif change=='symlink':target.rename(tmp_path/'weights');target.symlink_to(tmp_path/'weights')
    elif change=='hardlink':os.link(target,tmp_path/'second')
    elif change=='world_writable':target.chmod(0o666)
    else:target.unlink();os.mkfifo(target,0o600)
    with pytest.raises(compat.ShieldCompatibilityError):compat.verify_bundle(root)


def loaded_fixture():
    cfg=compat.make_config(CONFIG.read_bytes(),config_class);cfg._attn_implementation='eager'
    weight=lambda shape:SimpleNamespace(weight=SimpleNamespace(shape=shape))
    attention=lambda:SimpleNamespace(num_attention_heads=12,attention_head_size=64,relative_attention=True,
        share_att_key=True,position_buckets=256,max_relative_positions=512,pos_ebd_size=256,pos_att_type=['p2c','c2p'])
    encoder=SimpleNamespace(layer=[SimpleNamespace(attention=SimpleNamespace(self=attention())) for _ in range(12)],
        relative_attention=True,max_relative_positions=512,position_buckets=256,norm_rel_ebd=['layer_norm'],conv=None,
        rel_embeddings=weight((512,768)))
    embed=SimpleNamespace(word_embeddings=weight((128100,768)),position_embeddings=None,token_type_embeddings=None,
        embed_proj=None,position_biased_input=False)
    cls=type('DebertaV2ForSequenceClassification',(),{});cls.__module__='transformers.models.deberta_v2.modeling_deberta_v2'
    model=cls();model.training=False;model.config=cfg;model.deberta=SimpleNamespace(encoder=encoder,embeddings=embed)
    model.pooler=SimpleNamespace(dense=weight((768,768)),config=cfg)
    model.classifier=weight((2,768));model.classifier.bias=SimpleNamespace(shape=(2,))
    params=[SimpleNamespace(device=SimpleNamespace(type='cpu'),dtype='float32',numel=lambda:1) for _ in range(201)]
    params.append(SimpleNamespace(device=SimpleNamespace(type='cpu'),dtype='float32',numel=lambda:compat.PARAMETERS-201))
    model.parameters=lambda:iter(params)
    return model,SimpleNamespace(float32='float32'),{k:[] for k in ('missing_keys','unexpected_keys','mismatched_keys','error_msgs')},params


def test_actual_loaded_projection_retains_finite_zero_scoring_identity():
    model,torch,info,_=loaded_fixture();result=compat.verify_loaded_model(model,torch,info)
    assert result['parameters']==184423682 and result['parameter_tensors']==202
    assert result['attack_softmax_index']==1 and result['classifier_pooling']=='first_native_token_dense_gelu'
    assert result['scoring_requests']==0


@pytest.mark.parametrize('field',['missing_keys','unexpected_keys','mismatched_keys','error_msgs'])
def test_any_loading_diagnostic_refuses(field):
    model,torch,info,_=loaded_fixture();info[field]=['synthetic']
    with pytest.raises(compat.ShieldCompatibilityError):compat.verify_loaded_model(model,torch,info)


@pytest.mark.parametrize('fault',['dtype','device','count','tensors','embedding','head','pooler','layers','heads','relative','buckets','sharing','conv','attention','training'])
def test_wrong_actual_architecture_refuses_before_predictor(fault):
    m,t,i,p=loaded_fixture()
    if fault=='dtype':p[0].dtype='float16'
    elif fault=='device':p[0].device.type='cuda'
    elif fault=='count':p[0].numel=lambda:2
    elif fault=='tensors':p.pop()
    elif fault=='embedding':m.deberta.embeddings.word_embeddings.weight.shape=(128000,768)
    elif fault=='head':m.classifier.weight.shape=(1,768)
    elif fault=='pooler':m.pooler.dense.weight.shape=(384,384)
    elif fault=='layers':m.deberta.encoder.layer.pop()
    elif fault=='heads':m.deberta.encoder.layer[0].attention.self.num_attention_heads=6
    elif fault=='relative':m.deberta.encoder.relative_attention=False
    elif fault=='buckets':m.deberta.encoder.layer[0].attention.self.position_buckets=128
    elif fault=='sharing':m.deberta.encoder.layer[0].attention.self.share_att_key=False
    elif fault=='conv':m.deberta.encoder.conv=object()
    elif fault=='attention':m.config._attn_implementation='sdpa'
    else:m.training=True
    with pytest.raises(compat.ShieldCompatibilityError):compat.verify_loaded_model(m,t,i)


def test_authenticated_tokenizer_bytes_reach_native_constructor_unchanged(monkeypatch):
    value={'version':'1.0','model':{'type':'Unigram','unk_id':3,'byte_fallback':False,'vocab':[['synthetic',0.0]]*128000},
           **compat.TOKENIZER_COMPONENTS,'added_tokens':added_tokens()}
    raw=json.dumps(value,ensure_ascii=False).encode();pins=dict(compat.FILES)
    pins['tokenizer.json']=(hashlib.sha256(raw).hexdigest(),len(raw));monkeypatch.setattr(compat,'FILES',pins)
    backend=Backend();calls=[]
    class Native:
        @staticmethod
        def from_str(text):calls.append(text);return backend
    tokenizer=compat.make_tokenizer(raw,Native)
    assert calls==[raw.decode()] and tokenizer.num_special_tokens_to_add(pair=False)==0
    assert backend.calls==['no_truncation','no_padding']


def test_full_loader_uses_one_local_eager_model_and_no_encode_or_forward(monkeypatch,tmp_path):
    model,torch,info,_=loaded_fixture();calls=[];original_import=builtins.__import__
    torch.set_num_threads=lambda n:calls.append(('threads',n))
    torch.set_num_interop_threads=lambda n:calls.append(('interop',n))
    model.to=lambda device:(calls.append(('device',device)) or model)
    model.eval=lambda:(calls.append(('eval',)) or model)
    def load(path,**kw):calls.append(('pretrained',path,kw));return model,info
    type(model).from_pretrained=staticmethod(load)
    def importer(name,*args,**kw):
        if name=='torch':return torch
        if name=='tokenizers':return SimpleNamespace(Tokenizer=object())
        if name.endswith('.configuration_deberta_v2'):return SimpleNamespace(DebertaV2Config=config_class)
        if name.endswith('.modeling_deberta_v2'):return SimpleNamespace(DebertaV2ForSequenceClassification=type(model))
        return original_import(name,*args,**kw)
    monkeypatch.setattr(builtins,'__import__',importer)
    monkeypatch.setattr(compat.sys,'version_info',(3,13,16))
    versions={'torch':'2.10.0+cpu','transformers':'4.57.6','tokenizers':'0.22.2','sentencepiece':'0.2.1'}
    monkeypatch.setattr(compat.importlib.metadata,'version',versions.__getitem__)
    package=tmp_path/'transformers';package.mkdir()
    monkeypatch.setattr(compat.importlib.metadata,'distribution',lambda name:SimpleNamespace(locate_file=lambda part:package))
    def bundle(path):calls.append(('bundle',path));return {'config.json':CONFIG.read_bytes(),'tokenizer.json':b'synthetic-captured'}
    monkeypatch.setattr(compat,'verify_bundle',bundle)
    monkeypatch.setattr(compat,'_read_pinned',lambda path,pin:calls.append(('library',path,pin)))
    monkeypatch.setattr(compat,'verify_imported_modules',lambda *args:calls.append(('imported',args)))
    native=object()
    def tokenizer(raw,cls):assert raw==b'synthetic-captured';calls.append(('tokenizer',));return native
    monkeypatch.setattr(compat,'make_tokenizer',tokenizer)
    # Restore every modified environment value after the real loader body.
    for name in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_DISABLE_TELEMETRY','TOKENIZERS_PARALLELISM','OMP_NUM_THREADS','MKL_NUM_THREADS','CUDA_VISIBLE_DEVICES'):
        monkeypatch.setenv(name,os.environ.get(name,''))
    result,predict,metadata=compat.load_for_qualification(str(tmp_path))
    assert result is native and callable(predict)
    loads=[row for row in calls if row[0]=='pretrained'];assert len(loads)==1
    assert loads[0][1]==str(tmp_path)
    assert set(loads[0][2])=={'config','local_files_only','trust_remote_code','use_safetensors','torch_dtype','attn_implementation','output_loading_info'}
    assert {k:v for k,v in loads[0][2].items() if k!='config'}=={
        'local_files_only':True,'trust_remote_code':False,'use_safetensors':True,'torch_dtype':'float32',
        'attn_implementation':'eager','output_loading_info':True}
    assert [row for row in calls if row[0]=='bundle']==[('bundle',str(tmp_path))]*2
    assert metadata['scoring_requests']==0 and metadata['qualification_only'] is True and metadata['quality_verified'] is False
    assert metadata['single_template_ids']==[] and metadata['empty_encoding']=='refuse'
    assert metadata['repository']==compat.REPOSITORY and metadata['revision']==compat.REVISION
    assert metadata['files']==dict(compat.FILES) and metadata['versions']==versions


@pytest.mark.parametrize('shape,value,finite,accepted',[
    ((1,2),0.,True,True),((1,2),1.,True,True),((1,2),.9,True,True),
    ((2,2),.5,True,False),((1,1),.5,True,False),((1,2),float('nan'),True,False),
    ((1,2),1.01,True,False),((1,2),-.01,True,False),((1,2),.5,False,False)])
def test_binary_softmax_uses_exact_index_one_without_calibration(shape,value,finite,accepted):
    calls=[]
    class Probability:
        def __getitem__(self,index):calls.append(index);return value
    torch=SimpleNamespace(isfinite=lambda _:SimpleNamespace(all=lambda:finite),
        softmax=lambda logits,dim:(calls.append(('softmax',dim)) or Probability()))
    logits=SimpleNamespace(shape=shape)
    if accepted:
        assert compat.validate_logits(logits,torch)==value and calls==[('softmax',-1),(0,1)]
    else:
        with pytest.raises(compat.ShieldCompatibilityError):compat.validate_logits(logits,torch)
