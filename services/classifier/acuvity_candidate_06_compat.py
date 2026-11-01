"""Explicit candidate06 native DistilBERT compatibility for the pinned Transformers 4 runtime.

Qualification-only seam. This module does not select a runtime model, import ML,
load weights, run a forward pass or mutate a downloaded file at import time.
The parent authenticates the complete model/dependency/source closure separately.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import stat
import sys
from pathlib import Path
from types import MappingProxyType

from services.classifier.wolf_full_compat import (
    _read_pinned,
    _stamp,
    _state,
    validate_logits,
)

REPOSITORY = 'acuvity/distilbert-base-uncased-prompt-injection-v0.1'
REVISION = 'cfb923f6cf14862c05deebe3ad0c0ce4e7cde6d9'
MANIFEST_SHA256 = '474f650267eaf8768ec57561eb1905ff87d254f0bb6f3e8b3faa5871128cf1d8'
CONFIG_SHA256 = '761938e21983f4831428273cb7230b11d3c6da9253e184ea34a69a235dda2d46'
WEIGHTS_SHA256 = 'b403f37b45c972b511f1734501d51b58dc8f94974fd66dd558c677cc2462a4a5'
WEIGHTS_BYTES = 267832560
PARAMETERS = 66955010
FILES = MappingProxyType({'README.md': ('207df75251d9e51bc1d69c4edb55e504968a98d754b4712a8aefa8c5087a61f1', 6783), 'config.json': ('761938e21983f4831428273cb7230b11d3c6da9253e184ea34a69a235dda2d46', 744), 'model.safetensors': ('b403f37b45c972b511f1734501d51b58dc8f94974fd66dd558c677cc2462a4a5', 267832560), 'special_tokens_map.json': ('5aa43c2f985a25296d5a5ce621c2f77376ca8091a47993c492fd5460b895b140', 732), 'tokenizer.json': ('cb374d6bc042c22455946f4e09a89d29882a199fdaf8fb25be00dc8b8857a448', 711661), 'tokenizer_config.json': ('aaff2a36793728cd86b5ac3c2f8cf34653d7cb838c1c1345446a8bfa4341bfcc', 1449), 'vocab.txt': ('07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3', 231508)})
LIBRARY_SOURCES = MappingProxyType({'models/distilbert/configuration_distilbert.py': ('876ac1287fdafda11d443a3ee4910701b57051fd7ee92cbac693518dd2ef8671',6055), 'models/distilbert/modeling_distilbert.py': ('2d900f7eac999845ccea9c0fee366f7a477bc4424fbc2d97daf17b7c8e828643',56462)})
SPECIAL_IDS = MappingProxyType({'[PAD]':0,'[UNK]':100,'[CLS]':101,'[SEP]':102,'[MASK]':103})
TOKENIZER_COMPONENT_SHA256 = MappingProxyType({name: pin for name, pin in {'normalizer': '6a3fa93e4dde6c7896c3d09cc22730b0c9de08fcad23f328b200282108ad8e0c', 'pre_tokenizer': '68675889c62737f850f10d3138306aacd545af055548a996d526597ce0ed0f0d', 'post_processor': '4e3c80b366caeedfee88323c25447af79771b82b2608bf5547f59259b8d0f50c', 'decoder': '3aca5de17e552d212af4c1bdeee60f5162cb1a1112d497455f5a198c069b3eb6'}.items()})
TOKENIZER_MODEL_SETTINGS = {'type': 'WordPiece', 'unk_token': '[UNK]', 'continuing_subword_prefix': '##', 'max_input_chars_per_word': 100}
TOKENIZER_MAX_BYTES = 1024 * 1024
CONFIG_FIELDS = {'_name_or_path': 'acuvity/prompt-injection', 'activation': 'gelu', 'architectures': ['DistilBertForSequenceClassification'], 'attention_dropout': 0.1, 'dim': 768, 'dropout': 0.1, 'hidden_dim': 3072, 'id2label': {'0': 'safe', '1': 'prompt_injection'}, 'initializer_range': 0.02, 'label2id': {'prompt_injection': 1, 'safe': 0}, 'max_position_embeddings': 512, 'model_type': 'distilbert', 'n_heads': 12, 'n_layers': 6, 'pad_token_id': 0, 'problem_type': 'single_label_classification', 'qa_dropout': 0.1, 'seq_classif_dropout': 0.2, 'sinusoidal_pos_embds': False, 'tie_weights_': True, 'torch_dtype': 'float32', 'transformers_version': '4.43.4', 'vocab_size': 30522}



class DistilBertCompatibilityError(ValueError):
    """Finite compatibility refusal, without model/text/credential contents."""


def require(condition):
    if not condition:
        raise DistilBertCompatibilityError("candidate06_runtime_semantics_unverified")


def native_config(raw):
    """No model translation: preserve the authenticated native v4 config bytes."""
    require(type(raw) is bytes and len(raw) == FILES['config.json'][1]
            and hashlib.sha256(raw).hexdigest() == CONFIG_SHA256)
    value = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=nonfinite)
    require(value == CONFIG_FIELDS)
    return value


def verify_config(config):
    fields = {key:value for key,value in CONFIG_FIELDS.items()
              if key not in {'_name_or_path','id2label','label2id','torch_dtype','transformers_version'}}
    require(all(getattr(config,key,None) == value for key,value in fields.items())
            and config.num_labels == 2 and config.id2label == {0:'safe',1:'prompt_injection'}
            and config.label2id == {'safe':0,'prompt_injection':1}
            and getattr(config,'chunk_size_feed_forward',0) == 0)


def make_config(raw, config_class):
    config = config_class(**native_config(raw))
    verify_config(config)
    return config


def verify_loaded_model(model, torch, loading_info):
    require(type(loading_info) is dict and set(loading_info) == {'missing_keys','unexpected_keys','mismatched_keys','error_msgs'}
            and all(type(value) is list and not value for value in loading_info.values()))
    require(type(model).__module__ == 'transformers.models.distilbert.modeling_distilbert'
            and type(model).__name__ == 'DistilBertForSequenceClassification' and model.training is False
            and model.config._attn_implementation == 'eager')
    verify_config(model.config)
    count,tensors = 0,0
    for parameter in model.parameters():
        require(parameter.device.type == 'cpu' and parameter.dtype == torch.float32)
        count += parameter.numel();tensors += 1
    embeddings = model.distilbert.embeddings
    layers = model.distilbert.transformer.layer
    require(count == PARAMETERS and tensors == 104 and len(layers) == 6
            and tuple(embeddings.word_embeddings.weight.shape) == (30522,768)
            and tuple(embeddings.position_embeddings.weight.shape) == (512,768)
            and embeddings.word_embeddings.padding_idx == 0 and embeddings.LayerNorm.eps == 1e-12
            and tuple(model.pre_classifier.weight.shape) == (768,768)
            and tuple(model.pre_classifier.bias.shape) == (768,)
            and tuple(model.classifier.weight.shape) == (2,768)
            and tuple(model.classifier.bias.shape) == (2,))
    for layer in layers:
        attention = layer.attention
        require(type(attention).__name__ == 'MultiHeadSelfAttention'
                and type(attention).__module__ == 'transformers.models.distilbert.modeling_distilbert'
                and attention.n_heads == 12 and attention.dim == 768 and attention.attention_head_size == 64
                and attention.is_causal is False and not attention.pruned_heads
                and all(tuple(getattr(attention,key).weight.shape) == (768,768)
                        for key in ('q_lin','k_lin','v_lin','out_lin'))
                and tuple(layer.ffn.lin1.weight.shape) == (3072,768)
                and tuple(layer.ffn.lin2.weight.shape) == (768,3072)
                and layer.ffn.chunk_size_feed_forward == 0
                and type(layer.ffn.activation).__name__ == 'GELUActivation'
                and layer.sa_layer_norm.eps == layer.output_layer_norm.eps == 1e-12)
    return {'model_type':'distilbert','parameters':count,'weight_tensors':tensors,
        'dtype':'float32','device':'cpu','attention':'eager','layers':6,
        'classifier_pooling':'native_cls_dense_relu','attack_softmax_index':1,
        'missing_weight_keys':0,'unexpected_weight_keys':0,'mismatched_weight_keys':0,
        'error_messages':0,'scoring_requests':0}


def verify_imported_modules(package, config_class, model_class):
    names = {'transformers.models.distilbert.configuration_distilbert':'models/distilbert/configuration_distilbert.py',
             'transformers.models.distilbert.modeling_distilbert':'models/distilbert/modeling_distilbert.py'}
    for name,relative in names.items():
        module = sys.modules.get(name)
        expected = package / relative
        require(module is not None and type(getattr(module,'__file__',None)) is str
                and Path(module.__file__).absolute() == expected and Path(module.__file__).resolve() == expected)
        _read_pinned(expected,LIBRARY_SOURCES[relative])
    config_module = sys.modules['transformers.models.distilbert.configuration_distilbert']
    model_module = sys.modules['transformers.models.distilbert.modeling_distilbert']
    require(config_class.__module__ == config_module.__name__ and model_class.__module__ == model_module.__name__
            and config_module.DistilBertConfig is config_class
            and model_module.DistilBertForSequenceClassification is model_class
            and model_module.DistilBertConfig is config_class)


def verify_bundle(directory):
    """Authenticate the exact seven public files, returning only two inputs."""
    require(all(type(pin) is str and len(pin) == 64 and type(size) is int and size > 0
                for pin, size in FILES.values()))
    root = Path(directory)
    require(root.is_absolute() and root.resolve() == root)
    before = root.stat(follow_symlinks=False)
    require(stat.S_ISDIR(before.st_mode) and before.st_uid == os.getuid() and not before.st_mode & 0o022)
    names = set()
    with os.scandir(root) as entries:
        for entry in entries:
            require(len(names) < len(FILES) and entry.name in FILES)
            names.add(entry.name)
    require(names == set(FILES))
    captured = {}
    for name, expected in FILES.items():
        path = root / name
        require(path.stat(follow_symlinks=False).st_uid == os.getuid())
        keep = name in {"config.json", "tokenizer.json"}
        value = _read_pinned(path, expected, keep=keep)
        if keep:
            captured[name] = value
    require(_stamp(root.stat(follow_symlinks=False)) == _stamp(before) and root.resolve() == root)
    return captured


def component_digest(value):
    """Compare complete native components, including the complete published WordPiece components."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def unique_pairs(rows):
    result = {}
    for key, value in rows:
        require(key not in result)
        result[key] = value
    return result


def nonfinite(_value):
    raise DistilBertCompatibilityError("candidate06_runtime_semantics_unverified")


class CandidateTokenizer:
    """The original native tokenizer with complete, nontruncating window input.

    No token strings/AddedToken flags are re-registered. The one manually
    assembled single-sequence template is authenticated against the actual
    backend template; the pinned config agrees with the same special IDs.
    """

    def __init__(self, backend, added_tokens):
        require(type(added_tokens) is list and len(added_tokens) == 5)
        self._backend = backend
        self._verify_components()
        require(type(backend.model).__name__ == "WordPiece" and backend.get_vocab_size(with_added_tokens=True) == 30522
                and backend.truncation == {"direction": "right", "max_length": 512,
                    "strategy": "longest_first", "stride": 0} and backend.padding == {"length":512,"pad_to_multiple_of":None,"pad_id":0,
                    "pad_type_id":0,"pad_token":"[PAD]","direction":"right"})
        actual = backend.get_added_tokens_decoder()
        require(set(actual) == {row["id"] for row in added_tokens})
        for row in added_tokens:
            require(set(row) == {"id", "content", "single_word", "lstrip", "rstrip", "normalized", "special"})
            token = actual[row["id"]]
            require(all(getattr(token, key) == value for key, value in row.items() if key != "id"))
        require(all(backend.token_to_id(token) == value for token, value in SPECIAL_IDS.items())
                and backend.num_special_tokens_to_add(False) == 2)
        backend.no_truncation()
        backend.no_padding()
        self._complete()
        # One empty native validation only; no predictor or model forward.
        empty=backend.encode("",add_special_tokens=True).ids
        self._complete()
        require(type(empty) is list and all(type(n) is int for n in empty) and empty == [101,102])
        self.native_empty_validation_calls=1

    def _verify_components(self):
        require(all(component_digest(_state(getattr(self._backend, name))) == expected
                    for name, expected in TOKENIZER_COMPONENT_SHA256.items()))

    def _complete(self):
        require(self._backend.truncation is None and self._backend.padding is None)

    def encode(self, text, *, add_special_tokens=False, truncation=False):
        require(type(text) is str and len(text) <= 65536
                and add_special_tokens is False and truncation is False)
        self._complete()
        result = self._backend.encode(text, add_special_tokens=False).ids
        self._complete()
        require(type(result) is list and all(type(item) is int and 0 <= item < 30522 for item in result))
        return result

    def num_special_tokens_to_add(self, *, pair=False):
        require(pair is False)
        self._complete()
        require(self._backend.num_special_tokens_to_add(False) == 2)
        return 2

    def build_inputs_with_special_tokens(self, tokens):
        require(type(tokens) is list and len(tokens) <= 510
                and all(type(item) is int and 0 <= item < 30522 for item in tokens))
        self._complete()
        require(component_digest(_state(self._backend.post_processor)) == TOKENIZER_COMPONENT_SHA256["post_processor"])
        return [101, *tokens, 102]


def make_tokenizer(raw, tokenizer_class):
    require(type(raw) is bytes and len(raw) == FILES["tokenizer.json"][1] <= TOKENIZER_MAX_BYTES
            and hashlib.sha256(raw).hexdigest() == FILES["tokenizer.json"][0])
    value = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=nonfinite)
    require(value["version"] == "1.0" and value["model"]["type"] == "WordPiece"
            and all(component_digest(value[name]) == expected for name, expected in TOKENIZER_COMPONENT_SHA256.items())
            and {key: value["model"][key] for key in TOKENIZER_MODEL_SETTINGS} == TOKENIZER_MODEL_SETTINGS
            and len(value["model"]["vocab"]) == 30522)
    # Construct from authenticated bytes, never a reread pathname or remote ID.
    return CandidateTokenizer(tokenizer_class.from_str(raw.decode("utf-8")), value["added_tokens"])


def load_for_qualification(directory):
    """Parent-only offline load; return an uninvoked predictor and finite proof.

    The parent must enforce the dedicated no-network process and outer deadline,
    authenticate the entire Python/29-wheel environment and retain stdout/stderr
    privately. This is not a quality result or permission to enable this model.
    """
    require(sys.version_info[:3] == (3, 13, 16))
    require(not any(name in sys.modules for name in ("torch", "transformers", "tokenizers")))
    versions = {"torch": "2.10.0+cpu", "transformers": "4.57.6", "tokenizers": "0.22.2", "sentencepiece": "0.2.1"}
    require(all(importlib.metadata.version(name) == expected for name, expected in versions.items()))
    captured = verify_bundle(directory)
    package = Path(importlib.metadata.distribution("transformers").locate_file("transformers")).resolve()
    for name, expected in LIBRARY_SOURCES.items():
        _read_pinned(package / name, expected)
    os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
        "TOKENIZERS_PARALLELISM": "false", "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "CUDA_VISIBLE_DEVICES": ""})
    import torch
    from tokenizers import Tokenizer
    from transformers.models.distilbert.configuration_distilbert import DistilBertConfig
    from transformers.models.distilbert.modeling_distilbert import (
        DistilBertForSequenceClassification,
    )

    verify_imported_modules(package, DistilBertConfig, DistilBertForSequenceClassification)
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    tokenizer = make_tokenizer(captured["tokenizer.json"], Tokenizer)
    config = make_config(captured["config.json"], DistilBertConfig)
    model, diagnostics = DistilBertForSequenceClassification.from_pretrained(
        str(directory), config=config, local_files_only=True, trust_remote_code=False,
        use_safetensors=True, torch_dtype=torch.float32, attn_implementation="eager", output_loading_info=True)
    model = model.to("cpu").eval()
    metadata = verify_loaded_model(model, torch, diagnostics)
    verify_bundle(directory)
    metadata.update({"repository": REPOSITORY, "revision": REVISION, "weights_sha256": WEIGHTS_SHA256,
        "config_sha256": CONFIG_SHA256, "files": dict(FILES), "versions": versions,
        "tokenizer": "native-tokenizers", "added_tokens": 5, "embedded_truncation_disabled": True,
        "single_template_ids": [101,102], "native_empty_validation_calls":1,
        "tokenizer_encode_calls":1, "native_empty_ids":[101,102], "candidate_manifest_sha256":MANIFEST_SHA256, "windows": {"maximum": 16, "tokens": 512, "stride": 256},
        "qualification_only": True, "quality_verified": False})

    def predict(tokens):
        require(type(tokens) is list and 2 <= len(tokens) <= 512
                and tokens[0] == 101 and tokens[-1] == 102
                and all(type(item) is int and 0 <= item < 30522 for item in tokens))
        with torch.inference_mode():
            encoded = torch.tensor([tokens], dtype=torch.long, device="cpu")
            return validate_logits(model(input_ids=encoded, attention_mask=torch.ones_like(encoded)).logits, torch)

    return tokenizer, predict, metadata
