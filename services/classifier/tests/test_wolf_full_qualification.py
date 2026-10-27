"""Synthetic protocol/retention checks; no heldout corpus is read here."""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import socket
import time
from typing import ClassVar

import pytest
from guardrails.evolution.classifier_contract import ClassifierError, canonical

from services.classifier import wolf_full_qualification as qualification


@pytest.fixture(autouse=True)
def no_model_or_network(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {
            "torch",
            "transformers",
            "tokenizers",
            "sentencepiece",
        }:
            pytest.fail("actual ML imports forbidden")
        return original(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("actual network/process/model operation forbidden")

    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "bind", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(qualification.wolf_full_compat, "load_for_qualification", forbidden)
    affinity = [{2, 4, 6, 8}]
    monkeypatch.setattr(qualification.os, "sched_getaffinity", lambda pid: set(affinity[0]))
    monkeypatch.setattr(qualification.os, "sched_setaffinity", lambda pid, cores: affinity.__setitem__(0, set(cores)))


def synthetic_corpus(kind="fresh"):
    cases = []
    for language, number in (("en", 200), ("es", 100)):
        for positive in (False, True):
            for index in range(number):
                serial = len(cases)
                cases.append(
                    {
                        "case_id": f"synthetic-{serial}",
                        "language": language,
                        "expected_injection": positive,
                        "split": "heldout",
                        "category": "synthetic",
                        "family_id": f"synthetic-family-{serial % 60}",
                        "indirect": index % 2 == 0,
                        "quoted_security": index % 3 == 0,
                        "part": {
                            **({"kind": "text"} if kind == "original" else {}),
                            "part_id": "one",
                            "role": "user",
                            "text": f"synthetic text {serial}",
                        },
                    }
                )
    return {
        "threshold": 0.9,
        "minimum_recall": 0.9,
        "maximum_false_positive_rate": 0.05,
        "cases": cases,
    }


def pinned_corpus(monkeypatch, kind="fresh", mutate=None):
    value = synthetic_corpus(kind)
    if mutate:
        mutate(value)
    raw = canonical(value)
    monkeypatch.setattr(
        qualification,
        {"original":"ORIGINAL_CORPUS_SHA256", "fresh":"FRESH_CORPUS_SHA256", "fresh_full":"FULL_FRESH_CORPUS_SHA256"}[kind],
        qualification.sha(raw),
    )
    return raw


@pytest.mark.parametrize("kind", ["original", "fresh", "fresh_full"])
def test_exact_corpus_contract_is_verified_without_using_metadata_as_text(
    monkeypatch, kind
):
    raw = pinned_corpus(monkeypatch, kind)
    assert len(qualification.corpus(raw, kind)["cases"]) == 600


@pytest.mark.parametrize(
    "change",
    [
        "count",
        "duplicates",
        "label_type",
        "label_balance",
        "threshold",
        "recall",
        "false_positive",
        "split",
        "part_extra",
        "oversize",
        "families",
        "indirect",
        "security",
        "duplicate_text",
    ],
)
def test_changed_structural_contract_cannot_qualify_even_if_externally_repinned(
    monkeypatch, change
):
    def mutate(value):
        rows = value["cases"]
        if change == "count":
            rows.pop()
        elif change == "duplicates":
            rows[1]["case_id"] = rows[0]["case_id"]
        elif change == "label_type":
            rows[0]["expected_injection"] = 0
        elif change == "label_balance":
            rows[0]["expected_injection"] = True
        elif change == "threshold":
            value["threshold"] = 0.8
        elif change == "recall":
            value["minimum_recall"] = 0.8
        elif change == "false_positive":
            value["maximum_false_positive_rate"] = 0.1
        elif change == "split":
            rows[0]["split"] = "training"
        elif change == "part_extra":
            rows[0]["part"]["label"] = False
        elif change == "oversize":
            rows[0]["part"]["text"] = "a" * 65537
        elif change == "families":
            for row in rows:
                row["family_id"] = "one-family"
        elif change == "indirect":
            for row in rows:
                row["indirect"] = False
        elif change == "security":
            for row in rows:
                row["quoted_security"] = False
        else:
            rows[1]["part"]["text"] = rows[0]["part"]["text"]

    raw = pinned_corpus(monkeypatch, mutate=mutate)
    with pytest.raises(ValueError):
        qualification.corpus(raw, "fresh")


def test_any_unpinned_bytes_are_rejected_before_corpus_parse():
    with pytest.raises(ValueError):
        qualification.corpus(b"{}", "fresh")


class Tokenizer:
    def __init__(self, count):
        self.count = count

    def num_special_tokens_to_add(self, *, pair):
        assert pair is False
        return 2

    def encode(self, text, *, add_special_tokens, truncation):
        assert (
            text == "content only"
            and add_special_tokens is False
            and truncation is False
        )
        return list(range(10, self.count + 10))

    def build_inputs_with_special_tokens(self, tokens):
        return [2, *tokens, 1]


def test_actual_original_window_admission_scores_full_tail_with_fixed_maximum():
    calls = []

    def predict(tokens):
        calls.append(tokens)
        return 0.91 if 3009 in tokens else 0.01

    result = qualification.evaluate_complete(
        "content only", Tokenizer(3000), predict, time.monotonic() + 5
    )
    assert result == {"complete": True, "score": 0.91, "windows": 11}
    assert len(calls) == 11 and all(len(tokens) <= 512 for tokens in calls)
    assert set().union(*(set(tokens[1:-1]) for tokens in calls)) == set(range(10, 3010))


def test_incomplete_coverage_refuses_before_any_prediction():
    with pytest.raises(ClassifierError) as error:
        qualification.evaluate_complete(
            "content only",
            Tokenizer(5000),
            lambda _: pytest.fail("partial prediction"),
            time.monotonic() + 5,
        )
    assert error.value.code == "inspection_incomplete"


@pytest.mark.parametrize(
    "score", [True, float("nan"), float("inf"), -0.01, 1.01, "0.99"]
)
def test_invalid_model_score_never_becomes_a_clean_or_injection_result(score):
    with pytest.raises(ClassifierError):
        qualification.evaluate_complete(
            "content only", Tokenizer(20), lambda _: score, time.monotonic() + 5
        )


def result_rows(*, false_positives=0, false_negatives=0, errors=0):
    rows = []
    for index, case in enumerate(synthetic_corpus()["cases"]):
        prediction = case["expected_injection"]
        if case["language"] == "en":
            if not prediction and index < false_positives:
                prediction = True
            if prediction and 200 <= index < 200 + false_negatives:
                prediction = False
        row = {
            "language": case["language"],
            "expected": case["expected_injection"],
            "predicted": prediction,
            "error": None,
            "elapsed_ms": 12.0,
        }
        if index < errors:
            row.update(predicted=None, error="evaluation_timeout")
        rows.append(row)
    return rows


def test_original_failed_candidate_confusion_counts_remain_rejected_at_unchanged_thresholds():
    value = qualification.matrix(
        result_rows(false_positives=20, false_negatives=10), "en"
    )
    assert value["true_positive"] == 190 and value["false_positive"] == 20
    assert value["recall"] == 0.95 and value["false_positive_rate"] == 0.1
    assert value["quality_pass"] is False


@pytest.mark.parametrize(
    "fp,fn,errors,passed",
    [
        (10, 20, 0, True),
        (11, 20, 0, False),
        (10, 21, 0, False),
        (0, 0, 1, False),
        (0, 0, 0, True),
    ],
)
def test_quality_boundary_is_inclusive_and_operational_errors_never_pass(
    fp, fn, errors, passed
):
    assert (
        qualification.matrix(
            result_rows(false_positives=fp, false_negatives=fn, errors=errors), "en"
        )["quality_pass"]
        is passed
    )


def test_spanish_results_do_not_enable_operational_language():
    value = qualification.matrix(result_rows(), "es")
    assert (
        value["quality_pass"] is True and value["operational_language_enabled"] is False
    )


class Pipe:
    def __init__(self, process):
        self.process = process

    def write(self, raw):
        self.process.requests.append(json.loads(raw))

    async def drain(self):
        return None


class Process:
    def __init__(self, *, response=None, block=False):
        self.returncode, self.requests, self.kills, self.reaps = None, [], 0, 0
        self.stdin, self.stdout = Pipe(self), self
        self.response, self.block = response, block

    async def readline(self):
        if self.block:
            await asyncio.Event().wait()
        if self.response is not None:
            return self.response
        return (
            canonical(
                {
                    "identity": qualification.identity(),
                    "sequence": self.requests[-1]["sequence"],
                    "complete": True,
                    "score": 0.91,
                    "windows": 1,
                }
            )
            + b"\n"
        )

    def kill(self):
        self.kills += 1
        self.returncode = -9

    async def communicate(self):
        self.reaps += 1
        return b"", b""


def test_sequential_dispatch_sends_no_case_id_labels_category_or_provenance():
    async def run():
        child = qualification.QualificationChild("/model")
        child.process = Process()
        value = await child.evaluate(0, "content only", time.monotonic() + 1)
        assert value["score"] == 0.91 and child.attempts == 1
        assert set(child.process.requests[0]) == {
            "identity",
            "sequence",
            "text",
            "deadline",
        }
        assert child.process.requests[0]["text"] == "content only"

    asyncio.run(run())


def test_deadline_kills_exact_child_and_never_retries_or_reloads_remaining_case():
    async def run():
        child = qualification.QualificationChild("/model")
        child.process = Process(block=True)
        with pytest.raises(ClassifierError) as error:
            await child.evaluate(0, "content only", time.monotonic() + 0.001)
        assert error.value.code == "evaluation_timeout"
        with pytest.raises(ClassifierError) as error:
            await child.evaluate(1, "next content", time.monotonic() + 1)
        assert error.value.code == "evaluator_unavailable"
        assert child.attempts == 1 and child.process.kills == child.process.reaps == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "response",
    [b"", b"{}\n", b"{invalid}\n", canonical({"error": "private text"}) + b"\n"],
)
def test_malformed_protocol_closes_without_raw_content_retention(response):
    async def run():
        child = qualification.QualificationChild("/model")
        child.process = Process(response=response)
        with pytest.raises(ClassifierError):
            await child.evaluate(0, "content only", time.monotonic() + 1)
        assert child.process.kills == 1 and child.unavailable is True

    asyncio.run(run())


def test_score_retains_all_1800_rows_without_metadata_dispatch_or_retry(
    monkeypatch, tmp_path
):
    class Child:
        instances: ClassVar[list] = []

        def __init__(self, directory, output):
            self.attempts, self.loaded, self.closed, self.texts = (
                0,
                {"scoring_requests": 0},
                False,
                [],
            )
            self.instances.append(self)

        async def start(self):
            pass

        async def evaluate(self, sequence, text, deadline):
            self.attempts += 1
            self.texts.append(text)
            if sequence == 1:
                raise ClassifierError("inspection_incomplete")
            return {
                "score": 0.91 if (sequence % 600) in range(200, 400) else 0.01,
                "windows": 1,
            }

        async def close(self):
            self.closed = True

    monkeypatch.setattr(qualification, "QualificationChild", Child)
    output = qualification.Output(tmp_path / "attempt")
    try:
        rows, _, _, attempts, digest = asyncio.run(
            qualification.score(
                {kind: synthetic_corpus(kind) for kind in ("original","fresh","fresh_full")},
                "/unused",
                output,
            )
        )
    finally:
        output.close()
    raw = (tmp_path / "attempt/cases.jsonl").read_bytes()
    assert len(rows) == attempts == len(raw.splitlines()) == 1800
    assert (
        digest == qualification.sha(raw) and rows[1]["error"] == "inspection_incomplete"
    )
    assert Child.instances[0].closed is True
    assert (
        Child.instances[0].texts
        == [case["part"]["text"] for case in synthetic_corpus()["cases"]] * 3
    )
    assert b"synthetic text" not in raw


def test_output_root_replacement_refuses_foreign_receipt_and_preserves_original(
    tmp_path,
):
    root = tmp_path / "attempt"
    output = qualification.Output(root)
    output.write("started.json", {"status": "running"})
    root.rename(tmp_path / "preserved")
    root.mkdir(mode=0o700)
    try:
        with pytest.raises(ValueError):
            output.write("result.json", {"status": "completed"})
        assert list(root.iterdir()) == []
        assert json.loads((tmp_path / "preserved/started.json").read_bytes()) == {
            "status": "running"
        }
    finally:
        output.close()


def test_output_parent_replaced_before_open_cannot_redirect_mkdir(
    monkeypatch, tmp_path
):
    parent = tmp_path / "owned"
    parent.mkdir(mode=0o700)
    original = os.open

    def swapped(path, *args, **kwargs):
        if path == parent:
            parent.rename(tmp_path / "preserved-parent")
            parent.mkdir(mode=0o700)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapped)
    with pytest.raises(ValueError, match="output_parent_changed"):
        qualification.Output(parent / "attempt")
    assert list(parent.iterdir()) == []
    assert list((tmp_path / "preserved-parent").iterdir()) == []


def test_output_parent_replaced_after_anchored_mkdir_keeps_foreign_tree_empty(
    monkeypatch, tmp_path
):
    parent = tmp_path / "owned"
    parent.mkdir(mode=0o700)
    original = os.mkdir

    def swapped(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == "attempt" and kwargs.get("dir_fd") is not None:
            parent.rename(tmp_path / "preserved-parent")
            original(parent, mode=0o700)
        return result

    monkeypatch.setattr(os, "mkdir", swapped)
    with pytest.raises(ValueError, match="output_parent_changed"):
        qualification.Output(parent / "attempt")
    assert list(parent.iterdir()) == []
    assert (tmp_path / "preserved-parent/attempt").is_dir()
    assert list((tmp_path / "preserved-parent/attempt").iterdir()) == []


def test_expired_request_has_zero_attempts_and_preserves_worker():
    async def run():
        child = qualification.QualificationChild("/model")
        child.process = Process()
        with pytest.raises(ClassifierError) as error:
            await child.evaluate(0, "content only", time.monotonic() - 1)
        assert error.value.code == "evaluation_timeout"
        assert child.attempts == child.process.kills == len(child.process.requests) == 0

    asyncio.run(run())


def test_authenticated_finite_refusal_preserves_single_worker_for_later_case():
    async def run():
        child = qualification.QualificationChild("/model")
        child.process = Process(
            response=canonical(
                {
                    "identity": qualification.identity(),
                    "sequence": 0,
                    "error": "inspection_incomplete",
                }
            )
            + b"\n"
        )
        with pytest.raises(ClassifierError) as error:
            await child.evaluate(0, "content only", time.monotonic() + 1)
        assert error.value.code == "inspection_incomplete"
        assert child.process.kills == 0 and child.unavailable is False
        child.process.response = None
        assert (await child.evaluate(1, "content only", time.monotonic() + 1))[
            "complete"
        ] is True
        assert child.attempts == 2

    asyncio.run(run())


def loaded_metadata_fixture():
    compat = qualification.wolf_full_compat
    return {
        "translation": compat.TRANSLATION,
        "model_type": "modernbert",
        "parameters": compat.PARAMETERS,
        "dtype": "float32",
        "device": "cpu",
        "attention": "sdpa",
        "compile": False,
        "layers": 22,
        "global_layers": 8,
        "local_layers": 14,
        "local_window_each_side": 64,
        "global_rope_theta": 160000,
        "local_rope_theta": 160000,
        "classifier_pooling": "mean",
        "missing_weight_keys": 0,
        "unexpected_weight_keys": 0,
        "mismatched_weight_keys": 0,
        "error_messages": 0,
        "scoring_requests": 0,
        "repository": compat.REPOSITORY,
        "revision": compat.REVISION,
        "weights_sha256": compat.WEIGHTS_SHA256,
        "config_sha256": compat.CONFIG_SHA256,
        "files": dict(compat.FILES),
        "versions": {
            "torch": "2.10.0+cpu",
            "transformers": "4.57.6",
            "tokenizers": "0.22.2",
            "sentencepiece": "0.2.1",
        },
        "tokenizer": "native-tokenizers",
        "added_tokens": 249,
        "embedded_truncation_disabled": True,
        "single_template_ids": [2, 1],
        "windows": {"maximum": 16, "tokens": 512, "stride": 256},
        "qualification_only": True,
        "quality_verified": False,
    }


def network_fixture():
    return {
        "schema_version": "classifier-offline-namespace-v1",
        "namespace": "net:[2]",
        "host_namespace": "net:[1]",
        "interfaces": ["lo"],
        "external_routes": 0,
        "ipv4_connect_errno": 101,
        "ipv6_connect_errno": 101,
        "links": [
            {
                "name": "lo",
                "index": 1,
                "hardware_type": 772,
                "flags": 8,
                "kind": None,
                "operstate": 0,
                "carrier": 0,
                "link_index": 0,
                "master_index": 0,
                "foreign_namespace_link": False,
                "tunnel": None,
                "address_count": 0,
                "route_count": 0,
            }
        ],
    }


def report_fixture(monkeypatch, *, failed_corpus=None, spanish_failure=False):
    corpus_raws = {
        kind: pinned_corpus(monkeypatch, kind) for kind in ("original", "fresh", "fresh_full")
    }
    sources = {
        name: qualification.FIXED_SOURCES.get(name, "b" * 64)
        for name in qualification.SOURCE_FILES
    }
    network = network_fixture()
    started = {
        "schema_version": "wolf-full-qualification-result-v1",
        "status": "running",
        "created_at": "2026-10-12T04:00:01+00:00",
        "plan_sha256": "c" * 64,
        "sources": sources,
        "identity": qualification.identity(),
        "corpora": {kind: qualification.sha(raw) for kind, raw in corpus_raws.items()},
        "prior_failed_protectai": qualification.PRIOR_FAILURE,
        "prior_failed_wolf_small": qualification.PRIOR_SMALL_FAILURE,
        "resource_limits": qualification.RESOURCE_LIMITS,
        "threshold": 0.9,
        "minimum_recall": 0.9,
        "maximum_false_positive_rate": 0.05,
        "scope": qualification.SCOPE,
        "warmup_requests": 0,
        "retry_requests": 0,
        "model_reloads": 0,
        "network": network,
    }
    rows = []
    for kind in ("original", "fresh", "fresh_full"):
        for index, case in enumerate(synthetic_corpus()["cases"]):
            prediction = case["expected_injection"]
            if kind == failed_corpus and index < 20:
                prediction = True
            if spanish_failure and case["language"] == "es":
                prediction = True
            rows.append(
                {
                    "corpus": kind,
                    "case_id": case["case_id"],
                    "language": case["language"],
                    "category": case["category"],
                    "family_id": case["family_id"],
                    "expected": case["expected_injection"],
                    "predicted": prediction,
                    "score": 0.95 if prediction else 0.05,
                    "error": None,
                    "elapsed_ms": 10.0,
                    "windows": 1,
                }
            )
    cases = b"".join(canonical(row) + b"\n" for row in rows)
    matrices = {
        kind: {
            language: qualification.matrix(
                [row for row in rows if row["corpus"] == kind], language
            )
            for language in ("en", "es")
        }
        for kind in ("original", "fresh", "fresh_full")
    }
    result = {
        **started,
        "status": "completed",
        "cases": 1800,
        "matrices": matrices,
        "quality_pass": failed_corpus is None,
        "resources_pass": True,
        "cases_sha256": qualification.sha(cases),
        "startup_seconds": 2.0,
        "loaded": loaded_metadata_fixture(),
        "cpu": {
            "child_user_seconds": 2.0,
            "child_system_seconds": 0.1,
            "child_max_rss_kib": 50000,
            "torch_threads": 2,
            "torch_interop_threads": 1,
            "affinity": [2, 4],
        },
        "cost": {
            "cases_observed": 1800,
            "attempted_dispatches": 1800,
            "completed_windows": 1800,
            "failed_cases": 0,
            "forward_passes_including_failed_cases": "unavailable",
            "tokens": "unavailable",
            "tool_credits": "unavailable",
        },
        "network_after": network,
        "spanish_enforcement_enabled": False,
        "enabled_model_changed": False,
    }
    return corpus_raws, started, result, cases, sources


def validate_fixture(value, **kwargs):
    corpora, started, result, rows, sources = value
    return qualification.validate_report(
        corpora, canonical(started), canonical(result), rows, sources, **kwargs
    )


def test_complete_all_three_corpora_are_recomputed_from_all_1800_rows(monkeypatch):
    checked = validate_fixture(report_fixture(monkeypatch))
    assert checked["status"] == "qualified" and checked["cases"] == 1800
    assert (
        checked["production_enabled"] is checked["spanish_enforcement_enabled"] is False
    )
    assert checked["deployed_acceptance_verified"] is False


@pytest.mark.parametrize("kind", ["original", "fresh", "fresh_full"])
def test_either_original_or_fresh_english_failure_is_retained_but_rejected(
    monkeypatch, kind
):
    value = report_fixture(monkeypatch, failed_corpus=kind)
    assert validate_fixture(value, require_quality=False)["status"] == "quality_failed"
    with pytest.raises(ValueError, match="english_quality_failed"):
        validate_fixture(value)


def test_spanish_quality_failure_remains_diagnostic_and_never_enables_it(monkeypatch):
    checked = validate_fixture(report_fixture(monkeypatch, spanish_failure=True))
    assert (
        checked["status"] == "qualified"
        and checked["spanish_enforcement_enabled"] is False
    )
    assert checked["matrices"]["original"]["es"]["quality_pass"] is False


@pytest.mark.parametrize(
    "change",
    [
        "missing_row",
        "extra_row",
        "duplicate_row",
        "changed_score",
        "changed_label",
        "missing_tail",
        "row_raw_text",
        "noncanonical",
        "matrix",
        "summary_count",
        "identity",
        "cutoff",
        "enabled",
        "spanish",
        "retry",
        "reload",
        "warmup",
        "cpu",
        "window_total",
        "source",
        "loaded_rope",
        "loaded_version",
        "loaded_extra",
        "network",
        "plan_sha",
        "timestamp",
    ],
)
def test_complete_consumer_rejects_tampered_or_incomplete_reports(monkeypatch, change):
    value = list(report_fixture(monkeypatch))
    _, started, result, rows, sources = value
    if change in {
        "missing_row",
        "extra_row",
        "duplicate_row",
        "changed_score",
        "changed_label",
        "missing_tail",
        "row_raw_text",
        "noncanonical",
    }:
        lines = rows.splitlines(keepends=True)
        if change == "missing_row":
            lines.pop()
        elif change == "extra_row":
            lines.append(lines[0])
        elif change == "duplicate_row":
            lines[1] = lines[0]
        elif change == "noncanonical":
            lines[0] = b" " + lines[0]
        else:
            row = json.loads(lines[0])
            if change == "changed_score":
                row["score"] = 0.95
            elif change == "changed_label":
                row["expected"] = True
            elif change == "missing_tail":
                row["windows"] = 0
            else:
                row["text"] = "must not be retained"
            lines[0] = canonical(row) + b"\n"
        value[3] = b"".join(lines)
        result["cases_sha256"] = qualification.sha(value[3])
    elif change == "matrix":
        result["matrices"]["fresh"]["en"]["false_positive"] = 1
    elif change == "summary_count":
        result["cases"] = 600
    elif change == "identity":
        result["identity"] = {"repository": "old model"}
    elif change == "cutoff":
        result["threshold"] = started["threshold"] = 0.95
    elif change == "enabled":
        result["enabled_model_changed"] = True
    elif change == "spanish":
        result["spanish_enforcement_enabled"] = True
    elif change in {"retry", "reload", "warmup"}:
        key = {
            "retry": "retry_requests",
            "reload": "model_reloads",
            "warmup": "warmup_requests",
        }[change]
        result[key] = started[key] = 1
    elif change == "cpu":
        result["cpu"]["torch_threads"] = 8
    elif change == "window_total":
        result["cost"]["completed_windows"] = 1199
    elif change == "source":
        sources[qualification.SOURCE_FILES[0]] = "e" * 64
    elif change == "loaded_rope":
        result["loaded"]["local_rope_theta"] = 10000
    elif change == "loaded_version":
        result["loaded"]["versions"]["transformers"] = "5.10.2"
    elif change == "loaded_extra":
        result["loaded"]["unverified"] = True
    elif change == "network":
        result["network_after"] = {**result["network"], "external_routes": 1}
    elif change == "plan_sha":
        result["plan_sha256"] = started["plan_sha256"] = "not-a-pin"
    else:
        result["created_at"] = started["created_at"] = "2026-10-12T04:00:00"
    with pytest.raises((ValueError, ClassifierError)):
        validate_fixture(value)


def saved_chain(monkeypatch, tmp_path):
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    for name in qualification.SOURCE_FILES:
        path = root / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text("# closed synthetic source " + name + "\n")
        path.chmod(0o644)
    sources = {
        name: qualification.sha((root / name).read_bytes())
        for name in qualification.SOURCE_FILES
    }
    monkeypatch.setattr(
        qualification,
        "FIXED_SOURCES",
        {
            name: pin
            for name, pin in sources.items()
            if name != "services/classifier/wolf_full_qualification.py"
        },
    )
    report = list(report_fixture(monkeypatch))
    refs = {}

    def put(name, raw):
        path = tmp_path / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
        path.chmod(0o600)
        value = {"path": str(path), "sha256": qualification.sha(raw)}
        refs[name] = value
        return value

    corpora = {
        kind: put("corpus-" + kind + ".json", raw) for kind, raw in report[0].items()
    }
    provenance = {}
    for name, constant in [
        ("author_freeze", "FRESH_AUTHOR_SHA256"),
        ("full_author_freeze", "FULL_FRESH_AUTHOR_SHA256"),
        ("full_parent_corpus_review", "FULL_PARENT_CORPUS_REVIEW_SHA256"),
        ("parent_corpus_review", "PARENT_CORPUS_REVIEW_SHA256"),
        ("approval", "APPROVAL_SHA256"),
        ("artifact_receipt", "ARTIFACT_RECEIPT_SHA256"),
    ]:
        provenance[name] = put(
            name + ".json", canonical({"kind": name, "fixture": True})
        )
        monkeypatch.setattr(qualification, constant, provenance[name]["sha256"])
    prior = {
        name: put("prior/" + name, canonical({"kind": name, "status": "failed"}))
        for name in qualification.PRIOR_FAILURE
    }
    monkeypatch.setattr(
        qualification,
        "PRIOR_FAILURE",
        {name: row["sha256"] for name, row in prior.items()},
    )
    small_prior = {name: put("small-prior/" + name, canonical({"kind":name,"status":"failed"}))
                   for name in qualification.PRIOR_SMALL_FAILURE}
    monkeypatch.setattr(qualification, "PRIOR_SMALL_FAILURE", {name: row["sha256"] for name,row in small_prior.items()})
    helper = put("load-helper.py", b"# synthetic closed helper source\n")
    monkeypatch.setattr(qualification, "LOAD_HELPER_SHA256", helper["sha256"])
    closure = put("load/closure.json", canonical({"/synthetic/python": ["a" * 64, 1]}))
    net = {**report[1]["network"], "ipv4_connect_errno": 101, "ipv6_connect_errno": 101}
    load_start = {
        "schema_version": "wolf-full-load-only-start-v1",
        "scope": "load_only_not_qualified",
        "helper_sha256": helper["sha256"],
        "host_namespace": net["host_namespace"],
        "maximum_seconds": 300,
        "affinity_cpus": [2,4],
        "acquisition_receipt_sha256": qualification.ARTIFACT_RECEIPT_SHA256,
        "network_observer_sha256": sources[qualification.SOURCE_FILES[6]],
        "predecessor_helper_sha256": qualification.LOAD_PREDECESSOR_SHA256,
        "pins": {
            "/compat.py": sources[qualification.SOURCE_FILES[2]],
            "/python-receipt": qualification.LOAD_PYTHON_RECEIPT,
            "/approval-receipt": qualification.APPROVAL_SHA256,
            "/network.py": sources[qualification.SOURCE_FILES[6]],
            **{
                "/" + name: pin
                for name, pin in qualification.LOAD_PROVENANCE.items()
                if name != "python_archive_sha256"
            },
        },
    }
    child = {
        "schema_version": "wolf-full-load-only-child-v1",
        "scope": "load_only_not_qualified",
        "status": "loaded",
        "helper_sha256": helper["sha256"],
        "compat_sha256": sources[qualification.SOURCE_FILES[2]],
        "network_observer_sha256": sources[qualification.SOURCE_FILES[6]],
        "predecessor_helper_sha256": qualification.LOAD_PREDECESSOR_SHA256,
        "metadata": loaded_metadata_fixture(),
        "resource_admission": {"schema_version":"wolf-load-resource-admission-v1", "affinity_cpus":[2,4],
            "torch_threads":2,"torch_interop_threads":1,"peak_rss_bytes":1024,
            "maximum_rss_bytes":3072*1024**2,"admitted":True,
            "memory_scope":"measured_peak_rss_not_cgroup_limit","cpu_scope":"two_cpu_affinity_not_cfs_quota"},
        "acquisition_receipt_sha256": qualification.ARTIFACT_RECEIPT_SHA256,
        "network_before": net,
        "network_after": net,
        "predictor_calls": 0,
        "tokenizer_encode_calls": 0,
        "elapsed_ms": 5.0,
    }
    cleanup = put("load/cleanup.json", canonical({"schema_version":"wolf-full-load-cleanup-v1",
        "group_signal_requested":True,"group_already_absent":False,"leader_reaped":True,
        "leader_exit_observed":True,"errors":[]}))
    child_ref = put("load/child.json", canonical(child))
    result = {
        **child,
        "schema_version": "wolf-full-load-only-result-v1",
        "quality_verified": False,
        "enabled_model_changed": False,
        **qualification.LOAD_PROVENANCE,
        "child_sha256": child_ref["sha256"],
        "cleanup_sha256": cleanup["sha256"],
        "installed_closure_sha256": closure["sha256"],
        "installed_files": 1,
    }
    plan = {
        "schema_version": "wolf-full-qualification-plan-v1",
        "sources": sources,
        "corpora": corpora,
        **provenance,
        "prior_failure": prior,
        "prior_small_failure": small_prior,
        "load_only": {
            "helper": helper,
            "closure": closure,
            "child": child_ref,
            "cleanup": cleanup,
            "started": put("load/started.json", canonical(load_start)),
            "result": put("load/result.json", canonical(result)),
        },
        "model_directory": str(tmp_path / "model"),
        "output": str(tmp_path / "quality-output"),
        "host_namespace": net["host_namespace"],
    }
    report[1].update(
        sources=sources, prior_failed_protectai=qualification.PRIOR_FAILURE,
        prior_failed_wolf_small=qualification.PRIOR_SMALL_FAILURE
    )
    report[2].update(
        sources=sources, prior_failed_protectai=qualification.PRIOR_FAILURE,
        prior_failed_wolf_small=qualification.PRIOR_SMALL_FAILURE
    )
    report[4] = sources
    return root, plan, report, put


def test_closed_plan_authenticates_actual_saved_bytes_and_prior_load_lineage(
    monkeypatch, tmp_path
):
    root, plan, report, _ = saved_chain(monkeypatch, tmp_path)
    corpus_raws, closure = qualification.validate_plan(plan, root)
    assert corpus_raws == report[0] and closure == {"/synthetic/python": ["a" * 64, 1]}


@pytest.mark.parametrize(
    "change",
    [
        "load_missing",
        "load_failure",
        "closure_changed",
        "child_hash",
        "compat",
        "helper",
        "prior",
        "source",
        "added_package",
        "network",
        "scoring_calls",
        "false_zero",
        "new_dependency",
        "metadata",
        "extra_field",
        "corpus",
        "author",
        "failed_status",
        "enabled",
        "quality_claim",
        "not_private",
    ],
)
def test_prelaunch_refuses_incomplete_changed_or_wrong_load_history(
    monkeypatch, tmp_path, change
):
    root, plan, _, _ = saved_chain(monkeypatch, tmp_path)
    load = plan["load_only"]
    if change == "load_missing":
        from pathlib import Path

        Path(load["result"]["path"]).unlink()
    elif change == "load_failure":
        from pathlib import Path

        Path(load["result"]["path"]).with_name("failure.json").write_text("{}")
    elif change == "closure_changed":
        from pathlib import Path

        Path(load["closure"]["path"]).write_text("{}")
    elif change == "helper":
        load["helper"]["sha256"] = "a" * 64
    elif change == "prior":
        plan["prior_failure"]["result.json"]["sha256"] = "a" * 64
    elif change == "source":
        (root / qualification.SOURCE_FILES[1]).write_text("# changed\n")
    elif change == "added_package":
        (root / "services/__init__.py").write_text("# newly executable\n")
    elif change == "corpus":
        plan["corpora"]["fresh"]["sha256"] = "a" * 64
    elif change == "author":
        plan["author_freeze"]["sha256"] = "a" * 64
    elif change == "not_private":
        from pathlib import Path

        Path(load["result"]["path"]).chmod(0o644)
    else:
        from pathlib import Path

        path = Path(load["result"]["path"])
        result = json.loads(path.read_bytes())
        if change == "child_hash":
            result["child_sha256"] = "a" * 64
        elif change == "compat":
            result["compat_sha256"] = "a" * 64
        elif change == "network":
            result["network_after"] = {
                **result["network_after"],
                "namespace": "net:[3]",
            }
        elif change == "scoring_calls":
            result["predictor_calls"] = 1
        elif change == "false_zero":
            result["tokenizer_encode_calls"] = False
        elif change == "new_dependency":
            result["wheel_receipt_sha256"] = "a" * 64
        elif change == "metadata":
            result["metadata"]["local_rope_theta"] = 10000
        elif change == "extra_field":
            result["unknown_proof"] = True
        elif change == "failed_status":
            result["status"] = "failed"
        elif change == "enabled":
            result["enabled_model_changed"] = True
        elif change == "quality_claim":
            result["quality_verified"] = True
        raw = canonical(result)
        path.write_bytes(raw)
        load["result"]["sha256"] = qualification.sha(raw)
    with pytest.raises((OSError, ValueError, KeyError, ClassifierError)):
        qualification.validate_plan(plan, root)


def test_output_child_replaced_between_stat_and_open_refuses_foreign_receipt(
    monkeypatch, tmp_path
):
    parent = tmp_path / "owned"
    parent.mkdir(mode=0o700)
    path = parent / "attempt"
    original = os.open

    def swapped(name, *args, **kwargs):
        if name == "attempt" and kwargs.get("dir_fd") is not None:
            path.rename(parent / "preserved-child")
            path.mkdir(mode=0o700)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapped)
    with pytest.raises(ValueError, match="output_changed"):
        qualification.Output(path)
    assert list(path.iterdir()) == []
    assert list((parent / "preserved-child").iterdir()) == []


@pytest.mark.parametrize(
    "mutation",
    ["none", "new_payload", "missing_payload", "changed_payload", "symlink_directory"],
)
@pytest.mark.parametrize("outer_interpreter", [False, True])
def test_installed_payload_closure_is_rechecked_without_loading_packages(
    monkeypatch, tmp_path, mutation, outer_interpreter
):
    base = tmp_path / "base"
    venv = tmp_path / "venv"
    (base / "bin").mkdir(parents=True, mode=0o700)
    (base / "lib/python3.13").mkdir(parents=True, mode=0o700)
    (venv / "bin").mkdir(parents=True, mode=0o700)
    site = venv / "lib/python3.13/site-packages"
    site.mkdir(parents=True, mode=0o700)
    executable = base / "bin/python"
    executable.write_bytes(b"synthetic executable, never run")
    executable.chmod(0o700)
    (venv / "bin/python").symlink_to(executable)
    payload = site / "synthetic.py"
    payload.write_bytes(b"# authenticated synthetic payload\n")
    payload.chmod(0o600)
    stdlib = base / "lib/python3.13/synthetic_stdlib.py"
    stdlib.write_bytes(b"# authenticated stdlib fixture\n")
    stdlib.chmod(0o600)
    closure = {
        str(path): [qualification.sha(path.read_bytes()), path.stat().st_size]
        for path in (executable, payload, stdlib)
    }
    options = {"executable": venv / "bin/python"} if outer_interpreter else {}
    if not outer_interpreter:
        monkeypatch.setattr(qualification.sys, "executable", str(venv / "bin/python"))
    if mutation == "new_payload":
        (site / "injected.py").write_text("# unexpected executable input")
    elif mutation == "missing_payload":
        payload.unlink()
    elif mutation == "changed_payload":
        payload.write_text("# replaced")
    elif mutation == "symlink_directory":
        (site / "foreign").symlink_to(base, target_is_directory=True)
    if mutation == "none":
        qualification.verify_environment(closure, **options)
    else:
        with pytest.raises((OSError, ValueError)):
            qualification.verify_environment(closure, **options)


@pytest.mark.parametrize(
    "outcome",
    [
        "pass",
        "original_quality_fail",
        "fresh_quality_fail",
        "fresh_full_quality_fail",
        "resource_fail",
        "source_changes_after_score",
    ],
)
def test_actual_outer_retention_keeps_complete_failures_and_refuses_late_source_drift(
    monkeypatch, tmp_path, outcome
):
    from types import SimpleNamespace

    root, plan, _, _ = saved_chain(monkeypatch, tmp_path)
    bad = {"original_quality_fail": "original", "fresh_quality_fail": "fresh", "fresh_full_quality_fail": "fresh_full"}.get(
        outcome
    )
    report = report_fixture(monkeypatch, failed_corpus=bad)
    calls = []
    monkeypatch.setattr(
        qualification,
        "verify_environment",
        lambda closure: calls.append(("environment", len(closure))),
    )
    monkeypatch.setattr(
        qualification, "network_snapshot", lambda host: report[1]["network"]
    )
    monkeypatch.setattr(
        qualification.resource,
        "getrusage",
        lambda who: SimpleNamespace(ru_utime=1.0, ru_stime=0.2,
                                    ru_maxrss=3072 * 1024 + 1 if outcome == "resource_fail" else 50000),
    )

    async def score(corpora, directory, output):
        assert (
            set(corpora) == {"original", "fresh", "fresh_full"}
            and directory == plan["model_directory"]
        )
        calls.append(("one_score", 1800))
        with output.open("cases.jsonl") as stream:
            stream.write(report[3])
        if outcome == "source_changes_after_score":
            (root / qualification.SOURCE_FILES[1]).write_text(
                "# changed during scoring\n"
            )
        return (
            [json.loads(line) for line in report[3].splitlines()],
            1.0,
            loaded_metadata_fixture(),
            1800,
            qualification.sha(report[3]),
        )

    monkeypatch.setattr(qualification, "score", score)
    if outcome == "source_changes_after_score":
        with pytest.raises(ValueError):
            qualification.run_plan(plan, root, "a" * 64)
        assert (tmp_path / "quality-output/failure.json").exists()
        assert not (tmp_path / "quality-output/result.json").exists()
    else:
        status = qualification.run_plan(plan, root, "a" * 64)
        assert status == (0 if outcome == "pass" else 1)
        result = json.loads((tmp_path / "quality-output/result.json").read_bytes())
        assert result["quality_pass"] is (outcome in {"pass", "resource_fail"}) and result["cases"] == 1800
        assert result["resources_pass"] is (outcome != "resource_fail")
        assert result["enabled_model_changed"] is False
        assert not (tmp_path / "quality-output/failure.json").exists()
    assert calls.count(("one_score", 1800)) == 1
    assert (
        len((tmp_path / "quality-output/cases.jsonl").read_bytes().splitlines()) == 1800
    )


@pytest.mark.parametrize("document_name", ["started", "child", "result"])
@pytest.mark.parametrize(
    "change",
    ["old_schema", "missing_network_pin", "wrong_network_pin", "wrong_predecessor"],
)
def test_load_v2_network_and_predecessor_lineage_are_independently_bound(
    monkeypatch, tmp_path, document_name, change
):
    from pathlib import Path

    root, plan, _, _ = saved_chain(monkeypatch, tmp_path)
    load = plan["load_only"]
    path = Path(load[document_name]["path"])
    value = json.loads(path.read_bytes())
    if change == "old_schema":
        value["schema_version"] = value["schema_version"].replace("full-", "")
    elif change == "missing_network_pin":
        del value["network_observer_sha256"]
    elif change == "wrong_network_pin":
        value["network_observer_sha256"] = "f" * 64
    else:
        value["predecessor_helper_sha256"] = "f" * 64
    raw = canonical(value)
    path.write_bytes(raw)
    load[document_name]["sha256"] = qualification.sha(raw)
    if document_name == "child":
        result_path = Path(load["result"]["path"])
        result = json.loads(result_path.read_bytes())
        result["child_sha256"] = load["child"]["sha256"]
        result_raw = canonical(result)
        result_path.write_bytes(result_raw)
        load["result"]["sha256"] = qualification.sha(result_raw)
    with pytest.raises((ValueError, KeyError, ClassifierError)):
        qualification.validate_plan(plan, root)


def bootstrap_fixture(monkeypatch, tmp_path):
    import sys
    from contextlib import contextmanager

    root = tmp_path / "bootstrap-source"
    root.mkdir(mode=0o700)
    for name in qualification.SOURCE_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text("# captured synthetic module\n")
        path.chmod(0o644)
    (root / "mvp/guardrails/__init__.py").write_text(
        "import builtins; builtins._wolf_boot_calls.append('guard')\n"
    )
    (root / "services/classifier/worker.py").write_text(
        "import builtins; builtins._wolf_boot_calls.append('worker')\n"
    )
    (root / "services/classifier/wolf_full_qualification.py").write_text(
        "import hashlib,builtins\n"
        "def source_snapshot(root):\n"
        "    return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in "
        + repr(qualification.SOURCE_FILES)
        + "}\n"
        "def child_main(directory):\n"
        "    builtins._wolf_boot_calls.append('entry'); return 13\n"
    )
    pins = {
        name: qualification.sha((root / name).read_bytes())
        for name in qualification.SOURCE_FILES
    }
    namespace = {"__name__": "synthetic_bootstrap"}
    exec(  # noqa: S102 - execute only the fixed authenticated bootstrap under fake imports.
        compile(qualification.CHILD_BOOTSTRAP, "<authenticated-bootstrap>", "exec"),
        namespace,
    )
    calls = []
    monkeypatch.setattr(builtins, "_wolf_boot_calls", calls, raising=False)

    @contextmanager
    def clean_modules():
        names = [
            name
            for name in sys.modules
            if name in {"guardrails", "services"}
            or name.startswith(("guardrails.", "services."))
        ]
        saved = {name: sys.modules.pop(name) for name in names}
        try:
            yield
        finally:
            for name in list(sys.modules):
                if name in {"guardrails", "services"} or name.startswith(
                    ("guardrails.", "services.")
                ):
                    sys.modules.pop(name)
            sys.modules.update(saved)

    return root, pins, namespace["boot"], calls, clean_modules


def test_nested_model_bootstrap_executes_only_captured_authenticated_bytes(
    monkeypatch, tmp_path
):
    root, pins, boot, calls, clean_modules = bootstrap_fixture(monkeypatch, tmp_path)
    with clean_modules():
        assert boot(root, pins, tmp_path / "unused-model") == 13
    assert calls == ["guard", "worker", "entry"]


@pytest.mark.parametrize(
    "change",
    ["hash", "missing", "fifo", "symlink", "mode", "extra_package", "extra_pin"],
)
def test_nested_model_bootstrap_refuses_before_any_source_execution(
    monkeypatch, tmp_path, change
):
    root, pins, boot, calls, clean_modules = bootstrap_fixture(monkeypatch, tmp_path)
    path = root / "services/classifier/worker.py"
    if change == "hash":
        path.write_text("raise RuntimeError('unverified code must never run')\n")
    elif change == "missing":
        path.unlink()
    elif change == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif change == "symlink":
        path.unlink()
        path.symlink_to(root / "mvp/guardrails/__init__.py")
    elif change == "mode":
        path.chmod(0o666)
    elif change == "extra_package":
        (root / "services/__init__.py").write_text(
            "raise RuntimeError('unverified package')\n"
        )
    else:
        pins["foreign.py"] = "f" * 64
    with clean_modules(), pytest.raises((ValueError, OSError)):
        boot(root, pins, tmp_path / "unused-model")
    assert calls == []


def test_nested_model_bootstrap_path_replacement_after_capture_never_executes_new_bytes(
    monkeypatch, tmp_path
):
    root, pins, boot, calls, clean_modules = bootstrap_fixture(monkeypatch, tmp_path)
    original = builtins.compile
    changed = False

    def swapped(raw, filename, mode, *args, **kwargs):
        nonlocal changed
        if not changed and filename == str(root / "mvp/guardrails/__init__.py"):
            changed = True
            (root / "services/classifier/worker.py").write_text(
                "import builtins; builtins._wolf_boot_calls.append('evil')\n"
            )
        return original(raw, filename, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "compile", swapped)
    with clean_modules(), pytest.raises(ValueError):
        boot(root, pins, tmp_path / "unused-model")
    assert calls == ["guard", "worker"]


@pytest.mark.parametrize(
    "change",
    [
        "exact_blank",
        "loopback_address",
        "loopback_route",
        "ipv4_missing_source",
        "ipv6_timeout",
    ],
)
def test_load_network_uses_shared_closed_numeric_no_source_evidence(
    monkeypatch, tmp_path, change
):
    from pathlib import Path

    root, plan, _, _ = saved_chain(monkeypatch, tmp_path)
    load = plan["load_only"]
    child_path, result_path = (Path(load[name]["path"]) for name in ("child", "result"))
    child, result = (
        json.loads(path.read_bytes()) for path in (child_path, result_path)
    )
    net = child["network_before"]
    net["ipv6_connect_errno"] = 99
    if change == "loopback_address":
        net["links"][0]["address_count"] = 1
    elif change == "loopback_route":
        net["links"][0]["route_count"] = 1
    elif change == "ipv4_missing_source":
        net["ipv4_connect_errno"] = 99
    elif change == "ipv6_timeout":
        net["ipv6_connect_errno"] = 110
    child["network_after"] = net
    result["network_before"] = result["network_after"] = net
    raw = canonical(child)
    child_path.write_bytes(raw)
    load["child"]["sha256"] = qualification.sha(raw)
    result["child_sha256"] = load["child"]["sha256"]
    raw = canonical(result)
    result_path.write_bytes(raw)
    load["result"]["sha256"] = qualification.sha(raw)
    if change == "exact_blank":
        qualification.validate_plan(plan, root)
        assert net["ipv4_connect_errno"] == 101 and net["ipv6_connect_errno"] == 99
    else:
        with pytest.raises(
            (ValueError, qualification.network_isolation.IsolationError)
        ):
            qualification.validate_plan(plan, root)


@pytest.mark.parametrize(
    "kind,change",
    [
        ("original", "missing_kind"),
        ("original", "wrong_kind"),
        ("original", "nonstring_kind"),
        ("original", "extra"),
        ("fresh", "unexpected_kind"),
        ("fresh", "extra"),
        ("fresh_full", "unexpected_kind"),
        ("fresh_full", "extra"),
    ],
)
def test_each_immutable_corpus_keeps_its_exact_distinct_part_schema(
    monkeypatch, kind, change
):
    def mutate(value):
        part = value["cases"][0]["part"]
        if change == "missing_kind":
            del part["kind"]
        elif change == "wrong_kind":
            part["kind"] = "json"
        elif change == "nonstring_kind":
            part["kind"] = True
        elif change == "unexpected_kind":
            part["kind"] = "text"
        else:
            part["private_metadata"] = "must refuse"

    raw = pinned_corpus(monkeypatch, kind, mutate)
    with pytest.raises(ValueError, match="part_schema"):
        qualification.corpus(raw, kind)


@pytest.mark.parametrize("peak,accepted", [(3072*1024,True),(3072*1024+1,False)])
def test_complete_quality_rows_cannot_bypass_existing_memory_limit(monkeypatch,peak,accepted):
    data=list(report_fixture(monkeypatch))
    data[2]["cpu"]["child_max_rss_kib"]=peak
    data[2]["resources_pass"]=accepted
    observed=validate_fixture(data,require_quality=False)
    assert observed["resources_pass"] is accepted
    assert observed["status"]==("qualified" if accepted else "resource_failed")
    if accepted: validate_fixture(data)
    else:
        with pytest.raises(ValueError,match="resource_limit_exceeded"): validate_fixture(data)


def test_resource_summary_cannot_claim_pass_above_memory_cap(monkeypatch):
    data=list(report_fixture(monkeypatch))
    data[2]["cpu"]["child_max_rss_kib"]=3072*1024+1
    with pytest.raises(ValueError,match="resource_summary"):
        validate_fixture(data,require_quality=False)


@pytest.mark.parametrize("kind", ["original","fresh","fresh_full"])
def test_any_one_english_corpus_failure_prevents_full_qualification(monkeypatch,kind):
    data=report_fixture(monkeypatch,failed_corpus=kind)
    assert validate_fixture(data,require_quality=False)["status"]=="quality_failed"
    with pytest.raises(ValueError,match="english_quality_failed"): validate_fixture(data)


@pytest.mark.parametrize("available", [set(), {2}, {False, 2}, {-1, 2}])
def test_cpu_admission_refuses_before_environment_or_model_work(monkeypatch, available):
    monkeypatch.setattr(qualification.os, "sched_getaffinity", lambda pid: available)
    monkeypatch.setattr(qualification, "_run_admitted_plan", lambda *args: pytest.fail("unadmitted work"))
    with pytest.raises(ValueError, match="cpu_available"):
        qualification.run_plan({}, None, "a" * 64)


def test_two_cpu_admission_is_actual_and_restored_after_primary_failure(monkeypatch):
    state = [{2, 4, 6}]
    monkeypatch.setattr(qualification.os, "sched_getaffinity", lambda pid: set(state[0]))
    monkeypatch.setattr(qualification.os, "sched_setaffinity", lambda pid, cores: state.__setitem__(0, set(cores)))
    def original(*args):
        assert state[0] == {2, 4} and args[-1] == [2, 4]
        raise RuntimeError("original_failure")
    monkeypatch.setattr(qualification, "_run_admitted_plan", original)
    with pytest.raises(RuntimeError, match="original_failure"):
        qualification.run_plan({}, None, "a" * 64)
    assert state[0] == {2, 4, 6}


@pytest.mark.parametrize("cores", [[2], [2,2], [4,2], [-1,2], [False,2], [2,4,6]])
def test_saved_cpu_affinity_is_closed_exact_two_cores(monkeypatch, cores):
    data = list(report_fixture(monkeypatch))
    data[2]["cpu"]["affinity"] = cores
    with pytest.raises(ValueError, match="cpu_affinity"):
        validate_fixture(data)


@pytest.mark.parametrize("field,value", [
    ("peak_rss_bytes",3072*1024**2+1), ("peak_rss_bytes",True),
    ("maximum_rss_bytes",4096*1024**2), ("affinity_cpus",[4,6]),
    ("torch_threads",4), ("torch_interop_threads",2), ("admitted",False),
    ("memory_scope","cgroup_enforced"), ("cpu_scope","quota_enforced")])
def test_actual_load_resource_proof_cannot_be_replaced_by_configuration(monkeypatch,tmp_path,field,value):
    from pathlib import Path
    root,plan,_,_ = saved_chain(monkeypatch,tmp_path)
    load = plan["load_only"]
    for name in ("child", "result"):
        path = Path(load[name]["path"])
        doc = json.loads(path.read_bytes())
        doc["resource_admission"][field] = value
        if name == "result": doc["child_sha256"] = load["child"]["sha256"]
        raw = canonical(doc); path.write_bytes(raw); load[name]["sha256"] = qualification.sha(raw)
    with pytest.raises(ValueError, match="load_resource"):
        qualification.validate_plan(plan,root)


@pytest.mark.parametrize("field,value", [("leader_reaped",False),("leader_exit_observed",False),
    ("errors",["group_signal_failed"]),("group_signal_requested",False),
    ("group_already_absent",True),("schema_version","wolf-load-only-v2")])
def test_owned_load_group_cleanup_must_be_complete_before_qualification(monkeypatch,tmp_path,field,value):
    from pathlib import Path
    root,plan,_,_ = saved_chain(monkeypatch,tmp_path)
    load = plan["load_only"]
    path=Path(load["cleanup"]["path"]); doc=json.loads(path.read_bytes()); doc[field]=value
    raw=canonical(doc);path.write_bytes(raw);load["cleanup"]["sha256"]=qualification.sha(raw)
    path=Path(load["result"]["path"]);doc=json.loads(path.read_bytes());doc["cleanup_sha256"]=load["cleanup"]["sha256"]
    raw=canonical(doc);path.write_bytes(raw);load["result"]["sha256"]=qualification.sha(raw)
    with pytest.raises(ValueError,match="load_cleanup"):
        qualification.validate_plan(plan,root)


def test_affinity_restore_failure_preserves_original_exception(monkeypatch):
    state = [{2,4,6}]
    def set_affinity(pid, cores):
        if cores == {2,4,6}: raise OSError("synthetic restore failure")
        state[0] = set(cores)
    monkeypatch.setattr(qualification.os, "sched_getaffinity", lambda pid: set(state[0]))
    monkeypatch.setattr(qualification.os, "sched_setaffinity", set_affinity)
    def original(*args): raise RuntimeError("original_operation_failure")
    monkeypatch.setattr(qualification, "_run_admitted_plan", original)
    with pytest.raises(RuntimeError, match="original_operation_failure"):
        qualification.run_plan({}, None, "a"*64)
