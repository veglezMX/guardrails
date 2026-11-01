"""Persistent CPU-only inference process; imports ML only after startup guards."""
from __future__ import annotations

import hashlib
import importlib.metadata
import math
import os
import stat
import sys
import time
from pathlib import Path

from guardrails.evolution.classifier_contract import (
    FILES,
    MAX_RESULT_BYTES,
    MAX_WINDOWS,
    MAX_WIRE_BYTES,
    STRIDE_TOKENS,
    WINDOW_TOKENS,
    ClassifierError,
    canonical,
    identity,
    parse,
    request_checked,
    score_checked,
)


def verify_bundle(directory):
    root = Path(directory)
    if root.is_symlink() or not root.is_dir() or root.resolve() != root.absolute() or {p.name for p in root.iterdir()} != set(FILES):
        raise ClassifierError("evaluator_unavailable")
    for name, (expected, size) in FILES.items():
        descriptor = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != size or before.st_nlink != 1:
                raise ClassifierError("evaluator_unavailable")
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
            key = lambda row: (row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_nlink, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
            if actual != expected or key(os.fstat(stream.fileno())) != key(before) or key((root / name).stat(follow_symlinks=False)) != key(before):
                raise ClassifierError("evaluator_unavailable")


def window_jobs(segments, tokenizer, deadline):
    """Tokenize full content before inference. Never truncate an uninspected tail."""
    special = tokenizer.num_special_tokens_to_add(pair=False)
    if type(special) is not int or not 1 <= special < WINDOW_TOKENS - STRIDE_TOKENS:
        raise ClassifierError("evaluator_unavailable")
    capacity = WINDOW_TOKENS - special
    jobs = []
    for index, segment in enumerate(segments):
        if time.monotonic() >= deadline:
            raise ClassifierError("evaluation_timeout")
        tokens = tokenizer.encode(segment["text"], add_special_tokens=False, truncation=False)
        if not isinstance(tokens, list) or len(tokens) > 65536 or any(type(token) is not int or token < 0 for token in tokens):
            raise ClassifierError("inspection_incomplete")
        # Even empty text receives the ordinary model special-token window.
        start = 0
        while True:
            if len(jobs) >= MAX_WINDOWS:
                raise ClassifierError("inspection_incomplete")
            window = tokenizer.build_inputs_with_special_tokens(tokens[start:start + capacity])
            if not isinstance(window, list) or not 1 <= len(window) <= WINDOW_TOKENS:
                raise ClassifierError("evaluator_unavailable")
            jobs.append((index, window))
            if start + capacity >= len(tokens):
                break
            start += STRIDE_TOKENS
    if time.monotonic() >= deadline:
        raise ClassifierError("evaluation_timeout")
    return jobs


def classify(request, tokenizer, predict, deadline):
    request_checked(request)
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ClassifierError("validation_failed")
    segments = request["segments"]
    jobs = window_jobs(segments, tokenizer, deadline)
    maxima, counts = [0.0] * len(segments), [0] * len(segments)
    # Full coverage was admitted before the first forward pass.
    for index, tokens in jobs:
        if time.monotonic() >= deadline:
            raise ClassifierError("evaluation_timeout")
        value = predict(tokens)
        if not score_checked(value):
            raise ClassifierError("evaluator_unavailable")
        maxima[index], counts[index] = max(maxima[index], float(value)), counts[index] + 1
    if time.monotonic() >= deadline:
        raise ClassifierError("evaluation_timeout")
    return {"api_major": 1, "identity": identity(), "request_id": request["request_id"], "complete": True, "windows": len(jobs), "scores": [{"part_id": row["part_id"], "json_pointer": row["json_pointer"], "score": maxima[index], "windows": counts[index]} for index, row in enumerate(segments)]}


def load_engine(directory):
    if sys.version_info[:3] != (3, 13, 16):
        raise ClassifierError("evaluator_unavailable")
    for distribution, expected in {"torch": "2.10.0+cpu", "transformers": "4.57.6", "sentencepiece": "0.2.1"}.items():
        if importlib.metadata.version(distribution) != expected:
            raise ClassifierError("evaluator_unavailable")
    verify_bundle(directory)
    os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "TOKENIZERS_PARALLELISM": "false", "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "CUDA_VISIBLE_DEVICES": ""})
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True, trust_remote_code=False, use_fast=True)
    model = AutoModelForSequenceClassification.from_pretrained(directory, local_files_only=True, trust_remote_code=False, use_safetensors=True).to("cpu").eval()
    if model.config.id2label != {0: "SAFE", 1: "INJECTION"} or model.config.max_position_embeddings != 512 or model.config.model_type != "deberta-v2" or next(model.parameters()).device.type != "cpu":
        raise ClassifierError("evaluator_unavailable")

    def predict(tokens):
        with torch.inference_mode():
            encoded = torch.tensor([tokens], dtype=torch.long, device="cpu")
            logits = model(input_ids=encoded, attention_mask=torch.ones_like(encoded)).logits
            if tuple(logits.shape) != (1, 2) or not bool(torch.isfinite(logits).all()):
                raise ClassifierError("evaluator_unavailable")
            return float(torch.softmax(logits, dim=-1)[0, 1])

    return tokenizer, predict


def main():
    try:
        if len(sys.argv) != 2:
            raise ClassifierError("evaluator_unavailable")
        tokenizer, predict = load_engine(sys.argv[1])
        sys.stdout.buffer.write(canonical({"api_major": 1, "ready": True, "identity": identity()}) + b"\n")
        sys.stdout.buffer.flush()
        while raw := sys.stdin.buffer.readline(MAX_WIRE_BYTES + 2):
            if len(raw) > MAX_WIRE_BYTES + 1 or not raw.endswith(b"\n"):
                raise ClassifierError("payload_too_large")
            try:
                envelope = parse(raw[:-1])
                if set(envelope) != {"deadline", "request"} or type(envelope["deadline"]) not in (float, int):
                    raise ClassifierError("validation_failed")
                result = classify(envelope["request"], tokenizer, predict, envelope["deadline"])
            except ClassifierError as error:
                result = {"error": error.code}
            encoded = canonical(result)
            if len(encoded) > MAX_RESULT_BYTES:
                raise ClassifierError("evaluator_unavailable")
            sys.stdout.buffer.write(encoded + b"\n")
            sys.stdout.buffer.flush()
    except BaseException:  # noqa: BLE001 - worker failure closes IPC without content logging
        # Failure closes the pipe. No payload, model output or exception text leaks.
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
