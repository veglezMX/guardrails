"""Candidate07 BERT compatibility checks; no tokenizer, torch, model or network load."""
from __future__ import annotations

import builtins
import hashlib
import json
import math
import os
import socket
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from services.classifier import pranesh_candidate_07_compat as compat

CONFIG = Path(__file__).parents[3] / "mvp/tests/evolution/fixtures/pranesh-candidate-07-config.json"


@pytest.fixture(autouse=True)
def forbid_ml_and_network(monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"torch", "transformers", "tokenizers", "sentencepiece"}:
            pytest.fail("real ML package import forbidden in isolated qualification checks")
        return original(name, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail("network operation forbidden in isolated qualification checks")
    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "bind", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def config_class(**kwargs):
    value=SimpleNamespace(**kwargs)
    value.id2label={0:'safe',1:'attack'}
    value.label2id={'safe':0,'attack':1}
    value.num_labels=2;value.chunk_size_feed_forward=0;value.position_embedding_type="absolute"
    return value


def test_exact_native_configuration_is_not_translated_or_rewritten():
    before=CONFIG.read_bytes();calls=[]
    def factory(**kw):calls.append(kw);return config_class(**kw)
    value=compat.make_config(before,factory)
    assert calls==[json.loads(before)] and compat.native_config(before)==json.loads(before)
    assert CONFIG.read_bytes()==before and value.num_labels==2 and value.max_position_embeddings==512


@pytest.mark.parametrize('field,value',[('hidden_act','relu'),('hidden_size',384),('num_attention_heads',6),('num_hidden_layers',12),
    ('vocab_size',30523),('max_position_embeddings',1024),('model_type','distilbert'),
    ('id2label',{'0':'attack','1':'safe'}),('auto_map',{'AutoModel':'remote'})])
def test_changed_native_config_refuses_before_construction(field,value):
    raw=json.loads(CONFIG.read_bytes());raw[field]=value
    with pytest.raises(compat.BertCompatibilityError):compat.native_config(json.dumps(raw).encode())


@pytest.mark.parametrize('raw',[b'{}',b'',b'NaN',b'{"duplicate":1,"duplicate":2}','not-bytes'])
def test_unbound_config_never_reaches_native_constructor(raw):
    with pytest.raises(compat.BertCompatibilityError):compat.native_config(raw)


@pytest.mark.parametrize('field,value',[('hidden_act','relu'),('hidden_size',384),('num_attention_heads',6),('num_hidden_layers',12),
    ('position_embedding_type','relative_key'),('chunk_size_feed_forward',1),('num_labels',3),('id2label',{0:'attack',1:'safe'})])
def test_actual_native_config_mismatch_refuses(field,value):
    def factory(**kw):
        value_object=config_class(**kw);setattr(value_object,field,value);return value_object
    with pytest.raises(compat.BertCompatibilityError):compat.make_config(CONFIG.read_bytes(),factory)


@pytest.mark.parametrize("key", ["missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"])
def test_any_nonempty_weight_loading_diagnostic_refuses_before_module_use(key):
    info = {name: [] for name in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")}
    info[key] = ["synthetic"]
    with pytest.raises(compat.BertCompatibilityError): compat.verify_loaded_model(object(), object(), info)


@pytest.mark.parametrize("info", [{}, {"missing_keys": []},
    dict.fromkeys(("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"), ()),
    {"missing_keys": [], "unexpected_keys": [], "mismatched_keys": [], "error_msgs": [], "private": "unretained"}])
def test_incomplete_or_ambiguous_loading_result_is_not_a_success(info):
    with pytest.raises(compat.BertCompatibilityError): compat.verify_loaded_model(object(), object(), info)


class Component:
    def __init__(self, value):
        self.value = value

    def __getstate__(self):
        return json.dumps(self.value).encode()


PROJECTION = Path(__file__).parents[3] / "mvp/tests/evolution/fixtures/pranesh-candidate-07-tokenizer-projection.json"
TOKENIZER_COMPONENTS = {name: value for name, value in json.loads(PROJECTION.read_bytes()).items()
                        if name in compat.TOKENIZER_COMPONENT_SHA256}


def added_tokens():
    return json.loads(PROJECTION.read_bytes())["added_tokens"]


class Backend:
    """Finite fake of the native API; no tokenizer package is imported."""

    def __init__(self, tokens=None):
        self.model = type("WordPiece", (), {})()
        for name, value in TOKENIZER_COMPONENTS.items():
            setattr(self, name, Component(json.loads(json.dumps(value))))
        self.truncation = {"direction": "right", "max_length": 128, "strategy": "longest_first", "stride": 0}
        self.padding = None
        self.added = {row["id"]: SimpleNamespace(**{k: v for k, v in row.items() if k != "id"}) for row in added_tokens()}
        self.ids = dict(compat.SPECIAL_IDS)
        self.tokens = tokens if tokens is not None else [11, 12]
        self.calls = []

    def get_vocab_size(self, *, with_added_tokens):
        assert with_added_tokens is True
        return 30522

    def get_added_tokens_decoder(self):
        return self.added

    def token_to_id(self, token):
        return self.ids[token]

    def num_special_tokens_to_add(self, pair):
        assert pair is False
        return 2

    def no_truncation(self):
        self.calls.append("no_truncation")
        self.truncation = None

    def no_padding(self):
        self.calls.append("no_padding")
        self.padding = None

    def encode(self, text, *, add_special_tokens):
        self.calls.append((text, add_special_tokens))
        return SimpleNamespace(ids=[101,102] if add_special_tokens else self.tokens.copy())


def test_native_wrapper_disables_truncation_and_preserves_all_added_token_flags():
    backend = Backend()
    before = {k: vars(v).copy() for k, v in backend.added.items()}
    tokenizer = compat.CandidateTokenizer(backend, added_tokens())
    assert backend.calls == ["no_truncation", "no_padding", ("",True)]
    assert tokenizer.encode("original text", add_special_tokens=False, truncation=False) == [11, 12]
    assert backend.calls[-1] == ("original text", False)
    assert tokenizer.num_special_tokens_to_add(pair=False) == 2
    assert tokenizer.build_inputs_with_special_tokens([11, 12]) == [101,11,12,102]
    assert {k: vars(v).copy() for k, v in backend.added.items()} == before
    assert backend.added[103].lstrip is False


@pytest.mark.parametrize("component", list(TOKENIZER_COMPONENTS))
def test_changed_native_preprocessing_or_template_refuses(component):
    backend = Backend()
    getattr(backend, component).value["unexpected"] = True
    with pytest.raises(compat.BertCompatibilityError): compat.CandidateTokenizer(backend, added_tokens())


@pytest.mark.parametrize("change", ["mask_lstrip", "missing_token", "extra_token", "wrong_bos", "wrong_model",
    "wrong_truncation", "padding", "missing_truncation", "wrong_added_flags"])
def test_native_state_cannot_silently_fall_back_or_reregister_tokens(change):
    backend = Backend()
    if change == "mask_lstrip": backend.added[103].lstrip = True
    elif change == "missing_token": del backend.added[103]
    elif change == "extra_token": backend.added[999] = backend.added[103]
    elif change == "wrong_bos": backend.ids["[CLS]"] = 102
    elif change == "wrong_model": backend.model = object()
    elif change == "wrong_truncation": backend.truncation["max_length"] = 1024
    elif change == "padding": backend.padding = {"length": 512}
    elif change == "missing_truncation": backend.truncation = None
    else: backend.added[100].normalized = not backend.added[100].normalized
    with pytest.raises(compat.BertCompatibilityError): compat.CandidateTokenizer(backend, added_tokens())


@pytest.mark.parametrize("method", ["encode", "num_special_tokens_to_add", "build_inputs_with_special_tokens"])
@pytest.mark.parametrize("field", ["truncation", "padding"])
def test_reenabled_native_truncation_or_padding_fails_before_each_operation(method, field):
    backend = Backend()
    tokenizer = compat.CandidateTokenizer(backend, added_tokens())
    setattr(backend, field, {"unexpected": True})
    args = {"encode": ("text",), "num_special_tokens_to_add": (), "build_inputs_with_special_tokens": ([11],)}
    with pytest.raises(compat.BertCompatibilityError): getattr(tokenizer, method)(*args[method])
    assert backend.calls == ["no_truncation", "no_padding", ("",True)]


def test_changed_template_after_construction_cannot_use_wrong_cls_token():
    backend = Backend()
    tokenizer = compat.CandidateTokenizer(backend, added_tokens())
    backend.post_processor.value["special_tokens"]["[CLS]"]["ids"] = [102]
    with pytest.raises(compat.BertCompatibilityError): tokenizer.build_inputs_with_special_tokens([11])


@pytest.mark.parametrize("tokens", [[True], [-1], [30522], [1.5], (1,), [1] * 511])
def test_template_rejects_bad_or_oversized_window_without_partial_input(tokens):
    tokenizer = compat.CandidateTokenizer(Backend(), added_tokens())
    with pytest.raises(compat.BertCompatibilityError): tokenizer.build_inputs_with_special_tokens(tokens)


def test_original_worker_windows_include_a_tail_beyond_embedded_128_limit():
    from services.classifier.worker import window_jobs
    backend = Backend(list(range(3000)))
    tokenizer = compat.CandidateTokenizer(backend, added_tokens())
    jobs = window_jobs([{"text": "synthetic full document"}], tokenizer, time.monotonic() + 5)
    assert len(jobs) == 11 and all(len(tokens) <= 512 for _, tokens in jobs)
    assert set().union(*(set(tokens[1:-1]) for _, tokens in jobs)) == set(range(3000))
    assert jobs[-1][1][-2] == 2999


def test_original_worker_refuses_coverage_beyond_sixteen_windows_without_scoring():
    from guardrails.evolution.classifier_contract import ClassifierError

    from services.classifier.worker import window_jobs
    tokenizer = compat.CandidateTokenizer(Backend(list(range(5000))), added_tokens())
    with pytest.raises(ClassifierError) as error:
        window_jobs([{"text": "synthetic oversized document"}], tokenizer, time.monotonic() + 5)
    assert error.value.code == "inspection_incomplete"


@pytest.mark.parametrize("raw", [b"{}", b"", "not-bytes", b"x"])
def test_unpinned_tokenizer_never_reaches_native_constructor(raw):
    class Never:
        @staticmethod
        def from_str(_):
            raise AssertionError("native constructor must not run")
    with pytest.raises(compat.BertCompatibilityError): compat.make_tokenizer(raw, Never)


def test_parent_only_loader_refuses_wrong_python_before_model_or_files(monkeypatch):
    monkeypatch.setattr(compat.sys, "version_info", (3, 14, 0))
    monkeypatch.setattr(compat, "verify_bundle", lambda _: pytest.fail("bundle must not be read"))
    with pytest.raises(compat.BertCompatibilityError): compat.load_for_qualification("/not-used")


@pytest.mark.parametrize("name", ["torch", "transformers", "tokenizers"])
def test_parent_only_loader_requires_a_clean_process_before_artifact_load(monkeypatch, name):
    monkeypatch.setattr(compat.sys, "version_info", (3, 13, 16))
    monkeypatch.setitem(compat.sys.modules, name, ModuleType(name))
    monkeypatch.setattr(compat, "verify_bundle", lambda _: pytest.fail("bundle must not be read"))
    with pytest.raises(compat.BertCompatibilityError): compat.load_for_qualification("/not-used")


def full_loaded_fixture():
    torch=SimpleNamespace(float32='float32')
    config=compat.make_config(CONFIG.read_bytes(),config_class);config._attn_implementation='eager'
    cls=type('BertForSequenceClassification',(),{});cls.__module__='transformers.models.bert.modeling_bert'
    model=cls();model.training=False;model.config=config
    attention_cls=type('BertSelfAttention',(),{});attention_cls.__module__=cls.__module__
    layers=[]
    for _ in range(4):
        attention=attention_cls();attention.num_attention_heads=4;attention.all_head_size=256;attention.attention_head_size=64
        attention.is_decoder=False;attention.position_embedding_type='absolute'
        layers.append(SimpleNamespace(attention=SimpleNamespace(self=attention,pruned_heads=set(),
            output=SimpleNamespace(LayerNorm=SimpleNamespace(eps=1e-12))),
            output=SimpleNamespace(LayerNorm=SimpleNamespace(eps=1e-12)),
            intermediate=SimpleNamespace(intermediate_act_fn=type('GELUActivation',(),{})()),
            is_decoder=False,add_cross_attention=False,chunk_size_feed_forward=0))
    embeddings=SimpleNamespace(word_embeddings=SimpleNamespace(padding_idx=0),LayerNorm=SimpleNamespace(eps=1e-12),position_embedding_type='absolute')
    pooler_cls=type('BertPooler',(),{});pooler_cls.__module__=cls.__module__
    pooler=pooler_cls();pooler.activation=type('Tanh',(),{})()
    model.bert=SimpleNamespace(embeddings=embeddings,encoder=SimpleNamespace(layer=layers),pooler=pooler)
    params={name:SimpleNamespace(shape=shape,device=SimpleNamespace(type='cpu'),dtype='float32',numel=lambda shape=shape:math.prod(shape))
        for name,shape in compat.expected_shapes().items()}
    model.named_parameters=lambda:list(params.items())
    info={key:[] for key in ('missing_keys','unexpected_keys','mismatched_keys','error_msgs')}
    return model,torch,info


def test_actual_loaded_native_shapes_are_verified_without_forward():
    model,torch,info=full_loaded_fixture();value=compat.verify_loaded_model(model,torch,info)
    assert value['parameters']==11171074 and value['weight_tensors']==73 and value['scoring_requests']==0
    assert value['classifier_pooling']=='native_cls_dense_tanh' and value['attack_softmax_index']==1


@pytest.mark.parametrize('fault',['embedding','positions','head','pooler','heads','causal','norm','ffn','activation','layers','dtype','attention','segments','relative','cross_attention','pooler_activation','missing_tensor','extra_tensor','duplicate_tensor'])
def test_wrong_loaded_architecture_refuses_before_predictor(fault):
    model,torch,info=full_loaded_fixture();layer=model.bert.encoder.layer[0]
    params=dict(model.named_parameters())
    if fault=='embedding':params['bert.embeddings.word_embeddings.weight'].shape=(30521,256)
    elif fault=='positions':params['bert.embeddings.position_embeddings.weight'].shape=(1024,256)
    elif fault=='head':params['classifier.weight'].shape=(1,256)
    elif fault=='pooler':params['bert.pooler.dense.weight'].shape=(128,256)
    elif fault=='segments':params['bert.embeddings.token_type_embeddings.weight'].shape=(1,256)
    elif fault=='heads':layer.attention.self.num_attention_heads=8
    elif fault=='causal':layer.attention.self.is_decoder=True
    elif fault=='relative':layer.attention.self.position_embedding_type='relative_key'
    elif fault=='cross_attention':layer.add_cross_attention=True
    elif fault=='norm':layer.output.LayerNorm.eps=1e-5
    elif fault=='ffn':params['bert.encoder.layer.0.intermediate.dense.weight'].shape=(512,256)
    elif fault=='activation':layer.intermediate.intermediate_act_fn=object()
    elif fault=='pooler_activation':model.bert.pooler.activation=object()
    elif fault=='layers':model.bert.encoder.layer.pop()
    elif fault=='attention':model.config._attn_implementation='sdpa'
    elif fault=='missing_tensor':del params['classifier.bias']
    elif fault=='extra_tensor':params['unreviewed.weight']=params['classifier.bias']
    elif fault=='duplicate_tensor':model.named_parameters=lambda:[*params.items(),next(iter(params.items()))]
    else:params['classifier.bias'].dtype='float16'
    if fault!='duplicate_tensor':model.named_parameters=lambda:list(params.items())
    with pytest.raises(compat.BertCompatibilityError):compat.verify_loaded_model(model,torch,info)


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
    with pytest.raises(ValueError):compat.verify_bundle(root)


@pytest.mark.parametrize("invoke", [False, True])
def test_full_loader_uses_one_local_model_and_only_explicit_predictor_calls(monkeypatch,tmp_path,invoke):
    model,torch,info=full_loaded_fixture();calls=[];original_import=builtins.__import__
    torch.set_num_threads=lambda n:calls.append(('threads',n))
    torch.set_num_interop_threads=lambda n:calls.append(('interop',n))
    model.to=lambda device:(calls.append(('device',device)) or model)
    model.eval=lambda:(calls.append(('eval',)) or model)
    def load(path,**kw):calls.append(('pretrained',path,kw));return model,info
    type(model).from_pretrained=staticmethod(load)
    def importer(name,*args,**kw):
        if name=='torch':return torch
        if name=='tokenizers':return SimpleNamespace(Tokenizer=object())
        if name.endswith('.configuration_bert'):return SimpleNamespace(BertConfig=config_class)
        if name.endswith('.modeling_bert'):return SimpleNamespace(BertForSequenceClassification=type(model))
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
    backend=Backend();native=None
    def tokenizer(raw,cls):
        nonlocal native
        assert raw==b'synthetic-captured';calls.append(('tokenizer',))
        native=compat.CandidateTokenizer(backend,added_tokens());return native
    monkeypatch.setattr(compat,'make_tokenizer',tokenizer)
    # Restore every modified environment value after the real loader body.
    for name in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_DISABLE_TELEMETRY','TOKENIZERS_PARALLELISM','OMP_NUM_THREADS','MKL_NUM_THREADS','CUDA_VISIBLE_DEVICES'):
        monkeypatch.setenv(name,os.environ.get(name,''))
    model_calls=[]
    def forward(self,**kw):model_calls.append(kw);return SimpleNamespace(logits='original-native-logits')
    type(model).__call__=forward
    torch.long='long'
    torch.tensor=lambda value,**kw:(value,kw)
    torch.ones_like=lambda encoded:('ones',encoded)
    torch.zeros_like=lambda encoded:('zeros',encoded)
    from contextlib import nullcontext
    torch.inference_mode=nullcontext
    monkeypatch.setattr(compat,'validate_logits',lambda logits,t:0.95 if logits=='original-native-logits' and t is torch else pytest.fail('wrong logit source'))
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
    assert metadata['single_template_ids']==[101,102]
    assert metadata['native_empty_validation_calls']==metadata['tokenizer_encode_calls']==1
    assert metadata['native_empty_ids']==[101,102] and backend.calls==['no_truncation','no_padding',('',True)]
    assert metadata['repository']==compat.REPOSITORY and metadata['revision']==compat.REVISION
    assert metadata['files']==dict(compat.FILES) and metadata['versions']==versions
    assert model_calls==[]
    if invoke:
        assert predict([101,7,102])==0.95
        encoded=([[101,7,102]],{'dtype':'long','device':'cpu'})
        assert model_calls==[{'input_ids':encoded,'attention_mask':('ones',encoded),'token_type_ids':('zeros',encoded)}]
        for bad in ([],[101],[101,True,102],[101,30522,102],[1,7,2],[101,*([7]*511),102]):
            with pytest.raises(compat.BertCompatibilityError):predict(bad)
        assert len(model_calls)==1


@pytest.mark.parametrize('empty',[[],[2],[1,2],[2,1,0],(2,1)])
def test_actual_native_empty_template_must_match_before_any_model_construction(empty):
    b=Backend();b.encode=lambda text,**kw:SimpleNamespace(ids=empty)
    with pytest.raises(compat.BertCompatibilityError):compat.CandidateTokenizer(b,added_tokens())


def test_native_empty_works_with_original_production_window_algorithm_without_invented_tokens():
    from services.classifier.worker import window_jobs
    b=Backend([]);tokenizer=compat.CandidateTokenizer(b,added_tokens())
    assert window_jobs([{'text':''}],tokenizer,time.monotonic()+5)==[(0,[101,102])]
    assert b.calls==['no_truncation','no_padding',('',True),('',False)]


def test_parameter_tensor_count_must_match_actual_authenticated_header():
    model,torch,info=full_loaded_fixture();params=list(model.named_parameters());model.named_parameters=lambda:params[:-1]
    with pytest.raises(compat.BertCompatibilityError):compat.verify_loaded_model(model,torch,info)


def test_native_constructor_receives_the_complete_authenticated_json_once(monkeypatch):
    projection=json.loads(PROJECTION.read_bytes())
    value={k:v for k,v in projection.items() if k not in {'model_settings','vocab_size'}}
    value['model']={**projection['model_settings'],'vocab':{str(i):i for i in range(30522)}}
    raw=json.dumps(value).encode();pins=dict(compat.FILES);pins['tokenizer.json']=(hashlib.sha256(raw).hexdigest(),len(raw))
    monkeypatch.setattr(compat,'FILES',pins);calls=[]
    class Factory:
        @staticmethod
        def from_str(text):calls.append(text);return Backend()
    tokenizer=compat.make_tokenizer(raw,Factory)
    assert calls==[raw.decode()] and tokenizer.native_empty_validation_calls==1


@pytest.mark.parametrize('fault',[None,'missing','shadow_path','wrong_config_object','wrong_model_object','wrong_class_module','model_reexport'])
def test_actual_imported_native_class_and_path_identity_are_required(monkeypatch,tmp_path,fault):
    package=tmp_path/'transformers';package.mkdir()
    c_name='transformers.models.bert.configuration_bert';m_name='transformers.models.bert.modeling_bert'
    c=ModuleType(c_name);m=ModuleType(m_name)
    config=type('BertConfig',(),{});config.__module__=c_name
    model=type('BertForSequenceClassification',(),{});model.__module__=m_name
    c.BertConfig=config;m.BertConfig=config;m.BertForSequenceClassification=model
    for name,mod,rel in [(c_name,c,'models/bert/configuration_bert.py'),(m_name,m,'models/bert/modeling_bert.py')]:
        path=package/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'captured stdlib stand-in')
        mod.__file__=str(path);monkeypatch.setitem(compat.sys.modules,name,mod)
    monkeypatch.setattr(compat,'_read_pinned',lambda *args:None)
    if fault=='missing':monkeypatch.delitem(compat.sys.modules,c_name)
    elif fault=='shadow_path':m.__file__=str(tmp_path/'untrusted.py')
    elif fault=='wrong_config_object':c.BertConfig=object()
    elif fault=='wrong_model_object':m.BertForSequenceClassification=object()
    elif fault=='wrong_class_module':model.__module__='untrusted'
    elif fault=='model_reexport':m.BertConfig=object()
    if fault is None:compat.verify_imported_modules(package,config,model)
    else:
        with pytest.raises(compat.BertCompatibilityError):compat.verify_imported_modules(package,config,model)


def test_all_tensor_shapes_match_independently_reviewed_native_source_projection():
    path=Path(__file__).parents[3]/'mvp/tests/evolution/fixtures/pranesh-candidate-07-native-shapes.json'
    expected=json.loads(path.read_bytes())
    assert {name:list(shape) for name,shape in compat.expected_shapes().items()}==expected
    assert sum(math.prod(shape) for shape in expected.values())==11171074 and len(expected)==73
