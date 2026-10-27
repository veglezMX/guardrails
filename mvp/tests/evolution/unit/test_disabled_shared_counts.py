"""Small real-fork checks; no database, socket, model or evaluator child."""
import asyncio
import multiprocessing

import pytest

from mvp.tests.evolution.disabled_observation import ReadinessOnly, SharedCounts


def test_parent_observes_each_actual_child_counter_increment():
    counts = SharedCounts()
    def child():
        for name in counts.names:
            counts.increment(name)
            counts.increment(name)
    process = multiprocessing.get_context("fork").Process(target=child)
    process.start()
    try:
        process.join(5)
        assert process.exitcode == 0
        assert counts.snapshot() == dict.fromkeys(counts.names, 2)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_unknown_counter_and_overflow_refuse_without_wrapping():
    counts = SharedCounts()
    with pytest.raises(AssertionError):
        counts.increment("arbitrary")
    assert counts.snapshot() == dict.fromkeys(counts.names, 0)
    with counts.values.get_lock():
        counts.values[0] = 2**63 - 1
    with pytest.raises(AssertionError):
        counts.increment("evaluate")
    assert counts.snapshot()["evaluate"] == 2**63 - 1


def test_protocol_only_readiness_and_forbidden_dispatch_are_visible_after_fork():
    transport = ReadinessOnly()
    class Route:
        def validate(self):
            return None
    def child():
        async def run():
            result = await transport.request(Route(), method="GET", path="/v1/classifier/ready", body=b"")
            assert result.status_code == 200
            try:
                await transport.request(Route(), method="POST", path="/v1/classifier/evaluate", body=b"private")
            except AssertionError:
                return
            raise AssertionError("Inference must be refused")
        asyncio.run(run())
    process = multiprocessing.get_context("fork").Process(target=child)
    process.start()
    try:
        process.join(5)
        assert process.exitcode == 0
        assert transport.counters.snapshot() == {"evaluate": 0, "evaluate_content_lists": 0,
            "transform": 0, "readiness": 1, "dispatch": 1}
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
