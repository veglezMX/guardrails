"""Real tiny sealed closures; fake only the native Docker process boundary."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.classifier import build_context as context
from services.classifier.tests import test_image_context as recipe_tests

closure = recipe_tests.closure
put = recipe_tests.put

ROOT = Path(__file__).resolve().parents[3]


def module(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "deploy/evolution" / (name + ".py")
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


build = module("build_images")
bootstrap = module("bootstrap")


class Deployment:
    def __init__(self, plan):
        self.root = Path(plan["root"])
        self.state = self.root.parent / "private-state"
        (self.state / "builds").mkdir(mode=0o700, parents=True)
        self.env = {}
        self.calls = []
        self.images = {}
        self.base = {
            "Os": "linux",
            "Architecture": "amd64",
            "Size": 123,
            "Id": "sha256:" + "a" * 64,
            "RepoDigests": [context.BASE],
        }
        self.result = {
            "Os": "linux",
            "Architecture": "amd64",
            "Size": 456,
            "Id": "sha256:" + "b" * 64,
            "RepoDigests": [
                "docker.io/guardrails-evolution/classifier@sha256:" + "c" * 64
            ],
            "Descriptor": {"digest": "sha256:" + "c" * 64},
        }
        self.hook = None
        self.missing_base = False
        self.write_json(
            self.root / "deploy/evolution/preparation.lock.json", {"images": []}
        )
        self.write_json(
            self.root / "deploy/evolution/artifacts.lock.json", {"artifacts": []}
        )

    @staticmethod
    def sha256(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def read_json(path):
        return json.loads(Path(path).read_bytes())

    @staticmethod
    def write_json(path, value):
        put(path, context.canonical(value))

    def specs(self):
        return self.read_json(self.root / "deploy/evolution/preparation.lock.json")

    def run(self, argv, **kwargs):
        self.calls.append((list(map(str, argv)), kwargs))
        if list(argv[:3]) == list(build.NATIVE_DOCKER):
            if argv[3:5] == ["image", "inspect"]:
                if self.missing_base and argv[-1] == context.BASE:
                    return 1, ""
                return 0, json.dumps(
                    [self.base if argv[-1] == context.BASE else self.result]
                )
            assert argv[3] == "build"
            stream = kwargs["input_stream"]
            assert stream.tell() == 0 and stream.read(512)
            if self.hook:
                self.hook()
            return 0, ""
        assert argv[0] == "docker"
        if argv[1] == "build":
            tag = argv[argv.index("--tag") + 1]
            self.images[tag] = {
                "Descriptor": {"digest": "sha256:" + "d" * 64},
                "Size": 99,
            }
            return 0, ""
        assert argv[1:3] == ["image", "inspect"]
        return 0, json.dumps([self.images[argv[-1]]])

    def inspect_image(self, artifact):
        return None


@pytest.fixture
def bundle(closure, monkeypatch):
    # Reviewed real builder + tiny model/wheel fixtures; no production artifact import.
    context.stage(closure, context.digest(context.canonical(closure)))
    monkeypatch.setattr(build, "classifier_builder", lambda d: context)
    d = Deployment(closure)
    directory = Path(closure["output"])
    return d, directory, d.sha256(directory / "seal.json")


def prepare(bundle):
    d, directory, pin = bundle
    return build.prepare_classifier(d, directory, pin)


def pin_locks(d, artifact):
    for name, key in [
        ("preparation.lock.json", "images"),
        ("artifacts.lock.json", "artifacts"),
    ]:
        d.write_json(d.root / "deploy/evolution" / name, {key: [artifact]})


def test_real_seal_drives_exact_offline_build_and_verification(bundle):
    d, directory, pin = bundle
    artifact = prepare(bundle)
    record = d.read_json(d.state / "builds/classifier.json")
    assert set(record) == build.CLASSIFIER_RECORD_KEYS
    assert len(record["inputs"]) == 46
    assert len([r for r in record["inputs"] if r["path"].startswith("model/")]) == 9
    assert len([r for r in record["inputs"] if r["path"].startswith("wheels/")]) == 29
    calls = [r for r in d.calls if r[0][3] == "build"]
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv == [
        *build.NATIVE_DOCKER,
        "build",
        "--builder",
        "default",
        "--pull=false",
        "--network=none",
        "--provenance=false",
        "--tag",
        "guardrails-evolution/classifier:" + record["source_digest"][:16],
        "-",
    ]
    assert options["timeout"] == 900
    assert options["input_stream"].closed
    assert (
        record["context_directory"] == str(directory) and record["seal_sha256"] == pin
    )
    pin_locks(d, artifact)
    build.verify_classifier(d, record, artifact)


@pytest.mark.parametrize(
    "what",
    ["seal", "manifest", "plan", "tar", "extra", "source", "wheel", "model", "python"],
)
def test_tampered_closure_refuses_before_any_process(bundle, what):
    d, directory, _pin = bundle
    plan = d.read_json(directory / "plan.json")
    if what == "extra":
        put(directory / "unsealed", b"no")
    elif what == "source":
        put(d.root / "services/classifier/app.py", b"changed")
    elif what in {"wheel", "model"}:
        put(next(Path(plan[what + "_directory"]).iterdir()), b"changed")
    elif what == "python":
        put(Path(plan["python_archive"]), b"changed")
    else:
        target = directory / ("context.tar" if what == "tar" else what + ".json")
        target.write_bytes(target.read_bytes() + b"changed")
    with pytest.raises((build.BuildError, context.ContextError, ValueError)):
        prepare(bundle)
    assert d.calls == [] and not (d.state / "builds/classifier.json").exists()


@pytest.mark.parametrize("kind", ["source", "tar", "model", "metadata"])
def test_drift_during_build_cannot_publish_record(bundle, kind):
    d, directory, _pin = bundle
    plan = d.read_json(directory / "plan.json")
    targets = {
        "source": d.root / "services/classifier/app.py",
        "tar": directory / "context.tar",
        "model": next(Path(plan["model_directory"]).iterdir()),
        "metadata": directory / "manifest.json",
    }
    d.hook = lambda: targets[kind].write_bytes(targets[kind].read_bytes() + b"x")
    with pytest.raises((build.BuildError, context.ContextError)):
        prepare(bundle)
    assert not (d.state / "builds/classifier.json").exists()
    assert sum(r[0][3] == "build" for r in d.calls) == 1


@pytest.mark.parametrize("kind", ["add", "delete", "change", "symlink"])
def test_source_inventory_detects_full_classifier_tree_change(bundle, kind):
    d, _directory, _pin = bundle
    doc = d.root / "services/classifier/public-note.txt"
    put(doc, b"public")
    artifact = prepare(bundle)
    pin_locks(d, artifact)
    record = d.read_json(d.state / "builds/classifier.json")
    if kind == "add":
        put(doc.with_name("new.py"), b"new")
    elif kind == "delete":
        doc.unlink()
    elif kind == "change":
        put(doc, b"changed")
    else:
        doc.unlink()
        doc.symlink_to(d.root / "services/classifier/app.py")
    with pytest.raises((build.BuildError, context.ContextError)):
        build.verify_classifier(d, record, artifact)


@pytest.mark.parametrize("name", ["preparation.lock.json", "artifacts.lock.json"])
@pytest.mark.parametrize("kind", ["missing", "duplicate", "different"])
def test_exact_both_lock_correspondence(bundle, name, kind):
    d, _, _ = bundle
    artifact = prepare(bundle)
    pin_locks(d, artifact)
    key = "images" if name.startswith("preparation") else "artifacts"
    rows = (
        []
        if kind == "missing"
        else [artifact, artifact]
        if kind == "duplicate"
        else [{**artifact, "sha256": "0" * 64}]
    )
    d.write_json(d.root / "deploy/evolution" / name, {key: rows})
    with pytest.raises(build.BuildError, match="lock_mismatch"):
        build.verify_classifier(
            d, d.read_json(d.state / "builds/classifier.json"), artifact
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("DOCKER_HOST", "tcp://foreign:1234"),
        ("DOCKER_CONTEXT", "other"),
        ("DOCKER_TLS_VERIFY", "1"),
        ("DOCKER_CERT_PATH", "/elsewhere"),
        ("BUILDX_BUILDER", "remote"),
        ("BUILDKIT_HOST", "tcp://foreign"),
        ("BUILDX_CONFIG", "/foreign"),
        ("DOCKER_CONFIG", "/foreign"),
    ],
)
def test_ambient_docker_routing_refused_before_process(bundle, field, value):
    d, _, _ = bundle
    d.env[field] = value
    with pytest.raises(build.BuildError, match="routing_invalid"):
        prepare(bundle)
    assert d.calls == []


def test_cached_base_required_no_pull_or_build(bundle):
    d, _, _ = bundle
    d.missing_base = True
    with pytest.raises(build.BuildError, match="image_missing"):
        prepare(bundle)
    assert len(d.calls) == 1 and "build" not in d.calls[0][0]


@pytest.mark.parametrize("change", ["pin", "architecture", "manifest", "id", "size"])
def test_wrong_image_identity_never_records(bundle, change):
    d, _, _ = bundle
    if change == "pin":
        d.base["RepoDigests"] = []
    elif change == "architecture":
        d.base["Architecture"] = "arm64"
    elif change == "manifest":
        d.result["Descriptor"]["digest"] = "sha256:bad"
    elif change == "id":
        d.result["Id"] = "sha256:" + "z" * 64
    else:
        d.result["Size"] = True
    with pytest.raises(build.BuildError):
        prepare(bundle)
    assert not (d.state / "builds/classifier.json").exists()


def default_inputs(d):
    put(d.root / ".evolution/wheels/tiny.whl", b"wheel")
    d.write_json(
        d.root / ".evolution/dependencies.json",
        {
            "wheelhouse": "wheels",
            "artifacts": [
                {
                    "name": "tiny.whl",
                    "sha256": d.sha256(d.root / ".evolution/wheels/tiny.whl"),
                    "size_bytes": 5,
                }
            ],
        },
    )
    for name in ["service", "gateway", "model"]:
        put(d.root / f"deploy/evolution/images/{name}.Dockerfile", b"FROM pinned\n")
    for name in ["guardrails", "connector", "proof"]:
        put(d.root / f"mvp/{name}/safe.py", b"pass\n")
    put(d.root / "evolution-requirements.lock", b"locked")
    put(d.root / "deploy/evolution/application.py", b"pass\n")
    put(d.state / "models/public", b"model")


@pytest.mark.parametrize("enabled", [False, True])
def test_prepare_preserves_three_default_builds_and_only_explicit_classifier(
    bundle, enabled
):
    d, directory, pin = bundle
    default_inputs(d)
    options = (
        {"classifier_context": directory, "classifier_seal_sha256": pin}
        if enabled
        else {}
    )
    artifacts = build.prepare(d, **options)
    assert [r["name"] for r in artifacts] == [
        "evolution-service",
        "evolution-gateway",
        "evolution-model",
    ] + (["evolution-classifier"] if enabled else [])
    for file, key in [
        ("preparation.lock.json", "images"),
        ("artifacts.lock.json", "artifacts"),
    ]:
        assert d.read_json(d.root / "deploy/evolution" / file)[key] == artifacts
    build.verify_inputs(d)


@pytest.mark.parametrize(
    "kind", ["directory_only", "pin_only", "stale_lock", "stale_record"]
)
def test_explicit_arguments_before_any_prepare_mutation(bundle, kind):
    d, directory, pin = bundle
    if kind == "stale_lock":
        pin_locks(d, {"name": "evolution-classifier"})
    if kind == "stale_record":
        put(d.state / "builds/classifier.json", b"{}")
    options = (
        {"classifier_context": directory}
        if kind == "directory_only"
        else {"classifier_seal_sha256": pin}
        if kind == "pin_only"
        else {}
    )
    with pytest.raises(build.BuildError):
        build.prepare(d, **options)
    assert d.calls == []


def test_command_binary_stream_is_forwarded_without_copy_or_encoding(monkeypatch):
    source = io.BytesIO(b"\xff\x00tar")
    seen = {}

    class Process:
        returncode = 0

        def __init__(self, argv, **kw):
            seen.update(argv=argv, **kw)

        def communicate(self, raw, timeout):
            assert raw is None and timeout == 7
            return b"ok", None

    monkeypatch.setattr(bootstrap.subprocess, "Popen", Process)
    assert bootstrap.command(["exact"], input_stream=source, timeout=7) == (0, "ok")
    assert seen["stdin"] is source and seen["start_new_session"] is True
    assert not source.closed


def test_command_rejects_conflicting_input_before_process(monkeypatch):
    monkeypatch.setattr(
        bootstrap.subprocess, "Popen", lambda *a, **k: pytest.fail("process started")
    )
    with pytest.raises(bootstrap.DeploymentError, match="conflicting_command_input"):
        bootstrap.command(["exact"], input_text="", input_stream=io.BytesIO())


def test_command_original_text_behavior(monkeypatch):
    class Process:
        returncode = 0

        def __init__(self, argv, **kw):
            assert kw["stdin"] == bootstrap.subprocess.PIPE

        def communicate(self, raw, timeout):
            assert raw == b"text"
            return b"answer", None

    monkeypatch.setattr(bootstrap.subprocess, "Popen", Process)
    assert bootstrap.command(["exact"], input_text="text") == (0, "answer")


def test_command_timeout_keeps_original_group_cleanup(monkeypatch):
    seen = []

    class Process:
        pid = 71

        def __init__(self, *a, **k):
            pass

        def communicate(self, *a, **k):
            raise TimeoutError("original")

        def wait(self):
            seen.append("wait")

    monkeypatch.setattr(bootstrap.subprocess, "Popen", Process)
    monkeypatch.setattr(bootstrap.os, "killpg", lambda *args: seen.append(args))
    with pytest.raises(TimeoutError, match="original"):
        bootstrap.command(["exact"], input_stream=io.BytesIO())
    assert seen == [(71, bootstrap.signal.SIGKILL), "wait"]


def reseal_tar(d, directory, raw):
    put(directory / "context.tar", raw)
    manifest = d.read_json(directory / "manifest.json")
    manifest.update(context_sha256=context.digest(raw), context_size_bytes=len(raw))
    put(directory / "manifest.json", context.canonical(manifest))
    seal = d.read_json(directory / "seal.json")
    seal.update(
        context_sha256=context.digest(raw),
        context_size_bytes=len(raw),
        manifest_sha256=d.sha256(directory / "manifest.json"),
    )
    put(directory / "seal.json", context.canonical(seal))
    return d.sha256(directory / "seal.json")


@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        "hardlink",
        "directory",
        "traversal",
        "extra",
        "absent",
        "duplicate",
        "payload",
        "mode",
        "owner",
        "pax",
    ],
)
def test_resealed_malformed_tar_still_requires_exact_public_files(bundle, kind):
    import tarfile

    d, directory, _ = bundle
    with tarfile.open(directory / "context.tar") as archive:
        rows = [(copy.copy(item), archive.extractfile(item).read()) for item in archive]
    item, payload = rows[0]
    if kind == "symlink":
        item.type, item.linkname, item.size = tarfile.SYMTYPE, "/private", 0
    elif kind == "hardlink":
        item.type, item.linkname, item.size = tarfile.LNKTYPE, "/private", 0
    elif kind == "directory":
        item.type, item.size = tarfile.DIRTYPE, 0
    elif kind == "traversal":
        item.name = "../outside"
    elif kind == "extra":
        rows.append(rows[-1])
    elif kind == "absent":
        rows.pop()
    elif kind == "duplicate":
        rows[1] = rows[0]
    elif kind == "payload":
        rows[0] = (item, b"x" * len(payload))
    elif kind == "mode":
        item.mode = 0o777
    elif kind == "owner":
        item.uid = 99
    else:
        item.pax_headers = {"comment": "unapproved"}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for member, value in rows:
            archive.addfile(member, io.BytesIO(value))
    pin = reseal_tar(d, directory, stream.getvalue())
    with pytest.raises(build.BuildError, match="classifier_tar_"):
        build.prepare_classifier(d, directory, pin)
    assert not d.calls


@pytest.mark.parametrize(
    "name",
    [
        "context_sha256",
        "source_digest",
        "manifest_sha256",
        "base_image",
        "image_id",
        "image_manifest_digest",
        "source_inventory",
        "inputs",
    ],
)
def test_saved_record_field_drift_refuses(bundle, name):
    d, _, _ = bundle
    artifact = prepare(bundle)
    pin_locks(d, artifact)
    record = d.read_json(d.state / "builds/classifier.json")
    record[name] = (
        {} if name == "source_inventory" else [] if name == "inputs" else "0" * 64
    )
    with pytest.raises(build.BuildError):
        build.verify_classifier(d, record, artifact)


def test_image_repository_normalization_preserves_exact_digest(bundle):
    d, _, _ = bundle
    d.base["RepoDigests"] = [context.BASE.removeprefix("docker.io/library/")]
    prepare(bundle)


def test_deadline_refuses_before_docker(bundle, monkeypatch):
    d, _, _ = bundle
    ticks = iter([1, 10000])
    monkeypatch.setattr(build.time, "monotonic", lambda: next(ticks))
    with pytest.raises(build.BuildError, match="verification_timeout"):
        prepare(bundle)
    assert not d.calls


@pytest.mark.parametrize("enabled", [False, True])
def test_certificate_names_remain_unchanged_without_classifier_artifact(
    tmp_path, enabled
):
    issued = []
    fake = SimpleNamespace(
        state=tmp_path,
        support=lambda _: SimpleNamespace(
            prepare=lambda _: None, issue_server=lambda *a, **k: issued.append((a, k))
        ),
        specs=lambda: {"images": [{"name": "evolution-classifier"}] if enabled else []},
    )
    bootstrap.LocalDeployment.prepare_certificates(fake)
    assert len(issued) == 7 + int(enabled)
    classifier = [
        row
        for row in issued
        if row[1]["names"] == ["classifier.gr-platform.svc.cluster.local"]
    ]
    assert len(classifier) == int(enabled)
    if enabled:
        assert classifier[0][0][1] == tmp_path / "assembly/tls/gr-platform/classifier"


@pytest.mark.parametrize("explicit", [False, True])
def test_platform_seam_preserves_defaults_and_exact_private_path(tmp_path, explicit):
    called = []

    class AssemblyError(RuntimeError):
        pass

    module = SimpleNamespace(
        platform=lambda *a, **k: called.append((a, k)) or "ready",
        AssemblyError=AssemblyError,
    )
    fake = SimpleNamespace(support=lambda _: module)
    options = (
        {"profile": "classifier", "evaluator_config_file": tmp_path / "private.json"}
        if explicit
        else {}
    )
    assert bootstrap.LocalDeployment.platform(fake, **options) == "ready"
    assert called[0][0] == (fake,)
    assert called[0][1] == (
        {"profile": "classifier", "evaluator_config_file": tmp_path / "private.json"}
        if explicit
        else {"profile": "deterministic_mock"}
    )


@pytest.mark.parametrize(
    "options",
    [
        {"classifier_context": Path("/public")},
        {"classifier_seal_sha256": "0" * 64},
        {
            "tools_only": True,
            "classifier_context": Path("/public"),
            "classifier_seal_sha256": "0" * 64,
        },
    ],
)
def test_bootstrap_invalid_prepare_flags_refuse_before_lock(options):
    fake = SimpleNamespace(lock=lambda: pytest.fail("lock/mutation reached"))
    with pytest.raises(bootstrap.DeploymentError):
        bootstrap.LocalDeployment.prepare(fake, **options)


def test_orphan_secondary_classifier_lock_refuses_before_prepare(bundle):
    d, _, _ = bundle
    d.write_json(
        d.root / "deploy/evolution/artifacts.lock.json",
        {"artifacts": [{"name": "evolution-classifier"}]},
    )
    with pytest.raises(build.BuildError, match="classifier_lock_mismatch"):
        build.prepare(d)
    assert not d.calls


def test_both_default_paths_ignore_no_classifier_modules(bundle, monkeypatch):
    d, _, _ = bundle
    default_inputs(d)
    monkeypatch.setattr(
        build, "classifier_builder", lambda _: pytest.fail("optional classifier loaded")
    )
    build.prepare(d)
    build.verify_inputs(d)


def test_builder_loads_only_pinned_captured_source(closure):
    d = Deployment(closure)
    loaded = build.classifier_builder(d)
    assert loaded.__file__ == str(d.root / context.BUILDER)
    assert loaded.SOURCE_KEYS == context.SOURCE_KEYS


def test_builder_path_replacement_cannot_execute_unverified_source(
    closure, monkeypatch
):
    import builtins

    d = Deployment(closure)
    path = d.root / context.BUILDER
    original = path.read_bytes()

    def replaced(raw, filename, mode):
        assert raw == original
        put(
            path.with_name("replacement.py"),
            b'raise AssertionError("unverified source executed")',
        )
        path.with_name("replacement.py").replace(path)
        return builtins.compile(raw, filename, mode)

    monkeypatch.setattr(build, "compile", replaced, raising=False)
    with pytest.raises(build.BuildError, match="classifier_builder_changed"):
        build.classifier_builder(d)


@pytest.mark.parametrize("kind", ["symlink", "fifo", "writable", "hash"])
def test_builder_unsafe_source_refuses_before_execution(closure, kind):
    import os

    d = Deployment(closure)
    path = d.root / context.BUILDER
    if kind == "writable":
        path.chmod(0o666)
    elif kind == "hash":
        path.write_bytes(b'raise AssertionError("not authenticated")')
    else:
        other = path.with_name("saved.py")
        path.rename(other)
        if kind == "symlink":
            path.symlink_to(other)
        else:
            os.mkfifo(path, 0o600)
    with pytest.raises(build.BuildError, match="classifier_builder_changed"):
        build.classifier_builder(d)
