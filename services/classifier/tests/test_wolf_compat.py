"""Pure Wolf compatibility checks; no tokenizer, torch, model or network load."""
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

from services.classifier import wolf_compat as compat

CONFIG = Path(__file__).parent / "fixtures/wolf-config.json"


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
    value.id2label = {int(k): v for k, v in value.id2label.items()}
    value.num_labels = len(value.id2label)
    return value


def test_exact_public_config_is_translated_without_rewriting_or_changing_scoring_metadata():
    before = CONFIG.read_bytes()
    original = json.loads(before)
    translated = compat.translated_config(before)
    removed = {"rope_parameters", "layer_types"}
    added = {"global_rope_theta", "local_rope_theta", "torch_dtype", "reference_compile"}
    assert set(translated) == (set(original) - removed) | added
    assert all(translated[key] == value for key, value in original.items() if key not in removed)
    assert translated["global_rope_theta"] == translated["local_rope_theta"] == 160000.0
    assert translated["reference_compile"] is False
    assert translated["classifier_pooling"] == "mean" and translated["position_embedding_type"] == "sans_pos"
    assert translated["max_position_embeddings"] == 8192 and translated["transformers_version"] == "5.10.2"
    assert CONFIG.read_bytes() == before


@pytest.mark.parametrize("field,value", [("classifier_pooling", "cls"), ("dtype", "float16"),
    ("local_attention", 256), ("num_hidden_layers", 23), ("global_attn_every_n_layers", 4),
    ("position_embedding_type", "absolute"), ("model_type", "bert"),
    ("id2label", {"0": "injection", "1": "benign"}),
    ("rope_parameters", {"full_attention": {"rope_theta": 10000, "rope_type": "default"}}),
    ("auto_map", {"AutoModel": "remote-code"})])
def test_any_changed_model_configuration_is_rejected_before_construction(field, value):
    changed = json.loads(CONFIG.read_bytes()); changed[field] = value
    with pytest.raises(compat.WolfCompatibilityError): compat.translated_config(json.dumps(changed).encode())


@pytest.mark.parametrize("raw", [b"{}", b"", b"NaN", b'{"duplicate":1,"duplicate":2}', "not-bytes", b"x" * 2085])
def test_unpinned_or_nonbytes_config_is_never_admitted(raw):
    with pytest.raises(compat.WolfCompatibilityError): compat.translated_config(raw)


def test_explicit_actual_configuration_factory_receives_translated_fields_once():
    calls = []
    def factory(**kwargs):
        calls.append(kwargs)
        return config_class(**kwargs)
    config = compat.make_config(CONFIG.read_bytes(), factory)
    assert len(calls) == 1 and config.num_labels == 2
    assert config.id2label == {0: "benign", 1: "injection"}
    assert config.local_rope_theta == 160000.0


@pytest.mark.parametrize("field,value", [("local_rope_theta", 10000.0), ("global_rope_theta", 10000.0),
    ("global_attn_every_n_layers", 2), ("local_attention", 64), ("max_position_embeddings", 512),
    ("classifier_pooling", "cls"), ("reference_compile", True), ("num_labels", 1),
    ("id2label", {0: "injection", 1: "benign"}), ("rope_scaling", {"rope_type": "dynamic"}),
    ("rope_parameters", {"ignored": "metadata"})])
def test_actual_config_default_or_ignored_translation_refuses(field, value):
    def factory(**kwargs):
        config = config_class(**kwargs)
        setattr(config, field, value)
        return config
    with pytest.raises(compat.WolfCompatibilityError): compat.make_config(CONFIG.read_bytes(), factory)


@pytest.mark.parametrize("key", ["missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"])
def test_any_nonempty_weight_loading_diagnostic_refuses_before_module_use(key):
    info = {name: [] for name in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")}
    info[key] = ["synthetic"]
    with pytest.raises(compat.WolfCompatibilityError): compat.verify_loaded_model(object(), object(), info)


@pytest.mark.parametrize("info", [{}, {"missing_keys": []},
    dict.fromkeys(("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"), ()),
    {"missing_keys": [], "unexpected_keys": [], "mismatched_keys": [], "error_msgs": [], "private": "unretained"}])
def test_incomplete_or_ambiguous_loading_result_is_not_a_success(info):
    with pytest.raises(compat.WolfCompatibilityError): compat.verify_loaded_model(object(), object(), info)


class Component:
    def __init__(self, value):
        self.value = value

    def __getstate__(self):
        return json.dumps(self.value).encode()


def added_tokens():
    rows = []
    for token, number in [*compat.SPECIAL_IDS.items(), *((f"token-{i}", i) for i in range(108, 350))]:
        rows.append({"id": number, "content": token, "single_word": False,
            "lstrip": token == "<mask>", "rstrip": False, "normalized": False, "special": True})
    assert len(rows) == 249
    return rows


class Backend:
    """Finite fake of the native API; no tokenizer package is imported."""

    def __init__(self, tokens=None):
        self.model = type("BPE", (), {})()
        for name, value in compat.TOKENIZER_COMPONENTS.items():
            setattr(self, name, Component(json.loads(json.dumps(value))))
        self.truncation = {"direction": "right", "max_length": 2049, "strategy": "longest_first", "stride": 0}
        self.padding = None
        self.added = {row["id"]: SimpleNamespace(**{k: v for k, v in row.items() if k != "id"}) for row in added_tokens()}
        self.ids = dict(compat.SPECIAL_IDS)
        self.tokens = tokens if tokens is not None else [11, 12]
        self.calls = []

    def get_vocab_size(self, *, with_added_tokens):
        assert with_added_tokens is True
        return 256000

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
        return SimpleNamespace(ids=self.tokens.copy())


def test_native_wrapper_disables_truncation_and_preserves_all_added_token_flags():
    backend = Backend()
    before = {k: vars(v).copy() for k, v in backend.added.items()}
    tokenizer = compat.WolfTokenizer(backend, added_tokens())
    assert backend.calls == ["no_truncation", "no_padding"]
    assert tokenizer.encode("original text", add_special_tokens=False, truncation=False) == [11, 12]
    assert backend.calls[-1] == ("original text", False)
    assert tokenizer.num_special_tokens_to_add(pair=False) == 2
    assert tokenizer.build_inputs_with_special_tokens([11, 12]) == [2, 11, 12, 1]
    assert {k: vars(v).copy() for k, v in backend.added.items()} == before
    assert backend.added[4].lstrip is True


@pytest.mark.parametrize("component", list(compat.TOKENIZER_COMPONENTS))
def test_changed_native_preprocessing_or_template_refuses(component):
    backend = Backend()
    getattr(backend, component).value["unexpected"] = True
    with pytest.raises(compat.WolfCompatibilityError): compat.WolfTokenizer(backend, added_tokens())


@pytest.mark.parametrize("change", ["mask_lstrip", "missing_token", "extra_token", "wrong_bos", "wrong_model",
    "wrong_truncation", "padding", "missing_truncation", "wrong_added_flags"])
def test_native_state_cannot_silently_fall_back_or_reregister_tokens(change):
    backend = Backend()
    if change == "mask_lstrip": backend.added[4].lstrip = False
    elif change == "missing_token": del backend.added[4]
    elif change == "extra_token": backend.added[999] = backend.added[4]
    elif change == "wrong_bos": backend.ids["<bos>"] = 1
    elif change == "wrong_model": backend.model = object()
    elif change == "wrong_truncation": backend.truncation["max_length"] = 512
    elif change == "padding": backend.padding = {"length": 512}
    elif change == "missing_truncation": backend.truncation = None
    else: backend.added[108].normalized = True
    with pytest.raises(compat.WolfCompatibilityError): compat.WolfTokenizer(backend, added_tokens())


@pytest.mark.parametrize("method", ["encode", "num_special_tokens_to_add", "build_inputs_with_special_tokens"])
@pytest.mark.parametrize("field", ["truncation", "padding"])
def test_reenabled_native_truncation_or_padding_fails_before_each_operation(method, field):
    backend = Backend()
    tokenizer = compat.WolfTokenizer(backend, added_tokens())
    setattr(backend, field, {"unexpected": True})
    args = {"encode": ("text",), "num_special_tokens_to_add": (), "build_inputs_with_special_tokens": ([11],)}
    with pytest.raises(compat.WolfCompatibilityError): getattr(tokenizer, method)(*args[method])
    assert backend.calls == ["no_truncation", "no_padding"]


def test_changed_template_after_construction_cannot_use_wrong_cls_token():
    backend = Backend()
    tokenizer = compat.WolfTokenizer(backend, added_tokens())
    backend.post_processor.value["special_tokens"]["<bos>"]["ids"] = [1]
    with pytest.raises(compat.WolfCompatibilityError): tokenizer.build_inputs_with_special_tokens([11])


@pytest.mark.parametrize("tokens", [[True], [-1], [256000], [1.5], (1,), [1] * 511])
def test_template_rejects_bad_or_oversized_window_without_partial_input(tokens):
    tokenizer = compat.WolfTokenizer(Backend(), added_tokens())
    with pytest.raises(compat.WolfCompatibilityError): tokenizer.build_inputs_with_special_tokens(tokens)


def test_original_worker_windows_include_a_tail_beyond_embedded_2049_limit():
    from services.classifier.worker import window_jobs
    backend = Backend(list(range(3000)))
    tokenizer = compat.WolfTokenizer(backend, added_tokens())
    jobs = window_jobs([{"text": "synthetic full document"}], tokenizer, time.monotonic() + 5)
    assert len(jobs) == 11 and all(len(tokens) <= 512 for _, tokens in jobs)
    assert set().union(*(set(tokens[1:-1]) for _, tokens in jobs)) == set(range(3000))
    assert jobs[-1][1][-2] == 2999


def test_original_worker_refuses_coverage_beyond_sixteen_windows_without_scoring():
    from guardrails.evolution.classifier_contract import ClassifierError

    from services.classifier.worker import window_jobs
    tokenizer = compat.WolfTokenizer(Backend(list(range(5000))), added_tokens())
    with pytest.raises(ClassifierError) as error:
        window_jobs([{"text": "synthetic oversized document"}], tokenizer, time.monotonic() + 5)
    assert error.value.code == "inspection_incomplete"


@pytest.mark.parametrize("raw", [b"{}", b"", "not-bytes", b"x"])
def test_unpinned_tokenizer_never_reaches_native_constructor(raw):
    class Never:
        @staticmethod
        def from_str(_):
            raise AssertionError("native constructor must not run")
    with pytest.raises(compat.WolfCompatibilityError): compat.make_tokenizer(raw, Never)


def test_parent_only_loader_refuses_wrong_python_before_model_or_files(monkeypatch):
    monkeypatch.setattr(compat.sys, "version_info", (3, 14, 0))
    monkeypatch.setattr(compat, "verify_bundle", lambda _: pytest.fail("bundle must not be read"))
    with pytest.raises(compat.WolfCompatibilityError): compat.load_for_qualification("/not-used")


@pytest.mark.parametrize("name", ["torch", "transformers", "tokenizers"])
def test_parent_only_loader_requires_a_clean_process_before_artifact_load(monkeypatch, name):
    monkeypatch.setattr(compat.sys, "version_info", (3, 13, 16))
    monkeypatch.setitem(compat.sys.modules, name, ModuleType(name))
    monkeypatch.setattr(compat, "verify_bundle", lambda _: pytest.fail("bundle must not be read"))
    with pytest.raises(compat.WolfCompatibilityError): compat.load_for_qualification("/not-used")


def modules(monkeypatch, tmp_path):
    package = tmp_path / "transformers"
    checks = []
    monkeypatch.setattr(compat, "_read_pinned", lambda path, expected: checks.append((path, expected)))
    full_names = ["transformers.models.modernbert.configuration_modernbert",
                  "transformers.models.modernbert.modeling_modernbert", "transformers.modeling_rope_utils"]
    rows = []
    for full, relative in zip(full_names, compat.LIBRARY_SOURCES, strict=True):
        module = ModuleType(full)
        module.__file__ = str(package / relative)
        monkeypatch.setitem(compat.sys.modules, full, module)
        rows.append(module)
    config, model = type("ModernBertConfig", (), {}), type("ModernBertForSequenceClassification", (), {})
    config.__module__, model.__module__ = full_names[:2]
    rows[0].ModernBertConfig = rows[1].ModernBertConfig = config
    rows[1].ModernBertForSequenceClassification = model
    rows[2]._compute_default_rope_parameters = lambda: None
    rows[1].ROPE_INIT_FUNCTIONS = {"default": rows[2]._compute_default_rope_parameters}
    return package, rows, config, model, checks


def test_actual_imported_modules_and_class_identity_must_match_authenticated_distribution(monkeypatch, tmp_path):
    package, _, config, model, checks = modules(monkeypatch, tmp_path)
    compat.verify_imported_modules(package, config, model)
    assert checks == [(package / relative, expected) for relative, expected in compat.LIBRARY_SOURCES.items()]


@pytest.mark.parametrize("change", ["config_path", "model_path", "rope_path", "no_file", "model_class",
    "config_class", "model_config", "rope_function", "model_module"])
def test_shadowed_or_cached_wrong_module_refuses_before_any_pretrained_load(monkeypatch, tmp_path, change):
    package, rows, config, model, _ = modules(monkeypatch, tmp_path)
    if change.endswith("_path"):
        rows[{"config_path": 0, "model_path": 1, "rope_path": 2}[change]].__file__ += ".untrusted"
    elif change == "no_file": del rows[0].__file__
    elif change == "model_class": rows[1].ModernBertForSequenceClassification = object()
    elif change == "config_class": rows[0].ModernBertConfig = object()
    elif change == "model_config": rows[1].ModernBertConfig = object()
    elif change == "rope_function": rows[1].ROPE_INIT_FUNCTIONS["default"] = lambda: None
    else: model.__module__ = "foreign.cached.module"
    with pytest.raises(compat.WolfCompatibilityError): compat.verify_imported_modules(package, config, model)


def tiny_bundle(monkeypatch, tmp_path):
    root = tmp_path / "model"
    root.mkdir(mode=0o700)
    pins = {}
    for name in compat.FILES:
        raw = ("synthetic:" + name).encode()
        target = root / name
        target.write_bytes(raw)
        target.chmod(0o600)
        pins[name] = (hashlib.sha256(raw).hexdigest(), len(raw))
    monkeypatch.setattr(compat, "FILES", pins)
    return root


def test_full_bundle_verifies_all_seven_but_only_retains_config_and_tokenizer(monkeypatch, tmp_path):
    root = tiny_bundle(monkeypatch, tmp_path)
    assert compat.verify_bundle(root) == {name: ("synthetic:" + name).encode()
                                         for name in ("config.json", "tokenizer.json")}


@pytest.mark.parametrize("change", ["extra", "missing", "changed_weights", "symlink", "hardlink", "world_writable", "fifo"])
def test_complete_closure_rejects_added_missing_or_unsafe_file_before_load(monkeypatch, tmp_path, change):
    root = tiny_bundle(monkeypatch, tmp_path)
    target = root / "model.safetensors"
    if change == "extra": (root / "training_args.bin").write_bytes(b"untrusted")
    elif change == "missing": target.unlink()
    elif change == "changed_weights": target.write_bytes(b"different")
    elif change == "symlink":
        target.rename(tmp_path / "weights")
        target.symlink_to(tmp_path / "weights")
    elif change == "hardlink": os.link(target, tmp_path / "second-link")
    elif change == "world_writable": target.chmod(0o666)
    else:
        target.unlink()
        os.mkfifo(target, 0o600)
    with pytest.raises(compat.WolfCompatibilityError): compat.verify_bundle(root)


@pytest.mark.parametrize("field,value", [
    ("direction", "Right"), ("direction", "left"), ("direction", "RIGHT"),
    ("strategy", "LongestFirst"), ("strategy", "only_first"), ("strategy", "only_second"),
    ("stride", 1), ("unexpected", True),
])
def test_native_truncation_getter_requires_exact_python_binding_values(field, value):
    backend = Backend()
    backend.truncation[field] = value
    with pytest.raises(compat.WolfCompatibilityError):
        compat.WolfTokenizer(backend, added_tokens())
    assert backend.calls == []
