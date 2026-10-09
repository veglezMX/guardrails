"""Host orchestration for prepared proof gates; bootstrap is deliberately separate."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", choices=["unit", "contract", "integration", "acceptance", "down", "reset"])
    parser.add_argument("profile", nargs="?", default="all")
    args = parser.parse_args()
    if args.suite == "down":
        return compose("down", "--remove-orphans", check=False).returncode
    if args.suite == "reset":
        # Reset only this named disposable stack; never remove exported reports or secrets.
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
    if args.profile != "smoke":
        # All/later gates must fail rather than count a smoke run as release acceptance.
        print("Only the Phase 1 smoke integration profile is implemented.", file=sys.stderr)
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
