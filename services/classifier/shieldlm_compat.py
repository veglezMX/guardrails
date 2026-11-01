"""Candidate01 native compatibility; no ML imports or enabled-model selection.

The authenticated v5 export has a zero-special-token postprocessor. Preserve it;
never inject a CLS/SEP token. Empty native encodings refuse (an explicit candidate
limitation, not a claim that existing empty-input production requirements pass).
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import stat
import sys
import time
from pathlib import Path
from types import MappingProxyType

REPOSITORY = "dmilush/shieldlm-deberta-base"
REVISION = "032a0beaf33a24e08f60617b047291d94adbbb8f"
CONFIG_SHA256 = "fb3ddec311f6b8e670587d21b3823b2247d33baeae23ea389e31e3fc6dca1193"
WEIGHTS_SHA256 = "95729650ba6a8211ee931335e3ada1be46af3e98b6ec6a757f9f40a87fc344cd"
WEIGHTS_BYTES = 737719248
PARAMETERS = 184423682
FILES = MappingProxyType({
    "README.md": ("f7bc6d85e7b7b83bcc6e9e06c6bf4126ed098cd7baaa918f1893ae59b93355bf", 4864),
    "config.json": (CONFIG_SHA256, 993),
    "model.safetensors": (WEIGHTS_SHA256, WEIGHTS_BYTES),
    "tokenizer.json": ("f9a14ed43a575805d8c736954cd306ec120797203897db06b9fd63213b32505c", 8339358),
    "tokenizer_config.json": ("7931febe14783fb3e26ca14599a0fee4faac0284b7f94094e559d0bbcd8bfe3d", 509),
})
LIBRARY_SOURCES = MappingProxyType({
    "models/deberta_v2/configuration_deberta_v2.py": ("f7a4ce3e96eb3525b67be32fb360f74be02e2b26fcaff90bb52b6fd13447feb6", 8964),
    "models/deberta_v2/modeling_deberta_v2.py": ("f1943e7ef2d179c57b0238b4a390a50484a4901539fec41e33fe3b9ce8831652", 56711),
})
SPECIAL_IDS = MappingProxyType({"[PAD]": 0, "[CLS]": 1, "[SEP]": 2, "[UNK]": 3, "[MASK]": 128000})
TOKENIZER_COMPONENTS = {
    "normalizer": {"type": "Sequence", "normalizers": [
        {"type": "Replace", "pattern": {"Regex": r"\s{2,}|[\n\r\t]"}, "content": " "},
        {"type": "NFC"}, {"type": "Strip", "strip_left": False, "strip_right": True}]},
    "pre_tokenizer": {"type": "Sequence", "pretokenizers": [
        {"type": "Metaspace", "replacement": "▁", "prepend_scheme": "always", "split": True}]},
    "decoder": {"type": "Metaspace", "replacement": "▁", "prepend_scheme": "always", "split": True},
    "post_processor": {"type": "TemplateProcessing", "single": [{"Sequence": {"id": "A", "type_id": 0}}],
        "pair": [{"Sequence": {"id": "A", "type_id": 0}}, {"Sequence": {"id": "B", "type_id": 1}}], "special_tokens": {}},
}
CONFIG_FIELDS = {
    "model_type": "deberta-v2", "architectures": ["DebertaV2ForSequenceClassification"],
    "transformers_version": "5.2.0", "dtype": "float32", "hidden_size": 768,
    "num_hidden_layers": 12, "num_attention_heads": 12, "intermediate_size": 3072,
    "max_position_embeddings": 512, "max_relative_positions": -1, "position_buckets": 256,
    "relative_attention": True, "share_att_key": True, "pos_att_type": ["p2c", "c2p"],
    "position_biased_input": False, "norm_rel_ebd": "layer_norm", "layer_norm_eps": 1e-7,
    "vocab_size": 128100, "type_vocab_size": 0, "pad_token_id": 0,
    "pooler_hidden_size": 768, "pooler_hidden_act": "gelu", "pooler_dropout": 0,
    "hidden_act": "gelu", "problem_type": "single_label_classification",
}


class ShieldCompatibilityError(ValueError):
    """Finite refusal; never include text, weights or arbitrary exception content."""


def require(condition, code="shieldlm_runtime_semantics_unverified"):
    if not condition:
        raise ShieldCompatibilityError(code)


def parse(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value)
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: require(False))


def make_config(raw, config_class):
    require(type(raw) is bytes and len(raw) == FILES["config.json"][1]
            and hashlib.sha256(raw).hexdigest() == CONFIG_SHA256)
    value = parse(raw)
    require(type(value) is dict and all(value.get(key) == item for key, item in CONFIG_FIELDS.items())
            and "id2label" not in value and "label2id" not in value and "num_labels" not in value)
    # dtype is v5 metadata; use v4's consumed torch_dtype spelling, preserving the
    # original export version. Two default LABEL_n values are not semantic labels:
    # the separately authenticated publisher README defines attack index1.
    value["torch_dtype"] = value.pop("dtype")
    config = config_class(**value)
    verify_config(config)
    return config


def verify_config(config):
    require(all(getattr(config, key, None) == value for key, value in CONFIG_FIELDS.items()
                if key not in {"dtype", "transformers_version"})
            and config.num_labels == 2 and config.id2label == {0: "LABEL_0", 1: "LABEL_1"}
            and config.label2id == {"LABEL_0": 0, "LABEL_1": 1}
            and getattr(config, "conv_kernel_size", 0) == 0)


def verify_loaded_model(model, torch, loading_info):
    require(type(loading_info) is dict and set(loading_info) == {
        "missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"}
        and all(type(value) is list and not value for value in loading_info.values()))
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
            and model.pooler.config.pooler_hidden_act == "gelu"
            and tuple(model.classifier.weight.shape) == (2, 768)
            and tuple(model.classifier.bias.shape) == (2,))
    for layer in encoder.layer:
        attention = layer.attention.self
        require(attention.num_attention_heads == 12 and attention.attention_head_size == 64
                and attention.relative_attention is True and attention.share_att_key is True
                and attention.position_buckets == 256 and attention.max_relative_positions == 512
                and attention.pos_ebd_size == 256 and attention.pos_att_type == ["p2c", "c2p"])
    return {"model_type": "deberta-v2", "parameters": count, "parameter_tensors": tensors,
        "dtype": "float32", "device": "cpu", "attention": "eager", "layers": 12,
        "classifier_pooling": "first_native_token_dense_gelu", "attack_softmax_index": 1,
        "label_authority_sha256": FILES["README.md"][0], "missing_weight_keys": 0,
        "unexpected_weight_keys": 0, "mismatched_weight_keys": 0, "error_messages": 0,
        "scoring_requests": 0}


def validate_logits(logits, torch):
    require(tuple(logits.shape) == (1, 2) and bool(torch.isfinite(logits).all()))
    value = float(torch.softmax(logits, dim=-1)[0, 1])
    require(math.isfinite(value) and 0 <= value <= 1)
    return value


def _stamp(row):
    return (row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_nlink,
            row.st_size, row.st_mtime_ns, row.st_ctime_ns)


def _read_pinned(path, expected, *, keep=False):
    path = Path(path)
    require(path.is_absolute() and path.absolute() == path.resolve())
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
            and all(_stamp((root / name).stat(follow_symlinks=False)) == stamp for name, stamp in stamps.items()))
    return captured


def _state(component):
    raw = component.__getstate__()
    require(type(raw) is bytes and len(raw) <= 16384)
    return parse(raw)


class ShieldTokenizer:
    """Exact native export, with saved truncation/padding disabled for coverage."""

    def __init__(self, backend, added_tokens):
        require(type(added_tokens) is list and len(added_tokens) == 5)
        self._backend = backend
        self._verify_components()
        require(type(backend.model).__name__ == "Unigram"
                and backend.get_vocab_size(with_added_tokens=False) == 128000
                and backend.get_vocab_size(with_added_tokens=True) == 128001
                and backend.truncation == {"direction": "right", "max_length": 512,
                    "strategy": "longest_first", "stride": 0}
                and backend.padding == {"length": 512, "direction": "right", "pad_to_multiple_of": None,
                    "pad_id": 0, "pad_type_id": 0, "pad_token": "[PAD]"})
        actual = backend.get_added_tokens_decoder()
        require(set(actual) == set(SPECIAL_IDS.values()) == {row["id"] for row in added_tokens})
        for row in added_tokens:
            require(set(row) == {"id", "content", "single_word", "lstrip", "rstrip", "normalized", "special"})
            token = actual[row["id"]]
            require(all(getattr(token, key) == value for key, value in row.items() if key != "id"))
        require(all(backend.token_to_id(token) == value for token, value in SPECIAL_IDS.items())
                and backend.num_special_tokens_to_add(False) == 0)
        backend.no_truncation()
        backend.no_padding()
        self._complete()

    def _verify_components(self):
        require(all(_state(getattr(self._backend, name)) == expected
                    for name, expected in TOKENIZER_COMPONENTS.items()))

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
        require(self._backend.num_special_tokens_to_add(False) == 0)
        return 0

    def build_inputs_with_special_tokens(self, tokens):
        require(type(tokens) is list and 1 <= len(tokens) <= 512
                and all(type(item) is int and 0 <= item < 128001 for item in tokens))
        self._complete()
        require(_state(self._backend.post_processor) == TOKENIZER_COMPONENTS["post_processor"])
        return list(tokens)


def make_tokenizer(raw, tokenizer_class):
    require(type(raw) is bytes and len(raw) == FILES["tokenizer.json"][1]
            and hashlib.sha256(raw).hexdigest() == FILES["tokenizer.json"][0])
    value = parse(raw)
    require(value["version"] == "1.0" and value["model"]["type"] == "Unigram"
            and value["model"]["unk_id"] == 3 and value["model"]["byte_fallback"] is False
            and len(value["model"]["vocab"]) == 128000
            and all(value[name] == expected for name, expected in TOKENIZER_COMPONENTS.items()))
    return ShieldTokenizer(tokenizer_class.from_str(raw.decode("utf-8")), value["added_tokens"])


def window_jobs(segments, tokenizer, deadline):
    """Candidate-only original window algebra with authenticated zero specials.

    All text is tokenized and every window admitted before any forward call.
    The caller retains its original error/timeout rows; empty native text refuses.
    """
    from guardrails.evolution.classifier_contract import ClassifierError
    require(type(deadline) in (int, float) and math.isfinite(deadline))
    require(tokenizer.num_special_tokens_to_add(pair=False) == 0)
    jobs = []
    for index, segment in enumerate(segments):
        if time.monotonic() >= deadline:
            raise ClassifierError("evaluation_timeout")
        tokens = tokenizer.encode(segment["text"], add_special_tokens=False, truncation=False)
        if type(tokens) is not list or not 1 <= len(tokens) <= 65536 or any(type(token) is not int or not 0 <= token < 128001 for token in tokens):
            raise ClassifierError("inspection_incomplete")
        start = 0
        while True:
            if len(jobs) >= 16:
                raise ClassifierError("inspection_incomplete")
            window = tokenizer.build_inputs_with_special_tokens(tokens[start:start + 512])
            require(type(window) is list and window == tokens[start:start + 512] and 1 <= len(window) <= 512)
            jobs.append((index, window))
            if start + 512 >= len(tokens):
                break
            start += 256
    if time.monotonic() >= deadline:
        raise ClassifierError("evaluation_timeout")
    return jobs


def verify_imported_modules(package, config_class, model_class):
    modules = {}
    for stem in ("configuration_deberta_v2", "modeling_deberta_v2"):
        name = "transformers.models.deberta_v2." + stem
        relative = "models/deberta_v2/" + stem + ".py"
        module = sys.modules.get(name)
        expected = package / relative
        require(module is not None and type(getattr(module, "__file__", None)) is str
                and Path(module.__file__).absolute() == expected and Path(module.__file__).resolve() == expected)
        _read_pinned(expected, LIBRARY_SOURCES[relative])
        modules[stem] = module
    cfg, model = modules["configuration_deberta_v2"], modules["modeling_deberta_v2"]
    require(config_class.__module__ == cfg.__name__ and model_class.__module__ == model.__name__
            and cfg.DebertaV2Config is config_class and model.DebertaV2Config is config_class
            and model.DebertaV2ForSequenceClassification is model_class)


def load_for_qualification(directory):
    """Parent-only exact offline CPU load; no encode or forward during loading."""
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
        "embedded_padding_disabled": True, "single_template_ids": [], "empty_encoding": "refuse",
        "windows": {"maximum": 16, "tokens": 512, "stride": 256},
        "qualification_only": True, "quality_verified": False})

    def predict(tokens):
        require(type(tokens) is list and 1 <= len(tokens) <= 512
                and all(type(item) is int and 0 <= item < 128001 for item in tokens))
        with torch.inference_mode():
            encoded = torch.tensor([tokens], dtype=torch.long, device="cpu")
            return validate_logits(model(input_ids=encoded, attention_mask=torch.ones_like(encoded)).logits, torch)

    return tokenizer, predict, metadata
