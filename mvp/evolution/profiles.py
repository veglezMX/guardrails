"""Explicit offline quality and optional managed verification command boundaries."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from pathlib import Path

from .registry import EvidenceError


def _absolute(path):
    if not isinstance(path, Path) or not path.is_absolute() or "\x00" in str(path):
        raise EvidenceError("profile_absolute_path_required")
    return str(path)


def run_profile(args, root):
    environment = dict(os.environ, PYTHONPATH=f"{root / 'mvp'}{os.pathsep}{root}",
                       PYTHONDONTWRITEBYTECODE="1")
    for name in ("PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT", "PYTHONOPTIMIZE"):
        environment.pop(name, None)
    if args.command == "benchmark":
        if not re.fullmatch(r"[0-9a-f]{64}", args.source_digest):
            raise EvidenceError("classifier_source_digest_required")
        command = [_absolute(args.classifier_python), "-B", "-s", "-m",
                   "services.classifier.benchmark", "--run-frozen-heldout",
                   "--corpus", str(root / "mvp/tests/evolution/fixtures/injection-heldout-v1.json"),
                   "--model-directory", _absolute(args.model_directory),
                   "--output", _absolute(args.output), "--source-digest", args.source_digest]
        environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                           HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false",
                           OMP_NUM_THREADS="2", MKL_NUM_THREADS="2")
        timeout = 3600
    elif args.command == "bedrock-live":
        if not args.allow_external:
            raise EvidenceError("external_profile_not_authorized")
        command = [sys.executable, "-B", "-s", "-m", "guardrails.evolution.bedrock",
                   "--allow-external", "--config", _absolute(args.config),
                   "--credentials", _absolute(args.credentials), "--ca-file", _absolute(args.ca_file)]
        timeout = 60
    else:
        raise EvidenceError("profile_not_supported")
    try:
        process = subprocess.Popen(command, cwd=root, env=environment, start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
    except subprocess.TimeoutExpired:
        raise EvidenceError("profile_timeout") from None
    except OSError:
        raise EvidenceError("profile_start_failed") from None
