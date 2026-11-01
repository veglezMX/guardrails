"""Native TLS service process; configuration paths come from a trusted mount."""

import argparse
import os
from pathlib import Path

import uvicorn
from guardrails.evolution.config import load_config


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=["management", "runtime"])
    parser.add_argument("--config-directory", type=Path, default=Path("/config"))
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args(argv)
    directory = args.config_directory.resolve(strict=True)
    config = load_config(directory / "service.json")
    os.chdir(directory)
    uvicorn.run(
        "guardrails.evolution."
        + ("management_api" if args.component == "management" else "runtime_api")
        + ":create_app",
        factory=True,
        host="0.0.0.0",
        port=args.port,
        proxy_headers=False,
        access_log=False,
        ssl_certfile=str(config.tls_cert_file),
        ssl_keyfile=str(config.tls_key_file),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
