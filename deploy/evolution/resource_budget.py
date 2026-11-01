"""Converge owned CPU donors before increasing the PostgreSQL allocation."""

import copy
import json


PROBES = (
    ("gr-platform", "tls-probe"),
    ("gr-team-a", "allowed-probe"),
    ("gr-team-b", "allowed-probe"),
    ("gr-team-a", "denied-probe"),
)


class ResourceBudgetError(RuntimeError):
    """Finite deployment failure without Kubernetes response contents."""


def _json(deployment, *args, namespace, **kwargs):
    code, raw = deployment.kubectl(*args, namespace=namespace, timeout=15, **kwargs)
    if code:
        raise ResourceBudgetError("cpu_donor_command_failed")
    try:
        return json.loads(raw) if raw.strip() else None
    except TypeError, ValueError:
        raise ResourceBudgetError("cpu_donor_response_invalid") from None


def _spec(spec, *, probe, images, same_limits, final=False):
    """Accept only the known old/new allocation and the retained image pins."""
    containers = spec.get("containers", [])
    initial = spec.get("initContainers", [])
    if len(containers) != 1 or len(initial) != 1 or spec.get("ephemeralContainers"):
        raise ResourceBudgetError("cpu_donor_inventory_mismatch")
    app, init = containers[0], initial[0]
    name, old, new, memory = (
        ("probe", "100m", "50m", "48Mi") if probe else ("mock", "250m", "200m", "512Mi")
    )
    for value, expected_name in (
        (app, name),
        (init, "private-secrets" if probe else "private-files"),
    ):
        security = value.get("securityContext", {})
        if (
            value.get("name") != expected_name
            or value.get("image") not in images[expected_name]
            or value.get("imagePullPolicy") != "Never"
            or security.get("allowPrivilegeEscalation") is not False
            or security.get("readOnlyRootFilesystem") is not True
            or security.get("capabilities") != {"drop": ["ALL"]}
        ):
            raise ResourceBudgetError("cpu_donor_template_mismatch")
    expected_request = (
        {"cpu": "10m", "memory": "16Mi"}
        if probe
        else {"cpu": "100m", "memory": "128Mi"}
    )
    resource = app.get("resources", {})
    if (
        set(resource) != {"requests", "limits"}
        or not same_limits(resource["requests"], expected_request)
        or not any(
            same_limits(resource["limits"], {"cpu": cpu, "memory": memory})
            for cpu in ((new,) if final else (old, new))
        )
        or set(init.get("resources", {})) != {"requests", "limits"}
        or not same_limits(
            init["resources"]["requests"], {"cpu": "10m", "memory": "16Mi"}
        )
        or not same_limits(
            init["resources"]["limits"],
            {"cpu": "50m" if probe else "100m", "memory": "32Mi"},
        )
    ):
        raise ResourceBudgetError("cpu_donor_resource_mismatch")
    return new


def _target(value, owner, namespace, name, *, probe, images, same_limits, final=False):
    metadata, spec = value.get("metadata", {}), value.get("spec", {})
    selector = {"app": name} if probe else {"guardrails.evolution/instance": name}
    if (
        metadata.get("name") != name
        or metadata.get("namespace") != namespace
        or metadata.get("labels", {}).get("guardrails.evolution/owner") != owner
        or not metadata.get("uid")
        or not metadata.get("resourceVersion")
        or metadata.get("deletionTimestamp")
        or type(spec.get("replicas")) is not int
        or spec["replicas"] not in (0, 1)
        or spec.get("selector") != {"matchLabels": selector}
        or any(
            spec.get("template", {}).get("metadata", {}).get("labels", {}).get(k) != v
            for k, v in selector.items()
        )
    ):
        raise ResourceBudgetError("cpu_donor_owner_mismatch")
    strategy = spec.get("strategy", {})
    if (
        strategy.get("type")
        not in ({"Recreate", "RollingUpdate"} if probe and not final else {"Recreate"})
        or strategy.get("type") == "Recreate"
        and set(strategy) != {"type"}
    ):
        raise ResourceBudgetError("cpu_donor_strategy_mismatch")
    if strategy.get("type") == "RollingUpdate" and strategy not in (
        {"type": "RollingUpdate"},
        {
            "type": "RollingUpdate",
            "rollingUpdate": {"maxSurge": "25%", "maxUnavailable": "25%"},
        },
    ):
        raise ResourceBudgetError("cpu_donor_strategy_mismatch")
    return _spec(
        spec["template"]["spec"],
        probe=probe,
        images=images,
        same_limits=same_limits,
        final=final,
    )


def _converged(
    deployment, value, owner, namespace, name, *, probe, images, same_limits
):
    _target(
        value,
        owner,
        namespace,
        name,
        probe=probe,
        images=images,
        same_limits=same_limits,
        final=True,
    )
    selector = "app=" + name if probe else "guardrails.evolution/instance=" + name
    result = _json(
        deployment, "get", "pods", "-l", selector, "-o", "json", namespace=namespace
    )
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        raise ResourceBudgetError("cpu_donor_response_invalid")
    pods = result["items"]
    if value["spec"]["replicas"] == 0:
        if pods:
            raise ResourceBudgetError("cpu_donor_not_converged")
        return
    # Do not exclude a terminating old pod: it still owns its previous limit.
    if len(pods) != 1 or pods[0].get("metadata", {}).get("deletionTimestamp"):
        raise ResourceBudgetError("cpu_donor_not_converged")
    pod = pods[0]
    _spec(
        pod.get("spec", {}),
        probe=probe,
        images=images,
        same_limits=same_limits,
        final=True,
    )
    refs = [
        r
        for r in pod.get("metadata", {}).get("ownerReferences", [])
        if r.get("kind") == "ReplicaSet" and r.get("controller") is True
    ]
    statuses = pod.get("status", {}).get("containerStatuses", [])
    if (
        len(refs) != 1
        or pod.get("status", {}).get("phase") != "Running"
        or len(statuses) != 1
        or statuses[0].get("name") != ("probe" if probe else "mock")
        or statuses[0].get("ready") is not True
        or "running" not in statuses[0].get("state", {})
    ):
        raise ResourceBudgetError("cpu_donor_not_converged")
    replica = _json(
        deployment,
        "get",
        "replicaset",
        refs[0]["name"],
        "-o",
        "json",
        namespace=namespace,
    )
    if replica.get("metadata", {}).get("uid") != refs[0].get("uid") or not any(
        r.get("kind") == "Deployment"
        and r.get("controller") is True
        and r.get("uid") == value["metadata"]["uid"]
        for r in replica.get("metadata", {}).get("ownerReferences", [])
    ):
        raise ResourceBudgetError("cpu_donor_owner_mismatch")


def _check(
    deployment,
    owner,
    same_limits,
    *,
    mock_images=(),
    change=False,
    require_probes=False,
):
    """Absent donors stay absent; every present donor must converge before return."""
    python = deployment.image("python")
    targets = [(namespace, name, True) for namespace, name in PROBES]
    if mock_images:
        targets.append(("gr-model", "mock", False))
    prepared = []
    # Validate the whole known inventory before the first resource mutation.
    for namespace, name, probe in targets:
        value = _json(
            deployment,
            "get",
            "deployment",
            name,
            "--ignore-not-found",
            "-o",
            "json",
            namespace=namespace,
        )
        if value is None:
            if probe and require_probes:
                raise ResourceBudgetError("cpu_donor_missing")
            continue
        images = (
            {"probe": {python}, "private-secrets": {python}}
            if probe
            else {"mock": set(mock_images), "private-files": {python}}
        )
        new = _target(
            value,
            owner,
            namespace,
            name,
            probe=probe,
            images=images,
            same_limits=same_limits,
            final=not change,
        )
        desired = copy.deepcopy(value["spec"])
        desired["strategy"] = {"type": "Recreate"}
        limits = desired["template"]["spec"]["containers"][0]["resources"]["limits"]
        if not same_limits(
            limits, {"cpu": new, "memory": "48Mi" if probe else "512Mi"}
        ):
            limits["cpu"] = new
        prepared.append((namespace, name, probe, images, value, desired))
    for namespace, name, probe, images, value, desired in prepared:
        if change and value["spec"] != desired:
            patch = [
                {
                    "op": "test",
                    "path": "/metadata/uid",
                    "value": value["metadata"]["uid"],
                },
                {
                    "op": "test",
                    "path": "/metadata/resourceVersion",
                    "value": value["metadata"]["resourceVersion"],
                },
                {"op": "test", "path": "/spec", "value": value["spec"]},
                {"op": "replace", "path": "/spec", "value": desired},
            ]
            _json(
                deployment,
                "patch",
                "deployment",
                name,
                "--type=json",
                "--patch-file=/dev/stdin",
                "-o",
                "json",
                namespace=namespace,
                input_text=json.dumps(patch),
            )
        if change:
            code, _ = deployment.kubectl(
                "rollout",
                "status",
                "deployment/" + name,
                "--timeout=120s",
                namespace=namespace,
                timeout=125,
            )
            if code:
                raise ResourceBudgetError("cpu_donor_rollout_failed")
        observed = _json(
            deployment, "get", "deployment", name, "-o", "json", namespace=namespace
        )
        if (
            not isinstance(observed, dict)
            or observed.get("metadata", {}).get("uid") != value["metadata"]["uid"]
            or observed.get("spec") != desired
        ):
            raise ResourceBudgetError("cpu_donor_changed_during_rollout")
        _converged(
            deployment,
            observed,
            owner,
            namespace,
            name,
            probe=probe,
            images=images,
            same_limits=same_limits,
        )


def check(
    deployment,
    owner,
    same_limits,
    *,
    mock_images=(),
    change=False,
    require_probes=False,
):
    try:
        _check(
            deployment,
            owner,
            same_limits,
            mock_images=mock_images,
            change=change,
            require_probes=require_probes,
        )
    except KeyError, TypeError, AttributeError, IndexError:
        raise ResourceBudgetError("cpu_donor_response_invalid") from None
