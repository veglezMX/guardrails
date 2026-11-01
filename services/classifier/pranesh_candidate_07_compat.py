"""Explicit candidate07 native BERT compatibility for the pinned Transformers 4 runtime.

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

REPOSITORY = 'PraneshJs/PromptGuard'
REVISION = 'efe704d7bc35076d0b48b1651033e34b36c7b9a5'
MANIFEST_SHA256 = '0180c14320834f39227c8a965abc36e3028498bd9e211339bd32f7a9852cd64d'
CONFIG_SHA256 = '5c51b19526d1f22b7514f9b0113edefb160413e68f9d21626c06a6a1c7faa8de'
WEIGHTS_SHA256 = 'ca394195e0265a40342626503e759295780eb99f213d954e211cbffe5d631a46'
WEIGHTS_BYTES = 44692608
PARAMETERS = 11171074
FILES = MappingProxyType({'README.md': ('64ae0d2c3bcf435a9180176dab64c081f3c8d224f78efeb664a1f84f6067cde5', 10164), 'config.json': ('5c51b19526d1f22b7514f9b0113edefb160413e68f9d21626c06a6a1c7faa8de', 846), 'model.safetensors': ('ca394195e0265a40342626503e759295780eb99f213d954e211cbffe5d631a46', 44692608), 'tokenizer.json': ('79ea220416a57ca8acdf069d71875d5d2ddadef66021aeb6c120a7c546c04344', 711494), 'tokenizer_config.json': ('814a5fe911f2bfcebce8d052fc1696000b17100f82ab3b510b768193c1b70139', 487)})
LIBRARY_SOURCES = MappingProxyType({'models/bert/configuration_bert.py': ('76fe8eb30215a4d52b5ad23b5a63375da000f02f1907e4b72f3b39265f4b9159', 7314), 'models/bert/modeling_bert.py': ('89e5e5098b98a206c8835c6c00b52b40d6d078a5994c40f300f98173969137c2', 78330)})
SPECIAL_IDS = MappingProxyType({'[PAD]':0,'[UNK]':100,'[CLS]':101,'[SEP]':102,'[MASK]':103})
TOKENIZER_COMPONENT_SHA256 = MappingProxyType({'normalizer': '6a3fa93e4dde6c7896c3d09cc22730b0c9de08fcad23f328b200282108ad8e0c', 'pre_tokenizer': '68675889c62737f850f10d3138306aacd545af055548a996d526597ce0ed0f0d', 'post_processor': '4e3c80b366caeedfee88323c25447af79771b82b2608bf5547f59259b8d0f50c', 'decoder': '3aca5de17e552d212af4c1bdeee60f5162cb1a1112d497455f5a198c069b3eb6'})
TOKENIZER_MODEL_SETTINGS = {'type': 'WordPiece', 'unk_token': '[UNK]', 'continuing_subword_prefix': '##', 'max_input_chars_per_word': 100}
TOKENIZER_MAX_BYTES = 1024 * 1024
CONFIG_FIELDS = {'add_cross_attention': False, 'architectures': ['BertForSequenceClassification'], 'attention_probs_dropout_prob': 0.1, 'bos_token_id': None, 'classifier_dropout': None, 'dtype': 'float32', 'eos_token_id': None, 'hidden_act': 'gelu', 'hidden_dropout_prob': 0.1, 'hidden_size': 256, 'id2label': {'0': 'safe', '1': 'attack'}, 'initializer_range': 0.02, 'intermediate_size': 1024, 'is_decoder': False, 'label2id': {'attack': 1, 'safe': 0}, 'layer_norm_eps': 1e-12, 'max_position_embeddings': 512, 'model_type': 'bert', 'num_attention_heads': 4, 'num_hidden_layers': 4, 'pad_token_id': 0, 'problem_type': 'single_label_classification', 'tie_word_embeddings': True, 'transformers_version': '5.12.0', 'type_vocab_size': 2, 'use_cache': False, 'vocab_size': 30522}



class BertCompatibilityError(ValueError):
    """Finite compatibility refusal, without model/text/credential contents."""


def require(condition):
    if not condition:
        raise BertCompatibilityError("candidate07_runtime_semantics_unverified")


def native_config(raw):
    """No model translation: preserve the authenticated publisher config bytes."""
    require(type(raw) is bytes and len(raw) == FILES['config.json'][1]
            and hashlib.sha256(raw).hexdigest() == CONFIG_SHA256)
    value = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=nonfinite)
    require(value == CONFIG_FIELDS)
    return value


def verify_config(config):
    fields = {key:value for key,value in CONFIG_FIELDS.items()
              if key not in {'_name_or_path','id2label','label2id','dtype','torch_dtype','transformers_version'}}
    require(all(getattr(config,key,None) == value for key,value in fields.items())
            and config.num_labels == 2 and config.id2label == {0:'safe',1:'attack'}
            and config.label2id == {'safe':0,'attack':1}
            and getattr(config,'chunk_size_feed_forward',None) == 0
            and getattr(config,'position_embedding_type',None) == 'absolute')


def make_config(raw, config_class):
    config = config_class(**native_config(raw))
    verify_config(config)
    return config


def expected_shapes():
    """All 73 native tensor names/shapes; no tensor values or weight translation."""
    shapes = {
        'bert.embeddings.word_embeddings.weight': (30522,256),
        'bert.embeddings.position_embeddings.weight': (512,256),
        'bert.embeddings.token_type_embeddings.weight': (2,256),
        'bert.embeddings.LayerNorm.weight': (256,),
        'bert.embeddings.LayerNorm.bias': (256,),
        'bert.pooler.dense.weight': (256,256),
        'bert.pooler.dense.bias': (256,),
        'classifier.weight': (2,256),
        'classifier.bias': (2,),
    }
    for index in range(4):
        prefix = f'bert.encoder.layer.{index}.'
        for name in ('attention.self.query','attention.self.key','attention.self.value','attention.output.dense'):
            shapes[prefix + name + '.weight'] = (256,256)
            shapes[prefix + name + '.bias'] = (256,)
        for name in ('attention.output.LayerNorm','output.LayerNorm'):
            shapes[prefix + name + '.weight'] = (256,)
            shapes[prefix + name + '.bias'] = (256,)
        shapes[prefix + 'intermediate.dense.weight'] = (1024,256)
        shapes[prefix + 'intermediate.dense.bias'] = (1024,)
        shapes[prefix + 'output.dense.weight'] = (256,1024)
        shapes[prefix + 'output.dense.bias'] = (256,)
    return shapes


def verify_loaded_model(model, torch, loading_info):
    require(type(loading_info) is dict and set(loading_info) == {'missing_keys','unexpected_keys','mismatched_keys','error_msgs'}
            and all(type(value) is list and not value for value in loading_info.values()))
    require(type(model).__module__ == 'transformers.models.bert.modeling_bert'
            and type(model).__name__ == 'BertForSequenceClassification' and model.training is False
            and model.config._attn_implementation == 'eager')
    verify_config(model.config)
    expected = expected_shapes(); seen = set(); count = 0
    for name,parameter in model.named_parameters():
        require(name in expected and name not in seen and tuple(parameter.shape) == expected[name]
                and parameter.device.type == 'cpu' and parameter.dtype == torch.float32)
        count += parameter.numel(); seen.add(name)
    require(seen == set(expected) and len(seen) == 73 and count == PARAMETERS)
    embeddings = model.bert.embeddings
    layers = model.bert.encoder.layer
    require(len(layers) == 4 and embeddings.word_embeddings.padding_idx == 0
            and embeddings.LayerNorm.eps == 1e-12 and embeddings.position_embedding_type == 'absolute'
            and type(model.bert.pooler).__name__ == 'BertPooler'
            and type(model.bert.pooler).__module__ == 'transformers.models.bert.modeling_bert'
            and type(model.bert.pooler.activation).__name__ == 'Tanh')
    for layer in layers:
        attention = layer.attention.self
        require(type(attention).__name__ == 'BertSelfAttention'
                and type(attention).__module__ == 'transformers.models.bert.modeling_bert'
                and attention.num_attention_heads == 4 and attention.all_head_size == 256
                and attention.attention_head_size == 64 and attention.position_embedding_type == 'absolute'
                and attention.is_decoder is False and not layer.attention.pruned_heads
                and layer.is_decoder is False and layer.add_cross_attention is False
                and layer.chunk_size_feed_forward == 0
                and type(layer.intermediate.intermediate_act_fn).__name__ == 'GELUActivation'
                and layer.attention.output.LayerNorm.eps == layer.output.LayerNorm.eps == 1e-12)
    return {'model_type':'bert','parameters':count,'weight_tensors':len(seen),
        'dtype':'float32','device':'cpu','attention':'eager','layers':4,
        'classifier_pooling':'native_cls_dense_tanh','attack_softmax_index':1,
        'missing_weight_keys':0,'unexpected_weight_keys':0,'mismatched_weight_keys':0,
        'error_messages':0,'scoring_requests':0}


def verify_imported_modules(package, config_class, model_class):
    names = {'transformers.models.bert.configuration_bert':'models/bert/configuration_bert.py',
             'transformers.models.bert.modeling_bert':'models/bert/modeling_bert.py'}
    for name,relative in names.items():
        module = sys.modules.get(name)
        expected = package / relative
        require(module is not None and type(getattr(module,'__file__',None)) is str
                and Path(module.__file__).absolute() == expected and Path(module.__file__).resolve() == expected)
        _read_pinned(expected,LIBRARY_SOURCES[relative])
    config_module = sys.modules['transformers.models.bert.configuration_bert']
    model_module = sys.modules['transformers.models.bert.modeling_bert']
    require(config_class.__module__ == config_module.__name__ and model_class.__module__ == model_module.__name__
            and config_module.BertConfig is config_class
            and model_module.BertForSequenceClassification is model_class
            and model_module.BertConfig is config_class)


def verify_bundle(directory):
    """Authenticate the exact five public files, returning only two inputs."""
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
    raise BertCompatibilityError("candidate07_runtime_semantics_unverified")


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
                and backend.truncation == {"direction": "right", "max_length": 128,
                    "strategy": "longest_first", "stride": 0} and backend.padding is None)
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
    from transformers.models.bert.configuration_bert import BertConfig
    from transformers.models.bert.modeling_bert import (
        BertForSequenceClassification,
    )

    verify_imported_modules(package, BertConfig, BertForSequenceClassification)
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    tokenizer = make_tokenizer(captured["tokenizer.json"], Tokenizer)
    config = make_config(captured["config.json"], BertConfig)
    model, diagnostics = BertForSequenceClassification.from_pretrained(
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
            return validate_logits(model(input_ids=encoded, attention_mask=torch.ones_like(encoded),
                token_type_ids=torch.zeros_like(encoded)).logits, torch)

    return tokenizer, predict, metadata
