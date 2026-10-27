"""Candidate03 fake-only semantic/closure tests: no real ML or network."""
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

from services.classifier import piguard_candidate_03_compat as compat
from services.classifier.shieldlm_compat import ShieldCompatibilityError

FIXTURES = Path(__file__).parents[3] / "mvp/tests/evolution/fixtures"
CONFIG = FIXTURES / "piguard-candidate-03-config.json"
COMPONENTS = json.loads((FIXTURES / "piguard-candidate-03-tokenizer-components.json").read_bytes())


@pytest.fixture(autouse=True)
def forbid_real_ml_and_network(monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"torch", "transformers", "tokenizers", "sentencepiece"}:
            pytest.fail("real ML package import forbidden")
        return original(name, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail("network forbidden")
    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "bind", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def config_class(**kw):
    kw["id2label"] = {int(k): v for k, v in kw["id2label"].items()}
    return SimpleNamespace(**kw, num_labels=2)


def test_exact_config_removes_only_unused_remote_dispatch_metadata():
    raw = CONFIG.read_bytes(); calls = []
    def factory(**kw):
        calls.append(kw.copy()); return config_class(**kw)
    config = compat.make_config(raw, factory)
    original = json.loads(raw)
    assert calls == [{k: v for k, v in original.items() if k != "auto_map"}]
    assert not hasattr(config, "auto_map") and config.model_type == "piguard"
    assert config.id2label == {0: "benign", 1: "injection"}
    assert raw == CONFIG.read_bytes()


@pytest.mark.parametrize("field,value", [("hidden_size", 384), ("num_hidden_layers", 24),
    ("max_position_embeddings", 1024), ("pos_att_type", ["c2p"]), ("position_buckets", 128),
    ("position_biased_input", True), ("relative_attention", False), ("share_att_key", False),
    ("id2label", {"0": "injection", "1": "benign"}), ("torch_dtype", "float16"),
    ("auto_map", {"AutoModel": "foreign"})])
def test_changed_configuration_never_reaches_native_constructor(field, value):
    document = json.loads(CONFIG.read_bytes()); document[field] = value
    with pytest.raises(compat.PIGuardCompatibilityError):
        compat.make_config(json.dumps(document).encode(), lambda **kw: pytest.fail("unbound factory"))


@pytest.mark.parametrize("field,value", [("position_biased_input", True), ("share_att_key", False),
    ("position_buckets", 128), ("num_labels", 1), ("id2label", {0: "injection", 1: "benign"}),
    ("label2id", None), ("conv_kernel_size", 3), ("auto_map", {"unused": "foreign"})])
def test_actual_consumed_configuration_cannot_fall_back(field, value):
    def factory(**kw):
        config = config_class(**kw); setattr(config, field, value); return config
    with pytest.raises(compat.PIGuardCompatibilityError): compat.make_config(CONFIG.read_bytes(), factory)


class Component:
    def __init__(self, value): self.value = json.loads(json.dumps(value))
    def __getstate__(self): return json.dumps(self.value).encode()


class Backend:
    def __init__(self, tokens=None):
        self.model = type("Unigram", (), {})()
        for name in compat.COMPONENT_SHA256: setattr(self, name, Component(COMPONENTS[name]))
        self.added = {row["id"]: SimpleNamespace(**{k: v for k, v in row.items() if k != "id"})
            for row in COMPONENTS["added_tokens"]}
        self.truncation = None; self.padding = None; self.ids = dict(compat.SPECIAL_IDS)
        self.tokens = [11, 12] if tokens is None else tokens; self.calls = []
    def get_vocab_size(self, *, with_added_tokens): return 128001 if with_added_tokens else 128000
    def get_added_tokens_decoder(self): return self.added
    def token_to_id(self, name): return self.ids[name]
    def num_special_tokens_to_add(self, pair): assert pair is False; return 2
    def no_truncation(self): self.calls.append("no_truncation"); self.truncation = None
    def no_padding(self): self.calls.append("no_padding"); self.padding = None
    def encode(self, text, *, add_special_tokens):
        self.calls.append((text, add_special_tokens))
        return SimpleNamespace(ids=[1, 2] if add_special_tokens else self.tokens.copy())


def tokenizer(backend=None):
    return compat.CandidateTokenizer(Backend() if backend is None else backend, COMPONENTS["added_tokens"])


def test_full_native_normalizer_flags_and_empty_template_are_preserved():
    b = Backend(); before = {key: vars(value).copy() for key, value in b.added.items()}
    t = tokenizer(b)
    assert b.calls == ["no_truncation", "no_padding", ("", True)]
    assert t.native_empty_validation_calls == 1 and t.num_special_tokens_to_add(pair=False) == 2
    assert t.build_inputs_with_special_tokens([]) == [1, 2]
    assert t.encode("  Original\tText  ") == [11, 12] and b.calls[-1] == ("  Original\tText  ", False)
    assert before == {key: vars(value) for key, value in b.added.items()}
    assert len(b.normalizer.value["normalizers"][1]["precompiled_charsmap"]) == 316720


@pytest.mark.parametrize("component", list(compat.COMPONENT_SHA256))
def test_changed_native_component_refuses_before_empty_encode(component):
    b = Backend(); getattr(b, component).value["foreign"] = True
    with pytest.raises(compat.PIGuardCompatibilityError): tokenizer(b)
    assert b.calls == []


@pytest.mark.parametrize("fault", ["model", "missing", "extra", "mask_lstrip", "unk_normalized", "cls_id",
    "vocab", "special_count", "truncation", "padding", "precompiled"])
def test_native_semantic_guard_refuses_before_any_encode(fault):
    b = Backend()
    if fault == "model": b.model = object()
    elif fault == "missing": del b.added[128000]
    elif fault == "extra": b.added[999] = b.added[0]
    elif fault == "mask_lstrip": b.added[128000].lstrip = True
    elif fault == "unk_normalized": b.added[3].normalized = False
    elif fault == "cls_id": b.ids["[CLS]"] = 2
    elif fault == "vocab": b.get_vocab_size = lambda **kw: 128100
    elif fault == "special_count": b.num_special_tokens_to_add = lambda pair: 0
    elif fault == "truncation": b.truncation = {"max_length": 512}
    elif fault == "padding": b.padding = {"length": 512}
    else: b.normalizer.value["normalizers"][1]["precompiled_charsmap"] += "A"
    with pytest.raises(compat.PIGuardCompatibilityError): tokenizer(b)
    assert b.calls == []


@pytest.mark.parametrize("empty", [[], [0], [2, 1], [True, 2], [1, 2, 2], (1, 2)])
def test_actual_native_empty_encoding_must_be_exact(empty):
    b = Backend(); b.encode = lambda *a, **kw: SimpleNamespace(ids=empty)
    with pytest.raises(compat.PIGuardCompatibilityError): tokenizer(b)


@pytest.mark.parametrize("method", ["encode", "num_special_tokens_to_add", "build_inputs_with_special_tokens"])
@pytest.mark.parametrize("field", ["truncation", "padding"])
def test_padding_or_truncation_reenable_refuses_all_operations(method, field):
    b = Backend(); t = tokenizer(b); setattr(b, field, {"enabled": True})
    args = {"encode": ("text",), "num_special_tokens_to_add": (), "build_inputs_with_special_tokens": ([11],)}
    with pytest.raises(compat.PIGuardCompatibilityError): getattr(t, method)(*args[method])
    assert b.calls == ["no_truncation", "no_padding", ("", True)]


@pytest.mark.parametrize("tokens", [[True], [-1], [128001], [1.5], (1,), [11] * 511])
def test_bad_window_refuses_without_truncation(tokens):
    t = tokenizer()
    with pytest.raises(compat.PIGuardCompatibilityError): t.build_inputs_with_special_tokens(tokens)


@pytest.mark.parametrize("length,expected", [(0, 1), (1, 1), (510, 1), (511, 2), (766, 2), (767, 3), (4350, 16)])
def test_original_window_algebra_covers_every_native_token_including_empty(length, expected):
    from services.classifier.worker import window_jobs
    b = Backend(list(range(10, 10 + length))); t = tokenizer(b)
    jobs = window_jobs([{"text": "synthetic full document"}], t, time.monotonic() + 5)
    assert len(jobs) == expected and all(ids[0] == 1 and ids[-1] == 2 for _, ids in jobs)
    assert [ids[1] for _, ids in jobs] == [10 + i * 256 for i in range(expected)] if length else jobs == [(0, [1, 2])]
    assert set().union(*(set(ids[1:-1]) for _, ids in jobs)) == set(range(10, 10 + length))


def test_original_window_limit_refuses_before_any_forward():
    from guardrails.evolution.classifier_contract import ClassifierError

    from services.classifier.worker import window_jobs
    t = tokenizer(Backend(list(range(10, 4361))))
    with pytest.raises(ClassifierError, match="inspection_incomplete"):
        window_jobs([{"text": "synthetic"}], t, time.monotonic() + 5)


def test_published_forward_uses_only_first_encoder_token_and_checkpoint_linear():
    calls = []
    class Hidden:
        def __getitem__(self, key):
            assert key == (slice(None), 0, slice(None)); calls.append("cls_slice"); return "first-token"
    def encoder(**kwargs): calls.append(kwargs); return SimpleNamespace(last_hidden_state=Hidden())
    def head(value): assert value == "first-token"; calls.append("classifier"); return "logits"
    def forbidden(*args, **kwargs): pytest.fail("native pooler/dropout/base classifier forward invoked")
    model = SimpleNamespace(deberta=encoder, classifier=head, pooler=forbidden, dropout=forbidden, forward=forbidden)
    assert compat.published_logits(model, "input", "ones") == "logits"
    assert calls == [{"input_ids": "input", "attention_mask": "ones", "output_hidden_states": False}, "cls_slice", "classifier"]


@pytest.mark.parametrize("raw", [b"{}", b"", b"x", "not-bytes"])
def test_unpinned_tokenizer_never_constructs_native_backend(raw):
    class Never:
        @staticmethod
        def from_str(_): pytest.fail("unbound native tokenizer")
    with pytest.raises(compat.PIGuardCompatibilityError): compat.make_tokenizer(raw, Never)


def test_wrong_interpreter_refuses_before_any_bundle_or_ml_import(monkeypatch):
    monkeypatch.setattr(compat.sys, "version_info", (3, 14, 0))
    monkeypatch.setattr(compat, "verify_bundle", lambda _: pytest.fail("bundle read"))
    with pytest.raises(compat.PIGuardCompatibilityError): compat.load_for_qualification("/unused")


@pytest.mark.parametrize("name", ["torch", "transformers", "tokenizers"])
def test_preimported_ml_refuses_before_bundle_or_model(monkeypatch, name):
    monkeypatch.setattr(compat.sys, "version_info", (3, 13, 16))
    monkeypatch.setitem(compat.sys.modules, name, ModuleType(name))
    monkeypatch.setattr(compat, "verify_bundle", lambda _: pytest.fail("bundle read"))
    with pytest.raises(compat.PIGuardCompatibilityError): compat.load_for_qualification("/unused")


def test_authenticated_complete_tokenizer_bytes_reach_native_constructor_unchanged(monkeypatch):
    value = {"version": "1.0", "truncation": None, "padding": None,
        "model": {"type": "Unigram", "unk_id": 3, "byte_fallback": False,
            "vocab": [["synthetic", 0.0]] * 128000}, **COMPONENTS}
    raw = json.dumps(value, ensure_ascii=False).encode(); pins = dict(compat.FILES)
    pins["tokenizer.json"] = (hashlib.sha256(raw).hexdigest(), len(raw))
    monkeypatch.setattr(compat, "FILES", pins)
    b = Backend(); calls = []
    class Native:
        @staticmethod
        def from_str(text): calls.append(text); return b
    t = compat.make_tokenizer(raw, Native)
    assert calls == [raw.decode()] and t.num_special_tokens_to_add(pair=False) == 2
    assert b.calls == ["no_truncation", "no_padding", ("", True)]


@pytest.mark.parametrize("fault", ["normalizer", "template", "truncation", "padding", "vocab", "fallback", "unk"])
def test_authenticated_but_semantically_wrong_tokenizer_refuses_before_native_constructor(monkeypatch, fault):
    value = json.loads(json.dumps({"version": "1.0", "truncation": None, "padding": None,
        "model": {"type": "Unigram", "unk_id": 3, "byte_fallback": False,
            "vocab": [["synthetic", 0.0]] * 128000}, **COMPONENTS}))
    if fault == "normalizer": value["normalizer"] = {"type": "NFC"}
    elif fault == "template": value["post_processor"]["single"] = [{"Sequence": {"id": "A", "type_id": 0}}]
    elif fault in {"truncation", "padding"}: value[fault] = {"length": 512}
    elif fault == "vocab": value["model"]["vocab"].pop()
    elif fault == "fallback": value["model"]["byte_fallback"] = True
    else: value["model"]["unk_id"] = 1
    raw = json.dumps(value).encode(); pins = dict(compat.FILES)
    pins["tokenizer.json"] = (hashlib.sha256(raw).hexdigest(), len(raw)); monkeypatch.setattr(compat, "FILES", pins)
    class Never:
        @staticmethod
        def from_str(_): pytest.fail("semantic refusal must precede construction")
    with pytest.raises(compat.PIGuardCompatibilityError): compat.make_tokenizer(raw, Never)


def tiny_bundle(monkeypatch,tmp_path):
    root=tmp_path/'model';root.mkdir(mode=0o700);pins={}
    for name in compat.FILES:
        raw=('synthetic:'+name).encode();p=root/name;p.write_bytes(raw);p.chmod(0o600)
        pins[name]=(hashlib.sha256(raw).hexdigest(),len(raw))
    monkeypatch.setattr(compat,'FILES',pins);return root

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
    with pytest.raises((compat.PIGuardCompatibilityError, ShieldCompatibilityError)):compat.verify_bundle(root)

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

@pytest.mark.parametrize('field',['missing_keys','unexpected_keys','mismatched_keys','error_msgs'])
def test_any_loading_diagnostic_refuses(field):
    model,torch,info,_=loaded_fixture();info[field]=['synthetic']
    with pytest.raises((compat.PIGuardCompatibilityError, ShieldCompatibilityError)):compat.verify_loaded_model(model,torch,info)

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
    with pytest.raises((compat.PIGuardCompatibilityError, ShieldCompatibilityError)):compat.verify_loaded_model(m,t,i)

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
    assert metadata['single_template_ids']==[1,2] and metadata['native_empty_ids']==[1,2]
    assert metadata['native_empty_validation_calls']==metadata['tokenizer_encode_calls']==1
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
        with pytest.raises((compat.PIGuardCompatibilityError, ShieldCompatibilityError)):compat.validate_logits(logits,torch)
