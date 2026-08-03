"""Command line for the reference runtime.

    python -m guardrails serve        run the Evaluation API
    python -m guardrails validate     validate the policies in a deployment
    python -m guardrails capabilities print the capability manifest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .app import load_runtime
from .app import make_server
from .errors import PolicyValidationError
from .policy import load_policy_file

DEFAULT_ROOT = Path("mvp/deployment")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="guardrails")
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="deployment directory (policies, bindings, identities, runtime.json)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the Evaluation API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)

    sub.add_parser("validate", help="validate every policy document")
    sub.add_parser("capabilities", help="print the capability manifest")

    args = parser.parse_args(argv)

    if args.command == "validate":
        return _validate(args.root)
    if args.command == "capabilities":
        runtime = load_runtime(args.root)
        print(json.dumps(runtime.capabilities()[1], indent=2))
        return 0

    runtime = load_runtime(args.root)
    server = make_server(runtime, args.host, args.port)
    print(
        f"guardrails runtime listening on http://{args.host}:{args.port}\n"
        f"  POST /v1/evaluate\n  GET  /v1/capabilities",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


def _validate(root: Path) -> int:
    paths = sorted((root / "policies").glob("*.json"))
    if not paths:
        print(f"no policy documents under {root / 'policies'}", file=sys.stderr)
        return 1
    failed = 0
    for path in paths:
        try:
            policy = load_policy_file(path)
        except PolicyValidationError as exc:
            failed += 1
            print(f"REJECTED {path.name}: {exc}", file=sys.stderr)
        else:
            print(f"ok       {path.name}: {policy.name} v{policy.version}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
