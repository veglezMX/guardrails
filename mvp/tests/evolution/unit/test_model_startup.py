"""Pinned model metadata layout, replay guards, and isolated offline loading."""
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_assembly import ROOT, bootstrap, render_chart
from test_assembly_reconcile import (
    assembly,
    protected_bytes,
    setup,  # noqa: F401 - pytest fixture
)


@pytest.mark.parametrize("boundary", ["template", "pod"])
@pytest.mark.parametrize("change", ["missing", "writable", "alias", "shadow", "bound", "env", "init", "init-env", "init-envfrom", "root"])
def test_model_metadata_drift_reconciles_or_refuses_before_live_success(
    setup, boundary, change  # noqa: F811 - pytest fixture
):
    d, directory, values = setup
    values["components"]["ollama"]["enabled"] = True
    d.install(values)
    selected = d.rows["ollama"]["spec"]["template"] if boundary == "template" else d.pods["ollama"]
    spec = selected["spec"]
    model = next(c for c in spec["containers"] if c["name"] == "ollama")
    mount = next(m for m in model["volumeMounts"] if m["mountPath"] == "/models/manifests-v2")
    if change == "missing":
        model["volumeMounts"].remove(mount)
    elif change == "writable":
        mount["readOnly"] = False
    elif change == "alias":
        next(c for c in spec["containers"] if c["name"] == "tls")["volumeMounts"].append(
            {"name": "state", "mountPath": "/alias"})
    elif change == "shadow":
        mount["mountPath"] = "/models"
    elif change == "bound":
        next(v for v in spec["volumes"] if v["name"] == "state")["emptyDir"]["sizeLimit"] = "128Mi"
    elif change == "env":
        next(e for e in model["env"] if e["name"] == "OLLAMA_NOPRUNE")["value"] = "false"
    elif change == "init":
        next(c for c in spec["initContainers"] if c["name"] == "private-files")["args"] = ["pass"]
    elif change in {"init-env", "init-envfrom"}:
        initial = next(c for c in spec["initContainers"] if c["name"] == "private-files")
        if change == "init-env":
            initial["env"] = [{"name": "PYTHONOPTIMIZE", "value": "2"}]
        else:
            initial["envFrom"] = [{"configMapRef": {"name": "foreign"}}]
    else:
        model["securityContext"]["readOnlyRootFilesystem"] = False
    before = protected_bytes(directory)
    d.events.clear()
    if boundary == "template":
        assembly._reconcile_application_images(d, directory, values, d.owner())
        assert d.events.count("helm") == 1
        assert d.events.index("stop_forwards") < d.events.index("helm")
        assert "verify_model_manifests" in d.events
    else:
        with pytest.raises(assembly.AssemblyError, match="^model_manifest_configuration_mismatch$"):
            assembly._reconcile_application_images(d, directory, values, d.owner())
        assert "helm" not in d.events
        assert "verify_model_manifests" not in d.events
        assert not (directory / "running-images.json").exists()
    assert protected_bytes(directory) == before


def test_model_rendered_private_sibling_has_no_writable_alias(tmp_path):
    image = "registry.invalid/model@sha256:" + "a" * 64
    python = "registry.invalid/python@sha256:" + "b" * 64
    d = SimpleNamespace(root=ROOT, image=lambda name: python)
    row = assembly._component("gr-model", "ollama", image, "revision", command=["ollama", "serve"],
                              env=dict(assembly.MODEL_ENV), sidecar={"image": image})
    docs = render_chart(tmp_path, "platform", {"ownerId": "model-test", "initImage": python,
                        "servicesOnly": False, "components": {"ollama": row}})
    template = next(v for v in docs if v["kind"] == "Deployment")["spec"]["template"]
    assert assembly._model_settings_match(d, row, template)
    assert next(v for v in template["spec"]["volumes"] if v["name"] == "state") == {
        "name": "state", "emptyDir": {"sizeLimit": "64Mi"}}
    initial = next(c for c in template["spec"]["initContainers"] if c["name"] == "private-files")
    assert initial["resources"]["limits"] == {"cpu": "100m", "memory": "32Mi"}
    for container in template["spec"]["containers"]:
        for mount in container["volumeMounts"]:
            if mount["name"] == "state":
                assert mount["subPath"] == "data" or (
                    container["name"] == "ollama" and mount["subPath"] == "model-manifests-v2"
                    and mount["mountPath"] == "/models/manifests-v2" and mount["readOnly"] is True)


def test_model_live_nonzero_metadata_check_cannot_publish_success(setup, monkeypatch):  # noqa: F811
    d, directory, values = setup
    values["components"]["ollama"]["enabled"] = True
    d.install(values)
    original = d.kubectl

    def rejected(*args, **kwargs):
        if args[:4] == ("exec", "ollama", "-c", "ollama"):
            return 17, ""
        return original(*args, **kwargs)

    monkeypatch.setattr(d, "kubectl", rejected)
    with pytest.raises(assembly.AssemblyError, match="^model_manifest_runtime_mismatch$"):
        assembly._reconcile_application_images(d, directory, values, d.owner())
    assert not (directory / "running-images.json").exists()


@pytest.mark.parametrize("failed", [False, True])
def test_same_profile_model_replay_requires_load_before_ready_and_preserves_state(
    setup, monkeypatch, failed  # noqa: F811
):
    d, directory, values = setup
    values["components"]["ollama"]["enabled"] = True
    d.install(values)
    assembly._write(directory / "values.json", values)
    access = d.read_json(directory / "access.json")
    access.update(profile="local_model", ollama_url="https://model.invalid")
    assembly._write(directory / "access.json", access)
    before = protected_bytes(directory)
    access_before = (directory / "access.json").read_bytes()
    monkeypatch.setattr(assembly, "_port_record", lambda *_: {"ollama": 45001})
    monkeypatch.setattr(assembly, "_forward", lambda *_: d.events.append("forward"))

    def warm(*_args):
        d.events.append("warm_model")
        assert "verify_model_manifests" in d.events
        assert d.events[-2] == "forward"
        if failed:
            raise assembly.AssemblyError("prepared_model_load_failed")

    monkeypatch.setattr(assembly, "_warm_model", warm)
    d.events.clear()
    if failed:
        with pytest.raises(assembly.AssemblyError, match="^prepared_model_load_failed$"):
            assembly.platform(d, profile="local_model")
        assert "https_ready" not in d.events
    else:
        assert assembly.platform(d, profile="local_model")["status"] == "ready"
        assert d.events.index("warm_model") < d.events.index("https_ready")
    assert "helm" not in d.events
    assert d.events.count("warm_model") == 1
    assert protected_bytes(directory) == before
    assert (directory / "access.json").read_bytes() == access_before


@pytest.mark.parametrize("fault", ["nonempty", "mode", "setgid-mode", "symlink"])
def test_model_private_initializer_refuses_existing_untrusted_metadata(tmp_path, fault):
    # Execute the exact initializer using isolated synthetic mount roots.
    paths = {name: tmp_path / name for name in ("private", "state", "projected")}
    for p in paths.values():
        p.mkdir()
    target = paths["state"] / "model-manifests-v2"
    if fault == "symlink":
        target.symlink_to(paths["projected"], target_is_directory=True)
    else:
        target.mkdir(mode=0o700 if fault == "nonempty" else 0o755)
        if fault == "setgid-mode":
            target.chmod(0o2700)
        if fault == "nonempty":
            (target / "unexpected").write_bytes(b"synthetic-existing-metadata")
    before = (target / "unexpected").read_bytes() if fault == "nonempty" else None
    script = assembly._model_private_files_script(SimpleNamespace(root=ROOT))
    # Replace the fixed mount roots at the pathlib boundary, not the behavior.
    import pathlib
    original = pathlib.Path

    def mounted(value, *parts):
        p = original(value, *parts)
        if p.is_absolute() and len(p.parts) > 1 and p.parts[1] in paths:
            return paths[p.parts[1]].joinpath(*p.parts[2:])
        return p

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pathlib, "Path", mounted)
        with pytest.raises(RuntimeError, match="^model_manifest_directory_(invalid|not_empty)$"):
            exec(compile(script, "model-private-files.py", "exec", optimize=2), {})  # noqa: S102 - exact trusted chart initializer
    if before is not None:
        assert (target / "unexpected").read_bytes() == before
    if fault == "symlink":
        assert target.is_symlink()
    if fault == "setgid-mode":
        assert target.stat().st_mode & 0o7777 == 0o2700


def test_model_initializer_normalizes_only_fresh_directory_under_setgid_parent(tmp_path):
    import pathlib
    original = pathlib.Path
    roots = {name: tmp_path / name for name in ("private", "state", "projected")}
    for p in roots.values():
        p.mkdir()
    roots["state"].chmod(0o2770)
    control = roots["state"] / "setgid-control"
    control.mkdir(mode=0o700)
    assert control.stat().st_mode & 0o7777 == 0o2700

    def mounted(value, *parts):
        p = original(value, *parts)
        if p.is_absolute() and len(p.parts) > 1 and p.parts[1] in roots:
            return roots[p.parts[1]].joinpath(*p.parts[2:])
        return p

    script = assembly._model_private_files_script(SimpleNamespace(root=ROOT))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pathlib, "Path", mounted)
        exec(compile(script, "model-private-files.py", "exec", optimize=2), {})  # noqa: S102 - exact trusted chart initializer
    target = roots["state"] / "model-manifests-v2"
    assert target.stat().st_mode & 0o7777 == 0o700
    assert target.stat().st_uid == os.getuid()
    assert not list(target.iterdir())
    assert control.stat().st_mode & 0o7777 == 0o2700


@pytest.mark.parametrize("fault", ["wrong-digest", "extra-model", "missing-model"])
def test_model_warmup_requires_exact_pinned_tags_before_loading(monkeypatch, fault):
    expected = "a" * 64
    models = [{"name": "qwen2.5:0.5b", "digest": expected}]
    if fault == "wrong-digest":
        models[0]["digest"] = "b" * 64
    elif fault == "extra-model":
        models.append({"name": "other:latest", "digest": "b" * 64})
    else:
        models = []
    calls = []

    class Client:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def get(self, url):
            calls.append(url)
            return SimpleNamespace(status_code=200, json=lambda: {"models": models})
        def post(self, *_args, **_kwargs): pytest.fail("untrusted model must never be loaded")

    monkeypatch.setattr(assembly, "_https_ready", lambda *_: None)
    monkeypatch.setattr(assembly.ssl, "create_default_context", lambda **_: None)
    monkeypatch.setattr(assembly.httpx, "Client", Client)
    d = SimpleNamespace(specs=lambda: {"files": [{"name": "qwen-manifest", "sha256": expected}]})
    with pytest.raises(assembly.AssemblyError, match="^prepared_model_manifest_mismatch$"):
        assembly._warm_model(d, "https://model.invalid", "synthetic-ca")
    assert calls == ["https://model.invalid/api/tags"]


def test_actual_pinned_model_reads_legacy_manifest_and_loads_with_read_only_v2():
    state = Path(os.environ["EVOLUTION_DEPLOYMENT_STATE"])
    prepared = bootstrap.read_json(state / "prepared.json")
    refs = {a["name"]: a["source_url"].removeprefix("docker://")
            for a in prepared["artifacts"]}
    lock = json.loads((ROOT / "deploy/evolution/preparation.lock.json").read_text())
    expected = next(a["sha256"] for a in lock["files"] if a["name"] == "qwen-manifest")
    name = "evolution-model-test-" + uuid4().hex[:12]
    common = ["--pull=never", "--read-only", "--user", "10001:10001",
              "--cap-drop=ALL", "--security-opt=no-new-privileges"]
    initialized = False

    def run(argv, *, timeout=30):
        process = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
        code = process.returncode
        assert code == 0
        return process.stdout

    client = r'''
import json,time,urllib.request
expected=EXPECTED
start=time.monotonic()
while True:
 try:
  with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=5) as r: tags=json.load(r)
  break
 except Exception:
  if time.monotonic()-start>30: raise
  time.sleep(.2)
assert [(x['name'],x['digest'].removeprefix('sha256:')) for x in tags['models']]==[('qwen2.5:0.5b',expected)]
request=urllib.request.Request('http://127.0.0.1:11434/api/generate',data=json.dumps({'model':'qwen2.5:0.5b','prompt':'','stream':False,'keep_alive':'30m'}).encode(),headers={'Content-Type':'application/json'},method='POST')
with urllib.request.urlopen(request,timeout=90) as r: loaded=json.load(r)
assert loaded['done'] is True
with urllib.request.urlopen('http://127.0.0.1:11434/api/ps',timeout=5) as r: running=json.load(r)
assert any(x['digest'].removeprefix('sha256:')==expected for x in running['models'])
print(json.dumps({'tags_pinned':True,'empty_prompt_loaded':True,'running_digest_pinned':True}))
'''.replace("EXPECTED", repr(expected))
    checksums = "sha256sum /models/manifests/registry.ollama.ai/library/qwen2.5/0.5b /models/blobs/*"
    try:
        run(["docker", "volume", "create", "--driver", "local", "--opt", "type=tmpfs", "--opt", "device=tmpfs",
             "--opt", "o=size=67108864,uid=10001,gid=10001,mode=2770", name])
        initialized = True
        run(["docker", "run", "-d", "--name", name + "-keep", *common, "--network=none", "--memory=32m", "--cpus=0.1",
             "--mount", "type=volume,src=" + name + ",dst=/state", "--entrypoint", "python", refs["python"],
             "-S", "-c", "import time; time.sleep(1000)"])
        run(["docker", "run", "--rm", "--name", name + "-init", *common, "--network=none", "--memory=32m", "--cpus=0.1",
             "--mount", "type=volume,src=" + name + ",dst=/state",
             "--tmpfs", "/private:uid=10001,gid=10001,mode=0700,size=8388608",
             "--tmpfs", "/projected:uid=10001,gid=10001,mode=0700,size=8388608",
             "--entrypoint", "python", refs["python"], "-B", "-c",
             assembly._model_private_files_script(SimpleNamespace(root=ROOT))])
        model_args = [*common, "--network=none", "--memory=1536m", "--cpus=0.95",
                      "--mount", "type=volume,src=" + name + ",dst=/state,volume-subpath=data",
                      "--mount", "type=volume,src=" + name + ",dst=/models/manifests-v2,volume-subpath=model-manifests-v2,readonly",
                      "--tmpfs", "/tmp:uid=10001,gid=10001,mode=0700,size=67108864"]
        for key, value in sorted(assembly.MODEL_ENV.items()):
            model_args.extend(["--env", key + "=" + value])
        before = run(["docker", "run", "--rm", "--name", name + "-hash", *model_args,
                      "--entrypoint", "/bin/sh", refs["evolution-model"], "-ec", checksums])
        assert len(before.splitlines()) >= 5
        assert before.splitlines()[0].split()[0].decode() == expected
        for _restart in range(2):
            run(["docker", "run", "-d", "--name", name + "-model", *model_args,
                 "--entrypoint", "/bin/ollama", refs["evolution-model"], "serve"])
            observed = json.loads(run(["docker", "inspect", name + "-model"]))[0]
            assert observed["HostConfig"]["NetworkMode"] == "none"
            assert observed["HostConfig"]["ReadonlyRootfs"] is True
            assert observed["HostConfig"]["Memory"] == 1536 * 1024**2
            assert observed["HostConfig"]["NanoCpus"] == 950_000_000
            assert observed["Config"]["User"] == "10001:10001"
            assert observed["HostConfig"]["CapDrop"] == ["ALL"]
            started = time.monotonic()
            result = run(["docker", "run", "--rm", "--name", name + "-client", *common,
                          "--network", "container:" + name + "-model", "--memory=64m", "--cpus=0.1",
                          "--entrypoint", "python", refs["python"], "-B", "-c", client], timeout=135)
            assert json.loads(result) == {"tags_pinned": True, "empty_prompt_loaded": True, "running_digest_pinned": True}
            assert time.monotonic() - started < 135
            run(["docker", "exec", name + "-model", "timeout", "10", "/bin/sh", "-ec", assembly._model_verify_command()], timeout=15)
            after = run(["docker", "exec", name + "-model", "/bin/sh", "-ec", checksums])
            assert after == before
            # Loading may attempt best-effort legacy compatibility migration. The
            # immutable blob tree prevents publication; no v2 data is silently erased.
            assert next(m for m in observed["Mounts"] if m["Destination"] == "/models/manifests-v2")["RW"] is False
            run(["docker", "stop", "--time", "5", name + "-model"], timeout=10)
            run(["docker", "rm", name + "-model"])
    finally:
        for suffix in ("client", "model", "hash", "init", "keep"):
            subprocess.run(["docker", "rm", "-f", name + "-" + suffix],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        if initialized:
            run(["docker", "volume", "rm", name])
        remaining = run(["docker", "ps", "-a", "--filter", "name=^/" + name + "-", "--format", "{{.Names}}"])
        assert remaining == b""
