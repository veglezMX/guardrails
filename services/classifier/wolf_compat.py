"""Explicit Wolf Small config translation for the pinned Transformers 4 runtime.

Qualification-only seam. This module does not select a runtime model, import ML,
load weights, run a forward pass or mutate a downloaded file at import time.
The parent authenticates the complete model/dependency/source closure separately.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import stat
import sys
from pathlib import Path
from types import MappingProxyType

REPOSITORY = "patronus-studio/wolf-defender-prompt-injection-small"
REVISION = "bcab2eff97bcabd7227849639e2d0d7a61b46c92"
CONFIG_SHA256 = "b5bfba7b100b4b1aa361e8160e5593695164d81ca09b47f3b26561332b520218"
WEIGHTS_SHA256 = "88574ea37b2aaff38468194c544a5918b5e160fb9ee630bb11bba0aa3f533cf9"
WEIGHTS_BYTES = 562583392
PARAMETERS = 140642306
CONFIG4_SHA256 = "1d02b4b9c5fb8adea0d22958def1d353246ea731cecf9d0aa4b372356b0ff768"
MODEL4_SHA256 = "1b23db2f59d3f80bd9af89fb6df3ce15affd90b5ffc09925e732450e694868ff"
TRANSLATION = "wolf-small-bcab2eff-transformers4576-rope-v1"
FILES = MappingProxyType({
    "LICENSE": ("cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30", 11358),
    "README.md": ("579530e7f49cf2e3ead3df04078b7ebfd8640ffe2948d5740fc925bb1e19e8d1", 21899),
    "config.json": (CONFIG_SHA256, 2084),
    "model.safetensors": (WEIGHTS_SHA256, WEIGHTS_BYTES),
    "special_tokens_map.json": ("97f6f6ef323755568c9dddf57b7b2bac4c2019e8c10c2ef1262001a9f6487124", 173),
    "tokenizer.json": ("7e426c3929b44e6ab4c931770b5f22b913280633f5a1c67c81e9ad64decef55c", 34363287),
    "tokenizer_config.json": ("14b147f2a4f939d9b12ab36e9633917040dd948fa78ce283b03402e4cf2c9cba", 574),
})
LIBRARY_SOURCES = MappingProxyType({
    "models/modernbert/configuration_modernbert.py": (CONFIG4_SHA256, 11493),
    "models/modernbert/modeling_modernbert.py": (MODEL4_SHA256, 67793),
    "modeling_rope_utils.py": ("55cc0c8cb76f592ab178b4662adcbdf4ad5012bb0b4249c0a96603141f18e9ac", 43192),
})
SPECIAL_IDS = MappingProxyType({"<pad>": 0, "<eos>": 1, "<bos>": 2, "<unk>": 3,
    "<mask>": 4, "<start_of_turn>": 106, "<end_of_turn>": 107})
TOKENIZER_COMPONENTS = {
    "normalizer": {"type": "Replace", "pattern": {"String": " "}, "content": "▁"},
    "pre_tokenizer": {"type": "Metaspace", "replacement": "▁", "prepend_scheme": "always", "split": True},
    "decoder": {"type": "Sequence", "decoders": [
        {"type": "Replace", "pattern": {"String": "▁"}, "content": " "},
        {"type": "ByteFallback"}, {"type": "Fuse"}]},
    "post_processor": {"type": "TemplateProcessing", "single": [
        {"SpecialToken": {"id": "<bos>", "type_id": 0}},
        {"Sequence": {"id": "A", "type_id": 0}},
        {"SpecialToken": {"id": "<eos>", "type_id": 0}}],
        "pair": [{"SpecialToken": {"id": "<bos>", "type_id": 0}},
            {"Sequence": {"id": "A", "type_id": 0}},
            {"SpecialToken": {"id": "<eos>", "type_id": 0}},
            {"Sequence": {"id": "B", "type_id": 0}},
            {"SpecialToken": {"id": "<eos>", "type_id": 0}}],
        "special_tokens": {"<bos>": {"id": "<bos>", "ids": [2], "tokens": ["<bos>"]},
            "<eos>": {"id": "<eos>", "ids": [1], "tokens": ["<eos>"]}}},
}


class WolfCompatibilityError(ValueError):
    """Finite compatibility refusal, without model/text/credential contents."""


def require(condition):
    if not condition:
        raise WolfCompatibilityError("wolf_runtime_semantics_unverified")


def translated_config(raw):
    """Translate only the one approved immutable config; never rewrite it.

    Transformers4 ignores v5 rope_parameters and would use local theta10000.
    Map both declared thetas to the corresponding consumed v4 fields. The
    explicit layer list must equal v4's cadence; otherwise no translation.
    position_embedding_type is inert in both pinned ModernBERT implementations,
    not authority to disable their rotary embeddings. Keep it as provenance.
    """
    require(type(raw) is bytes and len(raw) == 2084 and hashlib.sha256(raw).hexdigest() == CONFIG_SHA256)
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result)
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(WolfCompatibilityError("wolf_runtime_semantics_unverified")))
    require(type(value) is dict and value["model_type"] == "modernbert"
            and value["architectures"] == ["ModernBertForSequenceClassification"]
            and value["transformers_version"] == "5.10.2"
            and value["position_embedding_type"] == "sans_pos"
            and value["dtype"] == "float32" and value["classifier_pooling"] == "mean"
            and value["id2label"] == {"0": "benign", "1": "injection"}
            and value["label2id"] == {"benign": 0, "injection": 1}
            and value["max_position_embeddings"] == 8192
            and value["hidden_size"] == 384 and value["num_attention_heads"] == 6
            and value["num_hidden_layers"] == 22 and value["global_attn_every_n_layers"] == 3
            and value["local_attention"] == 128 and value["intermediate_size"] == 1152
            and value["vocab_size"] == 256000 and value["norm_eps"] == 1e-5)
    pattern = ["full_attention" if i % 3 == 0 else "sliding_attention" for i in range(22)]
    require(value["layer_types"] == pattern
            and value["rope_parameters"] == {kind: {"rope_theta": 160000, "rope_type": "default"}
                                              for kind in ("full_attention", "sliding_attention")}
            and {key: value[key] for key in ("pad_token_id", "bos_token_id", "eos_token_id", "cls_token_id", "sep_token_id", "mask_token_id")}
            == {"pad_token_id": 0, "bos_token_id": 2, "eos_token_id": 1, "cls_token_id": 1, "sep_token_id": 1, "mask_token_id": 4})
    # No unknown rope keys reach v4's ignored **kwargs. Other original metadata
    # stays intact; source-version provenance is deliberately retained as5.10.2.
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
            and config.global_rope_theta == config.local_rope_theta == 160000.0
            and config.global_attn_every_n_layers == 3 and config.local_attention == 128
            and config.classifier_pooling == "mean" and config.reference_compile is False
            and config.num_labels == 2 and config.id2label == {0: "benign", 1: "injection"}
            and config.label2id == {"benign": 0, "injection": 1}
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
            and config.global_rope_theta == config.local_rope_theta == 160000.0
            and config.global_attn_every_n_layers == 3 and config.local_attention == 128
            and config.max_position_embeddings == 8192 and config.num_labels == 2
            and config.id2label == {0: "benign", 1: "injection"})
    count = 0
    for parameter in model.parameters():
        require(parameter.device.type == "cpu" and parameter.dtype == torch.float32)
        count += parameter.numel()
    require(count == PARAMETERS and len(model.model.layers) == 22
            and tuple(model.model.embeddings.tok_embeddings.weight.shape) == (256000, 384)
            and tuple(model.classifier.weight.shape) == (2, 384)
            and tuple(model.classifier.bias.shape) == (2,))
    expected = 1.0 / (160000.0 ** (torch.arange(0, 64, 2, dtype=torch.int64, device="cpu").float() / 64))
    for index, layer in enumerate(model.model.layers):
        attention = layer.attn
        require(attention.layer_id == index and attention.head_dim == 64 and attention.num_heads == 6
                and attention.local_attention == ((-1, -1) if index % 3 == 0 else (64, 64)))
        rotary = attention.rotary_emb
        require(rotary.rope_type == "default" and rotary.config.global_rope_theta == 160000.0
                and rotary.config.rope_theta == 160000.0
                and rotary.attention_scaling == 1.0 and rotary.inv_freq.device.type == "cpu"
                and rotary.inv_freq.dtype == torch.float32 and tuple(rotary.inv_freq.shape) == (32,)
                and torch.equal(rotary.inv_freq, expected))
    return {"translation": TRANSLATION, "model_type": "modernbert", "parameters": count,
        "dtype": "float32", "device": "cpu", "attention": "sdpa", "compile": False,
        "layers": 22, "global_layers": 8, "local_layers": 14, "local_window_each_side": 64,
        "global_rope_theta": 160000, "local_rope_theta": 160000, "classifier_pooling": "mean",
        "missing_weight_keys": 0, "unexpected_weight_keys": 0, "mismatched_weight_keys": 0,
        "error_messages": 0, "scoring_requests": 0}


def validate_logits(logits, torch):
    """Same fixed binary softmax probability; no threshold or label adaptation."""
    require(tuple(logits.shape) == (1, 2) and bool(torch.isfinite(logits).all()))
    value = float(torch.softmax(logits, dim=-1)[0, 1])
    require(math.isfinite(value) and 0 <= value <= 1)
    return value


def _stamp(row):
    return (row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_nlink,
            row.st_size, row.st_mtime_ns, row.st_ctime_ns)


def _read_pinned(path, expected, *, keep=False):
    """Bounded regular-file read; no FIFO/symlink or changing-file authority."""
    path = Path(path)
    require(path.absolute() == path.resolve() and path.is_absolute())
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size == expected[1])
        digest, chunks, total = hashlib.sha256(), [], 0
        while chunk := stream.read(min(1024 * 1024, expected[1] + 1 - total)):
            total += len(chunk)
            require(total <= expected[1])
            digest.update(chunk)
            if keep:
                chunks.append(chunk)
        require(total == expected[1] and digest.hexdigest() == expected[0]
                and _stamp(os.fstat(stream.fileno())) == _stamp(before)
                and _stamp(path.stat(follow_symlinks=False)) == _stamp(before)
                and path.resolve() == path)
    return b"".join(chunks) if keep else _stamp(before)


def verify_bundle(directory):
    """Authenticate the exact seven public files, returning only two inputs."""
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


def _state(component):
    raw = component.__getstate__()
    require(type(raw) is bytes and len(raw) <= 16384)
    return json.loads(raw)


class WolfTokenizer:
    """The original native tokenizer with complete, nontruncating window input.

    No token strings/AddedToken flags are re-registered. The one manually
    assembled single-sequence template is authenticated against the actual
    backend template; config.cls_token_id is deliberately not used.
    """

    def __init__(self, backend, added_tokens):
        require(type(added_tokens) is list and len(added_tokens) == 249)
        self._backend = backend
        self._verify_components()
        require(type(backend.model).__name__ == "BPE" and backend.get_vocab_size(with_added_tokens=True) == 256000
                and backend.truncation == {"direction": "right", "max_length": 2049,
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
        require(_state(self._backend.post_processor) == TOKENIZER_COMPONENTS["post_processor"])
        return [2, *tokens, 1]


def make_tokenizer(raw, tokenizer_class):
    require(type(raw) is bytes and len(raw) == FILES["tokenizer.json"][1]
            and hashlib.sha256(raw).hexdigest() == FILES["tokenizer.json"][0])
    value = json.loads(raw)
    require(value["version"] == "1.0" and value["model"]["type"] == "BPE"
            and all(value[name] == expected for name, expected in TOKENIZER_COMPONENTS.items()))
    # Construct from authenticated bytes, never a reread pathname or remote ID.
    return WolfTokenizer(tokenizer_class.from_str(raw.decode("utf-8")), value["added_tokens"])


def verify_imported_modules(package, config_class, model_class):
    """Bind the actual imported objects to the authenticated distribution files."""
    names = {
        "transformers.models.modernbert.configuration_modernbert": "models/modernbert/configuration_modernbert.py",
        "transformers.models.modernbert.modeling_modernbert": "models/modernbert/modeling_modernbert.py",
        "transformers.modeling_rope_utils": "modeling_rope_utils.py",
    }
    for name, relative in names.items():
        module = sys.modules.get(name)
        expected = package / relative
        require(module is not None and type(getattr(module, "__file__", None)) is str
                and Path(module.__file__).absolute() == expected
                and Path(module.__file__).resolve() == expected)
        _read_pinned(expected, LIBRARY_SOURCES[relative])
    config_module = sys.modules["transformers.models.modernbert.configuration_modernbert"]
    model_module = sys.modules["transformers.models.modernbert.modeling_modernbert"]
    rope_module = sys.modules["transformers.modeling_rope_utils"]
    require(config_class.__module__ == config_module.__name__ and model_class.__module__ == model_module.__name__
            and config_module.ModernBertConfig is config_class
            and model_module.ModernBertForSequenceClassification is model_class
            and model_module.ModernBertConfig is config_class
            and model_module.ROPE_INIT_FUNCTIONS["default"] is rope_module._compute_default_rope_parameters)


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
        "single_template_ids": [2, 1], "windows": {"maximum": 16, "tokens": 512, "stride": 256},
        "qualification_only": True, "quality_verified": False})

    def predict(tokens):
        require(type(tokens) is list and 2 <= len(tokens) <= 512
                and tokens[0] == 2 and tokens[-1] == 1
                and all(type(item) is int and 0 <= item < 256000 for item in tokens))
        with torch.inference_mode():
            encoded = torch.tensor([tokens], dtype=torch.long, device="cpu")
            return validate_logits(model(input_ids=encoded, attention_mask=torch.ones_like(encoded)).logits, torch)

    return tokenizer, predict, metadata
