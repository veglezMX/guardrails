"""Candidate02 ModernBERT compatibility checks; no tokenizer, torch, model or network load."""
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

from services.classifier import modernbert_candidate_02_compat as compat

CONFIG = Path(__file__).parents[3] / "mvp/tests/evolution/fixtures/modernbert-candidate-02-config.json"


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
    value = SimpleNamespace(**kwargs)
    value.id2label = {0:"LABEL_0",1:"LABEL_1"}
    value.label2id = {"LABEL_0":0,"LABEL_1":1}
    value.num_labels = 2
    return value


def test_exact_public_config_is_translated_without_rewriting_or_changing_scoring_metadata():
    before = CONFIG.read_bytes()
    original = json.loads(before)
    translated = compat.translated_config(before)
    removed = {"rope_parameters", "layer_types"}
    added = {"global_rope_theta", "local_rope_theta", "torch_dtype", "reference_compile"}
    assert set(translated) == (set(original) - removed) | added
    assert all(translated[key] == value for key, value in original.items() if key not in removed)
    assert translated["global_rope_theta"] == 160000.0 and translated["local_rope_theta"] == 10000.0
    assert translated["reference_compile"] is False
    assert translated["classifier_pooling"] == "mean" and translated["position_embedding_type"] == "absolute"
    assert translated["max_position_embeddings"] == 8192 and translated["transformers_version"] == "5.13.0"
    assert CONFIG.read_bytes() == before


@pytest.mark.parametrize("field,value", [("classifier_pooling", "cls"), ("dtype", "float16"),
    ("local_attention", 256), ("num_hidden_layers", 23), ("global_attn_every_n_layers", 4),
    ("position_embedding_type", "sans_pos"), ("model_type", "bert"),
    ("id2label", {"0": "injection", "1": "benign"}),
    ("rope_parameters", {"full_attention": {"rope_theta": 10000, "rope_type": "default"}}),
    ("auto_map", {"AutoModel": "remote-code"})])
def test_any_changed_model_configuration_is_rejected_before_construction(field, value):
    changed = json.loads(CONFIG.read_bytes()); changed[field] = value
    with pytest.raises(compat.ModernBertCompatibilityError): compat.translated_config(json.dumps(changed).encode())


@pytest.mark.parametrize("raw", [b"{}", b"", b"NaN", b'{"duplicate":1,"duplicate":2}', "not-bytes", b"x" * 2085])
def test_unpinned_or_nonbytes_config_is_never_admitted(raw):
    with pytest.raises(compat.ModernBertCompatibilityError): compat.translated_config(raw)


def test_explicit_actual_configuration_factory_receives_translated_fields_once():
    calls = []
    def factory(**kwargs):
        calls.append(kwargs)
        return config_class(**kwargs)
    config = compat.make_config(CONFIG.read_bytes(), factory)
    assert len(calls) == 1 and config.num_labels == 2
    assert config.id2label == {0:"LABEL_0",1:"LABEL_1"}
    assert config.local_rope_theta == 10000.0


@pytest.mark.parametrize("field,value", [("local_rope_theta", 160000.0), ("global_rope_theta", 10000.0),
    ("global_attn_every_n_layers", 2), ("local_attention", 64), ("max_position_embeddings", 512),
    ("classifier_pooling", "cls"), ("reference_compile", True), ("num_labels", 1),
    ("id2label", {0: "injection", 1: "benign"}), ("rope_scaling", {"rope_type": "dynamic"}),
    ("rope_parameters", {"ignored": "metadata"})])
def test_actual_config_default_or_ignored_translation_refuses(field, value):
    def factory(**kwargs):
        config = config_class(**kwargs)
        setattr(config, field, value)
        return config
    with pytest.raises(compat.ModernBertCompatibilityError): compat.make_config(CONFIG.read_bytes(), factory)


@pytest.mark.parametrize("key", ["missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"])
def test_any_nonempty_weight_loading_diagnostic_refuses_before_module_use(key):
    info = {name: [] for name in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")}
    info[key] = ["synthetic"]
    with pytest.raises(compat.ModernBertCompatibilityError): compat.verify_loaded_model(object(), object(), info)


@pytest.mark.parametrize("info", [{}, {"missing_keys": []},
    dict.fromkeys(("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"), ()),
    {"missing_keys": [], "unexpected_keys": [], "mismatched_keys": [], "error_msgs": [], "private": "unretained"}])
def test_incomplete_or_ambiguous_loading_result_is_not_a_success(info):
    with pytest.raises(compat.ModernBertCompatibilityError): compat.verify_loaded_model(object(), object(), info)


class Component:
    def __init__(self, value):
        self.value = value

    def __getstate__(self):
        return json.dumps(self.value).encode()


def added_tokens():
    rows = []
    for token, number in [*compat.SPECIAL_IDS.items(), *((f"token-{i}", i) for i in range(108, 219))]:
        rows.append({"id": number, "content": token, "single_word": False,
            "lstrip": token == "[MASK]", "rstrip": False, "normalized": False, "special": True})
    assert len(rows) == 116
    return rows


class Backend:
    """Finite fake of the native API; no tokenizer package is imported."""

    def __init__(self, tokens=None):
        self.model = type("BPE", (), {})()
        for name, value in compat.TOKENIZER_COMPONENTS.items():
            setattr(self, name, Component(json.loads(json.dumps(value))))
        self.truncation = {"direction": "right", "max_length": 256, "strategy": "longest_first", "stride": 0}
        self.padding = {"length":None,"direction":"right","pad_to_multiple_of":None,"pad_id":50283,"pad_type_id":0,"pad_token":"[PAD]"}
        self.added = {row["id"]: SimpleNamespace(**{k: v for k, v in row.items() if k != "id"}) for row in added_tokens()}
        self.ids = dict(compat.SPECIAL_IDS)
        self.tokens = tokens if tokens is not None else [11, 12]
        self.calls = []

    def get_vocab_size(self, *, with_added_tokens):
        assert with_added_tokens is True
        return 50368

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
        return SimpleNamespace(ids=[50281,50282] if add_special_tokens else self.tokens.copy())


def test_native_wrapper_disables_truncation_and_preserves_all_added_token_flags():
    backend = Backend()
    before = {k: vars(v).copy() for k, v in backend.added.items()}
    tokenizer = compat.CandidateTokenizer(backend, added_tokens())
    assert backend.calls == ["no_truncation", "no_padding", ("",True)]
    assert tokenizer.encode("original text", add_special_tokens=False, truncation=False) == [11, 12]
    assert backend.calls[-1] == ("original text", False)
    assert tokenizer.num_special_tokens_to_add(pair=False) == 2
    assert tokenizer.build_inputs_with_special_tokens([11, 12]) == [50281,11,12,50282]
    assert {k: vars(v).copy() for k, v in backend.added.items()} == before
    assert backend.added[50284].lstrip is True


@pytest.mark.parametrize("component", list(compat.TOKENIZER_COMPONENTS))
def test_changed_native_preprocessing_or_template_refuses(component):
    backend = Backend()
    getattr(backend, component).value["unexpected"] = True
    with pytest.raises(compat.ModernBertCompatibilityError): compat.CandidateTokenizer(backend, added_tokens())


@pytest.mark.parametrize("change", ["mask_lstrip", "missing_token", "extra_token", "wrong_bos", "wrong_model",
    "wrong_truncation", "padding", "missing_truncation", "wrong_added_flags"])
def test_native_state_cannot_silently_fall_back_or_reregister_tokens(change):
    backend = Backend()
    if change == "mask_lstrip": backend.added[50284].lstrip = False
    elif change == "missing_token": del backend.added[50284]
    elif change == "extra_token": backend.added[999] = backend.added[50284]
    elif change == "wrong_bos": backend.ids["[CLS]"] = 1
    elif change == "wrong_model": backend.model = object()
    elif change == "wrong_truncation": backend.truncation["max_length"] = 512
    elif change == "padding": backend.padding = {"length": 512}
    elif change == "missing_truncation": backend.truncation = None
    else: backend.added[108].normalized = True
    with pytest.raises(compat.ModernBertCompatibilityError): compat.CandidateTokenizer(backend, added_tokens())


@pytest.mark.parametrize("method", ["encode", "num_special_tokens_to_add", "build_inputs_with_special_tokens"])
@pytest.mark.parametrize("field", ["truncation", "padding"])
def test_reenabled_native_truncation_or_padding_fails_before_each_operation(method, field):
    backend = Backend()
    tokenizer = compat.CandidateTokenizer(backend, added_tokens())
    setattr(backend, field, {"unexpected": True})
    args = {"encode": ("text",), "num_special_tokens_to_add": (), "build_inputs_with_special_tokens": ([11],)}
    with pytest.raises(compat.ModernBertCompatibilityError): getattr(tokenizer, method)(*args[method])
    assert backend.calls == ["no_truncation", "no_padding", ("",True)]


def test_changed_template_after_construction_cannot_use_wrong_cls_token():
    backend = Backend()
    tokenizer = compat.CandidateTokenizer(backend, added_tokens())
    backend.post_processor.value["special_tokens"]["[CLS]"]["ids"] = [1]
    with pytest.raises(compat.ModernBertCompatibilityError): tokenizer.build_inputs_with_special_tokens([11])


@pytest.mark.parametrize("tokens", [[True], [-1], [50368], [1.5], (1,), [1] * 511])
def test_template_rejects_bad_or_oversized_window_without_partial_input(tokens):
    tokenizer = compat.CandidateTokenizer(Backend(), added_tokens())
    with pytest.raises(compat.ModernBertCompatibilityError): tokenizer.build_inputs_with_special_tokens(tokens)


def test_original_worker_windows_include_a_tail_beyond_embedded_256_limit():
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
    with pytest.raises(compat.ModernBertCompatibilityError): compat.make_tokenizer(raw, Never)


def test_parent_only_loader_refuses_wrong_python_before_model_or_files(monkeypatch):
    monkeypatch.setattr(compat.sys, "version_info", (3, 14, 0))
    monkeypatch.setattr(compat, "verify_bundle", lambda _: pytest.fail("bundle must not be read"))
    with pytest.raises(compat.ModernBertCompatibilityError): compat.load_for_qualification("/not-used")


@pytest.mark.parametrize("name", ["torch", "transformers", "tokenizers"])
def test_parent_only_loader_requires_a_clean_process_before_artifact_load(monkeypatch, name):
    monkeypatch.setattr(compat.sys, "version_info", (3, 13, 16))
    monkeypatch.setitem(compat.sys.modules, name, ModuleType(name))
    monkeypatch.setattr(compat, "verify_bundle", lambda _: pytest.fail("bundle must not be read"))
    with pytest.raises(compat.ModernBertCompatibilityError): compat.load_for_qualification("/not-used")


def full_loaded_fixture():
    """Finite public tensor-shape stand-ins; no weights or ML package load."""
    class Vector:
        device=SimpleNamespace(type="cpu")
        dtype="float32"
        shape=(32,)
        def __init__(self,values): self.values=values
        def float(self): return self
        def __truediv__(self,value): return Vector([x/value for x in self.values])
        def __rpow__(self,value): return Vector([value**x for x in self.values])
        def __rtruediv__(self,value): return Vector([value/x for x in self.values])
    torch=SimpleNamespace(float32="float32",int64="int64",
        arange=lambda begin,end,step,**kw:Vector(list(range(begin,end,step))),
        equal=lambda a,b:a.values==b.values)
    config=compat.make_config(CONFIG.read_bytes(),config_class)
    config._attn_implementation="sdpa"
    layers=[]
    for index in range(22):
        theta=160000.0 if index%3==0 else 10000.0
        expected=1/(theta**(torch.arange(0,64,2).float()/64))
        rotary=SimpleNamespace(rope_type="default",config=SimpleNamespace(rope_theta=theta),
                              attention_scaling=1.0,inv_freq=expected)
        attention=SimpleNamespace(layer_id=index,head_dim=64,num_heads=12,
                                  local_attention=(-1,-1) if index%3==0 else (64,64),rotary_emb=rotary)
        layers.append(SimpleNamespace(attn=attention))
    cls=type("ModernBertForSequenceClassification",(),{})
    cls.__module__="transformers.models.modernbert.modeling_modernbert"
    model=cls();model.training=False;model.config=config
    model.model=SimpleNamespace(layers=layers,embeddings=SimpleNamespace(tok_embeddings=SimpleNamespace(weight=SimpleNamespace(shape=(50368,768)))))
    model.classifier=SimpleNamespace(weight=SimpleNamespace(shape=(2,768)),bias=SimpleNamespace(shape=(2,)))
    model.parameters=lambda:[SimpleNamespace(device=SimpleNamespace(type="cpu"),dtype="float32",numel=lambda:149606402),*[SimpleNamespace(device=SimpleNamespace(type="cpu"),dtype="float32",numel=lambda:0) for _ in range(137)]]
    info={key:[] for key in ("missing_keys","unexpected_keys","mismatched_keys","error_msgs")}
    return model,torch,info


def test_full_actual_module_shape_and_rotary_projection_accepts_only_full_architecture():
    model,torch,info=full_loaded_fixture()
    value=compat.verify_loaded_model(model,torch,info)
    assert value["parameters"]==149606402 and value["scoring_requests"]==0
    assert value["layers"]==22 and value["local_window_each_side"]==64


@pytest.mark.parametrize("fault",["small_embedding","small_head","small_heads","small_count","rope","window","layers","dtype"])
def test_small_or_wrong_actual_loaded_structure_refuses_before_scoring(fault):
    model,torch,info=full_loaded_fixture()
    if fault=="small_embedding": model.model.embeddings.tok_embeddings.weight.shape=(256000,384)
    elif fault=="small_head": model.classifier.weight.shape=(2,384)
    elif fault=="small_heads": model.model.layers[0].attn.num_heads=6
    elif fault=="small_count": model.parameters=lambda:[SimpleNamespace(device=SimpleNamespace(type="cpu"),dtype="float32",numel=lambda:140642306)]
    elif fault=="rope": model.model.layers[1].attn.rotary_emb.config.rope_theta=160000.0
    elif fault=="window": model.model.layers[1].attn.local_attention=(63,63)
    elif fault=="layers": model.model.layers.pop()
    else: model.parameters=lambda:[SimpleNamespace(device=SimpleNamespace(type="cpu"),dtype="float16",numel=lambda:149606402),*[SimpleNamespace(device=SimpleNamespace(type="cpu"),dtype="float32",numel=lambda:0) for _ in range(137)]]
    with pytest.raises(compat.ModernBertCompatibilityError): compat.verify_loaded_model(model,torch,info)

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


def test_full_loader_uses_one_local_model_one_empty_encode_and_no_forward(monkeypatch,tmp_path):
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
        if name.endswith('.configuration_modernbert'):return SimpleNamespace(ModernBertConfig=config_class)
        if name.endswith('.modeling_modernbert'):return SimpleNamespace(ModernBertForSequenceClassification=type(model))
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
    result,predict,metadata=compat.load_for_qualification(str(tmp_path))
    assert result is native and callable(predict)
    loads=[row for row in calls if row[0]=='pretrained'];assert len(loads)==1
    assert loads[0][1]==str(tmp_path)
    assert set(loads[0][2])=={'config','local_files_only','trust_remote_code','use_safetensors','torch_dtype','attn_implementation','output_loading_info'}
    assert {k:v for k,v in loads[0][2].items() if k!='config'}=={
        'local_files_only':True,'trust_remote_code':False,'use_safetensors':True,'torch_dtype':'float32',
        'attn_implementation':'sdpa','output_loading_info':True}
    assert [row for row in calls if row[0]=='bundle']==[('bundle',str(tmp_path))]*2
    assert metadata['scoring_requests']==0 and metadata['qualification_only'] is True and metadata['quality_verified'] is False
    assert metadata['single_template_ids']==[50281,50282]
    assert metadata['native_empty_validation_calls']==metadata['tokenizer_encode_calls']==1
    assert metadata['native_empty_ids']==[50281,50282] and backend.calls==['no_truncation','no_padding',('',True)]
    assert metadata['repository']==compat.REPOSITORY and metadata['revision']==compat.REVISION
    assert metadata['files']==dict(compat.FILES) and metadata['versions']==versions


@pytest.mark.parametrize('empty',[[],[50281],[50282,50281],[50281,50282,0],(50281,50282)])
def test_actual_native_empty_template_must_match_before_any_model_construction(empty):
    b=Backend();b.encode=lambda text,**kw:SimpleNamespace(ids=empty)
    with pytest.raises(compat.ModernBertCompatibilityError):compat.CandidateTokenizer(b,added_tokens())


def test_native_empty_works_with_original_production_window_algorithm_without_invented_tokens():
    from services.classifier.worker import window_jobs
    b=Backend([]);tokenizer=compat.CandidateTokenizer(b,added_tokens())
    assert window_jobs([{'text':''}],tokenizer,time.monotonic()+5)==[(0,[50281,50282])]
    assert b.calls==['no_truncation','no_padding',('',True),('',False)]


def test_false_local_theta_cannot_be_hidden_by_correct_global_theta():
    model,torch,info=full_loaded_fixture()
    model.model.layers[1].attn.rotary_emb.inv_freq=model.model.layers[0].attn.rotary_emb.inv_freq
    with pytest.raises(compat.ModernBertCompatibilityError):compat.verify_loaded_model(model,torch,info)


def test_parameter_tensor_count_must_match_actual_authenticated_header():
    model,torch,info=full_loaded_fixture();params=list(model.parameters());model.parameters=lambda:params[:-1]
    with pytest.raises(compat.ModernBertCompatibilityError):compat.verify_loaded_model(model,torch,info)
