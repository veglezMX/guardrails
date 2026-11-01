"""Explicit candidate02 native ModernBERT config translation for the pinned Transformers 4 runtime.

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
    LIBRARY_SOURCES,
    _read_pinned,
    _stamp,
    _state,
    validate_logits,
    verify_imported_modules,
)

REPOSITORY = 'siberiancat/modernbert-prompt-injection'
REVISION = 'fd9d17421e2e6bbe2eeea1874269fddc64e95e03'
MANIFEST_SHA256 = "57b55a5edcc0692477725c22f206fc8ef25d126007837a7d2909d64c783ca404"
CONFIG_SHA256 = '9c8a7d025f35c85687ab3ab9efb351add930a597428ec7c0dfb08a69299a2616'
WEIGHTS_SHA256 = '0e70e2e0c02c802c98b0a1dd423aed988b4bba4a925cf0927d0dc835d4d4858f'
WEIGHTS_BYTES = 598439784
PARAMETERS = 149606402
TRANSLATION = "candidate02-fd9d1742-transformers4576-rope-v1"
FILES = MappingProxyType({'README.md': ('c1aa3ae8c8333c206bae69c2ecfe2986ba1e3fff71474cb0c85df84abb5ceb78', 4372), 'config.json': ('9c8a7d025f35c85687ab3ab9efb351add930a597428ec7c0dfb08a69299a2616', 1989), 'model.safetensors': ('0e70e2e0c02c802c98b0a1dd423aed988b4bba4a925cf0927d0dc835d4d4858f', 598439784), 'tokenizer.json': ('a4285a69e0471620769dce979c70a6994eb7ff1554da8fb4bdbc7ad3eb5ad666', 3583485), 'tokenizer_config.json': ('fb54f027372062b2ca52282efb04d178a8b57167a00cd8f4e816515823a2c016', 380)})
SPECIAL_IDS = MappingProxyType({'[CLS]': 50281, '[SEP]': 50282, '[PAD]': 50283, '[UNK]': 50280, '[MASK]': 50284})
TOKENIZER_COMPONENTS = {'normalizer': {'type': 'NFC'}, 'pre_tokenizer': {'type': 'ByteLevel', 'add_prefix_space': False, 'trim_offsets': True, 'use_regex': True}, 'decoder': {'type': 'ByteLevel', 'add_prefix_space': False, 'trim_offsets': True, 'use_regex': True}, 'post_processor': {'type': 'TemplateProcessing', 'single': [{'SpecialToken': {'id': '[CLS]', 'type_id': 0}}, {'Sequence': {'id': 'A', 'type_id': 0}}, {'SpecialToken': {'id': '[SEP]', 'type_id': 0}}], 'pair': [{'SpecialToken': {'id': '[CLS]', 'type_id': 0}}, {'Sequence': {'id': 'A', 'type_id': 0}}, {'SpecialToken': {'id': '[SEP]', 'type_id': 0}}, {'Sequence': {'id': 'B', 'type_id': 0}}, {'SpecialToken': {'id': '[SEP]', 'type_id': 0}}], 'special_tokens': {'[CLS]': {'id': '[CLS]', 'ids': [50281], 'tokens': ['[CLS]']}, '[MASK]': {'id': '[MASK]', 'ids': [50284], 'tokens': ['[MASK]']}, '[PAD]': {'id': '[PAD]', 'ids': [50283], 'tokens': ['[PAD]']}, '[SEP]': {'id': '[SEP]', 'ids': [50282], 'tokens': ['[SEP]']}, '[UNK]': {'id': '[UNK]', 'ids': [50280], 'tokens': ['[UNK]']}}}}


class ModernBertCompatibilityError(ValueError):
    """Finite compatibility refusal, without model/text/credential contents."""


def require(condition):
    if not condition:
        raise ModernBertCompatibilityError("candidate02_runtime_semantics_unverified")


def translated_config(raw):
    """Translate only the one approved immutable config; never rewrite it.

    Transformers4 ignores v5 rope_parameters; map each declared theta to the
    corresponding consumed v4 field explicitly, without relying on defaults. The
    explicit layer list must equal v4's cadence; otherwise no translation.
    position_embedding_type is inert in both pinned ModernBERT implementations,
    not authority to disable their rotary embeddings. Keep it as provenance.
    """
    require(type(raw) is bytes and len(raw) == 1989 and hashlib.sha256(raw).hexdigest() == CONFIG_SHA256)
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result)
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ModernBertCompatibilityError("candidate02_runtime_semantics_unverified")))
    require(type(value) is dict and value["model_type"] == "modernbert"
            and value["architectures"] == ["ModernBertForSequenceClassification"]
            and value["transformers_version"] == "5.13.0"
            and value["position_embedding_type"] == "absolute"
            and value["dtype"] == "float32" and value["classifier_pooling"] == "mean"
            and "id2label" not in value and "label2id" not in value
            and value["max_position_embeddings"] == 8192
            and value["hidden_size"] == 768 and value["num_attention_heads"] == 12
            and value["num_hidden_layers"] == 22 and value["global_attn_every_n_layers"] == 3
            and value["local_attention"] == 128 and value["intermediate_size"] == 1152
            and value["vocab_size"] == 50368 and value["norm_eps"] == 1e-5)
    pattern = ["full_attention" if i % 3 == 0 else "sliding_attention" for i in range(22)]
    require(value["layer_types"] == pattern
            and value["rope_parameters"] == {"full_attention": {"rope_theta":160000,"rope_type":"default"},
                                              "sliding_attention": {"rope_theta":10000,"rope_type":"default"}}
            and {key: value[key] for key in ("pad_token_id", "bos_token_id", "eos_token_id", "cls_token_id", "sep_token_id")}
            == {"pad_token_id":50283,"bos_token_id":50281,"eos_token_id":50282,"cls_token_id":50281,"sep_token_id":50282})
    # No unknown rope keys reach v4's ignored **kwargs. Other original metadata
    # stays intact; source-version provenance is deliberately retained as5.13.0.
    value.pop("rope_parameters")
    value.pop("layer_types")
    value["global_rope_theta"] = 160000.0
    value["local_rope_theta"] = 10000.0
    value["torch_dtype"] = "float32"
    value["reference_compile"] = False
    return value


def make_config(raw, config_class):
    """Called only by parent-authorized load qualification; no default AutoConfig."""
    config = config_class(**translated_config(raw))
    require(config.model_type == "modernbert" and config.max_position_embeddings == 8192
            and config.global_rope_theta == 160000.0 and config.local_rope_theta == 10000.0
            and config.global_attn_every_n_layers == 3 and config.local_attention == 128
            and config.classifier_pooling == "mean" and config.reference_compile is False
            and config.num_labels == 2 and config.id2label == {0: "LABEL_0", 1: "LABEL_1"}
            and config.label2id == {"LABEL_0": 0, "LABEL_1": 1}
            and not hasattr(config, "rope_parameters") and not hasattr(config, "rope_scaling"))
    return config


def verify_loaded_model(model, torch, loading_info):
    """Inspect actual loaded CPU modules and buffers before any scoring request.

    This is not a loading/quality proof until a parent invokes it with the actual
    closed-bundle model. Missing, unexpected or mismatched weight keys refuse.
    """
    require(type(loading_info) is dict and set(loading_info) == {"missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"}
            and all(type(v) is list and not v for v in loading_info.values()))
    require(type(model).__module__ == "transformers.models.modernbert.modeling_modernbert"
            and type(model).__name__ == "ModernBertForSequenceClassification" and model.training is False)
    config = model.config
    require(config._attn_implementation == "sdpa" and config.reference_compile is False
            and config.model_type == "modernbert" and config.classifier_pooling == "mean"
            and config.global_rope_theta == 160000.0 and config.local_rope_theta == 10000.0
            and config.global_attn_every_n_layers == 3 and config.local_attention == 128
            and config.max_position_embeddings == 8192 and config.num_labels == 2
            and config.id2label == {0: "LABEL_0", 1: "LABEL_1"})
    count = 0
    tensors = 0
    for parameter in model.parameters():
        require(parameter.device.type == "cpu" and parameter.dtype == torch.float32)
        count += parameter.numel()
        tensors += 1
    require(count == PARAMETERS and tensors == 138 and len(model.model.layers) == 22
            and tuple(model.model.embeddings.tok_embeddings.weight.shape) == (50368, 768)
            and tuple(model.classifier.weight.shape) == (2, 768)
            and tuple(model.classifier.bias.shape) == (2,))
    for index, layer in enumerate(model.model.layers):
        attention = layer.attn
        require(attention.layer_id == index and attention.head_dim == 64 and attention.num_heads == 12
                and attention.local_attention == ((-1, -1) if index % 3 == 0 else (64, 64)))
        theta=160000.0 if index % 3 == 0 else 10000.0
        expected=1.0/(theta ** (torch.arange(0,64,2,dtype=torch.int64,device="cpu").float()/64))
        rotary = attention.rotary_emb
        require(rotary.rope_type == "default" and rotary.config.rope_theta == theta
                and rotary.attention_scaling == 1.0 and rotary.inv_freq.device.type == "cpu"
                and rotary.inv_freq.dtype == torch.float32 and tuple(rotary.inv_freq.shape) == (32,)
                and torch.equal(rotary.inv_freq, expected))
    return {"translation": TRANSLATION, "model_type": "modernbert", "parameters": count, "weight_tensors":tensors,
        "dtype": "float32", "device": "cpu", "attention": "sdpa", "compile": False,
        "layers": 22, "global_layers": 8, "local_layers": 14, "local_window_each_side": 64,
        "global_rope_theta": 160000, "local_rope_theta": 10000, "classifier_pooling": "mean",
        "missing_weight_keys": 0, "unexpected_weight_keys": 0, "mismatched_weight_keys": 0,
        "error_messages": 0, "scoring_requests": 0}


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


class CandidateTokenizer:
    """The original native tokenizer with complete, nontruncating window input.

    No token strings/AddedToken flags are re-registered. The one manually
    assembled single-sequence template is authenticated against the actual
    backend template; the pinned config agrees with the same special IDs.
    """

    def __init__(self, backend, added_tokens):
        require(type(added_tokens) is list and len(added_tokens) == 116)
        self._backend = backend
        self._verify_components()
        require(type(backend.model).__name__ == "BPE" and backend.get_vocab_size(with_added_tokens=True) == 50368
                and backend.truncation == {"direction": "right", "max_length": 256,
                    "strategy": "longest_first", "stride": 0} and backend.padding == {"length":None,"direction":"right","pad_to_multiple_of":None,
                    "pad_id":50283,"pad_type_id":0,"pad_token":"[PAD]"})
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
        require(type(empty) is list and all(type(n) is int for n in empty) and empty == [50281,50282])
        self.native_empty_validation_calls=1

    def _verify_components(self):
        require(all(_state(getattr(self._backend, name)) == expected
                    for name, expected in TOKENIZER_COMPONENTS.items()))

    def _complete(self):
        require(self._backend.truncation is None and self._backend.padding is None)

    def encode(self, text, *, add_special_tokens=False, truncation=False):
        require(type(text) is str and len(text) <= 65536
                and add_special_tokens is False and truncation is False)
        self._complete()
        result = self._backend.encode(text, add_special_tokens=False).ids
        self._complete()
        require(type(result) is list and all(type(item) is int and 0 <= item < 50368 for item in result))
        return result

    def num_special_tokens_to_add(self, *, pair=False):
        require(pair is False)
        self._complete()
        require(self._backend.num_special_tokens_to_add(False) == 2)
        return 2

    def build_inputs_with_special_tokens(self, tokens):
        require(type(tokens) is list and len(tokens) <= 510
                and all(type(item) is int and 0 <= item < 50368 for item in tokens))
        self._complete()
        require(_state(self._backend.post_processor) == TOKENIZER_COMPONENTS["post_processor"])
        return [50281, *tokens, 50282]


def make_tokenizer(raw, tokenizer_class):
    require(type(raw) is bytes and len(raw) == FILES["tokenizer.json"][1]
            and hashlib.sha256(raw).hexdigest() == FILES["tokenizer.json"][0])
    value = json.loads(raw)
    require(value["version"] == "1.0" and value["model"]["type"] == "BPE"
            and all(value[name] == expected for name, expected in TOKENIZER_COMPONENTS.items()))
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
    from transformers.models.modernbert.configuration_modernbert import ModernBertConfig
    from transformers.models.modernbert.modeling_modernbert import (
        ModernBertForSequenceClassification,
    )

    verify_imported_modules(package, ModernBertConfig, ModernBertForSequenceClassification)
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    tokenizer = make_tokenizer(captured["tokenizer.json"], Tokenizer)
    config = make_config(captured["config.json"], ModernBertConfig)
    model, diagnostics = ModernBertForSequenceClassification.from_pretrained(
        str(directory), config=config, local_files_only=True, trust_remote_code=False,
        use_safetensors=True, torch_dtype=torch.float32, attn_implementation="sdpa", output_loading_info=True)
    model = model.to("cpu").eval()
    metadata = verify_loaded_model(model, torch, diagnostics)
    verify_bundle(directory)
    metadata.update({"repository": REPOSITORY, "revision": REVISION, "weights_sha256": WEIGHTS_SHA256,
        "config_sha256": CONFIG_SHA256, "files": dict(FILES), "versions": versions,
        "tokenizer": "native-tokenizers", "added_tokens": 116, "embedded_truncation_disabled": True,
        "single_template_ids": [50281,50282], "native_empty_validation_calls":1,
        "tokenizer_encode_calls":1, "native_empty_ids":[50281,50282], "candidate_manifest_sha256":MANIFEST_SHA256, "windows": {"maximum": 16, "tokens": 512, "stride": 256},
        "qualification_only": True, "quality_verified": False})

    def predict(tokens):
        require(type(tokens) is list and 2 <= len(tokens) <= 512
                and tokens[0] == 50281 and tokens[-1] == 50282
                and all(type(item) is int and 0 <= item < 50368 for item in tokens))
        with torch.inference_mode():
            encoded = torch.tensor([tokens], dtype=torch.long, device="cpu")
            return validate_logits(model(input_ids=encoded, attention_mask=torch.ones_like(encoded)).logits, torch)

    return tokenizer, predict, metadata
