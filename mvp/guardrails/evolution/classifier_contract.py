"""Closed classifier wire and pinned local model identity (Python 3.13 safe)."""
from __future__ import annotations

import hashlib
import json
import math
import re
from types import MappingProxyType

MODEL = "protectai/deberta-v3-base-prompt-injection-v2"
MODEL_REVISION = "90c9989b1a342275dd0d1a95aad283c04e075671"
MODEL_ID = MODEL + "@" + MODEL_REVISION
EVALUATOR_REVISION = "deberta-v2-90c9989b1a34"
WEIGHTS_SHA256 = "6521cb8d0ac08148c81464899c424e6148fcc62befa371089fa4061d8b6e0424"
FILES = MappingProxyType({
    "LICENSE": ("59899c6091b540582ed617e8eeaac4919dc985ccfc35459ee9752b699be5205b", 10172),
    "README.md": ("37221a38df1e60ad56364c39a233c75b77fa08740ecdee248d1b4e07ccc8ef5a", 6327),
    "added_tokens.json": ("dc046d04c9b0ada7ae6f1dc89c465801799acdf0c9a6aab8c15a1b2d5ca4e91f", 23),
    "config.json": ("05079f4735092040b780d459027afab413faa6eeb66a548571a58832304b60bb", 994),
    "model.safetensors": (WEIGHTS_SHA256, 737719272),
    "special_tokens_map.json": ("9463f61e1b109a8eb4688b829260d7c6b1e6dff04c98ff7269bb89e2b92369b9", 286),
    "spm.model": ("c679fbf93643d19aab7ee10c0b99e460bdbc02fedf34b92b05af343b4af586fd", 2464616),
    "tokenizer.json": ("f0a66ad0d735d8dca9ecac4ff50fcdef4bb6adbadd2941a926844844d2c2059b", 8656744),
    "tokenizer_config.json": ("557b3d33d3f41b81ad769244e506549e98a1857d41dd58160aacd4d98d710b5a", 1284),
})
MAX_WIRE_BYTES = 512 * 1024
MAX_RESULT_BYTES = 256 * 1024
MAX_CONTENT_BYTES = 65536
MAX_SEGMENTS = 1024
MAX_WINDOWS = 16
WINDOW_TOKENS = 512
STRIDE_TOKENS = 256
DETECTOR_SECONDS = 2.9  # Runtime keeps 100 ms of its three-second budget for audit.
STAGES = ("input", "output", "retrieval", "tool_arguments", "tool_result")
ERRORS = frozenset({"validation_failed", "payload_too_large", "inspection_incomplete", "evaluation_timeout", "evaluator_unavailable", "queue_full", "unauthorized", "resource_not_found"})


class ClassifierError(Exception):
    def __init__(self, code):
        self.code = code if code in ERRORS else "evaluator_unavailable"
        super().__init__(self.code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


BUNDLE_SHA256 = hashlib.sha256(canonical(dict(FILES))).hexdigest()


def identity():
    return {"model_id": MODEL_ID, "model_revision": MODEL_REVISION, "weights_sha256": WEIGHTS_SHA256, "bundle_sha256": BUNDLE_SHA256, "evaluator_revision": EVALUATOR_REVISION, "class_mapping": {"0": "SAFE", "1": "INJECTION"}, "supported_languages": ["en"]}


def parse(raw, *, limit=MAX_WIRE_BYTES):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise ClassifierError("payload_too_large")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def invalid(_):
        raise ValueError

    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid, parse_float=number)
    except (ValueError, TypeError, RecursionError):
        raise ClassifierError("validation_failed") from None
    if type(value) is not dict:
        raise ClassifierError("validation_failed")
    return value


def segments_checked(segments):
    if type(segments) is not list or len(segments) > MAX_SEGMENTS:
        raise ClassifierError("inspection_incomplete")
    seen, size = set(), 0
    for row in segments:
        if type(row) is not dict or set(row) != {"part_id", "role", "json_pointer", "text"}:
            raise ClassifierError("validation_failed")
        part, pointer, text = row["part_id"], row["json_pointer"], row["text"]
        if type(part) is not str or not 1 <= len(part) <= 128 or type(row["role"]) is not str or row["role"] not in {"system", "user", "assistant", "retrieval", "tool"}:
            raise ClassifierError("validation_failed")
        if pointer is not None and (type(pointer) is not str or len(pointer) > 8192 or pointer and not pointer.startswith("/") or re.search(r"~(?![01])", pointer)):
            raise ClassifierError("validation_failed")
        if type(text) is not str or len(text) > MAX_CONTENT_BYTES:
            raise ClassifierError("payload_too_large")
        try:
            size += len(text.encode("utf-8"))
        except UnicodeError:
            raise ClassifierError("validation_failed") from None
        if size > MAX_CONTENT_BYTES or (part, pointer) in seen:
            raise ClassifierError("payload_too_large" if size > MAX_CONTENT_BYTES else "validation_failed")
        seen.add((part, pointer))
    return segments


def request_checked(value):
    if type(value) is not dict or set(value) != {"api_major", "identity", "request_id", "budget_ms", "segments"} or type(value["api_major"]) is not int or value["api_major"] != 1 or value["identity"] != identity():
        raise ClassifierError("validation_failed")
    if type(value["request_id"]) is not str or not re.fullmatch(r"[0-9a-f]{32}", value["request_id"]):
        raise ClassifierError("validation_failed")
    if type(value["budget_ms"]) is not int or not 1 <= value["budget_ms"] <= 2900:
        raise ClassifierError("validation_failed")
    segments_checked(value["segments"])
    return value


def score_checked(value):
    return type(value) in (float, int) and math.isfinite(value) and 0 <= value <= 1
