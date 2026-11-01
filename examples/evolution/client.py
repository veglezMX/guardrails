"""Team-owned text client. Provider credentials stay on the team's gateway.

Use distinct gateway/token/model arguments for team A and B. The configured
profile labels the requested run; cluster acceptance independently verifies the
model artifact and real inference path. No model prose is treated as a golden.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from connector.evolution import MAX_WIRE_BYTES, https_origin, read_secret
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import PolicyRef, strict_json_loads


class TeamClient:
    def __init__(self, *, gateway: str, ca_file: str, token_file: str,
                 tenant_id: str, model: str, profile: str):
        endpoint = https_origin(gateway)
        if httpx.URL(endpoint).path not in {"", "/"} or profile not in {"local_model", "deterministic_mock"}:
            raise ValueError("Invalid client configuration")
        self.tenant_id, self.model, self.profile = tenant_id, model, profile
        self.token = read_secret(token_file)
        self.timeout = 50 if profile == "local_model" else 15
        self.client = httpx.AsyncClient(base_url=endpoint, trust_env=False, follow_redirects=False,
            verify=ssl.create_default_context(cafile=ca_file),
            timeout=httpx.Timeout(self.timeout, connect=.25))

    async def close(self):
        await self.client.aclose()

    async def complete(self, text: str) -> dict:
        if type(text) is not str or not text or len(text.encode("utf-8")) > 65536:
            raise EvolutionError("validation_failed")
        request_id = str(uuid4())
        headers = {"Authorization": "Bearer " + self.token, "X-Request-ID": request_id,
                   "Accept-Encoding": "identity"}
        payload = {"model": self.model, "messages": [{"role": "user", "content": text}],
                   "stream": False, "temperature": 0, "max_tokens": 128}
        try:
            async with asyncio.timeout(self.timeout), self.client.stream("POST", "/v1/chat/completions",
                    headers=headers, json=payload) as response:
                if response.status_code != 200:
                    # Do not persist or echo error bodies that may contain input.
                    codes = {401: "authentication_failed", 403: "forbidden", 429: "quota_exceeded"}
                    raise EvolutionError(codes.get(response.status_code, "evaluator_unavailable"))
                if (response.headers.get("content-type", "").split(";", 1)[0] != "application/json"
                        or response.headers.get("content-encoding", "identity") != "identity"
                        or response.headers.get("cache-control") != "no-store"):
                    raise EvolutionError("inspection_incomplete")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > MAX_WIRE_BYTES:
                        raise EvolutionError("payload_too_large")
                    raw.extend(chunk)
            value = strict_json_loads(bytes(raw))
            metadata = value["guardrails"]
            policy = PolicyRef.model_validate(metadata["policy"])
            if policy.tenant_id != self.tenant_id or value["model"] != self.model:
                raise ValueError()
            if metadata["correlation_id"] != request_id:
                raise ValueError()
            for field in ("input_decision_id", "output_decision_id"):
                if str(UUID(metadata[field])) != metadata[field]:
                    raise ValueError()
            if metadata["input_decision_id"] == metadata["output_decision_id"]:
                raise ValueError()
            for field in ("binding_revision", "snapshot_generation"):
                if type(metadata[field]) is not int or metadata[field] < 1:
                    raise ValueError()
            for field in ("input_action", "output_action"):
                if metadata[field] not in {"allow", "warn", "redact"}:
                    raise ValueError()
            if metadata["audit_status"] not in {"persisted", "spooled"}:
                raise ValueError()
            choices = value["choices"]
            if len(choices) != 1 or choices[0]["message"]["role"] != "assistant" or type(choices[0]["message"]["content"]) is not str:
                raise ValueError()
            return {"content": choices[0]["message"]["content"], "policy": policy.model_dump(mode="json"),
                    "input_action": metadata["input_action"], "output_action": metadata["output_action"],
                    "binding_revision": metadata["binding_revision"], "snapshot_generation": metadata["snapshot_generation"],
                    "correlation_id": request_id, "input_decision_id": metadata["input_decision_id"],
                    "output_decision_id": metadata["output_decision_id"], "audit_status": metadata["audit_status"]}
        except (KeyError, TypeError, ValueError):
            raise EvolutionError("inspection_incomplete") from None
        except (httpx.HTTPError, TimeoutError):
            raise EvolutionError("evaluator_unavailable") from None


async def run(args):
    client = TeamClient(gateway=args.gateway, ca_file=args.ca_file, token_file=args.token_file,
                        tenant_id=args.tenant, model=args.model, profile=args.profile)
    try:
        prompt = (Path(args.prompt_file).read_text() if args.prompt_file else sys.stdin.read(65537))
        for _ in range(args.repeat):
            result = await client.complete(prompt)
            if not args.show_response:
                result.pop("content")
            print(json.dumps({"status": "pass", "requested_profile": args.profile, **result}, sort_keys=True))
    finally:
        await client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("gateway", "ca-file", "token-file", "tenant", "model"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--profile", choices=("local_model", "deterministic_mock"), default="local_model")
    parser.add_argument("--prompt-file")
    parser.add_argument("--repeat", type=int, choices=range(1, 101), default=1)
    parser.add_argument("--show-response", action="store_true", help="Display inspected assistant content; default prints metadata only")
    args = parser.parse_args(argv)
    try:
        asyncio.run(run(args))
    except EvolutionError as error:
        print(json.dumps({"status": "fail", "code": error.code}))
        return 1
    except (OSError, ValueError):
        print(json.dumps({"status": "fail", "code": "validation_failed"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
