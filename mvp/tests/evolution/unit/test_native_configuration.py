"""Rendered gateway configuration is accepted by the exact pinned proxy loader."""

import base64
import json
import os
import subprocess
from uuid import uuid4

from connector.evolution import GatewaySettings
from evolution.seed import private_write
from guardrails.evolution.models import (
    Destination,
    ProviderProfile,
    with_resource_digest,
)
from test_assembly import FilesOnlyDeployment, assembly, certificates

IMAGE = "ghcr.io/berriai/litellm@sha256:3d88bd757134a00e8c00237b85a021ab1f6d54abb9c1df750f411dbe7256a500"


class CapturedDeployment(FilesOnlyDeployment):
    """Capture Kubernetes I/O while rendering actual production Secret bytes."""

    def __init__(self, directory):
        super().__init__(directory)
        self.secrets = {}

    def image(self, name):
        assert name == "evolution-gateway"
        return IMAGE

    def kubectl(self, *args, namespace, input_text=None):
        if args[:2] == ("get", "secret"):
            previous = self.secrets.get((namespace, args[2]))
            return 0, json.dumps(previous) if previous else ""
        assert args == ("apply", "-f", "-")
        document = json.loads(input_text)
        assert document["kind"] == "Secret"
        assert document["metadata"]["namespace"] == namespace
        self.secrets[(namespace, document["metadata"]["name"])] = document
        return 0, ""


def test_both_gateway_profiles_emit_mandatory_config_recognized_by_pinned_loader(
    tmp_path,
):
    deployment = CapturedDeployment(tmp_path)
    directory, owner = assembly._state(deployment)
    certificates.prepare(deployment.state / "credentials")
    fixtures = tmp_path / "loader-inputs"
    fixtures.mkdir(mode=0o700)
    for tenant in ("team-a", "team-b"):
        workload_token = directory / (tenant + "-workload.token")
        gateway_token = directory / (tenant + "-gateway.token")
        private_write(workload_token, (uuid4().hex * 2).encode())
        private_write(gateway_token, (uuid4().hex * 2).encode())
        profiles = {}
        for name, model in (
            ("mock", "gpt-4o-evolution-mock"),
            ("ollama", "qwen2.5:0.5b"),
        ):
            destination = with_resource_digest(
                Destination(
                    destination_id=str(uuid4()),
                    tenant_id=tenant,
                    name=name,
                    host=name + ".gr-model.svc.cluster.local",
                    port=8443,
                    path_prefix="/v1" if name == "mock" else "/",
                    cidrs=("10.96.0.10/32",),
                    zone="local-shared",
                    purpose="inference",
                    tls_ca_ref="fixture-ca",
                )
            )
            provider = with_resource_digest(
                ProviderProfile(
                    provider_id=str(uuid4()),
                    tenant_id=tenant,
                    kind="model",
                    vendor="ollama",
                    model_id=model,
                    destination_id=destination.destination_id,
                    zone="local-shared",
                )
            )
            manifest = directory / (tenant + "-" + name + "-routes.json")
            private_write(
                manifest,
                json.dumps(
                    {
                        "tenant_id": tenant,
                        "workload_id": "chat",
                        "routes": [
                            {
                                "policy": {
                                    "tenant_id": tenant,
                                    "policy_id": "fixture",
                                    "version": 1,
                                    "digest": "a" * 64,
                                },
                                "provider": provider.model_dump(mode="json"),
                                "destination": destination.model_dump(mode="json"),
                            }
                        ],
                        "ca_files": {
                            "fixture-ca": str(deployment.state / "credentials/ca.crt")
                        },
                        "local_services": {
                            destination.destination_id: {
                                "hostname": destination.host,
                                "addresses": ["10.96.0.10"],
                            }
                        },
                    }
                ).encode(),
            )
            profiles[name] = {
                "provider": provider.model_dump(mode="json"),
                "destination": destination.model_dump(mode="json"),
                "route_manifest_file": str(manifest),
            }
        value = {
            "workload": {"workload_id": "chat"},
            "profiles": profiles,
            "roles": {"workload": {"token_file": str(workload_token)}},
            "gateway_token_file": str(gateway_token),
        }
        for profile in ("deterministic_mock", "local_model"):
            component = assembly._gateway(
                deployment, directory, owner, tenant, value, profile, uuid4().hex * 2
            )
            secret = deployment.secrets[
                ("gr-" + tenant, "gateway-" + tenant + "-config")
            ]
            files = {
                key: base64.b64decode(raw, validate=True)
                for key, raw in secret["data"].items()
            }
            settings = GatewaySettings.model_validate_json(files["settings.json"])
            assert (
                component["env"]["EVOLUTION_GATEWAY_CONFIG_FILE"]
                == "/config/settings.json"
            )
            assert settings.tenant_id == tenant and settings.profile == profile
            assert settings.litellm_config_file == "/config/litellm.yaml"
            expected_native_filename = (
                "litellm.yaml" in files and "litellm.json" not in files
            )
            assert expected_native_filename, (
                "mounted native configuration filename mismatch"
            )
            native = json.loads(files["litellm.yaml"])
            assert len(native["guardrails"]) == len(native["model_list"]) == 1
            guard = native["guardrails"][0]
            assert guard["guardrail_name"] == "evolution"
            assert guard["litellm_params"] == {
                "guardrail": "connector.evolution_guardrail.EvolutionGuardrail",
                "mode": ["pre_call", "post_call"],
                "default_on": True,
                "settings_file": component["env"]["EVOLUTION_GATEWAY_CONFIG_FILE"],
            }
            model = native["model_list"][0]
            assert model["model_name"] == settings.model_alias
            assert model["litellm_params"]["model"] == settings.model_route
            assert model["litellm_params"]["guardrails"] == ["evolution"]
            folder = fixtures / (tenant + "-" + profile)
            folder.mkdir(mode=0o700)
            # Neither credentials nor private TLS material enters this mount.
            (folder / "litellm.yaml").write_bytes(files["litellm.yaml"])
            (folder / "litellm.json").write_bytes(files["litellm.yaml"])

    program = """
import json
from pathlib import Path
from litellm.proxy.proxy_server import ProxyConfig
loader = ProxyConfig()
paths = sorted(Path('/fixtures').glob('*/litellm.yaml'))
recognized = rejected = mandatory = 0
for path in paths:
    recognized += loader.is_yaml(str(path)) is True
    rejected += loader.is_yaml(str(path.with_suffix('.json'))) is False
    value = loader._load_yaml_file(str(path))
    guard = value['guardrails'][0]['litellm_params']
    mandatory += guard['default_on'] is True and guard['mode'] == ['pre_call', 'post_call'] and guard['settings_file'] == '/config/settings.json'
print(json.dumps({'fixture': 'pinned-native-config-loader', 'files': len(paths), 'recognized': recognized, 'old_json_rejected': rejected, 'mandatory': mandatory}))
"""
    container_name = "evolution-config-loader-" + uuid4().hex
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--name",
                container_name,
                "--rm",
                "--pull=never",
                "--network=none",
                "--read-only",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--log-driver=none",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
                "--env",
                "HOME=/tmp",
                "--env",
                "LITELLM_LOCAL_MODEL_COST_MAP=True",
                "--volume",
                f"{fixtures}:/fixtures:ro",
                "--entrypoint",
                "python",
                IMAGE,
                "-c",
                program,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container_name],
            capture_output=True,
            timeout=15,
            check=False,
        )
    assert result.returncode == 0, "pinned native configuration loader failed"
    reports = [
        json.loads(line)
        for line in result.stdout.splitlines()
        if line.startswith('{"fixture": "pinned-native-config-loader"')
    ]
    assert reports == [
        {
            "fixture": "pinned-native-config-loader",
            "files": 4,
            "recognized": 4,
            "old_json_rejected": 4,
            "mandatory": 4,
        }
    ]
