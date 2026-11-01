"""Explicit candidate04 native ModernBERT config translation for the pinned Transformers 4 runtime.

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

REPOSITORY = 'Horizon-Labs/prompt-injection-guard-small'
REVISION = 'e0e88c4e940ee7e1b52bdf943d501ec696b83b9b'
MANIFEST_SHA256 = 'd46f956fff633ffe23a2c7311c08125246b208ff424c15ea99e6442bb75fd1ca'
CONFIG_SHA256 = 'd6264037c9aa4b3d821c1c9a0329572033adbdb622788ecca91c3636066d9622'
WEIGHTS_SHA256 = 'a23196c117baf414b4b8abd138c5f5656e728877c0bdb2775c361ee89df16deb'
WEIGHTS_BYTES = 562583392
PARAMETERS = 140642306
TRANSLATION = "candidate04-e0e88c4e-transformers4576-rope-v1"
FILES = MappingProxyType({'README.md': ('a9f0632cb16789448e9923a3411654ecc3beb1ba0d262938dc881f09f14af87e', 20560), 'config.json': ('d6264037c9aa4b3d821c1c9a0329572033adbdb622788ecca91c3636066d9622', 2031), 'model.safetensors': ('a23196c117baf414b4b8abd138c5f5656e728877c0bdb2775c361ee89df16deb', 562583392), 'special_tokens_map.json': ('baec30ea10906f16adb8c18af7a34023002c1746542612b8b41c9f09e1351351', 636), 'tokenizer.json': ('47834a7dbbb0c4324fe9569fe47955241594a9ee539c9e478755d6fbc51e5f46', 34387555), 'tokenizer_config.json': ('ea349495764b24570b4560fe2e4a557e5c0a47e6bce782c2409b1d6445cf8633', 46440)})
SPECIAL_IDS = MappingProxyType({'<pad>': 0, '<eos>': 1, '<bos>': 2, '<unk>': 3, '<mask>': 4, '<start_of_turn>': 106, '<end_of_turn>': 107})
TOKENIZER_COMPONENT_SHA256 = MappingProxyType({'normalizer': '1c201079da30bc87d7fd83540317094bc38909f464dcf3d41eba4db89a297bb0', 'pre_tokenizer': 'fddbb5aee17fd4aaefee35092ee329a6e5c3107d84157319a82686a4f6bcd882', 'post_processor': '5bb1a7d53f23210ef6d2756ff8b540de6f6478f80da4d8e4caf878d4ce913246', 'decoder': 'f846d4ed93a314c1b928bf824285f4c45188e36315195cd443e5c542fb840dba'})
TOKENIZER_MODEL_SETTINGS = {'type': 'BPE', 'dropout': None, 'unk_token': '<unk>', 'continuing_subword_prefix': None, 'end_of_word_suffix': None, 'fuse_unk': True, 'byte_fallback': True, 'ignore_merges': False}
TOKENIZER_MAX_BYTES = 64 * 1024 * 1024



class ModernBertCompatibilityError(ValueError):
    """Finite compatibility refusal, without model/text/credential contents."""


def require(condition):
    if not condition:
        raise ModernBertCompatibilityError("candidate04_runtime_semantics_unverified")


def translated_config(raw):
    """Translate only the one approved immutable config; never rewrite it.

    Transformers4 ignores v5 rope_parameters; map each declared theta to the
    corresponding consumed v4 field explicitly, without relying on defaults. The
    explicit layer list must equal v4's cadence; otherwise no translation.
    position_embedding_type is inert in both pinned ModernBERT implementations,
    not authority to disable their rotary embeddings. Keep it as provenance.
    """
    require(type(raw) is bytes and len(raw) == 2031 and hashlib.sha256(raw).hexdigest() == CONFIG_SHA256)
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result)
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ModernBertCompatibilityError("candidate04_runtime_semantics_unverified")))
    require(type(value) is dict and value["model_type"] == "modernbert"
            and value["architectures"] == ["ModernBertForSequenceClassification"]
            and value["transformers_version"] == "5.17.0"
            and value["position_embedding_type"] == "sans_pos"
            and value["dtype"] == "float32" and value["classifier_pooling"] == "mean"
            and value["id2label"] == {"0":"SAFE","1":"INJECTION"}
            and value["label2id"] == {"SAFE":0,"INJECTION":1}
            and value["max_position_embeddings"] == 8192
            and value["hidden_size"] == 384 and value["num_attention_heads"] == 6
            and value["num_hidden_layers"] == 22 and value["global_attn_every_n_layers"] == 3
            and value["local_attention"] == 128 and value["intermediate_size"] == 1152
            and value["vocab_size"] == 256000 and value["norm_eps"] == 1e-5)
    pattern = ["full_attention" if i % 3 == 0 else "sliding_attention" for i in range(22)]
    require(value["layer_types"] == pattern
            and value["rope_parameters"] == {"full_attention": {"rope_theta":160000,"rope_type":"default"},
                                              "sliding_attention": {"rope_theta":160000,"rope_type":"default"}}
            and {key: value[key] for key in ("pad_token_id", "bos_token_id", "eos_token_id", "cls_token_id", "sep_token_id")}
            == {"pad_token_id":0,"bos_token_id":2,"eos_token_id":1,"cls_token_id":1,"sep_token_id":1})
    # No unknown rope keys reach v4's ignored **kwargs. Other original metadata
    # stays intact; source-version provenance is deliberately retained as5.17.0.
    value.pop("rope_parameters")
    value.pop("layer_types")
    value["global_rope_theta"] = 160000.0
    value["local_rope_theta"] = 160000.0
    value["torch_dtype"] = "float32"
    value["reference_compile"] = False
    return value


def make_config(raw, config_class):
    """Called only by parent-authorized load qualification; no default AutoConfig."""
    config = config_class(**translated_config(raw))
    require(config.model_type == "modernbert" and config.max_position_embeddings == 8192
            and config.global_rope_theta == 160000.0 and config.local_rope_theta == 160000.0
            and config.global_attn_every_n_layers == 3 and config.local_attention == 128
            and config.classifier_pooling == "mean" and config.reference_compile is False
            and config.num_labels == 2 and config.id2label == {0: "SAFE", 1: "INJECTION"}
            and config.label2id == {"SAFE": 0, "INJECTION": 1}
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
            and config.global_rope_theta == 160000.0 and config.local_rope_theta == 160000.0
            and config.global_attn_every_n_layers == 3 and config.local_attention == 128
            and config.max_position_embeddings == 8192 and config.num_labels == 2
            and config.id2label == {0: "SAFE", 1: "INJECTION"})
    count = 0
    tensors = 0
    for parameter in model.parameters():
        require(parameter.device.type == "cpu" and parameter.dtype == torch.float32)
        count += parameter.numel()
        tensors += 1
    require(count == PARAMETERS and tensors == 138 and len(model.model.layers) == 22
            and tuple(model.model.embeddings.tok_embeddings.weight.shape) == (256000, 384)
            and tuple(model.classifier.weight.shape) == (2, 384)
            and tuple(model.classifier.bias.shape) == (2,))
    for index, layer in enumerate(model.model.layers):
        attention = layer.attn
        require(attention.layer_id == index and attention.head_dim == 64 and attention.num_heads == 6
                and attention.local_attention == ((-1, -1) if index % 3 == 0 else (64, 64)))
        theta=160000.0
        expected=1.0/(theta ** (torch.arange(0,64,2,dtype=torch.int64,device="cpu").float()/64))
        rotary = attention.rotary_emb
        require(rotary.rope_type == "default" and rotary.config.rope_theta == theta
                and rotary.attention_scaling == 1.0 and rotary.inv_freq.device.type == "cpu"
                and rotary.inv_freq.dtype == torch.float32 and tuple(rotary.inv_freq.shape) == (32,)
                and torch.equal(rotary.inv_freq, expected))
    return {"translation": TRANSLATION, "model_type": "modernbert", "parameters": count, "weight_tensors":tensors,
        "dtype": "float32", "device": "cpu", "attention": "sdpa", "compile": False,
        "layers": 22, "global_layers": 8, "local_layers": 14, "local_window_each_side": 64,
        "global_rope_theta": 160000, "local_rope_theta": 160000, "classifier_pooling": "mean",
        "missing_weight_keys": 0, "unexpected_weight_keys": 0, "mismatched_weight_keys": 0,
        "error_messages": 0, "scoring_requests": 0}


def verify_bundle(directory):
    """Authenticate the exact six public files, returning only two inputs."""
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
    """Compare complete native components, including all193 normalizer steps."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def unique_pairs(rows):
    result = {}
    for key, value in rows:
        require(key not in result)
        result[key] = value
    return result


def nonfinite(_value):
    raise ModernBertCompatibilityError("candidate04_runtime_semantics_unverified")


class CandidateTokenizer:
    """The original native tokenizer with complete, nontruncating window input.

    No token strings/AddedToken flags are re-registered. The one manually
    assembled single-sequence template is authenticated against the actual
    backend template; the pinned config agrees with the same special IDs.
    """

    def __init__(self, backend, added_tokens):
        require(type(added_tokens) is list and len(added_tokens) == 249)
        self._backend = backend
        self._verify_components()
        require(type(backend.model).__name__ == "BPE" and backend.get_vocab_size(with_added_tokens=True) == 256000
                and backend.truncation == {"direction": "right", "max_length": 1024,
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
        require(type(empty) is list and all(type(n) is int for n in empty) and empty == [2,1])
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
        require(type(result) is list and all(type(item) is int and 0 <= item < 256000 for item in result))
        return result

    def num_special_tokens_to_add(self, *, pair=False):
        require(pair is False)
        self._complete()
        require(self._backend.num_special_tokens_to_add(False) == 2)
        return 2

    def build_inputs_with_special_tokens(self, tokens):
        require(type(tokens) is list and len(tokens) <= 510
                and all(type(item) is int and 0 <= item < 256000 for item in tokens))
        self._complete()
        require(component_digest(_state(self._backend.post_processor)) == TOKENIZER_COMPONENT_SHA256["post_processor"])
        return [2, *tokens, 1]


def make_tokenizer(raw, tokenizer_class):
    require(type(raw) is bytes and len(raw) == FILES["tokenizer.json"][1] <= TOKENIZER_MAX_BYTES
            and hashlib.sha256(raw).hexdigest() == FILES["tokenizer.json"][0])
    value = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=nonfinite)
    require(value["version"] == "1.0" and value["model"]["type"] == "BPE"
            and all(component_digest(value[name]) == expected for name, expected in TOKENIZER_COMPONENT_SHA256.items())
            and {key: value["model"][key] for key in TOKENIZER_MODEL_SETTINGS} == TOKENIZER_MODEL_SETTINGS
            and len(value["model"]["vocab"]) == 256000 and len(value["model"]["merges"]) == 580604)
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
        "tokenizer": "native-tokenizers", "added_tokens": 249, "embedded_truncation_disabled": True,
        "single_template_ids": [2,1], "native_empty_validation_calls":1,
        "tokenizer_encode_calls":1, "native_empty_ids":[2,1], "candidate_manifest_sha256":MANIFEST_SHA256, "windows": {"maximum": 16, "tokens": 512, "stride": 256},
        "qualification_only": True, "quality_verified": False})

    def predict(tokens):
        require(type(tokens) is list and 2 <= len(tokens) <= 512
                and tokens[0] == 2 and tokens[-1] == 1
                and all(type(item) is int and 0 <= item < 256000 for item in tokens))
        with torch.inference_mode():
            encoded = torch.tensor([tokens], dtype=torch.long, device="cpu")
            return validate_logits(model(input_ids=encoded, attention_mask=torch.ones_like(encoded)).logits, torch)

    return tokenizer, predict, metadata
