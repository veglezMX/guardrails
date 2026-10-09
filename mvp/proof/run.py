"""Host orchestration for prepared proof gates; bootstrap is deliberately separate."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".proof"
IMAGE = "local/guardrails-proof:phase1"
COMPOSE_FILE = ROOT / "mvp/proof/compose.yaml"


def run(command, *, capture=False, check=True):
    return subprocess.run(command, cwd=ROOT, check=check, text=True, capture_output=capture)


def compose(*args, capture=False, check=True):
    env = os.environ.copy()
    env["PROOF_UID"] = str(os.getuid())
    env["PROOF_GID"] = str(os.getgid())
    return subprocess.run(["docker", "compose", "-p", "guardrails-phase1", "-f", str(COMPOSE_FILE), *args],
                          cwd=ROOT, env=env, text=True, capture_output=capture, check=check)


def prepared():
    if not (STATE / "artifacts/manifest.json").exists():
        raise RuntimeError("Missing prepared artifact manifest. Run just proof-bootstrap first.")
    for image in (IMAGE, "local/guardrails-gateway:phase1"):
        run(["docker", "image", "inspect", image], capture=True)


def container_tests(suite, profile):
    return run(["docker", "run", "--rm", "--network", "none",
                "--user", f"{os.getuid()}:{os.getgid()}",
                "-v", f"{ROOT}:/workspace:ro", "-w", "/workspace",
                "-e", "PYTHONPATH=/workspace/mvp", "-e", "PYTHONDONTWRITEBYTECODE=1",
                IMAGE, "python", "-m", "proof.test_profiles", suite, profile], check=False).returncode


def export_schema():
    result = run(["docker", "run", "--rm", "--network", "none", "-v", f"{ROOT}:/workspace:ro",
                  "-w", "/workspace", "-e", "PYTHONPATH=/workspace/mvp", IMAGE, "python", "-c",
                  "import json;from guardrails.transport import contract_openapi;print(json.dumps(contract_openapi(),sort_keys=True,indent=2))"], capture=True)
    # A serialized schema is an implementation artifact, not a separately authored contract.
    schema = json.loads(result.stdout)
    (ROOT / "mvp/proof/openapi.json").write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    result = run(["docker", "run", "--rm", "--network", "none", "-v", f"{ROOT}:/workspace:ro",
                  "-w", "/workspace", "-e", "PYTHONPATH=/workspace/mvp", IMAGE, "python", "-c",
                  "import json;from guardrails.management.app import create_app;print(json.dumps(create_app(schema_only=True).openapi(),sort_keys=True,indent=2))"], capture=True)
    (ROOT / 'mvp/proof/management-openapi.json').write_text(json.dumps(json.loads(result.stdout), indent=2, sort_keys=True) + '\n')
    result = run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{ROOT}:/workspace:ro',
                  '-w', '/workspace', '-e', 'PYTHONPATH=/workspace/mvp', IMAGE, 'python', '-c',
                  'import json;from guardrails.runtime.app import create_app;print(json.dumps(create_app(schema_only=True).openapi(),sort_keys=True,indent=2))'], capture=True)
    (ROOT / 'mvp/proof/runtime-openapi.json').write_text(json.dumps(json.loads(result.stdout), indent=2, sort_keys=True) + '\n')


def management_tests(profile='management'):
    # Separate real HTTPS process per fixture, real files retained after container exit.
    target = STATE / f'state/{profile}-tests' / str(uuid4())
    target.mkdir(parents=True, mode=0o700)
    result = run(['docker', 'run', '--rm', '--network', 'none',
                  '--user', f'{os.getuid()}:{os.getgid()}',
                  '-v', f'{ROOT}:/workspace:ro', '-v', f'{STATE / "pki"}:/pki:ro',
                  '-v', f'{target}:/results', '-w', '/workspace',
                  '-e', 'PYTHONPATH=/workspace/mvp', '-e', 'PYTHONDONTWRITEBYTECODE=1',
                  '-e', 'PROOF_MANAGEMENT_TEST_ROOT=/results', '-e', 'PROOF_PKI=/pki',
                  IMAGE, 'python', '-m', 'proof.test_profiles', 'integration', profile], check=False)
    print(f'{profile.title()} gate state and sanitized service logs retained: {target.relative_to(ROOT)}')
    return result.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", choices=["unit", "contract", "integration", "acceptance", "up", "down", "reset"])
    parser.add_argument("profile", nargs="?", default="all")
    args = parser.parse_args()
    if args.suite == 'up':
        prepared()
        return run([str(STATE / 'host-venv/bin/python'), '-m', 'proof.launcher', 'up'], check=False).returncode
    if args.suite == "down":
        launcher = run([str(STATE / 'host-venv/bin/python'), '-m', 'proof.launcher', 'down'], check=False).returncode if (STATE / 'live.json').exists() else 0
        if launcher:
            return launcher
        return compose("down", "--remove-orphans", check=False).returncode
    if args.suite == "reset":
        # Reset only this named disposable stack; never remove exported reports or secrets.
        if (STATE / 'live.json').exists():
            run([str(STATE / 'host-venv/bin/python'), '-m', 'proof.launcher', 'reset'])
        compose("down", "--remove-orphans")
        runtime = STATE / "state/runtime"
        if runtime.exists():
            for child in runtime.iterdir():
                if child.is_file() and child.name in {"smoke-audit.jsonl"}:
                    child.unlink()
        print("Disposable smoke state reset; reports, prepared artifacts and credentials retained.")
        return 0
    if args.suite == "acceptance":
        print("Release acceptance is not implemented; Phase 5 is pending.", file=sys.stderr)
        return 2
    prepared()
    if args.suite in {"unit", "contract"}:
        if args.suite == "contract":
            export_schema()
        return container_tests(args.suite, args.profile)
    if args.profile in ('management', 'runtime'):
        return management_tests(args.profile)
    if args.profile == 'all':
        for profile in ('smoke', 'management', 'runtime'):
            code = run([sys.executable, '-m', 'proof.run', 'integration', profile], check=False).returncode
            if code:
                return code
            if profile == 'smoke':
                compose('down', '--remove-orphans')
        environment = os.environ | {'PYTHONPATH': str(ROOT / 'mvp'), 'PYTHONDONTWRITEBYTECODE': '1'}
        code = "import pytest;from proof.test_profiles import GatePlugin,ALL_EXTRAS;files=ALL_EXTRAS['integration'];raise SystemExit(pytest.main(['-q','-p','no:cacheprovider',*files],plugins=[GatePlugin(files)]))"
        return subprocess.run([str(STATE / 'host-venv/bin/python'), '-c', code], cwd=ROOT, env=environment).returncode
    if args.profile != "smoke":
        print('Unknown integration profile', file=sys.stderr)
        return 2
    compose("up", "-d", "--no-build", "--pull", "never", "runtime", "mock-model", "gateway-a", "gateway-b")
    result = compose("run", "--rm", "--no-deps", "--pull", "never", "runner",
                     "python", "-m", "proof.test_profiles", "integration", "smoke", check=False)
    print("Smoke stack retained for inspection; stop it with just proof-down.")
    return result.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(f"Proof command failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(2)
