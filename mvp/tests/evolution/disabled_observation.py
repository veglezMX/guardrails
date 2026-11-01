"""Finite test observations shared across the actual forked HTTPS worker."""
import multiprocessing
from types import SimpleNamespace

from guardrails.evolution.classifier_contract import canonical, identity


class SharedCounts:
    """Finite synchronized observations shared with the actual forked HTTPS child."""
    names = ("evaluate", "evaluate_content_lists", "transform", "readiness", "dispatch")

    def __init__(self):
        self.values = multiprocessing.get_context("fork").Array("q", len(self.names), lock=True)

    def increment(self, name):
        assert name in self.names
        index = self.names.index(name)
        with self.values.get_lock():
            assert 0 <= self.values[index] < 2**63 - 1
            self.values[index] += 1

    def snapshot(self):
        with self.values.get_lock():
            return dict(zip(self.names, self.values[:], strict=True))


class ReadinessOnly:
    def __init__(self):
        self.counters = SharedCounts()

    async def request(self, route, **options):
        route.validate()
        if options["method"] != "GET" or options["path"] != "/v1/classifier/ready":
            self.counters.increment("dispatch")
            raise AssertionError("Disabled must not dispatch classifier content")
        assert options["body"] == b""
        self.counters.increment("readiness")
        return SimpleNamespace(status_code=200, content=canonical({"api_major": 1, "ready": True, "identity": identity()}))

