"""Pinned candidate03 native DeBERTa and published direct CLS linear head.

Qualification only. Importing this module loads no ML package. Downloaded Python
is provenance only and never executed. The original native classifier object is
loaded to preserve every checkpoint tensor, including its unused pooler.
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

from services.classifier.shieldlm_compat import (
    LIBRARY_SOURCES,
    _read_pinned,
    _stamp,
    parse,
    validate_logits,
    verify_imported_modules,
)

REPOSITORY = "leolee99/PIGuard"
REVISION = "dd78b24e330193a22d2293ac66922dd4f982f563"
MANIFEST_SHA256 = "a14740056f9eb5fad372914539dbaa004c0d0bcc0cbc4c048016015dc020d2e7"
CONFIG_SHA256 = "f7ab538357be6b11364e4ad225595fe2a96b64f69506387a04e3cae699bac1d0"
WEIGHTS_SHA256 = "f90b9806de93b6286cda517300d4b55e5ce2e5ccbf8339dc59be21ca0dd9a25e"
PARAMETERS = 184423682
FILES = MappingProxyType({
    "README.md": ("edd81d29d8e2b8f1225afc3602e8be5201903e5c184c65d365534471c82811d0", 3123),
    "added_tokens.json": ("dc046d04c9b0ada7ae6f1dc89c465801799acdf0c9a6aab8c15a1b2d5ca4e91f", 23),
    "config.json": (CONFIG_SHA256, 1062),
    "model.safetensors": (WEIGHTS_SHA256, 737719272),
    "special_tokens_map.json": ("9463f61e1b109a8eb4688b829260d7c6b1e6dff04c98ff7269bb89e2b92369b9", 286),
    "tokenizer.json": ("5124ef2ead1a10a717703bc436de7f353da76d6340e4587719b42b1693707964", 8656624),
    "tokenizer_config.json": ("557b3d33d3f41b81ad769244e506549e98a1857d41dd58160aacd4d98d710b5a", 1284),
})
SPECIAL_IDS = MappingProxyType({"[PAD]": 0, "[CLS]": 1, "[SEP]": 2, "[UNK]": 3, "[MASK]": 128000})
# Canonical JSON hashes include the entire 316720-character Precompiled map.
# It is native opaque normalization data, never replaced with an approximation.
COMPONENT_SHA256 = MappingProxyType({
    "normalizer": "9d788f1f1ddbebe58568f226fab4969713c327d821e396bf05a217c903625cd4",
    "pre_tokenizer": "9fb1975ab83e3fb658575a44de4af367dc46683d0a541a4f67dfb1ae4d414171",
    "decoder": "dc1afc9d4c86f11e7cdb67cee3d96e9ec8ec493dc942e6af4aaf923947c1f1fd",
    "post_processor": "02878e7d22a2452f0f64757b240d34e052e15f33601e7632498f54e54c53c3bd",
})
ADDED_TOKENS_SHA256 = "584728e7cdf28b0a73e51cd7e8a5ad93f2936dd34b37cbad1222e1148444dae2"
CONFIG_FIELDS = {
    "model_type": "piguard", "architectures": ["PIGuard"], "transformers_version": "4.44.0",
    "torch_dtype": "float32", "hidden_size": 768, "num_hidden_layers": 12,
    "num_attention_heads": 12, "intermediate_size": 3072, "max_position_embeddings": 512,
    "max_relative_positions": -1, "position_buckets": 256, "relative_attention": True,
    "share_att_key": True, "pos_att_type": ["p2c", "c2p"], "position_biased_input": False,
    "norm_rel_ebd": "layer_norm", "layer_norm_eps": 1e-7, "vocab_size": 128100,
    "type_vocab_size": 0, "pad_token_id": 0, "pooler_hidden_size": 768,
    "pooler_hidden_act": "gelu", "pooler_dropout": 0, "hidden_act": "gelu",
}


class PIGuardCompatibilityError(ValueError):
    """Finite refusal, without input text or arbitrary library exception data."""


def require(value):
    if not value:
        raise PIGuardCompatibilityError("candidate03_runtime_semantics_unverified")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def make_config(raw, config_class):
    require(type(raw) is bytes and len(raw) == FILES["config.json"][1]
        and hashlib.sha256(raw).hexdigest() == CONFIG_SHA256)
    value = parse(raw)
    require(type(value) is dict and all(value.get(k) == v for k, v in CONFIG_FIELDS.items())
        and value["id2label"] == {"0": "benign", "1": "injection"}
        and value["label2id"] == {"benign": 0, "injection": 1}
        and value["auto_map"] == {"AutoConfig": "modeling_piguard.PIGuardConfig",
            "AutoModelForSequenceClassification": "modeling_piguard.PIGuard"})
    # An explicit native class consumes this original config; remove only the
    # unused dynamic-code dispatch metadata. Model/type provenance stays intact.
    value.pop("auto_map")
    config = config_class(**value)
    verify_config(config)
    return config


def verify_config(config):
    require(all(getattr(config, k, None) == v for k, v in CONFIG_FIELDS.items()
        if k not in {"torch_dtype", "transformers_version"})
        and config.num_labels == 2 and config.id2label == {0: "benign", 1: "injection"}
        and config.label2id == {"benign": 0, "injection": 1}
        and not hasattr(config, "auto_map") and getattr(config, "conv_kernel_size", 0) == 0)


def verify_loaded_model(model, torch, loading_info):
    require(type(loading_info) is dict and set(loading_info) == {
        "missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"}
        and all(type(v) is list and not v for v in loading_info.values()))
    require(type(model).__module__ == "transformers.models.deberta_v2.modeling_deberta_v2"
        and type(model).__name__ == "DebertaV2ForSequenceClassification"
        and model.training is False and model.config._attn_implementation == "eager")
    verify_config(model.config)
    count, tensors = 0, 0
    for parameter in model.parameters():
        require(parameter.device.type == "cpu" and parameter.dtype == torch.float32)
        count += parameter.numel()
        tensors += 1
    embeddings, encoder = model.deberta.embeddings, model.deberta.encoder
    require(count == PARAMETERS and tensors == 202 and len(encoder.layer) == 12
        and tuple(embeddings.word_embeddings.weight.shape) == (128100, 768)
        and embeddings.position_embeddings is None and embeddings.token_type_embeddings is None
        and embeddings.embed_proj is None and embeddings.position_biased_input is False
        and encoder.relative_attention is True and encoder.max_relative_positions == 512
        and encoder.position_buckets == 256 and encoder.norm_rel_ebd == ["layer_norm"]
        and encoder.conv is None and tuple(encoder.rel_embeddings.weight.shape) == (512, 768)
        and tuple(model.pooler.dense.weight.shape) == (768, 768)
        and tuple(model.classifier.weight.shape) == (2, 768)
        and tuple(model.classifier.bias.shape) == (2,))
    for layer in encoder.layer:
        attention = layer.attention.self
        require(attention.num_attention_heads == 12 and attention.attention_head_size == 64
            and attention.relative_attention is True and attention.share_att_key is True
            and attention.position_buckets == 256 and attention.max_relative_positions == 512
            and attention.pos_ebd_size == 256 and attention.pos_att_type == ["p2c", "c2p"])
    return {"model_type": "piguard", "parameters": count, "parameter_tensors": tensors,
        "dtype": "float32", "device": "cpu", "attention": "eager", "layers": 12,
        "classifier_pooling": "published_direct_cls_linear_no_pooler", "attack_softmax_index": 1,
        "label_authority_sha256": CONFIG_SHA256, "missing_weight_keys": 0,
        "unexpected_weight_keys": 0, "mismatched_weight_keys": 0, "error_messages": 0,
        "scoring_requests": 0}


def published_logits(model, input_ids, attention_mask):
    """Exact published inference operations, without downloaded Python execution."""
    outputs = model.deberta(input_ids=input_ids, attention_mask=attention_mask,
        output_hidden_states=False)
    return model.classifier(outputs.last_hidden_state[:, 0, :])


def verify_bundle(directory):
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
    captured, stamps = {}, {}
    for name, expected in FILES.items():
        path = root / name
        require(path.stat(follow_symlinks=False).st_uid == os.getuid())
        stamps[name] = _stamp(path.stat(follow_symlinks=False))
        keep = name in {"config.json", "tokenizer.json"}
        value = _read_pinned(path, expected, keep=keep)
        if keep:
            captured[name] = value
    require(_stamp(root.stat(follow_symlinks=False)) == _stamp(before) and root.resolve() == root
        and all(_stamp((root / name).stat(follow_symlinks=False)) == value for name, value in stamps.items()))
    return captured


def component_digest(component):
    raw = component.__getstate__()
    require(type(raw) is bytes and len(raw) <= 512 * 1024)
    return digest(parse(raw))


class CandidateTokenizer:
    """Native JSON normalization/flags/template, with one empty validation."""

    def __init__(self, backend, added_tokens):
        require(type(added_tokens) is list and len(added_tokens) == 5
            and digest(added_tokens) == ADDED_TOKENS_SHA256)
        self._backend = backend
        require(all(component_digest(getattr(backend, name)) == pin
            for name, pin in COMPONENT_SHA256.items()))
        require(type(backend.model).__name__ == "Unigram"
            and backend.get_vocab_size(with_added_tokens=False) == 128000
            and backend.get_vocab_size(with_added_tokens=True) == 128001
            and backend.truncation is None and backend.padding is None)
        actual = backend.get_added_tokens_decoder()
        require(set(actual) == set(SPECIAL_IDS.values()) == {row["id"] for row in added_tokens})
        for row in added_tokens:
            token = actual[row["id"]]
            require(all(getattr(token, key) == value for key, value in row.items() if key != "id"))
        require(all(backend.token_to_id(token) == value for token, value in SPECIAL_IDS.items())
            and backend.num_special_tokens_to_add(False) == 2)
        # Preserve the exported null settings, and recheck the actual native API.
        backend.no_truncation()
        backend.no_padding()
        self._complete()
        empty = backend.encode("", add_special_tokens=True).ids
        self._complete()
        require(type(empty) is list and all(type(n) is int for n in empty) and empty == [1, 2])
        self.native_empty_validation_calls = 1

    def _complete(self):
        require(self._backend.truncation is None and self._backend.padding is None)

    def encode(self, text, *, add_special_tokens=False, truncation=False):
        require(type(text) is str and len(text) <= 65536 and add_special_tokens is False and truncation is False)
        self._complete()
        result = self._backend.encode(text, add_special_tokens=False).ids
        self._complete()
        require(type(result) is list and len(result) <= 65536
            and all(type(item) is int and 0 <= item < 128001 for item in result))
        return result

    def num_special_tokens_to_add(self, *, pair=False):
        require(pair is False)
        self._complete()
        require(self._backend.num_special_tokens_to_add(False) == 2)
        return 2

    def build_inputs_with_special_tokens(self, tokens):
        require(type(tokens) is list and len(tokens) <= 510
            and all(type(item) is int and 0 <= item < 128001 for item in tokens))
        self._complete()
        require(component_digest(self._backend.post_processor) == COMPONENT_SHA256["post_processor"])
        return [1, *tokens, 2]


def make_tokenizer(raw, tokenizer_class):
    require(type(raw) is bytes and len(raw) == FILES["tokenizer.json"][1]
        and hashlib.sha256(raw).hexdigest() == FILES["tokenizer.json"][0])
    value = parse(raw)
    require(value["version"] == "1.0" and value["model"]["type"] == "Unigram"
        and value["model"]["unk_id"] == 3 and value["model"]["byte_fallback"] is False
        and len(value["model"]["vocab"]) == 128000 and value["truncation"] is None and value["padding"] is None
        and all(digest(value[name]) == pin for name, pin in COMPONENT_SHA256.items()))
    return CandidateTokenizer(tokenizer_class.from_str(raw.decode("utf-8")), value["added_tokens"])


def load_for_qualification(directory):
    """Parent-only strict offline CPU load; no predictor/model forward call."""
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
    from transformers.models.deberta_v2.configuration_deberta_v2 import DebertaV2Config
    from transformers.models.deberta_v2.modeling_deberta_v2 import (
        DebertaV2ForSequenceClassification,
    )

    verify_imported_modules(package, DebertaV2Config, DebertaV2ForSequenceClassification)
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    tokenizer = make_tokenizer(captured["tokenizer.json"], Tokenizer)
    config = make_config(captured["config.json"], DebertaV2Config)
    model, diagnostics = DebertaV2ForSequenceClassification.from_pretrained(
        str(directory), config=config, local_files_only=True, trust_remote_code=False,
        use_safetensors=True, torch_dtype=torch.float32, attn_implementation="eager", output_loading_info=True)
    model = model.to("cpu").eval()
    metadata = verify_loaded_model(model, torch, diagnostics)
    verify_bundle(directory)
    metadata.update({"repository": REPOSITORY, "revision": REVISION, "weights_sha256": WEIGHTS_SHA256,
        "config_sha256": CONFIG_SHA256, "files": dict(FILES), "versions": versions,
        "tokenizer": "native-tokenizers", "added_tokens": 5, "embedded_truncation_disabled": True,
        "embedded_padding_disabled": True, "single_template_ids": [1, 2],
        "native_empty_validation_calls": 1, "tokenizer_encode_calls": 1, "native_empty_ids": [1, 2],
        "candidate_manifest_sha256": MANIFEST_SHA256, "windows": {"maximum": 16, "tokens": 512, "stride": 256},
        "qualification_only": True, "quality_verified": False})

    def predict(tokens):
        require(type(tokens) is list and 2 <= len(tokens) <= 512 and tokens[0] == 1 and tokens[-1] == 2
            and all(type(item) is int and 0 <= item < 128001 for item in tokens))
        with torch.inference_mode():
            encoded = torch.tensor([tokens], dtype=torch.long, device="cpu")
            return validate_logits(published_logits(model, encoded, torch.ones_like(encoded)), torch)

    return tokenizer, predict, metadata
