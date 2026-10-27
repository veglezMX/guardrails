"""Finite measurement diagnostics using explicit clock and HTTP test ports."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import test_noisy_neighbor as fairness
from guardrails.evolution.errors import EvolutionError


def test_failure_samples_preserve_scheduled_order_and_strip_private_fields():
    attempts = [
        {"request_id": "private-id-canary", "correlation_id": "private-correlation",
         "text": "private-text-canary", "token": "private-token-canary",
         "replica": n % 2, "status": 200 if n < 3 else 504,
         "code": "decision" if n < 3 else "evaluation_timeout",
         "correct": n < 3, "operational_block": False,
         "latency_ms": 532.1234, "launch_lag_ms": n / 10}
        for n in range(16)
    ]
    result = fairness._failure_samples(attempts)
    assert [row["ordinal"] for row in result["rows"]] == list(range(3, 11))
    assert result["omitted"] == 5
    assert all(row["replica"] == row["ordinal"] % 2 for row in result["rows"])
    assert all(row["response_bin"] == "evaluation_timeout" for row in result["rows"])
    assert all(row["status"] == 504 and row["elapsed_ms"] == 532.123 for row in result["rows"])
    assert "private" not in json.dumps(result)
    assert fairness._failure_samples(attempts[:3]) == {"rows": [], "omitted": 0}


@pytest.mark.parametrize("direction", [1, -1], ids=["forward", "backward"])
def test_clock_observer_retains_transient_step_after_endpoint_recovers(
    monkeypatch, direction
):
    # Actual observer scheduling takes one sample during the interval. Its
    # clock port steps five seconds, then returns to the correct endpoint.
    samples = iter(
        [
            (0, 0),
            (direction * 5_000_000_000 + 250_000_000, 250_000_000),
            (500_000_000, 500_000_000),
        ]
    )
    monkeypatch.setattr(fairness, "_clock_sample", lambda: next(samples))
    records = {}

    async def observe():
        async with fairness._clock_interval(records, "baseline"):
            await asyncio.sleep(0.3)

    asyncio.run(observe())
    assert records["baseline"] == {
        "wall_elapsed_ms": 500.0,
        "monotonic_elapsed_ms": 500.0,
        "max_divergence_ms": 5000.0,
        "stable": False,
    }
    properties = []

    async def incomplete(_systems, _servers, clocks):
        clocks.update(records)
        raise EvolutionError("snapshot_stale")

    monkeypatch.setattr(fairness, "_experiment_body", incomplete)
    with pytest.raises(AssertionError, match="^environment_clock_discontinuity$"):
        asyncio.run(
            fairness._experiment(
                [], [], lambda name, value: properties.append((name, value))
            )
        )
    assert properties == [("evolution_environment_failure", "clock_discontinuity")]

    async def completed(_systems, _servers, clocks):
        clocks.update(records)
        return "completed diagnostic"

    properties.clear()
    monkeypatch.setattr(fairness, "_experiment_body", completed)
    assert (
        asyncio.run(
            fairness._experiment(
                [], [], lambda name, value: properties.append((name, value))
            )
        )
        == "completed diagnostic"
    )
    assert properties == [("evolution_environment_failure", "clock_discontinuity")]


def test_clock_sample_retries_wide_preempted_monotonic_bracket(monkeypatch):
    monotonic = iter([0, 200_000_000, 300_000_000, 300_000_002])
    wall = iter([100_000_000, 800_000_001])
    monkeypatch.setattr(
        fairness,
        "time",
        SimpleNamespace(
            monotonic_ns=lambda: next(monotonic), time_ns=lambda: next(wall)
        ),
    )
    assert fairness._clock_sample() == (800_000_001, 300_000_001)
    with pytest.raises(StopIteration):
        next(monotonic)


def test_exact_stale_snapshot_error_is_diagnosed_without_overload_acceptance():
    system = {
        "tenant": "diagnostic-tenant",
        "provider": SimpleNamespace(provider_id=str(uuid4())),
    }

    def response(request):
        correlation = request.headers["X-Request-ID"]
        return httpx.Response(
            503,
            json=EvolutionError("snapshot_stale").envelope(correlation),
            headers={"X-Request-ID": correlation},
        )

    async def run():
        async with httpx.AsyncClient(
            base_url="https://diagnostic.invalid",
            transport=httpx.MockTransport(response),
            trust_env=False,
        ) as client:
            now = asyncio.get_running_loop().time()
            return await fairness._call(
                client, system, target=now, end=now + 1, replica=0
            )

    result = asyncio.run(run())
    assert result["status"] == 503 and result["code"] == "snapshot_stale"
    assert result["safe_error"] is False
    assert result["correct"] is False and result["operational_block"] is False
    summary = fairness._summary([result])
    assert summary["response_counts"]["snapshot_stale"] == 1
    assert sum(summary["response_counts"].values()) == 1
    assert summary["operational_errors"] == 1
    assert summary["correct_terminal"] == 0
