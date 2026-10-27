"""Live cluster boundary tests. Missing deployment evidence fails, never skips."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import threading
from pathlib import Path

import httpx
from evolution.seed import private_read

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "deployment_network_bootstrap", ROOT / "deploy/evolution/bootstrap.py"
)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def deployment():
    state = os.environ.get("EVOLUTION_DEPLOYMENT_STATE")
    assert state, "The required real cluster state must be explicitly selected"
    result = bootstrap.LocalDeployment(ROOT, state=state)
    result.owner()
    result.verify_node_boundary()
    return result


def test_actual_cluster_dns_tls_default_deny_and_zero_forbidden_dispatch():
    d = deployment()
    result = d.probes()
    checks = result["observations"]
    assert len(checks) == 10
    assert checks[0]["observation"]["result"] == "resolved"
    assert checks[0]["observation"]["addresses"]
    for index in (1, 2, 9):
        assert checks[index]["observation"]["result"] == "tls_verified"
        assert checks[index]["observation"]["protocol"] in {"TLSv1.2", "TLSv1.3"}
    assert [checks[index]["observation"]["verify_code"] for index in (3, 4, 5)] == [
        20,
        62,
        10,
    ]
    for index in (6, 7, 8):
        assert checks[index]["observation"]["result"] == "denied_timeout"
        assert checks[index]["observation"]["attempts"] >= 1
    assert (
        result["dispatch_counts"]["before_denials"]
        == result["dispatch_counts"]["after_denials"]
    )
    assert result["dispatch_counts"]["before_denials"]["8443"] >= 2
    assert result["privacy"]["canary_attempts"] == 3
    assert result["privacy"]["raw_canary_in_logs"] is False
    _, raw = d.kubectl(
        "get", "pods", "-l", "app=allowed-probe", "-o", "json", namespace="gr-team-a"
    )
    pods = json.loads(raw)["items"]
    assert len(pods) == 1
    spec = pods[0]["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert spec["securityContext"]["runAsUser"] == 10001
    for resource in ("networkpolicies", "namespaces"):
        code, output = d.run(
            [
                d.bin / "kubectl",
                "--kubeconfig",
                d.kubeconfig,
                "--context",
                bootstrap.PROFILE,
                *(
                    ["--namespace", "gr-team-a"]
                    if resource == "networkpolicies"
                    else []
                ),
                "auth",
                "can-i",
                "patch",
                resource,
                *(["--all-namespaces"] if resource == "namespaces" else []),
                "--as=system:serviceaccount:gr-team-a:default",
            ],
            check=False,
        )
        assert code == 1
        assert output.strip() == "no"


def test_cold_prepared_node_denies_registry_metadata_host_proxy_and_external_dns():
    d = deployment()
    observed = d.verify_node_boundary()
    boot = d.read_json(d.state / "offline-boot.json")
    assert boot["cold_start"] is True
    assert boot["node_ready"] is True
    assert boot["container_id"] == observed["container_id"]
    assert boot["image"] == observed["image"]
    assert boot["rules_sha256"] == observed["rules_sha256"]
    assert boot["prepared_digest"] == d.sha256(d.state / "prepared.json")
    assert observed["cpu_limit"] == 6
    assert observed["cpu_quota_us"] == 6 * observed["cpu_period_us"]
    assert observed["memory_limit_bytes"] == 8 * 1024**3
    _, raw = d.kubectl(
        "get", "configmap", "coredns", "-o", "json", namespace="kube-system"
    )
    corefile = json.loads(raw)["data"]["Corefile"]
    assert "forward" not in corefile
    assert "proxy" not in corefile
    # A live host-side sink supplies a positive control. Only its count is retained.
    listener = socket.socket()
    listener.bind(("192.168.51.1", 0))
    listener.listen()
    listener.settimeout(0.1)
    port = listener.getsockname()[1]
    accepted = []
    stop = threading.Event()
    positive = threading.Event()

    def receive():
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            with connection:
                accepted.append(1)
                positive.set()

    worker = threading.Thread(target=receive)
    worker.start()
    try:
        with socket.create_connection(("192.168.51.1", port), timeout=2):
            pass
        assert positive.wait(2)
        assert len(accepted) == 1
        for host, target in (
            ("192.168.51.1", port),
            ("1.1.1.1", 443),
            ("169.254.169.254", 80),
            ("127.0.0.11", 53),
        ):
            code, _ = d.run(
                [
                    "docker",
                    "exec",
                    bootstrap.PROFILE,
                    "timeout",
                    "--kill-after=1s",
                    "3s",
                    "bash",
                    "-c",
                    "exec 3<>/dev/tcp/" + host + "/" + str(target),
                ],
                timeout=6,
                check=False,
            )
            assert code == 124
        assert len(accepted) == 1
        # This digest is deliberately absent; no prepared local alias can satisfy it.
        code, _ = d.run(
            [
                "docker",
                "exec",
                bootstrap.PROFILE,
                "timeout",
                "--kill-after=1s",
                "4s",
                "ctr",
                "-n=k8s.io",
                "images",
                "pull",
                "docker.io/library/busybox@sha256:" + "0" * 64,
            ],
            timeout=7,
            check=False,
        )
        assert code != 0
        assert len(accepted) == 1
        _, output = d.kubectl(
            "exec",
            "deployment/allowed-probe",
            "-c",
            "probe",
            "--",
            "python",
            "-c",
            "import socket\ntry:\n socket.getaddrinfo('registry-1.docker.io',443)\nexcept socket.gaierror:\n print('external_dns_denied')\nelse:\n raise SystemExit(1)",
            namespace="gr-team-a",
            timeout=10,
        )
        assert output.strip() == "external_dns_denied"
    finally:
        stop.set()
        worker.join(timeout=2)
        listener.close()
    assert not worker.is_alive()


def test_model_cni_blocks_direct_native_alternate_and_pod_ip_without_dispatch():
    d = deployment()
    d.platform(profile="local_model")
    access = json.loads(private_read(Path(d.access()["access_file"])))
    token = Path(access["mock_control_token_file"]).read_text().strip()
    with httpx.Client(verify=access["ca_file"], trust_env=False, timeout=5) as client:

        def count():
            response = client.get(
                access["mock_url"] + "/proof/calls",
                headers={"Authorization": "Bearer " + token},
            )
            assert response.status_code == 200
            # Captured synthetic bodies remain in memory and are never serialized.
            return response.json()["count"]

        reset = client.put(
            access["mock_url"] + "/proof/scenario",
            headers={"Authorization": "Bearer " + token},
            json={"scenario": "clean"},
        )
        assert reset.status_code == 200
        initial = count()
        inference_token = Path(access["mock_token_file"]).read_text().strip()
        positive = client.post(
            access["mock_url"] + "/v1/chat/completions",
            headers={"Authorization": "Bearer " + inference_token},
            json={
                "model": "gpt-4o-evolution-mock",
                "messages": [
                    {"role": "user", "content": "network probe positive control"}
                ],
            },
        )
        assert positive.status_code == 200
        before = count()
        assert before == initial + 1
        _, raw = d.kubectl(
            "get",
            "pods",
            "-l",
            "guardrails.evolution/instance=mock",
            "-o",
            "json",
            namespace="gr-model",
        )
        pods = json.loads(raw)["items"]
        assert len(pods) == 1
        pod_ip = pods[0]["status"]["podIP"]
        for host, port in (
            ("mock.gr-model.svc.cluster.local", 8443),
            (pod_ip, 8443),
            ("mock.gr-model.svc.cluster.local", 11434),
            ("mock.gr-model.svc.cluster.local", 9443),
            ("ollama.gr-model.svc.cluster.local", 11434),
        ):
            _, raw = d.kubectl(
                "exec",
                "deployment/denied-probe",
                "-c",
                "probe",
                "--",
                "python",
                "-B",
                "/code/probe.py",
                "deny",
                "--host",
                host,
                "--port",
                str(port),
                namespace="gr-team-a",
                timeout=10,
            )
            assert json.loads(raw)["result"] == "denied_timeout"
        assert count() == before
        _, sockets = d.kubectl(
            "exec",
            "deployment/ollama",
            "-c",
            "ollama",
            "--",
            "cat",
            "/proc/net/tcp",
            namespace="gr-model",
        )
        listeners = [
            line.split()[1]
            for line in sockets.splitlines()[1:]
            if line.split()[3] == "0A"
        ]
        assert "0100007F:2CAA" in listeners
        assert "00000000:2CAA" not in listeners
        _, raw = d.kubectl(
            "get", "service", "ollama", "-o", "json", namespace="gr-model"
        )
        assert [x["port"] for x in json.loads(raw)["spec"]["ports"]] == [8443]
