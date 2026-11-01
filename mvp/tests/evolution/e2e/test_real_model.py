"""Twenty real locked Ollama inferences through both team-owned sample clients."""

from uuid import uuid4

from cluster_support import ROOT, Cluster, record_real_model_observation
from guardrails.evolution import models


def test_twenty_real_ollama_calls_preserve_exact_policy_audit_and_private_content():
    cluster, observations = None, []
    try:
        cluster = Cluster("local_model")
        lock = models.strict_json_loads(
            (ROOT / "deploy/evolution/preparation.lock.json").read_bytes()
        )
        manifest = next(row for row in lock["files"] if row["name"] == "qwen-manifest")
        expected_digest = manifest["sha256"]
        tags = cluster.http.get(cluster.access["ollama_url"] + "/api/tags")
        assert tags.status_code == 200
        available = models.strict_json_loads(tags.content)["models"]
        assert any(
            row["digest"].removeprefix("sha256:") == expected_digest
            for row in available
        )
        revisions = {
            tenant: cluster.release(tenant, "block") for tenant in cluster.tenants
        }
        before = cluster.calls()["count"]
        canaries, decisions, completed = (
            [],
            set(),
            {tenant: 0 for tenant in cluster.tenants},
        )
        for trial in range(10):
            for side, tenant in enumerate(cluster.tenants):
                marker = "modeltrial" + uuid4().hex
                prompt = (
                    "Reply with one short sentence describing a blue sky. Reference marker "
                    + marker
                )
                observation = {
                    "call_ordinal": trial * 2 + side,
                    "tenant_side": "A" if side == 0 else "B",
                    "http_status": 0, "wire_code": None,
                    "elapsed_ms": 0.0, "sample_completed": False,
                }
                observations.append(observation)
                result = cluster.sample_complete(tenant, prompt, observation=observation)
                cluster.assert_attribution(
                    tenant,
                    result,
                    revisions[tenant],
                    input_action="allow",
                    output_action="allow",
                )
                nonempty = isinstance(result["content"], str) and bool(
                    result["content"].strip()
                )
                assert nonempty, "real model must return inspected text"
                assert result["input_decision_id"] not in decisions
                assert result["output_decision_id"] not in decisions
                decisions.update(
                    (result["input_decision_id"], result["output_decision_id"])
                )
                canaries.extend((marker, prompt, result["content"]))
                completed[tenant] += 1
        assert sorted(completed.values()) == [10, 10]
        assert len(decisions) == 40
        assert cluster.calls()["count"] == before
        running = cluster.http.get(cluster.access["ollama_url"] + "/api/ps")
        assert running.status_code == 200
        loaded = models.strict_json_loads(running.content)["models"]
        assert any(
            row["digest"].removeprefix("sha256:") == expected_digest for row in loaded
        )
        for tenant in cluster.tenants:
            value = cluster.inventory["tenants"][tenant]
            assert value["profiles"]["ollama"]["provider"]["model_id"] == "qwen2.5:0.5b"
            assert (
                value["profiles"]["ollama"]["destination"]
                in value["policy"]["resolved_destinations"]
            )
        cluster.assert_private(canaries)
    finally:
        try:
            if cluster is not None:
                cluster.close()
        finally:
            record_real_model_observation(observations)
